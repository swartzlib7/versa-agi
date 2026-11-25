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
        self.assertEqual(s.max_minutes, 25)  # old 90 snaps to the largest choice
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
        self.assertEqual(s.reasoning_effort, "auto")

    def test_reasoning_level_is_read_and_a_wrong_value_falls_back(self):
        for stored, expected in (("low", "low"), ("HIGH", "high"), ("auto", "auto"), ("max", "auto"), ("", "auto")):
            path = os.path.join(self.tmp.name, "r.ini")
            with open(path, "w") as f:
                f.write(f"[live_call]\nreasoning_effort={stored}\n")
            self.assertEqual(lcc.read_settings(path).reasoning_effort, expected, stored)


class TestCallEffort(unittest.TestCase):
    """The reasoning level a call turn asks for (state_live_voice_call.md §3.13)."""

    def test_auto_uses_the_level_shipped_for_the_model(self):
        allowed = ("low", "medium", "high")
        self.assertEqual(lcc.call_effort(allowed, "auto", "low"), "low")
        self.assertEqual(lcc.call_effort(allowed, "auto", "medium"), "medium")
        self.assertEqual(lcc.call_effort(allowed, "auto", ""), "medium")       # nothing shipped
        self.assertEqual(lcc.call_effort(allowed, "auto", "xhigh"), "medium")  # not a call level

    def test_a_chosen_level_overrides_the_shipped_one(self):
        allowed = ("low", "medium", "high")
        self.assertEqual(lcc.call_effort(allowed, "medium", "low"), "medium")
        self.assertEqual(lcc.call_effort(allowed, "low", "medium"), "low")
        self.assertEqual(lcc.call_effort(allowed, "high", "low"), "high")

    def test_medium_where_the_model_has_it(self):
        self.assertEqual(lcc.call_effort(("low", "medium", "high")), "medium")
        self.assertEqual(lcc.call_effort(("none", "low", "medium", "high", "xhigh", "max")), "medium")

    def test_nearest_level_below_when_medium_is_missing(self):
        self.assertEqual(lcc.call_effort(("low", "high", "max")), "low")
        self.assertEqual(lcc.call_effort(("none", "low", "high", "max")), "low")

    def test_lowest_reasoning_level_when_nothing_is_at_or_below(self):
        self.assertEqual(lcc.call_effort(("high", "xhigh")), "high")
        self.assertEqual(lcc.call_effort(("none", "high")), "high")

    def test_a_model_that_cannot_reason(self):
        self.assertEqual(lcc.call_effort(("none",)), "none")
        self.assertIsNone(lcc.call_effort(()))

    def test_the_setting_moves_the_level(self):
        allowed = ("low", "medium", "high", "xhigh")
        self.assertEqual(lcc.call_effort(allowed, "low"), "low")
        self.assertEqual(lcc.call_effort(allowed, "high"), "high")
        self.assertEqual(lcc.call_effort(allowed, "nonsense"), "medium")


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

    def test_order_is_the_shipped_order_then_site_keys_by_name(self):
        self.assertEqual(lcc.call_order(self.models), ["gemini-3.7-flash", "gemma4:e4b", "gpt-5.6-luna"])

    def test_suggested_is_the_first_usable_key_in_order(self):
        both = lcc.selectable_call_models(models_ini=self.models, key_check=lambda slug: True)
        self.assertEqual([r["key"] for r in both if r["suggested"]], ["gemini-3.7-flash"])
        self.assertEqual(lcc.suggested_call_model(models_ini=self.models, key_check=lambda slug: True),
                         "gemini-3.7-flash")
        self.assertEqual(lcc.suggested_call_model(models_ini=self.models, key_check=lambda slug: slug == "openai"),
                         "gpt-5.6-luna")
        self.assertEqual(lcc.suggested_call_model(models_ini=self.models, key_check=lambda slug: False), "")

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
        self.assertEqual(self._v("live_call", "max_minutes", " 25 "), (True, "", "25"))
        self.assertFalse(self._v("live_call", "max_minutes", "20")[0])
        self.assertIn("one of 5, 10, 15, 25", self._v("live_call", "max_minutes", "90")[1])
        self.assertFalse(self._v("live_call", "max_minutes", "abc")[0])
        self.assertFalse(self._v("live_call", "join_timeout_seconds", "abc")[0])
        self.assertEqual(self._v("live_call", "calls_per_cycle", "3"), (True, "", "3"))
        self.assertFalse(self._v("live_call", "calls_per_cycle", "0")[0])
        self.assertFalse(self._v("live_call", "calls_per_cycle", "6")[0])
        self.assertFalse(self._v("live_call", "voice_model", "")[0])
        self.assertFalse(self._v("live_call", "ringtone", "x")[0])

    def test_reasoning_effort(self):
        self.assertEqual(self._v("live_call", "reasoning_effort", "High"), (True, "", "high"))
        self.assertEqual(self._v("live_call", "reasoning_effort", "auto"), (True, "", "auto"))
        ok, err, _ = self._v("live_call", "reasoning_effort", "max")
        self.assertFalse(ok)
        self.assertIn("auto, low, medium, high", err)

    def test_narrate_progress(self):
        self.assertEqual(self._v("live_call", "narrate_progress", "False"), (True, "", "false"))
        self.assertFalse(self._v("live_call", "narrate_progress", "sometimes")[0])
        self.assertTrue(lcc.read_settings(self.setup_ini).narrate_progress)

    def test_other_sections_pass_through(self):
        self.assertEqual(self._v("search", "enabled", "true"), (True, "", "true"))


