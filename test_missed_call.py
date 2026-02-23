"""
Unit tests for missed_call_handler.py (P3 — Missed Call Auto-Recovery).
No real WhatsApp API required — uses MagicMock throughout.

Run:
    cd "c:\\Users\\ASUS\\Downloads\\replyfast auto"
    python test_missed_call.py
"""
import unittest
from unittest.mock import MagicMock

from missed_call_handler import (
    extract_missed_call,
    send_missed_call_recovery,
    RECOVERY_KEY_PREFIX,
    RECOVERY_COOLDOWN_SECONDS,
)


# ---------------------------------------------------------------------------
# Helpers: Build realistic Meta webhook payloads
# ---------------------------------------------------------------------------

def _build_missed_call_payload(wa_id="971501234567", business_phone="971509876543"):
    """Return a Meta webhook payload for a missed WhatsApp call."""
    return {
        "entry": [{
            "changes": [{
                "value": {
                    "messages": [{
                        "from": wa_id,
                        "id": "wamid.test123",
                        "type": "call",
                        "call": {
                            "status": "missed"
                        }
                    }],
                    "metadata": {
                        "display_phone_number": business_phone,
                        "phone_number_id": "853033551227517"
                    }
                }
            }]
        }]
    }


def _build_text_message_payload(wa_id="971501234567", text="Hello"):
    """Return a normal text message webhook payload."""
    return {
        "entry": [{
            "changes": [{
                "value": {
                    "messages": [{
                        "from": wa_id,
                        "id": "wamid.text123",
                        "type": "text",
                        "text": {"body": text}
                    }],
                    "metadata": {
                        "display_phone_number": "971509876543",
                        "phone_number_id": "853033551227517"
                    }
                }
            }]
        }]
    }


def _build_answered_call_payload(wa_id="971501234567"):
    """Return a webhook payload for an ANSWERED call (should NOT trigger recovery)."""
    return {
        "entry": [{
            "changes": [{
                "value": {
                    "messages": [{
                        "from": wa_id,
                        "id": "wamid.call_answered",
                        "type": "call",
                        "call": {
                            "status": "completed"
                        }
                    }],
                    "metadata": {
                        "display_phone_number": "971509876543",
                    }
                }
            }]
        }]
    }


# ---------------------------------------------------------------------------
# Test: extract_missed_call
# ---------------------------------------------------------------------------

class TestExtractMissedCall(unittest.TestCase):

    def test_detects_missed_whatsapp_call(self):
        payload = _build_missed_call_payload(
            wa_id="971501234567",
            business_phone="971509876543"
        )
        result = extract_missed_call(payload)
        self.assertIsNotNone(result)
        wa_id, recipient_id = result
        self.assertEqual(wa_id, "971501234567")
        self.assertEqual(recipient_id, "971509876543")

    def test_ignores_normal_text_message(self):
        payload = _build_text_message_payload()
        result  = extract_missed_call(payload)
        self.assertIsNone(result)

    def test_ignores_answered_call(self):
        payload = _build_answered_call_payload()
        result  = extract_missed_call(payload)
        self.assertIsNone(result)

    def test_ignores_empty_payload(self):
        result = extract_missed_call({})
        self.assertIsNone(result)

    def test_ignores_payload_with_no_messages(self):
        payload = {
            "entry": [{"changes": [{"value": {"messages": []}}]}]
        }
        result = extract_missed_call(payload)
        self.assertIsNone(result)


# ---------------------------------------------------------------------------
# Test: send_missed_call_recovery
# ---------------------------------------------------------------------------

class TestSendMissedCallRecovery(unittest.TestCase):

    def test_sends_recovery_message_with_model(self):
        send_mock  = MagicMock()
        redis_mock = MagicMock()
        redis_mock.exists.return_value = 0  # No cooldown

        result = send_missed_call_recovery(
            wa_id="971501234567",
            dealer_name="Dubai Motors",
            send_msg_fn=send_mock,
            model_interest="Toyota Camry",
            dealer_wa="971509876543",
            redis_client=redis_mock,
        )

        self.assertTrue(result["sent"])
        # Customer message + dealer alert
        self.assertEqual(send_mock.call_count, 2)
        # Customer message should mention the model
        customer_msg = send_mock.call_args_list[0][0][1]
        self.assertIn("Toyota Camry", customer_msg)

    def test_sends_generic_message_without_model(self):
        send_mock  = MagicMock()
        redis_mock = MagicMock()
        redis_mock.exists.return_value = 0

        result = send_missed_call_recovery(
            wa_id="971501234567",
            dealer_name="Dubai Motors",
            send_msg_fn=send_mock,
            model_interest=None,
            redis_client=redis_mock,
        )

        self.assertTrue(result["sent"])
        customer_msg = send_mock.call_args_list[0][0][1]
        self.assertIn("missed your WhatsApp call", customer_msg)

    def test_respects_cooldown(self):
        """Should NOT send if cooldown is active (called multiple times)."""
        send_mock  = MagicMock()
        redis_mock = MagicMock()
        redis_mock.exists.return_value = 1  # Cooldown active

        result = send_missed_call_recovery(
            wa_id="971501234567",
            dealer_name="Dubai Motors",
            send_msg_fn=send_mock,
            redis_client=redis_mock,
        )

        self.assertFalse(result["sent"])
        self.assertEqual(result["reason"], "cooldown")
        self.assertEqual(send_mock.call_count, 0)

    def test_sets_cooldown_after_sending(self):
        """After sending, a cooldown key should be set in Redis."""
        send_mock  = MagicMock()
        redis_mock = MagicMock()
        redis_mock.exists.return_value = 0

        send_missed_call_recovery(
            wa_id="971501234567",
            dealer_name="Dubai Motors",
            send_msg_fn=send_mock,
            redis_client=redis_mock,
        )

        expected_key = f"{RECOVERY_KEY_PREFIX}971501234567"
        redis_mock.setex.assert_called_with(
            expected_key, RECOVERY_COOLDOWN_SECONDS, "1"
        )

    def test_works_without_redis(self):
        """Should still send when Redis is unavailable, just no cooldown tracking."""
        send_mock = MagicMock()

        result = send_missed_call_recovery(
            wa_id="971501234567",
            dealer_name="Dubai Motors",
            send_msg_fn=send_mock,
            redis_client=None,
        )

        self.assertTrue(result["sent"])
        self.assertGreaterEqual(send_mock.call_count, 1)

    def test_no_dealer_alert_when_no_dealer_wa(self):
        """Only one message (customer) when dealer_wa not provided."""
        send_mock  = MagicMock()
        redis_mock = MagicMock()
        redis_mock.exists.return_value = 0

        send_missed_call_recovery(
            wa_id="971501234567",
            dealer_name="Dubai Motors",
            send_msg_fn=send_mock,
            dealer_wa=None,
            redis_client=redis_mock,
        )

        # Only customer message, no dealer alert
        self.assertEqual(send_mock.call_count, 1)


if __name__ == "__main__":
    print("🧪 Running Missed Call Auto-Recovery tests (P3)...\n")
    loader = unittest.TestLoader()
    suite  = loader.loadTestsFromModule(__import__("__main__"))
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    if result.wasSuccessful():
        print("\n✅ All missed call recovery tests passed!")
    else:
        print(f"\n❌ {len(result.failures)} test(s) failed.")
