"""
检查 Salesforce 邮件发送配置和状态
"""
import os
import sys
from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(__file__))

load_dotenv('.env', override=True)

from salesforce_service import get_salesforce_service
import requests

def check_email_config():
    print("=" * 60)
    print("Salesforce Email Configuration Check")
    print("=" * 60)
    
    sf_service = get_salesforce_service()
    
    if not sf_service.is_available():
        print("\n[ERROR] Salesforce is not available")
        return
    
    print("\n[OK] Salesforce connection established")
    
    # 1. 检查用户信息
    print("\n1. Checking User Information...")
    try:
        username = os.environ.get("SALESFORCE_USERNAME", "")
        user_query = f"SELECT Id, Username, Email, IsActive FROM User WHERE Username = '{username}' LIMIT 1"
        user_result = sf_service.sf.query(user_query)
        
        if user_result["totalSize"] > 0:
            user = user_result["records"][0]
            print(f"   User ID: {user['Id']}")
            print(f"   Username: {user['Username']}")
            print(f"   Email: {user.get('Email', 'NOT SET')}")
            print(f"   Is Active: {user.get('IsActive', False)}")
            
            if not user.get('Email'):
                print("   [WARNING] User email is not set!")
                print("   Fix: Setup -> My Personal Information -> Email")
        else:
            print("   [ERROR] User not found")
    except Exception as e:
        print(f"   [ERROR] {str(e)}")
    
    # 2. 检查邮件可发送性设置
    print("\n2. Checking Email Deliverability Settings...")
    try:
        instance_url = sf_service.instance_url or sf_service.sf.sf_instance
        deliverability_url = f"{instance_url}/services/data/v58.0/query/?q=SELECT+Id+FROM+Organization+LIMIT+1"
        headers = {
            "Authorization": f"Bearer {sf_service.sf.session_id}",
            "Content-Type": "application/json"
        }
        
        # 注意：Deliverability 设置需要通过 UI 检查，API 无法直接查询
        print("   [INFO] Email Deliverability settings must be checked in Salesforce UI:")
        print("   Steps:")
        print("   1. Login to Salesforce")
        print("   2. Setup (齿轮图标) -> Email Administration -> Deliverability")
        print("   3. Check 'Access Level' - should be 'All Email' or 'System Email Only'")
        print("   4. If restricted, change to 'All Email' and save")
    except Exception as e:
        print(f"   [ERROR] {str(e)}")
    
    # 3. 测试 emailSimple API
    print("\n3. Testing emailSimple API...")
    try:
        instance_url = sf_service.instance_url or sf_service.sf.sf_instance
        email_endpoint = f"{instance_url}/services/data/v58.0/actions/standard/emailSimple"
        
        test_payload = {
            "inputs": [{
                "emailBody": "<p>This is a test email from VoiceRAG.</p>",
                "emailAddresses": "test@example.com",  # 使用测试邮箱，不会真正发送
                "emailSubject": "Test Email",
                "senderType": "CurrentUser"
            }]
        }
        
        headers = {
            "Authorization": f"Bearer {sf_service.sf.session_id}",
            "Content-Type": "application/json"
        }
        
        print(f"   Endpoint: {email_endpoint}")
        print("   Sending test request (to test@example.com - won't actually send)...")
        
        response = requests.post(email_endpoint, json=test_payload, headers=headers, timeout=10)
        
        print(f"   Response Status: {response.status_code}")
        
        if response.status_code in [200, 201]:
            response_data = response.json() if response.text else {}
            print(f"   Response: {response_data}")
            
            if isinstance(response_data, dict):
                if "results" in response_data:
                    for i, result in enumerate(response_data["results"]):
                        print(f"   Result {i+1}:")
                        if "errors" in result and result["errors"]:
                            print(f"     [ERROR] {result['errors']}")
                        else:
                            print(f"     [OK] No errors in response")
                        if "outputValues" in result:
                            print(f"     Output: {result['outputValues']}")
        else:
            print(f"   [ERROR] API returned error: {response.text[:500]}")
            
    except Exception as e:
        print(f"   [ERROR] {str(e)}")
    
    # 4. 检查邮件发送历史（如果可能）
    print("\n4. Checking Email Send History...")
    try:
        # 查询最近的 EmailMessage 记录
        email_query = "SELECT Id, Subject, ToAddress, Status, CreatedDate FROM EmailMessage ORDER BY CreatedDate DESC LIMIT 5"
        email_result = sf_service.sf.query(email_query)
        
        if email_result["totalSize"] > 0:
            print(f"   Found {email_result['totalSize']} recent email records:")
            for record in email_result["records"]:
                print(f"   - {record.get('Subject', 'N/A')} to {record.get('ToAddress', 'N/A')} (Status: {record.get('Status', 'N/A')})")
        else:
            print("   No email records found (this is normal if no emails have been sent)")
    except Exception as e:
        print(f"   [ERROR] {str(e)}")
    
    # 5. 建议
    print("\n" + "=" * 60)
    print("RECOMMENDATIONS:")
    print("=" * 60)
    print("1. Verify Email Deliverability Settings in Salesforce:")
    print("   Setup -> Email Administration -> Deliverability")
    print("   Set 'Access Level' to 'All Email'")
    print("\n2. Check User Email Address:")
    print("   Setup -> My Personal Information -> Email")
    print("   Ensure email is set and verified")
    print("\n3. Check Spam/Junk Folder:")
    print("   Emails from Salesforce may be filtered as spam")
    print("\n4. Test with a different email address:")
    print("   Some email providers block Salesforce emails")
    print("\n5. Check Salesforce Email Logs:")
    print("   Setup -> Email Administration -> View Email Logs")
    print("   Look for your sent emails and their status")
    print("=" * 60)

if __name__ == "__main__":
    check_email_config()

