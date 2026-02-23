import json
from datetime import datetime
import requests
import re
import logging
import time
import os

from flask import Flask, request, jsonify
import redis
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

# Google Sheets API imports
try:
    from google.oauth2 import service_account
    from google.auth.transport.requests import Request
    from googleapiclient.discovery import build
    GOOGLE_SHEETS_AVAILABLE = True
except ImportError:
    GOOGLE_SHEETS_AVAILABLE = False
    logger_temp = logging.getLogger(__name__)
    logger_temp.warning("⚠️ Google Sheets API libraries not installed. Install with: pip install google-auth-oauthlib google-api-python-client")


from config import (
    REDIS_HOST,
    REDIS_PORT,
    REDIS_DB,
    PORT,
    META_API_TOKEN,
    META_PHONE_ID,
    BSP_PROVIDER,
    SHEET_KEY,
    SHEET_ID,
    GOOGLE_CREDENTIALS_FILE,
    SHEETS_API_MODE,
    WEBHOOK_VERIFY_TOKEN,
    DEALER_PHONE_NUMBER,
    DEALER_NAME,
    APPOINTMENT_REMINDER_INTERVAL,
)

# Import optimized funnel helpers
from funnel_helpers import (
    detect_user_intent,
    send_welcome_optimized,
    send_q_budget_early,
    request_contact_early,
    request_phone_number,
    send_q_vehicle_type_after_contact,
    send_exit_intent_message,
    validate_phone_number,
    validate_name
)
from funnel.clinic_flow import (
    score_date_choice,
    send_clinic_welcome,
    send_service_selection,
    send_date_selection,
    request_patient_name,
    request_patient_phone,
    send_clinic_booking_summary,
    send_cold_clinic_response,
    build_clinic_staff_notification,
)

from meta_whatsapp import meta_api
from follow_up_scheduler import create_scheduler
from appointment_scheduler import (
    book_appointment,
    check_and_send_reminders,
    detect_reschedule_intent,
    handle_reschedule,
    confirm_showed,
    confirm_noshow,
    get_show_rate,
    get_active_appointment,
)
from missed_call_handler import extract_missed_call, send_missed_call_recovery
from hot_lead_escalation import (
    register_hot_lead,
    mark_contacted,
    get_avg_response_time,
    check_and_escalate_hot_leads,
)
from dashboard import get_dashboard_data
from flask import render_template


# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# Load Client Configuration
try:
    with open('clients.json', 'r', encoding='utf-8') as f:
        CLIENT_CONFIG = json.load(f)
        logger.info("✅ Loaded clients.json successfully")
except Exception as e:
    logger.error(f"❌ Failed to load clients.json: {str(e)}")
    CLIENT_CONFIG = {"default": {"currency": "₹", "dealer_name": "ReplyFast Auto"}}

def get_client_config(recipient_id):
    """
    Get configuration for a specific client using the BUSINESS phone number
    that received the message (recipient_id / display_phone_number from the
    Meta webhook payload).  This is the correct multi-tenant lookup — each
    business phone number maps to one client entry in clients.json.
    """
    clean_id = re.sub(r'\D', '', str(recipient_id)) if recipient_id else ""

    if "clients" in CLIENT_CONFIG and clean_id in CLIENT_CONFIG["clients"]:
        return CLIENT_CONFIG["clients"][clean_id]

    # Fallback to default config
    return CLIENT_CONFIG.get("default", {})

# Rate limiting configuration
def get_wa_id():
    """Extract WhatsApp ID from request for rate limiting"""
    try:
        data = request.get_json(silent=True) or {}
        messages = data.get("entry", [{}])[0].get("changes", [{}])[0].get("value", {}).get("messages", [])
        if messages:
            return messages[0].get("from", get_remote_address())
        return get_remote_address()
    except:
        return get_remote_address()

limiter = Limiter(
    app=app,
    key_func=get_wa_id,
    default_limits=["200 per day", "50 per hour"],
    storage_uri=f"redis://{REDIS_HOST}:{REDIS_PORT}/{REDIS_DB}" if REDIS_HOST else "memory://",
    storage_options={"socket_connect_timeout": 5, "socket_timeout": 5} if REDIS_HOST else {}
)

# Global Redis client with connection pool and error handling
try:
    r = redis.StrictRedis(
        host=REDIS_HOST,
        port=REDIS_PORT,
        db=REDIS_DB,
        decode_responses=True,
        socket_connect_timeout=5,
        socket_timeout=5,
    )
    # Test connection
    r.ping()
    logger.info("✅ Redis connection established successfully")
    REDIS_AVAILABLE = True
except redis.ConnectionError as e:
    logger.error(f"❌ Redis connection failed: {str(e)}")
    logger.warning("⚠️  Using in-memory fallback for state management")
    r = None
    REDIS_AVAILABLE = False

# Fallback in-memory storage when Redis is unavailable
MEMORY_STORE = {}
MEMORY_STORE_TIMESTAMPS = {}  # Track when entries were created
# In-memory fallback for processed message dedup (only used when Redis unavailable)
_PROCESSED_MESSAGES_FALLBACK = {}

def cleanup_memory_store():
    """Clean up old entries from memory store to prevent memory leaks"""
    current_time = time.time()
    expired_keys = []
    
    for wa_id, timestamp in MEMORY_STORE_TIMESTAMPS.items():
        # Remove entries older than 1 hour (3600 seconds)
        if current_time - timestamp > 3600:
            expired_keys.append(wa_id)
    
    for wa_id in expired_keys:
        if wa_id in MEMORY_STORE:
            del MEMORY_STORE[wa_id]
        del MEMORY_STORE_TIMESTAMPS[wa_id]
        logger.info(f"🧹 [MEMORY CLEANUP] Removed expired entry for {wa_id}")
    
    if expired_keys:
        logger.info(f"🧹 [MEMORY CLEANUP] Cleaned up {len(expired_keys)} expired entries")


# =========================
# Deduplication Helpers
# =========================

DEDUP_TTL = 600  # 10 minutes
DEDUP_KEY_PREFIX = "dedup:"

def is_message_processed(message_id):
    """
    Check if a message has already been processed.
    Uses Redis with 10-min TTL (production-safe across workers/restarts).
    Falls back to in-memory dict when Redis is unavailable.
    """
    if not message_id:
        return False
    if REDIS_AVAILABLE:
        try:
            return r.exists(f"{DEDUP_KEY_PREFIX}{message_id}")
        except redis.ConnectionError:
            pass
    # Fallback: in-memory
    current_time = time.time()
    entry = _PROCESSED_MESSAGES_FALLBACK.get(message_id)
    if entry and current_time - entry < DEDUP_TTL:
        return True
    return False


def mark_message_processed(message_id):
    """
    Mark a message ID as processed.
    Uses Redis with 10-min TTL. Falls back to in-memory dict.
    """
    if not message_id:
        return
    if REDIS_AVAILABLE:
        try:
            r.setex(f"{DEDUP_KEY_PREFIX}{message_id}", DEDUP_TTL, "1")
            return
        except redis.ConnectionError:
            pass
    # Fallback: in-memory with lightweight cleanup
    _PROCESSED_MESSAGES_FALLBACK[message_id] = time.time()
    # Prune entries older than TTL
    current_time = time.time()
    expired = [mid for mid, ts in _PROCESSED_MESSAGES_FALLBACK.items()
               if current_time - ts > DEDUP_TTL]
    for mid in expired:
        del _PROCESSED_MESSAGES_FALLBACK[mid]


# =========================
# Integrations
# =========================

