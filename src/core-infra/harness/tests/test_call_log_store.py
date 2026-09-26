"""Call log store (messages.db ``calls``) — state_live_voice_call.md §1.8."""

import os
import re
import shutil
import sqlite3
import subprocess
import tempfile
import unittest

import call_log_store as store


class TestCallLogStore(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.tmp.name, "messages.db")

    def tearDown(self):
        self.tmp.cleanup()

    def _insert(self, call_id="c1", status="calling", **kw):
        store.insert_attempt(self.db, call_id=call_id, agent_name="coa", status=status,
                             reason="Blocked task needs a decision", **kw)

    def test_offline_attempt_is_closed_on_insert(self):
        self._insert("c-off", status="offline", close_reason="no_device")
        row = store.get_call(self.db, "c-off")
        self.assertEqual(row["status"], "offline")
        self.assertIsNotNone(row["ended_at"])
        self.assertEqual(row["transcript"], [])

    def test_open_call_and_update_lifecycle(self):
        self._insert("c2", call_model="gemini-3.7-flash", voice_model="gpt-live-1", cycle_id="77")
        self.assertTrue(store.has_open_call(self.db, "coa"))
        store.update_call(self.db, "c2", status="live", openai_session_id="live_1")
        segments = [{"speaker": "pu", "text": "Hi"}, {"speaker": "assistant", "text": "Hello"}]
        store.update_call(self.db, "c2", status="ended", close_reason="remote_hangup", voice_seconds=93,
                          transcript_json=segments, delegations_json=[{"delegation_id": "item_1"}],
                          ended_at=store.utc_now())
        self.assertFalse(store.has_open_call(self.db, "coa"))
        row = store.get_call(self.db, "c2")
        self.assertEqual(row["voice_seconds"], 93)
        self.assertEqual(row["transcript"], segments)
        self.assertEqual(row["delegations"][0]["delegation_id"], "item_1")
        listed = store.list_calls(self.db, "coa")
        self.assertEqual(listed[0]["call_id"], "c2")
        self.assertNotIn("transcript_json", listed[0])

    def test_rejects_unknown_status(self):
        with self.assertRaises(ValueError):
            self._insert("c3", status="ringing")

    def test_expire_stale_open_calls(self):
        self._insert("old")
        conn = sqlite3.connect(self.db)
        conn.execute("UPDATE calls SET created_at='2020-01-01T00:00:00Z' WHERE call_id='old'")
        conn.commit()
        conn.close()
        self._insert("fresh")
        self.assertEqual(store.expire_stale_open_calls(self.db, "coa", 600), 1)
        self.assertEqual(store.get_call(self.db, "old")["close_reason"], "unconfirmed")
        self.assertEqual(store.get_call(self.db, "fresh")["status"], "calling")

    def test_summary_column_added_to_existing_table(self):
        first_release = store.SCHEMA_SQL.replace(",\n  summary            TEXT", "")
        self.assertNotIn("summary", first_release)
        conn = sqlite3.connect(self.db)
        conn.executescript(first_release)
        conn.execute("INSERT INTO calls (call_id, agent_name, status, reason, created_at) "
                     "VALUES ('old', 'coa', 'ended', 'r', '2026-09-26T00:00:00Z')")
        conn.commit()
        conn.close()
        self.assertTrue(store.update_call(self.db, "old", summary="Chose Friday."))
        self.assertEqual(store.list_calls(self.db, "coa")[0]["summary"], "Chose Friday.")

    def test_transcript_text(self):
        text = store.transcript_text([
            {"speaker": "pu", "text": "Can you check task 12?"},
            {"speaker": "assistant", "text": " "},
            {"speaker": "assistant", "text": "It is blocked on review."},
        ])
        self.assertEqual(text, "PU: Can you check task 12?\nCOA: It is blocked on review.")


class TestSchemaParity(unittest.TestCase):
    """init_messages_db.sh and call_log_store must create the same ``calls`` table."""

    @unittest.skipUnless(shutil.which("sqlite3"), "sqlite3 CLI not installed")
    def test_init_script_matches_store(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        script_db = os.path.join(tmp.name, "script.db")
        store_db = os.path.join(tmp.name, "store.db")
        script = os.path.join(os.path.dirname(__file__), "..", "..", "scripts", "init_messages_db.sh")
        subprocess.run(["bash", script, script_db], check=True, capture_output=True)
        store.list_calls(store_db)

        def cols(path):
            conn = sqlite3.connect(path)
            try:
                return [(r[1], r[2], r[3], r[5]) for r in conn.execute("PRAGMA table_info(calls)")]
            finally:
                conn.close()

        def check_sql(path):
            conn = sqlite3.connect(path)
            try:
                sql = conn.execute("SELECT sql FROM sqlite_master WHERE name='calls'").fetchone()[0]
            finally:
                conn.close()
            return re.search(r"CHECK\(status IN \(([^)]*)\)\)", sql).group(1)

        self.assertEqual(cols(script_db), cols(store_db))
        self.assertEqual(check_sql(script_db), check_sql(store_db))


if __name__ == "__main__":
    unittest.main()
