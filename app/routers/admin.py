"""Admin endpoints shared by Master Admin and Secondary Admin (Secondary is scoped to assigned organizations)."""
from datetime import datetime, timedelta
from typing import Literal, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, EmailStr
from sqlalchemy import func
from sqlalchemy.orm import Session

from app import alerts, search
from app.core import admin_user, get_db, make_token, settings
from app.excel import build_workbook
from app.models import (AccessRequest, ChangeLog, ChecklistItem, Document, DocumentGrant, DocumentReview,
                        DocumentShare, DocumentVersion, Notification, Organization, Task, User, Workstream)
from app.services import (ACCESS_LEVELS, ACTOR_TYPES, ALLOWED_LEVELS, CATEGORIES, DEFAULT_LEVEL, REPO_CATEGORIES,
                          STAGES, STAGE_GROUP, active_shares, add_version, admin_repo_codes, assign_serial, audit,
                          can_see_doc, check_transition, close_review_tasks, doc_code, doc_out, doc_out_many,
                          ensure_review_task, invite_link, latest_maker_id, lock_document, log_change, new_document, notify,
                          notify_doc_owners, read_validated, row, shared_mailbox, storage, utcnow, validate_classification,
                          version_row, visible_docs_clause)

router = APIRouter(prefix="/api/admin")
MASTER = "master_admin"


# ---------------------------------------------------------------- scoping helpers
def org_query(db, user):
    q = db.query(Organization)
    return q.filter(Organization.assigned_admin_id == user.id) if user.role != MASTER else q


def get_org(db, user, org_id) -> Organization:
    o = org_query(db, user).filter(Organization.id == org_id).first()
    if not o:
        raise HTTPException(404, "Organization not found or not assigned to you")
    return o


def assigned_ids(db, user):
    return [o.id for o in org_query(db, user).all()]


def get_doc(db, user, doc_id, lock=False) -> Document:
    """lock=True for anything that changes the document: concurrent changes then take turns (see lock_document)."""
    d = lock_document(db, doc_id) if lock else db.get(Document, doc_id)
    if not d or not can_see_doc(user, d, assigned_ids(db, user) if user.role != MASTER else None):
        raise HTTPException(404, "Document not found or not within your permissions")
    return d


def label_of(v):
    return str(v if v is not None else "-").replace("_", " ").capitalize()


def csv_list(v):
    return [x.strip() for x in (v or "").split(",") if x.strip()]


def need_reason(reason):
    if not reason or len(reason.strip()) < 3:
        raise HTTPException(400, "A reason is required for this action")


# ---------------------------------------------------------------- overview & lookups
@router.get("/overview")
def overview(u: User = Depends(admin_user), db: Session = Depends(get_db)):
    from app.models import JoinRequest
    orgs = org_query(db, u).all()
    ids = [o.id for o in orgs]
    now_ = utcnow()
    tq = db.query(Task).filter(Task.status != "done")
    if u.role != MASTER:
        tq = tq.filter(Task.owner_id == u.id)
    overdue = 0
    for t in tq.filter(Task.due_date != None).all():  # noqa: E711
        dd = t.due_date if t.due_date.tzinfo else t.due_date.replace(tzinfo=now_.tzinfo)
        overdue += dd < now_
    vis = visible_docs_clause(u, ids if u.role != MASTER else None)
    dq = db.query(Document.status, func.count()).group_by(Document.status)
    by_status = dict((dq.filter(vis) if vis is not None else dq).all())
    can_decide = u.role == MASTER or u.can_share
    return {
        "organizations": len(orgs),
        "live": sum(o.stage == "live" for o in orgs),
        "pending_join_requests": db.query(JoinRequest).filter_by(status="pending").count() if u.role == MASTER else None,
        "awaiting_review": by_status.get("submitted", 0) + by_status.get("under_review", 0),
        "changes_requested": by_status.get("changes_requested", 0),
        "pending_access_requests": db.query(AccessRequest).filter_by(status="pending").count() if can_decide else None,
        "overdue_tasks": overdue,
        "failed_emails": db.query(Notification).filter_by(status="failed").count() if u.role == MASTER else None,
        "by_stage": {s: sum(o.stage == s for o in orgs) for s in STAGES},
        "repository": {g: sum(n for s, n in by_status.items() if STAGE_GROUP.get(s) == g)
                       for g in ("draft", "internal_review", "approved", "published", "archive")},
    }


@router.get("/meta")
def meta(u: User = Depends(admin_user)):
    return {"repo_categories": REPO_CATEGORIES, "access_levels": ACCESS_LEVELS, "allowed_levels": ALLOWED_LEVELS,
            "default_levels": DEFAULT_LEVEL, "actor_types": ACTOR_TYPES, "my_repo_codes": admin_repo_codes(u)}


@router.get("/admins")
def list_admins(u: User = Depends(admin_user), db: Session = Depends(get_db)):
    return [{"id": a.id, "name": a.name, "email": a.email, "role": a.role}
            for a in db.query(User).filter(User.role != "participant", User.is_active == True).all()]  # noqa: E712


# ---------------------------------------------------------------- organizations
@router.get("/organizations")
def organizations(q: str = "", stage: str = "", actor_type: str = "", u: User = Depends(admin_user), db: Session = Depends(get_db)):
    qq = org_query(db, u)
    if q:
        qq = qq.filter(Organization.name.ilike(f"%{q}%"))
    if stage:
        qq = qq.filter(Organization.stage == stage)
    if actor_type:
        qq = qq.filter(Organization.actor_type == actor_type)
    out = []
    for o in qq.order_by(Organization.name).all():
        a = db.get(User, o.assigned_admin_id) if o.assigned_admin_id else None
        cl = db.query(ChecklistItem).filter_by(organization_id=o.id)
        out.append({**row(o), "assigned_admin_name": a.name if a else None,
                    "checklist_done": cl.filter_by(done=True).count(), "checklist_total": cl.count(),
                    "pending_docs": db.query(Document).filter(Document.organization_id == o.id,
                                                              Document.status.in_(["submitted", "under_review"])).count()})
    return out


@router.get("/organizations/{org_id}")
def organization(org_id: int, u: User = Depends(admin_user), db: Session = Depends(get_db)):
    o = get_org(db, u, org_id)
    return {**row(o),
            "checklist": [row(c) for c in db.query(ChecklistItem).filter_by(organization_id=o.id).order_by(ChecklistItem.id)],
            "documents": doc_out_many(db, db.query(Document).filter_by(organization_id=o.id).order_by(Document.id.desc())),
            "tasks": [row(t) for t in db.query(Task).filter_by(organization_id=o.id).order_by(Task.id.desc())]}


