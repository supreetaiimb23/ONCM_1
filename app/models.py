"""Database tables (work on SQLite locally and on Supabase PostgreSQL)."""
from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, Column, Date, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy import false as sa_false
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import declarative_base

Base = declarative_base()


def now():
    return datetime.now(timezone.utc)


# JSON on SQLite, JSONB on Supabase/PostgreSQL
JSONType = JSON().with_variant(JSONB(), "postgresql")


def _ts():
    return Column(DateTime(timezone=True), default=now)


class User(Base):
    __tablename__ = "profiles"
    id = Column(Integer, primary_key=True)
    email = Column(String(255), unique=True, index=True, nullable=False)
    name = Column(String(255), nullable=False)
    password_hash = Column(String(255))
    role = Column(String(30), nullable=False, index=True)  # master_admin | secondary_admin | participant
    is_active = Column(Boolean, default=True)
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=True)
    can_share = Column(Boolean, default=False)   # Secondary Admin: may authorise external sharing
    can_export = Column(Boolean, default=False)  # Secondary Admin: may export Excel reports
    repo_access = Column(JSON, nullable=True)    # admin: repository category codes they may open (None = defaults)
    # two-step verification (TOTP); never sent to the browser (see user_out)
    mfa_secret = Column(String(64))              # base32; set at setup, active once mfa_enabled
    mfa_enabled = Column(Boolean, default=False, server_default=sa_false())
    mfa_recovery = Column(JSON, nullable=True)   # bcrypt hashes of unused one-time recovery codes
    mfa_last_step = Column(Integer)              # last accepted TOTP time step: a code cannot be used twice
    session_version = Column(Integer, default=0, server_default="0")  # +1 = every existing session is signed out
    created_at = _ts()


class Organization(Base):
    __tablename__ = "organizations"
    id = Column(Integer, primary_key=True)
    name = Column(String(255), nullable=False, index=True)
    actor_type = Column(String(60), nullable=False)
    contact_name = Column(String(255))
    contact_email = Column(String(255))
    intended_role = Column(String(255))
    status = Column(String(20), default="active")  # active | suspended
    stage = Column(String(30), default="joined")   # joined|documents_in_progress|agreement_signed|implementing|live
    waiting_on = Column(String(20), default="participant")
    assigned_admin_id = Column(Integer, ForeignKey("profiles.id"), nullable=True)
    created_at = _ts()
    updated_at = Column(DateTime(timezone=True), default=now, onupdate=now)


class JoinRequest(Base):
    __tablename__ = "join_requests"
    id = Column(Integer, primary_key=True)
    org_name = Column(String(255), nullable=False)
    actor_type = Column(String(60), nullable=False)
    contact_name = Column(String(255), nullable=False)
    contact_email = Column(String(255), nullable=False)
    intended_role = Column(String(255))
    evidence = Column(Text)
    status = Column(String(20), default="pending")  # pending|approved|rejected|more_info
    reason = Column(Text)
    duplicate_flag = Column(Boolean, default=False)
    reviewer_id = Column(Integer, ForeignKey("profiles.id"), nullable=True)
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=True)
    created_at = _ts()
    decided_at = Column(DateTime(timezone=True))
    # ---- Organizational Profile and Scale (questions 2-17 of the request-to-join form; Q1 is org_name) ----
    org_type = Column(String(40))                  # Q2
    registration_number = Column(String(100))      # Q3 CIN / LLPIN / equivalent
    date_of_incorporation = Column(Date)           # Q4
    country = Column(String(100))                  # Q5
    state_ut = Column(String(100))                 # Q6
    city = Column(String(100))                     # Q7
    year_established = Column(Integer)             # Q8
    registered_address = Column(Text)              # Q9
    employee_range = Column(String(20))            # Q10
    geographic_presence = Column(JSONType)         # Q11 list[str]
    beneficiary_segments = Column(JSONType)        # Q12 list[str]
    standards_worked_with = Column(JSONType)       # Q13 list[str]
    certifications = Column(Text)                  # Q14
    carbon_services = Column(JSONType)             # Q15 list[str]
    org_profile = Column(Text)                     # Q16 (max 150 words)
    website = Column(String(300))                  # Q17
    contact_phone = Column(String(40))
    contact_designation = Column(String(120))


class ChecklistItem(Base):
    __tablename__ = "onboarding_checklists"
    id = Column(Integer, primary_key=True)
    organization_id = Column(Integer, ForeignKey("organizations.id"), index=True)
    title = Column(String(255), nullable=False)
    done = Column(Boolean, default=False)
    completed_by = Column(Integer, ForeignKey("profiles.id"), nullable=True)
    completed_at = Column(DateTime(timezone=True))


