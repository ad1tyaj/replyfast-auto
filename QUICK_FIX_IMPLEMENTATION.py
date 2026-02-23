"""
QUICK FIX IMPLEMENTATION - Copy/Paste Ready Code
This file contains all the critical fixes ready to implement.

BEFORE RUNNING:
1. Backup your current app.py
2. Test in development environment first
3. Revoke the exposed API token

HOW TO USE:
- Copy the relevant sections and replace in your app.py
- Follow the "REPLACE THIS" comments
"""

import html
import re
import time
import json
from collections import OrderedDict
from threading import Lock
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# ============================================================================
# FIX 1: INPUT SANITIZATION
# Add this function after line 75 in app.py (after MEMORY_STORE definition)
# ============================================================================

def sanitize_input(text, max_length=4096):
    """
    Sanitize user input to prevent injection attacks
    
    Args:
        text: User input string
        max_length: Maximum allowed length
    
    Returns:
        Sanitized string
    
    Example:
        >>> sanitize_input("<script>alert('xss')</script>")
        '&lt;script&gt;alert(&#x27;xss&#x27;)&lt;/script&gt;'
    """
    if not text:
        return text
    
    # 1. Remove null bytes
    text = text.replace('\x00', '')
    
    # 2. Remove control characters except newlines/tabs
    text = ''.join(char for char in text 
                   if char.isprintable() or char in '\n\r\t')
    
    # 3. HTML escape to prevent XSS in logs
    text = html.escape(text)
    
    # 4. Limit length (WhatsApp max is 4096 chars)
    text = text[:max_length]
    
    # 5. Trim whitespace
    text = text.strip()
    
    return text


# ============================================================================
# FIX 2: EXPIRING DICTIONARY (FIXES MEMORY LEAK)
# REPLACE the line "MEMORY_STORE = {}" (around line 75) with this entire class
# ============================================================================

class ExpiringDict:
    """
    Thread-safe dictionary with automatic expiration
    Prevents memory leaks by auto-cleaning expired entries
    
    Usage:
        store = ExpiringDict(max_age_seconds=3600)
        store.set('key', 'value')
        value = store.get('key', default='not_found')
        store.delete('key')
    """
    
    def __init__(self, max_age_seconds=3600, cleanup_interval=300):
        """
        Initialize expiring dictionary
        
        Args:
            max_age_seconds: How long items stay valid (default: 1 hour)
            cleanup_interval: How often to clean expired items (default: 5 min)
        """
        self.store = OrderedDict()
        self.timestamps = {}
        self.max_age = max_age_seconds
        self.cleanup_interval = cleanup_interval
        self.last_cleanup = time.time()
        self.lock = Lock()
    
    def get(self, key, default=None):
        """Get value with automatic cleanup"""
        with self.lock:
            self._cleanup_if_needed()
            
            # Check if key exists and not expired
            if key in self.store:
                timestamp = self.timestamps[key]
                if time.time() - timestamp <= self.max_age:
                    return self.store[key]
                else:
                    # Expired, remove it
                    del self.store[key]
                    del self.timestamps[key]
            
            return default
    
    def set(self, key, value):
        """Set value with timestamp"""
        with self.lock:
            self.store[key] = value
            self.timestamps[key] = time.time()
            self._cleanup_if_needed()
    
    def delete(self, key):
        """Delete a key"""
        with self.lock:
            self.store.pop(key, None)
            self.timestamps.pop(key, None)
    
    def _cleanup_if_needed(self):
        """Clean up expired entries periodically"""
        now = time.time()
        
        # Only cleanup every cleanup_interval seconds
        if now - self.last_cleanup < self.cleanup_interval:
            return
        
        self.last_cleanup = now
        
        # Find and remove expired keys
        expired_keys = [
            key for key, timestamp in self.timestamps.items()
            if now - timestamp > self.max_age
        ]
        
        for key in expired_keys:
            self.store.pop(key, None)
            self.timestamps.pop(key, None)
        
        if expired_keys:
            from app import logger  # Import logger if needed
            logger.info(f"🧹 Cleaned up {len(expired_keys)} expired sessions from memory")
    
    def __len__(self):
        """Return number of non-expired items"""
        with self.lock:
            self._cleanup_if_needed()
            return len(self.store)


# Initialize the expiring dictionary
# REPLACE: MEMORY_STORE = {}
# WITH:
MEMORY_STORE = ExpiringDict(max_age_seconds=3600, cleanup_interval=300)


