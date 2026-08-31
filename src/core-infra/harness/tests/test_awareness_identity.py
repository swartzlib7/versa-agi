"""GOL-03 / GOL-06: awareness table default + execute-bash identity forward."""

from __future__ import annotations

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

WRAPPER = os.path.join(CORE_INFRA, "bin", "agictl-wrapper")


class TestExecIdentityForward(unittest.TestCase):
    def test_forward_pairs_include_identity(self):
        env = {
            "AGICTL_CONFIG": "/etc/versa-agi/web-dev_config.json",
            "VERSA_AGENT_NAME": "web-dev",
            "AGICTL_AGENT_USER": "agi-web-dev",
            "AGICTL_TASKS_DB": "/tmp/tasks.db",
            "AGICTL_AGENT_DIR": "/home/agi-web-dev/.agent",
        }
        with patch.dict(os.environ, env, clear=False):
            pairs = agictl_cli._exec_forward_env_pairs()
        joined = " ".join(pairs)
        self.assertIn("AGICTL_CONFIG=/etc/versa-agi/web-dev_config.json", joined)
        self.assertIn("VERSA_AGENT_NAME=web-dev", joined)
        self.assertIn("AGICTL_AGENT_USER=agi-web-dev", joined)
        self.assertIn("AGICTL_TASKS_DB=/tmp/tasks.db", joined)

    def test_get_exec_cmd_sudo_forwards_identity(self):
        env = {
            "AGICTL_AGENT_USER": "agi-web-dev",
            "USER": "watchdog",
            "VERSA_AGENT_NAME": "web-dev",
            "AGICTL_CONFIG": "/etc/versa-agi/web-dev_config.json",
        }
        with patch.dict(os.environ, env, clear=False):
            cmd = agictl_cli._get_exec_cmd("bash", "/tmp/script.sh")
        self.assertEqual(cmd[0:3], ["sudo", "-u", "agi-web-dev"])
        self.assertEqual(cmd[3], "env")
        self.assertIn("VERSA_AGENT_NAME=web-dev", cmd)
        self.assertIn("AGICTL_CONFIG=/etc/versa-agi/web-dev_config.json", cmd)
        self.assertEqual(cmd[-2:], ["bash", "/tmp/script.sh"])


class TestWrapperRecognizesAgiUsers(unittest.TestCase):
    def test_wrapper_maps_agi_prefix(self):
        with open(WRAPPER, encoding="utf-8") as fh:
            text = fh.read()
        self.assertIn("agi-*)", text)
        self.assertIn('${CALLER#agi-}', text)
        self.assertIn("VERSA_AGENT_NAME", text)
        self.assertIn("AGICTL_TASKS_DB", text)


class TestAwarenessTableDefault(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tempdir.name, "tasks.db")
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                CREATE TABLE agent_awareness (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  agent_name TEXT NOT NULL,
                  type TEXT NOT NULL,
                  subject_type TEXT NOT NULL,
                  subject_id TEXT,
                  content TEXT NOT NULL,
                  action_conclusion_id INTEGER,
                  context TEXT,
                  status TEXT DEFAULT 'active',
                  created_at DATETIME DEFAULT (datetime('now')),
                  updated_at DATETIME DEFAULT (datetime('now'))
                )
                """
            )
            conn.executemany(
                "INSERT INTO agent_awareness (agent_name, type, subject_type, content, status) "
                "VALUES (?, 'conclusion', 'self', ?, 'active')",
                [("web-dev", "mine only"), ("coa", "coa only")],
            )

    def tearDown(self):
        self.tempdir.cleanup()

    def _invoke(self, args, caller="web-dev"):
        runner = CliRunner()
        with (
            patch.object(agictl_cli, "tasks_db", self.db_path),
            patch.object(agictl_cli, "_caller_agent_name", return_value=caller),
        ):
            return runner.invoke(agictl_cli.cli, ["awareness", "table", *args])

    def test_default_is_caller_board(self):
        result = self._invoke(["--status", "active"])
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("mine only", result.output)
        self.assertNotIn("coa only", result.output)

    def test_all_shows_fleet(self):
        result = self._invoke(["--status", "active", "--all"])
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("mine only", result.output)
        self.assertIn("coa only", result.output)
        self.assertIn("Agent", result.output)


if __name__ == "__main__":
    unittest.main()
