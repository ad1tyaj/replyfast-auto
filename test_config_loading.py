import json
import re

# Mock the CLIENT_CONFIG loading from app.py
try:
    with open('clients.json', 'r', encoding='utf-8') as f:
        CLIENT_CONFIG = json.load(f)
        print("✅ Loaded clients.json successfully")
except Exception as e:
    print(f"❌ Failed to load clients.json: {str(e)}")
    CLIENT_CONFIG = {}

def get_client_config(wa_id):
    clean_id = re.sub(r'\D', '', str(wa_id))
    if "clients" in CLIENT_CONFIG and clean_id in CLIENT_CONFIG["clients"]:
        return CLIENT_CONFIG["clients"][clean_id]
    return CLIENT_CONFIG.get("default", {})

# Test Cases
test_numbers = [
    ("919876543210", "Aditya Motors Mumbai", "₹"),  # India Client
    ("14155552671", "Bay Area Auto", "$"),        # US Client
    ("919999999999", "ReplyFast Auto (Default)", "₹") # Unknown/Default
]

print("\n🧪 Testing Config Loading:\n")

for wa_id, expected_name, expected_currency in test_numbers:
    config = get_client_config(wa_id)
    name = config.get("dealer_name")
    currency = config.get("currency")
    
    print(f"Checking ID: {wa_id}")
    print(f"  Expected: {expected_name} ({expected_currency})")
    print(f"  Got:      {name} ({currency})")
    
    if name == expected_name and currency == expected_currency:
        print("  ✅ PASS")
    else:
        print("  ❌ FAIL")
    print("-" * 30)
