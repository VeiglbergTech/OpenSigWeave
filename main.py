import os
import logging
import uuid
import json
import hmac
import hashlib
import secrets
from urllib.parse import parse_qs, urlsplit, urlunsplit
from fastapi import FastAPI, Depends, HTTPException, Request, Header, Response
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import func
from sqlalchemy.orm import Session
from pydantic import BaseModel, Field
from typing import List, Optional, Literal

from dotenv import load_dotenv
from starlette.middleware.sessions import SessionMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.body_limit import RequestBodyLimitMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.responses import JSONResponse
from authlib.integrations.starlette_client import OAuth
from jinja2 import TemplateError

load_dotenv()

# Database infrastructure is isolated from the application models and routes.
from database import get_db
from models import DomainDB, UserOverrideDB, SignatureDB, SignatureDomainDB, SignatureVariantDB, SignatureVersionDB, SignatureRuleDB, SignatureRuleConditionDB, IdentityCacheDB
from rate_limiter import RateLimitMiddleware

from signature_renderer import render_signature_template
from identity.providers.base import IdentityProviderUnavailable
from identity.attributes import IdentityAttributes
from identity.registry import IdentityProviderRegistry, ProviderConfigError
from models import IdentityProviderDB
from core.selection_repository import select_and_record_variant
from core.modes import SignatureMode, resolve_mode
from core.rules import RuleContext, evaluate_rules
from core.rule_repository import load_signature_rules
from signature_catalog import SignatureNotFoundError, add_signature_variant, add_signature_version, create_signature, update_signature, update_signature_variant
from signature_storage import SignatureFileStore, SignatureStorageError
from signature_sanitizer import sanitize_signature_html
from auth_config import (
    is_valid_email, is_valid_password_hash, oidc_enabled_from_env,
    verify_local_admin_password,
)
logger = logging.getLogger(__name__)

oauth = OAuth()
OIDC_ENABLED = oidc_enabled_from_env()
LOCAL_ADMIN_EMAIL = os.environ.get("LOCAL_ADMIN_EMAIL", "").strip().casefold()
LOCAL_ADMIN_PASSWORD_HASH = os.environ.get("LOCAL_ADMIN_PASSWORD_HASH", "")

identity_provider_registry = IdentityProviderRegistry(os.environ.get("IDP_CONFIG_DIR", ".idp"))


async def get_identity_attributes(db: Session, email: str, provider_id: str | None) -> IdentityAttributes:
    if not provider_id:
        return IdentityAttributes()
    meta = db.get(IdentityProviderDB, provider_id)
    if meta is None or not meta.is_enabled:
        return IdentityAttributes()
    normalized_email = email.strip().casefold()
    cache_key = f"{provider_id}:{normalized_email}"
    try:
        provider = identity_provider_registry.get(provider_id)
        if provider is None:
            return IdentityAttributes()
        fresh = await provider.get_attributes(normalized_email)
        if not isinstance(fresh, IdentityAttributes):
            raise IdentityProviderUnavailable("Unsupported identity result")
        cached = db.get(IdentityCacheDB, cache_key)
        values = {"first_name": fresh.first_name[:256], "last_name": fresh.last_name[:256],
                  "title": fresh.title[:256], "phone": fresh.phone[:256],
                  "groups": sorted(fresh.groups), "claims": dict(fresh.claims),
                  "attributes": dict(fresh.attributes)}
        if cached is None:
            db.add(IdentityCacheDB(email=cache_key, **values))
        else:
            for key, value in values.items(): setattr(cached, key, value)
        db.commit()
        return fresh
    except Exception as exc:
        db.rollback()
        logger.warning("Identity provider %s unavailable; using provider-specific cache (%s)", provider_id, type(exc).__name__)
        cached = db.get(IdentityCacheDB, cache_key)
        if cached is None: return IdentityAttributes()
        return IdentityAttributes(first_name=cached.first_name or "", last_name=cached.last_name or "",
            title=cached.title or "", phone=cached.phone or "", groups=frozenset(cached.groups or []),
            claims=cached.claims or {}, attributes=cached.attributes or {})

signature_file_store = SignatureFileStore(
    os.environ.get("SIGNATURE_STORAGE_PATH", "./signatures")
)


