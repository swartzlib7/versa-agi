"""Cache keep-alive while a chat waits (state_model_drivers.md §2.7 B17, B18)."""

from __future__ import annotations

import os
import sys
import unittest

CORE_INFRA = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, CORE_INFRA)

try:
    from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
    from harness import model_traits as mt
    from harness import prompt_cache as pc
    from harness.cache_keepalive import (
        INTERVAL_SECONDS, LIFETIME_SECONDS, KeepAlive, LastRequest, output_limit,
    )
    from harness.listening import ListeningWindow, wait_for_mail
    from harness.model_params import to_native_kwargs
    HAVE_LANGCHAIN = True
except ImportError:  # the agictl interpreter has no LangChain
    HAVE_LANGCHAIN = False


class Clock:
    def __init__(self):
        self.now = 1_000_000.0

    def __call__(self):
        return self.now


class FakeModel:
    """Stands in for the cycle model: records what a ping asks of it."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.copied, self.bound, self.calls = None, None, []

    def model_copy(self, update=None):
        self.copied = update
        return self

    def bind_tools(self, tools):
        self.bound = list(tools)
        return self

    def invoke(self, messages, **kwargs):
        self.calls.append((list(messages), kwargs))
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


def _reply(read=0, written=0, tokens=50_000, out=0):
    return AIMessage(content=[], usage_metadata={
        "input_tokens": tokens, "output_tokens": out, "total_tokens": tokens + out,
        "input_token_details": {"cache_read": read, "cache_creation": written},
    })


def _claude(route="anthropic"):
    if route == "anthropic":
        return mt.resolve_traits("anthropic", "anthropic", "claude-opus-5-5")
    return mt.resolve_traits("openai_compat", "openrouter", "anthropic/claude-opus-5.5")


def _keeper(replies, traits=None, family="anthropic"):
    clock = Clock()
    last = LastRequest(clock=clock)
    model = FakeModel(replies)
    lines: list[str] = []
    keeper = KeepAlive(traits or _claude(), model, ["tool_a", "tool_b"], last, family=family,
                       log=lines.append, clock=clock)
    return keeper, last, model, clock, lines


SENT = None


def _sent():
    return [[SystemMessage("rules"), HumanMessage("hello")]]


@unittest.skipUnless(HAVE_LANGCHAIN, "LangChain is in the harness interpreter only")
class TestLastRequest(unittest.TestCase):
    def test_records_what_was_sent_and_when(self):
        clock = Clock()
        last = LastRequest(clock=clock)
        self.assertIsNone(last.messages)
        last.on_chat_model_start({}, _sent())
        self.assertEqual([m.content for m in last.messages], ["rules", "hello"])
        self.assertEqual(last.started, clock.now)


@unittest.skipUnless(HAVE_LANGCHAIN, "LangChain is in the harness interpreter only")
class TestWhenAPingGoes(unittest.TestCase):
    def test_nothing_before_the_first_request(self):
        keeper, _last, model, clock, _ = _keeper([_reply(read=50_000)])
        clock.now += 10_000
        self.assertFalse(keeper.tick())
        self.assertEqual(model.calls, [])

    def test_four_minutes_after_the_last_request_started(self):
        keeper, last, model, clock, _ = _keeper([_reply(read=50_000)])
        last.on_chat_model_start({}, _sent())
        clock.now += INTERVAL_SECONDS - 1
        self.assertFalse(keeper.tick())
        clock.now += 1
        self.assertTrue(keeper.tick())
        self.assertEqual(len(model.calls), 1)
        self.assertLess(INTERVAL_SECONDS, LIFETIME_SECONDS)

    def test_the_next_ping_counts_from_the_ping(self):
        keeper, last, model, clock, _ = _keeper([_reply(read=50_000), _reply(read=50_000)])
        last.on_chat_model_start({}, _sent())
        clock.now += INTERVAL_SECONDS
        keeper.tick()
        clock.now += INTERVAL_SECONDS - 1
        self.assertFalse(keeper.tick())
        clock.now += 1
        self.assertTrue(keeper.tick())
        self.assertEqual(keeper.pings, 2)

    def test_only_models_that_carry_cache_marks(self):
        for traits in (mt.resolve_traits("openai_compat", "openai", "gpt-6.1-sol"),
                       mt.resolve_traits("google", "google", "gemini-4-carbon"),
                       mt.resolve_traits("openai_compat", "openrouter", "x-ai/grok-4.7")):
            keeper, last, model, clock, _ = _keeper([_reply(read=1)], traits=traits)
            last.on_chat_model_start({}, _sent())
            clock.now += 10_000
            self.assertFalse(keeper.tick())
            self.assertEqual(model.calls, [])


@unittest.skipUnless(HAVE_LANGCHAIN, "LangChain is in the harness interpreter only")
class TestWhatAPingSends(unittest.TestCase):
    def _ping(self, family="anthropic", traits=None):
        keeper, last, model, clock, lines = _keeper([_reply(read=50_000)], traits=traits, family=family)
        last.on_chat_model_start({}, _sent())
        clock.now += INTERVAL_SECONDS
        keeper.tick()
        return keeper, model, lines

    def test_the_last_request_again_with_no_reply_asked_for(self):
        keeper, model, lines = self._ping()
        messages, kwargs = model.calls[0]
        self.assertEqual([m.content for m in messages], ["rules", "hello"])
        self.assertEqual(kwargs["max_tokens"], 0)
        self.assertIn("timeout", kwargs)
        self.assertEqual(model.copied, {"streaming": False})      # no tokens cannot be streamed
        self.assertEqual(model.bound, ["tool_a", "tool_b"])        # the same tools, the same order
        self.assertEqual(lines, ["CACHE KEEP-ALIVE: ping 1 — 50,000 input tokens read from the cache"])

    def test_openrouter_is_asked_for_one_token(self):
        """OpenRouter ignores a limit of 0 and lets the model write a whole reply (probed)."""
        _keeper_, model, _ = self._ping(family="openai_compat", traits=_claude("openrouter"))
        self.assertEqual(model.calls[0][1]["max_tokens"], 1)
        self.assertEqual((output_limit("anthropic"), output_limit("openai_compat")), (0, 1))

    def test_the_tokens_are_counted(self):
        keeper, _model, _ = self._ping()
        self.assertEqual(keeper.usage(), (50_000, 0, 50_000, 0))
        self.assertEqual(pc.cache_log_line(150_000, 140_000, 900, pings=keeper.pings),
                         "CACHE: 140,000 of 150,000 input tokens read from the cache (93%); 900 written; "
                         "1 keep-alive ping")


@unittest.skipUnless(HAVE_LANGCHAIN, "LangChain is in the harness interpreter only")
class TestWhenItStops(unittest.TestCase):
    def test_a_ping_that_reads_nothing_turns_it_off(self):
        """The entry should have been there: more pings would not protect the next request."""
        keeper, last, model, clock, lines = _keeper([_reply(read=0, written=50_000), _reply(read=50_000)])
        last.on_chat_model_start({}, _sent())
        clock.now += INTERVAL_SECONDS
        keeper.tick()
        self.assertIn("read nothing", keeper.off_reason)
        self.assertIn("off for this cycle", lines[-1])
        clock.now += 10_000
        self.assertFalse(keeper.tick())
        self.assertEqual(len(model.calls), 1)

    def test_a_ping_after_the_entry_expired_writes_it_and_carries_on(self):
        """A reply that took more than 5 minutes: the ping pays the write the next request would have."""
        keeper, last, model, clock, _ = _keeper([_reply(read=0, written=50_000), _reply(read=50_000)])
        last.on_chat_model_start({}, _sent())
        clock.now += LIFETIME_SECONDS + 30
        keeper.tick()
        self.assertEqual(keeper.off_reason, "")
        clock.now += INTERVAL_SECONDS
        self.assertTrue(keeper.tick())
        self.assertEqual(keeper.usage(), (100_000, 0, 50_000, 50_000))

    def test_a_failed_ping_never_ends_a_chat(self):
        keeper, last, model, clock, lines = _keeper([RuntimeError("connection reset")])
        last.on_chat_model_start({}, _sent())
        clock.now += INTERVAL_SECONDS
        self.assertTrue(keeper.tick())           # no exception reaches the wait loop
        self.assertIn("a ping failed", keeper.off_reason)
        self.assertEqual(keeper.usage(), (0, 0, 0, 0))
        clock.now += 10_000
        self.assertFalse(keeper.tick())


@unittest.skipUnless(HAVE_LANGCHAIN, "LangChain is in the harness interpreter only")
class TestChatWait(unittest.TestCase):
    PU = "pu-1"

    def _window(self, clock):
        window = ListeningWindow(15, None, pu_ids={self.PU}, clock=clock, env={})
        window.note_rows([{"from_user_id": self.PU, "channel": "vv"}])
        return window

    def test_tick_runs_on_every_poll_without_mail_until_the_chat_closes(self):
        clock = Clock()
        ticks = []
        result = wait_for_mail(self._window(clock), lambda: False, poll_seconds=60,
                               sleep=lambda s: setattr(clock, "now", clock.now + s),
                               tick=lambda: ticks.append(clock.now))
        self.assertEqual(result, "idle")
        self.assertEqual(len(ticks), 15)

    def test_no_tick_on_the_poll_that_brings_mail(self):
        clock = Clock()
        ticks = []
        result = wait_for_mail(self._window(clock), lambda: True, poll_seconds=3,
                               sleep=lambda s: setattr(clock, "now", clock.now + s),
                               tick=lambda: ticks.append(1))
        self.assertEqual(result, "mail")
        self.assertEqual(ticks, [])

    def test_a_fifteen_minute_wait_sends_three_pings(self):
        clock = Clock()
        last = LastRequest(clock=clock)
        model = FakeModel([_reply(read=50_000)] * 5)
        keeper = KeepAlive(_claude(), model, [], last, clock=clock)
        last.on_chat_model_start({}, _sent())
        wait_for_mail(self._window(clock), lambda: False, poll_seconds=3,
                      sleep=lambda s: setattr(clock, "now", clock.now + s), tick=keeper.tick)
        self.assertEqual(keeper.pings, 3)       # at 4, 8 and 12 minutes; the chat closes at 15


@unittest.skipUnless(HAVE_LANGCHAIN, "LangChain is in the harness interpreter only")
class TestOpenRouterSession(unittest.TestCase):
    PARAMS = {"temperature": 0.2, "reasoning_effort": "high", "extra": {}}

    def test_id_is_stable_per_thread_and_names_nothing(self):
        one = pc.session_id("coa", "1-0")
        self.assertEqual(one, pc.session_id("coa", "1-0"))
        self.assertNotEqual(one, pc.session_id("coa", "1-7"))
        self.assertNotEqual(one, pc.session_id("researcher", "1-0"))
        self.assertRegex(one, r"^[0-9a-f]{32}$")
        self.assertNotIn("coa", one)

    def test_it_goes_with_the_cache_field_through_openrouter(self):
        native = to_native_kwargs("openai_compat", "anthropic/claude-fable-5.2", dict(self.PARAMS),
                                  provider_slug="openrouter", prompt_cache=True, cache_session="abc123")
        self.assertEqual(native["extra_body"]["session_id"], "abc123")
        self.assertEqual(native["extra_body"]["cache_control"], {"type": "ephemeral"})

    def test_not_without_the_cache_and_not_on_other_routes(self):
        native = to_native_kwargs("openai_compat", "anthropic/claude-fable-5.2", dict(self.PARAMS),
                                  provider_slug="openrouter", cache_session="abc123")
        self.assertNotIn("session_id", native.get("extra_body", {}))
        native = to_native_kwargs("openai_compat", "google/gemini-4-carbon", dict(self.PARAMS),
                                  provider_slug="openrouter", prompt_cache=True, cache_session="abc123")
        self.assertNotIn("session_id", native.get("extra_body", {}))
        native = to_native_kwargs("anthropic", "claude-opus-5-5", dict(self.PARAMS),
                                  provider_slug="anthropic", prompt_cache=True, cache_session="abc123")
        self.assertNotIn("session_id", native)
        self.assertNotIn("session_id", native.get("model_kwargs", {}))


if __name__ == "__main__":
    unittest.main()
