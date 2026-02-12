# ACS 本地测试指南

本指南说明如何在**本地环境**测试 ACS Call Automation 的来电处理功能，无需部署到 Azure。

## 为什么需要本地测试？

- ✅ 快速开发和调试
- ✅ 无需等待部署
- ✅ 节省 Azure 资源成本
- ✅ 实时查看日志和调试信息

## 本地测试架构

```
电话拨打 → Azure ACS → ngrok 隧道 → 本地服务器 (localhost:8766)
```

## 前置要求

### 1. 安装 ngrok

ngrok 是一个隧道工具，可以将本地端口暴露到公网。

#### Windows
1. 访问 https://ngrok.com/download
2. 下载 ngrok.exe
3. 解压到任意目录（例如 `C:\ngrok\`）
4. 将目录添加到 PATH 环境变量，或直接使用完整路径

#### Mac
```bash
brew install ngrok
```

#### Linux
```bash
# 下载并解压
wget https://bin.equinox.io/c/bNyj1mQVY4c/ngrok-v3-stable-linux-amd64.tgz
tar -xzf ngrok-v3-stable-linux-amd64.tgz
sudo mv ngrok /usr/local/bin/
```

### 2. 注册 ngrok 账号（可选但推荐）

1. 访问 https://dashboard.ngrok.com/signup
2. 注册免费账号
3. 获取 authtoken
4. 配置 token：
   ```bash
   ngrok config add-authtoken YOUR_AUTH_TOKEN
   ```

**注意**：免费账号有连接时长限制，但对于测试足够使用。

## 本地测试步骤

### 步骤 1: 配置环境变量

在 `app/backend/.env` 文件中添加：

```bash
# Azure Communication Services 配置
ACS_CONNECTION_STRING=endpoint=https://xxx.communication.azure.com/;accesskey=xxx

# 注意：ACS_CALLBACK_URL 稍后配置（需要先启动 ngrok 获取 URL）
# ACS_CALLBACK_URL=https://xxxx.ngrok-free.app/api/acs/calls/events
```

### 步骤 2: 启动本地测试服务器

打开第一个终端窗口：

```bash
cd VoiceRAG/app/backend
python test_acs_server.py
```

你应该看到：
```
🚀 Starting ACS Test Server
Server URL: http://0.0.0.0:8766
Webhook endpoint: http://0.0.0.0:8766/api/acs/calls/events
```

### 步骤 3: 启动 ngrok 隧道

打开第二个终端窗口：

```bash
ngrok http 8766
```

ngrok 会显示类似这样的信息：
```
Session Status                online
Account                       your-email@example.com
Version                       3.x.x
Region                        United States (us)
Latency                       -
Web Interface                 http://127.0.0.1:4040
Forwarding                    https://abc123.ngrok-free.app -> http://localhost:8766
```

**重要**：复制 `Forwarding` 中的 HTTPS URL（例如：`https://abc123.ngrok-free.app`）

### 步骤 4: 更新回调 URL

有两种方式：

#### 方式 A: 更新环境变量（推荐）

更新 `.env` 文件：
```bash
ACS_CALLBACK_URL=https://abc123.ngrok-free.app/api/acs/calls/events
```

然后重启测试服务器（Ctrl+C 停止，再运行 `python test_acs_server.py`）

#### 方式 B: 临时设置环境变量

```bash
# Windows PowerShell
$env:ACS_CALLBACK_URL="https://abc123.ngrok-free.app/api/acs/calls/events"
python test_acs_server.py

# Windows CMD
set ACS_CALLBACK_URL=https://abc123.ngrok-free.app/api/acs/calls/events
python test_acs_server.py

# Linux/Mac
export ACS_CALLBACK_URL=https://abc123.ngrok-free.app/api/acs/calls/events
python test_acs_server.py
```

### 步骤 5: 配置 Azure Portal

