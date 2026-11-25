"""agictl system activity + Lifeline wiring (state_live_chat.md §1.8, LCH-06)."""

from __future__ import annotations

import json
import os
import re
import sys
import unittest
from unittest.mock import patch

CORE_INFRA = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, CORE_INFRA)

try:
    from click.testing import CliRunner
    sys.path.insert(0, os.path.join(CORE_INFRA, "agictl"))
    import agictl.cli as agictl_cli  # noqa: E402
    import comms  # noqa: E402
except ImportError:  # harness venv has no click; run under /opt/versa-agi/venv
    agictl_cli = None


@unittest.skipIf(agictl_cli is None, "click not installed in this interpreter")
class TestActivityCli(unittest.TestCase):
    BASE = ["system", "activity"]
    IDS = ["--sub-account", "sub_coa", "--token", "tok", "--cycle-id", "coa-1792200000"]

    def _run(self, args, response, env=None):
        calls = []

        def fake(endpoint, token, method="GET", body=None, timeout=None):
            calls.append({"endpoint": endpoint, "token": token, "method": method, "body": body})
            return response

        env = {"AGICTL_AGENT_USER": ""} | (env or {})
        with patch.object(comms, "api_request", side_effect=fake), patch.dict(os.environ, env):
            result = CliRunner().invoke(agictl_cli.cli, self.BASE + args)
        return result, calls

    def test_start_sends_fields_and_returns_granted_minutes(self):
        result, calls = self._run(
            ["start"] + self.IDS + ["--wake", "messages", "--listen-minutes", "15",
                                    "--hard-cap-epoch", "1792202700", "--cycles-total", "812",
                                    "--channel-id", "a_sub_coa", "--channel-id", "a_sub_coa",
                                    "--channel-id", "sub_coa_z"],
            {"success": True, "data": {"idleMinutes": 0}},
        )
        self.assertEqual(result.exit_code, 0, result.output)
        out = json.loads(result.output.strip().splitlines()[-1])
        self.assertEqual(out["idleMinutes"], 0)
        call = calls[0]
        self.assertEqual((call["endpoint"], call["method"], call["token"]), ("/agi/activity", "POST", "tok"))
        body = call["body"]
        self.assertEqual(body["event"], "start")
        self.assertEqual(body["subAccountId"], "sub_coa")
        self.assertEqual(body["cycleId"], "coa-1792200000")
        self.assertEqual(body["wake"], "messages")
        self.assertEqual(body["listenMinutes"], 15)
        self.assertEqual(body["cyclesTotal"], 812)
        self.assertRegex(body["hardCapAt"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")
        self.assertEqual(body["channelIds"], ["a_sub_coa", "sub_coa_z"])
        self.assertNotIn("outcome", body)
        self.assertNotIn("proofOfLife", body, "no promise unless Lifeline makes one")

    def test_start_can_promise_a_proof_of_life(self):
        """state_host_services.md §1.4.2 rule 1."""
        result, calls = self._run(
            ["start"] + self.IDS + ["--proof-of-life"], {"success": True, "data": {"idleMinutes": 0}})
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIs(calls[0]["body"]["proofOfLife"], True)
        _, calls = self._run(
            ["end"] + self.IDS + ["--outcome", "ok", "--proof-of-life"], {"success": True, "data": {}})
        self.assertNotIn("proofOfLife", calls[0]["body"])

    def test_a_late_end_carries_the_time_the_cycle_really_stopped(self):
        """state_host_services.md §1.6.3: the report for a cycle that was cut off."""
        result, calls = self._run(
            ["end"] + self.IDS + ["--outcome", "shutdown", "--duration-seconds", "203",
                                  "--ended-epoch", "1795037599"],
            {"success": True, "data": {"applied": True}},
        )
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(calls[0]["body"]["endedAt"], "2026-11-18T21:33:19Z")
        _, calls = self._run(["end"] + self.IDS + ["--outcome", "ok"], {"success": True, "data": {}})
        self.assertNotIn("endedAt", calls[0]["body"], "an ordinary end is timed by the server")

    def test_end_accepts_the_shutdown_outcome(self):
        result, calls = self._run(
            ["end"] + self.IDS + ["--outcome", "shutdown", "--duration-seconds", "203"],
            {"success": True, "data": {"applied": True}},
        )
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(calls[0]["body"]["outcome"], "shutdown")

    def test_the_token_can_come_from_the_environment(self):
        result, calls = self._run(
            ["start", "--sub-account", "sub_coa", "--cycle-id", "coa-1792200000"],
            {"success": True, "data": {"idleMinutes": 15}},
            env={"AGICTL_VV_TOKEN": "tok-from-env"},
        )
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(calls[0]["token"], "tok-from-env")

    def test_end_sends_outcome_and_duration(self):
        result, calls = self._run(
            ["end"] + self.IDS + ["--outcome", "timeout", "--duration-seconds", "2700"],
            {"success": True, "data": {"applied": True}},
        )
        self.assertEqual(result.exit_code, 0, result.output)
        body = calls[0]["body"]
        self.assertEqual((body["event"], body["outcome"], body["durationSeconds"]), ("end", "timeout", 2700))
        self.assertNotIn("hardCapAt", body)

    def test_failure_exits_1_with_zero_minutes(self):
        result, _ = self._run(["start"] + self.IDS, None)
        self.assertEqual(result.exit_code, 1)
        out = json.loads(result.output.strip().splitlines()[-1])
        self.assertFalse(out["success"])
        self.assertEqual(out["idleMinutes"], 0)

    def test_agents_are_refused(self):
        result, calls = self._run(["start"] + self.IDS, {"success": True}, env={"AGICTL_AGENT_USER": "coa"})
        self.assertEqual(result.exit_code, 1)
        self.assertIn("Lifeline only", result.output)
        self.assertEqual(calls, [])


class TestLifelineWiring(unittest.TestCase):
    """Order matters: start before the harness spawn; end after the runaway handler."""

    @classmethod
    def setUpClass(cls):
        with open(os.path.join(CORE_INFRA, "lifeline.sh"), encoding="utf-8") as fh:
            cls.text = fh.read()

    def test_start_before_spawn_end_after_runaway(self):
        start = self.text.index("_vv_activity start")
        spawn = self.text.index("-m harness.agent_harness --agent")
        runaway = self.text.index("EXIT_CODE=99  # Custom exit code for runaway")
        end = self.text.index("_vv_activity end", self.text.index("# ─── Activity end"))
        log_exit = self.text.index("# ─── Log Exit Code")
        self.assertLess(start, spawn)
        self.assertLess(runaway, end)
        self.assertLess(end, log_exit)

    def test_outcome_map(self):
        heading = self.text.index("# ─── Activity end")
        block = self.text[heading:self.text.index("_vv_activity end", heading)]
        pairs = dict(re.findall(r"(\d+|\*)\)\s+ACTIVITY_OUTCOME=\"(\w+)\"", block))
        self.assertEqual(pairs, {"0": "ok", "124": "timeout", "54": "timeout", "99": "runaway",
                                 "53": "step_limit", "97": "stall", "55": "loop", "*": "crash"})

    def test_shutdown_overrides_the_outcome_and_the_marker_goes_after_the_end(self):
        """state_host_services.md §1.4.1 rule 6."""
        end = self.text.index("_vv_activity end", self.text.index("# ─── Activity end"))
        block = self.text[self.text.index("# ─── Activity end"):self.text.index("# ─── Log Exit Code")]
        cut = block.index("if _shutdown_pending; then")
        self.assertLess(block.index('*)   ACTIVITY_OUTCOME="crash"'), cut)
        self.assertLess(cut, block.index('ACTIVITY_OUTCOME="shutdown"'))
        self.assertLess(block.index('ACTIVITY_OUTCOME="shutdown"'), block.index("_vv_activity end"))
        self.assertLess(block.index("_vv_activity end"), block.index('rm -f "${ACTIVITY_OPEN_MARKER:-}"'))
        start = self.text.index("LISTEN_MINUTES=$(_vv_activity start")
        marker = self.text.index('echo "${CURRENT_CYCLE_ID:-} ${SPAWN_EPOCH}" > "${ACTIVITY_OPEN_MARKER}"')
        self.assertLess(start, marker)
        self.assertLess(marker, end)
        # 143 is a harness stopped by SIGTERM; it must not count toward the circuit breaker.
        self.assertIn("exit_code IN (1, 42, 55, 97, 99)", self.text)

    def test_the_proof_of_life_is_promised_only_with_the_spawn_fetch_running(self):
        """state_host_services.md §1.4.2 rules 1 and 2."""
        self.assertIn('if [ -n "${INBOX_POLL_PID}" ] && [ -n "${CURRENT_CYCLE_ID:-}" ]; then\n'
                      "    ACTIVITY_PROOF_ARGS=(--proof-of-life)", self.text)
        self.assertIn('${ACTIVITY_PROOF_ARGS[@]+"${ACTIVITY_PROOF_ARGS[@]}"}', self.text)
        self.assertIn('"${SUB_ACCOUNT_ID}" "${API_TOKEN}" "${CURRENT_CYCLE_ID:-}" &', self.text)
        self.assertLess(self.text.index("INBOX_POLL_PID=$!"), self.text.index("ACTIVITY_PROOF_ARGS=(--proof-of-life)"))

    def test_hard_cap_sent_to_versavoice_is_the_backstop_time(self):
        # state_long_cycles.md §1.8: the cycle may run to the time limit plus the grace.
        self.assertIn('--hard-cap-epoch "$(( DEADLINE_EPOCH + TIMEOUT_GRACE_MIN * 60 ))"', self.text)


class TestLifelineListening(unittest.TestCase):
    """P3 wiring: setting → start → granted minutes → harness; turn marker → monitor."""

    @classmethod
    def setUpClass(cls):
        with open(os.path.join(CORE_INFRA, "lifeline.sh"), encoding="utf-8") as fh:
            cls.text = fh.read()

    def test_grant_reaches_harness(self):
        self.assertIn('LISTEN_MINUTES=$(_vv_activity start', self.text)
        self.assertIn("--listen-minutes '${LISTEN_MINUTES:-0}'", self.text)
        self.assertLess(self.text.index("LISTEN_MINUTES=$(_vv_activity start"),
                        self.text.index("-m harness.agent_harness --agent"))

    def test_setting_values_and_exports(self):
        self.assertIn("0|5|10|15|30) ;;", self.text)
        self.assertIn("export VERSA_AGENT_IDS=", self.text)

    def test_no_turn_marker_since_the_rolling_window(self):
        # state_long_cycles.md §1.4 rule 4: a rolling window already forgets old output.
        self.assertNotIn("TURN_MARKER", self.text)


if __name__ == "__main__":
    unittest.main()