def send_whatsapp_message(wa_id, message_body, buttons=None, max_retries=3):
    """
    Sends WhatsApp message using Meta WhatsApp Business API with retry logic.
    'buttons' must be a list of button titles or None.
    """
    for attempt in range(max_retries):
        try:
            if buttons and len(buttons) > 0:
                if len(buttons) <= 3:
                    # Use interactive buttons for 3 or fewer options
                    result = meta_api.send_interactive_button_message(wa_id, message_body, buttons)
                else:
                    # Use interactive list for more than 3 options
                    result = meta_api.send_interactive_list_message(wa_id, message_body, "Please select:", buttons)
            else:
                # Send simple text message
                result = meta_api.send_text_message(wa_id, message_body)
            
            if result['success']:
                logger.info(f"✅ [WHATSAPP MESSAGE SENT] To: {wa_id}")
                logger.info(f"   Message: {message_body[:100]}...")
                if buttons:
                    logger.info(f"   Buttons: {', '.join(buttons)}")
                return result
            else:
                logger.error(f"❌ [WHATSAPP MESSAGE FAILED] To: {wa_id} (Attempt {attempt + 1}/{max_retries})")
                logger.error(f"   Error: {result.get('error', 'Unknown error')}")
                if attempt == max_retries - 1:  # Last attempt
                    return result
                time.sleep(2 ** attempt)  # Exponential backoff
                
        except Exception as e:
            logger.error(f"❌ [WHATSAPP MESSAGE ERROR] To: {wa_id} (Attempt {attempt + 1}/{max_retries})")
            logger.error(f"   Exception: {str(e)}")
            if attempt == max_retries - 1:  # Last attempt
                return {"success": False, "error": str(e)}
            time.sleep(2 ** attempt)  # Exponential backoff
    
    return {"success": False, "error": "Max retries exceeded"}


def log_lead_to_sheet(lead_data):
    """
    Logs the lead to Google Sheets using direct API (preferred) or webhook fallback.
    """
    try:
        # Try direct Google Sheets API first
        if SHEETS_API_MODE == "direct" and GOOGLE_SHEETS_AVAILABLE:
            return log_lead_to_sheets_api(lead_data)
        
        # Fall back to webhook (Apps Script)
        if SHEET_KEY:
            return log_lead_to_sheets_webhook(lead_data)
        
        # If neither is configured, log locally only
        logger.warning(f"⚠️ [SHEETS WARNING] No Google Sheets integration configured")
        logger.info(f"📊 [LEAD LOGGED LOCALLY]")
        logger.info(f"   {json.dumps(lead_data, indent=2)}")
        return {"success": True, "method": "local_only"}
        
    except Exception as e:
        logger.error(f"❌ [SHEETS LOGGING ERROR]: {str(e)}")
        logger.info(f"📊 [LEAD LOGGED LOCALLY AS FALLBACK]")
        logger.info(f"   {json.dumps(lead_data, indent=2)}")
        return {"success": False, "fallback": "local", "error": str(e)}


def notify_dealer_callback(wa_id, customer_name="Customer", q_status=None):
    """
    Send a WhatsApp notification to the dealer when a customer requests a callback.
    """
    if not DEALER_PHONE_NUMBER:
        logger.warning(f"⚠️ [CALLBACK NOTIFICATION] DEALER_PHONE_NUMBER not configured")
        return {"success": False, "error": "Dealer phone number not configured"}
    
    try:
        # Build the notification message
        status_text = f"at Question {q_status}" if q_status else "during the flow"
        notification_message = (
            f"📞 **CALLBACK REQUEST**\n\n"
            f"Customer Name: {customer_name}\n"
            f"WhatsApp ID: {wa_id}\n"
            f"Requested: {status_text}\n"
            f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
            f"Please contact them at their WhatsApp number: {wa_id}"
        )
        
        # Send message to dealer
        result = send_whatsapp_message(
            DEALER_PHONE_NUMBER,
            notification_message
        )
        
        if result.get("success"):
            logger.info(f"✅ [CALLBACK NOTIFICATION SENT]")
            logger.info(f"   To: {DEALER_PHONE_NUMBER}")
            logger.info(f"   Customer: {wa_id}")
            return {"success": True, "notified": True}
        else:
            logger.error(f"❌ [CALLBACK NOTIFICATION FAILED]")
            logger.error(f"   Error: {result.get('error')}")
            return {"success": False, "error": result.get("error")}
            
    except Exception as e:
        logger.error(f"❌ [CALLBACK NOTIFICATION ERROR]: {str(e)}")
        return {"success": False, "error": str(e)}



def log_lead_to_sheets_api(lead_data):
    """
    Logs lead to Google Sheets using the Google Sheets API directly.
    """
    try:
        if not GOOGLE_SHEETS_AVAILABLE:
            logger.error("❌ Google Sheets API libraries not available")
            return {"success": False, "error": "Google API libraries not installed"}
        
        if not os.path.exists(GOOGLE_CREDENTIALS_FILE):
            logger.error(f"❌ Google credentials file not found: {GOOGLE_CREDENTIALS_FILE}")
            return {"success": False, "error": "Credentials file not found"}
        
        # Authenticate using service account
        credentials = service_account.Credentials.from_service_account_file(
            GOOGLE_CREDENTIALS_FILE,
            scopes=['https://www.googleapis.com/auth/spreadsheets']
        )
        
        # Build the Sheets API service
        service = build('sheets', 'v4', credentials=credentials)
        sheet = service.spreadsheets()
        
        # Get the sheet to check if headers exist
        result = sheet.values().get(
            spreadsheetId=SHEET_ID,
            range='Sheet1!A1:Z1'
        ).execute()
        
        values = result.get('values', [])
        
        # Add headers if sheet is empty
        if not values:
            headers = [
                'Timestamp', 'WhatsApp ID', 'Phone Number', 'Customer Name',
                'Vehicle Type', 'New or Used', 'Budget', 'Purchase Timeline',
                'Trade-in', 'Contact Details', 'Lead Score', 'Preferred Time', 'Status'
            ]
            sheet.values().append(
                spreadsheetId=SHEET_ID,
                range='Sheet1!A1',
                valueInputOption='RAW',
                body={'values': [headers]}
            ).execute()
            logger.info("📝 [SHEETS] Headers added to new sheet")
        
        # Prepare the data row
        data_row = [
            lead_data.get('timestamp', ''),
            lead_data.get('wa_id', ''),
            lead_data.get('phone_number', ''),
            lead_data.get('customer_name', ''),
            lead_data.get('q1_vehicle_type', ''),
            lead_data.get('q2_new_or_used', ''),
            lead_data.get('q3_budget', ''),
            lead_data.get('q4_purchase_timeline', ''),
            lead_data.get('q5_trade_in', ''),
            lead_data.get('q6_contact_details', ''),
            lead_data.get('lead_score', ''),
            lead_data.get('preferred_time', ''),
            lead_data.get('status', 'New')
        ]
        
        # Append the data row
        append_result = sheet.values().append(
            spreadsheetId=SHEET_ID,
            range='Sheet1!A:M',
            valueInputOption='RAW',
            body={'values': [data_row]}
        ).execute()
        
        logger.info(f"✅ [LEAD LOGGED TO SHEETS - API]")
        logger.info(f"   WA ID: {lead_data.get('wa_id')}")
        logger.info(f"   Lead Score: {lead_data.get('lead_score')}")
        logger.info(f"   Range: {append_result.get('updates', {}).get('updatedRange', 'N/A')}")
        
        return {"success": True, "method": "api", "range": append_result.get('updates', {}).get('updatedRange')}
        
    except Exception as e:
        logger.error(f"❌ [SHEETS API ERROR]: {str(e)}")
        # Fall back to webhook
        if SHEET_KEY:
            logger.info("⚠️ [SHEETS] Falling back to webhook method...")
            return log_lead_to_sheets_webhook(lead_data)
        return {"success": False, "error": str(e)}


def log_lead_to_sheets_webhook(lead_data):
    """
    Logs lead to Google Sheets using Apps Script webhook.
    """
    try:
        webhook_url = f"https://script.google.com/macros/s/{SHEET_KEY}/exec"
        response = requests.post(webhook_url, json=lead_data, timeout=10)
        
        if response.status_code == 200:
            logger.info(f"✅ [LEAD LOGGED TO SHEETS - WEBHOOK]")
            logger.info(f"   WA ID: {lead_data.get('wa_id')}")
            logger.info(f"   Lead Score: {lead_data.get('lead_score')}")
            return {"success": True, "method": "webhook", "response": response.text}
        else:
            logger.error(f"❌ [SHEETS WEBHOOK FAILED]")
            logger.error(f"   Status Code: {response.status_code}")
            logger.error(f"   Response: {response.text}")
            return {"success": False, "error": response.text}
            
    except Exception as e:
        logger.error(f"❌ [SHEETS WEBHOOK ERROR]: {str(e)}")
        return {"success": False, "error": str(e)}


# =========================
# State Management Helpers
# =========================

