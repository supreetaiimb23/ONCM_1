"""Master Admin only: join requests, admin accounts, audit log, workflow overrides."""
from datetime import datetime, timezone
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from app import alerts
from app.core import get_db, hash_password, master_user
from app.models import AuditLog, Document, JoinRequest, Organization, User
from app.services import (ADMIN_DEFAULT_REPO, REPO_CATEGORIES, audit, doc_out, invite_link, lock_document, make_checklist,
                          notify, row, TRANSITIONS, user_out)

router = APIRouter(prefix="/api/master")


# ---------------------------------------------------------------- join requests
@router.get("/join-requests")
def join_requests(status: str = "", u: User = Depends(master_user), db: Session = Depends(get_db)):
    q = db.query(JoinRequest)
    if status:
        q = q.filter(JoinRequest.status == status)
    return [row(j) for j in q.order_by(JoinRequest.id.desc()).limit(300).all()]


class DecisionIn(BaseModel):
    decision: Literal["approve", "reject", "more_info"]
    reason: str = ""
    assigned_admin_id: Optional[int] = None


@router.post("/join-requests/{jid}/decision")
def decide(jid: int, body: DecisionIn, u: User = Depends(master_user), db: Session = Depends(get_db)):
    j = db.get(JoinRequest, jid)
    if not j:
        raise HTTPException(404, "Request not found")
    if j.status in ("approved", "rejected"):
        raise HTTPException(409, "This request was already decided")
    if body.decision != "approve" and not body.reason.strip():
        raise HTTPException(400, "Please give the applicant a reason or next step")
    j.reviewer_id, j.reason, j.decided_at = u.id, body.reason, datetime.now(timezone.utc)
    result = {"status": body.decision}
    if body.decision == "approve":
        if db.query(User).filter(func.lower(User.email) == j.contact_email.lower()).first():
            raise HTTPException(409, "A user with this email already exists")
        org = Organization(name=j.org_name, actor_type=j.actor_type, contact_name=j.contact_name,
                           contact_email=j.contact_email, intended_role=j.intended_role,
                           assigned_admin_id=body.assigned_admin_id, stage="joined", waiting_on="participant")
        db.add(org)
        db.flush()
        make_checklist(db, org)
        user = User(email=j.contact_email.lower(), name=j.contact_name, role="participant",
                    organization_id=org.id, is_active=True)
        db.add(user)
        db.flush()
        link = invite_link(user)
        j.status, j.organization_id = "approved", org.id
        notify(db, "join_approved", j.contact_email, "Welcome to ONCM - set up your access",
               f"Your request for {org.name} was approved. Use this secure link to set your password (valid 7 days):\n{link}",
               f"join-approved:{j.id}")
        result.update(organization_id=org.id, invite_link=link)  # shown to admin in case email is not configured
        admin = db.get(User, body.assigned_admin_id) if body.assigned_admin_id else None
        alerts.queue_alert(db, "joins", "join_approved", f"Organisation joined: {org.name}",
                           alerts.render(f"{org.name} has been approved and its workspace is open. (The invitation link is sent only to the applicant.)",
                                         [("Organisation", org.name), ("Actor type", org.actor_type),
                                          ("Contact", f"{org.contact_name} <{org.contact_email}>"),
                                          ("ONCM contact", admin.name if admin else "Unassigned"),
                                          ("Approved by", u.name), ("Onboarding checklist", "created")]),
                           f"join-approved:{j.id}")
    else:
        j.status = "rejected" if body.decision == "reject" else "more_info"
        notify(db, f"join_{body.decision}", j.contact_email, "Update on your ONCM join request", body.reason,
               f"join-{body.decision}:{j.id}:{datetime.now(timezone.utc):%H%M%S}")
        what = "was rejected" if body.decision == "reject" else "needs more information from the applicant"
        alerts.queue_alert(db, "joins", f"join_{body.decision}",
                           f"Join request {'rejected' if body.decision == 'reject' else 'needs more info'}: {j.org_name}",
                           alerts.render(f"The join request from {j.org_name} {what}.",
                                         [("Organisation", j.org_name), ("Actor type", j.actor_type),
                                          ("Contact", f"{j.contact_name} <{j.contact_email}>"),
                                          ("Reason / next step given", body.reason), ("Decided by", u.name)]),
                           f"join-{body.decision}:{j.id}:{datetime.now(timezone.utc):%H%M%S}")
    audit(db, u, f"join_request.{body.decision}", "join_request", j.id, {"reason": body.reason})
    db.commit()
    return result


