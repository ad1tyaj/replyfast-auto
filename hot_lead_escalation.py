"""
HOT Lead 5-Minute Escalation Engine — P2
==========================================
When a HOT lead completes the funnel:

  1. Store lead in Redis with a timestamp
  2. After 5 minutes, check if it's been marked as contacted
  3. If NOT contacted:
     - Alert the rep  (WhatsApp)
     - Alert the manager (WhatsApp)
     - Log response time failure
  4. If contacted: log response time ✅

Redis Keys:
  hot_lead:{wa_id}          → Hash  — lead data + contact status
  hot_leads_pending         → ZSet  — score = lead arrival Unix timestamp
  response_times:{dealer_wa} → List — logged response times (seconds)

Plugs into the same APScheduler already running in app.py.
"""

import logging
import time

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Timing constants
# ---------------------------------------------------------------------------
ESCALATION_WINDOW    = 5 * 60       # 5 minutes in seconds
MANAGER_WINDOW       = 10 * 60      # escalate to manager after 10 min
SCHEDULER_INTERVAL   = 60           # check every 60 seconds
HOT_LEAD_KEY_PREFIX  = "hot_lead:"
PENDING_KEY          = "hot_leads_pending"
RESP_TIME_KEY_PREFIX = "response_times:"
HOT_LEAD_TTL         = 24 * 60 * 60  # expire Redis entries after 24h


# ---------------------------------------------------------------------------
# Register a new HOT lead
# ---------------------------------------------------------------------------

def register_hot_lead(wa_id, name, phone, model, budget, timeline,
                      dealer_wa, manager_wa, dealer_name, redis_client):
    """
    Called at the end of complete_lead() when lead_score == HOT.
    Stores the lead in Redis and adds to the pending escalation set.

    Parameters
    ----------
    wa_id        : Customer WhatsApp ID
    name         : Customer full name
    phone        : Customer phone
    model        : Car model / vehicle type
    budget       : Budget range
    timeline     : Purchase timeline
    dealer_wa    : Rep/dealer WhatsApp (first alert target)
    manager_wa   : Manager WhatsApp (second escalation target)
    dealer_name  : Dealership display name
    redis_client : redis.StrictRedis (or None)
    """
    if not redis_client:
        logger.warning("⚠️ [P2] Redis unavailable — HOT lead escalation not registered")
        return

    arrived_ts = time.time()
    key        = f"{HOT_LEAD_KEY_PREFIX}{wa_id}"

    lead_data = {
        "wa_id":        wa_id,
        "name":         name,
        "phone":        phone,
        "model":        model,
        "budget":       budget,
        "timeline":     timeline,
        "dealer_wa":    dealer_wa,
        "manager_wa":   manager_wa,
        "dealer_name":  dealer_name,
        "arrived_ts":   str(arrived_ts),
        "contacted":    "0",        # 0 = not contacted, 1 = contacted
        "contacted_ts": "",
        "rep_alerted":  "0",
        "mgr_alerted":  "0",
    }

    try:
        redis_client.hset(key, mapping=lead_data)
        redis_client.expire(key, HOT_LEAD_TTL)
        redis_client.zadd(PENDING_KEY, {wa_id: arrived_ts})
        logger.info(
            f"🔥 [P2 REGISTERED] HOT lead: {name} ({wa_id}) | "
            f"5-min escalation timer started"
        )
    except Exception as e:
        logger.error(f"❌ [P2 REGISTER ERROR] {e}")


# ---------------------------------------------------------------------------
# Mark lead as contacted (call from admin endpoint)
# ---------------------------------------------------------------------------

