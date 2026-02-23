"""
P4 — Revenue Dashboard
=======================
Aggregates data from all Redis data structures written by P1, P2, P3.
Returns clean dicts that the dashboard route can pass to Jinja2.

Data sources:
  P1  appt:{wa_id}           → appointment details
      showrate:{dealer}:showed / :total  → show-rate counters
  P2  hot:pending (ZSET)     → pending HOT leads
      resp_time:{dealer}     → response time list (seconds per contact)
  P3  missed_call:{wa_id}    → missed call recovery log
  app active_sessions (ZSET) → mid-funnel users (proxy for daily volume)
"""

import time
import json
import logging
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

# ── Redis key constants (must match the modules that write them) ──────────────
PENDING_KEY          = "hot:pending"
HOT_LEAD_KEY_PREFIX  = "hot:lead:"
RESP_TIME_PREFIX     = "resp_time:"
APPT_KEY_PREFIX      = "appt:"
SHOWRATE_PREFIX      = "showrate:"
MISSED_CALL_PREFIX   = "missed_call:"


# ---------------------------------------------------------------------------
# Core aggregation helpers
# ---------------------------------------------------------------------------

def _safe_int(v, default=0):
    try:
        return int(v) if v is not None else default
    except (ValueError, TypeError):
        return default


def _safe_float(v, default=0.0):
    try:
        return float(v) if v is not None else default
    except (ValueError, TypeError):
        return default


def _hms(seconds):
    """Convert seconds → human-readable string, e.g. '3m 42s'."""
    seconds = int(seconds)
    m, s = divmod(seconds, 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h}h {m}m"
    if m:
        return f"{m}m {s}s"
    return f"{s}s"


# ---------------------------------------------------------------------------
# P2: HOT lead response-time stats
# ---------------------------------------------------------------------------

def get_response_stats(dealer_wa, redis_client):
    """Returns avg/fastest/slowest response time for a dealer."""
    if not redis_client:
        return {"avg_label": "N/A", "avg_sec": 0, "fastest": "N/A",
                "slowest": "N/A", "sample_count": 0}
    try:
        key = f"{RESP_TIME_PREFIX}{dealer_wa}"
        raw = redis_client.lrange(key, 0, -1)
        if not raw:
            return {"avg_label": "No data", "avg_sec": 0, "fastest": "N/A",
                    "slowest": "N/A", "sample_count": 0}
        times = [_safe_float(v) for v in raw]
        avg   = sum(times) / len(times)
        return {
            "avg_sec":     int(avg),
            "avg_label":   _hms(avg),
            "fastest":     _hms(min(times)),
            "slowest":     _hms(max(times)),
            "sample_count": len(times),
        }
    except Exception as e:
        logger.error(f"[DASHBOARD] response stats error: {e}")
        return {"avg_label": "Error", "avg_sec": 0, "fastest": "N/A",
                "slowest": "N/A", "sample_count": 0}


# ---------------------------------------------------------------------------
# P1: Show-rate stats
# ---------------------------------------------------------------------------

def get_showrate_stats(dealer_wa, redis_client):
    """Returns show-rate percentage and raw counts."""
    if not redis_client:
        return {"showed": 0, "total": 0, "noshow": 0, "rate_pct": 0.0, "rate_label": "N/A"}
    try:
        showed = _safe_int(redis_client.get(f"{SHOWRATE_PREFIX}{dealer_wa}:showed"))
        total  = _safe_int(redis_client.get(f"{SHOWRATE_PREFIX}{dealer_wa}:total"))
        noshow = total - showed
        rate   = round((showed / total * 100), 1) if total else 0.0
        return {
            "showed":     showed,
            "total":      total,
            "noshow":     noshow,
            "rate_pct":   rate,
            "rate_label": f"{rate}%" if total else "No data",
        }
    except Exception as e:
        logger.error(f"[DASHBOARD] showrate error: {e}")
        return {"showed": 0, "total": 0, "noshow": 0, "rate_pct": 0.0, "rate_label": "Error"}


# ---------------------------------------------------------------------------
# P2: Pending HOT leads
# ---------------------------------------------------------------------------

def get_pending_hot_leads(redis_client):
    """Returns list of currently pending (uncontacted) HOT leads with wait times."""
    if not redis_client:
        return []
    try:
        now    = time.time()
        # All pending leads, sorted by arrival
        items  = redis_client.zrangebyscore(PENDING_KEY, "-inf", "+inf", withscores=True)
        leads  = []
        for wa_id_bytes, arrived_ts in items:
            wa_id     = wa_id_bytes.decode() if isinstance(wa_id_bytes, bytes) else wa_id_bytes
            lead_data = redis_client.hgetall(f"{HOT_LEAD_KEY_PREFIX}{wa_id}")
            if not lead_data:
                continue
            # Decode bytes keys/values
            lead  = {
                (k.decode() if isinstance(k, bytes) else k):
                (v.decode() if isinstance(v, bytes) else v)
                for k, v in lead_data.items()
            }
            wait_sec = int(now - _safe_float(lead.get("arrived_ts", arrived_ts)))
            leads.append({
                "wa_id":      wa_id,
                "name":       lead.get("name", "Unknown"),
                "phone":      lead.get("phone", ""),
                "service":    lead.get("model", ""),
                "wait":       _hms(wait_sec),
                "wait_sec":   wait_sec,
                "contacted":  lead.get("contacted", "0") == "1",
                "rep_alerted":lead.get("rep_alerted", "0") == "1",
                "mgr_alerted":lead.get("mgr_alerted", "0") == "1",
            })
        # Sort: longest waiting first
        leads.sort(key=lambda x: x["wait_sec"], reverse=True)
        return leads
    except Exception as e:
        logger.error(f"[DASHBOARD] pending leads error: {e}")
        return []


