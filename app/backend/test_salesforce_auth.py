"""
测试 Salesforce 认证（OAuth 2.0 Username-Password Flow）
"""

import os
import json
import requests
from dotenv import load_dotenv

# ---------------------------------------------------------------------
# 1. 加载环境变量
# ---------------------------------------------------------------------
env_path = os.path.join(os.path.dirname(__file__), ".env")
load_dotenv(env_path, override=True)

print("=" * 60)
print("Testing Salesforce Authentication (Username + Password [+ Token])")
print("=" * 60)

# ---------------------------------------------------------------------
# 2. 读取配置
# ---------------------------------------------------------------------
login_url = os.environ.get("SALESFORCE_LOGIN_URL", "https://login.salesforce.com").strip("/")
username = os.environ.get("SALESFORCE_USERNAME")
password = os.environ.get("SALESFORCE_PASSWORD")
security_token = os.environ.get("SALESFORCE_SECURITY_TOKEN", "")  # 可选
consumer_key = os.environ.get("SALESFORCE_CONSUMER_KEY")
consumer_secret = os.environ.get("SALESFORCE_CONSUMER_SECRET")

print("\nConfiguration Check:")
print(f"  Login URL:       {login_url or 'NOT SET'}")
print(f"  Username:        {username or 'NOT SET'}")
print(f"  Password:        {'*' * len(password) if password else 'NOT SET'}")
print(f"  Security Token:  {'*' * len(security_token) if security_token else '(empty, will use password only)'}")
print(f"  Consumer Key:    {consumer_key[:20] + '...' if consumer_key else 'NOT SET'}")
print(f"  Consumer Secret: {'*' * 20 if consumer_secret else 'NOT SET'}")

# ---------------------------------------------------------------------
# 3. 检查必需配置
# ---------------------------------------------------------------------
missing = []
if not login_url:
    missing.append("SALESFORCE_LOGIN_URL (or use default https://login.salesforce.com)")
if not username:
    missing.append("SALESFORCE_USERNAME")
if not password:
    missing.append("SALESFORCE_PASSWORD")
if not consumer_key:
    missing.append("SALESFORCE_CONSUMER_KEY")
if not consumer_secret:
    missing.append("SALESFORCE_CONSUMER_SECRET")

if missing:
    print("\n[ERROR] Missing required configuration!")
    print("Missing:", ", ".join(missing))
    print("请在 .env 中补充以上变量后再运行本脚本。")
    exit(1)

# 实际用于请求的密码：密码 + 可选的 security token
sf_password = password + security_token

# ---------------------------------------------------------------------
# 4. 构造请求
# ---------------------------------------------------------------------
token_url = f"{login_url}/services/oauth2/token"

print("\n" + "=" * 60)
print("Testing OAuth 2.0 Username-Password Flow")
print("=" * 60)

print(f"\nToken Endpoint: {token_url}")

data = {
    "grant_type": "password",
    "client_id": consumer_key,
    "client_secret": consumer_secret,
    "username": username,
    "password": sf_password,
    # 也可以加上 "format": "json"，但默认已经是 json
}

print("\nAttempting authentication...\n(不打印敏感信息，只显示参数结构)")
print("  grant_type  = password")
print("  client_id   = <your consumer key>")
print("  username    =", username)
print("  password    = <password [+ token]>")

# ---------------------------------------------------------------------
# 5. 发送请求并处理响应
# ---------------------------------------------------------------------
try:
    response = requests.post(token_url, data=data, timeout=10)
except requests.exceptions.RequestException as e:
    print(f"\n[ERROR] Network/Request error: {str(e)}")
    print("\n💡 Possible issues:")
    print("  1. 网络连接问题")
    print("  2. SALESFORCE_LOGIN_URL 写错（应该是 https://login.salesforce.com 或 https://test.salesforce.com）")
    print("  3. 防火墙 / 代理 阻止了请求")
    exit(1)

print(f"\nResponse Status: {response.status_code}")

# 尝试解析 JSON；不保证一定是 JSON 响应
try:
    resp_json = response.json()
except ValueError:
    resp_json = None

if response.status_code == 200 and resp_json:
    print("\n[SUCCESS] Authentication successful!")
    access_token = resp_json.get("access_token", "")
    instance_url = resp_json.get("instance_url", "")
    token_type = resp_json.get("token_type", "")

    print(f"  Access Token: {access_token[:50]}...")
    print(f"  Instance URL: {instance_url}")
    print(f"  Token Type:   {token_type}")
    if security_token:
        print("\n✅ Security Token appears to be CORRECT (password + token 成功通过认证)")
    else:
        print("\n✅ 仅使用密码认证成功（当前 IP 可能在 Trusted IP 范围内）")

else:
    print("\n[ERROR] Authentication failed!")

    # 打印原始返回内容（非常关键）
    print("\n--- Raw Response Body ---")
    print(response.text)
    print("--------------------------\n")

    # 尝试解析 JSON
    try:
        resp_json = response.json()
        error = resp_json.get("error", "Unknown error")
        error_description = resp_json.get("error_description", "")
    except ValueError:
        resp_json = None
        error = "Non-JSON response"
        error_description = response.text

    print(f"  Error:        {error}")
    print(f"  Description:  {error_description}")

    # 错误分类分析
    desc_lower = (error_description or "").lower()

    if "invalid_grant" in desc_lower or "authentication failure" in desc_lower:
        print("\n[ANALYSIS] Authentication failure detected")
        print("Possible issues:")
        print("  1. Username 写错或不是这个 org 的用户")
        print("  2. Password 写错或已重置但 .env 未更新")
        print("  3. Security Token 写错 / 已过期 / 没有拼在密码后面")
        print("  4. 登录 URL 写错（Dev Edition 用 login.salesforce.com）")
        print("  5. 用户没有被允许访问这个 Connected App（Permission Set 或 OAuth Policies）")
        print("  6. Org 阻止了 username-password flow（安全设置）")
        print("\nSolutions:")
        print("  - 浏览器手动用 Username+Password 测试能否登录 Salesforce")
        print("  - Reset My Security Token，并更新 .env")
        print("  - Connected App -> Manage -> Permitted Users = All users may self-authorize")
        print("  - 等待 Connected App 激活 2-5 分钟")
        print("  - 检查 Security -> Block Authorization Flows")
    elif "invalid_client_id" in desc_lower or "invalid client" in desc_lower:
        print("\n[ANALYSIS] Client (Connected App) 配置问题")
        print("Possible issues:")
        print("  1. Consumer Key 错了")
        print("  2. Consumer Secret 错了")
        print("  3. Connected App 刚创建还没生效（等 2-5 分钟）")
        print("  4. 请求 URL 不正确")
    else:
        print("\n[ANALYSIS] 未归类错误，请查看 Raw Response 或 Salesforce 文档")


print("\n" + "=" * 60)
print("Test finished.")
print("=" * 60)
