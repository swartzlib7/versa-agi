"""MD-CAT regressions for shipped multimodal output model metadata."""

from __future__ import annotations

import configparser
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

CORE_INFRA = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)
SRC_ROOT = os.path.dirname(CORE_INFRA)
sys.path.insert(0, CORE_INFRA)
sys.path.insert(0, os.path.join(CORE_INFRA, "agictl"))

from model_catalog import (  # noqa: E402
    load_catalog,
    model_output_includes,
    parse_catalog_row,
    validate_preferred_output_key,
)


OUTPUT_MODELS = {
    "google/gemini-3.1-flash-image": {
        "provider": "openrouter",
        "input": {"text", "image"},
        "output": {"text", "image"},
        "ctx_max": 131072,
    },
    "openai/gpt-audio": {
        "provider": "openrouter",
        "input": {"text", "audio"},
        "output": {"text", "audio"},
        "ctx_max": 128000,
    },
    "gpt-audio-1.5": {
        "provider": "openai",
        "input": {"text", "audio"},
        "output": {"text", "audio"},
        "ctx_max": 128000,
    },
    "gemini-3.1-flash-image": {
        "provider": "google",
        "input": {"text", "image"},
        "output": {"text", "image"},
        "ctx_max": 131072,
    },
}

PROMOTED_MODELS = {
    "openai/gpt-5.6-luna": {
        "provider": "openrouter",
        "input": {"text", "image"},
        "output": {"text"},
        "ctx_max": 1050000,
    },
    "openai/gpt-5.4-image-2": {
        "provider": "openrouter",
        "input": {"text", "image"},
        "output": {"text", "image"},
        "ctx_max": 272000,
    },
    "openai/gpt-audio-mini": {
        "provider": "openrouter",
        "input": {"text", "audio"},
        "output": {"text", "audio"},
        "ctx_max": 128000,
    },
}

CURRENT_INPUT_REPLACEMENTS = {
    "gemini-3.1-flash-lite": {
        "provider": "google",
        "input": {"text", "image", "audio", "video"},
        "ctx_max": 1048576,
    },
    "grok-4.5": {
        "provider": "xai",
        "input": {"text", "image"},
        "ctx_max": 500000,
    },
}

RETIRED_INPUT_MODELS = {
    "gemini-3-pro-preview",
    "gemini-3.1-flash-lite-preview",
    "grok-4-1-fast-reasoning",
    "grok-4.3",
    "grok-4.20-reasoning",
}


def _csv(raw: str) -> set[str]:
    return {item.strip() for item in raw.split(",") if item.strip()}


def _config(path: str) -> configparser.ConfigParser:
    cfg = configparser.ConfigParser(delimiters=("=",), strict=False)
    cfg.optionxform = str
    cfg.read(path)
    return cfg


