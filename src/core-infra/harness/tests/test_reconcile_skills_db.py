"""U4a reconcile also prunes missing non-shipped skill rows."""

from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

CORE_INFRA = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)
sys.path.insert(0, CORE_INFRA)
sys.path.insert(0, os.path.join(CORE_INFRA, "scripts"))

import reconcile_skills_db as reconcile_mod  # noqa: E402


class TestReconcileNonshippedPrune(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.workspace = self.root / "coa-ws"
        self.coa_skills = self.workspace / ".agent" / "skills"
        self.coa_skills.mkdir(parents=True)
        self.shipped = self.root / "skills"
        self.shipped.mkdir()
        (self.shipped / "communication.md").write_text("# Communication\n")
        (self.coa_skills / "keep.md").write_text("# Keep\n")
        self.db_path = str(self.root / "agents.db")
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("CREATE TABLE agents (name TEXT PRIMARY KEY, workspace TEXT)")
            conn.execute(
                "INSERT INTO agents (name, workspace) VALUES ('coa', ?)",
                (str(self.workspace),),
            )
            conn.execute(
                """
                CREATE TABLE skills (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  name TEXT NOT NULL UNIQUE,
                  type TEXT NOT NULL DEFAULT 'system',
                  origin TEXT NOT NULL DEFAULT 'shipped',
                  has_assets BOOLEAN DEFAULT 0,
                  description TEXT,
                  status TEXT NOT NULL DEFAULT 'synced',
                  scope TEXT NOT NULL DEFAULT 'all',
                  created_at DATETIME NOT NULL DEFAULT (datetime('now')),
                  updated_at DATETIME NOT NULL DEFAULT (datetime('now'))
                )
                """
            )
            conn.executemany(
                "INSERT INTO skills (name, type, origin, status, scope) VALUES (?,?,?,?,?)",
                [
                    ("communication", "system", "shipped", "ready", "all"),
                    ("keep", "agent_created", "coa", "synced", "all"),
                    ("product_ui_patterns", "agent_created", "coa", "synced", "all"),
                    ("business_admin_override", "override", "coa", "draft", "all"),
                    ("retired_shipped", "system", "shipped", "ready", "all"),
                ],
            )

    def tearDown(self):
        self.tempdir.cleanup()

    def test_prunes_missing_nonshipped_and_shipped_orphans(self):
        with (
            patch.object(reconcile_mod, "SKILLS_SRC", self.shipped),
            patch.object(reconcile_mod, "SCOPE_INI", self.root / "missing.ini"),
        ):
            inserted, updated, deleted = reconcile_mod.reconcile(self.db_path)
        self.assertGreaterEqual(deleted, 3)
        with sqlite3.connect(self.db_path) as conn:
            names = {r[0] for r in conn.execute("SELECT name FROM skills").fetchall()}
        self.assertIn("communication", names)
        self.assertIn("keep", names)
        self.assertNotIn("product_ui_patterns", names)
        self.assertNotIn("business_admin_override", names)
        self.assertNotIn("retired_shipped", names)


if __name__ == "__main__":
    unittest.main()
