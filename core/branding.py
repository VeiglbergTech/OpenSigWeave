"""Persistent application-branding helpers and safe image validation."""
from __future__ import annotations

import base64
import binascii
from pathlib import Path
import re
import zlib

from sqlalchemy.orm import Session

from models import BrandingSettingsDB

DEFAULT_APP_NAME = "OpenSigWeave"
DEFAULT_APP_COLOR = "#ff7043"

# Resolve bundled assets relative to the source tree, never to the working directory
# or to a path provided by a browser.
BRANDING_DIRECTORY = Path(__file__).resolve().parent.parent / "branding"
BUILTIN_LOGO_PATH = BRANDING_DIRECTORY / "logo.png"
BUILTIN_FAVICON_PATH = BRANDING_DIRECTORY / "favicon.ico"

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
PNG_MIME = "image/png"
ICO_MIMES = frozenset({"image/x-icon", "image/vnd.microsoft.icon"})


def branding_context(db: Session) -> dict:
    settings = db.get(BrandingSettingsDB, 1)
    custom_logo = bool(settings is not None and settings.logo_png)
    custom_dark_logo = bool(settings is not None and settings.dark_logo_png)
    custom_favicon = bool(settings is not None and settings.favicon_data)
    has_builtin_logo = BUILTIN_LOGO_PATH.is_file()
    return {
        "app_name": (settings.app_name if settings is not None else DEFAULT_APP_NAME) or DEFAULT_APP_NAME,
        "app_color": (settings.app_color if settings is not None else DEFAULT_APP_COLOR) or DEFAULT_APP_COLOR,
        "has_logo": custom_logo or custom_dark_logo or has_builtin_logo,
        "has_favicon": custom_favicon or BUILTIN_FAVICON_PATH.is_file() or has_builtin_logo,
        "large_image_uploads": bool(settings.large_image_uploads) if settings is not None else False,
        "image_upload_limit_mib": 12 if settings is not None and settings.large_image_uploads else 6,
        "has_custom_logo": custom_logo,
        "has_custom_dark_logo": custom_dark_logo,
        "has_custom_favicon": custom_favicon,
    }


