# Teams Calling Bot Integration Guide

本文档说明如何将 VoiceRAG 应用配置为 Microsoft Teams Calling Bot，使其能够程序化地发起 Teams 通话。

## 功能概述

集成后，VoiceRAG 可以：
- 程序化发起 Teams 通话（拨打 Teams 用户或外部电话号码）
- 接收和处理 Teams 通话状态回调
- 查询和管理活跃的通话
- （未来）处理通话中的音频流，将 Teams 音频与 GPT-4o Realtime API 桥接

## 前置要求

### 1. Azure AD 应用注册

1. 登录 [Azure Portal](https://portal.azure.com)
2. 进入 **Microsoft Entra ID** > **应用注册**
3. 点击 **新注册**
4. 填写应用信息：
   - **名称**: 例如 `VoiceRAG Calling Bot`
   - **支持的账户类型**: 选择 "仅此组织目录中的账户"（单租户）
   - **重定向 URI**: 可留空
5. 点击 **注册**
6. 记录以下信息：
   - **应用程序(客户端) ID** (Client ID)
   - **目录(租户) ID** (Tenant ID)

### 2. 配置 API 权限

1. 在应用注册页面，进入 **API 权限**
2. 点击 **添加权限** > **Microsoft Graph** > **应用程序权限**
3. 添加以下权限：
   - `Calls.Initiate.All` - 发起通话（必需）
   - `Calls.AccessMedia.All` - 访问通话媒体（推荐，用于后续媒体流处理）
   - `User.Read.All` - 读取用户信息（用于解析用户 UPN）
4. **重要**: 点击 **授予管理员同意**，确保权限状态显示为 "✓ Granted"

### 3. 创建客户端密钥

1. 在应用注册页面，进入 **证书和密码**
2. 点击 **新客户端密码**
3. 添加描述，选择过期时间
4. **重要**: 复制密钥值（只显示一次），保存备用

### 4. 配置回调 URL

Teams 通话需要配置一个可公网访问的回调端点。推荐方式：

#### 本地开发（使用 ngrok）

```bash
# 安装 ngrok
# Windows: 下载 ngrok.exe
# Linux/Mac: brew install ngrok 或从官网下载

# 启动 ngrok 隧道
ngrok http 8765

# 记录生成的 HTTPS URL，例如: https://xxxx.ngrok-free.app
```

#### 生产环境

使用已部署到 Azure Container Apps 的 HTTPS URL，例如：
```
https://your-app.azurecontainerapps.io/api/teams/callbacks
```

## 环境变量配置

在 `app/backend/.env` 文件中添加以下配置：

```bash
# Teams Calling Bot 配置
TEAMS_TENANT_ID=your-tenant-id-here
TEAMS_CLIENT_ID=your-client-id-here
TEAMS_CLIENT_SECRET=your-client-secret-here
TEAMS_BOT_APP_ID=your-bot-app-id  # 可选，默认使用 TEAMS_CLIENT_ID
TEAMS_BOT_DISPLAY_NAME=VoiceRAG Bot  # 可选，通话中显示的名称
TEAMS_CALLBACK_URL=https://xxxx.ngrok-free.app/api/teams/callbacks  # 本地开发使用 ngrok URL
```

**注意**: 
- 如果 `TEAMS_TENANT_ID` 未设置，将使用 `AZURE_TENANT_ID`（如果存在）
- `TEAMS_CALLBACK_URL` 必须是可以从互联网访问的 HTTPS URL

## API 端点

### 1. 发起 Teams 通话

**POST** `/api/teams/calls`

请求体：
```json
{
  "type": "phone",  // 或 "teams_user"
  "target": "+8613800138000",  // 电话号码（E.164格式）或 Teams 用户 UPN/objectId
  "callback_uri": "https://your-app.com/api/teams/callbacks"  // 可选，默认使用 TEAMS_CALLBACK_URL
}
```

响应：
```json
{
  "id": "call-id-here",
  "state": "establishing",
  "direction": "outgoing",
  ...
}
```

示例：拨打 Teams 用户
```json
{
  "type": "teams_user",
  "target": "user@yourdomain.com"
}
```

### 2. 查询通话状态

**GET** `/api/teams/calls/{call_id}`

响应：
```json
{
  "id": "call-id",
  "state": "established",
  ...
}
```

### 3. 结束通话

**DELETE** `/api/teams/calls/{call_id}`

响应：
```json
{
  "success": true,
  "call_id": "call-id"
}
```

### 4. 获取活跃通话列表

**GET** `/api/teams/calls`

响应：
```json
{
  "active_calls": [
    {
      "call_id": "call-id-1",
      "call_type": "phone",
      "target": "+8613800138000",
      "state": "established",
      "created_at": "2024-01-01T00:00:00Z"
    }
  ],
  "count": 1
}
```

### 5. Teams 回调端点

**POST** `/api/teams/callbacks`

此端点由 Microsoft Graph API 调用，用于接收通话状态更新。应用会自动处理回调，无需手动调用。

## 使用示例

### Python 示例

```python
import aiohttp
import asyncio

async def make_call():
    async with aiohttp.ClientSession() as session:
        # 拨打 Teams 用户
        async with session.post(
            "http://localhost:8765/api/teams/calls",
            json={
                "type": "teams_user",
                "target": "user@yourdomain.com"
            }
        ) as resp:
            result = await resp.json()
            call_id = result["id"]
            print(f"Call created: {call_id}")
            
            # 查询状态
            async with session.get(
                f"http://localhost:8765/api/teams/calls/{call_id}"
            ) as status_resp:
                status = await status_resp.json()
                print(f"Call state: {status['state']}")
            
            # 结束通话
            async with session.delete(
                f"http://localhost:8765/api/teams/calls/{call_id}"
            ) as end_resp:
                result = await end_resp.json()
                print("Call ended")

asyncio.run(make_call())
```

### cURL 示例

```bash
# 发起通话
curl -X POST http://localhost:8765/api/teams/calls \
  -H "Content-Type: application/json" \
  -d '{
    "type": "phone",
    "target": "+8613800138000"
  }'

# 查询状态
curl http://localhost:8765/api/teams/calls/{call_id}

# 结束通话
curl -X DELETE http://localhost:8765/api/teams/calls/{call_id}
```

## 后续步骤：媒体流集成

目前实现的版本只包含通话管理功能。要完整地将 VoiceRAG 作为 Calling Bot，还需要实现：

1. **媒体流处理**: 处理 Teams 通话中的音频流（RTP/RTCP）
2. **音频桥接**: 将 Teams 音频流转换为 GPT-4o Realtime API 可用的格式
3. **双向音频**: 将 GPT-4o 生成的音频流发送回 Teams 通话

这需要：
- 实现 Microsoft Graph Media Streaming API 集成
- 音频编解码转换（PCM、G.711 等）
- WebRTC 或类似协议的音频流处理

## 故障排除

### 错误: "Teams calling is not configured"
- 检查环境变量是否正确设置
- 确保 `TEAMS_TENANT_ID`、`TEAMS_CLIENT_ID`、`TEAMS_CLIENT_SECRET` 都已配置

### 错误: "Failed to acquire access token"
- 检查客户端密钥是否正确
- 确认应用注册中的权限已授予管理员同意

### 错误: "TEAMS_CALLBACK_URL must start with https://"
- 确保回调 URL 使用 HTTPS
- 本地开发时使用 ngrok 或其他 HTTPS 隧道工具

### 通话无法建立
- 检查用户是否有 Teams 电话许可证（拨打外部电话需要）
- 确认权限 `Calls.Initiate.All` 已授予管理员同意
- 检查目标用户 ID 或电话号码格式是否正确

## 参考资源

- [Microsoft Graph 通信 API 文档](https://docs.microsoft.com/en-us/graph/api/resources/call)
- [Microsoft Graph 认证文档](https://docs.microsoft.com/en-us/graph/auth/)
- [Teams Calling Bot 文档](https://docs.microsoft.com/en-us/graph/cloud-communications-concept-overview)

