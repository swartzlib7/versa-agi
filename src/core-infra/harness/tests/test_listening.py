"""Live Chat listening window (state_live_chat.md §1.4–§1.6, LCH-12/13/15)."""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

CORE_INFRA = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, CORE_INFRA)

from harness.deadline import FIVE_MINUTES, TEN_MINUTES, DeadlineWarnings  # noqa: E402
from harness.listening import (  # noqa: E402
    CLOSE_LEAD_SECONDS,
    ListeningWindow,
    agent_ids_from_env,
    conversations_block,
    is_person,
    read_close_request,
    wait_for_mail,
)
from harness.midcycle_mail import format_arrival_block  # noqa: E402

PU = "pu_uid"
AGENTS = {"sub_coa", "sub_clerk", "coa", "clerk"}


class Clock:
    def __init__(self, now=1_000_000.0):
        self.now = now

    def __call__(self):
        return self.now


def _row(sender, channel="vv", mid="m1"):
    return {"from_user_id": sender, "channel": channel, "message_id": mid}


class TestPerson(unittest.TestCase):
    def test_person_rules(self):
        self.assertTrue(is_person(_row(PU), {PU}, AGENTS))
        self.assertTrue(is_person(_row(PU, "internal"), {PU}, AGENTS))  # agitop mail from the PU
        self.assertTrue(is_person(_row("steve"), {PU}, AGENTS))
        self.assertFalse(is_person(_row("sub_clerk"), {PU}, AGENTS))
        self.assertFalse(is_person(_row("clerk", "internal"), {PU}, AGENTS))
        self.assertFalse(is_person(_row("steve", "internal"), {PU}, AGENTS))
        self.assertFalse(is_person(_row(""), {PU}, AGENTS))

    def test_agent_ids_from_env(self):
        self.assertEqual(agent_ids_from_env({"VERSA_AGENT_IDS": "a, b,,c"}), {"a", "b", "c"})
        self.assertEqual(agent_ids_from_env({}), set())


class TestWindow(unittest.TestCase):
    def _window(self, minutes=15, deadline_in=45 * 60):
        clock = Clock()
        env = {}
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        marker = os.path.join(tmp.name, "live_turn_marker")
        w = ListeningWindow(minutes, clock.now + deadline_in, pu_ids={PU}, agent_ids=AGENTS,
                            clock=clock, env=env, marker_path=marker)
        return w, clock, env, marker

    def test_off_never_opens(self):
        w, _, env, _ = self._window(minutes=0)
        self.assertFalse(w.note_rows([_row(PU)]))
        self.assertFalse(w.should_wait())
        self.assertNotIn("VERSA_LISTENING", env)

    def test_agent_mail_does_not_open(self):
        w, _, _, marker = self._window()
        self.assertFalse(w.note_rows([_row("sub_clerk"), _row("clerk", "internal")]))
        self.assertFalse(w.opened)
        self.assertFalse(os.path.exists(marker))

    def test_person_opens_sets_env_and_marker(self):
        w, _, env, marker = self._window()
        self.assertTrue(w.note_rows([_row("sub_clerk"), _row("steve")]))
        self.assertTrue(w.should_wait())
        self.assertEqual(env, {"VERSA_LISTENING": "open", "VERSA_LISTEN_MINUTES": "15"})
        self.assertTrue(os.path.exists(marker))

    def test_idle_closes_and_activity_resets(self):
        w, clock, _, _ = self._window(minutes=5)
        w.note_rows([_row(PU)])
        clock.now += 4 * 60
        self.assertIsNone(w.close_reason())
        w.note_activity()  # the agent replied
        clock.now += 4 * 60
        self.assertIsNone(w.close_reason())
        clock.now += 61
        self.assertEqual(w.close_reason(), "idle")

    def test_deadline_closes_first(self):
        w, clock, _, _ = self._window(minutes=30, deadline_in=20 * 60)
        w.note_rows([_row(PU)])
        clock.now += 20 * 60 - CLOSE_LEAD_SECONDS
        self.assertEqual(w.close_reason(), "deadline")

    def test_closing_sets_env_and_stops_waiting(self):
        w, _, env, _ = self._window()
        w.note_rows([_row(PU)])
        text = w.begin_closing("idle")
        self.assertIn("cycle end", text)
        self.assertIn("15 minutes", text)
        self.assertEqual(env["VERSA_LISTENING"], "closing")
        self.assertFalse(w.should_wait())
        self.assertFalse(w.note_rows([_row(PU)]))  # no reopening once closing


