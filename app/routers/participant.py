"""Participant / client side: own organization only, plus explicitly shared resources."""
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app import search
from app.core import get_db, participant_user
from app.models import (AccessRequest, ChecklistItem, Document, DocumentVersion, Notification, Organization, Task, User)
from app.services import (CATEGORIES, FOLDER_TO_CODE, STAGES, active_shares, add_version, assign_serial,
                          audit, catalogue_docs, check_transition, doc_code, doc_out, lock_document, new_document, notify,
                          participant_resources, read_validated, shared_mailbox, storage, version_row, row)

router = APIRouter(prefix="/api/me")


def _org(db, u) -> Organization:
    o = db.get(Organization, u.organization_id)
    if not o or o.status != "active":
        raise HTTPException(403, "Organization access is suspended")
    return o


def _own_doc(db, u, doc_id, lock=False) -> Document:
    d = lock_document(db, doc_id) if lock else db.get(Document, doc_id)
    # participants only ever touch documents THEY submitted for their own organization
    if not d or d.organization_id != u.organization_id or d.origin != "participant":
        raise HTTPException(404, "Document not found")
    return d


@router.get("/dashboard")
def dashboard(u: User = Depends(participant_user), db: Session = Depends(get_db)):
    o = _org(db, u)
    admin = db.get(User, o.assigned_admin_id) if o.assigned_admin_id else None
    cl = db.query(ChecklistItem).filter_by(organization_id=o.id).order_by(ChecklistItem.id).all()
    docs = db.query(Document).filter_by(organization_id=o.id, origin="participant").all()
    return {
        "organization": {"id": o.id, "name": o.name, "actor_type": o.actor_type, "stage": o.stage,
                         "waiting_on": o.waiting_on, "updated_at": row(o)["updated_at"]},
        "stages": STAGES,
        "oncm_contact": {"name": admin.name, "email": admin.email} if admin else {"name": "ONCM team", "email": shared_mailbox()},
        "checklist": [{"id": c.id, "title": c.title, "done": c.done} for c in cl],
        "docs_needing_action": [doc_out(db, d, True) for d in docs if d.status in ("changes_requested", "rejected")],
        "docs_with_oncm": [doc_out(db, d, True) for d in docs if d.status in ("submitted", "under_review")],
        "deadlines": [{"title": t.title, "due_date": row(t)["due_date"]} for t in
                      db.query(Task).filter(Task.organization_id == o.id, Task.status != "done", Task.due_date != None)  # noqa: E711
                      .order_by(Task.due_date).limit(5).all()],
    }


@router.get("/documents")
def my_documents(u: User = Depends(participant_user), db: Session = Depends(get_db)):
    docs = db.query(Document).filter_by(organization_id=u.organization_id, origin="participant").order_by(Document.id.desc()).all()
    out = []
    for d in docs:
        o = doc_out(db, d, True)
        o["versions"] = [version_row(v) for v in db.query(DocumentVersion).filter_by(document_id=d.id).order_by(DocumentVersion.version)]
        out.append(o)
    return out


@router.post("/documents", status_code=201)
def upload(category: str = Form(...), title: str = Form(...), doc_type: str = Form(""),
           file: UploadFile = File(...), u: User = Depends(participant_user), db: Session = Depends(get_db)):
    o = _org(db, u)
    if category not in CATEGORIES:
        raise HTTPException(400, "Unknown category")
    upload_ = read_validated(file)
    d = new_document(db, FOLDER_TO_CODE[category], organization_id=o.id, category=category, title=title.strip()[:250],
                     doc_type=doc_type[:100], origin="participant", status="submitted", created_by=u.id,
                     assigned_reviewer_id=o.assigned_admin_id, access_level="restricted")  # submissions are confidential by default
    add_version(db, d, upload_, u)
    assign_serial(db, d)
    o.waiting_on = "oncm"
    if o.stage == "joined":
        o.stage = "documents_in_progress"
    reviewer = db.get(User, o.assigned_admin_id) if o.assigned_admin_id else None
    notify(db, "document_uploaded", reviewer.email if reviewer else shared_mailbox(), f"Document submitted: {o.name}",
           f"{o.name} submitted a new document for review ({doc_code(d)}). Open the approval inbox in the Workbench.",
           f"doc-uploaded:{d.id}:1")
    notify(db, "submission_confirmed", u.email, "Submission received",
           f"We received “{d.title}”. You will be notified when ONCM reviews it.", f"doc-confirm:{d.id}:1")
    audit(db, u, "document.submitted", "document", d.id, {"org": o.id})
    db.commit()
    return doc_out(db, d, True)


