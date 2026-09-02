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
    assigned_local_catalog_rows_to_upsert,
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

    def test_phase_output_rows_are_driver_only_not_stock_library(self) -> None:
        from model_drivers.registry import MODEL_DRIVERS

        cfg = _config(self.models_path)
        section = "catalog_library" if cfg.has_section("catalog_library") else "catalog"
        bound = {binding.catalog_key for binding in MODEL_DRIVERS.values()}
        for key in OUTPUT_MODELS:
            with self.subTest(key=key):
                self.assertFalse(cfg.has_option(section, key), f"{key} should not ship in {section}")
                self.assertIn(key, bound)

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

    def test_setup_ini_local_models_includes_qwen38(self) -> None:
        working = os.path.join(SRC_ROOT, "setup.ini")
        path = working if os.path.isfile(working) else self.setup_path
        cfg = _config(path)
        self.assertIn("qwen3.8:27b", _csv(cfg.get("local_ai", "local_models")))

    def test_stock_library_has_local_qwen36_image_in(self) -> None:
        cfg = _config(self.models_path)
        section = "catalog_library" if cfg.has_section("catalog_library") else "catalog"
        self.assertTrue(cfg.has_option(section, "qwen3.6:35b"))
        row = parse_catalog_row(cfg.get(section, "qwen3.6:35b"))
        self.assertEqual(row["class"], "local")
        self.assertEqual(row["provider"], "llamacpp")
        self.assertEqual(_csv(row["input_modalities"]), {"text", "image"})
        self.assertEqual(_csv(row["output_modalities"]), {"text"})
        self.assertFalse(row["router_eligible"])

    def test_stock_library_has_local_qwen38(self) -> None:
        cfg = _config(self.models_path)
        section = "catalog_library" if cfg.has_section("catalog_library") else "catalog"
        self.assertTrue(cfg.has_option(section, "qwen3.8:27b"))
        row = parse_catalog_row(cfg.get(section, "qwen3.8:27b"))
        self.assertEqual(row["class"], "local")
        self.assertEqual(row["provider"], "llamacpp")
        self.assertEqual(_csv(row["input_modalities"]), {"text", "image"})
        self.assertEqual(_csv(row["output_modalities"]), {"text"})
        self.assertEqual(row["ctx_max"], 262144)
        self.assertFalse(row["router_eligible"])

    def test_stock_library_has_local_flux(self) -> None:
        cfg = _config(self.models_path)
        section = "catalog_library" if cfg.has_section("catalog_library") else "catalog"
        self.assertTrue(cfg.has_option(section, "flux1-dev"))
        row = parse_catalog_row(cfg.get(section, "flux1-dev"))
        self.assertEqual(row["class"], "local")
        self.assertEqual(row["provider"], "local_media")
        self.assertEqual(_csv(row["output_modalities"]), {"image"})
        self.assertFalse(row["router_eligible"])

    def test_stock_activation_lists_exclude_output_specialties(self) -> None:
        from shipped_models import all_keys

        models = _config(self.models_path)
        section = (
            "catalog_library" if models.has_section("catalog_library") else "catalog"
        )
        activated = set(all_keys(self.models_path))
        specialty = set(OUTPUT_MODELS) | {
            key for key in PROMOTED_MODELS if key != "openai/gpt-5.6-luna"
        }
        for key in specialty:
            with self.subTest(key=key):
                self.assertFalse(
                    models.has_option(section, key), f"{key} should not ship in {section}"
                )
                self.assertNotIn(key, activated)
        self.assertIn("openai/gpt-5.6-luna", activated)
        self.assertTrue(models.has_option(section, "openai/gpt-5.6-luna"))

    def test_promoted_active_site_models_have_exact_stock_metadata(self) -> None:
        cfg = _config(self.models_path)
        section = "catalog_library" if cfg.has_section("catalog_library") else "catalog"
        shipped_promoted = {
            key: expected
            for key, expected in PROMOTED_MODELS.items()
            if key == "openai/gpt-5.6-luna"
        }
        for key, expected in shipped_promoted.items():
            with self.subTest(key=key):
                self.assertTrue(cfg.has_option(section, key), f"{key} missing from {section}")
                row = parse_catalog_row(cfg.get(section, key))
                self.assertEqual(row["provider"], expected["provider"])
                self.assertEqual(_csv(row["input_modalities"]), expected["input"])
                self.assertEqual(_csv(row["output_modalities"]), expected["output"])
                self.assertEqual(row["ctx_max"], expected["ctx_max"])
        for key in PROMOTED_MODELS:
            if key == "openai/gpt-5.6-luna":
                continue
            with self.subTest(driver_only=key):
                self.assertFalse(cfg.has_option(section, key))

    def test_current_input_replacements_ship_without_retired_aliases(self) -> None:
        models = _config(self.models_path)
        section = (
            "catalog_library"
            if models.has_section("catalog_library")
            else "catalog"
        )
        for key in CURRENT_INPUT_REPLACEMENTS:
            with self.subTest(key=key):
                self.assertFalse(models.has_option(section, key))
        for key in RETIRED_INPUT_MODELS:
            with self.subTest(retired=key):
                self.assertFalse(models.has_option(section, key))

        from shipped_models import all_keys

        activated = set(all_keys(self.models_path))
        self.assertTrue(RETIRED_INPUT_MODELS.isdisjoint(activated))
        self.assertTrue(set(CURRENT_INPUT_REPLACEMENTS).isdisjoint(activated))


