"""
测试密码编码问题
"""
import os
import requests
from urllib.parse import quote
from dotenv import load_dotenv

load_dotenv('.env', override=True)

username = os.environ.get("SALESFORCE_USERNAME")
password = os.environ.get("SALESFORCE_PASSWORD")
security_token = os.environ.get("SALESFORCE_SECURITY_TOKEN")
consumer_key = os.environ.get("SALESFORCE_CONSUMER_KEY")
consumer_secret = os.environ.get("SALESFORCE_CONSUMER_SECRET")

print("Testing password encoding...")
print(f"Username: {username}")
print(f"Password contains special chars: {any(c in password for c in '.-_@#$%^&*()[]{}|\\/:;\"\'<>?,=+~`')}")

token_url = "https://test.salesforce.com/services/oauth2/token"

# 测试 1: 标准方式（requests 会自动编码）
print("\n[Test 1] Using requests (auto-encoding)")
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
        print("  [SUCCESS]")
    else:
        print(f"  Error: {r1.json().get('error_description', 'Unknown')}")
except Exception as e:
    print(f"  Exception: {str(e)[:100]}")

# 测试 2: 手动 URL 编码
print("\n[Test 2] Manual URL encoding")
full_password = password + security_token
data2 = {
    "grant_type": "password",
    "client_id": consumer_key,
    "client_secret": consumer_secret,
    "username": quote(username, safe=''),
    "password": quote(full_password, safe='')
}
try:
    r2 = requests.post(token_url, data=data2, timeout=10)
    print(f"  Status: {r2.status_code}")
    if r2.status_code == 200:
        print("  [SUCCESS]")
    else:
        print(f"  Error: {r2.json().get('error_description', 'Unknown')}")
except Exception as e:
    print(f"  Exception: {str(e)[:100]}")

# 测试 3: 检查是否是用户名格式问题
print("\n[Test 3] Testing username variations")
username_variants = [
    username,
    username.lower(),
    username.replace('@agentforce.com', ''),
]
for variant in username_variants:
    print(f"  Trying: {variant}")
    data3 = {
        "grant_type": "password",
        "client_id": consumer_key,
        "client_secret": consumer_secret,
        "username": variant,
        "password": password + security_token
    }
    try:
        r3 = requests.post(token_url, data=data3, timeout=10)
        if r3.status_code == 200:
            print(f"    [SUCCESS] Username format '{variant}' works!")
            break
        else:
            error = r3.json().get('error_description', '')[:50]
            print(f"    Failed: {error}")
    except:
        print(f"    Failed")

print("\n" + "=" * 60)
print("If all tests fail:")
print("1. Verify password can login to Salesforce web interface")
print("2. Verify Security Token is correct and recent")
print("3. Check if account has API access enabled")
print("4. Try waiting 5-10 minutes after resetting Security Token")

