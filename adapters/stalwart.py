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

from core.signature_placement import insert_signature


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
    if not isinstance(headers, list):
        raise ValueError("Invalid Stalwart message headers")
    for item in headers:
        if (not isinstance(item, list) or len(item) != 2
                or not isinstance(item[0], str) or not isinstance(item[1], str)
                or not _HEADER_NAME.fullmatch(item[0])):
            raise ValueError("Invalid Stalwart header entry")
        value = item[1]
        value_without_terminator = value[:-2] if value.endswith("\r\n") else value
        if "\r" in value_without_terminator or "\n" in value_without_terminator:
            raise ValueError("Invalid Stalwart header value")


def _build_raw_message(headers: Any, contents: Any) -> bytes:
    if not isinstance(contents, str):
        raise ValueError("Invalid Stalwart message structure")
    _validate_headers(headers)
    lines = [
        (item[0] + ":" + (item[1][:-2] if item[1].endswith("\r\n") else item[1])).encode("utf-8")
        for item in headers
    ]
    return b"\r\n".join(lines) + b"\r\n\r\n" + contents.encode("utf-8")


def _safe_message_parts(root):
    """Yield editable outer-message MIME parts, excluding attached messages/files."""
    if root.get_content_type().lower() == "message/rfc822":
        return
    if root.get_content_disposition() == "attachment" or root.get_filename():
        return
    yield root
    if root.is_multipart():
        for child in root.iter_parts():
            yield from _safe_message_parts(child)


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
    _validate_headers(headers)

    # Remove OpenSigWeave control headers even when no signature is selected.
    seen_control_headers: dict[str, int] = {}
    control_header_deletions: list[dict[str, Any]] = []
    for header in headers:
        if (isinstance(header, list) and len(header) == 2
                and isinstance(header[0], str)
                and header[0].casefold() in _CONTROL_HEADERS):
            normalized_name = header[0].casefold()
            index = seen_control_headers.get(normalized_name, 0)
            seen_control_headers[normalized_name] = index + 1
            control_header_deletions.append({
                "type": "deleteHeader",
                "name": header[0],
                "index": index,
            })
    # Stalwart applies header modifications sequentially. Delete duplicate
    # occurrences from highest to lowest index so earlier deletions do not
    # shift the remaining target occurrences.
    control_header_deletions.sort(key=lambda item: (item["name"].casefold(), -item["index"]))
    modifications.extend(control_header_deletions)

    html_signature = signature_data.get("html")
    if not isinstance(html_signature, str) or not html_signature:
        return {"action": "accept", **({"modifications": modifications} if modifications else {})}

    raw = _build_raw_message(headers, message.get("contents"))
    parsed = BytesParser(policy=policy.default).parsebytes(raw)
    safe_parts = list(_safe_message_parts(parsed))
    if any(part.defects for part in safe_parts):
        return {"action": "accept", **({"modifications": modifications} if modifications else {})}
    if any(part.get_content_type().lower() in _SIGNED_OR_ENCRYPTED for part in safe_parts):
        return {"action": "accept", **({"modifications": modifications} if modifications else {})}

    supported_transfer_encodings = {"7bit", "8bit", "binary", "quoted-printable", "base64"}
    for part in safe_parts:
        if part.is_multipart() or part.get_content_type().lower() not in {"text/plain", "text/html"}:
            continue
        transfer_encoding = part.get("Content-Transfer-Encoding", "7bit")
        if not isinstance(transfer_encoding, str) or transfer_encoding.strip().casefold() not in supported_transfer_encodings:
            # Do not mutate only one decodable branch of a multipart alternative.
            return {"action": "accept", **({"modifications": modifications} if modifications else {})}

    # Preflight every editable text representation before changing any of them.
    # A multipart/alternative must never end up with only one branch updated.
    plain = _plain_signature(html_signature)
    for part in safe_parts:
        if part.is_multipart() or part.get_content_type().lower() not in {"text/plain", "text/html"}:
            continue
        try:
            body = part.get_content()
        except (LookupError, UnicodeError, TypeError):
            return {"action": "accept", **({"modifications": modifications} if modifications else {})}
        if not isinstance(body, str):
            return {"action": "accept", **({"modifications": modifications} if modifications else {})}
        content_type = part.get_content_type().lower()
        if content_type == "text/html":
            if re.search(r"<!--\s*OpenSigWeave:INJECTED\s*-->", body, re.I):
                # A body marker means the message has already been processed.
                # Never update only the plain-text branch of multipart/alternative.
                return {"action": "accept", **({"modifications": modifications} if modifications else {})}
            candidate, _quote_detected = insert_signature(body, html_signature, html=True)
        else:
            if not plain:
                continue
            candidate, _quote_detected = insert_signature(body, plain, html=False)
        charset = part.get_content_charset() or "utf-8"
        try:
            candidate.encode(charset)
        except (LookupError, UnicodeError, TypeError):
            return {"action": "accept", **({"modifications": modifications} if modifications else {})}

    header_names = [item[0].casefold() for item in headers]
    if "x-opensigweave-injected" in header_names:
        return {"action": "accept", **({"modifications": modifications} if modifications else {})}

    is_reply = any(name in {"in-reply-to", "references"} for name in header_names)
    if is_reply and not bool(signature_data.get("inject_on_replies", False)):
        return {"action": "accept", **({"modifications": modifications} if modifications else {})}

    original_mime_headers = {name: [str(value) for value in parsed.get_all(name, [])]
                             for name in ("Content-Type", "Content-Transfer-Encoding", "MIME-Version")}
    changed = False
    for part in safe_parts:
        if part.is_multipart():
            continue
        content_type = part.get_content_type().lower()
        if content_type not in {"text/plain", "text/html"}:
            continue
        try:
            body = part.get_content()
        except (LookupError, UnicodeError, TypeError):
            return {"action": "accept", **({"modifications": modifications} if modifications else {})}
        if not isinstance(body, str):
            return {"action": "accept", **({"modifications": modifications} if modifications else {})}
        if content_type == "text/html":
            if re.search(r"<!--\s*OpenSigWeave:INJECTED\s*-->", body, re.I):
                continue
            body, _quote_detected = insert_signature(body, html_signature, html=True)
        else:
            if not plain:
                continue
            body, _quote_detected = insert_signature(body, plain, html=False)
        charset = part.get_content_charset() or "utf-8"
        transfer_encoding = part.get("Content-Transfer-Encoding", "quoted-printable")
        transfer_encoding = transfer_encoding.strip().casefold()
        cte = transfer_encoding if transfer_encoding in {"7bit", "8bit", "quoted-printable", "base64"} else "quoted-printable"
        try:
            part.set_content(body, subtype=content_type.split("/", 1)[1], charset=charset, cte=cte)
        except (LookupError, UnicodeError, TypeError, ValueError):
            # Never serialize a partially mutated multipart/alternative.
            return {"action": "accept", **({"modifications": modifications} if modifications else {})}
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
