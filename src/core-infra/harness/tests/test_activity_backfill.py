"""Activity history backfill from cycles.db (state_live_chat.md LCH-24)."""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

CORE_INFRA = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, CORE_INFRA)
sys.path.insert(0, os.path.join(CORE_INFRA, "agictl"))

from activity_backfill import build_stats, outcome_for, wake_for  # noqa: E402

NOW = datetime(2026, 11, 9, 12, 0, tzinfo=timezone.utc)


def _row(cid, start, end, code, wake="1 unprocessed message(s)"):
    return {"id": cid, "started_at": start, "ended_at": end, "exit_code": code, "wake_reason": wake}


class TestMapping(unittest.TestCase):
    def test_outcomes(self):
        self.assertEqual(outcome_for(0, "x"), "ok")
        self.assertEqual(outcome_for(124, None), "timeout")
        self.assertEqual(outcome_for(99, None), "runaway")
        self.assertEqual(outcome_for(53, "x"), "step_limit")
        self.assertEqual(outcome_for(143, None), "crash")
        self.assertEqual(outcome_for(None, "2026-11-09 08:00:00"), "ok")
        self.assertIsNone(outcome_for(None, None))  # still running

    def test_wake(self):
        self.assertEqual(wake_for("2 unprocessed message(s)"), "messages")
        self.assertEqual(wake_for("1 active task(s) reached due date"), "tasks")
        self.assertEqual(wake_for(None), "tasks")


class TestBuild(unittest.TestCase):
    def test_full_history(self):
        rows = [_row(f"coa-{i}", f"2026-10-0{1 + i % 9} 08:00:00", f"2026-10-0{1 + i % 9} 08:05:00", 0)
                for i in range(30)]
        rows += [
            _row("coa-900", "2026-11-09 10:50:00", "2026-11-09 11:10:00", 0),          # spans 10→11 UTC
            _row("coa-901", "2026-11-09 11:20:00", None, 124, "2 active task(s)"),    # timeout, no end
            _row("coa-902", "2026-11-09 11:40:00", None, None),                       # running now
        ]
        st = build_stats(rows, now=NOW)
        self.assertEqual(st["cyclesTotal"], 33)
        self.assertEqual(st["outcomes"], {"ok": 31, "timeout": 1, "runaway": 0, "step_limit": 0, "crash": 0})
        self.assertEqual(len(st["recent"]), 20)
        self.assertEqual(st["recent"][0]["cycleId"], "coa-900")  # newest timed cycle first
        self.assertEqual(st["recent"][0]["durationSeconds"], 1200)
        self.assertEqual(st["hourly"]["2026-11-09T10"], {"cycles": 0, "busySeconds": 600})
        self.assertEqual(st["hourly"]["2026-11-09T11"], {"cycles": 1, "busySeconds": 600})
        self.assertNotIn("2026-10-01T08", st["hourly"])  # older than 48 h
        self.assertEqual(st["trackedSince"], "2026-10-01T08:00:00Z")

    def test_empty(self):
        st = build_stats([], now=NOW)
        self.assertEqual((st["cyclesTotal"], st["recent"], st["hourly"]), (0, [], {}))


try:
    from click.testing import CliRunner
    import agictl.cli as agictl_cli  # noqa: E402
    import comms  # noqa: E402
except ImportError:
    agictl_cli = None


@unittest.skipIf(agictl_cli is None, "click not installed in this interpreter")
class TestBackfillCli(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.tmp.name, "cycles.db")
        with sqlite3.connect(self.db) as conn:
            conn.execute("CREATE TABLE cycles (id TEXT PRIMARY KEY, started_at TEXT, ended_at TEXT, "
                         "exit_code INTEGER, wake_reason TEXT)")
            conn.executemany("INSERT INTO cycles VALUES (?,?,?,?,?)", [
                ("coa-1", "2026-11-09 08:00:00", "2026-11-09 08:04:00", 0, "1 unprocessed message(s)"),
                ("coa-2", "2026-11-09 09:00:00", None, 124, "1 active task(s)"),
                ("coach-3", "2026-11-09 09:00:00", "2026-11-09 09:01:00", 0, ""),
            ])

    def tearDown(self):
        self.tmp.cleanup()

    def test_posts_backfill_for_this_agent_only(self):
        calls = []

        def fake(endpoint, token, method="GET", body=None, timeout=None):
            calls.append(body)
            return {"success": True, "data": {"applied": True}}

        with patch.object(agictl_cli, "cycles_db", self.db), patch.object(comms, "api_request", side_effect=fake), \
                patch.dict(os.environ, {"AGICTL_AGENT_USER": ""}):
            result = CliRunner().invoke(agictl_cli.cli, [
                "system", "activity-backfill", "--sub-account", "sub_coa", "--token", "t", "--agent", "coa"])
        self.assertEqual(result.exit_code, 0, result.output)
        body = calls[0]
        self.assertEqual((body["event"], body["subAccountId"]), ("backfill", "sub_coa"))
        self.assertEqual(body["stats"]["cyclesTotal"], 2)  # coach-3 excluded
        self.assertEqual(body["stats"]["outcomes"]["timeout"], 1)
        self.assertEqual(json.loads(result.output.strip().splitlines()[-1])["cycles"], 2)

    def test_agents_refused(self):
        with patch.dict(os.environ, {"AGICTL_AGENT_USER": "coa"}):
            result = CliRunner().invoke(agictl_cli.cli, [
                "system", "activity-backfill", "--sub-account", "s", "--token", "t", "--agent", "coa"])
        self.assertEqual(result.exit_code, 1)


class TestLifelineBackfill(unittest.TestCase):
    def test_runs_once_per_agent_after_credentials(self):
        with open(os.path.join(CORE_INFRA, "lifeline.sh"), encoding="utf-8") as fh:
            text = fh.read()
        creds = text.index("API_TOKEN=$(jq -r '.versavoice.api_token // empty' \"${SYSTEM_CONFIG}\"")
        call = text.index("  _vv_activity_backfill_once\n")
        self.assertLess(creds, call)
        self.assertIn('.activity_backfill_done', text)
        self.assertIn('-mmin -60', text)


if __name__ == "__main__":
    unittest.main()
