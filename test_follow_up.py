"""
Unit test for the exit-intent follow-up scheduler.
Tests the core check_and_send_follow_ups() function in isolation
without a real Redis connection or running scheduler.

Run:
    cd "c:\\Users\\ASUS\\Downloads\\replyfast auto"
    python test_follow_up.py
"""
import time
import json
import unittest
from unittest.mock import MagicMock, call

from follow_up_scheduler import (
    check_and_send_follow_ups,
    FOLLOW_UP_1_DELAY,
    FOLLOW_UP_2_DELAY,
    ACTIVE_SESSIONS_KEY,
)


class TestFollowUpScheduler(unittest.TestCase):

    def _make_redis_mock(self, wa_id, last_activity_ts):
        """Return a minimal Redis mock with a single active session."""
        redis_mock = MagicMock()
        redis_mock.zrangebyscore.return_value = [(wa_id, last_activity_ts)]
        return redis_mock

    def _make_state(self, q_status=1, name="Rahul", follow_up_1=False, follow_up_2=False):
        return {
            "q_status": q_status,
            "answers": {"name": name},
            "recipient_id": "919999999999",
            "follow_up_1_sent": follow_up_1,
            "follow_up_2_sent": follow_up_2,
            "last_activity": time.time(),
        }

    # ------------------------------------------------------------------
    # Test: Follow-up 1 sent after 10 minutes silence
    # ------------------------------------------------------------------
    def test_follow_up_1_sent_after_10_minutes(self):
        wa_id = "911234567890"
        stale_ts = time.time() - (FOLLOW_UP_1_DELAY + 60)  # 11 min ago
        state = self._make_state(q_status=1)

        redis_mock  = self._make_redis_mock(wa_id, stale_ts)
        send_mock   = MagicMock()
        get_mock    = MagicMock(return_value=state)
        save_mock   = MagicMock()
        config_mock = MagicMock(return_value={"dealer_name": "Test Motors"})

        check_and_send_follow_ups(redis_mock, send_mock, get_mock, save_mock, config_mock)

        # Message should have been sent exactly once
        self.assertEqual(send_mock.call_count, 1)
        # State should have follow_up_1_sent = True
        saved_state = save_mock.call_args[0][1]
        self.assertTrue(saved_state["follow_up_1_sent"])
        self.assertFalse(saved_state.get("follow_up_2_sent", False))

    # ------------------------------------------------------------------
    # Test: Follow-up 2 sent after 24 hours (when follow-up 1 already sent)
    # ------------------------------------------------------------------
    def test_follow_up_2_sent_after_24_hours(self):
        wa_id = "911234567890"
        stale_ts = time.time() - (FOLLOW_UP_2_DELAY + 60)  # 25 hr ago
        state = self._make_state(q_status=1, follow_up_1=True, follow_up_2=False)

        redis_mock  = self._make_redis_mock(wa_id, stale_ts)
        send_mock   = MagicMock()
        get_mock    = MagicMock(return_value=state)
        save_mock   = MagicMock()
        config_mock = MagicMock(return_value={"dealer_name": "Test Motors"})

        check_and_send_follow_ups(redis_mock, send_mock, get_mock, save_mock, config_mock)

        self.assertEqual(send_mock.call_count, 1)
        saved_state = save_mock.call_args[0][1]
        self.assertTrue(saved_state["follow_up_2_sent"])
        # User should be removed from active_sessions after follow-up 2
        redis_mock.zrem.assert_called_with(ACTIVE_SESSIONS_KEY, wa_id)

    # ------------------------------------------------------------------
    # Test: No follow-up sent if both already sent
    # ------------------------------------------------------------------
    def test_no_followup_if_both_already_sent(self):
        wa_id = "911234567890"
        stale_ts = time.time() - (FOLLOW_UP_2_DELAY + 3600)
        state = self._make_state(q_status=1, follow_up_1=True, follow_up_2=True)

        redis_mock  = self._make_redis_mock(wa_id, stale_ts)
        send_mock   = MagicMock()
        get_mock    = MagicMock(return_value=state)
        save_mock   = MagicMock()
        config_mock = MagicMock(return_value={"dealer_name": "Test Motors"})

        check_and_send_follow_ups(redis_mock, send_mock, get_mock, save_mock, config_mock)

        # No message should be sent
        self.assertEqual(send_mock.call_count, 0)

    # ------------------------------------------------------------------
    # Test: No follow-up for completed sessions (q_status=7)
    # ------------------------------------------------------------------
    def test_no_followup_for_completed_session(self):
        wa_id = "911234567890"
        stale_ts = time.time() - (FOLLOW_UP_1_DELAY + 60)
        state = self._make_state(q_status=7)

        redis_mock  = self._make_redis_mock(wa_id, stale_ts)
        send_mock   = MagicMock()
        get_mock    = MagicMock(return_value=state)
        save_mock   = MagicMock()
        config_mock = MagicMock(return_value={})

        check_and_send_follow_ups(redis_mock, send_mock, get_mock, save_mock, config_mock)

        self.assertEqual(send_mock.call_count, 0)

    # ------------------------------------------------------------------
    # Test: User not yet stale — no follow-up sent
    # ------------------------------------------------------------------
    def test_no_followup_for_fresh_session(self):
        wa_id = "911234567890"
        fresh_ts = time.time() - 60  # only 1 minute ago
        state = self._make_state(q_status=1)

        redis_mock  = self._make_redis_mock(wa_id, fresh_ts)
        send_mock   = MagicMock()
        get_mock    = MagicMock(return_value=state)
        save_mock   = MagicMock()
        config_mock = MagicMock(return_value={})

        check_and_send_follow_ups(redis_mock, send_mock, get_mock, save_mock, config_mock)

        self.assertEqual(send_mock.call_count, 0)

    # ------------------------------------------------------------------
    # Test: Gracefully handles Redis being unavailable (None)
    # ------------------------------------------------------------------
    def test_handles_redis_unavailable(self):
        send_mock   = MagicMock()
        get_mock    = MagicMock()
        save_mock   = MagicMock()
        config_mock = MagicMock()

        # Should not raise
        check_and_send_follow_ups(None, send_mock, get_mock, save_mock, config_mock)
        self.assertEqual(send_mock.call_count, 0)


if __name__ == "__main__":
    print("🧪 Running exit-intent follow-up scheduler tests...\n")
    loader = unittest.TestLoader()
    suite  = loader.loadTestsFromTestCase(TestFollowUpScheduler)
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    if result.wasSuccessful():
        print("\n✅ All follow-up scheduler tests passed!")
    else:
        print(f"\n❌ {len(result.failures)} test(s) failed.")
