"""Refusals and web search sources read from a model reply (§2.7 B6, NM-09)."""

from __future__ import annotations

import os
import sys
import unittest

CORE_INFRA = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, CORE_INFRA)

from harness.model_reply import (  # noqa: E402
    REFUSAL_CLOSED_MARK,
    REFUSALS_BEFORE_CLOSE,
    refusal_closed_line,
    refusal_note,
    refusal_reason,
    refusal_summary,
    search_log_line,
    search_sources,
)
import provider_alerts  # noqa: E402


class Reply:
    def __init__(self, content="", meta=None, tool_calls=None, extra=None):
        self.content = content
        self.response_metadata = meta or {}
        self.tool_calls = tool_calls or []
        self.additional_kwargs = extra or {}


class TestRefusal(unittest.TestCase):
    def test_anthropic_refusal_with_a_category(self):
        reply = Reply([], {"stop_reason": "refusal", "stop_details": {"category": "cyber"}})
        self.assertEqual(refusal_reason(reply), "cyber")

    def test_anthropic_refusal_without_details(self):
        self.assertEqual(refusal_reason(Reply("", {"stop_reason": "refusal"})), "refusal")

    def test_an_ordinary_reply_is_not_a_refusal(self):
        self.assertIsNone(refusal_reason(Reply("done", {"stop_reason": "end_turn"})))
        self.assertIsNone(refusal_reason(Reply("done", {"finish_reason": "stop"})))
        self.assertIsNone(refusal_reason(Reply("", {})))

    def test_content_filter_with_no_text_is_a_refusal(self):
        self.assertEqual(refusal_reason(Reply("", {"finish_reason": "content_filter"})), "content_filter")

    def test_content_filter_that_still_answered_is_not(self):
        self.assertIsNone(refusal_reason(Reply("partial answer", {"finish_reason": "content_filter"})))

    def test_a_reply_with_a_tool_call_is_not(self):
        reply = Reply("", {"stop_reason": "refusal"}, tool_calls=[{"name": "add"}])
        self.assertIsNone(refusal_reason(reply))

    def test_summary_names_the_model_and_the_reason(self):
        text = refusal_summary("bio", "claude-fable-5-2")
        self.assertIn("claude-fable-5-2", text)
        self.assertIn("bio", text)
        self.assertIn("twice", text)
        self.assertTrue(text.startswith("[Harness]"))

    def test_the_note_tells_the_agent_what_to_do_and_what_happens_next(self):
        note = refusal_note("content_filter")
        self.assertTrue(note.startswith("[Harness]"))
        self.assertIn("content_filter", note)
        self.assertIn("discarded", note)
        self.assertIn("tell the person who asked", note)
        self.assertIn("end the cycle", note)
        self.assertIn("Primary User is told", note)

    def test_the_agent_gets_one_turn_before_the_cycle_is_closed(self):
        self.assertEqual(REFUSALS_BEFORE_CLOSE, 2)


class TestRefusalAlert(unittest.TestCase):
    """Lifeline reads the closed-refusal line from the cycle log (provider_alerts)."""

    LOG = (
        "[2026-11-14 10:00:00] EXECUTION MODEL: claude-fable-5-2 (assigned=claude-fable-5-2, mode=none)\n"
        "[2026-11-14 10:00:09] [REFUSAL] claude-fable-5-2 declined a reply (reason: cyber)\n"
        "[2026-11-14 10:00:09] [REFUSAL] The declined reply was discarded and the agent was told\n"
        "[2026-11-14 10:00:15] [REFUSAL] claude-fable-5-2 declined a reply (reason: cyber)\n"
        "[2026-11-14 10:00:15] " + refusal_closed_line("cyber", "claude-fable-5-2") + "\n"
    )

    def test_the_harness_line_carries_the_mark_lifeline_looks_for(self):
        line = refusal_closed_line("cyber", "claude-fable-5-2")
        self.assertTrue(line.startswith(REFUSAL_CLOSED_MARK))
        self.assertIsNotNone(provider_alerts._REFUSAL_CLOSED.search(line))

    def test_a_closed_refusal_is_its_own_kind_and_asks_for_a_wait(self):
        info = provider_alerts.classify_text(self.LOG)
        self.assertEqual(info["kind"], provider_alerts.KIND_REFUSAL)
        self.assertTrue(info["fatal"])  # the cycle exits 0; this is what sets the wait
        self.assertEqual(info["model"], "claude-fable-5-2")
        self.assertIn("cyber", info["detail"])

    def test_one_refusal_the_agent_handled_is_not_an_alert(self):
        log = "\n".join(self.LOG.splitlines()[:3]) + "\n[STEP 3/300] AGENT → tool call: agictl_message\n"
        self.assertEqual(provider_alerts.classify_text(log)["kind"], provider_alerts.KIND_NONE)

    def test_the_primary_user_message(self):
        text = provider_alerts.format_pu_message("coa", provider_alerts.classify_text(self.LOG))
        self.assertIn("The model declined a request", text)
        self.assertIn("Agent: coa", text)
        self.assertIn("Model: claude-fable-5-2", text)
        self.assertIn("15 minutes", text)
        self.assertIn("assign another model", text)


