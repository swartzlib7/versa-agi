"""Time warnings and the harness close (state_long_cycles.md §1.3, §1.7; LRC-02, LRC-03)."""

import unittest

from harness.deadline import CLOSE_NOW, WRAP_UP, DeadlineWarnings


class Clock:
    def __init__(self, now: float = 1_000_000.0):
        self.now = now

    def __call__(self) -> float:
        return self.now


def _warnings(timeout_minutes: int = 45, step_seconds: float = 60.0):
    clock = Clock()
    started = clock.now
    w = DeadlineWarnings(started + timeout_minutes * 60, started_epoch=started, clock=clock,
                         typical_step=lambda: step_seconds)
    return w, clock


class TestDeadlineWarnings(unittest.TestCase):
    def test_no_deadline_never_warns_or_closes(self):
        w = DeadlineWarnings(None)
        self.assertIsNone(w.due())
        self.assertFalse(w.close_due())

    def test_nothing_before_fifteen_minutes_left(self):
        w, clock = _warnings(45)
        self.assertIsNone(w.due())
        clock.now += 29 * 60
        self.assertIsNone(w.due())

    def test_wrap_up_at_fifteen_then_close_now_at_eight(self):
        w, clock = _warnings(45)
        clock.now += 30 * 60
        stage, text = w.due()
        self.assertEqual(stage, WRAP_UP)
        self.assertIn("running for 30 minutes", text)
        self.assertIn("about 15 minutes", text)
        self.assertIn("next spawn", text)
        w.mark(stage)
        self.assertIsNone(w.due())
        clock.now += 7 * 60
        stage, text = w.due()
        self.assertEqual(stage, CLOSE_NOW)
        self.assertIn("About 8 minutes left", text)
        self.assertIn("the harness closes it for you and keeps your thread", text)
        w.mark(stage)
        self.assertIsNone(w.due())

    def test_unmarked_warning_is_offered_again(self):
        w, clock = _warnings(45)
        clock.now += 31 * 60
        self.assertEqual(w.due()[0], WRAP_UP)
        self.assertEqual(w.due()[0], WRAP_UP)  # inject failed; not marked

    def test_first_check_inside_the_last_lead_gives_only_close_now(self):
        w, clock = _warnings(45)
        clock.now += 40 * 60
        stage, _ = w.due()
        self.assertEqual(stage, CLOSE_NOW)
        w.mark(stage)
        self.assertIsNone(w.due())

    def test_slow_steps_move_both_warnings_earlier(self):
        # §1.7 rule 6: 4 minutes a step, 60-minute limit → 24 and 12 minutes left.
        w, clock = _warnings(60, step_seconds=240)
        self.assertEqual(w.wrap_up_lead(), 24 * 60)
        self.assertEqual(w.close_now_lead(), 12 * 60)
        clock.now += 35 * 60
        self.assertIsNone(w.due())
        clock.now += 60          # 24 minutes left
        self.assertEqual(w.due()[0], WRAP_UP)
        w.mark(WRAP_UP)
        clock.now += 12 * 60     # 12 minutes left
        self.assertEqual(w.due()[0], CLOSE_NOW)

    def test_nothing_in_the_first_half_of_the_limit(self):
        # 10 minutes a step would ask for a wrap-up 60 minutes before a 60-minute limit.
        w, clock = _warnings(60, step_seconds=600)
        clock.now += 29 * 60
        self.assertIsNone(w.due())
        self.assertFalse(w.close_due())
        clock.now += 60
        self.assertIsNotNone(w.due())

    def test_listening_wrap_up_counts_down_to_the_close_now_time(self):
        w, clock = _warnings(45)
        clock.now += 30 * 60
        stage, text = w.due(listening=True)
        self.assertEqual(stage, WRAP_UP)
        self.assertIn("stays open for about 7 more minutes", text)

    def test_tone_has_no_panic_words(self):
        w, clock = _warnings(30)
        clock.now += 15 * 60
        _, wrap = w.due()
        clock.now += 8 * 60
        _, close = w.due()
        for msg in (wrap, close):
            lower = msg.lower()
            for banned in ("critical", "stop all work", "you must", "immediately"):
                self.assertNotIn(banned, lower)
            self.assertIn("cycle end", msg)


class TestCloseDue(unittest.TestCase):
    """§1.3 rule 1: no model call the cycle cannot finish."""

    def test_fast_steps_close_in_the_last_ninety_seconds(self):
        w, clock = _warnings(45, step_seconds=60)
        clock.now += 43 * 60
        self.assertFalse(w.close_due())
        clock.now += 31            # 89 seconds left
        self.assertTrue(w.close_due())

    def test_never_less_than_one_minute(self):
        w, clock = _warnings(45, step_seconds=5)
        clock.now += 45 * 60 - 61
        self.assertFalse(w.close_due())
        clock.now += 2
        self.assertTrue(w.close_due())

    def test_slow_steps_close_earlier(self):
        w, clock = _warnings(60, step_seconds=240)   # 6 minutes
        clock.now += 53 * 60
        self.assertFalse(w.close_due())
        clock.now += 61
        self.assertTrue(w.close_due())

    def test_past_the_limit_is_always_due(self):
        w, clock = _warnings(45)
        clock.now += 46 * 60
        self.assertTrue(w.close_due())

    def test_a_broken_step_clock_falls_back_to_the_fixed_times(self):
        clock = Clock()
        w = DeadlineWarnings(clock.now + 2700, started_epoch=clock.now, clock=clock,
                             typical_step=lambda: "n/a")
        self.assertEqual(w.wrap_up_lead(), 900)
        self.assertEqual(w.close_now_lead(), 480)
        self.assertEqual(w.no_new_step_lead(), 60)


if __name__ == "__main__":
    unittest.main()
