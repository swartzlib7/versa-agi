"""Watchdog-side mail and registry reads stay on the named agent.

Run from core-infra:
  python -m unittest harness.tests.test_agent_reads
"""

from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest

from agent_reads import ids_for_spawned_agent, received_unread, registry_for_agent


def _messages(path: str) -> None:
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE messages ("
        "message_id TEXT, from_user_id TEXT, to_user_id TEXT, display_name TEXT, "
        "text TEXT, original_text TEXT, created_at TEXT, raw_payload TEXT, "
        "status TEXT, direction TEXT, has_attachments INTEGER, attachment_path TEXT)"
    )
    conn.executemany(
        "INSERT INTO messages (message_id, from_user_id, to_user_id, display_name, "
        "text, original_text, created_at, raw_payload, status, direction, "
        "has_attachments, attachment_path) VALUES (?, ?, ?, ?, ?, NULL, ?, NULL, ?, ?, 0, NULL)",
        [
            ("s-new", "pu", "sylvie-sub", "Stephen", "for sylvie", "2026-09-29 01:00:00", "unprocessed", "received"),
            ("s-name", "clerk", "sylvie", "Clerk", "internal", "2026-09-29 01:01:00", "unprocessed", "received"),
            ("s-done", "pu", "sylvie-sub", "Stephen", "old", "2026-09-29 00:00:00", "processed", "received"),
            ("s-out", "sylvie-sub", "pu", "Sylvie", "outbound", "2026-09-29 01:02:00", "unprocessed", "sent"),
            ("c-new", "pu", "coa", "Stephen", "for coa", "2026-09-29 01:03:00", "unprocessed", "received"),
        ],
    )
    conn.commit()
    conn.close()


def _tasks(path: str) -> None:
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE projects (
          id INTEGER PRIMARY KEY, name TEXT, description TEXT, status TEXT, workspace_path TEXT
        );
        CREATE TABLE project_members (
          project_id INTEGER, member_type TEXT, member_id TEXT
        );
        CREATE TABLE tasks (
          id INTEGER PRIMARY KEY, title TEXT, description TEXT, project_id INTEGER,
          status TEXT, assigned_to TEXT
        );
        INSERT INTO projects VALUES (1, 'Sylvie work', 'hers', 'active', '/s');
        INSERT INTO projects VALUES (2, 'Other', 'not hers', 'active', '/o');
        INSERT INTO projects VALUES (3, 'Old', 'archived', 'archived', '/a');
        INSERT INTO project_members VALUES (1, 'agent', 'sylvie');
        INSERT INTO tasks VALUES (10, 'Open', 'do it', 1, 'planned', 'sylvie');
        INSERT INTO tasks VALUES (11, 'Coa task', 'fleet', 2, 'planned', 'coa');
        INSERT INTO tasks VALUES (12, 'Done', 'finished', 1, 'done', 'sylvie');
        """
    )
    conn.commit()
    conn.close()


class TestAgentReads(unittest.TestCase):
    def test_sub_account_follows_the_config_agent_only(self) -> None:
        self.assertEqual(
            ids_for_spawned_agent("sylvie", "sylvie", "sylvie-sub"),
            ["sylvie-sub", "sylvie"],
        )
        self.assertEqual(
            ids_for_spawned_agent("sylvie", "coa", "coa-sub"),
            ["sylvie"],
        )

    def test_received_unread_is_only_this_agent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "messages.db")
            _messages(db)
            rows = received_unread(db, ["sylvie-sub", "sylvie"])
            self.assertEqual([r["message_id"] for r in rows], ["s-new", "s-name"])

    def test_registry_is_only_this_agent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "tasks.db")
            _tasks(db)
            data = registry_for_agent(db, "sylvie")
            self.assertEqual([p["id"] for p in data["projects"]], [1])
            self.assertEqual([t["id"] for t in data["tasks"]], [10])
            fleet = registry_for_agent(db, "coa")
            self.assertEqual(sorted(p["id"] for p in fleet["projects"]), [1, 2])
            self.assertEqual(sorted(t["id"] for t in fleet["tasks"]), [10, 11])


if __name__ == "__main__":
    unittest.main()
