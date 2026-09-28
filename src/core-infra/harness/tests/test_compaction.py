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

    def test_note_names_agictl_not_databases(self):
        from harness.compaction import HARNESS_NOTE
        self.assertIn("agictl", HARNESS_NOTE)
        self.assertNotIn("database", HARNESS_NOTE.lower())


if __name__ == "__main__":
    unittest.main()
