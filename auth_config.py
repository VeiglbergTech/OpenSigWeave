"""Authentication configuration and local administrator password hashing."""
import base64
import hashlib
import hmac
import os
import re
import secrets

_SCRYPT_N = 1 << 14
_SCRYPT_R = 8
_SCRYPT_P = 1
_SCRYPT_DKLEN = 64
_EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s.]+(?:\.[^@\s.]+)+$")


def oidc_enabled_from_env(value: str | None = None) -> bool:
    raw = os.environ.get("OIDC_ENABLED", "true") if value is None else value
    normalized = str(raw).strip().casefold()
    if normalized in {"true", "1", "yes", "on"}:
        return True
    if normalized in {"false", "0", "no", "off"}:
        return False
    raise RuntimeError("OIDC_ENABLED must be true or false")


def is_valid_email(value: str) -> bool:
    return isinstance(value, str) and len(value) <= 254 and bool(_EMAIL_RE.fullmatch(value))


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _b64decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def hash_local_admin_password(password: str) -> str:
    if not isinstance(password, str) or len(password) < 12 or len(password) > 1024:
        raise ValueError("Local administrator password must contain 12 to 1024 characters")
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(
        password.encode("utf-8"), salt=salt, n=_SCRYPT_N, r=_SCRYPT_R,
        p=_SCRYPT_P, dklen=_SCRYPT_DKLEN, maxmem=64 * 1024 * 1024,
    )
    return f"scrypt${_SCRYPT_N}${_SCRYPT_R}${_SCRYPT_P}${_b64encode(salt)}${_b64encode(digest)}"


def _parse_password_hash(encoded: str):
    try:
        algorithm, n, r, p, salt_text, digest_text = encoded.split("$")
        n, r, p = int(n), int(r), int(p)
        salt, expected = _b64decode(salt_text), _b64decode(digest_text)
    except (AttributeError, TypeError, ValueError):
        return None
    if algorithm != "scrypt" or n != _SCRYPT_N or r != _SCRYPT_R or p != _SCRYPT_P:
        return None
    if not 16 <= len(salt) <= 32 or len(expected) != _SCRYPT_DKLEN:
        return None
    return n, r, p, salt, expected


def is_valid_password_hash(encoded: str) -> bool:
    return _parse_password_hash(encoded) is not None


def verify_local_admin_password(password: str, encoded: str) -> bool:
    parsed = _parse_password_hash(encoded)
    if parsed is None or not isinstance(password, str) or len(password) > 1024:
        return False
    n, r, p, salt, expected = parsed
    try:
        actual = hashlib.scrypt(
            password.encode("utf-8"), salt=salt, n=n, r=r, p=p,
            dklen=len(expected), maxmem=64 * 1024 * 1024,
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(actual, expected)
