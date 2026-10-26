#!/usr/bin/env bash
# Harden the files bucket (safe to re-run):
#  1. customer-managed KMS key (yearly rotation) used as the bucket's default encryption (SSE-KMS + bucket key);
#     the key policy lets the server role encrypt/decrypt, account admins manage it.
#  2. bucket policy: refuse any request that is not HTTPS.
#  3. existing objects re-encrypted with the key.
#  4. CloudTrail trail with S3 data events: who read or wrote which document file, kept in a separate private bucket.
# Then redeploy (deploy.sh) so the app writes new files with the key (S3_KMS_KEY_ID).
set -euo pipefail
cd "$(dirname "$0")"
# shellcheck disable=SC1091
source .state
NAME=${NAME:-oncm}
aws() { command aws --region "$REGION" --output text "$@"; }
save() { grep -v "^$1=" .state > .state.tmp || true; printf '%s=%q\n' "$1" "$2" >> .state.tmp; mv .state.tmp .state; }
ACCOUNT=$(aws sts get-caller-identity --query Account)
ROLE_ARN=$(aws iam get-role --role-name "$NAME-server" --query Role.Arn)

echo "== 1. KMS key"
KEY_ARN=$(aws kms describe-key --key-id "alias/$NAME-files" --query KeyMetadata.Arn 2>/dev/null || true)
if [[ -z $KEY_ARN ]]; then
  POLICY=$(cat <<JSON
{"Version":"2012-10-17","Statement":[
 {"Sid":"AccountAdmins","Effect":"Allow","Principal":{"AWS":"arn:aws:iam::$ACCOUNT:root"},"Action":"kms:*","Resource":"*"},
 {"Sid":"ServerUsesKey","Effect":"Allow","Principal":{"AWS":"$ROLE_ARN"},
  "Action":["kms:Encrypt","kms:Decrypt","kms:ReEncrypt*","kms:GenerateDataKey*","kms:DescribeKey"],"Resource":"*"}]}
JSON
)
  KEY_ARN=$(aws kms create-key --description "ONCM document files (S3)" --policy "$POLICY" --query KeyMetadata.Arn)
  aws kms create-alias --alias-name "alias/$NAME-files" --target-key-id "$KEY_ARN"
fi
aws kms enable-key-rotation --key-id "$KEY_ARN"
save S3_KMS_KEY_ID "$KEY_ARN"
echo "$KEY_ARN"

echo "== 2. bucket default encryption + HTTPS-only policy"
aws s3api put-bucket-encryption --bucket "$BUCKET" --server-side-encryption-configuration \
  "{\"Rules\":[{\"ApplyServerSideEncryptionByDefault\":{\"SSEAlgorithm\":\"aws:kms\",\"KMSMasterKeyID\":\"$KEY_ARN\"},\"BucketKeyEnabled\":true}]}"
aws s3api put-bucket-policy --bucket "$BUCKET" --policy "{\"Version\":\"2012-10-17\",\"Statement\":[
 {\"Sid\":\"HttpsOnly\",\"Effect\":\"Deny\",\"Principal\":\"*\",\"Action\":\"s3:*\",
  \"Resource\":[\"arn:aws:s3:::$BUCKET\",\"arn:aws:s3:::$BUCKET/*\"],\"Condition\":{\"Bool\":{\"aws:SecureTransport\":\"false\"}}}]}"

echo "== 3. re-encrypt existing objects with the key"
command aws --region "$REGION" s3 cp "s3://$BUCKET/" "s3://$BUCKET/" --recursive --only-show-errors \
  --sse aws:kms --sse-kms-key-id "$KEY_ARN" --metadata-directive COPY
echo "done ($(command aws --region "$REGION" s3 ls "s3://$BUCKET/" --recursive | wc -l | tr -d ' ') objects)"

echo "== 4. CloudTrail: file access log"
AUDIT=$NAME-audit-$ACCOUNT-$REGION
if ! command aws s3api head-bucket --bucket "$AUDIT" 2>/dev/null; then
  aws s3api create-bucket --bucket "$AUDIT" --create-bucket-configuration LocationConstraint="$REGION" >/dev/null
fi
aws s3api put-public-access-block --bucket "$AUDIT" --public-access-block-configuration \
  BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true
aws s3api put-bucket-policy --bucket "$AUDIT" --policy "{\"Version\":\"2012-10-17\",\"Statement\":[
 {\"Sid\":\"AclCheck\",\"Effect\":\"Allow\",\"Principal\":{\"Service\":\"cloudtrail.amazonaws.com\"},\"Action\":\"s3:GetBucketAcl\",
  \"Resource\":\"arn:aws:s3:::$AUDIT\",\"Condition\":{\"StringEquals\":{\"aws:SourceArn\":\"arn:aws:cloudtrail:$REGION:$ACCOUNT:trail/$NAME-files\"}}},
 {\"Sid\":\"Write\",\"Effect\":\"Allow\",\"Principal\":{\"Service\":\"cloudtrail.amazonaws.com\"},\"Action\":\"s3:PutObject\",
  \"Resource\":\"arn:aws:s3:::$AUDIT/AWSLogs/$ACCOUNT/*\",\"Condition\":{\"StringEquals\":{\"s3:x-amz-acl\":\"bucket-owner-full-control\",
  \"aws:SourceArn\":\"arn:aws:cloudtrail:$REGION:$ACCOUNT:trail/$NAME-files\"}}},
 {\"Sid\":\"HttpsOnly\",\"Effect\":\"Deny\",\"Principal\":\"*\",\"Action\":\"s3:*\",
  \"Resource\":[\"arn:aws:s3:::$AUDIT\",\"arn:aws:s3:::$AUDIT/*\"],\"Condition\":{\"Bool\":{\"aws:SecureTransport\":\"false\"}}}]}"
aws s3api put-bucket-lifecycle-configuration --bucket "$AUDIT" --lifecycle-configuration \
  '{"Rules":[{"ID":"keep-1-year","Status":"Enabled","Filter":{},"Expiration":{"Days":365}}]}'
if ! aws cloudtrail get-trail --name "$NAME-files" >/dev/null 2>&1; then
  aws cloudtrail create-trail --name "$NAME-files" --s3-bucket-name "$AUDIT" --enable-log-file-validation >/dev/null
fi
aws cloudtrail put-event-selectors --trail-name "$NAME-files" --advanced-event-selectors "[
 {\"Name\":\"Document files read and written\",\"FieldSelectors\":[
   {\"Field\":\"eventCategory\",\"Equals\":[\"Data\"]},{\"Field\":\"resources.type\",\"Equals\":[\"AWS::S3::Object\"]},
   {\"Field\":\"resources.ARN\",\"StartsWith\":[\"arn:aws:s3:::$BUCKET/organizations/\",\"arn:aws:s3:::$BUCKET/repository/\"]}]}]" >/dev/null
aws cloudtrail start-logging --name "$NAME-files"
echo "trail $NAME-files -> s3://$AUDIT (kept 1 year)"

echo "== 5. redeploy so new uploads use the key"
./deploy.sh