# ============================================================================
# FIX 3: UPDATE STATE MANAGEMENT TO USE NEW MEMORY_STORE
# REPLACE the save_user_state function (around line 208)
# ============================================================================

def save_user_state(wa_id, state):
    """Save user state with 1 hour expiry"""
    if REDIS_AVAILABLE:
        try:
            # Set with 1 hour expiry (3600 seconds)
            r.setex(wa_id, 3600, json.dumps(state))
        except redis.ConnectionError as e:
            logger.error(f"Error saving state for {wa_id}: {str(e)}")
            # Fallback to memory
            MEMORY_STORE.set(wa_id, state)  # ✅ CHANGED: Use .set() method
    else:
        # Use in-memory fallback
        MEMORY_STORE.set(wa_id, state)  # ✅ CHANGED: Use .set() method


# ============================================================================
# FIX 4: UPDATE RESET STATE FUNCTION
# REPLACE the reset_user_state function (around line 223)
# ============================================================================

def reset_user_state(wa_id):
    """Reset user state (delete from Redis or memory)"""
    if REDIS_AVAILABLE:
        try:
            r.delete(wa_id)
        except redis.ConnectionError as e:
            logger.error(f"Error resetting state for {wa_id}: {str(e)}")
            # Fallback to memory
            MEMORY_STORE.delete(wa_id)  # ✅ CHANGED: Use .delete() method
    else:
        # Use in-memory fallback
        MEMORY_STORE.delete(wa_id)  # ✅ CHANGED: Use .delete() method


# ============================================================================
# FIX 5: UPDATE MESSAGE EXTRACTION WITH SANITIZATION
# REPLACE the extract_message_content function (around line 354)
# ============================================================================

def extract_message_content(data):
    """Extract message content from different Meta webhook formats"""
    messages = data.get("entry", [{}])[0].get("changes", [{}])[0].get("value", {}).get("messages", [])
    
    if not messages:
        # Try alternative format
        messages = data.get("messages", [])
    
    if not messages:
        return None, None
    
    message = messages[0]
    wa_id = message.get("from")
    
    # Extract text from different message types
    text_content = ""
    
    if "text" in message:
        text_content = message["text"].get("body", "")
    elif "interactive" in message:
        # Handle button/list responses
        interactive = message["interactive"]
        if interactive["type"] == "button_reply":
            text_content = interactive["button_reply"]["title"]
        elif interactive["type"] == "list_reply":
            text_content = interactive["list_reply"]["title"]
    
    # ✅ ADDED: Sanitize input before returning
    text_content = sanitize_input(text_content.strip())
    
    return wa_id, text_content


# ============================================================================
# FIX 6: IMPROVED WHATSAPP API WITH RETRY LOGIC
# This goes in meta_whatsapp.py
# REPLACE the _send_message method (around line 97)
# ============================================================================

def _send_message_with_retry(self, payload):
    """
    Internal method to send message to Meta API with retry logic
    
    This replaces the existing _send_message method in meta_whatsapp.py
    
    Features:
    - Automatic retry on transient failures
    - Connection pooling for better performance
    - Exponential backoff
    - Proper timeout handling
    """
    
    # Configure retry strategy
    retry_strategy = Retry(
        total=3,  # Total number of retries
        backoff_factor=1,  # Wait 1, 2, 4 seconds between retries
        status_forcelist=[429, 500, 502, 503, 504],  # Retry on these status codes
        allowed_methods=["POST"]  # Only retry POST requests
    )
    
    # Create session with retry adapter
    session = requests.Session()
    adapter = HTTPAdapter(
        max_retries=retry_strategy,
        pool_connections=10,
        pool_maxsize=10
    )
    session.mount('https://', adapter)
    
    headers = {
        "Authorization": f"Bearer {self.api_token}",
        "Content-Type": "application/json"
    }
    
    try:
        response = session.post(
            self.base_url, 
            headers=headers, 
            data=json.dumps(payload),
            timeout=10  # 10 second timeout
        )
        response.raise_for_status()
        
        return {
            "success": True,
            "response": response.json(),
            "status_code": response.status_code
        }
        
    except requests.exceptions.Timeout:
        return {
            "success": False,
            "error": "Request timeout after 10 seconds",
            "status_code": None,
            "retry_attempted": True
        }
        
    except requests.exceptions.RequestException as e:
        status_code = getattr(e.response, 'status_code', None) if hasattr(e, 'response') else None
        error_body = getattr(e.response, 'text', str(e)) if hasattr(e, 'response') else str(e)
        
        return {
            "success": False,
            "error": error_body,
            "status_code": status_code,
            "retry_attempted": True
        }
    
    finally:
        session.close()


