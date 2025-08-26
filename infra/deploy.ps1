# ================================
# deploy.ps1 - Azure Infra & Deploy for Streamlit App
# ================================

# Load .env variables
$envPath = Join-Path $PSScriptRoot ".env"
if (-Not (Test-Path $envPath)) {
    Write-Error ".env file not found in infra/ directory"
    exit 1
}

Get-Content $envPath | ForEach-Object {
    if ($_ -match "^\s*#") { return }
    if ($_ -match "^\s*$") { return }
    $name, $value = $_ -split "=", 2
    Set-Item -Path Env:$name -Value $value
}

# Load into variables
$LOCATION              = $Env:LOCATION
$RG                    = $Env:RG
$PREFIX_HYPHEN          = $Env:PREFIX_HYPHEN
$SUFFIX                = $Env:SUFFIX
$IMAGE_NAME            = $Env:IMAGE_NAME
$IMAGE_TAG             = $Env:IMAGE_TAG
$AZURE_API_BASE        = $Env:AZURE_API_BASE
$AZURE_API_VERSION     = $Env:AZURE_API_VERSION
$AZURE_OPENAI_DEPLOYMENT = $Env:AZURE_OPENAI_DEPLOYMENT
$AZURE_API_KEY         = $Env:AZURE_API_KEY
$DUPLICATE_LANGCHAIN_VARS = $Env:DUPLICATE_LANGCHAIN_VARS

# Azure resource names
$ACR_NAME          = "${PREFIX_HYPHEN}acr${SUFFIX}" -replace "-", ""
$ACA_ENV_NAME      = "${PREFIX_HYPHEN}-aca-env-${SUFFIX}"
$ACA_APP_NAME      = "${PREFIX_HYPHEN}-aca-streamlit-${SUFFIX}"
$LOG_ANALYTICS     = "${PREFIX_HYPHEN}-log-${SUFFIX}"
$STORAGE_NAME      = ("${PREFIX_HYPHEN}st${SUFFIX}" -replace "-", "")
$KV_NAME           = "${PREFIX_HYPHEN}-kv-${SUFFIX}"

# Create Azure resources
Write-Host "🔹 Creating Azure Container Registry: ${ACR_NAME}"
az acr create --resource-group $RG --name $ACR_NAME --sku Basic --location $LOCATION --admin-enabled true

Write-Host "🔹 Creating Log Analytics workspace: ${LOG_ANALYTICS}"
az monitor log-analytics workspace create --resource-group $RG --workspace-name $LOG_ANALYTICS --location $LOCATION

Write-Host "🔹 Creating Container Apps environment: ${ACA_ENV_NAME}"
az containerapp env create --name $ACA_ENV_NAME --resource-group $RG --location $LOCATION --logs-destination log-analytics --logs-workspace-id $(az monitor log-analytics workspace show --resource-group $RG --workspace-name $LOG_ANALYTICS --query customerId -o tsv) --logs-workspace-key $(az monitor log-analytics workspace get-shared-keys --resource-group $RG --workspace-name $LOG_ANALYTICS --query primarySharedKey -o tsv)

Write-Host "🔹 Creating Storage Account: ${STORAGE_NAME}"
az storage account create --name $STORAGE_NAME --resource-group $RG --location $LOCATION --sku Standard_LRS

Write-Host "🔹 Creating Key Vault: ${KV_NAME}"
az keyvault create --name $KV_NAME --resource-group $RG --location $LOCATION

# Build & push image to ACR
Write-Host "📦 Building image ${IMAGE_NAME}:${IMAGE_TAG} in ACR"
az acr build --registry $ACR_NAME --image "${IMAGE_NAME}:${IMAGE_TAG}" ..

# Create Container App
Write-Host "🚀 Creating Container App: ${ACA_APP_NAME}"
az containerapp create `
    --name $ACA_APP_NAME `
    --resource-group $RG `
    --environment $ACA_ENV_NAME `
    --image "${ACR_NAME}.azurecr.io/${IMAGE_NAME}:${IMAGE_TAG}" `
    --target-port 8501 `
    --ingress external `
    --min-replicas 1 `
    --max-replicas 1 `
    --registry-server "${ACR_NAME}.azurecr.io" `
    --query configuration.ingress.fqdn

# Store secrets in Key Vault
Write-Host "🔑 Storing API keys in Key Vault"
az keyvault secret set --vault-name $KV_NAME --name "AZURE-API-BASE" --value $AZURE_API_BASE
az keyvault secret set --vault-name $KV_NAME --name "AZURE-API-VERSION" --value $AZURE_API_VERSION
az keyvault secret set --vault-name $KV_NAME --name "AZURE-OPENAI-DEPLOYMENT" --value $AZURE_OPENAI_DEPLOYMENT
az keyvault secret set --vault-name $KV_NAME --name "AZURE-API-KEY" --value $AZURE_API_KEY

Write-Host "✅ Deployment complete!"
