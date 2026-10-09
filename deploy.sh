#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="$ROOT_DIR/.env"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "Environment file $ENV_FILE not found. Copy .env.example and fill the values before deploying." >&2
  exit 1
fi

set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a

required_vars=(
  AZURE_SUBSCRIPTION_ID
  AZURE_LOCATION
  AZURE_RESOURCE_GROUP
  AZURE_CONTAINER_REGISTRY
  AZURE_CONTAINERAPPS_ENVIRONMENT
  AZURE_CONTAINERAPPS_BACKEND
  AZURE_CONTAINERAPPS_FRONTEND
  AZURE_OPENAI_API_KEY
)

for var in "${required_vars[@]}"; do
  if [[ -z "${!var:-}" ]]; then
    echo "Missing $var in .env" >&2
    exit 1
  fi
done

IMAGE_TAG="${IMAGE_TAG:-$(git -C "$ROOT_DIR" rev-parse --short HEAD 2>/dev/null || date -u +%Y%m%d%H%M%S)}"
REGISTRY_SERVER="${AZURE_CONTAINER_REGISTRY}.azurecr.io"
BACKEND_IMAGE="${REGISTRY_SERVER}/${AZURE_CONTAINERAPPS_BACKEND}:${IMAGE_TAG}"
FRONTEND_IMAGE="${REGISTRY_SERVER}/${AZURE_CONTAINERAPPS_FRONTEND}:${IMAGE_TAG}"
DEFAULT_ALLOWED_ORIGINS="${SORA_WORKFLOW_ALLOWED_ORIGINS:-*}"

command -v az >/dev/null || { echo "Azure CLI (az) is required." >&2; exit 1; }
command -v docker >/dev/null || { echo "Docker CLI is required." >&2; exit 1; }

az account set --subscription "$AZURE_SUBSCRIPTION_ID"
az config set extension.use_dynamic_install=yes_without_prompt >/dev/null
az extension add --name containerapp --upgrade >/dev/null

if ! az group show --name "$AZURE_RESOURCE_GROUP" >/dev/null 2>&1; then
  az group create --name "$AZURE_RESOURCE_GROUP" --location "$AZURE_LOCATION" >/dev/null
fi

if ! az acr show --name "$AZURE_CONTAINER_REGISTRY" --resource-group "$AZURE_RESOURCE_GROUP" >/dev/null 2>&1; then
  az acr create \
    --name "$AZURE_CONTAINER_REGISTRY" \
    --resource-group "$AZURE_RESOURCE_GROUP" \
    --location "$AZURE_LOCATION" \
    --sku Basic \
    --admin-enabled true >/dev/null
fi

az acr login --name "$AZURE_CONTAINER_REGISTRY" >/dev/null
REGISTRY_USERNAME=$(az acr credential show --name "$AZURE_CONTAINER_REGISTRY" --query username -o tsv)
REGISTRY_PASSWORD=$(az acr credential show --name "$AZURE_CONTAINER_REGISTRY" --query passwords[0].value -o tsv)

if ! az containerapp env show --name "$AZURE_CONTAINERAPPS_ENVIRONMENT" --resource-group "$AZURE_RESOURCE_GROUP" >/dev/null 2>&1; then
  az containerapp env create \
    --name "$AZURE_CONTAINERAPPS_ENVIRONMENT" \
    --resource-group "$AZURE_RESOURCE_GROUP" \
    --location "$AZURE_LOCATION" \
    --logs-destination none >/dev/null
fi

DOCKER_BUILDKIT=1 docker build \
  --file "$ROOT_DIR/Dockerfile.backend" \
  --tag "$BACKEND_IMAGE" \
  "$ROOT_DIR"
docker push "$BACKEND_IMAGE"

BACKEND_SECRETS=("sora-openai-api-key=$AZURE_OPENAI_API_KEY")
if [[ -n "${OPENAI_API_KEY:-}" ]]; then
  BACKEND_SECRETS+=("openai-api-key=$OPENAI_API_KEY")
fi
if [[ -n "${SORA_WORKFLOW_PASSWORD:-}" ]]; then
  BACKEND_SECRETS+=("sora-workflow-password=$SORA_WORKFLOW_PASSWORD")
fi
if [[ -n "${SORA_WORKFLOW_API_TOKEN:-}" ]]; then
  BACKEND_SECRETS+=("sora-workflow-api-token=$SORA_WORKFLOW_API_TOKEN")
fi
if [[ -n "${KLING_ACCESS_KEY:-}" ]]; then
  BACKEND_SECRETS+=("kling-access-key=$KLING_ACCESS_KEY")
