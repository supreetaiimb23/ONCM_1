"""Who can see and do what: organization isolation, admin scoping and maker-checker."""
from tests.conftest import admin_doc, approve, auth, participant_doc, start_review


# ------------------------------------------------------------------ participants are isolated to their organization
def test_participant_cannot_open_another_orgs_document(client, make_org):
    _, alice = make_org()
    _, bob = make_org()
    doc = participant_doc(client, alice)
    assert client.get(f"/api/me/documents/{doc['id']}/download", headers=auth(bob)).status_code == 404
    assert client.post(f"/api/me/documents/{doc['id']}/versions", headers=auth(bob),
                       files={"file": ("x.pdf", b"%PDF-1.4 x", "application/pdf")}).status_code == 404
    assert doc["id"] not in [d["id"] for d in client.get("/api/me/documents", headers=auth(bob)).json()]


def test_participant_never_sees_internal_or_unpublished_documents(client, master, make_org):
    _, p = make_org()
    internal = admin_doc(client, master, "CM", "internal")
    draft_for_pilot = admin_doc(client, master, "CM", "pilot_shared")   # classified for sharing but never published
    ids = [r["id"] for r in client.get("/api/me/resources", headers=auth(p)).json()]
    assert internal["id"] not in ids and draft_for_pilot["id"] not in ids
    assert client.get(f"/api/me/resources/{draft_for_pilot['id']}/download", headers=auth(p)).status_code == 404


def test_suspended_participant_loses_access(client, master, make_org):
    o, p = make_org()
    h = auth(p)
    assert client.get("/api/me/dashboard", headers=h).status_code == 200
    client.patch(f"/api/admin/organizations/{o.id}", headers=auth(master), json={"status": "suspended", "reason": "Audit"})
    assert client.get("/api/me/dashboard", headers=h).status_code in (401, 403)


# ------------------------------------------------------------------ secondary admins are scoped
def test_secondary_admin_only_sees_assigned_organizations(client, make_admin, make_org):
    a = make_admin()
    mine, _ = make_org(assigned_admin=a)
    other, other_p = make_org()
    ids = [o["id"] for o in client.get("/api/admin/organizations", headers=auth(a)).json()]
    assert mine.id in ids and other.id not in ids
    assert client.get(f"/api/admin/organizations/{other.id}", headers=auth(a)).status_code == 404
    doc = participant_doc(client, other_p)
    assert client.get(f"/api/admin/documents/{doc['id']}", headers=auth(a)).status_code == 404


def test_agreements_category_needs_explicit_grant(client, master, make_admin):
    a = make_admin()  # default repository access excludes AGR
    doc = admin_doc(client, master, "AGR", "internal")
    assert client.get(f"/api/admin/documents/{doc['id']}", headers=auth(a)).status_code == 404
    r = client.post("/api/admin/documents", headers=auth(a), data={"title": "x", "repo_category": "AGR"},
                    files={"file": ("a.pdf", b"%PDF-1.4 a", "application/pdf")})
    assert r.status_code == 403


def test_secondary_admin_cannot_change_org_assignment_or_status(client, make_admin, make_org):
    a = make_admin()
    o, _ = make_org(assigned_admin=a)
    assert client.patch(f"/api/admin/organizations/{o.id}", headers=auth(a), json={"assigned_admin_id": a.id}).status_code == 403
    assert client.patch(f"/api/admin/organizations/{o.id}", headers=auth(a), json={"status": "suspended", "reason": "x y z"}).status_code == 403


def test_publish_and_export_need_granted_permissions(client, master, make_admin):
    plain = make_admin()
    doc = admin_doc(client, master, "CM", "pilot_shared")
    assert client.post(f"/api/admin/documents/{doc['id']}/publish", headers=auth(plain), json={}).status_code == 403
    assert client.get("/api/admin/reports/export", headers=auth(plain)).status_code == 403
    exporter = make_admin(can_export=True)
    r = client.get("/api/admin/reports/export", headers=auth(exporter))
    assert r.status_code == 200 and r.content[:2] == b"PK"


# ------------------------------------------------------------------ maker-checker
def test_maker_cannot_review_or_approve_own_document(client, master, make_admin):
    maker = make_admin()
    doc = admin_doc(client, maker, "CM", "internal")
    r = client.post(f"/api/admin/documents/{doc['id']}/review", headers=auth(maker), json={"decision": "start_review"})
    assert r.status_code == 400  # must assign someone else first
    r = client.post(f"/api/admin/documents/{doc['id']}/assign", headers=auth(master), json={"reviewer_id": maker.id})
    assert r.status_code == 409


def test_master_admin_is_also_bound_by_maker_checker(client, master, make_admin):
    reviewer = make_admin()
    doc = admin_doc(client, master, "CM", "internal")
    client.post(f"/api/admin/documents/{doc['id']}/assign", headers=auth(master), json={"reviewer_id": reviewer.id})
    start_review(client, master, doc["id"])
    r = client.post(f"/api/admin/documents/{doc['id']}/review", headers=auth(master),
                    json={"decision": "approve", "confirm_classification": True})
    assert r.status_code == 403


def test_reviewer_and_approver_must_differ(client, master, make_admin):
    a = make_admin()
    doc = admin_doc(client, master, "CM", "internal")
    r = client.post(f"/api/admin/documents/{doc['id']}/assign", headers=auth(master), json={"reviewer_id": a.id, "approver_id": a.id})
    assert r.status_code == 409


def test_designated_approver_can_give_final_approval(client, master, make_admin):
    maker, approver = make_admin(), make_admin()
    doc = admin_doc(client, maker, "RES", "public")
    client.post(f"/api/admin/documents/{doc['id']}/assign", headers=auth(master), json={"approver_id": approver.id})
    start_review(client, master, doc["id"])
    assert approve(client, approver, doc["id"])["status"] == "internally_approved"


def test_approval_requires_confirmed_classification(client, master, make_admin):
    maker = make_admin()
    doc = admin_doc(client, maker, "CM", "internal")
    start_review(client, master, doc["id"])
    r = client.post(f"/api/admin/documents/{doc['id']}/review", headers=auth(master), json={"decision": "approve"})
    assert r.status_code == 400


def test_classification_locked_after_approval(client, master, make_admin):
    maker = make_admin()
    doc = admin_doc(client, maker, "CM", "internal")
    start_review(client, master, doc["id"])
    approve(client, master, doc["id"])
    assert client.patch(f"/api/admin/documents/{doc['id']}", headers=auth(maker), json={"access_level": "public"}).status_code == 409


def test_secondary_admin_cannot_override_status(client, make_admin):
    a = make_admin()
    doc = admin_doc(client, a, "CM", "internal")
    assert client.post(f"/api/master/documents/{doc['id']}/override", headers=auth(a),
                       json={"status": "shared_externally", "reason": "please"}).status_code == 403
