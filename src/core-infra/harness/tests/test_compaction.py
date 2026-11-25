"""Oldest-span selection for compaction frames."""

import unittest

from harness.compaction import frame_slots, select_oldest_span


class _Msg:
    def __init__(self, kind, text="", tool_calls=None):
        self.kind = kind
        self.content = text
        self.tool_calls = tool_calls or []
        self.type = "tool" if kind == "tool" else "ai"

    def __repr__(self):
        return self.kind


class TestSpan(unittest.TestCase):
    def test_extends_through_tool_results(self):
        msgs = [
            _Msg("ai", "a", tool_calls=[{"id": "1"}]),
            _Msg("tool", "result"),
            _Msg("ai", "b"),
            _Msg("ai", "c"),
        ]
        span, rest = select_oldest_span(msgs, 0.35)
        self.assertEqual([m.kind for m in span], ["ai", "tool"])
        self.assertEqual([m.content for m in rest], ["b", "c"])

    def test_slots(self):
        self.assertEqual(frame_slots(10000, 1000), 3)
        self.assertEqual(frame_slots(10000, 0), 1)

    def test_covered_messages_leave_the_verbatim_list(self):
        from harness.compaction import framed_ids, split_verbatim
        msgs = [_Msg("ai", "a"), _Msg("ai", "b"), _Msg("ai", "c")]
        for i, m in enumerate(msgs):
            m.id = f"m{i}"
        covered = framed_ids([{"message_ids": "m0,m1"}])
        self.assertEqual([m.id for m in split_verbatim(msgs, covered)], ["m2"])

    def test_rolled_frame_still_covers_its_messages(self):
        """SPWN-43: a message is framed once. Rolling a frame out of the payload
        must not return its messages to the verbatim list."""
        from harness.compaction import active_frames, framed_ids, split_verbatim
        msgs = [_Msg("ai", str(i)) for i in range(6)]
        for i, m in enumerate(msgs):
            m.id = f"m{i}"
        rows = [
            {"id": 1, "status": "rolled", "message_ids": "m0,m1", "summary": ""},
            {"id": 2, "status": "active", "message_ids": "m2,m3", "summary": "two"},
        ]
        covered = framed_ids(rows)
        self.assertEqual([m.id for m in split_verbatim(msgs, covered)], ["m4", "m5"])
        self.assertEqual([r["id"] for r in active_frames(rows)], [2])

    def test_placeholder_id_is_not_a_message(self):
        from harness.compaction import framed_ids
        self.assertEqual(framed_ids([{"message_ids": "-"}]), set())

    def test_next_span_is_new_messages_after_a_roll(self):
        """The span picked after a roll starts after everything already framed."""
        from harness.compaction import framed_ids, split_verbatim
        msgs = [_Msg("ai", str(i)) for i in range(20)]
        for i, m in enumerate(msgs):
            m.id = f"m{i}"
        rows = [
            {"status": "rolled", "message_ids": ",".join(f"m{i}" for i in range(0, 5))},
            {"status": "active", "message_ids": ",".join(f"m{i}" for i in range(5, 10))},
        ]
        span, _rest = select_oldest_span(split_verbatim(msgs, framed_ids(rows)), 0.35)
        self.assertEqual(span[0].id, "m10")
        self.assertFalse({m.id for m in span} & framed_ids(rows))


class TestFrameTimes(unittest.TestCase):
    START = "2026-11-12T19:00:00Z"

    def _msgs(self, *ids):
        out = []
        for i in ids:
            m = _Msg("ai", i)
            m.id = i
            out.append(m)
        return out

    def test_first_call_stamps_the_cycle_start(self):
        from harness.compaction import note_seen
        seen = {}
        note_seen(seen, self._msgs("old1", "wake"), self.START, "2026-11-12T19:00:40Z")
        self.assertEqual(seen, {"old1": self.START, "wake": self.START})

    def test_later_messages_get_the_time_they_appeared(self):
        from harness.compaction import note_seen
        seen = {}
        note_seen(seen, self._msgs("wake"), self.START, "2026-11-12T19:00:40Z")
        note_seen(seen, self._msgs("wake", "a1"), self.START, "2026-11-12T19:04:00Z")
        note_seen(seen, self._msgs("wake", "a1", "a2"), self.START, "2026-11-12T19:09:00Z")
        self.assertEqual(seen["a1"], "2026-11-12T19:04:00Z")
        self.assertEqual(seen["a2"], "2026-11-12T19:09:00Z")
        self.assertEqual(seen["wake"], self.START)

    def test_end_is_when_the_last_span_message_appeared_not_now(self):
        from harness.compaction import frame_times
        seen = {"a1": "2026-11-12T19:04:00Z", "a2": "2026-11-12T19:09:00Z"}
        start, end = frame_times(
            self._msgs("a1", "a2"), seen, "2026-11-12T19:02:00Z", self.START, "2026-11-12T19:30:00Z",
        )
        self.assertEqual((start, end), ("2026-11-12T19:02:00Z", "2026-11-12T19:09:00Z"))

    def test_first_frame_starts_at_the_cycle_start(self):
        from harness.compaction import frame_times
        start, _end = frame_times(self._msgs("a1"), {"a1": self.START}, None, self.START, "x")
        self.assertEqual(start, self.START)

    def test_end_never_before_start(self):
        from harness.compaction import frame_times
        start, end = frame_times(
            self._msgs("old"), {"old": self.START}, "2026-11-12T19:20:00Z", self.START, "2026-11-12T19:30:00Z",
        )
        self.assertEqual(end, start)

    def test_unseen_message_falls_back_to_now(self):
        from harness.compaction import frame_times
        _start, end = frame_times(self._msgs("x"), {}, None, self.START, "2026-11-12T19:30:00Z")
        self.assertEqual(end, "2026-11-12T19:30:00Z")


