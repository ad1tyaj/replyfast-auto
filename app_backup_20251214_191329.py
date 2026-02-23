import json
from datetime import datetime
import requests
import re
import logging
import time

from flask import Flask, request, jsonify
import redis
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

from config import (
    REDIS_HOST,
    REDIS_PORT,
    REDIS_DB,
    PORT,
    META_API_TOKEN,
    META_PHONE_ID,
    BSP_PROVIDER,
    SHEET_KEY,
    WEBHOOK_VERIFY_TOKEN,
)
from meta_whatsapp import meta_api

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

app = Flask(__name__)

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


# =========================
# Integrations
# =========================

def send_whatsapp_message(wa_id, message_body, buttons=None):
    """
    Sends WhatsApp message using Meta WhatsApp Business API.
    'buttons' must be a list of button titles or None.
    """
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
        else:
            logger.error(f"❌ [WHATSAPP MESSAGE FAILED] To: {wa_id}")
            logger.error(f"   Error: {result.get('error', 'Unknown error')}")
        
        return result
        
    except Exception as e:
        logger.error(f"❌ [WHATSAPP MESSAGE ERROR] To: {wa_id}")
        logger.error(f"   Exception: {str(e)}")
        return {"success": False, "error": str(e)}


def log_lead_to_sheet(lead_data):
    """
    Logs the lead to Google Sheets using webhook or direct API.
    """
    try:
        if not SHEET_KEY:
            logger.warning(f"⚠️ [SHEETS WARNING] SHEET_KEY not configured, only logging locally")
            logger.info(f"📊 [LEAD LOGGED LOCALLY]")
            logger.info(f"   {json.dumps(lead_data, indent=2)}")
            return {"success": True, "method": "local_only"}
        
        webhook_url = f"https://script.google.com/macros/s/{SHEET_KEY}/exec"
        response = requests.post(webhook_url, json=lead_data, timeout=10)
        
        if response.status_code == 200:
            logger.info(f"✅ [LEAD LOGGED TO SHEETS]")
            logger.info(f"   WA ID: {lead_data.get('wa_id')}")
            logger.info(f"   Lead Score: {lead_data.get('lead_score')}")
            return {"success": True, "response": response.text}
        else:
            logger.error(f"❌ [SHEETS LOGGING FAILED]")
            logger.error(f"   Status Code: {response.status_code}")
            logger.info(f"📊 [LEAD LOGGED LOCALLY AS FALLBACK]")
            logger.info(f"   {json.dumps(lead_data, indent=2)}")
            return {"success": False, "fallback": "local", "error": response.text}
            
    except Exception as e:
        logger.error(f"❌ [SHEETS LOGGING ERROR]: {str(e)}")
        logger.info(f"📊 [LEAD LOGGED LOCALLY AS FALLBACK]")
        logger.info(f"   {json.dumps(lead_data, indent=2)}")
        return {"success": False, "fallback": "local", "error": str(e)}


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
    """Save user state with 1 hour expiry"""
    if REDIS_AVAILABLE:
        try:
            r.setex(wa_id, 3600, json.dumps(state))
        except redis.ConnectionError as e:
            logger.error(f"Error saving state for {wa_id}: {str(e)}")
            MEMORY_STORE[wa_id] = state
    else:
        MEMORY_STORE[wa_id] = state


def reset_user_state(wa_id):
    """Reset user state (delete from Redis or memory)"""
    if REDIS_AVAILABLE:
        try:
            r.delete(wa_id)
        except redis.ConnectionError as e:
            logger.error(f"Error resetting state for {wa_id}: {str(e)}")
            if wa_id in MEMORY_STORE:
                del MEMORY_STORE[wa_id]
    else:
        if wa_id in MEMORY_STORE:
            del MEMORY_STORE[wa_id]


# =========================
# Question Sending Helpers
# =========================

def send_q1(wa_id):
    text = "🚗 Welcome to ReplyFast Auto! \n\nQ1: Are you looking for a New or Used Car?"
    buttons = ["New", "Used"]
    send_whatsapp_message(wa_id, text, buttons=buttons)