fi
if [[ -n "${KLING_SECRET_KEY:-}" ]]; then
  BACKEND_SECRETS+=("kling-secret-key=$KLING_SECRET_KEY")
fi
if [[ -n "${KLING_API_KEY:-}" ]]; then
  BACKEND_SECRETS+=("kling-api-key=$KLING_API_KEY")
fi
if [[ -n "${AZURE_CONTENT_UNDERSTANDING_API_KEY:-}" ]]; then
  BACKEND_SECRETS+=("content-understanding-api-key=$AZURE_CONTENT_UNDERSTANDING_API_KEY")
fi
if [[ -n "${AZURE_SPEECH_KEY:-}" ]]; then
  BACKEND_SECRETS+=("azure-speech-key=$AZURE_SPEECH_KEY")
fi
if [[ -n "${AZURE_OPENAI_REASONING_API_KEY:-}" ]]; then
  BACKEND_SECRETS+=("reasoning-api-key=$AZURE_OPENAI_REASONING_API_KEY")
fi
if [[ -n "${FOUNDRY_GPT_IMAGE_API_KEY:-}" ]]; then
  BACKEND_SECRETS+=("foundry-image-api-key=$FOUNDRY_GPT_IMAGE_API_KEY")
fi
if [[ -n "${ARK_API_KEY:-}" ]]; then
  BACKEND_SECRETS+=("ark-api-key=$ARK_API_KEY")
fi
if [[ -n "${R2_ACCESS_KEY_ID:-}" ]]; then
  BACKEND_SECRETS+=("r2-access-key-id=$R2_ACCESS_KEY_ID")
fi
if [[ -n "${R2_SECRET_ACCESS_KEY:-}" ]]; then
  BACKEND_SECRETS+=("r2-secret-access-key=$R2_SECRET_ACCESS_KEY")
fi
BACKEND_ENV_VARS=(
  "AZURE_OPENAI_API_KEY=secretref:sora-openai-api-key"
  "SORA_WORKFLOW_ALLOWED_ORIGINS=$DEFAULT_ALLOWED_ORIGINS"
  "SORA_WORKFLOW_REQUIRE_AUTH=${SORA_WORKFLOW_REQUIRE_AUTH:-1}"
)
optional_backend_envs=(
  AZURE_OPENAI_ENDPOINT
  ENDPOINT_URL
  DEPLOYMENT_NAME
  AZURE_OPENAI_CHAT_DEPLOYMENT_NAME
  AZURE_OPENAI_RESPONSES_DEPLOYMENT_NAME
  AZURE_OPENAI_REASONING_ENDPOINT
  AZURE_OPENAI_REASONING_DEPLOYMENT
  AZURE_OPENAI_REASONING_EFFORT
  AZURE_OPENAI_RESPONSES_DEPLOYMENT
  FOUNDRY_GPT_IMAGE_ENDPOINT
  FOUNDRY_GPT_IMAGE_DEPLOYMENT
  AZURE_OPENAI_API_VERSION
  AZURE_CONTENT_UNDERSTANDING_ENDPOINT
  AZURE_CONTENT_UNDERSTANDING_API_VERSION
  AZURE_CONTENT_UNDERSTANDING_ANALYZER_ID
  CONTENT_UNDERSTANDING_MAX_UPLOAD_MB
  CONTENT_UNDERSTANDING_POLL_INTERVAL
  CONTENT_UNDERSTANDING_POLL_TIMEOUT
  JSON_IMAGE_MAX_UPLOAD_MB
  SUBPROCESS_TIMEOUT_SECONDS
  SORA_WORKFLOW_THREADS
  SORA_WORKFLOW_USERNAME
  VIDEO_DOWNLOAD_VARIANT
  SORA_WORKFLOW_ALLOWED_LANGUAGES
  SORA_WORKFLOW_MODE
  VIDEO_PROVIDER
  SEEDANCE_MODEL
  SEEDANCE_GENERATE_AUDIO
  SEEDANCE_RESOLUTION
  SEEDANCE_IMAGE_ROLE
  SEEDANCE_POLL_INTERVAL
  SEEDANCE_POLL_TIMEOUT
  R2_ACCOUNT_ID
  R2_BUCKET
  R2_PUBLIC_BASE_URL
  R2_UPLOAD_PREFIX
  LOG_LEVEL
  KLING_API_BASE_URL
  KLING_TOKEN_TTL
  KLING_MODEL_NAME
  KLING_MODE
  KLING_CFG_SCALE
  KLING_POLL_INTERVAL
  KLING_POLL_TIMEOUT
  KLING_DOWNLOAD_CHUNK_SIZE
  KLING_CREATE_MAX_RETRIES
  KLING_CREATE_RETRY_BASE_DELAY
  KLING_CREATE_RETRY_MAX_DELAY
  KLING_CREATE_RETRY_JITTER
  AZURE_SPEECH_ENDPOINT
)
for var in "${optional_backend_envs[@]}"; do
  if [[ -n "${!var:-}" ]]; then
    BACKEND_ENV_VARS+=("$var=${!var}")
  fi
