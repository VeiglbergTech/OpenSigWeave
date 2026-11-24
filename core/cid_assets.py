"""Build transport-neutral CID payloads using only validated managed images.

External URLs are never fetched while preparing an outgoing signature.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import re
import stat
from html.parser import HTMLParser
from urllib.parse import urlsplit

from core.engine_contract import SignatureEngineError
from signature_storage import MAX_SIGNATURE_IMAGE_BYTES, SignatureStorageError


_MAX_INLINE_ASSETS = 6
_MAX_TOTAL_BYTES = 4 * 1024 * 1024
_MANAGED_IMAGE = re.compile(r"^/signature-assets/([a-f0-9]{64}\.(?:png|jpg|webp|gif))$")
_IMG_SRC = re.compile(
    r"(?P<start><img\b[^>]*?\s+src\s*=\s*)(?P<quote>[\"'])(?P<src>[^\"']*)(?P=quote)",
    re.I,
)


class _RenderedImageAudit(HTMLParser):
    """Verify that every IMG has exactly one source from the shipped assets."""

    def __init__(self, allowed_sources: set[str]):
        super().__init__(convert_charrefs=True)
        self.allowed_sources = allowed_sources
        self.image_count = 0
        self.invalid = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "img":
            return
        self.image_count += 1
        sources = [value for name, value in attrs if name == "src"]
        if len(sources) != 1 or sources[0] not in self.allowed_sources:
            self.invalid = True


def _safe_managed_image(source: str) -> str:
    try:
        parts = urlsplit(source)
    except ValueError as exc:
        raise SignatureEngineError("INVALID_INLINE_IMAGE", "Invalid signature image source") from exc
    if parts.scheme.lower() not in {"https", "http"} or not parts.netloc or parts.username or parts.password:
        raise SignatureEngineError("UNMANAGED_INLINE_IMAGE", "Signature image must be a managed OpenSigWeave asset")
    if parts.query or parts.fragment:
        raise SignatureEngineError("UNMANAGED_INLINE_IMAGE", "Signature image URL must not contain parameters")
    match = _MANAGED_IMAGE.fullmatch(parts.path)
    if not match:
        raise SignatureEngineError("UNMANAGED_INLINE_IMAGE", "Import external signature images before sending")
    return match.group(1)


def _read_managed_image(file_store, filename: str) -> bytes:
    try:
        path = file_store.image_path(filename)
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_SIGNATURE_IMAGE_BYTES:
                raise SignatureStorageError("Managed image file is unavailable")
            with os.fdopen(fd, "rb", closefd=False) as handle:
                data = handle.read(MAX_SIGNATURE_IMAGE_BYTES + 1)
        finally:
            os.close(fd)
        if not data or len(data) > MAX_SIGNATURE_IMAGE_BYTES:
            raise SignatureStorageError("Managed image file is unavailable")
        # The filename is content-addressed. Never send substituted or corrupted
        # bytes under the original CID, even when they still have valid image magic.
        expected_digest = filename.split(".", 1)[0]
        if not hmac.compare_digest(hashlib.sha256(data).hexdigest(), expected_digest):
            raise SignatureStorageError("Managed image integrity check failed")
        return data
    except (OSError, SignatureStorageError) as exc:
        raise SignatureEngineError(
            "INLINE_IMAGE_ASSET_UNAVAILABLE", "Managed signature image is unavailable"
        ) from exc


def build_cid_payload(html: str, file_store) -> tuple[str, list[dict[str, str]]]:
    """Return image CID HTML and safe, bounded base64 MIME attachment metadata."""
    if not isinstance(html, str):
        raise TypeError("Signature HTML must be a string")
    if "<img" not in html.lower():
        return html, []

    assets = {}
    total_bytes = 0

    def substitute(match: re.Match[str]) -> str:
        nonlocal total_bytes
        filename = _safe_managed_image(match.group("src"))
        if filename not in assets:
            if len(assets) >= _MAX_INLINE_ASSETS:
                raise SignatureEngineError("INLINE_IMAGE_LIMIT", "Too many signature images")
            data = _read_managed_image(file_store, filename)
            total_bytes += len(data)
            if total_bytes > _MAX_TOTAL_BYTES:
                raise SignatureEngineError("INLINE_IMAGE_LIMIT", "Signature images exceed total size limit")
            digest, extension = filename.rsplit(".", 1)
            mime_type = file_store.image_media_type(filename)
            if mime_type not in {"image/png", "image/jpeg", "image/webp", "image/gif"}:
                raise SignatureEngineError("INVALID_INLINE_IMAGE", "Unsupported managed signature image")
            assets[filename] = {
                "filename": filename,
                "content_type": mime_type,
                # Digest-derived identity keeps exactly-once signatures recognizable
                # when clients re-submit the same original message.
                "content_id": f"osw-{digest}@assets.opensigweave.invalid",
                "base64": base64.b64encode(data).decode("ascii"),
            }
        return (
            match.group("start") + match.group("quote")
            + "cid:" + assets[filename]["content_id"] + match.group("quote")
        )

    rendered = _IMG_SRC.sub(substitute, html)
    # A CID elsewhere in the HTML must never mask an unmanaged IMG source.
    # Parse each image's actual attributes, including unquoted/duplicate src.
    audit = _RenderedImageAudit({
        "cid:" + asset["content_id"] for asset in assets.values()
    })
    audit.feed(rendered)
    audit.close()
    if audit.invalid or audit.image_count != len(re.findall(r"<img\b", rendered, re.I)):
        raise SignatureEngineError("UNMANAGED_INLINE_IMAGE", "Signature image source cannot be embedded")
    return rendered, list(assets.values())