class Document(Base):
    __tablename__ = "documents"
    __table_args__ = (UniqueConstraint("repo_category", "year", "serial", name="uq_doc_code"),)
    id = Column(Integer, primary_key=True)
    repo_category = Column(String(5), index=True)   # CM | POL | RES | PPD | CDP | AGR
    year = Column(Integer)                          # year of submission (part of the document code)
    serial = Column(Integer)                        # running number per category + year
    access_level = Column(String(20), default="internal")  # internal|pilot_shared|role_specific|restricted|public|request_only
    target_actor_types = Column(JSON, nullable=True)       # intended user group for role_specific
    workstream_id = Column(Integer, ForeignKey("workstreams.id"), nullable=True)
    approver_id = Column(Integer, ForeignKey("profiles.id"), nullable=True)   # maker-checker: designated approver
    classification_confirmed_by = Column(Integer, ForeignKey("profiles.id"), nullable=True)
    classification_confirmed_at = Column(DateTime(timezone=True))
    superseded_by_id = Column(Integer, nullable=True)
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=True, index=True)  # NULL = ONCM resource
    category = Column(String(60), nullable=False)
    title = Column(String(255), nullable=False)
    doc_type = Column(String(100))
    origin = Column(String(20), default="participant")  # participant | admin
    status = Column(String(30), default="draft", index=True)
    current_version = Column(Integer, default=1)
    assigned_reviewer_id = Column(Integer, ForeignKey("profiles.id"), nullable=True)
    due_date = Column(DateTime(timezone=True))
    created_by = Column(Integer, ForeignKey("profiles.id"))
    created_at = _ts()
    updated_at = Column(DateTime(timezone=True), default=now, onupdate=now)


class DocumentVersion(Base):
    __tablename__ = "document_versions"
    __table_args__ = (Index("uq_doc_version", "document_id", "version", unique=True),)  # also ensured on start (main.py)
    id = Column(Integer, primary_key=True)
    document_id = Column(Integer, ForeignKey("documents.id"), index=True)
    version = Column(Integer, nullable=False)
    filename = Column(String(255))
    storage_key = Column(String(500), nullable=False)  # S3 key or local relative path (never the file itself)
    checksum = Column(String(64), index=True)
    size = Column(Integer)
    uploader_id = Column(Integer, ForeignKey("profiles.id"))
    created_at = _ts()


class DocumentReview(Base):
    __tablename__ = "document_reviews"
    id = Column(Integer, primary_key=True)
    document_id = Column(Integer, ForeignKey("documents.id"), index=True)
    version = Column(Integer)
    reviewer_id = Column(Integer, ForeignKey("profiles.id"))
    decision = Column(String(30))  # start_review|recommend|approve|changes|reject
    internal_comment = Column(Text)   # NEVER exposed to participants
    feedback = Column(Text)           # participant-visible feedback
    created_at = _ts()


class DocumentShare(Base):
    __tablename__ = "document_shares"
    id = Column(Integer, primary_key=True)
    document_id = Column(Integer, ForeignKey("documents.id"), index=True)
    version = Column(Integer)
    audience = Column(String(20))  # access level: pilot_shared | role_specific | restricted | public | request_only
    actor_types = Column(JSON, nullable=True)  # role_specific: which stakeholder categories
    granted_by = Column(Integer, ForeignKey("profiles.id"))
    status = Column(String(20), default="active")  # active | revoked
    expires_at = Column(DateTime(timezone=True))
    created_at = _ts()


class Task(Base):
    __tablename__ = "onboarding_tasks"
    id = Column(Integer, primary_key=True)
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=True)
    title = Column(String(255), nullable=False)
    description = Column(Text)
    owner_id = Column(Integer, ForeignKey("profiles.id"), nullable=True)
    due_date = Column(DateTime(timezone=True))
    priority = Column(String(10), default="medium")
    workstream_id = Column(Integer, ForeignKey("workstreams.id"), nullable=True)
    document_id = Column(Integer, ForeignKey("documents.id"), nullable=True)   # review tasks tied to a document
    status = Column(String(20), default="open")  # open|in_progress|done
    created_by = Column(Integer, ForeignKey("profiles.id"))
    created_at = _ts()
    completed_at = Column(DateTime(timezone=True))


