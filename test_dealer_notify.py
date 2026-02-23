"""
Unit tests for notify_hot_lead_dealer().
Tests all 4 key scenarios: HOT alert sent, WARM alert sent,
COLD skipped, and missing dealer_whatsapp gracefully skipped.

Run:
    cd "c:\\Users\\ASUS\\Downloads\\replyfast auto"
    python test_dealer_notify.py
"""
import unittest
from unittest.mock import MagicMock, patch


class TestNotifyHotLeadDealer(unittest.TestCase):
    """Tests for notify_hot_lead_dealer — isolated from Flask/Redis."""

    def _make_lead(self, score="HOT", name="Rahul Sharma", phone="9876543210",
                   wa_id="919876543210"):
        return {
            "wa_id":               wa_id,
            "customer_name":       name,
            "phone_number":        phone,
            "lead_score":          score,
            "budget":              "₹10-15 Lakhs",
            "q1_vehicle_type":     "SUV",
            "q2_new_or_used":      "New",
            "q4_purchase_timeline": "Within 1 week",
            "q5_trade_in":         "No",
        }

    def _make_config(self, dealer_wa="919999999999", dealer_name="Test Motors"):
        return {
            "dealer_whatsapp": dealer_wa,
            "dealer_name":     dealer_name,
        }

    # --- patch send_whatsapp_message inside app module ---
    def _run(self, lead, config, mock_send):
        # Import here to pick up the real function after patching
        import app as app_module
        original = app_module.send_whatsapp_message
        app_module.send_whatsapp_message = mock_send
        try:
            app_module.notify_hot_lead_dealer(lead, config)
        finally:
            app_module.send_whatsapp_message = original

    # ----------------------------------------------------------------
    # HOT lead → message sent to dealer_whatsapp, contains 🔥
    # ----------------------------------------------------------------
    def test_hot_lead_sends_message(self):
        send_mock = MagicMock()
        self._run(self._make_lead("HOT"), self._make_config(), send_mock)

        send_mock.assert_called_once()
        call_args = send_mock.call_args
        # First arg is dealer WhatsApp number
        self.assertEqual(call_args[0][0], "919999999999")
        # Message text contains fire emoji (HOT indicator)
        self.assertIn("🔥", call_args[0][1])
        self.assertIn("Rahul Sharma", call_args[0][1])
        self.assertIn("9876543210", call_args[0][1])

    # ----------------------------------------------------------------
    # WARM lead → message sent to dealer, contains 🌶️
    # ----------------------------------------------------------------
    def test_warm_lead_sends_message(self):
        send_mock = MagicMock()
        self._run(self._make_lead("WARM"), self._make_config(), send_mock)

        send_mock.assert_called_once()
        msg = send_mock.call_args[0][1]
        self.assertIn("🌶️", msg)

    # ----------------------------------------------------------------
    # COLD lead → NO message sent (don't spam dealer)
    # ----------------------------------------------------------------
    def test_cold_lead_no_notification(self):
        send_mock = MagicMock()
        self._run(self._make_lead("COLD"), self._make_config(), send_mock)

        send_mock.assert_not_called()

    # ----------------------------------------------------------------
    # Missing dealer_whatsapp → no crash, no message
    # ----------------------------------------------------------------
    def test_missing_dealer_whatsapp_no_crash(self):
        send_mock = MagicMock()
        config_no_wa = {"dealer_whatsapp": "", "dealer_name": "Test Motors"}
        self._run(self._make_lead("HOT"), config_no_wa, send_mock)

        send_mock.assert_not_called()

    # ----------------------------------------------------------------
    # WhatsApp deep-link to customer is included in message
    # ----------------------------------------------------------------
    def test_message_contains_customer_wa_link(self):
        send_mock = MagicMock()
        self._run(self._make_lead("HOT"), self._make_config(), send_mock)

        msg = send_mock.call_args[0][1]
        self.assertIn("wa.me/919876543210", msg)

    # ----------------------------------------------------------------
    # send_whatsapp_message raises → notify_hot_lead_dealer doesn't crash
    # ----------------------------------------------------------------
    def test_send_failure_does_not_crash(self):
        send_mock = MagicMock(side_effect=Exception("API down"))
        try:
            self._run(self._make_lead("HOT"), self._make_config(), send_mock)
        except Exception:
            self.fail("notify_hot_lead_dealer raised an exception — it should swallow errors")


if __name__ == "__main__":
    print("🧪 Running dealer notification tests...\n")
    loader = unittest.TestLoader()
    suite  = loader.loadTestsFromTestCase(TestNotifyHotLeadDealer)
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    if result.wasSuccessful():
        print("\n✅ All dealer notification tests passed!")
    else:
        print(f"\n❌ {len(result.failures)} test(s) failed.")
