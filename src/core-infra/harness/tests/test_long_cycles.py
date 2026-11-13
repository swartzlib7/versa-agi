"""Long-running cycles: pure parts (state_long_cycles.md §1.2, §1.5, §1.6, §1.7)."""

import os
import tempfile
import time
import unittest
from unittest import mock

from harness import cycle_close
from harness.checkin import CheckIns, checkin_line, checkin_message
from harness.heartbeat import Heartbeat, age_seconds
from harness.repeat_guard import RepeatGuard, call_label
from harness.step_clock import StepClock


class Clock:
    def __init__(self, now: float = 1_000_000.0):
        self.now = now

    def __call__(self) -> float:
        return self.now


class TestStepClock(unittest.TestCase):
    """LRC-01."""

    def _run(self, clock, sc, gaps):
        for gap in gaps:
            clock.now += gap
            sc.note_reply()

    def test_default_until_three_steps_are_measured(self):
        clock = Clock()
        sc = StepClock(clock=clock)
        self.assertEqual(sc.typical(), 60)
        sc.start()
        self._run(clock, sc, [200, 200])
        self.assertEqual(sc.typical(), 60)
        self._run(clock, sc, [200])
        self.assertEqual(sc.typical(), 200)

    def test_local_default_is_two_minutes(self):
        self.assertEqual(StepClock(local=True).typical(), 120)

    def test_median_of_the_last_eight(self):
        clock = Clock()
        sc = StepClock(clock=clock)
        sc.start()
        self._run(clock, sc, [1000, 1000, 1000])          # old, pushed out below
        self._run(clock, sc, [10, 20, 30, 40, 50, 60, 70, 80])
        self.assertEqual(sc.samples, 8)
        self.assertEqual(sc.typical(), 45)

    def test_one_long_step_does_not_move_the_typical_step(self):
        clock = Clock()
        sc = StepClock(clock=clock)
        sc.start()
        self._run(clock, sc, [30, 30, 30, 30, 900])
        self.assertEqual(sc.typical(), 30)

    def test_waiting_for_mail_is_left_out(self):
        clock = Clock()
        sc = StepClock(clock=clock)
        sc.start()
        self._run(clock, sc, [30, 30])
        sc.skip()
        clock.now += 600            # waiting for mail
        sc.start()
        self._run(clock, sc, [30])
        self.assertEqual(sc.samples, 3)
        self.assertEqual(sc.typical(), 30)

    def test_live_call_turns_are_left_out(self):
        clock = Clock()
        sc = StepClock(clock=clock)
        sc.start()
        self._run(clock, sc, [30])
        clock.now += 5
        sc.note_reply(counted=False)
        clock.now += 5
        sc.note_reply(counted=False)
        clock.now += 400
        sc.note_reply()             # first reply after the call: nothing to measure from
        self.assertEqual(sc.samples, 1)

    def test_start_does_not_reset_a_running_measurement(self):
        clock = Clock()
        sc = StepClock(clock=clock)
        sc.start()
        clock.now += 50
        sc.start()                  # re-invoke after a warning
        clock.now += 50
        sc.note_reply()
        self._run(clock, sc, [100, 100])
        self.assertEqual(sc.typical(), 100)


class TestHeartbeat(unittest.TestCase):
    """LRC-04, harness side."""

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.dir.name, "heartbeat")

    def tearDown(self):
        self.dir.cleanup()

    def _age(self):
        return time.time() - os.path.getmtime(self.path)

    def test_beat_creates_and_touches(self):
        hb = Heartbeat(self.path)
        hb.beat()
        self.assertTrue(os.path.exists(self.path))
        os.utime(self.path, (1, 1))
        hb.beat()
        self.assertLess(self._age(), 5)

    def test_no_path_is_a_no_op(self):
        hb = Heartbeat(None)
        hb.beat()
        hb.start()
        self.assertFalse(hb.tick())

    def test_unwritable_path_never_raises(self):
        Heartbeat(os.path.join(self.dir.name, "missing", "heartbeat")).beat()

    def test_tick_touches_only_while_held(self):
        hb = Heartbeat(self.path)
        hb.beat()
        os.utime(self.path, (1, 1))
        self.assertFalse(hb.tick())                 # a model call in flight: no touch
        self.assertGreater(self._age(), 1000)
        with hb.holding("tools"):
            self.assertLess(self._age(), 5)         # a tool starting touches at once
            os.utime(self.path, (1, 1))
            self.assertTrue(hb.tick())
            self.assertLess(self._age(), 5)
        os.utime(self.path, (1, 1))
        self.assertFalse(hb.tick())

    def test_two_holds_release_independently(self):
        hb = Heartbeat(self.path)
        hb.set_hold("tools", True)
        hb.set_hold("mail", True)
        hb.set_hold("tools", False)
        self.assertTrue(hb.held())
        hb.set_hold("mail", False)
        self.assertFalse(hb.held())

    def test_busy_callable_counts_as_a_hold(self):
        live = {"on": True}
        hb = Heartbeat(self.path, busy=lambda: live["on"])
        self.assertTrue(hb.tick())
        live["on"] = False
        self.assertFalse(hb.tick())

    def test_background_thread_beats_while_held(self):
        hb = Heartbeat(self.path, interval=0.05)
        hb.start()
        hb.set_hold("tools", True)
        os.utime(self.path, (1, 1))
        time.sleep(0.3)
        hb.stop()
        self.assertLess(self._age(), 5)

    def test_age_is_never_counted_from_before_the_cycle(self):
        with open(self.path, "w"):
            pass
        os.utime(self.path, (1000, 1000))           # left by an earlier cycle
        self.assertEqual(age_seconds(self.path, since=5000, now=5060), 60)
        os.utime(self.path, (5050, 5050))
        self.assertEqual(age_seconds(self.path, since=5000, now=5060), 10)
        self.assertEqual(age_seconds(self.path + ".none", since=5000, now=5030), 30)


