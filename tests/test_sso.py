"""Sign in with Microsoft (OpenID Connect) against a simulated Entra ID: real RS256 signatures, real checks."""
import time
import urllib.parse
import uuid

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from app import mfa, sso
from app.core import settings

TENANT, CLIENT = "11111111-2222-3333-4444-555555555555", "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
OTHER_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture
def microsoft(monkeypatch):
    """Configure SSO and stand in for login.microsoftonline.com. `ms.claims` shapes the next ID token."""
    for k, v in {"sso_microsoft_tenant_id": TENANT, "sso_microsoft_client_id": CLIENT,
                 "sso_microsoft_client_secret": "s3cret", "sso_microsoft_domains": "", "sso_required_domains": "",
                 "sso_trust_microsoft_mfa": False}.items():
        monkeypatch.setattr(settings, k, v)

    class MS:
        claims, key, seen = {}, KEY, {}
    ms = MS()

    def exchange(code, verifier):
        ms.seen = {"code": code, "verifier": verifier}
        now = int(time.time())
        claims = {"iss": f"https://login.microsoftonline.com/{TENANT}/v2.0", "aud": CLIENT, "tid": TENANT,
                  "sub": "s-" + ms.claims.get("oid", "x"), "iat": now, "exp": now + 3600, "nonce": ms.nonce, **ms.claims}
        return {"id_token": jwt.encode(claims, ms.key, algorithm="RS256")}
    monkeypatch.setattr(sso, "_exchange_code", exchange)
    monkeypatch.setattr(sso, "_signing_key", lambda token: KEY.public_key())
    return ms


def sign_in_with_microsoft(client, ms, **claims):
    """Follow the real redirect dance: /start -> (Microsoft) -> /callback. Returns the callback response."""
    r = client.get("/api/auth/sso/microsoft/start", follow_redirects=False)
    assert r.status_code == 302
    q = urllib.parse.parse_qs(urllib.parse.urlparse(r.headers["location"]).query)
    assert q["code_challenge_method"] == ["S256"] and q["client_id"] == [CLIENT]
    ms.nonce = q["nonce"][0]
    ms.claims = {"oid": str(uuid.uuid4()), **claims}
    state = claims.pop("_state", q["state"][0]) if "_state" in claims else q["state"][0]
    ms.claims.pop("_state", None)
    return client.get(f"/api/auth/sso/microsoft/callback?code=abc&state={state}", follow_redirects=False)


def error_of(r):
    return urllib.parse.parse_qs(urllib.parse.urlparse(r.headers["location"]).query).get("sso_error", [None])[0]


def test_off_until_configured(client):
    assert client.get("/api/health").json()["sso"] == []
    r = client.get("/api/auth/sso/microsoft/start", follow_redirects=False)
    assert error_of(r) == "not-configured"


def test_existing_account_signs_in(client, microsoft, make_admin):
    a = make_admin()
    assert client.get("/api/health").json()["sso"] == ["microsoft"]
    r = sign_in_with_microsoft(client, microsoft, email=a.email.upper())
    assert r.status_code == 302 and r.headers["location"] == "/"
    assert "httponly" in r.headers["set-cookie"].lower()
    assert client.get("/api/auth/me").json()["email"] == a.email
    assert len(microsoft.seen["verifier"]) > 40  # PKCE verifier sent with the code


def test_unknown_email_gets_no_account(client, microsoft):
    assert error_of(sign_in_with_microsoft(client, microsoft, email="stranger@iimb.ac.in")) == "no-account"
    assert client.get("/api/auth/me").status_code == 401


