#!/usr/bin/env bash
# Store the Microsoft (Entra ID) app registration in Secrets Manager (oncm/app) and redeploy. Run it yourself:
# it asks for the values; the client secret is typed hidden and never shown or written to disk.
#   deploy/aws/set-microsoft-sso.sh
set -euo pipefail
cd "$(dirname "$0")"
# shellcheck disable=SC1091
source .state
read -rp "Directory (tenant) ID: " TENANT
read -rp "Application (client) ID: " CLIENT
read -rsp "Client secret VALUE (hidden): " SECRET; echo
read -rp "Allowed email domains, comma-separated (e.g. iimb.ac.in; empty = anyone in the tenant): " DOMAINS
read -rp "Domains that MUST use Microsoft, password sign-in refused (empty = none): " REQUIRED
read -rp "Does Entra Conditional Access enforce MFA for this app? (yes/no): " TRUST
CUR=$(aws secretsmanager get-secret-value --region "$REGION" --secret-id "$SECRET_ID" --query SecretString --output text)
NEW=$(CUR="$CUR" TENANT="$TENANT" CLIENT="$CLIENT" SECRET="$SECRET" DOMAINS="$DOMAINS" REQUIRED="$REQUIRED" TRUST="$TRUST" python3 -c '
import json, os
s = json.loads(os.environ["CUR"])
s.update({"SSO_MICROSOFT_TENANT_ID": os.environ["TENANT"].strip(), "SSO_MICROSOFT_CLIENT_ID": os.environ["CLIENT"].strip(),
          "SSO_MICROSOFT_CLIENT_SECRET": os.environ["SECRET"].strip(), "SSO_MICROSOFT_DOMAINS": os.environ["DOMAINS"].strip(),
          "SSO_REQUIRED_DOMAINS": os.environ["REQUIRED"].strip(),
          "SSO_TRUST_MICROSOFT_MFA": "true" if os.environ["TRUST"].strip().lower().startswith("y") else ""})
print(json.dumps(s))')
umask 077; F=$(mktemp); trap 'rm -f "$F"' EXIT; printf '%s' "$NEW" > "$F"
aws secretsmanager put-secret-value --region "$REGION" --secret-id "$SECRET_ID" --secret-string "file://$F" >/dev/null
unset SECRET NEW CUR
echo "Stored in $SECRET_ID. Redeploying..."
./deploy.sh
echo "Check: https://${PUBLIC_IP//./-}.sslip.io shows 'Sign in with Microsoft' on the sign-in page."
