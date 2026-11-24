"""Safe, bounded CID MIME embedding for all mail adapters."""
from __future__ import annotations

import base64
import copy
import binascii
import hashlib
import re
from email.message import EmailMessage

MAX_IMAGES = 6
MAX_SINGLE = 2 * 1024 * 1024
MAX_TOTAL = 4 * 1024 * 1024
_NAME = re.compile(r"^([a-f0-9]{64})\.(png|jpg|webp|gif)$")
_TYPES = {"png": "image/png", "jpg": "image/jpeg", "webp": "image/webp", "gif": "image/gif"}
_MAGIC = {"png": (b"\x89PNG\r\n\x1a\n",), "jpg": (b"\xff\xd8\xff",),
          "webp": (b"RIFF",), "gif": (b"GIF87a", b"GIF89a")}


class InlineImageError(ValueError):
    """Invalid inline payload; leave the message unchanged."""


def attach_inline_images(message: EmailMessage, assets: object) -> bool:
    """Validate all images before making a single MIME mutation."""
    if assets is None or assets == []:
        return False
    if not isinstance(assets, list) or not 1 <= len(assets) <= MAX_IMAGES:
        raise InlineImageError("Invalid number of managed images")
    if any(part.get_content_maintype() == "message" or part.get_content_type() in (
            "multipart/signed", "multipart/encrypted", "application/pgp-encrypted",
            "application/pkcs7-mime", "application/x-pkcs7-mime")
           for part in message.walk()):
        raise InlineImageError("Protected message must not be rewritten")

    existing = {str(p["Content-ID"]).strip().strip("<>").casefold()
                for p in message.walk() if p["Content-ID"]}
    html = []
    for part in message.walk():
        if (part.get_content_type() == "text/html" and
                part.get_content_disposition() != "attachment" and not part.get_filename()):
            try:
                source = part.get_content()
            except (LookupError, UnicodeError, TypeError) as exc:
                raise InlineImageError("Cannot decode HTML representation") from exc
            if not isinstance(source, str):
                raise InlineImageError("Invalid HTML representation")
            html.append(source.casefold())
    prepared, seen, total = [], set(), 0
    for asset in assets:
        if not isinstance(asset, dict):
            raise InlineImageError("Invalid managed image")
        filename, cid, media, encoded = (asset.get(k) for k in
            ("filename", "content_id", "content_type", "base64"))
        if not all(isinstance(v, str) for v in (filename, cid, media, encoded)):
            raise InlineImageError("Invalid managed image")
        match = _NAME.fullmatch(filename)
        if not match:
            raise InlineImageError("Invalid managed filename")
        digest, suffix = match.groups()
        if media != _TYPES[suffix] or cid != f"osw-{digest}@assets.opensigweave.invalid":
            raise InlineImageError("CID metadata mismatch")
        if cid.casefold() in seen or cid.casefold() in existing:
            raise InlineImageError("Duplicate MIME Content-ID")
        seen.add(cid.casefold())
        if len(encoded) > ((MAX_SINGLE + 2) // 3) * 4:
            raise InlineImageError("Image too large")
        try:
            data = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise InlineImageError("Invalid image encoding") from exc
        total += len(data)
        if (not data or len(data) > MAX_SINGLE or total > MAX_TOTAL or
                hashlib.sha256(data).hexdigest() != digest or
                not any(data.startswith(magic) for magic in _MAGIC[suffix])):
            raise InlineImageError("Image integrity, media type or size failed")
        if html and not any(("cid:" + cid).casefold() in content for content in html):
            raise InlineImageError("Unreferenced managed image")
        prepared.append((data, suffix, filename, cid))
    # A plain-text-only message may still receive a textual signature, but
    # should never acquire orphaned CID image attachments.
    if not html:
        return False
    # make_related() rejects existing multipart/mixed, multipart/related and
    # multipart/alternative roots. Preserve that complete original tree as
    # the first related child and retain all non-MIME envelope headers outside.
    inner = copy.deepcopy(message)
    for name in list(inner.keys()):
        if not name.casefold().startswith("content-"):
            del inner[name]
    for name in list(message.keys()):
        if name.casefold().startswith("content-"):
            del message[name]
    message.set_payload([inner])
    message.set_type("multipart/related")
    for data, suffix, filename, cid in prepared:
        message.add_related(data, maintype="image",
                            subtype="jpeg" if suffix == "jpg" else suffix,
                            cid=f"<{cid}>", filename=filename,
                            disposition="inline", cte="base64")
    return True
