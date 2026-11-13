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


class TestOneCycleOneRow(_Dbs):
    """SPWN-46 / SPWN-47: cycle end and Lifeline's close each write to one row."""

    def _rows(self):
        with sqlite3.connect(self.cycles_db) as conn:
            conn.row_factory = sqlite3.Row
            return {r["id"]: dict(r) for r in conn.execute("SELECT * FROM cycles")}

    def test_end_leaves_earlier_open_rows_alone(self):
        # Two cycles killed at the time limit, never closed; then a clean one.
        self.add_cycle("web-dev-100", _ago(hours=3), summary=None, exit_code=124)
        self.add_cycle("web-dev-200", _ago(hours=2), summary=None, exit_code=124)
        self.add_cycle("web-dev-300", _ago(minutes=20), summary=None)
        r = self.invoke(["cycle", "end", "Did X", "--handoff", "Open: Y"])
        self.assertEqual(r.exit_code, 0, r.output)
        rows = self._rows()
        self.assertEqual(rows["web-dev-300"]["summary"], "Did X")
        self.assertEqual(rows["web-dev-300"]["handoff"], "Open: Y")
        self.assertIsNotNone(rows["web-dev-300"]["ended_at"])
        for cid in ("web-dev-100", "web-dev-200"):
            self.assertIsNone(rows[cid]["summary"], cid)
            self.assertIsNone(rows[cid]["handoff"], cid)
            self.assertIsNone(rows[cid]["ended_at"], cid)

    def test_end_ignores_an_agent_whose_name_starts_the_same(self):
        self.add_cycle("web-dev-100", _ago(hours=1), summary=None)
        self.add_cycle("web-200", _ago(minutes=5), summary=None)
        r = self.invoke(["cycle", "end", "Web done", "--no-handoff"], caller="web", agent_user="agi-web")
        self.assertEqual(r.exit_code, 0, r.output)
        rows = self._rows()
        self.assertEqual(rows["web-200"]["summary"], "Web done")
        self.assertIsNone(rows["web-dev-100"]["ended_at"])

    def test_awareness_timestamp_goes_to_the_newest_open_row(self):
        self.add_cycle("web-dev-100", _ago(hours=3), summary=None, exit_code=124)
        self.add_cycle("web-dev-300", _ago(minutes=20), summary=None)
        with patch.object(agictl_cli, "cycles_db", self.cycles_db):
            agictl_cli._update_awareness_timestamp("web-dev")
        rows = self._rows()
        self.assertIsNotNone(rows["web-dev-300"]["last_awareness_ts"])
        self.assertIsNone(rows["web-dev-100"]["last_awareness_ts"])

    def _reader(self):
        return AgentReader(self.agents_db, self.cycles_db, "", self.tasks_db)

    def test_lifeline_closes_a_killed_cycle_on_its_own_row(self):
        self.add_cycle("web-dev-100", _ago(hours=2), summary=None)
        self.add_cycle("web-dev-200", _ago(minutes=50), summary=None)
        # Lifeline reports on the older spawn: the id decides the row, not "newest".
        self._reader().update_last_cycle_tokens(
            "web-dev", 0, 0, 0, 0, exit_code=124, cycle_id="web-dev-100", outcome="timeout",
        )
        rows = self._rows()
        self.assertEqual(rows["web-dev-100"]["exit_code"], 124)
        self.assertIsNotNone(rows["web-dev-100"]["ended_at"])
        self.assertIn("time limit", rows["web-dev-100"]["summary"])
        self.assertTrue(rows["web-dev-100"]["summary"].startswith("[Lifeline]"))
        self.assertIsNone(rows["web-dev-200"]["exit_code"])
        self.assertIsNone(rows["web-dev-200"]["ended_at"])

    def test_lifeline_keeps_a_summary_the_agent_saved(self):
        # Live chat: `cycle end` saved the summary and left the row open.
        self.add_cycle("web-dev-100", _ago(minutes=30), summary="Chat with Stephen", handoff="Open: Z")
        self._reader().update_last_cycle_tokens(
            "web-dev", 10, 2, 0, 12, exit_code=0, cycle_id="web-dev-100", outcome="ok",
        )
        row = self._rows()["web-dev-100"]
        self.assertEqual(row["summary"], "Chat with Stephen")
        self.assertEqual(row["handoff"], "Open: Z")
        self.assertIsNotNone(row["ended_at"])
        self.assertEqual(row["tokens_total"], 12)

    def test_lifeline_does_not_move_the_end_time_of_a_closed_cycle(self):
        ended = _ago(minutes=10)
        self.add_cycle("web-dev-100", _ago(minutes=30), ended=ended, summary="Did X")
        self._reader().update_last_cycle_tokens(
            "web-dev", 10, 2, 0, 12, exit_code=0, cycle_id="web-dev-100", outcome="ok",
        )
        row = self._rows()["web-dev-100"]
        self.assertEqual(row["ended_at"], ended)
        self.assertEqual(row["summary"], "Did X")

    def test_without_cycle_id_the_newest_row_gets_the_tokens(self):
        self.add_cycle("web-dev-100", _ago(hours=2), summary=None)
        self.add_cycle("web-dev-200", _ago(minutes=5), summary=None)
        self._reader().update_last_cycle_tokens("web-dev", 1, 1, 0, 2, exit_code=0)
        rows = self._rows()
        self.assertEqual(rows["web-dev-200"]["tokens_total"], 2)
        self.assertIsNone(rows["web-dev-200"]["ended_at"])  # no outcome: row not closed
        self.assertEqual(rows["web-dev-100"]["tokens_total"] or 0, 0)

    def test_cycle_tokens_command_passes_id_and_outcome(self):
        self.add_cycle("web-dev-100", _ago(minutes=50), summary=None)
        with patch.object(agictl_cli, "agent_reader", self._reader()):
            r = self.invoke(["cycle", "tokens", "web-dev", "0", "0", "0", "0", "--exit-code", "99",
                             "--cycle-id", "web-dev-100", "--outcome", "runaway"])
        self.assertEqual(r.exit_code, 0, r.output)
        row = self._rows()["web-dev-100"]
        self.assertEqual(row["exit_code"], 99)
        self.assertIn("runaway", row["summary"])
        self.assertIsNotNone(row["ended_at"])

    def test_wake_block_after_a_lifeline_close(self):
        self.add_cycle("web-dev-100", _ago(minutes=50), summary=None, reason="1 active task(s)")
        self._reader().update_last_cycle_tokens(
            "web-dev", 0, 0, 0, 0, exit_code=124, cycle_id="web-dev-100", outcome="timeout",
        )
        block = self.handoff_block()
        self.assertIn("Exit code 124.", block)
        self.assertIn("Your last cycle ended without a handoff.", block)


