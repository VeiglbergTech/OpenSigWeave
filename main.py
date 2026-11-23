import os
import asyncio
import logging
from anyio import to_thread
import uuid
import json
import hmac
import hashlib
import secrets
import re
from datetime import datetime, timezone
from dataclasses import replace
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit
from fastapi import FastAPI, Depends, HTTPException, Request, Header, Response
from fastapi.responses import FileResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, text
from sqlalchemy.exc import SQLAlchemyError, TimeoutError as DatabasePoolTimeout
from sqlalchemy.orm import Session
from pydantic import BaseModel, Field, model_validator
from typing import List, Optional, Literal

from dotenv import load_dotenv
from starlette.middleware.sessions import SessionMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.responses import JSONResponse
from authlib.integrations.starlette_client import OAuth
from jinja2 import TemplateError

load_dotenv()

# Database infrastructure is isolated from the application models and routes.
from database import get_db, SessionLocal
from models import DomainDB, UserOverrideDB, SignatureDB, SignatureDomainDB, SignatureVersionDB, SignatureRuleDB, SignatureRuleConditionDB, IdentityCacheDB, SignatureGroupDB, SignatureGroupDomainDB, SignatureGroupMemberDB, GroupSelectionStateDB, SignatureScheduleDB, BrandingSettingsDB
from rate_limiter import RateLimitMiddleware

from signature_renderer import render_signature_template
from identity.providers.authentik import AuthentikAttributeProvider
from identity.providers.base import IdentityProviderUnavailable
from identity.attributes import IdentityAttributes
from identity.registry import IdentityProviderRegistry, ProviderConfigError
from models import IdentityProviderDB
from core.group_selection_repository import get_group_preview_signature
from core.slug import slugify
from core.engine_contract import (
    SignatureEngineError,
    SignatureEngineRequest,
    SignatureEngineResult,
    normalize_engine_request,
)
from core.engine_service import SignatureEngineService
from core.cid_assets import build_cid_payload
from core.branding import (
    DEFAULT_APP_COLOR,
    DEFAULT_APP_NAME,
    branding_context,
    decode_branding_data_url,
    BUILTIN_LOGO_PATH,
    BUILTIN_FAVICON_PATH,
)
from signature_catalog import SignatureNotFoundError, SignatureVersionNotFoundError, add_signature_version, create_signature, restore_signature_version, update_signature
from signature_storage import SignatureFileStore, SignatureStorageError, MAX_SIGNATURE_IMAGE_BYTES
from signature_image_import import import_image_from_url, SignatureImageImportError
from admin_export import AdminExportError, build_admin_export, MAX_EXPORT_BYTES
from admin_import import AdminImportError, inspect_admin_import, apply_admin_import
from backup_control_client import BackupControlError, backup_request, backup_download
from backup_locations import (
    BackupLocationError, apply_location, ensure_local_directory,
    public_location, test_location,
)
from models import BackupLocationDB
from sqlalchemy.exc import IntegrityError
from request_body_limit import PathAwareRequestBodyLimitMiddleware
from signature_sanitizer import sanitize_signature_html
from adapters.stalwart import build_stalwart_hook_response
from auth_config import (
    is_valid_email, is_valid_password_hash,
    verify_local_admin_password,
)
logger = logging.getLogger(__name__)

LOCAL_ADMIN_EMAIL = os.environ.get("LOCAL_ADMIN_EMAIL", "").strip().casefold()
LOCAL_ADMIN_PASSWORD_HASH = os.environ.get("LOCAL_ADMIN_PASSWORD_HASH", "")
LOCAL_ADMIN_CONFIGURED = bool(LOCAL_ADMIN_EMAIL or LOCAL_ADMIN_PASSWORD_HASH)

identity_provider_registry = IdentityProviderRegistry(os.environ.get("IDP_CONFIG_DIR", ".idp"))

# Temporary profile-attribute bridge for one-time migration of older Authentik setups.
attribute_provider = AuthentikAttributeProvider(
    base_url=os.environ.get("AUTHENTIK_URL", ""),
    api_token=os.environ.get("AUTHENTIK_API_TOKEN", ""),
)

async def get_identity_attributes(db: Session, email: str, provider_id: str | None = None) -> IdentityAttributes:
    normalized_email = email.strip().casefold()
    if provider_id:
        meta = db.get(IdentityProviderDB, provider_id)
        if meta is None or not meta.is_enabled:
            return IdentityAttributes()
        cache_key = f"{provider_id}:{normalized_email}"
        if meta.provider_type == "oidc":
            cached = db.get(IdentityCacheDB, cache_key)
            if cached is None or cached.updated_at is None:
                return IdentityAttributes()
            cache_updated = cached.updated_at
            if cache_updated.tzinfo is None:
                cache_updated = cache_updated.replace(tzinfo=timezone.utc)
            # Generic OIDC providers do not expose a portable account-status API.
            # Expire cached claims after 24 hours so removed memberships do not live forever.
            if (datetime.now(timezone.utc) - cache_updated).total_seconds() > 86400:
                return IdentityAttributes()
            return IdentityAttributes(
                principal_id=cached.principal_id or "",
                first_name=cached.first_name or "",
                last_name=cached.last_name or "",
                title=cached.title or "",
                phone=cached.phone or "",
                groups=frozenset(cached.groups or []),
                claims=cached.claims or {},
                attributes=cached.attributes or {},
            )
    else:
        # Profile-only compatibility bridge; it never authenticates users.
        provider = attribute_provider
        cache_key = normalized_email
        if provider is None:
            return IdentityAttributes()
    try:
        if provider_id:
            provider = identity_provider_registry.get(provider_id)
            if provider is None:
                return IdentityAttributes()
        fresh = await provider.get_attributes(normalized_email)
        if not isinstance(fresh, IdentityAttributes):
            raise IdentityProviderUnavailable("Unsupported identity result")
        if provider_id and fresh.principal_id:
            fresh = replace(fresh, principal_id=f"{provider_id}:{fresh.principal_id}")
        else:
            fresh = replace(fresh, principal_id="")
        cached = db.get(IdentityCacheDB, cache_key)
        values = {
            "principal_id": fresh.principal_id or None,
            "first_name": fresh.first_name[:256],
            "last_name": fresh.last_name[:256],
            "title": fresh.title[:256],
            "phone": fresh.phone[:256],
            "groups": sorted(fresh.groups),
            "claims": dict(fresh.claims),
            "attributes": dict(fresh.attributes),
        }
        if cached is None:
            db.add(IdentityCacheDB(email=cache_key, **values))
        else:
            for key, value in values.items():
                setattr(cached, key, value)
        db.commit()
        return fresh
    except Exception as exc:
        db.rollback()
        logger.warning("Identity provider %s unavailable; using provider-specific cache (%s)", provider_id or "legacy-profile", type(exc).__name__)
        cached = db.get(IdentityCacheDB, cache_key)
        if cached is None:
            return IdentityAttributes()
        return IdentityAttributes(
            principal_id=cached.principal_id or "",
            first_name=cached.first_name or "",
            last_name=cached.last_name or "",
            title=cached.title or "",
            phone=cached.phone or "",
            groups=frozenset(cached.groups or []),
            claims=cached.claims or {},
            attributes=cached.attributes or {},
        )

signature_file_store = SignatureFileStore(
    os.environ.get("SIGNATURE_STORAGE_PATH", "./signatures")
)


# Pydantic Models
class DomainCreate(BaseModel):
    domain_name: str = Field(min_length=3, max_length=253)
    is_active: bool = True
    identity_provider_id: Optional[str] = Field(default=None, max_length=64)
    authentication_provider_id: Optional[str] = Field(default=None, max_length=64)

class DomainUpdate(BaseModel):
    is_active: bool
    allow_overrides: bool
    inject_on_replies: bool
    trim_whitespace: bool
    strip_device_signatures: bool
    template_html: Optional[str] = Field(default=None, max_length=65536)  # Retired, accepted for old clients only
    identity_provider_id: Optional[str] = Field(default=None, max_length=64)
    authentication_provider_id: Optional[str] = Field(default=None, max_length=64)

class OverrideUpdate(BaseModel):
    html_content: str = Field(max_length=65536)

class AdminOverrideSave(BaseModel):
    user_email: str = Field(min_length=3, max_length=254)
    html_content: str = Field(max_length=65536)
    is_disabled: bool = False

class NoLegacySignatureSelectionRequest(BaseModel):
    """Reject retired selectors without changing handling of unrelated extra fields."""

    @model_validator(mode="before")
    @classmethod
    def reject_retired_selectors(cls, value):
        if isinstance(value, dict) and any(
            field in value for field in ("mode", "pool_name", "variants", "variant_id", "weight")
        ):
            raise ValueError("Legacy signature mode and variant fields are no longer supported")
        return value


class SignatureCreateRequest(NoLegacySignatureSelectionRequest):
    name: str = Field(min_length=1, max_length=255)
    domain_id: Optional[int] = None
    domain_ids: Optional[List[int]] = None
    html_content: str = Field(min_length=1, max_length=65536)
    is_active: bool = True

class SignatureUpdateRequest(NoLegacySignatureSelectionRequest):
    name: str = Field(min_length=1, max_length=255)
    domain_id: Optional[int] = None
    domain_ids: Optional[List[int]] = None
    is_active: bool = True

class SignatureVersionCreateRequest(NoLegacySignatureSelectionRequest):
    html_content: str = Field(min_length=1, max_length=65536)

class SignatureRuleConditionRequest(BaseModel):
    kind: Literal[
        "sender_equals", "sender_not_equals",
        "domain_equals", "domain_not_equals",
        "group_contains",
        "claim_equals", "claim_not_equals",
        "attribute_equals", "attribute_not_equals",
    ]
    key: str = Field(default="", max_length=255)
    value: str = Field(min_length=1, max_length=4096)

class SignatureGroupCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    slug: Optional[str] = Field(default=None, min_length=1, max_length=64)
    domain_ids: List[int] = Field(default_factory=list, max_length=100)
    is_active: bool = True


class SignatureGroupMemberRequest(BaseModel):
    signature_id: str = Field(min_length=1, max_length=32)
    weight: int = Field(default=1, ge=1, le=1000)
    is_enabled: bool = True


class SignatureRuleCreateRequest(NoLegacySignatureSelectionRequest):
    name: str = Field(min_length=1, max_length=255)
    priority: int = Field(default=0, ge=-100000, le=100000)
    action_mode: Literal["SIGNATURE", "GROUP", "NONE"] = "SIGNATURE"
    signature_id: Optional[str] = None
    group_id: Optional[str] = None
    is_enabled: bool = True
    conditions: List[SignatureRuleConditionRequest] = Field(default_factory=list, max_length=64)

class BackupSettingsUpdate(BaseModel):
    directory: str = Field(min_length=1, max_length=255)
    automatic: bool = False
    interval_hours: int = Field(ge=1, le=720)
    keep: int = Field(ge=1, le=365)


class BackupLocationInput(BaseModel):
    name: str
    type: Literal["local", "smb"]
    enabled: bool = True
    server: str = ""
    share: str = ""
    directory: str = ""
    username: str = ""
    password: Optional[str] = None
    domain: str = ""


class BackupActionRequest(BaseModel):
    confirm: str = Field(min_length=1, max_length=64)


class BrandingUpdateRequest(BaseModel):
    app_name: str = Field(min_length=1, max_length=120)
    app_color: str = Field(min_length=7, max_length=7)
    logo_data_url: Optional[str] = Field(default=None, max_length=900000)
    dark_logo_data_url: Optional[str] = Field(default=None, max_length=900000)
    favicon_data_url: Optional[str] = Field(default=None, max_length=500000)
    reset_logo: bool = False
    reset_dark_logo: bool = False
    reset_favicon: bool = False


class SignatureScheduleRequest(NoLegacySignatureSelectionRequest):
    name: str = Field(min_length=1, max_length=255)
    target_type: Literal["SIGNATURE", "GROUP"]
    signature_id: Optional[str] = Field(default=None, max_length=32)
    group_id: Optional[str] = Field(default=None, max_length=32)
    domain_id: Optional[int] = None
    starts_at: datetime
    ends_at: datetime
    priority: int = Field(default=0, ge=-100000, le=100000)
    is_enabled: bool = True

# 3. FastAPI App Initialization
app = FastAPI(title="OpenSigWeave API", version="1.0.0", docs_url=None, redoc_url=None, openapi_url=None)

@app.on_event("startup")
def migrate_legacy_identity_provider_configuration():
    """Move the previous single-provider .env configuration into the managed registry."""
    with SessionLocal() as db:
        if db.query(IdentityProviderDB).count():
            return
        legacy_type = os.environ.get("IDENTITY_PROVIDER", "authentik" if os.environ.get("AUTHENTIK_API_TOKEN") else "none").strip().casefold()
        settings = {}
        if legacy_type == "authentik":
            settings = {
                "AUTHENTIK_URL": os.environ.get("AUTHENTIK_URL", ""),
                "AUTHENTIK_API_TOKEN": os.environ.get("AUTHENTIK_API_TOKEN", ""),
                "AUTHENTIK_TIMEOUT": os.environ.get("AUTHENTIK_TIMEOUT", "5"),
            }
            if not settings["AUTHENTIK_API_TOKEN"]:
                return
        elif legacy_type == "ldap":
            settings = {
                "LDAP_URL": os.environ.get("LDAP_URL", ""),
                "LDAP_BIND_DN": os.environ.get("LDAP_BIND_DN", ""),
                "LDAP_BIND_PASSWORD": os.environ.get("LDAP_BIND_PASSWORD", ""),
                "LDAP_BASE_DN": os.environ.get("LDAP_BASE_DN", ""),
                "LDAP_USER_FILTER": os.environ.get("LDAP_USER_FILTER", "(&(objectCategory=person)(objectClass=user)(mail={email}))"),
                "LDAP_TIMEOUT": os.environ.get("LDAP_TIMEOUT", "5"),
                "LDAP_CA_CERT_FILE": os.environ.get("LDAP_CA_CERT_FILE", ""),
            }
            if not all(settings[key] for key in ("LDAP_URL", "LDAP_BIND_DN", "LDAP_BIND_PASSWORD", "LDAP_BASE_DN")):
                return
        else:
            return
        try:
            item = identity_provider_registry.save("legacy", "Legacy " + legacy_type.title(), legacy_type, settings, True)
            db.add(IdentityProviderDB(id=item["id"], display_name=item["display_name"], provider_type=item["provider_type"], is_enabled=True))
            for domain in db.query(DomainDB).filter(DomainDB.identity_provider_id.is_(None)).all():
                domain.identity_provider_id = item["id"]
            db.commit()
            logger.info("Migrated legacy %s attribute provider into .idp/legacy/.env", legacy_type)
        except Exception:
            db.rollback()
            logger.exception("Could not migrate legacy identity provider configuration; administrator setup remains available")


