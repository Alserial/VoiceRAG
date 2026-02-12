# ACS 来电处理测试步骤

## 测试前准备检查清单

- [ ] `.env` 文件已配置 `ACS_CONNECTION_STRING`
- [ ] `.env` 文件已配置 `ACS_CALLBACK_URL`
- [ ] 虚拟环境已激活
- [ ] 依赖已安装：`pip install -r requirements.txt`
- [ ] ngrok 已安装

## 步骤 1: 验证配置

### 测试 ACS 连接

```bash
cd VoiceRAG/app/backend

# 激活虚拟环境（如果还没激活）
.\.venv\Scripts\Activate.ps1  # Windows PowerShell
# 或
.venv\Scripts\activate.bat     # Windows CMD
# 或
source .venv/bin/activate      # Linux/Mac

# 运行连接测试
python test_acs_connection.py
```

**期望输出**：
```
✅ ACS connection test PASSED
```

如果看到错误，检查：
- `ACS_CONNECTION_STRING` 是否正确
- 连接字符串格式是否正确（包含 endpoint 和 accesskey）

## 步骤 2: 启动测试服务器

### 终端 1 - 启动服务器

```bash
cd VoiceRAG/app/backend

# 确保虚拟环境已激活
.\.venv\Scripts\Activate.ps1  # Windows

# 启动测试服务器
python test_acs_server.py
```

**期望输出**：
```
🚀 Starting ACS Test Server
Server URL: http://0.0.0.0:8766
Webhook endpoint: http://0.0.0.0:8766/api/acs/calls/events
📞 Ready to receive calls!
```

**保持这个终端窗口打开！**

## 步骤 3: 启动 ngrok 隧道

### 终端 2 - 启动 ngrok（新开一个终端窗口）

```bash
ngrok http 8766
```

**期望输出**：
```
Session Status                online
Forwarding                    https://24f919087be3.ngrok-free.app -> http://localhost:8766
```

**重要**：
- 保持这个终端窗口打开
- 如果 URL 变化，需要更新 `.env` 文件中的 `ACS_CALLBACK_URL`

### 验证 ngrok 连接

1. 访问 ngrok Web 界面：http://127.0.0.1:4040
2. 在浏览器中访问你的 ngrok URL：https://24f919087be3.ngrok-free.app/health
   - 应该看到 JSON 响应：`{"status": "healthy", ...}`

## 步骤 4: 配置 Azure Portal

### 在 Azure Portal 中配置来电路由

1. **登录 Azure Portal**
   - 访问 https://portal.azure.com
   - 进入你的 **Communication Services** 资源（Commn-Infinity）

2. **找到电话号码配置**
   - 在左侧菜单找到 **Phone numbers**（电话号码）
   - 或找到 **Telephony and SMS** → **Phone numbers**

3. **配置来电路由**
   - 选择你的电话号码（03 开头的号码）
   - 点击 **Configure** 或 **Edit**
   - 找到 **Inbound call routing**（来电路由）或 **Call Automation**
   - 选择 **Route to application**（路由到应用程序）
   - 输入回调 URL：`https://24f919087be3.ngrok-free.app/api/acs/calls/events`
   - 点击 **Save**（保存）

**注意**：如果找不到来电路由配置，可能需要：
- 检查电话号码是否已激活
- 查看是否有 "Call Automation" 或 "Inbound routing" 选项
- 参考 Azure 文档配置来电路由

## 步骤 5: 拨打测试电话

### 使用手机拨打你的电话号码

1. 使用手机拨打你的 ACS 电话号码（03 开头）
2. 观察测试服务器的日志输出

### 期望看到的日志

**当电话拨入时**：
```
📞 Received ACS Event: Microsoft.Communication.IncomingCall
📞 Incoming Call:
   Call ID: xxxxx
   Caller: +1234567890
📞 Answering call...
✅ Call answered successfully!
   Connection ID: xxxxx
```

**当电话接通时**：
```
✅ Call Connected - Connection ID: xxxxx
```

**当电话挂断时**：
```
❌ Call Disconnected - Connection ID: xxxxx
Removed call from active calls: xxxxx
```

## 步骤 6: 验证测试结果

### 检查活跃通话

在浏览器中访问：
```
http://localhost:8766/api/acs/calls
```

应该看到当前活跃的通话列表。

### 检查健康状态

```
http://localhost:8766/health
```

应该看到：
```json
{
  "status": "healthy",
  "acs_configured": true,
  "active_calls": 1
}
```

### 查看 ngrok 请求历史

访问：http://127.0.0.1:4040

在这里可以看到：
- 所有来自 Azure 的 webhook 请求
- 请求和响应的详细内容
- 用于调试非常有用

## 常见问题排查

### 问题 1: 电话无法接通

**检查清单**：
- [ ] 测试服务器是否正在运行？
- [ ] ngrok 是否正在运行？
- [ ] `ACS_CALLBACK_URL` 是否正确配置？
- [ ] Azure Portal 中的来电路由是否正确配置？
- [ ] 回调 URL 是否包含 `/api/acs/calls/events`？

**调试方法**：
1. 查看测试服务器日志
2. 查看 ngrok Web 界面（http://127.0.0.1:4040）
3. 检查是否有错误信息

### 问题 2: 没有收到来电事件

**可能原因**：
- Azure Portal 配置不正确
- ngrok URL 变化了但没有更新
- 防火墙阻止了连接

**解决方法**：
1. 验证 ngrok URL 是否可访问
2. 在 Azure Portal 中重新配置来电路由
3. 检查测试服务器日志

### 问题 3: 接听失败

**可能原因**：
- `ACS_CONNECTION_STRING` 不正确
- 权限问题

**解决方法**：
1. 运行 `python test_acs_connection.py` 验证连接
2. 检查连接字符串格式
3. 查看错误日志

### 问题 4: ngrok URL 变化

**解决方法**：
1. 更新 `.env` 文件中的 `ACS_CALLBACK_URL`
2. 重启测试服务器
3. 在 Azure Portal 中更新来电路由配置

## 成功标志

✅ **测试成功的标志**：
1. 电话能够接通
2. 测试服务器日志显示 "Call answered successfully"
3. 日志显示 "Call Connected"
4. `/api/acs/calls` 端点显示活跃通话
5. 电话挂断后，日志显示 "Call Disconnected"

## 下一步

成功接听电话后，你可以：

1. **添加欢迎语音**：在通话连接后播放欢迎语
2. **集成语音交互**：连接 ACS 音频流到 GPT-4o Realtime API
3. **添加业务逻辑**：集成报价、RAG 搜索等功能

## 测试命令总结

```bash
# 1. 验证配置
python test_acs_connection.py

# 2. 启动服务器（终端 1）
python test_acs_server.py

# 3. 启动 ngrok（终端 2）
ngrok http 8766

# 4. 检查健康状态
curl http://localhost:8766/health

# 5. 查看活跃通话
curl http://localhost:8766/api/acs/calls
```

现在可以开始测试了！