class TestFramesForHarness(_Dbs):
    """SPWN-43: `cycle frames list --harness` gives the harness every status."""

    def _add(self, thread, status, ids, summary="text", agent="web-dev"):
        with sqlite3.connect(self.cycles_db) as conn:
            conn.execute(
                "INSERT INTO frames (agent_name, thread_id, status, frame_start, frame_end, summary, message_ids) "
                "VALUES (?, ?, ?, 't0', 't1', ?, ?)",
                (agent, thread, status, summary, ids),
            )

    def test_rolled_rows_come_back_without_their_summary(self):
        self._add("7-1", "rolled", "m0,m1", "old summary")
        self._add("7-1", "active", "m2,m3", "current summary")
        self._add("7-2", "active", "x0", "other thread")
        self._add("7-1", "active", "z0", "other agent", agent="clerk")
        r = self.invoke(["cycle", "frames", "list", "--thread", "7-1", "--harness"])
        self.assertEqual(r.exit_code, 0, r.output)
        rows = json.loads(r.output)
        self.assertEqual([row["message_ids"] for row in rows], ["m0,m1", "m2,m3"])
        self.assertEqual([row["summary"] for row in rows], ["", "current summary"])

    def test_plain_list_and_search_keep_rolled_summaries(self):
        self._add("7-1", "rolled", "m0", "old summary")
        rows = json.loads(self.invoke(["cycle", "frames", "list", "--thread", "7-1"]).output)
        self.assertEqual(rows[0]["summary"], "old summary")
        found = json.loads(self.invoke(["cycle", "frames", "search", "old"]).output)
        self.assertEqual(len(found), 1)

    def test_harness_read_is_bounded_to_the_newest_frames(self):
        for i in range(agictl_cli.FRAMES_HARNESS_LIMIT + 5):
            self._add("7-1", "rolled", f"m{i}")
        rows = json.loads(self.invoke(["cycle", "frames", "list", "--thread", "7-1", "--harness"]).output)
        self.assertEqual(len(rows), agictl_cli.FRAMES_HARNESS_LIMIT)
        self.assertEqual(rows[-1]["message_ids"], f"m{agictl_cli.FRAMES_HARNESS_LIMIT + 4}")


