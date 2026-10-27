import base64
import hashlib
import logging
import re
import threading
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles

from app.core import SessionLocal, check_secrets, hash_password, settings
from app.models import AccessRequest, AlertLog, Document, Notification, Task, User
from app import alerts, search, sso
from app.migrate import migrate
from app.routers import admin, master, participant, public
from app.routers import alerts as alerts_router
from app.services import _pool, deliver, notify, shared_mailbox, storage

log = logging.getLogger("oncm")


def seed_master():
    db = SessionLocal()
    try:
        if not db.query(User).filter_by(role="master_admin").first():
            db.add(User(email=settings.master_admin_email.lower(), name=settings.master_admin_name, role="master_admin",
                        password_hash=hash_password(settings.master_admin_password), can_share=True, can_export=True))
            db.commit()
            log.warning("Created Master Admin %s - change the password after first login", settings.master_admin_email)
    finally:
        db.close()


def worker_tick():
    """Deadline reminders (REMINDER_DAYS before due) + retry of failed emails (max 3 attempts)."""
    db = SessionLocal()
    try:
        now = datetime.now(timezone.utc)
        days = [int(x) for x in settings.reminder_days.split(",") if x.strip().isdigit()]
        for t in db.query(Task).filter(Task.status != "done", Task.due_date != None).all():  # noqa: E711
            due = t.due_date if t.due_date.tzinfo else t.due_date.replace(tzinfo=timezone.utc)
            left = (due.date() - now.date()).days
            if left in days:
                owner = db.get(User, t.owner_id) if t.owner_id else None
                notify(db, "deadline_reminder", owner.email if owner else shared_mailbox(),
                       f"Deadline reminder: {t.title}", f"“{t.title}” is due in {left} day(s).",
                       f"remind:{t.id}:{left}:{now:%Y%m%d}")
                if t.organization_id:
                    from app.models import Organization
                    org = db.get(Organization, t.organization_id)
                    if org:
                        notify(db, "deadline_reminder", org.contact_email, f"Deadline reminder: {t.title}",
                               f"“{t.title}” is due in {left} day(s).", f"remind-org:{t.id}:{left}:{now:%Y%m%d}")
        db.commit()
        review_reminders(db)
        for n in db.query(Notification).filter(Notification.status == "failed", Notification.attempts < 3).all():
            _pool.submit(deliver, n.id)
        for a in db.query(AlertLog).filter(AlertLog.status == "failed", AlertLog.attempts < 3).all():
            _pool.submit(alerts.deliver_alert, a.id)
    finally:
        db.close()


def review_reminders(db):
    """Reminder when a document sits under review longer than REVIEW_REMINDER_DAYS (one email per day)."""
    now = datetime.now(timezone.utc)
    for d in db.query(Document).filter_by(status="under_review").all():
        upd = d.updated_at if d.updated_at.tzinfo else d.updated_at.replace(tzinfo=timezone.utc)
        rev = db.get(User, d.assigned_reviewer_id) if d.assigned_reviewer_id else None
        if rev and (now - upd).days >= settings.review_reminder_days:
            notify(db, "review_reminder", rev.email, f"Review pending: {d.title}",
                   "This document is still waiting for your review. Open the approval inbox in the Workbench.",
                   f"review-remind:{d.id}:{now:%Y%m%d}")
    db.commit()


