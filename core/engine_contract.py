"""Framework-independent input contract for the shared signature engine."""

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class SignatureEngineRequest:
    """Normalized sender and selector inputs shared by every transport adapter."""

    sender_email: str
    signature_name: Optional[str] = None
    signature_code: Optional[str] = None
    marker_requested: bool = False


def normalize_engine_request(
    email: str,
    signature_name: Optional[str] = None,
    signature_code: Optional[str] = None,
) -> SignatureEngineRequest:
    """Normalize transport inputs without depending on FastAPI or HTTP objects."""
    normalized_email = email.strip().casefold()
    if normalized_email.count("@") != 1 or any(ch.isspace() for ch in normalized_email):
        raise ValueError("Invalid sender email")
    local_part, domain = normalized_email.split("@", 1)
    if not local_part or not domain:
        raise ValueError("Invalid sender email")

    marker_requested = isinstance(signature_name, str) or isinstance(signature_code, str)
    return SignatureEngineRequest(
        sender_email=normalized_email,
        signature_name=signature_name,
        signature_code=signature_code,
        marker_requested=marker_requested,
    )


@dataclass(frozen=True)
class SignatureEngineResult:
    """Transport-neutral result shared by HTTP and in-process adapters."""

    html: str
    inject_on_replies: bool
    trim_whitespace: bool
    strip_device_signatures: bool
    # Server-only source for the portal's rule-derived editor draft. Never exposed by to_payload().
    template_html: str = ""

    def to_payload(self) -> dict[str, object]:
        """Serialize the stable public engine response without transport types."""
        return {
            "html": self.html,
            "inject_on_replies": self.inject_on_replies,
            "trim_whitespace": self.trim_whitespace,
            "strip_device_signatures": self.strip_device_signatures,
        }


class SignatureEngineError(Exception):
    """Safe, transport-neutral engine failure with a stable machine-readable code."""

    def __init__(self, code: str, message: str):
        self.code = code
        self.safe_message = message
        super().__init__(message)
