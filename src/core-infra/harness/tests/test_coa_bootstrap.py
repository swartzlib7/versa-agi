"""Unit tests for COA first-login bootstrap helpers (WU-02 / WU-03 / WU-08).

Run:  python -m unittest harness.tests.test_coa_bootstrap   (from core-infra)
"""

from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from agitop import coa_bootstrap as cb  # noqa: E402


class RecommendedMap(unittest.TestCase):
    def test_xai_is_grok_45_only(self):
        self.assertEqual(cb.recommended_keys("xai"), ["grok-4.5"])

    def test_anthropic_three(self):
        self.assertEqual(
            cb.recommended_keys("anthropic"),
            ["claude-fable-5", "claude-opus-4-8", "claude-sonnet-4-6"],
        )

    def test_openrouter_target_three(self):
        self.assertEqual(
            cb.recommended_keys("openrouter"),
            [
                "x-ai/grok-4.5",
                "google/gemini-3-flash-preview",
                "z-ai/glm-5.2",
            ],
        )

    def test_google_three(self):
        self.assertEqual(
            cb.recommended_keys("google"),
            [
                "gemini-3-flash-preview",
                "gemini-3.1-pro-preview",
                "gemini-2.5-flash",
            ],
        )

    def test_openai_three(self):
        self.assertEqual(
            cb.recommended_keys("openai"),
            [
                "gpt-5.5-2026-04-23",
                "gpt-5.4-2026-03-05",
                "gpt-5.4-mini-2026-03-17",
            ],
        )

    def test_options_label_first(self):
        opts = cb.recommended_options("xai")
        self.assertEqual(opts, [("xAI: Grok 4.5 (grok-4.5)", "grok-4.5")])