class TestModelDriverCatalogStock(unittest.TestCase):
    def setUp(self) -> None:
        stock_models = os.path.join(SRC_ROOT, "models.ini.stock")
        stock_setup = os.path.join(SRC_ROOT, "setup.ini.stock")
        self.models_path = (
            stock_models if os.path.isfile(stock_models) else "/etc/versa-agi/models.ini"
        )
        self.setup_path = (
            stock_setup if os.path.isfile(stock_setup) else "/etc/versa-agi/setup.ini"
        )

    def test_phase_output_rows_have_honest_exact_metadata(self) -> None:
        cfg = _config(self.models_path)
        section = "catalog_library" if cfg.has_section("catalog_library") else "catalog"
        for key, expected in OUTPUT_MODELS.items():
            with self.subTest(key=key):
                self.assertTrue(cfg.has_option(section, key), f"{key} missing from {section}")
                row = parse_catalog_row(cfg.get(section, key))
                self.assertIsNotNone(row)
                self.assertEqual(row["provider"], expected["provider"])
                self.assertEqual(_csv(row["input_modalities"]), expected["input"])
                self.assertEqual(_csv(row["output_modalities"]), expected["output"])
                self.assertEqual(row["ctx_max"], expected["ctx_max"])
                self.assertTrue(row["enabled"])
                self.assertFalse(row["coa"])
                self.assertFalse(row["router_eligible"])

    def test_stock_library_has_local_qwen_image(self) -> None:
        cfg = _config(self.models_path)
        section = "catalog_library" if cfg.has_section("catalog_library") else "catalog"
        self.assertTrue(cfg.has_option(section, "qwen-image-2512"))
        row = parse_catalog_row(cfg.get(section, "qwen-image-2512"))
        self.assertEqual(row["class"], "local")
        self.assertEqual(row["provider"], "local_media")
        self.assertEqual(_csv(row["output_modalities"]), {"image"})
        self.assertFalse(row["router_eligible"])

    def test_stock_library_has_no_krea2(self) -> None:
        cfg = _config(self.models_path)
        section = "catalog_library" if cfg.has_section("catalog_library") else "catalog"
        self.assertFalse(cfg.has_option(section, "krea2-turbo"))

    def test_stock_library_has_local_flux(self) -> None:
        cfg = _config(self.models_path)
        section = "catalog_library" if cfg.has_section("catalog_library") else "catalog"
        self.assertTrue(cfg.has_option(section, "flux1-dev"))
        row = parse_catalog_row(cfg.get(section, "flux1-dev"))
        self.assertEqual(row["class"], "local")
        self.assertEqual(row["provider"], "local_media")
        self.assertEqual(_csv(row["output_modalities"]), {"image"})
        self.assertFalse(row["router_eligible"])

    def test_stock_activation_lists_include_each_output_model(self) -> None:
        cfg = _config(self.setup_path)
        self.assertIn(
            "gemini-3.1-flash-image",
            _csv(cfg.get("gemini", "cloud_models")),
        )
        self.assertIn(
            "gpt-audio-1.5",
            _csv(cfg.get("third_party", "openai_models")),
        )
        openrouter = _csv(cfg.get("third_party", "openrouter_models"))
        self.assertIn("google/gemini-3.1-flash-image", openrouter)
        self.assertIn("openai/gpt-audio", openrouter)
        self.assertTrue(set(PROMOTED_MODELS) <= openrouter)

    def test_promoted_active_site_models_have_exact_stock_metadata(self) -> None:
        cfg = _config(self.models_path)
        section = "catalog_library" if cfg.has_section("catalog_library") else "catalog"
        for key, expected in PROMOTED_MODELS.items():
            with self.subTest(key=key):
                self.assertTrue(cfg.has_option(section, key), f"{key} missing from {section}")
                row = parse_catalog_row(cfg.get(section, key))
                self.assertEqual(row["provider"], expected["provider"])
                self.assertEqual(_csv(row["input_modalities"]), expected["input"])
                self.assertEqual(_csv(row["output_modalities"]), expected["output"])
                self.assertEqual(row["ctx_max"], expected["ctx_max"])

    def test_current_input_replacements_ship_without_retired_aliases(self) -> None:
        models = _config(self.models_path)
        section = (
            "catalog_library"
            if models.has_section("catalog_library")
            else "catalog"
        )
        for key, expected in CURRENT_INPUT_REPLACEMENTS.items():
            with self.subTest(key=key):
                self.assertTrue(models.has_option(section, key))
                row = parse_catalog_row(models.get(section, key))
                self.assertEqual(row["provider"], expected["provider"])
                self.assertEqual(_csv(row["input_modalities"]), expected["input"])
                self.assertEqual(row["ctx_max"], expected["ctx_max"])
        for key in RETIRED_INPUT_MODELS:
            with self.subTest(retired=key):
                self.assertFalse(models.has_option(section, key))

        setup = _config(self.setup_path)
        activated = (
            _csv(setup.get("gemini", "cloud_models"))
            | _csv(setup.get("third_party", "xai_models"))
        )
        self.assertTrue(set(CURRENT_INPUT_REPLACEMENTS) <= activated)
        self.assertTrue(RETIRED_INPUT_MODELS.isdisjoint(activated))


