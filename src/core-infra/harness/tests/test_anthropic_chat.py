"""A search made from the provider's code step goes back with its `caller`
(state_model_drivers.md §2.7 B19)."""

from __future__ import annotations

import copy
import os
import sys
import unittest

CORE_INFRA = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, CORE_INFRA)

try:
    from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
    from harness.anthropic_chat import (
        HarnessChatAnthropic, nested_callers, restore_callers,
    )
except ImportError:  # the system Python has no langchain
    HarnessChatAnthropic = None

CODE = {"type": "code_execution_20260120", "tool_id": "srv-code"}


def nested_search_reply() -> "AIMessage":
    """What Anthropic stores for: a code step that ran one search, a direct search, then our tool."""
    return AIMessage(
        content=[
            {"type": "text", "text": "Looking it up."},
            {"type": "server_tool_use", "id": "srv-code", "name": "code_execution", "input": {},
             "partial_json": '{"code": "search()"}'},
            {"type": "server_tool_use", "id": "srv-ws", "name": "web_search",
             "input": {"query": "q"}, "caller": dict(CODE)},
            {"type": "web_search_tool_result", "tool_use_id": "srv-ws", "caller": dict(CODE),
             "content": [{"type": "web_search_result", "url": "https://a.example", "title": "A",
                          "encrypted_content": "x"}]},
            {"type": "code_execution_tool_result", "tool_use_id": "srv-code",
             "content": {"type": "encrypted_code_execution_result", "content": [],
                         "encrypted_stdout": "x", "return_code": 0, "stderr": ""}},
            {"type": "server_tool_use", "id": "srv-direct", "name": "web_search", "input": {"query": "d"}},
            {"type": "web_search_tool_result", "tool_use_id": "srv-direct", "caller": {"type": "direct"},
             "content": []},
            {"type": "tool_use", "id": "toolu_1", "name": "add", "input": {"a": 1},
             "caller": {"type": "direct"}},
        ],
        tool_calls=[{"id": "toolu_1", "name": "add", "args": {"a": 1}}],
    )


@unittest.skipIf(HarnessChatAnthropic is None, "langchain is not installed for this Python")
class TestNestedCallers(unittest.TestCase):
    def setUp(self):
        self.reply = nested_search_reply()
        self.history = [HumanMessage("go"), self.reply, ToolMessage("1", tool_call_id="toolu_1"),
                        HumanMessage("next")]

    def _sent_reply(self, model_class=HarnessChatAnthropic) -> list:
        llm = model_class(model="claude-opus-5-5", api_key="test-key")
        payload = llm._get_request_payload(self.history)
        return [m for m in payload["messages"] if m["role"] == "assistant"][0]["content"]

    def test_only_calls_made_from_another_server_tool_are_found(self):
        self.assertEqual(nested_callers(self.history), {"srv-ws": CODE})

    def test_the_request_keeps_caller_on_the_nested_call_and_its_result(self):
        sent = {(p["type"], p.get("id") or p.get("tool_use_id")): p for p in self._sent_reply()}
        self.assertEqual(sent[("server_tool_use", "srv-ws")]["caller"], CODE)
        self.assertEqual(sent[("web_search_tool_result", "srv-ws")]["caller"], CODE)

    def test_the_other_parts_go_as_the_client_sends_them(self):
        sent = {(p["type"], p.get("id") or p.get("tool_use_id")): p for p in self._sent_reply()}
        self.assertNotIn("caller", sent[("server_tool_use", "srv-code")])
        self.assertNotIn("caller", sent[("code_execution_tool_result", "srv-code")])
        self.assertNotIn("caller", sent[("server_tool_use", "srv-direct")])
        self.assertNotIn("caller", sent[("web_search_tool_result", "srv-direct")])
        self.assertEqual(sent[("server_tool_use", "srv-code")]["input"], {"code": "search()"})
        self.assertEqual(sent[("tool_use", "toolu_1")]["caller"], {"type": "direct"})

    def test_the_stored_reply_is_not_changed(self):
        before = copy.deepcopy(self.reply.content)
        self._sent_reply()
        self.assertEqual(self.reply.content, before)

    def test_this_is_needed_with_the_installed_client(self):
        """The day the client keeps `caller` itself, this fails and the subclass can go."""
        from langchain_anthropic import ChatAnthropic
        sent = {(p["type"], p.get("id") or p.get("tool_use_id")): p
                for p in self._sent_reply(ChatAnthropic)}
        self.assertNotIn("caller", sent[("server_tool_use", "srv-ws")])

    def test_a_request_without_nested_parts_is_the_clients_own(self):
        from langchain_anthropic import ChatAnthropic
        history = [HumanMessage("go"), AIMessage("fine"), HumanMessage("next")]
        ours = HarnessChatAnthropic(model="claude-opus-5-5", api_key="test-key")._get_request_payload(history)
        theirs = ChatAnthropic(model="claude-opus-5-5", api_key="test-key")._get_request_payload(history)
        self.assertEqual(ours, theirs)

    def test_restore_leaves_a_caller_that_is_already_there(self):
        wire = [{"role": "assistant", "content": [
            {"type": "server_tool_use", "id": "srv-ws", "name": "web_search", "input": {},
             "caller": {"type": "other"}}]}]
        self.assertEqual(restore_callers(wire, {"srv-ws": CODE}), 0)
        self.assertEqual(wire[0]["content"][0]["caller"], {"type": "other"})


class TestWiring(unittest.TestCase):
    def test_every_anthropic_key_client_is_the_harness_one(self):
        with open(os.path.join(CORE_INFRA, "provider_runtime.py"), encoding="utf-8") as handle:
            source = handle.read()
        self.assertIn("from harness.anthropic_chat import HarnessChatAnthropic as ChatAnthropic", source)
        self.assertNotIn("from langchain_anthropic import ChatAnthropic", source)


if __name__ == "__main__":
    unittest.main()