@app.on_event("startup")
def disable_legacy_profile_attribute_fallback():
    """After migration, profile lookups use domain-assigned providers only."""
    global attribute_provider
    attribute_provider = None

session_secret = os.environ.get("SECRET_KEY", "")
if len(session_secret) < 32 or session_secret.lower() in {"changeme", "your_secure_random_string_here", "fallback-secret", "replace_with_random_secret_at_least_32_characters"}:
    raise RuntimeError("SECRET_KEY must be configured with at least 32 non-placeholder characters")
ENGINE_API_KEY = os.environ.get("ENGINE_API_KEY", "")
if len(ENGINE_API_KEY) < 32 or ENGINE_API_KEY.lower().startswith(("replace_", "changeme", "your_")):
    raise RuntimeError("ENGINE_API_KEY must be configured with a generated value of at least 32 characters")

# Optional, fully privileged management credential for unattended API clients.
# This is deliberately distinct from the mail-adapter engine credential.
MANAGEMENT_API_KEY = os.environ.get("MANAGEMENT_API_KEY", "")
if MANAGEMENT_API_KEY and (
    len(MANAGEMENT_API_KEY) < 32
    or MANAGEMENT_API_KEY.lower().startswith(("replace_", "changeme", "your_"))
    or MANAGEMENT_API_KEY in {ENGINE_API_KEY, session_secret}
):
    raise RuntimeError("MANAGEMENT_API_KEY must be an independent, generated value of at least 32 characters")


def is_management_api_path(path: str) -> bool:
    """Limit machine credentials to existing administrator REST endpoints."""
    return path.startswith("/api/admin/") or path.startswith("/domains/")


def has_valid_management_key(request: Request) -> bool:
    """Authenticate only explicit administrator API requests, never browser/engine routes."""
    return (
        isinstance(request, Request)
        and bool(MANAGEMENT_API_KEY)
        and is_management_api_path(request.url.path)
        and hmac.compare_digest(request.headers.get("X-Management-Key", ""), MANAGEMENT_API_KEY)
    )


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault(
            "Content-Security-Policy-Report-Only",
            "default-src 'self'; base-uri 'self'; object-src 'none'; frame-ancestors 'none'; "
            "form-action 'self'; script-src 'self' https://cdn.jsdelivr.net; script-src-attr 'none'; "
            "style-src 'self'; style-src-attr 'none'; img-src 'self' data: https:; "
            "font-src 'self' data:; connect-src 'self'; frame-src 'self'"
        )
        response.headers.setdefault("Strict-Transport-Security", "max-age=31536000")
        response.headers.setdefault("X-Permitted-Cross-Domain-Policies", "none")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        response.headers.setdefault("Cross-Origin-Resource-Policy", "same-origin")
        if is_management_api_path(request.url.path) or request.url.path == "/login" or (
            "session" in request.scope
            and request.session.get("user")
            and not request.url.path.startswith(("/static/", "/branding/"))
        ):
            response.headers.setdefault("Cache-Control", "no-store, private")
        return response


class CSRFProtectionMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if request.method in {"POST", "PUT", "PATCH", "DELETE"} and request.session.get("user") and not has_valid_management_key(request):
            expected = request.session.get("csrf_token")
            supplied = request.headers.get("X-CSRF-Token", "")
            if not expected or not supplied or not hmac.compare_digest(expected, supplied):
                return JSONResponse({"detail": "CSRF validation failed"}, status_code=403)
        return await call_next(request)


# SessionMiddleware must wrap CSRFProtectionMiddleware so the session is available
# while validating the anti-CSRF token.
app.add_middleware(
    PathAwareRequestBodyLimitMiddleware,
    default_max_body_size=1024 * 1024,
    path_limits={
        "/api/admin/imports/preview": MAX_EXPORT_BYTES,
        "/api/admin/imports/apply": MAX_EXPORT_BYTES,
        # Branding uploads are base64-encoded in JSON; keep a bounded budget
        # above the combined validated binary logo/favicon limits.
        "/api/admin/branding": 2 * 1024 * 1024,
        "/api/admin/signature-assets": MAX_SIGNATURE_IMAGE_BYTES,
        # MTA hooks carry the complete message, including MIME attachments.
        "/api/v1/adapters/stalwart/mta-hook": 20 * 1024 * 1024,
    },
)
app.add_middleware(CSRFProtectionMiddleware)
app.add_middleware(
    SessionMiddleware,
    secret_key=session_secret,
    session_cookie=os.getenv("SESSION_COOKIE_NAME", "__Host-opensigweave-session"),
    path="/",
    same_site="lax",
    https_only=os.getenv("SESSION_COOKIE_SECURE", "true").strip().lower() not in {"0", "false", "no"},
    max_age=3600,
)
# Validate explicit hostnames before accepting requests.
extra_allowed_hosts = [
    host.strip().lower()
    for host in os.environ.get("APP_ALLOWED_HOSTS", "").split(",")
    if host.strip()
]
allowed_hosts = extra_allowed_hosts
if not allowed_hosts or any(
    host == "*" or "/" in host or "@" in host or not host
    for host in allowed_hosts
):
    raise RuntimeError("Configure APP_ALLOWED_HOSTS with explicit hostnames; wildcard hosts are not allowed")

if LOCAL_ADMIN_CONFIGURED:
    if not is_valid_email(LOCAL_ADMIN_EMAIL):
        raise RuntimeError("LOCAL_ADMIN_EMAIL must be a valid email address when local administrator access is configured")
    if not is_valid_password_hash(LOCAL_ADMIN_PASSWORD_HASH):
        raise RuntimeError("LOCAL_ADMIN_PASSWORD_HASH must be a valid scrypt hash when local administrator access is configured")
else:
    raise RuntimeError("Configure local administrator credentials; managed identity providers are configured in the administrator portal")

# Host validation runs before rate limiting so invalid Host requests cannot consume buckets.
app.add_middleware(RateLimitMiddleware)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=allowed_hosts)

# SecurityHeadersMiddleware wraps both middlewares, including 400/429/503 responses.
app.add_middleware(SecurityHeadersMiddleware)

templates = Jinja2Templates(directory="templates")
app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/signature-assets/{asset_name}")
def signature_asset(asset_name: str):
    try:
        path = signature_file_store.image_path(asset_name)
    except SignatureStorageError:
        raise HTTPException(status_code=404, detail="Signature image not found")
    return FileResponse(
        path,
        media_type=signature_file_store.image_media_type(asset_name),
        headers={
            "Cache-Control": "public, max-age=31536000, immutable",
            "Cross-Origin-Resource-Policy": "cross-origin",
            "Content-Disposition": "inline",
        },
    )


def _bundled_branding_response(*, favicon: bool = False):
    """Only serve explicitly named bundled files; never accept paths from requests."""
    path = BUILTIN_FAVICON_PATH if favicon and BUILTIN_FAVICON_PATH.is_file() else BUILTIN_LOGO_PATH
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Bundled branding image is not configured")
    media_type = "image/x-icon" if path == BUILTIN_FAVICON_PATH else "image/png"
    return FileResponse(
        path,
        media_type=media_type,
        headers={"Cache-Control": "no-cache, must-revalidate", "X-Content-Type-Options": "nosniff"},
    )


@app.get("/branding/builtin/logo.png")
def builtin_branding_logo():
    return _bundled_branding_response()


@app.get("/branding/builtin/favicon")
def builtin_branding_favicon():
    return _bundled_branding_response(favicon=True)


@app.get("/branding/logo.png")
def branding_logo(db: Session = Depends(get_db)):
    settings = db.get(BrandingSettingsDB, 1)
    if settings is not None and settings.logo_png:
        return Response(
            content=settings.logo_png,
            media_type="image/png",
            headers={"Cache-Control": "no-cache, must-revalidate", "X-Content-Type-Options": "nosniff"},
        )
    return _bundled_branding_response()


@app.get("/branding/logo-dark.png")
def branding_logo_dark(db: Session = Depends(get_db)):
    """Serve an independent dark-mode logo, or the existing standard logo."""
    settings = db.get(BrandingSettingsDB, 1)
    if settings is not None and settings.dark_logo_png:
        return Response(
            content=settings.dark_logo_png,
            media_type="image/png",
            headers={"Cache-Control": "no-cache, must-revalidate", "X-Content-Type-Options": "nosniff"},
        )
    return branding_logo(db)


@app.get("/branding/favicon")
@app.get("/branding/favicon.ico")
def branding_favicon(db: Session = Depends(get_db)):
    settings = db.get(BrandingSettingsDB, 1)
    if settings is not None and settings.favicon_data:
        return Response(
            content=settings.favicon_data,
            media_type=settings.favicon_mime or "image/x-icon",
            headers={"Cache-Control": "no-cache, must-revalidate", "X-Content-Type-Options": "nosniff"},
        )
    return _bundled_branding_response(favicon=True)


def session_user(request: Request) -> dict:
    user = request.session.get("user")
    if not isinstance(user, dict):
        raise HTTPException(status_code=401, detail="Authentication required")
    email = user.get("email")
    if not isinstance(email, str) or len(email) > 254 or email.count("@") != 1 or any(ch.isspace() for ch in email):
        raise HTTPException(status_code=401, detail="A valid account email is required")
    local, domain = email.rsplit("@", 1)
    if not local or not domain or "." not in domain:
        raise HTTPException(status_code=401, detail="A valid account email is required")
    return user


def is_admin_user(request: Request):
    user = request.session.get("user")
    groups = user.get("groups", []) if isinstance(user, dict) else []
    if isinstance(groups, (list, tuple, set)) and "signature-admins" in groups:
        return True
    return has_valid_management_key(request)


def require_signature_user(request: Request) -> dict:
    user = session_user(request)
    groups = user.get("groups", [])
    if not isinstance(groups, (list, tuple, set)) or not ({"signature-admins", "signature-users"} & set(groups)):
        raise HTTPException(status_code=403, detail="Signature portal access required")
    return user


def normalized_user_email(user: dict) -> str:
    return user["email"].strip().casefold()


def normalize_domain(value: str) -> str:
    normalized = value.strip().casefold().rstrip(".")
    if len(normalized) > 253 or not normalized or any(
        not label or len(label) > 63 or label.startswith("-") or label.endswith("-")
        or not label.isascii()
        or not all(ch.isalnum() or ch == "-" for ch in label)
        for label in normalized.split(".")
    ):
        raise HTTPException(status_code=422, detail="Invalid domain name")
    return normalized

# --- Web UI Routes ---
def configured_login_providers(db) -> list[dict[str, str]]:
    if not hasattr(db, "query"):
        return []
    query = db.query(IdentityProviderDB).filter(
        IdentityProviderDB.is_enabled.is_(True),
        IdentityProviderDB.provider_type.in_({"authentik", "oidc"}),
    )
    if not hasattr(query, "all"):
        return []
    providers = []
    for provider in query.all():
        try:
            config = identity_provider_registry.get_config(provider.id)
            required = ("OIDC_DISCOVERY_URL", "OIDC_CLIENT_ID", "OIDC_CLIENT_SECRET", "OIDC_REDIRECT_URI")
            if all(config.get(key) for key in required):
                providers.append({"id": provider.id, "display_name": provider.display_name})
        except Exception as exc:
            logger.warning("OIDC login button skipped (%s)", type(exc).__name__)
    return providers


def login_page(request: Request, error: bool = False, status_code: int = 200, db=None):
    csrf_token = request.session.setdefault("login_csrf", secrets.token_urlsafe(32))
    brand = branding_context(db) if hasattr(db, "get") else {
        "app_name": DEFAULT_APP_NAME,
        "app_color": DEFAULT_APP_COLOR,
        "has_logo": BUILTIN_LOGO_PATH.is_file(),
        "has_favicon": BUILTIN_FAVICON_PATH.is_file() or os.path.isfile("branding/logo.png"),
        "has_custom_logo": False,
        "has_custom_favicon": False,
    }
    return templates.TemplateResponse(request, "login.html", {
        "request": request,
        "csrf_token": csrf_token,
        "error": error,
        "oidc_providers": configured_login_providers(db),
        **brand,
    }, status_code=status_code)