class BootstrapDetection(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.state = self.root / "coa_bootstrap.json"
        self.paths = self.root / "paths.env"
        self.setup = self.root / "setup.ini"
        self.coa_env = self.root / "coa.env"
        self.vault = self.root / "vault" / "gcp-credentials.json"
        self.db = self.root / "agents.db"
        self._init_db("")
        self.setup.write_text(
            "[gemini]\nenabled=false\nmodel=\n\n[third_party]\n",
            encoding="utf-8",
        )
        self.paths.write_text('VERSA_DEFAULT_MODEL=""\n', encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def _init_db(self, model: str):
        con = sqlite3.connect(self.db)
        con.execute(
            "CREATE TABLE IF NOT EXISTS agents (name TEXT PRIMARY KEY, model TEXT)"
        )
        con.execute("DELETE FROM agents")
        con.execute("INSERT INTO agents (name, model) VALUES ('coa', ?)", (model,))
        con.commit()
        con.close()

    def _kwargs(self):
        return dict(
            state_path=self.state,
            agents_db=self.db,
            paths_env=self.paths,
            setup_ini=self.setup,
            coa_env=self.coa_env,
            vault=self.vault,
        )

    def test_skip_all_needs_bootstrap(self):
        self.assertTrue(cb.needs_coa_bootstrap(**self._kwargs()))
        self.assertTrue(cb.should_auto_prompt_bootstrap(**self._kwargs()))

    def test_coa_explicit_on_keyed_provider_healthy(self):
        # Simulate openrouter keyed via monkeypatch
        self._init_db("z-ai/glm-5.2")
        orig = cb.usable_providers
        orig_cat = cb._model_in_live_catalog
        cb.usable_providers = lambda **kw: ["openrouter"]  # type: ignore
        cb._model_in_live_catalog = lambda m: True  # type: ignore
        try:
            self.assertFalse(cb.needs_coa_bootstrap(**self._kwargs()))
            self.assertFalse(cb.should_auto_prompt_bootstrap(**self._kwargs()))
        finally:
            cb.usable_providers = orig
            cb._model_in_live_catalog = orig_cat

    def test_coa_model_missing_from_catalog_needs(self):
        self._init_db("x-ai/grok-4.5")
        orig = cb.usable_providers
        orig_cat = cb._model_in_live_catalog
        cb.usable_providers = lambda **kw: ["openrouter"]  # type: ignore
        cb._model_in_live_catalog = lambda m: False  # type: ignore
        try:
            self.assertTrue(cb.needs_coa_bootstrap(**self._kwargs()))
        finally:
            cb.usable_providers = orig
            cb._model_in_live_catalog = orig_cat

    def test_coa_on_unkeyed_provider_needs(self):
        self._init_db("gemini-3-flash-preview")
        orig = cb.usable_providers
        cb.usable_providers = lambda **kw: ["openrouter"]  # type: ignore
        try:
            self.assertTrue(cb.needs_coa_bootstrap(**self._kwargs()))
        finally:
            cb.usable_providers = orig

    def test_empty_coa_blank_default_needs(self):
        self._init_db("")
        orig = cb.usable_providers
        cb.usable_providers = lambda **kw: ["openrouter"]  # type: ignore
        try:
            self.assertTrue(cb.needs_coa_bootstrap(**self._kwargs()))
        finally:
            cb.usable_providers = orig

    def test_remind_later_suppresses_auto_prompt(self):
        cb.mark_bootstrap_remind_later(self.state)
        self.assertTrue(cb.needs_coa_bootstrap(**self._kwargs()))
        self.assertTrue(cb.is_remind_later(self.state))
        self.assertFalse(cb.should_auto_prompt_bootstrap(**self._kwargs()))
        self.assertTrue(cb.should_show_remind_banner(**self._kwargs()))

    def test_done_with_healthy_no_need(self):
        self._init_db("z-ai/glm-5.2")
        cb.mark_bootstrap_done(self.state)
        orig = cb.usable_providers
        orig_cat = cb._model_in_live_catalog
        cb.usable_providers = lambda **kw: ["openrouter"]  # type: ignore
        cb._model_in_live_catalog = lambda m: True  # type: ignore
        try:
            self.assertFalse(cb.needs_coa_bootstrap(**self._kwargs()))
            self.assertFalse(cb.should_show_remind_banner(**self._kwargs()))
        finally:
            cb.usable_providers = orig
            cb._model_in_live_catalog = orig_cat

    def test_gemini_usable_respects_enabled_false(self):
        self.coa_env.write_text("GEMINI_API_KEY=test-key\n", encoding="utf-8")
        self.setup.write_text("[gemini]\nenabled=false\n", encoding="utf-8")
        self.assertFalse(
            cb.gemini_usable(
                coa_env=self.coa_env, vault=self.vault, setup_ini=self.setup
            )
        )

    def test_gemini_usable_enabled_true(self):
        self.coa_env.write_text("GEMINI_API_KEY=test-key\n", encoding="utf-8")
        self.setup.write_text("[gemini]\nenabled=true\n", encoding="utf-8")
        self.assertTrue(
            cb.gemini_usable(
                coa_env=self.coa_env, vault=self.vault, setup_ini=self.setup
            )
        )

    def test_sync_system_default_model(self):
        updated = cb.sync_system_default_model(
            "z-ai/glm-5.2", paths_env=self.paths, setup_ini=self.setup
        )
        self.assertIn(str(self.paths), updated)
        self.assertIn('VERSA_DEFAULT_MODEL="z-ai/glm-5.2"', self.paths.read_text())
        self.assertIn("model=z-ai/glm-5.2", self.setup.read_text())


class RoleModelSection(unittest.TestCase):
    def test_reads_model_section(self):
        with tempfile.NamedTemporaryFile("w", suffix=".ini", delete=False) as f:
            f.write("[model]\nmodel=claude-opus-4-8\n")
            path = f.name
        try:
            self.assertEqual(cb.read_role_model(path), "claude-opus-4-8")
        finally:
            os.unlink(path)

    def test_ignores_legacy_gemini_section(self):
        with tempfile.NamedTemporaryFile("w", suffix=".ini", delete=False) as f:
            f.write("[gemini]\nmodel=legacy-model\n")
            path = f.name
        try:
            self.assertEqual(cb.read_role_model(path), "")
        finally:
            os.unlink(path)

    def test_blank_model_inherits(self):
        with tempfile.NamedTemporaryFile("w", suffix=".ini", delete=False) as f:
            f.write("[model]\nmodel=\n")
            path = f.name
        try:
            self.assertEqual(cb.read_role_model(path), "")
        finally:
            os.unlink(path)


class HealCoaAssignment(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "agents.db"
        con = sqlite3.connect(self.db)
        con.execute(
            "CREATE TABLE agents ("
            "name TEXT PRIMARY KEY, model TEXT, num_ctx INTEGER, "
            "updated_at TEXT)"
        )
        con.execute(
            "INSERT INTO agents (name, model, num_ctx) VALUES ('coa', 'x-ai/grok-4.5', 4096)"
        )
        con.commit()
        con.close()

    def tearDown(self):
        self.tmp.cleanup()

    def test_fresh_clears_missing_catalog_key(self):
        orig = cb._model_in_live_catalog
        cb._model_in_live_catalog = lambda m: False  # type: ignore
        try:
            result = cb.heal_coa_assignment(agents_db=self.db, fresh_install=True)
        finally:
            cb._model_in_live_catalog = orig
        self.assertTrue(result["changed"])
        self.assertIn("cleared_missing_catalog_model", result["actions"])
        con = sqlite3.connect(self.db)
        row = con.execute("SELECT model, num_ctx FROM agents WHERE name='coa'").fetchone()
        con.close()
        self.assertEqual((row[0], row[1]), ("", 0))

    def test_update_leaves_missing_catalog_key(self):
        orig = cb._model_in_live_catalog
        cb._model_in_live_catalog = lambda m: False  # type: ignore
        try:
            result = cb.heal_coa_assignment(agents_db=self.db, fresh_install=False)
        finally:
            cb._model_in_live_catalog = orig
        self.assertFalse(result["changed"])
        self.assertIn("missing_catalog_model", result["actions"])

    def test_resets_leaked_4k_on_cloud(self):
        orig = cb._model_in_live_catalog
        cb._model_in_live_catalog = lambda m: True  # type: ignore
        try:
            with patch(
                "harness.model_context.get_model_context", return_value=(0, 131072)
            ):
                result = cb.heal_coa_assignment(agents_db=self.db, fresh_install=False)
        finally:
            cb._model_in_live_catalog = orig
        self.assertTrue(result["changed"])
        self.assertIn("reset_cloud_num_ctx_auto", result["actions"])
        con = sqlite3.connect(self.db)
        num_ctx = con.execute("SELECT num_ctx FROM agents WHERE name='coa'").fetchone()[0]
        con.close()
        self.assertEqual(num_ctx, 0)


if __name__ == "__main__":
    unittest.main()
