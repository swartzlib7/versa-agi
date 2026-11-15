"""A declined reply leaves the thread and the agent is told (§2.7 B6).

Runs the same two steps the harness takes, on a real LangGraph agent with a
scripted model: remove the declined reply in its own write, then send the note.
"""

from __future__ import annotations

import os
import sys
import unittest
import warnings

CORE_INFRA = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, CORE_INFRA)

try:
    from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
    from langchain_core.messages import AIMessage, HumanMessage, RemoveMessage
    from langgraph.checkpoint.memory import InMemorySaver
    from langgraph.prebuilt import create_react_agent
except ImportError as exc:  # the system interpreter has no LangChain
    raise unittest.SkipTest(f"LangChain not available: {exc}")

from harness.model_reply import refusal_note, refusal_reason  # noqa: E402


class ScriptedModel(GenericFakeChatModel):
    def bind_tools(self, tools, **kwargs):
        return self


class TestRefusalThread(unittest.TestCase):
    def test_the_declined_reply_is_gone_and_the_next_reply_follows_the_note(self):
        replies = iter([
            AIMessage(content="", id="declined", response_metadata={"stop_reason": "refusal"}),
            AIMessage(content="I told the Primary User the request was declined.", id="handled"),
        ])
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            agent = create_react_agent(model=ScriptedModel(messages=replies), tools=[],
                                       checkpointer=InMemorySaver())
        config = {"configurable": {"thread_id": "t1"}}

        first = list(agent.stream({"messages": [HumanMessage("do the thing", id="wake")]}, config=config))
        declined = first[-1]["agent"]["messages"][0]
        reason = refusal_reason(declined)
        self.assertEqual(reason, "refusal")

        agent.update_state(config, {"messages": [RemoveMessage(id=declined.id)]}, as_node="__start__")
        note = HumanMessage(content=refusal_note(reason), id="note")
        second = list(agent.stream({"messages": [note]}, config=config))

        self.assertEqual(len(second), 1)  # one model call, no error
        thread = agent.get_state(config).values["messages"]
        self.assertEqual([m.id for m in thread], ["wake", "note", "handled"])
        self.assertIsNone(refusal_reason(thread[-1]))
        self.assertEqual(agent.get_state(config).next, ())


if __name__ == "__main__":
    unittest.main()