def managed_oidc_client(provider_id: str):
    config = identity_provider_registry.get_config(provider_id)
    if config.get("IDP_TYPE", "").casefold() not in {"authentik", "oidc"}:
        raise ProviderConfigError("Selected provider is not an OIDC provider.")
    required = ("OIDC_DISCOVERY_URL", "OIDC_CLIENT_ID", "OIDC_CLIENT_SECRET", "OIDC_REDIRECT_URI")
    if not all(config.get(key) for key in required):
        raise ProviderConfigError("Managed OIDC provider is not fully configured.")
    client = OAuth()
    client.register(
        name="oidc",
        server_metadata_url=config["OIDC_DISCOVERY_URL"],
        client_id=config["OIDC_CLIENT_ID"],
        client_secret=config["OIDC_CLIENT_SECRET"],
        client_kwargs={"scope": config.get("OIDC_SCOPES", "openid email profile groups")},
    )
    return client.oidc, config



# Keep callback diagnostics actionable without logging passwords, ID tokens,
# access tokens, userinfo, addresses or provider-controlled exception messages.
_OIDC_CALLBACK_FAILURE_CODES = {
    "OIDC login state does not match the selected provider": "login_session_or_provider_mismatch",
    "OIDC login state has an invalid email": "login_session_invalid_email",
    "OIDC provider is no longer enabled": "provider_disabled",
    "OIDC userinfo is missing the subject": "subject_missing",
    "OIDC account email is missing or invalid": "email_missing_or_invalid",
    "OIDC account email is not verified": "email_not_verified",
    "OIDC account does not match the requested email": "submitted_email_mismatch",
    "OIDC groups claim has an invalid format": "groups_claim_invalid",
    "OIDC subject exceeds the supported length": "subject_too_long",
    "Email domain is no longer assigned to this OIDC provider": "email_domain_not_assigned",
}


def _oidc_callback_failure_code(exc: Exception) -> str:
    # Only literal, application-controlled errors are exposed to the log.
    # Values from OAuth libraries must never be logged verbatim: an exception
    # may contain an IdP response, claim or token.
    if type(exc) is ValueError:
        return _OIDC_CALLBACK_FAILURE_CODES.get(str(exc), "oauth_or_token_validation_error")
    return "callback_exception"


def _valid_oidc_identity(user: object, expected_email: str | None = None):
    if not isinstance(user, dict) or not user.get("sub"):
        raise ValueError("OIDC userinfo is missing the subject")
    email = user.get("email")
    if not is_valid_email(email):
        raise ValueError("OIDC account email is missing or invalid")
    if user.get("email_verified") is not True:
        raise ValueError("OIDC account email is not verified")
    normalized_email = email.strip().casefold()
    if expected_email and not hmac.compare_digest(normalized_email, expected_email):
        raise ValueError("OIDC account does not match the requested email")
    groups = user.get("groups", [])
    if (
        not isinstance(groups, list)
        or len(groups) > 128
        or not all(isinstance(group, str) and 0 < len(group) <= 255 for group in groups)
    ):
        raise ValueError("OIDC groups claim has an invalid format")
    if len(str(user["sub"])) > 255:
        raise ValueError("OIDC subject exceeds the supported length")
    return normalized_email, groups


@app.get("/login")
async def login(request: Request, db: Session = Depends(get_db)):
    if request.session.get("user"):
        return RedirectResponse(url="/", status_code=303)
    return login_page(
        request,
        request.query_params.get("error") in {"invalid_credentials", "authentication_failed"},
        db=db,
    )


@app.get("/login/oidc/{provider_id}")
async def managed_oidc_login(provider_id: str, request: Request, db: Session = Depends(get_db)):
    if request.session.get("user"):
        return RedirectResponse(url="/", status_code=303)
    provider_meta = db.get(IdentityProviderDB, provider_id)
    if provider_meta is None or not provider_meta.is_enabled or provider_meta.provider_type not in {"authentik", "oidc"}:
        return RedirectResponse(url="/login?error=authentication_failed", status_code=303)
    try:
        client, config = managed_oidc_client(provider_id)
        request.session["pending_managed_login"] = {"provider_id": provider_id}
        return await client.authorize_redirect(request, config["OIDC_REDIRECT_URI"])
    except Exception as exc:
        logger.warning("Managed OIDC sign-in could not start (%s)", type(exc).__name__)
        request.session.clear()
        return RedirectResponse(url="/login?error=authentication_failed", status_code=303)


@app.post("/login")
async def login_submit(request: Request, db: Session = Depends(get_db)):
    if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/x-www-form-urlencoded":
        raise HTTPException(status_code=415, detail="Login form must be URL encoded")
    try:
        form_data = parse_qs((await request.body()).decode("utf-8"), keep_blank_values=True, max_num_fields=8)
    except (UnicodeDecodeError, ValueError):
        raise HTTPException(status_code=400, detail="Invalid login form")
    form = {key: values[0] for key, values in form_data.items() if values}
    expected_csrf = request.session.pop("login_csrf", None)
    supplied_csrf = form.get("csrf_token", "")
    if (
        not isinstance(expected_csrf, str)
        or not isinstance(supplied_csrf, str)
        or not hmac.compare_digest(expected_csrf, supplied_csrf)
    ):
        raise HTTPException(status_code=403, detail="CSRF validation failed")

    submitted_email = form.get("email", "")
    submitted_email = submitted_email.strip().casefold() if isinstance(submitted_email, str) else ""
    submitted_password = form.get("password", "")
    if not is_valid_email(submitted_email) or not isinstance(submitted_password, str) or len(submitted_password) > 1024:
        request.session.clear()
        return login_page(request, True, 401, db)

    # The local administrator remains available as an independent emergency path.
    if LOCAL_ADMIN_CONFIGURED and hmac.compare_digest(submitted_email, LOCAL_ADMIN_EMAIL):
        if not verify_local_admin_password(submitted_password, LOCAL_ADMIN_PASSWORD_HASH):
            request.session.clear()
            return login_page(request, True, 401, db)
        request.session.clear()
        request.session["user"] = {
            "sub": f"local-admin:{LOCAL_ADMIN_EMAIL}",
            "email": LOCAL_ADMIN_EMAIL,
            "name": LOCAL_ADMIN_EMAIL.split("@", 1)[0],
            "groups": ["signature-admins", "signature-users"],
            "attributes": {},
            "auth_source": "local",
        }
        request.session["csrf_token"] = secrets.token_urlsafe(32)
        return RedirectResponse(url="/", status_code=303)

    email_domain = submitted_email.rsplit("@", 1)[1]
    domain = db.query(DomainDB).filter(func.lower(DomainDB.domain_name) == email_domain).first()
    provider_id = domain.authentication_provider_id if domain else None
    if provider_id and (domain is None or not domain.is_active):
        request.session.clear()
        return login_page(request, True, 401, db)
    if provider_id:
        provider_meta = db.get(IdentityProviderDB, provider_id)
        if provider_meta is None or not provider_meta.is_enabled:
            request.session.clear()
            return login_page(request, True, 401, db)
        if provider_meta.provider_type == "ldap":
            try:
                identity = await identity_provider_registry.authenticate(provider_id, submitted_email, submitted_password)
            except Exception as exc:
                logger.warning("LDAP sign-in unavailable (%s)", type(exc).__name__)
                identity = None
            if identity is None:
                request.session.clear()
                return login_page(request, True, 401, db)
            groups = [str(group)[:255] for group in sorted(identity.groups)[:128] if group]
            display_name = " ".join(part for part in (identity.first_name, identity.last_name) if part).strip()
            claims = identity.claims if isinstance(identity.claims, dict) else {}
            stable_dn = str(claims.get("distinguishedname") or submitted_email)[:255]
            request.session.clear()
            request.session["user"] = {
                "sub": f"ldap:{provider_id}:{stable_dn}",
                "email": submitted_email,
                "email_verified": True,
                "name": display_name or submitted_email.split("@", 1)[0],
                "groups": groups,
                "attributes": {
                    "phone": identity.phone[:256],
                    "title": identity.title[:256],
                },
                "auth_source": "ldap",
                "auth_provider_id": provider_id,
            }
            request.session["csrf_token"] = secrets.token_urlsafe(32)
            return RedirectResponse(url="/", status_code=303)
        if provider_meta.provider_type in {"authentik", "oidc"}:
            try:
                client, config = managed_oidc_client(provider_id)
                request.session["pending_managed_login"] = {
                    "email": submitted_email,
                    "provider_id": provider_id,
                }
                return await client.authorize_redirect(request, config["OIDC_REDIRECT_URI"])
            except Exception as exc:
                logger.warning("Managed OIDC sign-in could not start (%s)", type(exc).__name__)
                request.session.clear()
                return login_page(request, True, 401, db)
        request.session.clear()
        return login_page(request, True, 401, db)

    request.session.clear()
    return login_page(request, True, 401, db)


async def managed_oidc_callback(provider_id: str, request: Request, db: Session = Depends(get_db)):
    try:
        pending = request.session.get("pending_managed_login")
        if not isinstance(pending, dict) or pending.get("provider_id") != provider_id:
            raise ValueError("OIDC login state does not match the selected provider")
        expected_email = pending.get("email")
        if expected_email is not None and not isinstance(expected_email, str):
            raise ValueError("OIDC login state has an invalid email")
        provider_meta = db.get(IdentityProviderDB, provider_id)
        if provider_meta is None or not provider_meta.is_enabled or provider_meta.provider_type not in {"authentik", "oidc"}:
            raise ValueError("OIDC provider is no longer enabled")
        client, _config = managed_oidc_client(provider_id)
        token = await client.authorize_access_token(request)
        user = token.get("userinfo")
        email, groups = _valid_oidc_identity(user, expected_email)
        domain = db.query(DomainDB).filter(func.lower(DomainDB.domain_name) == email.rsplit("@", 1)[1]).first()
        if domain is None or not domain.is_active or domain.authentication_provider_id != provider_id:
            raise ValueError("Email domain is no longer assigned to this OIDC provider")
        raw_attributes = user.get("attributes")
        if not isinstance(raw_attributes, dict):
            raw_attributes = {}
        safe_attributes = {
            key: value[:256] if isinstance(value, str) else ""
            for key in ("phone", "title")
            if isinstance((value := raw_attributes.get(key)), str)
        }
        if provider_meta.provider_type == "oidc":
            def claim_text(value):
                return value[:256] if isinstance(value, str) else ""

            display_name = claim_text(user.get("name"))
            first_name = claim_text(user.get("given_name") or user.get("first_name"))
            last_name = claim_text(user.get("family_name") or user.get("last_name"))
            if not first_name and not last_name and display_name:
                name_parts = display_name.split(None, 1)
                first_name = name_parts[0][:256]
                last_name = name_parts[1][:256] if len(name_parts) > 1 else ""
            title = claim_text(user.get("title") or raw_attributes.get("title"))
            phone = claim_text(user.get("phone_number") or user.get("phone") or raw_attributes.get("phone"))
            safe_custom_attributes = {}
            for key, value in list(raw_attributes.items())[:32]:
                if not isinstance(key, str) or not key or len(key) > 64:
                    continue
                if isinstance(value, str):
                    safe_custom_attributes[key] = value[:256]
                elif isinstance(value, list) and len(value) <= 16 and all(isinstance(item, str) for item in value):
                    safe_custom_attributes[key] = [item[:256] for item in value]
            safe_claims = {"email": email, "email_verified": True, "groups": groups}
            for key in ("name", "preferred_username", "given_name", "family_name", "locale", "department", "job_title"):
                if isinstance(user.get(key), str):
                    safe_claims[key] = user[key][:256]
            cache_key = f"{provider_id}:{email}"
            cached = db.get(IdentityCacheDB, cache_key)
            if cached is None:
                cached = IdentityCacheDB(email=cache_key)
                db.add(cached)
            cached.principal_id = f"{provider_id}:oidc:{hashlib.sha256(str(user['sub']).encode('utf-8')).hexdigest()}"
            cached.first_name = first_name
            cached.last_name = last_name
            cached.title = title
            cached.phone = phone
            cached.groups = groups
            cached.claims = safe_claims
            cached.attributes = safe_custom_attributes
            cached.updated_at = datetime.now(timezone.utc)
            db.commit()
            safe_attributes = {"phone": phone, "title": title}
        request.session.clear()
        request.session["user"] = {
            "sub": f"oidc:{provider_id}:{str(user['sub'])[:220]}",
            "email": email,
            "email_verified": True,
            "name": str(user.get("name") or "")[:256],
            "groups": groups,
            "attributes": safe_attributes,
            "auth_source": "oidc",
            "auth_provider_id": provider_id,
        }
    except Exception as exc:
        logger.warning(
            "Managed OIDC callback rejected for provider %s (reason=%s, type=%s)",
            provider_id, _oidc_callback_failure_code(exc), type(exc).__name__,
        )
        request.session.clear()
        return RedirectResponse(url="/login?error=authentication_failed", status_code=303)
    return RedirectResponse(url="/", status_code=303)


app.add_api_route("/auth/{provider_id}", managed_oidc_callback, methods=["GET"], name="managed_oidc_callback")


@app.post("/logout")
async def logout(request: Request):
    user = request.session.get("user") or {}
    auth_source = user.get("auth_source")
    provider_id = user.get("auth_provider_id")
    request.session.clear()

    if auth_source == "oidc" and provider_id:
        try:
            client, config = managed_oidc_client(provider_id)
            metadata = await client.load_server_metadata()
            endpoint = metadata.get("end_session_endpoint") if isinstance(metadata, dict) else None
            endpoint_parts = urlsplit(str(endpoint or ""))
            if endpoint_parts.scheme != "https" or not endpoint_parts.hostname:
                raise ValueError("OIDC provider has no valid HTTPS end-session endpoint")
            callback_parts = urlsplit(config["OIDC_REDIRECT_URI"])
            post_logout_uri = config.get("OIDC_POST_LOGOUT_REDIRECT_URI") or urlunsplit((
                callback_parts.scheme, callback_parts.netloc, "/login", "", "",
            ))
            separator = "&" if "?" in endpoint else "?"
            logout_url = f"{endpoint}{separator}{urlencode({'post_logout_redirect_uri': post_logout_uri})}"
            return {"logout_url": logout_url}
        except Exception as exc:
            logger.warning("Managed OIDC logout could not be prepared (%s)", type(exc).__name__)
            return {"logout_url": "/login"}

    return {"logout_url": "/login"}


