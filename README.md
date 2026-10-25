# ONCM Workbench

Document management, participant onboarding, approval and communication platform.
**FastAPI + Supabase (PostgreSQL) + local/S3 file storage + Tailwind UI + Excel reports + email automation.**

## Run it (3 steps)

```bash
cp .env.example .env        # 1. edit SECRET_KEY, MASTER_ADMIN_EMAIL, MASTER_ADMIN_PASSWORD
docker compose up -d --build   # 2. build and start
# 3. open http://localhost:8000  and sign in as the Master Admin
```

Without Docker: `python -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt && uvicorn app.main:app --reload`
The app refuses to start with the default `SECRET_KEY` (or one under 32 characters) or `MASTER_ADMIN_PASSWORD`; set `APP_ENV=development` to allow them locally.
API docs (Swagger): http://localhost:8000/docs

## Tests

```bash
pip install -r requirements-dev.txt
pytest                                   # unit + API + concurrency, fresh SQLite per run
TEST_DATABASE_URL=postgresql://localhost/oncm_test pytest   # same suite on PostgreSQL (DB name must contain "test"; it is wiped)
playwright install chromium && pytest -m e2e                # browser tests; or: pytest -m e2e --browser-channel chrome
```

`tests/test_concurrency.py` runs uploads in parallel against a real single-process server, as in the Docker image. Run it on PostgreSQL before every release.

## Request to join form (Organizational Profile and Scale)

Public page `#/join` (17 questions + contact and ONCM role). Mobile-first, sticky scroll-progress bar with section tabs, answers auto-saved on the device until submitted. Server-side validation lives in `JoinIn` in `app/routers/public.py`; option lists are served from `GET /api/meta`. The Master Admin sees every answer under **Onboarding Requests > View**. Full Supabase schema: `supabase/schema.sql` (new project) or `supabase/migrations/002_join_request_profile.sql` (existing database; the app also adds the new columns itself on start).

## Everything is configured in `.env`

| Want to… | Set |
|---|---|
| Use **Supabase** | `DATABASE_URL=postgresql://postgres.<ref>:<pw>@aws-0-<region>.pooler.supabase.com:5432/postgres?sslmode=require` (Supabase → Project Settings → Database → Connection string). Tables are created automatically on start. |
| Stay on SQLite for local dev | leave `DATABASE_URL=sqlite:///./data/oncm.db` |
| Store files locally | `STORAGE_BACKEND=local` (saved in the `oncm_storage` Docker volume) |
| **Switch to AWS S3 later** | `STORAGE_BACKEND=s3`, `S3_BUCKET`, `S3_REGION`, `S3_ACCESS_KEY_ID`, `S3_SECRET_ACCESS_KEY`, restart. No code change. |
| Send real emails | fill `SMTP_*` (Amazon SES SMTP works). Left empty, emails are recorded as `logged` in the Email monitor. |
| Secondary Admin can only *recommend* | `TWO_PERSON_RULE=true` (Master Admin or the designated approver gives final approval) |
| Read access requests from the shared mailbox | `IMAP_HOST`, `IMAP_USER`, `IMAP_PASSWORD` (any IMAP mailbox) |
| **Search inside files** (PDF, Word, Excel, text) | `SEARCH_BACKEND=engine`, `SEARCH_ENGINE_TOKEN`, then `docker compose --profile search up -d --build` (builds `../adaptive-search-engine`). Finds documents by meaning too ("renewable energy contract" finds a solar PPA); scanned PDFs and images are read with OCR (`OCR_*`). Default `db` searches titles and document codes. Results are always permission-filtered by ONCM. |
| **Email admins about every update** (new organisations, decisions, onboarding, documents) | `ALERT_SMTP_*` (sender, e.g. a Gmail App Password) and `ALERT_RECIPIENTS`. See [ADMIN_ALERTS.md](ADMIN_ALERTS.md). |

S3 setup: create a **private** bucket (Block all public access = on), enable versioning, and give the IAM user only `s3:PutObject`/`s3:GetObject` on that bucket. Files are always downloaded through 5-minute presigned links created after a permission check. Existing local files are not moved automatically; copy `./storage/*` into the bucket with the same keys (`aws s3 sync storage/ s3://BUCKET/`).

Notes: Supabase is used as the PostgreSQL database; sign-in is handled by the app itself (bcrypt + JWT). The service-role/DB password only ever lives in `.env` on the server. For production, put the container behind HTTPS (Caddy, Nginx, or your cloud load balancer).

