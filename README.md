# ONCM Workbench

Document workflow and onboarding platform for the Open Network for Carbon Markets (ONCM). Partner organisations
submit documents; the ONCM team reviews, approves and publishes them under per-document access levels, with every
decision and download audited.

## Features

- Onboarding: public application form, approval workflow, per-organisation workspace and checklist
- Versioned documents with stable codes (`ONCM-POL-2026-V1-0003`), maker-checker review and two-person approval
- Six access levels, organisation isolation, request-only resources, short-lived signed downloads
- Search by title and code; optional full-text, OCR and semantic search via
  [adaptive-search-engine](https://github.com/supreetpatil79/adaptive-search-engine)
- ClamAV scanning, TOTP two-step verification, Microsoft Entra ID sign-in, encrypted storage
- Excel reporting, email notifications and reminders, admin alerts

## Architecture

```
Browser ──HTTPS──► Caddy (TLS) ──► ONCM app (FastAPI)
                                      ├── PostgreSQL           records (SQLite for local development)
                                      ├── S3 or local disk     files, encrypted
                                      ├── ClamAV               virus scan of every upload
                                      └── adaptive-search      full-text + semantic search (optional)
```

| Layer | Technology |
|---|---|
| Backend | Python 3.12, FastAPI, Uvicorn, Pydantic, SQLAlchemy 2, Alembic |
| Database | PostgreSQL 16 (AWS RDS or Supabase); SQLite locally |
| Files | AWS S3 (SSE-KMS, versioned) or local disk |
| Auth | bcrypt, JWT in HttpOnly cookies, TOTP, Microsoft Entra ID (OpenID Connect) |
| Frontend | Single-page app in plain JavaScript + Tailwind (`static/`), no build step |
| Text extraction | pypdf, pypdfium2 + Tesseract OCR, openpyxl |
| Deployment | Docker Compose, Caddy, AWS (EC2, RDS, S3, KMS, Secrets Manager, SSM, CloudTrail) |

## Quick start

```bash
cp .env.example .env                # set SECRET_KEY (32+ chars), MASTER_ADMIN_EMAIL, MASTER_ADMIN_PASSWORD
docker compose up -d --build        # app + ClamAV; add `--profile search` for the search engine
```

Open http://localhost:8000 and sign in as the Master Admin. API documentation: http://localhost:8000/docs

Without Docker:

```bash
python3.12 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt
APP_ENV=development MALWARE_SCAN=off uvicorn app.main:app --reload
```

The app refuses to start with a default or short `SECRET_KEY` or the default `MASTER_ADMIN_PASSWORD` unless
`APP_ENV=development`. The database schema is created and upgraded automatically on start.

## Configuration

Everything lives in `.env`; [.env.example](.env.example) documents every setting.

| To… | Set |
|---|---|
| Use PostgreSQL | `DATABASE_URL=postgresql://user:pw@host:5432/db?sslmode=require` |
| Store files in S3 | `STORAGE_BACKEND=s3`, `S3_BUCKET`, `S3_REGION` (+ `S3_KMS_KEY_ID` for SSE-KMS); keys optional with an IAM role |
| Scan uploads for viruses | `MALWARE_SCAN=clamav` (the `clamav` service); uploads are refused while the scanner is down |
| Send email | `SMTP_*`, `MAIL_FROM` (e.g. Amazon SES); empty = emails recorded as `logged` in the Email monitor |
| Alert admins about activity | `ALERT_SMTP_*`, `ALERT_RECIPIENTS` — see [ADMIN_ALERTS.md](ADMIN_ALERTS.md) |
| Require two-step verification for admins | `MFA_REQUIRED_FOR_ADMINS=true` (default) |
| Sign in with Microsoft | `SSO_MICROSOFT_*` — set-up steps in [deploy/aws/README.md](deploy/aws/README.md#sign-in-with-microsoft-entra-id) |
| Search inside files | `SEARCH_BACKEND=engine`, `SEARCH_ENGINE_URL`, `SEARCH_ENGINE_TOKEN`; `OCR_*` for scanned files |
| Two-person rule | `TWO_PERSON_RULE=true`: Secondary Admins recommend, the Master Admin or designated approver approves |
| Read emailed access requests | `IMAP_HOST`, `IMAP_USER`, `IMAP_PASSWORD` |

## Security

- **Sign-in**: bcrypt passwords; session in an `HttpOnly; Secure; SameSite=Strict` cookie; cookie-authenticated changes
  require the `X-ONCM` header (CSRF); login and second-step rate limits.
- **Two-step verification**: TOTP with QR set-up and 10 one-time recovery codes; required for admins, optional for
  participants; a Master Admin can reset it for someone who lost their phone (audited).
- **Sessions end** on password or MFA change (`session_version`); invite links work once.
- **Authorisation** is enforced on the server for every request: roles, organisation isolation, document visibility,
  maker-checker, two-person rule, status transitions.
- **Files**: type, size (≤ 25 MB) and content checks, ClamAV scan, private bucket, SSE-KMS, signed short-lived links.
- **Browser**: Content-Security-Policy (scripts only from this site and the Tailwind CDN, data only to this site),
  HSTS, no framing, no MIME sniffing.
- **Audit log** of sign-ins, uploads, decisions, permission changes and downloads.

## Domain rules

**Document code**: `ONCM-<Category>-<Year>-V<Version>-<Serial>`; the serial runs per category and year.

**Categories**: CM Carbon Markets · POL Policies/Rules/Notifications · RES Research · PPD Pilot project documents ·
CDP Common documents for pilot · AGR Agreements with external stakeholders.

| Access level | Who can open it |
|---|---|
| Internal | Authorised ONCM team only; never published |
| Pilot shared | All authorised pilot participants |
| Role-specific | Selected stakeholder categories (Project Developer, VVB, Obligated Entity, ACVA, TSP) |
| Restricted | Named organisations and individuals |
| Public | Anyone, no login |
| Request-only | Only people whose request ONCM approved |

Allowed levels per category: **AGR** Internal or Restricted · **CDP** Internal or Pilot shared · **PPD** Internal,
Role-specific or Restricted · **CM / POL / RES** any level.

**Workflow**: `draft → submitted → under_review → changes_requested | internally_approved | rejected →
shared_externally (published) → superseded → archived`. Approval confirms the access level; publication can never be
broader than what was approved. The person who prepared a version can never review or approve it.

| Role | Can |
|---|---|
| **Master Admin** | Everything: onboarding decisions, admins and permissions, final approval, publication, overrides (audited), audit log |
| **Secondary Admin** | Assigned organisations and granted categories: upload, review, recommend; publish / approve access requests only with `can_share`; reports with `can_export` |
| **Participant** | Own organisation only: upload into four folders, replace documents when changes are requested, resources published to them, request access, questions |

## API

All endpoints are under `/api`; interactive documentation at `/docs`. The portal authenticates with the session cookie;
API clients may send `Authorization: Bearer <token>` (request one with `"return_token": true` at `/api/auth/login`).

| Area | Endpoints |
|---|---|
| Public | `GET /health` · `GET /meta` · `POST /join-requests` · `GET /public/resources` · `POST /public/access-requests` |
| Sign-in | `POST /auth/login` · `POST /auth/mfa` · `POST /auth/logout` · `GET /auth/me` · `POST /auth/change-password` · `POST /auth/accept-invite` · `POST /auth/mfa/setup\|enable\|disable\|recovery-codes` · `GET /auth/sso/microsoft/start` |
| Participant | `/me/dashboard` · `/me/documents` · `/me/resources?q=` · `/me/catalogue?q=` · `/me/access-requests` · `/me/questions` |
| Admin | `/admin/overview` · `/admin/organizations` · `/admin/documents?q=` (+ versions, assign, review, publish, grants, archive) · `/admin/access-requests` · `/admin/workstreams` · `/admin/tasks` · `/admin/changelog` · `/admin/notifications` · `/admin/reports/export` |
| Master | `/master/join-requests` · `/master/admins` · `/master/users/{id}/reset-mfa` · `/master/audit-logs` · `/master/documents/{id}/override` · alert settings |

## Database migrations

Alembic, in [migrations/](migrations/), applied automatically on start ([app/migrate.py](app/migrate.py)); databases
created before migrations existed are brought to the baseline and stamped. After changing [app/models.py](app/models.py):

```bash
alembic revision --autogenerate -m "describe the change"   # then review the generated file and commit it
```

## Tests

```bash
pip install -r requirements-dev.txt
pytest                                                       # unit, API, concurrency (fresh SQLite per run)
TEST_DATABASE_URL=postgresql://localhost/oncm_test pytest    # same suite on PostgreSQL (the database is wiped)
playwright install chromium && pytest -m e2e                 # browser tests
```

CI runs the suite on every push and pull request (`.github/workflows/tests.yml`).

## Deployment

AWS (EC2 + RDS + S3, HTTPS with Caddy, no SSH): see [deploy/aws/README.md](deploy/aws/README.md).
`deploy/aws/provision.sh` creates the infrastructure, `deploy/aws/deploy.sh` ships an update. Further scripts:
`harden-s3.sh` (KMS, HTTPS-only bucket, file-access audit trail), `security-services.sh` (patching, alerts),
`rotate-secrets.sh`, `set-microsoft-sso.sh`, `reset-mfa.sh`.

Backups: RDS automated backups plus a nightly `pg_dump` to S3 (30 days). Patching: weekly OS updates via SSM,
monthly image rebuild. TLS certificates renew automatically.

## Project structure

```
app/
  core.py          settings, database, passwords, sessions, role guards, startup checks
  models.py        database tables
  services.py      storage, upload validation, virus scan, versions, permissions, email queue
  routers/         public (sign-in, join form) · participant · admin · master · alerts
  mfa.py, sso.py   two-step verification, Sign in with Microsoft
  search.py        text extraction, OCR, search
  migrate.py       migrations on start
  alerts.py, excel.py
static/            index.html, app.js (frontend)
migrations/        Alembic revisions
deploy/            Caddyfile, production compose override, AWS scripts
supabase/          SQL schema for a Supabase project
tests/             pytest suite and Playwright browser tests
```

## Excel report

`ONCM_Workbench_Report_YYYY-MM-DD.xlsx`: Participant Tracker, Document Register, Repository Register, Change Log,
Workstreams, Approval History, Pending Tasks, Access Requests, Automation Report, Audit Report. Scope follows the role;
every export is logged.