class TestWaitForMail(unittest.TestCase):
    def _setup(self, minutes=5):
        clock = Clock()
        w = ListeningWindow(minutes, clock.now + 45 * 60, pu_ids={PU}, agent_ids=AGENTS,
                            clock=clock, env={})
        w.note_rows([_row(PU)])
        return w, clock

    def test_mail_arrives(self):
        w, clock = self._setup()
        polls = []

        def inject():
            polls.append(clock.now)
            return len(polls) == 3

        result = wait_for_mail(w, inject, sleep=lambda s: setattr(clock, "now", clock.now + s),
                               poll_seconds=3)
        self.assertEqual(result, "mail")
        self.assertEqual(len(polls), 3)

    def test_idle_without_mail(self):
        w, clock = self._setup(minutes=5)
        result = wait_for_mail(w, lambda: False,
                               sleep=lambda s: setattr(clock, "now", clock.now + s), poll_seconds=30)
        self.assertEqual(result, "idle")
        self.assertGreaterEqual(clock.now - 1_000_000.0, 5 * 60)

    def test_deadline_without_mail(self):
        clock = Clock()
        w = ListeningWindow(30, clock.now + 10 * 60, pu_ids={PU}, agent_ids=AGENTS, clock=clock, env={})
        w.note_rows([_row(PU)])
        result = wait_for_mail(w, lambda: False,
                               sleep=lambda s: setattr(clock, "now", clock.now + s), poll_seconds=30)
        self.assertEqual(result, "deadline")


class TestPromptCopy(unittest.TestCase):
    def test_block_is_stable_and_names_pu(self):
        a = conversations_block("Steven")
        self.assertEqual(a, conversations_block("Steven"))
        self.assertIn("Just a second — Steven sent me a message", a)
        self.assertIn("next spawn", a)
        self.assertIn("Lifeline will wake you", a)
        self.assertIn("the Primary User comes first".split()[1], a)

    def test_pu_rows_flagged_first(self):
        rows = [
            {"message_id": "a", "from_user_id": "steve", "display_name": "Steve", "text": "hi"},
            {"message_id": "b", "from_user_id": PU, "display_name": "Steven", "text": "quick one"},
        ]
        text, ids = format_arrival_block(rows, set(), {PU})
        self.assertEqual(ids, {"a", "b"})
        self.assertIn("(pu_uid) [PRIMARY USER — answer first]: quick one", text)
        self.assertNotIn("(steve) [PRIMARY USER", text)


class TestDeadlineWhileListening(unittest.TestCase):
    def test_ten_minute_listening_variant(self):
        clock = Clock()
        w = DeadlineWarnings(clock.now + 45 * 60, started_epoch=clock.now, clock=clock)
        clock.now += 35 * 60
        stage, text = w.due(listening=True)
        self.assertEqual(stage, TEN_MINUTES)
        self.assertIn("Keep chatting", text)
        self.assertNotIn("cycle end", text)
        stage, text = w.due(listening=False)
        self.assertIn("cycle end", text)
        w.mark(TEN_MINUTES)
        clock.now += 5 * 60
        self.assertEqual(w.due(listening=True)[0], FIVE_MINUTES)


try:
    from click.testing import CliRunner
    sys.path.insert(0, os.path.join(CORE_INFRA, "agictl"))
    import agictl.cli as agictl_cli  # noqa: E402
except ImportError:
    agictl_cli = None


