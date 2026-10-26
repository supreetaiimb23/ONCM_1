#!/usr/bin/env bash
# Create the ONCM pilot stack in one AWS region, then deploy the code (deploy.sh). Safe to re-run: what exists is
# reused. IDs are kept in deploy/aws/.state (not committed). Run from the repo root with an IAM user, never root:
#   REGION=ap-south-1 deploy/aws/provision.sh
#
# Creates: private versioned S3 bucket (files, DB dumps, deploy bundles) · security groups (web: 80/443; db: 5432 from
# web only) · IAM role for the server (that bucket, its secrets, SSM) · Secrets Manager secret with generated app
# secrets · RDS PostgreSQL db.t4g.micro (encrypted, 7-day backups, not public, deletion protection) · EC2 t4g.medium
# Amazon Linux 2023 (no SSH: managed through SSM) + Elastic IP · monthly AWS Budget with email alerts.
set -euo pipefail
cd "$(dirname "$0")"
REGION=${REGION:-ap-south-1}
NAME=${NAME:-oncm}
INSTANCE_TYPE=${INSTANCE_TYPE:-c7i-flex.large}  # 4 GB x86_64; Free plan accounts only allow a few types
BUDGET_USD=${BUDGET_USD:-85}
BACKUP_DAYS=${BACKUP_DAYS:-7}  # RDS automated backups; AWS Free plan accounts allow only 1 (nightly S3 dumps add 30 days)
ALERT_EMAIL=${ALERT_EMAIL:?set ALERT_EMAIL (budget alerts go there)}
ADMIN_EMAIL=${ADMIN_EMAIL:-$ALERT_EMAIL}  # the first Master Admin sign-in
STATE=.state
touch "$STATE"
# shellcheck disable=SC1090
source "$STATE"
save() { printf '%s=%q\n' "$1" "$2" >> "$STATE"; printf -v "$1" '%s' "$2"; }
aws() { command aws --region "$REGION" --output text "$@"; }
step() { printf '\n== %s\n' "$*"; }

ARN=$(aws sts get-caller-identity --query Arn)
ACCOUNT=$(aws sts get-caller-identity --query Account)
if [[ $ARN == *":root" && ${ALLOW_ROOT:-} != 1 ]]; then
  echo "Signed in as the AWS root user. Create an IAM user and run 'aws configure' with it first" >&2
  echo "(or ALLOW_ROOT=1 to proceed anyway, then delete the root access keys afterwards)." >&2
  exit 1
fi
echo "Account $ACCOUNT as $ARN, region $REGION"

step "Network (default VPC)"
VPC_ID=$(aws ec2 describe-vpcs --filters Name=isDefault,Values=true --query 'Vpcs[0].VpcId')
[[ $VPC_ID == None ]] && { echo "No default VPC in $REGION" >&2; exit 1; }
SUBNETS=$(aws ec2 describe-subnets --filters Name=vpc-id,Values="$VPC_ID" Name=default-for-az,Values=true --query 'Subnets[].SubnetId')
SUBNET_ID=$(echo "$SUBNETS" | awk '{print $1}')

step "S3 bucket"
BUCKET=${BUCKET:-$NAME-$ACCOUNT-$REGION}
if ! command aws s3api head-bucket --bucket "$BUCKET" 2>/dev/null; then
  aws s3api create-bucket --bucket "$BUCKET" --create-bucket-configuration LocationConstraint="$REGION" >/dev/null
fi
aws s3api put-public-access-block --bucket "$BUCKET" --public-access-block-configuration \
  BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true
aws s3api put-bucket-versioning --bucket "$BUCKET" --versioning-configuration Status=Enabled
aws s3api put-bucket-encryption --bucket "$BUCKET" --server-side-encryption-configuration \
  '{"Rules":[{"ApplyServerSideEncryptionByDefault":{"SSEAlgorithm":"AES256"}}]}'
aws s3api put-bucket-lifecycle-configuration --bucket "$BUCKET" --lifecycle-configuration '{"Rules":[
  {"ID":"old-versions","Status":"Enabled","Filter":{},"NoncurrentVersionExpiration":{"NoncurrentDays":90}},
  {"ID":"deploy-bundles","Status":"Enabled","Filter":{"Prefix":"deploy/"},"Expiration":{"Days":30}},
  {"ID":"db-backups","Status":"Enabled","Filter":{"Prefix":"backups/"},"Expiration":{"Days":30}}]}'
echo "$BUCKET"

