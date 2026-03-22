"""
Appointment Show-Rate Engine — P1
==================================
Manages the full appointment lifecycle for dealers:

  • book_appointment()         — Store appointment + send instant confirmation
  • check_and_send_reminders() — Fires 24h / 2h / 30min WhatsApp reminders
  • detect_reschedule_intent() — Catches "can't come" style messages
  • confirm_showed()           — Mark customer as showed up
  • confirm_noshow()           — Mark customer as no-show
  • get_show_rate()            — Returns show-rate % per dealer

Redis Keys:
  appt:{wa_id}                → Hash  — appointment details
  appt_schedule               → ZSet  — score = appointment Unix timestamp
  showrate:{dealer_wa}        → Hash  — {total, showed, noshow}

Works with the same APScheduler + Redis infra as follow_up_scheduler.py.
Gracefully handles Redis unavailability (logs, skips).
"""

import json
import logging
import time
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Redis key constants
# ---------------------------------------------------------------------------
APPT_KEY_PREFIX     = "appt:"           # Hash per user appointment
APPT_SCHEDULE_KEY   = "appt_schedule"   # ZSet — score = appt Unix timestamp
SHOWRATE_KEY_PREFIX = "showrate:"       # Hash per dealer — total/showed/noshow
SLOT_LOCK_PREFIX    = "slot_lock:"      # String key — prevents double-booking
SLOT_LOCK_TTL       = 300              # Lock expires after 5 min (covers booking flow)

# ---------------------------------------------------------------------------
# Reminder timing (seconds before appointment)
# ---------------------------------------------------------------------------
REMINDER_24H    = 24 * 60 * 60   # 86400 s
REMINDER_2H     = 2  * 60 * 60   # 7200 s
REMINDER_30MIN  = 30 * 60         # 1800 s
SCHEDULER_INTERVAL = 5 * 60       # Check every 5 minutes

# ---------------------------------------------------------------------------
# Reschedule intent keywords
# ---------------------------------------------------------------------------
RESCHEDULE_KEYWORDS = [
    "can't come", "cant come", "cannot come", "can't make it", "cant make it",
    "reschedule", "postpone", "not today", "change time", "change date",
    "something came up", "busy", "cancel", "won't make it", "wont make it",
    "delay", "not available", "unavailable"
]


# ---------------------------------------------------------------------------
# Slot Availability Helpers
# ---------------------------------------------------------------------------

