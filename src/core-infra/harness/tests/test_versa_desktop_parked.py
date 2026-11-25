"""Versa Desktop is not released: nothing about it reaches an agent on a machine
without it (state_computer_use.md §1.0, PU 2026-11-20).

Run:  python -m unittest harness.tests.test_versa_desktop_parked   (from core-infra)
"""

import os
import re
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

CORE_INFRA = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SRC = os.path.dirname(CORE_INFRA)
sys.path.insert(0, CORE_INFRA)

from harness import triage  # noqa: E402

WORDS = re.compile(r"versa desktop|versa-desktop|heterosphere|perceptron|computer use|computer_use|agictl_desktop|"
                   r"\bdesktop (look|see|open|load|click)\b", re.I)


def _read(*parts):
    with open(os.path.join(*parts), encoding="utf-8") as handle:
        return handle.read()


class TestNothingInWhatEveryAgentReads(unittest.TestCase):
    def test_the_command_reference_in_every_prompt_has_no_desktop(self):
        self.assertIsNone(WORDS.search(_read(CORE_INFRA, "skills", "cli_reference_agent.md")))

    def test_the_full_command_reference_has_no_desktop(self):
        text = _read(CORE_INFRA, "skills", "cli_reference.md")
        self.assertIsNone(WORDS.search(text))
        self.assertNotIn("17b", text)

    def test_no_other_shipped_skill_or_prompt_names_it(self):
        """The desktop skill itself is the one place; it is gated below."""
        hits = []
        for folder in ("skills", "config"):
            for root, _dirs, files in os.walk(os.path.join(CORE_INFRA, folder)):
                for name in files:
                    if not name.endswith((".md", ".txt", ".ini", ".json")) or name == "computer_use.md":
                        continue
                    path = os.path.join(root, name)
                    # The note that tells COA it is not released, and the scope table, may name it.
                    if name in ("3.10.2.md", "skills_scope.ini"):
                        continue
                    if WORDS.search(_read(path)):
                        hits.append(os.path.relpath(path, CORE_INFRA))
        self.assertEqual(hits, [])

    def test_the_command_list_moved_into_the_desktop_skill(self):
        skill = _read(CORE_INFRA, "skills", "computer_use.md")
        self.assertIn("## 6. Command reference", skill)
        self.assertIn("agictl desktop look pu", skill)
        self.assertIn("agictl desktop press pu <key>", skill)


class TestSkillCatalog(unittest.TestCase):
    CATALOG = ('- "communication.md" — Message crafting\n'
               '- "computer_use.md" — The Primary User\'s desktop\n'
               '- "git_operations.md" — Git operations\n')

    def _catalog(self, installed):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "skills_catalog.md")
            with open(path, "w", encoding="utf-8") as out:
                out.write(self.CATALOG)
            env = {"VERSA_DESKTOP_INSTALLED": "1" if installed else "0",
                   "AGICTL_AGENTS_DB": os.path.join(tmp, "none.db"),
                   "AGICTL_SETUP_INI": os.path.join(tmp, "none.ini")}
            with patch.object(triage, "_SKILLS_CATALOG_PATH", path), patch.dict(os.environ, env), \
                    patch.object(triage, "already_loaded_skill_names", return_value=set()):
                return triage.loadable_skills_catalog("coa")

    def test_not_listed_on_a_machine_without_the_desktop(self):
        catalog = self._catalog(installed=False)
        self.assertNotIn("computer_use", catalog)
        self.assertIn("git_operations.md", catalog)

    def test_listed_where_the_desktop_is_installed(self):
        self.assertIn("computer_use.md", self._catalog(installed=True))

    def test_installed_means_the_install_folder_or_the_running_socket(self):
        with tempfile.TemporaryDirectory() as tmp:
            there, gone = os.path.join(tmp, "opt"), os.path.join(tmp, "nope")
            os.mkdir(there)
            with patch.dict(os.environ, {"VERSA_DESKTOP_INSTALLED": ""}):
                with patch.object(triage, "VERSA_DESKTOP_DIRS", (gone, gone)):
                    self.assertFalse(triage.versa_desktop_installed())
                with patch.object(triage, "VERSA_DESKTOP_DIRS", (gone, there)):
                    self.assertTrue(triage.versa_desktop_installed())
        self.assertEqual(triage.VERSA_DESKTOP_DIRS, ("/opt/heterosphere", "/tmp/heterosphere"))


