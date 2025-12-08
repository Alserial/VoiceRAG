# 邮件发送功能设置指南

本指南将帮助你在 VoiceRAG 应用中配置邮件发送功能，用于发送报价通知。

## 邮件服务选项

VoiceRAG 支持多种邮件发送方式：

### 选项 1: Azure Communication Services Email（推荐）

适合已使用 Azure 服务的场景，集成简单。

### 选项 2: SMTP（通用）

支持任何 SMTP 服务器（Gmail、Outlook、SendGrid、Mailgun 等）。

### 选项 3: Salesforce Email API

如果已配置 Salesforce，可以直接使用 Salesforce 的邮件功能。

## 方法 1: 使用 Azure Communication Services Email

### 步骤 1: 创建 Azure Communication Services 资源

1. 登录 [Azure Portal](https://portal.azure.com)
2. 创建新资源：**Communication Services**
3. 填写信息：
   - **Resource Group**: 选择现有或创建新的
   - **Resource Name**: 如 `voicerag-email`
   - **Region**: 选择区域
4. 点击 **Review + Create** → **Create**

### 步骤 2: 配置 Email Domain

1. 在 Communication Services 资源中，进入 **Email**
2. 点击 **Add Domain**
3. 选择 **Azure Managed Domain**（免费，但有限制）或 **Custom Domain**
4. 完成域名验证（如果使用自定义域名）

### 步骤 3: 获取连接字符串

1. 在 Communication Services 资源中，进入 **Keys**
2. 复制 **Connection String**

### 步骤 4: 配置环境变量

在 `.env` 文件中添加：

```env
# Azure Communication Services Email
AZURE_COMMUNICATION_CONNECTION_STRING=endpoint=https://...;accesskey=...
AZURE_COMMUNICATION_EMAIL_FROM=noreply@yourdomain.com
```

## 方法 2: 使用 SMTP（通用方法）

### 步骤 1: 选择 SMTP 服务

常见选项：
- **Gmail**: `smtp.gmail.com:587`
- **Outlook/Hotmail**: `smtp-mail.outlook.com:587`
- **SendGrid**: `smtp.sendgrid.net:587`
- **Mailgun**: `smtp.mailgun.org:587`
- **自定义 SMTP 服务器**

### 步骤 2: 获取 SMTP 凭据

#### Gmail 示例：

1. 进入 Google Account → **Security**
2. 启用 **2-Step Verification**
3. 生成 **App Password**（应用专用密码）
4. 使用 App Password 作为 SMTP 密码

#### SendGrid 示例：

1. 注册 SendGrid 账户
2. 创建 **API Key**
3. 或使用 **SMTP Relay** 功能获取凭据

### 步骤 3: 配置环境变量

在 `.env` 文件中添加：

```env
# SMTP 配置
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=your-email@gmail.com
SMTP_PASSWORD=your-app-password
SMTP_USE_TLS=true
EMAIL_FROM=your-email@gmail.com
EMAIL_FROM_NAME=VoiceRAG System
```

## 方法 3: 使用 Salesforce Email API

如果你已配置 Salesforce，可以直接使用 Salesforce 发送邮件。

### 配置环境变量

```env
# 使用 Salesforce 发送邮件（需要先配置 Salesforce）
USE_SALESFORCE_EMAIL=true
# 其他 Salesforce 配置已在 salesforce_setup_guide_zh.md 中说明
```

## 邮件模板配置

### 默认模板

应用包含默认的报价邮件模板，包含以下变量：
- `{customer_name}` - 客户名称
- `{quote_url}` - 报价链接
- `{product_package}` - 产品/套餐
- `{quantity}` - 数量
- `{expected_start_date}` - 期望开始日期
- `{notes}` - 备注

### 自定义模板

可以修改 `app/backend/email_templates.py` 来自定义邮件模板。

## 测试邮件发送

### 使用测试脚本

创建测试脚本 `test_email.py`：

```python
import asyncio
from app.backend.email_service import send_quote_email

async def test():
    await send_quote_email(
        to_email="test@example.com",
        customer_name="测试客户",
        quote_url="https://example.com/quotes/123",
        product_package="标准套餐",
        quantity="10",
        expected_start_date="2024-12-31",
        notes="测试备注"
    )

asyncio.run(test())
```

运行：
```bash
python test_email.py
```

## 环境变量完整列表

```env
# 邮件服务选择（azure, smtp, salesforce）
EMAIL_SERVICE=azure

# Azure Communication Services
AZURE_COMMUNICATION_CONNECTION_STRING=...
AZURE_COMMUNICATION_EMAIL_FROM=noreply@yourdomain.com

# SMTP 配置
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=your-email@gmail.com
SMTP_PASSWORD=your-password
SMTP_USE_TLS=true
EMAIL_FROM=your-email@gmail.com
EMAIL_FROM_NAME=VoiceRAG System

# 邮件模板配置
EMAIL_SUBJECT_PREFIX=[VoiceRAG]  # 可选
```

## 安全建议

1. **不要提交 `.env` 文件到 Git**
2. **使用环境变量或 Azure Key Vault 存储敏感信息**
3. **生产环境使用专用邮件服务**（SendGrid、Mailgun 等）
4. **限制发送频率**，避免被标记为垃圾邮件
5. **验证收件人邮箱格式**

## 常见问题

### Q: Gmail 发送失败怎么办？

**A**: 
- 确保启用了 2-Step Verification
- 使用 App Password 而不是普通密码
- 检查是否允许"不够安全的应用"访问（旧版 Gmail）

### Q: 邮件进入垃圾邮件箱？

**A**:
- 配置 SPF、DKIM、DMARC 记录（使用自定义域名时）
- 使用专业的邮件服务（SendGrid、Mailgun）
- 避免使用免费邮箱作为发件人

### Q: 如何发送 HTML 邮件？

**A**: 应用默认支持 HTML 邮件。修改 `email_templates.py` 中的模板即可。

## 下一步

完成配置后，报价功能将能够：
1. 在创建报价后自动发送邮件通知
2. 邮件包含报价链接和详细信息
3. 支持自定义邮件模板

参考代码实现：`app/backend/email_service.py`


