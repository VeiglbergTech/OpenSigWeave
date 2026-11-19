"""Standalone Postfix SMTP Milter sidecar for OpenSigWeave."""
from __future__ import annotations
import json, logging, os, re, ssl, tempfile, urllib.error, urllib.parse, urllib.request
from dataclasses import dataclass
from email import policy
from email.generator import BytesGenerator
from email.parser import BytesParser
from html.parser import HTMLParser
from io import BytesIO
from core.signature_placement import insert_signature

LOGGER = logging.getLogger("opensigweave.postfix")
MAX_MESSAGE_BYTES = 20 * 1024 * 1024
MAX_ENGINE_RESPONSE_BYTES = 1024 * 1024
BODY_REPLACEMENT_CHUNK = 64 * 1024
VALID_MODES = {"AUTO", "PROFESSIONAL", "INTERNAL", "NONE"}
# All OpenSigWeave control/marker headers are untrusted when received from SMTP clients.
CONTROL_HEADERS = {
    "x-opensigweave-mode",
    "x-opensigweave-signature",
    "x-opensigweave-signature-code",
    "x-opensigweave-injected",
}
SIGNED_TYPES = {"multipart/signed", "multipart/encrypted", "application/pkcs7-mime", "application/x-pkcs7-mime", "application/pgp-encrypted"}
HEADER_NAME = re.compile(r"^[!#$%&'*+.^_|~0-9A-Za-z-]+$")

