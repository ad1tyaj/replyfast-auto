"""
Unit tests for hot_lead_escalation.py (P2 — 5-Minute HOT Lead Escalation).
No real Redis or WhatsApp API required — uses MagicMock throughout.

Run:
    cd "c:\\Users\\ASUS\\Downloads\\replyfast auto"
    python test_hot_lead_escalation.py
"""
import time
import unittest
from unittest.mock import MagicMock, call

from hot_lead_escalation import (
    register_hot_lead,
    mark_contacted,
    get_avg_response_time,
    check_and_escalate_hot_leads,
    HOT_LEAD_KEY_PREFIX,
    PENDING_KEY,
    RESP_TIME_KEY_PREFIX,
    ESCALATION_WINDOW,
    MANAGER_WINDOW,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_lead(wa_id="971501234567", arrived_offset=-400,
               contacted="0", rep_alerted="0", mgr_alerted="0"):
    """Return a mock lead hash as Redis would return it."""
    return {
        "wa_id":        wa_id,
        "name":         "Khalid Al-Mansouri",
        "phone":        "971501234567",
        "model":        "Mercedes C200",
        "budget":       "AED 100k-200k",
        "timeline":     "This week",
        "dealer_wa":    "971509876543",
        "manager_wa":   "971507777777",
        "dealer_name":  "Dubai Motors",
        "arrived_ts":   str(time.time() + arrived_offset),
        "contacted":    contacted,
        "contacted_ts": "",
        "rep_alerted":  rep_alerted,
        "mgr_alerted":  mgr_alerted,
    }


def _make_redis_with_lead(wa_id, lead_data, arrived_ts):
    """Return a minimal Redis mock with a single pending lead."""
    redis_mock = MagicMock()
    redis_mock.zrangebyscore.return_value = [(wa_id, arrived_ts)]
    redis_mock.hgetall.return_value = lead_data
    return redis_mock


# ---------------------------------------------------------------------------
# Test: register_hot_lead
# ---------------------------------------------------------------------------

class TestRegisterHotLead(unittest.TestCase):

    def test_stores_lead_in_redis_and_pending_set(self):
        redis_mock = MagicMock()
        register_hot_lead(
            wa_id="971501234567",
            name="Khalid",
            phone="971501234567",
            model="Mercedes C200",
            budget="AED 150k",
            timeline="This week",
            dealer_wa="971509876543",
            manager_wa="971507777777",
            dealer_name="Dubai Motors",
            redis_client=redis_mock,
        )
        redis_mock.hset.assert_called_once()
        redis_mock.zadd.assert_called_once_with(PENDING_KEY, {"971501234567": unittest.mock.ANY})

    def test_does_not_raise_without_redis(self):
        """Gracefully handles None redis_client."""
        # Should not raise
        register_hot_lead(
            wa_id="971501234567",
            name="Khalid",
            phone="971501234567",
            model="Mercedes",
            budget="AED 100k",
            timeline="ASAP",
            dealer_wa="971509876543",
            manager_wa="",
            dealer_name="Dubai Motors",
            redis_client=None,
        )


# ---------------------------------------------------------------------------
# Test: check_and_escalate_hot_leads
# ---------------------------------------------------------------------------

class TestEscalationCheck(unittest.TestCase):

    def setUp(self):
        self.wa_id     = "971501234567"
        self.send_mock = MagicMock()

    def test_rep_alert_sent_after_5_minutes(self):
        """Rep alert fires when lead has been waiting > 5 min."""
        lead_data  = _make_lead(arrived_offset=-(ESCALATION_WINDOW + 30))
        arrived_ts = float(lead_data["arrived_ts"])
        redis_mock = _make_redis_with_lead(self.wa_id, lead_data, arrived_ts)

        check_and_escalate_hot_leads(redis_mock, self.send_mock)

        # Send message called with dealer_wa
        self.send_mock.assert_called_once()
        msg_target = self.send_mock.call_args[0][0]
        self.assertEqual(msg_target, "971509876543")

        # Mark rep_alerted = "1"
        redis_mock.hset.assert_called_with(
            f"{HOT_LEAD_KEY_PREFIX}{self.wa_id}", "rep_alerted", "1"
        )

    def test_manager_alert_sent_after_10_minutes(self):
        """Manager alert fires when lead has been waiting > 10 min and rep already alerted."""
        lead_data  = _make_lead(arrived_offset=-(MANAGER_WINDOW + 30), rep_alerted="1")
        arrived_ts = float(lead_data["arrived_ts"])
        redis_mock = _make_redis_with_lead(self.wa_id, lead_data, arrived_ts)

        check_and_escalate_hot_leads(redis_mock, self.send_mock)

        # Manager message sent
        self.send_mock.assert_called_once()
        msg_target = self.send_mock.call_args[0][0]
        self.assertEqual(msg_target, "971507777777")

    def test_no_alert_before_5_minutes(self):
        """No alerts fired within the first 5 minutes."""
        lead_data  = _make_lead(arrived_offset=-60)  # Only 1 minute old
        arrived_ts = float(lead_data["arrived_ts"])
        redis_mock = _make_redis_with_lead(self.wa_id, lead_data, arrived_ts)

        check_and_escalate_hot_leads(redis_mock, self.send_mock)
        self.send_mock.assert_not_called()

    def test_no_alert_if_already_contacted(self):
        """No alerts if lead was already contacted by rep."""
        lead_data  = _make_lead(arrived_offset=-(ESCALATION_WINDOW + 30), contacted="1")
        arrived_ts = float(lead_data["arrived_ts"])
        redis_mock = _make_redis_with_lead(self.wa_id, lead_data, arrived_ts)

        check_and_escalate_hot_leads(redis_mock, self.send_mock)
        self.send_mock.assert_not_called()

    def test_rep_not_alerted_twice(self):
        """Rep alert not resent if already done."""
        lead_data  = _make_lead(arrived_offset=-(ESCALATION_WINDOW + 30), rep_alerted="1")
        arrived_ts = float(lead_data["arrived_ts"])
        redis_mock = _make_redis_with_lead(self.wa_id, lead_data, arrived_ts)

        check_and_escalate_hot_leads(redis_mock, self.send_mock)
        # Should NOT alert rep again (rep_alerted="1"), and no manager (< 10min)
        self.send_mock.assert_not_called()

    def test_gracefully_handles_redis_unavailable(self):
        """Should not raise when redis_client is None."""
        check_and_escalate_hot_leads(None, self.send_mock)
        self.send_mock.assert_not_called()

    def test_skips_empty_lead_data(self):
        """Handles missing lead data gracefully by removing from pending set."""
        redis_mock = MagicMock()
        redis_mock.zrangebyscore.return_value = [("971501234567", time.time() - 400)]
        redis_mock.hgetall.return_value = {}  # Empty — lead expired

        check_and_escalate_hot_leads(redis_mock, self.send_mock)
        redis_mock.zrem.assert_called_with(PENDING_KEY, "971501234567")
        self.send_mock.assert_not_called()


# ---------------------------------------------------------------------------
# Test: mark_contacted
# ---------------------------------------------------------------------------

class TestMarkContacted(unittest.TestCase):

    def test_marks_lead_contacted_and_logs_response_time(self):
        lead_data = _make_lead(arrived_offset=-180)  # 3 minutes old
        redis_mock = MagicMock()
        redis_mock.hgetall.return_value = lead_data

        result = mark_contacted("971501234567", redis_mock)

        self.assertTrue(result["success"])
        self.assertIn("response_sec", result)
        self.assertGreater(result["response_sec"], 0)

        # Should remove from pending set
        redis_mock.zrem.assert_called_with(PENDING_KEY, "971501234567")

        # Should log response time to dealer list
        resp_key = f"{RESP_TIME_KEY_PREFIX}971509876543"
        redis_mock.rpush.assert_called_once_with(resp_key, unittest.mock.ANY)

    def test_returns_error_when_lead_not_found(self):
        redis_mock = MagicMock()
        redis_mock.hgetall.return_value = {}

        result = mark_contacted("971501234567", redis_mock)
        self.assertFalse(result["success"])
        self.assertEqual(result["error"], "Lead not found")

    def test_returns_error_without_redis(self):
        result = mark_contacted("971501234567", None)
        self.assertFalse(result["success"])


# ---------------------------------------------------------------------------
# Test: get_avg_response_time
# ---------------------------------------------------------------------------

class TestResponseTimeStats(unittest.TestCase):

    def test_calculates_correct_average(self):
        redis_mock = MagicMock()
        # 2 min, 4 min, 6 min = avg 4 min = 240 sec
        redis_mock.lrange.return_value = ["120", "240", "360"]

        result = get_avg_response_time("971509876543", redis_mock)

        self.assertEqual(result["avg_sec"], 240)
        self.assertEqual(result["avg_label"], "4m 0s")
        self.assertEqual(result["sample_count"], 3)
        self.assertEqual(result["fastest_sec"], 120)
        self.assertEqual(result["slowest_sec"], 360)

    def test_returns_no_data_when_empty(self):
        redis_mock = MagicMock()
        redis_mock.lrange.return_value = []

        result = get_avg_response_time("971509876543", redis_mock)
        self.assertEqual(result["avg_label"], "No data")
        self.assertEqual(result["sample_count"], 0)

    def test_returns_na_without_redis(self):
        result = get_avg_response_time("971509876543", None)
        self.assertEqual(result["avg_label"], "N/A")


if __name__ == "__main__":
    print("🧪 Running HOT Lead Escalation tests (P2)...\n")
    loader = unittest.TestLoader()
    suite  = loader.loadTestsFromModule(__import__("__main__"))
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    if result.wasSuccessful():
        print("\n✅ All HOT lead escalation tests passed!")
    else:
        print(f"\n❌ {len(result.failures)} test(s) failed.")
