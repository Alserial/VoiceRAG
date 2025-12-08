"""
检查 Salesforce 邮件发送状态和详细信息
"""
import os
import sys
from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(__file__))

load_dotenv('.env', override=True)

from salesforce_service import get_salesforce_service

def check_email_status():
    print("=" * 60)
    print("Salesforce Email Status Check")
    print("=" * 60)
    
    sf_service = get_salesforce_service()
    
    if not sf_service.is_available():
        print("\n[ERROR] Salesforce is not available")
        return
    
    # 查询最近的邮件记录
    print("\nRecent Email Messages:")
    print("-" * 60)
    
    try:
        # EmailMessage 状态码说明：
        # 0 = New (新建)
        # 1 = Read (已读)
        # 2 = Replied (已回复)
        # 3 = Sent (已发送)
        # 4 = Forwarded (已转发)
        # 5 = Bounced (退回)
        
        email_query = """
            SELECT Id, Subject, ToAddress, FromAddress, FromName, 
                   Status, CreatedDate, LastModifiedDate, 
                   MessageDate, IsDeleted, HasAttachment
            FROM EmailMessage 
            ORDER BY CreatedDate DESC 
            LIMIT 10
        """
        
        email_result = sf_service.sf.query(email_query)
        
        if email_result["totalSize"] > 0:
            print(f"\nFound {email_result['totalSize']} email records:\n")
            
            status_map = {
                0: "New",
                1: "Read", 
                2: "Replied",
                3: "Sent",
                4: "Forwarded",
                5: "Bounced"
            }
            
            for i, record in enumerate(email_result["records"], 1):
                status = record.get("Status", "Unknown")
                status_text = status_map.get(status, f"Unknown({status})")
                
                print(f"Email #{i}:")
                print(f"  ID: {record.get('Id')}")
                print(f"  Subject: {record.get('Subject', 'N/A')}")
                print(f"  To: {record.get('ToAddress', 'N/A')}")
                print(f"  From: {record.get('FromAddress', 'N/A')} ({record.get('FromName', 'N/A')})")
                print(f"  Status: {status_text} ({status})")
                print(f"  Created: {record.get('CreatedDate', 'N/A')}")
                print(f"  Message Date: {record.get('MessageDate', 'N/A')}")
                print(f"  Has Attachment: {record.get('HasAttachment', False)}")
                print()
        else:
            print("No email records found")
        
        # 检查 EmailStatus 对象（如果存在）
        print("\nChecking Email Status Records...")
        print("-" * 60)
        
        try:
            status_query = """
                SELECT Id, TaskId, WhoId, WhatId, Status, 
                       CreatedDate, LastModifiedDate
                FROM EmailStatus
                ORDER BY CreatedDate DESC
                LIMIT 10
            """
            status_result = sf_service.sf.query(status_query)
            
            if status_result["totalSize"] > 0:
                print(f"\nFound {status_result['totalSize']} email status records:\n")
                for record in status_result["records"]:
                    print(f"  Status: {record.get('Status', 'N/A')}")
                    print(f"  Task ID: {record.get('TaskId', 'N/A')}")
                    print(f"  Created: {record.get('CreatedDate', 'N/A')}")
                    print()
            else:
                print("No EmailStatus records found")
        except Exception as e:
            print(f"Could not query EmailStatus (may not be available): {str(e)}")
        
        # 检查 SingleEmailMessage 对象（用于 API 发送的邮件）
        print("\nChecking for API-sent emails...")
        print("-" * 60)
        
        try:
            # 查询最近的 Task 记录（邮件发送会创建 Task）
            task_query = """
                SELECT Id, Subject, WhoId, WhatId, Status, 
                       CreatedDate, Description, Type
                FROM Task
                WHERE Type = 'Email' OR Subject LIKE '%Quote%'
                ORDER BY CreatedDate DESC
                LIMIT 10
            """
            task_result = sf_service.sf.query(task_query)
            
            if task_result["totalSize"] > 0:
                print(f"\nFound {task_result['totalSize']} related Task records:\n")
                for record in task_result["records"]:
                    print(f"  Subject: {record.get('Subject', 'N/A')}")
                    print(f"  Type: {record.get('Type', 'N/A')}")
                    print(f"  Status: {record.get('Status', 'N/A')}")
                    print(f"  Created: {record.get('CreatedDate', 'N/A')}")
                    print()
            else:
                print("No related Task records found")
        except Exception as e:
            print(f"Could not query Task records: {str(e)}")
            
    except Exception as e:
        print(f"[ERROR] {str(e)}")
        import traceback
        traceback.print_exc()
    
    print("\n" + "=" * 60)
    print("IMPORTANT: Email Status Code 3 = 'Sent'")
    print("=" * 60)
    print("\nIf emails show Status=3 but are not received:")
    print("1. Check Salesforce Email Deliverability:")
    print("   Setup -> Email Administration -> Deliverability")
    print("   Access Level should be 'All Email'")
    print("\n2. Check Email Logs in Salesforce:")
    print("   Setup -> Email Administration -> View Email Logs")
    print("   Look for your emails and check their delivery status")
    print("\n3. Check Spam/Junk Folder:")
    print("   Emails from Salesforce may be filtered")
    print("\n4. Verify Email Address:")
    print("   Ensure the recipient email is correct and active")
    print("=" * 60)

if __name__ == "__main__":
    check_email_status()

