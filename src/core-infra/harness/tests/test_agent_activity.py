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
        end = self.text.index("_vv_activity end")
        log_exit = self.text.index("# ─── Log Exit Code")
        self.assertLess(start, spawn)
        self.assertLess(runaway, end)
        self.assertLess(end, log_exit)

    def test_outcome_map(self):
        block = self.text[self.text.index("# ─── Activity end"):self.text.index("_vv_activity end")]
        pairs = dict(re.findall(r"(\d+|\*)\)\s+ACTIVITY_OUTCOME=\"(\w+)\"", block))
        self.assertEqual(pairs, {"0": "ok", "124": "timeout", "99": "runaway", "53": "step_limit", "*": "crash"})


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
        self.assertIn("export VERSA_TURN_MARKER=", self.text)

    def test_monitor_gets_marker(self):
        self.assertIn('"${BASELINE_SESSION_SIZE}" "${TURN_MARKER}" &', self.text)
        self.assertIn("line_count=$(( line_count - line_base ))", self.text)


if __name__ == "__main__":
    unittest.main()
