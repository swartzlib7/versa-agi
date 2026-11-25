"""Unit tests for the shipped selection registry."""

from __future__ import annotations

import configparser
import json
import os
import sys
import unittest

CORE_INFRA = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SRC_ROOT = os.path.dirname(CORE_INFRA)
sys.path.insert(0, CORE_INFRA)

from coa_shipped import all_keys, keys_for_provider, recommended_pairs  # noqa: E402
from model_catalog import parse_catalog_row  # noqa: E402
from shipped_models import load_offerings  # noqa: E402

STOCK = os.path.join(SRC_ROOT, "models.ini.stock")


def _stock_cfg(name: str) -> configparser.ConfigParser:
    path = os.path.join(SRC_ROOT, name)
    cfg = configparser.ConfigParser(delimiters=("=",), strict=False)
    cfg.optionxform = str
    cfg.read(path)
    return cfg


class CoaShippedMap(unittest.TestCase):
    def test_openrouter_order(self):
        self.assertEqual(
            keys_for_provider("openrouter", STOCK),
            [
                "anthropic/claude-opus-5.5",
                "anthropic/claude-sonnet-5.5",
                "x-ai/grok-4.7",
                "x-ai/grok-4.6",
                "google/gemini-4-carbon",
                "google/gemini-3.7-flash",
                "openai/gpt-6.1-sol",
                "openai/gpt-6-sol",
                "openai/gpt-5.6-terra",
                "anthropic/claude-opus-4.8",
                "anthropic/claude-sonnet-5",
                "anthropic/claude-fable-5.2",
                "google/gemini-4-hydrogen",
                "openai/gpt-5.6-sol",
                "z-ai/glm-5.3-flash",
                "deepseek/deepseek-v4-flash-0731",
                "openai/gpt-5.6-luna",
                "google/gemini-4-neon",
                "google/gemini-4-argon",
                "openai/gpt-6.2-astra",
            ],
        )

    def test_grok_dual_provider(self):
        self.assertEqual(keys_for_provider("xai", STOCK), ["grok-4.7", "grok-4.6"])
        self.assertIn("x-ai/grok-4.6", keys_for_provider("openrouter", STOCK))
        self.assertIn("x-ai/grok-4.7", keys_for_provider("openrouter", STOCK))
        self.assertIn("grok-4.6", all_keys(STOCK))
        self.assertIn("grok-4.7", all_keys(STOCK))
        self.assertIn("x-ai/grok-4.6", all_keys(STOCK))
        self.assertIn("x-ai/grok-4.7", all_keys(STOCK))

    def test_pairs_label_order(self):
        labels = [label for _, label in recommended_pairs("openrouter", STOCK)]
        self.assertEqual(
            labels,
            [
                "Opus 5.5",
                "Sonnet 5.5",
                "Grok 4.7",
                "Grok 4.6",
                "Gemini 4 Carbon",
                "Gemini 3.7 Flash",
                "GPT-6.1 Sol",
                "GPT-6 Sol",
                "GPT-5.6 Terra",
                "Opus 4.8",
                "Sonnet 5",
                "Fable 5.2",
                "Gemini 4 Hydrogen",
                "GPT-5.6 Sol",
                "GLM 5.3 Flash",
                "DeepSeek V4Flash0731",
                "GPT-5.6 Luna",
                "Gemini 4 Neon",
                "Gemini 4 Argon",
                "GPT-6.2 Astra",
            ],
        )

    def test_offering_order_matches_stock_file(self):
        offerings = load_offerings(os.path.join(SRC_ROOT, "models.ini.stock"))
        self.assertEqual(
            [oid for oid, _, _ in offerings],
            [
                "claude-opus-5-5",
                "claude-sonnet-5-5",
                "grok-4.7",
                "grok-4.6",
                "gemini-4-carbon",
                "gemini-3.7-flash",
                "gpt-6.1-sol",
                "gpt-6-sol",
                "gpt-5.6-terra",
                "claude-opus-4-8",
                "claude-sonnet-5",
                "claude-fable-5-2",
                "gemini-4-hydrogen",
                "gpt-5.6-sol",
                "glm-5.3-flash",
                "deepseek-v4-flash-0731",
                "gpt-5.6-luna",
                "gemini-4-neon",
                "gemini-4-argon",
                "gpt-6.2-astra",
            ],
        )