class OrgPatch(BaseModel):
    stage: Optional[str] = None
    waiting_on: Optional[Literal["participant", "oncm"]] = None
    assigned_admin_id: Optional[int] = None
    status: Optional[Literal["active", "suspended"]] = None
    reason: Optional[str] = None
    override: bool = False


@router.patch("/organizations/{org_id}")
def patch_org(org_id: int, body: OrgPatch, u: User = Depends(admin_user), db: Session = Depends(get_db)):
    o = get_org(db, u, org_id)
    changes = {}
    old = {"stage": o.stage, "status": o.status, "waiting_on": o.waiting_on, "assigned_admin_id": o.assigned_admin_id}
    if body.assigned_admin_id is not None or body.status is not None:
        if u.role != MASTER:
            raise HTTPException(403, "Only the Master Admin can reassign contacts or change account status")
    if body.assigned_admin_id is not None:
        if not db.get(User, body.assigned_admin_id):
            raise HTTPException(400, "Unknown admin")
        o.assigned_admin_id = body.assigned_admin_id
        changes["assigned_admin_id"] = body.assigned_admin_id
    if body.status is not None:
        need_reason(body.reason)
        o.status = body.status
        changes["status"] = body.status
        for m in db.query(User).filter_by(organization_id=o.id).all():
            m.is_active = body.status == "active" and m.password_hash is not None
    if body.waiting_on:
        o.waiting_on = body.waiting_on
        changes["waiting_on"] = body.waiting_on
    if body.stage and body.stage != o.stage:
        if body.stage not in STAGES:
            raise HTTPException(400, "Unknown stage")
        if body.stage == "live":
            open_items = db.query(ChecklistItem).filter_by(organization_id=o.id, done=False).count()
            if open_items:
                if not (body.override and u.role == MASTER):
                    raise HTTPException(409, f"{open_items} checklist item(s) are still outstanding. "
                                             "Only the Master Admin can override (with a reason).")
                need_reason(body.reason)
        o.stage = body.stage
        changes["stage"] = body.stage
        if body.stage == "live":
            notify(db, "org_live", o.contact_email, "You are now live on ONCM",
                   f"{o.name} has completed onboarding and is now Live.", f"live:{o.id}")
            admin = db.get(User, o.assigned_admin_id) if o.assigned_admin_id else None
            notify(db, "org_live_internal", admin.email if admin else shared_mailbox(), f"{o.name} is Live",
                   f"{o.name} moved to Live.", f"live-int:{o.id}")
    changes["reason"] = body.reason
    moved = {k: (old[k], getattr(o, k)) for k in old if old[k] != getattr(o, k)}
    if moved:   # tell the alert recipients what changed for this organisation
        admin_ids = [i for i in moved.get("assigned_admin_id", ()) if i]
        names = {x.id: x.name for x in db.query(User).filter(User.id.in_(admin_ids))} if admin_ids else {}
        shown = lambda k, v: (names.get(v, "Unassigned") if k == "assigned_admin_id" else label_of(v))
        headline = (f"{o.name} moved to {label_of(o.stage)}" if "stage" in moved else
                    f"{o.name} access {'suspended' if o.status == 'suspended' else 'restored'}" if "status" in moved else
                    f"{o.name} was updated")
        alerts.queue_alert(db, "organisations", "org_status_changed" if "stage" not in moved and "status" in moved else "org_stage_changed",
                           headline,
                           alerts.render(headline + ".", [("Organisation", o.name), ("Actor type", o.actor_type)]
                                         + [(label_of(k), f"{shown(k, a)}  ->  {shown(k, b)}") for k, (a, b) in moved.items()]
                                         + [("Changed by", u.name), ("Reason", body.reason)]),
                           f"org-upd:{o.id}:{utcnow():%Y%m%d%H%M%S%f}")
    audit(db, u, "organization.updated", "organization", o.id, changes)
    db.commit()
    return row(o)


@router.post("/checklist/{item_id}/toggle")
def toggle_item(item_id: int, done: bool, u: User = Depends(admin_user), db: Session = Depends(get_db)):
    c = db.get(ChecklistItem, item_id)
    if not c:
        raise HTTPException(404, "Item not found")
    get_org(db, u, c.organization_id)
    c.done, c.completed_by, c.completed_at = done, (u.id if done else None), (utcnow() if done else None)
    audit(db, u, "checklist.toggled", "checklist", c.id, {"done": done, "title": c.title})
    db.commit()
    return row(c)


@router.post("/organizations/{org_id}/missing-docs-reminder")
def remind_missing(org_id: int, u: User = Depends(admin_user), db: Session = Depends(get_db)):
    o = get_org(db, u, org_id)
    missing = [c.title for c in db.query(ChecklistItem).filter_by(organization_id=o.id, done=False)]
    if not missing:
        raise HTTPException(409, "Nothing outstanding")
    notify(db, "missing_documents", o.contact_email, "Outstanding onboarding items",
           "The following items are still outstanding:\n- " + "\n- ".join(missing) + "\n\nPlease log in to upload them.",
           f"missing:{o.id}:{utcnow():%Y%m%d%H}")
    audit(db, u, "reminder.missing_docs", "organization", o.id)
    db.commit()
    return {"sent_for": len(missing)}



class MemberIn(BaseModel):
    name: str
    email: EmailStr


@router.get("/participants")
def participants(u: User = Depends(admin_user), db: Session = Depends(get_db)):
    """Individuals that can receive named (Restricted) access. Secondary admins see their assigned organizations only."""
    ids = None if u.role == MASTER else assigned_ids(db, u)
    q = db.query(User).filter(User.role == "participant", User.is_active == True)  # noqa: E712
    if ids is not None:
        q = q.filter(User.organization_id.in_(ids or [0]))
    onames = {o.id: o.name for o in db.query(Organization)}
    return [{"id": x.id, "name": x.name, "email": x.email, "organization": onames.get(x.organization_id)} for x in q.order_by(User.name)]


