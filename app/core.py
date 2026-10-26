"""Settings, database session, password/JWT helpers, auth dependencies."""
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import bcrypt
import jwt
from fastapi import Depends, HTTPException, Request, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "ONCM Workbench"
    app_env: str = "production"  # development = allow the insecure defaults below (local only)
    app_base_url: str = "http://localhost:8000"
    secret_key: str = "change-me"
    two_person_rule: bool = True
    reminder_days: str = "3,0"
    access_token_minutes: int = 480
    mfa_required_for_admins: bool = True  # admins must set up two-step verification before using admin pages

    # ---- Sign in with Microsoft (Entra ID, OpenID Connect). Off until all three are set. ----
    sso_microsoft_tenant_id: str = ""     # Directory (tenant) ID: only accounts of this organisation can sign in
    sso_microsoft_client_id: str = ""     # Application (client) ID of the app registration
    sso_microsoft_client_secret: str = ""
    sso_microsoft_domains: str = ""       # optional: only these email domains may use it (comma-separated)
    sso_required_domains: str = ""        # these email domains must use Microsoft: password sign-in is refused
    sso_trust_microsoft_mfa: bool = False # true only if Entra Conditional Access enforces MFA for this app

    master_admin_email: str = "admin@example.org"
    master_admin_password: str = "ChangeMe!12345"
    master_admin_name: str = "ONCM Master Admin"

    database_url: str = "sqlite:///./data/oncm.db"

    storage_backend: str = "local"  # local | s3
    local_storage_path: str = "./storage"
    max_upload_mb: int = 25
    allowed_extensions: str = "pdf,doc,docx,xls,xlsx,csv,png,jpg,jpeg,txt"
    s3_bucket: str = ""
    s3_region: str = "ap-south-1"
    s3_access_key_id: str = ""
    s3_secret_access_key: str = ""
    s3_endpoint_url: str = ""
    s3_kms_key_id: str = ""             # set = files encrypted with this KMS key (SSE-KMS); empty = SSE-S3 (AES256)

    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_use_tls: bool = True
    mail_from: str = "workbench@example.org"
    mail_reply_to: str = "oncm-support@example.org"
    oncm_shared_mailbox: str = "oncm-support@example.org"

    # optional: read access requests that arrive by email at the shared mailbox (any IMAP provider)
    imap_host: str = ""
    imap_port: int = 993
    imap_user: str = ""
    imap_password: str = ""
    imap_folder: str = "INBOX"
    review_reminder_days: int = 3   # remind reviewers when a document has been under review this long

    # ---- Admin alerts: a SEPARATE sender account (e.g. a Gmail) that tells admins what is happening ----
    # Gmail: smtp.gmail.com : 587 (STARTTLS) with an App Password (Google Account > Security > 2-Step Verification > App passwords).
    alert_smtp_host: str = ""
    alert_smtp_port: int = 587          # 587 = STARTTLS, 465 = implicit SSL
    alert_smtp_user: str = ""           # the sender mailbox, e.g. alerts@example.org
    alert_smtp_password: str = ""       # app password - NEVER commit this
    alert_smtp_use_tls: bool = True
    alert_mail_from: str = ""           # defaults to ALERT_SMTP_USER (Gmail always rewrites From to the signed-in account)
    alert_from_name: str = "ONCM Workbench Alerts"
    alert_recipients: str = ""          # comma-separated; seeded ONCE on first start, then managed in the UI

    # ---- Search: "db" = title and document code; "engine" = adds full text via adaptive-search-engine ----
    search_backend: str = "db"
    search_engine_url: str = "http://localhost:8080"
    search_engine_token: str = ""
    # Virus scanning of every upload: "clamav" (clamd, see docker-compose.yml) or "off"
    malware_scan: str = "off"
    clamav_host: str = "clamav"
    clamav_port: int = 3310

    # OCR for scanned PDFs and images (needs the tesseract program; skipped when it is not installed)
    ocr_enabled: bool = True
    ocr_languages: str = "eng"          # tesseract codes joined by "+", e.g. eng+hin (install tesseract-ocr-hin)
    ocr_max_pages: int = 30             # scanned pages read per document


settings = Settings()

_DEFAULT_SECRET_KEYS = {"", "change-me", "change-me-to-a-long-random-string"}
_DEFAULT_MASTER_PASSWORD = "ChangeMe!12345"


def check_secrets(s: Settings) -> list[str]:
    """Insecure-default problems. Raises outside APP_ENV=development (anyone could forge tokens or sign in as Master
    Admin); returns them in development so the caller can log a warning."""
    problems = []
    if s.secret_key in _DEFAULT_SECRET_KEYS or len(s.secret_key) < 32:
        problems.append("SECRET_KEY is a default or shorter than 32 characters "
                        '(generate one: python -c "import secrets; print(secrets.token_urlsafe(48))")')
    if s.master_admin_password == _DEFAULT_MASTER_PASSWORD:
        problems.append("MASTER_ADMIN_PASSWORD is the default ChangeMe!12345")
    if s.search_backend == "engine" and len(s.search_engine_token) < 32:
        problems.append("SEARCH_BACKEND=engine needs a SEARCH_ENGINE_TOKEN of 32+ characters (the engine holds document text)")
    if problems and s.app_env.strip().lower() != "development":
        raise RuntimeError("Refusing to start: " + "; ".join(problems) + ". Set them in .env, or APP_ENV=development for local use.")
    return problems


