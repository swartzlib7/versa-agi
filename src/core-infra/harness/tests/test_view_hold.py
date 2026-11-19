"""Viewed media held for the cycle (state_spawn_context.md §1.15, SPWN-59).

Run:  python -m unittest harness.tests.test_view_hold   (from core-infra)
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

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage  # noqa: E402

from harness.view_hold import MAX_HELD, ViewHold, hold_limit
from model_drivers.message_adapters import content_has_image_parts, trim_image_parts_from_message


def _media(n):
    return [
        {"type": "text", "text": f"Agent requested view of image at /tmp/p{n}.jpg"},
        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,AAAA{n}"}},
    ]


def _note(n):
    return HumanMessage(
        content=trim_image_parts_from_message(_media(n), f"/tmp/p{n}.jpg"), id=f"view-inject-{n}",
    )


class TestHoldLimit(unittest.TestCase):
    def test_large_window_holds_the_maximum(self):
        self.assertEqual(hold_limit(2_232_268), MAX_HELD)
        self.assertEqual(MAX_HELD, 20)

    def test_small_window_holds_fewer_and_at_least_one(self):
        self.assertEqual(hold_limit(256_000), 4)
        self.assertEqual(hold_limit(77_000), 1)
        self.assertEqual(hold_limit(32_000), 1)


class TestExpand(unittest.TestCase):
    def test_the_note_is_saved_and_the_media_is_sent(self):
        hold = ViewHold()
        hold.hold("view-inject-1", _media(1))
        saved = [HumanMessage(content="wake", id="w"), _note(1)]
        self.assertFalse(content_has_image_parts(saved[1].content))  # what the checkpoint holds
        sent = hold.expand(saved)
        self.assertTrue(content_has_image_parts(sent[1].content))
        self.assertEqual(sent[1].id, "view-inject-1")
        self.assertIs(sent[0], saved[0])
        self.assertFalse(content_has_image_parts(saved[1].content))  # the saved list is untouched

    def test_every_call_of_the_cycle_sends_the_same_bytes(self):
        """The fault of 2026-11-19: the photo was in one call and gone in the next."""
        hold = ViewHold()
        history = [HumanMessage(content="wake", id="w")]
        sent_before = None
        for n in range(1, 15):  # 14 photos, one after another
            hold.hold(f"view-inject-{n}", _media(n))
            history += [_note(n), AIMessage(content=f"saw {n}", id=f"a{n}")]
            sent = hold.expand(history)
            if sent_before is not None:
                shared = len(sent_before)
                self.assertEqual(
                    [(m.id, m.content) for m in sent[:shared]],
                    [(m.id, m.content) for m in sent_before],
                    f"the history before photo {n} changed",
                )
            sent_before = sent
        self.assertEqual(sum(content_has_image_parts(m.content) for m in sent_before), 14)

    def test_a_new_cycle_sends_the_note_only(self):
        """The hold lives in the process: a later cycle has none and resends nothing."""
        saved = [HumanMessage(content="wake", id="w"), _note(1)]
        sent = ViewHold().expand(saved)
        self.assertIs(sent, saved)
        self.assertFalse(content_has_image_parts(sent[1].content))

    def test_only_a_human_message_with_a_held_id_is_replaced(self):
        hold = ViewHold()
        hold.hold("x", _media(1))
        other = [AIMessage(content="reply", id="x"), ToolMessage(content="r", tool_call_id="c", id="t")]
        self.assertEqual([m.content for m in hold.expand(other)], ["reply", "r"])


class TestLimit(unittest.TestCase):
    def test_past_the_limit_the_oldest_half_become_notes_at_once(self):
        hold = ViewHold(limit=20)
        changes = [hold.hold(f"view-inject-{n}", _media(n)) for n in range(1, 31)]
        # 30 photos: the history changes once, at photo 21, not at every photo.
        self.assertEqual([n for n, c in enumerate(changes, 1) if c], [21])
        self.assertEqual(changes[20], 11)
        self.assertEqual(len(hold), 19)
        notes = [_note(n) for n in range(1, 31)]
        sent = hold.expand(notes)
        self.assertFalse(content_has_image_parts(sent[10].content))  # photo 11: a note
        self.assertTrue(content_has_image_parts(sent[11].content))   # photo 12: held
        self.assertTrue(content_has_image_parts(sent[29].content))

    def test_limit_one_keeps_the_newest(self):
        hold = ViewHold(limit=1)
        self.assertEqual(hold.hold("a", _media(1)), 0)
        self.assertEqual(hold.hold("b", _media(2)), 1)
        self.assertEqual(len(hold), 1)
        sent = hold.expand([_note(1), HumanMessage(content=_note(2).content, id="b")])
        self.assertTrue(content_has_image_parts(sent[1].content))


class TestVideoIsSentOnce(unittest.TestCase):
    """A video can be 200 MB: it goes with one model call, then it is a note."""

    def test_a_video_is_sent_with_the_next_call_only(self):
        hold = ViewHold()
        hold.hold("view-inject-1", _media(1))             # a photo
        hold.hold("view-inject-2", _media(2), once=True)  # a video
        saved = [_note(1), _note(2)]
        sent = hold.expand(saved)
        self.assertTrue(content_has_image_parts(sent[0].content))
        self.assertTrue(content_has_image_parts(sent[1].content))
        self.assertEqual(hold.release_once(), 1)          # the model replied
        sent = hold.expand(saved)
        self.assertTrue(content_has_image_parts(sent[0].content))   # the photo stays
        self.assertFalse(content_has_image_parts(sent[1].content))  # the video is a note
        self.assertEqual(hold.release_once(), 0)

    def test_photos_alone_release_nothing(self):
        hold = ViewHold()
        hold.hold("view-inject-1", _media(1))
        self.assertEqual(hold.release_once(), 0)
        self.assertEqual(len(hold), 1)

    def test_harness_sends_a_video_once(self):
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(here, "agent_harness.py"), encoding="utf-8") as handle:
            text = handle.read()
        self.assertIn('_is_video = tool_name == "agictl_view_video"', text)
        self.assertIn("inject_msg.id, inject_msg.content, once=_is_video,", text)
        self.assertIn("if view_hold.release_once():", text)


class TestHarnessWiring(unittest.TestCase):
    def setUp(self):
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(here, "agent_harness.py"), encoding="utf-8") as handle:
            self.text = handle.read()

    def test_media_is_put_back_before_anything_is_counted_or_trimmed(self):
        hook = self.text[self.text.index("    def pre_model_hook(state):"):]
        first = hook.index('all_msgs = view_hold.expand(list(state["messages"]))')
        self.assertLess(first, hook.index("trim_messages(verbatim"))
        self.assertLess(first, hook.index("split_verbatim(all_msgs, covered)"))

    def test_the_checkpoint_gets_the_note(self):
        at = self.text.index("became_notes = view_hold.hold(")
        queued = self.text.index("pending_view_injects.append(HumanMessage(", at)
        block = self.text[queued:queued + 400]
        self.assertIn("trim_image_parts_from_message(", block)
        self.assertNotIn("pending_view_injects.append(inject_msg)", self.text)

    def test_nothing_is_removed_between_two_calls(self):
        self.assertNotIn("pending_view_trims", self.text)
        loop = self.text[self.text.index("pending_view_injects: list[HumanMessage] = []"):
                         self.text.index("write_telemetry(args.agent, total_tokens")]
        self.assertNotIn("_trim_view_inject_payloads(", loop)

    def test_old_checkpoints_are_still_cleaned_at_resume_and_cycle_end(self):
        self.assertIn("stale_views = _trim_view_inject_payloads(agent, config)", self.text)
        tail = self.text[self.text.index("write_telemetry(args.agent, total_tokens"):]
        self.assertIn("_trim_view_inject_payloads(agent, config)", tail)


if __name__ == "__main__":
    unittest.main()