# ---------------------------------------------------------------------------
# Recent appointments
# ---------------------------------------------------------------------------

def get_recent_appointments(redis_client, limit=10):
    """Scan appt:* keys and return the most recent appointments."""
    if not redis_client:
        return []
    try:
        keys  = redis_client.keys(f"{APPT_KEY_PREFIX}*")
        appts = []
        for key in keys:
            raw = redis_client.hgetall(key)
            if not raw:
                continue
            data = {
                (k.decode() if isinstance(k, bytes) else k):
                (v.decode() if isinstance(v, bytes) else v)
                for k, v in raw.items()
            }
            appts.append({
                "name":    data.get("name", "Unknown"),
                "service": data.get("model", data.get("service", "Appointment")),
                "time":    data.get("appt_time", "TBD"),
                "status":  data.get("status", "booked"),
                "wa_id":   data.get("wa_id", ""),
            })
        # Sort by appointment time string (best-effort)
        appts.sort(key=lambda x: x["time"], reverse=True)
        return appts[:limit]
    except Exception as e:
        logger.error(f"[DASHBOARD] appointments error: {e}")
        return []


# ---------------------------------------------------------------------------
# Lead volume (today's active sessions proxy)
# ---------------------------------------------------------------------------

def get_lead_volume_today(redis_client):
    """
    Returns a rough count of leads captured today using the active_sessions
    sorted set. Score = last_activity timestamp.
    """
    if not redis_client:
        return {"today": 0, "this_week": 0}
    try:
        midnight    = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        week_start  = midnight - timedelta(days=midnight.weekday())
        today_count = redis_client.zcount("active_sessions", midnight.timestamp(), "+inf")
        week_count  = redis_client.zcount("active_sessions", week_start.timestamp(), "+inf")
        return {"today": int(today_count), "this_week": int(week_count)}
    except Exception as e:
        logger.error(f"[DASHBOARD] lead volume error: {e}")
        return {"today": 0, "this_week": 0}


# ---------------------------------------------------------------------------
# Revenue estimate
# ---------------------------------------------------------------------------

def estimate_revenue(hot_count, show_rate_pct, avg_deal_value, conversion_rate=0.30):
    """
    Simple revenue estimate pipeline:
      HOT leads → Show rate → Close rate → Revenue
    """
    shows     = hot_count * (show_rate_pct / 100)
    closes    = shows * conversion_rate
    revenue   = closes * avg_deal_value
    return {
        "shows":   round(shows, 1),
        "closes":  round(closes, 1),
        "revenue": int(revenue),
    }


# ---------------------------------------------------------------------------
# Master dashboard data aggregator
# ---------------------------------------------------------------------------

def get_dashboard_data(dealer_wa, redis_client, client_config=None):
    """
    Returns a single dict with all data needed to render the dashboard.

    dealer_wa     : the dealer's WhatsApp number (Redis key prefix)
    redis_client  : live Redis connection (or None for demo mode)
    client_config : dict from clients.json
    """
    cfg          = client_config or {}
    biz_name     = cfg.get("dealer_name", "ReplyFast Client")
    industry     = cfg.get("industry", "car_dealer")
    currency     = cfg.get("currency", "AED")
    avg_deal     = 150000 if industry == "car_dealer" else 2500  # AED

    resp_stats   = get_response_stats(dealer_wa, redis_client)
    show_stats   = get_showrate_stats(dealer_wa, redis_client)
    pending      = get_pending_hot_leads(redis_client)
    appointments = get_recent_appointments(redis_client)
    volume       = get_lead_volume_today(redis_client)

    # Revenue estimate based on total appointments shown
    rev          = estimate_revenue(
        hot_count       = show_stats["total"],
        show_rate_pct   = show_stats["rate_pct"],
        avg_deal_value  = avg_deal,
    )

    # Score card colours
    response_alert = (
        "green"  if resp_stats["avg_sec"] < 300   else   # < 5 min ✅
        "amber"  if resp_stats["avg_sec"] < 600   else   # < 10 min ⚠
        "red"                                             # > 10 min 🔴
    )

    show_alert = (
        "green" if show_stats["rate_pct"] >= 70 else
        "amber" if show_stats["rate_pct"] >= 50 else
        "red"
    )

    uncontacted_hot = [l for l in pending if not l["contacted"]]

    return {
        "dealer_wa":        dealer_wa,
        "biz_name":         biz_name,
        "industry":         industry,
        "currency":         currency,
        "generated_at":     datetime.now().strftime("%d %b %Y, %H:%M"),
        # KPI cards
        "leads_today":      volume["today"],
        "leads_week":       volume["this_week"],
        "hot_pending":      len(uncontacted_hot),
        "show_rate":        show_stats,
        "response":         resp_stats,
        "response_alert":   response_alert,
        "show_alert":       show_alert,
        # Tables
        "pending_leads":    pending,
        "appointments":     appointments,
        # Revenue
        "revenue":          rev,
        "avg_deal":         avg_deal,
    }