# Pydantic Models
class DomainCreate(BaseModel):
    domain_name: str = Field(min_length=3, max_length=253)
    is_active: bool = True

class DomainUpdate(BaseModel):
    is_active: bool
    allow_overrides: bool
    inject_on_replies: bool
    trim_whitespace: bool
    strip_device_signatures: bool
    template_html: str = Field(max_length=65536)

class OverrideUpdate(BaseModel):
    html_content: str = Field(max_length=65536)

class AdminOverrideSave(BaseModel):
    user_email: str = Field(min_length=3, max_length=254)
    html_content: str = Field(max_length=65536)
    is_disabled: bool = False

class SignatureCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    domain_id: Optional[int] = None
    domain_ids: Optional[List[int]] = None
    mode: Literal["AUTO", "PROFESSIONAL", "INTERNAL"] = "AUTO"
    html_content: str = Field(min_length=1, max_length=65536)
    weight: int = Field(default=1, ge=1, le=1000)
    is_active: bool = True

class SignatureUpdateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    domain_id: Optional[int] = None
    domain_ids: Optional[List[int]] = None
    is_active: bool = True

class SignatureVariantUpdateRequest(BaseModel):
    weight: int = Field(ge=1, le=1000)
    is_enabled: bool

class SignatureVersionCreateRequest(BaseModel):
    html_content: str = Field(min_length=1, max_length=65536)

class SignatureVariantCreateRequest(BaseModel):
    pool_name: Literal["AUTO", "PROFESSIONAL", "INTERNAL"]
    weight: int = Field(default=1, ge=1, le=1000)

class SignatureRuleConditionRequest(BaseModel):
    kind: Literal["sender_equals", "domain_equals", "group_contains", "claim_equals", "attribute_equals"]
    key: str = Field(default="", max_length=255)
    value: str = Field(min_length=1, max_length=4096)

class SignatureRuleCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    priority: int = Field(default=0, ge=-100000, le=100000)
    action_mode: Literal["AUTO", "PROFESSIONAL", "INTERNAL", "NONE"] = "AUTO"
    signature_id: Optional[str] = None
    is_enabled: bool = True
    conditions: List[SignatureRuleConditionRequest] = Field(default_factory=list, max_length=64)

# 3. FastAPI App Initialization
app = FastAPI(title="OpenSigWeave API", version="1.0.0", docs_url=None, redoc_url=None, openapi_url=None)
session_secret = os.environ.get("SECRET_KEY", "")
if len(session_secret) < 32 or session_secret.lower() in {"changeme", "your_secure_random_string_here", "fallback-secret", "replace_with_random_secret_at_least_32_characters"}:
    raise RuntimeError("SECRET_KEY must be configured with at least 32 non-placeholder characters")
ENGINE_API_KEY = os.environ.get("ENGINE_API_KEY", "")
if len(ENGINE_API_KEY) < 32 or ENGINE_API_KEY.lower().startswith(("replace_", "changeme", "your_")):
    raise RuntimeError("ENGINE_API_KEY must be configured with a generated value of at least 32 characters")
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
        if request.url.path == "/login" or (
            "session" in request.scope
            and request.session.get("user")
            and not request.url.path.startswith(("/static/", "/branding/"))
        ):
            response.headers.setdefault("Cache-Control", "no-store, private")
        return response


class CSRFProtectionMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if request.method in {"POST", "PUT", "PATCH", "DELETE"} and request.session.get("user"):
            expected = request.session.get("csrf_token")
            supplied = request.headers.get("X-CSRF-Token", "")
            if not expected or not supplied or not hmac.compare_digest(expected, supplied):
                return JSONResponse({"detail": "CSRF validation failed"}, status_code=403)
        return await call_next(request)


