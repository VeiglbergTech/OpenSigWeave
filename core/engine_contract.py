"""Framework-independent input contract for the shared signature engine."""

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class SignatureEngineRequest:
    """Normalized sender and selection inputs shared by every transport adapter."""

    sender_email: str
    mode: Optional[str] = None
    signature_name: Optional[str] = None
    signature_code: Optional[str] = None
    marker_requested: bool = False
    explicit_mode: bool = False


def normalize_engine_request(
    email: str,
    mode: Optional[str] = None,
    signature_name: Optional[str] = None,
    signature_code: Optional[str] = None,
) -> SignatureEngineRequest:
    """Normalize transport inputs without depending on FastAPI or HTTP objects.

    Invalid mode values intentionally become automatic selection inputs, matching
    the established API contract. Sender validation is strict and deterministic.
    """
    normalized_email = email.strip().casefold()
    if normalized_email.count("@") != 1 or any(ch.isspace() for ch in normalized_email):
        raise ValueError("Invalid sender email")

    mode_candidate = mode.strip().upper() if isinstance(mode, str) else ""
    explicit_mode = mode_candidate in {"AUTO", "PROFESSIONAL", "INTERNAL", "NONE"}
    marker_requested = isinstance(signature_name, str) or isinstance(signature_code, str)

    return SignatureEngineRequest(
        sender_email=normalized_email,
        mode=mode_candidate if explicit_mode else None,
        signature_name=signature_name,
        signature_code=signature_code,
        marker_requested=marker_requested,
        explicit_mode=explicit_mode,
    )