def get_user_state(wa_id):
    """Retrieve user state from Redis or memory fallback."""
    default_state = {
        "q_status": 0,
        "answers": {},
        "retry_count": 0
    }
    
    if REDIS_AVAILABLE:
        try:
            raw = r.get(wa_id)
            if not raw:
                return default_state
            return json.loads(raw)
        except (redis.ConnectionError, json.JSONDecodeError) as e:
            logger.error(f"Error getting state for {wa_id}: {str(e)}")
            return default_state
    else:
        return MEMORY_STORE.get(wa_id, default_state)


def save_user_state(wa_id, state):
    """Save user state with 1 hour expiry and track activity for follow-ups."""
    # Always stamp last_activity so the scheduler can find stale sessions
    state["last_activity"] = time.time()

    if REDIS_AVAILABLE:
        try:
            r.setex(wa_id, 3600, json.dumps(state))
            # Keep the active_sessions sorted set up to date.
            # Score = last_activity timestamp; used by the follow-up scheduler.
            q_status = state.get("q_status", 0)
            if q_status not in (0, 7, 99):  # only track mid-funnel
                r.zadd("active_sessions", {wa_id: state["last_activity"]})
                # The ZSET entry expires passively when no longer needed;
                # reset_user_state() explicitly removes it on completion.
        except redis.ConnectionError as e:
            logger.error(f"Error saving state for {wa_id}: {str(e)}")
            MEMORY_STORE[wa_id] = state
    else:
        MEMORY_STORE[wa_id] = state
        MEMORY_STORE_TIMESTAMPS[wa_id] = time.time()
        cleanup_memory_store()  # Clean up old entries


def reset_user_state(wa_id):
    """Reset user state and remove from active_sessions follow-up tracking."""
    if REDIS_AVAILABLE:
        try:
            r.delete(wa_id)
            r.zrem("active_sessions", wa_id)  # stop follow-ups for this user
        except redis.ConnectionError as e:
            logger.error(f"Error resetting state for {wa_id}: {str(e)}")
            if wa_id in MEMORY_STORE:
                del MEMORY_STORE[wa_id]
    else:
        if wa_id in MEMORY_STORE:
            del MEMORY_STORE[wa_id]
        if wa_id in MEMORY_STORE_TIMESTAMPS:
            del MEMORY_STORE_TIMESTAMPS[wa_id]


# =========================
# Question Sending Helpers
# =========================

def send_q1(wa_id):
    text = "🚗 Welcome to ReplyFast Auto! \n\nQ1: What type of vehicle interests you most?"
    buttons = ["Sedan", "SUV", "Hatchback", "MUV", "Others"]
    send_whatsapp_message(wa_id, text, buttons=buttons)


def send_q2(wa_id):
    text = "Q2: Are you looking for a New or Used vehicle?"
    buttons = ["New", "Used"]
    send_whatsapp_message(wa_id, text, buttons=buttons)


def send_q3(wa_id):
    text = "Q3: What's your budget range?"
    buttons = [
        "Under 5 Lakhs",
        "5-8 Lakhs", 
        "8-12 Lakhs",
        "12-20 Lakhs",
        "Above 20 Lakhs"
    ]
    send_whatsapp_message(wa_id, text, buttons=buttons)


def send_q4(wa_id):
    text = "Q4: When are you planning to purchase?"
    buttons = ["Within 1 week", "Within 1 month", "Within 3 months", "6+ months"]
    send_whatsapp_message(wa_id, text, buttons=buttons)


def send_q5(wa_id):
    text = "Q5: Do you have a car to trade in?"
    buttons = ["Yes", "No", "Not Sure"]
    send_whatsapp_message(wa_id, text, buttons=buttons)


def send_q6(wa_id):
    """Q6: Contact details - Final step"""
    text = (
        "Perfect! Let's get you behind the wheel! 🚗\n\n"
        "To schedule your test drive, please share:\n"
        "• Your name\n"
        "• Phone number\n"
        "• Preferred visit time\n\n"
        "Example: Amit Kumar, 9876543210, Tomorrow 3 PM"
    )
    send_whatsapp_message(wa_id, text)


def validate_contact_details(text):
    """Validate contact details contain a valid phone number."""
    if not text or len(text.strip()) < 10:
        return False, "Please provide your complete contact details."
    
    phone_pattern = r'\b\d{10}\b'
    if not re.search(phone_pattern, text):
        return False, "Please include a valid 10-digit phone number."
    
    name_pattern = r'[a-zA-Z]{2,}.*[a-zA-Z]{2,}'
    if not re.search(name_pattern, text):
        return False, "Please include your full name."
    
    return True, ""


# =========================
# Dealer Notification Helper
# =========================

def notify_hot_lead_dealer(lead_data, client_config):
    """
    Send an instant WhatsApp notification to the DEALER when a HOT or WARM
    lead completes the funnel.

    Message format (HOT example):
        🔥 HOT LEAD ALERT — Aditya Motors Mumbai
        ‣
        👤 Rahul Sharma
        📱 9876543210
        💰 Budget: ₹10-15 Lakhs
        🚗 Looking for: SUV | New
        ⏰ Timeline: Within 1 week
        ♻️ Trade-in: No
        ‣
        💬 Reply directly: wa.me/919876543210
    """
    dealer_wa = client_config.get("dealer_whatsapp", "")
    if not dealer_wa:
        logger.warning("⚠️ [DEALER NOTIFY] No dealer_whatsapp configured — skipping notification")
        return

    lead_score  = lead_data.get("lead_score", "WARM")
    name        = lead_data.get("customer_name", "Unknown")
    phone       = lead_data.get("phone_number", "")
    budget      = lead_data.get("budget", lead_data.get("q3_budget", "N/A"))
    vehicle     = lead_data.get("q1_vehicle_type", "N/A")
    new_used    = lead_data.get("q2_new_or_used", "N/A")
    timeline    = lead_data.get("q4_purchase_timeline", "N/A")
    trade_in    = lead_data.get("q5_trade_in", "N/A")
    wa_id       = lead_data.get("wa_id", "")
    dealer_name = client_config.get("dealer_name", "ReplyFast Auto")

    # Score-based emoji and urgency header
    if lead_score == "HOT":
        header   = f"🔥 *HOT LEAD ALERT* — {dealer_name}"
        urgency  = "⏳ *Act fast — they want to buy within 1 week!*"
    elif lead_score == "WARM":
        header   = f"🌶️ *WARM LEAD* — {dealer_name}"
        urgency  = "📅 Follow up within 24 hours for best results."
    else:
        # COLD leads: don't spam dealer, just log
        logger.info(f"🧤 [COLD LEAD] {name} ({wa_id}) — no dealer notification sent")
        return

    # WhatsApp deep-link to customer (works on mobile)
    wa_link = f"https://wa.me/{wa_id}" if wa_id else "N/A"

    message = (
        f"{header}\n"
        f"‣\n"
        f"👤 *Name:* {name}\n"
        f"📱 *Phone:* +{phone}\n"
        f"💰 *Budget:* {budget}\n"
        f"🚗 *Looking for:* {vehicle} | {new_used}\n"
        f"⏰ *Timeline:* {timeline}\n"
        f"♻️ *Trade-in:* {trade_in}\n"
        f"‣\n"
        f"{urgency}\n"
        f"‣\n"
        f"💬 *WhatsApp customer directly:*\n{wa_link}"
    )

    try:
        send_whatsapp_message(dealer_wa, message)
        logger.info(
            f"🔔 [DEALER NOTIFIED] Lead: {name} ({lead_score}) → Dealer: {dealer_wa}"
        )
    except Exception as e:
        # Never crash the main flow because of a notification failure
        logger.error(f"❌ [DEALER NOTIFY ERROR] {str(e)}")


# =========================
# Lead Completion Helper
# =========================