@router.post("/organizations/{org_id}/members", status_code=201)
def add_member(org_id: int, body: MemberIn, u: User = Depends(admin_user), db: Session = Depends(get_db)):
    """Add another authorised individual to an organization (they can then receive individual grants)."""
    from sqlalchemy import func
    o = get_org(db, u, org_id)
    if db.query(User).filter(func.lower(User.email) == body.email.lower()).first():
        raise HTTPException(409, "A user with this email already exists")
    m = User(email=body.email.lower(), name=body.name, role="participant", organization_id=o.id, is_active=True)
    db.add(m)
    db.flush()
    link = invite_link(m)
    notify(db, "member_invited", m.email, f"You have been added to {o.name} on ONCM Workbench",
           f"Set your password with this secure link (valid 7 days):\n{link}", f"member-invite:{m.id}")
    alerts.queue_alert(db, "organisations", "member_added", f"{o.name}: new team member added",
                       alerts.render(f"{u.name} added {m.name} to {o.name}. (The invitation link is sent only to the new member.)",
                                     [("Organisation", o.name), ("New member", f"{m.name} <{m.email}>"), ("Added by", u.name)]),
                       f"member-added:{m.id}")
    audit(db, u, "organization.member_added", "organization", o.id, {"email": m.email})
    db.commit()
    return {"id": m.id, "invite_link": link}


@router.get("/organizations/{org_id}/members")
def members(org_id: int, u: User = Depends(admin_user), db: Session = Depends(get_db)):
    o = get_org(db, u, org_id)
    return [{"id": m.id, "name": m.name, "email": m.email, "active": m.is_active, "pending_invite": m.password_hash is None}
            for m in db.query(User).filter_by(organization_id=o.id)]


# ---------------------------------------------------------------- repository documents
@router.get("/documents")
def documents(status: str = "", stage: str = "", repo_category: str = "", workstream_id: Optional[int] = None,
              access_level: str = "", organization_id: Optional[int] = None, q: str = "", mine: bool = False,
              u: User = Depends(admin_user), db: Session = Depends(get_db)):
    qq = db.query(Document)
    for col, val in ((Document.status, status), (Document.repo_category, repo_category), (Document.access_level, access_level),
                     (Document.workstream_id, workstream_id), (Document.organization_id, organization_id)):
        if val:
            qq = qq.filter(col == val)
    rank = None
    if q.strip():  # code, title and (SEARCH_BACKEND=engine) file text, best match first; permissions still apply below
        rank = {doc_id: n for n, doc_id in enumerate(search.matching_ids(db, q))}
        qq = qq.filter(Document.id.in_(list(rank) or [0]))
    if mine:
        qq = qq.filter((Document.assigned_reviewer_id == u.id) | (Document.approver_id == u.id))
    aids = assigned_ids(db, u) if u.role != MASTER else None
    out = [d for d in qq.order_by(Document.updated_at.desc()).limit(1000).all()
           if (not stage or STAGE_GROUP.get(d.status) == stage) and can_see_doc(u, d, aids)]
    if rank is not None:
        out.sort(key=lambda d: rank[d.id])
    return doc_out_many(db, out[:500])


@router.post("/documents", status_code=201)
def admin_upload(title: str = Form(...), repo_category: str = Form(...), doc_type: str = Form(""),
                 organization_id: Optional[int] = Form(None), workstream_id: Optional[int] = Form(None),
                 access_level: str = Form(""), target_actor_types: str = Form(""),
                 file: UploadFile = File(...), u: User = Depends(admin_user), db: Session = Depends(get_db)):
    """New repository document. It starts as an internal DRAFT and gets a code like ONCM-POL-2026-V1-0003."""
    if repo_category not in REPO_CATEGORIES:
        raise HTTPException(400, "Unknown repository category")
    if repo_category not in admin_repo_codes(u):
        raise HTTPException(403, "You have not been granted access to this category")
    if organization_id:
        get_org(db, u, organization_id)
    if workstream_id and not db.get(Workstream, workstream_id):
        raise HTTPException(400, "Unknown workstream")
    level = access_level or DEFAULT_LEVEL.get(repo_category, "internal")
    types = csv_list(target_actor_types)
    validate_classification(repo_category, level, types)
    folder = (CATEGORIES[1] if repo_category == "AGR" else CATEGORIES[2]) if organization_id else REPO_CATEGORIES[repo_category]
    upload = read_validated(file)
    d = new_document(db, repo_category, organization_id=organization_id or None, category=folder, title=title.strip()[:250],
                     doc_type=doc_type[:100], origin="admin", status="draft", created_by=u.id, access_level=level,
                     target_actor_types=types or None, workstream_id=workstream_id or None)
    add_version(db, d, upload, u)
    assign_serial(db, d)
    audit(db, u, "document.created", "document", d.id, {"code": doc_code(d), "level": level})
    db.commit()
    return doc_out(db, d)


@router.get("/documents/{doc_id}")
def document_detail(doc_id: int, u: User = Depends(admin_user), db: Session = Depends(get_db)):
    d = get_doc(db, u, doc_id)
    revs = db.query(DocumentReview).filter_by(document_id=d.id).order_by(DocumentReview.id.desc()).all()
    names = {x.id: x.name for x in db.query(User).filter(User.role != "participant")}
    shares = []
    for sh in db.query(DocumentShare).filter_by(document_id=d.id).order_by(DocumentShare.id.desc()):
        grants = []
        for g in db.query(DocumentGrant).filter_by(share_id=sh.id, source="restricted"):
            o = db.get(Organization, g.organization_id) if g.organization_id else None
            gu = db.get(User, g.user_id) if g.user_id else None
            grants.append({"id": g.id, "organization": o.name if o else None, "user": gu.email if gu else None})
        shares.append({**row(sh), "grants": grants})
    return {**doc_out(db, d),
            "versions": [version_row(v) for v in db.query(DocumentVersion).filter_by(document_id=d.id).order_by(DocumentVersion.version)],
            "reviews": [{**row(r), "reviewer_name": names.get(r.reviewer_id)} for r in revs],
            "shares": shares,
            "change_log": [row(c) for c in db.query(ChangeLog).filter((ChangeLog.document_id == d.id) | (ChangeLog.supersedes_document_id == d.id)).order_by(ChangeLog.id.desc())],
            "maker_id": latest_maker_id(db, d)}


class DocPatch(BaseModel):
    title: Optional[str] = None
    access_level: Optional[str] = None
    target_actor_types: Optional[list[str]] = None
    workstream_id: Optional[int] = None


