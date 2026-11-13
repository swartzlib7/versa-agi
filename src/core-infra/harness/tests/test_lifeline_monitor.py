"""Lifeline side of long-running cycles (state_long_cycles.md §1.1–§1.4, §1.8).

The monitor and the exit handling are bash. These tests run the real text
from lifeline.sh with fast polls and stand-ins for `log` and `pkill`.
"""

import os
import subprocess
import tempfile
import time
import unittest
import uuid

CORE_INFRA = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _lifeline() -> str:
    with open(os.path.join(CORE_INFRA, "lifeline.sh"), encoding="utf-8") as handle:
        return handle.read()


def _monitor_function(text: str) -> str:
    start = text.index("monitor_runaway() {")
    return text[start:text.index("\n}\n", start) + 3]


class _Monitor:
    """monitor_runaway from lifeline.sh, running in the background with fast polls."""

    def __init__(self, test, lines=100, size_kb=2048, stall_limit=0, old_heartbeat=False):
        self.tmp = tempfile.TemporaryDirectory()
        test.addCleanup(self.close)
        self.name = f"t-{uuid.uuid4().hex[:10]}"
        self.result = os.path.join(self.tmp.name, "result.json")
        self.heartbeat = os.path.join(self.tmp.name, "heartbeat")
        self.log = os.path.join(self.tmp.name, "log")
        self.runaway = f"/tmp/versa-agi-runaway-{self.name}"
        self.stall = f"/tmp/versa-agi-stall-{self.name}"
        open(self.result, "w").close()
        if old_heartbeat:
            open(self.heartbeat, "w").close()
            os.utime(self.heartbeat, (1000, 1000))
        script = (
            f"log() {{ echo \"LOG $*\" >> '{self.log}'; }}\n"
            f"pkill() {{ echo \"PKILL $*\" >> '{self.log}'; }}\n"
            + _monitor_function(_lifeline())
            + f"monitor_runaway '{self.result}' agi-x '{self.name}' {lines} {size_kb} "
              f"'{self.heartbeat}' {stall_limit}\n"
        )
        env = dict(os.environ, VERSA_MONITOR_POLL_SECONDS="0.05", VERSA_MONITOR_SAMPLE_POLLS="2")
        self.proc = subprocess.Popen(["bash", "-c", script], env=env)

    def write_lines(self, count, width=10):
        with open(self.result, "a", encoding="utf-8") as handle:
            handle.write(("x" * width + "\n") * count)

    def beat(self):
        open(self.heartbeat, "a").close()
        os.utime(self.heartbeat, None)

    def wait_for(self, path, seconds=4.0) -> bool:
        end = time.time() + seconds
        while time.time() < end:
            if os.path.exists(path):
                return True
            time.sleep(0.05)
        return False

    def read(self, path) -> str:
        with open(path, encoding="utf-8") as handle:
            return handle.read()

    def close(self):
        if self.proc.poll() is None:
            self.proc.kill()
        self.proc.wait()
        for path in (self.runaway, self.stall):
            try:
                os.remove(path)
            except OSError:
                pass
        self.tmp.cleanup()


class TestRollingRunawayWindow(unittest.TestCase):
    """LRC-05: growth in the last 15 samples, not the cycle total."""

    def test_a_long_cycle_passes_the_total_without_tripping(self):
        m = _Monitor(self, lines=100)
        m.write_lines(60)
        time.sleep(2.2)                 # more than 15 samples: the 60 lines leave the window
        m.write_lines(60)               # 120 in total, 60 in the window
        time.sleep(0.6)
        self.assertFalse(os.path.exists(m.runaway))
        self.assertIsNone(m.proc.poll())

    def test_a_flood_inside_the_window_trips(self):
        m = _Monitor(self, lines=100)
        m.write_lines(60)
        time.sleep(2.2)
        m.write_lines(150)
        self.assertTrue(m.wait_for(m.runaway))
        self.assertIn("result_lines:lines=", m.read(m.runaway))
        self.assertIn("in 15 min", m.read(m.runaway))
        m.proc.wait(timeout=3)
        log = m.read(m.log)
        self.assertIn("PKILL -u agi-x -f harness.agent_harness", log)
        self.assertIn("RUNAWAY:", log)

    def test_size_growth_trips(self):
        m = _Monitor(self, lines=100000, size_kb=1)
        m.write_lines(3, width=600)
        self.assertTrue(m.wait_for(m.runaway))
        self.assertIn("result_size:size=", m.read(m.runaway))

    def test_session_files_and_turn_marker_are_gone(self):
        text = _lifeline()
        self.assertNotIn("session-*", text)
        self.assertNotIn("TURN_MARKER", text)
        self.assertNotIn("BASELINE_SESSION_SIZE", text)


class TestStallTimer(unittest.TestCase):
    """LRC-04, Lifeline side."""

    def test_a_beating_harness_is_left_alone_then_stopped_when_it_goes_quiet(self):
        m = _Monitor(self, stall_limit=1)
        end = time.time() + 2.5
        while time.time() < end:
            m.beat()
            time.sleep(0.2)
        self.assertFalse(os.path.exists(m.stall))
        self.assertTrue(m.wait_for(m.stall, seconds=5))
        self.assertIn("no heartbeat for", m.read(m.stall))
        m.proc.wait(timeout=3)
        log = m.read(m.log)
        self.assertIn("PKILL -u agi-x -f harness.agent_harness", log)
        self.assertIn("STALL:", log)
        self.assertFalse(os.path.exists(m.runaway))

    def test_a_heartbeat_left_by_an_earlier_cycle_does_not_trip_at_once(self):
        m = _Monitor(self, stall_limit=2, old_heartbeat=True)
        time.sleep(0.8)
        self.assertFalse(os.path.exists(m.stall))
        self.assertTrue(m.wait_for(m.stall, seconds=5))     # still stalls once the limit passes

    def test_limit_zero_is_off(self):
        m = _Monitor(self, stall_limit=0)
        time.sleep(1.5)
        self.assertFalse(os.path.exists(m.stall))
        self.assertIsNone(m.proc.poll())


