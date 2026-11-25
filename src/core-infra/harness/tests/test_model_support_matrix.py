"""The support matrix, as a test (state_model_drivers.md §2.7 B9).

Every shipped model on every route has one row here: what its API accepts and
how the harness asks for it. A new shipped model fails this test until its row
is added here and in ``design/Versa AGi - Model Support Matrix.md``.
"""

from __future__ import annotations

import configparser
import json
import os
import sys
import unittest
from unittest.mock import patch

CORE_INFRA = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, CORE_INFRA)

from harness import model_params  # noqa: E402
from harness import model_traits as mt  # noqa: E402

STOCK = os.path.join(os.path.dirname(CORE_INFRA), "models.ini.stock")
MATRIX_DOC = os.path.join(
    os.path.dirname(os.path.dirname(CORE_INFRA)), "design", "Versa AGi - Model Support Matrix.md",
)

A, B, L = mt.THINKING_ADAPTIVE, mt.THINKING_BUDGET, mt.THINKING_LEVEL
BOUND, SIG = mt.REPLAY_BOUND, mt.REPLAY_SIGNATURE
OR, AN, OA = mt.SEARCH_OPENROUTER, mt.SEARCH_ANTHROPIC, mt.SEARCH_OPENAI
RESP = mt.API_RESPONSES

# key: (api, thinking, sampling, max_output_tokens, streaming, replay, web_search,
#       reasoning always on, default effort)
EXPECTED = {
    # Google's own API
    "gemini-3.7-flash":  ("", L, False, 0, False, SIG, "", True, "medium"),
    "gemini-4-hydrogen": ("", L, False, 0, True, SIG, "", True, "high"),
    "gemini-4-carbon":   ("", L, False, 0, True, SIG, "", True, "high"),
    "gemini-4-neon":     ("", L, False, 0, True, SIG, "", True, "high"),
    "gemini-4-argon":    ("", L, False, 0, True, SIG, "", True, "medium"),
    # xAI's own API
    "grok-4.6": ("", "", True, 0, False, "", "", True, "high"),
    "grok-4.7": ("", "", True, 0, False, "", "", True, "high"),
    # OpenAI's own API
    "gpt-6.2-astra": (RESP, "", True, 0, True, SIG, OA, True, "medium"),
    "gpt-6.1-sol":   (RESP, "", True, 0, True, SIG, OA, True, "medium"),
    "gpt-6-sol":     (RESP, "", True, 0, True, SIG, OA, False, "high"),
    "gpt-5.6-terra": (RESP, "", True, 0, False, SIG, OA, False, "none"),
    "gpt-5.6-sol":   (RESP, "", True, 0, False, SIG, OA, False, "none"),
    "gpt-5.6-luna":  (RESP, "", True, 0, False, SIG, OA, False, "none"),
    # Anthropic's own API
    "claude-opus-4-8":  ("", A, False, 64000, True, "", AN, False, "none"),
    "claude-opus-5-5":  ("", A, False, 64000, True, BOUND, AN, True, "high"),
    "claude-fable-5-2": ("", A, False, 64000, True, BOUND, AN, True, "high"),
    "claude-sonnet-5-5": ("", A, False, 64000, True, BOUND, AN, True, "high"),
    "claude-sonnet-5":   ("", A, False, 64000, True, "", AN, True, "high"),
    # OpenRouter
    "x-ai/grok-4.6": ("", "", True, 0, False, "", "", True, "high"),
    "x-ai/grok-4.7": ("", "", True, 0, False, "", "", True, "high"),
    "google/gemini-3.7-flash":  ("", "", True, 0, False, SIG, OR, True, "medium"),
    "google/gemini-4-hydrogen": ("", "", True, 0, True, SIG, OR, True, "high"),
    "google/gemini-4-carbon":   ("", "", True, 0, True, SIG, OR, True, "high"),
    "google/gemini-4-neon":     ("", "", True, 0, True, SIG, OR, True, "high"),
    "google/gemini-4-argon":    ("", "", True, 0, True, SIG, OR, True, "medium"),
    "openai/gpt-6.2-astra": ("", "", True, 0, True, SIG, "", True, "medium"),
    "openai/gpt-6.1-sol":   ("", "", True, 0, True, SIG, "", True, "medium"),
    "openai/gpt-6-sol":     ("", "", True, 0, True, SIG, "", False, "high"),
    "openai/gpt-5.6-terra": ("", "", True, 0, False, SIG, "", False, "none"),
    "openai/gpt-5.6-sol":   ("", "", True, 0, False, SIG, "", False, "none"),
    "openai/gpt-5.6-luna":  ("", "", True, 0, False, SIG, "", False, "none"),
    "anthropic/claude-opus-4.8":  ("", "", False, 0, False, "", OR, False, "none"),
    "anthropic/claude-opus-5.5":  ("", "", False, 0, True, BOUND, OR, True, "high"),
    "anthropic/claude-fable-5.2": ("", "", False, 0, True, BOUND, OR, True, "high"),
    "anthropic/claude-sonnet-5.5": ("", "", False, 0, True, BOUND, OR, True, "high"),
    "anthropic/claude-sonnet-5":   ("", "", False, 0, True, "", OR, True, "high"),
    "deepseek/deepseek-v4-flash-0731": ("", "", True, 0, False, "", "", False, "high"),
    "z-ai/glm-5.3-flash": ("", "", True, 0, False, "", "", True, "max"),
}

