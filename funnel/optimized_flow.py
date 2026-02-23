"""
Optimized Lead Capture Funnel - Implementation
Conversion-optimized flow with psychological triggers
"""

from datetime import datetime
import logging

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
    hot_keywords = [
        "yes", "let's go", "ready", "now", "urgent", "this week",
        "today", "asap", "immediately", "buying", "need"
    ]
    
    cold_keywords = [
        "browsing", "looking", "maybe", "later", "just checking",
        "exploring", "curious", "thinking", "considering"
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
# Welcome Messages
# =========================

def send_welcome_value_prop(wa_id, dealer_name, send_message_func):
    """
    Enhanced welcome message with value proposition
    
    Args:
        wa_id: WhatsApp ID
        dealer_name: Name of the dealership
        send_message_func: Function to send WhatsApp message
    """
    text = (
        f"🚗 Welcome to {dealer_name}! \n\n"
        "I'm your AI car assistant - I'll help you find the PERFECT car in under 2 minutes! ⚡\n\n"
        "✅ Instant price quotes\n"
        "✅ Live inventory check\n"
        "✅ Exclusive WhatsApp-only deals\n"
        "✅ Test drive in 24 hours\n\n"
        "Ready to find your dream car? 🎯"
    )
    
    buttons = ["Yes, let's go! 🚀", "Just browsing 👀"]
    
    send_message_func(wa_id, text, buttons=buttons)
    logger.info(f"✅ Sent value-prop welcome to {wa_id}")


def send_welcome_simple(wa_id, dealer_name, send_message_func):
    """
    Simple welcome message (for A/B testing)
    
    Args:
        wa_id: WhatsApp ID
        dealer_name: Name of the dealership
        send_message_func: Function to send WhatsApp message
    """
    text = (
        f"🚗 Welcome to {dealer_name}! \n\n"
        "I'm here to help you find your perfect car.\n\n"
        "Ready to start?"
    )
    
    buttons = ["Yes", "No"]
    
    send_message_func(wa_id, text, buttons=buttons)
    logger.info(f"✅ Sent simple welcome to {wa_id}")


# =========================
# Early Contact Capture
# =========================

def request_name(wa_id, send_message_func):
    """
    Request user's name (first micro-commitment)
    """
    text = (
        "Perfect! I found some great options for you 🎯\n\n"
        "Before I share them, let me personalize your experience.\n\n"
        "What's your name? (Just first name is fine!)"
    )
    
    send_message_func(wa_id, text)
    logger.info(f"📝 Requested name from {wa_id}")


def request_phone(wa_id, user_name, send_message_func):
    """
    Request user's phone number (after name)
    
    Args:
        wa_id: WhatsApp ID
        user_name: User's first name
        send_message_func: Function to send WhatsApp message
    """
    text = (
        f"Great, {user_name}! 👋\n\n"
        "And your phone number?\n\n"
        "(We'll NEVER spam you - promise! 🤝)\n\n"
        "This helps me send you:\n"
        "✅ Detailed specs\n"
        "✅ Live photos\n"
        "✅ Special pricing (not public!)"
    )
    
    send_message_func(wa_id, text)
    logger.info(f"📱 Requested phone from {wa_id} ({user_name})")


# =========================
# HOT Lead Path (Fast-Track)
# =========================

def send_hot_lead_budget_question(wa_id, send_message_func):
    """
    Fast-track budget question for hot leads
    """
    text = (
        "Awesome! 🎉 Let me find you the best options.\n\n"
        "First, what's your budget range?\n"
        "This helps me show you cars you can actually afford 💰"
    )
    
    buttons = [
        "Under ₹5L",
        "₹5-10L",
        "₹10-15L",
        "₹15-25L",
        "Above ₹25L"
    ]
    
    send_message_func(wa_id, text, buttons=buttons)
    logger.info(f"🔥 Sent HOT lead budget question to {wa_id}")


def send_hot_lead_urgency(wa_id, user_name, car_model, send_message_func):
    """
    Create urgency for hot leads
    """
    text = (
        f"🔥 GREAT NEWS, {user_name}!\n\n"
        f"We have {car_model} in stock RIGHT NOW!\n\n"
        "✅ Test drive available TODAY\n"
        "✅ Special financing (approved in 30 mins)\n"
        "✅ ₹10,000 OFF if you book this week!\n\n"
        "⚠️ Only 2 units left in this color\n\n"
        "Want to book your test drive now?"
    )
    
    buttons = ["Yes! Book now 🚀", "Tell me more first"]
    
    send_message_func(wa_id, text, buttons=buttons)
    logger.info(f"⚡ Sent urgency message to HOT lead {wa_id}")


# =========================
# COLD Lead Path (Nurture)
# =========================

def send_cold_lead_value_offer(wa_id, send_message_func):
    """
    Nurture cold leads with value offer
    """
    text = (
        "No problem! 😊 Let me share something cool with you.\n\n"
        "🎁 SPECIAL OFFER THIS WEEK:\n\n"
        "✅ Get ₹10,000 OFF + Free accessories\n"
        "✅ 0% interest for 6 months\n"
        "✅ Free insurance for 1 year\n\n"
        "⏰ Valid only for next 3 days!\n\n"
        "Want to see which cars qualify? It's free to check! 👇"
    )
    
    buttons = ["Show me the offer 🎁", "Maybe later"]
    
    send_message_func(wa_id, text, buttons=buttons)
    logger.info(f"❄️ Sent value offer to COLD lead {wa_id}")


def send_cold_lead_soft_capture(wa_id, send_message_func):
    """
    Soft contact capture for cold leads
    """
    text = (
        "Want me to send you these deals on WhatsApp? 📱\n\n"
        "I'll send you:\n"
        "📸 Car photos\n"
        "💰 Exact pricing\n"
        "🎁 Exclusive offers\n\n"
        "Just share your number and I'll send them now!"
    )
    
    send_message_func(wa_id, text)
    logger.info(f"📱 Soft contact capture for COLD lead {wa_id}")


# =========================
# Personalized Recommendations
# =========================

def send_personalized_recommendations(wa_id, user_data, send_message_func):
    """
    Send personalized car recommendations with social proof
    
    Args:
        wa_id: WhatsApp ID
        user_data: Dict with user preferences (budget, vehicle_type, etc.)
        send_message_func: Function to send WhatsApp message
    """
    user_name = user_data.get('name', 'there')
    budget = user_data.get('budget', 'your budget')
    
    text = (
        f"Based on your answers, {user_name}, here are your TOP 3 matches: 🎯\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "🚗 **Option 1: Maruti Swift VXi**\n"
        "💰 ₹7.8 Lakhs (₹10K off this week!)\n"
        "⭐ 4.8/5 rating (523 reviews)\n"
        "🔥 Only 2 left in red!\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "Want to see:\n"
        "📸 Live photos?\n"
        "📊 Detailed comparison?\n"
        "🎥 360° virtual tour?\n\n"
        "Or ready to book test drive?"
    )
    
    buttons = [
        "I like this! ❤️",
        "Show next →",
        "Book test drive 🚗"
    ]
    
    send_message_func(wa_id, text, buttons=buttons)
    logger.info(f"🎯 Sent personalized recommendations to {wa_id}")


# =========================
# Urgency & Scarcity Triggers
# =========================

def add_urgency_trigger(car_model, inventory_count):
    """
    Add scarcity/urgency messaging based on inventory
    
    Args:
        car_model: Name of the car model
        inventory_count: Number of units in stock
        
    Returns:
        str: Urgency message
    """
    if inventory_count <= 3:
        return f"⚠️ Only {inventory_count} left! Act fast!"
    
    # Weekend special
    if datetime.now().weekday() >= 5:
        return "🎁 Weekend special: Extra ₹5K off!"
    
    # Month-end clearance
    if datetime.now().day >= 25:
        return "🔥 Month-end clearance: Best prices!"
    
    # Festival season (Oct-Nov)
    if datetime.now().month in [10, 11]:
        return "🎊 Festival offer: ₹15K OFF + Free gifts!"
    
    return ""


def add_social_proof(car_model):
    """
    Add social proof elements
    
    Args:
        car_model: Name of the car model
        
    Returns:
        str: Social proof message
    """
    social_proof_options = [
        "⭐ 4.8/5 rating from 523 buyers",
        "🏆 #1 selling car in this segment",
        "💬 'Best decision ever!' - Priya, Mumbai",
        "📊 95% customers recommend this model",
        "🎯 1,247 happy customers this year"
    ]
    
    # Return random social proof (in production, use actual data)
    import random
    return random.choice(social_proof_options)


# =========================
# Exit Intent Detection
# =========================

def handle_exit_intent(wa_id, user_name, send_message_func):
    """
    Re-engage users who go silent (2+ minutes)
    
    Args:
        wa_id: WhatsApp ID
        user_name: User's first name
        send_message_func: Function to send WhatsApp message
    """
    text = (
        f"Still there, {user_name}? 👋\n\n"
        "I noticed you stopped. Need help with anything?\n\n"
        "Or want me to:\n"
        "📱 Call you instead?\n"
        "📧 Email the details?\n"
        "⏰ Follow up tomorrow?\n\n"
        "Just let me know! 😊"
    )
    
    buttons = [
        "Call me 📞",
        "Email details 📧",
        "I'm still here! 👍"
    ]
    
    send_message_func(wa_id, text, buttons=buttons)
    logger.info(f"🔔 Sent exit intent message to {wa_id}")


# =========================
# Appointment Booking
# =========================

def send_appointment_confirmation(wa_id, user_data, send_message_func):
    """
    Confirm test drive appointment
    
    Args:
        wa_id: WhatsApp ID
        user_data: Dict with user info and preferences
        send_message_func: Function to send WhatsApp message
    """
    user_name = user_data.get('name', 'there')
    car_model = user_data.get('vehicle_type', 'your selected car')
    preferred_time = user_data.get('preferred_time', 'your preferred time')
    
    text = (
        f"Perfect choice, {user_name}! 🎉\n\n"
        "Here's what happens next:\n\n"
        f"✅ I've reserved the {car_model} for you (24 hrs)\n"
        f"✅ Test drive slot: {preferred_time}\n"
        "✅ Location: [Showroom Address]\n"
        "✅ Sales rep will call you in 10 mins\n\n"
        "Confirm your visit?"
    )
    
    buttons = [
        "Confirmed! ✅",
        "Change time 🕐",
        "I'll think about it 🤔"
    ]
    
    send_message_func(wa_id, text, buttons=buttons)
    logger.info(f"📅 Sent appointment confirmation to {wa_id}")


def handle_appointment_hesitation(wa_id, user_name, send_message_func):
    """
    Handle users who say "I'll think about it"
    
    Args:
        wa_id: WhatsApp ID
        user_name: User's first name
        send_message_func: Function to send WhatsApp message
    """
    text = (
        f"No problem, {user_name}! 😊\n\n"
        "I'll send you:\n"
        "📱 Car details on WhatsApp\n"
        "📧 Email with full specs\n"
        "⏰ Reminder in 2 days\n\n"
        "Meanwhile, any questions I can answer?"
    )
    
    buttons = [
        "Ask a question 💬",
        "All good, thanks! 👍"
    ]
    
    send_message_func(wa_id, text, buttons=buttons)
    logger.info(f"🤔 Handled appointment hesitation for {wa_id}")


# =========================
# Funnel Analytics
# =========================

def track_funnel_stage(wa_id, stage_name, session_data=None):
    """
    Track user progress through funnel for analytics
    
    Args:
        wa_id: WhatsApp ID
        stage_name: Name of the funnel stage
        session_data: Optional session metadata
    """
    analytics_event = {
        'wa_id': wa_id,
        'stage': stage_name,
        'timestamp': datetime.utcnow().isoformat(),
        'session_data': session_data or {}
    }
    
    logger.info(f"📊 Funnel tracking: {wa_id} reached {stage_name}")
    
    # In production, send to analytics service (Google Analytics, Mixpanel, etc.)
    # analytics_service.track(analytics_event)
    
    return analytics_event


# =========================
# A/B Testing
# =========================

def get_ab_test_variant(wa_id, test_name):
    """
    Determine which A/B test variant to show
    
    Args:
        wa_id: WhatsApp ID
        test_name: Name of the A/B test
        
    Returns:
        str: "A" or "B"
    """
    # Simple hash-based assignment (consistent per user)
    hash_value = hash(f"{wa_id}_{test_name}")
    
    if hash_value % 2 == 0:
        variant = "A"
    else:
        variant = "B"
    
    logger.info(f"🧪 A/B Test '{test_name}': {wa_id} assigned to variant {variant}")
    
    return variant


def send_welcome_ab_test(wa_id, dealer_name, send_message_func):
    """
    A/B test welcome messages
    
    Variant A: Simple welcome
    Variant B: Value proposition welcome
    """
    variant = get_ab_test_variant(wa_id, "welcome_message")
    
    if variant == "A":
        send_welcome_simple(wa_id, dealer_name, send_message_func)
    else:
        send_welcome_value_prop(wa_id, dealer_name, send_message_func)
    
    track_funnel_stage(wa_id, "welcome_sent", {"ab_variant": variant})
