"""Publication rules, audiences, request-only access and superseding."""
from app.models import Notification
from tests.conftest import admin_doc, approve, auth, start_review


def approved_doc(client, master, maker, repo_category, level, **form):
    doc = admin_doc(client, maker, repo_category, level, **form)
    start_review(client, master, doc["id"])
    approve(client, master, doc["id"])
    return doc


def test_cannot_publish_before_approval(client, master, make_admin):
    doc = admin_doc(client, make_admin(), "CM", "pilot_shared")
    assert client.post(f"/api/admin/documents/{doc['id']}/publish", headers=auth(master), json={}).status_code == 409


def test_internal_documents_can_never_be_published(client, master, make_admin):
    doc = approved_doc(client, master, make_admin(), "CM", "internal")
    assert client.post(f"/api/admin/documents/{doc['id']}/publish", headers=auth(master), json={}).status_code == 409


def test_role_specific_audience_cannot_exceed_approval(client, master, make_admin, make_org):
    doc = approved_doc(client, master, make_admin(), "CM", "role_specific", target_actor_types="VVB")
    r = client.post(f"/api/admin/documents/{doc['id']}/publish", headers=auth(master), json={"actor_types": ["VVB", "TSP"]})
    assert r.status_code == 400
    assert client.post(f"/api/admin/documents/{doc['id']}/publish", headers=auth(master), json={"actor_types": ["VVB"]}).status_code == 201
    _, vvb = make_org("VVB")
    _, tsp = make_org("TSP")
    assert doc["id"] in [x["id"] for x in client.get("/api/me/resources", headers=auth(vvb)).json()]
    assert doc["id"] not in [x["id"] for x in client.get("/api/me/resources", headers=auth(tsp)).json()]


def test_restricted_needs_named_recipients_and_reaches_only_them(client, master, make_admin, make_org):
    doc = approved_doc(client, master, make_admin(), "CM", "restricted")
    assert client.post(f"/api/admin/documents/{doc['id']}/publish", headers=auth(master), json={}).status_code == 400
    o1, p1 = make_org()
    _, p2 = make_org()
    assert client.post(f"/api/admin/documents/{doc['id']}/publish", headers=auth(master), json={"organization_ids": [o1.id]}).status_code == 201
    assert doc["id"] in [x["id"] for x in client.get("/api/me/resources", headers=auth(p1)).json()]
    assert client.get(f"/api/me/resources/{doc['id']}/download", headers=auth(p2)).status_code == 404


def test_pilot_shared_notifies_active_orgs_after_commit(client, master, make_admin, make_org, db):
    o, p = make_org()
    doc = approved_doc(client, master, make_admin(), "CDP", "pilot_shared")
    r = client.post(f"/api/admin/documents/{doc['id']}/publish", headers=auth(master), json={})
    assert r.status_code == 201
    assert db.query(Notification).filter_by(event="document_shared", recipient=o.contact_email).count() == 1
    assert doc["id"] in [x["id"] for x in client.get("/api/me/resources", headers=auth(p)).json()]


def test_failed_request_sends_no_email(client, master, make_admin, db):
    """Emails are queued in the same transaction; a rejected action must not leave any behind."""
    doc = admin_doc(client, make_admin(), "CM", "internal")
    before = db.query(Notification).count()
    client.post(f"/api/admin/documents/{doc['id']}/review", headers=auth(master), json={"decision": "changes"})  # 400: no reason
    db.expire_all()
    assert db.query(Notification).count() == before


def test_public_documents_need_no_login(client, master, make_admin):
    doc = approved_doc(client, master, make_admin(), "RES", "public")
    client.post(f"/api/admin/documents/{doc['id']}/publish", headers=auth(master), json={})
    pub = client.get("/api/public/resources").json()
    assert doc["id"] in [x["id"] for x in pub["public"]]
    url = client.get(f"/api/public/resources/{doc['id']}/download").json()["url"]
    assert client.get(url).content.startswith(b"%PDF")


