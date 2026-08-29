"""COA-LAY-01: vendor-agnostic catalog layers."""

from __future__ import annotations

import configparser
import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

CORE_INFRA = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SRC_ROOT = os.path.dirname(CORE_INFRA)
sys.path.insert(0, CORE_INFRA)
sys.path.insert(0, os.path.join(CORE_INFRA, "agictl"))

from catalog_compat import (  # noqa: E402
    GENERIC_IMPORT_PARAMS,
    migrate_legacy_site_state,
    snapshot_vanishing_presets,
)
from model_catalog import (  # noqa: E402
    apply_catalog_overrides,
    catalog_row_to_value,
    compute_live_catalog_keys,
    parse_catalog_row,
)
from provider_registry import (  # noqa: E402
    load_merged_providers,
    parse_provider_library_row,
    set_site_enabled,
)
from shipped_models import keys_for_provider, load_offerings  # noqa: E402


def _cfg(path: str) -> configparser.ConfigParser:
    cfg = configparser.ConfigParser(delimiters=("=",), strict=False)
    cfg.optionxform = str
    cfg.read(path)
    return cfg


def _write(path: str, body: str) -> None:
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(body)


class ShippedRegistry(unittest.TestCase):
    def test_parser_order_and_dual_provider(self):
        path = os.path.join(SRC_ROOT, "models.ini.stock")
        offerings = load_offerings(path)
        self.assertGreaterEqual(len(offerings), 8)
        grok = next(item for item in offerings if item[0] == "grok-4.6")
        self.assertEqual(grok[1], "Grok 4.6")
        self.assertEqual(grok[2]["xai"], "grok-4.6")
        self.assertEqual(grok[2]["openrouter"], "x-ai/grok-4.6")

    def test_picker_is_shipped_intersect_live_coa(self):
        shipped = keys_for_provider("openrouter", os.path.join(SRC_ROOT, "models.ini.stock"))
        live = {
            "x-ai/grok-4.6": {"coa": True},
            "z-ai/glm-5.2": {"coa": False},
            "custom/other": {"coa": True},
        }
        picker = [key for key in shipped if key in live and live[key].get("coa")]
        self.assertEqual(picker, ["x-ai/grok-4.6"])


class ProviderParity(unittest.TestCase):
    def test_every_remote_library_provider_is_cloud(self):
        cfg = _cfg(os.path.join(SRC_ROOT, "models.ini.stock"))
        for slug, raw in cfg.items("provider_library"):
            row = parse_provider_library_row(slug, raw)
            if row["class"] == "local":
                continue
            with self.subTest(slug=slug):
                self.assertEqual(row["class"], "cloud")
                self.assertTrue(row["transport"])
                self.assertTrue(row["auth_adapter"])

    def test_merged_providers_have_no_google_special_class(self):
        path = os.path.join(SRC_ROOT, "models.ini.stock")
        with patch("provider_registry.credentials_present", return_value=False):
            merged = load_merged_providers(path)
        for slug in ("google", "xai", "openai", "anthropic", "openrouter"):
            self.assertEqual(merged[slug]["class"], "cloud")
        self.assertEqual(merged["google"]["transport"], "direct")
        self.assertEqual(merged["google"]["auth_adapter"], "google_gemini")
        self.assertEqual(merged["xai"]["transport"], "openai_compat")


class LiveKeys(unittest.TestCase):
    def test_shipped_plus_selected_plus_custom_minus_removed(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "models.ini")
            shutil.copyfile(os.path.join(SRC_ROOT, "models.ini.stock"), path)
            set_site_enabled(path, "openrouter", True)
            from catalog_compat import _ensure_section_lines
            _ensure_section_lines(path, "catalog_selected", {"openai/gpt-5.6-luna": "true"})
            _ensure_section_lines(
                path,
                "catalog_custom",
                {"site/custom": catalog_row_to_value({
                    "class": "cloud", "provider": "openrouter", "enabled": True,
                    "coa": False, "ctx_recommended": 0, "ctx_max": 8192,
                    "work_modality": "balanced", "input_modalities": "text",
                    "output_modalities": "text", "router_eligible": False,
                    "label": "Site custom",
                })},
            )
            _ensure_section_lines(path, "catalog_removed", {"z-ai/glm-5.2": "1"})
            with patch("provider_registry.credentials_present", return_value=True):
                keys = compute_live_catalog_keys(path, references=[])
        self.assertIn("x-ai/grok-4.6", keys)
        self.assertIn("openai/gpt-5.6-luna", keys)
        self.assertIn("site/custom", keys)
        self.assertNotIn("z-ai/glm-5.2", keys)


