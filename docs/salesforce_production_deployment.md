# Salesforce 生产环境部署配置指南

## 概述

当 VoiceRAG 应用部署到 Azure 云端后，需要更新 Salesforce Connected App 的 Callback URL 配置。

## 重要说明

虽然当前应用使用的是 **Username-Password OAuth Flow**（服务器端认证），这个流程实际上**不需要** callback URL 来完成认证。但是：

1. **Salesforce 要求配置**：即使不使用，Connected App 仍然要求配置至少一个 Callback URL
2. **安全验证**：Salesforce 可能会验证 Callback URL 的有效性
3. **最佳实践**：配置正确的生产环境 URL 可以避免潜在问题

## 更新步骤

### 1. 获取生产环境 URL

部署到 Azure 后，你的应用 URL 格式通常是：
```
https://capps-backend-xxx.xxx.azurecontainerapps.io
```

**查找方法：**
- 在 Azure Portal 中，进入你的 Container App
- 在 **Overview** 页面，查看 **Application Url** 字段
- 或者运行：`azd show` 查看部署信息

### 2. 更新 Salesforce Connected App

1. **登录 Salesforce**
   - 进入你的 Salesforce 组织（Production 或 Sandbox）

2. **进入 App Manager**
   - 点击右上角设置图标 ⚙️
   - 选择 **Setup**
   - 在 Quick Find 搜索框输入：**"App Manager"**
   - 点击 **App Manager**

3. **找到 Connected App**
   - 在应用列表中找到你的 Connected App（如 "VoiceRAG Integration"）
   - 点击应用名称进入详情页

4. **编辑配置**
   - 点击 **Edit** 按钮
   - 滚动到 **OAuth Settings** 部分
   - 找到 **Callback URL** 字段

5. **更新 Callback URL**
   
   **选项 A：只保留生产环境 URL**
   ```
   https://capps-backend-xxx.xxx.azurecontainerapps.io
   ```
   
   **选项 B：同时保留开发和生产环境 URL（推荐）**
   ```
   http://localhost:8765
   https://capps-backend-xxx.xxx.azurecontainerapps.io
   ```
   
   > **注意**：可以配置多个 Callback URL，每行一个

6. **保存更改**
   - 点击 **Save** 按钮
   - 等待 2-5 分钟让更改生效

## 验证配置

### 方法 1：检查 Connected App 配置

1. 在 Salesforce 中查看 Connected App 详情
2. 确认 Callback URL 包含生产环境 URL
3. 确认 Connected App 状态为 **Active**

### 方法 2：测试连接

运行测试脚本验证 Salesforce 连接：

```bash
cd app/backend
python test_salesforce_auth.py
```

如果看到 "Authentication successful!"，说明配置正确。

### 方法 3：测试报价功能

1. 访问生产环境应用
2. 使用语音或文本创建一个报价请求
3. 检查是否成功创建 Salesforce Quote
4. 查看应用日志确认没有 OAuth 相关错误

## 常见问题

### Q: 如果不更新 Callback URL 会怎样？

**A**: 由于使用的是 Username-Password Flow，理论上不会影响功能。但 Salesforce 可能会：
- 在 Connected App 审核时标记为配置不完整
- 在某些安全策略下拒绝连接
- 产生警告日志

**建议**：为了最佳实践和避免潜在问题，还是应该更新。

### Q: 可以配置多个 Callback URL 吗？

**A**: 可以。Salesforce 支持配置多个 Callback URL，每行一个。这样你可以同时支持：
- 本地开发：`http://localhost:8765`
- 生产环境：`https://capps-backend-xxx.xxx.azurecontainerapps.io`
- 测试环境：`https://test-env.xxx.azurecontainerapps.io`

### Q: 更新后需要重新获取 Consumer Key 和 Secret 吗？

**A**: 不需要。更新 Callback URL 不会改变 Consumer Key 和 Consumer Secret，你的 `.env` 文件中的配置无需修改。

### Q: 如何找到我的 Azure Container App URL？

**A**: 有几种方法：

1. **Azure Portal**
   - 进入 Container App
   - 查看 **Overview** 页面的 **Application Url**

2. **Azure CLI**
   ```bash
   az containerapp show --name <app-name> --resource-group <rg-name> --query "properties.configuration.ingress.fqdn"
   ```

3. **azd 命令**
   ```bash
   azd show
   ```

4. **部署输出**
   - 部署完成后，`azd up` 或 `azd deploy` 的输出中会显示应用 URL

## 相关文档

- [Salesforce 设置指南](salesforce_setup_guide_zh.md)
- [Salesforce 故障排查](salesforce_troubleshooting_zh.md)
- [更新部署指南](../更新部署指南.md)



