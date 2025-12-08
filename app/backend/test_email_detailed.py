"""
详细测试邮件发送功能
"""
import asyncio
import os
import sys
from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(__file__))

load_dotenv('.env', override=True)

from email_service import send_quote_email

async def test():
    print("=" * 60)
    print("Testing Email Sending (Detailed)")
    print("=" * 60)
    
    email_service = os.environ.get("EMAIL_SERVICE", "smtp").lower()
    print(f"\nEmail Service: {email_service}")
    
    # 测试邮件
    to_email = os.environ.get("TEST_EMAIL", "kenan2529044604@gmail.com")
    print(f"Using test email: {to_email}")
    print("(Set TEST_EMAIL environment variable to change)")
    
    print(f"\nSending test email to: {to_email}")
    print("From: jack@infinitysocial.co (Salesforce user)")
    print("Please wait...")
    print("-" * 60)
    
    result = await send_quote_email(
        to_email=to_email,
        customer_name="测试客户",
        quote_url="https://orgfarm-7ff24bad0b-dev-ed.develop.lightning.force.com/lightning/r/Quote/0Q0gL000000nhTdSAI/view",
        product_package="TestProduct1",
        quantity="1",
        expected_start_date="2025-06-01",
        notes="这是一个测试报价"
    )
    
    print("-" * 60)
    if result:
        print("\n[SUCCESS] Email sending function returned True")
        print("\nNext steps to verify:")
        print("1. Check the inbox of:", to_email)
        print("2. Check spam/junk folder")
        print("3. Verify Salesforce user email is configured:")
        print("   - Login to Salesforce")
        print("   - Setup -> My Personal Information -> Email")
        print("   - Confirm email: jack@infinitysocial.co")
        print("4. Check Salesforce email deliverability:")
        print("   - Setup -> Email Administration -> Deliverability")
        print("   - Ensure 'Access Level' allows sending emails")
    else:
        print("\n[ERROR] Email sending function returned False")
        print("Check the logs above for error details")
    
    print("=" * 60)

if __name__ == "__main__":
    asyncio.run(test())

