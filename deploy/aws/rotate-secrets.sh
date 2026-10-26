#!/usr/bin/env bash
# Replace every app secret (after a leak, or routinely): new SECRET_KEY (everyone is signed out), search token,
# database password (also changed on RDS), and Master Admin password (also set on the admin account). Then redeploys.
#   deploy/aws/rotate-secrets.sh
set -euo pipefail
cd "$(dirname "$0")"
# shellcheck disable=SC1091
source .state
umask 077
NEW=$(mktemp)
trap 'rm -f "$NEW"' EXIT
python3 -c 'import json, secrets
print(json.dumps({"SECRET_KEY": secrets.token_urlsafe(48), "SEARCH_ENGINE_TOKEN": secrets.token_urlsafe(40),
                  "DB_PASSWORD": secrets.token_urlsafe(32), "MASTER_ADMIN_PASSWORD": "Oncm!" + secrets.token_urlsafe(14)}))' > "$NEW"

echo "== database password (RDS)"
aws rds modify-db-instance --region "$REGION" --db-instance-identifier oncm-db --apply-immediately \
  --master-user-password "$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["DB_PASSWORD"])' "$NEW")" >/dev/null
sleep 30
aws rds wait db-instance-available --region "$REGION" --db-instance-identifier oncm-db

echo "== Secrets Manager"
aws secretsmanager put-secret-value --region "$REGION" --secret-id "$SECRET_ID" --secret-string "file://$NEW" >/dev/null

echo "== redeploy with the new secrets"
./deploy.sh

echo "== Master Admin password"
SCRIPT='docker exec oncm-workbench python -c "
from app.core import SessionLocal, hash_password, settings
from app.models import User
db = SessionLocal()
u = db.query(User).filter_by(role=\"master_admin\").order_by(User.id).first()
u.password_hash = hash_password(settings.master_admin_password)
db.commit()
print(\"master admin password reset for\", u.email)
"'
CMD=$(aws ssm send-command --region "$REGION" --instance-ids "$INSTANCE_ID" --document-name AWS-RunShellScript \
  --parameters "$(python3 -c 'import json,sys; print(json.dumps({"commands": [sys.argv[1]]}))' "$SCRIPT")" \
  --query Command.CommandId --output text)
sleep 15
aws ssm get-command-invocation --region "$REGION" --command-id "$CMD" --instance-id "$INSTANCE_ID" \
  --query StandardOutputContent --output text
echo "Done. New Master Admin password: Secrets Manager -> oncm/app -> MASTER_ADMIN_PASSWORD (do not paste it anywhere)."
