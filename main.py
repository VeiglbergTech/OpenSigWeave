import os
import uuid
import json
import hmac
import secrets
from fastapi import FastAPI, Depends, HTTPException, Request, Header, Response
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from pydantic import BaseModel, Field
from typing import List, Optional, Literal

from dotenv import load_dotenv
from starlette.middleware.sessions import SessionMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse
from authlib.integrations.starlette_client import OAuth
from jinja2 import TemplateError

load_dotenv()

# Database infrastructure is isolated from the application models and routes.
from database import get_db
from models import DomainDB, UserOverrideDB, SignatureDB, SignatureVariantDB, SignatureVersionDB, SignatureRuleDB, SignatureRuleConditionDB

from signature_renderer import render_signature_template
from identity.providers.authentik import AuthentikAttributeProvider
from identity.providers.base import AttributeProvider
from core.selection_repository import select_and_record_variant
from core.modes import SignatureMode, resolve_mode
from core.rules import RuleContext, evaluate_rules
from core.rule_repository import load_signature_rules
from signature_catalog import SignatureNotFoundError, add_signature_variant, add_signature_version, create_signature, update_signature, update_signature_variant
from signature_storage import SignatureFileStore, SignatureStorageError
from signature_sanitizer import sanitize_signature_html
attribute_provider: AttributeProvider = AuthentikAttributeProvider(
    base_url=os.environ.get("AUTHENTIK_URL", ""),
    api_token=os.environ.get("AUTHENTIK_API_TOKEN", ""),
)

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
    mode: Literal["AUTO", "PROFESSIONAL", "INTERNAL"] = "AUTO"
    html_content: str = Field(min_length=1, max_length=65536)
    weight: int = Field(default=1, ge=1, le=1000)
    is_active: bool = True

class SignatureUpdateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    domain_id: Optional[int] = None
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
    conditions: List[SignatureRuleConditionRequest] = Field(default_factory=list)

# 3. FastAPI App Initialization
app = FastAPI(title="OpenSigWeave API", version="1.0.0", docs_url=None, redoc_url=None, openapi_url=None)
session_secret = os.environ.get("SECRET_KEY", "")
if len(session_secret) < 32 or session_secret.lower() in {"changeme", "your_secure_random_string_here", "fallback-secret", "replace_with_random_secret_at_least_32_characters"}:
    raise RuntimeError("SECRET_KEY must be configured with at least 32 non-placeholder characters")
ENGINE_API_KEY = os.environ.get("ENGINE_API_KEY", "")
if len(ENGINE_API_KEY) < 32 or ENGINE_API_KEY.lower().startswith(("replace_", "changeme", "your_")):
    raise RuntimeError("ENGINE_API_KEY must be configured with a generated value of at least 32 characters")
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
app.add_middleware(CSRFProtectionMiddleware)
app.add_middleware(SessionMiddleware, secret_key=session_secret, same_site="lax", https_only=True)

oauth = OAuth()
oidc_discovery_url = os.environ.get("OIDC_DISCOVERY_URL") or (
    f"{os.environ.get('AUTHENTIK_URL', '').rstrip('/')}/application/o/"
    f"{os.environ.get('AUTHENTIK_SLUG', '')}/.well-known/openid-configuration"
)
oidc_client_id = os.environ.get("OIDC_CLIENT_ID") or os.environ.get("CLIENT_ID")
oidc_client_secret = os.environ.get("OIDC_CLIENT_SECRET") or os.environ.get("CLIENT_SECRET")
if (
    not isinstance(oidc_discovery_url, str)
    or not oidc_discovery_url.startswith("https://")
    or not oidc_client_id
    or not oidc_client_secret
    or str(oidc_client_id).lower().startswith(("your_", "replace_"))
    or str(oidc_client_secret).lower().startswith(("your_", "replace_"))
):
    raise RuntimeError("Configure an HTTPS OIDC discovery URL, OIDC_CLIENT_ID and OIDC_CLIENT_SECRET")
