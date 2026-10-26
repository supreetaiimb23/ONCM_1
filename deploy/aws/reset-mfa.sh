#!/usr/bin/env bash
# Break-glass: turn off two-step verification for one account when no other Master Admin can do it in the portal
# (the only Master Admin lost both phone and recovery codes). Needs AWS access; recorded in the audit log.
#   deploy/aws/reset-mfa.sh someone@example.org "reason"
set -euo pipefail
cd "$(dirname "$0")"
# shellcheck disable=SC1091
source .state
EMAIL=${1:?email}; REASON=${2:?reason}
PY="from app.core import SessionLocal
from app.models import User
from app.services import audit
db = SessionLocal(); u = db.query(User).filter(User.email == '$EMAIL'.lower()).one()
u.mfa_enabled, u.mfa_secret, u.mfa_recovery, u.mfa_last_step = False, None, None, None
u.session_version = (u.session_version or 0) + 1
audit(db, None, 'auth.mfa_reset_breakglass', 'user', u.id, {'email': u.email, 'reason': '''$REASON'''})
db.commit(); print('two-step verification reset for', u.email)"
CMD=$(aws ssm send-command --region "$REGION" --instance-ids "$INSTANCE_ID" --document-name AWS-RunShellScript \
  --parameters "$(python3 -c 'import base64,json,sys; b=base64.b64encode(sys.argv[1].encode()).decode()
print(json.dumps({"commands": ["echo " + b + " | base64 -d | docker exec -i -w /srv -e PYTHONPATH=/srv oncm-workbench python -"]}))' "$PY")" \
  --query Command.CommandId --output text)
sleep 8
aws ssm get-command-invocation --region "$REGION" --command-id "$CMD" --instance-id "$INSTANCE_ID" --query '[StandardOutputContent,StandardErrorContent]' --output text
