"""Public + auth endpoints: login, invites, join requests, public resources, local file delivery."""
import re
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from datetime import datetime, timezone

from fastapi.responses import FileResponse, RedirectResponse
from pydantic import BaseModel, EmailStr, Field, field_validator
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core import (current_user, decode_token, get_db, hash_password, make_token, rate_limit, settings,
                      verify_password)
from app import alerts
from app.models import AccessRequest, Document, DocumentShare, JoinRequest, Organization, User
from app.services import (ACTOR_TYPES, REPO_CATEGORIES, active_shares, audit, catalogue_docs, doc_code, doc_out, notify,
                          shared_mailbox, storage, user_out)
from app.models import DocumentVersion

router = APIRouter(prefix="/api")


@router.get("/health")
def health():
    return {"status": "ok", "storage": settings.storage_backend, "email": "smtp" if settings.smtp_host else "log-only",
            "alerts": "smtp" if alerts.smtp_configured() else "log-only", "search": settings.search_backend}


class LoginIn(BaseModel):
    email: EmailStr
    password: str


@router.post("/auth/login")
def login(body: LoginIn, request: Request, db: Session = Depends(get_db)):
    rate_limit("login:" + (request.client.host if request.client else "?"), 10, 300)
    u = db.query(User).filter(func.lower(User.email) == body.email.lower()).first()
    if not u or not u.is_active or not verify_password(body.password, u.password_hash):
        raise HTTPException(401, "Incorrect email or password")
    if u.role == "participant":
        org = db.get(Organization, u.organization_id)
        if not org or org.status != "active":
            raise HTTPException(403, "This organization's access is suspended. Contact ONCM.")
    audit(db, u, "auth.login", "user", u.id)
    db.commit()
    return {"token": make_token(u.id), "user": user_out(u)}


@router.get("/auth/me")
def me(u: User = Depends(current_user), db: Session = Depends(get_db)):
    out = user_out(u)
    if u.organization_id:
        o = db.get(Organization, u.organization_id)
        out["organization"] = {"id": o.id, "name": o.name, "actor_type": o.actor_type}
    out["two_person_rule"] = settings.two_person_rule
    return out


class InviteIn(BaseModel):
    token: str
    password: str = Field(min_length=10, max_length=128)


@router.post("/auth/accept-invite")
def accept_invite(body: InviteIn, db: Session = Depends(get_db)):
    p = decode_token(body.token, "invite")
    u = db.get(User, int(p["sub"]))
    if not u:
        raise HTTPException(404, "Invitation not found")
    first_time = u.password_hash is None
    u.password_hash, u.is_active = hash_password(body.password), True
    audit(db, u, "auth.invite_accepted", "user", u.id)
    if first_time and u.role == "participant" and u.organization_id:
        org = db.get(Organization, u.organization_id)
        if org:
            alerts.queue_alert(db, "joins", "org_activated", f"{u.name} ({org.name}) activated their account",
                               alerts.render(f"{u.name} accepted the invitation and can now sign in for {org.name}.",
                                             [("Organisation", org.name), ("Actor type", org.actor_type), ("User", f"{u.name} <{u.email}>")]),
                               f"activated:{u.id}")
    db.commit()
    return {"token": make_token(u.id), "user": user_out(u)}


class PwIn(BaseModel):
    current_password: str
    new_password: str = Field(min_length=10, max_length=128)


@router.post("/auth/change-password")
def change_password(body: PwIn, u: User = Depends(current_user), db: Session = Depends(get_db)):
    if not verify_password(body.current_password, u.password_hash):
        raise HTTPException(400, "Current password is incorrect")
    u.password_hash = hash_password(body.new_password)
    audit(db, u, "auth.password_changed", "user", u.id)
    db.commit()
    return {"ok": True}


# ---- option lists for the "Organizational Profile and Scale" form (the UI reads them from /api/meta) ----
ORG_TYPES = ["Private Limited Company", "Public Limited Company", "LLP", "Partnership", "Proprietorship", "Trust", "Society",
             "Section 8 Company", "Government body", "Academic/ Research institution", "NGO/ Non-Profit", "FPO/ Cooperative"]
EMPLOYEE_RANGES = ["1-10", "11-50", "51-200", "201-500", "501-1000", "More than 1000"]
GEOGRAPHIES = ["Pan-India", "South India", "West India", "North India", "East India", "Northeast India", "Southeast Asia",
               "European Economic region", "North America", "South America", "Africa", "Australia"]