class TestShippedStock(unittest.TestCase):
    STOCK = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "models.ini.stock"))
    # Best first; for each model the provider's own key, then its OpenRouter alias (§3.13).
    ORDER = [
        "claude-sonnet-5-5", "anthropic/claude-sonnet-5.5",
        "gemini-4-hydrogen", "google/gemini-4-hydrogen",
        "claude-opus-5-5", "anthropic/claude-opus-5.5",
        "claude-sonnet-5", "anthropic/claude-sonnet-5",
        "claude-opus-4-8", "anthropic/claude-opus-4.8",
        "grok-4.6", "x-ai/grok-4.6",
        "gemini-3.7-flash", "google/gemini-3.7-flash",
    ]

    def test_stock_call_models_in_order_of_preference(self):
        self.assertEqual(lcc.call_order(self.STOCK), self.ORDER)
        self.assertEqual(lcc.shipped_call_capable_keys(self.STOCK), set(self.ORDER))

    def test_carbon_and_fable_are_not_shipped_call_models(self):
        """Carbon answered in about 35 s; Fable is the most expensive model (§6, 2026-11-16)."""
        for key in ("gemini-4-carbon", "google/gemini-4-carbon", "claude-fable-5-2", "anthropic/claude-fable-5.2"):
            self.assertNotIn(key, self.ORDER)

    def test_every_stock_call_model_is_a_stock_catalog_model_with_its_own_key_first(self):
        library = lcc._read_raw_section(self.STOCK, "catalog_library")
        for own, alias in zip(self.ORDER[0::2], self.ORDER[1::2]):
            self.assertIn(own, library)
            self.assertIn(alias, library)
            self.assertNotEqual(library[own].split("|")[1], "openrouter", own)
            self.assertEqual(library[alias].split("|")[1], "openrouter", alias)

    def _suggest(self, keyed):
        library = lcc._read_raw_section(self.STOCK, "catalog_library")
        catalog = {key: {"enabled": True, "provider": row.split("|")[1], "label": row.split("|")[-1]}
                   for key, row in library.items()}
        return lcc.suggested_call_model(models_ini=self.STOCK, catalog=catalog,
                                        key_check=lambda slug: slug in keyed)

    def test_suggested_call_model_by_which_keys_exist(self):
        self.assertEqual(self._suggest({"anthropic", "openrouter", "google"}), "claude-sonnet-5-5")
        self.assertEqual(self._suggest({"openrouter"}), "anthropic/claude-sonnet-5.5")
        self.assertEqual(self._suggest({"google", "openrouter"}), "anthropic/claude-sonnet-5.5")
        self.assertEqual(self._suggest({"google"}), "gemini-4-hydrogen")
        self.assertEqual(self._suggest({"xai"}), "grok-4.6")
        self.assertEqual(self._suggest({"openai"}), "")

    # Measured 2026-11-16 (state_live_voice_call.md §6).
    LEVELS = {
        "claude-sonnet-5-5": "medium", "anthropic/claude-sonnet-5.5": "medium",
        "gemini-4-hydrogen": "low", "google/gemini-4-hydrogen": "medium",
        "claude-opus-5-5": "medium", "anthropic/claude-opus-5.5": "medium",
        "claude-sonnet-5": "medium", "anthropic/claude-sonnet-5": "medium",
        "claude-opus-4-8": "medium", "anthropic/claude-opus-4.8": "medium",
        "grok-4.6": "low", "x-ai/grok-4.6": "low",
        "gemini-3.7-flash": "low", "google/gemini-3.7-flash": "low",
    }

    def test_every_stock_call_model_has_a_measured_level_it_can_run_at(self):
        import json
        params = lcc._read_raw_section(self.STOCK, "model_params")
        self.assertEqual(set(lcc._read_raw_section(self.STOCK, lcc.CALL_REASONING_SECTION)), set(self.ORDER))
        for key in self.ORDER:
            level = lcc.shipped_call_reasoning(key, self.STOCK)
            self.assertEqual(level, self.LEVELS[key], key)
            allowed = json.loads(params[f"model:{key}"])["allowed_reasoning_efforts"]
            self.assertEqual(lcc.call_effort_for(key, allowed, "auto", self.STOCK), level, key)

    def test_a_model_with_no_shipped_level_gets_medium_on_auto(self):
        self.assertEqual(lcc.shipped_call_reasoning("gpt-6.1-sol", self.STOCK), "")
        self.assertEqual(lcc.call_effort_for("gpt-6.1-sol", ("low", "medium", "high"), "auto", self.STOCK), "medium")

    def test_stock_setup_ini_defaults_off(self):
        stock = os.path.join(os.path.dirname(__file__), "..", "..", "..", "setup.ini.stock")
        s = lcc.read_settings(os.path.abspath(stock))
        self.assertFalse(s.enabled)
        self.assertEqual(s.call_model, "")
        self.assertEqual((s.voice_model, s.max_minutes, s.join_timeout_seconds), ("gpt-live-1", 15, 45))
        self.assertTrue(s.narrate_progress)
        self.assertEqual(s.reasoning_effort, "auto")


class TestMaxMinutesChoices(unittest.TestCase):
    """state_live_chat.md D7: 5 / 10 / 15 / 25; old 1–55 values snap on read."""

    def test_choices(self):
        self.assertEqual(lcc.MAX_MINUTES_CHOICES, (5, 10, 15, 25))
        self.assertIn(lcc.DEFAULT_MAX_MINUTES, lcc.MAX_MINUTES_CHOICES)

    def test_snap(self):
        cases = {"1": 5, "5": 5, "7": 5, "8": 10, "12": 10, "13": 15, "15": 15,
                 "20": 15, "21": 25, "55": 25, "": 15, "x": 15}
        for raw, want in cases.items():
            self.assertEqual(lcc.snap_max_minutes(raw), want, raw)


if __name__ == "__main__":
    unittest.main()
