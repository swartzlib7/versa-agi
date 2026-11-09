"""Time warnings before Lifeline's hard stop (state_live_chat.md §1.5.3, LCH-04)."""

import unittest

from harness.deadline import FIVE_MINUTES, TEN_MINUTES, DeadlineWarnings


class Clock:
    def __init__(self, now: float = 1_000_000.0):
        self.now = now

    def __call__(self) -> float:
        return self.now


def _warnings(timeout_minutes: int = 45):
    clock = Clock()
    started = clock.now
    return DeadlineWarnings(started + timeout_minutes * 60, started_epoch=started, clock=clock), clock


class TestDeadlineWarnings(unittest.TestCase):
    def test_no_deadline_never_warns(self):
        w = DeadlineWarnings(None)
        self.assertIsNone(w.due())

    def test_nothing_before_ten_minutes_left(self):
        w, clock = _warnings(45)
        self.assertIsNone(w.due())
        clock.now += 34 * 60
        self.assertIsNone(w.due())

    def test_ten_then_five(self):
        w, clock = _warnings(45)
        clock.now += 35 * 60
        stage, text = w.due()
        self.assertEqual(stage, TEN_MINUTES)
        self.assertIn("running for 35 minutes", text)
        self.assertIn("about 10 minutes", text)
        self.assertIn("next spawn", text)
        w.mark(stage)
        self.assertIsNone(w.due())
        clock.now += 5 * 60
        stage, text = w.due()
        self.assertEqual(stage, FIVE_MINUTES)
        self.assertIn("About 5 minutes left", text)
        w.mark(stage)
        self.assertIsNone(w.due())

    def test_unmarked_warning_is_offered_again(self):
        w, clock = _warnings(45)
        clock.now += 36 * 60
        self.assertEqual(w.due()[0], TEN_MINUTES)
        self.assertEqual(w.due()[0], TEN_MINUTES)  # inject failed; not marked

    def test_first_check_inside_five_minutes_gives_only_five(self):
        w, clock = _warnings(45)
        clock.now += 42 * 60
        stage, _ = w.due()
        self.assertEqual(stage, FIVE_MINUTES)
        w.mark(stage)
        self.assertIsNone(w.due())

    def test_tone_has_no_panic_words(self):
        w, clock = _warnings(30)
        clock.now += 20 * 60
        _, ten = w.due()
        clock.now += 6 * 60
        _, five = w.due()
        for msg in (ten, five):
            lower = msg.lower()
            for banned in ("critical", "stop all work", "you must", "immediately"):
                self.assertNotIn(banned, lower)
            self.assertIn("cycle end", msg)


if __name__ == "__main__":
    unittest.main()
