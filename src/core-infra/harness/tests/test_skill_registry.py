"""SK-SYNC / registry: valid shares, prune, skill remove, --created-by."""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

from click.testing import CliRunner

CORE_INFRA = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)
sys.path.insert(0, CORE_INFRA)
sys.path.insert(0, os.path.join(CORE_INFRA, "agictl"))

import agictl.cli as agictl_cli  # noqa: E402
from project_workspace import reserved_workspace_repair_path  # noqa: E402


def _init_agents_db(path, workspace):
    with sqlite3.connect(path) as conn:
        conn.execute(
            """
            CREATE TABLE agents (
              name TEXT PRIMARY KEY,
              workspace TEXT
            )
            """
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
        conn.execute(
            "INSERT INTO agents (name, workspace) VALUES ('coa', ?), ('web-dev', ?)",
            (workspace, workspace),
        )


class TestValidSharesAndPrune(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.workspace = self.tempdir.name
        self.skills_dir = os.path.join(self.workspace, ".agent", "skills")
        os.makedirs(self.skills_dir, exist_ok=True)
        self.db_path = os.path.join(self.tempdir.name, "agents.db")
        _init_agents_db(self.db_path, self.workspace)

    def tearDown(self):
        self.tempdir.cleanup()

    def _conn(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def test_valid_share_requires_file_scope_and_status(self):
        open(os.path.join(self.skills_dir, "keep_me.md"), "w").write("# keep\n")
        conn = self._conn()
        conn.executemany(
            "INSERT INTO skills (name, type, origin, status, scope) VALUES (?,?,?,?,?)",
            [
                ("keep_me", "agent_created", "coa", "synced", "all"),
                ("stale_file", "agent_created", "coa", "synced", "all"),
                ("draft_only", "agent_created", "coa", "draft", "all"),
                ("coa_only_share", "agent_created", "coa", "synced", "coa_only"),
            ],
        )
        conn.commit()
        with patch.object(agictl_cli, "agents_db", self.db_path):
            names = agictl_cli._valid_shared_agent_created_names(conn)
        conn.close()
        self.assertEqual(names, ["keep_me"])

    def test_prune_drops_missing_nonshipped(self):
        open(os.path.join(self.skills_dir, "still_here.md"), "w").write("# ok\n")
        conn = self._conn()
        conn.executemany(
            "INSERT INTO skills (name, type, origin, status, scope) VALUES (?,?,?,?,?)",
            [
                ("still_here", "agent_created", "coa", "synced", "all"),
                ("product_ui_patterns", "agent_created", "coa", "synced", "all"),
                ("business_admin_override", "override", "coa", "draft", "all"),
                ("communication", "system", "shipped", "ready", "all"),
            ],
        )
        pruned = agictl_cli._prune_missing_nonshipped_skill_rows(conn, self.skills_dir)
        conn.commit()
        left = {r[0] for r in conn.execute("SELECT name FROM skills").fetchall()}
        conn.close()
        self.assertEqual(pruned, 2)
        self.assertEqual(left, {"still_here", "communication"})


class TestSkillCli(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.workspace = self.tempdir.name
        self.skills_dir = os.path.join(self.workspace, ".agent", "skills")
        os.makedirs(self.skills_dir, exist_ok=True)
        self.db_path = os.path.join(self.tempdir.name, "agents.db")
        _init_agents_db(self.db_path, self.workspace)

    def tearDown(self):
        self.tempdir.cleanup()

    def _invoke(self, args, agent_user=""):
        runner = CliRunner()
        env = {"AGICTL_AGENT_USER": agent_user}
        with (
            patch.dict(os.environ, env, clear=False),
            patch.object(agictl_cli, "agents_db", self.db_path),
        ):
            return runner.invoke(agictl_cli.cli, args)

    def test_subagent_cannot_skill_new(self):
        result = self._invoke(
            ["skill", "new", "nope", "--description", "x"],
            agent_user="web-dev",
        )
        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("Permission denied", result.output)

    def test_created_by_sets_origin(self):
        result = self._invoke(
            [
                "skill",
                "new",
                "from_web",
                "--description",
                "asked by web-dev",
                "--created-by",
                "web-dev",
            ],
            agent_user="coa",
        )
        self.assertEqual(result.exit_code, 0, result.output)
        payload = json.loads(result.output)
        self.assertTrue(payload["success"])
        self.assertEqual(payload["origin"], "web-dev")
        with sqlite3.connect(self.db_path) as conn:
            origin = conn.execute(
                "SELECT origin FROM skills WHERE name='from_web'"
            ).fetchone()[0]
        self.assertEqual(origin, "web-dev")
        self.assertTrue(os.path.isfile(os.path.join(self.skills_dir, "from_web.md")))

    def test_skill_remove_deletes_row_and_file(self):
        path = os.path.join(self.skills_dir, "product_ui_patterns.md")
        open(path, "w").write("# leftover\n")
        os.makedirs(os.path.join(self.skills_dir, "product_ui_patterns"), exist_ok=True)
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT INTO skills (name, type, origin, status, scope) "
                "VALUES ('product_ui_patterns', 'agent_created', 'coa', 'synced', 'all')"
            )
        result = self._invoke(["skill", "remove", "product_ui_patterns"], agent_user="coa")
        self.assertEqual(result.exit_code, 0, result.output)
        payload = json.loads(result.output)
        self.assertTrue(payload["success"])
        self.assertTrue(payload["deleted_file"])
        self.assertFalse(os.path.exists(path))
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT id FROM skills WHERE name='product_ui_patterns'"
            ).fetchone()
        self.assertIsNone(row)

    def test_skill_register_prunes_orphans(self):
        open(os.path.join(self.skills_dir, "keep.md"), "w").write("# Keep\n")
        with sqlite3.connect(self.db_path) as conn:
            conn.executemany(
                "INSERT INTO skills (name, type, origin, status, scope) VALUES (?,?,?,?,?)",
                [
                    ("keep", "agent_created", "coa", "draft", "all"),
                    ("gone", "agent_created", "coa", "synced", "all"),
                ],
            )
        result = self._invoke(["skill", "register"], agent_user="coa")
        self.assertEqual(result.exit_code, 0, result.output)
        payload = json.loads(result.output)
        self.assertGreaterEqual(payload.get("pruned", 0), 1)
        with sqlite3.connect(self.db_path) as conn:
            names = {r[0] for r in conn.execute("SELECT name FROM skills").fetchall()}
        self.assertIn("keep", names)
        self.assertNotIn("gone", names)


class TestReservedWorkspaceRepair(unittest.TestCase):
    def test_rewrites_leftover_slug(self):
        want = reserved_workspace_repair_path(
            "/home/coa/coa-env/workspace/versa-admin-system",
            "Versa-BusinessAdmin",
        )
        self.assertEqual(want, "/home/coa/coa-env/workspace/Versa-BusinessAdmin")

    def test_noop_when_already_shipped(self):
        self.assertIsNone(
            reserved_workspace_repair_path(
                "/home/coa/coa-env/workspace/Versa-BusinessAdmin",
                "Versa-BusinessAdmin",
            )
        )

    def test_other_reserved_names_untouched(self):
        self.assertIsNone(
            reserved_workspace_repair_path(
                "/home/coa/coa-env/workspace/old-tools",
                "AGi-Tools",
            )
        )


if __name__ == "__main__":
    unittest.main()
