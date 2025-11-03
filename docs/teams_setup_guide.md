# VoiceRAG Teams Integration Setup Guide

## Overview

This guide provides step-by-step instructions for integrating VoiceRAG with Microsoft Teams. It is designed for administrators and IT personnel responsible for deploying and configuring the Teams integration.

## Prerequisites

Before starting the Teams integration, ensure you have:

1. **Existing VoiceRAG Deployment**
   - VoiceRAG application deployed on Azure Container Apps
   - Backend API accessible via HTTPS
   - Azure OpenAI and AI Search services configured
   - Knowledge base index created and populated

2. **Azure Resources**
   - Azure subscription with appropriate permissions
   - Access to Azure Portal for Bot Service creation
   - Admin rights to deploy Teams apps

3. **Teams Access**
   - Microsoft Teams admin rights (for organization-wide deployment)
   - OR individual Teams access (for personal testing)

4. **Required Tools**
   - Azure CLI installed and configured
   - Access to Azure Portal
   - Teams admin console access (if deploying organization-wide)

## Integration Options

Choose the integration method that best suits your organization's needs:

### Option 1: Azure Bot Service Integration

**Best for:** Production deployments, organization-wide rollout, rich Teams features

**Benefits:**
- Native Teams integration
- Automatic scaling
- Built-in authentication
- Analytics and monitoring
- Supports adaptive cards and rich UI

**Requirements:**
- Azure Bot Service resource
- Teams app package deployment
- Azure AD app registration

**Time to Deploy:** 2-4 hours

### Option 2: Incoming Webhook Integration

**Best for:** Quick implementation, limited budget, simple use cases

**Benefits:**
- No additional Azure resources needed
- Quick setup (15-30 minutes)
- Direct integration with existing backend
- No Teams app approval required

**Limitations:**
- One-way communication only
- No rich UI elements
- Manual response handling

**Time to Deploy:** 15-30 minutes

### Option 3: Messaging Extension

**Best for:** Search-focused usage, minimal installation barrier

**Benefits:**
- Appears in Teams compose box
- Easy for users to discover
- Can share results as cards
- No ongoing conversation required

**Requirements:**
- Teams app manifest
- Azure AD registration
- Backend HTTP API endpoint

**Time to Deploy:** 1-2 hours

## Setup Instructions: Azure Bot Service (Option 1)

### Step 1: Register Azure AD Application

1. Navigate to Azure Portal
2. Go to **Microsoft Entra ID** → **App registrations**
3. Click **New registration**
4. Configure:
   - Name: `VoiceRAG Teams Bot`
   - Supported account types: **Accounts in any organizational directory**
   - Redirect URI: Leave empty for now