def _write_site_layers(path: str, *, enabled: str, selected: list[str]) -> None:
    from catalog_compat import _ensure_section_lines

    _ensure_section_lines(path, "providers_site", {"enabled": enabled})
    if selected:
        _ensure_section_lines(path, "catalog_selected", {key: "true" for key in selected})


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

        expected_models = {"openai/gpt-5.6-luna": PROMOTED_MODELS["openai/gpt-5.6-luna"]}
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "models.ini")
            with open(stock_models, encoding="utf-8") as handle:
                body = handle.read()
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(body)
            _write_site_layers(
                path,
                enabled="google,openai,openrouter",
                selected=list(OUTPUT_MODELS) + list(PROMOTED_MODELS),
            )
            with (
                patch.object(agictl_cli, "_read_ini_value", side_effect=lambda s, k, d="": {
                    ("local_ai", "enabled"): "false",
                    ("system", "mode"): "cloud",
                }.get((s, k), d)),
                patch.object(agictl_cli, "_read_ini_csv", return_value=([], "fixture")),
                patch("provider_registry.credentials_present", return_value=True),
                patch("model_catalog.collect_model_references", return_value=[]),
                patch.object(
                    agictl_cli,
                    "fetch_openrouter_index_with_fallback",
                    return_value={},
                ),
                patch.object(agictl_cli, "_resolve_gpu_backend", return_value="intel"),
            ):
                _providers, rows = agictl_cli._build_migration_rows(path)

        migrated = dict(rows)
        self.assertEqual(
            set(migrated).intersection(expected_models),
            set(expected_models),
        )
        self.assertTrue(set(OUTPUT_MODELS).isdisjoint(migrated))
        for key, expected in expected_models.items():
            with self.subTest(key=key):
                row = parse_catalog_row(migrated[key])
                self.assertEqual(row["provider"], expected["provider"])
                self.assertEqual(_csv(row["output_modalities"]), expected["output"])
                self.assertEqual(row["class"], "cloud")

    def test_migrate_sets_coa_from_library_not_csv(self) -> None:
        stock_models = os.path.join(SRC_ROOT, "models.ini.stock")
        if not os.path.isfile(stock_models):
            self.skipTest("source stock template is not installed")
        try:
            import agictl.cli as agictl_cli
        except ModuleNotFoundError as exc:
            if exc.name == "click":
                self.skipTest("agictl migration dependencies are not installed")
            raise

        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "models.ini")
            with open(stock_models, encoding="utf-8") as handle:
                body = handle.read()
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(body)
            _write_site_layers(
                path,
                enabled="google,openrouter",
                selected=[],
            )
            with (
                patch.object(agictl_cli, "_read_ini_value", side_effect=lambda s, k, d="": {
                    ("local_ai", "enabled"): "false",
                    ("system", "mode"): "cloud",
                }.get((s, k), d)),
                patch.object(agictl_cli, "_read_ini_csv", return_value=([], "fixture")),
                patch("provider_registry.credentials_present", return_value=True),
                patch("model_catalog.collect_model_references", return_value=[]),
                patch.object(
                    agictl_cli,
                    "fetch_openrouter_index_with_fallback",
                    return_value={},
                ),
                patch.object(agictl_cli, "_resolve_gpu_backend", return_value="intel"),
            ):
                _providers, rows = agictl_cli._build_migration_rows(path)

        migrated = dict(rows)
        flash = parse_catalog_row(migrated["gemini-3.7-flash"])
        grok = parse_catalog_row(migrated["x-ai/grok-4.6"])
        self.assertTrue(flash["coa"])
        self.assertTrue(grok["coa"])
        self.assertEqual(flash["class"], "cloud")
        self.assertEqual(grok["class"], "cloud")
        self.assertNotIn("gemini-3.1-flash-image", migrated)
        self.assertNotIn("google/gemini-3.1-flash-image", migrated)

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


QWEN38_ROW = (
    "local|llamacpp|true|false|32768|262144|local|text|text|false|"
    "Qwen 3.8 27B — hybrid thinking"
)


