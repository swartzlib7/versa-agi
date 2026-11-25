"""IDE mode: one command for "is there anything to do", and a loop the system counts.

state_coa_ide_mode.md rules 24 to 27.
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

CORE_INFRA = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SRC = os.path.dirname(CORE_INFRA)
sys.path.insert(0, CORE_INFRA)

from agictl import ide_loop  # noqa: E402

try:
    from click.testing import CliRunner
    sys.path.insert(0, os.path.join(CORE_INFRA, "agictl"))
    import agictl.cli as agictl_cli  # noqa: E402
    from agictl import ide_cli  # noqa: E402
except ImportError:  # the harness venv has no click; these run under /opt/versa-agi/venv
    agictl_cli = None

NOW = datetime(2026, 11, 16, 14, 0, 0, tzinfo=timezone.utc)


def _source(*parts: str) -> str:
    with open(os.path.join(*parts), encoding="utf-8") as handle:
        return handle.read()


class TestTerms(unittest.TestCase):
    def test_a_loop_needs_an_ending(self):
        self.assertIn("how the loop ends", ide_loop.terms_error(15, None, None))
        self.assertEqual(ide_loop.terms_error(15, 8, None), "")
        self.assertEqual(ide_loop.terms_error(15, None, 4), "")
        self.assertEqual(ide_loop.terms_error(15, 8, 4), "")

    def test_ranges(self):
        for every in (4, 121, 0):
            self.assertIn("--every", ide_loop.terms_error(every, 8, None), every)
        for hours in (0, -1, 24.5):
            self.assertIn("--hours", ide_loop.terms_error(15, hours, None), hours)
        self.assertIn("--idle-ticks", ide_loop.terms_error(15, None, 0))
        self.assertEqual(ide_loop.terms_error(5, 24, None), "")
        self.assertEqual(ide_loop.terms_error(120, 0.5, 1), "")

    def test_a_working_day(self):
        loop = ide_loop.new_loop(15, 8, None, NOW)
        self.assertEqual(loop["every_minutes"], 15)
        self.assertEqual(loop["ends_at"], "2026-11-16T22:00:00Z")
        self.assertEqual((loop["ticks"], loop["idle_in_a_row"], loop["max_idle"]), (0, 0, 0))
        seen = ide_loop.view(loop, NOW + timedelta(hours=1))
        self.assertEqual((seen["armed"], seen["minutes_left"], seen["idle_limit"]), (True, 420, None))
        self.assertEqual(ide_loop.view(None, NOW), {"armed": False})


class TestTick(unittest.TestCase):
    def loop(self, **terms):
        return ide_loop.new_loop(15, terms.get("hours"), terms.get("idle_ticks"), NOW,
                                 at_end=terms.get("at_end", ide_loop.AT_END_STAY))

    def test_mode_off_stops_and_forgets_the_loop(self):
        answer, after = ide_loop.decide(self.loop(hours=8), False, 3, 2, NOW)
        self.assertEqual(answer["do"], ide_loop.DO_STOP)
        self.assertIn("IDE mode is off", answer["say"])
        self.assertIsNone(after)

    def test_nothing_waiting_is_an_idle_tick(self):
        answer, after = ide_loop.decide(self.loop(hours=8), True, 0, 0, NOW + timedelta(minutes=15))
        self.assertEqual(answer["do"], ide_loop.DO_IDLE)
        self.assertIn("End this turn", answer["say"])
        self.assertEqual((after["ticks"], after["idle_in_a_row"]), (1, 1))

    def test_a_message_or_a_due_task_is_work_and_starts_the_idle_count_again(self):
        loop = self.loop(idle_ticks=4)
        loop["idle_in_a_row"] = 3
        for unread, due, words in ((2, 0, "2 unread messages"), (0, 1, "1 task due"),
                                   (1, 3, "1 unread message and 3 tasks due")):
            answer, after = ide_loop.decide(dict(loop), True, unread, due, NOW + timedelta(minutes=15))
            self.assertEqual(answer["do"], ide_loop.DO_WORK)
            self.assertIn(words, answer["say"].lower())
            self.assertEqual(after["idle_in_a_row"], 0)
            self.assertNotIn("last", answer)

    def test_the_idle_limit_ends_the_loop(self):
        loop = self.loop(idle_ticks=3)
        at = NOW
        for expected in (ide_loop.DO_IDLE, ide_loop.DO_IDLE, ide_loop.DO_STOP):
            at += timedelta(minutes=15)
            answer, loop = ide_loop.decide(loop, True, 0, 0, at)
            self.assertEqual(answer["do"], expected)
        self.assertIsNone(loop)
        self.assertIn("agreed limit", answer["say"])

    def test_a_fixed_length_runs_through_quiet_hours_and_ends_on_time(self):
        """The Primary User's case: every 15 minutes for 8 hours, however quiet."""
        loop = self.loop(hours=8)
        at, answers = NOW, []
        for _ in range(40):
            at += timedelta(minutes=15)
            answer, loop = ide_loop.decide(loop, True, 0, 0, at)
            answers.append(answer["do"])
            if loop is None:
                break
        self.assertEqual(answers.count(ide_loop.DO_IDLE), 31)
        self.assertEqual(answers[-1], ide_loop.DO_STOP)
        self.assertEqual(len(answers), 32)          # the tick at 8 hours is the stop

    def test_work_on_the_last_tick_is_done_before_the_loop_ends(self):
        answer, after = ide_loop.decide(self.loop(hours=8), True, 1, 0, NOW + timedelta(hours=8))
        self.assertEqual(answer["do"], ide_loop.DO_WORK)
        self.assertTrue(answer["last"])
        self.assertIn("disarm the loop", answer["say"])
        self.assertIn("IDE mode stays on", answer["say"])
        self.assertIsNone(after)

    def test_both_endings_whichever_comes_first(self):
        loop = self.loop(hours=8, idle_ticks=2)
        _, loop = ide_loop.decide(loop, True, 0, 0, NOW + timedelta(minutes=15))
        answer, loop = ide_loop.decide(loop, True, 0, 0, NOW + timedelta(minutes=30))
        self.assertEqual(answer["do"], ide_loop.DO_STOP)
        self.assertIsNone(loop)

    def test_a_message_from_the_primary_user_is_not_a_tick(self):
        loop = self.loop(idle_ticks=4)
        loop["ticks"], loop["idle_in_a_row"] = 5, 3
        answer, after = ide_loop.decide(loop, True, 0, 1, NOW + timedelta(minutes=80), on_message=True)
        self.assertEqual(answer["do"], ide_loop.DO_USER)
        self.assertIn("Also waiting: 1 task due", answer["say"])
        self.assertEqual((after["ticks"], after["idle_in_a_row"]), (5, 0))
        self.assertEqual(after["last_tick_at"], "2026-11-16T15:20:00Z")   # the chat is alive

    def test_without_a_loop_it_is_a_single_check(self):
        answer, after = ide_loop.decide(None, True, 0, 0, NOW)
        self.assertEqual((answer["do"], after), (ide_loop.DO_IDLE, None))
        answer, after = ide_loop.decide(None, True, 1, 0, NOW)
        self.assertEqual((answer["do"], after), (ide_loop.DO_WORK, None))
        self.assertIn("single check", answer["say"])