def _db_url(url: str) -> str:
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    if url.startswith("postgresql://"):
        url = "postgresql+psycopg2://" + url[len("postgresql://"):]
    return url


_url = _db_url(settings.database_url)
if _url.startswith("sqlite"):
    Path("data").mkdir(exist_ok=True)
    engine = create_engine(_url, connect_args={"check_same_thread": False})
else:
    engine = create_engine(_url, pool_pre_ping=True, pool_size=5, max_overflow=5)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ---------- passwords & tokens ----------
def hash_password(pw: str) -> str:
    return bcrypt.hashpw(pw.encode()[:72], bcrypt.gensalt()).decode()


def verify_password(pw: str, hashed: str | None) -> bool:
    if not hashed:
        return False
    try:
        return bcrypt.checkpw(pw.encode()[:72], hashed.encode())
    except ValueError:
        return False


def make_token(sub, typ: str = "access", minutes: int | None = None, **extra) -> str:
    exp = datetime.now(timezone.utc) + timedelta(minutes=minutes or settings.access_token_minutes)
    return jwt.encode({"sub": str(sub), "typ": typ, "exp": exp, **extra}, settings.secret_key, algorithm="HS256")


def decode_token(token: str, typ: str) -> dict:
    try:
        p = jwt.decode(token, settings.secret_key, algorithms=["HS256"])
    except jwt.PyJWTError:
        raise HTTPException(401, "Invalid or expired token")
    if p.get("typ") != typ:
        raise HTTPException(401, "Invalid token type")
    return p


# ---------- tiny in-memory rate limiter (use a gateway/Redis if you run many instances) ----------
_hits: dict[str, list[float]] = {}


def rate_limit(key: str, limit: int, window: int):
    now = time.time()
    q = [t for t in _hits.get(key, []) if now - t < window]
    if len(q) >= limit:
        raise HTTPException(429, "Too many attempts. Please wait and try again.")
    q.append(now)
    _hits[key] = q


# ---------- auth dependencies ----------
from app.models import User  # noqa: E402

bearer = HTTPBearer(auto_error=False)


SESSION_COOKIE = "oncm_session"
CSRF_HEADER = "x-oncm"  # browsers only let our own page's JavaScript set custom headers on cross-origin-safe requests


def session_token(u: User, ext_mfa: bool = False) -> str:
    # ext_mfa: the identity provider (Microsoft) already did multi-factor authentication for this session
    return make_token(u.id, sv=u.session_version or 0, **({"xmfa": True} if ext_mfa else {}))


def set_session_cookie(response: Response, u: User, ext_mfa: bool = False):
    """The browser session: HttpOnly (page scripts cannot read it), Secure on HTTPS, SameSite=Strict (not sent by
    requests started on other sites), scoped to /api."""
    response.set_cookie(SESSION_COOKIE, session_token(u, ext_mfa), max_age=settings.access_token_minutes * 60, path="/api",
                        httponly=True, secure=settings.app_base_url.startswith("https://"), samesite="strict")


def clear_session_cookie(response: Response):
    response.delete_cookie(SESSION_COOKIE, path="/api", httponly=True,
                           secure=settings.app_base_url.startswith("https://"), samesite="strict")


def current_user(request: Request, cred: HTTPAuthorizationCredentials | None = Depends(bearer),
                 db: Session = Depends(get_db)) -> User:
    """Bearer token (API clients, scripts) or the session cookie (the portal). Cookie-authenticated changes must
    also carry the X-ONCM header: a form or script on another site cannot add it (CSRF)."""
    if cred:
        token = cred.credentials
    else:
        token = request.cookies.get(SESSION_COOKIE)
        if not token:
            raise HTTPException(401, "Not authenticated")
        if request.method not in ("GET", "HEAD", "OPTIONS") and request.headers.get(CSRF_HEADER) != "1":
            raise HTTPException(403, "Missing request header (CSRF protection)")
    p = decode_token(token, "access")
    u = db.get(User, int(p["sub"]))
    if not u or not u.is_active:
        raise HTTPException(401, "Account inactive")
    if p.get("sv", 0) != (u.session_version or 0):
        raise HTTPException(401, "Session ended. Please sign in again.")
    u.session_claims = p  # not stored: lets mfa_setup_required see how this session was established
    return u


def mfa_setup_required(u: User) -> bool:
    ext_mfa = bool(getattr(u, "session_claims", {}).get("xmfa"))  # Microsoft sign-in with enforced MFA
    return settings.mfa_required_for_admins and u.role != "participant" and not u.mfa_enabled and not ext_mfa


def _require_mfa(u: User):
    if mfa_setup_required(u):
        raise HTTPException(403, "Two-step verification is required for admin accounts. Set it up under My account.")


def admin_user(u: User = Depends(current_user)) -> User:
    if u.role not in ("master_admin", "secondary_admin"):
        raise HTTPException(403, "Admin access required")
    _require_mfa(u)
    return u


def master_user(u: User = Depends(current_user)) -> User:
    if u.role != "master_admin":
        raise HTTPException(403, "Master Admin access required")
    _require_mfa(u)
    return u


def participant_user(u: User = Depends(current_user)) -> User:
    if u.role != "participant" or not u.organization_id:
        raise HTTPException(403, "Participant access required")
    return u