# ---------------------------------------------------------------- admin accounts
@router.get("/admins")
def admins(u: User = Depends(master_user), db: Session = Depends(get_db)):
    return [user_out(a) for a in db.query(User).filter(User.role != "participant").order_by(User.id)]


class AdminIn(BaseModel):
    name: str = Field(min_length=2, max_length=200)
    email: EmailStr
    password: str = Field(min_length=10, max_length=128)
    role: Literal["secondary_admin", "master_admin"] = "secondary_admin"
    can_share: bool = False
    can_export: bool = False
    repo_access: Optional[list[str]] = None   # categories this admin may open; default excludes AGR
    reason: str = ""


@router.post("/admins", status_code=201)
def create_admin(body: AdminIn, u: User = Depends(master_user), db: Session = Depends(get_db)):
    if db.query(User).filter(func.lower(User.email) == body.email.lower()).first():
        raise HTTPException(409, "Email already registered")
    if body.role == "master_admin" and len(body.reason.strip()) < 3:
        raise HTTPException(400, "A reason is required to create another Master Admin")
    a = User(name=body.name, email=body.email.lower(), role=body.role, password_hash=hash_password(body.password),
             can_share=body.can_share, can_export=body.can_export,
             repo_access=[c for c in (body.repo_access if body.repo_access is not None else ADMIN_DEFAULT_REPO) if c in REPO_CATEGORIES])
    db.add(a)
    db.flush()
    audit(db, u, "admin.created", "user", a.id, {"role": a.role, "reason": body.reason})
    db.commit()
    return user_out(a)


class AdminPatch(BaseModel):
    is_active: Optional[bool] = None
    can_share: Optional[bool] = None
    can_export: Optional[bool] = None
    repo_access: Optional[list[str]] = None
    reason: str = Field(min_length=3)  # required for every privilege change


@router.patch("/admins/{uid}")
def patch_admin(uid: int, body: AdminPatch, u: User = Depends(master_user), db: Session = Depends(get_db)):
    a = db.get(User, uid)
    if not a or a.role == "participant":
        raise HTTPException(404, "Admin not found")
    if a.id == u.id and body.is_active is False:
        raise HTTPException(400, "You cannot deactivate yourself")
    changes = body.model_dump(exclude_unset=True)
    if changes.get("repo_access") is not None:
        changes["repo_access"] = [c for c in changes["repo_access"] if c in REPO_CATEGORIES]
    for k, v in changes.items():
        if k != "reason":
            setattr(a, k, v)
    audit(db, u, "admin.privileges_changed", "user", a.id, changes)
    db.commit()
    return user_out(a)


# ---------------------------------------------------------------- audit & overrides
@router.get("/audit-logs")
def audit_logs(action: str = "", limit: int = 200, u: User = Depends(master_user), db: Session = Depends(get_db)):
    q = db.query(AuditLog)
    if action:
        q = q.filter(AuditLog.action.ilike(f"%{action}%"))
    return [row(a) for a in q.order_by(AuditLog.id.desc()).limit(min(limit, 1000)).all()]


class OverrideIn(BaseModel):
    status: str
    reason: str = Field(min_length=3)


@router.post("/documents/{doc_id}/override")
def override(doc_id: int, body: OverrideIn, u: User = Depends(master_user), db: Session = Depends(get_db)):
    """Exceptional case: force a document status. Reason is mandatory and audited."""
    d = lock_document(db, doc_id)
    if not d:
        raise HTTPException(404, "Document not found")
    if body.status not in TRANSITIONS:
        raise HTTPException(400, "Unknown status")
    audit(db, u, "document.override", "document", d.id, {"from": d.status, "to": body.status, "reason": body.reason})
    d.status = body.status
    db.commit()
    return doc_out(db, d)
