"""
详细诊断 Salesforce 认证问题
"""
import os
import requests
from dotenv import load_dotenv

load_dotenv('.env', override=True)

username = os.environ.get("SALESFORCE_USERNAME")
password = os.environ.get("SALESFORCE_PASSWORD")
security_token = os.environ.get("SALESFORCE_SECURITY_TOKEN")
consumer_key = os.environ.get("SALESFORCE_CONSUMER_KEY")
consumer_secret = os.environ.get("SALESFORCE_CONSUMER_SECRET")

print("=" * 60)
print("Salesforce Authentication Diagnosis")
print("=" * 60)

print(f"\nConfiguration:")
print(f"  Username: {username}")
print(f"  Password length: {len(password)}")
print(f"  Security Token: {security_token}")
print(f"  Token length: {len(security_token)}")
print(f"  Consumer Key: {consumer_key[:30]}...")

# 测试不同的密码组合方式
print("\n" + "=" * 60)
print("Testing different password combinations...")
print("=" * 60)

token_url = "https://test.salesforce.com/services/oauth2/token"

# 测试 1: 标准组合 (password + token)
print("\n[Test 1] Standard: password + security_token")
data1 = {
    "grant_type": "password",
    "client_id": consumer_key,
    "client_secret": consumer_secret,
    "username": username,
    "password": password + security_token
}
try:
    r1 = requests.post(token_url, data=data1, timeout=10)
    print(f"  Status: {r1.status_code}")
    if r1.status_code == 200:
        print("  [SUCCESS] Authentication works!")
    else:
        print(f"  Error: {r1.json().get('error_description', 'Unknown')}")
except Exception as e:
    print(f"  Exception: {str(e)[:100]}")

# 测试 2: 只使用密码（不使用 token）- 这应该会失败，但可以验证密码是否正确
print("\n[Test 2] Password only (should fail, but tests password)")
data2 = {
    "grant_type": "password",
    "client_id": consumer_key,
    "client_secret": consumer_secret,
    "username": username,
    "password": password  # 只有密码，没有 token
}
try:
    r2 = requests.post(token_url, data=data2, timeout=10)
    print(f"  Status: {r2.status_code}")
    if r2.status_code == 200:
        print("  [NOTE] Password works without token (unusual)")
    else:
        error_desc = r2.json().get('error_description', '')
        print(f"  Error: {error_desc}")
        if "token" in error_desc.lower():
            print("  [INFO] This confirms password is correct, token is required")
except Exception as e:
    print(f"  Exception: {str(e)[:100]}")

# 测试 3: 检查 Consumer Key/Secret
print("\n[Test 3] Testing Consumer Key/Secret validity")
# 使用一个明显错误的用户名来测试是否是 Consumer Key 的问题
data3 = {
    "grant_type": "password",
    "client_id": consumer_key,
    "client_secret": consumer_secret,
    "username": "invalid_user@test.com",
    "password": "invalid"
}
try:
    r3 = requests.post(token_url, data=data3, timeout=10)
    print(f"  Status: {r3.status_code}")
    error_desc = r3.json().get('error_description', '')
    if "invalid_client" in error_desc.lower():
        print("  [ERROR] Consumer Key/Secret is invalid!")
    elif "authentication failure" in error_desc.lower():
        print("  [OK] Consumer Key/Secret is valid (got auth error, not client error)")
    else:
        print(f"  Response: {error_desc}")
except Exception as e:
    print(f"  Exception: {str(e)[:100]}")

print("\n" + "=" * 60)
print("Recommendations:")
print("=" * 60)
print("1. If Test 1 failed but Test 2 shows password is correct:")
print("   -> Security Token might be wrong or not yet active")
print("2. If Test 3 shows invalid_client:")
print("   -> Consumer Key/Secret is incorrect")
print("3. If all tests fail with authentication failure:")
print("   -> Try using administrator account")
print("   -> Or wait 5-10 minutes for Security Token to fully activate")