class TestEndOfALoop(unittest.TestCase):
    """Rule 28: a loop that runs out closes the session, unless it was told to stay."""

    def loop(self, **terms):
        return ide_loop.new_loop(15, terms.get("hours"), terms.get("idle_ticks"), NOW, **(
            {"at_end": terms["at_end"]} if "at_end" in terms else {}))

    def test_closing_the_session_is_the_default(self):
        self.assertEqual(self.loop(hours=8)["at_end"], ide_loop.AT_END_OFF)
        self.assertEqual(ide_loop.new_loop(15, 8, None, NOW, at_end="nonsense")["at_end"], ide_loop.AT_END_OFF)

    def test_time_up_with_nothing_waiting_closes_the_session(self):
        answer, after = ide_loop.decide(self.loop(hours=8), True, 0, 0, NOW + timedelta(hours=8))
        self.assertEqual(answer["do"], ide_loop.DO_CLOSE)
        for part in ("without asking", "close-out in AGENTS.md", "send the Primary User one message",
                     "no closing notes to anyone else", "agictl agent ide off coa", "disarm the loop"):
            self.assertIn(part, answer["say"])
        self.assertEqual(after["ended_at"], "2026-11-16T22:00:00Z")      # kept until `ide off`
        self.assertEqual(ide_loop.view(after, NOW + timedelta(hours=8))["armed"], False)

    def test_the_idle_limit_closes_it_too(self):
        loop = self.loop(idle_ticks=2)
        _, loop = ide_loop.decide(loop, True, 0, 0, NOW + timedelta(minutes=15))
        answer, loop = ide_loop.decide(loop, True, 0, 0, NOW + timedelta(minutes=30))
        self.assertEqual(answer["do"], ide_loop.DO_CLOSE)
        self.assertIn("quiet tick 2 in a row", answer["say"])
        self.assertTrue(loop["ended_at"])

    def test_work_on_the_last_tick_comes_first_then_the_close(self):
        answer, after = ide_loop.decide(self.loop(hours=8), True, 2, 1, NOW + timedelta(hours=8))
        self.assertEqual((answer["do"], answer["last"]), (ide_loop.DO_WORK, True))
        self.assertIn("When the work is done", answer["say"])
        self.assertIn("agictl agent ide off coa", answer["say"])
        self.assertTrue(after["ended_at"])

    def test_a_wake_after_the_end_asks_for_the_close_again_and_counts_nothing(self):
        _, ended = ide_loop.decide(self.loop(hours=8), True, 0, 0, NOW + timedelta(hours=8))
        answer, after = ide_loop.decide(ended, True, 3, 0, NOW + timedelta(hours=8, minutes=15))
        self.assertEqual(answer["do"], ide_loop.DO_CLOSE)
        self.assertIn("already ended", answer["say"])
        self.assertEqual(after["ticks"], ended["ticks"])

    def test_the_primary_user_writing_after_the_end_is_followed(self):
        _, ended = ide_loop.decide(self.loop(hours=8), True, 0, 0, NOW + timedelta(hours=8))
        answer, _after = ide_loop.decide(ended, True, 0, 0, NOW + timedelta(hours=8, minutes=5), on_message=True)
        self.assertEqual(answer["do"], ide_loop.DO_USER)
        self.assertIn("unless they say otherwise", answer["say"])

    def test_stay_ends_only_the_loop(self):
        answer, after = ide_loop.decide(self.loop(hours=8, at_end="stay"), True, 0, 0, NOW + timedelta(hours=8))
        self.assertEqual(answer["do"], ide_loop.DO_STOP)
        self.assertIn("IDE mode stays on", answer["say"])
        self.assertIsNone(after)

    def test_mode_off_is_never_a_close(self):
        answer, after = ide_loop.decide(self.loop(hours=8), False, 0, 0, NOW + timedelta(hours=9))
        self.assertEqual((answer["do"], after), (ide_loop.DO_STOP, None))


