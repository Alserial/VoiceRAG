# update-backend.ps1
# Use UTF-8 (no BOM) encoding

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

# Configuration
$AppName       = "capps-backend-bgvscddssk7zk"
$ResourceGroup = "rg-voicerag-prod"
$RegistryName  = "voiceragprodacrbgvscddssk7zk"
$Repository    = "aisearch-openai-rag-audio/backend-voicerag-prod"

# Check prerequisites
if (-not (Get-Command az -ErrorAction SilentlyContinue)) {
    throw "Azure CLI (az) not found. Please install and log in: https://learn.microsoft.com/cli/azure/install-azure-cli"
}

try {
    az account show --only-show-errors | Out-Null
} catch {
    throw "Azure CLI is not logged in. Please run: az login"
}

# Retrieve ACR login server
$LoginServer = az acr show -n $RegistryName --query loginServer -o tsv --only-show-errors
if (-not $LoginServer) { throw "Failed to get ACR login server. Check RegistryName: $RegistryName" }

# Retrieve the latest tag
$LatestTag = az acr repository show-tags `
    -n $RegistryName `
    --repository $Repository `
    --orderby time_desc `
    --top 1 `
    -o tsv `
    --only-show-errors

if (-not $LatestTag) { throw "No tags found in repository: $Repository" }

# Get current image and build target image
$CurrentImage = az containerapp show `
    --name $AppName `
    --resource-group $ResourceGroup `
    --query 'properties.template.containers[0].image' `
    -o tsv `
    --only-show-errors

$TargetImage = [string]::Format("{0}/{1}:{2}", $LoginServer, $Repository, $LatestTag)

Write-Host ""
Write-Host "Latest image : $TargetImage"
Write-Host "Current image: $CurrentImage"
Write-Host ""

if ($CurrentImage -eq $TargetImage) {
    Write-Host "Already using the latest image. No update required."
    exit 0
}

# Update container app
Write-Host "Updating Container App to the latest image..."
az containerapp update `
    --name $AppName `
    --resource-group $ResourceGroup `
    --image $TargetImage `
    --only-show-errors | Out-Null

Write-Host ""
Write-Host "Update completed. Latest revisions:"
az containerapp revision list `
    --name $AppName `
    --resource-group $ResourceGroup `
    --query '[].{Name:name,Active:properties.active,Image:properties.template.containers[0].image,Created:properties.createdTime}' `
    -o table `
    --only-show-errors
