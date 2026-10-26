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
# Not available on AWS Free plan accounts ("needs a subscription"): skipped there, re-run after upgrading the plan.
if DETECTOR=$(aws guardduty list-detectors --query 'DetectorIds[0]' 2>/dev/null); then
  if [[ $DETECTOR == None || -z $DETECTOR ]]; then
    DETECTOR=$(aws guardduty create-detector --enable --finding-publishing-frequency FIFTEEN_MINUTES --query DetectorId)
  fi
  echo "detector $DETECTOR"
else
  echo "GuardDuty is not available on this account plan: skipped (re-run this script after upgrading)"
fi

echo "== Inspector (EC2 scanning)"
aws inspector2 enable --resource-types EC2 --query 'accounts[0].status' 2>/dev/null || echo "(Inspector could not be enabled on this account plan)"

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

echo "== account activity alerts (free: CloudTrail management events + EventBridge)"
# The oncm-files trail (harden-s3.sh) also records management events, so these rules see them.
BUCKET_ARN_PREFIX="arn:aws:s3:::$BUCKET"
aws cloudtrail put-event-selectors --trail-name "$NAME-files" --advanced-event-selectors "[
 {\"Name\":\"Account and API activity\",\"FieldSelectors\":[{\"Field\":\"eventCategory\",\"Equals\":[\"Management\"]}]},
 {\"Name\":\"Document files read and written\",\"FieldSelectors\":[
   {\"Field\":\"eventCategory\",\"Equals\":[\"Data\"]},{\"Field\":\"resources.type\",\"Equals\":[\"AWS::S3::Object\"]},
   {\"Field\":\"resources.ARN\",\"StartsWith\":[\"$BUCKET_ARN_PREFIX/organizations/\",\"$BUCKET_ARN_PREFIX/repository/\"]}]}]" >/dev/null
aws events put-rule --name "$NAME-root-signin" --event-pattern \
  '{"detail-type":["AWS Console Sign In via CloudTrail"],"detail":{"userIdentity":{"type":["Root"]}}}' >/dev/null
aws events put-targets --rule "$NAME-root-signin" --targets "Id=sns,Arn=$TOPIC" >/dev/null
aws events put-rule --name "$NAME-sensitive-changes" --event-pattern '{"detail-type":["AWS API Call via CloudTrail"],"detail":{"eventName":[
  "CreateUser","CreateAccessKey","AttachUserPolicy","PutUserPolicy","CreateLoginProfile","AttachRolePolicy","PutRolePolicy",
  "UpdateAssumeRolePolicy","DeleteTrail","StopLogging","UpdateTrail","PutEventSelectors","ScheduleKeyDeletion","DisableKey",
  "PutKeyPolicy","PutBucketPolicy","DeleteBucketPolicy","PutBucketPublicAccessBlock","DeletePublicAccessBlock",
  "AuthorizeSecurityGroupIngress","ModifyDBInstance","DeleteDBInstance"]}}' >/dev/null
aws events put-targets --rule "$NAME-sensitive-changes" --targets "Id=sns,Arn=$TOPIC" >/dev/null
echo "root sign-in and sensitive IAM/KMS/S3/firewall/database changes -> $ALERT_EMAIL"

echo "== global events (IAM, console sign-in) are delivered in us-east-1"
aws cloudtrail update-trail --name "$NAME-files" --is-multi-region-trail --include-global-service-events >/dev/null
GTOPIC=$(command aws --region us-east-1 --output text sns create-topic --name "$NAME-security-alerts" --query TopicArn)
command aws --region us-east-1 sns set-topic-attributes --topic-arn "$GTOPIC" --attribute-name Policy --attribute-value "{\"Version\":\"2012-10-17\",\"Statement\":[
 {\"Effect\":\"Allow\",\"Principal\":{\"Service\":\"events.amazonaws.com\"},\"Action\":\"sns:Publish\",\"Resource\":\"$GTOPIC\",
  \"Condition\":{\"StringEquals\":{\"aws:SourceAccount\":\"$ACCOUNT\"}}}]}"
if [[ $(command aws --region us-east-1 --output text sns list-subscriptions-by-topic --topic-arn "$GTOPIC" --query "length(Subscriptions[?Endpoint=='$ALERT_EMAIL'])") == 0 ]]; then
  command aws --region us-east-1 sns subscribe --topic-arn "$GTOPIC" --protocol email --notification-endpoint "$ALERT_EMAIL" >/dev/null
  echo "second confirmation email (us-east-1) sent to $ALERT_EMAIL"
fi
for RULE in root-signin sensitive-changes; do
  PATTERN=$(aws events describe-rule --name "$NAME-$RULE" --query EventPattern)
  command aws --region us-east-1 events put-rule --name "$NAME-$RULE" --event-pattern "$PATTERN" >/dev/null
  command aws --region us-east-1 events put-targets --rule "$NAME-$RULE" --targets "Id=sns,Arn=$GTOPIC" >/dev/null
done
echo "trail is multi-region; alert rules also in us-east-1"