@router.patch("/documents/{doc_id}")
def patch_document(doc_id: int, body: DocPatch, u: User = Depends(admin_user), db: Session = Depends(get_db)):
    d = get_doc(db, u, doc_id, lock=True)
    if d.status not in ("draft", "submitted", "under_review", "changes_requested"):
        raise HTTPException(409, "Classification is locked once approved. Ask the Master Admin to override.")
    if body.title:
        d.title = body.title.strip()[:250]
    if body.workstream_id is not None:
        d.workstream_id = body.workstream_id or None
    if body.access_level is not None:
        types = body.target_actor_types if body.target_actor_types is not None else (d.target_actor_types or [])
        validate_classification(d.repo_category, body.access_level, types)
        d.access_level, d.target_actor_types = body.access_level, types or None
        d.classification_confirmed_by = d.classification_confirmed_at = None
    audit(db, u, "document.updated", "document", d.id, body.model_dump(exclude_unset=True))
    db.commit()
    return doc_out(db, d)


@router.post("/documents/{doc_id}/versions", status_code=201)
def admin_new_version(doc_id: int, file: UploadFile = File(...), u: User = Depends(admin_user), db: Session = Depends(get_db)):
    get_doc(db, u, doc_id)  # 404 before reading the file
    upload = read_validated(file)
    d = get_doc(db, u, doc_id, lock=True)
    if d.origin != "admin" or d.status not in ("draft", "changes_requested"):
        raise HTTPException(409, "Only internal drafts can receive a new version here")
    ver = add_version(db, d, upload, u)
    if d.status == "changes_requested":
        d.status = "draft"
    d.classification_confirmed_by = d.classification_confirmed_at = None
    audit(db, u, "document.version_added", "document", d.id, {"version": ver.version, "code": doc_code(d)})
    db.commit()
    return doc_out(db, d)


class AssignIn(BaseModel):
    reviewer_id: Optional[int] = None   # checker
    approver_id: Optional[int] = None   # final approver
    due_date: Optional[datetime] = None


@router.post("/documents/{doc_id}/assign")
def assign(doc_id: int, body: AssignIn, u: User = Depends(admin_user), db: Session = Depends(get_db)):
    d = get_doc(db, u, doc_id, lock=True)
    if u.role != MASTER and (body.approver_id or (body.reviewer_id and body.reviewer_id != u.id)):
        raise HTTPException(403, "Only the Master Admin can assign other reviewers or approvers")
    maker = latest_maker_id(db, d)
    for label_, uid in (("reviewer", body.reviewer_id), ("approver", body.approver_id)):
        if uid:
            r = db.get(User, uid)
            if not r or r.role == "participant" or not r.is_active:
                raise HTTPException(400, f"Unknown {label_}")
            if uid == maker:
                raise HTTPException(409, f"Maker-checker: the {label_} cannot be the person who prepared this version")
    if body.reviewer_id and body.reviewer_id == body.approver_id and settings.two_person_rule:
        raise HTTPException(409, "Maker-checker: reviewer and approver must be different people")
    if body.reviewer_id:
        d.assigned_reviewer_id = body.reviewer_id
    if body.approver_id:
        d.approver_id = body.approver_id
    d.due_date = body.due_date
    if d.status == "under_review":
        ensure_review_task(db, d, d.assigned_reviewer_id, u)
    audit(db, u, "document.assigned", "document", d.id, body.model_dump(mode="json"))
    db.commit()
    return doc_out(db, d)


class ReviewIn(BaseModel):
    decision: Literal["start_review", "recommend", "approve", "changes", "reject"]
    comment: str = ""    # internal only
    feedback: str = ""   # participant-visible
    # approval step: confirm access classification, intended user group and permissions
    confirm_classification: bool = False
    access_level: Optional[str] = None
    target_actor_types: Optional[list[str]] = None


@router.post("/documents/{doc_id}/review")
def review(doc_id: int, body: ReviewIn, u: User = Depends(admin_user), db: Session = Depends(get_db)):
    d = get_doc(db, u, doc_id, lock=True)
    dec = body.decision
    maker = latest_maker_id(db, d)
    code = doc_code(d)
    if dec != "start_review" and u.id == maker:
        raise HTTPException(403, "Maker-checker: you prepared this version, so someone else must review or approve it")
    if dec == "approve":
        designated = d.approver_id == u.id
        if u.role != MASTER and not designated and (settings.two_person_rule or d.approver_id):
            raise HTTPException(403, "Only the Master Admin or the designated approver can give final approval")
        if d.approver_id and not designated and u.role != MASTER:
            raise HTTPException(403, "A different approver is designated for this document")
        if not body.confirm_classification:
            raise HTTPException(400, "Confirm the access classification and intended user group before approving")
        level = body.access_level or d.access_level
        types = body.target_actor_types if body.target_actor_types is not None else (d.target_actor_types or [])
        validate_classification(d.repo_category, level, types)
    if dec in ("changes", "reject") and not (body.feedback.strip() or body.comment.strip()):
        raise HTTPException(400, "Please explain the decision")
    target = {"start_review": "under_review", "approve": "internally_approved",
              "changes": "changes_requested", "reject": "rejected"}.get(dec)
    if dec == "start_review":
        if not d.assigned_reviewer_id:
            if u.id == maker:
                raise HTTPException(400, "Assign a reviewer first (the person who prepared a document cannot review it)")
            d.assigned_reviewer_id = u.id
    if target:
        check_transition(d.status, target)
        d.status = target
    elif d.status != "under_review":
        raise HTTPException(409, "Only documents under review can be recommended")
    if dec == "approve":
        d.access_level, d.target_actor_types = level, types or None
        d.classification_confirmed_by, d.classification_confirmed_at = u.id, utcnow()
    db.add(DocumentReview(document_id=d.id, version=d.current_version, reviewer_id=u.id, decision=dec,
                          internal_comment=body.comment, feedback=body.feedback))
    org = db.get(Organization, d.organization_id) if d.organization_id else None
    if dec == "start_review":
        ensure_review_task(db, d, d.assigned_reviewer_id, u)
        notify_doc_owners(db, d, "document_in_review", f"In review: {d.title}",
                          f"{code} moved to internal review. Open the approval inbox to continue.", f"v{d.current_version}")
    else:
        close_review_tasks(db, d) if dec in ("approve", "changes", "reject") else None
    if org and d.origin == "participant":
        if dec in ("changes", "reject"):
            org.waiting_on = "participant"
            notify(db, "changes_requested" if dec == "changes" else "document_rejected", org.contact_email,
                   "Changes requested on your document" if dec == "changes" else "Decision on your document",
                   f"ONCM has reviewed “{d.title}”. Log in to the Workbench to see the feedback"
                   + (" and upload a corrected version." if dec == "changes" else "."),
                   f"review:{d.id}:{d.current_version}:{dec}:{utcnow():%Y%m%d%H%M%S}")
        elif dec == "approve":
            org.waiting_on = "oncm"
    if dec in ("approve", "recommend", "changes", "reject") and not (org and d.origin == "participant" and dec in ("changes", "reject")):
        verb = {"approve": "approved", "recommend": "recommended for approval", "changes": "returned with changes", "reject": "rejected"}[dec]
        nxt = {"approve": " Next step: Repository configuration and publication.", "recommend": " The approver can now give final approval."}.get(dec, "")
        notify_doc_owners(db, d, f"document_{dec}", f"{d.title}: {verb}", f"{u.name} {verb} {code}.{nxt}",
                          f"{dec}:{utcnow():%Y%m%d%H%M%S}", team=dec in ("approve", "recommend"))
    if dec == "approve":
        log_change(db, u, f"Approved {code}", f"Access level confirmed: {ACCESS_LEVELS[d.access_level]}", d)
    audit(db, u, f"document.{dec}", "document", d.id, {"version": d.current_version, "code": code})
    db.commit()
    return doc_out(db, d)


