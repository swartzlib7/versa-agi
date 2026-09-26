"""Live call settings, call-capable keys, and the call-tool gate (state_live_voice_call.md §1.4, §1.7)."""

import os
import tempfile
import textwrap
import unittest
import unittest.mock

import live_call_config as lcc

MODELS_INI = textwrap.dedent("""
    [catalog]
    gemini-3.7-flash = cloud|google|true|true|0|1048576|balanced|text,image,audio,video|text|true|Gemini 3.7 Flash
    grok-4.6         = cloud|xai|true|true|0|500000|reasoning|text,image|text|true|Grok 4.6
    gpt-5.6-luna     = cloud|openai|true|true|0|1050000|fast|text,image|text|true|GPT-5.6 Luna
    gemma4:e4b       = local|llamacpp|false|false|32768|131072|local|text|text|false|Gemma 4 E4B

    [catalog_live_call]
    gemini-3.7-flash = true
    grok-4.6         = true
    gemma4:e4b       = true

    [catalog_live_call_custom]
    grok-4.6     = false
    gpt-5.6-luna = true
""")

SETUP_INI = textwrap.dedent("""
    [features]
    live_call=true

    [live_call]
    call_model=gemini-3.7-flash
    voice_model=gpt-live-1
    max_minutes=90
    join_timeout_seconds=abc
    calls_per_cycle=9
""")


