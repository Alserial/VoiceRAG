# Salesforce 报价功能设置指南

本指南将帮助你在 Salesforce 中设置报价功能，以便与 VoiceRAG 应用集成。

## 前置要求

- Salesforce 组织（Production 或 Sandbox）
- 管理员权限
- 已安装 Salesforce CLI（可选，用于测试）

## 步骤 1: 启用 Quotes 功能

### 1.1 启用 Quotes 标准对象

1. 登录 Salesforce
2. 进入 **Setup**（设置）
3. 在 Quick Find 搜索框输入：**"Quote Settings"**
4. 点击 **Quote Settings**
5. 勾选 **"Enable Quotes"**
6. 点击 **Save**

### 1.2 配置 Quotes 相关对象

确保以下标准对象已启用：
- **Account**（客户）
- **Contact**（联系人）
- **Opportunity**（商机）
- **Product2**（产品）
- **Pricebook2**（价格表）
- **PricebookEntry**（价格表条目）

## 步骤 2: 创建自定义字段（如果需要）

如果标准字段无法满足需求，可以创建自定义字段：

### 2.1 在 Quote 对象上创建自定义字段

1. 进入 **Setup** → **Object Manager** → **Quote**
2. 点击 **Fields & Relationships**
3. 点击 **New**
4. 创建以下字段（如果需要）：
   - **Expected Start Date**（期望开始日期）- Date 类型
   - **Notes**（备注）- Long Text Area 类型
   - **Source**（来源）- Text 类型（用于标记来自 VoiceRAG）

### 2.2 在 QuoteLineItem 上创建字段

如果需要存储额外信息：
1. 进入 **Setup** → **Object Manager** → **Quote Line Item**
2. 创建需要的自定义字段

## 步骤 3: 设置产品和价格表

### 3.1 创建产品

1. 进入 **Products**（产品）标签页
2. 点击 **New** 创建产品
3. 填写产品信息：
   - **Product Name**（产品名称）
   - **Product Code**（产品代码，可选）
   - **Is Active**（激活）
4. 保存

### 3.2 创建价格表（如果还没有）

1. 进入 **Price Books**（价格表）
2. 创建或使用标准价格表
3. 确保价格表是 **Active** 状态

### 3.3 添加产品到价格表

1. 打开价格表
2. 点击 **Add Products**（添加产品）
3. 选择产品并设置：
   - **Unit Price**（单价）
   - **List Price**（标价）
4. 保存

## 步骤 4: 创建 Connected App（用于 API 访问）

### 4.1 创建 Connected App

1. 进入 **Setup** → **App Manager**
2. 点击 **New Connected App**
3. 填写信息：
   - **Connected App Name**: VoiceRAG Integration
   - **API Name**: VoiceRAG_Integration
   - **Contact Email**: 你的邮箱
   - **Enable OAuth Settings**: 勾选
   - **Callback URL**: `http://localhost:8765`（开发环境）或你的生产环境 URL
   - **Selected OAuth Scopes**: 
     - `Full access (full)`
     - `Perform requests on your behalf at any time (refresh_token, offline_access)`
4. 点击 **Save**

### 4.2 获取凭据

保存后，你会看到：
- **Consumer Key**（客户端 ID）
- **Consumer Secret**（客户端密钥）

**重要**：立即复制 Consumer Secret，之后无法再查看！

## 步骤 5: 创建集成用户

### 5.1 创建专用用户

1. 进入 **Setup** → **Users** → **Users**
2. 点击 **New User**
3. 创建用户：
   - **First Name**: Integration
   - **Last Name**: User
   - **Email**: integration@yourcompany.com
   - **Username**: integration.user@yourcompany.com.sandbox（Sandbox）或 .com（Production）
   - **Profile**: System Administrator（或自定义 Profile）
   - **Role**: 根据需要设置
4. 保存并设置密码

### 5.2 分配权限

确保用户有以下权限：
- **Quote** 对象的 Create, Read, Edit, Delete
- **Account** 对象的 Create, Read, Edit
- **Contact** 对象的 Create, Read, Edit
- **Opportunity** 对象的 Create, Read, Edit（如果使用）
- **Product2** 对象的 Read
- **Pricebook2** 对象的 Read
- **PricebookEntry** 对象的 Read

## 步骤 6: 配置 OAuth 流程

### 6.1 使用 Username-Password Flow（推荐用于服务器端）

这是最简单的方式，适合服务器端应用：

