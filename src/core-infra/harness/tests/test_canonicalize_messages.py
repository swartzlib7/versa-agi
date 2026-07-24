"""Unit tests for harness checkpoint repair.

Covers :func:`harness.agent_harness._canonicalize_messages` (the pure
checkpoint-history repair) and the RESUME reseed predicate that guards against
interrupted-superstep PENDING WRITES (the thread 93-0 incident): even when the
committed transcript is clean (``changed=False``), a non-empty ``snapshot.next``
must still force an atomic reseed.

Run:  python -m unittest harness.tests.test_canonicalize_messages   (from core-infra)
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from langchain_core.messages import (  # noqa: E402
    AIMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)

from harness.agent_harness import (  # noqa: E402
    _canonicalize_messages,
    _stream_suffix_not_in_checkpoint,
    _unresolved_tool_call_ids,
)


def _ai_tool_call(text, call_id, name="agictl_task"):
    return AIMessage(
        content=text,
        tool_calls=[{"name": name, "args": {}, "id": call_id}],
        id=f"ai-{call_id}",
    )


def _resume_should_reseed(changed: bool, pending_next) -> bool:
    """Mirror of the harness RESUME guard predicate (agent_harness, ~L1713).

    The harness reseeds when ``changed or pending_next`` — pending writes from an
    interrupted superstep are invisible to canonicalize, so they must reseed on
    their own even when the committed transcript looks clean.
    """
    return bool(changed) or bool(tuple(pending_next or ()))


class TestCanonicalizeMessages(unittest.TestCase):
    def test_clean_history_unchanged(self):
        msgs = [
            SystemMessage(content="sys", id="s1"),
            HumanMessage(content="hi", id="h1"),
            _ai_tool_call("calling", "c1"),
            ToolMessage(content="ok", tool_call_id="c1", name="agictl_task", id="t1"),
            AIMessage(content="done", id="a2"),
        ]
        clean, changed, stats = _canonicalize_messages(msgs)
        self.assertFalse(changed)
        self.assertEqual([m.id for m in clean], [m.id for m in msgs])
        self.assertEqual(stats["placeholders"], 0)
        self.assertEqual(stats["orphans"], 0)

    def test_dangling_tool_call_gets_placeholder(self):
        # AIMessage requests a tool, but the ToolMessage never landed (crash).
        msgs = [
            HumanMessage(content="hi", id="h1"),
            _ai_tool_call("calling", "c1"),
        ]
        clean, changed, stats = _canonicalize_messages(msgs)
        self.assertTrue(changed)
        self.assertEqual(stats["placeholders"], 1)
        self.assertIsInstance(clean[-1], ToolMessage)
        self.assertEqual(clean[-1].tool_call_id, "c1")

    def test_dangling_call_buried_midhistory(self):
        # A dangling call in the MIDDLE must be repaired adjacent to its parent,
        # not only at the tail.
        msgs = [
            HumanMessage(content="hi", id="h1"),
            _ai_tool_call("call A", "c1"),
            # missing ToolMessage for c1
            HumanMessage(content="next", id="h2"),
            _ai_tool_call("call B", "c2"),
            ToolMessage(content="ok B", tool_call_id="c2", name="agictl_task", id="t2"),
        ]
        clean, changed, stats = _canonicalize_messages(msgs)
        self.assertTrue(changed)
        self.assertEqual(stats["placeholders"], 1)
        # placeholder for c1 sits immediately after its producing AIMessage
        idx = next(i for i, m in enumerate(clean)
                   if isinstance(m, ToolMessage) and m.tool_call_id == "c1")
        self.assertIsInstance(clean[idx - 1], AIMessage)
        self.assertEqual(clean[idx - 1].tool_calls[0]["id"], "c1")

    def test_orphan_tool_message_dropped(self):
        msgs = [
            HumanMessage(content="hi", id="h1"),
            ToolMessage(content="stray", tool_call_id="zzz", name="agictl_task", id="t9"),
            AIMessage(content="hello", id="a1"),
        ]
        clean, changed, stats = _canonicalize_messages(msgs)
        self.assertTrue(changed)
        self.assertGreaterEqual(stats["orphans"], 1)
        self.assertNotIn("t9", [m.id for m in clean])

    def test_parallel_calls_one_missing(self):
        msgs = [
            HumanMessage(content="hi", id="h1"),
            AIMessage(content="batch", tool_calls=[
                {"name": "agictl_task", "args": {}, "id": "c1"},
                {"name": "agictl_cycle", "args": {}, "id": "c2"},
            ]),
            ToolMessage(content="ok1", tool_call_id="c1", name="agictl_task", id="t1"),
            # c2 result missing
        ]
        clean, changed, stats = _canonicalize_messages(msgs)
        self.assertTrue(changed)
        self.assertEqual(stats["placeholders"], 1)
        answered = [m.tool_call_id for m in clean if isinstance(m, ToolMessage)]
        self.assertEqual(sorted(answered), ["c1", "c2"])

    def test_depth_trim_anchors_on_human(self):
        msgs = [SystemMessage(content="sys", id="s1")]
        for i in range(8):
            msgs.append(HumanMessage(content=f"h{i}", id=f"h{i}"))
            msgs.append(AIMessage(content=f"a{i}", id=f"a{i}"))
        clean, changed, stats = _canonicalize_messages(msgs, max_msgs=5)
        self.assertTrue(changed)
        self.assertGreater(stats["trimmed"], 0)
        # System preserved at head; first non-system message is a HumanMessage.
        self.assertIsInstance(clean[0], SystemMessage)
        self.assertIsInstance(clean[1], HumanMessage)


class TestResumeReseedGuard(unittest.TestCase):
    def test_clean_transcript_alone_does_not_reseed(self):
        msgs = [
            HumanMessage(content="hi", id="h1"),
            _ai_tool_call("calling", "c1"),
            ToolMessage(content="ok", tool_call_id="c1", name="agictl_task", id="t1"),
        ]
        _clean, changed, _stats = _canonicalize_messages(msgs)
        self.assertFalse(changed)
        self.assertFalse(_resume_should_reseed(changed, ()))

    def test_pending_writes_force_reseed_on_clean_transcript(self):
        # The thread 93-0 fix: committed transcript is clean, but the prior cycle
        # was interrupted mid-superstep → snapshot.next is non-empty → reseed.
        msgs = [
            HumanMessage(content="hi", id="h1"),
            _ai_tool_call("calling", "c1"),
            ToolMessage(content="ok", tool_call_id="c1", name="agictl_task", id="t1"),
        ]
        _clean, changed, _stats = _canonicalize_messages(msgs)
        self.assertFalse(changed)
        self.assertTrue(_resume_should_reseed(changed, ("pre_model_hook",)))
        self.assertTrue(_resume_should_reseed(changed, ("tools",)))

    def test_dirty_transcript_reseeds_regardless_of_next(self):
        self.assertTrue(_resume_should_reseed(True, ()))
        self.assertTrue(_resume_should_reseed(True, ("tools",)))


class TestViewReinvokeFlushHelpers(unittest.TestCase):
    """VIEW RE-INVOKE pending-writes race (5× crash 2026-07-24 on thread 1-26)."""

    def test_suffix_is_tool_results_when_ai_message_already_committed(self):
        # Agent node committed the parallel tool_calls AIMessage; tools streamed
        # locally but were not committed before break-and-reinvoke.
        ai = AIMessage(
            content="batch",
            id="ai-batch",
            tool_calls=[
                {"name": "agictl_view_image", "args": {"path": "/tmp/a.png"}, "id": "c1"},
                {"name": "agictl_execute", "args": {"command": "true"}, "id": "c2"},
            ],
        )
        t1 = ToolMessage(content='{"success":true}', tool_call_id="c1",
                         name="agictl_view_image", id="t1")
        t2 = ToolMessage(content='{"success":true}', tool_call_id="c2",
                         name="agictl_execute", id="t2")
        committed = [
            HumanMessage(content="wake", id="h0"),
            ai,
        ]
        streamed = [ai, t1, t2]
        suffix = _stream_suffix_not_in_checkpoint(committed, streamed)
        self.assertEqual([m.id for m in suffix], ["t1", "t2"])
        merged = committed + suffix
        self.assertFalse(_unresolved_tool_call_ids(merged))
        clean, changed, stats = _canonicalize_messages(merged)
        self.assertFalse(changed)
        self.assertEqual(stats["placeholders"], 0)
        self.assertEqual(len(clean), 4)

    def test_suffix_includes_ai_when_neither_committed(self):
        ai = _ai_tool_call("calling", "c1", name="agictl_view_image")
        t1 = ToolMessage(content="ok", tool_call_id="c1",
                         name="agictl_view_image", id="t1")
        committed = [HumanMessage(content="wake", id="h0")]
        streamed = [ai, t1]
        suffix = _stream_suffix_not_in_checkpoint(committed, streamed)
        self.assertEqual([m.id for m in suffix], [ai.id, "t1"])

    def test_unresolved_committed_matches_crash_shape(self):
        # The INVALID_CHAT_HISTORY shape from the five failed cycles: committed
        # AIMessage with parallel view+execute, no ToolMessages yet.
        committed = [
            AIMessage(
                content="",
                id="ai-x",
                tool_calls=[
                    {"name": "agictl_view_image", "args": {}, "id": "c1"},
                    {"name": "agictl_execute", "args": {}, "id": "c2"},
                ],
            ),
        ]
        pending = _unresolved_tool_call_ids(committed)
        self.assertEqual(pending, {"c1", "c2"})


if __name__ == "__main__":
    unittest.main()
