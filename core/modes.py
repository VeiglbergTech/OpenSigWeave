"""Signature mode definitions and explicit-mode resolution."""
from enum import Enum


class SignatureMode(str, Enum):
    AUTO = "AUTO"
    PROFESSIONAL = "PROFESSIONAL"
    INTERNAL = "INTERNAL"
    NONE = "NONE"


def resolve_mode(requested_mode: str | None, default: SignatureMode = SignatureMode.AUTO) -> SignatureMode:
    """Resolve a requested mode, rejecting unknown values rather than guessing."""
    if requested_mode is None or not requested_mode.strip():
        return default
    try:
        return SignatureMode(requested_mode.strip().upper())
    except ValueError as exc:
        raise ValueError(f"Unsupported signature mode: {requested_mode}") from exc
