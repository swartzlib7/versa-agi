"""`agictl memory system set`: a key held by another agent is refused, not taken over.

The table makes a key unique across all agents. A second agent writing the same key
used to replace the value and become the entry's owner, so the first agent lost it.
"""

from __future__ import annotations

import importlib.util as _importlib_util
import unittest as _unittest

if _importlib_util.find_spec("click") is None:
    raise _unittest.SkipTest("click is not installed in this interpreter; "
                             "the agictl interpreter runs this module")

import json
import os
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

from click.testing import CliRunner

CORE_INFRA = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, CORE_INFRA)
sys.path.insert(0, os.path.join(CORE_INFRA, "agictl"))

import agictl.cli as agictl_cli  # noqa: E402


class TestSystemMemoryKeys(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = os.path.join(self.tmp.name, "tasks.db")
        with sqlite3.connect(self.db) as conn:
            # The shipped definition (scripts/init_tasks_db.sh).
            conn.execute(
                "CREATE TABLE agent_memory_system (id INTEGER PRIMARY KEY AUTOINCREMENT, "
                "agent_name TEXT NOT NULL, key TEXT NOT NULL UNIQUE, value TEXT NOT NULL, "
                "created_at DATETIME DEFAULT (datetime('now')), "
                "updated_at DATETIME DEFAULT (datetime('now')))")

    def set_as(self, agent, key, value):
        with patch.object(agictl_cli, "tasks_db", self.db), \
             patch.object(agictl_cli, "_resolve_memory_target", lambda requested: (agent, False)):
            result = CliRunner().invoke(agictl_cli.cli, ["memory", "system", "set", key, value])
        return json.loads(result.output.strip().splitlines()[-1])

    def rows(self):
        with sqlite3.connect(self.db) as conn:
            return conn.execute(
                "SELECT agent_name, key, value FROM agent_memory_system ORDER BY id").fetchall()

    def test_the_shipped_table_still_makes_a_key_unique_across_agents(self):
        script = os.path.join(CORE_INFRA, "scripts", "init_tasks_db.sh")
        with open(script, encoding="utf-8") as handle:
            text = handle.read()
        start = text.index("CREATE TABLE IF NOT EXISTS agent_memory_system")
        self.assertIn("key             TEXT NOT NULL UNIQUE", text[start:start + 400])

    def test_an_agent_sets_and_then_replaces_its_own_key(self):
        self.assertTrue(self.set_as("coa", "reporting_schedule", "Daily at 18:30")["success"])
        self.assertTrue(self.set_as("coa", "reporting_schedule", "Daily at 17:00")["success"])
        self.assertEqual(self.rows(), [("coa", "reporting_schedule", "Daily at 17:00")])

    def test_a_key_another_agent_holds_is_refused_and_left_alone(self):
        self.set_as("coa", "reporting_schedule", "Daily at 18:30")
        answer = self.set_as("clerk", "reporting_schedule", "Fridays")
        self.assertFalse(answer["success"])
        self.assertIn("already used by agent 'coa'", answer["error"])
        self.assertIn("reporting_schedule.clerk", answer["error"])
        self.assertEqual(self.rows(), [("coa", "reporting_schedule", "Daily at 18:30")])

    def test_the_suggested_key_works_and_keeps_its_place_among_the_standing_rules(self):
        self.set_as("coa", "constraint_attachment_limit", "Max 10")
        self.assertTrue(self.set_as("clerk", "constraint_attachment_limit.clerk", "Max 10")["success"])
        self.assertEqual(len(self.rows()), 2)
        self.assertTrue(agictl_cli._STANDING_RULE_KEY.match("constraint_attachment_limit.clerk"))


if __name__ == "__main__":
    unittest.main()