try:
    from langchain_core.messages import AIMessage, HumanMessage, ToolMessage, trim_messages
    _HAVE_LANGCHAIN = True
except ImportError:  # system Python has no langchain; the harness venv does
    _HAVE_LANGCHAIN = False


@unittest.skipUnless(_HAVE_LANGCHAIN, "langchain_core not installed")
class TestVerbatimStart(unittest.TestCase):
    """SPWN-44: a human message arriving after the wake was framed must not cut
    the turns before it."""

    def _pairs(self, n):
        out = []
        for i in range(n):
            out.append(AIMessage(content=f"work {i}", id=f"a{i}",
                                 tool_calls=[{"name": "t", "args": {}, "id": f"c{i}"}]))
            out.append(ToolMessage(content=f"result {i}", tool_call_id=f"c{i}", id=f"t{i}"))
        return out

    @staticmethod
    def _count(msgs):
        return sum(len(str(m.content)) + 40 for m in msgs)

    def _trim(self, verbatim, has_block, budget=10**6):
        from harness.compaction import verbatim_start_types
        return trim_messages(
            verbatim, max_tokens=budget, strategy="last", token_counter=self._count,
            include_system=True, allow_partial=False, start_on=verbatim_start_types(has_block),
        )

    def test_old_rule_dropped_the_work_before_a_time_warning(self):
        verbatim = self._pairs(6) + [HumanMessage(content="TIME CHECK", id="h1")]
        self.assertEqual([m.id for m in self._trim(verbatim, has_block=False)], ["h1"])

    def test_with_a_frames_block_the_work_is_kept(self):
        verbatim = self._pairs(6) + [HumanMessage(content="TIME CHECK", id="h1")]
        kept = self._trim(verbatim, has_block=True)
        self.assertEqual(len(kept), 13)
        self.assertEqual(kept[0].id, "a0")

    def test_never_starts_at_a_tool_result(self):
        verbatim = self._pairs(6) + [HumanMessage(content="TIME CHECK", id="h1")]
        budget = self._count(verbatim[-4:])  # cut lands on a tool result
        kept = self._trim(verbatim, has_block=True, budget=budget)
        self.assertEqual([m.id for m in kept], ["a5", "t5", "h1"])


class TestHookWiring(unittest.TestCase):
    """SPWN-43: the harness reads every frame of the thread for coverage and
    sends only the active ones."""

    def setUp(self):
        import os
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(here, "agent_harness.py"), encoding="utf-8") as handle:
            self.text = handle.read()

    def test_no_read_is_limited_to_active_frames(self):
        import re
        calls = re.findall(r'\["agictl", "cycle", "frames", "list"[^\]]*\]', self.text, flags=re.S)
        self.assertEqual(len(calls), 2)
        for call in calls:
            self.assertNotIn("--status", call)
        self.assertEqual(self.text.count('"--harness"'), 2)  # pre_model_hook + resume

    def test_block_is_built_from_active_frames_only(self):
        self.assertEqual(self.text.count("_frames_block(active_frames(frames))"), 2)
        self.assertNotIn("_frames_block(frames)", self.text)

    def test_resume_removes_framed_messages(self):
        self.assertIn("unframed = split_verbatim(current, framed_ids(_rows))", self.text)
        self.assertIn("anchor_ai=_has_frames", self.text)
        self.assertIn("changed = changed or _framed > 0", self.text)


class TestNote(unittest.TestCase):
    def test_note_names_agictl_not_databases(self):
        from harness.compaction import HARNESS_NOTE
        self.assertIn("agictl", HARNESS_NOTE)
        self.assertNotIn("database", HARNESS_NOTE.lower())


if __name__ == "__main__":
    unittest.main()