## Knowledge repository (from the ONCM knowledge-repository and project notes)

**Document code:** `ONCM-<Category>-<Year>-V<Version>-<Serial>`, for example `ONCM-POL-2026-V1-0003`. Year is the year of submission, the serial runs per category and year, and the version follows the current version.

**Categories:** CM Carbon Markets · POL Policies/Rules/Notifications · RES Research · PPD Pilot project documents · CDP Common documents for pilot · AGR Agreements with external stakeholders.

**Access levels** (every document has one; the approver confirms it before anything is published):

| Level | Who can open it |
|---|---|
| 1 Internal | Authorised ONCM team only. Can never be published. |
| 2 Pilot shared | All authorised pilot participants |
| 3 Role-specific | Selected stakeholder categories (Project Developer, VVB, Obligated Entity, ACVA, TSP) |
| 4 Restricted | Named organizations and named individuals |
| 5 Public | Anyone, no login |
| 6 Request-only | Only people whose request ONCM approved |

Each category only allows the levels in the Access and Sharing table: **AGR** → Internal or Restricted; **CDP** → Internal or Pilot shared; **PPD** → Internal, Role-specific or Restricted; CM/POL/RES → any level. Internal team access follows each admin's *repository access* list (AGR is off by default).

**Workflow (enforced by the backend):** Upload → Review → Approval (confirm access level and intended group) → Repository configuration → Publication. Stages shown on every document: Draft · In review · Approved · Published · Archive. Publication can never be broader than the approved classification.

**Maker-checker:** the person who prepared the current version can never review or approve it. A document has a maker, a checker (reviewer) and a designated approver (reviewer and approver must differ when `TWO_PERSON_RULE=true`).

**Change log:** record decisions such as "use the newer interaction deck". Choosing a document that *replaces* another marks the old one **superseded**, unpublishes it and notifies its owners, so everyone uses the latest.

**Request-only access:** public web form, signed-in participants, or an **email to the shared mailbox** (mentioning the document code; picked up automatically if `IMAP_*` is set, or logged by an admin). Approval gives a signed-in participant access in Resources, or emails an expiring secure download link to an external requester.

**Workstreams and tasks:** workstreams (e.g. actor specs, TSP interface requirements, interaction mapping) have an owner and a shared status view across the core team. Review tasks are created automatically when a document goes into review, with reminders before the deadline and when a review is overdue.

## What each role can do

### Participant / client (own organization only)
- Request to join (public form); set password from the emailed invite link
- Dashboard: onboarding rail (Joined → Documents in progress → Agreement signed → Implementing → Live), checklist, items needing action with reviewer feedback, with-ONCM list, deadlines, ONCM contact
- Upload documents into the four folders (01 Onboarding, 02 Agreements, 03 Implementation, 04 Meetings and decisions); each gets a document code and starts Restricted; replace a document when changes are requested; version history; download own files
- Resources: everything published to them (pilot shared, their stakeholder category, restricted to them, public, approved request-only); a list of request-only documents with a **Request access** button
- Public page (no login): public documents and the request-only list with a request form
- Send a question to the shared ONCM support mailbox; request a contact change (goes through ONCM verification); change password
- **Cannot:** see other organizations' files, internal documents or review notes, approve anything, change statuses or the tracker

### Secondary Admin (assigned organizations and permitted repository categories)
- Overview, organizations, repository (stage tabs, filters by category and workstream), workstreams, shared task view, change log
- Create documents in categories they are permitted; propose the access level and intended group; upload new versions of their drafts
- Review: submit/start review, internal notes, request changes, reject, **recommend approval**; give final approval only if designated approver (or if `TWO_PERSON_RULE=false`)
- Mark checklist items complete, update stage / waiting-on, send missing-document reminders, add members to an assigned organization
- **Only if the Master Admin grants it:** configure access and publish, approve access requests (`can_share`); export Excel and view the email monitor (`can_export`)
- **Cannot:** create admins, change roles/permissions, reassign organizations, suspend accounts, override the workflow, view the audit log, or review their own work (maker-checker)

### Master Admin (everything)
- All of the above across all organizations and categories
- Approve / reject / request more info on join requests (creates the workspace, checklist, invite email)
- Create and deactivate admins; set repository category access, `can_share`, `can_export` (reason required, audited)
- Assign checker and approver per document; final approval; publish at the approved level (named organizations/individuals for Restricted); unpublish; archive and restore
- Override a document status or force an organization Live (reason required, audited); suspend / restore organization access
- Audit log, email monitor with retry, full Excel export

