"""Request traits: one rule set for what each model's API accepts (§2.7 B1)."""

from __future__ import annotations

import os
import sys
import unittest

CORE_INFRA = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, CORE_INFRA)

from harness import model_traits as mt  # noqa: E402


class TestModelIds(unittest.TestCase):
    def test_claude_ids_on_both_routes(self):
        self.assertEqual(mt.claude_version("claude-opus-4-8"), ("opus", (4, 8)))
        self.assertEqual(mt.claude_version("anthropic/claude-opus-4.8"), ("opus", (4, 8)))
        self.assertEqual(mt.claude_version("claude-fable-5-2"), ("fable", (5, 2)))
        self.assertEqual(mt.claude_version("anthropic/claude-fable-5.2"), ("fable", (5, 2)))
        self.assertEqual(mt.claude_version("claude-opus-5"), ("opus", (5, 0)))

    def test_a_date_suffix_is_not_a_minor_version(self):
        self.assertEqual(mt.claude_version("claude-opus-4-20250514"), ("opus", (4, 0)))
        self.assertEqual(mt.claude_version("claude-sonnet-4-5-20250929"), ("sonnet", (4, 5)))

    def test_old_and_foreign_ids_are_not_claude(self):
        self.assertIsNone(mt.claude_version("claude-3-5-sonnet-20241022"))
        self.assertIsNone(mt.claude_version("gpt-5.6-terra"))

    def test_gemini_ids(self):
        self.assertEqual(mt.gemini_version("gemini-3.7-flash"), (3, 7))
        self.assertEqual(mt.gemini_version("google/gemini-4-hydrogen"), (4, 0))
        self.assertEqual(mt.gemini_version("gemini-2.5-pro"), (2, 5))
        self.assertIsNone(mt.gemini_version("grok-4.7"))


class TestAnthropicDirect(unittest.TestCase):
    def traits(self, model):
        return mt.resolve_traits("anthropic", "anthropic", model)

    def test_five_line_models_think_adaptively_and_bind_their_thinking(self):
        for model in ("claude-fable-5-2", "claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5"):
            t = self.traits(model)
            self.assertEqual(t.thinking, mt.THINKING_ADAPTIVE, model)
            self.assertFalse(t.sampling, model)
            self.assertEqual(t.replay, mt.REPLAY_BOUND, model)
            self.assertEqual(t.max_output_tokens, 64_000, model)
            self.assertTrue(t.streaming, model)

    def test_opus_4_8_is_adaptive_but_not_bound(self):
        t = self.traits("claude-opus-4-8")
        self.assertEqual(t.thinking, mt.THINKING_ADAPTIVE)
        self.assertFalse(t.sampling)
        self.assertEqual(t.replay, "")
        self.assertEqual(t.max_output_tokens, 64_000)

    def test_opus_5_and_fable_5_are_adaptive_without_the_binding(self):
        self.assertEqual(self.traits("claude-opus-5").replay, "")
        self.assertEqual(self.traits("claude-fable-5").replay, "")
        self.assertEqual(self.traits("claude-fable-5-1").replay, mt.REPLAY_BOUND)

    def test_older_models_keep_budgets_and_sampling(self):
        for model in ("claude-opus-4-6", "claude-sonnet-4-6", "claude-haiku-4-5", "claude-3-5-sonnet-20241022"):
            t = self.traits(model)
            self.assertEqual(t.thinking, mt.THINKING_BUDGET, model)
            self.assertTrue(t.sampling, model)
            self.assertEqual(t.max_output_tokens, 0, model)
            self.assertTrue(t.streaming, model)

    def test_search_route(self):
        self.assertEqual(self.traits("claude-opus-5-5").web_search, mt.SEARCH_ANTHROPIC)
        self.assertEqual(self.traits("claude-haiku-4-5").web_search, "")