# SessionMiddleware must wrap CSRFProtectionMiddleware so the session is available
# while validating the anti-CSRF token.
app.add_middleware(RequestBodyLimitMiddleware, max_body_size=1024 * 1024)
app.add_middleware(CSRFProtectionMiddleware)
app.add_middleware(
    SessionMiddleware,
    secret_key=session_secret,
    session_cookie="__Host-opensigweave-session",
    path="/",
    same_site="lax",
    https_only=True,
    max_age=3600,
)
# Validate explicit hostnames before accepting requests. In local-only mode there is
# no OIDC redirect URI from which to derive the canonical host.
extra_allowed_hosts = [
    host.strip().lower()
    for host in os.environ.get("APP_ALLOWED_HOSTS", "").split(",")
    if host.strip()
]
canonical_host = (urlsplit(os.environ.get("OIDC_REDIRECT_URI", "")).hostname or "").lower()
allowed_hosts = list(dict.fromkeys(([canonical_host] if OIDC_ENABLED and canonical_host else []) + extra_allowed_hosts))
if not allowed_hosts or any(
    host == "*" or "/" in host or "@" in host or not host
    for host in allowed_hosts
):
    raise RuntimeError("Configure APP_ALLOWED_HOSTS with explicit hostnames; wildcard hosts are not allowed")
if OIDC_ENABLED:
    oidc_discovery_url = os.environ.get("OIDC_DISCOVERY_URL") or (
        f"{os.environ.get('AUTHENTIK_URL', '').rstrip('/')}/application/o/"
        f"{os.environ.get('AUTHENTIK_SLUG', '')}/.well-known/openid-configuration"
    )
    oidc_client_id = os.environ.get("OIDC_CLIENT_ID") or os.environ.get("CLIENT_ID")
    oidc_client_secret = os.environ.get("OIDC_CLIENT_SECRET") or os.environ.get("CLIENT_SECRET")
    oidc_redirect_uri = os.environ.get("OIDC_REDIRECT_URI", "")
    redirect_parts = urlsplit(oidc_redirect_uri)
    if (
        not isinstance(oidc_discovery_url, str)
        or not oidc_discovery_url.startswith("https://")
        or not oidc_client_id
        or not oidc_client_secret
        or str(oidc_client_id).lower().startswith(("your_", "replace_"))
        or str(oidc_client_secret).lower().startswith(("your_", "replace_"))
        or redirect_parts.scheme != "https"
        or not redirect_parts.hostname
        or redirect_parts.username is not None
        or redirect_parts.password is not None
        or redirect_parts.query
        or redirect_parts.fragment
        or not redirect_parts.path.endswith("/auth")
    ):
        raise RuntimeError("Configure HTTPS OIDC discovery, client credentials and an explicit OIDC_REDIRECT_URI ending in /auth")
    oauth.register(
        name="oidc",
        server_metadata_url=oidc_discovery_url,
        client_id=oidc_client_id,
        client_secret=oidc_client_secret,
        client_kwargs={"scope": os.environ.get("OIDC_SCOPES", "openid email profile groups")}
    )
else:
    if not is_valid_email(LOCAL_ADMIN_EMAIL):
        raise RuntimeError("LOCAL_ADMIN_EMAIL must be a valid email address when OIDC_ENABLED=false")
    if not is_valid_password_hash(LOCAL_ADMIN_PASSWORD_HASH):
        raise RuntimeError("LOCAL_ADMIN_PASSWORD_HASH must be a valid scrypt hash when OIDC_ENABLED=false")
    redirect_parts = urlsplit("")

# Host validation runs before rate limiting so invalid Host requests cannot consume buckets.
app.add_middleware(RateLimitMiddleware)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=allowed_hosts)

# SecurityHeadersMiddleware wraps both middlewares, including 400/429/503 responses.
app.add_middleware(SecurityHeadersMiddleware)

templates = Jinja2Templates(directory="templates")
app.mount("/branding", StaticFiles(directory="branding"), name="branding")
app.mount("/static", StaticFiles(directory="static"), name="static")

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
    return isinstance(groups, (list, tuple, set)) and "signature-admins" in groups


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
@app.get("/login")
async def login(request: Request):
    if OIDC_ENABLED:
        return await oauth.oidc.authorize_redirect(request, os.environ["OIDC_REDIRECT_URI"])
    csrf_token = request.session.setdefault("login_csrf", secrets.token_urlsafe(32))
    return templates.TemplateResponse(request, "login.html", {
        "request": request,
        "csrf_token": csrf_token,
        "error": request.query_params.get("error") == "invalid_credentials",
    })


