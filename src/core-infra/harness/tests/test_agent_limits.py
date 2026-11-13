"""Agent Timeout range 30–60 and shared defaults (state_live_chat.md §1.5, LCH-01/02)."""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

CORE_INFRA = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, CORE_INFRA)

import agent_limits  # noqa: E402


class TestAgentLimits(unittest.TestCase):
    def test_defaults(self):
        self.assertEqual(agent_limits.DEFAULT_TIMEOUT_MINUTES, 45)
        self.assertEqual(agent_limits.DEFAULT_RUNAWAY_LINES, 2500)
        self.assertEqual(agent_limits.DEFAULT_RUNAWAY_SIZE_KB, 2048)

    def test_timeout_error(self):
        for ok in (30, "45", " 60 ", 120, 240):
            self.assertEqual(agent_limits.timeout_error(ok), "", ok)
        for bad in (29, 241, 300, 0, -5, "abc", "", None):
            self.assertTrue(agent_limits.timeout_error(bad), bad)
        self.assertIn("between 30 and 240", agent_limits.timeout_error(300))

    def test_clamp(self):
        cases = {10: 30, 30: 30, 45: 45, 60: 60, 90: 90, 240: 240, 300: 240, "x": 45, None: 45}
        for raw, want in cases.items():
            self.assertEqual(agent_limits.clamp_timeout(raw), want, raw)

    def test_live_call_fits_shortest_timeout(self):
        import live_call_config as lcc
        self.assertLess(max(lcc.MAX_MINUTES_CHOICES), agent_limits.TIMEOUT_MIN_MINUTES)


class TestInitDbClamp(unittest.TestCase):
    """The clamp block in scripts/init_agents_db.sh, run against a temp DB."""

    def test_clamp_sql(self):
        script = os.path.join(CORE_INFRA, "scripts", "init_agents_db.sh")
        with open(script, encoding="utf-8") as fh:
            text = fh.read()
        start = text.index("# ─── Time limit range 30–240 minutes")
        end = text.index("\nfi\n", start) + 4
        block = text[start:end]
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "agents.db")
            with sqlite3.connect(db) as conn:
                conn.execute("CREATE TABLE agents (name TEXT, timeout_minutes INTEGER, "
                             "updated_at DATETIME)")
                conn.executemany("INSERT INTO agents VALUES (?, ?, NULL)",
                                 [("a", 10), ("b", 45), ("c", 300), ("d", None), ("e", 120)])
            out = subprocess.run(["bash", "-c", f'set -euo pipefail\nDB_PATH="{db}"\n{block}'],
                                 capture_output=True, text=True, check=True).stdout
            self.assertIn("for 3 agent(s)", out)
            with sqlite3.connect(db) as conn:
                rows = dict(conn.execute("SELECT name, timeout_minutes FROM agents"))
            self.assertEqual(rows, {"a": 30, "b": 45, "c": 240, "d": 45, "e": 120})


try:
    from click.testing import CliRunner
    sys.path.insert(0, os.path.join(CORE_INFRA, "agictl"))
    import agictl.cli as agictl_cli  # noqa: E402
except ImportError:  # harness venv has no click; run under /opt/versa-agi/venv
    agictl_cli = None


@unittest.skipIf(agictl_cli is None, "click not installed in this interpreter")
class TestSetTimeoutCli(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.tmp.name, "agents.db")
        with sqlite3.connect(self.db) as conn:
            conn.execute("CREATE TABLE agents (name TEXT, timeout_minutes INTEGER, updated_at DATETIME)")
            conn.execute("INSERT INTO agents VALUES ('coa', 45, NULL)")

    def tearDown(self):
        self.tmp.cleanup()

    def _set(self, minutes):
        with patch.object(agictl_cli, "agents_db", self.db):
            return CliRunner().invoke(agictl_cli.cli, ["agent", "set-timeout", "coa", str(minutes)])

    def _timeout(self):
        with sqlite3.connect(self.db) as conn:
            return conn.execute("SELECT timeout_minutes FROM agents WHERE name='coa'").fetchone()[0]

    def test_in_range_saves(self):
        result = self._set(50)
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertTrue(json.loads(result.output)["success"])
        self.assertEqual(self._timeout(), 50)
        self.assertEqual(self._set(120).exit_code, 0)    # state_long_cycles.md LRC-08
        self.assertEqual(self._timeout(), 120)

    def test_out_of_range_refused(self):
        for bad in (20, 300):
            result = self._set(bad)
            self.assertEqual(result.exit_code, 1, result.output)
            self.assertIn("between 30 and 240", result.output)
        self.assertEqual(self._timeout(), 45)


if __name__ == "__main__":
    unittest.main()