def _slot_key(dealer_wa, appt_dt):
    """Canonical Redis key for a dealer's time slot (rounded to 30-min blocks)."""
    # Round down to nearest 30 min so 10:00 and 10:15 share the same slot
    slot_minute = (appt_dt.minute // 30) * 30
    slot_str = appt_dt.strftime(f"%Y%m%d_%H{slot_minute:02d}")
    return f"{SLOT_LOCK_PREFIX}{dealer_wa}:{slot_str}"


def is_slot_available(dealer_wa, appt_dt, redis_client, slot_capacity=1):
    """
    Returns True if the slot still has room, False if at capacity.
    Call this BEFORE showing the user a time option.

    slot_capacity=1  → clinic/doctor (one patient per slot)
    slot_capacity=3  → car dealer   (multiple salespeople)
    """
    if not redis_client:
        return True  # Redis down — optimistically allow
    try:
        count = redis_client.get(_slot_key(dealer_wa, appt_dt))
        current = int(count) if count else 0
        return current < slot_capacity
    except Exception as e:
        logger.error(f"❌ [SLOT CHECK ERROR] {e}")
        return True  # Fail open


def _acquire_slot_lock(dealer_wa, appt_dt, wa_id, redis_client, slot_capacity=1):
    """
    Atomically increments the slot counter and checks against capacity.
    Returns True if slot was successfully claimed, False if slot is full.

    Uses INCR (atomic) then compares to capacity — if over capacity,
    decrements back so the count stays accurate.
    """
    if not redis_client:
        return True  # Redis down — allow booking
    try:
        key = _slot_key(dealer_wa, appt_dt)
        # Atomically increment
        new_count = redis_client.incr(key)
        # Set TTL on first booking
        if new_count == 1:
            redis_client.expire(key, SLOT_LOCK_TTL)

        if new_count <= slot_capacity:
            logger.debug(f"🔐 [SLOT] {key} = {new_count}/{slot_capacity} claimed")
            return True
        else:
            # Over capacity — roll back
            redis_client.decr(key)
            logger.debug(f"🚫 [SLOT FULL] {key} at capacity {slot_capacity}")
            return False
    except Exception as e:
        logger.error(f"❌ [SLOT LOCK ERROR] {e}")
        return True  # Fail open


def release_slot_lock(dealer_wa, appt_dt, redis_client):
    """
    Decrements the slot counter by 1 when a booking is cancelled/rescheduled.
    Removes the key entirely if count reaches 0.
    """
    if not redis_client:
        return
    try:
        key = _slot_key(dealer_wa, appt_dt)
        remaining = redis_client.decr(key)
        if remaining <= 0:
            redis_client.delete(key)
    except Exception as e:
        logger.error(f"❌ [SLOT RELEASE ERROR] {e}")


# ---------------------------------------------------------------------------
# Core: Book Appointment
# ---------------------------------------------------------------------------

def book_appointment(wa_id, name, phone, model, appt_dt, dealer_wa,
                     maps_link, send_msg_fn, redis_client, dealer_name="AutoDealer",
                     slot_capacity=1):
    """
    Book a test drive appointment and send instant WhatsApp confirmation.

    Parameters
    ----------
    wa_id        : Customer WhatsApp ID
    name         : Customer name
    phone        : Customer phone number
    model        : Car model they're interested in (e.g. "Toyota Camry")
    appt_dt      : datetime object of the appointment
    dealer_wa    : Dealer's WhatsApp number (for show-rate tracking)
    maps_link    : Google Maps URL for showroom
    send_msg_fn  : app.send_whatsapp_message reference
    redis_client : redis.StrictRedis instance (or None)
    dealer_name  : Dealership display name
    """
    appt_ts = appt_dt.timestamp()
    appt_key = f"{APPT_KEY_PREFIX}{wa_id}"

    appt_data = {
        "wa_id":              wa_id,
        "name":               name,
        "phone":              phone,
        "model":              model,
        "appt_ts":            str(appt_ts),
        "dealer_wa":          dealer_wa,
        "dealer_name":        dealer_name,
        "maps_link":          maps_link or "",
        "status":             "booked",
        "reminder_24h_sent":  "0",
        "reminder_2h_sent":   "0",
        "reminder_30min_sent": "0",
        "booked_at":          str(time.time()),
    }

    if redis_client:
        try:
            # ── Capacity-aware slot lock — prevents overbooking ─────────────
            if not _acquire_slot_lock(dealer_wa, appt_dt, wa_id, redis_client, slot_capacity):
                logger.warning(
                    f"🚫 [APPT SLOT FULL] {name} ({wa_id}) tried to book "
                    f"{appt_dt.strftime('%d %b %Y %H:%M')} — slot at capacity {slot_capacity}."
                )
                # Inform the customer the slot is gone
                send_msg_fn(
                    wa_id,
                    "⚠️ Sorry! That time slot was just taken by another customer.\n\n"
                    "Please choose a different time and I'll book it for you right away! 😊"
                )
                return None  # Signal to caller that booking failed

            # Store appointment hash (expires in 3 days after appointment)
            redis_client.hset(appt_key, mapping=appt_data)
            redis_client.expireat(appt_key, int(appt_ts + 3 * 86400))

            # Add to schedule sorted set (score = appointment timestamp)
            redis_client.zadd(APPT_SCHEDULE_KEY, {wa_id: appt_ts})
            logger.info(f"📅 [APPT BOOKED] {name} ({wa_id}) | {model} | {appt_dt.strftime('%d %b %Y %H:%M')}")
        except Exception as e:
            logger.error(f"❌ [APPT REDIS ERROR] {e}")
    else:
        logger.warning("⚠️ [APPT] Redis unavailable — appointment not persisted")

    # --- Instant confirmation message to customer ---
    appt_str = appt_dt.strftime("%A, %d %b %Y at %I:%M %p")
    maps_text = f"\n\n📍 *Showroom:* {maps_link}" if maps_link else ""

    confirmation = (
        f"✅ *Test Drive Confirmed!*\n\n"
        f"👤 *Name:* {name}\n"
        f"🚗 *Model:* {model}\n"
        f"📅 *Date & Time:* {appt_str}\n"
        f"📱 *Your number:* {phone}"
        f"{maps_text}\n\n"
        f"We'll remind you *24 hours* and *30 minutes* before your visit.\n\n"
        f"Reply *RESCHEDULE* anytime to change the time. See you there! 🤝"
    )

    try:
        send_msg_fn(wa_id, confirmation)
        logger.info(f"✅ [APPT CONFIRMATION SENT] To: {wa_id}")
    except Exception as e:
        logger.error(f"❌ [APPT CONFIRMATION ERROR] {e}")

    # --- Notify dealer of new booking ---
    if dealer_wa:
        dealer_msg = (
            f"📅 *NEW TEST DRIVE BOOKED*\n\n"
            f"👤 *Customer:* {name}\n"
            f"📱 *Phone:* {phone}\n"
            f"🚗 *Interested in:* {model}\n"
            f"📅 *Appointment:* {appt_str}\n\n"
            f"💬 WhatsApp customer: wa.me/{wa_id}"
        )
        try:
            send_msg_fn(dealer_wa, dealer_msg)
        except Exception as e:
            logger.error(f"❌ [APPT DEALER NOTIFY ERROR] {e}")

    return appt_data


# ---------------------------------------------------------------------------
# Core: Reminder Messages
# ---------------------------------------------------------------------------

def _send_24h_reminder(wa_id, appt_data, send_msg_fn):
    name      = appt_data.get("name", "there")
    model     = appt_data.get("model", "your car")
    appt_ts   = float(appt_data.get("appt_ts", 0))
    maps_link = appt_data.get("maps_link", "")
    appt_str  = datetime.fromtimestamp(appt_ts).strftime("%A, %d %b at %I:%M %p")

    maps_text = f"\n📍 {maps_link}" if maps_link else ""
    msg = (
        f"⏰ *Reminder — Test Drive Tomorrow!*\n\n"
        f"Hi {name}! Just a heads up:\n\n"
        f"🚗 *{model}* test drive is *tomorrow*\n"
        f"📅 {appt_str}{maps_text}\n\n"
        f"Still works for you? Reply *YES* to confirm or *RESCHEDULE* to change."
    )
    send_msg_fn(wa_id, msg, buttons=["✅ Confirmed!", "🔄 Reschedule"])


def _send_2h_reminder(wa_id, appt_data, send_msg_fn):
    name      = appt_data.get("name", "there")
    model     = appt_data.get("model", "your car")
    maps_link = appt_data.get("maps_link", "")

    maps_text = f"\n\n📍 *Get directions:* {maps_link}" if maps_link else ""
    msg = (
        f"🚗 *2 Hours to Your Test Drive!*\n\n"
        f"Hi {name}! Your *{model}* test drive is in 2 hours.\n\n"
        f"We're getting the car ready for you!{maps_text}\n\n"
        f"Any questions? Just reply here. See you soon! 🤝"
    )
    send_msg_fn(wa_id, msg)


def _send_30min_reminder(wa_id, appt_data, send_msg_fn):
    name  = appt_data.get("name", "there")
    model = appt_data.get("model", "your car")

    msg = (
        f"🔔 *{model} Test Drive in 30 Minutes!*\n\n"
        f"Hi {name}! We're all set for you.\n\n"
        f"On your way? Reply *ON THE WAY* 🚗\n"
        f"Need to reschedule? Reply *RESCHEDULE* 📅\n\n"
        f"We look forward to seeing you!"
    )
    send_msg_fn(wa_id, msg, buttons=["🚗 On my way!", "🔄 Reschedule"])


# ---------------------------------------------------------------------------
# Core: Scheduled Reminder Check (called every 5 min by APScheduler)
# ---------------------------------------------------------------------------

def check_and_send_reminders(redis_client, send_msg_fn):
    """
    Scans appt_schedule ZSet for upcoming appointments and sends
    the appropriate reminder at 24h / 2h / 30min before.

    Parameters
    ----------
    redis_client : redis.StrictRedis instance (or None)
    send_msg_fn  : app.send_whatsapp_message reference
    """
    if redis_client is None:
        logger.warning("⚠️ [APPT REMINDER] Redis unavailable — skipping scan")
        return

    now = time.time()
    sent_count = 0

    try:
        # Scan appointments from now to 25h from now (to catch 24h reminders)
        members = redis_client.zrangebyscore(
            APPT_SCHEDULE_KEY, now, now + REMINDER_24H + 3600, withscores=True
        )
    except Exception as e:
        logger.error(f"❌ [APPT REMINDER] Redis ZRANGEBYSCORE error: {e}")
        return

    for wa_id, appt_ts in members:
        time_until = appt_ts - now  # seconds until appointment

        if time_until < 0:
            # Appointment has passed — don't send more reminders
            # Auto-mark as noshow after 2h grace period
            if abs(time_until) > 7200:  # 2h grace
                try:
                    appt_key = f"{APPT_KEY_PREFIX}{wa_id}"
                    current_status = redis_client.hget(appt_key, "status") or "booked"
                    if current_status == "booked":
                        redis_client.hset(appt_key, "status", "noshow_auto")
                        dealer_wa = redis_client.hget(appt_key, "dealer_wa") or ""
                        _increment_showrate(dealer_wa, "noshow", redis_client)
                        redis_client.zrem(APPT_SCHEDULE_KEY, wa_id)
                        logger.info(f"📊 [APPT AUTO-NOSHOW] {wa_id}")
                except Exception as e:
                    logger.error(f"❌ [APPT AUTO-NOSHOW ERROR] {wa_id}: {e}")
            continue

        try:
            appt_key  = f"{APPT_KEY_PREFIX}{wa_id}"
            appt_data = redis_client.hgetall(appt_key)

            if not appt_data:
                redis_client.zrem(APPT_SCHEDULE_KEY, wa_id)
                continue

            if appt_data.get("status") in ("showed", "noshow", "rescheduled", "cancelled"):
                redis_client.zrem(APPT_SCHEDULE_KEY, wa_id)
                continue

            # --- 24h reminder ---
            if time_until <= REMINDER_24H and appt_data.get("reminder_24h_sent") == "0":
                _send_24h_reminder(wa_id, appt_data, send_msg_fn)
                redis_client.hset(appt_key, "reminder_24h_sent", "1")
                sent_count += 1
                logger.info(f"⏰ [24H REMINDER SENT] {wa_id} | {time_until/3600:.1f}h to go")

            # --- 2h reminder ---
            elif time_until <= REMINDER_2H and appt_data.get("reminder_2h_sent") == "0":
                _send_2h_reminder(wa_id, appt_data, send_msg_fn)
                redis_client.hset(appt_key, "reminder_2h_sent", "1")
                sent_count += 1
                logger.info(f"⏰ [2H REMINDER SENT] {wa_id} | {time_until/60:.0f}min to go")

            # --- 30min reminder ---
            elif time_until <= REMINDER_30MIN and appt_data.get("reminder_30min_sent") == "0":
                _send_30min_reminder(wa_id, appt_data, send_msg_fn)
                redis_client.hset(appt_key, "reminder_30min_sent", "1")
                sent_count += 1
                logger.info(f"⏰ [30MIN REMINDER SENT] {wa_id} | {time_until/60:.0f}min to go")

        except Exception as e:
            logger.error(f"❌ [APPT REMINDER] Error for {wa_id}: {e}")
            continue

    if sent_count:
        logger.info(f"✅ [APPT REMINDER SCAN] Sent {sent_count} reminder(s)")
    else:
        logger.debug("🔍 [APPT REMINDER SCAN] No reminders needed this cycle")


# ---------------------------------------------------------------------------
# Core: Reschedule Intent Detection
# ---------------------------------------------------------------------------

def detect_reschedule_intent(text):
    """
    Returns True if the user's message indicates they want to reschedule
    or cannot make the appointment.

    Parameters
    ----------
    text : str — incoming WhatsApp message text
    """
    if not text:
        return False
    normalized = text.lower().strip()
    return any(kw in normalized for kw in RESCHEDULE_KEYWORDS)


def handle_reschedule(wa_id, name, redis_client, send_msg_fn):
    """
    Triggered when reschedule intent is detected.
    Updates appointment status to 'rescheduled' and prompts for new time.
    """
    appt_key = f"{APPT_KEY_PREFIX}{wa_id}"

    if redis_client:
        try:
            redis_client.hset(appt_key, "status", "rescheduled")
            redis_client.zrem(APPT_SCHEDULE_KEY, wa_id)
        except Exception as e:
            logger.error(f"❌ [RESCHEDULE REDIS ERROR] {e}")

    msg = (
        f"No problem, {name}! 😊\n\n"
        "Let's find a better time for your test drive.\n\n"
        "Please share your preferred date and time, for example:\n"
        "📅 *Saturday, 1 March at 11 AM*\n\n"
        "Or tell us what works best for you!"
    )
    send_msg_fn(wa_id, msg)
    logger.info(f"🔄 [RESCHEDULE INITIATED] {wa_id}")


# ---------------------------------------------------------------------------
# Core: Show-Rate Tracking
# ---------------------------------------------------------------------------

def _increment_showrate(dealer_wa, outcome, redis_client):
    """Internal — increment show-rate counters. outcome = 'showed' | 'noshow'"""
    if not dealer_wa or not redis_client:
        return
    key = f"{SHOWRATE_KEY_PREFIX}{dealer_wa}"
    try:
        redis_client.hincrby(key, "total", 1)
        redis_client.hincrby(key, outcome, 1)
    except Exception as e:
        logger.error(f"❌ [SHOWRATE INCREMENT ERROR] {e}")


def confirm_showed(wa_id, dealer_wa, redis_client, send_msg_fn=None):
    """
    Mark appointment as SHOWED. Updates show-rate counters.
    Call this from admin endpoint when dealer confirms visit.
    """
    appt_key = f"{APPT_KEY_PREFIX}{wa_id}"

    if redis_client:
        try:
            appt_data = redis_client.hgetall(appt_key)
            if not appt_data:
                logger.warning(f"⚠️ [CONFIRM SHOWED] No appointment found for {wa_id}")
                return {"success": False, "error": "Appointment not found"}

            redis_client.hset(appt_key, "status", "showed")
            redis_client.zrem(APPT_SCHEDULE_KEY, wa_id)
            _increment_showrate(dealer_wa, "showed", redis_client)

            logger.info(f"✅ [SHOWED CONFIRMED] {wa_id} showed up!")
            return {"success": True, "status": "showed"}
        except Exception as e:
            logger.error(f"❌ [CONFIRM SHOWED ERROR] {e}")
            return {"success": False, "error": str(e)}
    return {"success": False, "error": "Redis unavailable"}


def confirm_noshow(wa_id, dealer_wa, redis_client):
    """
    Mark appointment as NO-SHOW. Updates show-rate counters.
    Call this from admin endpoint when dealer marks no-show.
    """
    appt_key = f"{APPT_KEY_PREFIX}{wa_id}"

    if redis_client:
        try:
            appt_data = redis_client.hgetall(appt_key)
            if not appt_data:
                logger.warning(f"⚠️ [CONFIRM NOSHOW] No appointment found for {wa_id}")
                return {"success": False, "error": "Appointment not found"}

            redis_client.hset(appt_key, "status", "noshow")
            redis_client.zrem(APPT_SCHEDULE_KEY, wa_id)
            _increment_showrate(dealer_wa, "noshow", redis_client)

            logger.info(f"📊 [NOSHOW CONFIRMED] {wa_id}")
            return {"success": True, "status": "noshow"}
        except Exception as e:
            logger.error(f"❌ [CONFIRM NOSHOW ERROR] {e}")
            return {"success": False, "error": str(e)}
    return {"success": False, "error": "Redis unavailable"}


def get_show_rate(dealer_wa, redis_client):
    """
    Returns show-rate statistics for a specific dealer.

    Returns
    -------
    dict: { total, showed, noshow, rate_pct }
    """
    if not redis_client:
        return {"total": 0, "showed": 0, "noshow": 0, "rate_pct": 0.0, "error": "Redis unavailable"}

    key = f"{SHOWRATE_KEY_PREFIX}{dealer_wa}"
    try:
        data    = redis_client.hgetall(key) or {}
        total   = int(data.get("total",  0))
        showed  = int(data.get("showed", 0))
        noshow  = int(data.get("noshow", 0))
        rate    = round((showed / total * 100), 1) if total > 0 else 0.0

        return {
            "dealer_wa": dealer_wa,
            "total":     total,
            "showed":    showed,
            "noshow":    noshow,
            "rate_pct":  rate,
        }
    except Exception as e:
        logger.error(f"❌ [GET SHOW RATE ERROR] {e}")
        return {"total": 0, "showed": 0, "noshow": 0, "rate_pct": 0.0, "error": str(e)}


def get_active_appointment(wa_id, redis_client):
    """
    Returns the active appointment data for a user, or None if none booked.
    Only returns appointments with status 'booked'.
    """
    if not redis_client:
        return None
    try:
        appt_key  = f"{APPT_KEY_PREFIX}{wa_id}"
        appt_data = redis_client.hgetall(appt_key)
        if appt_data and appt_data.get("status") == "booked":
            return appt_data
        return None
    except Exception as e:
        logger.error(f"❌ [GET APPT ERROR] {e}")
        return None
