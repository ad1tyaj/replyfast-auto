"""
Unit tests for appointment_scheduler.py (P1 — Show-Rate Engine).
No real Redis or WhatsApp API required — uses MagicMock throughout.

Run:
    cd "c:\\Users\\ASUS\\Downloads\\replyfast auto"
    python test_appointment_scheduler.py
"""
import time
import unittest
from datetime import datetime, timedelta
from unittest.mock import MagicMock, call, patch

from appointment_scheduler import (
    book_appointment,
    check_and_send_reminders,
    detect_reschedule_intent,
    handle_reschedule,
    confirm_showed,
    confirm_noshow,
    get_show_rate,
    get_active_appointment,
    APPT_KEY_PREFIX,
    APPT_SCHEDULE_KEY,
    SHOWRATE_KEY_PREFIX,
    REMINDER_24H,
    REMINDER_2H,
    REMINDER_30MIN,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_appt(wa_id="971501234567", model="Toyota Camry",
               minutes_from_now=90, status="booked",
               r24="0", r2="0", r30="0"):
    """Return a mock appointment hash as Redis would return it."""
    appt_ts = time.time() + (minutes_from_now * 60)
    return {
        "wa_id":              wa_id,
        "name":               "Ahmed Al-Rashid",
        "phone":              "971501234567",
        "model":              model,
        "appt_ts":            str(appt_ts),
        "dealer_wa":          "971509876543",
        "dealer_name":        "Dubai Motors",
        "maps_link":          "https://maps.google.com/?q=Dubai+Motors",
        "status":             status,
        "reminder_24h_sent":  r24,
        "reminder_2h_sent":   r2,
        "reminder_30min_sent": r30,
        "booked_at":          str(time.time()),
    }


def _make_redis_mock(wa_id, appt_data, appt_ts):
    """Return a minimal Redis mock with one scheduled appointment."""
    redis_mock = MagicMock()
    redis_mock.zrangebyscore.return_value = [(wa_id, appt_ts)]
    redis_mock.hgetall.return_value = appt_data
    redis_mock.hget.return_value = appt_data.get("status", "booked")
    return redis_mock


# ---------------------------------------------------------------------------
# Test: book_appointment
# ---------------------------------------------------------------------------

class TestBookAppointment(unittest.TestCase):

    def test_book_appointment_stores_redis_and_sends_confirmation(self):
        redis_mock = MagicMock()
        send_mock  = MagicMock()
        appt_dt    = datetime.now() + timedelta(days=1)

        book_appointment(
            wa_id="971501234567",
            name="Ahmed",
            phone="971501234567",
            model="Toyota Camry",
            appt_dt=appt_dt,
            dealer_wa="971509876543",
            maps_link="https://maps.google.com/?q=test",
            send_msg_fn=send_mock,
            redis_client=redis_mock,
            dealer_name="Dubai Motors",
        )

        # Redis hset and zadd should have been called
        redis_mock.hset.assert_called_once()
        redis_mock.zadd.assert_called_once()

        # Two WhatsApp messages: confirmation to customer + alert to dealer
        self.assertEqual(send_mock.call_count, 2)

        # Customer confirmation message should contain key fields
        customer_msg = send_mock.call_args_list[0][0][1]
        self.assertIn("Ahmed", customer_msg)
        self.assertIn("Toyota Camry", customer_msg)
        self.assertIn("Confirmed", customer_msg)

    def test_book_appointment_without_redis_still_sends_confirmation(self):
        """When Redis is unavailable, should still send the WhatsApp confirmation."""
        send_mock = MagicMock()
        appt_dt   = datetime.now() + timedelta(days=1)

        book_appointment(
            wa_id="971501234567",
            name="Ahmed",
            phone="971501234567",
            model="Toyota Camry",
            appt_dt=appt_dt,
            dealer_wa="",
            maps_link="",
            send_msg_fn=send_mock,
            redis_client=None,  # No Redis
        )

        # Should still send customer confirmation (no dealer notification without dealer_wa)
        self.assertGreaterEqual(send_mock.call_count, 1)


# ---------------------------------------------------------------------------
# Test: Reminders
# ---------------------------------------------------------------------------

class TestAppointmentReminders(unittest.TestCase):

    def setUp(self):
        self.wa_id    = "971501234567"
        self.send_mock = MagicMock()

    def _run_reminder_check(self, appt_data):
        appt_ts    = float(appt_data["appt_ts"])
        redis_mock = _make_redis_mock(self.wa_id, appt_data, appt_ts)
        check_and_send_reminders(redis_mock, self.send_mock)
        return redis_mock

    def test_24h_reminder_sent_when_within_24h(self):
        """24h reminder fires when appointment is < 24h away and reminder not sent."""
        minutes = (REMINDER_24H // 60) - 30  # 23.5h from now
        appt = _make_appt(minutes_from_now=minutes, r24="0")
        redis_mock = self._run_reminder_check(appt)

        self.assertEqual(self.send_mock.call_count, 1)
        redis_mock.hset.assert_called_with(
            f"{APPT_KEY_PREFIX}{self.wa_id}", "reminder_24h_sent", "1"
        )

    def test_24h_reminder_not_sent_again_if_already_sent(self):
        """No double-send for 24h reminder."""
        minutes = (REMINDER_24H // 60) - 30
        appt = _make_appt(minutes_from_now=minutes, r24="1")
        self._run_reminder_check(appt)
        self.assertEqual(self.send_mock.call_count, 0)

    def test_2h_reminder_sent_when_within_2h(self):
        """2h reminder fires when appointment is < 2h away."""
        minutes = (REMINDER_2H // 60) - 10  # ~110 min from now
        appt = _make_appt(minutes_from_now=minutes, r24="1", r2="0")
        redis_mock = self._run_reminder_check(appt)

        self.assertEqual(self.send_mock.call_count, 1)
        redis_mock.hset.assert_called_with(
            f"{APPT_KEY_PREFIX}{self.wa_id}", "reminder_2h_sent", "1"
        )

    def test_30min_reminder_sent_when_within_30min(self):
        """30min reminder fires when appointment is < 30min away."""
        minutes = (REMINDER_30MIN // 60) - 5  # 25 min from now
        appt = _make_appt(minutes_from_now=minutes, r24="1", r2="1", r30="0")
        redis_mock = self._run_reminder_check(appt)

        self.assertEqual(self.send_mock.call_count, 1)
        redis_mock.hset.assert_called_with(
            f"{APPT_KEY_PREFIX}{self.wa_id}", "reminder_30min_sent", "1"
        )

    def test_no_reminder_if_appointment_far_away(self):
        """No reminders for an appointment more than 25 hours away."""
        redis_mock = MagicMock()
        redis_mock.zrangebyscore.return_value = []  # Nothing in range
        check_and_send_reminders(redis_mock, self.send_mock)
        self.assertEqual(self.send_mock.call_count, 0)

    def test_gracefully_handles_redis_unavailable(self):
        """Should not raise when Redis is None."""
        check_and_send_reminders(None, self.send_mock)
        self.assertEqual(self.send_mock.call_count, 0)

    def test_skips_cancelled_appointments(self):
        """No reminders sent for cancelled or rescheduled appointments."""
        minutes = (REMINDER_24H // 60) - 30
        appt = _make_appt(minutes_from_now=minutes, status="cancelled")
        self._run_reminder_check(appt)
        self.assertEqual(self.send_mock.call_count, 0)


# ---------------------------------------------------------------------------
# Test: detect_reschedule_intent
# ---------------------------------------------------------------------------

class TestDetectRescheduleIntent(unittest.TestCase):

    def test_detects_cant_come(self):
        self.assertTrue(detect_reschedule_intent("I can't come today"))

    def test_detects_reschedule_keyword(self):
        self.assertTrue(detect_reschedule_intent("Can we reschedule?"))

    def test_detects_cancel(self):
        self.assertTrue(detect_reschedule_intent("I want to cancel"))

    def test_detects_not_available(self):
        self.assertTrue(detect_reschedule_intent("I'm not available tomorrow"))

    def test_normal_message_not_detected(self):
        self.assertFalse(detect_reschedule_intent("Yes I'm on my way!"))

    def test_empty_text_not_detected(self):
        self.assertFalse(detect_reschedule_intent(""))

    def test_none_not_detected(self):
        self.assertFalse(detect_reschedule_intent(None))


# ---------------------------------------------------------------------------
# Test: Show-Rate
# ---------------------------------------------------------------------------

class TestShowRate(unittest.TestCase):

    def _make_redis_with_stats(self, total=10, showed=8, noshow=2):
        redis_mock = MagicMock()
        redis_mock.hgetall.return_value = {
            "total":  str(total),
            "showed": str(showed),
            "noshow": str(noshow),
        }
        return redis_mock

    def test_show_rate_calculation(self):
        redis_mock = self._make_redis_with_stats(total=10, showed=8, noshow=2)
        result = get_show_rate("971509876543", redis_mock)
        self.assertEqual(result["total"],    10)
        self.assertEqual(result["showed"],   8)
        self.assertEqual(result["noshow"],   2)
        self.assertEqual(result["rate_pct"], 80.0)

    def test_show_rate_zero_when_no_appointments(self):
        redis_mock = MagicMock()
        redis_mock.hgetall.return_value = {}
        result = get_show_rate("971509876543", redis_mock)
        self.assertEqual(result["rate_pct"], 0.0)
        self.assertEqual(result["total"],    0)

    def test_confirm_showed_updates_redis(self):
        appt_data  = _make_appt()
        redis_mock = MagicMock()
        redis_mock.hgetall.return_value = appt_data
        result = confirm_showed("971501234567", "971509876543", redis_mock)
        self.assertTrue(result["success"])
        redis_mock.hset.assert_called_with(
            f"{APPT_KEY_PREFIX}971501234567", "status", "showed"
        )

    def test_confirm_noshow_updates_redis(self):
        appt_data  = _make_appt()
        redis_mock = MagicMock()
        redis_mock.hgetall.return_value = appt_data
        result = confirm_noshow("971501234567", "971509876543", redis_mock)
        self.assertTrue(result["success"])
        redis_mock.hset.assert_called_with(
            f"{APPT_KEY_PREFIX}971501234567", "status", "noshow"
        )

    def test_confirm_showed_returns_error_when_no_appointment(self):
        redis_mock = MagicMock()
        redis_mock.hgetall.return_value = {}  # No appointment
        result = confirm_showed("971501234567", "971509876543", redis_mock)
        self.assertFalse(result["success"])


if __name__ == "__main__":
    print("🧪 Running Appointment Show-Rate Engine tests (P1)...\n")
    loader = unittest.TestLoader()
    suite  = loader.loadTestsFromModule(__import__("__main__"))
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    if result.wasSuccessful():
        print("\n✅ All appointment scheduler tests passed!")
    else:
        print(f"\n❌ {len(result.failures)} test(s) failed.")