class OverlayUpgrade(unittest.TestCase):
    def test_untouched_preset_upgrades_all_fields(self):
        old = parse_catalog_row(
            "cloud|openrouter|true|true|0|1000|balanced|text|text|true|Old Label"
        )
        new = parse_catalog_row(
            "cloud|openrouter|true|true|0|2000|reasoning|text,image|text|true|New Label"
        )
        self.assertEqual(new["label"], "New Label")
        self.assertEqual(new["ctx_max"], 2000)
        self.assertEqual(new["work_modality"], "reasoning")
        self.assertNotEqual(old["label"], new["label"])

    def test_sparse_override_keeps_only_changed_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "models.ini")
            _write(
                path,
                "[catalog]\n"
                "demo = cloud|openrouter|true|false|0|1000|balanced|text|text|true|Stock\n"
                "[catalog_overrides]\n"
                "demo = {\"coa\": true}\n",
            )
            out = {
                "demo": parse_catalog_row(
                    "cloud|openrouter|true|false|0|2000|reasoning|text,image|text|true|Upgraded"
                )
            }
            apply_catalog_overrides(out, path)
            self.assertTrue(out["demo"]["coa"])
            self.assertEqual(out["demo"]["label"], "Upgraded")
            self.assertEqual(out["demo"]["ctx_max"], 2000)

    def test_referenced_removed_preset_is_retained(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "models.ini")
            shutil.copyfile(os.path.join(SRC_ROOT, "models.ini.stock"), path)
            with patch("provider_registry.credentials_present", return_value=False):
                keys = compute_live_catalog_keys(
                    path, references=["grok-4.6"]
                )
        self.assertIn("grok-4.6", keys)

    def test_unreferenced_untouched_removed_key_retires(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "models.ini")
            shutil.copyfile(os.path.join(SRC_ROOT, "models.ini.stock"), path)
            with patch("provider_registry.credentials_present", return_value=False):
                keys = compute_live_catalog_keys(path, references=[])
        self.assertNotIn("grok-4.6", keys)
        self.assertNotIn("x-ai/grok-4.6", keys)

    def test_vanishing_referenced_preset_is_snapshotted(self):
        with tempfile.TemporaryDirectory() as tmp:
            current = os.path.join(tmp, "current.ini")
            incoming = os.path.join(tmp, "incoming.ini")
            _write(
                current,
                "[catalog_library]\n"
                "keep-me = cloud|xai|true|true|0|100|balanced|text|text|true|Keep\n"
                "gone-ref = cloud|xai|true|true|0|100|balanced|text|text|true|Gone\n"
                "gone-free = cloud|xai|true|false|0|100|balanced|text|text|true|Free\n"
                "[model_params]\n"
                "model:gone-ref = {\"temperature\":0.2}\n"
                "[catalog_selected]\n"
                "[catalog_custom]\n"
                "[catalog_overrides]\n"
                "[model_params_custom]\n",
            )
            _write(
                incoming,
                "[catalog_library]\n"
                "keep-me = cloud|xai|true|true|0|100|balanced|text|text|true|Keep\n",
            )
            with patch(
                "model_catalog.collect_model_references",
                return_value=["gone-ref"],
            ):
                snapped = snapshot_vanishing_presets(
                    models_path=current, template_path=incoming
                )
            cfg = _cfg(current)
        self.assertEqual(snapped, ["gone-ref"])
        self.assertTrue(cfg.has_option("catalog_custom", "gone-ref"))
        self.assertFalse(cfg.has_option("catalog_custom", "gone-free"))
        self.assertTrue(cfg.has_option("model_params_custom", "model:gone-ref"))


class ImportBehavior(unittest.TestCase):
    def test_known_import_writes_selection_only(self):
        from agictl import cli as agictl_cli

        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "models.ini")
            shutil.copyfile(os.path.join(SRC_ROOT, "models.ini.stock"), path)
            with (
                patch.object(agictl_cli, "_models_ini_write_targets", return_value=[path]),
                patch.object(
                    agictl_cli,
                    "_library_row",
                    return_value={"coa": True, "label": "Grok 4.6"},
                ),
            ):
                agictl_cli._select_known_catalog_key("grok-4.6")
            cfg = _cfg(path)
        self.assertEqual(cfg.get("catalog_selected", "grok-4.6"), "true")
        self.assertFalse(
            cfg.has_option("catalog_custom", "grok-4.6")
            if cfg.has_section("catalog_custom")
            else False
        )
        self.assertFalse(
            cfg.has_option("model_params_custom", "model:grok-4.6")
            if cfg.has_section("model_params_custom")
            else False
        )

    def test_unknown_import_writes_full_custom_without_params(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "models.ini")
            _write(path, "[catalog_custom]\n[model_params_custom]\n")
            from catalog_compat import _ensure_section_lines
            row = {
                "class": "cloud",
                "provider": "openrouter",
                "enabled": True,
                "coa": False,
                "ctx_recommended": 0,
                "ctx_max": 8192,
                "work_modality": "balanced",
                "input_modalities": "text",
                "output_modalities": "text",
                "router_eligible": True,
                "label": "Unknown",
            }
            _ensure_section_lines(path, "catalog_custom", {"acme/new": catalog_row_to_value(row)})
            cfg = _cfg(path)
        self.assertTrue(cfg.has_option("catalog_custom", "acme/new"))
        self.assertFalse(cfg.has_option("model_params_custom", "model:acme/new"))
        self.assertEqual(parse_catalog_row(cfg.get("catalog_custom", "acme/new"))["class"], "cloud")