@app.get("/")
async def read_root(request: Request, db: Session = Depends(get_db)):
    user = request.session.get('user')
    if not user: return RedirectResponse(url='/login')
    csrf_token = request.session.setdefault("csrf_token", secrets.token_urlsafe(32))

    user_groups = user.get('groups', [])
    is_admin = 'signature-admins' in user_groups
    is_user = 'signature-users' in user_groups
    if not is_admin and not is_user:
        return templates.TemplateResponse(request, "error.html", {"request": request, "message": "You do not have permission to access the Signature Portal.", "csrf_token": csrf_token})

    brand = branding_context(db)
    has_logo = brand["has_logo"]
    has_favicon = brand["has_favicon"]
    app_name = brand["app_name"]
    app_color = brand["app_color"]

    attributes = user.get('attributes', {}) if user else {}
    phone = attributes.get('phone', 'Not Set')
    title = attributes.get('title', 'Not Set')

    name = user.get('name', '')
    name_parts = name.split(' ')
    first_name = name_parts[0] if name_parts else ''
    last_name = ' '.join(name_parts[1:]) if len(name_parts) > 1 else ''
    email = user.get('email', '')
    
    email = email.strip().casefold() if isinstance(email, str) else ""
    domain_part = email.rsplit("@", 1)[-1] if "@" in email else ""
    domain_obj = db.query(DomainDB).filter(func.lower(DomainDB.domain_name) == domain_part).first()
    if domain_obj and domain_obj.identity_provider_id:
        profile = await get_identity_attributes(db, email, domain_obj.identity_provider_id)
        first_name = profile.first_name or first_name
        last_name = profile.last_name or last_name
        phone = profile.phone or "Not Set"
        title = profile.title or "Not Set"
    override_obj = db.query(UserOverrideDB).filter(func.lower(UserOverrideDB.user_email) == email).first()
    
    can_edit = False
    lock_reason = "Your email domain is not registered in this system."
    
    if override_obj and override_obj.is_disabled:
        lock_reason = "Your signature has been explicitly disabled by an administrator."
    elif domain_obj:
        if not domain_obj.is_active:
            lock_reason = "Signatures for your domain are currently disabled by the administrator."
        elif not domain_obj.allow_overrides:
            lock_reason = "Custom overrides are disabled. Rules and schedules determine the managed signature."
        else:
            can_edit = True
            lock_reason = ""
    
    return templates.TemplateResponse(request, "index.html", {
        "request": request, "has_logo": has_logo, "has_favicon": has_favicon, "app_name": app_name, "app_color": app_color,
        "user": user, "is_admin": is_admin,
        "phone": phone, "title": title, "first_name": first_name, "last_name": last_name,
        "can_edit": can_edit, "lock_reason": lock_reason, "csrf_token": csrf_token,
        "idp_setup_required": is_admin and db.query(IdentityProviderDB).count() == 0
    })

# --- API Endpoints ---
@app.get("/domains/")
def read_domains(request: Request, db: Session = Depends(get_db)):
    if not is_admin_user(request):
        raise HTTPException(status_code=403, detail="Admins only")
    domains = db.query(DomainDB).order_by(DomainDB.domain_name.asc()).all()
    return [
        {
            "id": item.id,
            "domain_name": item.domain_name,
            "is_active": item.is_active,
            "allow_overrides": item.allow_overrides,
            "inject_on_replies": item.inject_on_replies,
            "trim_whitespace": item.trim_whitespace,
            "strip_device_signatures": item.strip_device_signatures,
            "identity_provider_id": item.identity_provider_id,
            "authentication_provider_id": item.authentication_provider_id,
            "template_html": item.template_html_source if item.template_html_source is not None else (item.template_html or ""),
        }
        for item in domains
    ]

@app.post("/domains/")
def create_domain(domain: DomainCreate, request: Request, db: Session = Depends(get_db)):
    if not is_admin_user(request):
        raise HTTPException(status_code=403, detail="Admins only")
    normalized_domain = normalize_domain(domain.domain_name)
    for provider_id in (domain.identity_provider_id, domain.authentication_provider_id):
        if provider_id and db.get(IdentityProviderDB, provider_id) is None:
            raise HTTPException(status_code=422, detail="Identity provider not found")
    db_domain = DomainDB(domain_name=normalized_domain, is_active=domain.is_active, identity_provider_id=domain.identity_provider_id, authentication_provider_id=domain.authentication_provider_id)
    db.add(db_domain)
    db.commit()
    db.refresh(db_domain)
    return db_domain

@app.put("/domains/{domain_id}")
def update_domain(domain_id: int, domain_update: DomainUpdate, request: Request, db: Session = Depends(get_db)):
    if not is_admin_user(request):
        raise HTTPException(status_code=403, detail="Admins only")
    db_domain = db.query(DomainDB).filter(DomainDB.id == domain_id).first()
    if not db_domain: raise HTTPException(status_code=404)
    db_domain.is_active = domain_update.is_active
    db_domain.allow_overrides = domain_update.allow_overrides
    db_domain.inject_on_replies = domain_update.inject_on_replies
    db_domain.trim_whitespace = domain_update.trim_whitespace
    db_domain.strip_device_signatures = domain_update.strip_device_signatures
    # Legacy master-template data is retained for rollback, but no longer edited by the UI.
    if domain_update.template_html is not None:
        db_domain.template_html_source = domain_update.template_html
        db_domain.template_html = sanitize_signature_html(domain_update.template_html)
    if "identity_provider_id" in domain_update.model_fields_set:
        if domain_update.identity_provider_id and db.get(IdentityProviderDB, domain_update.identity_provider_id) is None:
            raise HTTPException(status_code=422, detail="Identity provider not found")
        db_domain.identity_provider_id = domain_update.identity_provider_id
    if "authentication_provider_id" in domain_update.model_fields_set:
        if domain_update.authentication_provider_id and db.get(IdentityProviderDB, domain_update.authentication_provider_id) is None:
            raise HTTPException(status_code=422, detail="Authentication provider not found")
        db_domain.authentication_provider_id = domain_update.authentication_provider_id
    db.commit()
    return {"status": "success"}

@app.delete("/domains/{domain_id}")
def delete_domain(domain_id: int, request: Request, db: Session = Depends(get_db)):
    if not is_admin_user(request):
        raise HTTPException(status_code=403, detail="Admins only")
    db_domain = db.query(DomainDB).filter(DomainDB.id == domain_id).first()
    if db_domain:
        db.delete(db_domain)
        db.commit()
    return {"status": "deleted"}

@app.put("/domains/{domain_id}/identity-provider")
def assign_domain_identity_provider(domain_id: int, payload: dict, request: Request, db: Session = Depends(get_db)):
    if not is_admin_user(request): raise HTTPException(status_code=403, detail="Admins only")
    domain = db.get(DomainDB, domain_id)
    if domain is None: raise HTTPException(status_code=404, detail="Domain not found")
    provider_id = payload.get("identity_provider_id")
    if provider_id is not None and (not isinstance(provider_id, str) or db.get(IdentityProviderDB, provider_id) is None):
        raise HTTPException(status_code=422, detail="Identity provider not found")
    domain.identity_provider_id = provider_id
    db.commit()
    return {"domain_id": domain_id, "identity_provider_id": provider_id}


@app.put("/domains/{domain_id}/authentication-provider")
def assign_domain_authentication_provider(domain_id: int, payload: dict, request: Request, db: Session = Depends(get_db)):
    if not is_admin_user(request):
        raise HTTPException(status_code=403, detail="Admins only")
    domain = db.get(DomainDB, domain_id)
    if domain is None:
        raise HTTPException(status_code=404, detail="Domain not found")
    provider_id = payload.get("authentication_provider_id")
    if provider_id is not None and (not isinstance(provider_id, str) or db.get(IdentityProviderDB, provider_id) is None):
        raise HTTPException(status_code=422, detail="Authentication provider not found")
    domain.authentication_provider_id = provider_id
    db.commit()
    return {"domain_id": domain_id, "authentication_provider_id": provider_id}


@app.get("/api/my-signature")
def get_my_signature(request: Request, db: Session = Depends(get_db)):
    user = require_signature_user(request)
    email = normalized_user_email(user)
    override = db.query(UserOverrideDB).filter(func.lower(UserOverrideDB.user_email) == email).first()
    return {"html_content": (override.html_content_source if override.html_content_source is not None else sanitize_signature_html(override.html_content)) if override else ""}

@app.post("/api/my-signature")
def save_my_signature(req: OverrideUpdate, request: Request, db: Session = Depends(get_db)):
    user = require_signature_user(request)
    email = normalized_user_email(user)
    domain_part = email.rsplit("@", 1)[-1]
    domain_obj = db.query(DomainDB).filter(func.lower(DomainDB.domain_name) == domain_part).first()
    if not domain_obj or not domain_obj.is_active or not domain_obj.allow_overrides:
        raise HTTPException(status_code=403, detail="Personal signature overrides are disabled for this domain")
    override = db.query(UserOverrideDB).filter(func.lower(UserOverrideDB.user_email) == email).first()
    if override and override.is_disabled:
        raise HTTPException(status_code=403, detail="Your personal signature has been disabled by an administrator")
    safe_html = sanitize_signature_html(req.html_content)
    if override:
        override.html_content_source = req.html_content
        override.html_content = safe_html
    else:
        db.add(UserOverrideDB(user_email=email, html_content=safe_html, html_content_source=req.html_content))
    db.commit()
    return {"status": "success"}

@app.delete("/api/my-signature")
def delete_my_signature(request: Request, db: Session = Depends(get_db)):
    user = require_signature_user(request)
    email = normalized_user_email(user)
    domain_part = email.rsplit("@", 1)[-1]
    domain_obj = db.query(DomainDB).filter(func.lower(DomainDB.domain_name) == domain_part).first()
    if not domain_obj or not domain_obj.is_active or not domain_obj.allow_overrides:
        raise HTTPException(status_code=403, detail="Personal signature overrides are disabled for this domain")
    override = db.query(UserOverrideDB).filter(func.lower(UserOverrideDB.user_email) == email).first()
    if override and override.is_disabled:
        raise HTTPException(status_code=403, detail="Your personal signature has been disabled by an administrator")
    if override:
        db.delete(override)
        db.commit()
        return {"status": "deleted"}
    return {"status": "not_found"}

@app.get("/api/admin/domains/{domain_id}/users")
async def list_domain_users(
    domain_id: int, request: Request, q: str = "", source: str = "all",
    page: int = 1, page_size: int = 20, db: Session = Depends(get_db),
):
    """Admin-only paginated user directory; never disclose credentials, claims or override HTML."""
    if not is_admin_user(request):
        raise HTTPException(status_code=403, detail="Admins only")
    domain = db.get(DomainDB, domain_id)
    if domain is None:
        raise HTTPException(status_code=404, detail="Domain not found")
    if page < 1 or page_size < 1 or page_size > 100 or len(q) > 200:
        raise HTTPException(status_code=422, detail="Invalid search or pagination")
    if source not in {"all", "ldap", "authentik", "local", "override"}:
        raise HTTPException(status_code=422, detail="Invalid source filter")
    suffix = "@" + domain.domain_name.strip().casefold()
    users: dict[str, dict] = {}

    def merge(email, display_name="", origin="local", is_disabled=None):
        if not isinstance(email, str):
            return
        normalized = email.strip().casefold()
        if not normalized.endswith(suffix) or normalized.count("@") != 1 or len(normalized) > 320:
            return
        item = users.setdefault(normalized, {
            "email": normalized, "display_name": "", "source": origin,
            "override_status": "None", "directory_disabled": False,
        })
        if display_name and (not item["display_name"] or origin in {"ldap", "authentik"}):
            item["display_name"] = str(display_name)[:256]
        if origin in {"ldap", "authentik"}:
            item["source"] = origin
        elif origin == "override" and item["source"] not in {"ldap", "authentik"}:
            item["source"] = "override"
        if is_disabled is not None:
            item["directory_disabled"] = bool(is_disabled)

    for cached in db.query(IdentityCacheDB).filter(
        func.lower(IdentityCacheDB.email).endswith(suffix, autoescape=True)
    ).all():
        email = (cached.email or "").rsplit(":", 1)[-1]
        merge(email, " ".join(filter(None, (cached.first_name, cached.last_name))), "local")
    if LOCAL_ADMIN_EMAIL:
        merge(LOCAL_ADMIN_EMAIL, origin="local")
    for override in db.query(UserOverrideDB).filter(
        func.lower(UserOverrideDB.user_email).endswith(suffix, autoescape=True)
    ).all():
        merge(override.user_email, origin="override")
        users[override.user_email.strip().casefold()]["override_status"] = (
            "Disabled" if override.is_disabled else "Configured"
        )

    warning = ""
    truncated = False
    provider = db.get(IdentityProviderDB, domain.identity_provider_id) if domain.identity_provider_id else None
    if provider and provider.is_enabled and provider.provider_type in {"ldap", "authentik"}:
        try:
            directory = identity_provider_registry.get(provider.id)
            if directory is None or not hasattr(directory, "list_users"):
                raise IdentityProviderUnavailable("Provider enumeration unavailable")
            directory_users, truncated = await directory.list_users(domain.domain_name)
            for account in directory_users:
                merge(account.get("email"), account.get("display_name", ""),
                      provider.provider_type, account.get("disabled", False))
        except (IdentityProviderUnavailable, ProviderConfigError, OSError, ValueError):
            warning = "Identity provider users could not be loaded; cached identities and overrides are shown."
    elif provider and provider.provider_type == "oidc":
        warning = "OpenID Connect has no standard user-list API; locally known identities are shown."
    else:
        warning = "No browsable identity provider is configured; locally known identities are shown."
    if truncated:
        warning = (warning + " " if warning else "") + "Identity provider result limit reached; the user list is incomplete."
    partial = bool(truncated or warning)

    needle = q.strip().casefold()
    filtered = [
        item for item in users.values()
        if (source == "all" or item["source"] == source)
        and (not needle or needle in item["email"] or needle in item["display_name"].casefold())
    ]
    filtered.sort(key=lambda item: (item["email"], item["source"]))
    begin = (page - 1) * page_size
    return {
        "items": filtered[begin:begin + page_size],
        "total": len(filtered), "page": page, "page_size": page_size,
        "warning": warning, "partial": partial,
    }


