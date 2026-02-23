"""
Clinic Appointment Funnel
==========================
Industry-specific question flow for clinics (dental, dermatology,
general practice, physio, salons, etc.)

State machine — same q_status steps as the car funnel:
  0      → Initial (trigger welcome)
  0.5    → Awaiting service choice
  1      → Awaiting preferred date
  2      → Awaiting name
  3      → Awaiting phone
  7      → Awaiting appointment confirmation time (reuses appointment_scheduler)
  99     → Human handoff

HOT  = Today / Tomorrow booking
WARM = This Week
COLD = Just asking / exploring

Plugs into app.py via get_funnel_for_industry("clinic").
All infrastructure (reminders, escalation, Redis) unchanged.
"""

import logging

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Default service menus per sub-industry
# Clinic clients can override this in clients.json via "services" field
# ---------------------------------------------------------------------------

DEFAULT_SERVICES = {
    "dental":        ["Dental Cleaning", "Tooth Pain / Emergency", "Braces / Aligners", "Root Canal", "Whitening"],
    "dermatology":   ["Acne Treatment", "HydraFacial", "Laser Hair Removal", "Skin Consultation", "Anti-Aging"],
    "general":       ["General Consultation", "Follow-up Visit", "Lab Tests", "Vaccination", "Health Checkup"],
    "physio":        ["Pain Assessment", "Sports Injury", "Post-Surgery Rehab", "Back / Neck Pain", "Other"],
    "salon":         ["Haircut & Style", "Bridal Package", "Hair Color", "Facial / Cleanup", "Other"],
    "clinic":        ["General Consultation", "Dental Cleaning", "Skin Treatment", "Follow-up", "Other"],
}

DATE_OPTIONS = ["Today 📅", "Tomorrow 📅", "This Week 📅", "Another Date"]

# HOT/WARM scoring by date choice
DATE_SCORE = {
    "today":        "HOT",
    "today 📅":     "HOT",
    "tomorrow":     "HOT",
    "tomorrow 📅":  "HOT",
    "this week":    "WARM",
    "this week 📅": "WARM",
    "another date": "WARM",
}


# ---------------------------------------------------------------------------
# Intent detection — clinic variant
# ---------------------------------------------------------------------------

def detect_clinic_intent(user_response):
    """
    Classify user urgency:
      HOT  = wants appointment today/tomorrow
      WARM = this week / general interest
      COLD = just asking / browsing
    """
    normalized = user_response.lower().strip()

    cold_keywords = [
        "just asking", "just checking", "maybe", "later",
        "not sure", "exploring", "curious", "thinking", "eventually"
    ]

    hot_keywords = [
        "urgent", "pain", "emergency", "hurts", "bleeding",
        "asap", "today", "tomorrow", "immediately", "now"
    ]

    if any(kw in normalized for kw in hot_keywords):
        logger.info(f"🔥 [CLINIC] HOT intent: {user_response}")
        return "HOT"
    elif any(kw in normalized for kw in cold_keywords):
        logger.info(f"❄️ [CLINIC] COLD intent: {user_response}")
        return "COLD"
    else:
        logger.info(f"🌡️ [CLINIC] WARM intent: {user_response}")
        return "WARM"


def score_date_choice(date_text):
    """Return HOT/WARM/COLD based on the date preference button tapped."""
    return DATE_SCORE.get(date_text.lower().strip(), "WARM")


# ---------------------------------------------------------------------------
# Step 0: Welcome
# ---------------------------------------------------------------------------

def send_clinic_welcome(wa_id, clinic_name, send_msg_fn, sub_industry="clinic"):
    """
    First message the patient sees. Warm, professional, simple.
    """
    industry_emoji = {
        "dental":      "🦷",
        "dermatology": "💆",
        "physio":      "🏥",
        "salon":       "💇",
        "general":     "🏥",
        "clinic":      "👨‍⚕️",
    }.get(sub_industry, "👋")

    text = (
        f"{industry_emoji} *Welcome to {clinic_name}!*\n\n"
        "Would you like to book an appointment?\n\n"
        "✅ Instant confirmation\n"
        "✅ Reminder before your visit\n"
        "✅ Easy reschedule anytime"
    )

    send_msg_fn(wa_id, text, buttons=["📅 Book Appointment", "❓ Ask a Question"])
    logger.info(f"✅ [CLINIC WELCOME] Sent to {wa_id} | {clinic_name}")


# ---------------------------------------------------------------------------
# Step 0.5: Service Selection
# ---------------------------------------------------------------------------

def send_service_selection(wa_id, clinic_name, send_msg_fn,
                            sub_industry="clinic", custom_services=None):
    """
    Show service menu. Uses custom_services from clients.json if set,
    otherwise falls back to DEFAULT_SERVICES for the sub_industry.
    """
    services = custom_services or DEFAULT_SERVICES.get(sub_industry, DEFAULT_SERVICES["clinic"])

    text = (
        f"Great! What service do you need at *{clinic_name}*?\n\n"
        "Please select one below 👇"
    )

    send_msg_fn(wa_id, text, buttons=services[:5])  # WhatsApp button limit = 3 (will use list for >3)
    logger.info(f"🩺 [CLINIC SERVICE MENU] Sent to {wa_id} | {len(services)} options")


# ---------------------------------------------------------------------------
# Step 1: Preferred Date
# ---------------------------------------------------------------------------