def mark_contacted(wa_id, redis_client):
    """
    Mark a HOT lead as contacted by the rep.
    Stops further escalation and logs response time.

    Returns dict with response time info.
    """
    if not redis_client:
        return {"success": False, "error": "Redis unavailable"}

    key = f"{HOT_LEAD_KEY_PREFIX}{wa_id}"
    try:
        lead_data = redis_client.hgetall(key)
        if not lead_data:
            return {"success": False, "error": "Lead not found"}

        now          = time.time()
        arrived_ts   = float(lead_data.get("arrived_ts", now))
        response_sec = round(now - arrived_ts)
        dealer_wa    = lead_data.get("dealer_wa", "")

        # Update lead record
        redis_client.hset(key, mapping={
            "contacted":    "1",
            "contacted_ts": str(now),
        })
        redis_client.zrem(PENDING_KEY, wa_id)

        # Log response time for this dealer
        if dealer_wa:
            resp_key = f"{RESP_TIME_KEY_PREFIX}{dealer_wa}"
            redis_client.rpush(resp_key, response_sec)
            redis_client.expire(resp_key, 30 * 24 * 3600)  # 30 days

        mins = response_sec // 60
        secs = response_sec % 60
        logger.info(
            f"✅ [P2 CONTACTED] {lead_data.get('name')} ({wa_id}) | "
            f"Response time: {mins}m {secs}s"
        )
        return {
            "success":        True,
            "wa_id":          wa_id,
            "name":           lead_data.get("name"),
            "response_sec":   response_sec,
            "response_label": f"{mins}m {secs}s",
        }

    except Exception as e:
        logger.error(f"❌ [P2 MARK CONTACTED ERROR] {e}")
        return {"success": False, "error": str(e)}


# ---------------------------------------------------------------------------
# Response time stats
# ---------------------------------------------------------------------------

def get_avg_response_time(dealer_wa, redis_client):
    """
    Returns average response time in seconds for a dealer,
    and a human-readable label.
    """
    if not redis_client:
        return {"avg_sec": 0, "avg_label": "N/A", "sample_count": 0}

    resp_key = f"{RESP_TIME_KEY_PREFIX}{dealer_wa}"
    try:
        times = redis_client.lrange(resp_key, 0, -1)
        if not times:
            return {"avg_sec": 0, "avg_label": "No data", "sample_count": 0}

        values  = [int(t) for t in times]
        avg_sec = round(sum(values) / len(values))
        mins    = avg_sec // 60
        secs    = avg_sec % 60

        return {
            "dealer_wa":    dealer_wa,
            "avg_sec":      avg_sec,
            "avg_label":    f"{mins}m {secs}s",
            "sample_count": len(values),
            "fastest_sec":  min(values),
            "slowest_sec":  max(values),
        }
    except Exception as e:
        logger.error(f"❌ [GET RESPONSE TIME ERROR] {e}")
        return {"avg_sec": 0, "avg_label": "Error", "sample_count": 0}


# ---------------------------------------------------------------------------
# Escalation alert messages
# ---------------------------------------------------------------------------

def _build_rep_alert(lead_data, minutes_waiting):
    name      = lead_data.get("name", "Customer")
    phone     = lead_data.get("phone", "")
    model     = lead_data.get("model", "Car")
    budget    = lead_data.get("budget", "N/A")
    timeline  = lead_data.get("timeline", "N/A")
    wa_id     = lead_data.get("wa_id", "")
    wa_link   = f"https://wa.me/{wa_id}" if wa_id else "N/A"

    return (
        f"🔥 *HOT LEAD — ACT NOW!*\n\n"
        f"⏱️ Waiting *{minutes_waiting} minutes* — no response yet!\n\n"
        f"👤 *{name}*\n"
        f"📱 {phone}\n"
        f"🚗 Interested in: {model}\n"
        f"💰 Budget: {budget}\n"
        f"⏰ Timeline: {timeline}\n\n"
        f"💬 *Reply now:* {wa_link}\n\n"
        f"⚡ Every minute costs you a sale."
    )


def _build_manager_alert(lead_data, minutes_waiting, dealer_name):
    name   = lead_data.get("name", "Customer")
    wa_id  = lead_data.get("wa_id", "")

    return (
        f"🚨 *ESCALATION ALERT — {dealer_name}*\n\n"
        f"HOT lead *{name}* has been waiting *{minutes_waiting} minutes* "
        f"with NO rep contact.\n\n"
        f"📊 This is a revenue loss event.\n\n"
        f"WhatsApp: wa.me/{wa_id}\n\n"
        f"Please follow up immediately or assign to another rep."
    )


