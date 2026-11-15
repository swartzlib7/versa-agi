"""Prompt caching where a model has to be asked for it (state_model_drivers.md §2.7 B16)."""

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
    from harness.model_params import to_native_kwargs
    HAVE_LANGCHAIN = True
except ImportError:  # the agictl interpreter has no LangChain
    HAVE_LANGCHAIN = False

PROMPT = ("Rules that never change.\n\n"
          "## ── LIVE SITUATION — per-cycle data below ──\nThree tasks are due.")


def _claude(route: str = "anthropic"):
    if route == "anthropic":
        return mt.resolve_traits("anthropic", "anthropic", "claude-opus-5-5")
    return mt.resolve_traits("openai_compat", "openrouter", "anthropic/claude-opus-5.5")


@unittest.skipUnless(HAVE_LANGCHAIN, "LangChain is in the harness interpreter only")
class TestWhichModelsAreMarked(unittest.TestCase):
    def test_claude_is_marked_on_both_routes(self):
        for model in ("claude-opus-5-5", "claude-fable-5-2", "claude-opus-4-8"):
            self.assertEqual(mt.resolve_traits("anthropic", "anthropic", model).cache, mt.CACHE_MARKS, model)
        for model in ("anthropic/claude-opus-5.5", "anthropic/claude-fable-5.2", "anthropic/claude-opus-4.8"):
            self.assertEqual(mt.resolve_traits("openai_compat", "openrouter", model).cache, mt.CACHE_MARKS, model)

    def test_routes_that_cache_by_themselves_are_not(self):
        """Probed 2026-11-15: each of these reports cached input with no mark sent."""
        for family, slug, model in (
            ("openai_compat", "openai", "gpt-6.1-sol"),
            ("google", "google", "gemini-4-carbon"),
            ("openai_compat", "xai", "grok-4.7"),
            ("openai_compat", "openrouter", "google/gemini-4-carbon"),
            ("openai_compat", "openrouter", "openai/gpt-6.1-sol"),
            ("openai_compat", "openrouter", "x-ai/grok-4.7"),
            ("openai_compat", "openrouter", "z-ai/glm-5.3-flash"),
            ("openai_compat", "openrouter", "deepseek/deepseek-v4-flash-0731"),
        ):
            self.assertEqual(mt.resolve_traits(family, slug, model).cache, "", model)

    def test_a_model_row_can_turn_the_marks_off(self):
        traits = mt.resolve_traits("anthropic", "anthropic", "claude-opus-5-5", {"cache": ""})
        self.assertEqual(traits.cache, "")
        self.assertEqual(pc.system_prompt(traits, PROMPT), PROMPT)


@unittest.skipUnless(HAVE_LANGCHAIN, "LangChain is in the harness interpreter only")
class TestRequestField(unittest.TestCase):
    PARAMS = {"temperature": 0.2, "reasoning_effort": "high", "extra": {}}

    def test_anthropic_gets_the_top_level_field(self):
        native = to_native_kwargs("anthropic", "claude-opus-5-5", dict(self.PARAMS),
                                  provider_slug="anthropic", prompt_cache=True)
        self.assertEqual(native["cache_control"], {"type": "ephemeral"})

    def test_openrouter_gets_it_in_the_request_body(self):
        native = to_native_kwargs("openai_compat", "anthropic/claude-opus-5.5", dict(self.PARAMS),
                                  provider_slug="openrouter", prompt_cache=True)
        self.assertEqual(native["extra_body"]["cache_control"], {"type": "ephemeral"})

    def test_only_the_model_that_runs_the_cycle_asks(self):
        """A one-off call (triage, a summary) would pay the write surcharge and never read it back."""
        native = to_native_kwargs("anthropic", "claude-opus-5-5", dict(self.PARAMS), provider_slug="anthropic")
        self.assertNotIn("cache_control", native)
        native = to_native_kwargs("openai_compat", "anthropic/claude-opus-5.5", dict(self.PARAMS),
                                  provider_slug="openrouter")
        self.assertNotIn("cache_control", native.get("extra_body", {}))

    def test_other_models_never_get_it(self):
        native = to_native_kwargs("openai_compat", "google/gemini-4-carbon", dict(self.PARAMS),
                                  provider_slug="openrouter", prompt_cache=True)
        self.assertNotIn("cache_control", native.get("extra_body", {}))
        native = to_native_kwargs("openai_compat", "gpt-6.1-sol", dict(self.PARAMS),
                                  provider_slug="openai", prompt_cache=True)
        self.assertNotIn("cache_control", native)
        self.assertNotIn("cache_control", native.get("extra_body", {}))

    def test_each_request_gets_its_own_copy_of_the_mark(self):
        native = to_native_kwargs("anthropic", "claude-opus-5-5", dict(self.PARAMS),
                                  provider_slug="anthropic", prompt_cache=True)
        native["cache_control"]["ttl"] = "1h"
        self.assertEqual(mt.CACHE_MARK, {"type": "ephemeral"})