@router.post("/documents/{doc_id}/versions", status_code=201)
def replace(doc_id: int, file: UploadFile = File(...), u: User = Depends(participant_user), db: Session = Depends(get_db)):
    o = _org(db, u)
    _own_doc(db, u, doc_id)  # 404 before reading the file
    upload_ = read_validated(file)
    d = _own_doc(db, u, doc_id, lock=True)
    if d.status not in ("changes_requested", "rejected", "submitted"):
        raise HTTPException(409, "This document can't be replaced in its current status")
    check_transition(d.status, "submitted") if d.status != "submitted" else None
    ver = add_version(db, d, upload_, u)
    d.status = "submitted"
    o.waiting_on = "oncm"
    reviewer = db.get(User, o.assigned_admin_id) if o.assigned_admin_id else None
    notify(db, "document_uploaded", reviewer.email if reviewer else shared_mailbox(), f"Document resubmitted: {o.name}",
           f"{o.name} uploaded a corrected version (document {d.id}, v{ver.version}).", f"doc-uploaded:{d.id}:{ver.version}")
    audit(db, u, "document.resubmitted", "document", d.id, {"version": ver.version})
    db.commit()
    return doc_out(db, d, True)


@router.get("/documents/{doc_id}/download")
def download_own(doc_id: int, version: Optional[int] = None, u: User = Depends(participant_user), db: Session = Depends(get_db)):
    d = _own_doc(db, u, doc_id)
    v = db.query(DocumentVersion).filter_by(document_id=d.id, version=version or d.current_version).first()
    if not v:
        raise HTTPException(404, "Version not found")
    audit(db, u, "document.download", "document", d.id, {"version": v.version})
    db.commit()
    return {"url": storage().download_url(v.storage_key, v.filename), "filename": v.filename}


LEVEL_LABEL = {"pilot_shared": "Shared with all pilot participants", "role_specific": "For your stakeholder category",
               "restricted": "Shared with you", "public": "Public", "request_only": "Approved on request"}


@router.get("/resources")
def resources(q: str = "", u: User = Depends(participant_user), db: Session = Depends(get_db)):
    o = _org(db, u)
    pairs = participant_resources(db, u, o)
    if q.strip():  # only within what this participant can already open
        rank = {doc_id: n for n, doc_id in enumerate(search.matching_ids(db, q))}
        pairs = sorted((p for p in pairs if p[0].id in rank), key=lambda p: rank[p[0].id])
    out = []
    for d, s in pairs:
        x = doc_out(db, d, True)
        x.update(audience=s.audience, audience_label=LEVEL_LABEL[s.audience], shared_version=s.version)
        out.append(x)
    return out


@router.get("/resources/{doc_id}/download")
def download_resource(doc_id: int, u: User = Depends(participant_user), db: Session = Depends(get_db)):
    o = _org(db, u)
    pair = next(((d, s) for d, s in participant_resources(db, u, o) if d.id == doc_id), None)
    if not pair:
        raise HTTPException(404, "Resource not found")  # same answer for 'forbidden' and 'missing'
    d, s = pair
    v = db.query(DocumentVersion).filter_by(document_id=d.id, version=s.version).first()
    audit(db, u, "resource.download", "document", d.id, {"version": s.version})
    db.commit()
    return {"url": storage().download_url(v.storage_key, v.filename), "filename": v.filename}