class TestDrainDeletesFrames(_Dbs):
    """SPWN-49: a drained thread's frames go with it (§1.8 rule 6)."""

    def _add(self, agent, thread, status):
        with sqlite3.connect(self.cycles_db) as conn:
            conn.execute(
                "INSERT INTO frames (agent_name, thread_id, status, frame_start, frame_end, summary, message_ids) "
                "VALUES (?, ?, ?, 't0', 't1', 'text', 'm0')",
                (agent, thread, status),
            )

    def _left(self):
        with sqlite3.connect(self.cycles_db) as conn:
            return sorted(conn.execute("SELECT agent_name, thread_id, status FROM frames").fetchall())

    def _reader(self):
        return AgentReader(self.agents_db, self.cycles_db, "", self.tasks_db)

    def _seed(self):
        self._add("web-dev", "7-1", "rolled")
        self._add("web-dev", "7-1", "active")
        self._add("web-dev", "7-2", "active")
        self._add("clerk", "7-1", "active")

    def test_one_thread_loses_every_status_and_nothing_else_changes(self):
        self._seed()
        self.assertEqual(self._reader().delete_frames("web-dev", "7-1"), 2)
        self.assertEqual(self._left(), [("clerk", "7-1", "active"), ("web-dev", "7-2", "active")])

    def test_blank_thread_deletes_every_frame_of_the_agent_only(self):
        self._seed()
        self.assertEqual(self._reader().delete_frames("web-dev"), 3)
        self.assertEqual(self._left(), [("clerk", "7-1", "active")])

    def test_cycle_rows_are_left_alone(self):
        self._seed()
        self.add_cycle("web-dev-100", _ago(hours=1))
        self._reader().delete_frames("web-dev")
        with sqlite3.connect(self.cycles_db) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM cycles").fetchone()[0], 1)

    def test_no_frames_table_is_zero_and_an_unusable_db_is_none(self):
        with sqlite3.connect(self.cycles_db) as conn:
            conn.execute("DROP TABLE frames")
        self.assertEqual(self._reader().delete_frames("web-dev"), 0)
        missing = os.path.join(self.tempdir.name, "no-such-dir", "cycles.db")
        self.assertIsNone(AgentReader(self.agents_db, missing, "", self.tasks_db).delete_frames("web-dev"))

    def test_cycle_log_drain_keeps_frames(self):
        self._seed()
        self.add_cycle("web-dev-100", _ago(hours=1))
        self.assertTrue(self._reader().drain_all_cycles())
        self.assertEqual(len(self._left()), 4)

    def test_drain_dialog_deletes_frames_after_the_checkpoint(self):
        path = os.path.join(CORE_INFRA, "agitop", "panels", "thread_manager_modal.py")
        with open(path, encoding="utf-8") as fh:
            src = fh.read()
        press = src[src.index("def on_button_pressed", src.index("class DrainConfirmModal")):
                    src.index("class ThreadManagerModal")]
        self.assertLess(press.index("_drain_all_threads("), press.index("_delete_thread_frames("))
        self.assertIn('"" if self.drain_all else self.thread_id', press)
        self.assertIn("if ok else None", press)
        self.assertIn("return reader.delete_frames(agent_name, thread_id)", src)
        # The dialog says what goes, with the number of frames (PU 2026-11-13).
        modal = src[src.index("class DrainConfirmModal"):]
        self.assertIn("drain_confirm_text(", modal)
        self.assertIn("_count_thread_frames(", modal)
        text = src[src.index("def drain_confirm_text"):src.index("def _delete_thread_frames")]
        for needed in ("This will delete:", "summary frame(s)", "deleted for good", "Not affected:"):
            self.assertIn(needed, text)


