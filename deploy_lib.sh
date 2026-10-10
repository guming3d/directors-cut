# Shared helpers for deploy.sh and update.sh. Source after .env is loaded.

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

  local principal_id
  principal_id=$(az containerapp identity assign \
    --name "$AZURE_CONTAINERAPPS_BACKEND" \
    --resource-group "$AZURE_RESOURCE_GROUP" \
    --system-assigned \
    --query principalId -o tsv)

  local role="Cognitive Services OpenAI User"
  local existing
  existing=$(az role assignment list --assignee "$principal_id" --scope "$resource_id" --role "$role" --query "[0].id" -o tsv)
  if [[ -z "$existing" ]]; then
    az role assignment create \
      --assignee-object-id "$principal_id" \
      --assignee-principal-type ServicePrincipal \
      --role "$role" \
      --scope "$resource_id" >/dev/null
    echo "Granted '${role}' on ${account} to the backend identity; it can take a few minutes to take effect."
  fi
}
