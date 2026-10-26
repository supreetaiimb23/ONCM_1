"""Unit tests for workflow rules, classification, file validation and storage helpers."""
import io

import pytest
from fastapi import HTTPException, UploadFile

from app import alerts
from app.core import settings
from app.models import Document, User
from app.services import (ALLOWED_LEVELS, TRANSITIONS, LocalStorage, can_see_doc, check_transition, doc_code,
                          read_validated, validate_classification)


# ------------------------------------------------------------------ status transitions
@pytest.mark.parametrize("cur,new", [(c, n) for c, ns in TRANSITIONS.items() for n in ns])
def test_allowed_transitions(cur, new):
    check_transition(cur, new)


@pytest.mark.parametrize("cur,new", [
    ("draft", "shared_externally"),            # cannot publish without review and approval
    ("submitted", "internally_approved"),      # cannot skip review
    ("under_review", "shared_externally"),
    ("rejected", "internally_approved"),
    ("superseded", "shared_externally"),
    ("archived", "shared_externally"),
])
def test_forbidden_transitions(cur, new):
    with pytest.raises(HTTPException) as e:
        check_transition(cur, new)
    assert e.value.status_code == 409


# ------------------------------------------------------------------ access classification
@pytest.mark.parametrize("code,level", [("AGR", "public"), ("AGR", "pilot_shared"), ("CDP", "public"),
                                        ("CDP", "restricted"), ("PPD", "public"), ("PPD", "request_only")])
def test_category_rejects_levels_outside_the_sharing_table(code, level):
    with pytest.raises(HTTPException):
        validate_classification(code, level, ["VVB"])


@pytest.mark.parametrize("code", ["CM", "POL", "RES"])
def test_open_categories_accept_every_level(code):
    for level in ALLOWED_LEVELS[code]:
        validate_classification(code, level, ["VVB"] if level == "role_specific" else None)


def test_role_specific_needs_known_actor_types():
    with pytest.raises(HTTPException):
        validate_classification("CM", "role_specific", [])
    with pytest.raises(HTTPException):
        validate_classification("CM", "role_specific", ["Hacker"])
    validate_classification("CM", "role_specific", ["VVB", "TSP"])


def test_unknown_level_rejected():
    with pytest.raises(HTTPException):
        validate_classification("CM", "secret", None)


# ------------------------------------------------------------------ document code
def test_doc_code_format():
    d = Document(repo_category="POL", year=2026, current_version=2, serial=3)
    assert doc_code(d) == "ONCM-POL-2026-V2-0003"


def test_doc_code_absent_until_serial_assigned():
    assert doc_code(Document(repo_category="POL", year=2026, current_version=1, serial=None)) is None


# ------------------------------------------------------------------ internal visibility
def _admin(**kw):
    return User(id=10, role="secondary_admin", repo_access=["CM", "PPD"], **kw)


def test_master_sees_everything():
    assert can_see_doc(User(id=1, role="master_admin"), Document(repo_category="AGR", organization_id=5))


def test_secondary_limited_to_categories_and_assigned_orgs():
    a = _admin()
    assert can_see_doc(a, Document(repo_category="CM"), [])
    assert not can_see_doc(a, Document(repo_category="AGR"), [])
    assert can_see_doc(a, Document(repo_category="PPD", organization_id=7), [7])
    assert not can_see_doc(a, Document(repo_category="PPD", organization_id=8), [7])


def test_assigned_reviewer_sees_doc_outside_their_categories():
    assert can_see_doc(_admin(), Document(repo_category="AGR", assigned_reviewer_id=10), [])


# ------------------------------------------------------------------ upload validation
def _upload(name: str, data: bytes) -> UploadFile:
    return UploadFile(file=io.BytesIO(data), filename=name)


def test_valid_pdf_returns_checksum_and_safe_name():
    f = read_validated(_upload("../../etc/My Report (final).pdf", b"%PDF-1.4 hello"))
    assert f.name == "My Report _final_.pdf"
    assert len(f.checksum) == 64 and f.data.startswith(b"%PDF")


@pytest.mark.parametrize("name,data,status", [
    ("run.exe", b"MZ....", 400),               # extension not allowed
    ("fake.pdf", b"MZ not a pdf", 400),        # content does not match extension
    ("fake.docx", b"%PDF-1.4", 400),
    ("empty.pdf", b"", 400),
    ("binary.csv", b"a,b\x00c", 400),
])
def test_rejected_uploads(name, data, status):
    with pytest.raises(HTTPException) as e:
        read_validated(_upload(name, data))
    assert e.value.status_code == status


def test_upload_size_limit(monkeypatch):
    monkeypatch.setattr(settings, "max_upload_mb", 1)
    with pytest.raises(HTTPException) as e:
        read_validated(_upload("big.pdf", b"%PDF" + b"0" * (1024 * 1024 + 1)))
    assert e.value.status_code == 413


def test_csv_and_txt_accepted():
    assert read_validated(_upload("data.csv", b"a,b\n1,2\n")).name == "data.csv"


# ------------------------------------------------------------------ storage
def test_local_storage_blocks_path_traversal(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "local_storage_path", str(tmp_path))
    s = LocalStorage()
    s.save("organizations/1/a.pdf", b"x")
    assert (tmp_path / "organizations/1/a.pdf").read_bytes() == b"x"
    for key in ("../outside.pdf", "/etc/passwd", "a/../../outside.pdf"):
        with pytest.raises(HTTPException):
            s.path(key)


# ------------------------------------------------------------------ alerts
def test_redact_removes_secure_links():
    body = "Set your password: http://x/?invite=abc.def and download http://x/api/request-access/download?token=t"
    out = alerts.redact(body)
    assert "invite=" not in out and "token=" not in out
    assert out.count("[secure link removed]") == 2


def test_secure_link_emails_are_never_mirrored():
    assert {"member_invited", "join_approved", "access_approved"} <= alerts.SKIP_MIRROR


def test_alert_categories_validated():
    assert alerts.clean_categories(None) is None
    assert alerts.clean_categories(["joins", "joins"]) == ["joins"]
    with pytest.raises(ValueError):
        alerts.clean_categories(["nope"])
    with pytest.raises(ValueError):
        alerts.clean_categories([])


def test_s3_uploads_use_the_kms_key_when_configured(monkeypatch):
    from app.services import S3Storage
    calls = []
    s3 = S3Storage.__new__(S3Storage)  # no boto3 client needed
    s3.bucket, s3.client = "b", type("C", (), {"put_object": lambda self, **kw: calls.append(kw)})()
    s3.save("k", b"x")
    assert calls[-1]["ServerSideEncryption"] == "AES256"
    monkeypatch.setattr(settings, "s3_kms_key_id", "arn:aws:kms:ap-south-1:1:key/abc")
    s3.save("k", b"x")
    assert calls[-1]["ServerSideEncryption"] == "aws:kms" and calls[-1]["SSEKMSKeyId"].endswith("key/abc")