class TestCycleRowsBelongToOneAgent(_Dbs):
    """SPWN-52: an agent named `web` never reads or counts `web-dev` cycle rows."""

    def _reader(self):
        return AgentReader(self.agents_db, self.cycles_db, "", self.tasks_db)

    def _seed(self):
        # `web-dev` failed three times; `web` ran once, cleanly.
        for i, cid in enumerate(("web-dev-100", "web-dev-200", "web-dev-300")):
            self.add_cycle(cid, _ago(minutes=50 - i), ended=_ago(minutes=45 - i),
                           summary=f"dev {i}", exit_code=1, handoff="Open: dev work")
        self.add_cycle("web-400", _ago(minutes=30), ended=_ago(minutes=25), summary="web ok", exit_code=0,
                       handoff="Open: web work")
        with sqlite3.connect(self.cycles_db) as conn:
            conn.execute("UPDATE cycles SET tokens_total = 1000 WHERE id GLOB 'web-dev-*'")
            conn.execute("UPDATE cycles SET tokens_total = 7 WHERE id = 'web-400'")

    def test_agitop_readers(self):
        self._seed()
        reader = self._reader()
        self.assertEqual(reader.get_agent_cycles_count("web"), 1)
        self.assertEqual(reader.get_agent_cycles_count("web-dev"), 3)
        self.assertEqual(reader.get_agent_monthly_tokens("web"), 7)
        self.assertEqual(reader.get_last_cycle("web")["id"], "web-400")
        self.assertEqual(reader.get_last_cycle("web-dev")["id"], "web-dev-300")

    def test_handoff_block_is_the_agents_own(self):
        self._seed()
        block = self.handoff_block("web")
        self.assertIn("web work", block)
        self.assertNotIn("dev work", block)
        self.assertNotIn("Exit code 1", block)

    def test_cycle_recent_is_the_agents_own(self):
        self._seed()
        with patch.object(agictl_cli, "agent_reader", self._reader()):
            for args in (["cycle", "recent", "web"], ["cycle", "recent", "web", "--json"]):
                r = self.invoke(args, caller="web", agent_user="agi-web")
                self.assertEqual(r.exit_code, 0, r.output)
                self.assertIn("web ok", r.output)
                self.assertNotIn("dev 0", r.output)

    def test_breaker_queries_in_lifeline_match_one_agent(self):
        import subprocess
        self._seed()
        with open(os.path.join(CORE_INFRA, "lifeline.sh"), encoding="utf-8") as fh:
            text = fh.read()
        self.assertNotIn("LIKE '${AGENT_NAME}-%'", text)
        self.assertEqual(text.count("id GLOB '${AGENT_NAME}-[0-9]*'"), 5)
        # Run the breaker's own two counts for `web`: its three neighbours' failures must not count.
        start = text.index("  CB_HOURLY_COUNT=$(sqlite3")
        end = text.index("  # 4. Trip the breaker")
        script = (
            f"AGENT_NAME=web\nCYCLES_DB='{self.cycles_db}'\nCB_CONSECUTIVE=5\n"
            + text[start:end] + '\necho "${CB_HOURLY_COUNT} ${CB_CONSEC_COUNT}"\n'
        )
        out = subprocess.run(["bash", "-c", script], capture_output=True, text=True)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(out.stdout.split(), ["0", "0"])
        out = subprocess.run(["bash", "-c", script.replace("AGENT_NAME=web\n", "AGENT_NAME=web-dev\n")],
                             capture_output=True, text=True)
        self.assertEqual(out.stdout.split(), ["3", "3"])

    def test_no_prefix_match_is_left_in_the_python_readers(self):
        for rel in (("agictl", "cli.py"), ("agictl", "ide_cli.py"), ("agitop", "data", "agent_reader.py")):
            with open(os.path.join(CORE_INFRA, *rel), encoding="utf-8") as fh:
                src = fh.read()
            self.assertNotIn('-%",', src, rel)
            self.assertNotIn("LIKE 'coa-%'", src, rel)
        with open(os.path.join(CORE_INFRA, "agictl", "cli.py"), encoding="utf-8") as fh:
            self.assertIn('"DELETE FROM cycles WHERE id GLOB ? AND exit_code IN (1, 42, 55, 97, 99)"', fh.read())