5. Click **Register**
6. Note down the **Application (client) ID** and **Directory (tenant) ID**
7. Go to **Certificates & secrets**
8. Create a new client secret
9. Note down the secret value (you'll need this)

### Step 2: Create Azure Bot Service

1. In Azure Portal, click **Create a resource**
2. Search for **Azure Bot**
3. Click **Create**
4. Configure the bot:
   - Bot handle: `voicerag-teams-bot`
   - Subscription: Your subscription
   - Resource group: Your VoiceRAG resource group
   - Pricing tier: F0 (Free) or S1 (Standard)
   - Microsoft App ID: The Application ID from Step 1
5. Click **Review + create**, then **Create**

### Step 3: Configure Bot Service

1. Open the newly created Bot Service resource
2. Go to **Channels**
3. Click **Microsoft Teams**
4. Click **Apply** to enable Teams channel
5. Go to **Configuration**
6. Under **Messaging endpoint**, enter your VoiceRAG backend URL:
   - Format: `https://<your-voice-rag-url>/api/bot`
   - Example: `https://capps-backend-bgvscddssk7zk.azurecontainerapps.io/api/bot`
7. Click **Apply**

### Step 4: Extend Backend for Bot Support

The VoiceRAG backend needs to be extended with Bot Framework SDK support. This requires:

1. Add Bot Framework SDK to dependencies
2. Add bot endpoint handler to backend
3. Implement message processing logic
4. Deploy updated backend

**Note:** This requires code changes and redeployment. Refer to `docs/teams_integration_guide.md` for implementation details.

### Step 5: Create Teams App Package

1. Download the Teams App Manifest template
2. Edit the manifest file with your bot details:
   - Replace `<bot-id>` with your Bot Service Application ID
   - Configure bot name and descriptions
   - Add company information
   - Set valid domains
3. Create icon files (192x192 and 32x32 pixels)
4. Package everything into a ZIP file

### Step 6: Deploy to Teams

**Option A: Upload to Teams (for testing)**
1. Open Microsoft Teams
2. Go to **Apps** → **Upload a custom app**
3. Select your ZIP package
4. The bot appears in your personal app list

**Option B: Publish to App Catalog (for organization)**
1. Use Teams Admin Center
2. Navigate to **Teams apps** → **Upload**
3. Upload the app package
4. Approve for organization use

### Step 7: Test Integration

1. Open Teams and start a chat with your bot
2. Send a test message: `Hello`
3. Verify the bot responds
4. Send a knowledge base query: `What is our vacation policy?`
5. Verify the bot searches and returns relevant information

## Setup Instructions: Incoming Webhook (Option 2)

### Step 1: Create Webhook in Teams

1. Go to your Teams channel or chat
2. Click **Connectors** or **+ Apps**
3. Search for **Incoming Webhook**
4. Click **Add** or **Configure**
5. Name the webhook (e.g., "VoiceRAG Knowledge")
6. Optionally upload a custom image
7. Click **Create**
8. Copy the webhook URL

### Step 2: Configure Backend Endpoint

1. Ensure your VoiceRAG backend has a webhook handler endpoint
2. The endpoint should accept POST requests with JSON payload
3. Expected payload format:
```json
{
  "text": "user question here",
  "username": "user name",
  "channel": "channel name"
}
```

### Step 3: Set Up Backend Processing

1. Backend receives webhook POST
2. Extracts user question from payload
3. Processes through VoiceRAG (search knowledge base, generate answer)
4. Posts response back to Teams channel

### Step 4: Test Integration

1. Post a message in the Teams channel with the webhook
2. Verify backend receives the webhook
3. Confirm response is posted back to channel

## Setup Instructions: Messaging Extension (Option 3)

### Step 1: Create App Manifest

1. Create a new Teams app manifest (teams-manifest.json)
2. Configure messaging extension settings
3. Set search commands and parameters
4. Configure backend API endpoint

### Step 2: Register in Azure AD

Follow the same Azure AD registration steps as Option 1 (Steps 1-3)

### Step 3: Upload to Teams

1. Package manifest with required icons
2. Upload to Teams using the Apps interface
3. Approve for use

### Step 4: Configure Backend

1. Add messaging extension endpoint to VoiceRAG backend
2. Endpoint should accept search queries
3. Return results in Teams adaptive card format

### Step 5: Test in Teams

1. Open Teams compose box
2. Search for your bot name
3. Enter a query
4. Select result to view answer in chat

## Post-Deployment Configuration

### Monitoring

1. **Azure Portal Monitoring**
   - Monitor Bot Service metrics
   - Track API call volumes
   - Monitor error rates

2. **Teams Admin Center**
   - View bot usage statistics
   - Monitor user feedback
   - Track engagement metrics

3. **Application Insights** (if configured)
   - Monitor backend performance
   - Track latency
   - Analyze query patterns

### Authentication

Ensure proper authentication is configured:

1. **Bot Authentication**
   - Verify Bot Service authentication settings
   - Test with Teams credentials
   - Verify Entra ID integration

2. **Backend Authentication**
   - Ensure VoiceRAG backend validates Teams requests
   - Verify app ID and password authentication
   - Test with invalid credentials

### Security

1. **Network Security**
   - Verify HTTPS endpoints
   - Check firewall rules for Bot Service
   - Validate CORS settings

2. **Data Privacy**
   - Review conversation data storage
   - Ensure compliance with organizational policies
   - Configure data retention policies

## Troubleshooting

### Bot Not Responding

**Symptom:** Bot doesn't reply to messages

**Possible causes:**
- Backend endpoint not reachable
- Authentication failure
- Bot Service not connected to Teams

**Solutions:**
1. Verify backend URL in Bot Service configuration
2. Test backend endpoint directly with curl or Postman
3. Check Bot Service health status in Azure Portal
4. Review authentication credentials

### Authentication Errors

**Symptom:** Users see authentication errors when interacting with bot

**Solutions:**
1. Verify Azure AD app registration
2. Check app ID and password configuration
3. Ensure Teams app is properly registered
4. Clear Teams cache and retry

### Performance Issues

**Symptom:** Slow response times

**Solutions:**
1. Monitor backend API response times
2. Check Azure OpenAI quota and limits
3. Review AI Search index performance
4. Scale Bot Service if needed (upgrade from F0 to S1)

### Incomplete Responses

**Symptom:** Bot responses are cut off or incomplete

**Solutions:**
1. Check Teams message length limits
2. Review backend response formatting
3. Verify adaptive card payload size
4. Implement response pagination if needed

## Cost Considerations

### Azure Bot Service Costs

- **F0 (Free Tier):** 10,000 messages per month at no cost
- **S1 (Standard):** $0.50 per 1,000 messages after free tier

### Backend API Costs

- No additional Azure Container Apps costs (already deployed)
- OpenAI API costs apply per request
- AI Search costs apply per query

### Estimated Monthly Costs

For typical use (100 users, 10 messages per user per day):
- Bot Service: Free (within F0 tier)
- OpenAI API: Varies by model and usage
- AI Search: Existing costs apply
- **Total additional cost: ~$0-50/month** (depending on query volume)

## Maintenance

### Regular Tasks

1. **Monitor Usage**
   - Review bot usage weekly
   - Track popular queries
   - Identify knowledge gaps

2. **Update Knowledge Base**
   - Add new documents as needed
   - Remove outdated information
   - Update search index

3. **Update Bot Responses**
   - Refine system prompts as needed
   - Update error messages
   - Improve answer quality based on user feedback

### Upgrading

When VoiceRAG backend is upgraded:
1. Update bot endpoint if URL changes
2. Test bot functionality with new backend
3. Verify authentication still works
4. Monitor for errors post-upgrade

## Support and Resources

### Internal Support

- IT Helpdesk: For technical issues
- VoiceRAG Admin: For knowledge base updates
- Teams Admin: For Teams configuration issues

### Documentation

- User Guide: `docs/teams_user_guide.md`
- Technical Implementation: `docs/teams_integration_guide.md`
- Azure Bot Service: [Microsoft Documentation](https://learn.microsoft.com/en-us/azure/bot-service/)

### Training

Provide users with:
1. Quick start guide (first 3 questions to try)
2. Best practices for asking questions
3. Troubleshooting common issues
4. Where to find additional help

## Next Steps

After successful deployment:

1. Announce the Teams integration to users
2. Provide user training or documentation
3. Gather feedback and monitor usage
4. Iterate based on user needs
5. Consider additional enhancements (adaptive cards, file upload, etc.)

## Checklist

Use this checklist to ensure complete deployment:

- [ ] Azure AD app registered
- [ ] Azure Bot Service created
- [ ] Teams channel configured
- [ ] Backend endpoint updated and deployed
- [ ] Teams app package created
- [ ] App uploaded to Teams
- [ ] Integration tested in Teams
- [ ] Authentication verified
- [ ] Monitoring configured
- [ ] User documentation provided
- [ ] Support contacts identified
- [ ] Cost monitoring set up
- [ ] Rollout announcement prepared



