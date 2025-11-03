# VoiceRAG Agent Billing Guide

This document provides detailed information about VoiceRAG application billing to help you understand and control costs.

---

## Table of Contents

- [Billing Overview](#billing-overview)
- [Azure Service Billing Details](#azure-service-billing-details)
- [Cost Estimation](#cost-estimation)
- [Cost Optimization Recommendations](#cost-optimization-recommendations)
- [Monitoring and Alerts](#monitoring-and-alerts)
- [Cost Control Strategies](#cost-control-strategies)
- [Frequently Asked Questions](#frequently-asked-questions)

---

## Billing Overview

VoiceRAG application uses multiple Azure services, each with different billing models:

| Service | Billing Method | Primary Cost Source |
|---------|---------------|-------------------|
| **Azure OpenAI** | Per Token Usage | GPT-4o Realtime API calls |
| **Azure AI Search** | Per Hour + Storage | Search queries + index storage |
| **Azure Container Apps** | Per Usage | CPU + Memory usage |
| **Azure Blob Storage** | Per Storage + Operations | File storage + read/write operations |
| **Azure Monitor** | Per Data Ingestion | Log and metrics data |

---

## Azure Service Billing Details

### 1. Azure OpenAI Service 💰

**Primary Cost Sources**:
- GPT-4o Realtime API calls
- Text Embedding model usage

#### GPT-4o Realtime API Pricing

| Model | Input Tokens | Output Tokens | Voice Processing |
|-------|-------------|---------------|------------------|
| **gpt-4o-realtime-preview** | $0.005/1K tokens | $0.015/1K tokens | Included in token cost |

**Real Usage Example**:
```
User Question: "What is Contoso Electronics?" (~5 tokens)
AI Response: "Contoso Electronics is a tech company..." (~50 tokens)

Single Conversation Cost:
- Input: 5 tokens × $0.005/1K = $0.000025
- Output: 50 tokens × $0.015/1K = $0.00075
- Total: ~$0.0008 (less than 0.1 cent)
```

#### Text Embedding Pricing

| Model | Pricing |
|-------|---------|
| **text-embedding-3-large** | $0.00013/1K tokens |

**Document Indexing Cost**:
```
100-page PDF document:
- Approximately 50,000 tokens
- Cost: 50 × $0.00013 = $0.0065 (~0.7 cents)
```

### 2. Azure AI Search Service 💰

**Billing Model**: Per hour + storage

| Tier | Hourly Cost | Storage Cost | Query Limits |
|------|-------------|--------------|-------------|
| **Free** | $0 | $0 | 50MB storage, 3 indexes |
| **Basic** | $75/month | $0.25/GB/month | 2GB storage |
| **Standard S1** | $250/month | $0.25/GB/month | 25GB storage |
| **Standard S2** | $500/month | $0.25/GB/month | 100GB storage |
| **Standard S3** | $1000/month | $0.25/GB/month | 200GB storage |

**Your Configuration**: Standard S1 ($250/month)

**Additional Costs**:
- **Semantic Search**: Included in free tier
- **Vector Search**: Included in standard cost
- **Indexer Runs**: Included in standard cost

### 3. Azure Container Apps 💰

**Billing Model**: Per actual CPU and memory usage

| Resource | Pricing |
|----------|---------|
| **vCPU** | $0.000012/vCPU/second |
| **Memory** | $0.0000015/GB/second |

**Your Configuration**:
- Consumption plan
- 1 vCPU, 2GB memory

**Usage Example**:
```
Application running 24 hours:
- vCPU: 1 × 86400 seconds × $0.000012 = $1.04
- Memory: 2GB × 86400 seconds × $0.0000015 = $0.26
- Daily Total: ~$1.30
- Monthly Total: ~$39
```

### 4. Azure Blob Storage 💰

**Billing Model**: Storage + operations

| Item | Pricing |
|------|---------|
| **Storage (LRS)** | $0.0184/GB/month |
| **Read Operations** | $0.0004/10K operations |
| **Write Operations** | $0.005/10K operations |

**Your Usage**:
```
Assuming 1GB document storage:
- Storage Cost: 1GB × $0.0184 = $0.0184/month
- Read Operations: Minimal, negligible
- Write Operations: Minimal, negligible
```

### 5. Azure Monitor / Log Analytics 💰

**Billing Model**: Per data ingestion

| Item | Pricing |
|------|---------|
| **Data Ingestion** | $2.30/GB |
| **Data Retention** | $0.10/GB/month |

**Your Usage**:
```
Assuming 100MB log ingestion per month:
- Ingestion Cost: 0.1GB × $2.30 = $0.23
- Retention Cost: 0.1GB × $0.10 = $0.01
- Monthly Total: ~$0.24
```

---

## Cost Estimation

### Light Usage Scenario (Individual/Small Team)

**Usage Assumptions**:
- 50 conversations per day
- Average 100 tokens per conversation
- Application running 8 hours/day

**Monthly Cost Estimation**:

| Service | Cost | Description |
|---------|------|-------------|
| **Azure OpenAI** | $15 | 50/day × 100tokens × 30days |
| **Azure AI Search** | $250 | Standard S1 fixed cost |
| **Container Apps** | $13 | 8 hours/day × 30 days |
| **Blob Storage** | $1 | 1GB document storage |
| **Azure Monitor** | $1 | Minimal logs |
| **Total** | **~$280/month** | |

### Medium Usage Scenario (Team/Department)

**Usage Assumptions**:
- 200 conversations per day
- Average 150 tokens per conversation
- Application running 12 hours/day

**Monthly Cost Estimation**:

| Service | Cost | Description |
|---------|------|-------------|
| **Azure OpenAI** | $45 | 200/day × 150tokens × 30days |
| **Azure AI Search** | $250 | Standard S1 fixed cost |
| **Container Apps** | $20 | 12 hours/day × 30 days |
| **Blob Storage** | $2 | 2GB document storage |
| **Azure Monitor** | $2 | Moderate log volume |
| **Total** | **~$319/month** | |

### Heavy Usage Scenario (Enterprise)

**Usage Assumptions**:
- 500 conversations per day
- Average 200 tokens per conversation
- Application running 24 hours/day

**Monthly Cost Estimation**:

| Service | Cost | Description |
|---------|------|-------------|
| **Azure OpenAI** | $150 | 500/day × 200tokens × 30days |
| **Azure AI Search** | $500 | May need Standard S2 |
| **Container Apps** | $40 | 24 hours/day × 30 days |
| **Blob Storage** | $5 | 5GB document storage |
| **Azure Monitor** | $5 | High log volume |
| **Total** | **~$700/month** | |

---

## Cost Optimization Recommendations

### 1. Azure OpenAI Optimization

#### Reduce Token Usage
- **Short Responses**: System already configured for short responses
- **Avoid Repeated Queries**: Cache common question answers
- **Optimize Prompts**: Reduce system message length

#### Use Cheaper Models (Optional)
```bash
# If real-time voice is not needed, consider using GPT-4o
azd env set AZURE_OPENAI_REALTIME_DEPLOYMENT "gpt-4o"
```

### 2. Azure AI Search Optimization

#### Downgrade to Basic Tier
```bash
# If query volume is low, you can downgrade
# Modify AI Search service tier in Azure Portal
```

**Basic vs Standard Comparison**:
- **Basic**: $75/month, 2GB storage
- **Standard S1**: $250/month, 25GB storage

#### Optimize Index
- Regularly clean up unnecessary documents
- Use compressed formats for document storage

### 3. Container Apps Optimization

#### Auto-scaling
```yaml
# Configure in azure.yaml
scaling:
  minReplicas: 0  # Scale to 0 when no traffic
  maxReplicas: 3  # Maximum 3 instances
```

#### Resource Limits
```yaml
resources:
  cpu: 0.5        # Reduce CPU allocation
  memory: 1Gi     # Reduce memory allocation
```

### 4. Storage Optimization

#### Lifecycle Management
- Set automatic deletion of old logs
- Compress stored documents

#### Use Cheaper Storage
- Consider using Cool or Archive storage tiers

---

## Monitoring and Alerts

### 1. Set Up Cost Alerts

#### Azure Portal Setup
1. Go to **"Cost Management + Billing"**
2. Click **"Budgets"** → **"Create"**
3. Set budget amount (e.g., $300/month)
4. Configure alert thresholds (e.g., 80%)

#### Alert Configuration
```
Budget: $300/month
Alerts:
- Notify at 50%
- Warning at 80%
- Critical at 100%
```

### 2. Usage Monitoring

#### Azure OpenAI Monitoring
```bash
# Check usage
az cognitiveservices account usage show \
  --name <your-openai-service> \
  --resource-group rg-voicerag-prod
```

#### Container Apps Monitoring
- Check CPU/memory usage in Azure Portal
- Set up auto-scaling rules

### 3. Cost Analysis

#### By Service Analysis
```
Azure Portal → Cost Management → Cost Analysis
→ Group by service to view costs
```

#### By Resource Group Analysis
```
Resource Group: rg-voicerag-prod
→ View cost distribution by service
```

---

## Cost Control Strategies

### 1. Development/Test Environment

#### Use Free Tiers
```bash
# Create development environment
azd env new --environment dev

# Use free services
azd env set AZURE_SEARCH_SKU "free"
azd env set AZURE_CONTAINER_APPS_SKU "consumption"
```

#### Scheduled Shutdown
```bash
# Scheduled shutdown for dev environment
# Use Azure Automation or scheduled tasks
```

### 2. Production Environment Optimization

#### Smart Auto-scaling
- Adjust resources based on usage patterns
- Reduce instances during non-business hours

#### Caching Strategy
- Cache common query results
- Reduce duplicate AI calls

### 3. Cost Budget Management

#### Monthly Budgets
```
Development Environment: $50/month
Test Environment: $100/month
Production Environment: $300/month
```

#### Quarterly Reviews
- Review costs quarterly
- Optimize resource allocation
- Adjust budget allocation

---

## Frequently Asked Questions

### Q1: Why is Azure AI Search so expensive?

**A**: AI Search is a fixed-cost service:
- Standard S1: $250/month (fixed)
- This is the largest cost source
- Consider downgrading to Basic ($75/month) or Free tier

### Q2: How to reduce OpenAI costs?

**A**: Optimization strategies:
- Reduce conversation frequency
- Use shorter prompts
- Cache common answers
- Consider using cheaper models

### Q3: How is Container Apps cost calculated?

**A**: Billed per actual usage:
- Very low cost when no traffic
- Billed per CPU/memory usage when active
- Can set up auto-scaling

### Q4: Can I pause services to save costs?

**A**: Yes, but with limitations:
- **Container Apps**: Can scale to 0 instances
- **AI Search**: Cannot pause, only delete
- **OpenAI**: Cannot pause
- **Blob Storage**: Can delete files

### Q5: How to estimate actual usage costs?

**A**: Use Azure Pricing Calculator:
1. Visit: https://azure.com/e/a87a169b256e43c089015fda8182ca87
2. Enter your usage volume
3. Get accurate price estimation

### Q6: Are there free trial credits?

**A**: Azure provides:
- **New Users**: $200 free credit
- **Students**: Free Azure account
- **Some Services**: 12-month free trial

---

## Cost Comparison Table

| Usage Scenario | Monthly Cost | Primary Cost Source | Optimization Suggestions |
|----------------|--------------|-------------------|------------------------|
| **Personal Use** | $280 | AI Search ($250) | Downgrade to Basic |
| **Small Team** | $319 | AI Search ($250) | Optimize query frequency |
| **Enterprise** | $700 | AI Search + OpenAI | Consider enterprise agreement |
| **Dev/Test** | $50 | Container Apps | Use free services |

---

## Cost Control Checklist

### Monthly Checks
- [ ] Review Azure cost reports
- [ ] Check for unusual expenses
- [ ] Optimize resource allocation
- [ ] Clean up unnecessary resources

### Quarterly Checks
- [ ] Review service tiers
- [ ] Evaluate usage patterns
- [ ] Adjust budget allocation
- [ ] Optimize architecture design

### Annual Checks
- [ ] Assess overall cost-effectiveness
- [ ] Consider enterprise agreements
- [ ] Plan long-term cost strategy
- [ ] Technical architecture optimization

---

## Related Resources

- [Azure Pricing Calculator](https://azure.com/e/a87a169b256e43c089015fda8182ca87)
- [Azure Cost Management Documentation](https://learn.microsoft.com/azure/cost-management-billing/)
- [Azure OpenAI Pricing](https://azure.microsoft.com/pricing/details/cognitive-services/openai-service/)
- [Azure AI Search Pricing](https://azure.microsoft.com/pricing/details/search/)

---

## Getting Help

For cost optimization advice:

1. **Azure Support**: Contact Azure technical support
2. **Cost Advisor**: Use Azure Advisor for recommendations
3. **Community Forums**: Azure community forums
4. **Project Issues**: Submit issues on GitHub project page

---

**Last Updated**: October 2025  
**Version**: VoiceRAG v1.0  
**Maintainer**: VoiceRAG Team

