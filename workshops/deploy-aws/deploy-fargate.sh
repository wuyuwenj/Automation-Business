#!/usr/bin/env bash
# Deploy Bedrock seller and buyer agents to AWS Fargate with public ALB.
#
# Usage:
#   source .env   # or export vars manually
#   ./deploy-fargate.sh
#
# Required env vars:
#   SELLER_NVM_API_KEY, BUYER_NVM_API_KEY, NVM_PLAN_ID, NVM_AGENT_ID
#
# AWS credentials come from your AWS CLI config (aws configure).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SELLER_DIR="$SCRIPT_DIR/seller"
BUYER_DIR="$SCRIPT_DIR/buyer"

AWS_REGION="${AWS_REGION:-us-west-2}"
NVM_ENVIRONMENT="${NVM_ENVIRONMENT:-sandbox}"
CLUSTER_NAME="${CLUSTER_NAME:-nvm-agents}"
BEDROCK_MODEL_ID="${BEDROCK_MODEL_ID:-us.amazon.nova-pro-v1:0}"

SELLER_PORT=9000
BUYER_PORT=8000

# --- Colors ---
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BLUE='\033[0;34m'; NC='\033[0m'
info()  { echo -e "${BLUE}[INFO]${NC} $*"; }
ok()    { echo -e "${GREEN}[OK]${NC} $*"; }
err()   { echo -e "${RED}[ERROR]${NC} $*" >&2; }

# --- Load .env if present ---
if [[ -f "$SCRIPT_DIR/.env" ]]; then
    info "Loading .env"
    set -a; source "$SCRIPT_DIR/.env"; set +a
fi

# --- Prerequisites ---
for cmd in aws docker; do
    command -v $cmd &>/dev/null || { err "$cmd not found"; exit 1; }
done
aws sts get-caller-identity &>/dev/null || { err "AWS credentials not configured"; exit 1; }
docker info &>/dev/null 2>&1 || { err "Docker not running"; exit 1; }

# Get AWS credentials for task env vars
AWS_ACCESS_KEY_ID="${AWS_ACCESS_KEY_ID:-$(aws configure get aws_access_key_id 2>/dev/null || echo '')}"
AWS_SECRET_ACCESS_KEY="${AWS_SECRET_ACCESS_KEY:-$(aws configure get aws_secret_access_key 2>/dev/null || echo '')}"

for var in SELLER_NVM_API_KEY BUYER_NVM_API_KEY NVM_PLAN_ID NVM_AGENT_ID; do
    [[ -n "${!var:-}" ]] || { err "$var not set"; exit 1; }
done

ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text)"
info "Account: $ACCOUNT_ID | Region: $AWS_REGION"

# --- Infrastructure ---
VPC_ID="$(aws ec2 describe-vpcs --filters 'Name=isDefault,Values=true' --region "$AWS_REGION" --query 'Vpcs[0].VpcId' --output text)"
[[ "$VPC_ID" != "None" ]] || { err "No default VPC"; exit 1; }

