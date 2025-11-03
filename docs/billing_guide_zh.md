# VoiceRAG Agent 使用计费说明（中文版）

本文档详细说明 VoiceRAG 应用的使用计费情况，帮助您了解和控制成本。

> 📖 **English Version**: See [billing_guide.md](./billing_guide.md) for the full English documentation.

---

## 💰 计费概览

VoiceRAG 应用使用多个 Azure 服务，主要成本来源：

| 服务 | 计费方式 | 主要成本 |
|------|----------|----------|
| **Azure AI Search** | 固定月费 | $250/月 (最大成本) |
| **Azure OpenAI** | 按使用量 | $0.005-0.015/1K tokens |
| **Container Apps** | 按使用量 | ~$1.30/天 |
| **Blob Storage** | 按存储量 | ~$0.02/GB/月 |
| **Azure Monitor** | 按日志量 | ~$0.24/月 |

---

## 📊 成本估算

### 轻度使用（个人/小团队）
**假设**: 每天 50 次对话，应用运行 8 小时

| 服务 | 费用 | 说明 |
|------|------|------|
| Azure AI Search | $250 | Standard S1 固定费用 |
| Azure OpenAI | $15 | 对话 Token 费用 |
| Container Apps | $13 | 8小时/天运行 |
| 其他服务 | $2 | 存储和监控 |
| **总计** | **~$280/月** | |

### 中度使用（团队/部门）
**假设**: 每天 200 次对话，应用运行 12 小时

| 服务 | 费用 | 说明 |
|------|------|------|
| Azure AI Search | $250 | Standard S1 固定费用 |
| Azure OpenAI | $45 | 更多对话 |
| Container Apps | $20 | 12小时/天运行 |
| 其他服务 | $4 | 存储和监控 |
| **总计** | **~$319/月** | |

### 重度使用（企业级）
**假设**: 每天 500 次对话，应用运行 24 小时

| 服务 | 费用 | 说明 |
|------|------|------|
| Azure AI Search | $500 | 可能需要 S2 |
| Azure OpenAI | $150 | 大量对话 |
| Container Apps | $40 | 24小时运行 |
| 其他服务 | $10 | 存储和监控 |
| **总计** | **~$700/月** | |

---

## 🎯 成本优化建议

### 1. 最大成本来源：Azure AI Search

**当前配置**: Standard S1 ($250/月)

**优化选项**：
- ✅ **降级到 Basic**: $75/月（节省 $175）
- ✅ **降级到 Free**: $0/月（限制较多）

**降级方法**：
```
Azure Portal → AI Search 服务 → 定价层 → 选择 Basic
```

### 2. Azure OpenAI 优化

**减少 Token 使用**：
- ✅ 系统已配置简短回答
- ✅ 避免重复查询
- ✅ 缓存常见答案

**单次对话成本**：
```
用户问题: "What is Contoso?" (5 tokens)
AI 回答: "Contoso is..." (50 tokens)

成本: ~$0.0008 (不到 0.1 美分)
```

### 3. Container Apps 优化

**自动扩缩容**：
```yaml
# 无流量时缩放到 0，节省费用
scaling:
  minReplicas: 0
  maxReplicas: 3
```

**资源限制**：
```yaml
# 减少 CPU 和内存分配
resources:
  cpu: 0.5
  memory: 1Gi
```

---

## 📈 监控和告警

### 设置成本告警

**Azure Portal 设置**：
1. 进入 **"成本管理 + 计费"**
2. 点击 **"预算"** → **"创建"**
3. 设置预算：$300/月
4. 配置告警：50%、80%、100%

### 使用情况监控

**查看 OpenAI 使用量**：
```bash
az cognitiveservices account usage show \
  --name <your-service> \
  --resource-group rg-voicerag-prod
```

**Container Apps 监控**：
- Azure Portal → Container Apps → 监控
- 查看 CPU/内存使用率

---

## 💡 成本控制策略

### 开发/测试环境

**使用免费服务**：
```bash
# 创建开发环境
azd env new --environment dev

# 使用免费层级
azd env set AZURE_SEARCH_SKU "free"
```

**定时关闭**：
- 非工作时间自动缩放到 0
- 使用 Azure Automation

### 生产环境优化

**智能扩缩容**：
- 根据使用模式调整
- 非工作时间减少实例

**缓存策略**：
- 缓存常见查询
- 减少重复 AI 调用

---

## ❓ 常见问题

### Q: 为什么 AI Search 费用这么高？
**A**: Standard S1 是固定 $250/月，这是最大成本。考虑降级到 Basic ($75/月)。

### Q: 如何减少 OpenAI 费用？
**A**: 
- 减少对话次数
- 使用更短的回答
- 缓存常见答案

### Q: 可以暂停服务节省费用吗？
**A**: 
- ✅ Container Apps: 可以缩放到 0
- ❌ AI Search: 无法暂停
- ❌ OpenAI: 无法暂停

### Q: 有免费试用吗？
**A**: Azure 提供：
- 新用户 $200 免费额度
- 学生免费账户
- 12 个月免费试用

### Q: 如何预估实际成本？
**A**: 使用 [Azure 定价计算器](https://azure.com/e/a87a169b256e43c089015fda8182ca87)

---

## 📋 成本控制检查清单

### 每月检查
- [ ] 查看 Azure 成本报告
- [ ] 检查异常费用
- [ ] 优化资源配置
- [ ] 清理不需要的资源

### 每季度检查
- [ ] 审查服务层级
- [ ] 评估使用模式
- [ ] 调整预算分配
- [ ] 优化架构设计

---

## 🎯 快速成本优化

### 立即可做（节省 $175/月）

1. **降级 AI Search**：
   ```
   Azure Portal → AI Search → 定价层 → Basic
   从 $250/月 → $75/月
   ```

2. **设置自动扩缩容**：
   ```
   Container Apps → 自动扩缩容 → 最小副本 0
   ```

3. **设置成本告警**：
   ```
   成本管理 → 预算 → 创建 $200/月预算
   ```

### 长期优化

1. **监控使用模式**
2. **优化查询频率**
3. **考虑企业协议**
4. **定期审查成本**

---

## 📊 成本对比

| 优化方案 | 月度成本 | 节省金额 | 影响 |
|----------|----------|----------|------|
| **当前配置** | $280 | - | 标准功能 |
| **降级到 Basic** | $105 | $175 | 存储限制 2GB |
| **降级到 Free** | $30 | $250 | 存储限制 50MB |
| **开发环境** | $50 | $230 | 仅开发使用 |

---

## 📚 相关资源

- [Azure 定价计算器](https://azure.com/e/a87a169b256e43c089015fda8182ca87)
- [Azure 成本管理](https://learn.microsoft.com/azure/cost-management-billing/)
- [Azure OpenAI 定价](https://azure.microsoft.com/pricing/details/cognitive-services/openai-service/)

---

## 🚀 立即行动

**今天就可以做的优化**：

1. ✅ **设置成本告警**（5分钟）
2. ✅ **降级 AI Search 到 Basic**（节省 $175/月）
3. ✅ **配置自动扩缩容**（节省 Container Apps 费用）

**预计节省**: $200+/月

---

**祝您成本控制成功！** 💰

---

*最后更新: 2025年10月*