def poll_mailbox():
    """Mail-in channel: an email to the shared mailbox that mentions a document code (ONCM-RES-2026-V1-0012)
    becomes a pending access request. Works with any IMAP mailbox; disabled when IMAP_HOST is empty."""
    if not settings.imap_host:
        return
    import email
    import imaplib
    import re
    from email.header import decode_header, make_header
    from email.utils import parseaddr
    from fastapi import HTTPException
    from app.routers.admin import find_request_only_doc
    M = imaplib.IMAP4_SSL(settings.imap_host, settings.imap_port)
    db = SessionLocal()
    try:
        M.login(settings.imap_user, settings.imap_password)
        M.select(settings.imap_folder)
        _, data = M.search(None, "UNSEEN")
        for num in data[0].split():
            _, msg = M.fetch(num, "(RFC822)")
            m = email.message_from_bytes(msg[0][1])
            subject = str(make_header(decode_header(m.get("Subject", ""))))
            body = ""
            for part in m.walk():
                if part.get_content_type() == "text/plain":
                    body += part.get_payload(decode=True).decode(errors="ignore")[:1500]
            hit = re.search(r"ONCM-[A-Z]{2,3}-\d{4}-V\d+-\d+", (subject + " " + body).upper())
            name, addr = parseaddr(m.get("From", ""))
            if not hit or not addr:
                continue
            try:
                d = find_request_only_doc(db, hit.group(0))
            except HTTPException:
                continue
            if db.query(AccessRequest).filter_by(document_id=d.id, requester_email=addr.lower(), status="pending").first():
                continue
            db.add(AccessRequest(document_id=d.id, requester_name=name or addr, requester_email=addr.lower(),
                                 purpose=(body.strip() or subject)[:1000], source="email"))
            db.commit()
    finally:
        db.close()
        try:
            M.logout()
        except Exception:
            pass


def loop():
    while True:
        for fn in (worker_tick, poll_mailbox, search.sync_if_needed):
            try:
                fn()
            except Exception:  # never kill the worker thread
                log.exception("worker error in %s", fn.__name__)
        time.sleep(3600)


@asynccontextmanager
async def lifespan(app: FastAPI):
    for problem in check_secrets(settings):  # raises outside APP_ENV=development
        log.warning("INSECURE (APP_ENV=development): %s", problem)
    if settings.malware_scan != "clamav":
        log.warning("MALWARE_SCAN=%s: uploads are not virus-scanned", settings.malware_scan)
    migrate()  # Alembic: creates or upgrades the schema (app/migrate.py)
    storage()  # fail fast on bad storage config
    seed_master()
    if search.enabled():
        search._pool.submit(search.sync_all)  # background: a new or emptied engine catches up
    alerts.seed_recipients()
    threading.Thread(target=loop, daemon=True).start()
    yield


app = FastAPI(title=settings.app_name, version="1.0.0", lifespan=lifespan)


def _content_security_policy() -> str:
    """Only our own origin may run scripts and receive data (connect-src 'self'), so even a compromised third-party
    script (the Tailwind CDN) could not send tokens or documents elsewhere. The inline config script and the inline
    `this.select()` handlers are allowed by hash, computed from the shipped files so edits stay covered."""
    static = Path(__file__).resolve().parent.parent / "static"
    sha = lambda t: "'sha256-" + base64.b64encode(hashlib.sha256(t.encode()).digest()).decode() + "'"  # noqa: E731
    html = (static / "index.html").read_text()
    inline_scripts = re.findall(r"<script>(.*?)</script>", html, re.S)
    handlers = set(re.findall(r'\bon[a-z]+="([^"]*)"', html + (static / "app.js").read_text()))
    script_src = " ".join(["'self'", "https://cdn.tailwindcss.com", *map(sha, inline_scripts)]
                          + (["'unsafe-hashes'", *map(sha, sorted(handlers))] if handlers else []))
    return "; ".join([
        "default-src 'self'", f"script-src {script_src}",
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com",  # Tailwind's CDN build injects <style> at runtime
        "font-src 'self' https://fonts.gstatic.com", "img-src 'self' data:", "connect-src 'self'",
        "object-src 'none'", "base-uri 'none'", "form-action 'self'", "frame-ancestors 'none'"])


CSP = _content_security_policy()


@app.middleware("http")
async def security_headers(request: Request, call_next):
    resp = await call_next(request)
    resp.headers.update({"X-Content-Type-Options": "nosniff", "X-Frame-Options": "DENY",
                         "Referrer-Policy": "same-origin", "Cache-Control": "no-store" if request.url.path.startswith("/api") else "no-cache",
                         "Content-Security-Policy": CSP, "Cross-Origin-Opener-Policy": "same-origin",
                         "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=(), usb=()"})
    return resp


for r in (public.router, participant.router, admin.router, master.router, alerts_router.router, sso.router):
    app.include_router(r)

app.mount("/", StaticFiles(directory=Path(__file__).resolve().parent.parent / "static", html=True), name="static")
