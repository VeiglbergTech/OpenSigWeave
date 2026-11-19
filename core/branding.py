"""Persistent application-branding helpers."""
from __future__ import annotations

import base64
import binascii
import os

from sqlalchemy.orm import Session

from models import BrandingSettingsDB

DEFAULT_APP_NAME = "OpenSigWeave"
DEFAULT_APP_COLOR = "#ff7043"


def branding_context(db: Session) -> dict:
    settings = db.get(BrandingSettingsDB, 1)
    custom_logo = bool(settings is not None and settings.logo_png)
    custom_favicon = bool(settings is not None and settings.favicon_data)
    return {
        "app_name": (settings.app_name if settings is not None else DEFAULT_APP_NAME) or DEFAULT_APP_NAME,
        "app_color": (settings.app_color if settings is not None else DEFAULT_APP_COLOR) or DEFAULT_APP_COLOR,
        "has_logo": custom_logo or os.path.isfile("branding/logo.png"),
        "has_favicon": custom_favicon or os.path.isfile("branding/favicon.ico") or os.path.isfile("branding/logo.png"),
        "has_custom_logo": custom_logo,
        "has_custom_favicon": custom_favicon,
    }


def decode_branding_data_url(data_url: str, *, allowed_mimes: set[str], max_bytes: int) -> tuple[bytes, str]:
    if not isinstance(data_url, str) or not data_url.startswith("data:"):
        raise ValueError("Branding image must be a base64 data URL")
    metadata, separator, encoded = data_url.partition(",")
    if not separator or ";base64" not in metadata.casefold():
        raise ValueError("Branding image must use base64 encoding")
    mime = metadata[5:].split(";", 1)[0].strip().casefold()
    if mime not in allowed_mimes:
        raise ValueError("Unsupported branding image type")
    try:
        payload = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("Branding image contains invalid base64 data") from exc
    if not payload or len(payload) > max_bytes:
        raise ValueError("Branding image exceeds the allowed size")
    if mime == "image/png" and not payload.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ValueError("Branding image is not a valid PNG")
    if mime in {"image/x-icon", "image/vnd.microsoft.icon"} and not payload.startswith(b"\x00\x00\x01\x00"):
        raise ValueError("Branding image is not a valid ICO file")
    return payload, mime
