"""Database-backed, multi-worker rate limiting for sensitive endpoints."""
import hashlib
import hmac
import ipaddress
import os
import time

from fastapi import Request
from fastapi.responses import JSONResponse
from sqlalchemy import case, delete
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from starlette.middleware.base import BaseHTTPMiddleware

from database import engine
from models import RateLimitBucketDB


def _trusted_proxy_networks():
    raw = os.environ.get("TRUSTED_PROXY_CIDRS", "")
    try:
        return tuple(
            ipaddress.ip_network(item.strip(), strict=False)
            for item in raw.split(",")
            if item.strip()
        )
    except ValueError as exc:
        raise RuntimeError("TRUSTED_PROXY_CIDRS contains an invalid network") from exc


TRUSTED_PROXY_NETWORKS = _trusted_proxy_networks()


def _rate_limit_hash_key() -> bytes:
    """Derive a separate pseudonymization key from the mandatory session secret."""
    secret = os.environ.get("SECRET_KEY", "")
    if len(secret) < 32 or secret.lower().startswith(("replace_", "changeme", "your_")):
        raise RuntimeError("SECRET_KEY must be configured before rate limiting")
    return hmac.new(
        secret.encode("utf-8"),
        b"OpenSigWeave/rate-limit-bucket/v1",
        hashlib.sha256,
    ).digest()


def client_identity(request: Request) -> str:
    """Resolve the client address without trusting arbitrary forwarding headers."""
    peer = request.client.host if request.client else ""
    try:
        peer_ip = ipaddress.ip_address(peer)
    except ValueError:
        return peer or "unknown"

    if not any(peer_ip in network for network in TRUSTED_PROXY_NETWORKS):
        return peer_ip.compressed

    forwarded = request.headers.get("x-forwarded-for", "")
    if not forwarded:
        return peer_ip.compressed
    try:
        chain = [ipaddress.ip_address(item.strip()) for item in forwarded.split(",")]
    except ValueError:
        return peer_ip.compressed

    current = peer_ip
    for hop in reversed(chain):
        if not any(current in network for network in TRUSTED_PROXY_NETWORKS):
            break
        current = hop
    return current.compressed


def consume_rate_limit(
    bucket: str,
    limit: int,
    window_seconds: int,
    *,
    now=None,
    bind=None,
    hash_key: bytes | None = None,
) -> int:
    """Atomically increment a fixed-window counter shared by all application replicas.

    Only a keyed HMAC of the bucket identifier is persisted. The key is derived
    from SESSION_SECRET with domain separation; raw IP addresses and engine
    credentials are never written to the database.
    """
    if limit < 1 or window_seconds < 1:
        raise ValueError("Rate-limit values must be positive")
    timestamp = int(time.time() if now is None else now)
    window_start = timestamp - timestamp % window_seconds
    key = _rate_limit_hash_key() if hash_key is None else hash_key
    if not isinstance(key, bytes) or len(key) < 16:
        raise ValueError("Rate-limit hash key must contain at least 16 bytes")
    digest = hmac.new(key, bucket.encode("utf-8"), hashlib.sha256).hexdigest()
    table = RateLimitBucketDB.__table__
    database = bind or engine

    if database.dialect.name == "postgresql":
        insert_statement = postgres_insert(table)
    elif database.dialect.name == "sqlite":
        insert_statement = sqlite_insert(table)
    else:
        raise RuntimeError("Rate limiting requires PostgreSQL or SQLite")

    statement = insert_statement.values(
        bucket_key=digest,
        window_start=window_start,
        request_count=1,
    ).on_conflict_do_update(
        index_elements=[table.c.bucket_key],
        set_={
            "window_start": window_start,
            "request_count": case(
                (table.c.window_start == window_start, table.c.request_count + 1),
                else_=1,
            ),
        },
    ).returning(table.c.request_count)

    with database.begin() as connection:
        count = connection.execute(statement).scalar_one()
        # Opportunistically bound storage growth caused by one-off source addresses.
        if digest.startswith("00"):
            connection.execute(
                delete(table).where(table.c.window_start < window_start - 86400)
            )
    return int(count)


def _policy_for_path(path: str):
    """Return the fixed-window policy for a protected route."""
    if path == "/login":
        return ("login", int(os.environ.get("LOGIN_RATE_LIMIT", "10")), 300)
    if path == "/auth" or path.startswith("/auth/"):
        # Managed OIDC callbacks use /auth/{provider_id}; protect them just
        # like the legacy /auth callback against repeated state/token probes.
        return ("auth-callback", int(os.environ.get("AUTH_CALLBACK_RATE_LIMIT", "20")), 300)
    if path == "/api/admin/exports":
        return ("admin-export", int(os.environ.get("ADMIN_EXPORT_RATE_LIMIT", "5")), 60)
    if path.startswith("/api/signature/"):
        return ("engine", int(os.environ.get("ENGINE_RATE_LIMIT", "1800")), 60)
    return None


class RateLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        identity = client_identity(request)
        policy = _policy_for_path(path)

        if policy is None:
            return await call_next(request)

        name, limit, window = policy
        try:
            count = consume_rate_limit(f"{name}:{identity}", limit, window)
        except Exception:
            # Fail closed: a database outage must not silently disable protection.
            return JSONResponse(
                {"detail": "Request protection is temporarily unavailable"},
                status_code=503,
                headers={"Cache-Control": "no-store", "Retry-After": "5"},
            )

        if count > limit:
            retry_after = window - int(time.time()) % window
            return JSONResponse(
                {"detail": "Too many requests"},
                status_code=429,
                headers={
                    "Cache-Control": "no-store",
                    "Retry-After": str(retry_after),
                },
            )
        return await call_next(request)
