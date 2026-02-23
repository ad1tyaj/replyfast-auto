# 🚨 CRITICAL FIXES - DO IMMEDIATELY

**Priority:** URGENT  
**Estimated Time:** 2-4 hours  
**Impact:** Security & Stability

---

## 🔴 1. REVOKE EXPOSED API TOKEN (15 minutes)

**Why:** Your Meta WhatsApp API token is exposed in the `.env` file. Anyone with access can:
- Send messages using your account
- Exhaust your API quota
- Get your account suspended

**Steps:**
1. Go to: https://developers.facebook.com/apps/
2. Select your app
3. Navigate to: WhatsApp > API Setup
4. Click "Generate New Token" or "Revoke Token"
5. Copy the new token
6. Update your `.env` file (DON'T COMMIT IT)

**Verification:**
```bash
# Test old token should fail
curl -X POST "https://graph.facebook.com/v18.0/853033551227517/messages" \
  -H "Authorization: Bearer <OLD_TOKEN>" \
  -H "Content-Type: application/json"
# Should return 401 Unauthorized
```

---

## 🔴 2. ADD .gitignore FILE (5 minutes)

**Create `.gitignore` in project root:**
```gitignore
# Environment variables
.env
.env.local
.env.production

# Python
*.pyc
__pycache__/
*.py[cod]
*$py.class
*.so
.Python
venv/
env/
ENV/

# IDE
.vscode/
.idea/
*.swp
*.swo
*~

# OS
.DS_Store
Thumbs.db

# Logs
*.log
tunnel.log

# Testing
.pytest_cache/
.coverage
htmlcov/

# Temporary files
tmp_*
*.tmp
```

**Then remove .env from Git history:**
```bash
git rm --cached .env
git commit -m "security: remove .env from version control"
git push
```

---

## 🔴 3. ENABLE WEBHOOK RATE LIMITING (10 minutes)

**File:** `app.py` line 389

**Current (VULNERABLE):**
```python
@app.route("/webhook", methods=["GET", "POST"])
@limiter.exempt  # ❌ THIS IS BAD
def webhook():
```

**Fixed:**
```python
@app.route("/webhook", methods=["GET", "POST"])
@limiter.limit("100 per minute")  # ✅ Add rate limiting
def webhook():
    # Exempt only GET (verification)
    if request.method == "GET":
        return verify_webhook()
    
    # POST requests are rate limited
    return handle_message()
```

**Better approach with separate handling:**
```python
@app.route("/webhook", methods=["GET"])
@limiter.exempt  # OK for verification
def webhook_verify():
    verify_token = request.args.get("hub.verify_token")
    challenge = request.args.get("hub.challenge")
    
    if verify_token == WEBHOOK_VERIFY_TOKEN:
        logger.info("✅ Webhook verification successful")
        return challenge
    else:
        logger.warning(f"⚠️ Verification failed")
        return "Forbidden", 403

@app.route("/webhook", methods=["POST"])
@limiter.limit("100 per minute")  # Rate limited
def webhook_message():
    # Handle incoming messages
    data = request.get_json(silent=True) or {}
    # ... rest of your code
```

---

## 🔴 4. ADD INPUT SANITIZATION (30 minutes)

**File:** `app.py` - Add new function before line 354

```python
import html
import re

def sanitize_input(text, max_length=4096):
    """
    Sanitize user input to prevent injection attacks
    
    Args:
        text: User input string
        max_length: Maximum allowed length
    
    Returns:
        Sanitized string
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
    
    # 4. Limit length
    text = text[:max_length]
    
    # 5. Trim whitespace
    text = text.strip()
    
    return text
```

**Update `extract_message_content` function (around line 354):**
```python
def extract_message_content(data):
    """Extract message content from different Meta webhook formats"""
    messages = data.get("entry", [{}])[0].get("changes", [{}])[0].get("value", {}).get("messages", [])
    
    if not messages:
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
        interactive = message["interactive"]
        if interactive["type"] == "button_reply":
            text_content = interactive["button_reply"]["title"]
        elif interactive["type"] == "list_reply":
            text_content = interactive["list_reply"]["title"]
    
    # ✅ ADD SANITIZATION HERE
    text_content = sanitize_input(text_content.strip())
    
    return wa_id, text_content
```

---

## 🔴 5. FIX MEMORY LEAK IN MEMORY_STORE (45 minutes)

**File:** `app.py` - Replace MEMORY_STORE (line 75)

**Current (LEAKS MEMORY):**
```python
MEMORY_STORE = {}
```

**Fixed with automatic cleanup:**
```python
import time
from collections import OrderedDict
from threading import Lock

class ExpiringDict:
    """Thread-safe dictionary with automatic expiration"""
    
    def __init__(self, max_age_seconds=3600, cleanup_interval=300):
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
            logger.info(f"🧹 Cleaned up {len(expired_keys)} expired sessions from memory")

# Replace MEMORY_STORE
MEMORY_STORE = ExpiringDict(max_age_seconds=3600, cleanup_interval=300)
```

**Update usage in `save_user_state` (line 219):**
```python
def save_user_state(wa_id, state):
    """Save user state with 1 hour expiry"""
    if REDIS_AVAILABLE:
        try:
            r.setex(wa_id, 3600, json.dumps(state))
        except redis.ConnectionError as e:
            logger.error(f"Error saving state for {wa_id}: {str(e)}")
            MEMORY_STORE.set(wa_id, state)  # ✅ Use .set() method
    else:
        MEMORY_STORE.set(wa_id, state)  # ✅ Use .set() method
```

**Update usage in `reset_user_state` (line 233):**
```python
def reset_user_state(wa_id):
    """Reset user state (delete from Redis or memory)"""
    if REDIS_AVAILABLE:
        try:
            r.delete(wa_id)
        except redis.ConnectionError as e:
            logger.error(f"Error resetting state for {wa_id}: {str(e)}")
            MEMORY_STORE.delete(wa_id)  # ✅ Use .delete() method
    else:
        MEMORY_STORE.delete(wa_id)  # ✅ Use .delete() method
```

---

## 🔴 6. ADD RETRY LOGIC FOR API CALLS (30 minutes)

**File:** `meta_whatsapp.py`

**Add at top:**
```python
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
```

**Replace `_send_message` method (line 97):**
```python
def _send_message(self, payload):
    """Internal method to send message to Meta API with retry logic"""
    
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
            "status_code": None
        }
        
    except requests.exceptions.RequestException as e:
        status_code = getattr(e.response, 'status_code', None) if hasattr(e, 'response') else None
        error_body = getattr(e.response, 'text', str(e)) if hasattr(e, 'response') else str(e)
        
        return {
            "success": False,
            "error": error_body,
            "status_code": status_code
        }
    
    finally:
        session.close()
```

---

## ✅ Verification Checklist

After completing all fixes, verify:

```bash
# 1. Check .gitignore is working
git status
# Should NOT show .env file

# 2. Test the app starts without errors
python app.py
# Should see: ✅ Redis connection established successfully

# 3. Test webhook verification
curl "http://localhost:5000/webhook?hub.verify_token=your_secure_verify_token_here&hub.challenge=test123"
# Should return: test123

# 4. Test rate limiting
for i in {1..101}; do curl -X POST http://localhost:5000/webhook -H "Content-Type: application/json" -d '{}'; done
# Request 101 should return 429 Too Many Requests

# 5. Test input sanitization
# Send a message with special characters through WhatsApp
# Check logs - should be HTML escaped

# 6. Monitor memory usage
ps aux | grep python
# Memory should stay stable over time
```

---

## 🎯 Time Estimate

| Task | Time | Priority |
|------|------|----------|
| Revoke API token | 15 min | 🔴 Critical |
| Add .gitignore | 5 min | 🔴 Critical |
| Enable rate limiting | 10 min | 🔴 Critical |
| Add input sanitization | 30 min | 🔴 Critical |
| Fix memory leak | 45 min | 🔴 Critical |
| Add retry logic | 30 min | 🔴 Critical |
| **TOTAL** | **2h 15min** | |

---

## 🚀 After These Fixes

Once these critical fixes are done:

1. ✅ Your app will be much more secure
2. ✅ Memory leaks prevented
3. ✅ Better resilience to API failures
4. ✅ Protected against DoS attacks
5. ✅ No more credential leaks in Git

**Next step:** Review the full `CODE_REVIEW_REPORT.md` for additional improvements.

---

**Need help with any of these fixes?** Let me know which one you want to tackle first!