SEGMENTS = ["Smallholder farmers", "FPOs", "Forest Dependent communities", "Industry", "Corporates", "Government", "Investors",
            "Project developers", "Project / Resource owner"]
STANDARDS = ["ICM", "Gold Standard", "GCC", "Verra Standard", "Plan Vivo", "Isometric", "RenewCred"]
SERVICES = ["Project development", "MRV/dMRV", "Validation", "Verification", "Registry services", "Trading", "Advisory",
            "Technology", "Finance", "Capacity building"]
MAX_PROFILE_WORDS = 150


def _pick(values, allowed, label, required=True):
    values = [v for v in (values or []) if v]
    if required and not values:
        raise ValueError(f"{label}: choose at least one option")
    bad = [v for v in values if v not in allowed]
    if bad or len(set(values)) != len(values):
        raise ValueError(f"{label}: invalid choice")
    return values


class JoinIn(BaseModel):
    # 1-9 registration details
    org_name: str = Field(min_length=2, max_length=255)                     # 1 Full legal name
    org_type: str                                                           # 2
    registration_number: str = Field(min_length=2, max_length=100)         # 3
    date_of_incorporation: date                                             # 4
    country: str = Field(min_length=2, max_length=100)                     # 5
    state_ut: str = Field(min_length=2, max_length=100)                    # 6
    city: str = Field(min_length=2, max_length=100)                        # 7
    year_established: int = Field(ge=1800)                                  # 8
    registered_address: str = Field(min_length=10, max_length=600)         # 9
    # 10-12 scale and reach
    employee_range: str                                                     # 10
    geographic_presence: list[str]                                          # 11
    beneficiary_segments: list[str]                                         # 12
    # 13-15 carbon market experience
    standards_worked_with: list[str] = []                                   # 13 (optional)
    certifications: Optional[str] = Field(default="", max_length=1500)     # 14 (optional)
    carbon_services: list[str]                                              # 15
    # 16-17 about
    org_profile: str = Field(min_length=10, max_length=1500)               # 16
    website: Optional[str] = Field(default="", max_length=300)             # 17 (optional)
    # who ONCM should contact + the ONCM role being applied for (needed to open the workspace after approval)
    contact_name: str = Field(min_length=2, max_length=200)
    contact_email: EmailStr
    contact_phone: Optional[str] = Field(default="", max_length=40)
    contact_designation: Optional[str] = Field(default="", max_length=120)
    actor_type: str
    intended_role: Optional[str] = Field(default="", max_length=255)

    @field_validator("org_type")
    @classmethod
    def _org_type(cls, v):
        if v not in ORG_TYPES:
            raise ValueError("Organization type: choose one of the listed options")
        return v

    @field_validator("employee_range")
    @classmethod
    def _emp(cls, v):
        if v not in EMPLOYEE_RANGES:
            raise ValueError("Number of full-time employees: choose one of the listed ranges")
        return v

    @field_validator("date_of_incorporation")
    @classmethod
    def _doi(cls, v):
        if v > date.today() or v.year < 1800:
            raise ValueError("Date of incorporation must be a real date in the past")
        return v

    @field_validator("year_established")
    @classmethod
    def _year(cls, v):
        if v > date.today().year:
            raise ValueError("Year of establishment cannot be in the future")
        return v

    @field_validator("geographic_presence")
    @classmethod
    def _geo(cls, v):
        return _pick(v, GEOGRAPHIES, "Geographic presence")

    @field_validator("beneficiary_segments")
    @classmethod
    def _seg(cls, v):
        return _pick(v, SEGMENTS, "Beneficiary or customer segments")

    @field_validator("standards_worked_with")
    @classmethod
    def _std(cls, v):
        return _pick(v, STANDARDS, "Standards or registries", required=False)

    @field_validator("carbon_services")
    @classmethod
    def _svc(cls, v):
        return _pick(v, SERVICES, "Key carbon market services")

    @field_validator("org_profile")
    @classmethod
    def _profile(cls, v):
        v = v.strip()
        if len(v.split()) > MAX_PROFILE_WORDS:
            raise ValueError(f"Brief organizational profile must be {MAX_PROFILE_WORDS} words or fewer")
        return v

    @field_validator("website")
    @classmethod
    def _site(cls, v):
        v = (v or "").strip()
        if not v:
            return ""
        if not re.match(r"^https?://", v, re.I):
            v = "https://" + v
        if not re.match(r"^https?://[^\s/$.?#][^\s]*\.[^\s.]{2,}(/\S*)?$", v, re.I):
            raise ValueError("Official website: enter a valid web address, for example https://example.org")
        return v

    @field_validator("registration_number", "org_name", "city", "state_ut", "country", "registered_address", "contact_name")
    @classmethod
    def _strip(cls, v):
        return " ".join(v.split()) if v else v