def send_q2(wa_id):
    text = "Q2: What's your budget range?"
    buttons = [
        "Under 5 Lakhs",
        "5-8 Lakhs", 
        "8-12 Lakhs",
        "12-20 Lakhs",
        "Above 20 Lakhs"
    ]
    send_whatsapp_message(wa_id, text, buttons=buttons)


def send_q3(wa_id):
    text = "Q3: What is your urgency to purchase?"
    buttons = ["HOT (7 days)", "WARM (1 month)", "COLD (6+ months)"]
    send_whatsapp_message(wa_id, text, buttons=buttons)


def send_q4(wa_id):
    text = "Q4: Would you like to book a test drive?"
    buttons = ["Book Now", "More Details"]
    send_whatsapp_message(wa_id, text, buttons=buttons)


def send_q5(wa_id):
    """Q5: Contact details - Natural conclusion"""
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
# Lead Completion Helper
# =========================

def complete_lead(wa_id, state):
    answers = state.get("answers", {})
    timestamp = datetime.utcnow().isoformat()

    lead_data = {
        "wa_id": wa_id,
        "q1_car_type": answers.get("q1"),
        "q2_model_budget": answers.get("q2"),
        "q3_urgency": answers.get("q3"),
        "q4_test_drive": answers.get("q4"),
        "q5_contact_details": answers.get("q5"),
        "lead_score": answers.get("lead_score"),
        "timestamp": timestamp,
    }

    log_lead_to_sheet(lead_data)
    reset_user_state(wa_id)

    send_whatsapp_message(
        wa_id,
        "🎉 Thank you! Your information has been received.\n\n"
        "Our sales team will contact you shortly at your preferred time.\n\n"
        "Have a great day! 🚗✨"
    )


# =========================
# Message Processing Helpers
# =========================

def extract_message_content(data):
    """Extract message content from different Meta webhook formats"""
    messages = data.get("entry", [{}])[0].get("changes", [{}])[0].get("value", {}).get("messages", [])
    
    if not messages:
        messages = data.get("messages", [])
    
    if not messages:
        return None, None
    
    message = messages[0]
    wa_id = message.get("from")
    
    text_content = ""
    
    if "text" in message:
        text_content = message["text"].get("body", "")
    elif "interactive" in message:
        interactive = message["interactive"]
        if interactive["type"] == "button_reply":
            text_content = interactive["button_reply"].get("title", "")
        elif interactive["type"] == "list_reply":
            text_content = interactive["list_reply"].get("title", "")
    
    # Log extracted content for debugging
    logger.info(f"📥 Extracted from {wa_id}: '{text_content}'")
    
    return wa_id, text_content.strip()


# =========================
# Webhook Route - FIXED VERSION
# =========================

