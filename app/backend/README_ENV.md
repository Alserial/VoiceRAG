# .env 文件配置说明

## 快速开始

### 1. 创建 .env 文件

```bash
# Windows
copy env.example .env

# Linux/Mac
cp env.example .env
```

### 2. 最小配置（仅测试 ACS 来电）

如果你只想测试 ACS 来电处理功能，只需要配置：

```bash
ACS_CONNECTION_STRING=endpoint=https://xxx.communication.azure.com/;accesskey=xxx
ACS_CALLBACK_URL=https://your-ngrok-url.com/api/acs/calls/events
```

**注意**：`ACS_CALLBACK_URL` 需要先启动 ngrok 获取 URL。

### 3. 完整配置

如果需要使用所有功能，参考 `env.example` 文件配置所有变量。

## 必需配置（按功能分类）

### ✅ 仅测试 ACS 来电处理

```bash
ACS_CONNECTION_STRING=endpoint=https://xxx.communication.azure.com/;accesskey=xxx
ACS_CALLBACK_URL=https://your-ngrok-url.com/api/acs/calls/events
```

### ✅ 语音交互功能

```bash
AZURE_OPENAI_ENDPOINT=https://your-resource.openai.azure.com/
AZURE_OPENAI_API_KEY=your-api-key
AZURE_OPENAI_REALTIME_DEPLOYMENT=gpt-4o-realtime
AZURE_SEARCH_ENDPOINT=https://your-search.search.windows.net
AZURE_SEARCH_API_KEY=your-search-key
AZURE_SEARCH_INDEX=your-index-name
```

### ✅ 报价功能

```bash
SALESFORCE_INSTANCE_URL=https://your-instance.salesforce.com
SALESFORCE_USERNAME=your-username@example.com
SALESFORCE_PASSWORD=your-password
SALESFORCE_SECURITY_TOKEN=your-token
SALESFORCE_CONSUMER_KEY=your-key
SALESFORCE_CONSUMER_SECRET=your-secret
SALESFORCE_DEFAULT_PRICEBOOK_ID=01sXXXXXXXXXXXXXXX
```

## 如何获取配置值

### ACS_CONNECTION_STRING

1. 登录 [Azure Portal](https://portal.azure.com)
2. 进入 **Azure Communication Services** 资源
3. 选择 **Keys**
4. 复制 **Connection string**

### ACS_CALLBACK_URL

1. 启动测试服务器：`python test_acs_server.py`
2. 启动 ngrok：`ngrok http 8766`
3. 复制生成的 HTTPS URL
4. 添加路径：`https://xxx.ngrok-free.app/api/acs/calls/events`

### Azure OpenAI

1. Azure Portal → Azure OpenAI 资源
2. **Keys and Endpoint** → 复制 Endpoint 和 Key
3. **Deployments** → 查看部署名称

### Azure AI Search

1. Azure Portal → Azure AI Search 资源
2. **Keys** → 复制 Endpoint 和 Admin key
3. **Indexes** → 查看索引名称

## 验证配置

### 测试 ACS 配置

```bash
python test_acs_connection.py
```

### 测试完整功能

```bash
python app.py
```

查看日志中的错误和警告信息。

## 安全提示

⚠️ **重要**：
- `.env` 文件包含敏感信息
- **不要提交到 Git**
- 确保 `.env` 在 `.gitignore` 中



