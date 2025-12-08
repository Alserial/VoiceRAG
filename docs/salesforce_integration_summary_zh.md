# Salesforce 和邮件功能集成总结

## 已完成的工作

### 1. 代码实现

✅ **Salesforce 集成模块** (`app/backend/salesforce_service.py`)
- 支持 Username-Password OAuth 流程
- 自动创建/查找 Account 和 Contact
- 创建 Quote 和 Quote Line Items
- 支持自定义字段和配置

✅ **邮件发送服务** (`app/backend/email_service.py`)
- 支持三种邮件服务：
  - Azure Communication Services Email
  - SMTP（通用，支持 Gmail、Outlook、SendGrid 等）
  - Salesforce Email API
- HTML 和纯文本邮件模板
- 自动检测联系方式中的邮箱地址

✅ **报价处理器更新** (`app/backend/app.py`)
- 集成 Salesforce 创建报价
- 自动发送邮件通知
- 如果 Salesforce 不可用，自动回退到 Mock 模式

✅ **依赖更新** (`app/backend/requirements.txt`)
- 添加 `simple-salesforce==1.12.6`

### 2. 文档

✅ **Salesforce 设置指南** (`docs/salesforce_setup_guide_zh.md`)
- 详细的 Salesforce 配置步骤
- Connected App 创建指南
- 集成用户设置
- 产品和价格表配置

✅ **邮件设置指南** (`docs/email_setup_guide_zh.md`)
- 三种邮件服务的配置方法
- 环境变量说明
- 测试方法

## 下一步操作

### 步骤 1: 配置 Salesforce

1. 按照 `docs/salesforce_setup_guide_zh.md` 完成 Salesforce 设置
2. 获取以下信息：
   - Instance URL
   - Username
   - Password
   - Security Token
   - Consumer Key
   - Consumer Secret

### 步骤 2: 配置邮件服务

选择一种邮件服务并配置：

**选项 A: Azure Communication Services（推荐，如果使用 Azure）**
```env
EMAIL_SERVICE=azure
AZURE_COMMUNICATION_CONNECTION_STRING=endpoint=https://...;accesskey=...
AZURE_COMMUNICATION_EMAIL_FROM=noreply@yourdomain.com
```

**选项 B: SMTP（通用）**
```env
EMAIL_SERVICE=smtp
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=your-email@gmail.com
SMTP_PASSWORD=your-app-password
SMTP_USE_TLS=true
EMAIL_FROM=your-email@gmail.com
EMAIL_FROM_NAME=VoiceRAG System
```

**选项 C: Salesforce Email**
```env
EMAIL_SERVICE=salesforce
# 其他 Salesforce 配置已在步骤 1 中设置
```

### 步骤 3: 配置环境变量

在 `app/backend/.env` 文件中添加：

```env
# Salesforce 配置
SALESFORCE_INSTANCE_URL=https://yourinstance.salesforce.com
SALESFORCE_USERNAME=integration.user@yourcompany.com
SALESFORCE_PASSWORD=your_password
SALESFORCE_SECURITY_TOKEN=your_security_token
SALESFORCE_CONSUMER_KEY=your_consumer_key
SALESFORCE_CONSUMER_SECRET=your_consumer_secret

# 可选配置
SALESFORCE_DEFAULT_PRICEBOOK_ID=01sXXXXXXXXXXXXXXX
SALESFORCE_OPPORTUNITY_STAGE=Prospecting
SALESFORCE_CREATE_OPPORTUNITY=false

# 邮件服务配置（选择一种）
EMAIL_SERVICE=smtp
# ... 其他邮件配置
```

### 步骤 4: 安装依赖

```bash
# 激活虚拟环境
.\.venv\Scripts\activate

# 安装新依赖
pip install -r app/backend/requirements.txt
```

### 步骤 5: 测试

1. **测试 Salesforce 连接**：
   ```python
   from app.backend.salesforce_service import get_salesforce_service
   sf = get_salesforce_service()
   print("Salesforce available:", sf.is_available())
   ```

2. **测试邮件发送**：
   ```python
   import asyncio
   from app.backend.email_service import send_quote_email
   
   asyncio.run(send_quote_email(
       to_email="test@example.com",
       customer_name="测试客户",
       quote_url="https://example.com/quotes/123",
       product_package="标准套餐",
       quantity="10"
   ))
   ```

3. **测试完整流程**：
   - 启动服务器
   - 访问 http://localhost:8765
   - 填写报价表单并提交
   - 检查 Salesforce 中是否创建了 Quote
   - 检查邮箱是否收到通知

## 功能说明

### 报价创建流程

1. 用户提交报价表单
2. 系统尝试在 Salesforce 中创建：
   - Account（如果不存在）
   - Contact（如果不存在）
   - Opportunity（可选，如果启用）
   - Quote
   - Quote Line Items（如果配置了产品和价格表）
3. 如果 Salesforce 不可用，使用 Mock 模式
4. 如果联系方式是邮箱，自动发送邮件通知
5. 返回报价链接给前端

### 邮件发送逻辑

- 自动检测联系方式中的邮箱地址（包含 `@` 符号）
- 如果配置了邮件服务，自动发送通知
- 邮件包含报价链接和详细信息
- 支持 HTML 和纯文本格式

### 错误处理

- Salesforce 连接失败时自动回退到 Mock 模式
- 邮件发送失败不会影响报价创建
- 所有错误都会记录到日志中

## 注意事项

1. **安全性**：
   - 不要将 `.env` 文件提交到 Git
   - 生产环境使用 Azure Key Vault 或环境变量
   - Security Token 需要保密

2. **Salesforce 限制**：
   - API 调用有频率限制
   - 某些字段可能需要特殊权限
   - 产品和价格表需要预先配置

3. **邮件服务**：
   - Gmail 需要使用 App Password
   - 免费邮箱可能有限制
   - 建议使用专业邮件服务（SendGrid、Mailgun）

## 故障排查

### Salesforce 连接失败

1. 检查环境变量是否正确
2. 验证 Security Token（密码 + Security Token）
3. 确认 Connected App 已正确配置
4. 检查用户权限

### 邮件发送失败

1. 检查邮件服务配置
2. 验证 SMTP 凭据（如果使用 SMTP）
3. 检查防火墙和网络连接
4. 查看日志获取详细错误信息

### Quote 创建失败

1. 检查 Salesforce 中是否启用了 Quotes
2. 验证产品和价格表配置
3. 检查用户权限
4. 查看 Salesforce 日志

## 支持

如有问题，请查看：
- `docs/salesforce_setup_guide_zh.md` - Salesforce 设置
- `docs/email_setup_guide_zh.md` - 邮件设置
- 应用日志文件