@unittest.skipUnless(HAVE_LANGCHAIN, "LangChain is in the harness interpreter only")
class TestSystemPrompt(unittest.TestCase):
    def test_two_marked_blocks_split_where_the_live_data_starts(self):
        for route in ("anthropic", "openrouter"):
            message = pc.system_prompt(_claude(route), PROMPT)
            self.assertIsInstance(message, SystemMessage)
            fixed, live = message.content
            self.assertEqual(fixed["text"], "Rules that never change.\n\n")
            self.assertTrue(live["text"].startswith(pc.LIVE_BOUNDARY))
            self.assertEqual(fixed["text"] + live["text"], PROMPT)   # nothing added or lost
            for block in (fixed, live):
                self.assertEqual(block["type"], "text")
                self.assertEqual(block["cache_control"], {"type": "ephemeral"})

    def test_the_fixed_block_is_the_same_whatever_the_live_data(self):
        """The first mark is what the next cycle reads back."""
        one = pc.system_prompt(_claude(), PROMPT).content[0]
        two = pc.system_prompt(_claude(), PROMPT.replace("Three tasks", "No tasks")).content[0]
        self.assertEqual(one, two)

    def test_a_prompt_without_the_boundary_is_one_marked_block(self):
        message = pc.system_prompt(_claude(), "Short rules.")
        self.assertEqual(len(message.content), 1)
        self.assertEqual(message.content[0]["text"], "Short rules.")

    def test_other_models_get_the_text_as_it_is(self):
        traits = mt.resolve_traits("openai_compat", "openai", "gpt-6.1-sol")
        self.assertIs(pc.system_prompt(traits, PROMPT), PROMPT)
        self.assertEqual(pc.system_prompt(_claude(), ""), "")


@unittest.skipUnless(HAVE_LANGCHAIN, "LangChain is in the harness interpreter only")
class TestCachedTokens(unittest.TestCase):
    def _reply(self, **details):
        return AIMessage(content="ok", usage_metadata={
            "input_tokens": 8085, "output_tokens": 20, "total_tokens": 8105, "input_token_details": details,
        })

    def test_anthropic_reports_read_and_written(self):
        self.assertEqual(pc.cached_tokens(self._reply(cache_read=8009, cache_creation=74)), (8009, 74))

    def test_openai_style_routes_report_what_was_read(self):
        self.assertEqual(pc.cached_tokens(self._reply(cache_read=4239)), (4239, 0))

    def test_openrouter_reports_what_claude_wrote_in_its_usage_block(self):
        reply = self._reply(cache_read=0)
        reply.response_metadata = {"token_usage": {"prompt_tokens_details": {"cache_write_tokens": 8009}}}
        self.assertEqual(pc.cached_tokens(reply), (0, 8009))

    def test_a_reply_with_no_usage_counts_nothing(self):
        self.assertEqual(pc.cached_tokens(AIMessage(content="ok")), (0, 0))
        self.assertEqual(pc.cached_tokens(HumanMessage("hi")), (0, 0))

    def test_totals_over_a_cycle(self):
        replies = [HumanMessage("go"), self._reply(cache_creation=8009),
                   self._reply(cache_read=8009, cache_creation=74), self._reply(cache_read=8083, cache_creation=72)]
        self.assertEqual(pc.cache_totals(replies), (16092, 8155))

    def test_log_line(self):
        self.assertEqual(pc.cache_log_line(24255, 16092, 8155),
                         "CACHE: 16,092 of 24,255 input tokens read from the cache (66%); 8,155 written")
        self.assertEqual(pc.cache_log_line(5000, 0, 0), "")


if __name__ == "__main__":
    unittest.main()
