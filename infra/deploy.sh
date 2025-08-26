#!/usr/bin/env bash
set -euo pipefail

# ========= Helpers =========
err() { echo "❌ $*" >&2; exit 1; }
require() {
  local name="$1" example="$2"
  local val="${!name:-}"
  if [[ -z "${val}" ]]; then
    err "Missing required env var: ${name}. Example: ${name}=${example}"
  fi
  if [[ "${val}" == *"<"* || "${val}" == *">"* || "${val}" =~ your-aoai-resource ]]; then
    err "Env var ${name} still contains a placeholder: '${val}'. Replace with a real value (no < >)."
  fi
}
tolower() { echo "$1" | tr '[:upper:]' '[:lower:]'; }
alnum()   { echo "$1" | tr -cd '[:alnum:]'; }
truncate() { local s="$1" max="$2"; [[ ${#s} -le $max ]] && { echo "$s"; return; }; echo "${s:0:$max}"; }

# ========= Required inputs =========
require LOCATION           "westeurope"
require RG                 "iom-d-we-datallmpoc-rg"
require PREFIX_HYPHEN      "iom-d-we-donorint"
require SUFFIX             "01"
require IMAGE_NAME         "donorstreamlit-app"
require IMAGE_TAG          "12345"
require AZURE_API_BASE     "https://my-aoai-resource.openai.azure.com"
require AZURE_API_VERSION  "2024-05-01-preview"
require AZURE_OPENAI_DEPLOYMENT "gpt4o-prod"
DUPLICATE_LANGCHAIN_VARS="${DUPLICATE_LANGCHAIN_VARS:-true}"

# ========= Names (respect service rules) =========
prefix_hyphen="$(tolower "${PREFIX_HYPHEN}")"
prefix_nohyphen="$(echo "${prefix_hyphen}" | tr -d '-')"

ACR_NAME="$(truncate "$(alnum "${prefix_nohyphen}")acr${SUFFIX}" 50)"
[[ ${#ACR_NAME} -lt 5 ]] && err "Computed ACR name too short: ${ACR_NAME}"

LAW_NAME="${prefix_hyphen}-law-${SUFFIX}"
CAE_NAME="${prefix_hyphen}-cae-${SUFFIX}"
APP_NAME="$(tolower "${prefix_hyphen}-aca-${IMAGE_NAME}-${SUFFIX}")"

sa_base="$(alnum "${prefix_nohyphen}")"
SA_NAME="$(truncate "${sa_base}sa${SUFFIX}" 24)"
[[ ! "${SA_NAME}" =~ ^[a-z0-9]{3,24}$ ]] && err "Storage name invalid: ${SA_NAME}"

kv_base="$(truncate "${prefix_hyphen}" $((24 - (3 + ${#SUFFIX}))))"
[[ ! "${kv_base}" =~ ^[a-zA-Z] ]] && kv_base="a${kv_base}"
KV_NAME="$(tolower "${kv_base}-kv-${SUFFIX}")"
[[ "${KV_NAME}" =~ -$ ]] && KV_NAME="${KV_NAME%?}0"

ACR_LOGIN_SERVER="${ACR_NAME}.azurecr.io"

echo "📦 RG=${RG}  LOC=${LOCATION}"
echo "🔖 Prefix=${prefix_hyphen}  Suffix=${SUFFIX}"
echo "🧾 Names -> ACR:${ACR_NAME}  LAW:${LAW_NAME}  CAE:${CAE_NAME}  APP:${APP_NAME}  SA:${SA_NAME}  KV:${KV_NAME}"

# ========= Azure context =========
echo "💡 Subscription:" && az account show --query "{Name:name,Id:id}" -o table || true

# ========= Resources =========
if ! az acr show -g "$RG" -n "$ACR_NAME" &>/dev/null; then
  echo "🚀 Creating ACR $ACR_NAME"
  az acr create -g "$RG" -n "$ACR_NAME" -l "$LOCATION" --sku Basic >/dev/null
else
  echo "✅ ACR exists"
fi

if ! az monitor log-analytics workspace show -g "$RG" -n "$LAW_NAME" &>/dev/null; then
  echo "🚀 Creating Log Analytics $LAW_NAME"
  az monitor log-analytics workspace create -g "$RG" -n "$LAW_NAME" -l "$LOCATION" >/dev/null
else
  echo "✅ Log Analytics exists"
fi

LAW_ID="$(az monitor log-analytics workspace show -g "$RG" -n "$LAW_NAME" --query id -o tsv)"
LAW_KEY="$(az monitor log-analytics workspace get-shared-keys -g "$RG" -n "$LAW_NAME" --query primarySharedKey -o tsv)"

if ! az containerapp env show -g "$RG" -n "$CAE_NAME" &>/dev/null; then
  echo "🚀 Creating Container Apps Environment $CAE_NAME"
  az containerapp env create \
    -g "$RG" -n "$CAE_NAME" -l "$LOCATION" \
    --logs-destination log-analytics \
    --logs-workspace-id "$LAW_ID" \
    --logs-workspace-key "$LAW_KEY" >/dev/null
else
  echo "✅ CAE exists"
fi

if ! az storage account show -g "$RG" -n "$SA_NAME" &>/dev/null; then
  echo "🚀 Creating Storage $SA_NAME"
  az storage account create -g "$RG" -n "$SA_NAME" -l "$LOCATION" --sku Standard_LRS --kind StorageV2 >/dev/null
else
  echo "✅ Storage exists"
fi

if ! az keyvault show -g "$RG" -n "$KV_NAME" &>/dev/null; then
  echo "🚀 Creating Key Vault $KV_NAME"
  az keyvault create -g "$RG" -n "$KV_NAME" -l "$LOCATION" >/dev/null
else
  echo "✅ Key Vault exists"
fi

# ========= Build & push (context = repo root) =========
echo "📦 Building image ${IMAGE_NAME}:${IMAGE_TAG} in ACR"
repo_root="$(cd "$(dirname "$0")/.." && pwd)"
az acr build -r "$ACR_NAME" -t "${IMAGE_NAME}:${IMAGE_TAG}" "$repo_root" >/dev/null

# ========= Container App =========
if ! az containerapp show -g "$RG" -n "$APP_NAME" &>/dev/null; then
  echo "🚀 Creating Container App $APP_NAME"
  az containerapp create \
    -g "$RG" -n "$APP_NAME" \
    --environment "$CAE_NAME" \
    --image "${ACR_LOGIN_SERVER}/${IMAGE_NAME}:${IMAGE_TAG}" \
    --ingress external --target-port 8501 \
    --registry-server "$ACR_LOGIN_SERVER" \
    --query properties.configuration.ingress.fqdn -o tsv
else
  echo "🔄 Updating Container App image"
  az containerapp update \
    -g "$RG" -n "$APP_NAME" \
    --image "${ACR_LOGIN_SERVER}/${IMAGE_NAME}:${IMAGE_TAG}" >/dev/null
fi

# ========= Identity + ACR pull =========
echo "🔑 Assigning system identity + AcrPull"
az containerapp identity assign -g "$RG" -n "$APP_NAME" --system-assigned >/dev/null || true
APP_PRINCIPAL_ID="$(az containerapp show -g "$RG" -n "$APP_NAME" --query identity.principalId -o tsv)"
ACR_ID="$(az acr show -g "$RG" -n "$ACR_NAME" --query id -o tsv)"
az role assignment create \
  --assignee-object-id "$APP_PRINCIPAL_ID" \
  --assignee-principal-type ServicePrincipal \
  --scope "$ACR_ID" \
  --role "AcrPull" >/dev/null || echo "ℹ️ AcrPull likely exists"
az containerapp registry set -g "$RG" -n "$APP_NAME" --server "$ACR_LOGIN_SERVER" --identity system >/dev/null

# ========= Secrets & envs =========
if [[ -n "${AZURE_API_KEY:-}" ]]; then
  echo "🔒 Setting Container App secret AZURE_API_KEY"
  az containerapp secret set -g "$RG" -n "$APP_NAME" --secrets "AZURE_API_KEY=${AZURE_API_KEY}" >/dev/null
  KEY_REF="AZURE_API_KEY=secretref:AZURE_API_KEY"
else
  echo "⚠️  AZURE_API_KEY not provided; skipping secret."
  KEY_REF=""
fi

env_args=(
  "AZURE_API_BASE=${AZURE_API_BASE}"
  "AZURE_API_VERSION=${AZURE_API_VERSION}"
  "AZURE_OPENAI_DEPLOYMENT=${AZURE_OPENAI_DEPLOYMENT}"
)
[[ -n "$KEY_REF" ]] && env_args+=("$KEY_REF")

if [[ "$(tolower "$DUPLICATE_LANGCHAIN_VARS")" == "true" ]]; then
  env_args+=("AZURE_OPENAI_ENDPOINT=${AZURE_API_BASE}")
  env_args+=("OPENAI_API_VERSION=${AZURE_API_VERSION}")
  [[ -n "$KEY_REF" ]] && env_args+=("AZURE_OPENAI_API_KEY=secretref:AZURE_API_KEY")
fi

echo "🌿 Applying env vars to Container App"
az containerapp update -g "$RG" -n "$APP_NAME" --set-env-vars "${env_args[@]}" >/dev/null

# ========= Output =========
FQDN="$(az containerapp show -g "$RG" -n "$APP_NAME" --query properties.configuration.ingress.fqdn -o tsv)"
echo "🌐 App URL: https://${FQDN}"