# ---------------------------------------------------------------------------
# Core: Escalation Check (called every 60s by APScheduler)
# ---------------------------------------------------------------------------

def check_and_escalate_hot_leads(redis_client, send_msg_fn):
    """
    Scans hot_leads_pending ZSet for uncontacted HOT leads.
    Fires rep alert at 5 min, manager alert at 10 min.

    Parameters
    ----------
    redis_client : redis.StrictRedis (or None)
    send_msg_fn  : app.send_whatsapp_message reference
    """
    if not redis_client:
        logger.warning("⚠️ [P2] Redis unavailable — skipping escalation scan")
        return

    now = time.time()

    try:
        # Fetch all pending leads (arrived any time up to now)
        members = redis_client.zrangebyscore(
            PENDING_KEY, 0, now, withscores=True, start=0, num=200
        )
    except Exception as e:
        logger.error(f"❌ [P2] Redis ZRANGEBYSCORE error: {e}")
        return

    escalated = 0

    for wa_id, arrived_ts in members:
        idle_sec = now - arrived_ts

        if idle_sec < ESCALATION_WINDOW:
            continue  # Not yet 5 minutes

        try:
            key       = f"{HOT_LEAD_KEY_PREFIX}{wa_id}"
            lead_data = redis_client.hgetall(key)

            if not lead_data:
                redis_client.zrem(PENDING_KEY, wa_id)
                continue

            # Already contacted — remove from pending, skip
            if lead_data.get("contacted") == "1":
                redis_client.zrem(PENDING_KEY, wa_id)
                continue

            dealer_wa   = lead_data.get("dealer_wa", "")
            manager_wa  = lead_data.get("manager_wa", "")
            dealer_name = lead_data.get("dealer_name", "ReplyFast Auto")
            rep_alerted = lead_data.get("rep_alerted", "0")
            mgr_alerted = lead_data.get("mgr_alerted", "0")
            minutes     = int(idle_sec // 60)

            # ── 5-minute escalation: Alert REP ──────────────────────────
            if idle_sec >= ESCALATION_WINDOW and rep_alerted == "0":
                if dealer_wa:
                    send_msg_fn(dealer_wa, _build_rep_alert(lead_data, minutes))
                    redis_client.hset(key, "rep_alerted", "1")
                    escalated += 1
                    logger.warning(
                        f"🚨 [P2 REP ALERT] {lead_data.get('name')} ({wa_id}) | "
                        f"Waiting {minutes}min — rep notified: {dealer_wa}"
                    )

            # ── 10-minute escalation: Alert MANAGER ─────────────────────
            if idle_sec >= MANAGER_WINDOW and mgr_alerted == "0":
                if manager_wa:
                    send_msg_fn(manager_wa, _build_manager_alert(lead_data, minutes, dealer_name))
                    redis_client.hset(key, "mgr_alerted", "1")
                    escalated += 1
                    logger.warning(
                        f"🚨 [P2 MANAGER ALERT] {lead_data.get('name')} ({wa_id}) | "
                        f"Waiting {minutes}min — manager notified: {manager_wa}"
                    )
                elif rep_alerted == "1":
                    # No manager number — log only
                    logger.warning(
                        f"⚠️ [P2] No manager_wa configured for {dealer_name} — "
                        f"add 'manager_whatsapp' to clients.json"
                    )
                    redis_client.hset(key, "mgr_alerted", "1")  # Prevent re-logging

        except Exception as e:
            logger.error(f"❌ [P2 ESCALATION ERROR] {wa_id}: {e}")
            continue

    if escalated:
        logger.info(f"🚨 [P2] Escalated {escalated} HOT lead alert(s)")
    else:
        logger.debug("🔍 [P2] No escalations needed this cycle")