class TestPurgeAgentCycleRows(_Dbs):
    """SPWN-50: Purge All Logs clears one agent's cycle rows (§1.5 rule 5)."""

    def _reader(self):
        return AgentReader(self.agents_db, self.cycles_db, "", self.tasks_db)

    def _ids(self):
        with sqlite3.connect(self.cycles_db) as conn:
            return sorted(r[0] for r in conn.execute("SELECT id FROM cycles"))

    def _status(self, agent, status):
        with sqlite3.connect(self.agents_db) as conn:
            try:
                conn.execute("ALTER TABLE agents ADD COLUMN status TEXT")
            except sqlite3.OperationalError:
                pass
            conn.execute("UPDATE agents SET status=? WHERE name=?", (status, agent))

    def _seed(self):
        self._status("web-dev", "idle")
        self.add_cycle("web-dev-100", _ago(hours=3), ended=_ago(hours=2))
        self.add_cycle("web-dev-200", _ago(hours=2), summary=None, exit_code=124)   # never closed
        self.add_cycle("web-dev-300", _ago(minutes=10), summary=None)
        self.add_cycle("web-400", _ago(hours=1), ended=_ago(minutes=50))            # another agent
        self.add_cycle("clerk-500", _ago(hours=1), ended=_ago(minutes=50))

    def test_only_this_agents_rows_go(self):
        self._seed()
        self.assertEqual(self._reader().delete_agent_cycles("web-dev"), 3)
        self.assertEqual(self._ids(), ["clerk-500", "web-400"])

    def test_a_name_that_starts_the_same_is_left_alone(self):
        self._seed()
        self.assertEqual(self._reader().delete_agent_cycles("web"), 1)
        self.assertEqual(self._ids(), ["clerk-500", "web-dev-100", "web-dev-200", "web-dev-300"])

    def test_a_running_cycle_keeps_its_row(self):
        self._seed()
        self._status("web-dev", "active")
        self.assertEqual(self._reader().delete_agent_cycles("web-dev"), 2)
        self.assertEqual(self._ids(), ["clerk-500", "web-400", "web-dev-300"])

    def test_frames_are_not_touched(self):
        self._seed()
        with sqlite3.connect(self.cycles_db) as conn:
            conn.execute(
                "INSERT INTO frames (agent_name, thread_id, status, frame_start, frame_end, summary, message_ids) "
                "VALUES ('web-dev', '7-1', 'active', 't0', 't1', 'text', 'm0')")
        self._reader().delete_agent_cycles("web-dev")
        with sqlite3.connect(self.cycles_db) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM frames").fetchone()[0], 1)

    def test_unusable_db_is_none(self):
        missing = os.path.join(self.tempdir.name, "no-such-dir", "cycles.db")
        self.assertIsNone(AgentReader(self.agents_db, missing, "", self.tasks_db).delete_agent_cycles("web-dev"))

    def test_purge_dialog_deletes_rows_and_says_so(self):
        path = os.path.join(CORE_INFRA, "agitop", "panels", "cycle_log_modal.py")
        with open(path, encoding="utf-8") as fh:
            src = fh.read()
        modal = src[src.index("class PurgeLogsConfirmModal"):]
        self.assertIn("purge_agent_cycle_rows(getattr(self.app, \"agent_reader\", None), self.agent_name)", modal)
        self.assertIn("return reader.delete_agent_cycles(agent_name)", src)
        # The dialog shows the numbers before anything is deleted (PU 2026-11-13).
        self.assertIn("purge_confirm_text(self.agent_name, cycles_dir, files, rows)", modal)
        self.assertIn("files = len(_list_purgeable_cycle_logs(cycles_dir))", modal)
        self.assertIn("reader.get_agent_cycles_count(self.agent_name)", modal)
        text = src[src.index("def purge_confirm_text"):src.index("def purge_agent_cycle_rows")]
        for needed in ("This will delete:", "cycle log file(s)", "cycle record(s)", "summaries, handoffs, token counts",
                       "Not affected:", "summary frames, tasks", "every other agent"):
            self.assertIn(needed, text)


