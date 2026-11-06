"""Auto-freeze tells the assigned agent with an inbox-only Lifeline notice.

Run from core-infra:
  python -m unittest harness.tests.test_freeze_notice
"""

from __future__ import annotations

import os
import re
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
        self.assertIn("Lifeline auto-froze these tasks", text)
        self.assertIn("unfreeze it and snooze or reschedule it in the same cycle", text)

    def test_pu_notice_lists_tasks_by_name_and_explains_frozen(self):
        with open(LIFELINE, encoding="utf-8") as handle:
            text = handle.read()
        self.assertIn('• ${TASK_TITLE} (#${TASK_ID})', text)
        self.assertIn("Frozen means the task stays on the list", text)
        self.assertIn("may pick one back up when the work is still needed", text)
        self.assertLess(
            text.index('done <<< "${EXHAUSTED_TASKS}"'),
            text.index('message send "${SPONSOR_UID}" "${FREEZE_MSG}"'),
        )

    def test_frozen_tasks_skip_only_an_idle_wake(self):
        with open(LIFELINE, encoding="utf-8") as handle:
            text = handle.read()
        self.assertEqual(text.count("task count-frozen"), 1)
        self.assertIn("frozen task(s) — skipping spawn (Lifeline active, inbox sync continues)", text)
        self.assertNotIn("has pending task work but", text)

    def test_future_due_date_clears_spent_budget(self):
        with open(LIFELINE, encoding="utf-8") as handle:
            text = handle.read()
        match = re.search(r'BUDGET_RESET_SQL="([^"]+)"', text)
        self.assertIsNotNone(match)
        sql = match.group(1).replace("${AGENT_NAME}", "coa")
        self.assertLess(text.index("BUDGET_RESET_SQL="), text.index("# Step 1: Auto-freeze"))

        con = sqlite3.connect(":memory:")
        con.execute(
            "CREATE TABLE tasks (id INTEGER PRIMARY KEY, status TEXT, assigned_to TEXT, "
            "task_kind TEXT, due_date TEXT, spawn_attempts INTEGER DEFAULT 0, updated_at TEXT)"
        )
        future = "datetime('now', '+2 days')"
        past = "datetime('now', '-1 hour')"
        rows = [
            (1, "waiting", "coa", "standard", future),  # rescheduled after its last wake
            (2, "planned", "coa", None, future),
            (3, "waiting", "coa", "standard", past),     # still overdue: keeps counting
            (4, "frozen", "coa", "standard", future),
            (5, "planned", "coa", "utility", future),
            (6, "waiting", "researcher", "standard", future),
        ]
        for task_id, status, agent, kind, due in rows:
            con.execute(
                f"INSERT INTO tasks (id, status, assigned_to, task_kind, due_date, spawn_attempts) "
                f"VALUES (?, ?, ?, ?, {due}, 3)",
                (task_id, status, agent, kind),
            )
        con.executescript(sql)
        attempts = dict(con.execute("SELECT id, spawn_attempts FROM tasks").fetchall())
        con.close()
        self.assertEqual(attempts, {1: 0, 2: 0, 3: 3, 4: 3, 5: 3, 6: 3})

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
