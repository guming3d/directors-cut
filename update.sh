#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage: ./update.sh [--component backend|frontend|all] [--image-tag TAG]
Rebuilds the requested images, pushes them to ACR, and updates the matching Azure Container Apps.
USAGE
}

COMPONENT="all"
IMAGE_TAG_OVERRIDE=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --component)
      COMPONENT="$2"
      shift 2
      ;;
    --image-tag)
      IMAGE_TAG_OVERRIDE="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage
      exit 1
      ;;
  esac
done

if [[ "$COMPONENT" != "backend" && "$COMPONENT" != "frontend" && "$COMPONENT" != "all" ]]; then
  echo "--component must be backend, frontend, or all" >&2
  exit 1
fi

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="$ROOT_DIR/.env"
if [[ ! -f "$ENV_FILE" ]]; then
  echo "Missing $ENV_FILE. Run deploy.sh first." >&2
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
)
for var in "${required_vars[@]}"; do
  if [[ -z "${!var:-}" ]]; then
    echo "Missing $var in .env" >&2
    exit 1
  fi
done

command -v az >/dev/null || { echo "Azure CLI (az) is required." >&2; exit 1; }
command -v docker >/dev/null || { echo "Docker CLI is required." >&2; exit 1; }

az account set --subscription "$AZURE_SUBSCRIPTION_ID"
az config set extension.use_dynamic_install=yes_without_prompt >/dev/null
az extension add --name containerapp --upgrade >/dev/null

if ! az containerapp env show --name "$AZURE_CONTAINERAPPS_ENVIRONMENT" --resource-group "$AZURE_RESOURCE_GROUP" >/dev/null 2>&1; then
  echo "Container Apps environment $AZURE_CONTAINERAPPS_ENVIRONMENT not found. Run deploy.sh first." >&2
  exit 1
fi

REGISTRY_SERVER="${AZURE_CONTAINER_REGISTRY}.azurecr.io"
az acr login --name "$AZURE_CONTAINER_REGISTRY" >/dev/null
REGISTRY_USERNAME=$(az acr credential show --name "$AZURE_CONTAINER_REGISTRY" --query username -o tsv)
REGISTRY_PASSWORD=$(az acr credential show --name "$AZURE_CONTAINER_REGISTRY" --query passwords[0].value -o tsv)

IMAGE_TAG="${IMAGE_TAG_OVERRIDE:-$(git -C "$ROOT_DIR" rev-parse --short HEAD 2>/dev/null || date -u +%Y%m%d%H%M%S)}"
BACKEND_IMAGE="${REGISTRY_SERVER}/${AZURE_CONTAINERAPPS_BACKEND}:${IMAGE_TAG}"
FRONTEND_IMAGE="${REGISTRY_SERVER}/${AZURE_CONTAINERAPPS_FRONTEND}:${IMAGE_TAG}"