class TestLongCycleRows(_Dbs):
    """state_long_cycles.md §1.2, §1.6, §1.8: what lands on the cycle row and in the next wake."""

    def _row(self, cid):
        with sqlite3.connect(self.cycles_db) as conn:
            conn.row_factory = sqlite3.Row
            return dict(conn.execute("SELECT * FROM cycles WHERE id=?", (cid,)).fetchone())

    def test_checkin_lines_append_to_the_named_row(self):
        self.add_cycle("web-dev-100", _ago(hours=3), summary=None, exit_code=124)
        self.add_cycle("web-dev-300", _ago(minutes=65), summary=None)
        for line in ("12:00 | 30 min | 20 steps", "12:30 | 60 min | 41 steps"):
            r = self.invoke(["cycle", "checkin", line, "--cycle-id", "web-dev-300"])
            self.assertEqual(r.exit_code, 0, r.output)
        self.assertEqual(self._row("web-dev-300")["checkins"],
                         "12:00 | 30 min | 20 steps\n12:30 | 60 min | 41 steps")
        self.assertIsNone(self._row("web-dev-100")["checkins"])

    def test_checkin_without_an_id_goes_to_the_open_row(self):
        self.add_cycle("web-dev-300", _ago(minutes=35), summary=None)
        r = self.invoke(["cycle", "checkin", "12:00 | 30 min"])
        self.assertEqual(r.exit_code, 0, r.output)
        self.assertEqual(self._row("web-dev-300")["checkins"], "12:00 | 30 min")

    def test_checkin_adds_the_column_when_the_update_was_skipped(self):
        with sqlite3.connect(self.cycles_db) as conn:
            conn.execute("ALTER TABLE cycles DROP COLUMN checkins")
        self.add_cycle("web-dev-300", _ago(minutes=35), summary=None)
        r = self.invoke(["cycle", "checkin", "12:00 | 30 min", "--cycle-id", "web-dev-300"])
        self.assertEqual(r.exit_code, 0, r.output)
        self.assertEqual(self._row("web-dev-300")["checkins"], "12:00 | 30 min")

    def test_checkin_refuses_an_unknown_row_or_an_empty_line(self):
        self.assertEqual(self.invoke(["cycle", "checkin", "x", "--cycle-id", "web-dev-999"]).exit_code, 1)
        self.assertEqual(self.invoke(["cycle", "checkin", "  ", "--cycle-id", "web-dev-999"]).exit_code, 1)

    def test_a_stall_closes_the_row_with_its_own_note(self):
        self.add_cycle("web-dev-300", _ago(minutes=20), summary=None)
        AgentReader(self.agents_db, self.cycles_db, "", self.tasks_db).update_last_cycle_tokens(
            "web-dev", 0, 0, 0, 0, exit_code=97, cycle_id="web-dev-300", outcome="stall",
        )
        row = self._row("web-dev-300")
        self.assertEqual(row["exit_code"], 97)
        self.assertIsNotNone(row["ended_at"])
        self.assertIn("stall timer", row["summary"])
        r = self.invoke(["cycle", "tokens", "web-dev", "0", "0", "0", "0", "--outcome", "stall"])
        self.assertEqual(r.exit_code, 0, r.output)

    def test_next_wake_says_why_the_last_cycle_was_stopped(self):
        for code, words in ((54, "reached its time limit and the harness closed it"),
                            (55, "same tool call was repeated"),
                            (97, "stall timer"),
                            (124, "stopped at the time limit before it could be closed")):
            with sqlite3.connect(self.cycles_db) as conn:
                conn.execute("DELETE FROM cycles")
            self.add_cycle("web-dev-300", _ago(minutes=50), ended=_ago(minutes=5), exit_code=code)
            block = self.handoff_block()
            self.assertIn(f"Exit code {code}.", block)
            self.assertIn(words, block, code)

    def test_activate_clears_loop_and_stall_rows_with_the_other_breaker_codes(self):
        with open(agictl_cli.__file__, encoding="utf-8") as fh:
            self.assertIn("exit_code IN (1, 42, 55, 97, 99)", fh.read())


