"""Setup download picker + stock defaults (no GGUF / Ollama pulls).

Covers:
- stock default_model / sycl_active_model / model_routing.local = gemma4:e4b
- empty picker selection is e4b only
- --update reconcile preserves an existing site's local defaults

Run from core-infra::

    python -m unittest harness.tests.test_setup_local_model_pick
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest

CORE_INFRA = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)
SRC_ROOT = os.path.dirname(CORE_INFRA)
PICK_SH = os.path.join(SRC_ROOT, "setup_local_model_pick.sh")
STOCK_SETUP = os.path.join(SRC_ROOT, "setup.ini.stock")

sys.path.insert(0, CORE_INFRA)
sys.path.insert(0, os.path.join(CORE_INFRA, "agictl"))

from cli import _is_stock_setup_key, _parse_ini_pairs, _reconcile_setup_ini  # noqa: E402


def _ini_get(path: str, section: str, key: str) -> str:
    pairs = _parse_ini_pairs(path)
    return pairs.get((section, key), "")


def _parse_pick(raw: str) -> tuple[str, str]:
    script = f'''
    set -euo pipefail
    source "{PICK_SH}"
    _parse_stock_chat_pick {raw!r}
    '''
    out = subprocess.check_output(["bash", "-c", script], text=True).strip()
    default, csv = out.split("|", 1)
    return default, csv


class TestStockSetupDefaults(unittest.TestCase):
    def test_stock_default_model_is_e4b(self) -> None:
        self.assertTrue(os.path.isfile(STOCK_SETUP), STOCK_SETUP)
        self.assertEqual(_ini_get(STOCK_SETUP, "local_ai", "default_model"), "gemma4:e4b")
        self.assertEqual(_ini_get(STOCK_SETUP, "local_ai", "sycl_active_model"), "gemma4:e4b")
        self.assertEqual(_ini_get(STOCK_SETUP, "model_routing", "local"), "gemma4:e4b")

    def test_stock_known_keys_still_listed(self) -> None:
        csv = _ini_get(STOCK_SETUP, "local_ai", "local_models")
        for key in ("gemma4:e4b", "gemma4:26b", "gemma4:31b", "qwen3.6:35b", "qwen3.8:27b"):
            self.assertIn(key, csv.split(","), msg=key)

    def test_local_defaults_are_not_stock_owned(self) -> None:
        self.assertFalse(_is_stock_setup_key("local_ai", "default_model"))
        self.assertFalse(_is_stock_setup_key("local_ai", "local_models"))
        self.assertFalse(_is_stock_setup_key("local_ai", "sycl_active_model"))
        self.assertFalse(_is_stock_setup_key("model_routing", "local"))
        self.assertFalse(_is_stock_setup_key("system", "model"))
        self.assertFalse(_is_stock_setup_key("system", "mode"))
        self.assertFalse(_is_stock_setup_key("third_party", "google_api_key"))
        self.assertFalse(_is_stock_setup_key("gcp", "auth_method"))


class TestReconcilePreservesSiteDefaults(unittest.TestCase):
    def test_update_keeps_existing_qwen36(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            deployed = os.path.join(tmp, "setup.ini")
            with open(STOCK_SETUP, encoding="utf-8") as fh:
                body = fh.read()
            body = body.replace("default_model=gemma4:e4b", "default_model=qwen3.6:35b")
            body = body.replace("sycl_active_model=gemma4:e4b", "sycl_active_model=qwen3.6:35b")
            body = body.replace("local=gemma4:e4b", "local=qwen3.6:35b")
            body = body.replace(
                "local_models=gemma4:e4b,gemma4:26b,gemma4:31b,qwen3.6:35b,qwen3.8:27b",
                "local_models=qwen3.6:35b,qwen3.8:27b",
            )
            with open(deployed, "w", encoding="utf-8") as fh:
                fh.write(body)

            carried = _reconcile_setup_ini(STOCK_SETUP, deployed)
            self.assertGreater(carried, 0)
            self.assertEqual(_ini_get(deployed, "local_ai", "default_model"), "qwen3.6:35b")
            self.assertEqual(_ini_get(deployed, "local_ai", "sycl_active_model"), "qwen3.6:35b")
            self.assertEqual(_ini_get(deployed, "local_ai", "local_models"), "qwen3.6:35b,qwen3.8:27b")
            self.assertEqual(_ini_get(deployed, "model_routing", "local"), "qwen3.6:35b")


class TestStockChatPickParse(unittest.TestCase):
    def test_pick_lib_exists(self) -> None:
        self.assertTrue(os.path.isfile(PICK_SH), PICK_SH)

    def test_empty_selection_is_e4b_only(self) -> None:
        default, csv = _parse_pick("")
        self.assertEqual(default, "gemma4:e4b")
        self.assertEqual(csv, "gemma4:e4b")

    def test_whitespace_selection_is_e4b_only(self) -> None:
        default, csv = _parse_pick("   ")
        self.assertEqual(default, "gemma4:e4b")
        self.assertEqual(csv, "gemma4:e4b")

    def test_default_index_one(self) -> None:
        default, csv = _parse_pick("1")
        self.assertEqual(default, "gemma4:e4b")
        self.assertEqual(csv, "gemma4:e4b")

    def test_extra_numbers_keep_e4b_first(self) -> None:
        default, csv = _parse_pick("1,4")
        self.assertEqual(default, "gemma4:e4b")
        self.assertEqual(csv, "gemma4:e4b,qwen3.6:35b")

    def test_space_separated_and_dedupe(self) -> None:
        default, csv = _parse_pick("2 2 5")
        self.assertEqual(default, "gemma4:26b")
        self.assertEqual(csv, "gemma4:26b,qwen3.8:27b")

    def test_invalid_falls_back_to_e4b(self) -> None:
        default, csv = _parse_pick("0 99 foo")
        self.assertEqual(default, "gemma4:e4b")
        self.assertEqual(csv, "gemma4:e4b")

    def test_default_key_helper(self) -> None:
        out = subprocess.check_output(
            ["bash", "-c", f'set -euo pipefail; source "{PICK_SH}"; _stock_chat_pick_default_key'],
            text=True,
        ).strip()
        self.assertEqual(out, "gemma4:e4b")


if __name__ == "__main__":
    unittest.main()
