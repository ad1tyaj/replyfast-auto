"""
Exit-Intent Follow-Up Scheduler
================================
Runs two automatic follow-up nudges when a user goes silent mid-funnel:

  • 10 minutes of silence  → "Still there?" nudge
  • 24 hours of silence    → "We saved your progress" re-engagement

Uses APScheduler BackgroundScheduler (runs inside the Flask process).
Works with Redis for tracking; gracefully skips when Redis is unavailable.
"""

import json
import logging
import time

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Timing constants
# ---------------------------------------------------------------------------
FOLLOW_UP_1_DELAY   = 10 * 60       # 10 minutes in seconds
FOLLOW_UP_2_DELAY   = 24 * 60 * 60  # 24 hours in seconds
SCHEDULER_INTERVAL  = 5 * 60        # check every 5 minutes

# Redis sorted-set key that tracks active mid-funnel sessions
ACTIVE_SESSIONS_KEY = "active_sessions"

# q_status values that count as "mid-funnel" (user is in the flow but not done)
MID_FUNNEL_STATUSES = {0.5, 1, 1.5, 1.7, 2, 3, 4, 5, 6}


# ---------------------------------------------------------------------------
# Follow-up messages
# ---------------------------------------------------------------------------

def _build_followup_1_text(user_name):
    if user_name:
        return (
            f"Still there, {user_name}? 👋\n\n"
            "No worries — finding the right car takes time! 🚗\n\n"
            "Pick up where you left off?"
        )
    return (
        "Still there? 👋\n\n"
        "I'm still here to help you find the perfect car! 🚗\n\n"
        "Want to continue?"
    )


def _build_followup_1_buttons():
    return ["Yes, continue! 🚀", "Call me instead 📞"]


def _build_followup_2_text(user_name, dealer_name="ReplyFast Auto"):
    if user_name:
        return (
            f"Hi {user_name}! 👋\n\n"
            f"{dealer_name} here — we saved your progress! 🎉\n\n"
            "Ready to find your dream car? Your preferences are still on file.\n\n"
            "Type *HI* to jump back in, or tap below:"
        )
    return (
        f"Hi! 👋\n\n"
        f"{dealer_name} here — we've saved your car search! 🚗\n\n"
        "Ready to pick up where you left off?\n\n"
        "Type *HI* to restart:"
    )


def _build_followup_2_buttons():
    return ["Resume search 🚗", "Start fresh 🔄"]


# ---------------------------------------------------------------------------
# Core check function (called by scheduler; can also be called in tests)
# ---------------------------------------------------------------------------