@app.get("/api/admin/overrides/{domain_name}")
def get_domain_overrides(domain_name: str, request: Request, db: Session = Depends(get_db)):
    if not is_admin_user(request): raise HTTPException(status_code=403, detail="Admins only")
    normalized_domain = normalize_domain(domain_name)
    overrides = db.query(UserOverrideDB).filter(func.lower(UserOverrideDB.user_email).endswith("@" + normalized_domain, autoescape=True)).all()
    return [
        {
            "id": item.id,
            "user_email": item.user_email,
            "html_content": item.html_content_source if item.html_content_source is not None else sanitize_signature_html(item.html_content),
            "is_disabled": item.is_disabled,
        }
        for item in overrides
    ]

@app.post("/api/admin/overrides")
def save_admin_override(req: AdminOverrideSave, request: Request, db: Session = Depends(get_db)):
    if not is_admin_user(request): raise HTTPException(status_code=403, detail="Admins only")
    normalized_email = req.user_email.strip().casefold()
    if normalized_email.count("@") != 1 or any(ch.isspace() for ch in normalized_email):
        raise HTTPException(status_code=422, detail="Invalid email address")
    safe_html = sanitize_signature_html(req.html_content)
    override = db.query(UserOverrideDB).filter(func.lower(UserOverrideDB.user_email) == normalized_email).first()
    if override:
        override.html_content_source = req.html_content
        override.html_content = safe_html
        override.is_disabled = req.is_disabled
    else: 
        db.add(UserOverrideDB(user_email=normalized_email, html_content=safe_html, html_content_source=req.html_content, is_disabled=req.is_disabled))
    db.commit()
    return {"status": "success"}

@app.delete("/api/admin/overrides/{override_id}")
def delete_admin_override(override_id: int, request: Request, db: Session = Depends(get_db)):
    if not is_admin_user(request): raise HTTPException(status_code=403, detail="Admins only")
    override = db.query(UserOverrideDB).filter(UserOverrideDB.id == override_id).first()
    if override:
        db.delete(override)
        db.commit()
    return {"status": "deleted"}


@app.get("/api/admin/identity-providers")
def list_identity_providers(request: Request, db: Session = Depends(get_db)):
    if not is_admin_user(request): raise HTTPException(status_code=403, detail="Admins only")
    disk = {item["id"]: item for item in identity_provider_registry.list_configs()}
    rows = db.query(IdentityProviderDB).order_by(IdentityProviderDB.display_name.asc()).all()
    result = []
    for row in rows:
        safe = identity_provider_registry.describe(row.id) if row.id in disk else {
            "id": row.id, "display_name": row.display_name, "provider_type": row.provider_type,
            "is_enabled": row.is_enabled, "settings": {}, "secret_configured": False,
        }
        result.append({**safe, "is_enabled": row.is_enabled, "configured": row.id in disk})
    return result


class IdentityProviderCreateRequest(BaseModel):
    id: str = Field(min_length=1, max_length=64)
    display_name: str = Field(min_length=1, max_length=120)
    provider_type: Literal["authentik", "oidc", "ldap"]
    settings: dict = Field(default_factory=dict)
    is_enabled: bool = True


@app.post("/api/admin/identity-providers", status_code=201)
def create_identity_provider(payload: IdentityProviderCreateRequest, request: Request, db: Session = Depends(get_db)):
    if not is_admin_user(request): raise HTTPException(status_code=403, detail="Admins only")
    if db.get(IdentityProviderDB, payload.id): raise HTTPException(status_code=409, detail="Identity provider already exists")
    try: item = identity_provider_registry.save(payload.id, payload.display_name, payload.provider_type, payload.settings, payload.is_enabled)
    except (ProviderConfigError, ValueError, OSError) as exc: raise HTTPException(status_code=422, detail=str(exc))
    db.add(IdentityProviderDB(id=item["id"], display_name=item["display_name"], provider_type=item["provider_type"], is_enabled=item["is_enabled"]))
    try: db.commit()
    except Exception:
        db.rollback(); identity_provider_registry.delete(payload.id); raise
    return item


@app.put("/api/admin/identity-providers/{provider_id}")
def update_identity_provider(provider_id: str, payload: IdentityProviderCreateRequest, request: Request, db: Session = Depends(get_db)):
    if not is_admin_user(request): raise HTTPException(status_code=403, detail="Admins only")
    row = db.get(IdentityProviderDB, provider_id)
    if row is None or payload.id != provider_id: raise HTTPException(status_code=404, detail="Identity provider not found")
    if payload.provider_type != row.provider_type: raise HTTPException(status_code=422, detail="Provider type cannot be changed; create a new provider instead.")
    try: item = identity_provider_registry.save(provider_id, payload.display_name, payload.provider_type, payload.settings, payload.is_enabled)
    except (ProviderConfigError, ValueError, OSError) as exc: raise HTTPException(status_code=422, detail=str(exc))
    row.display_name, row.provider_type, row.is_enabled = item["display_name"], item["provider_type"], item["is_enabled"]
    db.commit()
    return item


@app.post("/api/admin/identity-providers/{provider_id}/test")
async def test_identity_provider(provider_id: str, request: Request, db: Session = Depends(get_db)):
    if not is_admin_user(request): raise HTTPException(status_code=403, detail="Admins only")
    if db.get(IdentityProviderDB, provider_id) is None: raise HTTPException(status_code=404, detail="Identity provider not found")
    try: await identity_provider_registry.test(provider_id)
    except Exception as exc:
        logger.warning("Identity provider test failed (%s)", type(exc).__name__)
        raise HTTPException(status_code=422, detail="Connection test failed. Check settings and application logs.")
    return {"status": "connected"}


@app.delete("/api/admin/identity-providers/{provider_id}")
def delete_identity_provider(provider_id: str, request: Request, db: Session = Depends(get_db)):
    if not is_admin_user(request): raise HTTPException(status_code=403, detail="Admins only")
    row = db.get(IdentityProviderDB, provider_id)
    if row is None: raise HTTPException(status_code=404, detail="Identity provider not found")
    if db.query(DomainDB).filter((DomainDB.identity_provider_id == provider_id) | (DomainDB.authentication_provider_id == provider_id)).first():
        raise HTTPException(status_code=409, detail="Unassign this provider from all domains before deleting it.")
    identity_provider_registry.delete(provider_id)
    db.delete(row); db.commit()
    return {"status": "deleted"}



@app.get("/api/admin/system-status")
def admin_system_status(request: Request, db: Session = Depends(get_db)):
    """Safe, read-only diagnostics for an authorized application administrator."""
    if not is_admin_user(request):
        raise HTTPException(status_code=403, detail="Admins only")
    database_status, counts = "ok", {}
    try:
        db.execute(text("SELECT 1"))
        counts = {
            "domains": db.query(DomainDB).count(),
            "identity_providers": db.query(IdentityProviderDB).count(),
            "signatures": db.query(SignatureDB).count(),
            "versions": db.query(SignatureVersionDB).count(),
            "groups": db.query(SignatureGroupDB).count(),
            "rules": db.query(SignatureRuleDB).count(),
            "schedules": db.query(SignatureScheduleDB).count(),
            "overrides": db.query(UserOverrideDB).count(),
        }
    except SQLAlchemyError as exc:
        db.rollback()
        logger.warning("System status database check failed (%s)", type(exc).__name__)
        database_status = "unavailable"

    def accessible(directory):
        try:
            return directory.is_dir() and not directory.is_symlink() and os.access(
                directory, os.R_OK | os.W_OK | os.X_OK
            )
        except OSError:
            return False

    signatures = accessible(signature_file_store.signatures_root)
    assets = accessible(signature_file_store.assets_root)
    storage_status = "ok" if signatures and assets else "unavailable"
    return {
        "status": "healthy" if database_status == "ok" and storage_status == "ok" else "degraded",
        "version": app.version,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "database": {"status": database_status},
        "storage": {"status": storage_status, "signature_files": signatures, "image_assets": assets},
        "counts": counts,
        "configuration": {
            "local_admin_enabled": LOCAL_ADMIN_CONFIGURED,
            "management_api_enabled": bool(MANAGEMENT_API_KEY),
            "backup_management": "host-operator" if os.environ.get("BACKUP_CONTROL_SOCKET") else "operator-cli",
            "mail_adapters": "external",
        },
    }


def _admin_backup_control(request: Request, action: str, **payload):
    if not is_admin_user(request):
        raise HTTPException(status_code=403, detail="Admins only")
    try:
        return backup_request(action, **payload)
    except BackupControlError as exc:
        logger.warning("Backup operator request failed: action=%s", action)
        raise HTTPException(status_code=503, detail=str(exc)) from None


def _require_backup_location_admin(request: Request) -> None:
    if not is_admin_user(request):
        raise HTTPException(status_code=403, detail="Admins only")


