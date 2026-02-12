# ACS 电话号码与事件订阅配置指南

## 概述

要让电话号码的来电事件发送到你的 webhook，需要配置两个部分：

1. **Event Grid 事件订阅**（你已经创建了 ✅）
2. **电话号码的来电路由配置**（需要配置）

## 配置步骤

### 方法 1: 通过 Call Automation 配置（推荐）

#### 步骤 1: 进入电话号码配置

1. 在 Azure Portal 中，进入你的 **Communication Services** 资源（Commn-Infinity）
2. 在左侧菜单找到 **Phone numbers**（电话号码）
3. 点击你的电话号码（例如：`6-137-048-0362`）

#### 步骤 2: 配置 Call Automation

1. 在电话号码详情页面，查找以下选项之一：
   - **Call Automation**（呼叫自动化）
   - **Inbound call routing**（来电路由）
   - **Event subscriptions**（事件订阅）
   - **Configure**（配置）

2. 配置来电路由：
   - 选择 **Route to application**（路由到应用程序）
   - 或选择 **Use Call Automation**（使用呼叫自动化）
   - 输入你的 **Application ID**（如果需要）
   - 输入回调 URL：`https://514c-183-141-68-136.ngrok-free.app/api/acs/calls/events`

#### 步骤 3: 保存配置

点击 **Save**（保存）或 **Update**（更新）

### 方法 2: 通过 Event Grid 订阅（你已经创建了）

如果你已经创建了 Event Grid 订阅（`automationCalls`），还需要确保：

1. **订阅的端点 URL 正确**：
   - 应该是：`https://514c-183-141-68-136.ngrok-free.app/api/acs/calls/events`

2. **订阅的事件类型包含来电事件**：
   - `Microsoft.Communication.CallStarted`
   - `Microsoft.Communication.IncomingCall`（如果使用 Call Automation）

3. **电话号码已关联到 Communication Services 资源**：
   - 确保电话号码属于同一个 ACS 资源

## 验证配置

### 检查清单

- [ ] Event Grid 订阅状态为 "Succeeded"
- [ ] 订阅的端点 URL 正确
- [ ] 电话号码已配置来电路由
- [ ] 测试服务器正在运行
- [ ] ngrok 正在运行

### 测试步骤

1. **确保服务运行**：
   ```cmd
   # 终端 1
   python test_acs_server.py
   
   # 终端 2
   ngrok http 8766
   ```

2. **拨打测试电话**：
   - 使用手机拨打你的电话号码
   - 观察测试服务器的日志

3. **检查事件**：
   - 在 Azure Portal 的 Event Subscriptions 页面
   - 查看图表是否显示事件数据
   - 查看 "Delivered Events" 指标

## 常见问题

### Q: 事件订阅已创建，但收不到来电事件？

**A**: 可能的原因：
1. 电话号码没有配置来电路由
2. 来电路由指向了错误的端点
3. 事件类型不匹配

**解决方法**：
- 检查电话号码的配置
- 确保来电路由指向正确的 webhook URL
- 确认事件类型包含 `Microsoft.Communication.CallStarted`

### Q: 需要同时配置 Call Automation 和 Event Grid 吗？

**A**: 
- **Call Automation**：用于程序化处理通话（接听、播放音频等）
- **Event Grid**：用于接收事件通知

对于你的用例（接收来电并自动接听），建议：
- 使用 **Call Automation** 配置来电路由
- Event Grid 订阅可以作为补充，用于监控和日志记录

### Q: 如何知道配置是否正确？

**A**: 
1. 拨打测试电话
2. 查看测试服务器日志
3. 查看 Azure Portal 中的事件指标
4. 如果看到事件被接收，说明配置正确

## 下一步

配置完成后：
1. ✅ 测试接收来电事件
2. 🔄 添加自动接听逻辑（代码已实现）
3. 🔄 集成语音交互功能
4. 🔄 添加业务逻辑（报价、RAG 等）