update_backend() {
  if ! az containerapp show --name "$AZURE_CONTAINERAPPS_BACKEND" --resource-group "$AZURE_RESOURCE_GROUP" >/dev/null 2>&1; then
    echo "Backend container app $AZURE_CONTAINERAPPS_BACKEND not found. Run deploy.sh first." >&2
    exit 1
  fi
  if [[ -z "${AZURE_OPENAI_API_KEY:-}" ]]; then
    echo "AZURE_OPENAI_API_KEY must be present in .env to update the backend" >&2
    exit 1
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

  az containerapp ingress update \
    --name "$AZURE_CONTAINERAPPS_BACKEND" \
    --resource-group "$AZURE_RESOURCE_GROUP" \
    --type external \
    --target-port 8000 >/dev/null

  BACKEND_ENV_VARS=("AZURE_OPENAI_API_KEY=secretref:sora-openai-api-key")
  [[ -n "${AZURE_OPENAI_ENDPOINT:-}" ]] && BACKEND_ENV_VARS+=("AZURE_OPENAI_ENDPOINT=$AZURE_OPENAI_ENDPOINT")
  [[ -n "${ENDPOINT_URL:-}" ]] && BACKEND_ENV_VARS+=("ENDPOINT_URL=$ENDPOINT_URL")
  [[ -n "${DEPLOYMENT_NAME:-}" ]] && BACKEND_ENV_VARS+=("DEPLOYMENT_NAME=$DEPLOYMENT_NAME")
  [[ -n "${AZURE_OPENAI_CHAT_DEPLOYMENT_NAME:-}" ]] && BACKEND_ENV_VARS+=("AZURE_OPENAI_CHAT_DEPLOYMENT_NAME=$AZURE_OPENAI_CHAT_DEPLOYMENT_NAME")
  [[ -n "${AZURE_OPENAI_RESPONSES_DEPLOYMENT_NAME:-}" ]] && BACKEND_ENV_VARS+=("AZURE_OPENAI_RESPONSES_DEPLOYMENT_NAME=$AZURE_OPENAI_RESPONSES_DEPLOYMENT_NAME")
  [[ -n "${AZURE_OPENAI_GPT5_DEPLOYMENT:-}" ]] && BACKEND_ENV_VARS+=("AZURE_OPENAI_GPT5_DEPLOYMENT=$AZURE_OPENAI_GPT5_DEPLOYMENT")
  [[ -n "${AZURE_OPENAI_RESPONSES_DEPLOYMENT:-}" ]] && BACKEND_ENV_VARS+=("AZURE_OPENAI_RESPONSES_DEPLOYMENT=$AZURE_OPENAI_RESPONSES_DEPLOYMENT")
  [[ -n "${AZURE_CONTENT_UNDERSTANDING_ENDPOINT:-}" ]] && BACKEND_ENV_VARS+=("AZURE_CONTENT_UNDERSTANDING_ENDPOINT=$AZURE_CONTENT_UNDERSTANDING_ENDPOINT")
  [[ -n "${AZURE_CONTENT_UNDERSTANDING_API_VERSION:-}" ]] && BACKEND_ENV_VARS+=("AZURE_CONTENT_UNDERSTANDING_API_VERSION=$AZURE_CONTENT_UNDERSTANDING_API_VERSION")
  [[ -n "${AZURE_CONTENT_UNDERSTANDING_ANALYZER_ID:-}" ]] && BACKEND_ENV_VARS+=("AZURE_CONTENT_UNDERSTANDING_ANALYZER_ID=$AZURE_CONTENT_UNDERSTANDING_ANALYZER_ID")
  [[ -n "${CONTENT_UNDERSTANDING_MAX_UPLOAD_MB:-}" ]] && BACKEND_ENV_VARS+=("CONTENT_UNDERSTANDING_MAX_UPLOAD_MB=$CONTENT_UNDERSTANDING_MAX_UPLOAD_MB")
  [[ -n "${CONTENT_UNDERSTANDING_POLL_INTERVAL:-}" ]] && BACKEND_ENV_VARS+=("CONTENT_UNDERSTANDING_POLL_INTERVAL=$CONTENT_UNDERSTANDING_POLL_INTERVAL")
  [[ -n "${CONTENT_UNDERSTANDING_POLL_TIMEOUT:-}" ]] && BACKEND_ENV_VARS+=("CONTENT_UNDERSTANDING_POLL_TIMEOUT=$CONTENT_UNDERSTANDING_POLL_TIMEOUT")
  [[ -n "${SORA_WORKFLOW_ALLOWED_ORIGINS:-}" ]] && BACKEND_ENV_VARS+=("SORA_WORKFLOW_ALLOWED_ORIGINS=$SORA_WORKFLOW_ALLOWED_ORIGINS")
  [[ -n "${SORA_WORKFLOW_THREADS:-}" ]] && BACKEND_ENV_VARS+=("SORA_WORKFLOW_THREADS=$SORA_WORKFLOW_THREADS")
  [[ -n "${VIDEO_DOWNLOAD_VARIANT:-}" ]] && BACKEND_ENV_VARS+=("VIDEO_DOWNLOAD_VARIANT=$VIDEO_DOWNLOAD_VARIANT")
  [[ -n "${AZURE_OPENAI_API_VERSION:-}" ]] && BACKEND_ENV_VARS+=("AZURE_OPENAI_API_VERSION=$AZURE_OPENAI_API_VERSION")
  [[ -n "${KLING_API_BASE_URL:-}" ]] && BACKEND_ENV_VARS+=("KLING_API_BASE_URL=$KLING_API_BASE_URL")
  [[ -n "${KLING_TOKEN_TTL:-}" ]] && BACKEND_ENV_VARS+=("KLING_TOKEN_TTL=$KLING_TOKEN_TTL")
  [[ -n "${KLING_MODEL_NAME:-}" ]] && BACKEND_ENV_VARS+=("KLING_MODEL_NAME=$KLING_MODEL_NAME")
  [[ -n "${KLING_MODE:-}" ]] && BACKEND_ENV_VARS+=("KLING_MODE=$KLING_MODE")
  [[ -n "${KLING_CFG_SCALE:-}" ]] && BACKEND_ENV_VARS+=("KLING_CFG_SCALE=$KLING_CFG_SCALE")
  [[ -n "${KLING_POLL_INTERVAL:-}" ]] && BACKEND_ENV_VARS+=("KLING_POLL_INTERVAL=$KLING_POLL_INTERVAL")
  [[ -n "${KLING_POLL_TIMEOUT:-}" ]] && BACKEND_ENV_VARS+=("KLING_POLL_TIMEOUT=$KLING_POLL_TIMEOUT")
  [[ -n "${KLING_DOWNLOAD_CHUNK_SIZE:-}" ]] && BACKEND_ENV_VARS+=("KLING_DOWNLOAD_CHUNK_SIZE=$KLING_DOWNLOAD_CHUNK_SIZE")
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
  if [[ -n "${AZURE_CONTENT_UNDERSTANDING_API_KEY:-}" ]]; then
    BACKEND_ENV_VARS+=("AZURE_CONTENT_UNDERSTANDING_API_KEY=secretref:content-understanding-api-key")
  fi

  az containerapp update \
    --name "$AZURE_CONTAINERAPPS_BACKEND" \
    --resource-group "$AZURE_RESOURCE_GROUP" \
    --image "$BACKEND_IMAGE" \
    --min-replicas 1 \
    --max-replicas 2 \
    --cpu 2.0 \
    --memory 4Gi \
    --set-env-vars "${BACKEND_ENV_VARS[@]}" >/dev/null
}

