"""
测试 Salesforce 连接的脚本
"""
import os
import sys

# 添加当前目录到路径
sys.path.insert(0, os.path.dirname(__file__))

# 尝试加载 .env 文件
try:
    from dotenv import load_dotenv
    import os
    # 确保从当前目录加载 .env 文件
    env_path = os.path.join(os.path.dirname(__file__), '.env')
    load_dotenv(env_path, override=True)
except ImportError:
    print("Warning: python-dotenv not installed, using system environment variables")
    pass

from salesforce_service import get_salesforce_service

print("=" * 50)
print("Testing Salesforce Connection")
print("=" * 50)

# 获取 Salesforce 服务
sf_service = get_salesforce_service()

# 检查连接状态
if sf_service.is_available():
    print("[OK] Salesforce connection successful!")
    print(f"Instance URL: {sf_service.instance_url}")
    
    # 测试查询
    try:
        result = sf_service.sf.query("SELECT Id, Name FROM Account LIMIT 1")
        print(f"[OK] Query test successful! Found {result['totalSize']} records")
    except Exception as e:
        print(f"[ERROR] Query test failed: {str(e)}")
    
    # 测试产品查询
    try:
        product_result = sf_service.sf.query("SELECT Id, Name FROM Product2 WHERE IsActive = true LIMIT 5")
        print(f"[OK] Product query successful! Found {product_result['totalSize']} active products")
        if product_result['totalSize'] > 0:
            print("Product list:")
            for product in product_result['records']:
                print(f"  - {product['Name']} (ID: {product['Id']})")
    except Exception as e:
        print(f"[ERROR] Product query failed: {str(e)}")
    
    # 测试价格表查询
    try:
        pricebook_id = os.environ.get("SALESFORCE_DEFAULT_PRICEBOOK_ID")
        if pricebook_id:
            pricebook_result = sf_service.sf.query(f"SELECT Id, Name FROM Pricebook2 WHERE Id = '{pricebook_id}'")
            if pricebook_result['totalSize'] > 0:
                print(f"[OK] Pricebook query successful! Name: {pricebook_result['records'][0]['Name']}")
            else:
                print(f"[WARNING] Pricebook ID {pricebook_id} not found")
        else:
            print("[WARNING] Pricebook ID not configured")
    except Exception as e:
        print(f"[ERROR] Pricebook query failed: {str(e)}")
        
else:
    print("[ERROR] Salesforce connection failed!")
    print("Please check the configuration in .env file")

print("=" * 50)