class TestEndedLoopLeftOn(unittest.TestCase):
    """Rule 29: Lifeline says so when an ended loop did not turn the mode off. It does not turn it off."""

    def ended(self):
        loop = ide_loop.new_loop(15, 8, None, NOW)
        _, loop = ide_loop.decide(loop, True, 0, 0, NOW + timedelta(hours=8))
        return loop

    def test_forty_five_minutes_of_grace_for_the_close_out(self):
        loop, end = self.ended(), NOW + timedelta(hours=8)
        self.assertEqual(ide_loop.end_overdue_minutes(loop, end + timedelta(minutes=45)), 0)
        self.assertEqual(ide_loop.end_overdue_minutes(loop, end + timedelta(minutes=46)), 46)
        self.assertEqual(ide_loop.silent_minutes(loop, end + timedelta(hours=3)), 0)   # not the silent kind

    def test_a_chat_still_heard_from_is_left_alone(self):
        loop, end = self.ended(), NOW + timedelta(hours=8)
        _, loop = ide_loop.decide(loop, True, 0, 0, end + timedelta(minutes=40), on_message=True)
        self.assertEqual(ide_loop.end_overdue_minutes(loop, end + timedelta(minutes=60)), 0)
        self.assertEqual(ide_loop.end_overdue_minutes(loop, end + timedelta(minutes=86)), 86)

    def test_an_end_time_that_passed_with_no_last_tick_counts_too(self):
        loop = ide_loop.new_loop(15, 8, None, NOW)      # the chat died early: no tick ever came
        self.assertEqual(ide_loop.end_overdue_minutes(loop, NOW + timedelta(hours=8, minutes=45)), 0)
        self.assertEqual(ide_loop.end_overdue_minutes(loop, NOW + timedelta(hours=8, minutes=50)), 50)

    def test_a_loop_told_to_stay_or_still_running_is_never_overdue(self):
        self.assertEqual(ide_loop.end_overdue_minutes(ide_loop.new_loop(15, 8, None, NOW, at_end="stay"),
                                                      NOW + timedelta(hours=12)), 0)
        self.assertEqual(ide_loop.end_overdue_minutes(ide_loop.new_loop(15, 8, None, NOW), NOW + timedelta(hours=7)), 0)
        self.assertEqual(ide_loop.end_overdue_minutes(ide_loop.new_loop(15, None, 4, NOW), NOW + timedelta(hours=30)), 0)
        self.assertEqual(ide_loop.end_overdue_minutes(None, NOW), 0)

    def test_the_text_says_what_happened_and_what_to_do(self):
        text = ide_loop.end_overdue_text(self.ended(), 50, 1, 0)
        for part in ("was to turn IDE mode off", "still on 50 minutes later", "1 unread message",
                     "Turn IDE mode off", "may not be written to her memory"):
            self.assertIn(part, text)