class _Files(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.models = os.path.join(self.tmp.name, "models.ini")
        self.setup_ini = os.path.join(self.tmp.name, "setup.ini")
        with open(self.models, "w") as f:
            f.write(MODELS_INI)
        with open(self.setup_ini, "w") as f:
            f.write(SETUP_INI)

    def tearDown(self):
        self.tmp.cleanup()


class TestSettings(_Files):
    def test_reads_and_clamps(self):
        s = lcc.read_settings(self.setup_ini)
        self.assertTrue(s.enabled)
        self.assertEqual(s.call_model, "gemini-3.7-flash")
        self.assertEqual(s.voice_model, "gpt-live-1")
        self.assertEqual(s.max_minutes, lcc.MAX_MINUTES_RANGE[1])
        self.assertEqual(s.join_timeout_seconds, lcc.DEFAULT_JOIN_TIMEOUT_SECONDS)
        self.assertEqual(s.calls_per_cycle, lcc.CALLS_PER_CYCLE_RANGE[1])

    def test_defaults_when_absent(self):
        empty = os.path.join(self.tmp.name, "empty.ini")
        open(empty, "w").close()
        s = lcc.read_settings(empty)
        self.assertFalse(s.enabled)
        self.assertEqual(s.call_model, "")
        self.assertEqual(s.max_minutes, lcc.DEFAULT_MAX_MINUTES)
        self.assertEqual(s.calls_per_cycle, 1)


class TestCallCapable(_Files):
    def test_site_layer_adds_and_removes(self):
        keys = lcc.call_capable_keys(self.models)
        self.assertEqual(keys, {"gemini-3.7-flash", "gemma4:e4b", "gpt-5.6-luna"})
        self.assertFalse(lcc.is_call_capable("grok-4.6", self.models))

    def test_shipped_keys_ignore_site_layer(self):
        self.assertEqual(
            lcc.shipped_call_capable_keys(self.models),
            {"gemini-3.7-flash", "grok-4.6", "gemma4:e4b"},
        )

    def test_selectable_needs_enabled_and_keyed(self):
        rows = lcc.selectable_call_models(models_ini=self.models, key_check=lambda slug: slug == "google")
        self.assertEqual([r["key"] for r in rows], ["gemini-3.7-flash"])

    def test_display_is_provider_then_model_name(self):
        labels = {"google": "Google", "openrouter": "OpenRouter", "xai": "xAI"}
        self.assertEqual(
            lcc.model_display("x-ai/grok-4.6", {"provider": "openrouter", "label": "Grok 4.6 — xAI flagship"}, labels),
            "OpenRouter — Grok 4.6",
        )
        self.assertEqual(
            lcc.model_display("gemini-3.7-flash", {"provider": "google", "label": "Gemini 3.7 Flash"}, labels),
            "Google — Gemini 3.7 Flash",
        )
        self.assertEqual(lcc.model_display("mystery", None, labels), "mystery")

    def test_selectable_sorted_by_display(self):
        with unittest.mock.patch.object(lcc, "provider_labels",
                                        return_value={"google": "Google", "openai": "OpenAI"}):
            rows = lcc.selectable_call_models(models_ini=self.models, key_check=lambda slug: True)
        self.assertEqual([r["display"] for r in rows], ["Google — Gemini 3.7 Flash", "OpenAI — GPT-5.6 Luna"])

    def test_site_layer_value(self):
        shipped = {"grok-4.6"}
        self.assertIsNone(lcc.site_layer_value("grok-4.6", True, shipped))
        self.assertEqual(lcc.site_layer_value("grok-4.6", False, shipped), "false")
        self.assertEqual(lcc.site_layer_value("gpt-5.6-luna", True, shipped), "true")
        self.assertIsNone(lcc.site_layer_value("gpt-5.6-luna", False, shipped))


class TestGate(_Files):
    def _gate(self, agent="coa", keyed=("openai", "google"), **setting_overrides):
        base = lcc.read_settings(self.setup_ini)
        settings = lcc.LiveCallSettings(**{**base.__dict__, **setting_overrides})
        return lcc.evaluate_gate(agent, settings=settings, models_ini=self.models,
                                 key_check=lambda slug: slug in keyed)

    def test_ready(self):
        gate = self._gate()
        self.assertTrue(gate.ok, gate.reasons)
        self.assertEqual(gate.call_provider, "google")

    def test_sub_agent_refused(self):
        gate = self._gate(agent="web-dev")
        self.assertFalse(gate.ok)
        self.assertIn("only COA can place calls", gate.reasons)

    def test_feature_off(self):
        self.assertFalse(self._gate(enabled=False).ok)

    def test_no_openai_key(self):
        gate = self._gate(keyed=("google",))
        self.assertTrue(any("OpenAI" in r for r in gate.reasons))

    def test_call_model_provider_unkeyed(self):
        gate = self._gate(keyed=("openai",))
        self.assertTrue(any("provider 'google'" in r for r in gate.reasons))

    def test_call_model_not_call_capable(self):
        gate = self._gate(call_model="grok-4.6", keyed=("openai", "xai"))
        self.assertTrue(any("not call-capable" in r for r in gate.reasons))

    def test_empty_call_model(self):
        gate = self._gate(call_model="")
        self.assertTrue(any("no call model" in r for r in gate.reasons))

    def test_disabled_catalog_row(self):
        gate = self._gate(call_model="gemma4:e4b", keyed=("openai", "llamacpp"))
        self.assertTrue(any("disabled" in r for r in gate.reasons))


class TestValidateSetting(_Files):
    def _v(self, section, key, value):
        return lcc.validate_setting(section, key, value, models_ini=self.models)

    def test_feature_flag(self):
        self.assertEqual(self._v("features", "live_call", "TRUE"), (True, "", "true"))
        self.assertFalse(self._v("features", "live_call", "maybe")[0])

    def test_call_model(self):
        self.assertTrue(self._v("live_call", "call_model", "")[0])
        self.assertTrue(self._v("live_call", "call_model", "gemini-3.7-flash")[0])
        self.assertIn("not a call-capable", self._v("live_call", "call_model", "grok-4.6")[1])
        self.assertIn("not an enabled model", self._v("live_call", "call_model", "gemma4:e4b")[1])

    def test_numbers_and_unknown_keys(self):
        self.assertEqual(self._v("live_call", "max_minutes", " 20 "), (True, "", "20"))
        self.assertFalse(self._v("live_call", "max_minutes", "90")[0])
        self.assertFalse(self._v("live_call", "join_timeout_seconds", "abc")[0])
        self.assertEqual(self._v("live_call", "calls_per_cycle", "3"), (True, "", "3"))
        self.assertFalse(self._v("live_call", "calls_per_cycle", "0")[0])
        self.assertFalse(self._v("live_call", "calls_per_cycle", "6")[0])
        self.assertFalse(self._v("live_call", "voice_model", "")[0])
        self.assertFalse(self._v("live_call", "ringtone", "x")[0])

    def test_narrate_progress(self):
        self.assertEqual(self._v("live_call", "narrate_progress", "False"), (True, "", "false"))
        self.assertFalse(self._v("live_call", "narrate_progress", "sometimes")[0])
        self.assertTrue(lcc.read_settings(self.setup_ini).narrate_progress)

    def test_other_sections_pass_through(self):
        self.assertEqual(self._v("search", "enabled", "true"), (True, "", "true"))


class TestShippedStock(unittest.TestCase):
    def test_stock_ships_gemini_and_grok(self):
        stock = os.path.join(os.path.dirname(__file__), "..", "..", "..", "models.ini.stock")
        self.assertEqual(
            lcc.shipped_call_capable_keys(os.path.abspath(stock)),
            {"gemini-3.7-flash", "google/gemini-3.7-flash", "grok-4.6", "x-ai/grok-4.6"},
        )

    def test_stock_setup_ini_defaults_off(self):
        stock = os.path.join(os.path.dirname(__file__), "..", "..", "..", "setup.ini.stock")
        s = lcc.read_settings(os.path.abspath(stock))
        self.assertFalse(s.enabled)
        self.assertEqual(s.call_model, "")
        self.assertEqual((s.voice_model, s.max_minutes, s.join_timeout_seconds), ("gpt-live-1", 15, 45))
        self.assertTrue(s.narrate_progress)


if __name__ == "__main__":
    unittest.main()