class Notification(Base):
    __tablename__ = "notifications"
    id = Column(Integer, primary_key=True)
    event = Column(String(60))
    recipient = Column(String(255))
    subject = Column(String(255))
    body = Column(Text)
    status = Column(String(20), default="queued")  # queued|sent|logged|failed
    attempts = Column(Integer, default=0)
    last_error = Column(Text)
    idempotency_key = Column(String(255), unique=True)
    created_at = _ts()
    sent_at = Column(DateTime(timezone=True))


class AuditLog(Base):
    __tablename__ = "audit_logs"
    id = Column(Integer, primary_key=True)
    actor_id = Column(Integer, ForeignKey("profiles.id"), nullable=True)
    actor_email = Column(String(255))
    action = Column(String(80), index=True)
    resource = Column(String(60))
    resource_id = Column(String(60))
    detail = Column(JSON)
    created_at = _ts()


class Workstream(Base):
    """A shared stream of core-team work (e.g. actor business-use-case specs, TSP interface specs, interaction mapping)."""
    __tablename__ = "workstreams"
    id = Column(Integer, primary_key=True)
    name = Column(String(200), unique=True, nullable=False)
    description = Column(Text)
    owner_id = Column(Integer, ForeignKey("profiles.id"), nullable=True)
    status = Column(String(20), default="active")  # active | on_hold | done
    created_at = _ts()


class DocumentGrant(Base):
    """Who may open a restricted / request-only document (organization = group, user = individual)."""
    __tablename__ = "document_grants"
    id = Column(Integer, primary_key=True)
    share_id = Column(Integer, ForeignKey("document_shares.id"), index=True)
    document_id = Column(Integer, ForeignKey("documents.id"), index=True)
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=True)
    user_id = Column(Integer, ForeignKey("profiles.id"), nullable=True)
    source = Column(String(20), default="restricted")  # restricted | request
    expires_at = Column(DateTime(timezone=True))
    created_at = _ts()


class AccessRequest(Base):
    """Request-only resources: portal requests from participants, or public requests received by mail."""
    __tablename__ = "access_requests"
    id = Column(Integer, primary_key=True)
    document_id = Column(Integer, ForeignKey("documents.id"), index=True)
    requester_name = Column(String(255))
    requester_email = Column(String(255))
    requester_org = Column(String(255))
    purpose = Column(Text)
    user_id = Column(Integer, ForeignKey("profiles.id"), nullable=True)
    source = Column(String(20), default="portal")  # portal | public_form | email
    status = Column(String(20), default="pending")  # pending | approved | rejected
    decided_by = Column(Integer, ForeignKey("profiles.id"), nullable=True)
    decision_note = Column(Text)
    created_at = _ts()
    decided_at = Column(DateTime(timezone=True))
    expires_at = Column(DateTime(timezone=True))


class ChangeLog(Base):
    """Recorded decisions, e.g. 'use the newer interaction deck over the older workbook sheet'."""
    __tablename__ = "change_log"
    id = Column(Integer, primary_key=True)
    title = Column(String(255), nullable=False)
    decision = Column(Text)
    document_id = Column(Integer, ForeignKey("documents.id"), nullable=True)
    supersedes_document_id = Column(Integer, ForeignKey("documents.id"), nullable=True)
    workstream_id = Column(Integer, ForeignKey("workstreams.id"), nullable=True)
    recorded_by = Column(Integer, ForeignKey("profiles.id"))
    created_at = _ts()


class AlertRecipient(Base):
    """An admin / stakeholder mailbox that receives live updates from the alert sender (managed by the Master Admin)."""
    __tablename__ = "alert_recipients"
    id = Column(Integer, primary_key=True)
    email = Column(String(255), unique=True, index=True, nullable=False)
    name = Column(String(255))
    categories = Column(JSON, nullable=True)      # which alert categories they want; None = all
    is_active = Column(Boolean, default=True)
    created_by = Column(Integer, ForeignKey("profiles.id"), nullable=True)
    created_at = _ts()


class AlertLog(Base):
    """One row per alert email per recipient: queued -> sent | logged | failed (retried up to 3 times)."""
    __tablename__ = "alert_log"
    id = Column(Integer, primary_key=True)
    recipient_email = Column(String(255), index=True)
    category = Column(String(30), index=True)
    event = Column(String(60))
    subject = Column(String(255))
    body = Column(Text)
    status = Column(String(20), default="queued", index=True)  # queued|sent|logged|failed
    attempts = Column(Integer, default=0)
    last_error = Column(Text)
    idempotency_key = Column(String(255), unique=True)
    created_at = _ts()
    sent_at = Column(DateTime(timezone=True))
