"""The resume tail and the frame written for what falls off it
(state_spawn_context.md §1.8 rules 7 to 9, SPWN-56..58).

Run:  python -m unittest harness.tests.test_resume_frame   (from core-infra)
"""

# This module needs langchain_core. An interpreter without it skips the whole module with that
# reason instead of failing to load; the harness interpreter has it and runs it. run_tests.py
# checks that every test passes on at least one interpreter.
import importlib.util as _importlib_util
import unittest as _unittest

if _importlib_util.find_spec("langchain_core") is None:
    raise _unittest.SkipTest("langchain_core is not installed in this interpreter; "
                             "the harness interpreter runs this module")

import os
import unittest
from datetime import datetime, timedelta, timezone

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage  # noqa: E402

from harness.agent_harness import _canonicalize_messages, _unresolved_tool_call_ids
from harness.compaction import (
    FRAMES_BLOCK_MAX_CHARS, frame_slots, resume_frame_times, resume_span, utc_stamp,
)
from harness.model_context import effective_resume_cap, resume_message_cap


def _thread(cycles, steps):
    """`cycles` wakes, each followed by `steps` tool-call pairs."""
    out = []
    for c in range(cycles):
        out.append(HumanMessage(content=f"wake {c}", id=f"h{c}"))
        for i in range(steps):
            call = f"c{c}-{i}"
            out.append(AIMessage(content="", id=f"a{c}-{i}",
                                 tool_calls=[{"name": "agictl_task", "args": {}, "id": call}]))
            out.append(ToolMessage(content="ok", tool_call_id=call, id=f"t{c}-{i}"))
    return out


class TestStoredValueIsHonoured(unittest.TestCase):
    """SPWN-56: COA stored 50 and resumed with 200."""

    def test_stored_value_wins_on_a_large_window(self):
        self.assertEqual(resume_message_cap(1_000_000), 200)
        self.assertEqual(effective_resume_cap(50, 1_000_000), 50)
        self.assertEqual(effective_resume_cap(20, 1_000_000), 20)

    def test_the_window_is_the_ceiling(self):
        self.assertEqual(effective_resume_cap(50, 32_768), resume_message_cap(32_768))
        self.assertEqual(effective_resume_cap(50, 128_000), 27)
        self.assertEqual(effective_resume_cap(300, 1_000_000), 200)

    def test_zero_means_the_window_rule(self):
        self.assertEqual(effective_resume_cap(0, 1_000_000), 200)
        self.assertEqual(effective_resume_cap(-1, 256_000), 53)
        self.assertEqual(effective_resume_cap(None, 64_000), 13)


class TestResumeSpan(unittest.TestCase):
    """SPWN-57: what the cut removes is exactly what the frame covers."""

    def test_span_and_tail_together_are_the_whole_thread(self):
        thread = _thread(cycles=4, steps=30)  # 244 messages
        tail, _, _ = _canonicalize_messages(thread, 50, anchor_ai=True)
        span = resume_span(thread, tail)
        self.assertEqual([m.id for m in span] + [m.id for m in tail], [m.id for m in thread])
        self.assertLessEqual(len(tail), 51)
        self.assertGreater(len(span), 190)

    def test_the_cut_never_splits_a_tool_call_from_its_result(self):
        thread = _thread(cycles=3, steps=40)
        for cap in (12, 25, 50, 51, 99):
            tail, _, stats = _canonicalize_messages(thread, cap, anchor_ai=True)
            span = resume_span(thread, tail)
            self.assertNotIsInstance(tail[0], ToolMessage)
            self.assertEqual(stats["placeholders"], 0, cap)
            self.assertEqual(_unresolved_tool_call_ids(span), set(), cap)

    def test_cutting_the_remaining_tail_again_changes_nothing(self):
        """After the frame is stored the harness cuts `thread minus span` with the same cap."""
        thread = _thread(cycles=4, steps=30)
        tail, _, _ = _canonicalize_messages(thread, 50, anchor_ai=True)
        rest = thread[len(resume_span(thread, tail)):]
        again, changed, stats = _canonicalize_messages(rest, 50, anchor_ai=True)
        self.assertFalse(changed)
        self.assertEqual(stats["trimmed"], 0)
        self.assertEqual([m.id for m in again], [m.id for m in tail])

    def test_a_thread_within_the_tail_has_no_span(self):
        thread = _thread(cycles=1, steps=10)
        tail, _, _ = _canonicalize_messages(thread, 50, anchor_ai=True)
        self.assertEqual(resume_span(thread, tail), [])

    def test_no_tail_means_no_span(self):
        self.assertEqual(resume_span(_thread(1, 5), []), [])

    def test_a_repair_placeholder_at_the_front_of_the_tail_is_skipped(self):
        thread = _thread(cycles=2, steps=10)
        placeholder = ToolMessage(content="x", tool_call_id="new", id="fresh-id")
        span = resume_span(thread, [placeholder] + thread[30:])
        self.assertEqual(len(span), 30)


