"""
测试邮件发送功能
"""
import asyncio
import os
import sys
from dotenv import load_dotenv

# 添加当前目录到路径
sys.path.insert(0, os.path.dirname(__file__))

# 加载环境变量
env_path = os.path.join(os.path.dirname(__file__), '.env')
load_dotenv(env_path, override=True)

from email_service import send_quote_email

async def test():
    print("=" * 60)
    print("Testing Email Sending")
    print("=" * 60)
    
    email_service = os.environ.get("EMAIL_SERVICE", "smtp").lower()
    print(f"\nEmail Service: {email_service}")
    
    # 测试邮件
    to_email = input("\nEnter test email address: ").strip()
    if not to_email:
        print("No email provided, using default test email")
        to_email = "test@example.com"
    
    print(f"\nSending test email to: {to_email}")
    print("Please wait...")
    
    result = await send_quote_email(
        to_email=to_email,
        customer_name="测试客户",
        quote_url="https://example.com/quotes/123",
        product_package="标准套餐",
        quantity="10",
        expected_start_date="2024-12-31",
        notes="这是一个测试报价"
    )
    
    if result:
        print("\n[SUCCESS] Email sent successfully!")
        print(f"Please check the inbox of: {to_email}")
    else:
        print("\n[ERROR] Failed to send email")
        print("Check the logs above for error details")
    
    print("=" * 60)

if __name__ == "__main__":
    asyncio.run(test())

