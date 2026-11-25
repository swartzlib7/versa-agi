"""What is sent to each provider for the four 2026-11 models (NM-02, NM-06, NM-07, NM-09)."""

from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import patch

CORE_INFRA = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, CORE_INFRA)

from harness import model_params  # noqa: E402
from harness.model_params import (  # noqa: E402
    build_params_layer_update,
    normalize_custom_params,
    resolve_model_params,
    to_native_kwargs,
)

STOCK = os.path.join(os.path.dirname(CORE_INFRA), "models.ini.stock")


class StockParams(unittest.TestCase):
    """Model rows come from the stock template, not from this machine's models.ini."""

    @classmethod
    def setUpClass(cls):
        cls._paths = patch.object(model_params, "_MODELS_INI_PATHS", [STOCK])
        cls._paths.start()

    @classmethod
    def tearDownClass(cls):
        cls._paths.stop()

    def native(self, family, slug, model, overrides=None):
        return to_native_kwargs(family, model, resolve_model_params(model, overrides), provider_slug=slug)


class TestAnthropicDirect(StockParams):
    def test_fable_5_2_default(self):
        self.assertEqual(self.native("anthropic", "anthropic", "claude-fable-5-2"), {
            "thinking": {"type": "adaptive", "display": "summarized"},
            "output_config": {"effort": "high"},
            "max_tokens": 64000,
            "streaming": True,
        })

    def test_opus_5_5_default_is_high(self):
        """Stock sets it: Anthropic's own default for this model is medium."""
        native = self.native("anthropic", "anthropic", "claude-opus-5-5")
        self.assertEqual(native["output_config"], {"effort": "high"})
        self.assertEqual(native["thinking"]["type"], "adaptive")

    def test_never_temperature_budget_or_disabled(self):
        for model in ("claude-fable-5-2", "claude-opus-5-5"):
            for effort in ("none", "low", "medium", "high", "xhigh", "max"):
                native = self.native("anthropic", "anthropic", model,
                                     {"reasoning_effort": effort, "reasoning_max_tokens": 9000,
                                      "temperature": 0.7})
                self.assertNotIn("temperature", native, (model, effort))
                self.assertEqual(native["thinking"]["type"], "adaptive", (model, effort))
                self.assertNotIn("budget_tokens", native["thinking"], (model, effort))
                self.assertIn(native["output_config"]["effort"],
                              ("low", "medium", "high", "xhigh", "max"), (model, effort))

    def test_an_agent_set_to_none_runs_at_the_models_default(self):
        native = self.native("anthropic", "anthropic", "claude-fable-5-2", {"reasoning_effort": "none"})
        self.assertEqual(native["output_config"], {"effort": "high"})

    def test_opus_4_8_without_reasoning_now_has_an_output_limit(self):
        self.assertEqual(self.native("anthropic", "anthropic", "claude-opus-4-8"),
                         {"max_tokens": 64000, "streaming": True})

    def test_opus_4_8_with_reasoning_is_unchanged_in_shape(self):
        native = self.native("anthropic", "anthropic", "claude-opus-4-8", {"reasoning_effort": "high"})
        self.assertEqual(native["thinking"], {"type": "adaptive"})
        self.assertEqual(native["output_config"], {"effort": "high"})
        self.assertNotIn("temperature", native)

    def test_a_pre_4_7_model_keeps_budget_and_temperature(self):
        on = to_native_kwargs("anthropic", "claude-sonnet-4-6",
                              {"temperature": 0.2, "reasoning_effort": "low", "extra": {}},
                              provider_slug="anthropic")
        self.assertEqual(on["thinking"], {"type": "enabled", "budget_tokens": 4096})
        off = to_native_kwargs("anthropic", "claude-sonnet-4-6",
                               {"temperature": 0.2, "reasoning_effort": "none", "extra": {}},
                               provider_slug="anthropic")
        self.assertEqual(off["temperature"], 0.2)
        self.assertNotIn("max_tokens", off)


