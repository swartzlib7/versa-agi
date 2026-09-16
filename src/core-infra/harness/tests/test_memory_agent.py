"""MEM-01: agictl memory --agent is COA/PU only."""

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


class TestMemoryAgentTarget(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tempdir.name, "tasks.db")
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                CREATE TABLE agent_memory_connection (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  agent_name TEXT NOT NULL,
                  contact_uid TEXT NOT NULL,
                  preferences TEXT,
                  personal_notes TEXT,
                  communication_style TEXT,
                  rapport_level TEXT,
                  emotional_notes TEXT,
                  last_interaction DATETIME,
                  updated_at DATETIME DEFAULT (datetime('now'))
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE agent_memory_system (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  agent_name TEXT NOT NULL,
                  key TEXT NOT NULL UNIQUE,
                  value TEXT,
                  updated_at DATETIME DEFAULT (datetime('now'))
                )
                """
            )
            conn.executemany(
                "INSERT INTO agent_memory_connection (agent_name, contact_uid, personal_notes) "
                "VALUES (?, ?, ?)",
                [
                    ("web-dev", "u-mine", "mine only"),
                    ("clerk", "u-clerk", "clerk only"),
                ],
            )
            conn.executemany(
                "INSERT INTO agent_memory_system (agent_name, key, value) VALUES (?, ?, ?)",
                [
                    ("web-dev", "mine_key", "mine"),
                    ("clerk", "clerk_key", "clerk"),
                ],
            )

    def tearDown(self):
        self.tempdir.cleanup()

    def _invoke(self, args, caller="web-dev", agent_user="agi-web-dev"):
        runner = CliRunner()
        env = {"AGICTL_AGENT_USER": agent_user if agent_user is not None else ""}
        with (
            patch.dict(os.environ, env, clear=False),
            patch.object(agictl_cli, "tasks_db", self.db_path),
            patch.object(agictl_cli, "_caller_agent_name", return_value=caller),
        ):
            return runner.invoke(agictl_cli.cli, args)

    def test_connection_list_defaults_to_caller(self):
        result = self._invoke(["memory", "connection", "list"])
        self.assertEqual(result.exit_code, 0, result.output)
        rows = json.loads(result.output)
        self.assertEqual([r["contact_uid"] for r in rows], ["u-mine"])

    def test_subagent_cannot_target_other(self):
        result = self._invoke(["memory", "connection", "list", "--agent", "clerk"])
        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("Permission denied", result.output)

    def test_coa_can_target_other_connection(self):
        result = self._invoke(
            ["memory", "connection", "list", "--agent", "clerk"],
            caller="coa",
            agent_user="coa",
        )
        self.assertEqual(result.exit_code, 0, result.output)
        rows = json.loads(result.output)
        self.assertEqual([r["contact_uid"] for r in rows], ["u-clerk"])

    def test_pu_can_target_other_connection(self):
        result = self._invoke(
            ["memory", "connection", "list", "--agent", "clerk"],
            caller="coa",
            agent_user="",
        )
        self.assertEqual(result.exit_code, 0, result.output)
        rows = json.loads(result.output)
        self.assertEqual([r["contact_uid"] for r in rows], ["u-clerk"])

    def test_system_list_without_agent_stays_global(self):
        result = self._invoke(["memory", "system", "list"])
        self.assertEqual(result.exit_code, 0, result.output)
        rows = json.loads(result.output)
        self.assertEqual(sorted(r["key"] for r in rows), ["clerk_key", "mine_key"])

    def test_coa_system_list_scopes_to_agent(self):
        result = self._invoke(
            ["memory", "system", "list", "--agent", "clerk"],
            caller="coa",
            agent_user="coa",
        )
        self.assertEqual(result.exit_code, 0, result.output)
        rows = json.loads(result.output)
        self.assertEqual([r["key"] for r in rows], ["clerk_key"])

    def test_coa_system_get_wrong_owner_is_missing(self):
        result = self._invoke(
            ["memory", "system", "get", "mine_key", "--agent", "clerk"],
            caller="coa",
            agent_user="coa",
        )
        self.assertEqual(result.exit_code, 0, result.output)
        payload = json.loads(result.output)
        self.assertEqual(payload.get("exists"), False)

    def test_self_agent_flag_allowed_for_subagent(self):
        result = self._invoke(["memory", "connection", "list", "--agent", "web-dev"])
        self.assertEqual(result.exit_code, 0, result.output)
        rows = json.loads(result.output)
        self.assertEqual([r["contact_uid"] for r in rows], ["u-mine"])


if __name__ == "__main__":
    unittest.main()
