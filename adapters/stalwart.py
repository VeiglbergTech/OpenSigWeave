"""Stalwart MTA-hook adapter helpers.

The adapter only edits MIME entities that Python's standards-based email parser
can safely round-trip. Signed/encrypted messages are deliberately left untouched.
"""
from __future__ import annotations

import re
from email import policy
from email.generator import BytesGenerator
from email.parser import BytesParser
from html.parser import HTMLParser
from io import BytesIO
from typing import Any


_HEADER_NAME = re.compile(r"^[!#$%&'*+.^_`|~0-9A-Za-z-]+$")
_CONTROL_HEADERS = {
    "x-opensigweave-mode",
    "x-opensigweave-signature",
    "x-opensigweave-signature-code",
}
_SIGNED_OR_ENCRYPTED = {
    "multipart/signed",
    "multipart/encrypted",
    "application/pkcs7-mime",
    "application/x-pkcs7-mime",
    "application/pgp-encrypted",
}


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() in {"br", "p", "div", "li", "tr"}:
            self.parts.append("\n")

    def text(self) -> str:
        return "".join(self.parts).strip()


def _plain_signature(html: str) -> str:
    parser = _TextExtractor()
    parser.feed(html)
    return parser.text()


def _validate_headers(headers: Any) -> None:
    """Validate every header before any early-return path can accept the message."""
    _validate_headers(headers)
    for item in headers:
        if (not isinstance(item, list) or len(item) != 2
                or not isinstance(item[0], str) or not isinstance(item[1], str)
                or not _HEADER_NAME.fullmatch(item[0])):
            raise ValueError("Invalid Stalwart header entry")
        value = item[1]
        # Stalwart documents raw values with a leading space and CRLF. Reject
        # embedded new header lines rather than accepting header injection.
        if (re.search(r"\r\n(?![ \t])", value.rstrip("\r\n"))
                or "\n" in value.replace("\r\n", "")
                or "\r" in value.replace("\r\n", "")):
            raise ValueError("Invalid Stalwart header value")


def _build_raw_message(headers: Any, contents: Any) -> bytes:
    if not isinstance(contents, str):
        raise ValueError("Invalid Stalwart message structure")
    _validate_headers(headers)
    lines = [
        (item[0] + ":" + item[1].rstrip("\r\n")).encode("utf-8")
        for item in headers
    ]
    return b"\r\n".join(lines) + b"\r\n\r\n" + contents.encode("utf-8")

def build_stalwart_hook_response(payload: dict[str, Any], signature_data: dict[str, Any]) -> dict[str, Any]:
    """Translate a rendered OpenSigWeave signature into Stalwart MTA modifications."""
    context = payload.get("context")
    envelope = payload.get("envelope")
    message = payload.get("message")
    if not isinstance(context, dict) or context.get("stage") != "data":
        raise ValueError("Stalwart adapter only accepts DATA-stage requests")
    if not isinstance(envelope, dict) or not isinstance(message, dict):
        raise ValueError("Missing Stalwart envelope or message")

    modifications: list[dict[str, Any]] = []
    headers = message.get("headers")
    if not isinstance(headers, list):
        raise ValueError("Invalid Stalwart message headers")

    # Remove OpenSigWeave control headers even when no signature is selected.
    seen_control_headers: dict[str, int] = {}
    for header in headers:
        if (isinstance(header, list) and len(header) == 2
                and isinstance(header[0], str)
                and header[0].casefold() in _CONTROL_HEADERS):
            normalized_name = header[0].casefold()
            index = seen_control_headers.get(normalized_name, 0)
            seen_control_headers[normalized_name] = index + 1
            modifications.append({
                "type": "deleteHeader",
                "name": header[0],
                "index": index,
            })

    html_signature = signature_data.get("html")
    if not isinstance(html_signature, str) or not html_signature:
        return {"action": "accept", **({"modifications": modifications} if modifications else {})}

    try:
        raw = _build_raw_message(headers, message.get("contents"))
    except (UnicodeEncodeError, ValueError):
        raise
    parsed = BytesParser(policy=policy.default).parsebytes(raw)

    def iter_message_parts(part):
        # Inspect and edit only the outer message. Attached files and embedded
        # forwarded messages must not affect the outer message's MIME policy.
        if part.get_content_type().lower() == "message/rfc822":
            return
        if part.get_content_disposition() == "attachment" or part.get_filename():
            return
        yield part
        if part.is_multipart():
            for child in part.iter_parts():
                yield from iter_message_parts(child)

    if any(part.get_content_type().lower() in _SIGNED_OR_ENCRYPTED
           for part in iter_message_parts(parsed)):
        return {"action": "accept", **({"modifications": modifications} if modifications else {})}

    header_names = [item[0].casefold() for item in headers]
    if "x-opensigweave-injected" in header_names:
        return {"action": "accept", **({"modifications": modifications} if modifications else {})}

    is_reply = any(name in {"in-reply-to", "references"} for name in header_names)
    if is_reply and not bool(signature_data.get("inject_on_replies", False)):
        return {"action": "accept", **({"modifications": modifications} if modifications else {})}

    original_mime_headers = {name: [str(value) for value in parsed.get_all(name, [])]
                             for name in ("Content-Type", "Content-Transfer-Encoding", "MIME-Version")}
    plain = _plain_signature(html_signature)
    changed = False

    for part in iter_message_parts(parsed):
        if part.is_multipart():
            continue
        content_type = part.get_content_type().lower()
        if content_type not in {"text/plain", "text/html"}:
            continue
        try:
            body = part.get_content()
        except (LookupError, UnicodeError, TypeError):
            continue
        if not isinstance(body, str):
            continue
        if content_type == "text/html":
            if re.search(r"<!--\s*OpenSigWeave:INJECTED\s*-->", body, re.I):
                continue
            fragment = "<br><br>" + html_signature
            closing_body = re.search(r"</body\s*>", body, re.I)
            if closing_body:
                body = body[:closing_body.start()] + fragment + body[closing_body.start():]
            else:
                body += fragment
        else:
            if not plain:
                continue
            body = body.rstrip() + "\n\n" + plain
        charset = part.get_content_charset() or "utf-8"
        part.set_content(body, subtype=content_type.split("/", 1)[1], charset=charset)
        changed = True

    if not changed:
        return {"action": "accept", **({"modifications": modifications} if modifications else {})}

    output = BytesIO()
    BytesGenerator(output, policy=policy.SMTP).flatten(parsed)
    serialized = output.getvalue()
    separator = b"\r\n\r\n"
    _, found, new_body = serialized.partition(separator)
    if not found:
        return {"action": "accept", **({"modifications": modifications} if modifications else {})}

    # The DATA hook receives headers separately from the body. set_content()
    # may update top-level MIME metadata, so mirror those deltas explicitly.
    for name, old_values in original_mime_headers.items():
        new_values = [str(value) for value in parsed.get_all(name, [])]
        if old_values == new_values:
            continue
        for _ in old_values:
            modifications.append({"type": "deleteHeader", "name": name, "index": 0})
        for value in new_values:
            modifications.append({"type": "addHeader", "name": name, "value": value})

    modifications.extend([
        {"type": "replaceContents", "value": new_body.decode("utf-8", errors="replace")},
        {"type": "addHeader", "name": "X-OpenSigWeave-Injected", "value": "true"},
    ])
    return {"action": "accept", "modifications": modifications}