def test_forged_or_mismatched_tokens_are_refused(client, microsoft, make_admin):
    a = make_admin()
    microsoft.key = OTHER_KEY  # signed by someone else
    assert error_of(sign_in_with_microsoft(client, microsoft, email=a.email)) == "token-invalid"
    microsoft.key = KEY
    assert error_of(sign_in_with_microsoft(client, microsoft, email=a.email, aud="another-app")) == "token-invalid"
    assert error_of(sign_in_with_microsoft(client, microsoft, email=a.email, tid="other-tenant")) == "token-invalid"
    assert error_of(sign_in_with_microsoft(client, microsoft, email=a.email, nonce="replayed")) == "token-invalid"
    assert error_of(sign_in_with_microsoft(client, microsoft, email=a.email, exp=int(time.time()) - 3600)) == "token-invalid"
    assert client.get("/api/auth/me").status_code == 401


def test_state_must_match(client, microsoft, make_admin):
    a = make_admin()
    assert error_of(sign_in_with_microsoft(client, microsoft, email=a.email, _state="attacker")) == "state-mismatch"
    client.cookies.clear()  # no state cookie at all (e.g. a link from an attacker)
    assert error_of(client.get("/api/auth/sso/microsoft/callback?code=x&state=y", follow_redirects=False)) == "expired"


def test_domain_allow_list(client, microsoft, make_admin, monkeypatch):
    monkeypatch.setattr(settings, "sso_microsoft_domains", "iimb.ac.in")
    a = make_admin()  # @test.oncm
    assert error_of(sign_in_with_microsoft(client, microsoft, email=a.email)) == "domain-not-allowed"


def test_account_is_bound_to_one_microsoft_identity(client, microsoft, make_admin):
    a = make_admin()
    sign_in_with_microsoft(client, microsoft, email=a.email, oid="first-oid")
    client.cookies.clear()
    assert error_of(sign_in_with_microsoft(client, microsoft, email=a.email, oid="someone-else")) == "account-linked-elsewhere"
    assert sign_in_with_microsoft(client, microsoft, email=a.email, oid="first-oid").headers["location"] == "/"


def test_oncm_second_step_still_applies(client, microsoft, make_admin):
    a = make_admin()
    client.post("/api/auth/login", json={"email": a.email, "password": "Admin!Pass123"})
    secret = client.post("/api/auth/mfa/setup", headers={"X-ONCM": "1"}).json()["secret"]
    step = int(time.time() // mfa.STEP)
    client.post("/api/auth/mfa/enable", headers={"X-ONCM": "1"}, json={"code": mfa._code(secret, step)})
    client.cookies.clear()

    r = sign_in_with_microsoft(client, microsoft, email=a.email)
    assert r.headers["location"] == "/?sso=mfa"
    assert client.get("/api/auth/me").status_code == 401  # not signed in yet
    assert client.post("/api/auth/mfa", json={"code": mfa._code(secret, step + 1)}).status_code == 200  # pending step from cookie
    assert client.get("/api/auth/me").status_code == 200


def test_microsoft_mfa_counts_when_trusted(client, microsoft, make_admin, monkeypatch):
    monkeypatch.setattr(settings, "mfa_required_for_admins", True)
    a = make_admin()
    sign_in_with_microsoft(client, microsoft, email=a.email, oid="admin-oid")
    assert client.get("/api/admin/overview").status_code == 403  # Microsoft MFA not trusted: ONCM setup required
    client.cookies.clear()
    monkeypatch.setattr(settings, "sso_trust_microsoft_mfa", True)
    sign_in_with_microsoft(client, microsoft, email=a.email, oid="admin-oid")
    assert client.get("/api/auth/me").json()["mfa_setup_required"] is False
    assert client.get("/api/admin/overview").status_code == 200


def test_staff_domains_can_be_forced_to_microsoft(client, microsoft, make_admin, monkeypatch):
    a = make_admin()
    monkeypatch.setattr(settings, "sso_required_domains", "test.oncm")
    r = client.post("/api/auth/login", json={"email": a.email, "password": "Admin!Pass123"})
    assert r.status_code == 403 and "Microsoft" in r.json()["detail"]


def test_suspended_organization_cannot_sign_in(client, microsoft, make_org, db):
    o, p = make_org()
    o.status = "suspended"
    db.commit()
    assert error_of(sign_in_with_microsoft(client, microsoft, email=p.email)) == "organization-suspended"