def _backup_location_or_404(db: Session, location_id: str) -> BackupLocationDB:
    if not re.fullmatch(r"[a-f0-9]{32}", location_id):
        raise HTTPException(status_code=404, detail="Backup location not found")
    row = db.get(BackupLocationDB, location_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Backup location not found")
    return row


def _unique_backup_location_name(db: Session, name: str, current_id: str = "") -> None:
    row = db.query(BackupLocationDB).filter(func.lower(BackupLocationDB.name) == name.strip().lower()).first()
    if row is not None and row.id != current_id:
        raise HTTPException(status_code=409, detail="A backup location with this name already exists")


@app.get("/api/admin/backups/locations")
def list_backup_locations(request: Request, db: Session = Depends(get_db)):
    _require_backup_location_admin(request)
    return [public_location(row) for row in db.query(BackupLocationDB).order_by(BackupLocationDB.name).all()]


@app.post("/api/admin/backups/locations", status_code=201)
def create_backup_location(payload: BackupLocationInput, request: Request, db: Session = Depends(get_db)):
    _require_backup_location_admin(request)
    _unique_backup_location_name(db, payload.name)
    row = BackupLocationDB(id=uuid.uuid4().hex, type="local")
    try:
        apply_location(row, payload.model_dump(), creating=True)
        if row.type == "local":
            ensure_local_directory(row.id)
        db.add(row)
        db.commit()
    except BackupLocationError as exc:
        db.rollback()
        raise HTTPException(status_code=exc.status, detail=str(exc)) from None
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Backup location already exists") from None
    return public_location(row)


@app.put("/api/admin/backups/locations/{location_id}")
def update_backup_location(location_id: str, payload: BackupLocationInput, request: Request,
                           db: Session = Depends(get_db)):
    _require_backup_location_admin(request)
    row = _backup_location_or_404(db, location_id)
    _unique_backup_location_name(db, payload.name, row.id)
    try:
        apply_location(row, payload.model_dump())
        if row.type == "local":
            ensure_local_directory(row.id)
        db.commit()
    except BackupLocationError as exc:
        db.rollback()
        raise HTTPException(status_code=exc.status, detail=str(exc)) from None
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Backup location already exists") from None
    return public_location(row)


@app.delete("/api/admin/backups/locations/{location_id}")
def delete_backup_location(location_id: str, request: Request, db: Session = Depends(get_db)):
    _require_backup_location_admin(request)
    row = _backup_location_or_404(db, location_id)
    # Deliberately do not erase stored archives or directories.
    db.delete(row)
    db.commit()
    return {"status": "deleted"}


@app.post("/api/admin/backups/locations/{location_id}/test")
def test_backup_location_connection(location_id: str, request: Request, db: Session = Depends(get_db)):
    _require_backup_location_admin(request)
    row = _backup_location_or_404(db, location_id)
    try:
        return test_location(row)
    except BackupLocationError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from None


@app.get("/api/admin/backups")
def get_admin_backups(request: Request):
    """List encrypted host backups and the currently configured schedule."""
    if not is_admin_user(request):
        raise HTTPException(status_code=403, detail="Admins only")
    try:
        return backup_request("status")
    except BackupControlError:
        return {"available": False, "settings": None, "backups": [], "job": {"state": "unavailable"},
                "error": "Host backup service is not configured or unavailable"}


@app.put("/api/admin/backups/settings")
def save_admin_backup_settings(payload: BackupSettingsUpdate, request: Request):
    return _admin_backup_control(request, "settings", settings=payload.model_dump())


@app.post("/api/admin/backups/create", status_code=202)
def create_admin_backup(request: Request):
    return _admin_backup_control(request, "create")


@app.delete("/api/admin/backups/{filename}")
def delete_admin_backup(filename: str, payload: BackupActionRequest, request: Request):
    return _admin_backup_control(request, "delete", name=filename, confirm=payload.confirm)


@app.post("/api/admin/backups/{filename}/restore", status_code=202)
def restore_admin_backup(filename: str, payload: BackupActionRequest, request: Request):
    """Never overwrite the live instance: restore only to a separate Compose project."""
    return _admin_backup_control(request, "restore", name=filename, confirm=payload.confirm)


@app.get("/api/admin/backups/{filename}/download")
def download_admin_backup(filename: str, request: Request):
    if not is_admin_user(request):
        raise HTTPException(status_code=403, detail="Admins only")
    # A bounded, linear-time ASCII filename check avoids regular-expression backtracking.
    prefix, suffix = "opensigweave-", ".age"
    valid_name = (
        isinstance(filename, str) and len(filename) <= 255
        and filename.startswith(prefix) and filename.endswith(suffix)
        and len(filename) > len(prefix) + len(suffix)
        and all(
            ("A" <= char <= "Z") or ("a" <= char <= "z")
            or ("0" <= char <= "9") or char in "_-"
            for char in filename[len(prefix):-len(suffix)]
        )
    )
    if not valid_name:
        raise HTTPException(status_code=404, detail="Backup not found")
    try:
        size, chunks = backup_download(filename)
    except BackupControlError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from None
    return StreamingResponse(chunks, media_type="application/octet-stream",
        headers={"Content-Length": str(size),
                 "Content-Disposition": f'attachment; filename="{filename}"',
                 "Cache-Control": "no-store, private", "X-Content-Type-Options": "nosniff"})


@app.get("/api/admin/branding")
def get_admin_branding(request: Request, db: Session = Depends(get_db)):
    if not is_admin_user(request):
        raise HTTPException(status_code=403, detail="Admins only")
    brand = branding_context(db)
    return {
        **brand,
        "logo_url": "/branding/logo.png",
        "dark_logo_url": "/branding/logo-dark.png",
        "favicon_url": "/branding/favicon",
    }


@app.put("/api/admin/branding")
def update_admin_branding(
    payload: BrandingUpdateRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    if not is_admin_user(request):
        raise HTTPException(status_code=403, detail="Admins only")
    if not payload.app_name.strip() or any(ord(char) < 32 or ord(char) == 127 for char in payload.app_name):
        raise HTTPException(status_code=422, detail="Application name must contain printable characters")
    if not re.fullmatch(r"#[0-9A-Fa-f]{6}", payload.app_color):
        raise HTTPException(status_code=422, detail="Brand color must be a six-digit hex color")
    if payload.reset_logo and payload.logo_data_url:
        raise HTTPException(status_code=422, detail="Choose either a logo upload or reset")
    if payload.reset_dark_logo and payload.dark_logo_data_url:
        raise HTTPException(status_code=422, detail="Choose either a dark-mode logo upload or reset")
    if payload.reset_favicon and payload.favicon_data_url:
        raise HTTPException(status_code=422, detail="Choose either a favicon upload or reset")

    settings = db.get(BrandingSettingsDB, 1)
    if settings is None:
        settings = BrandingSettingsDB(id=1)
        db.add(settings)
    settings.app_name = payload.app_name.strip()
    settings.app_color = payload.app_color.lower()

    try:
        if payload.reset_logo:
            settings.logo_png = None
        elif payload.logo_data_url:
            logo, _mime = decode_branding_data_url(
                payload.logo_data_url,
                allowed_mimes={"image/png"},
                max_bytes=512 * 1024,
            )
            settings.logo_png = logo

        if payload.reset_dark_logo:
            settings.dark_logo_png = None
        elif payload.dark_logo_data_url:
            dark_logo, _mime = decode_branding_data_url(
                payload.dark_logo_data_url,
                allowed_mimes={"image/png"},
                max_bytes=512 * 1024,
            )
            settings.dark_logo_png = dark_logo

        if payload.reset_favicon:
            settings.favicon_data = None
            settings.favicon_mime = None
        elif payload.favicon_data_url:
            favicon, favicon_mime = decode_branding_data_url(
                payload.favicon_data_url,
                allowed_mimes={"image/png", "image/x-icon", "image/vnd.microsoft.icon"},
                max_bytes=256 * 1024,
            )
            settings.favicon_data = favicon
            settings.favicon_mime = "image/x-icon" if favicon_mime in {"image/x-icon", "image/vnd.microsoft.icon"} else "image/png"
        db.commit()
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=422, detail=str(exc))
    except Exception:
        db.rollback()
        raise

    return {
        **branding_context(db),
        "logo_url": "/branding/logo.png",
        "dark_logo_url": "/branding/logo-dark.png",
        "favicon_url": "/branding/favicon",
    }

@app.post("/api/admin/branding/reset")
def reset_admin_branding(request: Request, db: Session = Depends(get_db)):
    """Restore bundled branding without changing other application settings."""
    if not is_admin_user(request):
        raise HTTPException(status_code=403, detail="Admins only")

    settings = db.get(BrandingSettingsDB, 1)
    if settings is None:
        settings = BrandingSettingsDB(id=1)
        db.add(settings)
    settings.app_name = DEFAULT_APP_NAME
    settings.app_color = DEFAULT_APP_COLOR
    settings.logo_png = None
    settings.dark_logo_png = None
    settings.favicon_data = None
    settings.favicon_mime = None
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise

    return get_admin_branding(request, db)


@app.get("/api/admin/exports")
def export_admin_components(
    request: Request,
    components: str = "signatures,domains,rules,groups,identity_providers",
    db: Session = Depends(get_db),
):
    """Download selected non-secret administrator configuration components."""
    if not is_admin_user(request):
        raise HTTPException(status_code=403, detail="Admins only")
    selected = [item.strip() for item in components.split(",") if item.strip()]
    try:
        archive, filename = build_admin_export(db, signature_file_store, selected)
    except AdminExportError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return Response(
        content=archive,
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store, private",
            "Pragma": "no-cache",
            "X-Content-Type-Options": "nosniff",
        },
    )


@app.post("/api/admin/imports/preview")
async def preview_admin_import(request: Request, db: Session = Depends(get_db)):
    """Validate an administrator ZIP and return an additive-import preview."""
    if not is_admin_user(request):
        raise HTTPException(status_code=403, detail="Admins only")
    if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/zip":
        raise HTTPException(status_code=415, detail="Import archive must use application/zip")
    try:
        payload = await request.body()
        return inspect_admin_import(db, signature_file_store, payload)
    except AdminImportError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@app.post("/api/admin/imports/apply")
async def apply_admin_import_endpoint(request: Request, db: Session = Depends(get_db)):
    """Apply a previously reviewed archive after validating it again."""
    if not is_admin_user(request):
        raise HTTPException(status_code=403, detail="Admins only")
    if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/zip":
        raise HTTPException(status_code=415, detail="Import archive must use application/zip")
    try:
        payload = await request.body()
        return apply_admin_import(db, signature_file_store, payload)
    except AdminImportError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@app.post("/api/admin/signature-assets", status_code=201)
async def upload_signature_asset(request: Request):
    if not is_admin_user(request):
        raise HTTPException(status_code=403, detail="Admins only")
    content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    try:
        filename = signature_file_store.write_image(await request.body(), content_type)
    except (SignatureStorageError, TypeError) as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return {"url": f"/signature-assets/{filename}"}


class ImageUrlImportRequest(BaseModel):
    url: str = Field(min_length=12, max_length=2048)


@app.post("/api/admin/signature-assets/import-url", status_code=201)
def import_signature_asset_url(request: Request, payload: ImageUrlImportRequest):
    if not is_admin_user(request):
        raise HTTPException(status_code=403, detail="Admins only")
    try:
        filename = import_image_from_url(signature_file_store, payload.url)
    except SignatureImageImportError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return {"url": f"/signature-assets/{filename}"}


@app.get("/api/admin/signatures")
def list_signatures(request: Request, db: Session = Depends(get_db)):
    if not is_admin_user(request):
        raise HTTPException(status_code=403, detail="Admins only")
    signatures = db.query(SignatureDB).order_by(SignatureDB.name.asc()).all()
    return [
        {
            "id": item.id,
            "name": item.name,
            "marker_code": hashlib.sha256(item.name.casefold().encode("utf-8")).hexdigest()[:4],
            "domain_id": item.domain_id,
            "domain_ids": [row.domain_id for row in db.query(SignatureDomainDB).filter(SignatureDomainDB.signature_id == item.id).order_by(SignatureDomainDB.domain_id).all()],
            "scope": item.scope,
            "is_active": item.is_active,
            "versions": db.query(SignatureVersionDB)
                .filter(SignatureVersionDB.signature_id == item.id)
                .count(),
        }
        for item in signatures
    ]


