"""Connection invitation follow-up (state_live_voice_call.md §3.11, LVC-26).

`agictl connection request` creates one check_connection task per contact,
first check in 15 minutes, carrying the reason.
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from click.testing import CliRunner

CORE_INFRA = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, CORE_INFRA)
sys.path.insert(0, os.path.join(CORE_INFRA, "agictl"))

import agictl.cli as agictl_cli  # noqa: E402
import comms  # noqa: E402
from agitop.data.tasks_reader import (  # noqa: E402
    CONNECTION_CHECK_MINUTES, TasksReader,
)

JOE = "LN374HrmLPRFVPDaKtjZ2AHBhRr1"
SIENNA = "9WeURzeolSWF7UhUv3ZA1KIQibe2"


class _Db(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.tmp.name, "tasks.db")
        subprocess.run(["bash", os.path.join(CORE_INFRA, "scripts", "init_tasks_db.sh"), self.db],
                       check=True, capture_output=True)
        self.reader = TasksReader(self.db)

    def tearDown(self):
        self.tmp.cleanup()

    def rows(self):
        con = sqlite3.connect(self.db)
        con.row_factory = sqlite3.Row
        out = [dict(r) for r in con.execute(
            "SELECT id, title, description, status, assigned_to, tags, callback_action, "
            "(julianday(wake_after) - julianday('now')) * 1440 AS wake_in_min, "
            "(julianday(due_date) - julianday('now')) * 1440 AS due_in_min "
            "FROM tasks ORDER BY id")]
        con.close()
        return out


class TestScheduleConnectionCheck(_Db):
    def test_first_check_in_15_minutes_with_reason(self):
        task_id = self.reader.schedule_connection_check(
            "coa", JOE, "Joe Nortje", "Live call: test call Stephen asked for")
        self.assertIsNotNone(task_id)
        [row] = self.rows()
        self.assertEqual(row["title"], "Check connection: Joe Nortje")
        self.assertEqual(row["status"], "blocked")
        self.assertEqual(row["assigned_to"], "coa")
        self.assertEqual(row["tags"], f"connection:{JOE}")
        self.assertEqual(row["callback_action"], "check_connection")
        self.assertAlmostEqual(row["wake_in_min"], CONNECTION_CHECK_MINUTES, delta=1)
        self.assertAlmostEqual(row["due_in_min"], CONNECTION_CHECK_MINUTES, delta=1)
        self.assertIn("Live call: test call Stephen asked for", row["description"])
        self.assertIn(f"recipient_id={JOE}", row["description"])
        self.assertIn("snooze <this_task_id> 30", row["description"])
        self.assertIn("snooze <this_task_id> 1440", row["description"])

    def test_same_contact_reuses_open_task(self):
        first = self.reader.schedule_connection_check("coa", JOE, "Joe Nortje")
        again = self.reader.schedule_connection_check("coa", JOE, "Joe Nortje", "second ask")
        self.assertEqual(first, again)
        self.assertEqual(len(self.rows()), 1)

    def test_second_contact_gets_its_own_task(self):
        self.reader.schedule_connection_check("coa", JOE, "Joe Nortje")
        self.reader.schedule_connection_check("coa", SIENNA, "Sienna")
        self.assertEqual([r["tags"] for r in self.rows()],
                         [f"connection:{JOE}", f"connection:{SIENNA}"])

    def test_done_task_does_not_block_a_new_invitation(self):
        first = self.reader.schedule_connection_check("coa", JOE, "Joe Nortje")
        self.reader.update_task_status(first, "done")
        second = self.reader.schedule_connection_check("coa", JOE, "Joe Nortje")
        self.assertNotEqual(first, second)


class TestConnectionRequestCommand(_Db):
    def setUp(self):
        super().setUp()
        conf = os.path.join(self.tmp.name, "coa_config.json")
        with open(conf, "w") as f:
            json.dump({"versavoice": {"api_token": "tok", "sub_account_id": "sub_1"}}, f)
        self.env = {"AGICTL_CONFIG": conf}
        self._patches = [
            patch.object(agictl_cli, "tasks_db", self.db),
            patch.object(agictl_cli, "tasks_reader", self.reader),
            patch.object(agictl_cli, "agents_db", os.path.join(self.tmp.name, "agents.db")),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in reversed(self._patches):
            p.stop()
        super().tearDown()

    def invoke(self, link_response):
        def api(endpoint, token, method="GET", body=None, timeout=None):
            if endpoint == "/connections":
                return {"connections": [{"uid": JOE, "displayName": "Joe Nortje"}]}
            return link_response

        with patch.object(comms, "api_request", side_effect=api), patch.dict(os.environ, self.env):
            return CliRunner().invoke(agictl_cli.cli, [
                "connection", "request", JOE, "--reason", "Live call: test call",
            ])

    def test_invitation_creates_follow_up(self):
        res = self.invoke({"success": True, "data": {"displayName": "Joe Nortje"}})
        self.assertEqual(res.exit_code, 0, res.output)
        out = json.loads(res.output.strip().splitlines()[-1])
        self.assertEqual(out["status"], "invitation_sent")
        [row] = self.rows()
        self.assertEqual(out["follow_up_task_id"], row["id"])
        self.assertEqual(row["assigned_to"], "coa")
        self.assertIn("Live call: test call", row["description"])
        self.assertIn("15 minutes", out["follow_up"])

    def test_refused_invitation_creates_nothing(self):
        res = self.invoke({"success": False, "message": "Forbidden"})
        self.assertNotIn("invitation_sent", res.output)
        self.assertEqual(self.rows(), [])


if __name__ == "__main__":
    unittest.main()
