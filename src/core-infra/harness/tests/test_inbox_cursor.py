"""Inbox pull resumes from the newest stored message for that sub-account.

Run from core-infra:
  python -m unittest harness.tests.test_inbox_cursor
"""

from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import unittest

from agictl.comms import fetch_inbox, inbox_since

CORE_INFRA = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
LIFELINE = os.path.join(CORE_INFRA, "lifeline.sh")


def _db(path: str, rows: list[tuple]) -> None:
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE messages ("
        "message_id TEXT, from_user_id TEXT, to_user_id TEXT, created_at TEXT, raw_payload TEXT)"
    )
    conn.executemany(
        "INSERT INTO messages (message_id, from_user_id, to_user_id, created_at, raw_payload) "
        "VALUES (?, ?, ?, ?, ?)",
        rows,
    )
    conn.commit()
    conn.close()


class TestInboxSince(unittest.TestCase):
    def test_empty_mailbox_starts_at_the_beginning(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "messages.db")
            _db(path, [])
            self.assertEqual(inbox_since(path, "sub-coa"), "1970-01-01T00:00:00Z")

    def test_cursor_is_one_second_before_the_newest_cloud_time(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "messages.db")
            _db(path, [
                ("in", "pu", "sub-coa", "2026-10-05 01:50:05",
                 json.dumps({"timestamp": "2026-10-05T01:49:57.339Z"})),
                ("out", "sub-coa", "pu", "2026-10-05 09:02:11", None),
                ("other", "pu", "sub-other", "2026-10-05 12:00:00",
                 json.dumps({"timestamp": "2026-10-05T12:00:00Z"})),
                ("attach", "pu", "sub-coa", "2026-10-04 08:00:00",
                 json.dumps([{"type": "url", "value": "https://example", "meta": {}}])),
            ])
            self.assertEqual(inbox_since(path, "sub-coa"), "2026-10-05T09:02:10Z")

    def test_full_sync_ignores_the_cursor(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "messages.db")
            _db(path, [("out", "sub-coa", "pu", "2026-10-05 09:02:11", None)])
            self.assertEqual(inbox_since(path, "sub-coa", full_sync=True), "1970-01-01T00:00:00Z")

    def test_fetch_asks_for_read_mail_since_the_cursor(self):
        import agictl.comms as comms
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "messages.db")
            _db(path, [("out", "sub-coa", "pu", "2026-10-05 09:02:11", None)])
            seen = {}

            def fake_api(endpoint, token, method="GET", body=None, timeout=None):
                seen["endpoint"] = endpoint
                return {"messages": []}

            original = comms.api_request
            comms.api_request = fake_api
            try:
                ok, inserted = fetch_inbox("coa", tmp, "sub-coa", "tok", path)
            finally:
                comms.api_request = original
            self.assertTrue(ok)
            self.assertEqual(inserted, 0)
            self.assertIn("unreadOnly=false", seen["endpoint"])
            self.assertIn("since=2026-10-05T09:02:10Z", seen["endpoint"])
            self.assertIn("markAsRead=true", seen["endpoint"])


class TestLastWakeWarning(unittest.TestCase):
    def test_overdue_block_names_the_last_wake(self):
        with open(LIFELINE, encoding="utf-8") as handle:
            text = handle.read()
        self.assertIn('_FREEZE_AT=$(( ${MAX_SPAWN_ATTEMPTS:-3} - 1 ))', text)
        self.assertIn("is on its last wake", text)
        self.assertIn("agictl task snooze", text)
        self.assertIn("${_LAST_CHANCE}", text)


if __name__ == "__main__":
    unittest.main()
