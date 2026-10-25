"""Shared fixtures. The app reads its settings at import time, so the environment is set before importing it.

Default: a fresh SQLite file per run. Set TEST_DATABASE_URL to run against PostgreSQL (what Supabase uses); the
database name must contain "test" because its public schema is dropped and recreated.
"""
import atexit
import os
import shutil
import socket
import tempfile
import threading
import time
import uuid
from pathlib import Path

import pytest

_tmp = Path(tempfile.mkdtemp(prefix="oncm-tests-"))
atexit.register(shutil.rmtree, _tmp, ignore_errors=True)  # test DB + uploaded files; runs leave nothing behind
_db_url = os.environ.get("TEST_DATABASE_URL") or f"sqlite:///{_tmp / 'test.db'}"
if not _db_url.startswith("sqlite") and "test" not in _db_url.rsplit("/", 1)[-1]:
    raise RuntimeError("TEST_DATABASE_URL must point at a database whose name contains 'test' (it is wiped)")

os.environ.update({
    "DATABASE_URL": _db_url,
    "STORAGE_BACKEND": "local",
    "LOCAL_STORAGE_PATH": str(_tmp / "storage"),
    "SECRET_KEY": "test-secret-key-not-for-production",
    "MASTER_ADMIN_EMAIL": "master@test.oncm",
    "MASTER_ADMIN_PASSWORD": "Master!Pass123",
    "MASTER_ADMIN_NAME": "Test Master",
    "APP_BASE_URL": "http://testserver",
    "TWO_PERSON_RULE": "true",
    "MAX_UPLOAD_MB": "25",
    "SMTP_HOST": "", "ALERT_SMTP_HOST": "", "ALERT_SMTP_USER": "", "ALERT_SMTP_PASSWORD": "",
    "ALERT_RECIPIENTS": "", "IMAP_HOST": "", "MALWARE_SCAN": "off", "SEARCH_BACKEND": "db", "APP_ENV": "production",
})

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app import core  # noqa: E402
from app.core import SessionLocal, engine, hash_password, make_token  # noqa: E402
from app.models import Organization, User  # noqa: E402
from app.services import make_checklist  # noqa: E402

MASTER_PW = os.environ["MASTER_ADMIN_PASSWORD"]


def _reset_postgres():
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))


if engine.dialect.name == "postgresql":
    _reset_postgres()

from app.main import app  # noqa: E402


@pytest.fixture(scope="session")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture(autouse=True)
def _clear_rate_limits():
    core._hits.clear()


@pytest.fixture
def db():
    s = SessionLocal()
    yield s
    s.close()


def uid() -> str:
    return uuid.uuid4().hex[:8]


def auth(user: User) -> dict:
    return {"Authorization": f"Bearer {make_token(user.id)}"}


@pytest.fixture(scope="session")
def master(client) -> User:
    s = SessionLocal()
    try:
        return s.query(User).filter_by(role="master_admin").first()
    finally:
        s.close()


@pytest.fixture
def make_admin(db):
    def _make(can_share=False, can_export=False, repo_access=None, password="Admin!Pass123") -> User:
        u = User(email=f"admin-{uid()}@test.oncm", name=f"Admin {uid()}", role="secondary_admin",
                 password_hash=hash_password(password), can_share=can_share, can_export=can_export,
                 repo_access=repo_access if repo_access is not None else ["CM", "POL", "RES", "PPD", "CDP"])
        db.add(u)
        db.commit()
        return u
    return _make


@pytest.fixture
def make_org(db):
    """An approved organization with one participant who can sign in."""
    def _make(actor_type="Project Developer", assigned_admin: User | None = None, password="Partner!Pass123"):
        o = Organization(name=f"Org {uid()}", actor_type=actor_type, contact_name="Contact",
                         contact_email=f"contact-{uid()}@test.oncm", assigned_admin_id=assigned_admin.id if assigned_admin else None)
        db.add(o)
        db.flush()
        make_checklist(db, o)
        p = User(email=f"partner-{uid()}@test.oncm", name="Partner User", role="participant", organization_id=o.id,
                 password_hash=hash_password(password), is_active=True)
        db.add(p)
        db.commit()
        return o, p
    return _make


def pdf_bytes(size: int = 2048) -> bytes:
    return b"%PDF-1.4\n" + os.urandom(max(size - 9, 1))


def pdf_file(name: str | None = None, size: int = 2048):
    return {"file": (name or f"{uid()}.pdf", pdf_bytes(size), "application/pdf")}


def admin_doc(client, user: User, repo_category="CM", access_level="internal", **form):
    """Create a repository document as an admin. Returns the JSON body."""
    data = {"title": f"Doc {uid()}", "repo_category": repo_category, "access_level": access_level, **form}
    r = client.post("/api/admin/documents", headers=auth(user), data=data, files=pdf_file())
    assert r.status_code == 201, r.text
    return r.json()


def participant_doc(client, participant: User, category="01 Onboarding documents"):
    r = client.post("/api/me/documents", headers=auth(participant), files=pdf_file(),
                    data={"title": f"Submission {uid()}", "category": category})
    assert r.status_code == 201, r.text
    return r.json()


def approve(client, approver: User, doc_id: int, **extra):
    body = {"decision": "approve", "confirm_classification": True, **extra}
    r = client.post(f"/api/admin/documents/{doc_id}/review", headers=auth(approver), json=body)
    assert r.status_code == 200, r.text
    return r.json()


def start_review(client, reviewer: User, doc_id: int):
    r = client.post(f"/api/admin/documents/{doc_id}/review", headers=auth(reviewer), json={"decision": "start_review"})
    assert r.status_code == 200, r.text
    return r.json()


# ------------------------------------------------------------------ a real server for concurrency and browser tests
def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="session")
def live_server(client):
    """uvicorn on a real socket, single process, like the Docker image. Yields the base URL."""
    import uvicorn
    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", lifespan="off"))
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    deadline = time.time() + 10
    while not server.started:
        if time.time() > deadline:
            raise RuntimeError("live server did not start")
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    t.join(timeout=5)
