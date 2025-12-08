# Salesforce 连接问题排查

## 当前错误：invalid_client_id

这个错误通常表示：
1. Consumer Key 不正确
2. Connected App 还没有完全生效（需要等待 2-5 分钟）
3. 使用了错误的 Connected App

## 解决方案

### 方案 1: 确认 Connected App 已生效

1. 等待 2-5 分钟让 Connected App 生效
2. 重新测试连接

### 方案 2: 验证 Consumer Key 和 Secret

1. 在 Salesforce 中，进入 Setup → App Manager
2. 找到你创建的 Connected App（"VoiceRAG Integration" 或 "orgfarm_app_1"）
3. 点击应用名称进入详情页
4. 确认 Consumer Key 和 Consumer Secret 与 .env 文件中的一致

### 方案 3: 检查 Connected App 配置

确保 Connected App 的配置正确：
- Enable OAuth Settings: 已勾选
- Callback URL: `http://localhost:8765`
- Selected OAuth Scopes 包含：
  - `Full access (full)`
  - `Manage user data via APIs (api)`

### 方案 4: 使用正确的 Connected App

如果你使用的是 "orgfarm_app_1"，请确保：
1. 在 Salesforce 中查看这个 Connected App 的 Consumer Key 和 Secret
2. 更新 .env 文件中的值

## 测试步骤

1. 确认 .env 文件中的 Consumer Key 和 Secret 正确
2. 等待 2-5 分钟
3. 重新运行测试：`python test_salesforce.py`