class TestModelDriverCatalogMigration(unittest.TestCase):
    def test_library_rows_survive_activation_migration(self) -> None:
        stock_models = os.path.join(SRC_ROOT, "models.ini.stock")
        if not os.path.isfile(stock_models):
            self.skipTest("source stock template is not installed")
        try:
            import agictl.cli as agictl_cli
        except ModuleNotFoundError as exc:
            if exc.name == "click":
                self.skipTest("agictl migration dependencies are not installed")
            raise

        csv_values = {
            ("gemini", "coa_approved_models"): [],
            ("gemini", "cloud_models"): ["gemini-3.1-flash-image"],
            ("local_ai", "local_models"): [],
            ("third_party", "providers"): ["openai", "openrouter"],
            ("third_party", "openai_models"): ["gpt-audio-1.5"],
            ("third_party", "openrouter_models"): [
                "google/gemini-3.1-flash-image",
                "openai/gpt-audio",
                *PROMOTED_MODELS,
            ],
            ("third_party", "xai_models"): [],
            ("third_party", "anthropic_models"): [],
        }
        value_values = {
            ("third_party", "xai_enabled"): "false",
            ("third_party", "openai_enabled"): "true",
            ("third_party", "anthropic_enabled"): "false",
            ("third_party", "openrouter_enabled"): "true",
            ("local_ai", "enabled"): "false",
            ("gemini", "mode"): "cloud",
        }

        def read_csv(section: str, key: str):
            return list(csv_values.get((section, key), [])), "fixture"

        def read_value(section: str, key: str, default: str = ""):
            return value_values.get((section, key), default)

        with (
            patch.object(agictl_cli, "_read_ini_csv", side_effect=read_csv),
            patch.object(agictl_cli, "_read_ini_value", side_effect=read_value),
            patch.object(agictl_cli, "_gemini_credentials_present", return_value=True),
            patch.object(agictl_cli, "_gemini_provider_enabled", return_value=True),
            patch.object(
                agictl_cli,
                "fetch_openrouter_index_with_fallback",
                return_value={},
            ),
            patch.object(agictl_cli, "_resolve_gpu_backend", return_value="intel"),
        ):
            _providers, rows = agictl_cli._build_migration_rows(stock_models)

        migrated = dict(rows)
        expected_models = {**OUTPUT_MODELS, **PROMOTED_MODELS}
        self.assertEqual(
            set(migrated).intersection(expected_models),
            set(expected_models),
        )
        for key, expected in expected_models.items():
            with self.subTest(key=key):
                row = parse_catalog_row(migrated[key])
                self.assertEqual(row["provider"], expected["provider"])
                self.assertEqual(_csv(row["output_modalities"]), expected["output"])

    def test_merged_catalog_exposes_output_capabilities(self) -> None:
        lines = ["[catalog]"]
        for key, expected in OUTPUT_MODELS.items():
            lines.append(
                f"{key} = third_party|{expected['provider']}|true|false|0|"
                f"{expected['ctx_max']}|balanced|"
                f"{','.join(sorted(expected['input']))}|"
                f"{','.join(sorted(expected['output']))}|false|{key}"
            )
        lines.extend(("", "[catalog_custom]", ""))

        with tempfile.NamedTemporaryFile("w", encoding="utf-8") as handle:
            handle.write("\n".join(lines))
            handle.flush()
            catalog = load_catalog(handle.name)

        self.assertTrue(
            model_output_includes(catalog["google/gemini-3.1-flash-image"], "image")
        )
        self.assertTrue(model_output_includes(catalog["openai/gpt-audio"], "audio"))
        self.assertTrue(model_output_includes(catalog["gpt-audio-1.5"], "audio"))
        self.assertTrue(
            model_output_includes(catalog["gemini-3.1-flash-image"], "image")
        )

    def test_output_preference_requires_driver_not_coa_assignment_flag(self) -> None:
        catalog = load_catalog(
            os.path.join(SRC_ROOT, "models.ini")
            if os.path.isfile(os.path.join(SRC_ROOT, "models.ini"))
            else "/etc/versa-agi/models.ini"
        )
        ok, error = validate_preferred_output_key(
            "openai/gpt-audio",
            "audio",
            catalog,
        )
        self.assertTrue(ok, error)

        hollow = {
            **catalog,
            "site/hollow-image": {
                "provider": "openrouter",
                "enabled": True,
                "coa": True,
                "output_modalities": "text,image",
            },
        }
        ok, error = validate_preferred_output_key(
            "site/hollow-image",
            "image",
            hollow,
        )
        self.assertFalse(ok)
        self.assertIn("no exact executable ModelDriver", error)


if __name__ == "__main__":
    unittest.main()