@app.post("/login")
async def local_login(request: Request):
    if OIDC_ENABLED:
        raise HTTPException(status_code=404, detail="Local authentication is disabled")
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
    submitted_password = form.get("password", "")
    submitted_email = submitted_email.strip().casefold() if isinstance(submitted_email, str) else ""
    password_ok = verify_local_admin_password(submitted_password, LOCAL_ADMIN_PASSWORD_HASH)
    email_ok = hmac.compare_digest(submitted_email.encode("utf-8"), LOCAL_ADMIN_EMAIL.encode("utf-8"))
    if not (password_ok and email_ok):
        request.session.clear()
        csrf_token = secrets.token_urlsafe(32)
        request.session["login_csrf"] = csrf_token
        return templates.TemplateResponse(request, "login.html", {
            "request": request, "csrf_token": csrf_token, "error": True,
        }, status_code=401)

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


async def auth(request: Request):
    try:
        token = await oauth.oidc.authorize_access_token(request)
        user = token.get("userinfo")
        if not isinstance(user, dict) or not user.get("sub"):
            raise ValueError("OIDC userinfo is missing the subject")
        email = user.get("email")
        if (
            not isinstance(email, str)
            or len(email) > 254
            or email.count("@") != 1
            or any(ch.isspace() for ch in email)
            or user.get("email_verified") is not True
        ):
            raise ValueError("OIDC account email is missing, invalid or unverified")
        local_part, email_domain = email.rsplit("@", 1)
        if not local_part or "." not in email_domain or not email_domain:
            raise ValueError("OIDC account email is invalid")
        groups = user.get("groups", [])
        if (
            not isinstance(groups, list)
            or len(groups) > 128
            or not all(isinstance(group, str) and 0 < len(group) <= 255 for group in groups)
        ):
            raise ValueError("OIDC groups claim has an invalid format")
        if len(str(user["sub"])) > 255:
            raise ValueError("OIDC subject exceeds the supported length")
        request.session.clear()
        raw_attributes = user.get("attributes")
        if not isinstance(raw_attributes, dict):
            raw_attributes = {}
        safe_attributes = {
            key: value[:256] if isinstance(value, str) else ""
            for key in ("phone", "title")
            if isinstance((value := raw_attributes.get(key)), str)
        }
        request.session["user"] = {
            "sub": str(user["sub"])[:255],
            "email": email.strip(),
            "email_verified": True,
            "name": str(user.get("name") or "")[:256],
            "groups": groups,
            "attributes": safe_attributes,
            "auth_source": "oidc",
        }
    except Exception:
        request.session.clear()
        return RedirectResponse(url="/login?error=authentication_failed", status_code=303)
    return RedirectResponse(url="/", status_code=303)


if OIDC_ENABLED:
    app.add_api_route("/auth", auth, methods=["GET"], name="oidc_callback")


@app.post("/logout")
async def logout(request: Request):
    request.session.clear()
    if not OIDC_ENABLED:
        return {"logout_url": "/login"}
    authentik_url = os.environ.get("AUTHENTIK_URL", "").rstrip("/")
    slug = os.environ.get("AUTHENTIK_SLUG", "")
    return_url = urlunsplit((
        redirect_parts.scheme, redirect_parts.netloc,
        redirect_parts.path[:-len("auth")], "", "",
    ))
    logout_url = f"{authentik_url}/application/o/{slug}/end-session/?post_logout_redirect_uri={return_url}"
    return {"logout_url": logout_url}