@router.get("/meta")
def meta():
    return {"actor_types": ACTOR_TYPES, "org_types": ORG_TYPES, "employee_ranges": EMPLOYEE_RANGES, "geographies": GEOGRAPHIES,
            "segments": SEGMENTS, "standards": STANDARDS, "services": SERVICES, "max_profile_words": MAX_PROFILE_WORDS}


@router.post("/join-requests", status_code=201)
def join(body: JoinIn, request: Request, db: Session = Depends(get_db)):
    rate_limit("join:" + (request.client.host if request.client else "?"), 5, 3600)
    if body.actor_type not in ACTOR_TYPES:
        raise HTTPException(400, "Unknown actor type")
    dup = db.query(Organization).filter(func.lower(Organization.name) == body.org_name.lower()).first() is not None
    if not dup:  # same registration number already applied or registered
        dup = db.query(JoinRequest.id).filter(func.lower(JoinRequest.registration_number) == body.registration_number.lower(),
                                              JoinRequest.status != "rejected").first() is not None
    jr = JoinRequest(org_name=body.org_name, actor_type=body.actor_type, contact_name=body.contact_name,
                     contact_email=body.contact_email.lower(), intended_role=body.intended_role or "",
                     evidence=body.org_profile, duplicate_flag=dup,
                     org_type=body.org_type, registration_number=body.registration_number,
                     date_of_incorporation=body.date_of_incorporation, country=body.country, state_ut=body.state_ut,
                     city=body.city, year_established=body.year_established, registered_address=body.registered_address,
                     employee_range=body.employee_range, geographic_presence=body.geographic_presence,
                     beneficiary_segments=body.beneficiary_segments, standards_worked_with=body.standards_worked_with,
                     certifications=(body.certifications or "").strip(), carbon_services=body.carbon_services,
                     org_profile=body.org_profile, website=body.website or "",
                     contact_phone=body.contact_phone or "", contact_designation=body.contact_designation or "")
    db.add(jr)
    db.flush()
    notify(db, "join_request_submitted", shared_mailbox(), f"New join request: {jr.org_name}",
           f"{jr.org_name} ({jr.actor_type}) has applied to join. Review it in the Workbench admin area.",
           f"join-submitted:{jr.id}")
    alerts.queue_alert(db, "joins", "join_request_submitted", f"New organisation wants to join: {jr.org_name}",
                       alerts.render(f"{jr.org_name} has asked to join ONCM and is waiting for verification.",
                                     [("Organisation", jr.org_name), ("Organisation type", jr.org_type),
                                      ("Registration number", jr.registration_number),
                                      ("Registered in", f"{jr.city}, {jr.state_ut}, {jr.country}"),
                                      ("Employees", jr.employee_range), ("Actor type", jr.actor_type),
                                      ("Carbon market services", ", ".join(jr.carbon_services)),
                                      ("Geographic presence", ", ".join(jr.geographic_presence)),
                                      ("Website", jr.website),
                                      ("Contact", f"{jr.contact_name} <{jr.contact_email}>"), ("Intended role", jr.intended_role),
                                      ("Profile", (jr.org_profile or "")[:800]),
                                      ("Possible duplicate", "YES - an organisation with this name or registration number already exists" if dup else None),
                                      ("Request number", jr.id)],
                                     "Open the full application under Onboarding Requests (Master Admin)."),
                       f"join-submitted:{jr.id}")
    notify(db, "join_request_received", jr.contact_email, "We received your request to join ONCM",
           "Thank you. The ONCM team will verify your request and contact you with the outcome.",
           f"join-received:{jr.id}")
    audit(db, None, "join_request.created", "join_request", jr.id, {"org": jr.org_name})
    db.commit()
    return {"id": jr.id, "message": "Request received. ONCM will contact you by email."}