update_frontend() {
  if ! az containerapp show --name "$AZURE_CONTAINERAPPS_FRONTEND" --resource-group "$AZURE_RESOURCE_GROUP" >/dev/null 2>&1; then
    echo "Frontend container app $AZURE_CONTAINERAPPS_FRONTEND not found. Run deploy.sh first." >&2
    exit 1
  fi
  BACKEND_FQDN=$(az containerapp show --name "$AZURE_CONTAINERAPPS_BACKEND" --resource-group "$AZURE_RESOURCE_GROUP" --query properties.configuration.ingress.fqdn -o tsv)
  FRONTEND_API_BASE_URL="${FRONTEND_API_BASE_URL:-https://${BACKEND_FQDN}}"
  DOCKER_BUILDKIT=1 docker build \
    --file "$ROOT_DIR/frontend/Dockerfile" \
    --build-arg "NEXT_PUBLIC_API_BASE_URL=$FRONTEND_API_BASE_URL" \
    --tag "$FRONTEND_IMAGE" \
    "$ROOT_DIR/frontend"
  docker push "$FRONTEND_IMAGE"

  az containerapp registry set \
    --name "$AZURE_CONTAINERAPPS_FRONTEND" \
    --resource-group "$AZURE_RESOURCE_GROUP" \
    --server "$REGISTRY_SERVER" \
    --username "$REGISTRY_USERNAME" \
    --password "$REGISTRY_PASSWORD" >/dev/null

  az containerapp ingress update \
    --name "$AZURE_CONTAINERAPPS_FRONTEND" \
    --resource-group "$AZURE_RESOURCE_GROUP" \
    --type external \
    --target-port 3000 >/dev/null

  az containerapp update \
    --name "$AZURE_CONTAINERAPPS_FRONTEND" \
    --resource-group "$AZURE_RESOURCE_GROUP" \
    --image "$FRONTEND_IMAGE" \
    --min-replicas 1 \
    --max-replicas 2 \
    --cpu 1.0 \
    --memory 2Gi \
    --set-env-vars "NEXT_PUBLIC_API_BASE_URL=$FRONTEND_API_BASE_URL" >/dev/null

  if [[ -z "${SORA_WORKFLOW_ALLOWED_ORIGINS:-}" ]]; then
    FRONTEND_FQDN=$(az containerapp show --name "$AZURE_CONTAINERAPPS_FRONTEND" --resource-group "$AZURE_RESOURCE_GROUP" --query properties.configuration.ingress.fqdn -o tsv)
    az containerapp update \
      --name "$AZURE_CONTAINERAPPS_BACKEND" \
      --resource-group "$AZURE_RESOURCE_GROUP" \
      --set-env-vars "SORA_WORKFLOW_ALLOWED_ORIGINS=https://${FRONTEND_FQDN}" >/dev/null
  fi
}

case "$COMPONENT" in
  backend)
    update_backend
    ;;
  frontend)
    update_frontend
    ;;
  all)
    update_backend
    update_frontend
    ;;
esac

BACKEND_FQDN=$(az containerapp show --name "$AZURE_CONTAINERAPPS_BACKEND" --resource-group "$AZURE_RESOURCE_GROUP" --query properties.configuration.ingress.fqdn -o tsv)
FRONTEND_FQDN=$(az containerapp show --name "$AZURE_CONTAINERAPPS_FRONTEND" --resource-group "$AZURE_RESOURCE_GROUP" --query properties.configuration.ingress.fqdn -o tsv)

echo "Updated containers pushed with tag ${IMAGE_TAG}.\nBackend: https://${BACKEND_FQDN}\nFrontend: https://${FRONTEND_FQDN}" 
