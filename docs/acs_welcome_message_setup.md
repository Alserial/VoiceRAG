# ACS 欢迎语音配置指南

## 概述

当 ACS 来电成功连接后，系统会自动播放欢迎语音消息。支持两种方式：

1. **音频文件播放**（推荐，简单快速）
2. **文本转语音（TTS）**（需要配置 Azure AI 服务）

## 方法 1: 使用音频文件（推荐）

### 步骤 1: 准备音频文件

**音频文件要求**：
- **WAV 格式**：单声道、16 位 PCM、16 kHz 采样率
- **MP3 格式**：需要包含 ID3V2TAG

**创建音频文件的方式**：

1. **使用 Azure 语音合成工具**：
   - 访问 [Azure 语音合成与音频内容创建工具](https://learn.microsoft.com/zh-cn/azure/ai-services/speech-service/how-to-audio-content-creation)
   - 输入欢迎文本（例如："Hello, welcome to our service. How can I help you today?"）
   - 选择语音和语言
   - 下载生成的音频文件

2. **使用其他 TTS 工具**：
   - 使用任何文本转语音工具生成音频
   - 确保格式符合要求（WAV：单声道、16位 PCM、16 kHz）

3. **录制音频**：
   - 使用录音软件录制欢迎语音
   - 转换为符合要求的格式

### 步骤 2: 上传音频文件

将音频文件上传到可公开访问的位置：

**选项 A: Azure Blob Storage**（推荐）
1. 创建 Azure Storage Account
2. 创建 Blob Container（设置为公共访问）
3. 上传音频文件
4. 获取文件的公共 URL（例如：`https://yourstorage.blob.core.windows.net/audio/welcome.wav`）

**选项 B: 其他云存储**
- 上传到任何可公开访问的 URL（如 GitHub Releases、CDN 等）

### 步骤 3: 配置环境变量

在 `.env` 文件中添加：

```bash
ACS_WELCOME_AUDIO_URL=https://your-storage.blob.core.windows.net/audio/welcome.wav
```

### 步骤 4: 重启服务器

重启测试服务器，配置即可生效。

## 方法 2: 使用文本转语音（TTS）

### 前提条件

需要将 Azure AI 服务连接到你的 Communication Services 资源。

### 配置步骤

1. **创建 Azure AI 服务资源**：
   - 在 Azure Portal 创建 "Speech Services" 或 "Cognitive Services" 资源
   - 记录资源名称和区域

2. **连接到 Communication Services**：
   - 进入你的 Communication Services 资源
   - 找到 "Text to Speech" 或 "AI Services" 配置
   - 连接到你的 Azure AI 服务资源

3. **配置自定义子域**（如果需要）：
   - 为 Azure AI 服务资源创建自定义子域
   - 确保子域已配置完成

4. **重启服务器**：
   - 系统会自动使用 TTS 功能
   - 欢迎文本在代码中定义（可修改）

### 修改欢迎文本

在 `test_acs_server.py` 的 `play_welcome_message` 函数中修改：

```python
welcome_text = "Hello, welcome to our service. How can I help you today?"
```

## 测试

1. 启动测试服务器：
   ```bash
   python test_acs_server.py
   ```

2. 拨打你的 ACS 电话号码

3. 观察日志：
   - 应该看到 "🎵 Playing welcome message..."
   - 如果成功，会显示 "✅ Welcome message playback initiated"
   - 如果失败，会显示错误信息和解决建议

4. 监听电话：
   - 通话连接后应该听到欢迎语音

## 故障排除

### 问题 1: 没有播放欢迎语音

**检查**：
- 查看日志中是否有错误信息
- 确认 `CallConnected` 事件是否被正确处理
- 检查 `ACS_WELCOME_AUDIO_URL` 是否正确配置

**解决**：
- 如果使用音频文件，确保 URL 可公开访问
- 如果使用 TTS，确保 Azure AI 服务已正确连接

### 问题 2: 播放失败

**可能原因**：
- 音频文件格式不正确
- URL 无法访问
- Azure AI 服务未配置

**解决**：
- 检查音频文件格式是否符合要求
- 测试 URL 是否可以在浏览器中直接访问
- 检查 Azure AI 服务连接状态

### 问题 3: 播放完成事件未收到

**说明**：
- 播放是异步操作
- 如果配置了回调 URL，会收到 `PlayCompleted` 或 `PlayFailed` 事件
- 检查日志中的事件处理

## 自定义欢迎文本

### 英文版本

```python
welcome_text = "Hello, welcome to our service. How can I help you today?"
```

### 中文版本

```python
welcome_text = "您好，欢迎致电我们的服务。请问有什么可以帮助您的？"
```

### 多语言版本

可以根据来电者的语言动态选择欢迎文本（需要从事件中获取语言信息）。

## 下一步

配置好欢迎语音后，可以继续：
- 集成 AI 语音交互（连接 GPT-4o Realtime API）
- 处理用户语音输入
- 实现完整的对话流程