# Effort lists that differ between the two routes of one model, and why.
EFFORTS = {
    "gemini-4-hydrogen": ["low", "medium", "high"],  # the Google client has no xhigh
    "google/gemini-4-hydrogen": ["low", "medium", "high", "xhigh"],
    "gemini-4-carbon": ["low", "medium", "high"],
    "google/gemini-4-carbon": ["low", "medium", "high"],
    "gemini-4-neon": ["low", "medium", "high"],  # the Google client has no xhigh
    "google/gemini-4-neon": ["low", "medium", "high", "xhigh"],
    "gemini-4-argon": ["low", "medium", "high"],
    "google/gemini-4-argon": ["low", "medium", "high"],
    "claude-opus-5-5": ["low", "medium", "high", "xhigh", "max"],
    "claude-fable-5-2": ["low", "medium", "high", "xhigh", "max"],
    "anthropic/claude-opus-5.5": ["low", "medium", "high", "xhigh", "max"],
    "anthropic/claude-fable-5.2": ["low", "medium", "high", "xhigh", "max"],
    "gpt-6.1-sol": ["low", "medium", "high", "xhigh", "max"],
    "openai/gpt-6.1-sol": ["low", "medium", "high", "xhigh", "max"],
    "gpt-6-sol": ["none", "low", "medium", "high", "xhigh", "max"],
    "openai/gpt-6-sol": ["none", "low", "medium", "high", "xhigh", "max"],
}

_FAMILY = {
    "ChatAnthropic": "anthropic",
    "ChatGoogleGenerativeAI": "google",
    "ChatOpenAI": "openai_compat",
    "ChatOllama": "local",
}


def _stock() -> configparser.ConfigParser:
    ini = configparser.ConfigParser(delimiters=("=",), strict=False, interpolation=None)
    ini.optionxform = str
    ini.read(STOCK, encoding="utf-8")
    return ini


def _shipped(ini) -> dict[str, str]:
    """{catalog_key: provider} for every shipped offering."""
    out: dict[str, str] = {}
    for _offering, raw in ini.items("shipped_models"):
        for provider, key in json.loads(raw)["keys"].items():
            out[key] = provider
    return out


