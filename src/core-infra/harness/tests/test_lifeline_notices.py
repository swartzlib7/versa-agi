"""Lifeline's own notices to the Primary User: circuit breaker and token budget
(state_provider_alerts.md rule 9). Runs the Lifeline functions against a stand-in `agictl`."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest

CORE_INFRA = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
LIFELINE = os.path.join(CORE_INFRA, "lifeline.sh")
CLI = os.path.join(CORE_INFRA, "agictl", "cli.py")
AGICTL = "/usr/local/bin/agictl"

STAND_IN = r'''#!/usr/bin/env bash
{ printf 'config=%s\n' "${AGICTL_CONFIG:-}"; printf 'arg=%s\n' "$@"; } >> "$CALLS"
exit "${STAND_IN_EXIT:-0}"
'''


def _source() -> str:
    with open(LIFELINE, encoding="utf-8") as handle:
        return handle.read()


def _function(name: str) -> str:
    """The text of one top-level shell function of lifeline.sh."""
    text = _source()
    start = text.index(f"\n{name}() {{\n") + 1
    return text[start:text.index("\n}\n", start) + 3]


def _send_commands() -> list[str]:
    """Every `agictl message send …` command in Lifeline, with `\\` line continuations joined."""
    joined = re.sub(r"\\\n\s*", " ", _source())
    return [line[line.index("message send"):] for line in joined.split("\n")
            if f"{AGICTL} message send" in line]


@unittest.skipUnless(shutil.which("bash") and shutil.which("jq"), "needs bash and jq")
class TestNotifyPrimaryUser(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.work = self._dir.name
        self.stand_in = os.path.join(self.work, "agictl")
        with open(self.stand_in, "w") as handle:
            handle.write(STAND_IN)
        os.chmod(self.stand_in, 0o755)
        self.config = os.path.join(self.work, "coa_config.json")
        self.write_config({"primary_user": {"uid": "pu-uid-1"}})

    def write_config(self, value):
        with open(self.config, "w") as handle:
            json.dump(value, handle)

    def run_function(self, *command, exit_code=0):
        functions = (_function("notify_primary_user") + _function("exit_code_words")
                     + _function("breaker_notice_text")).replace(AGICTL, self.stand_in)
        script = ('set -euo pipefail\nlog() { echo "$*" >> "$WORK/log"; }\n'
                  f'COA_CONFIG="{self.config}"\n{functions}\n"$@"\n')
        env = dict(os.environ, WORK=self.work, CALLS=os.path.join(self.work, "calls"),
                   STAND_IN_EXIT=str(exit_code))
        done = subprocess.run(["bash", "-c", script, "bash", *command], env=env, capture_output=True,
                              text=True, timeout=30)
        return done.returncode, done.stdout, self.read("log"), self.read("calls")

    def read(self, name):
        path = os.path.join(self.work, name)
        if not os.path.isfile(path):
            return ""
        with open(path, encoding="utf-8") as handle:
            return handle.read()

    def test_the_notice_is_sent_in_the_form_the_command_takes(self):
        code, _out, log, calls = self.run_function("notify_primary_user", "Circuit breaker (coa)", "Agent paused.")
        self.assertEqual(code, 0)
        self.assertEqual(calls.strip().split("\n"), [
            f"config={self.config}", "arg=message", "arg=send", "arg=pu-uid-1", "arg=Agent paused.",
            "arg=--mode", "arg=typed",
        ])
        self.assertEqual(log.strip(), "NOTIFY: Circuit breaker (coa) — Primary User told")

    def test_a_failed_send_is_logged_and_reported(self):
        code, _out, log, calls = self.run_function("notify_primary_user", "Circuit breaker (coa)", "x", exit_code=1)
        self.assertEqual(code, 1)
        self.assertIn("arg=send", calls)
        self.assertEqual(log.strip(),
                         "WARN: Circuit breaker (coa) — could not tell the Primary User (message send failed)")

    def test_without_a_primary_user_nothing_is_sent_and_the_log_says_so(self):
        self.write_config({"primary_user": {}})
        code, _out, log, calls = self.run_function("notify_primary_user", "Token budget exceeded (charlie)", "x")
        self.assertEqual(code, 1)
        self.assertEqual(calls, "")
        self.assertIn("WARN: Token budget exceeded (charlie) — could not tell the Primary User", log)

    def test_the_breaker_notice_says_who_why_and_how_to_bring_it_back(self):
        code, out, _log, _calls = self.run_function("breaker_notice_text", "coa", "5 consecutive failures", "1")
        self.assertEqual(code, 0)
        self.assertIn("Agent 'coa' is paused", out)
        self.assertIn("(5 consecutive failures; last failure: the cycle ended with an error)", out)
        self.assertNotIn("exit code", out)
        self.assertIn("sudo agictl agent activate coa", out)
        self.assertIn("Clear Circuit Breaker", out)
        self.assertTrue(out.endswith("If you are not sure what to do, contact your system administrator."))

    def test_the_last_failure_is_given_in_words(self):
        words = {"1": "the cycle ended with an error", "42": "the cycle had an input error",
                 "55": "the same tool call was repeated", "97": "the cycle stalled",
                 "99": "a runaway cycle was stopped", "7": "exit code 7", "": "", "?": ""}
        for exit_code, expected in words.items():
            code, out, _log, _calls = self.run_function("exit_code_words", exit_code)
            self.assertEqual((code, out), (0, expected), exit_code)
        # Every exit code the circuit breaker counts has words.
        counted = re.search(r"exit_code IN \(([0-9, ]+)\)", _source()).group(1).replace(" ", "").split(",")
        self.assertEqual(sorted(counted), sorted(k for k in words if k.isdigit() and k != "7"))

    def test_a_notice_without_a_known_last_failure_still_reads_well(self):
        _code, out, _log, _calls = self.run_function("breaker_notice_text", "coa", "20 failures in last hour", "?")
        self.assertIn("(20 failures in last hour)", out)
        _code, out, _log, _calls = self.run_function("breaker_notice_text", "coa", "", "")
        self.assertIn("(repeated failures)", out)

    def test_a_late_notice_reads_the_same_as_the_first(self):
        """The retry builds the text from the stored status reason."""
        source = _source()
        start = source.index('      CB_WHY="${CB_WHY#Auto-frozen: }"')
        parse = source[start:source.index("      if notify_primary_user", start)]
        self.assertIn("status_message='Auto-frozen: ${CB_TRIGGER} (last exit: ${CB_LAST_EXIT})'", source)
        functions = _function("exit_code_words") + _function("breaker_notice_text")
        script = (f'set -euo pipefail\n{functions}\nCB_WHY="$1"\n{parse}\n'
                  'breaker_notice_text coa "${CB_WHY}" "${CB_LATE_EXIT}"\n')
        late = subprocess.run(["bash", "-c", script, "bash", "Auto-frozen: 5 consecutive failures (last exit: 97)"],
                              capture_output=True, text=True, timeout=30)
        self.assertEqual(late.returncode, 0, late.stderr)
        _code, first, _log, _calls = self.run_function("breaker_notice_text", "coa", "5 consecutive failures", "97")
        self.assertEqual(late.stdout, first)
        self.assertIn("last failure: the cycle stalled", first)


class TestLifelineSendCommands(unittest.TestCase):
    """Every message Lifeline sends uses arguments `agictl message send` has."""

    def test_send_takes_a_uid_and_a_text_and_no_token(self):
        with open(CLI, encoding="utf-8") as handle:
            text = handle.read()
        start = text.index('@message.command("send")')
        decorators = text[start:text.index("def message_send(", start)]
        self.assertEqual(re.findall(r'@click\.argument\("(\w+)"\)', decorators), ["contact_uid", "text"])
        self.assertNotIn('"--token"', decorators)

    def test_every_send_in_lifeline_is_uid_text_then_options(self):
        commands = _send_commands()
        self.assertGreaterEqual(len(commands), 5)
        shape = re.compile(r'^message send "\$\{\w+\}" "\$\{\w+\}" --mode (typed|translate|speak|speak_translated)\b')
        for command in commands:
            self.assertRegex(command, shape)
            self.assertNotIn("--token", command)

    def test_the_three_notices_go_through_the_one_function(self):
        source = _source()
        for what in ('notify_primary_user "Token budget exceeded (${AGENT_NAME})"',
                     'notify_primary_user "Token budget at ${BUDGET_PCT}% (${AGENT_NAME})"',
                     'notify_primary_user "Circuit breaker (${AGENT_NAME})"'):
            self.assertIn(what, source)
        self.assertNotIn("COA_TOKEN", source)

    def test_an_unsent_breaker_notice_is_marked_and_tried_again_before_the_hold(self):
        source = _source()
        mark = 'CB_NOTICE_PENDING="/var/lib/versa-agi/${AGENT_NAME}/breaker_notice_pending"'
        retry = 'notify_primary_user "Circuit breaker (${AGENT_NAME}), sent late"'
        hold = "skipping spawn (assign a model or run 'agictl agent activate ${AGENT_NAME}')"
        trip = ': > "${CB_NOTICE_PENDING}"'
        for part in (mark, retry, hold, trip):
            self.assertIn(part, source)
        self.assertLess(source.index(mark), source.index(retry))
        self.assertLess(source.index(retry), source.index(hold), "a paused agent's pass ends at the hold")
        self.assertLess(source.index(hold), source.index(trip))
        # The text is built from the stored reason, never read from a file.
        self.assertNotIn("/tmp/versa-agi-breaker", source)


if __name__ == "__main__":
    unittest.main()