class StockLayerParity(unittest.TestCase):
    def test_setup_stock_has_no_model_or_enable_lists(self):
        cfg = _stock_cfg("setup.ini.stock")
        self.assertFalse(cfg.has_section("gemini"))
        self.assertTrue(cfg.has_section("system"))
        self.assertTrue(cfg.has_option("system", "mode"))
        self.assertTrue(cfg.has_option("system", "model"))
        self.assertTrue(cfg.has_option("third_party", "google_api_key"))
        self.assertTrue(cfg.has_option("gcp", "auth_method"))
        self.assertFalse(cfg.has_option("third_party", "providers"))
        for slug in ("google", "xai", "openai", "anthropic", "openrouter"):
            self.assertFalse(cfg.has_option("third_party", f"{slug}_models"))
            self.assertFalse(cfg.has_option("third_party", f"{slug}_enabled"))
        for section in cfg.sections():
            self.assertFalse(cfg.has_option(section, "coa_approved_models"))
            self.assertFalse(cfg.has_option(section, "thinking_level"))

    def test_models_stock_has_registries(self):
        cfg = _stock_cfg("models.ini.stock")
        self.assertTrue(cfg.has_section("shipped_models"))
        self.assertTrue(cfg.has_section("provider_library"))
        self.assertTrue(cfg.has_section("providers_site"))
        self.assertTrue(cfg.has_option("providers_site", "enabled"))

    def test_remote_library_rows_are_cloud(self):
        cfg = _stock_cfg("models.ini.stock")
        for key, raw in cfg.items("catalog_library"):
            row = parse_catalog_row(raw)
            if not row or row["class"] == "local":
                continue
            with self.subTest(key=key):
                self.assertEqual(row["class"], "cloud")

    def test_remote_library_matches_shipped_only(self):
        cfg = _stock_cfg("models.ini.stock")
        remote = {
            key for key, raw in cfg.items("catalog_library")
            if (parse_catalog_row(raw) or {}).get("class") == "cloud"
        }
        self.assertEqual(remote, set(all_keys(os.path.join(SRC_ROOT, "models.ini.stock"))))
        for key in (
            "gemini-2.5-pro",
            "gemini-3.1-flash-image",
            "grok-4.5",
            "gpt-audio-1.5",
            "claude-fable-5",
            "openai/gpt-5.4-image-2",
            "moonshotai/kimi-k2.7-code",
        ):
            self.assertNotIn(key, remote)

    def test_provider_reported_context_limits(self):
        cfg = _stock_cfg("models.ini.stock")
        expected = {
            "gemini-3.7-flash": 1_048_576,
            "grok-4.6": 500_000,
            "grok-4.7": 500_000,
            "gpt-6.1-sol": 1_050_000,
            "openai/gpt-6.1-sol": 1_050_000,
            "gpt-6-sol": 1_050_000,
            "openai/gpt-6-sol": 1_050_000,
            "gpt-5.6-terra": 1_050_000,
            "gpt-5.6-sol": 1_050_000,
            "gpt-5.6-luna": 1_050_000,
            "claude-opus-4-8": 1_000_000,
            "claude-opus-5-5": 1_000_000,
            "claude-fable-5-2": 1_000_000,
            "claude-sonnet-5-5": 1_000_000,
            "claude-sonnet-5": 1_000_000,
            "anthropic/claude-sonnet-5.5": 1_000_000,
            "anthropic/claude-sonnet-5": 1_000_000,
            "gemini-4-hydrogen": 1_048_576,
            "gemini-4-carbon": 1_048_576,
            "gemini-4-neon": 1_048_576,
            "gemini-4-argon": 1_048_576,
            "google/gemini-4-neon": 1_048_576,
            "google/gemini-4-argon": 1_048_576,
            "gpt-6.2-astra": 1_050_000,
            "openai/gpt-6.2-astra": 1_050_000,
            "anthropic/claude-opus-5.5": 1_000_000,
            "anthropic/claude-fable-5.2": 1_000_000,
            "google/gemini-4-hydrogen": 1_048_576,
            "google/gemini-4-carbon": 1_048_576,
            "deepseek/deepseek-v4-flash-0731": 1_310_720,
            "z-ai/glm-5.3-flash": 1_048_576,
        }
        for key, context in expected.items():
            with self.subTest(key=key):
                row = parse_catalog_row(cfg.get("catalog_library", key))
                self.assertEqual(row["ctx_max"], context)
        flash = parse_catalog_row(cfg.get("catalog_library", "z-ai/glm-5.3-flash"))
        self.assertEqual(flash["input_modalities"], "text,image,video")

    def test_provider_reported_reasoning_efforts(self):
        cfg = _stock_cfg("models.ini.stock")
        expected = {
            "gemini-3.7-flash": ("medium", ["low", "medium", "high"]),
            "grok-4.6": ("high", ["low", "medium", "high", "xhigh"]),
            "grok-4.7": ("high", ["low", "medium", "high", "xhigh"]),
            # OpenAI's own default for GPT-6.1 Sol and its advice for GPT-6.2 Astra (PU 2026-11-19).
            "gpt-6.1-sol": ("medium", ["low", "medium", "high", "xhigh", "max"]),
            "openai/gpt-6.1-sol": ("medium", ["low", "medium", "high", "xhigh", "max"]),
            "gpt-6.2-astra": ("medium", ["low", "medium", "high", "xhigh", "max"]),
            "openai/gpt-6.2-astra": ("medium", ["low", "medium", "high", "xhigh", "max"]),
            "gemini-4-neon": ("high", ["low", "medium", "high"]),
            "google/gemini-4-neon": ("high", ["low", "medium", "high", "xhigh"]),
            "gemini-4-argon": ("medium", ["low", "medium", "high"]),
            "google/gemini-4-argon": ("medium", ["low", "medium", "high"]),
            "gpt-6-sol": (
                "high", ["none", "low", "medium", "high", "xhigh", "max"]
            ),
            "openai/gpt-6-sol": (
                "high", ["none", "low", "medium", "high", "xhigh", "max"]
            ),
            "gpt-5.6-terra": (
                "none", ["none", "low", "medium", "high", "xhigh", "max"]
            ),
            "gpt-5.6-sol": (
                "none", ["none", "low", "medium", "high", "xhigh", "max"]
            ),
            "gpt-5.6-luna": (
                "none", ["none", "low", "medium", "high", "xhigh", "max"]
            ),
            "claude-opus-4-8": (
                "none", ["none", "low", "medium", "high", "xhigh", "max"]
            ),
            # Reasoning cannot be turned off on these four: no "none".
            "claude-opus-5-5": ("high", ["low", "medium", "high", "xhigh", "max"]),
            "claude-fable-5-2": ("high", ["low", "medium", "high", "xhigh", "max"]),
            # Sonnet 5.5 cannot have thinking off; Sonnet 5 can, but the harness has no
            # request for it (leaving thinking out runs it), so "none" is not offered.
            "claude-sonnet-5-5": ("high", ["low", "medium", "high", "xhigh", "max"]),
            "claude-sonnet-5": ("high", ["low", "medium", "high", "xhigh", "max"]),
            "anthropic/claude-sonnet-5.5": ("high", ["low", "medium", "high", "xhigh", "max"]),
            "anthropic/claude-sonnet-5": ("high", ["low", "medium", "high", "xhigh", "max"]),
            "gemini-4-hydrogen": ("high", ["low", "medium", "high"]),
            "gemini-4-carbon": ("high", ["low", "medium", "high"]),
            "google/gemini-4-hydrogen": ("high", ["low", "medium", "high", "xhigh"]),
            "deepseek/deepseek-v4-flash-0731": (
                "high", ["none", "low", "high", "max"]
            ),
            "z-ai/glm-5.3-flash": ("max", ["low", "high", "max"]),
        }
        for key, (default, efforts) in expected.items():
            with self.subTest(key=key):
                params = json.loads(cfg.get("model_params", f"model:{key}"))
                self.assertEqual(params["reasoning_effort"], default)
                self.assertEqual(params["allowed_reasoning_efforts"], efforts)


if __name__ == "__main__":
    unittest.main()