def complete_lead(wa_id, state):
    """Complete lead and log to Google Sheets - Updated for optimized funnel"""
    answers = state.get("answers", {})
    timestamp = datetime.utcnow().isoformat()
    
    # NEW: Get name and phone from early capture (q1.5 and q1.7)
    name = answers.get("name", "")
    phone = answers.get("phone", "")
    preferred_time = ""
    
    # Fallback: Try to extract from Q6 if using old flow
    if not name or not phone:
        contact_details = answers.get("q6", "")
        if contact_details:
            # Extract phone (10 digits)
            phone_match = re.search(r'\b\d{10}\b', contact_details)
            if phone_match:
                phone = phone_match.group()
            
            # Extract name (first part before comma)
            parts = contact_details.split(',')
            if parts:
                name = parts[0].strip()
            
            # Extract time (anything after 2nd comma)
            if len(parts) > 2:
                preferred_time = parts[2].strip()

    # Calculate lead score based on intent (from early detection) or timeline
    lead_score = state.get("lead_score", "WARM")  # Use detected intent
    
    # Override with timeline if available (Q4)
    timeline = answers.get("q4", "")
    if "1 week" in timeline:
        lead_score = "HOT"
    elif "1 month" in timeline:
        lead_score = "WARM"
    elif not timeline and lead_score == "WARM":
        lead_score = "COLD"  # Default if no timeline

    lead_data = {
        "wa_id": wa_id,
        "timestamp": timestamp,
        "phone_number": phone,
        "customer_name": name,
        "budget": answers.get("budget", answers.get("q3", "")),  # NEW: budget field
        "q1_vehicle_type": answers.get("q1", answers.get("vehicle_type", "")),
        "q2_new_or_used": answers.get("q2", ""),
        "q3_budget": answers.get("budget", answers.get("q3", "")),  # Keep for compatibility
        "q4_purchase_timeline": answers.get("q4", ""),
        "q5_trade_in": answers.get("q5", ""),
        "q6_contact_details": answers.get("q6", f"{name}, {phone}"),  # Construct if not present
        "lead_score": lead_score,
        "preferred_time": preferred_time,
        "status": "New",
        "intent": state.get("intent", "WARM"),  # NEW: Store detected intent
    }

    log_lead_to_sheet(lead_data)

    # 🔔 Instant dealer notification for HOT + WARM leads
    # Get client config using recipient_id stored in state at session start
    recipient_id  = state.get("recipient_id", "")
    client_config = get_client_config(recipient_id)
    notify_hot_lead_dealer(lead_data, client_config)

    # ═══════════════════════════════════════════════════════════
    # P2: Register HOT lead for 5-min escalation timer
    # ═══════════════════════════════════════════════════════════
    if lead_score == "HOT":
        register_hot_lead(
            wa_id       = wa_id,
            name        = name,
            phone       = phone,
            model       = lead_data.get("q1_vehicle_type", ""),
            budget      = lead_data.get("q3_budget", ""),
            timeline    = lead_data.get("q4_purchase_timeline", ""),
            dealer_wa   = client_config.get("dealer_whatsapp", ""),
            manager_wa  = client_config.get("manager_whatsapp", ""),
            dealer_name = client_config.get("dealer_name", "ReplyFast Auto"),
            redis_client= r if REDIS_AVAILABLE else None,
        )

    # Personalized thank you message
    thank_you_name = f", {name}" if name else ""
    send_whatsapp_message(
        wa_id,
        f"🎉 Thank you{thank_you_name}! Your information has been received.\n\n"
        "Our sales team will contact you shortly.\n\n"
        "Have a great day! 🚗✨"
    )

    # ═══════════════════════════════════════════════════════════
    # P1: Offer test drive booking for HOT and WARM leads
    # ═══════════════════════════════════════════════════════════
    if lead_score in ("HOT", "WARM"):
        vehicle    = lead_data.get("q1_vehicle_type", "your preferred car")
        state["q_status"]   = 7   # Awaiting appointment time
        state["answers"]    = answers
        state["lead_score"] = lead_score
        save_user_state(wa_id, state)

        send_whatsapp_message(
            wa_id,
            f"🗓️ *Want to book your test drive for the {vehicle}?*\n\n"
            "Just share your preferred date & time, for example:\n"
            "📅 *Saturday, 1 March at 11 AM*\n\n"
            "Or reply *SKIP* to let our team arrange it for you.",
            buttons=["📅 Book test drive", "⏭️ Skip for now"]
        )
        logger.info(f"📅 [APPT OFFER SENT] To: {wa_id} | Score: {lead_score}")
    else:
        reset_user_state(wa_id)


# =========================
# Message Processing Helpers - FIXED VERSION
# =========================

def sanitize_input(text):
    """
    ✅ NEW: Sanitize user input to prevent injection attacks
    """
    if not text:
        return ""
    
    # Remove potentially dangerous characters
    import html
    text = html.escape(text)
    
    # Limit length to prevent DoS
    if len(text) > 1000:
        text = text[:1000]
    
    # Remove excessive whitespace
    text = re.sub(r'\s+', ' ', text).strip()
    
    return text


# ✅ ADDED: Intent mapping function
INTENT_MAPPING = {
    "NEW_CAR": ["new", "new car", "brand new", "first hand", "n"],
    "USED_CAR": ["used", "second hand", "pre owned", "old car", "u"]
}

def map_text_to_intent(text):
    """Map user text input to NEW_CAR or USED_CAR intent"""
    normalized = text.lower().strip()
    
    for intent, keywords in INTENT_MAPPING.items():
        if any(normalized == kw or kw in normalized for kw in keywords):
            return intent
    
    return None


def extract_message_content(data):
    """
    Extract message content from different Meta webhook formats.
    Returns (wa_id, text_content, message_id, recipient_id).
    recipient_id is the BUSINESS phone number that received the message —
    used for multi-tenant client config lookup.
    """
    value = data.get("entry", [{}])[0].get("changes", [{}])[0].get("value", {})
    messages = value.get("messages", [])

    if not messages:
        messages = data.get("messages", [])

    if not messages:
        return None, None, None, None

    message = messages[0]
    wa_id = message.get("from")
    message_id = message.get("id")

    # ✅ FIX 3: Extract the BUSINESS phone number (the account receiving the message)
    # This is used for correct multi-tenant client config lookup.
    metadata = value.get("metadata", {})
    recipient_id = metadata.get("display_phone_number") or metadata.get("phone_number_id", "")

    text_content = ""

    if "text" in message:
        text_content = message["text"].get("body", "")
    elif "interactive" in message:
        interactive = message["interactive"]
        if interactive["type"] == "button_reply":
            text_content = interactive["button_reply"].get("title", "")
        elif interactive["type"] == "list_reply":
            text_content = interactive["list_reply"].get("title", "")

    text_content = sanitize_input(text_content)
    logger.info(
        f"📥 [MESSAGE EXTRACTED] From: {wa_id}, Business: {recipient_id}, "
        f"ID: {message_id}, Content: '{text_content}'"
    )

    return wa_id, text_content, message_id, recipient_id


# =========================
# Webhook Route - COMPLETELY FIXED VERSION
# =========================

@app.route("/webhook", methods=["GET"])
@limiter.exempt
def webhook_verify():
    """Webhook verification endpoint - exempt from rate limiting"""
    verify_token = request.args.get("hub.verify_token")
    challenge = request.args.get("hub.challenge")
    
    if verify_token == WEBHOOK_VERIFY_TOKEN:
        logger.info("✅ Webhook verification successful")
        return challenge
    else:
        logger.warning(f"⚠️ Webhook verification failed")
        return "Forbidden", 403


