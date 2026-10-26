#!/usr/bin/env bash
# Ship the committed code of ONCM (this repo's HEAD) and adaptive-search-engine (ENGINE_DIR's HEAD) to the server and
# restart the stack. Uses S3 + SSM (no SSH, no GitHub access needed on the server). Run after provision.sh:
#   deploy/aws/deploy.sh
set -euo pipefail
cd "$(dirname "$0")"
# shellcheck disable=SC1091
source .state
REPO=$(git rev-parse --show-toplevel)
ENGINE_DIR=${ENGINE_DIR:-$REPO/../adaptive-search-engine}
aws() { command aws --region "$REGION" --output text "$@"; }

for d in "$REPO" "$ENGINE_DIR"; do
  if [[ -n $(git -C "$d" status --porcelain --untracked-files=no) ]]; then
    echo "Note: $d has uncommitted changes; only committed code is deployed." >&2
  fi
done
REL=$(date -u +%Y%m%d-%H%M%S)-$(git -C "$REPO" rev-parse --short HEAD)
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
git -C "$REPO" archive --format=tar.gz -o "$TMP/oncm.tar.gz" HEAD
git -C "$ENGINE_DIR" archive --format=tar.gz -o "$TMP/engine.tar.gz" HEAD
aws s3 cp --quiet "$TMP/oncm.tar.gz" "s3://$BUCKET/deploy/$REL/oncm.tar.gz"
aws s3 cp --quiet "$TMP/engine.tar.gz" "s3://$BUCKET/deploy/$REL/engine.tar.gz"
echo "Release $REL uploaded"

# The SSM agent registers a minute or two after first boot
for _ in $(seq 1 60); do
  [[ $(aws ssm describe-instance-information --filters Key=InstanceIds,Values="$INSTANCE_ID" \
        --query 'InstanceInformationList[0].PingStatus') == Online ]] && break
  sleep 10
done

SCRIPT="set -euo pipefail
R=/opt/oncm-releases/$REL; mkdir -p \$R/oncm \$R/engine
aws s3 cp --quiet s3://$BUCKET/deploy/$REL/oncm.tar.gz - | tar xz -C \$R/oncm
aws s3 cp --quiet s3://$BUCKET/deploy/$REL/engine.tar.gz - | tar xz -C \$R/engine
REGION=$REGION BUCKET=$BUCKET SECRET_ID=$SECRET_ID DB_HOST=$DB_HOST PUBLIC_IP=$PUBLIC_IP \
ADMIN_EMAIL=${ADMIN_EMAIL:-} SITE_ADDRESS=${SITE_ADDRESS:-} bash \$R/oncm/deploy/aws/server-deploy.sh \$R"
CMD_ID=$(aws ssm send-command --instance-ids "$INSTANCE_ID" --document-name AWS-RunShellScript \
  --timeout-seconds 3600 --comment "ONCM $REL" \
  --parameters "$(python3 -c 'import json,sys; print(json.dumps({"commands": [sys.argv[1]], "executionTimeout": ["3600"]}))' "$SCRIPT")" \
  --query Command.CommandId)
echo "Building and starting on $INSTANCE_ID (first time ~15 minutes)..."
while :; do
  STATUS=$(aws ssm get-command-invocation --command-id "$CMD_ID" --instance-id "$INSTANCE_ID" --query Status 2>/dev/null || echo Pending)
  case $STATUS in Pending|InProgress|Delayed) sleep 15 ;; *) break ;; esac
done
aws ssm get-command-invocation --command-id "$CMD_ID" --instance-id "$INSTANCE_ID" --query StandardOutputContent | tail -25
if [[ $STATUS != Success ]]; then
  aws ssm get-command-invocation --command-id "$CMD_ID" --instance-id "$INSTANCE_ID" --query StandardErrorContent | tail -40 >&2
  echo "Deploy $STATUS" >&2
  exit 1
fi
