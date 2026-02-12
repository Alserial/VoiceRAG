# Azure Communication Services (ACS) 来电处理配置指南

本指南说明如何配置 VoiceRAG 以接收和处理来自 Azure Communication Services 的电话来电。

## 功能概述

配置完成后，VoiceRAG 可以：
- ✅ 自动接收来自 ACS 的电话来电
- ✅ 自动接听电话
- ✅ 处理通话事件（连接、断开等）
- ✅ 管理活跃通话列表
- 🔄 后续可集成语音交互功能

## 前置要求

### 1. Azure Communication Services 资源

1. 登录 [Azure Portal](https://portal.azure.com)
2. 创建或使用现有的 **Azure Communication Services** 资源
3. 记录以下信息：
   - **连接字符串** (Connection String)
   - **电话号码** (已配置的 03 开头的号码)

### 2. 获取连接字符串

1. 在 Azure Portal 中，进入你的 ACS 资源
2. 在左侧菜单选择 **Keys**
3. 复制 **Connection string**（格式类似：`endpoint=https://xxx.communication.azure.com/;accesskey=xxx`）

### 3. 配置公网可访问的回调 URL

ACS 需要通过 HTTPS webhook 发送事件到你的应用。你需要：

#### 本地开发（使用 ngrok）

```bash
# 安装 ngrok
# Windows: 下载 ngrok.exe
# Linux/Mac: brew install ngrok

# 启动 ngrok 隧道（假设你的应用运行在 8765 端口）
ngrok http 8765

# 记录生成的 HTTPS URL，例如: https://xxxx.ngrok-free.app
```

#### 生产环境

使用已部署到 Azure Container Apps 的 HTTPS URL，例如：
```
https://your-app.azurecontainerapps.io/api/acs/calls/events
```

## 环境变量配置

在 `app/backend/.env` 文件中添加以下配置：

```bash
# Azure Communication Services 配置
ACS_CONNECTION_STRING=endpoint=https://xxx.communication.azure.com/;accesskey=xxx
ACS_CALLBACK_URL=https://xxxx.ngrok-free.app/api/acs/calls/events
ACS_PHONE_NUMBER=+1234567890  # 可选，用于日志记录
```

**重要说明**：
- `ACS_CALLBACK_URL` 必须是 HTTPS URL
- URL 必须以 `/api/acs/calls/events` 结尾（这是 webhook 端点）
- 确保 URL 可以从公网访问

## 集成到应用

### 方法 1: 在主应用中注册路由（推荐）

在 `app/backend/app.py` 的 `create_app()` 函数中添加：

```python
from acs_call_handler import register_acs_routes

# 在创建 app 后，注册 ACS 路由
register_acs_routes(app)
```

### 方法 2: 独立运行测试服务器

创建一个测试脚本 `test_acs_server.py`：

```python
from aiohttp import web
from acs_call_handler import register_acs_routes

async def create_test_app():
    app = web.Application()
    register_acs_routes(app)
    return app

if __name__ == "__main__":
    web.run_app(create_test_app(), host="0.0.0.0", port=8765)
```

## 配置 ACS 电话号码来电路由

### 在 Azure Portal 中配置

1. 进入你的 ACS 资源
2. 选择 **Phone numbers** 或 **Call Automation**
3. 选择你的电话号码（03 开头的号码）
4. 配置 **Inbound call routing**：
   - 选择 **Route to application**
   - 输入你的回调 URL: `https://your-app.com/api/acs/calls/events`

### 使用 Azure CLI 配置

```bash
# 设置来电路由
az communication phonenumber update \
  --phone-number "+1234567890" \
  --connection-string "your-connection-string" \
  --application-id "your-application-id" \
  --callback-url "https://your-app.com/api/acs/calls/events"
```

## 测试连接

### 1. 运行连接测试

```bash
cd app/backend
python test_acs_connection.py
```

如果测试通过，你应该看到：
```
✅ ACS connection test PASSED
```

### 2. 启动应用

```bash
python app.py
```

或者如果已集成到主应用：
```bash
python -m app.backend.app
```

### 3. 检查日志

应用启动后，你应该看到：
```
ACS Call Automation client initialized successfully
ACS call handler routes registered
```

### 4. 拨打测试电话

1. 使用手机拨打你的 ACS 电话号码（03 开头）
2. 观察应用日志，应该看到：
   ```
   Received ACS event: Microsoft.Communication.IncomingCall
   Incoming call received - Call ID: xxx, Caller: +1234567890
   Call answered successfully - Connection ID: xxx
   Call connected - Connection ID: xxx
   ```

### 5. 检查活跃通话

访问 API 端点查看活跃通话：
```bash
curl http://localhost:8765/api/acs/calls
```

## API 端点

### 1. Webhook 端点（ACS 调用）

**POST** `/api/acs/calls/events`

这是 ACS 发送事件的端点，不需要手动调用。

### 2. 获取活跃通话列表

**GET** `/api/acs/calls`

响应：
```json
{
  "active_calls": [
    {
      "call_connection_id": "xxx",
      "call_id": "xxx",
      "caller_id": "+1234567890",
      "status": "connected",
      "started_at": "2024-01-01T00:00:00Z"
    }
  ],
  "count": 1
}
```

### 3. 获取特定通话状态

**GET** `/api/acs/calls/{call_connection_id}`

### 4. 挂断通话

**DELETE** `/api/acs/calls/{call_connection_id}`

## 故障排除

### 错误: "ACS client not configured"

- 检查 `ACS_CONNECTION_STRING` 环境变量是否正确设置
- 确保连接字符串格式正确（包含 `endpoint=` 和 `accesskey=`）

### 错误: "Callback URL not configured"

- 检查 `ACS_CALLBACK_URL` 环境变量是否正确设置
- 确保 URL 是 HTTPS
- 确保 URL 可以从公网访问

### 电话无法接通

1. **检查来电路由配置**：
   - 在 Azure Portal 中确认电话号码已配置来电路由
   - 确认回调 URL 正确

2. **检查网络连接**：
   - 确保你的应用可以从公网访问
   - 使用 `curl` 测试回调 URL 是否可访问

3. **检查日志**：
   - 查看应用日志是否有错误信息
   - 检查 ACS 资源的事件日志

### 事件未收到

1. **验证 webhook URL**：
   ```bash
   # 测试 webhook 端点是否可访问
   curl -X POST https://your-app.com/api/acs/calls/events \
     -H "Content-Type: application/json" \
     -d '{"type": "test"}'
   ```

2. **检查防火墙/安全组**：
   - 确保允许来自 Azure 的入站连接

3. **使用 ngrok 查看请求**：
   - ngrok 提供 web 界面查看所有请求
   - 访问 `http://127.0.0.1:4040` 查看请求历史

## 下一步

成功接听电话后，你可以：

1. **添加欢迎语音**：在 `handle_call_connected_event` 中播放欢迎语
2. **集成语音交互**：将 ACS 音频流连接到 GPT-4o Realtime API
3. **添加业务逻辑**：集成报价、RAG 搜索等功能

## 参考资源

- [Azure Communication Services 文档](https://docs.microsoft.com/azure/communication-services/)
- [Call Automation API 文档](https://docs.microsoft.com/azure/communication-services/concepts/voice-video-calling/call-automation)
- [Python SDK 文档](https://github.com/Azure/azure-sdk-for-python/tree/main/sdk/communication)