oauth.register(
    name="oidc",
    server_metadata_url=oidc_discovery_url,
    client_id=oidc_client_id,
    client_secret=oidc_client_secret,
    client_kwargs={"scope": os.environ.get("OIDC_SCOPES", "openid email profile groups")}
)

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
        or not all(ch.isalnum() or ch == "-" for ch in label)
        for label in normalized.split(".")
    ):
        raise HTTPException(status_code=422, detail="Invalid domain name")
    return normalized

# --- Web UI Routes ---
@app.get("/login")
async def login(request: Request):
    redirect_uri = request.url_for('auth').replace(scheme='https')
    return await oauth.oidc.authorize_redirect(request, str(redirect_uri))

@app.get("/auth")
async def auth(request: Request):
    try:
        token = await oauth.oidc.authorize_access_token(request)
        user = token.get('userinfo')
        if user: request.session['user'] = user
    except Exception as e:
        print(f"Login failed: {e}")
    return RedirectResponse(url='/')

@app.post("/logout")
async def logout(request: Request):
    request.session.clear()
    authentik_url = os.environ.get('AUTHENTIK_URL', '').rstrip('/')
    slug = os.environ.get('AUTHENTIK_SLUG', '')
    return_url = str(request.url_for('read_root')).replace('http://', 'https://')
    logout_url = f"{authentik_url}/application/o/{slug}/end-session/?post_logout_redirect_uri={return_url}"
    return {"logout_url": logout_url}

@app.get("/")
def read_root(request: Request, db: Session = Depends(get_db)):
    user = request.session.get('user')
    if not user: return RedirectResponse(url='/login')
        
    user_groups = user.get('groups', [])
    is_admin = 'signature-admins' in user_groups
    is_user = 'signature-users' in user_groups
    if not is_admin and not is_user:
        return templates.TemplateResponse("error.html", {"request": request, "message": "You do not have permission to access the Signature Portal."})

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
    domain_obj = db.query(DomainDB).filter(DomainDB.domain_name.ilike(domain_part)).first()
    override_obj = db.query(UserOverrideDB).filter(UserOverrideDB.user_email.ilike(email)).first()
    
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
    
    raw_template = (domain_obj.template_html if domain_obj else None) or "<p>No active domain template found for your email domain.</p>"
    
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

    csrf_token = request.session.setdefault("csrf_token", secrets.token_urlsafe(32))

    return templates.TemplateResponse("index.html", {
        "request": request, "has_logo": has_logo, "has_favicon": has_favicon, "app_name": app_name, "app_color": app_color,
        "user": user, "is_admin": is_admin, "authentik_settings_url": f"{os.environ.get('AUTHENTIK_URL', '')}/if/user/#/settings",
        "phone": phone, "title": title, "first_name": first_name, "last_name": last_name,
        "domain_template": parsed_template, "can_edit": can_edit, "lock_reason": lock_reason, "csrf_token": csrf_token
    })

# --- API Endpoints ---
@app.get("/domains/")
def read_domains(request: Request, db: Session = Depends(get_db)):
    if not is_admin_user(request):
        raise HTTPException(status_code=403, detail="Admins only")
    return db.query(DomainDB).all()

@app.post("/domains/")
def create_domain(domain: DomainCreate, request: Request, db: Session = Depends(get_db)):
    if not is_admin_user(request):
        raise HTTPException(status_code=403, detail="Admins only")
    normalized_domain = normalize_domain(domain.domain_name)
    db_domain = DomainDB(domain_name=normalized_domain, is_active=domain.is_active)
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

@app.get("/api/my-signature")
def get_my_signature(request: Request, db: Session = Depends(get_db)):
    user = require_signature_user(request)
    email = normalized_user_email(user)
    override = db.query(UserOverrideDB).filter(UserOverrideDB.user_email == email).first()
    return {"html_content": override.html_content if override else ""}