@router.get("/catalogue")
def request_catalogue(q: str = "", u: User = Depends(participant_user), db: Session = Depends(get_db)):
    """Request-only resources: title and code are visible, the file is not until a request is approved."""
    _org(db, u)
    mine = {r.document_id: r for r in db.query(AccessRequest).filter_by(user_id=u.id).order_by(AccessRequest.id)}
    have = {d.id for d, _ in participant_resources(db, u, db.get(Organization, u.organization_id))}
    docs = catalogue_docs(db, "request_only")
    if q.strip():  # title and code only: searching file text would reveal what is inside before access is granted
        rank = {doc_id: n for n, doc_id in enumerate(search.matching_ids(db, q, full_text=False))}
        docs = sorted((p for p in docs if p[0].id in rank), key=lambda p: rank[p[0].id])
    out = []
    for d, s in docs:
        r = mine.get(d.id)
        out.append({"id": d.id, "code": doc_code(d), "title": d.title, "repo_category_name": doc_out(db, d, True)["repo_category_name"],
                    "request_status": "approved" if d.id in have else (r.status if r else None)})
    return out


class RequestIn(BaseModel):
    document_id: int
    purpose: str = Field(min_length=5, max_length=1000)


@router.post("/access-requests", status_code=201)
def request_access(body: RequestIn, u: User = Depends(participant_user), db: Session = Depends(get_db)):
    o = _org(db, u)
    d = db.get(Document, body.document_id)
    if not d or not any(s.audience == "request_only" for s in active_shares(db, d.id)) or d.status != "shared_externally":
        raise HTTPException(404, "This resource is not available on request")
    if db.query(AccessRequest).filter_by(document_id=d.id, user_id=u.id, status="pending").first():
        raise HTTPException(409, "You already have a pending request for this document")
    r = AccessRequest(document_id=d.id, requester_name=u.name, requester_email=u.email, requester_org=o.name,
                      purpose=body.purpose, user_id=u.id, source="portal")
    db.add(r)
    db.flush()
    notify(db, "access_requested", shared_mailbox(), f"Access request: {doc_code(d)}",
           f"{u.name} ({o.name}) asked for access to “{d.title}”.\nPurpose: {body.purpose}", f"access-req:{r.id}")
    audit(db, u, "access_request.created", "document", d.id)
    db.commit()
    return {"message": "Request sent. You will be emailed when ONCM decides."}


class QuestionIn(BaseModel):
    subject: str = Field(min_length=3, max_length=150)
    message: str = Field(min_length=5, max_length=3000)


@router.post("/questions", status_code=201)
def ask(body: QuestionIn, u: User = Depends(participant_user), db: Session = Depends(get_db)):
    o = _org(db, u)
    key = f"question:{u.id}:{hash((body.subject, body.message))}:{db.query(Notification).count()}"
    notify(db, "participant_question", shared_mailbox(), f"[{o.name}] {body.subject}",
           f"From: {u.name} <{u.email}> ({o.name}, {o.actor_type})\n\n{body.message}", key)
    audit(db, u, "support.question", "organization", o.id, {"subject": body.subject})
    db.commit()
    return {"message": "Sent to the ONCM support team."}


class ContactChangeIn(BaseModel):
    contact_name: str = Field(min_length=2, max_length=200)
    contact_email: str = Field(min_length=5, max_length=200)
    reason: str = Field(min_length=3, max_length=500)


@router.post("/contact-change", status_code=201)
def contact_change(body: ContactChangeIn, u: User = Depends(participant_user), db: Session = Depends(get_db)):
    """Controlled process: creates a task for ONCM; participants cannot edit contact details directly."""
    o = _org(db, u)
    db.add(Task(organization_id=o.id, title=f"Review contact change request - {o.name}", owner_id=o.assigned_admin_id,
                description=f"New contact: {body.contact_name} <{body.contact_email}>. Reason: {body.reason}",
                priority="medium", created_by=u.id))
    audit(db, u, "contact_change.requested", "organization", o.id, body.model_dump())
    db.commit()
    return {"message": "Request sent. ONCM will verify and update the details."}