class TestRepeatGuard(unittest.TestCase):
    """LRC-06."""

    CALL = ("agictl_task", {"command": "task list"})

    def test_third_identical_call_gives_one_notice(self):
        g = RepeatGuard()
        g.note(*self.CALL, "[]")
        g.note(*self.CALL, "[]")
        self.assertIsNone(g.notice_due())
        self.assertEqual(g.note(*self.CALL, "[]"), 3)
        text = g.notice_due()
        self.assertIn("3 times with the same result", text)
        self.assertIn("`agictl_task task list`", text)
        g.mark_noticed()
        self.assertIsNone(g.notice_due())
        g.note(*self.CALL, "[]")
        self.assertIsNone(g.notice_due())           # told once
        self.assertIsNone(g.stop_due())

    def test_notice_waits_until_it_is_marked(self):
        g = RepeatGuard()
        for _ in range(3):
            g.note(*self.CALL, "[]")
        self.assertIsNotNone(g.notice_due())
        self.assertIsNotNone(g.notice_due())        # no safe boundary yet

    def test_sixth_identical_call_stops_the_cycle(self):
        g = RepeatGuard()
        for _ in range(5):
            g.note(*self.CALL, "[]")
        self.assertIsNone(g.stop_due())
        g.note(*self.CALL, "[]")
        self.assertEqual(
            g.stop_due(),
            "[Harness] Stopped: the same call was repeated 6 times: agictl_task task list",
        )
        self.assertIsNone(g.notice_due())           # the stop replaces a pending notice

    def test_a_different_result_is_not_a_repeat(self):
        g = RepeatGuard()
        for i in range(10):
            self.assertEqual(g.note(*self.CALL, f"{i} rows"), 1)
        self.assertIsNone(g.notice_due())

    def test_different_arguments_are_not_a_repeat(self):
        g = RepeatGuard()
        for i in range(10):
            g.note("agictl_task", {"command": f"task get {i}"}, "not found")
        self.assertIsNone(g.notice_due())

    def test_only_the_last_twenty_calls_count(self):
        g = RepeatGuard()
        g.note(*self.CALL, "[]")
        g.note(*self.CALL, "[]")
        for i in range(19):
            g.note("agictl_browser", {"command": f"browser goto p{i}"}, f"page {i}")
        self.assertEqual(g.note(*self.CALL, "[]"), 1)
        self.assertIsNone(g.notice_due())

    def test_argument_order_does_not_matter(self):
        g = RepeatGuard()
        g.note("t", {"a": 1, "b": 2}, "r")
        self.assertEqual(g.note("t", {"b": 2, "a": 1}, "r"), 2)

    def test_label_is_bounded(self):
        self.assertLessEqual(len(call_label("t", {"command": "x" * 500})), 200)
        self.assertEqual(call_label("t", {"a": 1}), 't {"a": 1}')


class TestCheckIns(unittest.TestCase):
    """LRC-07."""

    def test_due_every_thirty_minutes(self):
        clock = Clock()
        c = CheckIns(clock.now, clock=clock)
        clock.now += 29 * 60
        self.assertIsNone(c.due())
        clock.now += 60
        self.assertEqual(c.due(), 30)
        c.mark()
        self.assertIsNone(c.due())
        clock.now += 30 * 60
        self.assertEqual(c.due(), 60)

    def test_unmarked_check_in_stays_due(self):
        clock = Clock()
        c = CheckIns(clock.now, clock=clock)
        clock.now += 34 * 60
        self.assertEqual(c.due(), 34)
        self.assertEqual(c.due(), 34)

    def test_a_missed_interval_is_not_made_up(self):
        clock = Clock()
        c = CheckIns(clock.now, clock=clock)
        clock.now += 95 * 60                         # one very long step
        self.assertEqual(c.due(), 95)
        c.mark()
        self.assertIsNone(c.due())
        clock.now += 25 * 60
        self.assertEqual(c.due(), 120)

    def test_message_and_line(self):
        self.assertIn("running for 60 minutes", checkin_message(60))
        self.assertIn("agictl task progress", checkin_message(60))
        line = checkin_line(60, 41, 120000, "agictl_browser browser goto https://example.org", now=0)
        self.assertIn("| 60 min | 41 steps | 120000 tokens | last tool: agictl_browser", line)
        self.assertIn("last tool: none", checkin_line(30, 2, 0, ""))


