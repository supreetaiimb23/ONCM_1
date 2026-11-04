"""Checklist-linked uploads, password links, forgotten passwords and temporary admin passwords."""
from app.models import ChecklistItem, User
from tests.conftest import approve, auth, pdf_file, start_review, uid

H = {"X-ONCM": "1"}


def _items(db, org_id):
    return db.query(ChecklistItem).filter_by(organization_id=org_id).order_by(ChecklistItem.id).all()


def test_upload_for_checklist_item_is_ticked_on_approval(client, db, make_org, make_admin):
    reviewer = make_admin()
    o, p = make_org(assigned_admin=reviewer)
    item = _items(db, o.id)[0]
    r = client.post("/api/me/documents", headers=auth(p), files=pdf_file(),
                    data={"title": item.title, "category": "01 Onboarding documents", "checklist_item_id": str(item.id)})
    assert r.status_code == 201, r.text
    doc = r.json()
    assert doc["checklist_item_id"] == item.id
    entry = next(c for c in client.get("/api/me/dashboard", headers=auth(p)).json()["checklist"] if c["id"] == item.id)
    assert entry["document"] == {"id": doc["id"], "status": "submitted"} and entry["done"] is False

    start_review(client, reviewer, doc["id"])
    approve(client, _master(db), doc["id"])
    db.expire_all()
    assert db.get(ChecklistItem, item.id).done is True


def _master(db):
    return db.query(User).filter_by(role="master_admin").first()


def test_upload_not_for_a_checklist_item(client, make_org):
    _, p = make_org()
    r = client.post("/api/me/documents", headers=auth(p), files=pdf_file(),
                    data={"title": "Extra evidence", "category": "01 Onboarding documents", "checklist_item_id": ""})
    assert r.status_code == 201, r.text
    assert r.json()["checklist_item_id"] is None


def test_upload_cannot_use_another_organisations_checklist_item(client, db, make_org):
    o1, p1 = make_org()
    o2, _ = make_org()
    other = _items(db, o2.id)[0]
    r = client.post("/api/me/documents", headers=auth(p1), files=pdf_file(),
                    data={"title": "x", "category": "01 Onboarding documents", "checklist_item_id": str(other.id)})
    assert r.status_code == 400


def test_me_reports_upload_limits(client, make_org):
    _, p = make_org()
    lim = client.get("/api/auth/me", headers=auth(p)).json()["upload_limits"]
    assert lim["max_mb"] == 25 and "pdf" in lim["extensions"]


def test_password_link_lets_a_member_set_a_password(client, db, make_org, make_admin):
    admin = make_admin()
    o, _ = make_org(assigned_admin=admin)
    r = client.post(f"/api/admin/organizations/{o.id}/members", headers=auth(admin),
                    json={"name": "New Member", "email": f"member-{uid()}@test.oncm"})
    assert r.status_code == 201, r.text
    mid = r.json()["id"]
    link = client.post(f"/api/admin/users/{mid}/password-link", headers=auth(admin)).json()["link"]
    token = link.split("invite=")[1]
    r = client.post("/api/auth/accept-invite", json={"token": token, "password": "Member!Pass123"})
    assert r.status_code == 200
    assert client.post("/api/auth/accept-invite", json={"token": token, "password": "Another!Pass123"}).status_code == 404


def test_only_master_gets_links_for_admins(client, make_admin, master):
    a, b = make_admin(), make_admin()
    assert client.post(f"/api/admin/users/{b.id}/password-link", headers=auth(a)).status_code == 403
    assert client.post(f"/api/admin/users/{b.id}/password-link", headers=auth(master)).status_code == 200


def test_forgot_password_answers_the_same_for_unknown_emails(client, db, make_org):
    _, p = make_org()
    known = client.post("/api/auth/forgot-password", json={"email": p.email})
    unknown = client.post("/api/auth/forgot-password", json={"email": f"nobody-{uid()}@test.oncm"})
    assert known.status_code == unknown.status_code == 200
    assert known.json() == unknown.json()


def test_new_admin_must_change_temporary_password(client, master):
    email = f"temp-{uid()}@test.oncm"
    r = client.post("/api/master/admins", headers=auth(master),
                    json={"name": "Temp Admin", "email": email, "password": "Temp!Pass1234"})
    assert r.status_code == 201, r.text
    client.cookies.clear()
    r = client.post("/api/auth/login", json={"email": email, "password": "Temp!Pass1234"})
    assert r.status_code == 200 and r.json()["user"]["must_change_password"] is True
    assert client.get("/api/admin/meta").status_code == 403
    same = client.post("/api/auth/change-password", headers=H,
                       json={"current_password": "Temp!Pass1234", "new_password": "Temp!Pass1234"})
    assert same.status_code == 400
    r = client.post("/api/auth/change-password", headers=H,
                    json={"current_password": "Temp!Pass1234", "new_password": "Mine!Pass56789"})
    assert r.status_code == 200
    assert client.get("/api/admin/meta").status_code == 200
    client.cookies.clear()

