"""
测试产品 API
"""
import asyncio
import os
import sys
from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(__file__))

load_dotenv('.env', override=True)

from salesforce_service import get_salesforce_service

print("=" * 60)
print("Testing Products API")
print("=" * 60)

sf_service = get_salesforce_service()

print(f"\nSalesforce available: {sf_service.is_available()}")
print(f"Has sf object: {sf_service.sf is not None}")

if sf_service.is_available() and sf_service.sf:
    try:
        result = sf_service.sf.query(
            "SELECT Id, Name FROM Product2 WHERE IsActive = true ORDER BY Name LIMIT 10"
        )
        print(f"\nProducts found: {result['totalSize']}")
        if result['totalSize'] > 0:
            print("\nProduct list:")
            for record in result['records']:
                print(f"  - {record['Name']} (ID: {record['Id']})")
        else:
            print("\nNo active products found in Salesforce")
    except Exception as e:
        print(f"\nError querying products: {str(e)}")
else:
    print("\nSalesforce not available - cannot query products")

print("=" * 60)