def check_and_send_follow_ups(redis_client, send_message_func, get_state_func,
                              save_state_func, get_client_config_func):
    """
    Scans active_sessions sorted set for stale mid-funnel users and sends
    the appropriate follow-up message.

    Parameters
    ----------
    redis_client         : redis.StrictRedis instance (or None if unavailable)
    send_message_func    : app.send_whatsapp_message reference
    get_state_func       : app.get_user_state reference
    save_state_func      : app.save_user_state reference
    get_client_config_func : app.get_client_config reference
    """
    if redis_client is None:
        logger.warning("⚠️ [FOLLOW-UP] Redis unavailable — skipping follow-up scan")
        return

    now = time.time()

    try:
        # Find ALL active sessions (score = last_activity timestamp)
        # We fetch everything < now and filter by delay thresholds in Python.
        # Max 500 entries for safety.
        members = redis_client.zrangebyscore(
            ACTIVE_SESSIONS_KEY, 0, now, withscores=True, start=0, num=500
        )
    except Exception as e:
        logger.error(f"❌ [FOLLOW-UP] Redis ZRANGEBYSCORE error: {e}")
        return

    sent_count = 0

    for wa_id, last_activity_ts in members:
        idle_seconds = now - last_activity_ts

        # Not stale enough yet
        if idle_seconds < FOLLOW_UP_1_DELAY:
            continue

        try:
            state = get_state_func(wa_id)
            q_status = state.get("q_status", 0)

            # Only engage mid-funnel users
            if q_status not in MID_FUNNEL_STATUSES:
                # Remove from active sessions if they're done or at 0
                redis_client.zrem(ACTIVE_SESSIONS_KEY, wa_id)
                continue

            user_name     = state.get("answers", {}).get("name", "")
            follow_up_1   = state.get("follow_up_1_sent", False)
            follow_up_2   = state.get("follow_up_2_sent", False)
            recipient_id  = state.get("recipient_id", "")

            # Get dealer name for this client
            try:
                client_cfg  = get_client_config_func(recipient_id)
                dealer_name = client_cfg.get("dealer_name", "ReplyFast Auto")
            except Exception:
                dealer_name = "ReplyFast Auto"

            # ── Follow-up 1: 10 minutes ──────────────────────────────────
            if not follow_up_1:
                send_message_func(
                    wa_id,
                    _build_followup_1_text(user_name),
                    buttons=_build_followup_1_buttons()
                )
                state["follow_up_1_sent"] = True
                save_state_func(wa_id, state)
                sent_count += 1
                logger.info(
                    f"🔔 [FOLLOW-UP 1 SENT] To: {wa_id} | "
                    f"Idle: {idle_seconds/60:.1f} min | Q: {q_status}"
                )
                continue  # Check follow-up 2 on next scheduler run

            # ── Follow-up 2: 24 hours ─────────────────────────────────────
            if follow_up_1 and not follow_up_2 and idle_seconds >= FOLLOW_UP_2_DELAY:
                send_message_func(
                    wa_id,
                    _build_followup_2_text(user_name, dealer_name),
                    buttons=_build_followup_2_buttons()
                )
                state["follow_up_2_sent"] = True
                save_state_func(wa_id, state)
                # Remove from active_sessions — bot is now fully silent
                redis_client.zrem(ACTIVE_SESSIONS_KEY, wa_id)
                sent_count += 1
                logger.info(
                    f"🔔 [FOLLOW-UP 2 SENT] To: {wa_id} | "
                    f"Idle: {idle_seconds/3600:.1f} hr | Q: {q_status}"
                )

        except Exception as e:
            logger.error(f"❌ [FOLLOW-UP] Error processing {wa_id}: {e}")
            continue

    if sent_count:
        logger.info(f"✅ [FOLLOW-UP SCAN] Sent {sent_count} follow-up(s)")
    else:
        logger.debug("🔍 [FOLLOW-UP SCAN] No follow-ups needed this cycle")


# ---------------------------------------------------------------------------
# Scheduler factory
# ---------------------------------------------------------------------------

def create_scheduler(redis_client, send_message_func, get_state_func,
                     save_state_func, get_client_config_func):
    """
    Creates and returns a configured BackgroundScheduler.
    Call scheduler.start() after Flask app is initialized.
    """
    scheduler = BackgroundScheduler(daemon=True)

    scheduler.add_job(
        func=check_and_send_follow_ups,
        trigger=IntervalTrigger(seconds=SCHEDULER_INTERVAL),
        kwargs={
            "redis_client":          redis_client,
            "send_message_func":     send_message_func,
            "get_state_func":        get_state_func,
            "save_state_func":       save_state_func,
            "get_client_config_func": get_client_config_func,
        },
        id="exit_intent_follow_up",
        name="Exit-Intent Follow-Up",
        replace_existing=True,
        misfire_grace_time=60,  # tolerate up to 60s delay before skipping
    )

    logger.info(
        f"⏰ [SCHEDULER] Exit-intent follow-up configured "
        f"(check every {SCHEDULER_INTERVAL//60} min, "
        f"1st nudge at {FOLLOW_UP_1_DELAY//60} min, "
        f"2nd nudge at {FOLLOW_UP_2_DELAY//3600} hr)"
    )
    return scheduler
