"""
Missed Call Auto-Recovery — P3
================================
Detects WhatsApp missed call events from the Meta webhook
and automatically sends a recovery message to the customer.

In UAE, WhatsApp calling is the primary business communication channel,
so this fires frequently on missed WhatsApp calls.

Usage
-----
In webhook_message() in app.py:

    missed = extract_missed_call(data)
    if missed:
        wa_id, recipient_id = missed
        client_cfg = get_client_config(recipient_id)
        send_missed_call_recovery(
            wa_id,
            model_interest=...,   # from Redis state if known
            dealer_name=client_cfg.get("dealer_name", "our team"),
            dealer_wa=client_cfg.get("dealer_whatsapp", ""),
            send_msg_fn=send_whatsapp_message,
            redis_client=r,
        )
        return "", 200
"""

import logging
import time

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# How long to wait before sending another missed call recovery
# (prevents spam if customer calls multiple times)
# ---------------------------------------------------------------------------
RECOVERY_COOLDOWN_SECONDS = 6 * 60 * 60    # 6 hours
RECOVERY_KEY_PREFIX        = "missed_call:" # Redis key: missed_call:{wa_id}


# ---------------------------------------------------------------------------
# Meta Webhook Parser
# ---------------------------------------------------------------------------

def extract_missed_call(data):
    """
    Parse a Meta WhatsApp webhook payload and check if it's a missed call.

    Meta fires a webhook with message type "call" and status "missed"
    when a customer's WhatsApp call goes unanswered on the business number.

    Parameters
    ----------
    data : dict — raw parsed JSON from Meta webhook POST body

    Returns
    -------
    (wa_id, recipient_id) tuple if this is a missed call, else None
    """
    try:
        value    = data.get("entry", [{}])[0] \
                       .get("changes", [{}])[0] \
                       .get("value", {})
        messages = value.get("messages", [])
        metadata = value.get("metadata", {})

        if not messages:
            return None

        message = messages[0]
        msg_type = message.get("type", "")

        # Meta sends missed WhatsApp calls as type "call"
        if msg_type != "call":
            return None

        call_info = message.get("call", {})
        status    = call_info.get("status", "")

        if status != "missed":
            return None

        wa_id        = message.get("from")
        recipient_id = (
            metadata.get("display_phone_number")
            or metadata.get("phone_number_id", "")
        )

        if not wa_id:
            return None

        logger.info(f"📞 [MISSED CALL DETECTED] From: {wa_id} | Business: {recipient_id}")
        return (wa_id, recipient_id)

    except Exception as e:
        logger.error(f"❌ [MISSED CALL PARSE ERROR] {e}")
        return None


# ---------------------------------------------------------------------------
# Recovery Message Sender
# ---------------------------------------------------------------------------

def send_missed_call_recovery(wa_id, dealer_name, send_msg_fn,
                               model_interest=None, dealer_wa=None,
                               redis_client=None):
    """
    Auto-send a WhatsApp recovery message after a missed call.

    Features:
    - Respects cooldown (won't spam if they called multiple times)
    - Personalized with car model if known from existing session state
    - Notifies dealer about missed call opportunity

    Parameters
    ----------
    wa_id          : Customer WhatsApp ID
    dealer_name    : Dealership display name
    send_msg_fn    : app.send_whatsapp_message reference
    model_interest : Car model they enquired about (optional, from state)
    dealer_wa      : Dealer WhatsApp (for internal alert, optional)
    redis_client   : redis.StrictRedis instance (or None)
    """

    # --- Cooldown check: don't send if we recently recovered this number ---
    if redis_client:
        try:
            cooldown_key = f"{RECOVERY_KEY_PREFIX}{wa_id}"
            if redis_client.exists(cooldown_key):
                logger.info(
                    f"⏸️ [MISSED CALL RECOVERY] Skipped {wa_id} — cooldown active"
                )
                return {"sent": False, "reason": "cooldown"}

            # Set cooldown marker
            redis_client.setex(cooldown_key, RECOVERY_COOLDOWN_SECONDS, "1")
        except Exception as e:
            logger.warning(f"⚠️ [MISSED CALL REDIS ERROR] {e} — proceeding anyway")

    # --- Build personalized recovery message ---
    if model_interest:
        body = (
            f"Hi! 👋 We missed your WhatsApp call.\n\n"
            f"Are you still interested in the *{model_interest}*?\n\n"
            f"Reply *YES* and we'll call you back right away! 📞\n\n"
            f"— {dealer_name}"
        )
    else:
        body = (
            f"Hi! 👋 We missed your WhatsApp call just now.\n\n"
            f"Still looking for a car? We'd love to help! 🚗\n\n"
            f"Reply *YES* and our team will call you back in minutes.\n\n"
            f"— {dealer_name}"
        )

    buttons = ["📞 Yes, call me!", "🚗 I found one, thanks"]

    try:
        send_msg_fn(wa_id, body, buttons=buttons)
        logger.info(f"✅ [MISSED CALL RECOVERY SENT] To: {wa_id}")
        result = {"sent": True, "wa_id": wa_id}
    except Exception as e:
        logger.error(f"❌ [MISSED CALL RECOVERY SEND ERROR] {e}")
        result = {"sent": False, "error": str(e)}

    # --- Alert dealer about missed call opportunity ---
    if dealer_wa:
        model_text = f" about the *{model_interest}*" if model_interest else ""
        dealer_msg = (
            f"📞 *MISSED CALL ALERT*\n\n"
            f"A customer{model_text} called and we missed it.\n"
            f"WhatsApp ID: {wa_id}\n\n"
            f"✅ Auto-recovery message sent to them.\n"
            f"💬 Follow up: wa.me/{wa_id}"
        )
        try:
            send_msg_fn(dealer_wa, dealer_msg)
            logger.info(f"🔔 [DEALER ALERTED] Missed call from {wa_id}")
        except Exception as e:
            logger.error(f"❌ [DEALER MISSED CALL ALERT ERROR] {e}")

    return result


# ---------------------------------------------------------------------------
# BSP Missed Call Webhook Handler
# (For AiSensy / Interakt / Twilio that fire a SEPARATE missed call webhook)
# ---------------------------------------------------------------------------

def parse_bsp_missed_call(data, bsp="generic"):
    """
    Parse BSP-specific missed call webhook payloads.
    Returns (wa_id, model_interest) or None.

    Currently supports:
    - generic: expects { "phone": "...", "type": "missed_call" }
    - Can be extended per BSP format
    """
    try:
        if bsp == "generic":
            if data.get("type") == "missed_call":
                wa_id = data.get("phone") or data.get("from") or data.get("wa_id")
                model = data.get("model") or data.get("context", {}).get("model")
                if wa_id:
                    logger.info(f"📞 [BSP MISSED CALL] {bsp}: {wa_id}")
                    return (wa_id, model)
        return None
    except Exception as e:
        logger.error(f"❌ [BSP MISSED CALL PARSE ERROR] {e}")
        return None
