# ACS 来电处理 - 快速开始指南

## 步骤 1: 设置虚拟环境并安装依赖

### Windows (PowerShell)

```powershell
# 进入项目目录
cd VoiceRAG

# 创建虚拟环境（如果还没有）
python -m venv .venv

# 激活虚拟环境
.\.venv\Scripts\Activate.ps1

# 如果遇到执行策略错误，先运行：
# Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser

# 安装依赖
pip install -r app/backend/requirements.txt
```

### Windows (CMD)

```cmd
cd VoiceRAG
python -m venv .venv
.venv\Scripts\activate.bat
pip install -r app/backend/requirements.txt
```

### Linux/Mac

```bash
cd VoiceRAG
python3 -m venv .venv
source .venv/bin/activate
pip install -r app/backend/requirements.txt
```

### 或者使用项目提供的脚本

**Windows:**
```powershell
cd VoiceRAG
.\scripts\load_python_env.ps1
.\.venv\Scripts\Activate.ps1
```

**Linux/Mac:**
```bash
cd VoiceRAG
./scripts/load_python_env.sh
source .venv/bin/activate
```

## 步骤 2: 配置环境变量

在 `app/backend/.env` 文件中添加：

```bash
# Azure Communication Services 配置
ACS_CONNECTION_STRING=endpoint=https://xxx.communication.azure.com/;accesskey=xxx
# ACS_CALLBACK_URL 稍后配置（需要先启动 ngrok）
```

## 步骤 3: 测试连接

```bash
# 确保虚拟环境已激活
python app/backend/test_acs_connection.py
```

如果看到 `✅ ACS connection test PASSED`，说明配置正确。

## 步骤 4: 启动测试服务器

**终端 1 - 启动服务器：**
```bash
# 确保虚拟环境已激活
python app/backend/test_acs_server.py
```

**终端 2 - 启动 ngrok：**
```bash
ngrok http 8766
```

复制 ngrok 生成的 HTTPS URL（例如：`https://abc123.ngrok-free.app`）

## 步骤 5: 更新回调 URL

更新 `.env` 文件：
```bash
ACS_CALLBACK_URL=https://abc123.ngrok-free.app/api/acs/calls/events
```

重启测试服务器（Ctrl+C 停止，再运行 `python app/backend/test_acs_server.py`）

## 步骤 6: 配置 Azure Portal

1. 登录 Azure Portal
2. 进入你的 ACS 资源
3. 配置电话号码的来电路由指向：`https://abc123.ngrok-free.app/api/acs/calls/events`

## 步骤 7: 测试

拨打你的电话号码，观察服务器日志！

## 验证安装

运行以下命令验证所有依赖都已安装：

```bash
python -c "import azure.communication.callautomation; print('✅ ACS SDK installed')"
python -c "import aiohttp; print('✅ aiohttp installed')"
python -c "import dotenv; print('✅ python-dotenv installed')"
```

如果所有命令都成功，说明依赖安装正确。

## 常见问题

### Q: 虚拟环境激活失败？

**Windows PowerShell:**
```powershell
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
```

### Q: pip install 很慢？

使用国内镜像：
```bash
pip install -r app/backend/requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
```

### Q: 找不到 python 命令？

- Windows: 使用 `py` 或 `python3`
- Linux/Mac: 使用 `python3`

### Q: 如何确认虚拟环境已激活？

激活后，命令行提示符前应该显示 `(.venv)`：
```
(.venv) PS C:\...\VoiceRAG>
```



