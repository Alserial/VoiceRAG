# 报价功能测试指南

## 快速测试步骤

### 1. 启动开发服务器

**Windows PowerShell:**
```powershell
pwsh .\scripts\start.ps1
```

**或者手动启动:**

1. **构建前端**（如果还没构建）:
   ```powershell
   cd app/frontend
   npm install
   npm run build
   cd ../..
   ```

2. **启动后端**:
   ```powershell
   # 确保虚拟环境已激活
   .\.venv\Scripts\activate
   
   # 启动服务器
   cd app/backend
   python app.py
   ```

### 2. 访问应用

打开浏览器访问: **http://localhost:8765**

### 3. 测试报价功能

1. **找到报价表单**
   - 在页面下方，你会看到一个白色的卡片区域，标题是 "Request a quote"
   - 表单包含以下字段：
     - 客户名称（必填）
     - 联系方式（必填）
     - 产品/套餐（必填）
     - 数量（必填，数字）
     - 期望开始日期（必填，日期选择器）
     - 备注（可选）

2. **填写表单示例**:
   ```
   客户名称: 测试公司
   联系方式: test@example.com
   产品/套餐: 标准套餐
   数量: 10
   期望开始日期: 2024-12-31
   备注: 这是一个测试报价
   ```

3. **提交表单**
   - 点击 "Send quote" 按钮
   - 按钮会显示 "Sending..." 状态
   - 提交成功后，会在表单下方显示一个绿色的成功消息框
   - 消息框中会显示生成的报价链接（目前是 mock 链接，格式如：`https://example.com/quotes/xxxx-xxxx-xxxx`）

4. **验证结果**
   - ✅ 成功：看到绿色消息框和报价链接
   - ❌ 失败：看到红色错误消息

### 4. 检查后端日志

在后端控制台（运行 `python app.py` 的终端）中，你应该能看到类似这样的日志：

```
INFO:voicerag:Mock quote created: id=xxxx-xxxx-xxxx, customer=测试公司
```

## 测试场景

### 场景 1: 正常提交
- 填写所有必填字段
- 点击提交
- **预期**: 成功显示报价链接

### 场景 2: 缺少必填字段
- 留空某些必填字段（如客户名称）
- 点击提交
- **预期**: 浏览器阻止提交（HTML5 验证）

### 场景 3: 网络错误
- 停止后端服务器
- 填写表单并提交
- **预期**: 显示错误消息 "Unable to submit the quote right now."

### 场景 4: 多次提交
- 提交一次报价
- 再次填写并提交
- **预期**: 每次都会生成新的报价链接，新链接会替换旧的

## 当前 Mock 行为

目前后端返回的是模拟数据：
- 生成一个随机的 UUID 作为报价 ID
- 返回格式化的 URL: `https://example.com/quotes/{quote_id}`
- 这个链接目前不会真正打开任何页面（只是示例）

## 下一步：集成 Salesforce

当 Salesforce 配置完成后，需要：
1. 在 `app/backend/app.py` 中替换 `handle_mock_quote` 函数
2. 添加 Salesforce API 调用逻辑
3. 返回真实的 Salesforce Quote 记录链接

## 故障排查

### 问题：前端没有显示报价表单
- **检查**: 确保前端已构建（`npm run build`）
- **检查**: 查看浏览器控制台是否有 JavaScript 错误

### 问题：提交后没有响应
- **检查**: 后端服务器是否正在运行（端口 8765）
- **检查**: 浏览器开发者工具 Network 标签，查看 `/api/quotes` 请求的状态
- **检查**: 后端控制台是否有错误日志

### 问题：看到 CORS 错误
- **检查**: 确保前端是通过后端服务器访问的（http://localhost:8765），而不是直接打开 HTML 文件