class TestLifelineTokenWiring(unittest.TestCase):
    """SPWN-47: Lifeline reads tokens only from this spawn's telemetry."""

    def setUp(self):
        with open(os.path.join(CORE_INFRA, "lifeline.sh"), encoding="utf-8") as handle:
            self.text = handle.read()

    def test_telemetry_is_matched_to_the_cycle_id(self):
        self.assertIn("jq -r '.cycle_id // empty' \"${TELEMETRY_FILE}\"", self.text)
        self.assertIn('[ "${TELEMETRY_CYCLE_ID}" = "${CURRENT_CYCLE_ID}" ]', self.text)
        self.assertIn("telemetry is from another cycle", self.text)

    def test_cycle_id_and_outcome_reach_cycle_tokens(self):
        self.assertIn('CYCLE_ROW_ARGS=(--cycle-id "${CURRENT_CYCLE_ID}" --outcome "${ACTIVITY_OUTCOME}")', self.text)
        self.assertIn('"${CYCLE_ROW_ARGS[@]}"', self.text)

    def test_stale_file_yields_zero_tokens(self):
        """Run the Lifeline snippet itself against a telemetry file from an earlier cycle."""
        import subprocess
        start = self.text.index("  # The file is one per agent and survives a killed cycle.")
        end = self.text.index("  # Compute total if we have parts but no explicit total")
        snippet = self.text[start:end]
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "cycle_telemetry.json")
            for file_id, expected in (("researcher-OLD", "0"), ("researcher-NEW", "99433")):
                with open(path, "w", encoding="utf-8") as handle:
                    json.dump({"cycle_id": file_id, "total_tokens": 99433,
                               "prompt_tokens": 94006, "completion_tokens": 5427}, handle)
                script = (
                    "set -euo pipefail\nlog() { :; }\n"
                    f"TELEMETRY_FILE='{path}'\nCURRENT_CYCLE_ID='researcher-NEW'\nAGENT_NAME=researcher\n"
                    "TOKENS_IN=0; TOKENS_OUT=0; TOKENS_TOTAL=0\n" + snippet + "\necho \"${TOKENS_TOTAL}\"\n"
                )
                out = subprocess.run(["bash", "-c", script], capture_output=True, text=True)
                self.assertEqual(out.returncode, 0, out.stderr)
                self.assertEqual(out.stdout.strip(), expected, file_id)


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
