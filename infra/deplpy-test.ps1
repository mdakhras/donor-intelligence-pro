
$RG = $Env:RG
az containerapp env create --name $ACA_ENV_NAME
--resource-group $RG --location $LOCATION 
--logs-destination log-analytics 
--logs-workspace-id 
$(az monitor log-analytics workspace show --resource-group $RG --query customerId -o tsv) --logs-workspace-key $(az monitor log-analytics workspace get-shared-keys --resource-group $RG --workspace-name $LOG_ANALYTICS --query primarySharedKey -o tsv)


# az monitor log-analytics workspace show --resource-group iom-d-we-datallmpoc-rg --query customerId -o tsv


az acr create -g "iom-d-we-datallmpoc-rg" -n "donorintelligenceacr01" -l "westeurope" --sku Basic

az acr build -r iomdwedonorintelligenceacr01 -t donorstreamlit-app:1.0 .

az containerapp env create -g "iom-d-we-datallmpoc-rg" -n "donorintelligence-env" -l "westeurope"

az containerapp env create -g "iom-d-we-datallmpoc-rg" -n "drafter-env" -l  "westeurope"