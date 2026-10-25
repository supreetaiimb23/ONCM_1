"""End-to-end through the API: join request -> approval -> invite -> submission -> review -> publication -> download."""
from app.models import JoinRequest, Notification, Organization, User
from tests.conftest import approve, auth, pdf_file, start_review, uid

JOIN = {
    "org_type": "Private Limited Company", "registration_number": "U12345KA2020PTC123456",
    "date_of_incorporation": "2020-01-15", "country": "India", "state_ut": "Karnataka", "city": "Bengaluru",
    "year_established": 2020, "registered_address": "1 MG Road, Bengaluru 560001", "employee_range": "11-50",
    "geographic_presence": ["South India"], "beneficiary_segments": ["Smallholder farmers"],
    "carbon_services": ["Project development"], "org_profile": "We develop agroforestry carbon projects.",
    "contact_name": "Asha Rao", "actor_type": "Project Developer",
}


def join_payload(**kw):
    return {**JOIN, "org_name": f"Green Acres {uid()}", "contact_email": f"asha-{uid()}@test.oncm",
            "registration_number": f"U{uid()}", **kw}


def test_join_request_validation(client):
    r = client.post("/api/join-requests", json=join_payload(org_type="Spaceship", geographic_presence=[]))
    assert r.status_code == 422
    r = client.post("/api/join-requests", json=join_payload(date_of_incorporation="2999-01-01"))
    assert r.status_code == 422


def test_duplicate_join_request_is_flagged(client, db):
    body = join_payload()
    client.post("/api/join-requests", json=body)
    r = client.post("/api/join-requests", json={**body, "org_name": body["org_name"] + " Ltd", "contact_email": f"x-{uid()}@test.oncm"})
    assert db.get(JoinRequest, r.json()["id"]).duplicate_flag is True


def test_full_onboarding_and_document_lifecycle(client, master, make_admin, db):
    reviewer = make_admin()

    # 1. Public join request
    body = join_payload()
    r = client.post("/api/join-requests", json=body)
    assert r.status_code == 201
    jid = r.json()["id"]
    assert client.get("/api/master/join-requests", headers=auth(master)).json()[0]["id"] == jid

    # 2. Master Admin approves: workspace, checklist, participant and invite link are created
    r = client.post(f"/api/master/join-requests/{jid}/decision", headers=auth(master),
                    json={"decision": "approve", "assigned_admin_id": reviewer.id})
    assert r.status_code == 200, r.text
    org_id, link = r.json()["organization_id"], r.json()["invite_link"]
    assert db.get(Organization, org_id).assigned_admin_id == reviewer.id
    assert db.query(Notification).filter_by(event="join_approved", recipient=body["contact_email"]).count() == 1

    # 3. Participant sets a password from the invite and signs in
    token = link.split("invite=")[1]
    r = client.post("/api/auth/accept-invite", json={"token": token, "password": "Partner!Pass123"})
    assert r.status_code == 200
    ph = {"Authorization": f"Bearer {r.json()['token']}"}
    dash = client.get("/api/me/dashboard", headers=ph).json()
    assert dash["organization"]["stage"] == "joined" and len(dash["checklist"]) >= 4

    # 4. Participant submits a document: confidential (restricted), code allocated, stage moves on
    r = client.post("/api/me/documents", headers=ph, files=pdf_file("registration.pdf"),
                    data={"title": "Registration form", "category": "01 Onboarding documents"})
    assert r.status_code == 201, r.text
    doc = r.json()
    assert doc["status"] == "submitted" and doc["code"].startswith("ONCM-PPD-")
    assert client.get("/api/me/dashboard", headers=ph).json()["organization"]["stage"] == "documents_in_progress"

    # 5. Assigned reviewer starts review and requests changes; the internal note never reaches the participant
    start_review(client, reviewer, doc["id"])
    r = client.post(f"/api/admin/documents/{doc['id']}/review", headers=auth(reviewer),
                    json={"decision": "changes", "feedback": "Please sign page 2", "comment": "INTERNAL: signature looks off"})
    assert r.status_code == 200
    mine = client.get("/api/me/documents", headers=ph).json()[0]
    assert mine["status"] == "changes_requested" and mine["feedback"] == "Please sign page 2"
    assert "INTERNAL" not in str(client.get("/api/me/documents", headers=ph).json())

    # 6. Participant uploads a corrected version
    r = client.post(f"/api/me/documents/{doc['id']}/versions", headers=ph, files=pdf_file("registration-signed.pdf"))
    assert r.status_code == 201 and r.json()["current_version"] == 2

    # 7. Review again; the reviewer recommends and the Master Admin gives final approval (two-person rule)
    start_review(client, reviewer, doc["id"])
    r = client.post(f"/api/admin/documents/{doc['id']}/review", headers=auth(reviewer), json={"decision": "approve", "confirm_classification": True})
    assert r.status_code == 403
    assert client.post(f"/api/admin/documents/{doc['id']}/review", headers=auth(reviewer), json={"decision": "recommend"}).status_code == 200
    assert approve(client, master, doc["id"])["status"] == "internally_approved"

    # 8. Approved is still invisible to the participant's Resources until published
    assert client.get("/api/me/resources", headers=ph).json() == []
    r = client.post(f"/api/admin/documents/{doc['id']}/publish", headers=auth(master), json={"organization_ids": [org_id]})
    assert r.status_code == 201, r.text
    res = client.get("/api/me/resources", headers=ph).json()
    assert [x["id"] for x in res] == [doc["id"]]

    # 9. Download goes through a short-lived signed link and returns the published version's bytes
    url = client.get(f"/api/me/resources/{doc['id']}/download", headers=ph).json()["url"]
    f = client.get(url)
    assert f.status_code == 200 and f.content.startswith(b"%PDF")

    # 10. Every step is audited
    actions = {a["action"] for a in client.get("/api/master/audit-logs?limit=1000", headers=auth(master)).json()}
    assert {"join_request.approve", "document.submitted", "document.changes", "document.approve", "document.published",
            "resource.download"} <= actions


def test_rejected_join_request_needs_reason(client, master):
    jid = client.post("/api/join-requests", json=join_payload()).json()["id"]
    assert client.post(f"/api/master/join-requests/{jid}/decision", headers=auth(master), json={"decision": "reject"}).status_code == 400
    r = client.post(f"/api/master/join-requests/{jid}/decision", headers=auth(master), json={"decision": "reject", "reason": "Not eligible"})
    assert r.status_code == 200
    assert client.post(f"/api/master/join-requests/{jid}/decision", headers=auth(master),
                       json={"decision": "approve"}).status_code == 409


def test_org_cannot_go_live_with_open_checklist(client, master, make_org):
    o, _ = make_org()
    r = client.patch(f"/api/admin/organizations/{o.id}", headers=auth(master), json={"stage": "live"})
    assert r.status_code == 409
    r = client.patch(f"/api/admin/organizations/{o.id}", headers=auth(master),
                     json={"stage": "live", "override": True, "reason": "Board decision 12 Oct"})
    assert r.status_code == 200 and r.json()["stage"] == "live"


def test_suspending_org_deactivates_members(client, master, make_org, db):
    o, p = make_org()
    r = client.patch(f"/api/admin/organizations/{o.id}", headers=auth(master), json={"status": "suspended", "reason": "Contract ended"})
    assert r.status_code == 200
    db.expire_all()
    assert db.get(User, p.id).is_active is False