@app.post("/api/admin/signatures", status_code=201)
def create_signature_endpoint(
    payload: SignatureCreateRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    if not is_admin_user(request):
        raise HTTPException(status_code=403, detail="Admins only")
    user = request.session.get("user") or {}
    try:
        signature, version = create_signature(
            db,
            signature_file_store,
            name=payload.name,
            domain_id=payload.domain_id,
            domain_ids=payload.domain_ids,
            html_content=payload.html_content,
            is_active=payload.is_active,
            created_by=user.get("email"),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {
        "id": signature.id,
        "version_id": version.id,
        "version_number": version.version_number,
    }


@app.put("/api/admin/signatures/{signature_id}")
def update_signature_endpoint(
    signature_id: str,
    payload: SignatureUpdateRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    if not is_admin_user(request):
        raise HTTPException(status_code=403, detail="Admins only")
    try:
        signature = update_signature(
            db, signature_file_store, signature_id, name=payload.name,
            domain_id=payload.domain_id, domain_ids=payload.domain_ids, is_active=payload.is_active,
        )
    except SignatureNotFoundError:
        raise HTTPException(status_code=404, detail="Signature not found")
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    domain_ids = [row.domain_id for row in db.query(SignatureDomainDB).filter(SignatureDomainDB.signature_id == signature.id).order_by(SignatureDomainDB.domain_id).all()]
    return {"id": signature.id, "name": signature.name, "domain_id": signature.domain_id,
            "domain_ids": domain_ids, "scope": signature.scope, "is_active": signature.is_active}


@app.get("/api/admin/signatures/{signature_id}/versions")
def list_signature_versions(signature_id: str, request: Request, db: Session = Depends(get_db)):
    if not is_admin_user(request):
        raise HTTPException(status_code=403, detail="Admins only")
    signature = db.get(SignatureDB, signature_id)
    if signature is None:
        raise HTTPException(status_code=404, detail="Signature not found")
    versions = (
        db.query(SignatureVersionDB)
        .filter(SignatureVersionDB.signature_id == signature_id)
        .order_by(SignatureVersionDB.version_number.desc())
        .all()
    )
    current_version_id = versions[0].id if versions else None
    return [
        {
            "id": item.id,
            "version_number": item.version_number,
            "sha256": item.sha256,
            "size_bytes": item.size_bytes,
            "created_by": item.created_by,
            "created_at": item.created_at,
            "is_current": item.id == current_version_id,
        }
        for item in versions
    ]


@app.get("/api/admin/signatures/{signature_id}/versions/{version_id}")
def get_signature_version(
    signature_id: str,
    version_id: str,
    request: Request,
    db: Session = Depends(get_db),
):
    if not is_admin_user(request):
        raise HTTPException(status_code=403, detail="Admins only")
    signature = db.get(SignatureDB, signature_id)
    version = db.get(SignatureVersionDB, version_id)
    if signature is None or version is None or version.signature_id != signature_id:
        raise HTTPException(status_code=404, detail="Signature version not found")
    try:
        html_content = signature_file_store.read(
            signature.name, uuid.UUID(hex=version.id), version.sha256
        )
    except (SignatureStorageError, ValueError, TypeError):
        raise HTTPException(status_code=503, detail="Signature version asset unavailable")
    latest_version_id = db.query(SignatureVersionDB.id).filter(
        SignatureVersionDB.signature_id == signature_id
    ).order_by(SignatureVersionDB.version_number.desc()).limit(1).scalar()
    return {
        "id": version.id,
        "version_number": version.version_number,
        "sha256": version.sha256,
        "size_bytes": version.size_bytes,
        "created_by": version.created_by,
        "created_at": version.created_at,
        "html_content": html_content,
        "is_current": version.id == latest_version_id,
    }


@app.post("/api/admin/signatures/{signature_id}/versions", status_code=201)
def create_signature_version_endpoint(
    signature_id: str,
    payload: SignatureVersionCreateRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    if not is_admin_user(request):
        raise HTTPException(status_code=403, detail="Admins only")
    user = request.session.get("user") or {}
    try:
        version = add_signature_version(
            db,
            signature_file_store,
            signature_id,
            payload.html_content,
            created_by=user.get("email"),
        )
    except SignatureNotFoundError:
        raise HTTPException(status_code=404, detail="Signature not found")
    return {"id": version.id, "version_number": version.version_number, "sha256": version.sha256}


@app.post("/api/admin/signatures/{signature_id}/versions/{version_id}/restore", status_code=201)
def restore_signature_version_endpoint(
    signature_id: str,
    version_id: str,
    request: Request,
    db: Session = Depends(get_db),
):
    if not is_admin_user(request):
        raise HTTPException(status_code=403, detail="Admins only")
    user = request.session.get("user") or {}
    try:
        source_version = db.get(SignatureVersionDB, version_id)
        version = restore_signature_version(
            db,
            signature_file_store,
            signature_id,
            version_id,
            created_by=user.get("email"),
        )
    except SignatureNotFoundError:
        raise HTTPException(status_code=404, detail="Signature not found")
    except SignatureVersionNotFoundError:
        raise HTTPException(status_code=404, detail="Signature version not found")
    except SignatureStorageError:
        raise HTTPException(status_code=503, detail="Signature version asset unavailable")
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return {
        "id": version.id,
        "version_number": version.version_number,
        "sha256": version.sha256,
        "restored_from_version_id": version_id,
        "restored_from_version_number": source_version.version_number if source_version is not None else None,
    }


@app.get("/api/admin/signature-groups/{group_id}/preview")
def preview_signature_group(
    group_id: str,
    domain_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    """Render a stable group preview without consuming a user's rotation state."""
    if not is_admin_user(request):
        raise HTTPException(status_code=403, detail="Admins only")
    group = db.get(SignatureGroupDB, group_id)
    if group is None:
        raise HTTPException(status_code=404, detail="Group not found")
    selected = get_group_preview_signature(db, group_id, domain_id)
    if selected is None:
        raise HTTPException(status_code=404, detail="No eligible signature for this group and domain")
    latest_version = (
        db.query(SignatureVersionDB)
        .filter(SignatureVersionDB.signature_id == selected.id)
        .order_by(SignatureVersionDB.version_number.desc())
        .first()
    )
    if latest_version is None:
        raise HTTPException(status_code=503, detail="Signature version metadata is missing")
    try:
        raw_html = signature_file_store.read(
            selected.name, uuid.UUID(hex=latest_version.id), latest_version.sha256
        )
    except (SignatureStorageError, ValueError, TypeError):
        raise HTTPException(status_code=503, detail="Signature asset unavailable")
    preview_values = {
        "first_name": "Alex",
        "last_name": "Example",
        "title": "Position (Preview)",
        "phone": "+49 123 456789",
        "email": "alex@example.org",
        "domain_name": "example.org",
    }
    try:
        html = render_signature_template(sanitize_signature_html(raw_html), preview_values)
    except TemplateError:
        raise HTTPException(status_code=422, detail="Invalid signature template")
    return {
        "group_id": group.id,
        "group_slug": group.slug,
        "signature_id": selected.id,
        "signature_name": selected.name,
        "html": html,
        "rotation_state_changed": False,
    }


@app.get("/api/admin/signature-groups")
def list_signature_groups(request: Request, db: Session = Depends(get_db)):
    if not is_admin_user(request):
        raise HTTPException(status_code=403, detail="Admins only")
    groups = db.query(SignatureGroupDB).order_by(SignatureGroupDB.name.asc()).all()
    result = []
    for group in groups:
        members = db.query(SignatureGroupMemberDB).filter(SignatureGroupMemberDB.group_id == group.id).order_by(SignatureGroupMemberDB.signature_id.asc()).all()
        domain_ids = [row.domain_id for row in db.query(SignatureGroupDomainDB).filter(SignatureGroupDomainDB.group_id == group.id).order_by(SignatureGroupDomainDB.domain_id.asc()).all()]
        result.append({"id": group.id, "name": group.name, "slug": group.slug, "selection_policy": group.selection_policy, "is_active": group.is_active, "domain_ids": domain_ids, "members": [{"signature_id": item.signature_id, "weight": item.weight, "is_enabled": item.is_enabled} for item in members]})
    return result


@app.post("/api/admin/signature-groups", status_code=201)
def create_signature_group(payload: SignatureGroupCreateRequest, request: Request, db: Session = Depends(get_db)):
    if not is_admin_user(request):
        raise HTTPException(status_code=403, detail="Admins only")
    slug = (payload.slug or slugify(payload.name))[:64]
    if not slug or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", slug):
        raise HTTPException(status_code=422, detail="Invalid group slug")
    if db.query(SignatureGroupDB).filter(SignatureGroupDB.slug == slug).first():
        raise HTTPException(status_code=409, detail="Group slug already exists")
    domains = db.query(DomainDB).filter(DomainDB.id.in_(payload.domain_ids)).all() if payload.domain_ids else []
    if len(domains) != len(set(payload.domain_ids)):
        raise HTTPException(status_code=422, detail="One or more domains do not exist")
    group = SignatureGroupDB(id=uuid.uuid4().hex, name=payload.name.strip(), slug=slug, is_active=payload.is_active)
    try:
        db.add(group)
        db.flush()
        db.add_all([SignatureGroupDomainDB(group_id=group.id, domain_id=item.id) for item in domains])
        db.commit()
    except Exception:
        db.rollback()
        raise
    return {"id": group.id, "slug": group.slug}


@app.put("/api/admin/signature-groups/{group_id}")
def update_signature_group(group_id: str, payload: SignatureGroupCreateRequest, request: Request, db: Session = Depends(get_db)):
    if not is_admin_user(request):
        raise HTTPException(status_code=403, detail="Admins only")
    group = db.get(SignatureGroupDB, group_id)
    if group is None:
        raise HTTPException(status_code=404, detail="Group not found")
    slug = (payload.slug or slugify(payload.name))[:64]
    if not slug or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", slug):
        raise HTTPException(status_code=422, detail="Invalid group slug")
    if db.query(SignatureGroupDB).filter(SignatureGroupDB.slug == slug, SignatureGroupDB.id != group_id).first():
        raise HTTPException(status_code=409, detail="Group slug already exists")
    domains = db.query(DomainDB).filter(DomainDB.id.in_(payload.domain_ids)).all() if payload.domain_ids else []
    if len(domains) != len(set(payload.domain_ids)):
        raise HTTPException(status_code=422, detail="One or more domains do not exist")
    group.name, group.slug, group.is_active = payload.name.strip(), slug, payload.is_active
    db.query(SignatureGroupDomainDB).filter(SignatureGroupDomainDB.group_id == group_id).delete(synchronize_session=False)
    db.add_all([SignatureGroupDomainDB(group_id=group_id, domain_id=item.id) for item in domains])
    db.commit()
    return {"status": "updated"}


@app.post("/api/admin/signature-groups/{group_id}/members", status_code=201)
def add_signature_group_member(group_id: str, payload: SignatureGroupMemberRequest, request: Request, db: Session = Depends(get_db)):
    if not is_admin_user(request):
        raise HTTPException(status_code=403, detail="Admins only")
    if db.get(SignatureGroupDB, group_id) is None:
        raise HTTPException(status_code=404, detail="Group not found")
    if db.get(SignatureDB, payload.signature_id) is None:
        raise HTTPException(status_code=404, detail="Signature not found")
    if db.get(SignatureGroupMemberDB, (group_id, payload.signature_id)):
        raise HTTPException(status_code=409, detail="Signature is already a member")
    db.add(SignatureGroupMemberDB(group_id=group_id, signature_id=payload.signature_id, weight=payload.weight, is_enabled=payload.is_enabled))
    db.commit()
    return {"status": "created"}


@app.put("/api/admin/signature-groups/{group_id}/members/{signature_id}")
def update_signature_group_member(group_id: str, signature_id: str, payload: SignatureGroupMemberRequest, request: Request, db: Session = Depends(get_db)):
    if not is_admin_user(request):
        raise HTTPException(status_code=403, detail="Admins only")
    if payload.signature_id != signature_id:
        raise HTTPException(status_code=422, detail="Path and payload signature IDs must match")
    member = db.get(SignatureGroupMemberDB, (group_id, signature_id))
    if member is None:
        raise HTTPException(status_code=404, detail="Group membership not found")
    member.weight, member.is_enabled = payload.weight, payload.is_enabled
    db.commit()
    return {"status": "updated"}


@app.delete("/api/admin/signature-groups/{group_id}/members/{signature_id}")
def remove_signature_group_member(group_id: str, signature_id: str, request: Request, db: Session = Depends(get_db)):
    if not is_admin_user(request):
        raise HTTPException(status_code=403, detail="Admins only")
    member = db.get(SignatureGroupMemberDB, (group_id, signature_id))
    if member is None:
        raise HTTPException(status_code=404, detail="Group membership not found")
    db.delete(member)
    db.commit()
    return {"status": "deleted"}


@app.delete("/api/admin/signature-groups/{group_id}")
def delete_signature_group(group_id: str, request: Request, db: Session = Depends(get_db)):
    if not is_admin_user(request):
        raise HTTPException(status_code=403, detail="Admins only")
    group = db.get(SignatureGroupDB, group_id)
    if group is None:
        raise HTTPException(status_code=404, detail="Group not found")
    if db.query(SignatureRuleDB).filter(SignatureRuleDB.group_id == group_id).first():
        raise HTTPException(status_code=409, detail="Group is referenced by a rule; update the rule first")
    if db.query(SignatureScheduleDB).filter(SignatureScheduleDB.group_id == group_id).first():
        raise HTTPException(status_code=409, detail="Group is referenced by a schedule; update the schedule first")
    db.delete(group)
    db.commit()
    return {"status": "deleted"}


def _normalized_schedule_window(payload: SignatureScheduleRequest):
    if payload.starts_at.tzinfo is None or payload.starts_at.utcoffset() is None:
        raise HTTPException(status_code=422, detail="Schedule start must include a timezone")
    if payload.ends_at.tzinfo is None or payload.ends_at.utcoffset() is None:
        raise HTTPException(status_code=422, detail="Schedule end must include a timezone")
    starts_at = payload.starts_at.astimezone(timezone.utc)
    ends_at = payload.ends_at.astimezone(timezone.utc)
    if starts_at >= ends_at:
        raise HTTPException(status_code=422, detail="Schedule end must be after start")
    return starts_at, ends_at


def _schedule_target_scope(db: Session, payload: SignatureScheduleRequest):
    if payload.domain_id is not None and db.get(DomainDB, payload.domain_id) is None:
        raise HTTPException(status_code=404, detail="Schedule domain not found")

    if payload.target_type == "SIGNATURE":
        if not payload.signature_id or payload.group_id:
            raise HTTPException(status_code=422, detail="SIGNATURE schedules require signature_id only")
        target = db.get(SignatureDB, payload.signature_id)
        if target is None or not target.is_active:
            raise HTTPException(status_code=422, detail="Active target signature not found")
        assigned = {
            row.domain_id for row in db.query(SignatureDomainDB)
            .filter(SignatureDomainDB.signature_id == target.id).all()
        }
    else:
        if not payload.group_id or payload.signature_id:
            raise HTTPException(status_code=422, detail="GROUP schedules require group_id only")
        target = db.get(SignatureGroupDB, payload.group_id)
        if target is None or not target.is_active:
            raise HTTPException(status_code=422, detail="Active target group not found")
        assigned = {
            row.domain_id for row in db.query(SignatureGroupDomainDB)
            .filter(SignatureGroupDomainDB.group_id == target.id).all()
        }

    if payload.domain_id is None and assigned:
        raise HTTPException(status_code=422, detail="All-domain schedules require a globally scoped target")
    if payload.domain_id is not None and assigned and payload.domain_id not in assigned:
        raise HTTPException(status_code=422, detail="Scheduled target is not assigned to the selected domain")


def _serialize_schedule(item: SignatureScheduleDB):
    def iso_utc(value):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat()
    return {
        "id": item.id,
        "name": item.name,
        "target_type": item.target_type,
        "signature_id": item.signature_id,
        "group_id": item.group_id,
        "domain_id": item.domain_id,
        "starts_at": iso_utc(item.starts_at),
        "ends_at": iso_utc(item.ends_at),
        "priority": item.priority,
        "is_enabled": item.is_enabled,
    }


@app.get("/api/admin/schedules")
def list_signature_schedules(request: Request, db: Session = Depends(get_db)):
    if not is_admin_user(request):
        raise HTTPException(status_code=403, detail="Admins only")
    items = (
        db.query(SignatureScheduleDB)
        .order_by(SignatureScheduleDB.starts_at.asc(), SignatureScheduleDB.priority.desc(), SignatureScheduleDB.id.asc())
        .all()
    )
    return [_serialize_schedule(item) for item in items]


@app.post("/api/admin/schedules", status_code=201)
def create_signature_schedule(
    payload: SignatureScheduleRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    if not is_admin_user(request):
        raise HTTPException(status_code=403, detail="Admins only")
    starts_at, ends_at = _normalized_schedule_window(payload)
    if not payload.name.strip():
        raise HTTPException(status_code=422, detail="Schedule name must not be blank")
    _schedule_target_scope(db, payload)
    item = SignatureScheduleDB(
        id=uuid.uuid4().hex,
        name=payload.name.strip(),
        target_type=payload.target_type,
        signature_id=payload.signature_id,
        group_id=payload.group_id,
        domain_id=payload.domain_id,
        starts_at=starts_at,
        ends_at=ends_at,
        priority=payload.priority,
        is_enabled=payload.is_enabled,
    )
    db.add(item)
    db.commit()
    return _serialize_schedule(item)


@app.put("/api/admin/schedules/{schedule_id}")
def update_signature_schedule(
    schedule_id: str,
    payload: SignatureScheduleRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    if not is_admin_user(request):
        raise HTTPException(status_code=403, detail="Admins only")
    item = db.get(SignatureScheduleDB, schedule_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Schedule not found")
    starts_at, ends_at = _normalized_schedule_window(payload)
    if not payload.name.strip():
        raise HTTPException(status_code=422, detail="Schedule name must not be blank")
    _schedule_target_scope(db, payload)
    item.name = payload.name.strip()
    item.target_type = payload.target_type
    item.signature_id = payload.signature_id
    item.group_id = payload.group_id
    item.domain_id = payload.domain_id
    item.starts_at = starts_at
    item.ends_at = ends_at
    item.priority = payload.priority
    item.is_enabled = payload.is_enabled
    db.commit()
    return _serialize_schedule(item)


@app.delete("/api/admin/schedules/{schedule_id}")
def delete_signature_schedule(schedule_id: str, request: Request, db: Session = Depends(get_db)):
    if not is_admin_user(request):
        raise HTTPException(status_code=403, detail="Admins only")
    item = db.get(SignatureScheduleDB, schedule_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Schedule not found")
    db.delete(item)
    db.commit()
    return {"status": "deleted"}


@app.get("/api/admin/rules")
def list_signature_rules(request: Request, db: Session = Depends(get_db)):
    if not is_admin_user(request):
        raise HTTPException(status_code=403, detail="Admins only")
    rules = (
        db.query(SignatureRuleDB)
        .order_by(SignatureRuleDB.priority.desc(), SignatureRuleDB.id.asc())
        .all()
    )
    return [
        {
            "id": rule.id,
            "name": rule.name,
            "priority": rule.priority,
            "action_mode": rule.action_mode,
            "signature_id": rule.signature_id,
            "group_id": rule.group_id,
            "is_enabled": rule.is_enabled,
            "conditions": [
                {"kind": item.kind, "key": item.key, "value": item.value}
                for item in db.query(SignatureRuleConditionDB)
                .filter(SignatureRuleConditionDB.rule_id == rule.id)
                .order_by(SignatureRuleConditionDB.id.asc())
                .all()
            ],
        }
        for rule in rules
    ]


@app.post("/api/admin/rules", status_code=201)
def create_signature_rule(
    payload: SignatureRuleCreateRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    if not is_admin_user(request):
        raise HTTPException(status_code=403, detail="Admins only")
    if payload.action_mode == "GROUP":
        if not payload.group_id or payload.signature_id:
            raise HTTPException(status_code=422, detail="GROUP rules require group_id only")
        target_group = db.get(SignatureGroupDB, payload.group_id)
        if target_group is None or not target_group.is_active:
            raise HTTPException(status_code=422, detail="Active target group not found")
    elif payload.action_mode == "SIGNATURE":
        if not payload.signature_id or payload.group_id:
            raise HTTPException(status_code=422, detail="SIGNATURE rules require signature_id only")
        target_signature = db.get(SignatureDB, payload.signature_id)
        if target_signature is None:
            raise HTTPException(status_code=404, detail="Signature not found")
        if not target_signature.is_active:
            raise HTTPException(status_code=422, detail="Inactive signatures cannot be rule targets")
    elif payload.signature_id or payload.group_id:
        raise HTTPException(status_code=422, detail="NONE rules cannot target a signature or group")
    rule = SignatureRuleDB(
        id=uuid.uuid4().hex,
        name=payload.name.strip(),
        priority=payload.priority,
        action_mode=payload.action_mode,
        signature_id=payload.signature_id,
        group_id=payload.group_id,
        is_enabled=payload.is_enabled,
    )
    try:
        db.add(rule)
        db.flush()
        db.add_all([
            SignatureRuleConditionDB(
                rule_id=rule.id,
                kind=condition.kind,
                key=condition.key,
                value=condition.value,
            )
            for condition in payload.conditions
        ])
        db.commit()
    except Exception:
        db.rollback()
        raise
    return {"id": rule.id}


@app.put("/api/admin/rules/{rule_id}")
def update_signature_rule(
    rule_id: str,
    payload: SignatureRuleCreateRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    if not is_admin_user(request):
        raise HTTPException(status_code=403, detail="Admins only")
    rule = db.get(SignatureRuleDB, rule_id)
    if rule is None:
        raise HTTPException(status_code=404, detail="Rule not found")
    if payload.action_mode == "GROUP":
        if not payload.group_id or payload.signature_id:
            raise HTTPException(status_code=422, detail="GROUP rules require group_id only")
        target_group = db.get(SignatureGroupDB, payload.group_id)
        if target_group is None or not target_group.is_active:
            raise HTTPException(status_code=422, detail="Active target group not found")
    elif payload.action_mode == "SIGNATURE":
        if not payload.signature_id or payload.group_id:
            raise HTTPException(status_code=422, detail="SIGNATURE rules require signature_id only")
        target_signature = db.get(SignatureDB, payload.signature_id)
        if target_signature is None:
            raise HTTPException(status_code=404, detail="Signature not found")
        if not target_signature.is_active:
            raise HTTPException(status_code=422, detail="Inactive signatures cannot be rule targets")
    elif payload.signature_id or payload.group_id:
        raise HTTPException(status_code=422, detail="NONE rules cannot target a signature or group")
    try:
        rule.name = payload.name.strip()
        rule.priority = payload.priority
        rule.action_mode = payload.action_mode
        rule.signature_id = payload.signature_id
        rule.group_id = payload.group_id
        rule.is_enabled = payload.is_enabled
        db.query(SignatureRuleConditionDB).filter(
            SignatureRuleConditionDB.rule_id == rule_id
        ).delete(synchronize_session=False)
        db.add_all([
            SignatureRuleConditionDB(
                rule_id=rule_id,
                kind=condition.kind,
                key=condition.key,
                value=condition.value,
            )
            for condition in payload.conditions
        ])
        db.commit()
    except Exception:
        db.rollback()
        raise
    return {"status": "updated"}


@app.delete("/api/admin/rules/{rule_id}")
def delete_signature_rule(
    rule_id: str,
    request: Request,
    db: Session = Depends(get_db),
):
    if not is_admin_user(request):
        raise HTTPException(status_code=403, detail="Admins only")
    rule = db.get(SignatureRuleDB, rule_id)
    if rule is None:
        raise HTTPException(status_code=404, detail="Rule not found")
    db.delete(rule)
    db.commit()
    return {"status": "deleted"}

# ==========================================
# SHARED SIGNATURE ENGINE
# ==========================================
def _signature_engine_service() -> SignatureEngineService:
    """Build the shared service from application-owned collaborators."""
    return SignatureEngineService(
        identity_resolver=get_identity_attributes,
        file_store=signature_file_store,
    )


async def resolve_signature_engine(
    db: Session,
    engine_request: SignatureEngineRequest,
    *,
    commit_selection: bool = True,
) -> SignatureEngineResult:
    """Run synchronous SQLAlchemy and rendering work outside the ASGI event loop.

    A private event loop in the worker supports the existing async identity
    providers without sharing this SQLAlchemy Session across simultaneous tasks.
    Both HTTP routes and mail adapters use this same execution path.
    """
    # SQLite connections may be bound to the creating thread and in-memory
    # databases are per-connection. Transferring an injected SQLite Session
    # to a worker can corrupt request isolation or fail to find its tables.
    # Keep SQLite session work on its originating event loop; PostgreSQL is
    # the supported scalable production path with worker offloading.
    if isinstance(db, Session) and db.get_bind().dialect.name == "sqlite":
        return await _signature_engine_service().resolve(
            db, engine_request, commit_selection=commit_selection
        )

    def process():
        return asyncio.run(_signature_engine_service().resolve(
            db, engine_request, commit_selection=commit_selection
        ))

    try:
        return await to_thread.run_sync(process)
    except DatabasePoolTimeout as exc:
        logger.warning("Engine database connection pool exhausted")
        raise SignatureEngineError(
            "DATABASE_POOL_EXHAUSTED", "Database connection pool is temporarily busy"
        ) from exc
    except SQLAlchemyError as exc:
        # PostgreSQL 55P03: bounded lock timeout; keep the failure
        # explicit, safe and retryable without leaking database details.
        if getattr(getattr(exc, "orig", None), "sqlstate", None) == "55P03":
            raise SignatureEngineError(
                "ROTATION_CONTENTION", "Signature selection is temporarily busy"
            ) from exc
        raise


_ENGINE_ERROR_HTTP_STATUS = {
    "SIGNATURE_VERSION_METADATA_MISSING": 503,
    "SIGNATURE_ASSET_UNAVAILABLE": 503,
    "INVALID_STORED_RULE": 500,
    "GROUP_SIGNATURE_VERSION_METADATA_MISSING": 503,
    "GROUP_SIGNATURE_ASSET_UNAVAILABLE": 503,
    "INVALID_TEMPLATE": 422,
    "ROTATION_CONTENTION": 503,
    "DATABASE_POOL_EXHAUSTED": 503,
    "INLINE_IMAGE_ASSET_UNAVAILABLE": 503,
    "INLINE_IMAGE_LIMIT": 422,
    "UNMANAGED_INLINE_IMAGE": 422,
    "INVALID_INLINE_IMAGE": 422,
}


def _engine_http_exception(error: SignatureEngineError) -> HTTPException:
    return HTTPException(
        status_code=_ENGINE_ERROR_HTTP_STATUS.get(error.code, 500),
        detail=error.safe_message,
    )


@app.get("/api/v1/engine/signatures/{email}", name="get_engine_signature_v1")
@app.get("/api/signature/{email}", name="get_rspamd_signature_legacy")
async def get_engine_signature(
    email: str,
    response: Response,
    x_engine_key: Optional[str] = Header(None),
    x_signature_name: Optional[str] = Header(None, alias="X-OpenSigWeave-Signature"),
    x_signature_code: Optional[str] = Header(None, alias="X-OpenSigWeave-Signature-Code"),
    db: Session = Depends(get_db),
    x_inline_images: Optional[str] = Header(None, alias="X-OpenSigWeave-Inline-Images"),
):
    """HTTP transport wrapper; all selection and rendering lives in the service."""
    response.headers["Cache-Control"] = "no-store, private"
    if not ENGINE_API_KEY or not hmac.compare_digest(x_engine_key or "", ENGINE_API_KEY):
        raise HTTPException(status_code=401, detail="Unauthorized Engine Request")

    try:
        engine_request = normalize_engine_request(
            email, x_signature_name, x_signature_code
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    try:
        # Keep positional in-process callers working: a direct Python call uses
        # the FastAPI Header descriptor as its omitted default value.
        inline_mode = x_inline_images if isinstance(x_inline_images, str) else None
        want_cid = inline_mode == "cid"
        if inline_mode is not None and not want_cid:
            raise HTTPException(status_code=400, detail="Unsupported inline image mode")
        result = await resolve_signature_engine(
            db, engine_request, commit_selection=not want_cid
        )
        payload = result.to_payload()
        if want_cid and payload["html"]:
            payload["html"], payload["inline_assets"] = build_cid_payload(
                payload["html"], signature_file_store
            )
        if want_cid:
            db.commit()
    except SignatureEngineError as exc:
        db.rollback()
        raise _engine_http_exception(exc)
    return payload


# ==========================================
# STALWART MTA HOOK ADAPTER
# ==========================================
@app.post("/api/v1/adapters/stalwart/mta-hook", name="stalwart_mta_hook")
async def stalwart_mta_hook(
    request: Request,
    authorization: Optional[str] = Header(None, alias="Authorization"),
    db: Session = Depends(get_db),
):
    hook_token = os.environ.get("STALWART_HOOK_TOKEN", "")
    scheme, separator, credentials = authorization.partition(" ") if isinstance(authorization, str) else ("", "", "")
    supplied_token = credentials.strip() if separator and scheme.casefold() == "bearer" else ""
    if not hook_token or not hmac.compare_digest(supplied_token, hook_token):
        raise HTTPException(status_code=401, detail="Unauthorized Stalwart hook request")

    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid Stalwart hook JSON")
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Invalid Stalwart hook payload")

    context = payload.get("context")
    envelope = payload.get("envelope")
    message = payload.get("message")
    if not isinstance(context, dict) or context.get("stage") != "data":
        raise HTTPException(status_code=400, detail="Only Stalwart DATA-stage requests are supported")
    sasl = context.get("sasl")
    if not isinstance(sasl, dict) or not isinstance(sasl.get("login"), str) or not sasl["login"].strip():
        # Do not inject into unauthenticated inbound mail. Stalwart must also
        # enforce authenticated sender/alias authorization in its SMTP policy.
        return {"action": "accept"}
    if not isinstance(envelope, dict) or not isinstance(envelope.get("from"), dict):
        raise HTTPException(status_code=400, detail="Missing Stalwart envelope sender")
    sender = envelope["from"].get("address")
    if not isinstance(sender, str) or not sender.strip():
        raise HTTPException(status_code=400, detail="Missing Stalwart envelope sender address")
    if not isinstance(message, dict) or not isinstance(message.get("headers"), list):
        raise HTTPException(status_code=400, detail="Missing Stalwart message")

    def header_values(name: str) -> list[str]:
        values = []
        for item in message["headers"]:
            if (
                isinstance(item, list)
                and len(item) == 2
                and isinstance(item[0], str)
                and item[0].casefold() == name.casefold()
                and isinstance(item[1], str)
            ):
                values.append(item[1].strip())
        return values

    signature_values = header_values("X-OpenSigWeave-Signature")
    code_values = header_values("X-OpenSigWeave-Signature-Code")
    signature_name = signature_values[0] if len(signature_values) == 1 else None
    signature_code = code_values[0] if len(code_values) == 1 else None

    try:
        engine_request = normalize_engine_request(
            sender, signature_name, signature_code
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    try:
        # The adapter may still decline to modify this MIME message (for example,
        # signed mail or a reply excluded by policy). Keep rotation updates pending
        # until the adapter confirms that it will replace the message body.
        engine_result = await resolve_signature_engine(
            db, engine_request, commit_selection=False
        )
    except SignatureEngineError as exc:
        db.rollback()
        raise _engine_http_exception(exc)

    try:
        hook_response = build_stalwart_hook_response(
            payload, engine_result.to_payload()
        )
    except (TypeError, ValueError):
        db.rollback()
        logger.warning("Rejected malformed Stalwart MTA hook message")
        raise HTTPException(status_code=400, detail="Invalid Stalwart message structure")

    modifications = hook_response.get("modifications", [])
    if any(
        isinstance(item, dict) and item.get("type") == "replaceContents"
        for item in modifications
    ):
        db.commit()
    else:
        db.rollback()
    return hook_response