class TestWhatIsWaiting(unittest.TestCase):
    def test_messages_are_counted_per_sender_without_their_text(self):
        rows = [
            {"display_name": "Steven", "from_user_id": "u1", "created_at": "2026-11-16 13:00:00", "text": "private"},
            {"display_name": "Steven", "from_user_id": "u1", "created_at": "2026-11-16 13:40:00", "text": "private"},
            {"display_name": "", "from_user_id": "researcher", "created_at": "2026-11-16 13:50:00", "text": "x"},
        ]
        seen = ide_loop.summarize_messages(rows)
        self.assertEqual(seen["unread"], 3)
        self.assertEqual([(s["name"], s["count"]) for s in seen["from"]], [("researcher", 1), ("Steven", 2)])
        self.assertNotIn("private", json.dumps(seen))
        self.assertEqual(ide_loop.summarize_messages([]), {"unread": 0, "from": []})

    def test_due_tasks_show_what_is_needed_to_start(self):
        seen = ide_loop.summarize_tasks([
            {"id": 7, "title": "Send report", "status": "planned", "priority": "high",
             "due_date": "2026-11-16 13:00:00", "description": "long text"},
        ])
        self.assertEqual(set(seen[0]), {"id", "title", "status", "priority", "due"})
        self.assertTrue(seen[0]["due"].startswith("2026-11-16"))


class TestSilentLoop(unittest.TestCase):
    def test_silent_after_two_missed_ticks(self):
        loop = ide_loop.new_loop(15, 8, None, NOW)
        self.assertEqual(ide_loop.silent_minutes(loop, NOW + timedelta(minutes=30)), 0)
        self.assertEqual(ide_loop.silent_minutes(loop, NOW + timedelta(minutes=31)), 0)   # the grace minute
        self.assertEqual(ide_loop.silent_minutes(loop, NOW + timedelta(minutes=32)), 32)
        self.assertEqual(ide_loop.silent_minutes(None, NOW), 0)

    def test_the_primary_user_is_told_what_waits_and_the_two_ways_out(self):
        loop = ide_loop.new_loop(15, 8, None, NOW)
        text = ide_loop.silence_text(loop, 34, 2, 1)
        for part in ("silent for 34 minutes", "every 15", "2 unread messages and 1 task due",
                     "Open the IDE chat again", "turn IDE mode off", "Sub-agents are not affected"):
            self.assertIn(part, text)
        self.assertIn("Nothing is waiting", ide_loop.silence_text(loop, 34, 0, 0))