**需要的凭据：**
- **Username**: 集成用户的用户名
- **Password**: 集成用户的密码
- **Security Token**: 用户的 Security Token（在用户设置中重置密码后获取）
- **Consumer Key**: Connected App 的 Consumer Key
- **Consumer Secret**: Connected App 的 Consumer Secret
- **Instance URL**: 你的 Salesforce 实例 URL（如：`https://yourinstance.salesforce.com`）

### 6.2 获取 Security Token

1. 以集成用户身份登录 Salesforce
2. 进入 **Setup** → **My Personal Information** → **Reset My Security Token**
3. 点击 **Reset Security Token**
4. Security Token 会发送到用户的邮箱

## 步骤 7: 测试连接

使用 Salesforce CLI 或 Postman 测试连接：

```bash
# 使用 Salesforce CLI 测试
sf org login web --alias test --instance-url https://yourinstance.salesforce.com
```

或使用 Python 脚本测试：

```python
import requests

# OAuth 2.0 Username-Password Flow
url = "https://yourinstance.salesforce.com/services/oauth2/token"
data = {
    "grant_type": "password",
    "client_id": "YOUR_CONSUMER_KEY",
    "client_secret": "YOUR_CONSUMER_SECRET",
    "username": "integration.user@yourcompany.com",
    "password": "YOUR_PASSWORD" + "YOUR_SECURITY_TOKEN"
}

response = requests.post(url, data=data)
print(response.json())
```

## 步骤 8: 配置环境变量

在 VoiceRAG 应用的 `.env` 文件中添加：

```env
# Salesforce 配置
SALESFORCE_INSTANCE_URL=https://yourinstance.salesforce.com
SALESFORCE_USERNAME=integration.user@yourcompany.com
SALESFORCE_PASSWORD=your_password
SALESFORCE_SECURITY_TOKEN=your_security_token
SALESFORCE_CONSUMER_KEY=your_consumer_key
SALESFORCE_CONSUMER_SECRET=your_consumer_secret

# 可选：默认价格表 ID
SALESFORCE_DEFAULT_PRICEBOOK_ID=01sXXXXXXXXXXXXXXX

# 可选：默认商机阶段
SALESFORCE_OPPORTUNITY_STAGE=Prospecting
```

## 步骤 9: 配置自动化（可选）

### 9.1 创建 Flow 自动发送邮件

1. 进入 **Setup** → **Flows**
2. 创建新的 **Record-Triggered Flow**
3. 触发器：**Quote** 对象，**After Save**
4. 添加操作：
   - 获取 Quote 相关信息
   - 发送邮件（使用 Email Alert 或 Send Email）
5. 激活 Flow

### 9.2 创建 Email Template

1. 进入 **Setup** → **Email Templates**
2. 创建新的 **Text** 或 **HTML** 模板
3. 使用合并字段引用 Quote 信息：
   - `{!Quote.Name}` - Quote 编号
   - `{!Quote.QuoteNumber}` - Quote 号码
   - `{!Quote.TotalPrice}` - 总价
   - `{!Quote.ExpirationDate}` - 过期日期

## 步骤 10: 验证设置

### 检查清单

- [ ] Quotes 功能已启用
- [ ] 产品和价格表已配置
- [ ] Connected App 已创建并获取凭据
- [ ] 集成用户已创建并分配权限
- [ ] Security Token 已获取
- [ ] 环境变量已配置
- [ ] 测试连接成功

## 常见问题

### Q: 如何找到我的 Salesforce 实例 URL？

**A**: 登录 Salesforce 后，查看浏览器地址栏。URL 格式通常是：
- Production: `https://yourcompany.my.salesforce.com`
- Sandbox: `https://yourcompany--sandboxname.sandbox.my.salesforce.com`

### Q: Security Token 是什么？

**A**: Security Token 是 Salesforce 的安全机制。当从外部应用访问时，密码需要加上 Security Token。格式：`password + security_token`

### Q: 如何测试 API 访问？

**A**: 可以使用 Postman、Salesforce CLI 或 Python 的 `simple-salesforce` 库进行测试。

### Q: 报价创建后如何自动发送邮件？

**A**: 可以使用 Salesforce Flow 或 Process Builder 在 Quote 创建后触发邮件发送。

## 下一步

完成设置后，VoiceRAG 应用将能够：
1. 在 Salesforce 中创建 Quote 记录
2. 关联 Account 和 Contact
3. 添加 Quote Line Items
4. 返回 Quote 链接
5. （可选）自动发送邮件通知

参考 `docs/salesforce_integration_guide_zh.md` 了解代码实现细节。


