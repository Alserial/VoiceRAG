# Salesforce 配置步骤指南

## 步骤 1: 获取标准价格表的 ID

### 方法 A: 通过 App Launcher（最简单）

1. **打开 App Launcher**
   - 点击 Salesforce 左上角的 **九宫格图标**（App Launcher）

2. **搜索价格表**
   - 在搜索框中输入：`Price Books`
   - 点击搜索结果中的 **Price Books**

3. **找到标准价格表**
   - 在价格表列表中，找到名为 **"Standard Price Book"** 的价格表
   - 如果列表为空或没有标准价格表，继续看下面的方法

4. **获取 ID**
   - 点击 **"Standard Price Book"** 的名称打开详情页
   - 查看浏览器地址栏，URL 类似：
     ```
     https://yourinstance.salesforce.com/lightning/r/Pricebook2/01sXXXXXXXXXXXXXXX/view
     ```
   - 其中 `01sXXXXXXXXXXXXXXX` 就是价格表 ID
   - **复制这个 ID**，稍后会用到

### 方法 B: 如果找不到标准价格表

如果方法 A 找不到，可以创建一个新的价格表：

1. **创建新价格表**
   - 在 Price Books 页面，点击 **New**（新建）
   - 填写：
     - **Price Book Name**: `VoiceRAG Price Book`（或任意名称）
     - **Description**: `Price book for VoiceRAG quotes`（可选）
     - **Active**: 勾选
   - 点击 **Save**

2. **获取新价格表的 ID**
   - 保存后，查看浏览器地址栏的 URL
   - 复制其中的 ID（格式：`01sXXXXXXXXXXXXXXX`）

---

## 步骤 2: 创建产品

1. **打开产品页面**
   - 点击 App Launcher（九宫格图标）
   - 搜索：`Products`
   - 点击 **Products**

2. **创建新产品**
   - 点击 **New**（新建）按钮
   - 填写产品信息：
     - **Product Name**: `标准套餐`（或你想要的名称）
     - **Product Code**: `STD-PKG-001`（可选，但建议填写）
     - **Is Active**: **必须勾选** ✓
   - 点击 **Save**

3. **记录产品名称**
   - 记住你创建的产品名称（例如："标准套餐"）
   - 这个名称需要与用户在表单中输入的"产品/套餐"名称匹配

---

## 步骤 3: 将产品添加到价格表

1. **打开价格表**
   - 回到 Price Books 页面
   - 点击你之前找到或创建的 **Standard Price Book**（或你创建的价格表）

2. **添加产品**
   - 在价格表详情页面，找到 **Related**（相关）选项卡
   - 点击 **Related** 标签
   - 找到 **Price Book Entries**（价格表条目）部分
   - 点击 **New**（新建）或 **Add Products**（添加产品）

3. **选择产品并设置价格**
   - 在弹出窗口中，选择你刚才创建的产品
   - 填写价格信息：
     - **List Price**: `1000.00`（或你想要的默认价格）
     - **Unit Price**: `1000.00`（通常与 List Price 相同）
   - 点击 **Save**

4. **验证**
   - 确认产品已出现在价格表的 **Price Book Entries** 列表中
   - 确认产品的 **Active** 状态为 ✓

---

## 步骤 4: 创建 Connected App（用于 API 访问）

1. **进入 Setup**
   - 点击右上角的 **设置图标**（齿轮图标）
   - 选择 **Setup**

2. **搜索 Connected Apps**
   - 在左侧 Quick Find 搜索框输入：`App Manager`
   - 点击 **App Manager**

3. **创建新的 Connected App**
   - 点击右上角的 **New Connected App** 按钮

4. **填写基本信息**
   - **Connected App Name**: `VoiceRAG Integration`
   - **API Name**: 会自动填充为 `VoiceRAG_Integration`
   - **Contact Email**: 填写你的邮箱地址

5. **启用 OAuth 设置**
   - 向下滚动到 **API (Enable OAuth Settings)** 部分
   - 勾选 **Enable OAuth Settings**
   - **Callback URL**: 输入 `http://localhost:8765`
   - **Selected OAuth Scopes**: 
     - 在左侧选择：`Full access (full)`
     - 点击 **Add** 箭头将其移到右侧
     - 在左侧选择：`Perform requests on your behalf at any time (refresh_token, offline_access)`
     - 点击 **Add** 箭头将其移到右侧

6. **保存**
   - 点击 **Save**
   - **重要**：等待 2-5 分钟让 Connected App 生效

7. **获取凭据**
   - 保存后，页面会显示 **Consumer Key**（客户端 ID）
   - 点击 **Click to reveal** 显示 **Consumer Secret**（客户端密钥）
   - **立即复制这两个值**，因为 Consumer Secret 之后无法再查看！

---

## 步骤 5: 创建集成用户

1. **进入用户管理**
   - 在 Setup 中，Quick Find 搜索：`Users`
   - 点击 **Users**