class TestPendingRule(unittest.TestCase):
    """The tick lists exactly the tasks Lifeline would wake the agent for."""

    def test_pending_for_and_count_pending_agree(self):
        sys.path.insert(0, os.path.join(CORE_INFRA, "agitop"))
        from data.tasks_reader import TasksReader
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "tasks.db")
            with sqlite3.connect(db) as conn:
                conn.execute("CREATE TABLE tasks (id INTEGER PRIMARY KEY, title TEXT, status TEXT, priority TEXT, "
                             "due_date DATETIME, wake_after DATETIME, task_kind TEXT, assigned_to TEXT)")
                conn.executemany(
                    "INSERT INTO tasks (title, status, priority, due_date, wake_after, task_kind, assigned_to) "
                    "VALUES (?, ?, ?, datetime('now', ?), ?, ?, ?)",
                    [("due, mine", "planned", "normal", "-1 hour", None, "standard", "coa"),
                     ("due, urgent, unassigned", "waiting", "urgent", "-5 minutes", None, None, None),
                     ("running", "in_progress", "low", "+1 day", None, "standard", "coa"),
                     ("later", "planned", "high", "+2 hours", None, "standard", "coa"),
                     ("soonest later", "waiting", "normal", "+20 minutes", None, "standard", "coa"),
                     ("someone else's", "planned", "high", "-1 hour", None, "standard", "researcher"),
                     ("a script", "planned", "high", "-1 hour", None, "script", "coa"),
                     ("frozen", "frozen", "high", "-1 hour", None, "standard", "coa")])
            reader = TasksReader(db)
            pending = reader.pending_for("coa")
            self.assertEqual(len(pending), reader.count_pending("coa"))
            self.assertEqual([t["title"] for t in pending], ["due, urgent, unassigned", "due, mine", "running"])
            self.assertEqual(reader.next_due_for("coa")["title"], "soonest later")
            self.assertEqual(reader.count_pending("researcher"), len(reader.pending_for("researcher")))