class TestExitHandling(unittest.TestCase):
    """LRC-03, LRC-04, LRC-06, LRC-09: what Lifeline does with each exit code."""

    def setUp(self):
        self.text = _lifeline()

    def _outcomes(self, exit_code, stall=False):
        start = self.text.index("  # ─── Stall Handler")
        end = self.text.index("  _vv_activity end --outcome")
        with tempfile.TemporaryDirectory() as tmp:
            sentinel = os.path.join(tmp, "stall")
            if stall:
                with open(sentinel, "w", encoding="utf-8") as handle:
                    handle.write("no heartbeat for 700s (limit 600s)")
            script = (
                "set -uo pipefail\n"
                f"EXIT_CODE={exit_code}\nSTALL_SENTINEL='{sentinel}'\n"
                + self.text[start:end]
                + '\necho "${EXIT_CODE}|${ACTIVITY_OUTCOME}|${STALL_INFO}"\n'
            )
            out = subprocess.run(["bash", "-c", script], capture_output=True, text=True)
            self.assertEqual(out.returncode, 0, out.stderr)
            return out.stdout.strip().split("|")

    def test_outcomes_sent_to_versavoice(self):
        self.assertEqual(self._outcomes(0)[:2], ["0", "ok"])
        self.assertEqual(self._outcomes(53)[:2], ["53", "step_limit"])
        self.assertEqual(self._outcomes(54)[:2], ["54", "timeout"])
        self.assertEqual(self._outcomes(55)[:2], ["55", "loop"])
        self.assertEqual(self._outcomes(124)[:2], ["124", "timeout"])
        self.assertEqual(self._outcomes(99)[:2], ["99", "runaway"])
        self.assertEqual(self._outcomes(1)[:2], ["1", "crash"])

    def test_a_stall_becomes_exit_97_with_its_own_outcome(self):
        code, outcome, info = self._outcomes(143, stall=True)
        self.assertEqual((code, outcome), ("97", "stall"))
        self.assertIn("no heartbeat for 700s", info)

    def test_every_outcome_lifeline_sends_is_one_agictl_accepts(self):
        import re
        sent = set(re.findall(r'ACTIVITY_OUTCOME="(\w+)"', self.text))
        with open(os.path.join(CORE_INFRA, "agictl", "cli.py"), encoding="utf-8") as fh:
            cli = fh.read()
        listed = re.search(r'ACTIVITY_OUTCOMES = \(([^)]*)\)', cli).group(1)
        accepted = set(re.findall(r'"(\w+)"', listed))
        self.assertEqual(sent, accepted)
        self.assertEqual(accepted, {"ok", "timeout", "runaway", "step_limit", "stall", "loop", "crash"})

    def test_time_limit_queues_a_resume_and_logs(self):
        i = self.text.index('if [ "${EXIT_CODE}" -eq 54 ]; then')
        block = self.text[i:i + 300]
        self.assertIn('touch "${STEP_RESUME_SENTINEL}"', block)
        self.assertIn("LIMIT: ${AGENT_NAME} time limit reached", self.text)
        self.assertIn("LOOP: ${AGENT_NAME} stopped", self.text)
        self.assertIn("STALL: ${AGENT_NAME} stopped by the stall timer", self.text)

    def test_breaker_counts_loop_and_stall_but_not_the_time_limit(self):
        self.assertEqual(self.text.count("exit_code IN (1, 42, 55, 97, 99)"), 2)
        self.assertNotIn("exit_code IN (1, 42, 99)", self.text)

    def test_backstop_is_the_limit_plus_grace_and_the_range_is_30_to_240(self):
        self.assertIn('if [ "${AGENT_TIMEOUT_MIN}" -gt 240 ]; then AGENT_TIMEOUT_MIN=240; fi', self.text)
        self.assertIn("TIMEOUT_GRACE_MIN=5", self.text)
        self.assertIn('TIMEOUT_DURATION="$(( AGENT_TIMEOUT_MIN + TIMEOUT_GRACE_MIN ))m"', self.text)
        # The harness still gets the limit itself, not the backstop.
        self.assertIn("DEADLINE_EPOCH=$(( SPAWN_EPOCH + AGENT_TIMEOUT_MIN * 60 ))", self.text)

    def test_stall_limit_is_ten_minutes_and_twenty_for_a_local_model(self):
        self.assertIn("STALL_LIMIT_SECONDS=600", self.text)
        self.assertIn('if [ "${IS_LOCAL_MODEL}" = true ]; then STALL_LIMIT_SECONDS=1200; fi', self.text)

    def test_harness_gets_the_heartbeat_file_and_the_local_flag(self):
        self.assertIn("export VERSA_HEARTBEAT_FILE='${HEARTBEAT_FILE}'", self.text)
        self.assertIn("export VERSA_LOCAL_MODEL='1'", self.text)
        self.assertIn('"${HEARTBEAT_FILE}" "${STALL_LIMIT_SECONDS}" &', self.text)


if __name__ == "__main__":
    unittest.main()