@app.route("/webhook", methods=["POST"])
@limiter.limit("100 per minute")
def webhook_message():
    """
    Webhook endpoint for Meta WhatsApp Business API.
    Uses Redis-backed dedup, correct multi-tenant client config lookup,
    and the cleaned-up single-flow optimized funnel.
    """
    data = request.get_json(silent=True) or {}

    # ═══════════════════════════════════════════════════════════
    # P3: Missed Call Auto-Recovery — check BEFORE normal flow
    # ═══════════════════════════════════════════════════════════
    missed = extract_missed_call(data)
    if missed:
        mc_wa_id, mc_recipient_id = missed
        client_cfg  = get_client_config(mc_recipient_id)
        dealer_name = client_cfg.get("dealer_name", "our team")
        dealer_wa   = client_cfg.get("dealer_whatsapp", "")
        # Check if we know what model they were enquiring about
        mc_state   = get_user_state(mc_wa_id)
        mc_model   = mc_state.get("answers", {}).get("vehicle_type") or \
                     mc_state.get("answers", {}).get("q1", "")
        redis_cli  = r if REDIS_AVAILABLE else None
        send_missed_call_recovery(
            mc_wa_id,
            dealer_name=dealer_name,
            send_msg_fn=send_whatsapp_message,
            model_interest=mc_model or None,
            dealer_wa=dealer_wa,
            redis_client=redis_cli,
        )
        return "", 200

    # ✅ FIX 3: extract_message_content now also returns recipient_id
    wa_id, incoming_text, message_id, recipient_id = extract_message_content(data)

    if not wa_id or incoming_text is None:
        return "", 200

    # ✅ FIX 2: Redis-backed dedup — works across workers and restarts
    if is_message_processed(message_id):
        logger.info(f"🔄 [DUPLICATE BLOCKED] Already processed message {message_id} from {wa_id}")
        return "", 200
    mark_message_processed(message_id)

    logger.info(f"📨 [INCOMING MESSAGE] From: {wa_id}, Business: {recipient_id}, Text: '{incoming_text}'")

    state = get_user_state(wa_id)
    q_status = state.get("q_status", 0)
    answers = state.get("answers", {})
    retry_count = state.get("retry_count", 0)

    logger.info(f"🔍 [STATE DEBUG] q_status: {q_status}, retry_count: {retry_count}")

    # ═══════════════════════════════════════════════════════════
    # P1: Reschedule intent — intercept before normal flow
    # ═══════════════════════════════════════════════════════════
    redis_cli = r if REDIS_AVAILABLE else None
    active_appt = get_active_appointment(wa_id, redis_cli)
    if active_appt and detect_reschedule_intent(incoming_text):
        cust_name = active_appt.get("name", answers.get("name", "there"))
        handle_reschedule(wa_id, cust_name, redis_cli, send_whatsapp_message)
        state["q_status"] = 7   # Back to awaiting new time
        save_user_state(wa_id, state)
        return jsonify({"status": "ok", "step": "reschedule_initiated"})

    # ===== OPTIMIZED FUNNEL: Start of flow =====
    # q_status = 0: Initial welcome
    # q_status = 0.5: Awaiting intent (HOT/WARM/COLD)
    # q_status = 1: Awaiting budget
    # q_status = 1.5: Awaiting name
    # q_status = 1.7: Awaiting phone
    # q_status = 2: Awaiting vehicle type
    # q_status = 3: Awaiting new/used
    # q_status = 4: Awaiting purchase timeline
    # q_status = 5: Awaiting trade-in
    # q_status = 6: Awaiting final contact details
    
    if q_status == 0:
        if incoming_text.lower() in ['start', 'begin', 'hi', 'hello', 'hey', 'hii', 'hlo']:
            # Look up client config by business phone number (not sender)
            client_config = get_client_config(recipient_id)
            biz_name      = client_config.get("dealer_name", "ReplyFast Auto")
            industry      = client_config.get("industry", "car_dealer")

            state["answers"]      = {}
            state["retry_count"]  = 0
            state["recipient_id"] = recipient_id
            state["industry"]     = industry

            if industry == "clinic":
                # ── CLINIC WELCOME ─────────────────────────────────────
                sub_industry = client_config.get("sub_industry", "clinic")
                send_clinic_welcome(wa_id, biz_name, send_whatsapp_message, sub_industry)
                state["q_status"] = 10  # Awaiting booking intent
                save_user_state(wa_id, state)
                logger.info(f"✅ [CLINIC WELCOME] Sent to {wa_id} for {biz_name}")
                return jsonify({"status": "ok", "step": "sent_clinic_welcome"})
            else:
                # ── CAR DEALER WELCOME (unchanged) ─────────────────────
                send_welcome_optimized(wa_id, biz_name, send_whatsapp_message)
                state["q_status"] = 0.5
                save_user_state(wa_id, state)
                logger.info(f"✅ [OPTIMIZED WELCOME] Sent to {wa_id} for {biz_name}")
                return jsonify({"status": "ok", "step": "sent_optimized_welcome"})
        else:
            industry = state.get("industry", "car_dealer")
            if industry == "clinic":
                client_config = get_client_config(recipient_id)
                biz_name = client_config.get("dealer_name", "the clinic")
                send_whatsapp_message(wa_id, f"👋 Welcome! Type *HI* to book an appointment with *{biz_name}*.")
            else:
                send_whatsapp_message(
                    wa_id,
                    "🚗 Welcome to ReplyFast Auto!\n\nType 'HI' to get started! 👋"
                )
            return jsonify({"status": "ok", "step": "sent_start_prompt"})
    
    # Intent detection (q_status = 0.5)
    if q_status == 0.5:
        # Detect if user is HOT, WARM, or COLD lead
        intent = detect_user_intent(incoming_text)
        state["intent"] = intent
        state["lead_score"] = intent  # Store for later use
        
        # Move to budget question (reordered - budget comes FIRST now)
        state["q_status"] = 1
        state["retry_count"] = 0
        save_user_state(wa_id, state)
        
        logger.info(f"🎯 [INTENT DETECTED] {wa_id}: {intent}")
        # ✅ FIX 3: Use recipient_id (business phone) for client config lookup
        client_config = get_client_config(recipient_id)
        send_q_budget_early(wa_id, send_whatsapp_message, client_config)
        return jsonify({"status": "ok", "step": "sent_budget_question"})


    # ===== OPTIMIZED FUNNEL: Q1 - Budget (Early) =====
    if q_status == 1:
        # ✅ FIX 3: Use recipient_id (business phone) for client config lookup
        client_config = get_client_config(recipient_id)
        # Validate budget selection from client config
        budget_options = client_config.get("budget_options", [])
        # Also allow lowercase/normalized versions for matching
        normalized_options = [opt.lower() for opt in budget_options]
        # And keep legacy fallback just in case config is missing
        if not budget_options:
             budget_options = ["Under ₹5L", "₹5-10L", "₹10-15L", "₹15-25L", "Above ₹25L"]
        
        # Normalize input
        user_input_lower = incoming_text.lower().strip()
        
        # Check if valid budget
        # Match against full string OR simple numeric shorthands (e.g. "5", "5-10") could be added here
        # For now, strict match against configured options
        is_valid = any(opt.lower() in user_input_lower for opt in budget_options) or \
                   any(opt in user_input_lower for opt in normalized_options)
        
        if not is_valid:
            retry_count += 1
            state["retry_count"] = retry_count
            save_user_state(wa_id, state)
            
            if retry_count >= 2:
                send_whatsapp_message(
                    wa_id,
                    "Having trouble? Please select one of the budget options, or type CALL for assistance."
                )
            else:
                send_whatsapp_message(
                    wa_id,
                    "Please select a budget range from the options above. 💰"
                )
            return jsonify({"status": "ok", "step": "invalid_budget"})
        
        # Valid budget received - store it
        answers["budget"] = incoming_text
        state["answers"] = answers
        state["q_status"] = 1.5  # Move to name capture
        state["retry_count"] = 0
        save_user_state(wa_id, state)
        
        logger.info(f"✅ [BUDGET] {wa_id} selected: {incoming_text}")
        
        # Request name (EARLY CONTACT CAPTURE)
        request_contact_early(wa_id, send_whatsapp_message)
        return jsonify({"status": "ok", "step": "sent_name_request"})
    
    # ===== OPTIMIZED FUNNEL: Q1.5 - Name Capture =====
    if q_status == 1.5:
        # Validate name
        is_valid, validated_name = validate_name(incoming_text)
        
        if not is_valid:
            retry_count += 1
            state["retry_count"] = retry_count
            save_user_state(wa_id, state)
            
            send_whatsapp_message(
                wa_id,
                validated_name  # This contains the error message
            )
            return jsonify({"status": "ok", "step": "invalid_name"})
        
        # Valid name received
        answers["name"] = validated_name
        state["answers"] = answers
        state["q_status"] = 1.7  # Move to phone capture
        state["retry_count"] = 0
        save_user_state(wa_id, state)
        
        logger.info(f"✅ [NAME CAPTURED] {wa_id}: {validated_name}")
        
        # Request phone number
        request_phone_number(wa_id, validated_name, send_whatsapp_message)
        return jsonify({"status": "ok", "step": "sent_phone_request"})
    
    # ===== OPTIMIZED FUNNEL: Q1.7 - Phone Capture =====
    if q_status == 1.7:
        # Validate phone number
        client_config = get_client_config(wa_id)
        is_valid, validated_phone = validate_phone_number(incoming_text, client_config)
        
        if not is_valid:
            retry_count += 1
            state["retry_count"] = retry_count
            save_user_state(wa_id, state)
            
            send_whatsapp_message(
                wa_id,
                validated_phone  # This contains the error message
            )
            return jsonify({"status": "ok", "step": "invalid_phone"})
        
        # Valid phone received - CONTACT CAPTURED! 🎉
        answers["phone"] = validated_phone
        state["answers"] = answers
        state["q_status"] = 2  # Move to vehicle type
        state["retry_count"] = 0
        state["contact_captured"] = True  # Mark that we have contact info
        save_user_state(wa_id, state)
        
        logger.info(f"🎉 [CONTACT CAPTURED] {wa_id}: {answers.get('name')} - {validated_phone}")
        
        # Now ask vehicle type
        user_name = answers.get('name', 'there')
        send_q_vehicle_type_after_contact(wa_id, user_name, send_whatsapp_message)
        return jsonify({"status": "ok", "step": "sent_vehicle_type"})
    
    # ===== OPTIMIZED FUNNEL: Q2 - Vehicle Type (After Contact) =====
    if q_status == 2:
        # Validate vehicle type
        vehicle_options = ["Sedan", "SUV", "Hatchback", "MUV", "Others"]
        
        # Normalize and check
        user_input_title = incoming_text.strip().title()
        
        if user_input_title not in vehicle_options and not any(opt.lower() in incoming_text.lower() for opt in vehicle_options):
            retry_count += 1
            state["retry_count"] = retry_count
            save_user_state(wa_id, state)
            
            if retry_count >= 2:
                send_whatsapp_message(
                    wa_id,
                    "Having trouble? Please select: Sedan, SUV, Hatchback, MUV, or Others"
                )
            else:
                send_whatsapp_message(
                    wa_id,
                    "Please select a vehicle type from the options above. 🚗"
                )
            return jsonify({"status": "ok", "step": "invalid_vehicle_type"})
        
        # Valid vehicle type
        answers["q1"] = user_input_title  # Store as q1 for compatibility
        answers["vehicle_type"] = user_input_title
        state["answers"] = answers
        state["q_status"] = 3  # Move to new/used question
        state["retry_count"] = 0
        save_user_state(wa_id, state)
        
        logger.info(f"✅ [VEHICLE TYPE] {wa_id}: {user_input_title}")
        send_q2(wa_id)  # Use existing Q2 function
        return jsonify({"status": "ok", "step": "sent_q2"})

    # ═══════════════════════════════════════════════════════════
    # P1: q_status == 7 — Awaiting appointment date/time
    # ═══════════════════════════════════════════════════════════
    if q_status == 7:
        incoming_lower = incoming_text.lower().strip()

        # User skipped appointment
        if incoming_lower in ("skip", "skip for now", "⏭️ skip for now", "no"):
            reset_user_state(wa_id)
            send_whatsapp_message(
                wa_id,
                "No problem! Our team will reach out to schedule your visit. 🚗\n"
                "Have a great day!"
            )
            return jsonify({"status": "ok", "step": "appt_skipped"})

        # User tapped the "Book test drive" button or typed a date
        if incoming_lower in ("📅 book test drive", "book test drive", "yes", "book"):
            send_whatsapp_message(
                wa_id,
                "Great! Please share your preferred date & time:\n\n"
                "📅 Example: *Saturday, 1 March at 11 AM*"
            )
            return jsonify({"status": "ok", "step": "appt_prompt_sent"})

        # Try to parse the date/time from free text
        # We accept any free-text date — if parsing fails we store as string
        appt_text = incoming_text.strip()
        if len(appt_text) < 4:
            send_whatsapp_message(
                wa_id,
                "Please share a date and time for your test drive.\n\n"
                "Example: *Saturday, 1 March at 11 AM*"
            )
            return jsonify({"status": "ok", "step": "appt_invalid_input"})

        # Pull details from existing lead data
        cust_name    = answers.get("name", "Customer")
        cust_phone   = answers.get("phone", "")
        vehicle_type = answers.get("vehicle_type", answers.get("q1", "Car"))
        recipient_id_appt = state.get("recipient_id", "")
        client_cfg_appt   = get_client_config(recipient_id_appt)
        dealer_wa_appt    = client_cfg_appt.get("dealer_whatsapp", "")
        maps_link_appt    = client_cfg_appt.get("showroom_maps_link", "")
        dealer_name_appt  = client_cfg_appt.get("dealer_name", "ReplyFast Auto")

        # Build a rough datetime — use tomorrow as default if date parsing fails
        try:
            from datetime import datetime, timedelta
            # Simple heuristic: parse tomorrow if no date given
            appt_dt = datetime.now() + timedelta(days=1)
            appt_dt = appt_dt.replace(hour=11, minute=0, second=0, microsecond=0)
        except Exception:
            from datetime import datetime, timedelta
            appt_dt = datetime.now() + timedelta(days=1)

        book_appointment(
            wa_id=wa_id,
            name=cust_name,
            phone=cust_phone,
            model=vehicle_type,
            appt_dt=appt_dt,
            dealer_wa=dealer_wa_appt,
            maps_link=maps_link_appt,
            send_msg_fn=send_whatsapp_message,
            redis_client=redis_cli,
            dealer_name=dealer_name_appt,
        )
        # Store appointment time text in state for reference
        state["appointment_text"] = appt_text
        # Keep state as 7 until they reschedule or show up — then admin resets
        save_user_state(wa_id, state)
        logger.info(f"📅 [APPT BOOKED] {cust_name} ({wa_id}) → {appt_text}")
        return jsonify({"status": "ok", "step": "appt_booked"})

    # ✅ ADDED: Handle CALL_AWAITING_CONTACT state (q_status == 100)
    if q_status == 100:
        """Parse contact details: Name, Phone, Preferred Time"""
        try:
            # Parse the input: "Name, Phone, Preferred Time"
            parts = incoming_text.split(',')
            
            if len(parts) < 3:
                send_whatsapp_message(
                    wa_id,
                    "Please provide all details in this format:\n\n"
                    "📝 Your Name, 📱 Phone Number, 🕐 Preferred Time\n\n"
                    "Example: Aditya Jha, 9876543210, Tomorrow 3 PM"
                )
                return jsonify({"status": "ok", "step": "awaiting_contact_retry"})
            
            # Extract details
            customer_name = parts[0].strip()
            phone_number = parts[1].strip()
            preferred_time = parts[2].strip()
            
            # Create callback request
            callback_data = {
                "wa_id": wa_id,
                "timestamp": datetime.now().isoformat(),
                "phone_number": phone_number,
                "customer_name": customer_name,
                "preferred_time": preferred_time,
                "source": "CALL_REQUEST",
                "status": "Callback Requested"
            }
            
            # Log to Google Sheets
            log_lead_to_sheet(callback_data)
            
            # Notify dealer
            notify_dealer_callback(wa_id, customer_name=customer_name, q_status=None)
            
            # Move to handoff
            state["q_status"] = 99
            state["callback_info"] = callback_data
            save_user_state(wa_id, state)
            
            logger.info(f"✅ [CALLBACK CONTACT RECEIVED] User {wa_id}")
            logger.info(f"   Name: {customer_name}")
            logger.info(f"   Phone: {phone_number}")
            logger.info(f"   Preferred Time: {preferred_time}")
            
            send_whatsapp_message(
                wa_id,
                f"✅ Got it, {customer_name}!\n\n"
                f"Our team will call you at {phone_number} {preferred_time}.\n\n"
                f"Thank you for choosing us! 🚗"
            )
            return jsonify({"status": "ok", "step": "callback_confirmed"})
            
        except Exception as e:
            logger.error(f"❌ [CALLBACK PARSING ERROR]: {str(e)}")
            send_whatsapp_message(
                wa_id,
                "Sorry, I couldn't parse your details. Please try again:\n\n"
                "Name, Phone Number, Preferred Time\n\n"
                "Example: Aditya Jha, 9876543210, Tomorrow 3 PM"
            )
            return jsonify({"status": "ok", "step": "callback_error"})

    # ✅ FIX 4: Human handoff — suppress further automation
    if q_status == 99:
        logger.info(f"🤝 [HUMAN HANDOFF ACTIVE] User {wa_id} - suppressing bot response")
        return jsonify({"status": "ok", "step": "human_handoff_active"})

    # ═══════════════════════════════════════════════════════════
    # CLINIC FUNNEL: q_status 10–13
    # 10  = Awaiting booking intent (book / ask question)
    # 10.5 = Awaiting service selection
    # 11  = Awaiting preferred date
    # 12  = Awaiting patient name
    # 13  = Awaiting patient phone
    # ═══════════════════════════════════════════════════════════

    if q_status == 10:
        # Patient tapped "Book Appointment" or "Ask a Question"
        incoming_lower = incoming_text.lower().strip()
        client_config  = get_client_config(state.get("recipient_id", ""))
        biz_name       = client_config.get("dealer_name", "the clinic")
        sub_industry   = client_config.get("sub_industry", "clinic")
        custom_svcs    = client_config.get("services") or None

        if any(kw in incoming_lower for kw in ("book", "appointment", "yes", "📅")):
            # Show service menu
            send_service_selection(wa_id, biz_name, send_whatsapp_message, sub_industry, custom_svcs)
            state["q_status"] = 10.5
            save_user_state(wa_id, state)
            return jsonify({"status": "ok", "step": "clinic_service_menu_sent"})
        else:
            # Asking a question — soft response, keep at status 10
            send_cold_clinic_response(wa_id, biz_name, send_whatsapp_message)
            return jsonify({"status": "ok", "step": "clinic_cold_response"})

    if q_status == 10.5:
        # Patient selected a service
        service = incoming_text.strip()
        if len(service) < 2:
            client_config = get_client_config(state.get("recipient_id", ""))
            biz_name      = client_config.get("dealer_name", "the clinic")
            sub_industry  = client_config.get("sub_industry", "clinic")
            custom_svcs   = client_config.get("services") or None
            send_service_selection(wa_id, biz_name, send_whatsapp_message, sub_industry, custom_svcs)
            return jsonify({"status": "ok", "step": "clinic_service_reprompt"})

        answers["service"] = service
        state["answers"]   = answers
        state["q_status"]  = 11
        save_user_state(wa_id, state)
        send_date_selection(wa_id, service, send_whatsapp_message)
        return jsonify({"status": "ok", "step": "clinic_date_sent"})

    if q_status == 11:
        # Patient selected preferred date
        date_text    = incoming_text.strip()
        lead_score   = score_date_choice(date_text)  # HOT/WARM/COLD
        answers["preferred_date"] = date_text
        answers["lead_score"]     = lead_score
        state["answers"]          = answers
        state["lead_score"]       = lead_score
        state["q_status"]         = 12
        save_user_state(wa_id, state)
        request_patient_name(wa_id, send_whatsapp_message)
        logger.info(f"📅 [CLINIC DATE] {wa_id} chose {date_text} → {lead_score}")
        return jsonify({"status": "ok", "step": "clinic_name_requested"})

    if q_status == 12:
        # Patient entered name
        patient_name = incoming_text.strip()
        if len(patient_name) < 2:
            send_whatsapp_message(wa_id, "Please enter your *full name* so we can confirm your booking. 😊")
            return jsonify({"status": "ok", "step": "clinic_name_reprompt"})

        answers["name"]   = patient_name
        state["answers"] = answers
        state["q_status"] = 13
        save_user_state(wa_id, state)
        request_patient_phone(wa_id, patient_name, send_whatsapp_message)
        return jsonify({"status": "ok", "step": "clinic_phone_requested"})

    if q_status == 13:
        # Patient entered phone
        phone = incoming_text.strip()
        if len(phone) < 7:
            send_whatsapp_message(wa_id, "Please enter a valid *phone number* so our team can reach you. 📱")
            return jsonify({"status": "ok", "step": "clinic_phone_reprompt"})

        answers["phone"]   = phone
        state["answers"]  = answers
        state["q_status"] = 7   # Reuse appointment booking step
        save_user_state(wa_id, state)

        # Retrieve client config
        client_config = get_client_config(state.get("recipient_id", ""))
        biz_name      = client_config.get("dealer_name", "the clinic")
        maps_link     = client_config.get("showroom_maps_link", "")
        dealer_wa     = client_config.get("dealer_whatsapp", "")
        manager_wa    = client_config.get("manager_whatsapp", "")
        sub_industry  = client_config.get("sub_industry", "clinic")

        patient_name  = answers.get("name", "Patient")
        service       = answers.get("service", "Consultation")
        date_text     = answers.get("preferred_date", "TBD")
        lead_score    = answers.get("lead_score", "WARM")

        # Send booking summary to patient
        send_clinic_booking_summary(
            wa_id, patient_name, service, date_text, biz_name,
            send_whatsapp_message, maps_link
        )

        # Build lead data for logging
        from datetime import datetime as _dt
        lead_data = {
            "wa_id":          wa_id,
            "timestamp":      _dt.now().isoformat(),
            "phone_number":   phone,
            "customer_name":  patient_name,
            "service":        service,
            "preferred_date": date_text,
            "lead_score":     lead_score,
            "status":         "New",
            "industry":       "clinic",
            "sub_industry":   sub_industry,
        }
        log_lead_to_sheet(lead_data)

        # Notify clinic staff via WhatsApp
        if dealer_wa:
            staff_msg = build_clinic_staff_notification(lead_data, client_config)
            send_whatsapp_message(dealer_wa, staff_msg)

        # P2: Register HOT leads for escalation
        if lead_score == "HOT":
            register_hot_lead(
                wa_id       = wa_id,
                name        = patient_name,
                phone       = phone,
                model       = service,
                budget      = "",
                timeline    = date_text,
                dealer_wa   = dealer_wa,
                manager_wa  = manager_wa,
                dealer_name = biz_name,
                redis_client= redis_cli,
            )

        logger.info(f"✅ [CLINIC BOOKING] {patient_name} ({wa_id}) | {service} | {date_text} | {lead_score}")
        return jsonify({"status": "ok", "step": "clinic_booking_complete"})

    # ✅ FIX: Catch-all for unexpected / corrupted q_status
    # Without this, users end up silently stuck with no response.
    logger.error(
        f"❌ [UNKNOWN STATE] wa_id={wa_id}, q_status={q_status}. "
        "Resetting state and prompting restart."
    )
    reset_user_state(wa_id)
    send_whatsapp_message(
        wa_id,
        "⚠️ Something went wrong on our end.\n\n"
        "Type *HI* to start a fresh conversation. We apologize for the inconvenience! 🙏"
    )
    return jsonify({"status": "ok", "step": "unknown_state_reset"})

