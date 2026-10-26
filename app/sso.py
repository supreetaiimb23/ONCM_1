"""Sign in with Microsoft (Entra ID) using OpenID Connect: authorization code flow with PKCE, single tenant.

Only people who already have an ONCM account can use it: the Microsoft email must match an active account, which is
then bound to the Microsoft object ID (oid) at first use. Roles and permissions stay in ONCM. Every ID token is
checked: RS256 signature against Microsoft's published keys, issuer (our tenant), audience (our app), expiry, nonce.
ONCM's own second step still applies to accounts that use it, unless SSO_TRUST_MICROSOFT_MFA says the tenant enforces
MFA for this app (Conditional Access).
"""
import base64
import hashlib
import json
import logging
import secrets
import urllib.parse
import urllib.request

import jwt
from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core import decode_token, get_db, make_token, set_session_cookie, settings
from app.models import Organization, User

log = logging.getLogger("oncm")
router = APIRouter(prefix="/api/auth/sso/microsoft")
STATE_COOKIE = "oncm_sso"
MFA_COOKIE = "oncm_mfa"  # pending second step after a Microsoft sign-in (read by POST /api/auth/mfa)
_jwks = {}


def enabled() -> bool:
    return bool(settings.sso_microsoft_tenant_id and settings.sso_microsoft_client_id and settings.sso_microsoft_client_secret)


def _domains(value: str) -> set[str]:
    return {d.strip().lower().lstrip("@") for d in value.split(",") if d.strip()}


def password_sign_in_blocked(email: str) -> bool:
    """Staff domains that must use Microsoft (SSO_REQUIRED_DOMAINS): their passwords are not accepted."""
    return enabled() and email.rsplit("@", 1)[-1].lower() in _domains(settings.sso_required_domains)


def _authority() -> str:
    return f"https://login.microsoftonline.com/{settings.sso_microsoft_tenant_id}"


def _redirect_uri() -> str:
    return settings.app_base_url.rstrip("/") + "/api/auth/sso/microsoft/callback"


def _b64url(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def _secure() -> bool:
    return settings.app_base_url.startswith("https://")


def _fail(reason: str):
    log.warning("Microsoft sign-in refused: %s", reason)
    r = RedirectResponse("/?sso_error=" + urllib.parse.quote(reason), status_code=302)
    r.delete_cookie(STATE_COOKIE, path="/api/auth/sso")
    return r


# ---- network calls (replaced in tests) ----
def _exchange_code(code: str, verifier: str) -> dict:
    data = urllib.parse.urlencode({
        "client_id": settings.sso_microsoft_client_id, "client_secret": settings.sso_microsoft_client_secret,
        "grant_type": "authorization_code", "code": code, "redirect_uri": _redirect_uri(),
        "code_verifier": verifier, "scope": "openid email profile"}).encode()
    req = urllib.request.Request(_authority() + "/oauth2/v2.0/token", data=data, method="POST",
                                 headers={"Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.load(r)


def _signing_key(id_token: str):
    url = _authority() + "/discovery/v2.0/keys"
    if url not in _jwks:
        _jwks[url] = jwt.PyJWKClient(url, cache_keys=True, lifespan=3600)
    return _jwks[url].get_signing_key_from_jwt(id_token).key


def _verify_id_token(id_token: str, nonce: str) -> dict:
    claims = jwt.decode(id_token, _signing_key(id_token), algorithms=["RS256"], audience=settings.sso_microsoft_client_id,
                        issuer=f"https://login.microsoftonline.com/{settings.sso_microsoft_tenant_id}/v2.0",
                        options={"require": ["exp", "iat", "iss", "aud", "sub"]}, leeway=60)
    if claims.get("nonce") != nonce:
        raise jwt.InvalidTokenError("nonce mismatch")
    if claims.get("tid") != settings.sso_microsoft_tenant_id:
        raise jwt.InvalidTokenError("wrong tenant")
    return claims


# ---- endpoints ----
@router.get("/start")
def start():
    if not enabled():
        return _fail("not-configured")
    state, nonce, verifier = secrets.token_urlsafe(24), secrets.token_urlsafe(24), secrets.token_urlsafe(48)
    params = {"client_id": settings.sso_microsoft_client_id, "response_type": "code", "redirect_uri": _redirect_uri(),
              "response_mode": "query", "scope": "openid email profile", "state": state, "nonce": nonce,
              "code_challenge": _b64url(hashlib.sha256(verifier.encode()).digest()), "code_challenge_method": "S256",
              "prompt": "select_account"}
    r = RedirectResponse(_authority() + "/oauth2/v2.0/authorize?" + urllib.parse.urlencode(params), status_code=302)
    # SameSite=Lax: the browser must send it on the redirect back from login.microsoftonline.com (a cross-site GET)
    r.set_cookie(STATE_COOKIE, make_token(0, typ="sso", minutes=10, state=state, nonce=nonce, verifier=verifier),
                 max_age=600, path="/api/auth/sso", httponly=True, secure=_secure(), samesite="lax")
    return r


@router.get("/callback")
def callback(request: Request, db: Session = Depends(get_db), code: str = "", state: str = "", error: str = ""):
    from app.services import audit  # avoid an import cycle at module load
    if error:
        return _fail("cancelled" if error == "access_denied" else "microsoft-error")
    try:
        pending = decode_token(request.cookies.get(STATE_COOKIE, ""), "sso")
    except Exception:
        return _fail("expired")
    if not code or not secrets.compare_digest(state, pending.get("state", "")):
        return _fail("state-mismatch")
    try:
        claims = _verify_id_token(_exchange_code(code, pending["verifier"])["id_token"], pending["nonce"])
    except Exception as e:
        log.warning("Microsoft token check failed: %s", e)
        return _fail("token-invalid")

    email = (claims.get("email") or claims.get("preferred_username") or "").strip().lower()
    allowed = _domains(settings.sso_microsoft_domains)
    if not email or (allowed and email.rsplit("@", 1)[-1] not in allowed):
        return _fail("domain-not-allowed")
    u = db.query(User).filter(func.lower(User.email) == email).first()
    if not u or not u.is_active:
        return _fail("no-account")
    if u.sso_oid and u.sso_oid != claims.get("oid"):
        return _fail("account-linked-elsewhere")  # the email was reassigned to another Microsoft identity
    if u.role == "participant":
        org = db.get(Organization, u.organization_id)
        if not org or org.status != "active":
            return _fail("organization-suspended")
    if not u.sso_oid:
        u.sso_oid = claims.get("oid")
    ext_mfa = settings.sso_trust_microsoft_mfa or "mfa" in (claims.get("amr") or [])

    r = RedirectResponse("/", status_code=302)
    r.delete_cookie(STATE_COOKIE, path="/api/auth/sso")
    if u.mfa_enabled and not ext_mfa:  # ONCM's own second step
        audit(db, u, "auth.sso_password_step", "user", u.id, {"provider": "microsoft"})
        db.commit()
        r.headers["location"] = "/?sso=mfa"
        r.set_cookie(MFA_COOKIE, make_token(u.id, typ="mfa", minutes=5, sv=u.session_version or 0), max_age=300,
                     path="/api/auth/mfa", httponly=True, secure=_secure(), samesite="strict")
        return r
    audit(db, u, "auth.login", "user", u.id, {"provider": "microsoft", "microsoft_mfa": ext_mfa})
    db.commit()
    set_session_cookie(r, u, ext_mfa=ext_mfa)
    return r
