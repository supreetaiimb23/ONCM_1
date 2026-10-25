"""Upload behaviour through the API: versions, duplicates, limits and atomicity."""
from pathlib import Path

from fastapi import HTTPException

from app.core import settings
from app.models import Document, DocumentVersion
from tests.conftest import admin_doc, auth, participant_doc, pdf_bytes, pdf_file, uid


def test_versions_increment_and_old_versions_stay_downloadable(client, master):
    doc = admin_doc(client, master, "POL", "internal")
    for _ in range(2):
        r = client.post(f"/api/admin/documents/{doc['id']}/versions", headers=auth(master), files=pdf_file())
        assert r.status_code == 201
    det = client.get(f"/api/admin/documents/{doc['id']}", headers=auth(master)).json()
    assert [v["version"] for v in det["versions"]] == [1, 2, 3]
    assert "-V3-" in det["code"]
    url = client.get(f"/api/admin/documents/{doc['id']}/download?version=1", headers=auth(master)).json()["url"]
    assert client.get(url).status_code == 200


def test_document_codes_run_per_category_and_year(client, master):
    codes = [admin_doc(client, master, "CDP", "internal")["code"] for _ in range(3)]
    serials = [int(c.rsplit("-", 1)[1]) for c in codes]
    assert serials == list(range(serials[0], serials[0] + 3))


def test_identical_file_rejected_within_an_organization(client, make_org):
    _, p = make_org()
    data = pdf_bytes()
    form = {"title": "Same", "category": "01 Onboarding documents"}
    assert client.post("/api/me/documents", headers=auth(p), data=form, files={"file": ("a.pdf", data, "application/pdf")}).status_code == 201
    assert client.post("/api/me/documents", headers=auth(p), data=form, files={"file": ("b.pdf", data, "application/pdf")}).status_code == 409


def test_rejected_upload_leaves_no_document_behind(client, master, db):
    title = f"Bad {uid()}"
    r = client.post("/api/admin/documents", headers=auth(master), data={"title": title, "repo_category": "CM"},
                    files={"file": ("evil.pdf", b"MZ\x90\x00 not a pdf", "application/pdf")})
    assert r.status_code == 400
    assert db.query(Document).filter_by(title=title).count() == 0


def test_duplicate_upload_is_rolled_back(client, make_org, db):
    _, p = make_org()
    data = pdf_bytes()
    form = {"title": f"Dup {uid()}", "category": "01 Onboarding documents"}
    client.post("/api/me/documents", headers=auth(p), data=form, files={"file": ("a.pdf", data, "application/pdf")})
    client.post("/api/me/documents", headers=auth(p), data=form, files={"file": ("b.pdf", data, "application/pdf")})
    assert db.query(Document).filter_by(title=form["title"]).count() == 1


def test_file_over_limit_rejected(client, master, monkeypatch):
    monkeypatch.setattr(settings, "max_upload_mb", 1)
    r = client.post("/api/admin/documents", headers=auth(master), data={"title": "Big", "repo_category": "CM"},
                    files={"file": ("big.pdf", pdf_bytes(1024 * 1024 + 10), "application/pdf")})
    assert r.status_code == 413


def test_large_file_round_trip(client, master, db):
    """24 MB (just under the 25 MB default limit) is stored intact and comes back byte for byte."""
    data = pdf_bytes(24 * 1024 * 1024)
    r = client.post("/api/admin/documents", headers=auth(master), data={"title": "Large", "repo_category": "RES"},
                    files={"file": ("large.pdf", data, "application/pdf")})
    assert r.status_code == 201
    v = db.query(DocumentVersion).filter_by(document_id=r.json()["id"]).one()
    assert v.size == len(data)
    url = client.get(f"/api/admin/documents/{r.json()['id']}/download", headers=auth(master)).json()["url"]
    assert client.get(url).content == data


def test_participant_can_only_replace_when_allowed(client, master, make_org):
    _, p = make_org()
    doc = participant_doc(client, p)
    client.post(f"/api/admin/documents/{doc['id']}/review", headers=auth(master), json={"decision": "start_review"})
    r = client.post(f"/api/me/documents/{doc['id']}/versions", headers=auth(p), files=pdf_file())
    assert r.status_code == 409   # under review: ONCM is looking at it


def test_local_download_link_is_signed(client):
    assert client.get("/api/files/local?token=forged").status_code == 401


def test_failed_request_removes_the_file_it_stored(client, master, monkeypatch):
    """The file is stored before the database commit; if the request then fails, the file must not stay behind."""
    doc = admin_doc(client, master, "POL", "internal")
    folder = Path(settings.local_storage_path).resolve() / "repository" / "pol" / str(doc["id"])
    before = sorted(folder.iterdir())

    def boom(*a, **k):
        raise HTTPException(500, "simulated failure after the file was stored")
    monkeypatch.setattr("app.routers.admin.audit", boom)
    r = client.post(f"/api/admin/documents/{doc['id']}/versions", headers=auth(master), files=pdf_file())
    assert r.status_code == 500
    assert sorted(folder.iterdir()) == before
    monkeypatch.undo()
    assert client.post(f"/api/admin/documents/{doc['id']}/versions", headers=auth(master), files=pdf_file()).status_code == 201
    assert len(list(folder.iterdir())) == len(before) + 1  # committed uploads keep their file