def test_unpublish_hides_document_again(client, master, make_admin):
    doc = approved_doc(client, master, make_admin(), "RES", "public")
    share = client.post(f"/api/admin/documents/{doc['id']}/publish", headers=auth(master), json={}).json()
    assert client.delete(f"/api/admin/shares/{share['id']}", headers=auth(master)).status_code == 200
    assert doc["id"] not in [x["id"] for x in client.get("/api/public/resources").json()["public"]]
    assert client.get(f"/api/public/resources/{doc['id']}/download").status_code == 404


def test_request_only_flow_for_external_requester(client, master, make_admin):
    doc = approved_doc(client, master, make_admin(), "RES", "request_only")
    client.post(f"/api/admin/documents/{doc['id']}/publish", headers=auth(master), json={})

    listed = client.get("/api/public/resources").json()["request_only"]
    assert doc["id"] in [x["id"] for x in listed]
    assert "url" not in str(listed)
    assert client.get(f"/api/public/resources/{doc['id']}/download").status_code == 404   # file is never public

    r = client.post("/api/public/access-requests", json={"document_id": doc["id"], "requester_name": "Ravi Kumar",
                                                        "requester_email": "ravi@test.oncm", "purpose": "Academic research"})
    assert r.status_code == 201
    req = next(x for x in client.get("/api/admin/access-requests", headers=auth(master)).json() if x["document_id"] == doc["id"])
    r = client.post(f"/api/admin/access-requests/{req['id']}/decision", headers=auth(master), json={"decision": "approve", "valid_days": 7})
    link = r.json()["link"]
    path = link.split("http://testserver", 1)[1]
    dl = client.get(path, follow_redirects=False)
    assert dl.status_code == 307
    assert client.get(dl.headers["location"]).content.startswith(b"%PDF")
    assert client.post(f"/api/admin/access-requests/{req['id']}/decision", headers=auth(master), json={"decision": "approve"}).status_code == 409


def test_request_only_flow_for_participant(client, master, make_admin, make_org):
    doc = approved_doc(client, master, make_admin(), "RES", "request_only")
    client.post(f"/api/admin/documents/{doc['id']}/publish", headers=auth(master), json={})
    _, p = make_org()
    cat = client.get("/api/me/catalogue", headers=auth(p)).json()
    assert next(x for x in cat if x["id"] == doc["id"])["request_status"] is None
    assert client.get(f"/api/me/resources/{doc['id']}/download", headers=auth(p)).status_code == 404
    assert client.post("/api/me/access-requests", headers=auth(p), json={"document_id": doc["id"], "purpose": "Need it for MRV"}).status_code == 201
    assert client.post("/api/me/access-requests", headers=auth(p), json={"document_id": doc["id"], "purpose": "Again please"}).status_code == 409
    req = next(x for x in client.get("/api/admin/access-requests", headers=auth(master)).json() if x["user_id"] == p.id)
    client.post(f"/api/admin/access-requests/{req['id']}/decision", headers=auth(master), json={"decision": "approve"})
    assert doc["id"] in [x["id"] for x in client.get("/api/me/resources", headers=auth(p)).json()]


def test_superseding_unpublishes_the_old_document(client, master, make_admin):
    maker = make_admin()
    old = approved_doc(client, master, maker, "RES", "public")
    client.post(f"/api/admin/documents/{old['id']}/publish", headers=auth(master), json={})
    new = admin_doc(client, maker, "RES", "public")
    r = client.post("/api/admin/changelog", headers=auth(master),
                    json={"title": "Use the newer interaction deck", "document_id": new["id"], "supersedes_document_id": old["id"]})
    assert r.status_code == 201
    assert client.get(f"/api/admin/documents/{old['id']}", headers=auth(master)).json()["status"] == "superseded"
    assert old["id"] not in [x["id"] for x in client.get("/api/public/resources").json()["public"]]