class TestGoogleDirect(unittest.TestCase):
    def test_gemini_3_and_later_use_levels(self):
        for model in ("gemini-3.7-flash", "gemini-4-carbon", "gemini-4-hydrogen"):
            t = mt.resolve_traits("google", "google", model)
            self.assertEqual(t.thinking, mt.THINKING_LEVEL, model)
            self.assertFalse(t.sampling, model)
            self.assertEqual(t.replay, mt.REPLAY_SIGNATURE, model)

    def test_gemini_2_5_keeps_the_budget_shape(self):
        t = mt.resolve_traits("google", "google", "gemini-2.5-flash")
        self.assertEqual(t.thinking, mt.THINKING_BUDGET)
        self.assertTrue(t.sampling)
        self.assertEqual(t.replay, "")

    def test_no_search_route_on_googles_own_api(self):
        self.assertEqual(mt.resolve_traits("google", "google", "gemini-4-hydrogen").web_search, "")

    def test_levels_the_client_accepts(self):
        self.assertEqual(mt.google_thinking_level("medium"), "medium")
        self.assertEqual(mt.google_thinking_level("xhigh"), "high")
        self.assertEqual(mt.google_thinking_level("max"), "high")
        self.assertEqual(mt.google_thinking_level("none"), "low")


class TestOpenRouter(unittest.TestCase):
    def traits(self, model):
        return mt.resolve_traits("openai_compat", "openrouter", model)

    def test_claude_through_openrouter(self):
        t = self.traits("anthropic/claude-fable-5.2")
        self.assertFalse(t.sampling)
        self.assertEqual(t.replay, mt.REPLAY_BOUND)
        self.assertEqual(t.web_search, mt.SEARCH_OPENROUTER)
        self.assertEqual(t.thinking, "")
        self.assertEqual(self.traits("anthropic/claude-opus-4.8").replay, "")

    def test_gemini_through_openrouter(self):
        t = self.traits("google/gemini-4-hydrogen")
        self.assertTrue(t.sampling)
        self.assertEqual(t.replay, mt.REPLAY_SIGNATURE)
        self.assertEqual(t.web_search, mt.SEARCH_OPENROUTER)

    def test_other_models_have_no_special_rule(self):
        for model in ("x-ai/grok-4.7", "z-ai/glm-5.3-flash", "openai/gpt-4.1"):
            self.assertEqual(self.traits(model), mt.ModelTraits(), model)

    def test_gpt_keeps_its_reasoning_record_and_has_no_search_route(self):
        """With the web plugin GPT-6 and GPT-6.1 repeated one tool call in a loop (probed)."""
        for model in ("openai/gpt-5.6-terra", "openai/gpt-6-sol", "openai/gpt-6.1-sol"):
            t = self.traits(model)
            self.assertEqual(t.api, "", model)  # OpenRouter picks the endpoint
            self.assertEqual(t.replay, mt.REPLAY_SIGNATURE, model)
            self.assertEqual(t.web_search, "", model)
            self.assertEqual(mt.openrouter_search_plugins(t, True), [], model)

    def test_other_providers_have_no_special_rule(self):
        self.assertEqual(mt.resolve_traits("openai_compat", "xai", "grok-4.7"), mt.ModelTraits())
        self.assertEqual(mt.resolve_traits("local", "ollama", "qwen3.6:35b"), mt.ModelTraits())