step "Security groups"
if [[ -z ${SG_WEB:-} ]]; then
  save SG_WEB "$(aws ec2 create-security-group --group-name "$NAME-web" --description "ONCM web (HTTPS)" --vpc-id "$VPC_ID" --query GroupId)"
  aws ec2 authorize-security-group-ingress --group-id "$SG_WEB" --ip-permissions \
    'IpProtocol=tcp,FromPort=80,ToPort=80,IpRanges=[{CidrIp=0.0.0.0/0}],Ipv6Ranges=[{CidrIpv6=::/0}]' \
    'IpProtocol=tcp,FromPort=443,ToPort=443,IpRanges=[{CidrIp=0.0.0.0/0}],Ipv6Ranges=[{CidrIpv6=::/0}]' >/dev/null
fi
if [[ -z ${SG_DB:-} ]]; then
  save SG_DB "$(aws ec2 create-security-group --group-name "$NAME-db" --description "ONCM database (from web only)" --vpc-id "$VPC_ID" --query GroupId)"
  aws ec2 authorize-security-group-ingress --group-id "$SG_DB" --protocol tcp --port 5432 --source-group "$SG_WEB" >/dev/null
fi
echo "web $SG_WEB, db $SG_DB"

step "Secrets"
SECRET_ID=$NAME/app
if ! aws secretsmanager describe-secret --secret-id "$SECRET_ID" >/dev/null 2>&1; then
  # Generated here, never printed. MASTER_ADMIN_PASSWORD is read once in the console (see README) and then changed.
  SECRET_FILE=$(mktemp)  # private temp file: secrets never appear on a command line
  chmod 600 "$SECRET_FILE"
  python3 -c 'import json, secrets
print(json.dumps({"SECRET_KEY": secrets.token_urlsafe(48), "SEARCH_ENGINE_TOKEN": secrets.token_urlsafe(40),
                  "DB_PASSWORD": secrets.token_urlsafe(32), "MASTER_ADMIN_PASSWORD": "Oncm!" + secrets.token_urlsafe(14)}))' > "$SECRET_FILE"
  aws secretsmanager create-secret --name "$SECRET_ID" --description "ONCM app secrets" --secret-string "file://$SECRET_FILE" >/dev/null
  rm -f "$SECRET_FILE"
fi
SECRET_ARN=$(aws secretsmanager describe-secret --secret-id "$SECRET_ID" --query ARN)
echo "$SECRET_ID"

step "IAM role for the server"
ROLE=$NAME-server
if ! aws iam get-role --role-name "$ROLE" >/dev/null 2>&1; then
  aws iam create-role --role-name "$ROLE" --assume-role-policy-document \
    '{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"ec2.amazonaws.com"},"Action":"sts:AssumeRole"}]}' >/dev/null
  aws iam attach-role-policy --role-name "$ROLE" --policy-arn arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore
  aws iam create-instance-profile --instance-profile-name "$ROLE" >/dev/null
  aws iam add-role-to-instance-profile --instance-profile-name "$ROLE" --role-name "$ROLE"
  sleep 10  # IAM is eventually consistent; run-instances may not see a brand-new profile yet
