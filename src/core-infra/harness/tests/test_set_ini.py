"""`agictl system config set-ini`, as agitop's settings tabs call it.

Run:  cd core-infra
      python3 -m unittest harness.tests.test_set_ini
"""

from __future__ import annotations

import ast
import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

CORE_INFRA = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, CORE_INFRA)
sys.path.insert(0, os.path.join(CORE_INFRA, "agictl"))
CLI = os.path.join(CORE_INFRA, "agictl", "cli.py")

try:  # click and rich are not installed on every interpreter the suite runs on
    from click.testing import CliRunner
    import agictl.cli as agictl_cli
except Exception:  # noqa: BLE001
    CliRunner = agictl_cli = None

INI = """[agent]
timeout_minutes=45

[live_chat]
# minutes of quiet before a chat closes
idle_minutes=15

[features]
live_call=false
live_link=false
"""


@unittest.skipIf(agictl_cli is None, "click or rich is not installed in this interpreter")
class TestSetIni(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.ini = os.path.join(self._dir.name, "setup.ini")
        with open(self.ini, "w", encoding="utf-8") as out:
            out.write(INI)

    def set(self, section, key, value, agent_user=""):
        with (
            patch.object(agictl_cli, "SETUP_INI_CANONICAL", self.ini),
            patch.object(agictl_cli, "_sync_ini_to_source", lambda path: None),
            patch.dict(os.environ, {"AGICTL_AGENT_USER": agent_user}),
        ):
            result = CliRunner().invoke(agictl_cli.cli, ["system", "config", "set-ini", section, key, value])
        return result, json.loads(result.output.strip().splitlines()[-1])

    def stored(self):
        with open(self.ini, encoding="utf-8") as handle:
            return handle.read()

    def test_live_chat_minutes_are_saved(self):
        """agitop's Live Chat tab: this failed with UnboundLocalError from 3.7.0 to 3.9.3."""
        result, answer = self.set("live_chat", "idle_minutes", "10")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertTrue(answer["success"])
        self.assertIn("idle_minutes=10\n", self.stored())
        self.assertIn("# minutes of quiet before a chat closes\n", self.stored(), "comments are kept")

    def test_live_chat_minutes_are_checked(self):
        result, answer = self.set("live_chat", "idle_minutes", "soon")
        self.assertEqual(result.exit_code, 1)
        self.assertFalse(answer["success"])
        self.assertIn("idle_minutes=15\n", self.stored())

    def test_live_chat_is_not_changed_by_an_agent_through_set_ini(self):
        """D24: COA has its own command; set-ini names it and changes nothing."""
        for caller in ("agi-clerk", "coa"):
            result, answer = self.set("live_chat", "idle_minutes", "10", agent_user=caller)
            self.assertEqual(result.exit_code, 1)
            self.assertIn("agictl system live-chat set", answer["error"])
        self.assertIn("idle_minutes=15", self.stored())

    def test_the_agent_time_limit_is_still_checked_and_saved(self):
        result, answer = self.set("agent", "timeout_minutes", "60")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("timeout_minutes=60\n", self.stored())
        result, answer = self.set("agent", "timeout_minutes", "not a number")
        self.assertEqual(result.exit_code, 1)
        self.assertIn("timeout_minutes=60\n", self.stored())

    def test_live_link_is_saved(self):
        result, answer = self.set("features", "live_link", "true")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("live_link=true\n", self.stored())
        self.assertIn("live_call=false\n", self.stored())


class TestNoImportHidesAModuleName(unittest.TestCase):
    """A module imported at the top of cli.py and imported again inside a function
    becomes local to that whole function. Any use of it that the second import
    does not come before then fails at run time, on that path only."""

    def test_every_use_follows_the_import_that_makes_the_name_local(self):
        with open(CLI, encoding="utf-8") as handle:
            tree = ast.parse(handle.read())
        top = {a.asname or a.name.split(".")[0] for node in tree.body
               if isinstance(node, (ast.Import, ast.ImportFrom)) for a in node.names}

        def blocks(node):
            for field in ("body", "orelse", "finalbody"):
                seq = getattr(node, field, None)
                if isinstance(seq, list) and seq and isinstance(seq[0], ast.stmt):
                    yield seq
            for handler in getattr(node, "handlers", None) or []:
                yield handler.body

        def block_of(function, target):
            stack = [function]
            while stack:
                node = stack.pop()
                for seq in blocks(node):
                    if target in seq:
                        return seq
                    stack.extend(seq)
            return []

        problems = []
        for function in ast.walk(tree):
            if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            covered: dict[str, set[int]] = {}
            for statement in [n for n in ast.walk(function) if isinstance(n, ast.Import)]:
                for alias in statement.names:
                    name = alias.asname or alias.name.split(".")[0]
                    if name not in top:
                        continue
                    seq = block_of(function, statement)
                    after = seq[seq.index(statement) + 1:] if statement in seq else []
                    covered.setdefault(name, set()).update(id(n) for s in after for n in ast.walk(s))
            for name, reached in covered.items():
                missed = sorted({n.lineno for n in ast.walk(function)
                                 if isinstance(n, ast.Name) and n.id == name and id(n) not in reached})
                if missed:
                    problems.append(f"{function.name}: `{name}` used at line(s) {missed} before or outside its local import")
        self.assertEqual(problems, [])



@unittest.skipIf(agictl_cli is None, "click or rich is not installed in this interpreter")
class TestLiveChatCommand(unittest.TestCase):
    """state_live_chat.md D24 / LCH-34: COA sees Live Chat and changes it when the Primary User asks."""

    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.ini = os.path.join(self._dir.name, "setup.ini")
        with open(self.ini, "w", encoding="utf-8") as out:
            out.write(INI)
        self.link_state = os.path.join(self._dir.name, "link_state")

    def run_cmd(self, *args, caller="coa"):
        from agitop.data import system_reader
        with (
            patch.object(agictl_cli, "SETUP_INI_CANONICAL", self.ini),
            patch.object(agictl_cli, "_sync_ini_to_source", lambda path: None),
            patch.object(system_reader.read_link_state, "__defaults__", (self.link_state, None)),
            patch.dict(os.environ, {"AGICTL_AGENT_USER": caller}),
        ):
            result = CliRunner().invoke(agictl_cli.cli, ["system", "live-chat", *args])
        return result, json.loads(result.output.strip().splitlines()[-1])

    def stored(self):
        with open(self.ini, encoding="utf-8") as handle:
            return handle.read()

    def test_show_says_whether_it_is_on(self):
        result, answer = self.run_cmd()
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertTrue(answer["enabled"])
        self.assertEqual(answer["listening_minutes"], 15)
        self.assertEqual(answer["listening_choices"], ["off", "5", "10", "15", "30"])
        self.assertEqual(answer["live_link"], {"enabled": False, "state": "off"})

    def test_a_sub_agent_may_look(self):
        result, answer = self.run_cmd(caller="agi-clerk")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(answer["listening_minutes"], 15)

    def test_coa_sets_the_listening_time(self):
        result, answer = self.run_cmd("set", "30")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual((answer["was_minutes"], answer["listening_minutes"]), (15, 30))
        self.assertIn("next cycle", answer["applies"])
        self.assertIn("idle_minutes=30", self.stored())
        self.assertIn("# minutes of quiet before a chat closes", self.stored())  # comments kept

    def test_off_turns_live_chat_off(self):
        result, answer = self.run_cmd("set", "off")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertFalse(answer["enabled"])
        self.assertIn("idle_minutes=0", self.stored())
        _, shown = self.run_cmd()
        self.assertFalse(shown["enabled"])

    def test_the_primary_user_sets_it_too(self):
        result, _ = self.run_cmd("set", "5", caller="")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("idle_minutes=5", self.stored())

    def test_other_values_are_refused(self):
        for bad in ("20", "soon", "1", "60"):
            result, answer = self.run_cmd("set", bad)
            self.assertEqual(result.exit_code, 1, bad)
            self.assertIn("off, 5, 10, 15 or 30", answer["error"])
        self.assertIn("idle_minutes=15", self.stored())

    def test_a_sub_agent_cannot_change_anything(self):
        for args in (("set", "30"), ("link", "on")):
            result, answer = self.run_cmd(*args, caller="agi-clerk")
            self.assertEqual(result.exit_code, 1)
            self.assertIn("only COA or the Primary User", answer["error"])
        self.assertIn("idle_minutes=15", self.stored())
        self.assertIn("live_link=false", self.stored())

    def test_coa_switches_live_link(self):
        result, answer = self.run_cmd("link", "on")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertFalse(answer["was_enabled"])
        self.assertTrue(answer["live_link"]["enabled"])
        self.assertIn("within a minute", answer["applies"])
        self.assertIn("live_link=true", self.stored())
        self.assertIn("live_call=false", self.stored())  # the neighbour key is untouched
        result, answer = self.run_cmd("link", "off")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertTrue(answer["was_enabled"])
        self.assertIn("live_link=false", self.stored())

    def test_link_state_is_the_link_services_own_report(self):
        self.run_cmd("link", "on")
        with open(self.link_state, "w", encoding="utf-8") as out:
            out.write("up 1795106057 8 agents\n")
        _, answer = self.run_cmd()
        self.assertEqual(answer["live_link"], {"enabled": True, "state": "up", "detail": "8 agents"})
        with open(self.link_state, "w", encoding="utf-8") as out:
            out.write("down 1795106057 no network\n")
        _, answer = self.run_cmd()
        self.assertEqual(answer["live_link"]["state"], "down")
        self.assertEqual(answer["live_link"]["detail"], "no network")

    def test_link_takes_on_or_off_only(self):
        result = CliRunner().invoke(agictl_cli.cli, ["system", "live-chat", "link", "maybe"])
        self.assertNotEqual(result.exit_code, 0)

if __name__ == "__main__":
    unittest.main()
