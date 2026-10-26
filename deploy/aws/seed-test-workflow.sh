#!/usr/bin/env bash
# Run seed_test_workflow.py on the live server: labelled TEST accounts, documents and decisions, with PASS/FAIL checks.
# Test-account passwords are generated into deploy/aws/test-accounts.local.json (git-ignored, only on this machine).
set -euo pipefail
cd "$(dirname "$0")"
# shellcheck disable=SC1091
source .state
ACC=test-accounts.local.json
if [[ ! -f $ACC ]]; then
  (umask 077; python3 -c 'import json, secrets
json.dump({k: {"email": e, "password": "Test!" + secrets.token_urlsafe(12)} for k, e in [
  ("admin2", "supreetpatil79+oncm-admin2@gmail.com"), ("pd", "supreetpatil79+oncmtest@gmail.com"),
  ("vvb", "supreetpatil79+oncm-vvb@gmail.com")]}, open("'"$ACC"'", "w"), indent=1)')
fi
aws s3 cp --quiet --region "$REGION" "${SEED_SCRIPT:-seed_test_workflow.py}" "s3://$BUCKET/deploy/seed/seed.py"
aws s3 cp --quiet --region "$REGION" "$ACC" "s3://$BUCKET/deploy/seed/accounts.json"
SCRIPT="set -e
aws s3 cp --quiet s3://$BUCKET/deploy/seed/seed.py /tmp/seed.py
aws s3 cp --quiet s3://$BUCKET/deploy/seed/accounts.json /tmp/accounts.json
aws s3 rm --quiet s3://$BUCKET/deploy/seed/accounts.json
docker cp /tmp/seed.py oncm-workbench:/tmp/seed.py
docker cp /tmp/accounts.json oncm-workbench:/tmp/accounts.json
rm -f /tmp/accounts.json /tmp/seed.py
set +e
docker exec -w /srv -e PYTHONPATH=/srv oncm-workbench python /tmp/seed.py; rc=\$?
docker exec -u root oncm-workbench rm -f /tmp/accounts.json /tmp/seed.py
exit \$rc"
CMD=$(aws ssm send-command --region "$REGION" --instance-ids "$INSTANCE_ID" --document-name AWS-RunShellScript --timeout-seconds 1800 \
  --parameters "$(python3 -c 'import json,sys; print(json.dumps({"commands": [sys.argv[1]], "executionTimeout": ["1800"]}))' "$SCRIPT")" \
  --query Command.CommandId --output text)
while :; do
  S=$(aws ssm get-command-invocation --region "$REGION" --command-id "$CMD" --instance-id "$INSTANCE_ID" --query Status --output text 2>/dev/null || echo Pending)
  case $S in Pending|InProgress|Delayed) sleep 10 ;; *) break ;; esac
done
aws ssm get-command-invocation --region "$REGION" --command-id "$CMD" --instance-id "$INSTANCE_ID" --query StandardOutputContent --output text
aws ssm get-command-invocation --region "$REGION" --command-id "$CMD" --instance-id "$INSTANCE_ID" --query StandardErrorContent --output text | tail -20
echo "Status: $S"