# ---------- public knowledge repository (no login) ----------
@router.get("/public/resources")
def public_resources(db: Session = Depends(get_db)):
    """Level 5 (Public) documents, plus a metadata-only list of Level 6 (Request-only) documents."""
    pub = [dict(doc_out(db, d, True), access="public") for d, _ in catalogue_docs(db, "public")]
    req = [{"id": d.id, "code": doc_code(d), "title": d.title, "repo_category_name": REPO_CATEGORIES.get(d.repo_category), "access": "request_only"}
           for d, _ in catalogue_docs(db, "request_only")]
    return {"public": pub, "request_only": req}


@router.get("/public/resources/{doc_id}/download")
def public_download(doc_id: int, db: Session = Depends(get_db)):
    pair = next(((d, s) for d, s in catalogue_docs(db, "public") if d.id == doc_id), None)
    if not pair:
        raise HTTPException(404, "Not found")
    d, s = pair
    v = db.query(DocumentVersion).filter_by(document_id=d.id, version=s.version).first()
    return {"url": storage().download_url(v.storage_key, v.filename), "filename": v.filename}


class PublicRequestIn(BaseModel):
    document_id: int
    requester_name: str = Field(min_length=2, max_length=200)
    requester_email: EmailStr
    requester_org: Optional[str] = ""
    purpose: str = Field(min_length=5, max_length=1000)


@router.post("/public/access-requests", status_code=201)
def public_request(body: PublicRequestIn, request: Request, db: Session = Depends(get_db)):
    """Public access on request (the web form equivalent of emailing the shared ONCM mailbox)."""
    rate_limit("req:" + (request.client.host if request.client else "?"), 5, 3600)
    d = db.get(Document, body.document_id)
    if not d or d.status != "shared_externally" or not any(s.audience == "request_only" for s in active_shares(db, d.id)):
        raise HTTPException(404, "This document is not available on request")
    r = AccessRequest(document_id=d.id, requester_name=body.requester_name, requester_email=body.requester_email.lower(),
                      requester_org=body.requester_org, purpose=body.purpose, source="public_form")
    db.add(r)
    db.flush()
    notify(db, "access_requested", shared_mailbox(), f"Access request: {doc_code(d)}",
           f"{r.requester_name} <{r.requester_email}> ({r.requester_org or 'no organization'}) requests “{d.title}”.\nPurpose: {r.purpose}",
           f"access-req:{r.id}")
    notify(db, "access_request_received", r.requester_email, "We received your request",
           "ONCM will review your request and reply by email.", f"access-recv:{r.id}")
    audit(db, None, "access_request.created", "document", d.id, {"email": r.requester_email})
    db.commit()
    return {"message": "Request received. ONCM will reply by email."}


@router.get("/request-access/download")
def request_download(token: str, db: Session = Depends(get_db)):
    """Emailed link for an approved request. Valid until the approval expires; the document must still be request-only."""
    p = decode_token(token, "reqdl")
    r = db.get(AccessRequest, int(p["sub"]))
    exp = r.expires_at.replace(tzinfo=timezone.utc) if r and r.expires_at is not None and r.expires_at.tzinfo is None else (r.expires_at if r else None)
    if not r or r.status != "approved" or (exp and exp < datetime.now(timezone.utc)):
        raise HTTPException(403, "This link is no longer valid")
    pair = next(((d, s) for d, s in catalogue_docs(db, "request_only") if d.id == r.document_id), None)
    if not pair:
        raise HTTPException(404, "This document is no longer available")
    d, s = pair
    v = db.query(DocumentVersion).filter_by(document_id=d.id, version=s.version).first()
    audit(db, None, "request_access.download", "document", d.id, {"request": r.id})
    db.commit()
    return RedirectResponse(storage().download_url(v.storage_key, v.filename))


@router.get("/files/local")
def local_file(token: str):
    """Short-lived (5 min) signed link used only when STORAGE_BACKEND=local."""
    p = decode_token(token, "file")
    if settings.storage_backend != "local":
        raise HTTPException(404, "Not found")
    path = storage().path(p["key"])
    if not path.exists():
        raise HTTPException(404, "File missing")
    return FileResponse(path, filename=p["name"])