class _Msg:
    def __init__(self, type_, content):
        self.type = type_
        self.content = content


class TestCycleClose(unittest.TestCase):
    """LRC-03: what the harness writes when it closes the cycle."""

    def test_time_limit_summary(self):
        self.assertEqual(
            cycle_close.time_limit_summary(60, 41),
            "[Harness] Time limit reached (60 min, 41 steps). Work continues on the next spawn.",
        )

    def test_exit_codes(self):
        self.assertEqual((cycle_close.EXIT_STEP_BUDGET, cycle_close.EXIT_TIME_LIMIT, cycle_close.EXIT_LOOP),
                         (53, 54, 55))

    def test_handoff_has_the_last_reply_and_the_last_tool_call(self):
        text = cycle_close.harness_handoff("Found  3 sources.\nReading the second.", "agictl_browser browser goto x")
        self.assertTrue(text.startswith("[Harness] The cycle was closed for you"))
        self.assertIn("Your last reply: Found 3 sources. Reading the second.", text)
        self.assertIn("Your last tool call: agictl_browser browser goto x", text)

    def test_handoff_reply_is_cut_at_600_characters(self):
        text = cycle_close.harness_handoff("a" * 2000, "")
        reply = text.split("Your last reply: ")[1].split(" | ")[0]
        self.assertEqual(len(reply), 600)
        self.assertIn("No tool call was made", text)

    def test_handoff_with_nothing_to_quote(self):
        text = cycle_close.harness_handoff("", "")
        self.assertIn("You had written no reply", text)

    def test_last_plain_reply_skips_tool_only_turns_and_other_roles(self):
        messages = [
            _Msg("human", "wake"),
            _Msg("ai", "Plan: read the report."),
            _Msg("ai", ""),                                  # tool call only
            _Msg("tool", "result"),
            _Msg("ai", [{"type": "text", "text": "Read it."}, {"type": "tool_use", "id": "1"}]),
            _Msg("tool", "result"),
            _Msg("human", "time check"),
        ]
        self.assertEqual(cycle_close.last_plain_reply(messages), "Read it.")
        self.assertEqual(cycle_close.last_plain_reply([]), "")


