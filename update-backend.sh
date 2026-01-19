#!/bin/bash
# update-backend.sh
# Mac/Linux version of update-backend.ps1

set -euo pipefail

# Configuration
AppName="capps-backend-bgvscddssk7zk"
ResourceGroup="rg-voicerag-prod"
RegistryName="voiceragprodacrbgvscddssk7zk"
Repository="aisearch-openai-rag-audio/backend-voicerag-prod"

# Check prerequisites
if ! command -v az &> /dev/null; then
    echo "Error: Azure CLI (az) not found. Please install and log in: https://learn.microsoft.com/cli/azure/install-azure-cli" >&2
    exit 1
fi

# Check if logged in
if ! az account show --only-show-errors &> /dev/null; then
    echo "Error: Azure CLI is not logged in. Please run: az login" >&2
    exit 1
fi

# Retrieve ACR login server
LoginServer=$(az acr show -n "$RegistryName" --query loginServer -o tsv --only-show-errors)
if [ -z "$LoginServer" ]; then
    echo "Error: Failed to get ACR login server. Check RegistryName: $RegistryName" >&2
    exit 1
fi

# Retrieve the latest tag
LatestTag=$(az acr repository show-tags \
    -n "$RegistryName" \
    --repository "$Repository" \
    --orderby time_desc \
    --top 1 \
    -o tsv \
    --only-show-errors)

if [ -z "$LatestTag" ]; then
    echo "Error: No tags found in repository: $Repository" >&2
    exit 1
fi

# Get current image and build target image
CurrentImage=$(az containerapp show \
    --name "$AppName" \
    --resource-group "$ResourceGroup" \
    --query 'properties.template.containers[0].image' \
    -o tsv \
    --only-show-errors)

TargetImage="${LoginServer}/${Repository}:${LatestTag}"

echo ""
echo "Latest image : $TargetImage"
echo "Current image: $CurrentImage"
echo ""

if [ "$CurrentImage" == "$TargetImage" ]; then
    echo "Already using the latest image. No update required."
    exit 0
fi

# Update container app
echo "Updating Container App to the latest image..."
az containerapp update \
    --name "$AppName" \
    --resource-group "$ResourceGroup" \
    --image "$TargetImage" \
    --only-show-errors > /dev/null

echo ""
echo "Update completed. Latest revisions:"
az containerapp revision list \
    --name "$AppName" \
    --resource-group "$ResourceGroup" \
    --query '[].{Name:name,Active:properties.active,Image:properties.template.containers[0].image,Created:properties.createdTime}' \
    -o table \
    --only-show-errors