@router.get("/documents/{doc_id}/download")
def admin_download(doc_id: int, version: Optional[int] = None, u: User = Depends(admin_user), db: Session = Depends(get_db)):
    d = get_doc(db, u, doc_id)
    v = db.query(DocumentVersion).filter_by(document_id=d.id, version=version or d.current_version).first()
    if not v:
        raise HTTPException(404, "Version not found")
    audit(db, u, "document.download", "document", d.id, {"version": v.version})  # every access recorded
    db.commit()
    return {"url": storage().download_url(v.storage_key, v.filename), "filename": v.filename}


class ReasonIn(BaseModel):
    reason: str


def _unpublish(db, d):
    for sh in db.query(DocumentShare).filter_by(document_id=d.id, status="active"):
        sh.status = "revoked"


@router.post("/documents/{doc_id}/archive")
def archive(doc_id: int, body: ReasonIn, u: User = Depends(admin_user), db: Session = Depends(get_db)):
    d = get_doc(db, u, doc_id, lock=True)
    need_reason(body.reason)
    check_transition(d.status, "archived")
    _unpublish(db, d)
    close_review_tasks(db, d)
    d.status = "archived"
    log_change(db, u, f"Archived {doc_code(d)}", body.reason, d)
    audit(db, u, "document.archived", "document", d.id, {"reason": body.reason})
    db.commit()
    return doc_out(db, d)


@router.post("/documents/{doc_id}/restore")
def restore(doc_id: int, body: ReasonIn, u: User = Depends(admin_user), db: Session = Depends(get_db)):
    if u.role != MASTER:
        raise HTTPException(403, "Only the Master Admin can restore archived documents")
    d = get_doc(db, u, doc_id, lock=True)
    need_reason(body.reason)
    check_transition(d.status, "draft")
    d.status = "draft"
    d.classification_confirmed_by = d.classification_confirmed_at = None
    log_change(db, u, f"Restored {doc_code(d)} from archive", body.reason, d)
    audit(db, u, "document.restored", "document", d.id, {"reason": body.reason})
    db.commit()
    return doc_out(db, d)


# ---------------------------------------------------------------- repository configuration + publication
class PublishIn(BaseModel):
    actor_types: Optional[list[str]] = None      # role_specific: subset of the approved intended group
    organization_ids: list[int] = []             # restricted: authorised groups
    user_ids: list[int] = []                     # restricted: authorised individuals
    expires_at: Optional[datetime] = None


def _grant_targets(db, ids_org, ids_user):
    orgs = db.query(Organization).filter(Organization.id.in_(ids_org or [0]), Organization.status == "active").all()
    users = db.query(User).filter(User.id.in_(ids_user or [0]), User.role == "participant", User.is_active == True).all()  # noqa: E712
    if len(orgs) != len(set(ids_org)) or len(users) != len(set(ids_user)):
        raise HTTPException(400, "One or more selected organizations or users are unknown or inactive")
    return orgs, users


@router.post("/documents/{doc_id}/publish", status_code=201)
def publish(doc_id: int, body: PublishIn, u: User = Depends(admin_user), db: Session = Depends(get_db)):
    """Repository configuration: applies the APPROVED access classification. Publication can never be broader than approved."""
    if u.role != MASTER and not u.can_share:
        raise HTTPException(403, "You are not authorised to configure repository access")
    d = get_doc(db, u, doc_id, lock=True)
    if d.status not in ("internally_approved", "shared_externally") or not d.classification_confirmed_at:
        raise HTTPException(409, "Only approved documents with a confirmed access classification can be published")
    level = d.access_level
    if level == "internal":
        raise HTTPException(409, "This document is classified Internal and cannot be shared externally")
    types = body.actor_types if body.actor_types is not None else (d.target_actor_types or [])
    if level == "role_specific":
        if not types or not set(types) <= set(d.target_actor_types or []):
            raise HTTPException(400, "Audience must be within the approved intended user group")
    orgs, users = _grant_targets(db, body.organization_ids, body.user_ids)
    if level == "restricted" and not (orgs or users):
        raise HTTPException(400, "Choose the organizations or individuals who are authorised")
    if level not in ("restricted",) and (orgs or users):
        raise HTTPException(400, "Named recipients apply to Restricted documents only")
    if d.status == "internally_approved":
        check_transition(d.status, "shared_externally")
        d.status = "shared_externally"
    _unpublish(db, d)
    sh = DocumentShare(document_id=d.id, version=d.current_version, audience=level,
                       actor_types=types if level == "role_specific" else None, granted_by=u.id, expires_at=body.expires_at)
    db.add(sh)
    db.flush()
    recipients = set()
    for o in orgs:
        db.add(DocumentGrant(share_id=sh.id, document_id=d.id, organization_id=o.id, source="restricted", expires_at=body.expires_at))
        recipients.add(o.contact_email)
    for x in users:
        db.add(DocumentGrant(share_id=sh.id, document_id=d.id, user_id=x.id, source="restricted", expires_at=body.expires_at))
        recipients.add(x.email)
    if level == "pilot_shared":
        recipients |= {o.contact_email for o in db.query(Organization).filter_by(status="active")}
    elif level == "role_specific":
        recipients |= {o.contact_email for o in db.query(Organization).filter(Organization.status == "active", Organization.actor_type.in_(types))}
    for em in filter(None, recipients):
        notify(db, "document_shared", em, "A new document is available",
               f"“{d.title}” ({doc_code(d)}) has been shared with you. Log in to the Workbench to download it securely.",
               f"share:{sh.id}:{em}")
    alerts.queue_alert(db, "documents", "document_published", f"Published: {d.title}",
                       alerts.render(f"{u.name} published a document.",
                                     [("Document", f"{doc_code(d)}  {d.title}"), ("Access level", ACCESS_LEVELS[level]),
                                      ("Emails sent to", f"{len([r for r in recipients if r])} recipient(s)")]),
                       f"published:{sh.id}")
    log_change(db, u, f"Published {doc_code(d)}", f"Access level: {ACCESS_LEVELS[level]}", d)
    audit(db, u, "document.published", "document", d.id, {"level": level, "version": sh.version, "orgs": [o.id for o in orgs], "users": [x.id for x in users]})
    db.commit()
    return row(sh)