def _validate_png(payload: bytes) -> None:
    """Validate PNG chunks, checksums and bounded decompression of scanlines."""
    if not payload.startswith(PNG_SIGNATURE):
        raise ValueError("Branding image is not a valid PNG")
    offset = len(PNG_SIGNATURE)
    has_ihdr = False
    has_idat = False
    has_iend = False
    idat_chunks = []
    expected_bytes = None
    while offset + 12 <= len(payload):
        size = int.from_bytes(payload[offset:offset + 4], "big")
        end = offset + 12 + size
        if end > len(payload):
            break
        kind = payload[offset + 4:offset + 8]
        data = payload[offset + 8:end - 4]
        checksum = int.from_bytes(payload[end - 4:end], "big")
        if not all(65 <= byte <= 90 or 97 <= byte <= 122 for byte in kind):
            break
        if zlib.crc32(kind + data) != checksum:
            break
        if not has_ihdr:
            if kind != b"IHDR" or size != 13:
                break
            width = int.from_bytes(data[0:4], "big")
            height = int.from_bytes(data[4:8], "big")
            depth, color, compression, filtering, interlace = data[8:]
            valid_depths = {
                0: {1, 2, 4, 8, 16},
                2: {8, 16},
                3: {1, 2, 4, 8},
                4: {8, 16},
                6: {8, 16},
            }
            if (not 1 <= width <= 16384 or not 1 <= height <= 16384
                    or depth not in valid_depths.get(color, set())
                    or compression != 0 or filtering != 0 or interlace not in (0, 1)):
                break
            if interlace == 0:
                channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}[color]
                expected_bytes = height * (1 + (width * channels * depth + 7) // 8)
                if expected_bytes > 32 * 1024 * 1024:
                    break
            has_ihdr = True
        elif kind == b"IHDR":
            break
        if kind == b"IDAT":
            has_idat = True
            idat_chunks.append(data)
        if kind == b"IEND":
            if size != 0 or not has_idat or end != len(payload):
                break
            has_iend = True
            break
        # Unknown critical PNG chunks are not safe to assume browser support for.
        if kind[0] <= 90 and kind not in {b"IHDR", b"PLTE", b"IDAT", b"IEND"}:
            break
        offset = end
    if not (has_ihdr and has_idat and has_iend):
        raise ValueError("Branding image is not a valid PNG")
    # CRCs alone do not prove that the IDAT stream contains valid PNG pixels.
    # Never inflate untrusted user input without a strict output limit.
    max_output = 32 * 1024 * 1024
    inflater = zlib.decompressobj()
    decompressed_size = 0
    try:
        for chunk in idat_chunks:
            inflated = inflater.decompress(chunk, max_output + 1 - decompressed_size)
            decompressed_size += len(inflated)
            if decompressed_size > max_output or inflater.unconsumed_tail or inflater.unused_data:
                raise ValueError("Branding image has an invalid PNG pixel stream")
        inflated = inflater.flush(max_output + 1 - decompressed_size)
        decompressed_size += len(inflated)
    except zlib.error as exc:
        raise ValueError("Branding image has an invalid PNG pixel stream") from exc
    if (decompressed_size > max_output or not inflater.eof or inflater.unused_data
            or (expected_bytes is not None and decompressed_size != expected_bytes)):
        raise ValueError("Branding image has an invalid PNG pixel stream")


def _validate_ico(payload: bytes) -> None:
    """Check ICO header, entry bounds and embedded PNG/DIB image structures."""
    if len(payload) < 22 or payload[:4] != b"\x00\x00\x01\x00":
        raise ValueError("Branding image is not a valid ICO file")
    count = int.from_bytes(payload[4:6], "little")
    directory_end = 6 + 16 * count
    if not 1 <= count <= 256 or directory_end > len(payload):
        raise ValueError("Branding image is not a valid ICO file")
    for index in range(count):
        entry = payload[6 + 16 * index: 22 + 16 * index]
        image_size = int.from_bytes(entry[8:12], "little")
        image_offset = int.from_bytes(entry[12:16], "little")
        if entry[3] != 0 or image_size < 8:
            raise ValueError("Branding image is not a valid ICO file")
        if image_offset < directory_end or image_size > len(payload) - image_offset:
            raise ValueError("Branding image is not a valid ICO file")
        image = payload[image_offset:image_offset + image_size]
        if image.startswith(PNG_SIGNATURE):
            _validate_png(image)
        else:
            if len(image) < 44:
                raise ValueError("Branding image is not a valid ICO file")
            dib_header_size = int.from_bytes(image[0:4], "little")
            bitmap_width = int.from_bytes(image[4:8], "little", signed=True)
            bitmap_height = int.from_bytes(image[8:12], "little", signed=True)
            bit_count = int.from_bytes(image[14:16], "little")
            if (dib_header_size < 40 or dib_header_size >= len(image)
                    or bitmap_width <= 0 or bitmap_height <= 0
                    or bit_count not in {1, 4, 8, 16, 24, 32}):
                raise ValueError("Branding image is not a valid ICO file")


def decode_branding_data_url(data_url: str, *, allowed_mimes: set[str], max_bytes: int) -> tuple[bytes, str]:
    if not isinstance(data_url, str) or not data_url.startswith("data:"):
        raise ValueError("Branding image must be a base64 data URL")
    metadata, separator, encoded = data_url.partition(",")
    if not separator or not re.fullmatch(r"data:image/[a-z.+-]+;base64", metadata, flags=re.IGNORECASE):
        raise ValueError("Branding image must use base64 encoding")
    mime = metadata[5:].split(";", 1)[0].casefold()
    if mime not in allowed_mimes:
        raise ValueError("Unsupported branding image type")
    # Avoid decoding unreasonable input sizes, even when invoked outside the HTTP layer.
    if len(encoded) > 4 * ((max_bytes + 2) // 3):
        raise ValueError("Branding image exceeds the allowed size")
    try:
        payload = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("Branding image contains invalid base64 data") from exc
    if not payload or len(payload) > max_bytes:
        raise ValueError("Branding image exceeds the allowed size")
    if mime == PNG_MIME:
        _validate_png(payload)
    elif mime in ICO_MIMES:
        _validate_ico(payload)
    return payload, mime