@app.route("/webhook", methods=["GET", "POST"])
@limiter.exempt
def webhook():
    """Webhook endpoint for Meta WhatsApp Business API - FIXED"""
    
    if request.method == "GET":
        verify_token = request.args.get("hub.verify_token")
        challenge = request.args.get("hub.challenge")
        
        if verify_token == WEBHOOK_VERIFY_TOKEN:
            logger.info("✅ Webhook verification successful")
            return challenge
        else:
            logger.warning(f"⚠️  Webhook verification failed")
            return "Forbidden", 403
    
    # POST - handle messages
    data = request.get_json(silent=True) or {}
    wa_id, incoming_text = extract_message_content(data)
    
    if not wa_id or incoming_text is None:
        return "", 200

    logger.info(f"📨 Message from {wa_id}: '{incoming_text}'")

    state = get_user_state(wa_id)
    q_status = state.get("q_status", 0)
    answers = state.get("answers", {})
    retry_count = state.get("retry_count", 0)

    # ✅ FIX: Log current state for debugging
    logger.info(f"🔍 Current state - q_status: {q_status}, retry_count: {retry_count}")

    # Start of flow
    if q_status == 0:
        state["q_status"] = 1
        state["answers"] = {}
        state["retry_count"] = 0
        save_user_state(wa_id, state)
        send_q1(wa_id)
        return jsonify({"status": "ok", "step": "sent_q1"})

    # ✅ FIXED Q1: Better validation and retry logic
    if q_status == 1:
        valid_options = ["New", "Used"]
        user_choice = None
        
        for option in valid_options:
            if option.lower() in incoming_text.lower():
                user_choice = option
                break
        
        if not user_choice:
            retry_count += 1
            state["retry_count"] = retry_count
            save_user_state(wa_id, state)
            
            if retry_count >= 3:
                logger.warning(f"⚠️  User {wa_id} failed 3 times at Q1, resetting")
                reset_user_state(wa_id)
                send_whatsapp_message(
                    wa_id,
                    "🤔 Let's start fresh! I'll guide you step by step.\n\n"
                    "💡 Tip: Just tap one of the green buttons that appear below the question."
                )
                time.sleep(1)
                new_state = {"q_status": 1, "answers": {}, "retry_count": 0}
                save_user_state(wa_id, new_state)
                send_q1(wa_id)
            else:
                # ✅ FIX: Don't resend question, just send error
                send_whatsapp_message(
                    wa_id,
                    f"❌ Please tap one of the buttons:\n• New\n• Used\n\n"
                    f"(Attempt {retry_count} of 3)"
                )
            
            return jsonify({"status": "ok", "step": "invalid_q1_option"})

        # Valid answer
        answers["q1"] = user_choice
        state["answers"] = answers
        state["q_status"] = 2
        state["retry_count"] = 0
        save_user_state(wa_id, state)
        send_q2(wa_id)
        return jsonify({"status": "ok", "step": "sent_q2"})

    # ✅ FIXED Q2
    if q_status == 2:
        valid_options = ["Under 5 Lakhs", "5-8 Lakhs", "8-12 Lakhs", "12-20 Lakhs", "Above 20 Lakhs"]
        user_choice = None
        
        for option in valid_options:
            if option.lower() in incoming_text.lower() or option in incoming_text:
                user_choice = option
                break
        
        if not user_choice:
            retry_count += 1
            state["retry_count"] = retry_count
            save_user_state(wa_id, state)
            
            if retry_count >= 3:
                reset_user_state(wa_id)
                send_whatsapp_message(wa_id, "🤔 Let's start over. Please tap one of the options from the list.")
                time.sleep(1)
                new_state = {"q_status": 1, "answers": {}, "retry_count": 0}
                save_user_state(wa_id, new_state)
                send_q1(wa_id)
            else:
                send_whatsapp_message(
                    wa_id,
                    f"❌ Please select a budget range from the list above.\n\n"
                    f"(Attempt {retry_count} of 3)"
                )
            
            return jsonify({"status": "ok", "step": "invalid_q2_option"})
        
        answers["q2"] = user_choice
        state["answers"] = answers
        state["q_status"] = 3
        state["retry_count"] = 0
        save_user_state(wa_id, state)
        send_q3(wa_id)
        return jsonify({"status": "ok", "step": "sent_q3"})

    # ✅ FIXED Q3
    if q_status == 3:
        valid_options = ["HOT (7 days)", "WARM (1 month)", "COLD (6+ months)"]
        normalized = incoming_text.lower()
        
        user_choice = None
        lead_score = None
        
        if "hot" in normalized or "7 days" in normalized:
            user_choice = "HOT (7 days)"
            lead_score = "HOT"
        elif "warm" in normalized or "1 month" in normalized:
            user_choice = "WARM (1 month)"
            lead_score = "WARM"
        elif "cold" in normalized or "6" in normalized or "months" in normalized:
            user_choice = "COLD (6+ months)"
            lead_score = "COLD"
        
        if not user_choice:
            retry_count += 1
            state["retry_count"] = retry_count
            save_user_state(wa_id, state)
            
            if retry_count >= 3:
                reset_user_state(wa_id)
                send_whatsapp_message(wa_id, "🤔 Let's restart. Tap one of the buttons.")
                time.sleep(1)
                new_state = {"q_status": 1, "answers": {}, "retry_count": 0}
                save_user_state(wa_id, new_state)
                send_q1(wa_id)
            else:
                send_whatsapp_message(
                    wa_id,
                    f"❌ Please tap one of the urgency options.\n\n"
                    f"(Attempt {retry_count} of 3)"
                )
            
            return jsonify({"status": "ok", "step": "invalid_q3_option"})

        answers["q3"] = user_choice
        answers["lead_score"] = lead_score
        state["answers"] = answers
        state["q_status"] = 4
        state["retry_count"] = 0
        save_user_state(wa_id, state)
        send_q4(wa_id)
        return jsonify({"status": "ok", "step": "sent_q4"})

    # ✅ FIXED Q4
    if q_status == 4:
        valid_options = ["Book Now", "More Details"]
        user_choice = None
        
        for option in valid_options:
            if option.lower() in incoming_text.lower():
                user_choice = option
                break
        
        if not user_choice:
            retry_count += 1
            state["retry_count"] = retry_count
            save_user_state(wa_id, state)
            
            if retry_count >= 3:
                reset_user_state(wa_id)
                send_whatsapp_message(wa_id, "🤔 Starting over. Tap the buttons.")
                time.sleep(1)
                new_state = {"q_status": 1, "answers": {}, "retry_count": 0}
                save_user_state(wa_id, new_state)
                send_q1(wa_id)
            else:
                send_whatsapp_message(
                    wa_id,
                    f"❌ Please tap 'Book Now' or 'More Details'.\n\n"
                    f"(Attempt {retry_count} of 3)"
                )
            
            return jsonify({"status": "ok", "step": "invalid_q4_option"})

        answers["q4"] = user_choice
        state["answers"] = answers
        state["q_status"] = 5
        state["retry_count"] = 0
        save_user_state(wa_id, state)
        send_q5(wa_id)
        return jsonify({"status": "ok", "step": "sent_q5"})

    # Q5: Contact details
    if q_status == 5:
        if not incoming_text:
            send_whatsapp_message(
                wa_id,
                "Please share your contact details:\n\n"
                "Format: Your Name, Phone Number, Preferred time\n"
                "Example: John Doe, 9876543210, 3 PM today"
            )
            return jsonify({"status": "ok", "step": "repeat_q5_empty"})
        
        is_valid, error_msg = validate_contact_details(incoming_text)
        if not is_valid:
            retry_count += 1
            state["retry_count"] = retry_count
            save_user_state(wa_id, state)
            
            if retry_count >= 3:
                reset_user_state(wa_id)
                send_whatsapp_message(wa_id, "🤔 Let's start over from the beginning.")
                time.sleep(1)
                new_state = {"q_status": 1, "answers": {}, "retry_count": 0}
                save_user_state(wa_id, new_state)
                send_q1(wa_id)
            else:
                send_whatsapp_message(
                    wa_id,
                    f"❌ {error_msg}\n\n"
                    "Please provide:\n"
                    "• Your full name\n"
                    "• 10-digit phone number\n"
                    "• Preferred call time\n\n"
                    "Example: John Doe, 9876543210, 3 PM today"
                )
            
            return jsonify({"status": "ok", "step": "invalid_q5_format"})

        answers["q5"] = incoming_text
        state["answers"] = answers
        state["q_status"] = 6
        save_user_state(wa_id, state)
        complete_lead(wa_id, state)
        return jsonify({"status": "ok", "step": "completed_lead"})

    # Fallback
    reset_user_state(wa_id)
    new_state = {"q_status": 1, "answers": {}, "retry_count": 0}
    save_user_state(wa_id, new_state)
    send_q1(wa_id)
    return jsonify({"status": "ok", "step": "reset_and_sent_q1"})


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
        "fallback_mode": not REDIS_AVAILABLE
    })


@app.errorhandler(429)
def ratelimit_handler(e):
    """Custom handler for rate limit exceeded"""
    logger.warning(f"Rate limit exceeded: {e.description}")
    return jsonify({
        "error": "rate_limit_exceeded",
        "message": "Too many requests. Please try again later."
    }), 429


if __name__ == "__main__":
    logger.info(f"🚀 ReplyFast Auto starting on port {PORT}")
    logger.info(f"📱 Meta WhatsApp API: {'✅ Configured' if META_API_TOKEN and META_PHONE_ID else '❌ Not Configured'}")
    logger.info(f"📊 Google Sheets: {'✅ Configured' if SHEET_KEY else '❌ Not Configured'}")
    
    redis_status = "❌ Not Connected (Using memory fallback)"
    if REDIS_AVAILABLE:
        try:
            if r.ping():
                redis_status = "✅ Connected"
        except:
            redis_status = "❌ Not Connected"
    
    logger.info(f"🔴 Redis: {redis_status}")
    
    app.run(host="0.0.0.0", port=PORT, debug=True)
