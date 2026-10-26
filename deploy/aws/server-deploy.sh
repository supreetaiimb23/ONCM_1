#!/usr/bin/env bash
# Runs ON the server (as root, via SSM from deploy.sh): writes .env from Secrets Manager, builds the images,
# (re)starts the stack and waits until HTTPS answers. Argument: release dir containing oncm/ and engine/.
set -euo pipefail
R=$1
for _ in $(seq 1 90); do [[ -f /var/lib/oncm-init-done ]] && break; sleep 10; done  # first boot: Docker install
[[ -f /var/lib/oncm-init-done ]] || { echo "server-init.sh did not finish (see /var/log/cloud-init-output.log)"; exit 1; }

SITE=${SITE_ADDRESS:-${PUBLIC_IP//./-}.sslip.io}
umask 077
aws --region "$REGION" secretsmanager get-secret-value --secret-id "$SECRET_ID" --query SecretString --output text \
| python3 - "$R/oncm/.env" <<PY
import json, sys, urllib.parse
s = json.load(sys.stdin)
env = {
    "APP_ENV": "production",
    "APP_BASE_URL": "https://$SITE",
    "SITE_ADDRESS": "$SITE",
    "SECRET_KEY": s["SECRET_KEY"],
    "MASTER_ADMIN_EMAIL": "${ADMIN_EMAIL:-admin@example.org}",
    "MASTER_ADMIN_PASSWORD": s["MASTER_ADMIN_PASSWORD"],
    "DATABASE_URL": "postgresql://oncm:%s@$DB_HOST:5432/oncm?sslmode=require" % urllib.parse.quote(s["DB_PASSWORD"], safe=""),
    "STORAGE_BACKEND": "s3", "S3_BUCKET": "$BUCKET", "S3_REGION": "$REGION",  # credentials: the server's IAM role
    "MALWARE_SCAN": "clamav", "CLAMAV_HOST": "clamav",
    "SEARCH_BACKEND": "engine", "SEARCH_ENGINE_URL": "http://search:8080", "SEARCH_ENGINE_TOKEN": s["SEARCH_ENGINE_TOKEN"],
    "SEARCH_ENGINE_DIR": "$R/engine",
}
with open(sys.argv[1], "w") as f:
    f.writelines(f"{k}={v}\n" for k, v in env.items())
PY

echo "== building images"
docker build -q -t oncm-workbench:latest "$R/oncm"
docker build -q -t adaptive-search:latest "$R/engine"

echo "== starting"
cd "$R/oncm"
docker compose -p oncm -f docker-compose.yml -f deploy/docker-compose.prod.yml --profile search up -d --remove-orphans

echo "== waiting for https://$SITE"
for _ in $(seq 1 60); do
  if curl -fsS --max-time 5 --resolve "$SITE:443:127.0.0.1" "https://$SITE/api/health"; then
    echo; ln -sfn "$R" /opt/oncm-current
    ls -1dt /opt/oncm-releases/* | tail -n +4 | xargs -r rm -rf   # keep the last 3 releases
    docker image prune -f >/dev/null
    docker compose -p oncm ps --format '{{.Service}}: {{.Status}}'
    echo "LIVE: https://$SITE"
    exit 0
  fi
  sleep 10
done
docker compose -p oncm logs --tail 40
echo "HTTPS did not come up"; exit 1