class TestSupportMatrix(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ini = _stock()
        cls.shipped = _shipped(cls.ini)
        cls.families = {
            slug: _FAMILY.get(row.split("|")[2].strip(), "")
            for slug, row in cls.ini.items("provider_library")
        }
        cls._paths = patch.object(model_params, "_MODELS_INI_PATHS", [STOCK])
        cls._paths.start()

    @classmethod
    def tearDownClass(cls):
        cls._paths.stop()

    def test_every_shipped_model_has_a_matrix_row(self):
        self.assertEqual(sorted(self.shipped), sorted(EXPECTED))

    def test_every_shipped_model_has_catalog_and_param_rows(self):
        for key, provider in self.shipped.items():
            self.assertTrue(self.ini.has_option("catalog_library", key), key)
            self.assertEqual(self.ini.get("catalog_library", key).split("|")[1], provider, key)
            layer = json.loads(self.ini.get("model_params", f"model:{key}"))
            self.assertTrue(layer.get("allowed_reasoning_efforts"), key)
            self.assertIn(layer.get("reasoning_effort"), layer["allowed_reasoning_efforts"], key)

    def test_traits_match_the_matrix(self):
        for key, provider in self.shipped.items():
            api, thinking, sampling, max_out, streaming, replay, search, always, effort = EXPECTED[key]
            traits = model_params.traits_for(key, self.families[provider], provider)
            got = (traits.api, traits.thinking, traits.sampling, traits.max_output_tokens,
                   traits.streaming, traits.replay, traits.web_search)
            self.assertEqual(got, (api, thinking, sampling, max_out, streaming, replay, search), key)
            self.assertEqual(model_params.reasoning_always_on(key), always, key)
            self.assertEqual(model_params.resolve_model_params(key)["reasoning_effort"], effort, key)

    def test_cache_marks_go_to_claude_rows_only(self):
        """Matrix §2 "Prompt caching": every other shipped route caches without being asked."""
        for key, provider in self.shipped.items():
            traits = model_params.traits_for(key, self.families[provider], provider)
            self.assertEqual(traits.cache == mt.CACHE_MARKS, "claude" in key, key)

    def test_effort_lists(self):
        for key, efforts in EFFORTS.items():
            self.assertEqual(list(model_params.allowed_reasoning_efforts(key)), efforts, key)

    def test_web_search_is_on_where_a_model_has_a_search_route(self):
        """On by default (stock `default` layer); it reaches a request only through a route."""
        for key, provider in self.shipped.items():
            self.assertTrue(model_params.resolve_model_params(key)["web_search"], key)
            traits = model_params.traits_for(key, self.families[provider], provider)
            params = model_params.resolve_model_params(key)
            native = model_params.to_native_kwargs(
                self.families[provider], key, params, provider_slug=provider,
            )
            plugins = (native.get("extra_body") or {}).get("plugins")
            reasoning = params["reasoning_effort"] != "none"
            tools = mt.builtin_search_tools(traits, params["web_search"], reasoning_on=reasoning)
            self.assertEqual(bool(plugins), traits.web_search == OR, key)
            # OpenAI's search is offered only while the model reasons.
            self.assertEqual(
                bool(tools),
                traits.web_search == AN or (traits.web_search == OA and reasoning), key,
            )

    def test_no_stock_model_row_sets_web_search(self):
        """The one switch is the `default` layer; a model row would hide it."""
        for name, raw in self.ini.items("model_params"):
            if name.startswith("model:"):
                self.assertNotIn("web_search", json.loads(raw), name)

    def test_a_model_that_always_reasons_never_resolves_to_none(self):
        for key in self.shipped:
            if not EXPECTED[key][7]:
                continue
            resolved = model_params.resolve_model_params(key, {"reasoning_effort": "none"})
            self.assertNotEqual(resolved["reasoning_effort"], "none", key)
            self.assertEqual(resolved["reasoning_effort"], EXPECTED[key][8], key)

    def test_the_matrix_document_names_every_shipped_model(self):
        if not os.path.isfile(MATRIX_DOC):
            self.skipTest("design documents are not part of an installation")
        with open(MATRIX_DOC, encoding="utf-8") as handle:
            text = handle.read()
        missing = [key for key in self.shipped if f"`{key}`" not in text]
        self.assertEqual(missing, [])

    def test_the_live_call_table_is_the_stock_list(self):
        """§3.3: same keys, same order, same level on Auto as the stock file."""
        if not os.path.isfile(MATRIX_DOC):
            self.skipTest("design documents are not part of an installation")
        with open(MATRIX_DOC, encoding="utf-8") as handle:
            text = handle.read()
        start = text.index("### 3.3 Shipped Live Call models")
        rows = []
        for line in text[start:text.index("\n---\n", start)].split("\n"):
            cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
            if len(cells) == 7 and cells[3].startswith("`"):
                rows.append((cells[3].strip("`"), cells[4]))
        ini = _stock()
        stock = [(key, ini.get("catalog_live_call_reasoning", key)) for key in ini.options("catalog_live_call")]
        self.assertEqual(rows, stock)


if __name__ == "__main__":
    unittest.main()