def send_date_selection(wa_id, service, send_msg_fn):
    """
    Ask when the patient wants to come in.
    """
    text = (
        f"Perfect! *{service}* — when would you like to come in?\n\n"
        "Choose your preferred time 👇"
    )

    send_msg_fn(wa_id, text, buttons=DATE_OPTIONS)
    logger.info(f"📅 [CLINIC DATE] Sent date options to {wa_id}")


# ---------------------------------------------------------------------------
# Step 2: Capture Name
# ---------------------------------------------------------------------------

def request_patient_name(wa_id, send_msg_fn):
    """
    Ask for the patient's name — first micro-commitment.
    """
    text = (
        "Almost done! Just a couple of quick details.\n\n"
        "What's your *full name*? 📝"
    )

    send_msg_fn(wa_id, text)
    logger.info(f"📝 [CLINIC NAME] Requested name from {wa_id}")


# ---------------------------------------------------------------------------
# Step 3: Capture Phone
# ---------------------------------------------------------------------------

def request_patient_phone(wa_id, patient_name, send_msg_fn):
    """
    Ask for phone number. Reassures no spam.
    """
    text = (
        f"Thank you, *{patient_name}*! 👋\n\n"
        "And your *phone number*?\n\n"
        "We'll use it to:\n"
        "✅ Send your appointment confirmation\n"
        "✅ Remind you before your visit\n"
        "✅ Contact you if anything changes\n\n"
        "_(We never share your number)_ 🔒"
    )

    send_msg_fn(wa_id, text)
    logger.info(f"📱 [CLINIC PHONE] Requested phone from {wa_id} ({patient_name})")


# ---------------------------------------------------------------------------
# Completion: Confirmation message + handoff
# ---------------------------------------------------------------------------

def send_clinic_booking_summary(wa_id, patient_name, service, date_text,
                                 clinic_name, send_msg_fn, maps_link=""):
    """
    Sent after name + phone captured. Summarises what was booked
    and sets expectation for confirmation call.
    """
    maps_text = f"\n\n📍 *Location:* {maps_link}" if maps_link else ""

    text = (
        f"✅ *Booking Request Received!*\n\n"
        f"👤 *Name:* {patient_name}\n"
        f"🩺 *Service:* {service}\n"
        f"📅 *Preferred:* {date_text}"
        f"{maps_text}\n\n"
        f"Our team at *{clinic_name}* will confirm your exact time shortly.\n\n"
        f"We'll send you a reminder before your visit! 🔔"
    )

    send_msg_fn(wa_id, text)
    logger.info(f"✅ [CLINIC BOOKING SUMMARY] Sent to {wa_id} | {service} | {date_text}")


def send_cold_clinic_response(wa_id, clinic_name, send_msg_fn):
    """
    For patients who are just asking questions, not booking.
    """
    text = (
        f"No problem! Feel free to ask any questions you have about *{clinic_name}*.\n\n"
        "What would you like to know? 😊"
    )
    send_msg_fn(wa_id, text)


# ---------------------------------------------------------------------------
# Clinic notification template (for clinic staff)
# ---------------------------------------------------------------------------

def build_clinic_staff_notification(lead_data, client_config):
    """
    Generates the WhatsApp alert message sent to clinic staff
    when a new appointment request comes in.

    Replaces the car dealer "HOT Lead" notification for clinics.
    """
    name     = lead_data.get("customer_name", "Patient")
    phone    = lead_data.get("phone_number", "N/A")
    service  = lead_data.get("service", "N/A")
    date     = lead_data.get("preferred_date", "N/A")
    score    = lead_data.get("lead_score", "WARM")
    wa_id    = lead_data.get("wa_id", "")

    score_emoji = {"HOT": "🔥", "WARM": "🌡️", "COLD": "❄️"}.get(score, "📋")
    urgency_tag = {
        "HOT":  "⚡ *URGENT — Wants appointment TODAY / TOMORROW*",
        "WARM": "📅 Wants appointment this week",
        "COLD": "💬 Just enquiring",
    }.get(score, "")

    return (
        f"{score_emoji} *NEW APPOINTMENT REQUEST*\n\n"
        f"👤 *Patient:* {name}\n"
        f"📱 *Phone:* {phone}\n"
        f"🩺 *Service:* {service}\n"
        f"📅 *Preferred:* {date}\n\n"
        f"{urgency_tag}\n\n"
        f"💬 Reply to patient: wa.me/{wa_id}"
    )


# ---------------------------------------------------------------------------
# Weekly summary builder
# ---------------------------------------------------------------------------

def build_weekly_summary(stats, clinic_name):
    """
    Build the weekly WhatsApp report for clinic staff.

    stats dict should include:
      bookings_count, hot_count, warm_count,
      show_rate_pct, reminders_sent
    """
    bookings  = stats.get("bookings_count", 0)
    hot       = stats.get("hot_count", 0)
    show_rate = stats.get("show_rate_pct", 0.0)
    reminders = stats.get("reminders_sent", 0)

    return (
        f"📊 *Weekly Report — {clinic_name}*\n\n"
        f"📅 Appointments booked: *{bookings}*\n"
        f"🔥 Urgent (Today/Tomorrow): *{hot}*\n"
        f"✅ Estimated show rate: *{show_rate}%*\n"
        f"🔔 Reminders sent: *{reminders}*\n\n"
        f"Powered by ReplyFast 🚀"
    )
