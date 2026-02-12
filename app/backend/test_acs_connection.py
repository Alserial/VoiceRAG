"""
测试 ACS Call Automation 连接和配置

运行此脚本来验证 ACS 配置是否正确：
    python test_acs_connection.py
"""

import asyncio
import os
import sys
from dotenv import load_dotenv

# 添加当前目录到路径
sys.path.insert(0, os.path.dirname(__file__))

from acs_call_handler import get_acs_client, test_acs_connection

def main():
    """测试 ACS 连接"""
    print("=" * 60)
    print("ACS Call Automation Connection Test")
    print("=" * 60)
    
    # 加载环境变量
    load_dotenv()
    
    # 检查必要的环境变量
    required_vars = ["ACS_CONNECTION_STRING", "ACS_CALLBACK_URL"]
    missing_vars = [var for var in required_vars if not os.environ.get(var)]
    
    if missing_vars:
        print("\n❌ Missing required environment variables:")
        for var in missing_vars:
            print(f"   - {var}")
        print("\n请确保在 .env 文件中配置了这些变量")
        print("\n配置示例:")
        print("ACS_CONNECTION_STRING=endpoint=https://...;accesskey=...")
        print("ACS_CALLBACK_URL=https://your-app.com/api/acs/calls/events")
        return False
    
    print("\n✓ Environment variables check passed")
    print(f"   - ACS_CONNECTION_STRING: {'*' * 20}...{os.environ.get('ACS_CONNECTION_STRING')[-10:]}")
    print(f"   - ACS_CALLBACK_URL: {os.environ.get('ACS_CALLBACK_URL')}")
    
    # 测试连接
    print("\n测试 ACS 客户端连接...")
    try:
        success = asyncio.run(test_acs_connection())
        
        if success:
            print("\n✅ ACS connection test PASSED")
            print("\n下一步:")
            print("1. 确保你的回调 URL 可以从公网访问")
            print("2. 在 Azure Portal 中配置电话号码的来电路由")
            print("3. 拨打你的电话号码进行测试")
            return True
        else:
            print("\n❌ ACS connection test FAILED")
            print("请检查 ACS_CONNECTION_STRING 是否正确")
            return False
            
    except Exception as e:
        print(f"\n❌ Error during connection test: {str(e)}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)




