"""Which IDE, which tool, and whether a loop may be offered (state_coa_ide_mode.md rules 21–23, IDE-18)."""

from __future__ import annotations

import os
import subprocess
import sys
import unittest

CORE_INFRA = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SRC = os.path.dirname(CORE_INFRA)
sys.path.insert(0, CORE_INFRA)

import ide_probe  # noqa: E402

# What Claude Code 2.1.325 in Cursor set on 2026-11-13 (names; values shortened).
CLAUDE_CODE_IN_CURSOR = {
    "CLAUDECODE": "1",
    "CLAUDE_CODE_ENTRYPOINT": "claude-vscode",
    "CLAUDE_CODE_EXECPATH": "/home/coa/.cursor-server/extensions/anthropic.claude-code-2.1.325-linux-x64",
    "CURSOR_SPAWNED_BY_EXTENSION_ID": "anthropic.claude-code",
    "VSCODE_PID": "2464388",
    "VSCODE_IPC_HOOK": "/run/user/1000/vscode-1.sock",
}


class TestTheIde(unittest.TestCase):
    """Rule 21: the IDE comes from every marker and the parent processes, forks before VS Code."""

    def test_claude_code_in_cursor_is_cursor_not_vs_code(self):
        found = ide_probe.identify(CLAUDE_CODE_IN_CURSOR, [])
        self.assertEqual(found["ide"], ide_probe.IDE_CURSOR)
        self.assertEqual(found["tool"], ide_probe.TOOL_CLAUDE_CODE)

    def test_the_old_table_would_have_said_vs_code(self):
        # None of the Cursor agent's own variables are set under an extension, and VSCODE_PID is.
        for key in ("CURSOR_AGENT", "CURSOR_TRACE_ID", "CURSOR_CONVERSATION_ID"):
            self.assertNotIn(key, CLAUDE_CODE_IN_CURSOR)
        self.assertIn("VSCODE_PID", CLAUDE_CODE_IN_CURSOR)

    def test_a_path_names_the_ide_when_no_variable_does(self):
        env = {"CLAUDECODE": "1", "VSCODE_PID": "1",
               "CLAUDE_CODE_EXECPATH": "/home/coa/.cursor-server/extensions/anthropic.claude-code"}
        self.assertEqual(ide_probe.identify(env, [])["ide"], ide_probe.IDE_CURSOR)
        env["CLAUDE_CODE_EXECPATH"] = "/home/coa/.antigravity-server/extensions/anthropic.claude-code"
        self.assertEqual(ide_probe.identify(env, [])["ide"], ide_probe.IDE_ANTIGRAVITY)
        env["CLAUDE_CODE_EXECPATH"] = "/home/coa/.vscode-server/extensions/anthropic.claude-code"
        self.assertEqual(ide_probe.identify(env, [])["ide"], ide_probe.IDE_VSCODE)

    def test_a_parent_process_names_the_ide_when_the_environment_is_bare(self):
        chain = ["/bin/bash -c probe", "/home/coa/.cursor-server/bin/abc/node --type=extensionHost"]
        self.assertEqual(ide_probe.identify({"CLAUDECODE": "1"}, chain)["ide"], ide_probe.IDE_CURSOR)
        chain = ["/bin/bash", "/home/coa/.vscode-server/bin/abc/node"]
        self.assertEqual(ide_probe.identify({}, chain)["ide"], ide_probe.IDE_VSCODE)

    def test_forks_are_checked_before_vs_code(self):
        env = {"VSCODE_PID": "1", "TERM_PROGRAM": "vscode", "ANTIGRAVITY_CLI_ALIAS": "agy"}
        self.assertEqual(ide_probe.identify(env, [])["ide"], ide_probe.IDE_ANTIGRAVITY)
        env = {"VSCODE_PID": "1", "VSCODE_GIT_ASKPASS_MAIN": "/opt/Antigravity/resources/app/askpass-main.js"}
        self.assertEqual(ide_probe.identify(env, [])["ide"], ide_probe.IDE_ANTIGRAVITY)
        self.assertEqual(ide_probe.identify({"VSCODE_PID": "1"}, [])["ide"], ide_probe.IDE_VSCODE)

    def test_a_word_that_only_contains_the_name_is_not_a_marker(self):
        env = {"VSCODE_PID": "1", "VSCODE_CWD": "/home/coa/precursors/xcursor-themes"}
        self.assertEqual(ide_probe.identify(env, [])["ide"], ide_probe.IDE_VSCODE)

    def test_nothing_found_is_unknown_for_both(self):
        found = ide_probe.identify({"HOME": "/home/coa"}, ["/bin/bash", "sshd: coa"])
        self.assertEqual((found["ide"], found["tool"], found["same_chat_wake"]), ("unknown", "unknown", ""))