class TestSearchSources(unittest.TestCase):
    def test_openrouter_annotations(self):
        reply = Reply("text", extra={"annotations": [
            {"type": "url_citation", "url_citation": {"url": "https://a.example/x", "title": "A"}},
            {"type": "url_citation", "url_citation": {"url": "https://a.example/x", "title": "A"}},
            {"type": "url_citation", "url_citation": {"url": "https://b.example", "title": ""}},
            {"type": "file_citation", "file_citation": {"file_id": "f"}},
        ]})
        self.assertEqual(search_sources(reply), [("A", "https://a.example/x"), ("", "https://b.example")])

    def test_anthropic_result_blocks(self):
        reply = Reply([
            {"type": "server_tool_use", "name": "web_search", "input": {"query": "q"}},
            {"type": "web_search_tool_result", "content": [
                {"type": "web_search_result", "url": "https://c.example", "title": "C"},
            ]},
            {"type": "web_search_tool_result", "content": {"type": "web_search_tool_result_error",
                                                            "error_code": "max_uses_exceeded"}},
            {"type": "text", "text": "answer"},
        ])
        self.assertEqual(search_sources(reply), [("C", "https://c.example")])

    def test_no_search_no_line(self):
        self.assertEqual(search_log_line(Reply("plain")), "")

    def test_log_line_counts_and_shortens(self):
        notes = [{"type": "url_citation", "url_citation": {"url": f"https://s{i}.example"}}
                 for i in range(7)]
        line = search_log_line(Reply("x", extra={"annotations": notes}))
        self.assertTrue(line.startswith("WEB SEARCH: 7 source(s)"))
        self.assertIn("(+2 more)", line)


class TestOpenAIReplies(unittest.TestCase):
    def test_a_refusal_part_with_no_text_is_a_refusal(self):
        reply = Reply(content=[{"type": "refusal", "refusal": "I can't help with that."}])
        self.assertEqual(refusal_reason(reply), "refusal")

    def test_the_chat_completions_refusal_field(self):
        self.assertEqual(refusal_reason(Reply(content="", extra={"refusal": "No."})), "refusal")

    def test_a_refusal_part_beside_an_answer_or_a_tool_call_is_not(self):
        answered = Reply(content=[{"type": "refusal", "refusal": "x"}, {"type": "text", "text": "Here."}])
        self.assertIsNone(refusal_reason(answered))
        calling = Reply(content=[{"type": "refusal", "refusal": "x"}], tool_calls=[{"name": "add"}])
        self.assertIsNone(refusal_reason(calling))

    def test_responses_citations_are_search_sources(self):
        reply = Reply(content=[
            {"type": "web_search_call", "id": "ws_1"},
            {"type": "text", "text": "Python 3.15", "annotations": [
                {"type": "url_citation", "url": "https://python.example/a", "title": "Downloads"},
                {"type": "url_citation", "url": "https://python.example/a", "title": "Downloads"},
            ]},
        ])
        self.assertEqual(search_sources(reply), [("Downloads", "https://python.example/a")])

    def test_the_prompt_hint_asks_for_a_search_step_of_its_own(self):
        from harness.model_reply import PROVIDER_SEARCH_HINT
        self.assertIn("step of its own", PROVIDER_SEARCH_HINT)
        self.assertIn("state what you found", PROVIDER_SEARCH_HINT)


if __name__ == "__main__":
    unittest.main()
