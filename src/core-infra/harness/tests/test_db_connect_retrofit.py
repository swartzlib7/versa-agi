"""ORG-D24 — db_connect helper six-point retrofit checks (temp DBs)."""
from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import db_connect  # noqa: E402


class TestDbConnectRetrofit(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.tmp.name, "t.db")
        conn = db_connect.connect(self.db, row_factory=False)
        conn.executescript(
            """
            PRAGMA journal_mode=WAL;
            CREATE TABLE parents (
              id INTEGER PRIMARY KEY,
              name TEXT NOT NULL
            );
            CREATE TABLE children (
              id INTEGER PRIMARY KEY,
              parent_id INTEGER NOT NULL REFERENCES parents(id),
              label TEXT
            );
            INSERT INTO parents(id, name) VALUES (1, 'ok');
            """
        )
        conn.close()

    def tearDown(self):
        self.tmp.cleanup()

    def test_01_foreign_key_check_clean(self):
        conn = db_connect.connect(self.db)
        self.assertEqual(list(conn.execute("PRAGMA foreign_key_check")), [])
        conn.close()

    def test_02_foreign_keys_pragma_on(self):
        conn = db_connect.connect(self.db)
        self.assertEqual(conn.execute("PRAGMA foreign_keys").fetchone()[0], 1)
        conn.close()

    def test_03_busy_timeout_5000(self):
        conn = db_connect.connect(self.db)
        self.assertEqual(conn.execute("PRAGMA busy_timeout").fetchone()[0], 5000)
        conn.close()

    def test_04_representative_write_succeeds(self):
        conn = db_connect.connect(self.db, row_factory=False)
        conn.execute("INSERT INTO children(parent_id, label) VALUES (1, 'a')")
        conn.commit()
        n = conn.execute("SELECT COUNT(*) FROM children").fetchone()[0]
        self.assertEqual(n, 1)
        conn.close()

    def test_05_bad_fk_rejected(self):
        conn = db_connect.connect(self.db, row_factory=False)
        with self.assertRaises(sqlite3.IntegrityError):
            conn.execute("INSERT INTO children(parent_id, label) VALUES (999, 'x')")
            conn.commit()
        conn.close()

    def test_06_readonly_open(self):
        conn = db_connect.connect(self.db, readonly=True, timeout=2)
        rows = list(conn.execute("SELECT id FROM parents"))
        self.assertEqual(len(rows), 1)
        conn.close()

    def test_connect_compat_uri_ro(self):
        conn = db_connect.connect_compat(
            f"file:{self.db}?mode=ro", uri=True, timeout=2
        )
        self.assertEqual(conn.execute("PRAGMA foreign_keys").fetchone()[0], 1)
        conn.close()


if __name__ == "__main__":
    unittest.main()