@app.get("/")
def read_root(request: Request, db: Session = Depends(get_db)):
    user = request.session.get('user')
    if not user: return RedirectResponse(url='/login')
    csrf_token = request.session.setdefault("csrf_token", secrets.token_urlsafe(32))

    user_groups = user.get('groups', [])
    is_admin = 'signature-admins' in user_groups
    is_user = 'signature-users' in user_groups
    if not is_admin and not is_user:
        return templates.TemplateResponse(request, "error.html", {"request": request, "message": "You do not have permission to access the Signature Portal.", "csrf_token": csrf_token})

    has_logo = os.path.isfile("branding/logo.png")
    has_favicon = os.path.isfile("branding/favicon.ico")
    
    app_name = "OpenSigWeave"
    app_color = "#60a5fa"
    
    settings_path = "branding/settings.json"
    if os.path.isfile(settings_path):
        try:
            with open(settings_path, "r") as f:
                settings = json.load(f)
                if "app_name" in settings: app_name = settings["app_name"]
                if "app_color" in settings: app_color = settings["app_color"]
        except Exception: pass
            
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
    override_obj = db.query(UserOverrideDB).filter(func.lower(UserOverrideDB.user_email) == email).first()
    
    can_edit = False
    lock_reason = "Your email domain is not registered in this system."
    
    if override_obj and override_obj.is_disabled:
        lock_reason = "Your signature has been explicitly disabled by an administrator."
    elif domain_obj:
        if not domain_obj.is_active:
            lock_reason = "Signatures for your domain are currently disabled by the administrator."
        elif not domain_obj.allow_overrides:
            lock_reason = "Custom overrides have been disabled for your domain. The master template will be used."
        else:
            can_edit = True
            lock_reason = ""
    
    raw_template = sanitize_signature_html(
        (domain_obj.template_html if domain_obj else None)
        or "<p>No active domain template found for your email domain.</p>"
    )
    
    try:
        parsed_template = render_signature_template(raw_template, {
            "first_name": first_name,
            "last_name": last_name,
            "title": title,
            "phone": phone,
            "email": email,
            "domain_name": domain_part,
        })
    except TemplateError:
        parsed_template = "<p>Signature template is invalid. Please contact an administrator.</p>"

    return templates.TemplateResponse(request, "index.html", {
        "request": request, "has_logo": has_logo, "has_favicon": has_favicon, "app_name": app_name, "app_color": app_color,
        "user": user, "is_admin": is_admin, "authentik_settings_url": (
            f"{os.environ.get('AUTHENTIK_URL', '')}/if/user/#/settings"
            if OIDC_ENABLED else None
        ),
        "phone": phone, "title": title, "first_name": first_name, "last_name": last_name,
        "domain_template": parsed_template, "can_edit": can_edit, "lock_reason": lock_reason, "csrf_token": csrf_token,
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
            "template_html": sanitize_signature_html(item.template_html or ""),
        }
        for item in domains
    ]

@app.post("/domains/")
def create_domain(domain: DomainCreate, request: Request, db: Session = Depends(get_db)):
    if not is_admin_user(request):
        raise HTTPException(status_code=403, detail="Admins only")
    normalized_domain = normalize_domain(domain.domain_name)
    if domain.identity_provider_id and db.get(IdentityProviderDB, domain.identity_provider_id) is None:
        raise HTTPException(status_code=422, detail="Identity provider not found")
    db_domain = DomainDB(domain_name=normalized_domain, is_active=domain.is_active, identity_provider_id=domain.identity_provider_id)
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
    db_domain.template_html = sanitize_signature_html(domain_update.template_html)
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


@app.get("/api/my-signature")
def get_my_signature(request: Request, db: Session = Depends(get_db)):
    user = require_signature_user(request)
    email = normalized_user_email(user)
    override = db.query(UserOverrideDB).filter(func.lower(UserOverrideDB.user_email) == email).first()
    return {"html_content": sanitize_signature_html(override.html_content) if override else ""}

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
    if override: override.html_content = safe_html
    else: db.add(UserOverrideDB(user_email=email, html_content=safe_html))
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