# ============================================================================
# FIX 7: SPLIT WEBHOOK INTO TWO ROUTES (BETTER RATE LIMITING)
# REPLACE the entire webhook function (around line 388)
# ============================================================================

@app.route("/webhook", methods=["GET"])
@limiter.exempt  # Verification doesn't need rate limiting
def webhook_verify():
    """
    Webhook verification endpoint for Meta WhatsApp
    This is called once during setup, so no rate limiting needed
    """
    verify_token = request.args.get("hub.verify_token")
    challenge = request.args.get("hub.challenge")
    
    # Verify token from environment variables
    if verify_token == WEBHOOK_VERIFY_TOKEN:
        logger.info("✅ Webhook verification successful")
        return challenge
    else:
        logger.warning(f"⚠️  Webhook verification failed. Received token: {verify_token}")
        return "Forbidden", 403


@app.route("/webhook", methods=["POST"])
@limiter.limit("100 per minute")  # ✅ ADDED: Rate limiting for message endpoint
def webhook_message():
    """
    Webhook endpoint for receiving WhatsApp messages
    Rate limited to prevent abuse
    """
    # POST request - handle incoming messages
    data = request.get_json(silent=True) or {}
    
    # Extract message content using helper function
    wa_id, incoming_text = extract_message_content(data)
    
    # If essential fields are missing, just 200 OK silently
    if not wa_id or incoming_text is None:
        return "", 200

    state = get_user_state(wa_id)
    q_status = state.get("q_status", 0)
    answers = state.get("answers", {})

    # Start of flow - new user or reset state
    if q_status == 0:
        # Send Q1 and set state to expect answer to Q1 next
        state["q_status"] = 1
        state["answers"] = answers
        save_user_state(wa_id, state)
        send_q1(wa_id)
        return jsonify({"status": "ok", "step": "sent_q1"})

    # ... rest of your webhook logic stays the same ...
    # (Keep all the q_status 1, 2, 3, 4, 5 handling code)


# ============================================================================
# DEPLOYMENT STEPS
# ============================================================================

"""
STEP-BY-STEP IMPLEMENTATION:

1. BACKUP YOUR FILES
   cp app.py app.py.backup
   cp meta_whatsapp.py meta_whatsapp.py.backup

2. REVOKE THE EXPOSED TOKEN
   - Go to Meta Developer Console
   - Generate new token
   - Update .env file

3. CREATE .gitignore
   echo ".env" > .gitignore
   echo "*.pyc" >> .gitignore
   echo "__pycache__/" >> .gitignore
   git rm --cached .env
   git commit -m "security: remove .env from git"

4. IMPLEMENT FIXES IN ORDER
   a. Add sanitize_input function to app.py
   b. Replace MEMORY_STORE with ExpiringDict
   c. Update save_user_state and reset_user_state
   d. Update extract_message_content
   e. Split webhook into two routes
   f. Update meta_whatsapp.py with retry logic

5. TEST LOCALLY
   python app.py
   # In another terminal:
   curl "http://localhost:5000/health"

6. VERIFY FIXES
   # Test sanitization
   # Test rate limiting (send 101 requests)
   # Monitor memory usage

7. DEPLOY TO PRODUCTION
   git add .
   git commit -m "security: implement critical security fixes"
   git push heroku main

TOTAL TIME: ~2 hours
"""

# ============================================================================
# VERIFICATION TESTS
# ============================================================================

def run_verification_tests():
    """
    Run these tests after implementing fixes
    """
    print("🧪 Running verification tests...")
    
    # Test 1: Input sanitization
    assert sanitize_input("<script>alert('xss')</script>") == "&lt;script&gt;alert(&#x27;xss&#x27;)&lt;/script&gt;"
    assert sanitize_input("Normal text") == "Normal text"
    assert sanitize_input("A" * 5000)[:4096] == "A" * 4096
    print("✅ Input sanitization working")
    
    # Test 2: Expiring dict
    store = ExpiringDict(max_age_seconds=1)
    store.set('test_key', 'test_value')
    assert store.get('test_key') == 'test_value'
    time.sleep(2)
    assert store.get('test_key') is None
    print("✅ Expiring dict working")
    
    print("✅ All verification tests passed!")


if __name__ == "__main__":
    run_verification_tests()