2. **创建新用户**
   - 点击 **New User** 按钮
   - 填写用户信息：
     - **First Name**: `Integration`
     - **Last Name**: `User`
     - **Alias**: `intuser`（自动生成，可以修改）
     - **Email**: 使用一个真实邮箱（例如：`integration@yourcompany.com`）
     - **Username**: `integration.user@yourcompany.com.sandbox`（如果是 Sandbox）
       或 `integration.user@yourcompany.com`（如果是 Production）
     - **Nickname**: `Integration User`
     - **User License**: 选择 `Salesforce` 或 `Salesforce Platform`
     - **Profile**: 选择 `System Administrator`（或创建一个自定义 Profile）

3. **保存用户**
   - 点击 **Save**
   - 系统会发送密码重置邮件到用户邮箱

4. **设置密码**
   - 登录用户邮箱，点击密码重置链接
   - 设置一个新密码
   - **记录这个密码**，稍后会用到

---

## 步骤 6: 获取 Security Token

1. **以集成用户身份登录**
   - 退出当前用户
   - 使用刚才创建的集成用户登录 Salesforce

2. **重置 Security Token**
   - 登录后，进入 **Setup**
   - 在 Quick Find 搜索：`Security Token`
   - 点击 **Reset My Security Token**
   - 点击 **Reset Security Token** 按钮

3. **获取 Token**
   - Security Token 会发送到集成用户的邮箱
   - 登录邮箱，查看邮件
   - **复制 Security Token**（通常是一串字母和数字）

---

## 步骤 7: 获取 Salesforce 实例 URL

1. **查看浏览器地址栏**
   - 登录 Salesforce 后，查看浏览器地址栏
   - URL 格式通常是：
     - Production: `https://yourcompany.my.salesforce.com`
     - Sandbox: `https://yourcompany--sandboxname.sandbox.my.salesforce.com`
   - **复制这个 URL**（不包括路径，只到 `.com`）

---

## 步骤 8: 配置环境变量

现在你有了所有需要的信息，在 `app/backend/.env` 文件中添加：

```env
# Salesforce 配置
SALESFORCE_INSTANCE_URL=https://yourinstance.salesforce.com
SALESFORCE_USERNAME=integration.user@yourcompany.com
SALESFORCE_PASSWORD=your_password
SALESFORCE_SECURITY_TOKEN=your_security_token
SALESFORCE_CONSUMER_KEY=your_consumer_key
SALESFORCE_CONSUMER_SECRET=your_consumer_secret

# Pricebook 配置
SALESFORCE_DEFAULT_PRICEBOOK_ID=01sXXXXXXXXXXXXXXX

# 可选配置
SALESFORCE_OPPORTUNITY_STAGE=Prospecting
SALESFORCE_CREATE_OPPORTUNITY=false
```

**重要提示**：
- 将上述所有 `your_xxx` 替换为你实际获取的值
- `SALESFORCE_PASSWORD` 是集成用户的密码
- `SALESFORCE_SECURITY_TOKEN` 是刚才从邮箱获取的 Token
- `SALESFORCE_DEFAULT_PRICEBOOK_ID` 是步骤 1 中获取的价格表 ID

---

## 步骤 9: 测试连接

1. **安装依赖**
   ```bash
   .\.venv\Scripts\activate
   pip install -r app/backend/requirements.txt
   ```

2. **测试 Salesforce 连接**
   ```python
   from app.backend.salesforce_service import get_salesforce_service
   sf = get_salesforce_service()
   print("Salesforce available:", sf.is_available())
   ```

3. **启动服务器并测试**
   - 启动服务器
   - 访问 http://localhost:8765
   - 填写报价表单并提交
   - 检查 Salesforce 中是否创建了 Quote

---

## 配置清单

完成所有步骤后，检查以下项目：

- [ ] 已获取标准价格表的 ID
- [ ] 已创建至少一个产品
- [ ] 已将产品添加到价格表
- [ ] 已创建 Connected App
- [ ] 已获取 Consumer Key 和 Consumer Secret
- [ ] 已创建集成用户
- [ ] 已获取 Security Token
- [ ] 已获取 Salesforce 实例 URL
- [ ] 已在 `.env` 文件中配置所有变量
- [ ] 已测试连接成功

---

## 常见问题

### Q: 找不到 Standard Price Book 怎么办？

**A**: 可以创建一个新的价格表，然后使用新价格表的 ID。

### Q: Consumer Secret 忘记了怎么办？

**A**: 需要重新创建 Connected App，或者使用 Salesforce CLI 查询。

### Q: Security Token 收不到邮件？

**A**: 检查垃圾邮件文件夹，或者尝试重新发送。

### Q: 连接测试失败？

**A**: 检查：
1. 所有环境变量是否正确
2. 密码是否包含 Security Token（格式：`password + security_token`）
3. 集成用户是否有足够权限
4. Connected App 是否已生效（等待 2-5 分钟）

---

完成这些步骤后，你的 Salesforce 就配置好了！🎉