class TestHarnessWiring(unittest.TestCase):
    """The step loop uses the pieces above (source checks; the loop needs a model to run)."""

    @classmethod
    def setUpClass(cls):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(root, "agent_harness.py"), encoding="utf-8") as fh:
            cls.src = fh.read()

    def test_warnings_use_the_step_clock(self):
        self.assertIn("typical_step=step_clock.typical", self.src)
        self.assertIn("step_clock.note_reply(counted=not _in_call_turn())", self.src)
        self.assertIn("step_clock.skip()  # waiting is not step time", self.src)

    def test_listening_window_closes_at_the_last_warning(self):
        self.assertIn("close_lead=deadline_warnings.close_now_lead", self.src)
        self.assertIn("time_stage == CLOSE_NOW and listening.should_wait()", self.src)
        self.assertNotIn("VERSA_TURN_MARKER", self.src)

    def test_heartbeat_at_each_point_of_the_contract(self):
        self.assertIn("_HEARTBEAT = Heartbeat.from_env()", self.src)
        self.assertIn("_HEARTBEAT.beat()  # a model call starts", self.src)
        self.assertIn('_HEARTBEAT.set_hold("tools", bool(getattr(msg, "tool_calls", None)))', self.src)
        self.assertIn('_HEARTBEAT.set_hold("tools", bool(_unresolved_tool_call_ids(messages)))', self.src)
        self.assertIn('with _HEARTBEAT.holding("mail"):', self.src)
        self.assertIn("_HEARTBEAT.set_busy(", self.src)

    def test_no_model_call_is_started_in_the_main_thread_while_a_hold_is_on(self):
        # The stall timer must see a model call in flight: the tools hold is
        # released once the batch is answered, before the next call.
        hook = self.src[self.src.index("def pre_model_hook(state):"):]
        self.assertLess(hook.index("_HEARTBEAT.beat()"), hook.index("from harness.compaction import"))

    def test_close_at_the_limit_before_a_model_call_only_at_a_safe_boundary(self):
        self.assertEqual(self.src.count("deadline_warnings.close_due()"), 2)
        top = self.src[self.src.index("# No model call the cycle cannot finish"):]
        top = top[:top.index("step_clock.start()")]
        self.assertIn("not _in_call_turn()", top)
        self.assertIn("not _unresolved_tool_call_ids(messages)", top)
        mid = self.src[self.src.index("# ── Time limit: no model call the cycle cannot finish"):]
        mid = mid[:mid.index("limit_stop = True")]
        for needed in ('"tools" in chunk', "not pending_tool_calls", "not _in_call_turn()",
                       "_flush_stream_messages_to_checkpoint"):
            self.assertIn(needed, mid)

    def test_exit_codes_and_close_order(self):
        tail = self.src[self.src.index("if budget_hard_stop:\n        _finalize_cycle_step_budget"):]
        self.assertLess(tail.index("sys.exit(EXIT_STEP_BUDGET)"), tail.index("sys.exit(EXIT_TIME_LIMIT)"))
        self.assertLess(tail.index("sys.exit(EXIT_TIME_LIMIT)"), tail.index("sys.exit(EXIT_LOOP)"))
        self.assertIn("time_limit_summary(minutes_run, step_count - call_steps)", tail)

    def test_repeat_guard_sees_every_finished_tool_call_outside_call_turns(self):
        self.assertIn("repeat_guard.note(_called[0], _called[1], content)", self.src)
        self.assertIn("warning, warn_tag, on_sent = notice, \"[LOOP]\", repeat_guard.mark_noticed", self.src)
        self.assertIn("loop_stop = repeat_guard.stop_due()", self.src)

    def test_check_in_line_is_written_even_when_the_message_is_held(self):
        block = self.src[self.src.index("checkin_minutes = checkins.due()"):]
        block = block[:block.index("if warning:")]
        self.assertLess(block.index("_write_checkin_line("), block.index("if not warning and not listening.should_wait() and not _in_wrap_up"))
        self.assertLess(block.index("checkins.mark()"), block.index("_write_checkin_line("))

    def test_harness_close_ignores_the_listening_flag(self):
        helper = self.src[self.src.index("def _close_cycle_from_harness("):self.src.index("def _finalize_cycle_step_budget(")]
        self.assertIn('"VERSA_LISTENING"', helper)
        self.assertIn('["--handoff", handoff] if handoff else ["--no-handoff"]', helper)


try:
    import harness.agent_harness as agent_harness
except Exception:  # system Python has no langchain; these run under the harness venv
    agent_harness = None


@unittest.skipIf(agent_harness is None, "harness dependencies not installed in this interpreter")
class TestHarnessClose(unittest.TestCase):
    """LRC-03: the close itself, with `agictl` replaced by a recorder."""

    def _run(self, saved_handoff):
        calls = []

        def fake_run(cmd, **kw):
            calls.append((cmd, kw.get("env")))

            class P:
                returncode = 0
                stderr = ""
                stdout = ('{"handoff": %s}' % ('"Open: X"' if saved_handoff else "null")) if cmd[1:3] == ["cycle", "get"] else "ok"
            return P()

        env = {"VERSA_CYCLE_ID": "web-dev-300", "VERSA_LISTENING": "open", "VERSA_LISTEN_MINUTES": "15"}
        with mock.patch.object(agent_harness.subprocess, "run", fake_run), \
                mock.patch.dict(os.environ, env, clear=False), \
                mock.patch.object(agent_harness, "tlog", lambda *_: None):
            agent_harness._finalize_cycle_with_handoff(
                "web-dev", cycle_close.time_limit_summary(60, 41), "TIME LIMIT",
                [_Msg("ai", "Reading source two.")], "agictl_browser browser goto x",
            )
        return calls

    def test_writes_a_harness_handoff_when_the_agent_saved_none(self):
        calls = self._run(saved_handoff=False)
        self.assertEqual(calls[0][0], ["agictl", "cycle", "get", "web-dev-300"])
        end, env = calls[1]
        self.assertEqual(end[:4], ["agictl", "cycle", "end", "[Harness] Time limit reached (60 min, 41 steps). Work continues on the next spawn."])
        self.assertEqual(end[4], "--handoff")
        self.assertIn("Your last reply: Reading source two.", end[5])
        self.assertIn("Your last tool call: agictl_browser browser goto x", end[5])
        self.assertEqual(end[-2:], ["--agent", "web-dev"])
        self.assertNotIn("VERSA_LISTENING", env)

    def test_keeps_the_handoff_the_agent_saved(self):
        end, _ = self._run(saved_handoff=True)[1]
        self.assertIn("--no-handoff", end)
        self.assertNotIn("--handoff", end)


if __name__ == "__main__":
    unittest.main()