class TestGoogleDirect(StockParams):
    def test_gemini_4_uses_a_thinking_level_only(self):
        for model in ("gemini-4-carbon", "gemini-4-hydrogen"):
            self.assertEqual(self.native("google", "google", model),
                             {"thinking_level": "high", "streaming": True}, model)

    def test_no_budget_shape_even_when_an_agent_sets_one(self):
        native = self.native("google", "google", "gemini-4-carbon", {"reasoning_max_tokens": 4096})
        self.assertNotIn("model_kwargs", native)
        self.assertNotIn("thinking_budget", native)

    def test_xhigh_is_sent_as_high_on_googles_own_api(self):
        native = to_native_kwargs("google", "gemini-4-hydrogen",
                                  {"reasoning_effort": "xhigh", "extra": {}}, provider_slug="google")
        self.assertEqual(native["thinking_level"], "high")

    def test_gemini_3_7_flash_is_unchanged(self):
        self.assertEqual(self.native("google", "google", "gemini-3.7-flash"), {"thinking_level": "medium"})


class TestOpenRouter(StockParams):
    def test_claude_gets_effort_and_no_temperature(self):
        native = self.native("openai_compat", "openrouter", "anthropic/claude-fable-5.2")
        self.assertEqual(native, {"streaming": True, "extra_body": {
            "reasoning": {"effort": "high"}, "plugins": [{"id": "web", "engine": "native"}],
        }})

    def test_gemini_4_keeps_temperature(self):
        native = self.native("openai_compat", "openrouter", "google/gemini-4-hydrogen")
        self.assertEqual(native["temperature"], 0.2)
        self.assertEqual(native["extra_body"]["reasoning"], {"effort": "high"})
        self.assertTrue(native["streaming"])

    def test_a_disable_is_never_sent_for_a_model_that_always_reasons(self):
        for model in ("anthropic/claude-fable-5.2", "anthropic/claude-opus-5.5",
                      "google/gemini-4-carbon", "google/gemini-4-hydrogen"):
            native = self.native("openai_compat", "openrouter", model, {"reasoning_effort": "none"})
            reasoning = native["extra_body"]["reasoning"]
            self.assertNotIn("enabled", reasoning, model)
            self.assertIn(reasoning["effort"], model_params.allowed_reasoning_efforts(model), model)

    def test_effort_outside_the_models_list_falls_back_to_its_default(self):
        native = self.native("openai_compat", "openrouter", "google/gemini-4-carbon",
                             {"reasoning_effort": "xhigh"})
        self.assertEqual(native["extra_body"]["reasoning"], {"effort": "high"})

    def test_opus_4_8_can_still_be_turned_off(self):
        native = self.native("openai_compat", "openrouter", "anthropic/claude-opus-4.8")
        self.assertEqual(native["extra_body"]["reasoning"], {"enabled": False})
        self.assertNotIn("temperature", native)


