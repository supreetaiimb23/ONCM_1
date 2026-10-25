# Admin Alerts

A second, dedicated email sender that keeps your admins informed. When something happens in the Workbench (an organisation asks to join, a request is decided, a document moves forward, a participant asks a question), the alert sender emails a managed list of admin mailboxes.

```
 Workbench event ──► alert queue (after DB commit) ──► alert SMTP sender ──► recipient 1
                                                       alerts@example.org                  recipient 2
                                                                                           recipient N …
```

The normal `SMTP_*` sender still talks to participants (invites, decisions, reminders). The alert sender talks only to your admins, so the two never get mixed up.

## Features

### 1. Separate sender account
- Configured with its own `ALERT_SMTP_*` settings, independent of the normal `SMTP_*` settings.
- Works with Gmail (port 587 STARTTLS or 465 SSL) and any other SMTP provider.
- Google shows App Passwords with spaces; they are accepted as pasted.
- If the sender is not configured, alerts are recorded as `logged` (nothing is lost, nothing fails).

### 2. Any number of recipients, managed live
- Starts with the addresses in `ALERT_RECIPIENTS` (loaded **once**, on first start).
- After that the Master Admin adds, edits, pauses and removes recipients on the **Admin alerts** page. No restart or code change.
- Each recipient chooses what they receive (see categories below), or "all updates".
- A recipient can be paused without being deleted.
- Every change to the list is written to the audit log.

### 3. What admins are told

| Category | What triggers an alert |
|---|---|
| **Join requests and new organisations** | **New organisation wants to join** (organisation, actor type, contact, intended role, supporting information, *possible duplicate* warning) · **Organisation joined** (approved, with who it is assigned to) · Join request rejected · More information requested from the applicant · The organisation's contact activated their account |
| **Organisation progress, status and members** | Stage change (Joined → Documents in progress → Agreement signed → Implementing → **Live**) showing old and new value and who changed it · Access suspended or restored · ONCM contact reassigned · New team member added |
| **Documents, reviews and publication** | Document submitted or resubmitted · Moved into review · Recommended / approved / returned with changes / rejected · Superseded · **Published** (one summary alert: code, access level, number of recipients) |
| **Access requests** | New request (portal or public form) · Decision (approved or rejected, by whom, valid until) |
| **Questions from participants** | The full question, with the sender's organisation and actor type |
| **Reminders** | Deadline reminders, overdue-review reminders, missing-document reminders |
| **Everything else** | Any event type added in future |

Because alerts are hooked into the existing `notify()` function, **every other email the Workbench sends is also copied** to recipients who subscribed to its category. New email types added later reach your admins automatically.

### 4. Safe by design
- **Secure links are never copied.** Password-setup (invite) links and secure download links go only to the applicant. Those emails are not mirrored, and any token link in a copied body is replaced with `[secure link removed]`.
- **No noise.** Applicant acknowledgements and the per-organisation "document shared" emails are not copied (a single summary alert is sent instead).
- **No false alarms.** Alerts are sent only after the database transaction commits, so a failed or rolled-back action never emails anyone.
- **No duplicates.** Each alert has an idempotency key per recipient.
- **No header injection.** Organisation names and other user-supplied text cannot break the email headers.
- **Secrets stay in `.env`.** The SMTP password is never stored in the database, returned by the API or shown on screen.
- **Master Admin only** can see or change recipients, test the sender and read the alert log.

### 5. Reliable delivery and monitoring
- Failed sends are retried automatically (max 3 attempts) by the existing hourly worker, and manually with **Retry**.
- **Send test** on each recipient sends a real email right now and shows the real result, with a plain-English hint for the common Gmail mistake (using the normal password instead of an App Password).
- The **Admin alerts** page shows the sender status (Ready / Not configured), recipients with what each receives, and a filterable log of recent alerts (status, attempts, last error).
- `/api/health` reports `"alerts": "smtp"` or `"log-only"`.

## Set-up (about 5 minutes)

1. **Create a Gmail App Password for the sender** (`alerts@example.org`): turn on 2-Step Verification, then Google Account → Security → App passwords → create one. Copy the 16-character code.
2. **Edit `.env`:**
   ```
   ALERT_SMTP_HOST=smtp.gmail.com
   ALERT_SMTP_PORT=587
   ALERT_SMTP_USER=alerts@example.org
   ALERT_SMTP_PASSWORD=abcd efgh ijkl mnop
   ALERT_RECIPIENTS=admin1@example.org,admin2@example.org
   ```
3. **Restart:** `docker compose up -d --build`. The two new tables (`alert_recipients`, `alert_log`) are created automatically; existing tables are not changed.
4. Sign in as Master Admin → **Admin alerts** → **Send test** next to each recipient.
5. Add more recipients any time with **Add recipient**.

## API (Master Admin, Bearer token)

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/master/alert-settings` | Sender address, ready/not configured, categories (never the password) |
| GET | `/api/master/alert-recipients` | List recipients |
| POST | `/api/master/alert-recipients` | Add `{email, name?, categories?: [..] \| null, is_active?}` (`null` = all) |
| PATCH | `/api/master/alert-recipients/{id}` | Change name, categories (`all_categories: true` resets), pause/resume |
| DELETE | `/api/master/alert-recipients/{id}` | Remove |
| POST | `/api/master/alert-recipients/{id}/test` | Send a real test email now |
| GET | `/api/master/alert-log?status=&category=` | Recent alerts (no bodies) |
| POST | `/api/master/alert-log/{id}/retry` | Re-send a failed or logged alert |

Category keys: `joins`, `organisations`, `documents`, `access_requests`, `support`, `reminders`, `other`.

## Things to know
- **Gmail limits.** A personal Gmail account can send roughly 500 emails a day. Alerts are one email per recipient per event, so with many recipients and busy periods consider choosing categories per recipient, or a transactional provider (Amazon SES, which you may already use for `SMTP_*`) by changing only `ALERT_SMTP_*`.
- **From address.** Gmail always sends as the signed-in account, so alerts arrive from `alerts@example.org`. First alerts may land in spam; mark them "Not spam".
- **Google Workspace accounts** can have App Passwords disabled by the domain admin.
- **Alerts contain organisation contact details**, so add only trusted ONCM staff as recipients.
- **Recipients are not re-seeded on restart.** `ALERT_RECIPIENTS` is read only when the recipient table is empty, so a recipient you removed on purpose does not come back.
- **Scaling.** Like the existing email queue, delivery uses an in-process thread pool and the hourly worker. With several app replicas, move both to a job queue (already noted in the README hardening checklist).

## What changed in the code

| File | Change |
|---|---|
| `app/alerts.py` | **New.** Categories, queueing, copy-of-email logic, link redaction, SMTP delivery, test send, first-start seeding |
| `app/routers/alerts.py` | **New.** Master-only recipient, test and log endpoints |
| `app/models.py` | Two **new** tables: `alert_recipients`, `alert_log` (no existing table altered) |
| `app/core.py` | `ALERT_*` settings |
| `app/services.py` | `notify()` also copies each email for subscribed admins; commit and rollback hooks also dispatch or discard alerts |
| `app/routers/public.py` | Alerts: join request submitted, contact activated; health shows alert status |
| `app/routers/master.py` | Alerts: join approved (organisation joined), rejected, more info |
| `app/routers/admin.py` | Alerts: organisation stage/status/contact changes, member added, document published, access-request decision |
| `app/main.py` | Registers the router, seeds recipients, retries failed alerts |
| `static/app.js` | New **Admin alerts** page (Master Admin) |
| `.env.example`, `README.md` | New settings and documentation |