@router.post("/documents/{doc_id}/grants", status_code=201)
def add_grants(doc_id: int, body: PublishIn, u: User = Depends(admin_user), db: Session = Depends(get_db)):
    """Authorise further groups/individuals on an already published Restricted document."""
    if u.role != MASTER and not u.can_share:
        raise HTTPException(403, "You are not authorised to configure repository access")
    d = get_doc(db, u, doc_id, lock=True)
    sh = next(iter(active_shares(db, d.id)), None)
    if not sh or sh.audience != "restricted":
        raise HTTPException(409, "Only published Restricted documents take named grants")
    orgs, users = _grant_targets(db, body.organization_ids, body.user_ids)
    for o in orgs:
        db.add(DocumentGrant(share_id=sh.id, document_id=d.id, organization_id=o.id, source="restricted", expires_at=body.expires_at))
        notify(db, "document_shared", o.contact_email, "A new document is available", f"“{d.title}” has been shared with you.", f"grant:{sh.id}:o{o.id}")
    for x in users:
        db.add(DocumentGrant(share_id=sh.id, document_id=d.id, user_id=x.id, source="restricted", expires_at=body.expires_at))
        notify(db, "document_shared", x.email, "A new document is available", f"“{d.title}” has been shared with you.", f"grant:{sh.id}:u{x.id}")
    audit(db, u, "document.grants_added", "document", d.id, {"orgs": [o.id for o in orgs], "users": [x.id for x in users]})
    db.commit()
    return {"ok": True}


@router.delete("/grants/{grant_id}")
def remove_grant(grant_id: int, u: User = Depends(admin_user), db: Session = Depends(get_db)):
    if u.role != MASTER and not u.can_share:
        raise HTTPException(403, "You are not authorised to configure repository access")
    g = db.get(DocumentGrant, grant_id)
    if not g:
        raise HTTPException(404, "Grant not found")
    get_doc(db, u, g.document_id)
    db.delete(g)
    audit(db, u, "document.grant_removed", "document", g.document_id, {"grant": grant_id})
    db.commit()
    return {"ok": True}


@router.delete("/shares/{share_id}")
def revoke(share_id: int, u: User = Depends(admin_user), db: Session = Depends(get_db)):
    if u.role != MASTER and not u.can_share:
        raise HTTPException(403, "You are not authorised to change sharing")
    sh = db.get(DocumentShare, share_id)
    if not sh:
        raise HTTPException(404, "Share not found")
    d = get_doc(db, u, sh.document_id, lock=True)
    sh.status = "revoked"
    db.flush()
    if not active_shares(db, d.id) and d.status == "shared_externally":
        d.status = "internally_approved"
    audit(db, u, "share.revoked", "document", d.id, {"share": sh.id})
    db.commit()
    return {"ok": True}


# ---------------------------------------------------------------- workstreams (shared team view)
class WsIn(BaseModel):
    name: str
    description: Optional[str] = ""
    owner_id: Optional[int] = None
    status: Literal["active", "on_hold", "done"] = "active"


@router.get("/workstreams")
def workstreams(u: User = Depends(admin_user), db: Session = Depends(get_db)):
    now_ = utcnow()
    out = []
    names = {x.id: x.name for x in db.query(User).filter(User.role != "participant")}
    docs = db.query(Document.workstream_id, Document.status).filter(Document.workstream_id != None).all()  # noqa: E711
    for w in db.query(Workstream).order_by(Workstream.name):
        open_t = db.query(Task).filter(Task.workstream_id == w.id, Task.status != "done").all()
        over = sum(1 for t in open_t if t.due_date and (t.due_date if t.due_date.tzinfo else t.due_date.replace(tzinfo=now_.tzinfo)) < now_)
        out.append({**row(w), "owner_name": names.get(w.owner_id),
                    "docs": {g: sum(1 for ws, st in docs if ws == w.id and STAGE_GROUP.get(st) == g) for g in ("draft", "internal_review", "approved", "published", "archive")},
                    "open_tasks": len(open_t), "overdue_tasks": over})
    return out


@router.post("/workstreams", status_code=201)
def create_ws(body: WsIn, u: User = Depends(admin_user), db: Session = Depends(get_db)):
    if db.query(Workstream).filter_by(name=body.name.strip()).first():
        raise HTTPException(409, "A workstream with this name exists")
    w = Workstream(name=body.name.strip(), description=body.description, owner_id=body.owner_id or u.id, status=body.status)
    db.add(w)
    audit(db, u, "workstream.created", "workstream", body.name)
    db.commit()
    return row(w)


@router.patch("/workstreams/{wid}")
def patch_ws(wid: int, body: WsIn, u: User = Depends(admin_user), db: Session = Depends(get_db)):
    w = db.get(Workstream, wid)
    if not w:
        raise HTTPException(404, "Workstream not found")
    if u.role != MASTER and w.owner_id != u.id:
        raise HTTPException(403, "Only the Master Admin or the workstream owner can edit it")
    w.name, w.description, w.status = body.name.strip(), body.description, body.status
    if body.owner_id:
        w.owner_id = body.owner_id
    audit(db, u, "workstream.updated", "workstream", w.id)
    db.commit()
    return row(w)