class EngineRequestError(RuntimeError):
    pass


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Never forward the engine API key through an HTTP redirect."""
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None

@dataclass(frozen=True)
class HeaderChange:
    name: str
    value: str | None
    index: int

@dataclass(frozen=True)
class RewriteResult:
    body: bytes
    header_changes: tuple[HeaderChange, ...]
    changed: bool

class _TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
    def handle_data(self, data):
        self.parts.append(data)
    def handle_starttag(self, tag, attrs):
        if tag.lower() in {"br", "p", "div", "li", "tr"}:
            self.parts.append("\n")

def _plain_signature(html, *, trim_whitespace=True):
    parser = _TextExtractor()
    parser.feed(html)
    parser.close()
    text = "".join(parser.parts)
    return text.strip() if trim_whitespace else text

def _raw_message(headers, body):
    lines = []
    for name, value in headers:
        if not HEADER_NAME.fullmatch(name):
            raise ValueError("Invalid message header name")
        clean = value[:-2] if value.endswith("\r\n") else value
        if "\r" in clean or "\n" in clean:
            raise ValueError("Invalid message header value")
        lines.append(name.encode("ascii") + b":" + clean.encode("utf-8"))
    return b"\r\n".join(lines) + b"\r\n\r\n" + body

def _contains_protected_signed_part(part):
    """Detect signed/encrypted MIME only in the editable message tree.

    Attached messages and files are opaque: their own signatures must not
    prevent rewriting an unrelated outer message body.
    """
    content_type = part.get_content_type().lower()
    if (content_type == "message/rfc822"
            or part.get_content_disposition() == "attachment"
            or part.get_filename()):
        return False
    if content_type in SIGNED_TYPES:
        return True
    if part.is_multipart():
        return any(_contains_protected_signed_part(child) for child in part.iter_parts())
    return False


def _editable_parts(part):
    content_type = part.get_content_type().lower()
    if content_type in SIGNED_TYPES:
        return
    if content_type == "message/rfc822" or part.get_content_disposition() == "attachment" or part.get_filename():
        return
    yield part
    if part.is_multipart():
        for child in part.iter_parts():
            yield from _editable_parts(child)

def _header_changes(original, parsed):
    changes = []
    for name in ("MIME-Version", "Content-Type", "Content-Transfer-Encoding"):
        old = [value for field, value in original if field.casefold() == name.casefold()]
        new = [str(value) for value in parsed.get_all(name, [])]
        if old == new:
            continue
        for index in range(len(old), 0, -1):
            changes.append(HeaderChange(name, None, index))
        for index, value in enumerate(new, start=1):
            changes.append(HeaderChange(name, value, index))
    return tuple(changes)

def _trim_trailing_whitespace(content, *, html):
    """Remove common trailing editor whitespace from decoded text content."""
    if not html:
        return content.rstrip().removesuffix("\u00a0").rstrip()
    previous = None
    while content != previous:
        previous = content
        content = content.rstrip()
        content = re.sub(r"<br\b[^>]*>\s*$", "", content, flags=re.I)
        content = re.sub(r"<(?:p|div)\b[^>]*>\s*(?:&nbsp;|\u00a0)?\s*</(?:p|div)>\s*$", "", content, flags=re.I)
    return content


def _strip_device_signatures(content, *, html):
    """Remove mobile/Outlook auto-signature lines from decoded text parts."""
    if html:
        return re.sub(r"<br\b[^>]*>\s*(?:Sent from my |Get Outlook for )[^<]*", "", content, flags=re.I)
    return re.sub(r"\r?\n[ \t]*(?:Sent from my |Get Outlook for )[^\r\n<]*", "", content, flags=re.I)


def rewrite_message(headers, body, signature_html, *, inject_on_replies, trim_whitespace=True, strip_device_signatures=False):
    """Safely rewrite supported MIME text parts while retaining binary attachments."""
    if len(body) > MAX_MESSAGE_BYTES:
        raise ValueError("Message exceeds configured size limit")
    if not signature_html:
        return RewriteResult(body, (), False)
    names = [name.casefold() for name, _value in headers]
    if not inject_on_replies and any(name in {"in-reply-to", "references"} for name in names):
        return RewriteResult(body, (), False)
    parsed = BytesParser(policy=policy.default).parsebytes(_raw_message(headers, body))
    if _contains_protected_signed_part(parsed):
        return RewriteResult(body, (), False)
    parts = list(_editable_parts(parsed))
    if any(part.defects for part in parts):
        return RewriteResult(body, (), False)
    plain_signature = _plain_signature(signature_html, trim_whitespace=trim_whitespace)
    changed = False
    for part in parts:
        content_type = part.get_content_type().lower()
        if part.is_multipart() or content_type not in {"text/plain", "text/html"}:
            continue
        try:
            content = part.get_content()
        except (LookupError, UnicodeError, TypeError):
            continue
        if not isinstance(content, str):
            continue
        is_html = content_type == "text/html"
        if strip_device_signatures:
            content = _strip_device_signatures(content, html=is_html)
        if trim_whitespace:
            content = _trim_trailing_whitespace(content, html=is_html)
        selected_signature = signature_html if is_html else plain_signature
        if not selected_signature:
            continue
        if trim_whitespace:
            selected_signature = _trim_trailing_whitespace(selected_signature, html=is_html).lstrip()
        updated, _ = insert_signature(
            content, selected_signature, html=is_html, trim_whitespace=trim_whitespace
        )
        charset = part.get_content_charset() or "utf-8"
        transfer = str(part.get("Content-Transfer-Encoding", "quoted-printable")).casefold()
        cte = transfer if transfer in {"7bit", "8bit", "quoted-printable", "base64"} else "quoted-printable"
        part.set_content(updated, subtype=part.get_content_subtype(), charset=charset, cte=cte)
        changed = True
    if not changed:
        return RewriteResult(body, (), False)
    output = BytesIO()
    BytesGenerator(output, policy=policy.SMTP).flatten(parsed)
    serialized = output.getvalue()
    _head, found, new_body = serialized.partition(b"\r\n\r\n")
    if not found:
        raise ValueError("MIME serializer returned an invalid message")
    return RewriteResult(new_body, _header_changes(headers, parsed), True)

def fetch_signature(api_url, engine_api_key, sender, *, mode=None, signature_name=None, signature_code=None, timeout=5.0):
    """Call the existing versioned engine endpoint; selection remains centralized."""
    if not api_url.startswith("https://"):
        raise EngineRequestError("The engine URL must use HTTPS")
    if len(engine_api_key) < 32:
        raise EngineRequestError("The engine API key is not configured")
    url = api_url.rstrip("/") + "/api/v1/engine/signatures/" + urllib.parse.quote(sender, safe="")
    headers = {"X-Engine-Key": engine_api_key, "Accept": "application/json"}
    if mode in VALID_MODES:
        headers["X-OpenSigWeave-Mode"] = mode
    if isinstance(signature_name, str) and signature_name.strip():
        headers["X-OpenSigWeave-Signature"] = signature_name.strip()
    if isinstance(signature_code, str) and signature_code.strip():
        headers["X-OpenSigWeave-Signature-Code"] = signature_code.strip()
    request = urllib.request.Request(url, headers=headers, method="GET")
    try:
        ssl_context = ssl.create_default_context()
        opener = urllib.request.build_opener(
            urllib.request.HTTPSHandler(context=ssl_context), _NoRedirectHandler()
        )
        with opener.open(request, timeout=timeout) as response:
            if response.status != 200:
                raise EngineRequestError(f"Engine returned HTTP {response.status}")
            raw = response.read(MAX_ENGINE_RESPONSE_BYTES + 1)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise EngineRequestError("Signature engine request failed") from exc
    if len(raw) > MAX_ENGINE_RESPONSE_BYTES:
        raise EngineRequestError("Signature engine response exceeded the size limit")
    try:
        result = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise EngineRequestError("Signature engine returned invalid JSON") from exc
    if not isinstance(result, dict) or not isinstance(result.get("html"), str):
        raise EngineRequestError("Signature engine response has an invalid contract")
    for field in ("inject_on_replies", "trim_whitespace", "strip_device_signatures"):
        if not isinstance(result.get(field), bool):
            raise EngineRequestError(f"Signature engine response field {field} must be boolean")
    return result

def _sender_address(value):
    sender = value.strip()
    if sender.startswith("<") and sender.endswith(">"):
        sender = sender[1:-1]
    if not sender or any(char.isspace() for char in sender) or sender.count("@") != 1:
        return ""
    return sender.casefold()

def run():
    """Start the standalone sidecar. PyMilter is not an application dependency."""
    try:
        import Milter
    except ImportError as exc:
        raise RuntimeError("Install PyMilter to run the Postfix sidecar") from exc
    api_url = os.environ.get("OPENSIGWEAVE_ENGINE_URL", "").strip()
    api_key = os.environ.get("ENGINE_API_KEY", "")
    socket_name = os.environ.get("POSTFIX_MILTER_SOCKET", "inet:8899@127.0.0.1")
    timeout = min(max(float(os.environ.get("ENGINE_TIMEOUT_SECONDS", "5")), 0.5), 15.0)
    if not api_url.startswith("https://") or len(api_key) < 32:
        raise RuntimeError("Configure HTTPS OPENSIGWEAVE_ENGINE_URL and ENGINE_API_KEY (32+ characters)")

    Milter.factory = build_milter_class(Milter, api_url, api_key, timeout)
    Milter.set_flags(Milter.ADDHDRS | Milter.CHGHDRS | Milter.MODBODY)
    LOGGER.info("Starting OpenSigWeave Postfix Milter on %s", socket_name)
    Milter.runmilter("opensigweave", socket_name, 30)

def build_milter_class(Milter, api_url, api_key, timeout=5.0, signature_fetcher=fetch_signature):
    """Build the real Milter callback class; injection point supports isolated integration tests."""
    class OpenSigWeaveMilter(Milter.Base):
        @Milter.symlist("{auth_authen}")
        def envfrom(self, sender, *args):
            self.sender = _sender_address(sender)
            self.authenticated_user = self.getsymval("{auth_authen}")
            self.headers = []
            self.body_file = tempfile.SpooledTemporaryFile(max_size=1024 * 1024, mode="w+b")
            self.body_size = 0
            return Milter.CONTINUE
        def header(self, field, value):
            if not HEADER_NAME.fullmatch(field):
                return Milter.REJECT
            self.headers.append((field, value.strip("\r\n")))
            return Milter.CONTINUE
        def body(self, chunk):
            self.body_size += len(chunk)
            if self.body_size > MAX_MESSAGE_BYTES:
                return Milter.TEMPFAIL
            self.body_file.write(chunk)
            return Milter.CONTINUE
        def eom(self):
            try:
                # Capture selector headers before removing all adapter control headers.
                mode_values = [value.strip().upper() for name, value in self.headers
                               if name.casefold() == "x-opensigweave-mode"]
                signature_values = [value.strip() for name, value in self.headers
                                    if name.casefold() == "x-opensigweave-signature"]
                code_values = [value.strip() for name, value in self.headers
                               if name.casefold() == "x-opensigweave-signature-code"]
                mode = mode_values[0] if len(mode_values) == 1 and mode_values[0] in VALID_MODES else None
                signature_name = signature_values[0] if len(signature_values) == 1 else None
                signature_code = code_values[0] if len(code_values) == 1 else None
    
                # Remove client-supplied control headers even when no signature is selected.
                control_names = {}
                for name, _value in self.headers:
                    if name.casefold() in CONTROL_HEADERS:
                        control_names.setdefault(name.casefold(), name)
                for folded_name, original_name in control_names.items():
                    occurrences = sum(1 for name, _value in self.headers
                                      if name.casefold() == folded_name)
                    for index in range(occurrences, 0, -1):
                        self.chgheader(original_name, index, None)
    
                # Postfix sender_login_maps remains responsible for send-as authorization.
                if not self.authenticated_user or not self.sender:
                    return Milter.CONTINUE
                self.body_file.seek(0)
                original_body = self.body_file.read(MAX_MESSAGE_BYTES + 1)
                if len(original_body) > MAX_MESSAGE_BYTES:
                    return Milter.TEMPFAIL
                result = signature_fetcher(
                    api_url, api_key, self.sender, mode=mode,
                    signature_name=signature_name, signature_code=signature_code,
                    timeout=timeout,
                )
                if not result["html"]:
                    return Milter.CONTINUE
                # Never trust a client-supplied idempotency marker. Remove every
                # adapter control header from the message passed to the MIME rewriter.
                message_headers = [
                    (name, value) for name, value in self.headers
                    if name.casefold() not in CONTROL_HEADERS
                ]
                rewritten = rewrite_message(
                    message_headers, original_body, result["html"],
                    inject_on_replies=result["inject_on_replies"],
                    trim_whitespace=result["trim_whitespace"],
                    strip_device_signatures=result["strip_device_signatures"],
                )
                if not rewritten.changed:
                    return Milter.CONTINUE
                for change in rewritten.header_changes:
                    self.chgheader(change.name, change.index, change.value)
                self.addheader("X-OpenSigWeave-Injected", "true")
                for offset in range(0, len(rewritten.body), BODY_REPLACEMENT_CHUNK):
                    self.replacebody(rewritten.body[offset:offset + BODY_REPLACEMENT_CHUNK])
                return Milter.CONTINUE
            except Exception:
                LOGGER.exception("Postfix Milter failed to process message")
                return Milter.TEMPFAIL
            finally:
                try:
                    self.body_file.close()
                except Exception:
                    pass
        def _close_body(self):
            body_file = getattr(self, "body_file", None)
            if body_file is not None:
                try:
                    body_file.close()
                finally:
                    self.body_file = None
        def abort(self):
            self._close_body()
        def close(self):
            self._close_body()
            return Milter.CONTINUE
    
    
    return OpenSigWeaveMilter


if __name__ == "__main__":
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
    run()