class TestLifelineAndSetup(unittest.TestCase):
    def test_lifeline_leaves_the_skill_out_of_the_catalog_file(self):
        text = _read(CORE_INFRA, "lifeline.sh")
        start = text.index('CATALOG_FILE="/var/lib/versa-agi/skills_catalog.md"')
        block = text[start:text.index("# ─── Protected Agent Safety Check", start)]
        self.assertIn("CATALOG_SKIP=\"AND name != 'computer_use'\"", block)
        self.assertIn("${CATALOG_SKIP} ORDER BY", block)
        self.assertIn('[ ! -d "${VERSA_DESKTOP_DIR:-/opt/heterosphere}" ]', block)

    def _copy_loop(self, installed):
        """Run setup.sh's own skill copy loop against temp folders."""
        text = _read(SRC, "setup.sh")
        start = text.index('  for skill_file in "${SKILLS_SOURCE}"/*.md; do')
        loop = text[start:text.index('  ok "skills/ — shipped skills deployed', start)]
        with tempfile.TemporaryDirectory() as tmp:
            source, dest = os.path.join(tmp, "src"), os.path.join(tmp, "dest")
            os.mkdir(source), os.mkdir(dest)
            for name in ("communication.md", "computer_use.md"):
                with open(os.path.join(source, name), "w", encoding="utf-8") as out:
                    out.write("x")
            with open(os.path.join(dest, "computer_use.md"), "w", encoding="utf-8") as out:
                out.write("from an earlier version")
            present = os.path.join(tmp, "opt")
            if installed:
                os.mkdir(present)
            script = ("chown() { :; }; chmod() { :; }\n"
                      f'SKILLS_SOURCE="{source}"; SKILLS_DEST="{dest}"; WATCHDOG_USER=w; COA_USER=c\n' + loop)
            env = dict(os.environ, VERSA_DESKTOP_DIR=present, VERSA_DESKTOP_SOCKET_DIR=os.path.join(tmp, "sock"))
            done = subprocess.run(["bash", "-c", script], env=env, capture_output=True, text=True)
            self.assertEqual(done.returncode, 0, done.stderr)
            return sorted(os.listdir(dest))

    def test_setup_does_not_copy_it_and_removes_an_earlier_copy(self):
        self.assertEqual(self._copy_loop(installed=False), ["communication.md"])

    def test_setup_copies_it_where_the_desktop_is_installed(self):
        self.assertEqual(self._copy_loop(installed=True), ["communication.md", "computer_use.md"])


class TestToolAndHelp(unittest.TestCase):
    def test_the_tool_is_offered_only_while_the_desktop_runs(self):
        text = _read(CORE_INFRA, "harness", "agent_harness.py")
        self.assertIn('and os.path.isdir(HETEROSPHERE_SOCKET_DIR)):\n    ALL_TOOLS.append(agictl_desktop)', text)

    def test_the_command_group_is_hidden_from_help(self):
        text = _read(CORE_INFRA, "agictl", "desktop_cli.py")
        self.assertIn('@cli.group("desktop", hidden=not versa_desktop_installed())', text)

    def test_skill_list_leaves_it_out(self):
        text = _read(CORE_INFRA, "agictl", "cli.py")
        self.assertIn('rows = [r for r in rows if r["name"] != "computer_use"]', text)


if __name__ == "__main__":
    unittest.main()