done
if [[ -n "${KLING_ACCESS_KEY:-}" ]]; then
  BACKEND_ENV_VARS+=("KLING_ACCESS_KEY=secretref:kling-access-key")
fi
if [[ -n "${KLING_SECRET_KEY:-}" ]]; then
  BACKEND_ENV_VARS+=("KLING_SECRET_KEY=secretref:kling-secret-key")
fi
if [[ -n "${KLING_API_KEY:-}" ]]; then
  BACKEND_ENV_VARS+=("KLING_API_KEY=secretref:kling-api-key")
fi
if [[ -n "${OPENAI_API_KEY:-}" ]]; then
  BACKEND_ENV_VARS+=("OPENAI_API_KEY=secretref:openai-api-key")
fi
if [[ -n "${SORA_WORKFLOW_PASSWORD:-}" ]]; then
  BACKEND_ENV_VARS+=("SORA_WORKFLOW_PASSWORD=secretref:sora-workflow-password")
fi
if [[ -n "${SORA_WORKFLOW_API_TOKEN:-}" ]]; then
  BACKEND_ENV_VARS+=("SORA_WORKFLOW_API_TOKEN=secretref:sora-workflow-api-token")
fi
if [[ -n "${AZURE_CONTENT_UNDERSTANDING_API_KEY:-}" ]]; then
  BACKEND_ENV_VARS+=("AZURE_CONTENT_UNDERSTANDING_API_KEY=secretref:content-understanding-api-key")
fi
if [[ -n "${AZURE_SPEECH_KEY:-}" ]]; then
  BACKEND_ENV_VARS+=("AZURE_SPEECH_KEY=secretref:azure-speech-key")
fi
if [[ -n "${AZURE_OPENAI_REASONING_API_KEY:-}" ]]; then
  BACKEND_ENV_VARS+=("AZURE_OPENAI_REASONING_API_KEY=secretref:reasoning-api-key")
fi
if [[ -n "${FOUNDRY_GPT_IMAGE_API_KEY:-}" ]]; then
  BACKEND_ENV_VARS+=("FOUNDRY_GPT_IMAGE_API_KEY=secretref:foundry-image-api-key")
fi
if [[ -n "${ARK_API_KEY:-}" ]]; then
  BACKEND_ENV_VARS+=("ARK_API_KEY=secretref:ark-api-key")
fi
if [[ -n "${R2_ACCESS_KEY_ID:-}" ]]; then
  BACKEND_ENV_VARS+=("R2_ACCESS_KEY_ID=secretref:r2-access-key-id")
fi
if [[ -n "${R2_SECRET_ACCESS_KEY:-}" ]]; then
  BACKEND_ENV_VARS+=("R2_SECRET_ACCESS_KEY=secretref:r2-secret-access-key")
fi

BACKEND_SECRET_ARGS=(--secrets "${BACKEND_SECRETS[@]}")
BACKEND_ENV_SET_ARGS=(--set-env-vars "${BACKEND_ENV_VARS[@]}")
BACKEND_ENV_CREATE_ARGS=(--env-vars "${BACKEND_ENV_VARS[@]}")

if az containerapp show --name "$AZURE_CONTAINERAPPS_BACKEND" --resource-group "$AZURE_RESOURCE_GROUP" >/dev/null 2>&1; then
  az containerapp secret set \
    --name "$AZURE_CONTAINERAPPS_BACKEND" \
    --resource-group "$AZURE_RESOURCE_GROUP" \
    --secrets "${BACKEND_SECRETS[@]}" >/dev/null
  az containerapp registry set \
    --name "$AZURE_CONTAINERAPPS_BACKEND" \
    --resource-group "$AZURE_RESOURCE_GROUP" \
    --server "$REGISTRY_SERVER" \
    --username "$REGISTRY_USERNAME" \
    --password "$REGISTRY_PASSWORD" >/dev/null
  az containerapp update \
    --name "$AZURE_CONTAINERAPPS_BACKEND" \
    --resource-group "$AZURE_RESOURCE_GROUP" \
    --image "$BACKEND_IMAGE" \
    --min-replicas 1 \
    --max-replicas 2 \
    --cpu 2.0 \
    --memory 4Gi \
    "${BACKEND_ENV_SET_ARGS[@]}" >/dev/null