class TestOpenAIDirect(StockParams):
    """OpenAI's own API: GPT-5.6 and later go to the Responses endpoint."""

    RESPONSES = {
        "use_responses_api": True,
        "store": False,
        "include": ["reasoning.encrypted_content"],
    }

    def test_gpt_6_sol_default(self):
        self.assertEqual(self.native("openai_compat", "openai", "gpt-6-sol"), {
            **self.RESPONSES, "reasoning": {"effort": "high"}, "streaming": True,
        })

    def test_every_effort_goes_as_a_reasoning_object_never_reasoning_effort(self):
        for model in ("gpt-6-sol", "gpt-5.6-terra", "gpt-5.6-sol", "gpt-5.6-luna"):
            for effort in ("none", "low", "medium", "high", "xhigh", "max"):
                native = self.native("openai_compat", "openai", model, {"reasoning_effort": effort})
                self.assertEqual(native["reasoning"], {"effort": effort}, (model, effort))
                self.assertNotIn("reasoning_effort", native, (model, effort))
                self.assertTrue(native["use_responses_api"], (model, effort))
                self.assertIs(native["store"], False, (model, effort))

    def test_temperature_only_while_reasoning_is_off(self):
        on = self.native("openai_compat", "openai", "gpt-6-sol", {"temperature": 0.4})
        off = self.native("openai_compat", "openai", "gpt-6-sol",
                          {"temperature": 0.4, "reasoning_effort": "none"})
        self.assertNotIn("temperature", on)
        self.assertEqual(off["temperature"], 0.4)

    def test_gpt_5_6_default_is_unchanged_in_effort(self):
        native = self.native("openai_compat", "openai", "gpt-5.6-terra")
        self.assertEqual(native["reasoning"], {"effort": "none"})
        self.assertNotIn("streaming", native)

    def test_search_is_a_tool_not_a_request_field(self):
        native = self.native("openai_compat", "openai", "gpt-6-sol", {"web_search": True})
        self.assertNotIn("extra_body", native)
        with patch.object(model_params, "_load_catalog_provider", return_value="openai"), \
                patch.object(model_params, "_load_provider_cls", return_value="ChatOpenAI"):
            self.assertEqual(model_params.builtin_search_tools("gpt-6-sol"), [{"type": "web_search"}])
            self.assertEqual(model_params.web_search_route("gpt-6-sol"), "openai")
            # OpenAI's search needs reasoning: off for a model or an agent at `none`.
            self.assertEqual(model_params.builtin_search_tools("gpt-5.6-terra"), [])
            self.assertFalse(model_params.web_search_enabled("gpt-5.6-terra"))
            self.assertEqual(
                model_params.builtin_search_tools("gpt-5.6-terra", {"reasoning_effort": "low"}),
                [{"type": "web_search"}],
            )
            self.assertTrue(model_params.web_search_enabled("gpt-5.6-terra", {"reasoning_effort": "low"}))
            self.assertEqual(model_params.builtin_search_tools("gpt-6-sol", {"reasoning_effort": "none"}), [])

    def test_gpt_6_sol_through_openrouter(self):
        """No search plugin: through OpenRouter it made GPT repeat a tool call in a loop."""
        for key, effort in (("openai/gpt-6-sol", "high"), ("openai/gpt-6.1-sol", "medium"),
                            ("openai/gpt-6.2-astra", "medium")):
            native = self.native("openai_compat", "openrouter", key)
            self.assertEqual(native["extra_body"], {"reasoning": {"effort": effort}}, key)
            self.assertTrue(native["streaming"], key)
            self.assertNotIn("use_responses_api", native, key)

    def test_gpt_6_1_sol_always_reasons(self):
        """`none` is not in its list: OpenAI answers 400 to reasoning.effort none."""
        for slug, key in (("openai", "gpt-6.1-sol"), ("openrouter", "openai/gpt-6.1-sol")):
            resolved = resolve_model_params(key, {"reasoning_effort": "none"})
            self.assertEqual(resolved["reasoning_effort"], "medium", key)
        native = self.native("openai_compat", "openai", "gpt-6.1-sol", {"reasoning_effort": "none"})
        self.assertEqual(native["reasoning"], {"effort": "medium"})
        self.assertTrue(native["use_responses_api"])
        self.assertNotIn("temperature", native)

    def test_gpt_6_2_astra_on_openais_own_api(self):
        """Responses endpoint, always reasons at medium by default, streamed, no temperature,
        OpenAI's own web search beside the function tools (NM-30)."""
        for key in ("gpt-6.2-astra", "openai/gpt-6.2-astra"):
            resolved = resolve_model_params(key, {"reasoning_effort": "none"})
            self.assertEqual(resolved["reasoning_effort"], "medium", key)
        native = self.native("openai_compat", "openai", "gpt-6.2-astra")
        self.assertEqual(native["reasoning"], {"effort": "medium"})
        self.assertTrue(native["use_responses_api"])
        self.assertFalse(native["store"])
        self.assertTrue(native["streaming"])
        self.assertNotIn("temperature", native)
        with patch.object(model_params, "_load_catalog_provider", return_value="openai"), \
                patch.object(model_params, "_load_provider_cls", return_value="ChatOpenAI"):
            self.assertEqual(model_params.builtin_search_tools("gpt-6.2-astra"), [{"type": "web_search"}])
            self.assertEqual(model_params.web_search_route("gpt-6.2-astra"), "openai")


