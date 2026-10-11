# Shared helpers for deploy.sh and update.sh. Source after .env is loaded.

# Copy the Docker Hub base images into the project registry once, so builds do
# not depend on reaching Docker Hub from this machine (its DNS answers can be
# unreliable) and avoid its pull limits. Azure performs the import. Sets
# BACKEND_BASE_IMAGE and FRONTEND_BASE_IMAGE for the image builds.
# DOCKER_BASE_REFRESH=1 re-imports to pick up base-image updates.
mirror_base_images() {
  local registry="${AZURE_CONTAINER_REGISTRY}"
  local image repo tag
  for image in "python:3.11-slim" "node:20-alpine"; do
    repo="base/${image%%:*}"
    tag="${image##*:}"
    if [[ "${DOCKER_BASE_REFRESH:-0}" == "1" ]] || \
       ! az acr repository show-tags --name "$registry" --repository "$repo" -o tsv 2>/dev/null | grep -qx "$tag"; then
      az acr import --name "$registry" --source "docker.io/library/${image}" --image "${repo}:${tag}" --force >/dev/null
      echo "Mirrored ${image} into ${registry}.azurecr.io/${repo}:${tag}."
    fi
  done
  BACKEND_BASE_IMAGE="${registry}.azurecr.io/base/python:3.11-slim"
  FRONTEND_BASE_IMAGE="${registry}.azurecr.io/base/node:20-alpine"
}

# Object id of the backend app's system-assigned identity, enabling it if needed.
backend_principal_id() {
  az containerapp identity assign \
    --name "$AZURE_CONTAINERAPPS_BACKEND" \
    --resource-group "$AZURE_RESOURCE_GROUP" \
    --system-assigned \
    --query principalId -o tsv
}

# Grant a role to a principal on a scope unless it already has it.
ensure_role() {
  local principal_id="$1" role="$2" scope="$3" label="$4"
  local existing
  existing=$(az role assignment list --assignee "$principal_id" --scope "$scope" --role "$role" --query "[0].id" -o tsv)
  if [[ -z "$existing" ]]; then
    az role assignment create \
      --assignee-object-id "$principal_id" \
      --assignee-principal-type ServicePrincipal \
      --role "$role" \
      --scope "$scope" >/dev/null
    echo "Granted '${role}' on ${label} to the backend identity; it can take a few minutes to take effect."
  fi
}

# Sora is called with Microsoft Entra ID unless AZURE_OPENAI_AUTH=key, so the
# backend app needs a managed identity with data-plane access to the Azure
# OpenAI resource. Safe to re-run: the identity and role assignment are reused.
ensure_backend_entra_access() {
  if [[ "${AZURE_OPENAI_AUTH:-entra}" == "key" ]]; then
    return
  fi

  local endpoint="${ENDPOINT_URL:-${AZURE_OPENAI_ENDPOINT:-}}"
  local account="${AZURE_OPENAI_RESOURCE_NAME:-}"
  if [[ -z "$account" ]]; then
    account=$(printf '%s' "$endpoint" | sed -E 's#^https?://([^./]+)\..*#\1#')
  fi
  local resource_id
  resource_id=$(az cognitiveservices account list --query "[?name=='${account}'].id | [0]" -o tsv)
  if [[ -z "$resource_id" ]]; then
    echo "Azure OpenAI resource '${account}' not found in subscription ${AZURE_SUBSCRIPTION_ID}; set AZURE_OPENAI_RESOURCE_NAME." >&2
    exit 1
  fi

  ensure_role "$(backend_principal_id)" "Cognitive Services OpenAI User" "$resource_id" "$account"
}

# Jobs and media are mirrored to a private blob container so deploys keep them
# (backend/output_storage.py). Shared-key access stays disabled, as the
# subscription's security baseline requires; the backend uses its managed
# identity. Safe to re-run. Sets OUTPUT_STORAGE, AZURE_STORAGE_ACCOUNT_URL, and
# OUTPUT_BLOB_CONTAINER for the backend settings. DEPLOY_OUTPUT_STORAGE=local
# skips it and keeps the old temporary-disk behaviour.
ensure_output_storage() {
  if [[ "${DEPLOY_OUTPUT_STORAGE:-blob}" != "blob" ]]; then
    return
  fi

  local account="${AZURE_OUTPUT_STORAGE_ACCOUNT:-${AZURE_CONTAINER_REGISTRY}out}"
  account=$(printf '%s' "$account" | tr '[:upper:]' '[:lower:]' | tr -cd 'a-z0-9' | cut -c1-24)
  local container="${OUTPUT_BLOB_CONTAINER:-output}"

  if ! az storage account show --name "$account" --resource-group "$AZURE_RESOURCE_GROUP" >/dev/null 2>&1; then
    az storage account create \
      --name "$account" \
      --resource-group "$AZURE_RESOURCE_GROUP" \
      --location "$AZURE_LOCATION" \
      --sku Standard_LRS \
      --kind StorageV2 \
      --allow-shared-key-access false \
      --allow-blob-public-access false \
      --min-tls-version TLS1_2 >/dev/null
    echo "Created storage account ${account} for job data."
  fi
  local account_id
  account_id=$(az storage account show --name "$account" --resource-group "$AZURE_RESOURCE_GROUP" --query id -o tsv)

  # container-rm goes through Azure Resource Manager, so it needs no storage key.
  if [[ "$(az storage container-rm exists --storage-account "$account" --name "$container" --query exists -o tsv)" != "true" ]]; then
    az storage container-rm create --storage-account "$account" --name "$container" --public-access off >/dev/null
  fi

  ensure_role "$(backend_principal_id)" "Storage Blob Data Contributor" "$account_id" "$account"

  OUTPUT_STORAGE=blob
  AZURE_STORAGE_ACCOUNT_URL="https://${account}.blob.core.windows.net"
  OUTPUT_BLOB_CONTAINER="$container"
}

# A deploy restarts the backend and ends any render in progress, so refuse to
# deploy while cloud jobs are queued or running. Pass --force to update.sh to
# deploy anyway.
abort_if_cloud_jobs_running() {
  local fqdn
  fqdn=$(az containerapp show --name "$AZURE_CONTAINERAPPS_BACKEND" --resource-group "$AZURE_RESOURCE_GROUP" \
    --query properties.configuration.ingress.fqdn -o tsv 2>/dev/null)
  if [[ -z "$fqdn" ]]; then
    return
  fi
  local -a auth=()
  if [[ -n "${SORA_WORKFLOW_API_TOKEN:-}" ]]; then
    auth=(-H "Authorization: Bearer ${SORA_WORKFLOW_API_TOKEN}")
  elif [[ -n "${SORA_WORKFLOW_USERNAME:-}" && -n "${SORA_WORKFLOW_PASSWORD:-}" ]]; then
    auth=(-u "${SORA_WORKFLOW_USERNAME}:${SORA_WORKFLOW_PASSWORD}")
  fi

  local running="" endpoint body
  for endpoint in workflows single-videos video-insights reproduce; do
    if ! body=$(curl -sf --max-time 20 "${auth[@]}" "https://${fqdn}/api/${endpoint}"); then
      echo "Could not check running ${endpoint} jobs on https://${fqdn}; use --force to deploy anyway." >&2
      exit 1
    fi
    running+=$(printf '%s' "$body" | python3 -c '
import json, sys
print(" ".join(j["job_id"][:12] for j in json.load(sys.stdin) if j.get("status") in ("queued", "running")))
')" "
  done
  if [[ -n "${running// /}" ]]; then
    echo "Cloud jobs are still running (${running}). Deploying now would end them; wait or use --force." >&2
    exit 1
  fi
}