class TestResumeFrameTimes(unittest.TestCase):
    def test_from_the_newest_frame_to_when_the_thread_was_last_saved(self):
        self.assertEqual(
            resume_frame_times("2026-11-19T07:00:35Z", "2026-11-19T11:34:10Z", "2026-11-19T11:34:40Z"),
            ("2026-11-19T07:00:35Z", "2026-11-19T11:34:10Z"),
        )

    def test_first_frame_of_a_thread_starts_and_ends_at_the_last_save(self):
        self.assertEqual(
            resume_frame_times(None, "2026-11-19T11:34:10Z", "2026-11-19T11:34:40Z"),
            ("2026-11-19T11:34:10Z", "2026-11-19T11:34:10Z"),
        )

    def test_never_ends_before_it_starts(self):
        start, end = resume_frame_times("2026-11-19T12:00:00Z", "2026-11-19T11:00:00Z", "x")
        self.assertEqual((start, end), ("2026-11-19T12:00:00Z", "2026-11-19T12:00:00Z"))

    def test_unknown_save_time_falls_back_to_now(self):
        self.assertEqual(resume_frame_times(None, "", "2026-11-19T11:34:40Z")[1], "2026-11-19T11:34:40Z")

    def test_utc_stamp_reads_what_the_checkpoint_gives(self):
        self.assertEqual(utc_stamp("2026-11-19T09:34:10.123456+00:00"), "2026-11-19T09:34:10Z")
        self.assertEqual(utc_stamp("2026-11-19T11:34:10+02:00"), "2026-11-19T09:34:10Z")
        self.assertEqual(utc_stamp("2026-11-19T09:34:10"), "2026-11-19T09:34:10Z")
        local = datetime(2026, 11, 19, 11, 34, 10, tzinfo=timezone(timedelta(hours=2)))
        self.assertEqual(utc_stamp(local), "2026-11-19T09:34:10Z")
        self.assertEqual(utc_stamp(None), "")
        self.assertEqual(utc_stamp("not a time"), "")


class TestFramesBlockSize(unittest.TestCase):
    """SPWN-58: on a 1M-token model 35% of the window is about 240,000 tokens of summaries."""

    def test_large_window_is_held_to_the_block_limit(self):
        self.assertEqual(FRAMES_BLOCK_MAX_CHARS, 40_000)
        self.assertEqual(frame_slots(2_232_268, 4_000), 10)
        self.assertEqual(frame_slots(2_232_268, 50_000), 1)

    def test_small_window_keeps_the_35_percent_rule(self):
        self.assertEqual(frame_slots(77_000, 3_000), 8)  # 26,950 characters
        self.assertEqual(frame_slots(10_000, 1_000), 3)


class TestHarnessWiring(unittest.TestCase):
    def setUp(self):
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(here, "agent_harness.py"), encoding="utf-8") as handle:
            text = handle.read()
        self.text = text
        start = text.index("resume_cap = effective_resume_cap(args.resume_max_messages, window)")
        self.resume = text[start:text.index("pending_next = tuple(getattr(snapshot", start)]

    def test_the_tail_is_the_stored_value_under_the_window_ceiling(self):
        self.assertIn("resume_ceiling = resume_message_cap(window)", self.text)
        self.assertNotIn("resume_cap = resume_message_cap(window)", self.text)

    def test_the_frame_is_stored_before_anything_is_cut(self):
        stored = self.resume.index("_stored = _store_frame(_span, _start, _end)")
        cut = self.resume.index("clean, changed, stats = _canonicalize_messages(")
        self.assertLess(stored, cut)
        self.assertIn("if len(unframed) > resume_cap:", self.resume[:stored])

    def test_without_a_stored_frame_the_cut_is_at_the_ceiling(self):
        failed = self.resume[self.resume.index("elif _span:"):]
        self.assertIn("resume_cap = resume_ceiling", failed)
        self.assertLess(failed.index("resume_cap = resume_ceiling"),
                        failed.index("clean, changed, stats = _canonicalize_messages("))
        self.assertIn('if _stored and _stored.get("success"):', self.resume)

    def test_a_failed_summary_call_does_not_stop_the_resume(self):
        call = self.resume.index("_stored = _store_frame(_span, _start, _end)")
        self.assertIn("try:", self.resume[:call][-600:])
        self.assertIn("except Exception as exc:", self.resume[call:call + 200])

    def test_the_summarized_span_leaves_the_checkpoint_and_the_frames_block_leads(self):
        done = self.resume[self.resume.index('if _stored and _stored.get("success"):'):
                           self.resume.index("elif _span:")]
        self.assertIn("unframed = unframed[len(_span):]", done)
        self.assertIn("_has_frames = True", done)
        self.assertIn("_framed += len(_span)", done)
        self.assertIn("_roll_extra_frames(_rows)", done)

    def test_the_hook_and_the_resume_write_frames_with_the_same_code(self):
        self.assertEqual(self.text.count('"agictl", "cycle", "frames", "add"'), 1)
        self.assertEqual(self.text.count('"agictl", "cycle", "frames", "roll"'), 1)
        self.assertIn("return _store_frame(span, frame_start, frame_end)", self.text)


if __name__ == "__main__":
    unittest.main()
