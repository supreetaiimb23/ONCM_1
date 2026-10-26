# AWS deployment (pilot)

One EC2 server (`c7i-flex.large`, 4 GB; Docker Compose: app, ClamAV, search engine, Caddy for HTTPS) + RDS PostgreSQL
(`db.t4g.micro`; automated backups 7 days, or 1 day on an AWS Free plan account: `BACKUP_DAYS=1`) plus a nightly
`pg_dump` to `s3://<bucket>/backups/` kept 30 days + private S3 bucket (files, versioned). No SSH: the server is managed through AWS
Systems Manager. Roughly $80–85/month in `ap-south-1` (Free plan accounts may only use a few instance types); an $85/month budget
emails alerts at 50% and 100%.

## 1. Credentials (once)
Never use the root user. In the AWS console: **IAM → Users → Create user** `oncm-deployer` → *Attach policies directly*
→ `AdministratorAccess` → create. Open the user → **Security credentials → Create access key → Command Line Interface**.
Then in your terminal (paste the keys there, not into chats or files):

```bash
aws configure
```

Region `ap-south-1`, output `json`. Check with `aws sts get-caller-identity` (the Arn must end in `user/oncm-deployer`),
then delete the root access keys: account menu → **Security credentials → Access keys**.

## 2. Create everything and deploy
Both repos side by side (`ONCM_1/`, `adaptive-search-engine/`), changes committed:

```bash
ALERT_EMAIL=you@example.org deploy/aws/provision.sh
```

About 20 minutes the first time (RDS ~10, image builds ~10). It ends with `LIVE: https://<ip>.sslip.io`.
The first Master Admin is `ALERT_EMAIL`; its password is in **Secrets Manager → `oncm/app` → Retrieve secret value →
`MASTER_ADMIN_PASSWORD`**. Sign in and change it right away.

## 3. Updates
Commit, then `deploy/aws/deploy.sh` (ships the committed code of both repos, rebuilds, restarts; ~5 minutes).

## Later
- **Own domain:** point an A record at the server's Elastic IP, then `SITE_ADDRESS=portal.example.org deploy/aws/deploy.sh`.
- **Email:** verify the sender domain in Amazon SES, request production access, create SES SMTP credentials and add
  `SMTP_*` / `MAIL_FROM` to the `oncm/app` flow (until then emails are recorded as `logged` in the Email monitor).
- **Logs:** EC2 → instance → *Connect → Session Manager*, then `cd /opt/oncm-current/oncm && sudo docker compose -p oncm logs`.
- **Restore a nightly dump:** `aws s3 cp s3://<bucket>/backups/<file>.sql.gz -` piped through `gunzip` into `psql`
  against an empty database (from the server, Session Manager).
- **Tear down:** RDS has deletion protection; disable it, then delete the RDS instance, EC2 instance, Elastic IP,
  security groups, IAM role, secret, budget and (after downloading anything you need) the S3 bucket.
