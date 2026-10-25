"""Sign-in, tokens and role guards."""
from datetime import datetime, timedelta, timezone

import jwt
import pytest

from app.core import make_token, settings
from tests.conftest import MASTER_PW, auth


def test_health(client):
    assert client.get("/api/health").json()["status"] == "ok"


def test_login_success_and_me(client, master):
    r = client.post("/api/auth/login", json={"email": master.email.upper(), "password": MASTER_PW})
    assert r.status_code == 200
    assert "password_hash" not in r.json()["user"]
    me = client.get("/api/auth/me", headers={"Authorization": f"Bearer {r.json()['token']}"}).json()
    assert me["role"] == "master_admin"


def test_login_wrong_password(client, master):
    assert client.post("/api/auth/login", json={"email": master.email, "password": "wrong-password"}).status_code == 401


def test_login_inactive_user(client, make_admin, db):
    a = make_admin()
    a.is_active = False
    db.commit()
    assert client.post("/api/auth/login", json={"email": a.email, "password": "Admin!Pass123"}).status_code == 401


def test_login_suspended_organization(client, make_org, db):
    o, p = make_org()
    o.status = "suspended"
    db.commit()
    assert client.post("/api/auth/login", json={"email": p.email, "password": "Partner!Pass123"}).status_code == 403


def test_login_rate_limited(client, master):
    codes = [client.post("/api/auth/login", json={"email": master.email, "password": "nope"}).status_code for _ in range(11)]
    assert codes[:10] == [401] * 10 and codes[10] == 429


@pytest.mark.parametrize("path", ["/api/auth/me", "/api/admin/overview", "/api/me/dashboard", "/api/master/admins"])
def test_token_required(client, path):
    assert client.get(path).status_code == 401


def test_rejects_token_of_another_type(client, master):
    invite = make_token(master.id, typ="invite")
    assert client.get("/api/auth/me", headers={"Authorization": f"Bearer {invite}"}).status_code == 401


def test_rejects_expired_token(client, master):
    exp = datetime.now(timezone.utc) - timedelta(minutes=1)
    tok = jwt.encode({"sub": str(master.id), "typ": "access", "exp": exp}, settings.secret_key, algorithm="HS256")
    assert client.get("/api/auth/me", headers={"Authorization": f"Bearer {tok}"}).status_code == 401


def test_rejects_token_signed_with_another_key(client, master):
    tok = jwt.encode({"sub": str(master.id), "typ": "access", "exp": datetime.now(timezone.utc) + timedelta(hours=1)},
                     "change-me", algorithm="HS256")
    assert client.get("/api/auth/me", headers={"Authorization": f"Bearer {tok}"}).status_code == 401


def test_deactivated_user_token_stops_working(client, make_admin, db):
    a = make_admin()
    h = auth(a)
    assert client.get("/api/auth/me", headers=h).status_code == 200
    a.is_active = False
    db.commit()
    assert client.get("/api/auth/me", headers=h).status_code == 401


def test_participant_cannot_use_admin_api(client, make_org):
    _, p = make_org()
    assert client.get("/api/admin/overview", headers=auth(p)).status_code == 403
    assert client.get("/api/master/join-requests", headers=auth(p)).status_code == 403


def test_secondary_admin_cannot_use_master_api(client, make_admin):
    a = make_admin()
    for path in ("/api/master/admins", "/api/master/audit-logs", "/api/master/join-requests", "/api/master/alert-recipients"):
        assert client.get(path, headers=auth(a)).status_code == 403, path


def test_admin_cannot_use_participant_api(client, master):
    assert client.get("/api/me/dashboard", headers=auth(master)).status_code == 403


def test_change_password(client, make_admin):
    a = make_admin()
    h = auth(a)
    assert client.post("/api/auth/change-password", headers=h,
                       json={"current_password": "wrong-one", "new_password": "NewPass!12345"}).status_code == 400
    assert client.post("/api/auth/change-password", headers=h,
                       json={"current_password": "Admin!Pass123", "new_password": "NewPass!12345"}).status_code == 200
    assert client.post("/api/auth/login", json={"email": a.email, "password": "NewPass!12345"}).status_code == 200
