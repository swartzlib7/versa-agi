"""SPWN-33..38 / GOL-07: handoff, wake reason, outage tick, pins, operational memory."""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from click.testing import CliRunner

CORE_INFRA = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)
sys.path.insert(0, CORE_INFRA)
sys.path.insert(0, os.path.join(CORE_INFRA, "agictl"))
sys.path.insert(0, os.path.join(CORE_INFRA, "agitop"))

import agictl.cli as agictl_cli  # noqa: E402
from agitop.data.agent_reader import AgentReader  # noqa: E402

SCRIPTS = os.path.join(CORE_INFRA, "scripts")


def _ts(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def _ago(**kw) -> str:
    return _ts(datetime.now(timezone.utc) - timedelta(**kw))


class _Dbs(unittest.TestCase):
    """Real schemas from the init scripts, in a temp dir."""

    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        d = self.tempdir.name
        self.cycles_db = os.path.join(d, "cycles.db")
        self.tasks_db = os.path.join(d, "tasks.db")
        self.agents_db = os.path.join(d, "agents.db")
        os.system(f"bash {SCRIPTS}/init_cycles_db.sh {self.cycles_db} >/dev/null 2>&1")
        os.system(f"bash {SCRIPTS}/init_tasks_db.sh {self.tasks_db} >/dev/null 2>&1")
        with sqlite3.connect(self.agents_db) as conn:
            conn.execute("CREATE TABLE agents (name TEXT PRIMARY KEY, protected INTEGER DEFAULT 0)")
            conn.executemany(
                "INSERT INTO agents (name, protected) VALUES (?, ?)",
                [("coa", 1), ("web-dev", 0), ("clerk", 0)],
            )

    def tearDown(self):
        self.tempdir.cleanup()

    def invoke(self, args, caller="web-dev", agent_user="agi-web-dev"):
        env = {"AGICTL_AGENT_USER": agent_user if agent_user is not None else ""}
        with (
            patch.dict(os.environ, env, clear=False),
            patch.object(agictl_cli, "cycles_db", self.cycles_db),
            patch.object(agictl_cli, "tasks_db", self.tasks_db),
            patch.object(agictl_cli, "agents_db", self.agents_db),
            patch.object(agictl_cli, "_caller_agent_name", return_value=caller),
            patch.object(agictl_cli, "get_agent_name", return_value=caller),
        ):
            return CliRunner().invoke(agictl_cli.cli, args)

    def add_cycle(self, cid, started, ended=None, handoff=None, reason=None, summary="s", exit_code=None):
        with sqlite3.connect(self.cycles_db) as conn:
            conn.execute(
                "INSERT INTO cycles (id, started_at, ended_at, summary, handoff, wake_reason, exit_code) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (cid, started, ended, summary, handoff, reason, exit_code),
            )

    def add_awareness(self, agent, content, etype="conclusion", status="active", pinned=0, updated=None):
        with sqlite3.connect(self.tasks_db) as conn:
            cur = conn.execute(
                "INSERT INTO agent_awareness (agent_name, type, subject_type, content, status, pinned, updated_at) "
                "VALUES (?, ?, 'self', ?, ?, ?, ?)",
                (agent, etype, content, status, pinned, updated or _ago(minutes=1)),
            )
            return cur.lastrowid

    def handoff_block(self, agent="web-dev"):
        with (
            patch.object(agictl_cli, "cycles_db", self.cycles_db),
            patch.object(agictl_cli, "tasks_db", self.tasks_db),
        ):
            return agictl_cli.render_handoff_block(agent)


class TestCycleStartEnd(_Dbs):
    def test_start_stores_wake_reason(self):
        r = self.invoke(["cycle", "start", "--wake-reason", "1 unprocessed message(s) + 2 active task(s)"])
        self.assertEqual(r.exit_code, 0, r.output)
        cid = json.loads(r.output)["cycle_id"]
        with sqlite3.connect(self.cycles_db) as conn:
            reason = conn.execute("SELECT wake_reason FROM cycles WHERE id=?", (cid,)).fetchone()[0]
        self.assertEqual(reason, "1 unprocessed message(s) + 2 active task(s)")

    def test_end_stores_handoff_and_exits_zero(self):
        self.invoke(["cycle", "start"])
        r = self.invoke(["cycle", "end", "Did X", "--handoff", "Open: Y | Next: Z | Carry: #4"])
        self.assertEqual(r.exit_code, 0, r.output)
        self.assertIn("🛑 Cycle ended: Did X", r.output)
        self.assertNotIn("Cycle NOT ended", r.output)
        with sqlite3.connect(self.cycles_db) as conn:
            row = conn.execute("SELECT summary, handoff, ended_at FROM cycles").fetchone()
        self.assertEqual(row[0], "Did X")
        self.assertEqual(row[1], "Open: Y | Next: Z | Carry: #4")
        self.assertIsNotNone(row[2])

    def test_missing_handoff_refuses_and_keeps_cycle_open(self):
        self.invoke(["cycle", "start"])
        r = self.invoke(["cycle", "end", "Did X"])
        self.assertEqual(r.exit_code, 1, r.output)
        self.assertIn("Cycle NOT ended", r.output)
        self.assertIn("--no-handoff", r.output)
        self.assertNotIn("🛑 Cycle ended", r.output)
        with sqlite3.connect(self.cycles_db) as conn:
            row = conn.execute("SELECT summary, ended_at FROM cycles").fetchone()
        self.assertEqual(row, (None, None))

    def test_no_handoff_confirms_end(self):
        self.invoke(["cycle", "start"])
        self.invoke(["cycle", "end", "Did X"])
        r = self.invoke(["cycle", "end", "Did X", "--no-handoff"])
        self.assertEqual(r.exit_code, 0, r.output)
        self.assertIn("🛑 Cycle ended: Did X", r.output)
        with sqlite3.connect(self.cycles_db) as conn:
            row = conn.execute("SELECT summary, handoff, ended_at FROM cycles").fetchone()
        self.assertEqual(row[0], "Did X")
        self.assertIsNone(row[1])
        self.assertIsNotNone(row[2])

    def test_long_handoff_is_stored_in_full(self):
        self.invoke(["cycle", "start"])
        text = "Open: " + "x" * 5000
        self.invoke(["cycle", "end", "Did X", "--handoff", text])
        with sqlite3.connect(self.cycles_db) as conn:
            self.assertEqual(conn.execute("SELECT handoff FROM cycles").fetchone()[0], text)


class TestHandoffBlock(_Dbs):
    def test_no_cycles_no_block(self):
        self.assertEqual(self.handoff_block(), "")

    def test_last_handoff_with_reason_and_age(self):
        self.add_cycle("web-dev-1", _ago(hours=3), _ago(hours=2), handoff="Open: deploy | Next: verify",
                       reason="1 unprocessed message(s)")
        block = self.handoff_block()
        self.assertIn("SINCE YOUR LAST CYCLE", block)
        self.assertIn("It woke for: 1 unprocessed message(s).", block)
        self.assertIn("LAST HANDOFF", block)
        self.assertIn("Open: deploy | Next: verify", block)
        self.assertIn("2h 0m ago", block)
        self.assertIn("Continuity and time", block)

    def test_missing_handoff_shows_newest_earlier(self):
        self.add_cycle("web-dev-1", _ago(days=3), _ago(days=3), handoff="Open: old item")
        self.add_cycle("web-dev-2", _ago(days=2), _ago(days=2), handoff="Open: newer item")
        self.add_cycle("web-dev-3", _ago(hours=1), None, summary="[Harness] Step budget reached", exit_code=53)
        block = self.handoff_block()
        self.assertIn("never closed", block)
        self.assertIn("Exit code 53", block)
        self.assertIn("Your last cycle ended without a handoff.", block)
        self.assertIn("NEWEST EARLIER HANDOFF", block)
        self.assertIn("Open: newer item", block)
        self.assertNotIn("old item", block)

    def test_other_agent_cycles_ignored(self):
        self.add_cycle("clerk-1", _ago(hours=1), _ago(hours=1), handoff="Open: clerk stuff")
        self.assertEqual(self.handoff_block("web-dev"), "")

    def test_handoff_cut_only_above_ceiling(self):
        under = "Open: " + "a" * 2500
        self.add_cycle("web-dev-1", _ago(hours=2), _ago(hours=2), handoff=under)
        self.assertIn(under, self.handoff_block())
        over = "Open: " + "b" * 3500
        self.add_cycle("web-dev-2", _ago(hours=1), _ago(hours=1), handoff=over)
        block = self.handoff_block()
        self.assertNotIn(over, block)
        self.assertIn("[cut — full: agictl cycle get web-dev-2]", block)

    def test_carried_awareness_expanded(self):
        active = self.add_awareness("web-dev", "Mandy: no nudges until she writes first", updated=_ago(days=10))
        gone = self.add_awareness("web-dev", "old", status="superseded")
        pinned = self.add_awareness("web-dev", "pinned rule", pinned=1)
        self.add_cycle("web-dev-1", _ago(hours=2), _ago(hours=2),
                       handoff=f"Open: x | Carry: #{active}, #{gone}, #{pinned}, #9999")
        block = self.handoff_block()
        self.assertIn("CARRIED AWARENESS", block)
        self.assertIn(f"#{active} [conclusion · self]", block)
        self.assertIn("Mandy: no nudges", block)
        self.assertIn("10d 0h ago", block)
        self.assertIn(f"#{gone} — no longer active (superseded)", block)
        self.assertNotIn("pinned rule", block)
        self.assertIn("#9999 — not found", block)

    def test_carry_limit_five(self):
        ids = [self.add_awareness("web-dev", f"c{i}") for i in range(7)]
        self.add_cycle("web-dev-1", _ago(hours=2), _ago(hours=2),
                       handoff="Carry: " + ", ".join(f"#{i}" for i in ids))
        block = self.handoff_block()
        self.assertIn("c4", block)
        self.assertNotIn("c5", block)


class TestOutageTick(_Dbs):
    def config(self, key):
        with sqlite3.connect(self.cycles_db) as conn:
            row = conn.execute("SELECT value FROM config WHERE key=?", (key,)).fetchone()
        return row[0] if row else None

    def set_config(self, key, value):
        with sqlite3.connect(self.cycles_db) as conn:
            conn.execute("INSERT OR REPLACE INTO config (key, value) VALUES (?, ?)", (key, value))

    def test_first_tick_records_no_gap(self):
        r = self.invoke(["cycle", "tick", "--threshold-minutes", "30"])
        self.assertEqual(r.exit_code, 0, r.output)
        self.assertIsNone(json.loads(r.output)["gap"])
        self.assertIsNotNone(self.config("lifeline_last_tick"))

    def test_recent_tick_no_gap(self):
        self.set_config("lifeline_last_tick", _ago(minutes=2))
        r = self.invoke(["cycle", "tick", "--threshold-minutes", "30"])
        self.assertIsNone(json.loads(r.output)["gap"])
        self.assertIsNone(self.config("lifeline_gaps"))

    def test_old_tick_records_gap(self):
        start = _ago(days=40)
        self.set_config("lifeline_last_tick", start)
        r = self.invoke(["cycle", "tick", "--threshold-minutes", "30"])
        gap = json.loads(r.output)["gap"]
        self.assertEqual(gap["start"], start)
        self.assertEqual(json.loads(self.config("lifeline_gaps"))[-1]["start"], start)

    def test_gaps_capped(self):
        gaps = [{"start": _ago(days=100 - i), "end": _ago(days=99 - i)} for i in range(10)]
        self.set_config("lifeline_gaps", json.dumps(gaps))
        self.set_config("lifeline_last_tick", _ago(hours=5))
        self.invoke(["cycle", "tick", "--threshold-minutes", "30"])
        stored = json.loads(self.config("lifeline_gaps"))
        self.assertEqual(len(stored), 10)
        self.assertEqual(stored[0], gaps[1])

    def test_outage_shown_only_if_after_last_cycle(self):
        self.add_cycle("web-dev-1", _ago(days=40, hours=1), _ago(days=40), handoff="Open: thing")
        self.set_config("lifeline_gaps", json.dumps([
            {"start": _ago(days=60), "end": _ago(days=59)},
            {"start": _ago(days=39), "end": _ago(hours=1)},
        ]))
        block = self.handoff_block()
        self.assertEqual(block.count("SYSTEM OFFLINE"), 1)
        self.assertIn("(about 1 month)", block)

    def test_outage_before_last_cycle_not_shown(self):
        self.set_config("lifeline_gaps", json.dumps([{"start": _ago(days=3), "end": _ago(days=2)}]))
        self.add_cycle("web-dev-1", _ago(hours=2), _ago(hours=1), handoff="Open: thing")
        self.assertNotIn("SYSTEM OFFLINE", self.handoff_block())


class TestRecentActivity(_Dbs):
    def test_wake_reason_in_line_and_collapse(self):
        self.add_cycle("web-dev-1", _ago(hours=5), summary="old style")
        self.add_cycle("web-dev-2", _ago(hours=4), summary="idle", reason="2 due task(s)")
        self.add_cycle("web-dev-3", _ago(hours=3), summary="idle", reason="2 due task(s)")
        self.add_cycle("web-dev-4", _ago(hours=2), summary="idle", reason="1 unprocessed message(s)")
        lines = AgentReader(self.agents_db, self.cycles_db, "", self.tasks_db).get_recent_cycle_summaries("web-dev")
        self.assertEqual(len(lines), 3)
        self.assertTrue(lines[0].endswith(": old style"))
        self.assertNotIn("[woke:", lines[0])
        self.assertIn("[woke: 2 due task(s)]: idle (repeated ×2)", lines[1])
        self.assertIn("[woke: 1 unprocessed message(s)]: idle", lines[2])


class TestPins(_Dbs):
    def test_pin_and_table_filters(self):
        pinned = self.add_awareness("web-dev", "standing rule", updated=_ago(days=3))
        plain = self.add_awareness("web-dev", "status note")
        r = self.invoke(["awareness", "pin", str(pinned)])
        self.assertEqual(r.exit_code, 0, r.output)
        only = self.invoke(["awareness", "table", "--pinned", "--status", "active", "--dated"]).output
        self.assertIn("standing rule", only)
        self.assertNotIn("status note", only)
        self.assertIn("| Updated |", only)
        self.assertIn("(3d 0h ago)", only)
        rest = self.invoke(["awareness", "table", "--unpinned", "--status", "active"]).output
        self.assertIn("status note", rest)
        self.assertNotIn("standing rule", rest)
        self.assertNotIn("| Updated |", rest)
        self.assertTrue(plain)

    def test_pin_rejects_action_and_inactive(self):
        action = self.add_awareness("web-dev", "do it", etype="action")
        old = self.add_awareness("web-dev", "old", status="superseded")
        self.assertIn("only conclusions", self.invoke(["awareness", "pin", str(action)]).output)
        self.assertIn("only active conclusions", self.invoke(["awareness", "pin", str(old)]).output)

    def test_pin_ownership(self):
        theirs = self.add_awareness("clerk", "clerk rule")
        r = self.invoke(["awareness", "pin", str(theirs)])
        self.assertNotEqual(r.exit_code, 0)
        self.assertIn("Ownership denied", r.output)
        r = self.invoke(["awareness", "pin", str(theirs)], caller="coa", agent_user="coa")
        self.assertEqual(r.exit_code, 0, r.output)

    def test_pin_limit_ten(self):
        for i in range(10):
            self.add_awareness("web-dev", f"rule {i}", pinned=1)
        extra = self.add_awareness("web-dev", "eleventh")
        r = self.invoke(["awareness", "pin", str(extra)])
        self.assertNotEqual(r.exit_code, 0)
        self.assertIn("Pin limit reached (10)", r.output)

    def test_revise_carries_pin_supersede_drops(self):
        rule = self.add_awareness("web-dev", "rule v1", pinned=1)
        r = self.invoke(["awareness", "revise", str(rule), "--content", "rule v2"])
        new_id = json.loads(r.output)["new_id"]
        with sqlite3.connect(self.tasks_db) as conn:
            self.assertEqual(conn.execute("SELECT pinned FROM agent_awareness WHERE id=?", (new_id,)).fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT pinned FROM agent_awareness WHERE id=?", (rule,)).fetchone()[0], 0)
        self.invoke(["awareness", "supersede", str(new_id)])
        out = self.invoke(["awareness", "table", "--pinned", "--status", "active"]).output
        self.assertIn("No awareness entries found.", out)


class TestOperationalMemory(_Dbs):
    def setUp(self):
        super().setUp()
        rows = [
            ("web-dev", "discovery_api_quirk", "PATCH needs full body", _ago(minutes=5)),
            ("web-dev", "constraint_old", "old constraint", _ago(days=20)),
            ("web-dev", "user_instruction_voice", "PU prefers voice", _ago(days=2)),
            ("web-dev", "rule.restriction_x", "never touch x", _ago(days=1)),
            ("web-dev", "schedule_report", "weekly", _ago(hours=1)),
            ("web-dev", "discovery_long", "y" * 900, _ago(hours=2)),
            ("clerk", "constraint_clerk", "clerk only", _ago(minutes=1)),
        ]
        with sqlite3.connect(self.tasks_db) as conn:
            conn.executemany(
                "INSERT INTO agent_memory_system (agent_name, key, value, updated_at) VALUES (?, ?, ?, ?)",
                rows,
            )

    def render(self):
        r = self.invoke(["memory", "system", "list", "--agent", "web-dev", "--prompt"],
                        caller="coa", agent_user="")
        self.assertEqual(r.exit_code, 0, r.output)
        return r.output

    def test_scoped_ordered_dated(self):
        out = self.render()
        self.assertIn("OPERATIONAL MEMORY (your system memory — 6 entries)", out)
        self.assertNotIn("clerk only", out)
        keys = [ln.split()[0] for ln in out.splitlines() if ln.startswith("  ")]
        self.assertEqual(keys[:3], ["rule.restriction_x", "user_instruction_voice", "constraint_old"])
        self.assertEqual(keys[3:], ["discovery_api_quirk", "schedule_report", "discovery_long"])
        self.assertIn("(updated ", out)
        self.assertIn("20d 0h ago", out)

    def test_value_cut_and_footer(self):
        out = self.render()
        self.assertIn("[cut — full: agictl memory system get discovery_long]", out)
        self.assertNotIn("y" * 700, out)
        self.assertIn("'agictl memory system list --agent web-dev'", out)
        self.assertNotIn("more not shown", out)

    def test_block_limit_counts_hidden(self):
        with sqlite3.connect(self.tasks_db) as conn:
            conn.executemany(
                "INSERT INTO agent_memory_system (agent_name, key, value) VALUES (?, ?, ?)",
                [("web-dev", f"discovery_bulk_{i}", "z" * 500) for i in range(30)],
            )
        out = self.render()
        self.assertLessEqual(len(out), agictl_cli.OPERATIONAL_MEMORY_BLOCK_CUT + 400)
        self.assertIn("more not shown", out)
        self.assertIn("rule.restriction_x", out)

    def test_prompt_needs_agent(self):
        r = self.invoke(["memory", "system", "list", "--prompt"])
        self.assertNotEqual(r.exit_code, 0)
        self.assertIn("--prompt needs --agent", r.output)

    def test_plain_list_unchanged(self):
        r = self.invoke(["memory", "system", "list"])
        rows = json.loads(r.output)
        self.assertEqual(len(rows), 7)


if __name__ == "__main__":
    unittest.main()