@unittest.skipIf(agictl_cli is None, "click not installed in this interpreter")
class TestCommands(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.state = root / "ide_state.json"
        self.seed = root / "versa-agi_ide.md"
        self.seed.write_text("seed", encoding="utf-8")
        self.status = "ide"
        self.waiting = {"messages": [], "tasks": [], "next_task": None}
        self.patches = [
            patch.object(ide_cli, "IDE_STATE_PATH", self.state),
            patch.object(ide_cli, "IDE_FILE", self.seed),
            patch.object(ide_cli, "_agent_row", lambda name: {"name": name, "status": self.status,
                                                               "status_message": "", "workspace": ""}),
            patch.object(agictl_cli, "_ide_waiting", lambda name: self.waiting),
            patch.object(ide_cli.os, "chown", lambda *a, **k: None),
        ]
        for item in self.patches:
            item.start()

    def tearDown(self):
        for item in self.patches:
            item.stop()
        self.tmp.cleanup()

    def run_cli(self, *args, caller="coa"):
        with patch.dict(os.environ, {"AGICTL_AGENT_USER": caller}):
            result = CliRunner().invoke(agictl_cli.cli, ["agent", "ide", *args])
        try:
            return result.exit_code, json.loads(result.output)
        except json.JSONDecodeError:
            self.fail(f"not JSON: {result.output!r} ({result.exception!r})")

    def stored_loop(self):
        return json.loads(self.state.read_text(encoding="utf-8")).get("loop") if self.state.exists() else None

    def test_coa_arms_a_working_day_and_is_told_the_prompt(self):
        code, out = self.run_cli("loop", "start", "--every", "15", "--hours", "8")
        self.assertEqual(code, 0, out)
        self.assertEqual((out["loop"]["armed"], out["loop"]["every_minutes"], out["loop"]["tick"]), (True, 15, 0))
        self.assertIn("agictl agent ide tick coa", out["next"])
        self.assertEqual(self.stored_loop()["every_minutes"], 15)

    def test_terms_are_checked_and_the_mode_must_be_on(self):
        code, out = self.run_cli("loop", "start", "--every", "15")
        self.assertEqual(code, 1)
        self.assertIn("how the loop ends", out["error"])
        self.status = "idle"
        code, out = self.run_cli("loop", "start", "--every", "15", "--hours", "8")
        self.assertEqual(code, 1)
        self.assertIn("IDE mode is off", out["error"])

    def test_a_tick_answers_in_one_call(self):
        self.run_cli("loop", "start", "--every", "15", "--idle-ticks", "2")
        code, out = self.run_cli("tick")
        self.assertEqual((code, out["mode"], out["do"]), (0, "on", "idle"))
        self.assertEqual((out["loop"]["tick"], out["loop"]["idle_in_a_row"], out["loop"]["idle_limit"]), (1, 1, 2))
        self.waiting = {
            "messages": [{"display_name": "Steven", "created_at": "2026-11-16 13:00:00", "text": "hi"}],
            "tasks": [{"id": 9, "title": "Send report", "status": "planned", "priority": "high",
                       "due_date": "2026-11-16 13:00:00"}],
            "next_task": {"id": 12, "title": "Later", "due_date": "2026-11-17 09:00:00"},
        }
        code, out = self.run_cli("tick")
        self.assertEqual(out["do"], "work")
        self.assertEqual(out["messages"]["unread"], 1)
        self.assertEqual(out["tasks_due"][0]["id"], 9)
        self.assertEqual(out["next_task_due"]["id"], 12)
        self.assertEqual(out["loop"]["idle_in_a_row"], 0)

    def test_a_loop_told_to_stay_ends_itself_and_is_forgotten(self):
        self.run_cli("loop", "start", "--every", "15", "--idle-ticks", "2", "--at-end", "stay")
        self.assertEqual(self.run_cli("tick")[1]["do"], "idle")
        code, out = self.run_cli("tick")
        self.assertEqual(out["do"], "stop")
        self.assertEqual(out["loop"], {"armed": False})
        self.assertIsNone(self.stored_loop())

    def test_by_default_the_loop_closes_the_session_and_names_the_one_message(self):
        code, out = self.run_cli("loop", "start", "--every", "15", "--idle-ticks", "1")
        self.assertIn("turn IDE mode off", out["at_end"])
        self.assertEqual(out["loop"]["at_end"], "off")
        self.waiting = {"messages": [], "tasks": [], "next_task": None, "pu_uid": "pu-uid-1"}
        code, out = self.run_cli("tick")
        self.assertEqual(out["do"], "close")
        self.assertIn("agictl message send pu-uid-1", out["tell_primary_user"])
        self.assertEqual(out["loop"]["armed"], False)
        self.assertTrue(self.stored_loop()["ended_at"])          # kept for Lifeline until `ide off`

    def test_lifeline_is_told_once_when_the_ended_loop_left_the_mode_on(self):
        self.run_cli("loop", "start", "--every", "15", "--idle-ticks", "1")
        self.run_cli("tick")
        self.assertFalse(self.run_cli("loop-watch", caller="")[1]["alert"])     # within the grace time
        state = json.loads(self.state.read_text(encoding="utf-8"))
        state["loop"]["ended_at"] = state["loop"]["last_tick_at"] = "2026-01-01T00:00:00Z"
        self.state.write_text(json.dumps(state), encoding="utf-8")
        code, out = self.run_cli("loop-watch", caller="")
        self.assertEqual((out["alert"], out["kind"]), (True, "end_overdue"))
        self.assertIn("was to turn IDE mode off", out["text"])
        self.assertFalse(self.run_cli("loop-watch", caller="")[1]["alert"])

    def test_mode_off_stops_without_reading_anything(self):
        self.run_cli("loop", "start", "--every", "15", "--hours", "8")
        self.status = "idle"
        self.waiting = {"messages": [{"display_name": "x", "created_at": ""}], "tasks": [], "next_task": None}
        code, out = self.run_cli("tick")
        self.assertEqual((out["mode"], out["do"], out["messages"]["unread"]), ("off", "stop", 0))
        self.assertIsNone(self.stored_loop())

    def test_a_missing_seed_counts_as_off(self):
        self.seed.unlink()
        self.assertEqual(self.run_cli("tick")[1]["mode"], "off")

    def test_on_message_is_not_counted(self):
        self.run_cli("loop", "start", "--every", "15", "--idle-ticks", "3")
        self.run_cli("tick")
        code, out = self.run_cli("tick", "--on-message")
        self.assertEqual(out["do"], "user")
        self.assertEqual((out["loop"]["tick"], out["loop"]["idle_in_a_row"]), (1, 0))

    def test_loop_stop_and_status(self):
        self.run_cli("loop", "start", "--every", "15", "--hours", "8")
        with patch.object(ide_cli, "detect_host_class", return_value={"host_class": "native", "host_virt": ""}), \
                patch.object(ide_cli, "_autonomous_gap", return_value={"count": 0, "banner": ""}):
            self.assertTrue(self.run_cli("status")[1]["loop"]["armed"])
        code, out = self.run_cli("loop", "stop")
        self.assertEqual((out["was_armed"], out["loop"]), (True, {"armed": False}))
        self.assertIsNone(self.stored_loop())

    def test_a_session_turned_on_again_is_said_once(self):
        """The seed is read once per session: a chat that outlives an off and on is told to read it again."""
        with patch.object(ide_cli, "_autonomous_gap",
                          return_value={"count": 2, "banner": "COA ran 2 Lifeline cycle(s) since the last IDE session."}):
            self.state.write_text(json.dumps({"last_on": "2026-11-16T09:00:00Z", "session_seen": "2026-11-15T09:00:00Z"}),
                                  encoding="utf-8")
            code, out = self.run_cli("tick", "--on-message")
            self.assertIn("Read `.agent/versa-agi_ide.md` again, once", out["notice"])
            self.assertIn("2 Lifeline cycle(s)", out["notice"])
            self.assertNotIn("notice", self.run_cli("tick", "--on-message")[1])

    def test_a_new_chat_hears_it_from_status_not_again_from_the_tick(self):
        self.state.write_text(json.dumps({"last_on": "2026-11-16T09:00:00Z"}), encoding="utf-8")
        with patch.object(ide_cli, "detect_host_class", return_value={"host_class": "native", "host_virt": ""}), \
                patch.object(ide_cli, "_autonomous_gap", return_value={"count": 0, "banner": ""}):
            self.run_cli("status")
            self.assertNotIn("notice", self.run_cli("tick")[1])

    def test_status_and_tick_name_the_installed_rules_version(self):
        door = Path(self.tmp.name) / "AGENTS.md"
        door.write_text("# door\n\nRules version: `ab12cd34`. rest\n", encoding="utf-8")
        with patch.object(ide_cli, "AGENTS_DOOR", door), \
                patch.object(ide_cli, "detect_host_class", return_value={"host_class": "native", "host_virt": ""}), \
                patch.object(ide_cli, "_autonomous_gap", return_value={"count": 0, "banner": ""}):
            code, out = self.run_cli("status")
            self.assertEqual(out["rules_version"], "ab12cd34")
            self.assertIn("Rules version ab12cd34", out["message"])
            self.assertIn("old copy", out["message"])
            self.assertEqual(self.run_cli("tick")[1]["rules_version"], "ab12cd34")
            self.status = "idle"
            code, out = self.run_cli("status")
            self.assertNotIn("rules_version", out)
            self.assertNotIn("Rules version", out["message"])

    def test_sub_agents_are_refused(self):
        for args in (("tick",), ("loop", "start", "--every", "15", "--hours", "8"), ("loop", "stop")):
            code, out = self.run_cli(*args, caller="agi-web-dev")
            self.assertEqual(code, 1, args)
            self.assertIn("Primary User or COA only", out["error"])

    def test_the_watch_is_lifelines_and_warns_once_per_silence(self):
        code, out = self.run_cli("loop-watch", caller="coa")
        self.assertEqual(code, 1)
        self.assertIn("Lifeline only", out["error"])
        self.run_cli("loop", "start", "--every", "15", "--hours", "8")
        self.assertFalse(self.run_cli("loop-watch", caller="")[1]["alert"])     # just armed
        state = json.loads(self.state.read_text(encoding="utf-8"))
        state["loop"]["last_tick_at"] = (datetime.now(timezone.utc) - timedelta(minutes=40)).strftime(
            "%Y-%m-%dT%H:%M:%SZ")                                               # two ticks missed
        self.state.write_text(json.dumps(state), encoding="utf-8")
        self.waiting = {"messages": [{"display_name": "Steven", "created_at": ""}], "tasks": [], "next_task": None}
        code, out = self.run_cli("loop-watch", caller="")
        self.assertEqual((out["alert"], out["kind"]), (True, "silent"))
        self.assertIn("1 unread message", out["text"])
        self.assertFalse(self.run_cli("loop-watch", caller="")[1]["alert"])     # told once
        with patch.object(ide_cli.os, "geteuid", return_value=0):
            self.run_cli("tick", caller="coa")                                  # the chat is back
        self.assertEqual(self.stored_loop()["warned_at"], "")


class TestDoorVersion(unittest.TestCase):
    """Rule 30: a tool keeps the door it loaded; the installed version is named so an old copy shows."""

    def test_the_door_carries_its_own_version(self):
        door = _source(SRC, "coa-env", "AGENTS.md")
        self.assertRegex(ide_loop.door_stamp(door), r"^[0-9a-f]{8}$")
        self.assertEqual(
            ide_loop.door_stamp(door), ide_loop.door_version(door),
            "AGENTS.md was edited: set its `Rules version` to the value on the right "
            "(python3 -c \"import sys; sys.path.insert(0,'src/core-infra'); from agictl import ide_loop as l; "
            "p='src/coa-env/AGENTS.md'; open(p,'w').write(l.stamp_door(open(p).read()))\")",
        )
        self.assertIn("If either shows a version other than this one", door)

    def test_any_edit_changes_the_version_and_stamping_is_stable(self):
        door = _source(SRC, "coa-env", "AGENTS.md")
        self.assertNotEqual(ide_loop.door_version(door), ide_loop.door_version(door + "\nOne more rule."))
        self.assertEqual(ide_loop.stamp_door(door), door)
        self.assertEqual(ide_loop.door_stamp("# An old door with no stamp"), "")

    def test_the_notice_tells_an_old_copy_what_to_do(self):
        notice = ide_loop.door_notice("ab12cd34", "/home/coa/coa-env/AGENTS.md")
        for part in ("Rules version ab12cd34", "another version, or none", "old copy",
                     "Read /home/coa/coa-env/AGENTS.md again now"):
            self.assertIn(part, notice)
        self.assertEqual(ide_loop.door_notice("", "/x"), "")


class TestDoorAndLifeline(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.door = _source(SRC, "coa-env", "AGENTS.md")
        cls.seed = _source(CORE_INFRA, "config", "ide_session.md")
        cls.lifeline = _source(CORE_INFRA, "lifeline.sh")

    def test_the_seed_is_read_once(self):
        self.assertIn("once, at the start of a chat", self.door)
        self.assertIn("Do **not** re-read `.agent/versa-agi_ide.md`", self.door)
        self.assertNotIn("Re-read `.agent/versa-agi_ide.md`", self.door)
        self.assertIn("Read this file once, at the start of a chat.", self.seed)

    def test_every_message_and_every_wake_start_with_the_tick(self):
        self.assertIn("agictl agent ide tick coa --on-message", self.door)
        self.assertIn("**On every timer wake** run `agictl agent ide tick coa` and nothing before it", self.door)
        for do in ("| `idle` |", "| `work` |", "| `stop` |", "| `close` |"):
            self.assertIn(do, self.door)

    def test_a_loop_that_runs_out_closes_the_session_without_asking(self):
        """Rule 28, and the one exception to "do not infer an ending"."""
        row = next(line for line in self.door.split("\n") if line.startswith("| `close` |"))
        for part in ("without asking", "all four buckets", "one message to the Primary User",
                     "no closing notes to anyone else", "`agictl agent ide off coa`", "disarm the tool's loop"):
            self.assertIn(part, row)
        self.assertIn("**The one exception:** a loop that has run out and answers `do: \"close\"`", self.door)
        self.assertIn("Do not infer an ending and flip it yourself.", self.door)
        self.assertIn("what happens when it runs out", self.door)
        self.assertIn("`--at-end stay` only when the Primary User chose", self.door)

    def test_the_system_counts_not_the_model(self):
        self.assertIn("agictl agent ide loop start coa --every 15 --hours 8", self.door)
        self.assertIn("agictl agent ide loop start coa --every 15 --idle-ticks 4", self.door)
        self.assertIn("You never count ticks", self.door)
        self.assertNotIn("counts as one idle tick", self.door)

    def test_lifeline_warns_on_the_ide_hold_and_never_ends_the_mode(self):
        hold = self.lifeline.index('log "HOLD: ${AGENT_NAME} — IDE mode. Skipping spawn')
        block = self.lifeline[hold:hold + 2600]
        self.assertIn('agictl agent ide loop-watch "${AGENT_NAME}"', block)
        self.assertIn("agictl message send", block)
        self.assertNotIn("ide off", block)
        self.assertLess(block.index("loop-watch"), block.index("\n    continue\n"))


if __name__ == "__main__":
    unittest.main()