@unittest.skipIf(agictl_cli is None, "click not installed in this interpreter")
class TestCycleEndWhileListening(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.tmp.name, "cycles.db")
        with sqlite3.connect(self.db) as conn:
            conn.execute("CREATE TABLE cycles (id TEXT PRIMARY KEY, started_at TEXT, ended_at TEXT, "
                         "summary TEXT, handoff TEXT, session_start_ts TEXT, last_awareness_ts TEXT)")
            conn.execute("INSERT INTO cycles (id, started_at) VALUES ('coa-1', datetime('now'))")

    def tearDown(self):
        self.tmp.cleanup()

    def _end(self, env):
        with patch.object(agictl_cli, "cycles_db", self.db), \
                patch.dict(os.environ, {"AGICTL_AGENTS_DB": os.path.join(self.tmp.name, "none.db")} | env):
            return CliRunner().invoke(agictl_cli.cli, [
                "cycle", "end", "Chatted with Steve", "--handoff", "Open: x | Next: y", "--agent", "coa"])

    def _row(self):
        with sqlite3.connect(self.db) as conn:
            return conn.execute("SELECT ended_at, summary, handoff FROM cycles WHERE id='coa-1'").fetchone()

    def test_open_window_saves_and_stays_open(self):
        result = self._end({"VERSA_LISTENING": "open", "VERSA_LISTEN_MINUTES": "15"})
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("Thank you", result.output)
        self.assertIn("15 minutes", result.output)
        self.assertNotIn("Cycle ended:", result.output)
        ended_at, summary, handoff = self._row()
        self.assertIsNone(ended_at)
        self.assertEqual((summary, handoff), ("Chatted with Steve", "Open: x | Next: y"))

    def test_closing_window_ends_normally(self):
        result = self._end({"VERSA_LISTENING": "closing"})
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("🛑 Cycle ended: Chatted with Steve", result.output)
        self.assertIsNotNone(self._row()[0])


class TestCallClamp(unittest.TestCase):
    """§1.7 rule 3: a call gets only the time left before the hard stop."""

    def setUp(self):
        try:
            import harness.agent_harness as ah
        except ImportError as e:  # agitop venv has no langchain
            self.skipTest(f"harness deps missing: {e}")
        import time as _time
        from live_call_config import LiveCallSettings
        self.ah = ah
        self.time = _time
        self.placed = []
        test = self

        class Runtime:
            def place(self, reason, settings, **kw):
                test.placed.append(settings.max_minutes)
                return {"success": True, "status": "connected"}

            def shutdown(self, *a):
                pass

        class Gate:
            ok = True
            settings = LiveCallSettings(enabled=True, call_model="m", voice_model="v",
                                        max_minutes=15, join_timeout_seconds=45)

        self.saved = (ah._LIVE_CALL_RUNTIME, ah._LIVE_CALL_GATE, ah._HARNESS_DEADLINE_EPOCH)
        ah._LIVE_CALL_RUNTIME, ah._LIVE_CALL_GATE = Runtime(), Gate()

    def tearDown(self):
        if hasattr(self, "saved"):
            self.ah._LIVE_CALL_RUNTIME, self.ah._LIVE_CALL_GATE, self.ah._HARNESS_DEADLINE_EPOCH = self.saved

    def _call(self, minutes_left):
        self.ah._HARNESS_DEADLINE_EPOCH = self.time.time() + minutes_left * 60 if minutes_left else None
        return json.loads(self.ah.agictl_call_pu.func(reason="x"))

    def test_plenty_of_time_keeps_setting(self):
        self.assertEqual(self._call(40)["status"], "connected")
        self.assertEqual(self.placed, [15])

    def test_late_call_is_cut(self):
        self.assertEqual(self._call(12)["status"], "connected")
        self.assertEqual(self.placed, [8])  # 12 min − 3 min margin, whole minutes

    def test_too_late_is_refused(self):
        out = self._call(7)
        self.assertEqual(out["status"], "no_time")
        self.assertEqual(self.placed, [])

    def test_no_deadline_keeps_setting(self):
        self._call(None)
        self.assertEqual(self.placed, [15])