SUBNETS=($(aws ec2 describe-subnets --filters "Name=vpc-id,Values=$VPC_ID" --region "$AWS_REGION" --query 'Subnets[?MapPublicIpOnLaunch==`true`].SubnetId' --output text))
[[ ${#SUBNETS[@]} -gt 0 ]] || { err "No public subnets"; exit 1; }
info "VPC: $VPC_ID | Subnets: ${SUBNETS[*]}"

# Security groups
ensure_sg() {
    local name="$1" port="$2"
    local sg_id
    sg_id=$(aws ec2 describe-security-groups --filters "Name=group-name,Values=$name" "Name=vpc-id,Values=$VPC_ID" --region "$AWS_REGION" --query 'SecurityGroups[0].GroupId' --output text 2>/dev/null || echo "None")
    if [[ "$sg_id" != "None" && -n "$sg_id" ]]; then echo "$sg_id"; return; fi
    sg_id=$(aws ec2 create-security-group --group-name "$name" --description "Allow port $port" --vpc-id "$VPC_ID" --region "$AWS_REGION" --query 'GroupId' --output text)
    aws ec2 authorize-security-group-ingress --group-id "$sg_id" --protocol tcp --port "$port" --cidr 0.0.0.0/0 --region "$AWS_REGION" &>/dev/null || true
    echo "$sg_id"
}

SELLER_SG="$(ensure_sg nvm-seller-sg $SELLER_PORT)"
BUYER_SG="$(ensure_sg nvm-buyer-sg $BUYER_PORT)"

# IAM role
if ! aws iam get-role --role-name ecsTaskExecutionRole &>/dev/null; then
    aws iam create-role --role-name ecsTaskExecutionRole \
        --assume-role-policy-document '{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"ecs-tasks.amazonaws.com"},"Action":"sts:AssumeRole"}]}'
    aws iam attach-role-policy --role-name ecsTaskExecutionRole --policy-arn arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy
fi
aws iam attach-role-policy --role-name ecsTaskExecutionRole --policy-arn arn:aws:iam::aws:policy/CloudWatchLogsFullAccess 2>/dev/null || true
EXEC_ROLE_ARN="$(aws iam get-role --role-name ecsTaskExecutionRole --query 'Role.Arn' --output text)"

# ECS cluster
aws ecs describe-clusters --clusters "$CLUSTER_NAME" --region "$AWS_REGION" --query 'clusters[?status==`ACTIVE`].clusterName' --output text | grep -q "$CLUSTER_NAME" \
    || aws ecs create-cluster --cluster-name "$CLUSTER_NAME" --region "$AWS_REGION" >/dev/null

# Log groups
aws logs create-log-group --log-group-name /ecs/nvm-seller-agent --region "$AWS_REGION" 2>/dev/null || true
aws logs create-log-group --log-group-name /ecs/nvm-buyer-agent --region "$AWS_REGION" 2>/dev/null || true

# --- ECR + Docker build ---
ecr_login() {
    aws ecr get-login-password --region "$AWS_REGION" | docker login --username AWS --password-stdin "$ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com"
}

build_push() {
    local dir="$1" repo="$2"
    aws ecr describe-repositories --repository-names "$repo" --region "$AWS_REGION" &>/dev/null \
        || aws ecr create-repository --repository-name "$repo" --region "$AWS_REGION" >/dev/null
    local uri="$ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com/$repo"
    info "Building $repo (linux/amd64)" >&2
    docker build --platform linux/amd64 -t "$repo" "$dir" >&2
    docker tag "$repo:latest" "$uri:latest"
    docker push "$uri:latest" >&2
    echo "$uri:latest"
}

ecr_login
SELLER_IMAGE="$(build_push "$SELLER_DIR" nvm-seller-agent)"
BUYER_IMAGE="$(build_push "$BUYER_DIR" nvm-buyer-agent)"
ok "Images pushed"

# --- ALBs ---
create_alb() {
    local name="$1" sg="$2" port="$3"
    local alb_arn dns
    alb_arn=$(aws elbv2 describe-load-balancers --names "$name" --region "$AWS_REGION" --query 'LoadBalancers[0].LoadBalancerArn' --output text 2>/dev/null || echo "None")
    if [[ "$alb_arn" != "None" && -n "$alb_arn" ]]; then
        dns=$(aws elbv2 describe-load-balancers --load-balancer-arns "$alb_arn" --region "$AWS_REGION" --query 'LoadBalancers[0].DNSName' --output text)
        echo "$alb_arn|$dns"
        return
    fi
    alb_arn=$(aws elbv2 create-load-balancer --name "$name" --subnets "${SUBNETS[@]}" --security-groups "$sg" --scheme internet-facing --type application --region "$AWS_REGION" --query 'LoadBalancers[0].LoadBalancerArn' --output text)
    local tg_arn
    tg_arn=$(aws elbv2 create-target-group --name "${name}-tg" --protocol HTTP --port "$port" --vpc-id "$VPC_ID" --target-type ip --health-check-path "/ping" --health-check-interval-seconds 30 --healthy-threshold-count 2 --region "$AWS_REGION" --query 'TargetGroups[0].TargetGroupArn' --output text)
    aws elbv2 create-listener --load-balancer-arn "$alb_arn" --protocol HTTP --port "$port" --default-actions "Type=forward,TargetGroupArn=$tg_arn" --region "$AWS_REGION" >/dev/null
    dns=$(aws elbv2 describe-load-balancers --load-balancer-arns "$alb_arn" --region "$AWS_REGION" --query 'LoadBalancers[0].DNSName' --output text)
    echo "$alb_arn|$dns"
}

SELLER_ALB="$(create_alb nvm-seller-alb "$SELLER_SG" $SELLER_PORT)"
SELLER_ALB_ARN="${SELLER_ALB%%|*}"
SELLER_DNS="${SELLER_ALB##*|}"
SELLER_TG_ARN="$(aws elbv2 describe-target-groups --load-balancer-arn "$SELLER_ALB_ARN" --region "$AWS_REGION" --query 'TargetGroups[0].TargetGroupArn' --output text)"

BUYER_ALB="$(create_alb nvm-buyer-alb "$BUYER_SG" $BUYER_PORT)"
BUYER_ALB_ARN="${BUYER_ALB%%|*}"
BUYER_DNS="${BUYER_ALB##*|}"
BUYER_TG_ARN="$(aws elbv2 describe-target-groups --load-balancer-arn "$BUYER_ALB_ARN" --region "$AWS_REGION" --query 'TargetGroups[0].TargetGroupArn' --output text)"

SELLER_URL="http://$SELLER_DNS:$SELLER_PORT"
BUYER_URL="http://$BUYER_DNS:$BUYER_PORT"

ok "Seller ALB: $SELLER_URL"
ok "Buyer ALB: $BUYER_URL"

# --- Task definitions ---
register_task() {
    local family="$1" image="$2" port="$3"
    shift 3
    local env_json="$*"

    cat > /tmp/${family}-task.json <<EOF
{
  "family": "$family",
  "networkMode": "awsvpc",
  "requiresCompatibilities": ["FARGATE"],
  "cpu": "512",
  "memory": "1024",
  "executionRoleArn": "$EXEC_ROLE_ARN",
  "containerDefinitions": [{
    "name": "$family",
    "image": "$image",
    "portMappings": [{"containerPort": $port, "protocol": "tcp"}],
    "environment": [$env_json],
    "logConfiguration": {
      "logDriver": "awslogs",
      "options": {
        "awslogs-group": "/ecs/$family",
        "awslogs-region": "$AWS_REGION",
        "awslogs-stream-prefix": "ecs",
        "awslogs-create-group": "true"
      }
    }
  }]
}
EOF
    aws ecs register-task-definition --cli-input-json "file:///tmp/${family}-task.json" --region "$AWS_REGION" --query 'taskDefinition.taskDefinitionArn' --output text
}

SELLER_ENV=$(cat <<ENVEOF
{"name":"NVM_API_KEY","value":"$SELLER_NVM_API_KEY"},
{"name":"NVM_ENVIRONMENT","value":"$NVM_ENVIRONMENT"},
{"name":"NVM_PLAN_ID","value":"$NVM_PLAN_ID"},
{"name":"NVM_AGENT_ID","value":"$NVM_AGENT_ID"},
{"name":"AWS_REGION","value":"$AWS_REGION"},
{"name":"AWS_ACCESS_KEY_ID","value":"$AWS_ACCESS_KEY_ID"},
{"name":"AWS_SECRET_ACCESS_KEY","value":"$AWS_SECRET_ACCESS_KEY"},
{"name":"PORT","value":"$SELLER_PORT"},
{"name":"BEDROCK_MODEL_ID","value":"$BEDROCK_MODEL_ID"}
ENVEOF
)

BUYER_ENV=$(cat <<ENVEOF
{"name":"NVM_API_KEY","value":"$BUYER_NVM_API_KEY"},
{"name":"NVM_ENVIRONMENT","value":"$NVM_ENVIRONMENT"},
{"name":"NVM_PLAN_ID","value":"$NVM_PLAN_ID"},
{"name":"NVM_AGENT_ID","value":"$NVM_AGENT_ID"},
{"name":"SELLER_A2A_URL","value":"$SELLER_URL"},
{"name":"AWS_REGION","value":"$AWS_REGION"},
{"name":"AWS_ACCESS_KEY_ID","value":"$AWS_ACCESS_KEY_ID"},
{"name":"AWS_SECRET_ACCESS_KEY","value":"$AWS_SECRET_ACCESS_KEY"},
{"name":"PORT","value":"$BUYER_PORT"},
{"name":"BEDROCK_MODEL_ID","value":"$BEDROCK_MODEL_ID"}
ENVEOF
)

register_task nvm-seller-agent "$SELLER_IMAGE" "$SELLER_PORT" "$SELLER_ENV"
register_task nvm-buyer-agent "$BUYER_IMAGE" "$BUYER_PORT" "$BUYER_ENV"

# --- Deploy services ---
deploy_svc() {
    local svc="$1" family="$2" sg="$3" tg_arn="$4" port="$5"
    local subnet_json=""
    for s in "${SUBNETS[@]}"; do
        [[ -n "$subnet_json" ]] && subnet_json+=","
        subnet_json+="\"$s\""
    done

    local status
    status=$(aws ecs describe-services --cluster "$CLUSTER_NAME" --services "$svc" --region "$AWS_REGION" --query 'services[0].status' --output text 2>/dev/null || echo "MISSING")

    if [[ "$status" == "ACTIVE" ]]; then
        info "Updating $svc"
        aws ecs update-service --cluster "$CLUSTER_NAME" --service "$svc" --task-definition "$family" --force-new-deployment --region "$AWS_REGION" >/dev/null
    else
        info "Creating $svc"
        aws ecs create-service \
            --cluster "$CLUSTER_NAME" --service-name "$svc" --task-definition "$family" \
            --desired-count 1 --launch-type FARGATE \
            --network-configuration "awsvpcConfiguration={subnets=[$subnet_json],securityGroups=[\"$sg\"],assignPublicIp=ENABLED}" \
            --load-balancers "targetGroupArn=$tg_arn,containerName=$family,containerPort=$port" \
            --region "$AWS_REGION" >/dev/null
    fi
    ok "$svc deployed"
}

deploy_svc nvm-seller-svc nvm-seller-agent "$SELLER_SG" "$SELLER_TG_ARN" "$SELLER_PORT"
deploy_svc nvm-buyer-svc nvm-buyer-agent "$BUYER_SG" "$BUYER_TG_ARN" "$BUYER_PORT"

# --- Summary ---
echo ""
echo "======================================"
echo "  Deployment Complete"
echo "======================================"
echo ""
echo "  Seller: $SELLER_URL"
echo "  Buyer:  $BUYER_URL"
echo "  Model:  $BEDROCK_MODEL_ID"
echo ""
echo "  Test seller: curl $SELLER_URL/.well-known/agent.json"
echo "  Test buyer:  curl $BUYER_URL/ping"
echo ""
echo "  Logs:"
echo "    aws logs tail /ecs/nvm-seller-agent --follow --region $AWS_REGION"
echo "    aws logs tail /ecs/nvm-buyer-agent --follow --region $AWS_REGION"
echo ""