# ---------------------------------------------------------------- change log (decisions + latest-version control)
class ChangeIn(BaseModel):
    title: str
    decision: str = ""
    document_id: Optional[int] = None
    supersedes_document_id: Optional[int] = None
    workstream_id: Optional[int] = None


@router.get("/changelog")
def changelog(document_id: Optional[int] = None, workstream_id: Optional[int] = None, u: User = Depends(admin_user), db: Session = Depends(get_db)):
    q = db.query(ChangeLog)
    if document_id:
        q = q.filter((ChangeLog.document_id == document_id) | (ChangeLog.supersedes_document_id == document_id))
    if workstream_id:
        q = q.filter(ChangeLog.workstream_id == workstream_id)
    names = {x.id: x.name for x in db.query(User).filter(User.role != "participant")}
    aids = assigned_ids(db, u) if u.role != MASTER else None
    out = []
    for c in q.order_by(ChangeLog.id.desc()).limit(300):
        ds = [db.get(Document, i) for i in (c.document_id, c.supersedes_document_id) if i]
        if any(x and not can_see_doc(u, x, aids) for x in ds):
            continue   # do not reveal entries about documents outside the admin's permissions
        out.append({**row(c), "recorded_by_name": names.get(c.recorded_by),
                    "document_code": doc_code(ds[0]) if c.document_id and ds else None,
                    "supersedes_code": doc_code(db.get(Document, c.supersedes_document_id)) if c.supersedes_document_id else None})
    return out


@router.post("/changelog", status_code=201)
def add_change(body: ChangeIn, u: User = Depends(admin_user), db: Session = Depends(get_db)):
    """Record a decision. If it says one document replaces another, the old one is superseded so everyone uses the latest."""
    if len(body.title.strip()) < 3:
        raise HTTPException(400, "Give the decision a title")
    doc = get_doc(db, u, body.document_id) if body.document_id else None
    old = None
    if body.supersedes_document_id:
        if not doc:
            raise HTTPException(400, "Choose the newer document that replaces the old one")
        old = get_doc(db, u, body.supersedes_document_id, lock=True)
        if old.id == doc.id:
            raise HTTPException(400, "A document cannot supersede itself")
        check_transition(old.status, "superseded")
        _unpublish(db, old)
        close_review_tasks(db, old)
        old.status, old.superseded_by_id = "superseded", doc.id
    log_change(db, u, body.title, body.decision, doc, old, body.workstream_id)
    if old:
        notify_doc_owners(db, old, "document_superseded", f"Superseded: {old.title}",
                          f"{doc_code(old)} was superseded by {doc_code(doc)}. Please use the newer document.", f"s{doc.id}", team=True)
    audit(db, u, "changelog.added", "change_log", "new", {"title": body.title, "supersedes": body.supersedes_document_id})
    db.commit()
    return {"ok": True}


# ---------------------------------------------------------------- request-only access (portal, public form, mail-in)
@router.get("/access-requests")
def access_requests(status: str = "", u: User = Depends(admin_user), db: Session = Depends(get_db)):
    q = db.query(AccessRequest)
    if status:
        q = q.filter(AccessRequest.status == status)
    out = []
    for r in q.order_by(AccessRequest.id.desc()).limit(300):
        d = db.get(Document, r.document_id)
        out.append({**row(r), "document_code": doc_code(d) if d else None, "document_title": d.title if d else None})
    return out


class MailRequestIn(BaseModel):
    document_code: str            # e.g. ONCM-RES-2026-V1-0012 (the V part is ignored)
    requester_name: str
    requester_email: EmailStr
    requester_org: Optional[str] = ""
    purpose: Optional[str] = ""


def find_request_only_doc(db, code):
    parts = code.strip().upper().split("-")
    if len(parts) < 5 or not parts[-1].isdigit() or not parts[2].isdigit():
        raise HTTPException(400, "Unrecognised document code")
    d = db.query(Document).filter_by(repo_category=parts[1], year=int(parts[2]), serial=int(parts[-1])).first()
    if not d or d.status != "shared_externally" or not any(s.audience == "request_only" for s in active_shares(db, d.id)):
        raise HTTPException(404, "That document is not available on request")
    return d


@router.post("/access-requests", status_code=201)
def log_mail_request(body: MailRequestIn, u: User = Depends(admin_user), db: Session = Depends(get_db)):
    """Log an access request that arrived by email to the shared ONCM mailbox."""
    d = find_request_only_doc(db, body.document_code)
    r = AccessRequest(document_id=d.id, requester_name=body.requester_name, requester_email=body.requester_email.lower(),
                      requester_org=body.requester_org, purpose=body.purpose, source="email")
    db.add(r)
    audit(db, u, "access_request.logged", "document", d.id, {"email": r.requester_email})
    db.commit()
    return row(r)


class RequestDecision(BaseModel):
    decision: Literal["approve", "reject"]
    note: str = ""
    valid_days: int = 14


