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


def _build_raw_message(headers: Any, contents: Any) -> bytes:
    if not isinstance(headers, list) or not isinstance(contents, str):
        raise ValueError("Invalid Stalwart message structure")
    lines: list[bytes] = []
    for item in headers:
        if (not isinstance(item, list) or len(item) != 2
                or not isinstance(item[0], str) or not isinstance(item[1], str)
                or not _HEADER_NAME.fullmatch(item[0])):
            raise ValueError("Invalid Stalwart header entry")
        value = item[1]
        # Stalwart documents raw values with a leading space and CRLF. Reject
        # embedded new header lines rather than accepting header injection.
        if "\r\n" in value[:-2] or "\n" in value.replace("\r\n", ""):
            raise ValueError("Invalid Stalwart header value")
        lines.append((item[0] + ":" + value.rstrip("\r\n")).encode("utf-8"))
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
    for header in headers:
        if (isinstance(header, list) and len(header) == 2
                and isinstance(header[0], str)
                and header[0].casefold() in _CONTROL_HEADERS):
            modifications.append({
                "type": "deleteHeader",
                "name": header[0],
                "index": sum(
                    1 for prior in headers[:headers.index(header)]
                    if isinstance(prior, list) and len(prior) == 2
                    and prior[0].casefold() == header[0].casefold()
                ),
            })

    html_signature = signature_data.get("html")
    if not isinstance(html_signature, str) or not html_signature:
        return {"action": "accept", **({"modifications": modifications} if modifications else {})}

    if any(not isinstance(item, list) or len(item) != 2 or not isinstance(item[0], str)
           for item in headers):
        raise ValueError("Invalid Stalwart message headers")

    try:
        raw = _build_raw_message(headers, message.get("contents"))
    except (UnicodeEncodeError, ValueError):
        raise
    parsed = BytesParser(policy=policy.default).parsebytes(raw)

    if any(part.get_content_type().lower() in _SIGNED_OR_ENCRYPTED for part in parsed.walk()):
        return {"action": "accept", **({"modifications": modifications} if modifications else {})}

    header_names = [item[0].casefold() for item in headers]
    if "x-opensigweave-injected" in header_names:
        return {"action": "accept", **({"modifications": modifications} if modifications else {})}

    is_reply = any(name in {"in-reply-to", "references"} for name in header_names)
    if is_reply and not bool(signature_data.get("inject_on_replies", False)):
        return {"action": "accept", **({"modifications": modifications} if modifications else {})}

    plain = _plain_signature(html_signature)
    changed = False
    for part in parsed.walk():
        if part.is_multipart() or part.get_content_disposition() == "attachment":
            continue
        if part.get_filename():
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

    modifications.extend([
        {"type": "replaceContents", "value": new_body.decode("utf-8", errors="replace")},
        {"type": "addHeader", "name": "X-OpenSigWeave-Injected", "value": "true"},
    ])
    return {"action": "accept", "modifications": modifications}
