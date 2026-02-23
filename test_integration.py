#!/usr/bin/env python3
"""
Test script for ReplyFast Auto Meta WhatsApp integration
"""

import json
import requests
from config import META_API_TOKEN, META_PHONE_ID
from meta_whatsapp import MetaWhatsAppAPI

def test_meta_api_connection():
    """Test if Meta API credentials are working"""
    print("🧪 Testing Meta WhatsApp API Connection...")
    
    try:
        api = MetaWhatsAppAPI()
        print(f"✅ Meta API initialized successfully")
        print(f"📱 Phone ID: {META_PHONE_ID}")
        print(f"🔑 Token configured: {'Yes' if META_API_TOKEN else 'No'}")
        
        # Test API endpoint accessibility
        url = f"https://graph.facebook.com/v18.0/{META_PHONE_ID}"
        headers = {"Authorization": f"Bearer {META_API_TOKEN}"}
        
        response = requests.get(url, headers=headers)
        if response.status_code == 200:
            print("✅ Meta API endpoint accessible")
            data = response.json()
            print(f"📞 Phone Number: {data.get('display_phone_number', 'N/A')}")
            print(f"✅ Verification Status: {data.get('verified_name', 'N/A')}")
        else:
            print(f"❌ Meta API endpoint error: {response.status_code}")
            print(f"Response: {response.text}")
            
    except Exception as e:
        print(f"❌ Meta API test failed: {str(e)}")
        return False
    
    return True

def test_webhook_format():
    """Test webhook message parsing"""
    print("\n🧪 Testing Webhook Message Format...")
    
    # Sample Meta webhook payload
    test_payload = {
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "messages": [
                                {
                                    "from": "1234567890",
                                    "text": {
                                        "body": "Hello"
                                    },
                                    "type": "text"
                                }
                            ]
                        }
                    }
                ]
            }
        ]
    }
    
    # Test message extraction
    from app import extract_message_content
    
    wa_id, text = extract_message_content(test_payload)
    print(f"📱 Extracted WA ID: {wa_id}")
    print(f"💬 Extracted Text: {text}")
    
    if wa_id == "1234567890" and text == "Hello":
        print("✅ Message extraction working correctly")
        return True
    else:
        print("❌ Message extraction failed")
        return False

def test_redis_connection():
    """Test Redis connection"""
    print("\n🧪 Testing Redis Connection...")
    
    try:
        import redis
        from config import REDIS_HOST, REDIS_PORT, REDIS_DB
        
        r = redis.StrictRedis(
            host=REDIS_HOST,
            port=REDIS_PORT,
            db=REDIS_DB,
            decode_responses=True,
        )
        
        # Test connection
        r.ping()
        print("✅ Redis connection successful")
        
        # Test basic operations
        test_key = "test_replyfast_auto"
        test_value = '{"test": true}'
        
        r.set(test_key, test_value)
        retrieved = r.get(test_key)
        r.delete(test_key)
        
        if retrieved == test_value:
            print("✅ Redis read/write operations working")
            return True
        else:
            print("❌ Redis read/write operations failed")
            return False
            
    except Exception as e:
        print(f"❌ Redis connection failed: {str(e)}")
        print("💡 Make sure Redis server is running:")
        print("   Windows: docker run -d -p 6379:6379 redis:latest")
        print("   Linux/Mac: redis-server")
        return False

def main():
    """Run all tests"""
    print("🚀 ReplyFast Auto Integration Tests\n" + "="*50)
    
    tests_passed = 0
    total_tests = 3
    
    # Test 1: Meta API
    if test_meta_api_connection():
        tests_passed += 1
    
    # Test 2: Webhook parsing
    if test_webhook_format():
        tests_passed += 1
    
    # Test 3: Redis
    if test_redis_connection():
        tests_passed += 1
    
    print(f"\n📊 Test Results: {tests_passed}/{total_tests} passed")
    
    if tests_passed == total_tests:
        print("✅ All tests passed! Your integration is ready.")
        print("\n🚀 Next steps:")
        print("1. Update your .env file with real Meta API credentials")
        print("2. Set up webhook URL in Meta Developer Console")
        print("3. Start the application: python app.py")
        print("4. Test with a real WhatsApp message")
    else:
        print("❌ Some tests failed. Please check the configuration.")
        print("\n💡 Common fixes:")
        print("1. Ensure Redis is running")
        print("2. Check Meta API credentials in .env")
        print("3. Verify internet connection")

if __name__ == "__main__":
    main()