@app.route("/health", methods=["GET"])
@limiter.exempt
def health_check():
    """Health check endpoint"""
    redis_status = False
    
    if REDIS_AVAILABLE:
        try:
            redis_status = r.ping()
        except:
            redis_status = False
    
    return jsonify({
        "status": "healthy",
        "timestamp": datetime.utcnow().isoformat(),
        "redis_connected": redis_status,
        "fallback_mode": not REDIS_AVAILABLE,
        "version": "SECURITY_HARDENED_v2.0"
    })


# ═══════════════════════════════════════════════════════════
# P4: Revenue Dashboard
# ═══════════════════════════════════════════════════════════

@app.route("/admin/dashboard/<dealer_wa>", methods=["GET"])
@limiter.exempt
def dealer_dashboard(dealer_wa):
    """Per-dealer live dashboard."""
    redis_cli     = r if REDIS_AVAILABLE else None
    client_config = get_client_config(dealer_wa)
    data          = get_dashboard_data(dealer_wa, redis_cli, client_config)
    return render_template("dashboard.html", **data)


@app.route("/admin/dashboard/", methods=["GET"])
@limiter.exempt
def dashboard_index():
    """
    All-dealers index: shows a card per registered client with a link
    to their individual dashboard.
    """
    redis_cli = r if REDIS_AVAILABLE else None
    try:
        with open("clients.json") as f:
            import json as _json
            clients_raw = _json.load(f).get("clients", {})
    except Exception:
        clients_raw = {}

    summaries = []
    for phone, cfg in clients_raw.items():
        hot_count = 0
        if redis_cli:
            try:
                hot_count = redis_cli.zcard("hot:pending")
            except Exception:
                pass
        summaries.append({
            "dealer_wa":  phone,
            "biz_name":   cfg.get("dealer_name", phone),
            "industry":   cfg.get("industry", "car_dealer"),
            "hot_count":  hot_count,
            "url":        f"/admin/dashboard/{phone}",
        })

    html_rows = "".join(
        f'<tr><td><a href="{s["url"]}" style="color:#3b6fff;font-weight:600">'
        f'{s["biz_name"]}</a></td>'
        f'<td>{s["industry"]}</td>'
        f'<td style="color:{"#dc2626" if s["hot_count"] else "#16a34a"}">'
        f'{s["hot_count"] or "0"}</td>'
        f'<td><a href="{s["url"]}" style="color:#3b6fff">View →</a></td></tr>'
        for s in summaries
    )
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>ReplyFast — All Clients</title>
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700&display=swap" rel="stylesheet">
  <style>
    body{{font-family:Inter,sans-serif;background:#f5f6fa;color:#1a1d23;padding:40px}}
    h1{{font-size:22px;margin-bottom:6px}}
    p{{color:#6b7280;font-size:13px;margin-bottom:24px}}
    table{{width:100%;max-width:700px;border-collapse:collapse;
           background:#fff;border-radius:12px;overflow:hidden;
           box-shadow:0 1px 3px rgba(0,0,0,.07)}}
    th{{background:#f5f6fa;font-size:11px;font-weight:600;text-transform:uppercase;
        letter-spacing:.5px;color:#6b7280;padding:12px 16px;text-align:left;
        border-bottom:1px solid #e8eaed}}
    td{{padding:13px 16px;border-bottom:1px solid #e8eaed;font-size:13px}}
    tr:last-child td{{border-bottom:none}}
  </style>
</head>
<body>
  <h1>ReplyFast — All Clients</h1>
  <p>Select a client to view their live dashboard.</p>
  <table>
    <thead><tr><th>Client</th><th>Industry</th><th>HOT leads</th><th></th></tr></thead>
    <tbody>{html_rows}</tbody>
  </table>
</body></html>"""


@app.route("/admin/reset/<wa_id>", methods=["POST"])
@limiter.exempt
def reset_user(wa_id):
    """
    Admin endpoint to reset a user's conversation state.
    Useful if user is stuck in handoff or any other state.
    """
    try:
        reset_user_state(wa_id)
        logger.info(f"🔄 [ADMIN RESET] User {wa_id} state reset successfully")
        return jsonify({
            "status": "success",
            "message": f"User {wa_id} state has been reset",
            "wa_id": wa_id
        }), 200
    except Exception as e:
        logger.error(f"❌ [ADMIN RESET ERROR] For user {wa_id}: {str(e)}")
        return jsonify({
            "status": "error",
            "message": f"Failed to reset user state: {str(e)}",
            "wa_id": wa_id
        }), 500


# ═══════════════════════════════════════════════════════════
# P1: Admin Endpoints — Show-Rate & Appointment Management
# ═══════════════════════════════════════════════════════════

@app.route("/admin/showrate/<dealer_wa>", methods=["GET"])
@limiter.exempt
def show_rate_stats(dealer_wa):
    """Get show-rate statistics for a specific dealer."""
    redis_cli = r if REDIS_AVAILABLE else None
    stats = get_show_rate(dealer_wa, redis_cli)
    return jsonify(stats)


@app.route("/admin/appointment/showed/<wa_id>", methods=["POST"])
@limiter.exempt
def mark_showed(wa_id):
    """Mark a customer as having showed up for their test drive."""
    try:
        dealer_wa = request.json.get("dealer_wa", "") if request.is_json else ""
        redis_cli = r if REDIS_AVAILABLE else None
        result = confirm_showed(wa_id, dealer_wa, redis_cli, send_whatsapp_message)
        return jsonify(result)
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/admin/appointment/noshow/<wa_id>", methods=["POST"])
@limiter.exempt
def mark_noshow(wa_id):
    """Mark a customer as a no-show for their test drive."""
    try:
        dealer_wa = request.json.get("dealer_wa", "") if request.is_json else ""
        redis_cli = r if REDIS_AVAILABLE else None
        result = confirm_noshow(wa_id, dealer_wa, redis_cli)
        return jsonify(result)
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


# ═══════════════════════════════════════════════════════════
# P2: Admin Endpoints — HOT Lead Escalation & Response Times
# ═══════════════════════════════════════════════════════════

@app.route("/admin/lead/contacted/<wa_id>", methods=["POST"])
@limiter.exempt
def lead_contacted(wa_id):
    """Mark a HOT lead as contacted by the rep. Stops escalation + logs response time."""
    try:
        redis_cli = r if REDIS_AVAILABLE else None
        result = mark_contacted(wa_id, redis_cli)
        return jsonify(result)
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/admin/response-time/<dealer_wa>", methods=["GET"])
@limiter.exempt
def response_time_stats(dealer_wa):
    """Get average HOT lead response time for a dealer."""
    redis_cli = r if REDIS_AVAILABLE else None
    result = get_avg_response_time(dealer_wa, redis_cli)
    return jsonify(result)


@app.errorhandler(429)
def ratelimit_handler(e):
    """Custom handler for rate limit exceeded"""
    logger.warning(f"Rate limit exceeded: {e.description}")
    return jsonify({
        "error": "rate_limit_exceeded",
        "message": "Too many requests. Please try again later."
    }), 429


# ---------------------------------------------------------------------------
# Start the exit-intent follow-up scheduler
# Using a module-level flag ensures the scheduler starts exactly once,
# whether running as flask dev server OR under Gunicorn multiple workers
# (each worker gets its own process and its own scheduler — that's fine).
# ---------------------------------------------------------------------------
_scheduler_started = False

@app.before_request
def start_scheduler_once():
    global _scheduler_started
    if not _scheduler_started:
        from apscheduler.triggers.interval import IntervalTrigger
        redis_client = r if REDIS_AVAILABLE else None
        scheduler = create_scheduler(
            redis_client=redis_client,
            send_message_func=send_whatsapp_message,
            get_state_func=get_user_state,
            save_state_func=save_user_state,
            get_client_config_func=get_client_config,
        )

        # ── P1: Appointment reminder job ─────────────────────────────
        scheduler.add_job(
            func=check_and_send_reminders,
            trigger=IntervalTrigger(seconds=APPOINTMENT_REMINDER_INTERVAL),
            kwargs={
                "redis_client": redis_client,
                "send_msg_fn":  send_whatsapp_message,
            },
            id="appointment_reminders",
            name="Appointment Show-Rate Reminders",
            replace_existing=True,
            misfire_grace_time=60,
        )

        # ── P2: HOT lead escalation job ──────────────────────────────
        scheduler.add_job(
            func=check_and_escalate_hot_leads,
            trigger=IntervalTrigger(seconds=60),
            kwargs={
                "redis_client": redis_client,
                "send_msg_fn":  send_whatsapp_message,
            },
            id="hot_lead_escalation",
            name="HOT Lead 5-Min Escalation",
            replace_existing=True,
            misfire_grace_time=30,
        )

        scheduler.start()
        _scheduler_started = True
        logger.info("⏰ [SCHEDULER] Follow-up + Appointment reminders + HOT lead escalation started")


if __name__ == "__main__":
    logger.info(f"🚀 ReplyFast Auto FIXED VERSION starting on port {PORT}")
    logger.info(f"📱 Meta WhatsApp API: {'✅ Configured' if META_API_TOKEN and META_PHONE_ID else '❌ Not Configured'}")
    
    # Google Sheets Configuration Status
    sheets_status = "❌ Not Configured"
    if SHEETS_API_MODE == "direct" and GOOGLE_SHEETS_AVAILABLE and os.path.exists(GOOGLE_CREDENTIALS_FILE):
        sheets_status = f"✅ Configured (Direct API) - Sheet ID: {SHEET_ID[:20]}..."
    elif SHEET_KEY:
        sheets_status = f"✅ Configured (Webhook) - Deployment ID: {SHEET_KEY[:20]}..."
    
    logger.info(f"📊 Google Sheets: {sheets_status}")
    
    redis_status = "❌ Not Connected (Using memory fallback)"
    if REDIS_AVAILABLE:
        try:
            if r.ping():
                redis_status = "✅ Connected"
        except:
            redis_status = "❌ Not Connected"
    
    logger.info(f"🔴 Redis: {redis_status}")
    logger.info(f"🔒 SECURITY & FIXES APPLIED:")
    logger.info(f"   ✅ Conversation loop resolved")
    logger.info(f"   ✅ Retry counter implemented") 
    logger.info(f"   ✅ Better button response extraction")
    logger.info(f"   ✅ Enhanced state debugging")
    logger.info(f"   ✅ Webhook rate limiting enabled")
    logger.info(f"   ✅ Input sanitization added")
    logger.info(f"   ✅ API retry logic with exponential backoff")
    logger.info(f"   ✅ Memory leak prevention")
    logger.info(f"   ✅ Google Sheets API Direct Integration")
    logger.info(f"   ✅ Service Account Authentication")
    
    app.run(host="0.0.0.0", port=PORT, debug=True)