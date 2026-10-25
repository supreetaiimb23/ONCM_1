"""Admin read paths must run a fixed number of SQL queries, whatever the number of rows (no N+1)."""
from contextlib import contextmanager
from datetime import timedelta

import pytest
from sqlalchemy import event

from app.core import engine
from app.models import Document, DocumentGrant, DocumentShare, DocumentVersion, Workstream
from app.services import utcnow
from tests.conftest import auth, uid


@contextmanager
def count_queries():
    n = [0]

    def _count(*_):
        n[0] += 1
    event.listen(engine, "before_cursor_execute", _count)
    try:
        yield n
    finally:
        event.remove(engine, "before_cursor_execute", _count)


def add_docs(db, n, maker, reviewer, org=None, ws=None):
    """n documents with versions by different makers, live shares and grants - every field doc_out looks up."""
    docs = []
    for i in range(n):
        d = Document(repo_category="CM", year=2099, title=f"Bulk {uid()}", category="Carbon Markets", status="shared_externally",
                     current_version=2, organization_id=org.id if org and i % 2 else None, workstream_id=ws.id if ws else None,
                     assigned_reviewer_id=reviewer.id, approver_id=reviewer.id, created_by=maker.id, origin="admin")
        db.add(d)
        docs.append(d)
    db.flush()
    for d in docs:
        db.add_all([DocumentVersion(document_id=d.id, version=1, storage_key="k", uploader_id=maker.id),
                    DocumentVersion(document_id=d.id, version=2, storage_key="k", uploader_id=reviewer.id)])
        sh = DocumentShare(document_id=d.id, version=2, audience="restricted", status="active",
                           expires_at=utcnow() + timedelta(days=1))
        db.add(sh)
        db.flush()
        if org:
            db.add(DocumentGrant(share_id=sh.id, document_id=d.id, organization_id=org.id))
    db.commit()
    return docs


@pytest.fixture
def scoped_admin(db, make_admin, make_org):
    a = make_admin()
    o, p = make_org(assigned_admin=a)
    ws = Workstream(name=f"WS {uid()}", owner_id=a.id)
    db.add(ws)
    db.commit()
    return a, o, p, ws


def queries_for(client, user, path):
    with count_queries() as n:
        r = client.get(path, headers=auth(user))
    assert r.status_code == 200, r.text
    return n[0], r


@pytest.mark.parametrize("who", ["master", "secondary"])
def test_document_list_query_count_is_flat(client, db, master, scoped_admin, who):
    a, o, _, ws = scoped_admin
    u = master if who == "master" else a
    add_docs(db, 5, master, a, o, ws)
    small, r1 = queries_for(client, u, "/api/admin/documents")
    add_docs(db, 60, master, a, o, ws)
    big, r2 = queries_for(client, u, "/api/admin/documents")
    assert len(r2.json()) >= len(r1.json()) + 60
    assert big <= 15 and big == small, (small, big)
    row = next(d for d in r2.json() if d["title"].startswith("Bulk"))
    assert row["maker_name"] == a.name and row["share_count"] == 1 and row["workstream_name"] == ws.name


def test_overview_and_org_detail_query_count_is_flat(client, db, master, scoped_admin):
    a, o, _, ws = scoped_admin
    add_docs(db, 3, master, a, o, ws)
    ov_small, _ = queries_for(client, a, "/api/admin/overview")
    org_small, _ = queries_for(client, a, f"/api/admin/organizations/{o.id}")
    add_docs(db, 40, master, a, o, ws)
    ov_big, r = queries_for(client, a, "/api/admin/overview")
    org_big, _ = queries_for(client, a, f"/api/admin/organizations/{o.id}")
    assert ov_big == ov_small and org_big == org_small, (ov_small, ov_big, org_small, org_big)
    assert r.json()["repository"]["published"] >= 43


def test_participant_resources_query_count_is_flat(client, db, master, scoped_admin):
    a, o, p, ws = scoped_admin
    add_docs(db, 3, master, a, o, ws)
    small, r1 = queries_for(client, p, "/api/me/resources")
    add_docs(db, 40, master, a, o, ws)
    big, r2 = queries_for(client, p, "/api/me/resources")
    assert len(r2.json()) == len(r1.json()) + 40
    assert big == small, (small, big)


def test_excel_export_query_count_is_flat(client, db, master, scoped_admin):
    a, o, _, ws = scoped_admin
    add_docs(db, 3, master, a, o, ws)
    small, _ = queries_for(client, master, "/api/admin/reports/export")
    add_docs(db, 40, master, a, o, ws)
    big, _ = queries_for(client, master, "/api/admin/reports/export")
    assert big == small, (small, big)
