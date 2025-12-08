# Salesforce 环境变量配置到 Azure Container Apps

## 问题

部署到 Azure 后无法获取产品信息，因为 Salesforce 的环境变量没有配置到 Azure Container Apps 中。

## 解决方案

有两种方法可以将 Salesforce 环境变量添加到 Azure Container Apps：

### 方法 1: 使用 Azure Portal（推荐，最简单）

1. **登录 Azure Portal**
   - 访问 https://portal.azure.com

2. **找到 Container App**
   - 搜索你的 Container App 名称（如 `capps-backend-bgvscddssk7zk`）
   - 点击进入

3. **配置环境变量**
   - 左侧菜单 → **Environment variables**
   - 点击 **"Add"** 或 **"Edit"** 按钮
   - 添加以下环境变量：

   | 变量名 | 值 | 说明 |
   |--------|-----|------|
   | `SALESFORCE_INSTANCE_URL` | `https://yourinstance.salesforce.com` | 你的 Salesforce 实例 URL |
   | `SALESFORCE_USERNAME` | `your-username@example.com` | Salesforce 用户名 |
   | `SALESFORCE_PASSWORD` | `your-password` | Salesforce 密码 |
   | `SALESFORCE_SECURITY_TOKEN` | `your-security-token` | Security Token |
   | `SALESFORCE_CONSUMER_KEY` | `your-consumer-key` | Connected App 的 Consumer Key |
   | `SALESFORCE_CONSUMER_SECRET` | `your-consumer-secret` | Connected App 的 Consumer Secret |

4. **保存并重启**
   - 点击 **"Save"**
   - 等待配置生效（通常需要 1-2 分钟）
   - Container App 会自动重启以应用新的环境变量

### 方法 2: 使用 Azure CLI

```bash
# 设置环境变量
az containerapp update \
  --name capps-backend-bgvscddssk7zk \
  --resource-group rg-voicerag-prod \
  --set-env-vars \
    SALESFORCE_INSTANCE_URL="https://yourinstance.salesforce.com" \
    SALESFORCE_USERNAME="your-username@example.com" \
    SALESFORCE_PASSWORD="your-password" \
    SALESFORCE_SECURITY_TOKEN="your-security-token" \
    SALESFORCE_CONSUMER_KEY="your-consumer-key" \
    SALESFORCE_CONSUMER_SECRET="your-consumer-secret"
```

### 方法 3: 使用 Azure Key Vault（推荐用于生产环境）

对于敏感信息（如密码、密钥），建议使用 Azure Key Vault：

1. **创建 Key Vault Secret**
   ```bash
   az keyvault secret set \
     --vault-name <your-keyvault-name> \
     --name "salesforce-password" \
     --value "your-password"
   ```

2. **在 Container App 中引用**
   - 在 Azure Portal 的 Container App → Environment variables
   - 添加环境变量时，选择 **"Reference Key Vault secret"**
   - 选择对应的 Key Vault 和 Secret

## 验证配置

### 1. 检查环境变量

在 Azure Portal 中：
- Container App → **Environment variables**
- 确认所有 Salesforce 环境变量都已配置

### 2. 查看日志

在 Azure Portal 中：
- Container App → **Log stream** 或 **Logs**
- 查找 Salesforce 连接相关的日志：
  - `Successfully connected to Salesforce` - 连接成功
  - `Salesforce credentials not configured` - 配置缺失
  - `Failed to connect to Salesforce` - 连接失败

### 3. 测试 API

访问产品 API 端点：
```
https://capps-backend-xxx.xxx.azurecontainerapps.io/api/products
```

应该返回产品列表，而不是空数组。

## 常见问题

### Q: 为什么本地可以工作，但 Azure 不行？

**A**: 本地开发时，环境变量从 `.env` 文件加载。Azure Container Apps 需要单独配置环境变量。

### Q: 环境变量配置后多久生效？

**A**: 通常 1-2 分钟。Container App 会自动重启以应用新配置。

### Q: 如何查看环境变量是否生效？

**A**: 
1. 在 Azure Portal 中查看 Environment variables
2. 查看 Log stream 中的启动日志
3. 测试 API 端点

### Q: 可以使用 Secret 而不是明文吗？

**A**: 可以。在 Azure Portal 中添加环境变量时，可以选择 **"Secret"** 类型，这样值会被隐藏。

## 安全建议

1. **使用 Azure Key Vault**：存储敏感信息（密码、密钥）
2. **使用 Secret 类型**：在环境变量中标记为 Secret
3. **定期轮换**：定期更新密码和 Security Token
4. **最小权限**：确保 Salesforce 用户只有必要的权限

## 相关文档

- [Salesforce 设置指南](salesforce_setup_guide_zh.md)
- [Salesforce 生产环境部署](salesforce_production_deployment.md)
- [更新部署指南](../更新部署指南.md)