class TestOpenAI(unittest.TestCase):
    """OpenAI's own API: which endpoint a model is called on."""

    @staticmethod
    def traits(model, overrides=None):
        return mt.resolve_traits("openai_compat", "openai", model, overrides)

    def test_gpt_version(self):
        self.assertEqual(mt.gpt_version("gpt-5.6-sol"), (5, 6))
        self.assertEqual(mt.gpt_version("gpt-6-sol"), (6, 0))
        self.assertEqual(mt.gpt_version("openai/gpt-6.1-terra"), (6, 1))
        self.assertIsNone(mt.gpt_version("grok-4.7"))

    def test_gpt_56_and_later_use_the_responses_endpoint(self):
        for model in ("gpt-5.6-terra", "gpt-5.6-sol", "gpt-5.6-luna", "gpt-6-sol", "gpt-6.1-sol"):
            t = self.traits(model)
            self.assertEqual(t.api, mt.API_RESPONSES, model)
            self.assertEqual(t.replay, mt.REPLAY_SIGNATURE, model)
            self.assertEqual(t.web_search, mt.SEARCH_OPENAI, model)

    def test_older_and_unnamed_models_stay_on_chat_completions(self):
        for model in ("gpt-5.5", "gpt-4.1", "o4-mini", "my-fine-tune"):
            self.assertEqual(self.traits(model), mt.ModelTraits(), model)

    def test_a_row_can_turn_the_endpoint_rule_off(self):
        self.assertEqual(self.traits("gpt-6-sol", {"api": ""}).api, "")

    def test_search_tool_needs_the_responses_endpoint(self):
        self.assertEqual(mt.builtin_search_tools(self.traits("gpt-6-sol"), True), [{"type": "web_search"}])
        self.assertEqual(mt.builtin_search_tools(self.traits("gpt-6-sol"), False), [])
        self.assertEqual(mt.builtin_search_tools(self.traits("gpt-6-sol", {"api": ""}), True), [])

    def test_search_tool_is_not_offered_while_reasoning_is_off(self):
        """With reasoning off GPT answered from memory and left raw citation markers (probed)."""
        traits = self.traits("gpt-5.6-terra")
        self.assertEqual(mt.builtin_search_tools(traits, True, reasoning_on=False), [])
        self.assertEqual(mt.builtin_search_tools(traits, True, reasoning_on=True), [{"type": "web_search"}])
        claude = mt.resolve_traits("anthropic", "anthropic", "claude-opus-4-8")
        self.assertEqual(len(mt.builtin_search_tools(claude, True, reasoning_on=False)), 1)


class TestOverrides(unittest.TestCase):
    def test_a_model_row_overrides_one_rule(self):
        t = mt.resolve_traits("openai_compat", "openrouter", "google/gemini-4-carbon",
                              {"streaming": True, "max_output_tokens": 32000})
        self.assertTrue(t.streaming)
        self.assertEqual(t.max_output_tokens, 32000)
        self.assertEqual(t.replay, mt.REPLAY_SIGNATURE)  # the rest still comes from the rules

    def test_unknown_and_mistyped_overrides_are_ignored(self):
        t = mt.resolve_traits("anthropic", "anthropic", "claude-opus-5-5",
                              {"streaming": "yes", "max_output_tokens": -1, "colour": "red"})
        self.assertEqual(t, mt.resolve_traits("anthropic", "anthropic", "claude-opus-5-5"))

    def test_normalize_reports_what_it_dropped(self):
        cleaned, warnings = mt.normalize_overrides({"streaming": True, "colour": "red"})
        self.assertEqual(cleaned, {"streaming": True})
        self.assertEqual(len(warnings), 1)
        self.assertIn("colour", warnings[0])
        self.assertEqual(mt.normalize_overrides("text")[0], {})


class TestSearchEntries(unittest.TestCase):
    def test_anthropic_tool_entry_only_when_on(self):
        t = mt.resolve_traits("anthropic", "anthropic", "claude-fable-5-2")
        self.assertEqual(mt.builtin_search_tools(t, False), [])
        (entry,) = mt.builtin_search_tools(t, True)
        self.assertEqual(entry["type"], "web_search_20260209")
        self.assertEqual(entry["name"], "web_search")
        self.assertEqual(entry["max_uses"], 5)

    def test_openrouter_plugin_is_pinned_to_the_providers_own_search(self):
        t = mt.resolve_traits("openai_compat", "openrouter", "google/gemini-4-hydrogen")
        self.assertEqual(mt.openrouter_search_plugins(t, True), [{"id": "web", "engine": "native"}])
        self.assertEqual(mt.openrouter_search_plugins(t, False), [])
        self.assertEqual(mt.builtin_search_tools(t, True), [])

    def test_a_model_without_a_route_gets_nothing(self):
        t = mt.resolve_traits("openai_compat", "openrouter", "x-ai/grok-4.7")
        self.assertEqual(mt.openrouter_search_plugins(t, True), [])
        self.assertEqual(mt.builtin_search_tools(t, True), [])


if __name__ == "__main__":
    unittest.main()