class LegacyMigrate(unittest.TestCase):
    def test_lists_flags_and_generic_params_convert_idempotently(self):
        with tempfile.TemporaryDirectory() as tmp:
            setup = os.path.join(tmp, "setup.ini")
            models = os.path.join(tmp, "models.ini")
            shutil.copyfile(os.path.join(SRC_ROOT, "models.ini.stock"), models)
            _write(
                setup,
                "[gemini]\nenabled=true\ncloud_models=gemini-3.7-flash,extra-known\n"
                "[third_party]\nopenrouter_enabled=true\n"
                "openrouter_models=x-ai/grok-4.6,openai/gpt-5.4-image-2\n"
                "xai_enabled=false\n",
            )
            from catalog_compat import _ensure_section_lines
            _ensure_section_lines(
                models,
                "catalog_library",
                {"extra-known": (
                    "cloud|google|true|false|0|1000|balanced|text|text|true|Extra known"
                )},
            )
            _ensure_section_lines(
                models,
                "catalog_custom",
                {"gemini-2.5-flash": (
                    "cloud|google|true|true|0|1000000|reasoning|text,image|text|true|"
                    "Gemini 2.5 Flash — Fast, cost-efficient"
                )},
            )
            _ensure_section_lines(
                models,
                "model_params_custom",
                {"model:gemini-3.7-flash": GENERIC_IMPORT_PARAMS[1]},
            )
            first = migrate_legacy_site_state(setup_path=setup, models_path=models)
            second = migrate_legacy_site_state(setup_path=setup, models_path=models)
            cfg = _cfg(models)
        self.assertIn("google", first["providers_enabled"])
        self.assertIn("openrouter", first["providers_enabled"])
        self.assertIn("extra-known", first["selected"])
        self.assertNotIn("openai/gpt-5.4-image-2", first["selected"])
        self.assertNotIn("x-ai/grok-4.6", first["selected"])
        self.assertIn("model:gemini-3.7-flash", first["params_cleared"])
        self.assertTrue(cfg.has_option("catalog_custom", "gemini-2.5-flash"))
        self.assertEqual(cfg.get("catalog_selected", "extra-known"), "true")
        self.assertEqual(second["providers_enabled"], first["providers_enabled"])

    def test_whole_row_custom_becomes_sparse_override(self):
        with tempfile.TemporaryDirectory() as tmp:
            setup = os.path.join(tmp, "setup.ini")
            models = os.path.join(tmp, "models.ini")
            shutil.copyfile(os.path.join(SRC_ROOT, "models.ini.stock"), models)
            _write(setup, "[gemini]\nenabled=false\n")
            from catalog_compat import _ensure_section_lines
            _ensure_section_lines(
                models,
                "catalog_custom",
                {"gemini-3.7-flash": (
                    "cloud|google|true|false|0|1048576|balanced|text,image,audio,video|"
                    "text|true|Site label"
                )},
            )
            stats = migrate_legacy_site_state(setup_path=setup, models_path=models)
            cfg = _cfg(models)
        self.assertIn("gemini-3.7-flash", stats["overrides"])
        patch = json.loads(cfg.get("catalog_overrides", "gemini-3.7-flash"))
        self.assertEqual(patch.get("label"), "Site label")
        self.assertEqual(patch.get("coa"), False)
        self.assertFalse(cfg.has_option("catalog_custom", "gemini-3.7-flash"))


class FullCustomSurvives(unittest.TestCase):
    def test_unknown_user_model_is_not_converted(self):
        with tempfile.TemporaryDirectory() as tmp:
            setup = os.path.join(tmp, "setup.ini")
            models = os.path.join(tmp, "models.ini")
            shutil.copyfile(os.path.join(SRC_ROOT, "models.ini.stock"), models)
            _write(setup, "[third_party]\n")
            from catalog_compat import _ensure_section_lines
            raw = catalog_row_to_value({
                "class": "cloud", "provider": "openrouter", "enabled": True,
                "coa": False, "ctx_recommended": 0, "ctx_max": 4096,
                "work_modality": "fast", "input_modalities": "text",
                "output_modalities": "text", "router_eligible": False,
                "label": "Mine",
            })
            _ensure_section_lines(models, "catalog_custom", {"acme/mine": raw})
            migrate_legacy_site_state(setup_path=setup, models_path=models)
            cfg = _cfg(models)
        self.assertEqual(cfg.get("catalog_custom", "acme/mine"), raw)


if __name__ == "__main__":
    unittest.main()