class TestGemini4NeonAndArgon(StockParams):
    """NM-30: both get the Gemini 4 rules from their ids; nothing is special-cased."""

    def test_googles_own_api(self):
        for key, level in (("gemini-4-neon", "high"), ("gemini-4-argon", "medium")):
            native = self.native("google", "google", key)
            self.assertEqual(native["thinking_level"], level, key)
            self.assertNotIn("temperature", native, key)
            self.assertTrue(native["streaming"], key)

    def test_xhigh_on_googles_own_api_is_sent_as_high(self):
        """The installed Google client has no xhigh; the stock row does not offer it there."""
        resolved = resolve_model_params("gemini-4-neon", {"reasoning_effort": "xhigh"})
        self.assertIn(resolved["reasoning_effort"], ("low", "medium", "high"))

    def test_through_openrouter(self):
        for key, effort in (("google/gemini-4-neon", "high"), ("google/gemini-4-argon", "medium")):
            native = self.native("openai_compat", "openrouter", key)
            self.assertEqual(native["extra_body"]["reasoning"], {"effort": effort}, key)
            self.assertEqual(native["extra_body"]["plugins"], [{"id": "web", "engine": "native"}], key)
            self.assertTrue(native["streaming"], key)
        resolved = resolve_model_params("google/gemini-4-neon", {"reasoning_effort": "xhigh"})
        self.assertEqual(resolved["reasoning_effort"], "xhigh")


class TestWebSearchSetting(StockParams):
    def test_on_by_default_where_the_model_has_a_search_route(self):
        """Stock `default` layer: web_search true (PU 2026-11-14)."""
        self.assertTrue(resolve_model_params("google/gemini-4-hydrogen")["web_search"])
        native = self.native("openai_compat", "openrouter", "google/gemini-4-hydrogen")
        self.assertEqual(native["extra_body"]["plugins"], [{"id": "web", "engine": "native"}])

    def test_off_when_the_setting_says_off(self):
        native = self.native("openai_compat", "openrouter", "google/gemini-4-hydrogen",
                             {"web_search": False})
        self.assertNotIn("plugins", native["extra_body"])

    def test_a_site_row_can_turn_it_off(self):
        merged = model_params._merge_layers(
            {"web_search": True}, normalize_custom_params({"web_search": False})[0],
        )
        self.assertIs(merged["web_search"], False)

    def test_ignored_for_a_model_without_a_search_route(self):
        native = self.native("openai_compat", "openrouter", "x-ai/grok-4.7", {"web_search": True})
        self.assertNotIn("plugins", native["extra_body"])

    def test_anthropic_direct_search_is_a_tool_not_a_request_field(self):
        native = self.native("anthropic", "anthropic", "claude-opus-5-5", {"web_search": True})
        self.assertNotIn("extra_body", native)
        with patch.object(model_params, "_load_catalog_provider", return_value="anthropic"), \
                patch.object(model_params, "_load_provider_cls", return_value="ChatAnthropic"):
            self.assertEqual(model_params.builtin_search_tools("claude-opus-5-5"), [{
                "type": "web_search_20260209", "name": "web_search", "max_uses": 5,
            }])
            self.assertEqual(model_params.web_search_route("claude-opus-5-5"), "anthropic")
            with patch.object(model_params, "resolve_model_params", return_value={"web_search": False}):
                self.assertEqual(model_params.builtin_search_tools("claude-opus-5-5"), [])


class TestParamsLayer(unittest.TestCase):
    def test_web_search_and_traits_are_stored(self):
        layer = build_params_layer_update(base={"reasoning_effort": "high"}, web_search=True,
                                          traits={"streaming": True})
        self.assertEqual(layer, {"reasoning_effort": "high", "web_search": True,
                                 "traits": {"streaming": True}})

    def test_empty_traits_clears_the_override(self):
        layer = build_params_layer_update(base={"traits": {"streaming": True}}, traits={})
        self.assertNotIn("traits", layer)

    def test_an_unknown_trait_is_refused(self):
        with self.assertRaises(ValueError):
            build_params_layer_update(traits={"colour": "red"})

    def test_normalize_drops_bad_values_with_a_warning(self):
        out, warnings = normalize_custom_params({"web_search": "yes", "traits": {"colour": 1}})
        self.assertEqual(out, {})
        self.assertEqual(len(warnings), 2)

    def test_custom_traits_merge_onto_the_stock_row(self):
        merged = model_params._merge_layers(
            {"traits": {"streaming": True}}, {"traits": {"max_output_tokens": 32000}},
        )
        self.assertEqual(merged["traits"], {"streaming": True, "max_output_tokens": 32000})


if __name__ == "__main__":
    unittest.main()