class TestAssignedLocalCatalogFallback(unittest.TestCase):
    """Refresh can update local_models before migrate copies the key into [catalog]."""

    def _write_ini(self, handle, *, catalog: str, library: str, labels: str = "") -> None:
        handle.write(
            "[catalog]\n"
            f"{catalog}"
            "[catalog_custom]\n\n"
            "[catalog_library]\n"
            f"{library}"
            "[local_models]\n"
            f"{labels}"
            "[context_windows]\n"
            "qwen3.8:27b = 32768,262144\n"
            "[sycl_models]\n"
            "qwen3.8:27b = unsloth/Qwen3.8-27B-GGUF,Qwen3.8-27B-UD-Q6_K_XL.gguf,23\n"
        )
        handle.flush()

    def test_assigned_library_key_fills_when_catalog_row_missing(self) -> None:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8") as handle:
            self._write_ini(
                handle,
                catalog="gemma4:e4b = local|llamacpp|true|false|32768|131072|local|text|text|false|Gemma\n",
                library=f"qwen3.8:27b = {QWEN38_ROW}\n",
            )
            catalog = load_catalog(handle.name, assigned_local=["qwen3.8:27b"])
        self.assertIn("qwen3.8:27b", catalog)
        self.assertEqual(catalog["qwen3.8:27b"]["provider"], "llamacpp")
        self.assertEqual(catalog["qwen3.8:27b"]["origin"], "library")
        self.assertEqual(catalog["qwen3.8:27b"]["ctx_max"], 262144)

    def test_unassigned_library_key_stays_out(self) -> None:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8") as handle:
            self._write_ini(
                handle,
                catalog="gemma4:e4b = local|llamacpp|true|false|32768|131072|local|text|text|false|Gemma\n",
                library=f"qwen3.8:27b = {QWEN38_ROW}\n",
            )
            catalog = load_catalog(handle.name, assigned_local=[])
        self.assertNotIn("qwen3.8:27b", catalog)
        self.assertIn("gemma4:e4b", catalog)

    def test_custom_import_synthesizes_row_without_library(self) -> None:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8") as handle:
            self._write_ini(
                handle,
                catalog="",
                library="",
                labels="qwen3.8:27b = Qwen 3.8 27B\n",
            )
            catalog = load_catalog(
                handle.name,
                assigned_local=["qwen3.8:27b"],
            )
        self.assertEqual(catalog["qwen3.8:27b"]["origin"], "local_assigned")
        self.assertEqual(catalog["qwen3.8:27b"]["class"], "local")
        self.assertEqual(catalog["qwen3.8:27b"]["ctx_recommended"], 32768)

    def test_upsert_list_skips_keys_already_in_catalog(self) -> None:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8") as handle:
            self._write_ini(
                handle,
                catalog=f"qwen3.8:27b = {QWEN38_ROW}\n",
                library=f"qwen3.8:27b = {QWEN38_ROW}\n",
            )
            rows = assigned_local_catalog_rows_to_upsert(
                handle.name, assigned_local=["qwen3.8:27b"]
            )
        self.assertEqual(rows, [])

    def test_upsert_list_returns_missing_assigned_key(self) -> None:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8") as handle:
            self._write_ini(
                handle,
                catalog="",
                library=f"qwen3.8:27b = {QWEN38_ROW}\n",
            )
            rows = assigned_local_catalog_rows_to_upsert(
                handle.name, assigned_local=["qwen3.8:27b"]
            )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][0], "qwen3.8:27b")
        self.assertIn("llamacpp", rows[0][1])


class TestCatalogRemoved(unittest.TestCase):
    def test_stock_seeds_catalog_removed_section(self) -> None:
        cfg = _config(os.path.join(SRC_ROOT, "models.ini.stock"))
        self.assertTrue(cfg.has_section("catalog_removed"))

    def test_catalog_removed_keys_reads_section(self) -> None:
        from agictl import cli as agictl_cli

        with tempfile.TemporaryDirectory() as tmp:
            ini = os.path.join(tmp, "models.ini")
            with open(ini, "w", encoding="utf-8") as handle:
                handle.write("[catalog_removed]\ngpt-5.4-2026-03-05 = 1\n")
            with patch.object(agictl_cli, "_MODELS_INI_PATHS", [ini]):
                self.assertEqual(
                    agictl_cli._catalog_removed_keys(),
                    {"gpt-5.4-2026-03-05"},
                )

    def test_drop_setup_csv_removes_key(self) -> None:
        from agictl import cli as agictl_cli

        with tempfile.TemporaryDirectory() as tmp:
            ini = os.path.join(tmp, "setup.ini")
            with open(ini, "w", encoding="utf-8") as handle:
                handle.write("[third_party]\nopenrouter_models=a,b,c\n")
            with patch.object(agictl_cli, "_setup_ini_live_paths", return_value=[ini]):
                agictl_cli._drop_setup_csv("third_party", "openrouter_models", "b")
            import configparser
            cfg = configparser.ConfigParser()
            cfg.read(ini)
            left = [m.strip() for m in cfg.get("third_party", "openrouter_models").split(",") if m.strip()]
        self.assertEqual(left, ["a", "c"])


if __name__ == "__main__":
    unittest.main()
