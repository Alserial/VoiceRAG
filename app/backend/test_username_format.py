"""
测试不同的用户名格式
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

print("Testing different username formats...")
print(f"Original username: {username}")

# 尝试不同的用户名格式
username_variants = [
    username,  # 原始格式
    username.lower(),  # 小写
    username.upper(),  # 大写
]

token_url = "https://test.salesforce.com/services/oauth2/token"

for variant in username_variants:
    print(f"\nTrying username: {variant}")
    data = {
        "grant_type": "password",
        "client_id": consumer_key,
        "client_secret": consumer_secret,
        "username": variant,
        "password": password + security_token
    }
    
    try:
        response = requests.post(token_url, data=data, timeout=10)
        if response.status_code == 200:
            print(f"[SUCCESS] Username format '{variant}' works!")
            result = response.json()
            print(f"  Access Token: {result.get('access_token', '')[:50]}...")
            break
        else:
            error_data = response.json() if response.text else {}
            error_desc = error_data.get('error_description', '')
            print(f"  Failed: {error_desc[:100]}")
    except Exception as e:
        print(f"  Error: {str(e)[:100]}")

print("\n" + "=" * 60)
print("If all failed, please check:")
print("1. Wait 2-3 minutes after resetting Security Token")
print("2. Verify the Security Token from email is correct")
print("3. Check if password contains special characters that need escaping")
print("4. Try using administrator account instead")