1. 登录 [Azure Portal](https://portal.azure.com)
2. 进入你的 **Azure Communication Services** 资源
3. 选择 **Phone numbers** 或找到你的电话号码配置
4. 配置 **Inbound call routing**：
   - 选择 **Route to application** 或 **Call Automation**
   - 输入回调 URL: `https://abc123.ngrok-free.app/api/acs/calls/events`
   - 保存配置

**注意**：每次 ngrok 重启，URL 可能会变化（除非使用付费账号的固定域名）。如果 URL 变化，需要重新配置 Azure。

### 步骤 6: 测试电话

1. 使用手机拨打你的 ACS 电话号码（03 开头）
2. 观察测试服务器的日志输出

你应该看到类似这样的日志：
```
📞 Received ACS Event: Microsoft.Communication.IncomingCall
📞 Incoming Call:
   Call ID: xxxxx
   Caller: +1234567890
📞 Answering call...
✅ Call answered successfully!
   Connection ID: xxxxx
```

### 步骤 7: 验证通话状态

在浏览器中访问：
- 健康检查: http://localhost:8766/health
- 活跃通话: http://localhost:8766/api/acs/calls

## ngrok Web 界面

ngrok 提供了一个 Web 界面来查看所有请求：

访问: http://127.0.0.1:4040

在这里你可以：
- 查看所有 HTTP 请求
- 查看请求和响应内容
- 重放请求（用于调试）

## 常见问题

### Q: ngrok URL 每次启动都变化怎么办？

**A**: 有几种解决方案：

1. **使用 ngrok 付费账号**：可以配置固定域名
2. **使用其他工具**：
   - **localtunnel**: `npx localtunnel --port 8766`
   - **cloudflared**: `cloudflared tunnel --url http://localhost:8766`
3. **每次更新 Azure 配置**：虽然麻烦，但免费

### Q: ngrok 连接超时怎么办？

**A**: 
- 免费账号有连接时长限制（通常 2 小时）
- 可以重新启动 ngrok 获取新的 URL
- 或者使用付费账号

### Q: 如何知道 Azure 是否成功发送了事件？

**A**: 
1. 查看 ngrok Web 界面 (http://127.0.0.1:4040)
2. 查看测试服务器的日志
3. 如果看到请求但没有日志，可能是 JSON 解析问题

### Q: 测试时电话无法接通？

**A**: 检查清单：
- ✅ ngrok 是否正在运行？
- ✅ 测试服务器是否正在运行？
- ✅ `ACS_CALLBACK_URL` 是否正确配置？
- ✅ Azure Portal 中的来电路由是否正确配置？
- ✅ 回调 URL 是否包含 `/api/acs/calls/events`？

### Q: 如何测试多个并发通话？

**A**: 
- 本地测试服务器支持多个并发通话
- 每个通话会分配独立的 `call_connection_id`
- 可以通过 `/api/acs/calls` 端点查看所有活跃通话

## 本地测试的优势

✅ **快速迭代**：修改代码后立即测试，无需部署  
✅ **实时调试**：可以直接在代码中打断点  
✅ **详细日志**：所有事件和错误都会在控制台显示  
✅ **成本为零**：不需要 Azure 计算资源  

## 下一步

成功在本地接听电话后，你可以：

1. **添加欢迎语音**：在通话连接后播放欢迎语
2. **集成语音交互**：连接 ACS 音频流到 GPT-4o Realtime API
3. **添加业务逻辑**：集成报价、RAG 搜索等功能

## 替代工具

如果不想使用 ngrok，还有其他选择：

### localtunnel
```bash
npm install -g localtunnel
lt --port 8766
```

### cloudflared (Cloudflare Tunnel)
```bash
# 下载 cloudflared
cloudflared tunnel --url http://localhost:8766
```

### serveo (SSH 隧道)
```bash
ssh -R 80:localhost:8766 serveo.net
```

所有这些工具都可以将本地端口暴露到公网，让 Azure 能够访问你的本地服务器。



