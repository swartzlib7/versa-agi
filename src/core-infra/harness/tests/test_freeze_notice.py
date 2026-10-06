"""Auto-freeze tells the assigned agent with an inbox-only Lifeline notice.

Run from core-infra:
  python -m unittest harness.tests.test_freeze_notice
"""

from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest

CORE_INFRA = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
LIFELINE = os.path.join(CORE_INFRA, "lifeline.sh")


class TestFreezeNotice(unittest.TestCase):
    def test_lifeline_notifies_the_assignee(self):
        with open(LIFELINE, encoding="utf-8") as handle:
            text = handle.read()
        self.assertIn('message internal "${AGENT_NAME}"', text)
        self.assertIn("--from-lifeline", text)
        self.assertIn("Lifeline auto-froze task", text)

    def test_from_lifeline_is_inbox_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            messages = os.path.join(tmp, "messages.db")
            agents = os.path.join(tmp, "agents.db")
            config = os.path.join(tmp, "coa_config.json")
            with open(config, "w", encoding="utf-8") as handle:
                handle.write("{}")
            m = sqlite3.connect(messages)
            m.execute(
                "CREATE TABLE messages ("
                "direction TEXT, from_user_id TEXT, to_user_id TEXT, display_name TEXT, "
                "message_id TEXT, text TEXT, mode TEXT, status TEXT, channel TEXT)"
            )
            m.commit()
            m.close()
            a = sqlite3.connect(agents)
            a.execute("CREATE TABLE agents (name TEXT)")
            a.execute("INSERT INTO agents (name) VALUES ('coa')")
            a.commit()
            a.close()

            env = os.environ.copy()
            env.update({
                "PYTHONPATH": CORE_INFRA,
                "AGICTL_MESSAGES_DB": messages,
                "AGICTL_AGENTS_DB": agents,
                "AGICTL_CONFIG": config,
            })
            result = subprocess.run(
                [sys.executable, os.path.join(CORE_INFRA, "agictl", "cli.py"),
                 "message", "internal", "coa", "Lifeline auto-froze task #321.", "--from-lifeline"],
                env=env, capture_output=True, text=True,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            con = sqlite3.connect(messages)
            rows = con.execute(
                "SELECT direction, from_user_id, to_user_id, display_name, status FROM messages"
            ).fetchall()
            con.close()
            self.assertEqual(rows, [("received", "lifeline", "coa", "Lifeline", "unprocessed")])


if __name__ == "__main__":
    unittest.main()
