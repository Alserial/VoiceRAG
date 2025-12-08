# 邮件发送问题排查指南

## 当前状态

根据诊断脚本的结果：
- ✅ Salesforce 连接正常
- ✅ 用户邮箱已配置：jack@infinitysocial.co
- ✅ emailSimple API 返回成功（200 状态码）
- ✅ EmailMessage 记录已创建，状态为 3（Sent）
- ⚠️ 但邮件可能未实际送达

## 邮件状态码说明

EmailMessage 的 Status 字段：
- 0 = New（新建）
- 1 = Read（已读）
- 2 = Replied（已回复）
- **3 = Sent（已发送）** ← 当前状态
- 4 = Forwarded（已转发）
- 5 = Bounced（退回）

**注意**：Status=3 只表示 Salesforce 已处理发送请求，不代表邮件已实际送达收件箱。

## 检测邮件是否成功发送的方法

### 方法 1：检查 Salesforce 邮件日志（推荐）

1. 登录 Salesforce
2. 点击右上角**设置**（齿轮图标）
3. 进入 **Setup**
4. 在 Quick Find 搜索框输入：`Email Logs`
5. 选择 **Email Administration → View Email Logs**
6. 查找最近的邮件记录：
   - 查看 **Status** 列（应该是 "Sent"）
   - 查看 **Delivery Status** 列（应该是 "Delivered" 或 "Bounced"）
   - 如果显示 "Bounced" 或 "Failed"，说明发送失败

### 方法 2：检查 Email Deliverability 设置

1. 登录 Salesforce
2. 点击右上角**设置**（齿轮图标）
3. 进入 **Setup**
4. 在 Quick Find 搜索框输入：`Deliverability`
5. 选择 **Email Administration → Deliverability**
6. 检查 **Access Level**：
   - ✅ **All Email** - 允许发送所有邮件（推荐）
   - ⚠️ **System Email Only** - 只允许系统邮件
   - ❌ **No Access** - 禁止发送邮件
7. 如果设置不正确，改为 **All Email** 并保存

### 方法 3：检查收件箱和垃圾邮件文件夹

1. 检查收件箱：`kenan2529044604@gmail.com`
2. 检查**垃圾邮件/垃圾箱**文件夹
3. 检查**促销邮件**文件夹（Gmail 可能会分类）
4. 搜索发件人：`jack@infinitysocial.co`
5. 搜索主题：`Quote Request`

### 方法 4：使用诊断脚本

运行以下脚本检查邮件状态：

```bash
cd app/backend
python check_email_status.py
```

这会显示：
- 最近的邮件记录
- 邮件状态
- 发送时间
- 收件人地址

### 方法 5：检查服务器日志

查看服务器日志中的邮件发送记录：

```bash
# 在服务器日志中查找：
grep "Email sent" app/backend/*.log
grep "emailSimple" app/backend/*.log
```

或者在运行 `app.py` 时查看控制台输出：
- `INFO:voicerag:Email sent successfully via Salesforce REST API to ...`
- `INFO:voicerag:Email sent successfully to ...`

## 常见问题和解决方案

### 问题 1：邮件被 Salesforce 拦截

**症状**：EmailMessage 状态为 3，但 Email Logs 显示 "Failed" 或 "Bounced"

**解决方案**：
1. 检查 Email Deliverability 设置（见方法 2）
2. 确保用户邮箱已验证
3. 检查是否有发送限制

### 问题 2：邮件被收件人邮箱服务商拦截

**症状**：Email Logs 显示 "Sent"，但邮件未送达

**解决方案**：
1. 检查垃圾邮件文件夹
2. 将发件人 `jack@infinitysocial.co` 添加到白名单
3. 检查收件人邮箱服务商的拦截日志（如果可用）

### 问题 3：邮件地址错误

**症状**：Email Logs 显示 "Bounced"

**解决方案**：
1. 验证收件人邮箱地址是否正确
2. 测试发送到其他邮箱地址
3. 确保邮箱地址格式正确（包含 @ 符号）

### 问题 4：Salesforce 用户邮箱未配置

**症状**：诊断脚本显示 "User email is not set"

**解决方案**：
1. 登录 Salesforce
2. 点击右上角头像 → **My Settings**
3. 选择 **Personal → Email**
4. 设置并验证邮箱地址

## 测试邮件发送

### 使用测试脚本

```bash
cd app/backend
python test_email_detailed.py
```

这会：
1. 连接到 Salesforce
2. 发送测试邮件
3. 显示发送结果
4. 提供下一步检查建议

### 通过前端表单测试

1. 访问 http://localhost:8765
2. 填写报价表单
3. 提交后查看：
   - 浏览器控制台（F12）中的响应
   - 服务器日志中的邮件发送记录
   - 检查 `email_sent` 字段是否为 `true`

## 下一步操作

1. **立即检查**：
   - [ ] 运行 `python check_email_status.py` 查看邮件状态
   - [ ] 在 Salesforce 中检查 Email Logs
   - [ ] 检查 Email Deliverability 设置

2. **如果邮件仍未收到**：
   - [ ] 检查垃圾邮件文件夹
   - [ ] 尝试发送到不同的邮箱地址
   - [ ] 检查 Salesforce 用户邮箱是否已验证

3. **如果问题持续**：
   - [ ] 查看 Salesforce Email Logs 中的详细错误信息
   - [ ] 联系 Salesforce 支持（如果是配置问题）
   - [ ] 考虑使用其他邮件服务（如 Azure Communication Services Email）

## 相关文件

- `app/backend/email_service.py` - 邮件发送服务
- `app/backend/check_email_config.py` - 邮件配置检查脚本
- `app/backend/check_email_status.py` - 邮件状态检查脚本
- `app/backend/test_email_detailed.py` - 邮件发送测试脚本

