# 环境变量配置指南

## 快速开始

### 1. 创建 .env 文件

在 `app/backend/` 目录下创建 `.env` 文件：

```bash
cd VoiceRAG/app/backend
copy .env.example .env  # Windows
# 或
cp .env.example .env   # Linux/Mac
```

### 2. 根据需求配置

#### 仅测试 ACS 来电处理（最小配置）

只需要配置：
```bash
ACS_CONNECTION_STRING=endpoint=https://xxx.communication.azure.com/;accesskey=xxx
ACS_CALLBACK_URL=https://your-ngrok-url.com/api/acs/calls/events
```

#### 完整功能配置

需要配置所有相关变量，参考 `.env.example` 文件。

## 环境变量分类

### 🔴 必需配置（核心功能）

#### Azure OpenAI（语音交互）
- `AZURE_OPENAI_ENDPOINT` - Azure OpenAI 端点
- `AZURE_OPENAI_API_KEY` - API 密钥
- `AZURE_OPENAI_REALTIME_DEPLOYMENT` - Realtime API 部署名称

#### Azure AI Search（RAG 搜索）
- `AZURE_SEARCH_ENDPOINT` - 搜索服务端点
- `AZURE_SEARCH_API_KEY` - 搜索 API 密钥
- `AZURE_SEARCH_INDEX` - 索引名称

### 🟡 ACS 来电处理（新增功能）

- `ACS_CONNECTION_STRING` - ACS 连接字符串
- `ACS_CALLBACK_URL` - Webhook 回调 URL（需要公网可访问）

### 🟢 可选配置

#### Salesforce（报价功能）
- `SALESFORCE_*` - 所有 Salesforce 相关配置
- 如果不使用报价功能，可以不配置

#### Email（发送邮件）
- `EMAIL_SERVICE` - 邮件服务类型（smtp 或 azure_communication）
- 如果不发送邮件，可以不配置

#### Teams Calling
- `TEAMS_*` - Teams 通话相关配置
- 如果不使用 Teams 通话，可以不配置

## 如何获取配置值

### ACS_CONNECTION_STRING

1. 登录 [Azure Portal](https://portal.azure.com)
2. 进入你的 **Azure Communication Services** 资源
3. 选择 **Keys**
4. 复制 **Connection string**

格式：`endpoint=https://xxx.communication.azure.com/;accesskey=xxx`

### ACS_CALLBACK_URL

1. 启动 ngrok：`ngrok http 8766`
2. 复制生成的 HTTPS URL
3. 添加路径：`https://xxx.ngrok-free.app/api/acs/calls/events`

### Azure OpenAI 配置

1. 登录 Azure Portal
2. 进入你的 **Azure OpenAI** 资源
3. 在 **Keys and Endpoint** 中获取：
   - Endpoint
   - Key（Key1 或 Key2）
4. 在 **Deployments** 中查看部署名称

### Azure AI Search 配置

1. 登录 Azure Portal
2. 进入你的 **Azure AI Search** 资源
3. 在 **Keys** 中获取：
   - Endpoint
   - Admin key
4. 在 **Indexes** 中查看索引名称

## 配置检查

### 检查 ACS 配置

```bash
python test_acs_connection.py
```

### 检查所有配置

运行应用，查看日志中的警告信息：
```bash
python app.py
```

## 安全提示

⚠️ **重要**：
- `.env` 文件包含敏感信息，**不要提交到 Git**
- 确保 `.env` 在 `.gitignore` 中
- 生产环境使用 Azure Key Vault 或其他密钥管理服务

## 常见问题

### Q: 哪些变量是必需的？

**A**: 取决于你要使用的功能：

- **仅测试 ACS 来电**：只需要 `ACS_CONNECTION_STRING` 和 `ACS_CALLBACK_URL`
- **完整语音交互**：需要 Azure OpenAI 和 Azure AI Search 配置
- **报价功能**：需要 Salesforce 配置
- **邮件功能**：需要 Email 配置

### Q: 变量值从哪里获取？

**A**: 
- Azure 服务：Azure Portal → 资源 → Keys/Endpoints
- Salesforce：Salesforce Setup → App Manager
- Email：邮件服务提供商设置

### Q: 配置错误怎么办？

**A**: 
1. 检查变量名拼写是否正确
2. 检查值是否包含多余的空格
3. 检查引号是否正确（通常不需要引号）
4. 查看应用日志中的错误信息

### Q: 如何验证配置？

**A**: 
- 运行对应的测试脚本
- 查看应用启动日志
- 检查功能是否正常工作