class TestChatCloseRequest(unittest.TestCase):
    """LCH-23: `agictl agent chat-close` → the window closes gracefully."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "chat_close")

    def tearDown(self):
        self.tmp.cleanup()

    def _write(self, text):
        with open(self.path, "w", encoding="utf-8") as fh:
            fh.write(text)

    def test_read_request(self):
        self.assertIsNone(read_close_request(self.path, "coa-1"))  # no file
        self._write("coa-1 requested\n")
        self.assertEqual(read_close_request(self.path, "coa-1"), "requested")
        self.assertIsNone(read_close_request(self.path, "coa-2"))  # earlier cycle's request
        self._write("coa-2 shutdown")
        self.assertEqual(read_close_request(self.path, "coa-2"), "shutdown")
        self._write("coa-2 bogus")
        self.assertIsNone(read_close_request(self.path, "coa-2"))
        self.assertIsNone(read_close_request(None, "coa-2"))

    def test_window_closes_on_request(self):
        clock = Clock()

        def window():
            w = ListeningWindow(15, clock.now + 45 * 60, pu_ids={PU}, agent_ids=AGENTS,
                                clock=clock, env={}, close_file=self.path, cycle_id="coa-1")
            w.note_rows([_row(PU)])
            return w

        w = window()
        self.assertIsNone(w.close_reason())
        self._write("coa-1 requested")
        self.assertEqual(w.close_reason(), "requested")
        self.assertIn("The Primary User closed this chat", w.begin_closing("requested"))
        # The wait loop between turns returns at once with the request.
        self.assertEqual(wait_for_mail(window(), lambda: False, sleep=lambda s: None), "requested")


@unittest.skipIf(agictl_cli is None, "click not installed in this interpreter")
class TestChatCloseCli(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.tmp.name, "cycles.db")
        os.makedirs(os.path.join(self.tmp.name, "coa"))
        with sqlite3.connect(self.db) as conn:
            conn.execute("CREATE TABLE cycles (id TEXT PRIMARY KEY, started_at TEXT, ended_at TEXT)")
            conn.execute("INSERT INTO cycles VALUES ('coa-100', '2026-11-09 08:00:00', '2026-11-09 08:05:00')")
            conn.execute("INSERT INTO cycles VALUES ('coa-200', '2026-11-09 09:00:00', NULL)")
            conn.execute("INSERT INTO cycles VALUES ('coach-300', '2026-11-09 09:30:00', NULL)")

    def tearDown(self):
        self.tmp.cleanup()

    def _run(self, args, env=None):
        with patch.object(agictl_cli, "cycles_db", self.db), \
                patch.object(agictl_cli, "CHAT_CLOSE_DIR", self.tmp.name), \
                patch.dict(os.environ, {"AGICTL_AGENT_USER": ""} | (env or {})):
            return CliRunner().invoke(agictl_cli.cli, ["agent", "chat-close"] + args)

    def test_writes_request_for_open_cycle(self):
        result = self._run(["coa"])
        self.assertEqual(result.exit_code, 0, result.output)
        out = json.loads(result.output.strip().splitlines()[-1])
        self.assertTrue(out["closing"])
        self.assertEqual(out["cycleId"], "coa-200")  # not coach-300
        with open(os.path.join(self.tmp.name, "coa", "chat_close"), encoding="utf-8") as fh:
            self.assertEqual(fh.read().split(), ["coa-200", "requested"])

    def test_shutdown_reason(self):
        self._run(["coa", "--reason", "shutdown"])
        with open(os.path.join(self.tmp.name, "coa", "chat_close"), encoding="utf-8") as fh:
            self.assertEqual(fh.read().split(), ["coa-200", "shutdown"])

    def test_no_cycle_running(self):
        with sqlite3.connect(self.db) as conn:
            conn.execute("UPDATE cycles SET ended_at='2026-11-09 09:10:00' WHERE id='coa-200'")
        out = json.loads(self._run(["coa"]).output.strip().splitlines()[-1])
        self.assertFalse(out["closing"])
        self.assertFalse(os.path.exists(os.path.join(self.tmp.name, "coa", "chat_close")))

    def test_agents_refused(self):
        result = self._run(["coa"], env={"AGICTL_AGENT_USER": "clerk"})
        self.assertEqual(result.exit_code, 1)
        self.assertIn("Only the Primary User", result.output)


if __name__ == "__main__":
    unittest.main()
