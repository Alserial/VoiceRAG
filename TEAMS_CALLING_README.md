# VoiceRAG as Teams Calling Bot

VoiceRAG 现已集成 Microsoft Teams Calling Bot 功能，可以作为 Calling Bot 程序化地发起 Teams 通话。

## 快速开始

### 1. 配置环境变量

在 `app/backend/.env` 中添加：

```bash
TEAMS_TENANT_ID=your-tenant-id
TEAMS_CLIENT_ID=your-client-id
TEAMS_CLIENT_SECRET=your-client-secret
TEAMS_CALLBACK_URL=https://your-public-url.com/api/teams/callbacks
```

### 2. 发起通话

```bash
# 拨打 Teams 用户
curl -X POST http://localhost:8765/api/teams/calls \
  -H "Content-Type: application/json" \
  -d '{"type": "teams_user", "target": "user@domain.com"}'

# 拨打外部电话
curl -X POST http://localhost:8765/api/teams/calls \
  -H "Content-Type: application/json" \
  -d '{"type": "phone", "target": "+8613800138000"}'
```

## 详细文档

请参阅 [Teams Calling Integration Guide](docs/teams_calling_integration.md) 了解：
- Azure AD 应用注册步骤
- API 权限配置
- 完整的 API 文档
- 故障排除指南

## 当前功能

✅ 发起 Teams 通话（Teams 用户或外部电话）
✅ 查询通话状态
✅ 结束通话
✅ 接收通话回调
✅ 管理活跃通话列表

## 未来功能

🔄 媒体流处理（Teams 音频 <-> GPT-4o Realtime API）
🔄 实时语音交互（在 Teams 通话中使用 VoiceRAG）

