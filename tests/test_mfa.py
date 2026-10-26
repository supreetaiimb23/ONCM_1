"""Session cookie, CSRF header, session versioning, and two-step verification (TOTP + recovery codes)."""
import time

import pytest

from app import mfa
from app.core import make_token, settings
from tests.conftest import MASTER_PW, auth

PW = "Admin!Pass123"


def now_code(secret, ahead=0):
    return mfa._code(secret, int(time.time() // mfa.STEP) + ahead)


def login(client, email, password, **extra):
    return client.post("/api/auth/login", json={"email": email, "password": password, **extra})


def enable_mfa(client, user, password=PW):
    """Sign in (cookie), set up and enable two-step verification. Returns (secret, recovery codes)."""
    assert login(client, user.email, password).status_code == 200
    secret = client.post("/api/auth/mfa/setup", headers={"X-ONCM": "1"}).json()["secret"]
    r = client.post("/api/auth/mfa/enable", headers={"X-ONCM": "1"}, json={"code": now_code(secret)})
    assert r.status_code == 200, r.text
    client.cookies.clear()
    return secret, r.json()["recovery_codes"]


# ------------------------------------------------------------------ TOTP itself
def test_totp_matches_rfc_6238_test_vectors():
    secret = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"  # base32 of "12345678901234567890"
    assert mfa._code(secret, 59 // 30) == "287082"
    assert mfa._code(secret, 1111111109 // 30) == "081804"
    assert mfa._code(secret, 2000000000 // 30) == "279037"


def test_totp_allows_one_step_of_drift_and_refuses_replay():
    s = mfa.new_secret()
    t = 1_800_000_000
    step = mfa.verify(s, mfa._code(s, t // 30 - 1), now=t)
    assert step == t // 30 - 1
    assert mfa.verify(s, mfa._code(s, t // 30 - 2), now=t) is None          # too old
    assert mfa.verify(s, mfa._code(s, t // 30), last_step=t // 30, now=t) is None  # already used
    assert mfa.verify(s, "12345", now=t) is None


# ------------------------------------------------------------------ cookie session
def test_login_sets_a_hardened_cookie_and_no_token(client, master):
    r = login(client, master.email, MASTER_PW)
    cookie = r.headers["set-cookie"].lower()
    assert "oncm_session=" in cookie and "httponly" in cookie and "samesite=strict" in cookie and "path=/api" in cookie
    assert "token" not in r.json()
    assert client.get("/api/auth/me").status_code == 200


def test_cookie_is_secure_on_https(client, master, monkeypatch):
    monkeypatch.setattr(settings, "app_base_url", "https://portal.example.org")
    assert "secure" in login(client, master.email, MASTER_PW).headers["set-cookie"].lower()


def test_api_clients_can_still_ask_for_a_token(client, master):
    token = login(client, master.email, MASTER_PW, return_token=True).json()["token"]
    client.cookies.clear()
    assert client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"}).status_code == 200


def test_cookie_changes_need_the_csrf_header(client, make_admin):
    a = make_admin()
    login(client, a.email, PW)
    body = {"current_password": PW, "new_password": "Brand!New12345"}
    assert client.post("/api/auth/change-password", json=body).status_code == 403  # e.g. a form on another site
    assert client.post("/api/auth/change-password", json=body, headers={"X-ONCM": "1"}).status_code == 200


def test_logout_clears_the_cookie(client, master):
    login(client, master.email, MASTER_PW)
    client.post("/api/auth/logout")
    assert client.get("/api/auth/me").status_code == 401


def test_password_change_ends_other_sessions_but_keeps_this_one(client, make_admin):
    a = make_admin()
    other_device = login(client, a.email, PW, return_token=True).json()["token"]
    client.cookies.clear()
    login(client, a.email, PW)
    assert client.post("/api/auth/change-password", headers={"X-ONCM": "1"},
                       json={"current_password": PW, "new_password": "Brand!New12345"}).status_code == 200
    assert client.get("/api/auth/me").status_code == 200  # this browser
    client.cookies.clear()
    assert client.get("/api/auth/me", headers={"Authorization": f"Bearer {other_device}"}).status_code == 401


# ------------------------------------------------------------------ two-step verification
def test_two_step_sign_in(client, make_admin):
    a = make_admin()
    secret, codes = enable_mfa(client, a)
    assert len(codes) == 10 and len(set(codes)) == 10

    r = login(client, a.email, PW)
    assert r.json()["mfa_required"] and "set-cookie" not in r.headers  # password alone does not sign in
    assert client.get("/api/auth/me").status_code == 401
    tok = r.json()["mfa_token"]
    assert client.post("/api/auth/mfa", json={"mfa_token": tok, "code": "000000"}).status_code == 401
    good = now_code(secret, ahead=1)  # enabling used the current step; the next one is fresh
    assert client.post("/api/auth/mfa", json={"mfa_token": tok, "code": good}).status_code == 200
    assert client.get("/api/auth/me").json()["mfa_enabled"] is True
    client.cookies.clear()
    assert client.post("/api/auth/mfa", json={"mfa_token": tok, "code": good}).status_code == 401  # no replay


def test_recovery_code_works_once(client, make_admin):
    a = make_admin()
    _, codes = enable_mfa(client, a)
    tok = login(client, a.email, PW).json()["mfa_token"]
    assert client.post("/api/auth/mfa", json={"mfa_token": tok, "code": codes[0].upper()}).status_code == 200
    assert client.get("/api/auth/me").json()["mfa_recovery_codes_left"] == 9
    client.cookies.clear()
    tok = login(client, a.email, PW).json()["mfa_token"]
    assert client.post("/api/auth/mfa", json={"mfa_token": tok, "code": codes[0]}).status_code == 401


def test_second_step_is_rate_limited(client, make_admin):
    a = make_admin()
    enable_mfa(client, a)
    tok = login(client, a.email, PW).json()["mfa_token"]
    statuses = [client.post("/api/auth/mfa", json={"mfa_token": tok, "code": "000000"}).status_code for _ in range(6)]
    assert statuses[:5] == [401] * 5 and statuses[5] == 429


def test_password_token_is_not_a_session(client, make_admin):
    a = make_admin()
    enable_mfa(client, a)
    tok = login(client, a.email, PW).json()["mfa_token"]
    assert client.get("/api/auth/me", headers={"Authorization": f"Bearer {tok}"}).status_code == 401


def test_secrets_never_reach_the_browser(client, make_admin):
    a = make_admin()
    enable_mfa(client, a)
    for r in (client.get("/api/auth/me", headers=auth(a)), client.get("/api/master/admins", headers=auth(a))):
        text = r.text
        assert "mfa_secret" not in text and "mfa_recovery\"" not in text and "session_version" not in text


def test_admins_must_set_up_two_step_verification(client, make_admin, monkeypatch):
    monkeypatch.setattr(settings, "mfa_required_for_admins", True)
    a = make_admin()
    login(client, a.email, PW)
    assert client.get("/api/auth/me").json()["mfa_setup_required"] is True
    r = client.get("/api/admin/overview")
    assert r.status_code == 403 and "Two-step verification" in r.json()["detail"]
    secret = client.post("/api/auth/mfa/setup", headers={"X-ONCM": "1"}).json()["secret"]
    assert client.post("/api/auth/mfa/enable", headers={"X-ONCM": "1"}, json={"code": now_code(secret)}).status_code == 200
    assert client.get("/api/admin/overview").status_code == 200
    r = client.post("/api/auth/mfa/disable", headers={"X-ONCM": "1"}, json={"password": PW, "code": now_code(secret, 1)})
    assert r.status_code == 409  # admins cannot switch it off


def test_participants_may_switch_it_off(client, make_org):
    _, p = make_org()
    secret, codes = enable_mfa(client, p, "Partner!Pass123")
    tok = login(client, p.email, "Partner!Pass123").json()["mfa_token"]
    assert client.post("/api/auth/mfa", json={"mfa_token": tok, "code": now_code(secret, 1)}).status_code == 200
    bad = client.post("/api/auth/mfa/disable", headers={"X-ONCM": "1"}, json={"password": "wrong-password", "code": codes[0]})
    assert bad.status_code == 400
    ok = client.post("/api/auth/mfa/disable", headers={"X-ONCM": "1"}, json={"password": "Partner!Pass123", "code": codes[0]})
    assert ok.status_code == 200
    client.cookies.clear()
    assert login(client, p.email, "Partner!Pass123").json().get("mfa_required") is None


def test_master_can_reset_someone_elses_two_step_verification(client, master, make_admin):
    a = make_admin()
    enable_mfa(client, a)
    r = client.post(f"/api/master/users/{a.id}/reset-mfa", headers=auth(master), json={"reason": "lost phone, verified by call"})
    assert r.status_code == 200 and r.json()["mfa_enabled"] is False
    assert login(client, a.email, PW).json().get("mfa_required") is None  # password alone again
    assert client.post(f"/api/master/users/{master.id}/reset-mfa", headers=auth(master), json={"reason": "x" * 5}).status_code == 409


def test_invite_link_works_once(client, make_org, db):
    from app.services import invite_link
    _, p = make_org()
    token = invite_link(p).split("invite=")[1]
    assert client.post("/api/auth/accept-invite", json={"token": token, "password": "First!Pass1234"}).status_code == 200
    assert client.post("/api/auth/accept-invite", json={"token": token, "password": "Hijack!Pass1234"}).status_code == 404
    assert make_token(p.id) and login(client, p.email, "First!Pass1234").status_code == 200
