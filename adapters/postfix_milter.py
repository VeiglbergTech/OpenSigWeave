"""Standalone Postfix SMTP Milter sidecar for OpenSigWeave."""
from __future__ import annotations
import json, logging, os, re, tempfile, urllib.error, urllib.parse, urllib.request
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
CONTROL_HEADERS = {"x-opensigweave-mode", "x-opensigweave-signature", "x-opensigweave-signature-code"}
SIGNED_TYPES = {"multipart/signed", "multipart/encrypted", "application/pkcs7-mime", "application/x-pkcs7-mime", "application/pgp-encrypted"}
HEADER_NAME = re.compile(r"^[!#$%&'*+.^_|~0-9A-Za-z-]+$")

class EngineRequestError(RuntimeError):
    pass

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

def _plain_signature(html):
    parser = _TextExtractor()
    parser.feed(html)
    parser.close()
    return "".join(parser.parts).strip()

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
        for index in range(len(old)):
            changes.append(HeaderChange(name, None, index + 1))
        for index, value in enumerate(new, start=1):
            changes.append(HeaderChange(name, value, index))
    return tuple(changes)

def rewrite_message(headers, body, signature_html, *, inject_on_replies):
    """Safely rewrite supported MIME text parts while retaining binary attachments."""
    if len(body) > MAX_MESSAGE_BYTES:
        raise ValueError("Message exceeds configured size limit")
    if not signature_html:
        return RewriteResult(body, (), False)
    names = [name.casefold() for name, _ in headers]
    if "x-opensigweave-injected" in names:
        return RewriteResult(body, (), False)
    if not inject_on_replies and any(name in {"in-reply-to", "references"} for name in names):
        return RewriteResult(body, (), False)
    parsed = BytesParser(policy=policy.default).parsebytes(_raw_message(headers, body))
    if any(part.get_content_type().lower() in SIGNED_TYPES and
           part.get_content_disposition() != "attachment" and not part.get_filename()
           for part in parsed.walk()):
        return RewriteResult(body, (), False)
    parts = list(_editable_parts(parsed))
    if any(part.defects for part in parts):
        return RewriteResult(body, (), False)
    plain_signature = _plain_signature(signature_html)
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
        if content_type == "text/html":
            if re.search(r"<!--\s*OpenSigWeave:INJECTED\s*-->", content, re.I):
                continue
            updated, _ = insert_signature(content, signature_html, html=True)
        else:
            if not plain_signature:
                continue
            updated, _ = insert_signature(content, plain_signature, html=False)
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

def fetch_signature(api_url, engine_api_key, sender, *, mode=None, timeout=5.0):
    """Call the existing versioned engine endpoint; selection remains centralized."""
    if not api_url.startswith("https://"):
        raise EngineRequestError("The engine URL must use HTTPS")
    if len(engine_api_key) < 32:
        raise EngineRequestError("The engine API key is not configured")
    url = api_url.rstrip("/") + "/api/v1/engine/signatures/" + urllib.parse.quote(sender, safe="")
    headers = {"X-Engine-Key": engine_api_key, "Accept": "application/json"}
    if mode in VALID_MODES:
        headers["X-OpenSigWeave-Mode"] = mode
    request = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
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

    class OpenSigWeaveMilter(Milter.Base):
        @Milter.symlist("{auth_authen}")
        def envfrom(self, sender, *args):
            self.sender = _sender_address(sender)
            self.authenticated_user = self.getsymval("{auth_authen}")
            self.headers = []
            self.body = tempfile.SpooledTemporaryFile(max_size=1024 * 1024, mode="w+b")
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
            self.body.write(chunk)
            return Milter.CONTINUE
        def eom(self):
            try:
                # Postfix sender_login_maps remains responsible for send-as authorization.
                if not self.authenticated_user or not self.sender:
                    return Milter.CONTINUE
                self.body.seek(0)
                original_body = self.body.read(MAX_MESSAGE_BYTES + 1)
                if len(original_body) > MAX_MESSAGE_BYTES:
                    return Milter.TEMPFAIL
                modes = [value.strip().upper() for name, value in self.headers
                         if name.casefold() == "x-opensigweave-mode"]
                mode = modes[0] if len(modes) == 1 and modes[0] in VALID_MODES else None
                result = fetch_signature(api_url, api_key, self.sender, mode=mode, timeout=timeout)
                if not result["html"]:
                    return Milter.CONTINUE
                rewritten = rewrite_message(
                    self.headers, original_body, result["html"],
                    inject_on_replies=bool(result.get("inject_on_replies", False)),
                )
                if not rewritten.changed:
                    return Milter.CONTINUE
                for change in rewritten.header_changes:
                    self.chgheader(change.name, change.index, change.value)
                control_indexes = {}
                for name, _value in self.headers:
                    if name.casefold() in CONTROL_HEADERS:
                        key = name.casefold()
                        control_indexes[key] = control_indexes.get(key, 0) + 1
                        self.chgheader(name, control_indexes[key], None)
                self.addheader("X-OpenSigWeave-Injected", "true")
                for offset in range(0, len(rewritten.body), BODY_REPLACEMENT_CHUNK):
                    self.replacebody(rewritten.body[offset:offset + BODY_REPLACEMENT_CHUNK])
                return Milter.CONTINUE
            except Exception:
                LOGGER.exception("Postfix Milter failed to process message")
                return Milter.TEMPFAIL
            finally:
                try:
                    self.body.close()
                except Exception:
                    pass
        def abort(self):
            try:
                self.body.close()
            except Exception:
                pass
        def close(self):
            try:
                self.body.close()
            except Exception:
                pass
            return Milter.CONTINUE

    Milter.factory = OpenSigWeaveMilter
    Milter.set_flags(Milter.ADDHDRS | Milter.CHGHDRS | Milter.MODBODY)
    LOGGER.info("Starting OpenSigWeave Postfix Milter on %s", socket_name)
    Milter.runmilter("opensigweave", socket_name, 30)

if __name__ == "__main__":
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
    run()