@app.post("/api/my-signature")
def save_my_signature(req: OverrideUpdate, request: Request, db: Session = Depends(get_db)):
    user = require_signature_user(request)
    email = normalized_user_email(user)
    domain_part = email.rsplit("@", 1)[-1]
    domain_obj = db.query(DomainDB).filter(DomainDB.domain_name == domain_part).first()
    if not domain_obj or not domain_obj.is_active or not domain_obj.allow_overrides:
        raise HTTPException(status_code=403, detail="Personal signature overrides are disabled for this domain")
    override = db.query(UserOverrideDB).filter(UserOverrideDB.user_email == email).first()
    if override and override.is_disabled:
        raise HTTPException(status_code=403, detail="Your personal signature has been disabled by an administrator")
    safe_html = sanitize_signature_html(req.html_content)
    if override: override.html_content = safe_html
    else: db.add(UserOverrideDB(user_email=email, html_content=safe_html))
    db.commit()
    return {"status": "success"}

@app.delete("/api/my-signature")
def delete_my_signature(request: Request, db: Session = Depends(get_db)):
    user = request.session.get('user')
    if not user: raise HTTPException(status_code=401)
    email = user.get("email")
    email = email.strip().casefold() if isinstance(email, str) else ""
    override = db.query(UserOverrideDB).filter(UserOverrideDB.user_email.ilike(email)).first()
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
    overrides = db.query(UserOverrideDB).filter(UserOverrideDB.user_email.endswith("@" + normalized_domain, autoescape=True)).all()
    return overrides

@app.post("/api/admin/overrides")
def save_admin_override(req: AdminOverrideSave, request: Request, db: Session = Depends(get_db)):
    if not is_admin_user(request): raise HTTPException(status_code=403, detail="Admins only")
    normalized_email = req.user_email.strip().casefold()
    if normalized_email.count("@") != 1 or any(ch.isspace() for ch in normalized_email):
        raise HTTPException(status_code=422, detail="Invalid email address")
    safe_html = sanitize_signature_html(req.html_content)
    override = db.query(UserOverrideDB).filter(UserOverrideDB.user_email == normalized_email).first()
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


@app.get("/api/admin/signatures")
def list_signatures(request: Request, db: Session = Depends(get_db)):
    if not is_admin_user(request):
        raise HTTPException(status_code=403, detail="Admins only")
    signatures = db.query(SignatureDB).order_by(SignatureDB.name.asc()).all()
    return [
        {
            "id": item.id,
            "name": item.name,
            "domain_id": item.domain_id,
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
            db, signature_id, name=payload.name,
            domain_id=payload.domain_id, is_active=payload.is_active,
        )
    except SignatureNotFoundError:
        raise HTTPException(status_code=404, detail="Signature not found")
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return {"id": signature.id, "name": signature.name, "domain_id": signature.domain_id,
            "scope": signature.scope, "is_active": signature.is_active}


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
    db: Session = Depends(get_db)
):
    response.headers["Cache-Control"] = "no-store, private"
    if not ENGINE_API_KEY or not hmac.compare_digest(x_engine_key or "", ENGINE_API_KEY):
        raise HTTPException(status_code=401, detail="Unauthorized Engine Request")

    normalized_email = email.strip().casefold()
    if normalized_email.count("@") != 1 or any(ch.isspace() for ch in normalized_email):
        raise HTTPException(status_code=400, detail="Invalid sender email")
    domain_part = normalized_email.rsplit("@", 1)[-1]
    domain_obj = db.query(DomainDB).filter(DomainDB.domain_name == domain_part).first()
    empty_response = {
        "html": "",
        "inject_on_replies": bool(domain_obj and domain_obj.inject_on_replies),
        "trim_whitespace": bool(domain_obj.trim_whitespace) if domain_obj else True,
        "strip_device_signatures": bool(domain_obj.strip_device_signatures) if domain_obj else False,
    }
    if not domain_obj or not domain_obj.is_active:
        return empty_response

    override = db.query(UserOverrideDB).filter(
        UserOverrideDB.user_email == normalized_email
    ).first()
    if override and override.is_disabled:
        return empty_response

    selected_from_variant = False
    signature_id = None
    explicit_mode = bool(x_signature_mode and x_signature_mode.strip())
    if override and domain_obj.allow_overrides and not explicit_mode:
        identity_attributes = await attribute_provider.get_attributes(normalized_email)
        raw_template = override.html_content
    else:
        identity_attributes = await attribute_provider.get_attributes(normalized_email)
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
                try:
                    raw_template = signature_file_store.read(
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
