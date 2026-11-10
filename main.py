import os
import uuid
import json
import hmac
import secrets
from fastapi import FastAPI, Depends, HTTPException, Request, Header
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import List, Optional

from dotenv import load_dotenv
from starlette.middleware.sessions import SessionMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse
from authlib.integrations.starlette_client import OAuth
from jinja2 import TemplateError

load_dotenv()

# Database infrastructure is isolated from the application models and routes.
from database import get_db
from models import DomainDB, UserOverrideDB, SignatureVersionDB

from signature_renderer import render_signature_template
from identity.providers.authentik import AuthentikAttributeProvider
from identity.providers.base import AttributeProvider
from core.selection_repository import select_and_record_variant
from signature_storage import SignatureFileStore, SignatureStorageError
attribute_provider: AttributeProvider = AuthentikAttributeProvider(
    base_url=os.environ.get("AUTHENTIK_URL", ""),
    api_token=os.environ.get("AUTHENTIK_API_TOKEN", ""),
)

signature_file_store = SignatureFileStore(
    os.environ.get("SIGNATURE_STORAGE_PATH", "./signatures")
)


# Pydantic Models
class DomainCreate(BaseModel):
    domain_name: str
    is_active: bool = True

class DomainUpdate(BaseModel):
    is_active: bool
    allow_overrides: bool
    inject_on_replies: bool
    trim_whitespace: bool
    strip_device_signatures: bool
    template_html: str

class OverrideUpdate(BaseModel):
    html_content: str

class AdminOverrideSave(BaseModel):
    user_email: str
    html_content: str
    is_disabled: bool = False

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

def is_admin_user(request: Request):
    user = request.session.get('user')
    if not user: return False
    return 'signature-admins' in user.get('groups', [])

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
    
    domain_part = email.split('@')[-1] if '@' in email else ''
    domain_obj = db.query(DomainDB).filter(DomainDB.domain_name == domain_part).first()
    override_obj = db.query(UserOverrideDB).filter(UserOverrideDB.user_email == email).first()
    
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
    db_domain = DomainDB(domain_name=domain.domain_name, is_active=domain.is_active)
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
    db_domain.template_html = domain_update.template_html
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
    user = request.session.get('user')
    if not user: raise HTTPException(status_code=401)
    override = db.query(UserOverrideDB).filter(UserOverrideDB.user_email == user.get('email')).first()
    return {"html_content": override.html_content if override else ""}

@app.post("/api/my-signature")
def save_my_signature(req: OverrideUpdate, request: Request, db: Session = Depends(get_db)):
    user = request.session.get('user')
    if not user: raise HTTPException(status_code=401)
    email = user.get('email')
    override = db.query(UserOverrideDB).filter(UserOverrideDB.user_email == email).first()
    if override: override.html_content = req.html_content
    else: db.add(UserOverrideDB(user_email=email, html_content=req.html_content))
    db.commit()
    return {"status": "success"}

@app.delete("/api/my-signature")
def delete_my_signature(request: Request, db: Session = Depends(get_db)):
    user = request.session.get('user')
    if not user: raise HTTPException(status_code=401)
    email = user.get('email')
    override = db.query(UserOverrideDB).filter(UserOverrideDB.user_email == email).first()
    if override:
        db.delete(override)
        db.commit()
        return {"status": "deleted"}
    return {"status": "not_found"}

@app.get("/api/admin/overrides/{domain_name}")
def get_domain_overrides(domain_name: str, request: Request, db: Session = Depends(get_db)):
    if not is_admin_user(request): raise HTTPException(status_code=403, detail="Admins only")
    overrides = db.query(UserOverrideDB).filter(UserOverrideDB.user_email.endswith(f"@{domain_name}")).all()
    return overrides

@app.post("/api/admin/overrides")
def save_admin_override(req: AdminOverrideSave, request: Request, db: Session = Depends(get_db)):
    if not is_admin_user(request): raise HTTPException(status_code=403, detail="Admins only")
    override = db.query(UserOverrideDB).filter(UserOverrideDB.user_email == req.user_email).first()
    if override: 
        override.html_content = req.html_content
        override.is_disabled = req.is_disabled
    else: 
        db.add(UserOverrideDB(user_email=req.user_email, html_content=req.html_content, is_disabled=req.is_disabled))
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

# ==========================================
# THE RSPAMD COMPILATION ENGINE
# ==========================================
@app.get("/api/signature/{email}")
async def get_rspamd_signature(
    email: str,
    response: Response,
    x_engine_key: Optional[str] = Header(None),
    db: Session = Depends(get_db)
):
    response.headers["Cache-Control"] = "no-store, private"
    # --- SECURITY LOCK ---
    expected_key = ENGINE_API_KEY
    if not expected_key or x_engine_key != expected_key:
        raise HTTPException(status_code=401, detail="Unauthorized Engine Request")
        
    # 1. Look up Domain Rules
    domain_part = email.split('@')[-1] if '@' in email else ''
    domain_obj = db.query(DomainDB).filter(DomainDB.domain_name == domain_part).first()
    
    if not domain_obj or not domain_obj.is_active:
        return {"html": "", "inject_on_replies": False, "trim_whitespace": True, "strip_device_signatures": False}
        
    # 2. Check for User Override AND KILL SWITCH
    override = db.query(UserOverrideDB).filter(UserOverrideDB.user_email == email).first()
    
    if override and override.is_disabled:
        return {"html": "", "inject_on_replies": False, "trim_whitespace": True, "strip_device_signatures": False}
    
    selected_from_variant = False
    if override:
        raw_template = override.html_content
    else:
        selected_variant = select_and_record_variant(
            db,
            sender_email=email,
            pool_name="AUTO",
            domain_id=domain_obj.id,
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
                raw_template = domain_obj.template_html
        else:
            raw_template = domain_obj.template_html
        
    if not raw_template:
        return {
            "html": "", 
            "inject_on_replies": domain_obj.inject_on_replies,
            "trim_whitespace": domain_obj.trim_whitespace,
            "strip_device_signatures": domain_obj.strip_device_signatures
        }
        
    # 3. Resolve normalized identity attributes through the provider abstraction.
    identity_attributes = await attribute_provider.get_attributes(email)
    first_name = identity_attributes.first_name or email.split('@')[0].capitalize()
    last_name = identity_attributes.last_name
    title = identity_attributes.title
    phone = identity_attributes.phone

    # 4. Compile the HTML in a sandbox and escape every identity attribute.
    try:
        parsed = render_signature_template(raw_template, {
            "first_name": first_name,
            "last_name": last_name,
            "title": title,
            "phone": phone,
            "email": email,
            "domain_name": domain_part,
        })
    except TemplateError:
        raise HTTPException(status_code=422, detail="Invalid signature template")
    
    # Commit the selected variant only after the asset has been read and rendered.
    if selected_from_variant:
        db.commit()

    # 5. Deliver to Rspamd
    return {
        "html": parsed,
        "inject_on_replies": domain_obj.inject_on_replies,
        "trim_whitespace": domain_obj.trim_whitespace,
        "strip_device_signatures": domain_obj.strip_device_signatures
    }