else
  az containerapp create \
    --name "$AZURE_CONTAINERAPPS_BACKEND" \
    --resource-group "$AZURE_RESOURCE_GROUP" \
    --environment "$AZURE_CONTAINERAPPS_ENVIRONMENT" \
    --image "$BACKEND_IMAGE" \
    --target-port 8000 \
    --ingress external \
    --min-replicas 1 \
    --max-replicas 2 \
    --registry-server "$REGISTRY_SERVER" \
    --registry-username "$REGISTRY_USERNAME" \
    --registry-password "$REGISTRY_PASSWORD" \
    --cpu 2.0 \
    --memory 4Gi \
    "${BACKEND_SECRET_ARGS[@]}" \
    "${BACKEND_ENV_CREATE_ARGS[@]}" >/dev/null
fi

BACKEND_FQDN=$(az containerapp show --name "$AZURE_CONTAINERAPPS_BACKEND" --resource-group "$AZURE_RESOURCE_GROUP" --query properties.configuration.ingress.fqdn -o tsv)
echo "Backend available at https://${BACKEND_FQDN}" >&2

FRONTEND_API_BASE_URL="${FRONTEND_API_BASE_URL:-https://${BACKEND_FQDN}}"
DOCKER_BUILDKIT=1 docker build \
  --file "$ROOT_DIR/frontend/Dockerfile" \
  --build-arg "NEXT_PUBLIC_API_BASE_URL=$FRONTEND_API_BASE_URL" \
  --tag "$FRONTEND_IMAGE" \
  "$ROOT_DIR/frontend"
docker push "$FRONTEND_IMAGE"

FRONTEND_ENV_VARS=("NEXT_PUBLIC_API_BASE_URL=$FRONTEND_API_BASE_URL")
FRONTEND_ENV_SET_ARGS=(--set-env-vars "${FRONTEND_ENV_VARS[@]}")
FRONTEND_ENV_CREATE_ARGS=(--env-vars "${FRONTEND_ENV_VARS[@]}")

if az containerapp show --name "$AZURE_CONTAINERAPPS_FRONTEND" --resource-group "$AZURE_RESOURCE_GROUP" >/dev/null 2>&1; then
  az containerapp registry set \
    --name "$AZURE_CONTAINERAPPS_FRONTEND" \
    --resource-group "$AZURE_RESOURCE_GROUP" \
    --server "$REGISTRY_SERVER" \
    --username "$REGISTRY_USERNAME" \
    --password "$REGISTRY_PASSWORD" >/dev/null
  az containerapp update \
    --name "$AZURE_CONTAINERAPPS_FRONTEND" \
    --resource-group "$AZURE_RESOURCE_GROUP" \
    --image "$FRONTEND_IMAGE" \
    --min-replicas 1 \
    --max-replicas 2 \
    --cpu 1.0 \
    --memory 2Gi \
    "${FRONTEND_ENV_SET_ARGS[@]}" >/dev/null
else
  az containerapp create \
    --name "$AZURE_CONTAINERAPPS_FRONTEND" \
    --resource-group "$AZURE_RESOURCE_GROUP" \
    --environment "$AZURE_CONTAINERAPPS_ENVIRONMENT" \
    --image "$FRONTEND_IMAGE" \
    --target-port 3000 \
    --ingress external \
    --min-replicas 1 \
    --max-replicas 2 \
    --registry-server "$REGISTRY_SERVER" \
    --registry-username "$REGISTRY_USERNAME" \
    --registry-password "$REGISTRY_PASSWORD" \
    --cpu 1.0 \
    --memory 2Gi \
    "${FRONTEND_ENV_CREATE_ARGS[@]}" >/dev/null
fi

FRONTEND_FQDN=$(az containerapp show --name "$AZURE_CONTAINERAPPS_FRONTEND" --resource-group "$AZURE_RESOURCE_GROUP" --query properties.configuration.ingress.fqdn -o tsv)
echo "Frontend available at https://${FRONTEND_FQDN}" >&2

if [[ -z "${SORA_WORKFLOW_ALLOWED_ORIGINS:-}" || "$SORA_WORKFLOW_ALLOWED_ORIGINS" == "*" ]]; then
  az containerapp update \
    --name "$AZURE_CONTAINERAPPS_BACKEND" \
    --resource-group "$AZURE_RESOURCE_GROUP" \
    --set-env-vars "SORA_WORKFLOW_ALLOWED_ORIGINS=https://${FRONTEND_FQDN}" >/dev/null
fi

echo "Deployment complete.\nBackend URL: https://${BACKEND_FQDN}\nFrontend URL: https://${FRONTEND_FQDN}" 
