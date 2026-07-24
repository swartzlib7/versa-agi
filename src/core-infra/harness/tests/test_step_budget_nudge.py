"""Step-budget wrap-up / end-off nudge tone and thresholds."""

import unittest

from harness.agent_harness import (
    END_OFF_REMAINING_CAP,
    budget_end_off_message,
    budget_wrap_message,
    end_off_remaining_threshold,
)


class TestEndOffThreshold(unittest.TestCase):
    def test_large_budget_is_fifteen(self):
        self.assertEqual(end_off_remaining_threshold(300), END_OFF_REMAINING_CAP)

    def test_small_budget_clamps(self):
        self.assertEqual(end_off_remaining_threshold(50), 5)
        self.assertEqual(end_off_remaining_threshold(20), 2)


class TestBudgetMessageTone(unittest.TestCase):
    def test_end_off_has_no_panic_words(self):
        msg = budget_end_off_message(285, 300)
        self.assertIn("end off now", msg)
        self.assertIn("15 remaining", msg)
        lower = msg.lower()
        for banned in ("critical", "stop all work", "you must"):
            self.assertNotIn(banned, lower)

    def test_wrap_is_soft(self):
        msg = budget_wrap_message(240, 300)
        self.assertIn("wrapping up", msg)
        self.assertNotIn("CRITICAL", msg)
        self.assertNotIn("STOP all work", msg)


if __name__ == "__main__":
    unittest.main()
