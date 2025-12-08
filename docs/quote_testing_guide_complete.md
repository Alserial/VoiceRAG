# 完整报价功能测试指南

## 🚀 服务器状态

服务器已启动，访问地址：**http://localhost:8765**

## 📋 测试步骤

### 步骤 1: 访问应用

1. 打开浏览器（推荐 Chrome 或 Edge）
2. 访问：http://localhost:8765
3. 确认页面正常加载

### 步骤 2: 填写报价表单

向下滚动找到 **"Request a quote"** 表单，填写以下测试数据：

#### 必填字段：
- **Customer name（客户名称）**: `测试公司 ABC`
- **Contact information（联系方式）**: `你的真实邮箱地址`（用于接收邮件）
- **Product or package（产品/套餐）**: `GenWatt Diesel 10kW`（或从列表中选择其他产品）
- **Quantity（数量）**: `10`
- **Expected start date（期望开始日期）**: 选择一个未来日期，例如 `2025-06-01`

#### 可选字段：
- **Notes（备注）**: `这是一个完整的测试报价，用于验证 Salesforce 集成和邮件发送功能。`

### 步骤 3: 提交报价

1. 点击 **"Send quote"** 按钮
2. 等待处理（可能需要几秒钟）

### 步骤 4: 验证结果

#### 前端验证：
- ✅ 应该显示绿色的成功消息
- ✅ 在 "Latest quote link" 部分显示 Salesforce Quote 链接
- ✅ 链接格式类似：`https://orgfarm-7ff24bad0b-dev-ed.develop.lightning.force.com/lightning/r/Quote/...`

#### 邮件验证：
- ✅ 检查你填写的邮箱地址
- ✅ 应该收到一封来自 Salesforce 的报价邮件
- ✅ 邮件包含报价详情和链接

#### Salesforce 验证：
1. 登录 Salesforce：https://orgfarm-7ff24bad0b-dev-ed.develop.lightning.force.com
2. 检查以下对象是否已创建：

   **Account（客户）**:
   - 进入：Sales → Accounts
   - 查找：`测试公司 ABC`
   - 确认已创建

   **Contact（联系人）**:
   - 进入：Sales → Contacts
   - 查找：你填写的邮箱地址对应的联系人
   - 确认已创建并关联到 Account

   **Quote（报价）**:
   - 进入：Sales → Quotes
   - 查找：最新的报价记录
   - 确认包含：
     - 正确的 Account
     - 正确的 Contact
     - 报价状态

   **Quote Line Item（报价行项目）**:
   - 在 Quote 详情页面
   - 查看 Related → Quote Line Items
   - 确认包含：
     - 产品：`GenWatt Diesel 10kW`（或你选择的产品）
     - 数量：`10`
     - 价格信息

## 🔍 故障排查

### 如果前端显示错误：

1. **检查浏览器控制台**（F12）：
   - 查看是否有 JavaScript 错误
   - 查看 Network 标签，检查 API 请求状态

2. **检查服务器日志**：
   - 查看运行 `python app.py` 的终端窗口
   - 查找错误信息

### 如果邮件未收到：

1. **检查垃圾邮件文件夹**
2. **确认邮箱地址正确**
3. **检查 Salesforce 用户邮箱配置**：
   - 登录 Salesforce
   - Setup → My Personal Information → Email
   - 确认邮箱地址已配置

### 如果 Salesforce 中未创建记录：

1. **检查 Salesforce 连接**：
   ```bash
   cd app/backend
   python test_salesforce.py
   ```

2. **检查权限**：
   - 确认 Salesforce 用户有创建 Account、Contact、Quote 的权限
   - 检查 Profile 或 Permission Set 设置

3. **检查日志**：
   - 查看服务器日志中的 Salesforce 相关错误

## ✅ 成功标准

完整的测试应该满足以下所有条件：

- [ ] 前端表单可以正常提交
- [ ] 前端显示成功消息和 Quote 链接
- [ ] 收到报价邮件通知
- [ ] Salesforce 中创建了 Account
- [ ] Salesforce 中创建了 Contact（如果联系方式是邮箱）
- [ ] Salesforce 中创建了 Quote
- [ ] Quote 包含正确的产品信息
- [ ] Quote Line Item 已创建（如果产品在价格表中）

## 🎯 下一步

测试成功后，你可以：

1. **自定义邮件模板**：修改 `app/backend/email_service.py` 中的模板
2. **添加更多产品**：在 Salesforce 中添加更多产品和价格表条目
3. **配置工作流**：在 Salesforce 中设置自动化工作流
4. **集成其他功能**：添加更多业务逻辑

## 📞 需要帮助？

如果遇到问题，请检查：
- 服务器日志
- Salesforce 连接状态
- 环境变量配置（`.env` 文件）