## Workflow notes
`draft → submitted → under_review → changes_requested | internally_approved | rejected → shared_externally (published) → superseded → archived`.
Approval and publication are separate: an approved document is invisible outside ONCM until someone with permission publishes it. Emails are only sent after the database commit, use idempotency keys, never carry attachments or internal notes, and failed sends are retried up to 3 times by the hourly worker (which also sends deadline reminders at `REMINDER_DAYS`, overdue-review reminders after `REVIEW_REMINDER_DAYS`, and reads the mail-in channel).

## API reference (all under `/api`, Bearer token except where noted)

**Public**: `GET /health` · `GET /meta` · `POST /auth/login` · `POST /auth/accept-invite` · `POST /join-requests` · `GET /public/resources` · `GET /public/resources/{id}/download` · `POST /public/access-requests` · `GET /request-access/download?token=` (emailed link) · `GET /files/local?token=` (signed, 5 min)
**Any user**: `GET /auth/me` · `POST /auth/change-password`
**Participant `/me`**: `GET /dashboard` · `GET|POST /documents` · `POST /documents/{id}/versions` · `GET /documents/{id}/download?version=` · `GET /resources` · `GET /resources/{id}/download` · `GET /catalogue` · `POST /access-requests` · `POST /questions` · `POST /contact-change`
**Admin `/admin`** (Master + Secondary): `GET /overview` · `GET /meta` · `GET /admins` · `GET /participants` · `GET /organizations` · `GET|PATCH /organizations/{id}` · `GET|POST /organizations/{id}/members` · `POST /organizations/{id}/missing-docs-reminder` · `POST /checklist/{id}/toggle?done=` · `GET|POST /documents` · `GET|PATCH /documents/{id}` · `POST /documents/{id}/versions|assign|review|publish|grants|archive|restore` · `GET /documents/{id}/download` · `DELETE /shares/{id}` · `DELETE /grants/{id}` · `GET|POST /workstreams` · `PATCH /workstreams/{id}` · `GET|POST /changelog` · `GET|POST /access-requests` · `POST /access-requests/{id}/decision` · `GET|POST /tasks` · `PATCH /tasks/{id}` · `GET /notifications` · `POST /notifications/{id}/retry` (Master) · `GET /reports/export?organization_id=&actor_type=&stage=&doc_status=&reviewer_id=&date_from=&date_to=`
**Master alerts `/master`**: `GET /alert-settings` · `GET|POST /alert-recipients` · `PATCH|DELETE /alert-recipients/{id}` · `POST /alert-recipients/{id}/test` · `GET /alert-log` · `POST /alert-log/{id}/retry`
**Master `/master`**: `GET /join-requests` · `POST /join-requests/{id}/decision` · `GET|POST /admins` · `PATCH /admins/{id}` · `GET /audit-logs` · `POST /documents/{id}/override`

## Excel workbook
`ONCM_Workbench_Report_YYYY-MM-DD.xlsx` with sheets: Participant Tracker, Document Register, Repository Register, Change Log, Workstreams, Approval History, Pending Tasks, Access Requests, Automation Report, Audit Report. Scope follows the role (Secondary = assigned organizations). Every export is logged.

## Hardening checklist before go-live
Set a random 32+ character `SECRET_KEY` and a non-default `MASTER_ADMIN_PASSWORD` (startup fails otherwise) · keep `APP_ENV=production` · serve over HTTPS · set `APP_BASE_URL` to your public URL · configure SMTP and a verified sender domain · `MALWARE_SCAN=clamav` with the `clamav` service (uploads are refused while the scanner is down) · enable Supabase backups and S3 versioning · for several app replicas, move the rate limiter and worker to Redis/a job queue.

## Layout
`app/` (core, models, services, **alerts**, excel, routers/{public,participant,admin,master,**alerts**}) · `static/` (index.html, app.js) · `Dockerfile` · `docker-compose.yml` · `.env.example`

## Upgrading from the first version
The database layout changed (document codes, access levels, workstreams, grants, requests, change log). Tables are created automatically on a fresh database. If you already have test data, start clean: `docker compose down -v` (this deletes the data and file volumes), or point `DATABASE_URL` at an empty Supabase project/schema. Add Alembic migrations before holding real data.
