#!/usr/bin/env bash
# AWS-side security monitoring and patching (safe to re-run):
#  - Weekly OS security patches on the server: SSM Patch Manager (AWS-RunPatchBaseline), Sunday 02:00 IST,
#    reboots only when a patch needs it (containers come back automatically).
#  - GuardDuty: threat detection (compromised keys, unusual API calls, crypto-mining, known-bad IPs).
#  - Inspector: continuous vulnerability scanning of the EC2 server (OS packages).
#  - Email (ALERT_EMAIL) for GuardDuty findings of severity >= 7 and Inspector HIGH/CRITICAL findings.
#    AWS sends a confirmation email first: click "Confirm subscription".
set -euo pipefail
cd "$(dirname "$0")"
# shellcheck disable=SC1091
source .state
NAME=${NAME:-oncm}
ALERT_EMAIL=${ALERT_EMAIL:-$ADMIN_EMAIL}
aws() { command aws --region "$REGION" --output text "$@"; }
ACCOUNT=$(aws sts get-caller-identity --query Account)

echo "== weekly OS patching (SSM Patch Manager)"
if [[ $(aws ssm list-associations --association-filter-list key=AssociationName,value="$NAME-weekly-patch" --query 'length(Associations)') == 0 ]]; then
  aws ssm create-association --association-name "$NAME-weekly-patch" --name AWS-RunPatchBaseline \
    --targets "Key=InstanceIds,Values=$INSTANCE_ID" --schedule-expression "cron(30 20 ? * SAT *)" \
    --parameters '{"Operation":["Install"],"RebootOption":["RebootIfNeeded"]}' --compliance-severity HIGH >/dev/null
fi
echo "Sundays 02:00 IST"

echo "== GuardDuty"
DETECTOR=$(aws guardduty list-detectors --query 'DetectorIds[0]')
if [[ $DETECTOR == None || -z $DETECTOR ]]; then
  DETECTOR=$(aws guardduty create-detector --enable --finding-publishing-frequency FIFTEEN_MINUTES --query DetectorId)
fi
echo "detector $DETECTOR"

echo "== Inspector (EC2 scanning)"
aws inspector2 enable --resource-types EC2 --query 'accounts[0].status' || echo "(Inspector could not be enabled on this account plan)"

echo "== email alerts for serious findings"
TOPIC=$(aws sns create-topic --name "$NAME-security-alerts" --query TopicArn)
aws sns set-topic-attributes --topic-arn "$TOPIC" --attribute-name Policy --attribute-value "{\"Version\":\"2012-10-17\",\"Statement\":[
 {\"Effect\":\"Allow\",\"Principal\":{\"Service\":\"events.amazonaws.com\"},\"Action\":\"sns:Publish\",\"Resource\":\"$TOPIC\",
  \"Condition\":{\"StringEquals\":{\"aws:SourceAccount\":\"$ACCOUNT\"}}}]}"
if [[ $(aws sns list-subscriptions-by-topic --topic-arn "$TOPIC" --query "length(Subscriptions[?Endpoint=='$ALERT_EMAIL'])") == 0 ]]; then
  aws sns subscribe --topic-arn "$TOPIC" --protocol email --notification-endpoint "$ALERT_EMAIL" >/dev/null
  echo "confirmation email sent to $ALERT_EMAIL"
fi
aws events put-rule --name "$NAME-guardduty-high" --event-pattern \
  '{"source":["aws.guardduty"],"detail-type":["GuardDuty Finding"],"detail":{"severity":[{"numeric":[">=",7]}]}}' >/dev/null
aws events put-targets --rule "$NAME-guardduty-high" --targets "Id=sns,Arn=$TOPIC" >/dev/null
aws events put-rule --name "$NAME-inspector-high" --event-pattern \
  '{"source":["aws.inspector2"],"detail-type":["Inspector2 Finding"],"detail":{"severity":["HIGH","CRITICAL"],"status":["ACTIVE"]}}' >/dev/null
aws events put-targets --rule "$NAME-inspector-high" --targets "Id=sns,Arn=$TOPIC" >/dev/null
echo "alerts -> $ALERT_EMAIL"
