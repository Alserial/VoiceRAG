# 如何获取 ACS 配置值

本指南详细说明如何获取 `ACS_CONNECTION_STRING` 和 `ACS_CALLBACK_URL`。

## 一、获取 ACS_CONNECTION_STRING

### 步骤 1: 登录 Azure Portal

1. 访问 [Azure Portal](https://portal.azure.com)
2. 使用你的 Azure 账号登录

### 步骤 2: 找到 Azure Communication Services 资源

有两种方式：

**方式 A: 通过搜索**
1. 在顶部搜索框输入：`Communication Services`
2. 点击搜索结果中的 **Communication Services**

**方式 B: 通过资源组**
1. 点击左侧菜单 **资源组**
2. 找到包含你的 ACS 资源的资源组
3. 点击进入资源组
4. 找到类型为 **Communication Services** 的资源

### 步骤 3: 获取连接字符串

1. 点击进入你的 **Communication Services** 资源
2. 在左侧菜单中找到 **Keys**（密钥）或 **Access keys**（访问密钥）
3. 你会看到两个连接字符串：
   - **Primary connection string**（主连接字符串）
   - **Secondary connection string**（辅助连接字符串）
4. 点击 **Primary connection string** 旁边的复制图标 📋
5. 复制的内容格式类似：
   ```
   endpoint=https://your-resource.communication.azure.com/;accesskey=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
   ```

### 步骤 4: 配置到 .env 文件

在 `app/backend/.env` 文件中添加：

```bash
ACS_CONNECTION_STRING=endpoint=https://your-resource.communication.azure.com/;accesskey=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
```

**注意**：
- 直接粘贴，不需要添加引号
- 确保没有多余的空格
- 整个字符串在一行

---

## 二、获取 ACS_CALLBACK_URL

`ACS_CALLBACK_URL` 需要通过 ngrok 获取，因为本地服务器需要暴露到公网才能接收 Azure 的回调。

### 步骤 1: 安装 ngrok

#### Windows

1. 访问 [ngrok 下载页面](https://ngrok.com/download)
2. 下载 Windows 版本（.zip 文件）
3. 解压到任意目录（例如：`C:\ngrok\`）
4. 可选：将目录添加到 PATH 环境变量，或直接使用完整路径

#### Mac

```bash
brew install ngrok
```

#### Linux

```bash
# 下载
wget https://bin.equinox.io/c/bNyj1mQVY4c/ngrok-v3-stable-linux-amd64.tgz
# 解压
tar -xzf ngrok-v3-stable-linux-amd64.tgz
# 移动到系统路径
sudo mv ngrok /usr/local/bin/
```

### 步骤 2: 注册 ngrok 账号（推荐）

虽然可以不注册使用，但注册后可以获得：
- 更长的连接时间
- 查看请求历史
- 固定域名（付费功能）

1. 访问 [ngrok 注册页面](https://dashboard.ngrok.com/signup)
2. 使用邮箱注册（免费）
3. 登录后，在 **Your Authtoken** 页面获取你的 token

### 步骤 3: 配置 ngrok token（如果注册了）

```bash
ngrok config add-authtoken YOUR_AUTH_TOKEN
```

### 步骤 4: 启动本地测试服务器

打开**第一个终端窗口**：

```bash
cd VoiceRAG/app/backend

# 激活虚拟环境（如果还没激活）
.\.venv\Scripts\Activate.ps1  # Windows PowerShell
# 或
.venv\Scripts\activate.bat     # Windows CMD
# 或
source .venv/bin/activate      # Linux/Mac

# 启动测试服务器
python test_acs_server.py
```

你应该看到：
```
🚀 Starting ACS Test Server
Server URL: http://0.0.0.0:8766
Webhook endpoint: http://0.0.0.0:8766/api/acs/calls/events
```

**保持这个终端窗口运行！**

### 步骤 5: 启动 ngrok 隧道

打开**第二个终端窗口**：

```bash
# 启动 ngrok，将本地 8766 端口暴露到公网
ngrok http 8766
```

ngrok 会显示类似这样的信息：

```
ngrok                                                                              
                                                                                   
Session Status                online                                               
Account                       your-email@example.com (Plan: Free)                  
Version                       3.x.x                                                
Region                        United States (us)                                    
Latency                       -                                                    
Web Interface                 http://127.0.0.1:4040                                
Forwarding                    https://abc123def456.ngrok-free.app -> http://localhost:8766
                                                                                   
Connections                   ttl     opn     rt1     rt5     p50     p90         
                              0       0       0.00    0.00    0.00    0.00         
```

**重要信息**：
- **Forwarding** 这一行显示了你的公网 URL
- 格式：`https://xxxxx.ngrok-free.app -> http://localhost:8766`
- 复制 `https://xxxxx.ngrok-free.app` 这部分

### 步骤 6: 构建完整的回调 URL

回调 URL 需要包含完整的路径：

```
https://xxxxx.ngrok-free.app/api/acs/calls/events
```

**注意**：
- 必须以 `/api/acs/calls/events` 结尾
- 这是 webhook 端点的完整路径

### 步骤 7: 配置到 .env 文件

在 `app/backend/.env` 文件中添加或更新：

```bash
ACS_CALLBACK_URL=https://xxxxx.ngrok-free.app/api/acs/calls/events
```

**重要提示**：
- 每次重启 ngrok，URL 可能会变化（免费账号）
- 如果 URL 变化，需要：
  1. 更新 `.env` 文件中的 `ACS_CALLBACK_URL`
  2. 重启测试服务器
  3. 在 Azure Portal 中更新来电路由配置

### 步骤 8: 验证配置

1. 确保测试服务器正在运行（终端 1）
2. 确保 ngrok 正在运行（终端 2）
3. 访问 ngrok Web 界面：http://127.0.0.1:4040
   - 这里可以看到所有请求
   - 用于调试非常有用

---

## 完整示例

假设你的 ngrok 显示：
```
Forwarding    https://abc123.ngrok-free.app -> http://localhost:8766
```

那么你的 `.env` 文件应该包含：

```bash
ACS_CONNECTION_STRING=endpoint=https://your-resource.communication.azure.com/;accesskey=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
ACS_CALLBACK_URL=https://abc123.ngrok-free.app/api/acs/calls/events
```

---

## 常见问题

### Q: ngrok URL 每次启动都变化怎么办？

**A**: 
- 免费账号的 URL 每次启动都会变化
- 解决方案：
  1. 每次启动后更新 `.env` 文件
  2. 使用 ngrok 付费账号（可以配置固定域名）
  3. 使用其他工具如 localtunnel 或 cloudflared

### Q: 如何知道 ngrok 是否正常工作？

**A**: 
1. 访问 http://127.0.0.1:4040 查看 ngrok Web 界面
2. 在浏览器中访问你的 ngrok URL，应该能看到测试服务器的响应
3. 检查测试服务器的日志，看是否有请求进来

### Q: 测试服务器启动失败？

**A**: 
- 检查端口 8766 是否被占用
- 检查虚拟环境是否已激活
- 检查依赖是否已安装：`pip install -r requirements.txt`

### Q: ngrok 连接超时？

**A**: 
- 免费账号有连接时长限制（通常 2 小时）
- 重新启动 ngrok 获取新的 URL
- 或使用付费账号

### Q: 如何测试回调 URL 是否可访问？

**A**: 
在浏览器中访问：
```
https://your-ngrok-url.com/api/acs/calls/events
```

如果看到错误（这是正常的，因为需要 POST 请求），说明 URL 是可访问的。

或者使用 curl：
```bash
curl -X POST https://your-ngrok-url.com/api/acs/calls/events \
  -H "Content-Type: application/json" \
  -d '{"test": "data"}'
```

---

## 下一步

配置完成后：

1. ✅ 验证配置：`python test_acs_connection.py`
2. ✅ 启动服务器：`python test_acs_server.py`
3. ✅ 在 Azure Portal 配置电话号码的来电路由
4. ✅ 拨打测试电话

详细步骤请参考：`docs/acs_local_testing.md`



