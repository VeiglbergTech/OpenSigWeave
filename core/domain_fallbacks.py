"""Domain-local text fallbacks for signature rendering only.

These values never enter identity cache, rule evaluation, provider claims or
immutable rotation principals. Email and domain are trusted request values.
"""
from collections.abc import Mapping
from typing import Any

from identity.attributes import IdentityAttributes

FALLBACK_FIELDS = (
    "first_name", "last_name", "title", "phone", "company",
    "department", "location", "mobile", "website",
)
FALLBACK_MAX_LENGTH = 256

# Additional provider attributes are optional; no configured mapping is needed.
_PROVIDER_ALIASES = {
    "company": ("company", "organization", "o"),
    "department": ("department", "ou"),
    "location": ("location", "physicaldeliveryofficename", "l", "city"),
    "mobile": ("mobile", "mobilephone"),
    "website": ("website", "wwwhomepage", "url"),
}


def validate_fallback_values(values: Any) -> dict[str, str]:
    if not isinstance(values, dict):
        raise ValueError("Fallback values must be an object")
    unknown = set(values) - set(FALLBACK_FIELDS)
    if unknown:
        raise ValueError("Unsupported fallback fields: " + ", ".join(sorted(str(k) for k in unknown)))
    cleaned: dict[str, str] = {}
    for key, value in values.items():
        if not isinstance(value, str) or len(value) > FALLBACK_MAX_LENGTH:
            raise ValueError(f"Fallback {key} must be text of at most {FALLBACK_MAX_LENGTH} characters")
        # Empty values represent an intentionally unconfigured fallback.
        stripped = value.strip()
        if stripped:
            cleaned[key] = stripped
    return cleaned


def _text(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (list, tuple)) and value:
        return _text(value[0])
    return ""


def _provider_value(identity: IdentityAttributes, field: str) -> str:
    direct = _text(getattr(identity, field, ""))
    if direct:
        return direct
    aliases = (field,) + _PROVIDER_ALIASES.get(field, ())
    for source in (identity.attributes, identity.claims):
        if not isinstance(source, Mapping):
            continue
        lowered = {str(key).casefold(): value for key, value in source.items()}
        for alias in aliases:
            value = _text(lowered.get(alias.casefold()))
            if value:
                return value
    return ""


def signature_profile_values(
    identity: IdentityAttributes,
    sender_email: str,
    domain_name: str,
    fallback_values: Mapping[str, str] | None,
) -> dict[str, str]:
    """Apply the provider > domain fallback > empty precedence at render time."""
    fallbacks = fallback_values if isinstance(fallback_values, Mapping) else {}
    values = {}
    for field in FALLBACK_FIELDS:
        values[field] = _provider_value(identity, field) or _text(fallbacks.get(field))
    # Never replace sender email or the actual request domain.
    values["email"] = sender_email
    values["domain_name"] = domain_name
    return values