fi
aws iam put-role-policy --role-name "$ROLE" --policy-name "$NAME-app" --policy-document "{
  \"Version\":\"2012-10-17\",\"Statement\":[
   {\"Effect\":\"Allow\",\"Action\":[\"s3:ListBucket\"],\"Resource\":\"arn:aws:s3:::$BUCKET\"},
   {\"Effect\":\"Allow\",\"Action\":[\"s3:GetObject\",\"s3:PutObject\",\"s3:DeleteObject\"],\"Resource\":\"arn:aws:s3:::$BUCKET/*\"},
   {\"Effect\":\"Allow\",\"Action\":[\"secretsmanager:GetSecretValue\"],\"Resource\":\"$SECRET_ARN\"}]}"
echo "$ROLE"

step "RDS PostgreSQL (takes ~10 minutes the first time)"
DB_ID=$NAME-db
if ! aws rds describe-db-instances --db-instance-identifier "$DB_ID" >/dev/null 2>&1; then
  aws rds create-db-subnet-group --db-subnet-group-name "$NAME-db" --db-subnet-group-description "ONCM" \
    --subnet-ids $SUBNETS >/dev/null 2>&1 || true
  DB_PASSWORD=$(aws secretsmanager get-secret-value --secret-id "$SECRET_ID" --query SecretString \
                | python3 -c 'import json,sys; print(json.load(sys.stdin)["DB_PASSWORD"])')
  aws rds create-db-instance --db-instance-identifier "$DB_ID" --engine postgres --engine-version 16 \
    --db-instance-class db.t4g.micro --allocated-storage 20 --storage-type gp3 --storage-encrypted \
    --db-name oncm --master-username oncm --master-user-password "$DB_PASSWORD" \
    --vpc-security-group-ids "$SG_DB" --db-subnet-group-name "$NAME-db" --no-publicly-accessible \
    --backup-retention-period "$BACKUP_DAYS" --deletion-protection --copy-tags-to-snapshot --no-multi-az \
    --auto-minor-version-upgrade >/dev/null
  unset DB_PASSWORD
fi
aws rds wait db-instance-available --db-instance-identifier "$DB_ID"
DB_HOST=$(aws rds describe-db-instances --db-instance-identifier "$DB_ID" --query 'DBInstances[0].Endpoint.Address')
echo "$DB_HOST"

step "EC2 server"
if [[ -z ${INSTANCE_ID:-} ]]; then
  ARCH=$(aws ec2 describe-instance-types --instance-types "$INSTANCE_TYPE" --query 'InstanceTypes[0].ProcessorInfo.SupportedArchitectures[0]')
  AMI=$(aws ssm get-parameter --name "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-$ARCH" --query Parameter.Value)
  # assigned first (not inside save's arguments) so a failure stops the script under set -e
  NEW_ID=$(aws ec2 run-instances --image-id "$AMI" --instance-type "$INSTANCE_TYPE" \
    --subnet-id "$SUBNET_ID" --security-group-ids "$SG_WEB" --iam-instance-profile Name="$ROLE" \
    --metadata-options HttpTokens=required,HttpEndpoint=enabled \
    --block-device-mappings '[{"DeviceName":"/dev/xvda","Ebs":{"VolumeSize":30,"VolumeType":"gp3","Encrypted":true,"DeleteOnTermination":true}}]' \
    --tag-specifications "[{\"ResourceType\":\"instance\",\"Tags\":[{\"Key\":\"Name\",\"Value\":\"$NAME\"}]}]" \
    --user-data file://server-init.sh --query 'Instances[0].InstanceId')
  save INSTANCE_ID "$NEW_ID"
fi
aws ec2 wait instance-running --instance-ids "$INSTANCE_ID"
if [[ -z ${EIP_ALLOC:-} ]]; then
  NEW_EIP=$(aws ec2 allocate-address --domain vpc --query AllocationId)
  save EIP_ALLOC "$NEW_EIP"
fi
aws ec2 associate-address --instance-id "$INSTANCE_ID" --allocation-id "$EIP_ALLOC" >/dev/null
PUBLIC_IP=$(aws ec2 describe-addresses --allocation-ids "$EIP_ALLOC" --query 'Addresses[0].PublicIp')
echo "$INSTANCE_ID at $PUBLIC_IP"

step "Monthly budget alert"
if ! aws budgets describe-budget --account-id "$ACCOUNT" --budget-name "$NAME-monthly" >/dev/null 2>&1; then
  aws budgets create-budget --account-id "$ACCOUNT" \
    --budget "{\"BudgetName\":\"$NAME-monthly\",\"BudgetLimit\":{\"Amount\":\"$BUDGET_USD\",\"Unit\":\"USD\"},\"TimeUnit\":\"MONTHLY\",\"BudgetType\":\"COST\"}" \
    --notifications-with-subscribers \
      "[{\"Notification\":{\"NotificationType\":\"ACTUAL\",\"ComparisonOperator\":\"GREATER_THAN\",\"Threshold\":50,\"ThresholdType\":\"PERCENTAGE\"},\"Subscribers\":[{\"SubscriptionType\":\"EMAIL\",\"Address\":\"$ALERT_EMAIL\"}]},
        {\"Notification\":{\"NotificationType\":\"ACTUAL\",\"ComparisonOperator\":\"GREATER_THAN\",\"Threshold\":100,\"ThresholdType\":\"PERCENTAGE\"},\"Subscribers\":[{\"SubscriptionType\":\"EMAIL\",\"Address\":\"$ALERT_EMAIL\"}]},
        {\"Notification\":{\"NotificationType\":\"FORECASTED\",\"ComparisonOperator\":\"GREATER_THAN\",\"Threshold\":100,\"ThresholdType\":\"PERCENTAGE\"},\"Subscribers\":[{\"SubscriptionType\":\"EMAIL\",\"Address\":\"$ALERT_EMAIL\"}]}]"
fi
echo "\$$BUDGET_USD/month -> $ALERT_EMAIL"

save_config() {
  grep -v -E '^(REGION|BUCKET|DB_HOST|PUBLIC_IP|SECRET_ID|ADMIN_EMAIL)=' "$STATE" > "$STATE.tmp" || true
  { cat "$STATE.tmp"; printf 'REGION=%q\nBUCKET=%q\nDB_HOST=%q\nPUBLIC_IP=%q\nSECRET_ID=%q\nADMIN_EMAIL=%q\n' \
      "$REGION" "$BUCKET" "$DB_HOST" "$PUBLIC_IP" "$SECRET_ID" "$ADMIN_EMAIL"; } > "$STATE"
  rm -f "$STATE.tmp"
}
save_config

step "Deploying the code"
./deploy.sh