@app.get("/api/admin/overrides/{domain_name}")
def get_domain_overrides(domain_name: str, request: Request, db: Session = Depends(get_db)):
    if not is_admin_user(request): raise HTTPException(status_code=403, detail="Admins only")
    normalized_domain = normalize_domain(domain_name)
    overrides = db.query(UserOverrideDB).filter(func.lower(UserOverrideDB.user_email).endswith("@" + normalized_domain, autoescape=True)).all()
    return [
        {
            "id": item.id,
            "user_email": item.user_email,
            "html_content": sanitize_signature_html(item.html_content),
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
        override.html_content = safe_html
        override.is_disabled = req.is_disabled
    else: 
        db.add(UserOverrideDB(user_email=normalized_email, html_content=safe_html, is_disabled=req.is_disabled))
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
    return [{"id": row.id, "display_name": row.display_name, "provider_type": row.provider_type,
             "is_enabled": row.is_enabled, "configured": row.id in disk} for row in rows]


class IdentityProviderCreateRequest(BaseModel):
    id: str = Field(min_length=1, max_length=64)
    display_name: str = Field(min_length=1, max_length=120)
    provider_type: Literal["authentik", "ldap"]
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
        logger.warning("Identity provider test failed for %s (%s)", provider_id, type(exc).__name__)
        raise HTTPException(status_code=422, detail="Connection test failed. Check settings and application logs.")
    return {"status": "connected"}


@app.delete("/api/admin/identity-providers/{provider_id}")
def delete_identity_provider(provider_id: str, request: Request, db: Session = Depends(get_db)):
    if not is_admin_user(request): raise HTTPException(status_code=403, detail="Admins only")
    row = db.get(IdentityProviderDB, provider_id)
    if row is None: raise HTTPException(status_code=404, detail="Identity provider not found")
    if db.query(DomainDB).filter(DomainDB.identity_provider_id == provider_id).first():
        raise HTTPException(status_code=409, detail="Unassign this provider from all domains before deleting it.")
    identity_provider_registry.delete(provider_id)
    db.delete(row); db.commit()
    return {"status": "deleted"}


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
            "mode": item.mode,
            "is_active": item.is_active,
            "versions": db.query(SignatureVersionDB)
                .filter(SignatureVersionDB.signature_id == item.id)
                .count(),
            "variants": db.query(SignatureVariantDB)
                .filter(SignatureVariantDB.signature_id == item.id)
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
        signature, version, variant = create_signature(
            db,
            signature_file_store,
            name=payload.name,
            domain_id=payload.domain_id,
            domain_ids=payload.domain_ids,
            mode=SignatureMode(payload.mode),
            html_content=payload.html_content,
            weight=payload.weight,
            is_active=payload.is_active,
            created_by=user.get("email"),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {
        "id": signature.id,
        "version_id": version.id,
        "variant_id": variant.id,
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


@app.get("/api/admin/signatures/{signature_id}/variants")
def list_signature_variants(signature_id: str, request: Request, db: Session = Depends(get_db)):
    if not is_admin_user(request):
        raise HTTPException(status_code=403, detail="Admins only")
    if db.get(SignatureDB, signature_id) is None:
        raise HTTPException(status_code=404, detail="Signature not found")
    variants = (
        db.query(SignatureVariantDB)
        .filter(SignatureVariantDB.signature_id == signature_id)
        .order_by(SignatureVariantDB.pool_name.asc(), SignatureVariantDB.id.asc())
        .all()
    )
    return [{"id": item.id, "pool_name": item.pool_name, "weight": item.weight,
             "is_enabled": item.is_enabled} for item in variants]


@app.put("/api/admin/signature-variants/{variant_id}")
def update_signature_variant_endpoint(
    variant_id: str,
    payload: SignatureVariantUpdateRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    if not is_admin_user(request):
        raise HTTPException(status_code=403, detail="Admins only")
    try:
        variant = update_signature_variant(
            db, variant_id, weight=payload.weight, is_enabled=payload.is_enabled,
        )
    except SignatureNotFoundError:
        raise HTTPException(status_code=404, detail="Signature variant not found")
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return {"id": variant.id, "weight": variant.weight, "is_enabled": variant.is_enabled}


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
    return [
        {
            "id": item.id,
            "version_number": item.version_number,
            "sha256": item.sha256,
            "size_bytes": item.size_bytes,
            "created_by": item.created_by,
            "created_at": item.created_at,
        }
        for item in versions
    ]


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


@app.post("/api/admin/signatures/{signature_id}/variants", status_code=201)
def create_signature_variant_endpoint(
    signature_id: str,
    payload: SignatureVariantCreateRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    if not is_admin_user(request):
        raise HTTPException(status_code=403, detail="Admins only")
    try:
        variant = add_signature_variant(
            db,
            signature_id,
            pool_name=SignatureMode(payload.pool_name),
            weight=payload.weight,
        )
    except SignatureNotFoundError:
        raise HTTPException(status_code=404, detail="Signature not found")
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return {"id": variant.id, "pool_name": variant.pool_name, "weight": variant.weight}


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
    if payload.signature_id:
        target_signature = db.get(SignatureDB, payload.signature_id)
        if target_signature is None:
            raise HTTPException(status_code=404, detail="Signature not found")
        if target_signature.mode != payload.action_mode:
            raise HTTPException(status_code=422, detail="Rule action mode must match the targeted signature mode")
        if not target_signature.is_active:
            raise HTTPException(status_code=422, detail="Inactive signatures cannot be rule targets")
    rule = SignatureRuleDB(
        id=uuid.uuid4().hex,
        name=payload.name.strip(),
        priority=payload.priority,
        action_mode=payload.action_mode,
        signature_id=payload.signature_id,
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
    if payload.signature_id:
        target_signature = db.get(SignatureDB, payload.signature_id)
        if target_signature is None:
            raise HTTPException(status_code=404, detail="Signature not found")
        if target_signature.mode != payload.action_mode:
            raise HTTPException(status_code=422, detail="Rule action mode must match the targeted signature mode")
        if not target_signature.is_active:
            raise HTTPException(status_code=422, detail="Inactive signatures cannot be rule targets")
    try:
        rule.name = payload.name.strip()
        rule.priority = payload.priority
        rule.action_mode = payload.action_mode
        rule.signature_id = payload.signature_id
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
# THE RSPAMD COMPILATION ENGINE
# ==========================================
@app.get("/api/signature/{email}")
async def get_rspamd_signature(
    email: str,
    response: Response,
    x_engine_key: Optional[str] = Header(None),
    x_signature_mode: Optional[str] = Header(None, alias="X-OpenSigWeave-Mode"),
    x_signature_name: Optional[str] = Header(None, alias="X-OpenSigWeave-Signature"),
    x_signature_code: Optional[str] = Header(None, alias="X-OpenSigWeave-Signature-Code"),
    db: Session = Depends(get_db)
):
    response.headers["Cache-Control"] = "no-store, private"
    if not ENGINE_API_KEY or not hmac.compare_digest(x_engine_key or "", ENGINE_API_KEY):
        raise HTTPException(status_code=401, detail="Unauthorized Engine Request")

    normalized_email = email.strip().casefold()
    if normalized_email.count("@") != 1 or any(ch.isspace() for ch in normalized_email):
        raise HTTPException(status_code=400, detail="Invalid sender email")
    domain_part = normalized_email.rsplit("@", 1)[-1]
    domain_obj = db.query(DomainDB).filter(func.lower(DomainDB.domain_name) == domain_part).first()
    empty_response = {
        "html": "",
        "inject_on_replies": bool(domain_obj and domain_obj.inject_on_replies),
        "trim_whitespace": bool(domain_obj.trim_whitespace) if domain_obj else True,
        "strip_device_signatures": bool(domain_obj.strip_device_signatures) if domain_obj else False,
    }
    if not domain_obj or not domain_obj.is_active:
        return empty_response

    override = db.query(UserOverrideDB).filter(
        func.lower(UserOverrideDB.user_email) == normalized_email
    ).first()
    if override and override.is_disabled:
        return empty_response

    selected_from_variant = False
    signature_id = None
    marker_requested = isinstance(x_signature_name, str) or isinstance(x_signature_code, str)
    mode_candidate = x_signature_mode.strip().upper() if isinstance(x_signature_mode, str) else ""
    explicit_mode = mode_candidate in {"AUTO", "PROFESSIONAL", "INTERNAL", "NONE"}
    # Invalid mode headers are not fatal: continue with a body marker or automatic rules.
    x_signature_mode = mode_candidate if explicit_mode else None
    if marker_requested and not explicit_mode:
        # A text marker is a selector, not an authentication mechanism. It is
        # accepted only when its four-character SHA-256 check matches exactly.
        marker_name = (x_signature_name or "").strip()
        marker_code = (x_signature_code or "").strip().casefold()
        expected_code = hashlib.sha256(marker_name.casefold().encode("utf-8")).hexdigest()[:4]
        if (not marker_name or len(marker_name) > 255 or len(marker_code) != 4
                or not hmac.compare_digest(marker_code, expected_code)):
            return empty_response
        identity_attributes = await get_identity_attributes(db, normalized_email, domain_obj.identity_provider_id)
        matching_signatures = (
            db.query(SignatureDB)
            .filter(
                SignatureDB.is_active.is_(True),
                (SignatureDB.id.in_(db.query(SignatureDomainDB.signature_id).filter(SignatureDomainDB.domain_id == domain_obj.id)) | ~SignatureDB.id.in_(db.query(SignatureDomainDB.signature_id))),
            )
            .all()
        )
        matching_signatures = [
            item for item in matching_signatures
            if item.name.casefold() == marker_name.casefold()
        ]
        assigned_ids = {row.signature_id for row in db.query(SignatureDomainDB).filter(SignatureDomainDB.domain_id == domain_obj.id).all()}
        any_assigned_ids = {row.signature_id for row in db.query(SignatureDomainDB).all()}
        domain_matches = [item for item in matching_signatures if item.id in assigned_ids]
        global_matches = [item for item in matching_signatures if item.id not in any_assigned_ids]
        # Prefer one exact-domain signature; otherwise accept exactly one global
        # signature. Duplicate names are ambiguous and must not select arbitrarily.
        candidates = domain_matches if domain_matches else global_matches
        if len(candidates) != 1:
            return empty_response
        selected_signature = candidates[0]
        latest_version = (
            db.query(SignatureVersionDB)
            .filter(SignatureVersionDB.signature_id == selected_signature.id)
            .order_by(SignatureVersionDB.version_number.desc())
            .first()
        )
        if latest_version is None:
            raise HTTPException(status_code=503, detail="Signature version metadata is missing")
        try:
            raw_template = signature_file_store.read(selected_signature.name, uuid.UUID(hex=latest_version.id), latest_version.sha256)
        except (SignatureStorageError, ValueError, TypeError):
            raise HTTPException(status_code=503, detail="Signature asset unavailable")
    elif override and domain_obj.allow_overrides and not explicit_mode:
        identity_attributes = await get_identity_attributes(db, normalized_email, domain_obj.identity_provider_id)
        raw_template = override.html_content
    else:
        identity_attributes = await get_identity_attributes(db, normalized_email, domain_obj.identity_provider_id)
        if explicit_mode:
            try:
                mode = resolve_mode(x_signature_mode)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc))
        else:
            context = RuleContext(
                sender=normalized_email,
                domain=domain_part,
                groups=identity_attributes.groups,
                claims=identity_attributes.claims,
                attributes=identity_attributes.attributes,
            )
            matched_rule = evaluate_rules(load_signature_rules(db), context)
            try:
                mode = resolve_mode(matched_rule.action if matched_rule else None)
            except ValueError as exc:
                raise HTTPException(status_code=500, detail="A stored signature rule has an invalid action")
            signature_id = matched_rule.signature_id if matched_rule else None

        if mode == SignatureMode.NONE:
            return empty_response

        selected_variant = select_and_record_variant(
            db,
            sender_email=normalized_email,
            pool_name=mode.value,
            domain_id=domain_obj.id,
            signature_id=signature_id,
        )
        if selected_variant:
            latest_version = (
                db.query(SignatureVersionDB)
                .filter(SignatureVersionDB.signature_id == selected_variant.signature_id)
                .order_by(SignatureVersionDB.version_number.desc())
                .first()
            )
            if latest_version:
                selected_signature = db.get(SignatureDB, selected_variant.signature_id)
                if selected_signature is None:
                    raise HTTPException(status_code=503, detail="Selected signature metadata is missing")
                try:
                    raw_template = signature_file_store.read(
                        selected_signature.name,
                        uuid.UUID(hex=latest_version.id),
                        latest_version.sha256,
                    )
                    selected_from_variant = True
                except (SignatureStorageError, ValueError, TypeError):
                    raise HTTPException(status_code=503, detail="Signature asset unavailable")
            else:
                raise HTTPException(status_code=503, detail="Signature version metadata is missing")
        elif mode == SignatureMode.AUTO and signature_id is None:
            raw_template = domain_obj.template_html
        else:
            return empty_response

    if not raw_template:
        return empty_response

    first_name = identity_attributes.first_name or normalized_email.split("@")[0].capitalize()
    last_name = identity_attributes.last_name
    title = identity_attributes.title
    phone = identity_attributes.phone

    try:
        raw_template = sanitize_signature_html(raw_template)
        parsed = render_signature_template(raw_template, {
            "first_name": first_name,
            "last_name": last_name,
            "title": title,
            "phone": phone,
            "email": normalized_email,
            "domain_name": domain_part,
        })
    except TemplateError:
        raise HTTPException(status_code=422, detail="Invalid signature template")

    if selected_from_variant:
        db.commit()

    return {
        "html": parsed,
        "inject_on_replies": domain_obj.inject_on_replies,
        "trim_whitespace": domain_obj.trim_whitespace,
        "strip_device_signatures": domain_obj.strip_device_signatures,
    }