class TestTheToolAndTheLoop(unittest.TestCase):
    """Rule 22: whether a loop may be offered follows the tool, not the IDE."""

    def test_each_tool_is_named_from_its_own_marker(self):
        for env, tool in (({"CURSOR_AGENT": "1"}, ide_probe.TOOL_CURSOR_AGENT),
                          ({"CURSOR_TRACE_ID": "x"}, ide_probe.TOOL_CURSOR_AGENT),
                          ({"ANTIGRAVITY_AGENT": "1"}, ide_probe.TOOL_ANTIGRAVITY_AGENT),
                          ({"COPILOT_MODEL": "x", "VSCODE_PID": "1"}, ide_probe.TOOL_COPILOT),
                          ({"CLAUDE_CODE_ENTRYPOINT": "cli"}, ide_probe.TOOL_CLAUDE_CODE)):
            self.assertEqual(ide_probe.identify(env, [])["tool"], tool, env)

    def test_claude_code_has_the_loop_in_every_ide(self):
        for extra in ({"CURSOR_LAYOUT": "x"}, {"ANTIGRAVITY_CLI_ALIAS": "agy"}, {"VSCODE_PID": "1"}, {}):
            found = ide_probe.identify({"CLAUDECODE": "1", **extra}, [])
            self.assertEqual(found["same_chat_wake"], "/loop", extra)

    def test_copilot_and_the_antigravity_agent_have_none(self):
        self.assertEqual(ide_probe.identify({"COPILOT_MODEL": "x", "VSCODE_PID": "1"}, [])["same_chat_wake"], "")
        self.assertEqual(ide_probe.identify({"ANTIGRAVITY_AGENT": "1"}, [])["same_chat_wake"], "")
        self.assertIn("none (one cycle per message)",
                      ide_probe.report(ide_probe.identify({"ANTIGRAVITY_AGENT": "1"}, [])))

    def test_an_unknown_tool_is_told_the_rule_not_refused(self):
        text = ide_probe.report(ide_probe.identify({"VSCODE_PID": "1"}, []))
        self.assertIn("Tool: unknown", text)
        self.assertIn("only if the tool you are running in has its own command", text)

    def test_the_report_names_variables_never_their_values(self):
        env = dict(CLAUDE_CODE_IN_CURSOR, CURSOR_SPAWNED_BY_EXTENSION_ID="secret-value-123")
        self.assertNotIn("secret-value-123", ide_probe.report(ide_probe.identify(env, [])))


class TestTheDoorAndTheShipping(unittest.TestCase):
    """The door runs the probe and keys the loop to the tool; setup ships the probe."""

    def setUp(self):
        with open(os.path.join(SRC, "coa-env", "AGENTS.md"), encoding="utf-8") as fh:
            self.door = fh.read()

    def test_the_door_runs_the_probe_and_asks_for_ide_then_tool(self):
        self.assertIn("python3 /usr/local/lib/versa-agi/ide_probe.py", self.door)
        self.assertLess(self.door.index("**The IDE**"), self.door.index("**The tool**"))
        self.assertIn("which IDE and which tool", self.door)

    def test_no_loop_rule_names_an_ide(self):
        for phrase in ("only on Cursor", "Cursor only", "Cursor same-chat only", "this is Cursor", "Cursor loop"):
            self.assertNotIn(phrase, self.door, phrase)
        self.assertIn("| Claude Code, in any IDE | `/loop` |", self.door)
        self.assertIn("the same in every IDE and for every tool, listed here or not", self.door)

    def test_the_door_and_the_probe_agree_on_which_tools_can_loop(self):
        for tool, command in ide_probe.SAME_CHAT_WAKE.items():
            row = next(line for line in self.door.split("\n") if line.startswith(f"| {tool}"))
            self.assertIn(command, row, tool)
        for tool in ("GitHub Copilot", "Antigravity agent"):
            row = next(line for line in self.door.split("\n") if line.startswith(f"| {tool}"))
            self.assertIn("none", row, tool)

    def test_setup_ships_the_probe_and_it_runs_on_its_own(self):
        with open(os.path.join(SRC, "setup.sh"), encoding="utf-8") as fh:
            setup = fh.read()
        shared = setup[setup.index("_VERSA_LIB_SHARED_PY=("):]
        self.assertIn("  ide_probe.py\n", shared[:shared.index(")")])
        out = subprocess.run([sys.executable, "-I", os.path.join(CORE_INFRA, "ide_probe.py")],
                             capture_output=True, text=True, timeout=20)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertRegex(out.stdout, r"(?m)^IDE: .+\nTool: .+\nSame-chat wake: ")


if __name__ == "__main__":
    unittest.main()