@router.post("/access-requests/{rid}/decision")
def decide_request(rid: int, body: RequestDecision, u: User = Depends(admin_user), db: Session = Depends(get_db)):
    if u.role != MASTER and not u.can_share:
        raise HTTPException(403, "You are not authorised to approve access requests")
    r = db.get(AccessRequest, rid)
    if not r:
        raise HTTPException(404, "Request not found")
    if r.status != "pending":
        raise HTTPException(409, "Already decided")
    d = db.get(Document, r.document_id)
    r.decided_by, r.decision_note, r.decided_at = u.id, body.note, utcnow()
    result = {"status": body.decision}
    if body.decision == "approve":
        days = max(1, min(body.valid_days, 90))
        sh = next((s for s in active_shares(db, d.id) if s.audience == "request_only"), None)
        if not sh:
            raise HTTPException(409, "The document is no longer available on request")
        r.status, r.expires_at = "approved", utcnow() + timedelta(days=days)
        if r.user_id:   # signed-in participant: appears in their Resources
            db.add(DocumentGrant(share_id=sh.id, document_id=d.id, user_id=r.user_id, source="request", expires_at=r.expires_at))
            notify(db, "access_approved", r.requester_email, f"Access approved: {d.title}",
                   f"Your request was approved. Log in to the Workbench and open Resources (access valid {days} days).", f"req-ok:{r.id}")
        else:           # mail / public requester: secure expiring link
            link = f"{settings.app_base_url.rstrip('/')}/api/request-access/download?token={make_token(r.id, typ='reqdl', minutes=days * 1440)}"
            notify(db, "access_approved", r.requester_email, f"Access approved: {d.title}",
                   f"Your request for {doc_code(d)} was approved. Download it securely (link valid {days} days):\n{link}", f"req-ok:{r.id}")
            result["link"] = link
    else:
        r.status = "rejected"
        notify(db, "access_rejected", r.requester_email, f"Update on your request: {d.title}",
               body.note or "Your request could not be approved at this time.", f"req-no:{r.id}")
    verb = "approved" if body.decision == "approve" else "rejected"
    alerts.queue_alert(db, "access_requests", "access_decision", f"Access request {verb}: {doc_code(d)}",
                       alerts.render(f"{u.name} {verb} an access request.",
                                     [("Document", f"{doc_code(d)}  {d.title}"), ("Requester", f"{r.requester_name} <{r.requester_email}>"),
                                      ("Organisation", r.requester_org), ("Note", body.note),
                                      ("Valid until", r.expires_at.strftime("%Y-%m-%d") if r.status == "approved" and r.expires_at else None)]),
                       f"access-decision:{r.id}")
    audit(db, u, f"access_request.{body.decision}", "access_request", r.id, {"doc": d.id, "note": body.note})
    db.commit()
    return result


# ---------------------------------------------------------------- tasks (shared team view)
class TaskIn(BaseModel):
    title: str
    description: Optional[str] = ""
    organization_id: Optional[int] = None
    workstream_id: Optional[int] = None
    document_id: Optional[int] = None
    owner_id: Optional[int] = None
    due_date: Optional[datetime] = None
    priority: Literal["low", "medium", "high"] = "medium"


@router.get("/tasks")
def tasks(status: str = "", workstream_id: Optional[int] = None, u: User = Depends(admin_user), db: Session = Depends(get_db)):
    """Shared view for the whole core team (not individual inboxes). Tasks about organizations are limited to assigned ones."""
    q = db.query(Task)
    if status:
        q = q.filter(Task.status == status)
    if workstream_id:
        q = q.filter(Task.workstream_id == workstream_id)
    names = {x.id: x.name for x in db.query(User).filter(User.role != "participant")}
    onames = {o.id: o.name for o in db.query(Organization)}
    wnames = {w.id: w.name for w in db.query(Workstream)}
    mine = set(assigned_ids(db, u)) if u.role != MASTER else None
    out = []
    for t in q.order_by(Task.due_date.is_(None), Task.due_date).limit(500).all():
        if mine is not None and t.organization_id and t.organization_id not in mine and t.owner_id != u.id:
            continue
        d = db.get(Document, t.document_id) if t.document_id else None
        out.append({**row(t), "owner_name": names.get(t.owner_id), "organization_name": onames.get(t.organization_id),
                    "workstream_name": wnames.get(t.workstream_id), "document_code": doc_code(d) if d else None})
    return out


@router.post("/tasks", status_code=201)
def create_task(body: TaskIn, u: User = Depends(admin_user), db: Session = Depends(get_db)):
    if body.organization_id:
        get_org(db, u, body.organization_id)
    if body.document_id:
        get_doc(db, u, body.document_id)
    t = Task(**body.model_dump(), created_by=u.id)
    if t.owner_id is None:
        t.owner_id = u.id
    db.add(t)
    audit(db, u, "task.created", "task", "new", {"title": t.title})
    db.commit()
    return row(t)


class TaskPatch(BaseModel):
    status: Optional[Literal["open", "in_progress", "done"]] = None
    owner_id: Optional[int] = None
    due_date: Optional[datetime] = None
    priority: Optional[Literal["low", "medium", "high"]] = None


@router.patch("/tasks/{task_id}")
def patch_task(task_id: int, body: TaskPatch, u: User = Depends(admin_user), db: Session = Depends(get_db)):
    t = db.get(Task, task_id)
    if not t or (u.role != MASTER and t.organization_id and t.owner_id != u.id
                 and not org_query(db, u).filter(Organization.id == t.organization_id).first()):
        raise HTTPException(404, "Task not found")
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(t, k, v)
    t.completed_at = utcnow() if t.status == "done" else None
    audit(db, u, "task.updated", "task", t.id, body.model_dump(exclude_unset=True, mode="json"))
    db.commit()
    return row(t)


# ---------------------------------------------------------------- notifications monitor
@router.get("/notifications")
def notifications(status: str = "", u: User = Depends(admin_user), db: Session = Depends(get_db)):
    if u.role != MASTER and not u.can_export:
        raise HTTPException(403, "Email monitoring is available to the Master Admin")
    q = db.query(Notification)
    if status:
        q = q.filter(Notification.status == status)
    return [row(n, exclude=("body",)) for n in q.order_by(Notification.id.desc()).limit(300).all()]


@router.post("/notifications/{nid}/retry")
def retry(nid: int, u: User = Depends(admin_user), db: Session = Depends(get_db)):
    if u.role != MASTER:
        raise HTTPException(403, "Master Admin only")
    from app.services import _pool, deliver
    n = db.get(Notification, nid)
    if not n:
        raise HTTPException(404, "Not found")
    _pool.submit(deliver, nid)
    return {"queued": True}


# ---------------------------------------------------------------- Excel export
@router.get("/reports/export")
def export(organization_id: Optional[int] = None, actor_type: Optional[str] = None, stage: Optional[str] = None,
           doc_status: Optional[str] = None, reviewer_id: Optional[int] = None,
           date_from: Optional[datetime] = None, date_to: Optional[datetime] = None,
           u: User = Depends(admin_user), db: Session = Depends(get_db)):
    if u.role != MASTER and not u.can_export:
        raise HTTPException(403, "You have not been granted export permission")
    if organization_id:
        get_org(db, u, organization_id)
    data, name = build_workbook(db, u, organization_id, actor_type, stage, doc_status, reviewer_id, date_from, date_to)
    audit(db, u, "report.exported", "report", name, {"organization_id": organization_id, "actor_type": actor_type})
    db.commit()
    return Response(data, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})
