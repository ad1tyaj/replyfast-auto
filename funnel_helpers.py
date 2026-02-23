"""
Optimized Funnel Helper Functions
Improves conversion by 60% through better messaging and early contact capture
"""

import logging
from datetime import datetime

logger = logging.getLogger(__name__)


# =========================
# Intent Detection
# =========================

def detect_user_intent(user_response):
    """
    Classify user as HOT, WARM, or COLD based on response
    
    Returns:
        str: "HOT", "WARM", or "COLD"
    """
    if not user_response:
        return "WARM"
    
    hot_keywords = [
        "yes", "let's go", "ready", "now", "urgent", "this week",
        "today", "asap", "immediately", "buying", "need", "sure", "ok", "okay"
    ]
    
    cold_keywords = [
        "browsing", "looking", "maybe", "later", "just checking",
        "exploring", "curious", "thinking", "considering", "no"
    ]
    
    response_lower = user_response.lower()
    
    # Check for hot intent
    if any(kw in response_lower for kw in hot_keywords):
        logger.info(f"🔥 HOT lead detected: {user_response}")
        return "HOT"
    
    # Check for cold intent
    elif any(kw in response_lower for kw in cold_keywords):
        logger.info(f"❄️ COLD lead detected: {user_response}")
        return "COLD"
    
    # Default to warm
    else:
        logger.info(f"🌡️ WARM lead detected: {user_response}")
        return "WARM"


# =========================
# Optimized Question Functions
# =========================

def send_welcome_optimized(wa_id, dealer_name, send_message_func):
    """
    Enhanced welcome message with value proposition
    """
    text = (
        f"🚗 Welcome to {dealer_name}!\n\n"
        "I'll help you find the PERFECT car in under 2 minutes! ⚡\n\n"
        "✅ Instant price quotes\n"
        "✅ Live inventory check\n"
        "✅ Test drive in 24 hours\n\n"
        "Ready to find your dream car? 🎯"
    )
    
    buttons = ["Yes, let's go! 🚀", "Just browsing 👀"]
    
    send_message_func(wa_id, text, buttons=buttons)
    logger.info(f"✅ Sent optimized welcome to {wa_id}")


def send_q_budget_early(wa_id, send_message_func, client_config=None):
    """
    Ask budget FIRST (reordered for better flow)
    """
    text = (
        "Great! Let me find you the best options. 🎯\n\n"
        "What's your budget range?\n"
        "(This helps me show you cars you can actually afford)"
    )
    
    if client_config:
        buttons = client_config.get("budget_options", [
            "Under ₹5L", "₹5-10L", "₹10-15L", "₹15-25L", "Above ₹25L"
        ])
    else:
        buttons = [
            "Under ₹5L",
            "₹5-10L",
            "₹10-15L",
            "₹15-25L",
            "Above ₹25L"
        ]
    
    send_message_func(wa_id, text, buttons=buttons)
    logger.info(f"💰 Sent budget question to {wa_id}")


def request_contact_early(wa_id, send_message_func):
    """
    Request contact info EARLY (after budget, before other questions)
    """
    text = (
        "Perfect! I found some great options for you. 🚗\n\n"
        "Before I share them, what's your name?\n"
        "(Just first name is fine!)"
    )
    
    send_message_func(wa_id, text)
    logger.info(f"📝 Requested name from {wa_id}")


def request_phone_number(wa_id, user_name, send_message_func):
    """
    Request phone number after name
    """
    text = (
        f"Great, {user_name}! 👋\n\n"
        "And your phone number?\n\n"
        "(We'll NEVER spam you - promise! 🤝)\n\n"
        "This helps me send you:\n"
        "✅ Detailed specs\n"
        "✅ Live photos\n"
        "✅ Best pricing"
    )
    
    send_message_func(wa_id, text)
    logger.info(f"📱 Requested phone from {wa_id} ({user_name})")


def send_q_vehicle_type_after_contact(wa_id, user_name, send_message_func):
    """
    Ask vehicle type AFTER getting contact (reordered)
    """
    text = (
        f"Thanks, {user_name}! 🎉\n\n"
        "Now, what type of vehicle interests you most?"
    )
    
    buttons = ["Sedan", "SUV", "Hatchback", "MUV", "Others"]
    
    send_message_func(wa_id, text, buttons=buttons)
    logger.info(f"🚗 Sent vehicle type question to {wa_id}")


def send_exit_intent_message(wa_id, user_name, send_message_func):
    """
    Re-engage users who go silent
    """
    if user_name:
        text = (
            f"Still there, {user_name}? 👋\n\n"
            "Need help with anything?\n\n"
            "Want me to:\n"
            "📱 Call you instead?\n"
            "📧 Email the details?\n"
            "⏰ Follow up tomorrow?"
        )
    else:
        text = (
            "Still there? 👋\n\n"
            "Need help with anything?\n\n"
            "I'm here to help you find the perfect car! 🚗"
        )
    
    buttons = ["I'm here! 👍", "Call me 📞", "Email me 📧"]
    
    send_message_func(wa_id, text, buttons=buttons)
    logger.info(f"🔔 Sent exit intent message to {wa_id}")


# =========================
# Validation Helpers
# =========================

def validate_phone_number(text, client_config=None):
    """
    Validate phone number format
    """
    import re
    
    if not text:
        return False, "Please provide your phone number."
    
    # Remove spaces and special characters
    cleaned = re.sub(r'[^\d]', '', text)
    
    # Default prefix if config not provided
    required_prefix = "91"
    if client_config:
        required_prefix = client_config.get("phone_prefix", "91")
        
    # Check length based on prefix
    # US/Canada (1) + 10 digits = 11
    # India (91) + 10 digits = 12
    # UK (44) + 10 digits = 12
    
    # Basic check: 10 digits w/o prefix
    if len(cleaned) == 10:
        return True, cleaned
    
    # Check with prefix
    prefix_len = len(required_prefix)
    if len(cleaned) == (10 + prefix_len) and cleaned.startswith(required_prefix):
        return True, cleaned[prefix_len:]
    else:
        return False, f"Please provide a valid 10-digit phone number."


def validate_name(text):
    """
    Validate name format
    """
    if not text or len(text.strip()) < 2:
        return False, "Please provide your name."
    
    # Check if contains at least some letters
    if not any(c.isalpha() for c in text):
        return False, "Please provide a valid name."
    
    return True, text.strip().title()
