"""COA's sudo grant is seen where the grant folder is root-only
(state_agi_vv_control.md, sudo grant; found on a sentinel 2026-11-21).

Some servers keep /etc/sudoers.d at mode 0750 (Ubuntu 24.04's sudo package ships
it 0755; the Primary User's cloud server had 0750). Lifeline (watchdog) and the
harness (the agent's user) cannot see a file in it, so a grant that was written
read as "did not land". These tests close a folder to the test's own account
to get the same view.

Run:  python -m unittest harness.tests.test_coa_grant_visible   (from core-infra)
"""

import os
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

CORE_INFRA = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, CORE_INFRA)

import privilege_guard  # noqa: E402

LIFELINE = os.path.join(CORE_INFRA, "lifeline.sh")


def _lifeline_function():
    with open(LIFELINE, encoding="utf-8") as handle:
        text = handle.read()
    start = text.index("_coa_grant_landed() {")
    return text[start:text.index("# ─── End COA grant check ───", start)]


@unittest.skipIf(os.geteuid() == 0, "root can read any folder")
class GrantFolderCase(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.tmp = self._dir.name
        self.folder = os.path.join(self.tmp, "sudoers.d")
        os.mkdir(self.folder)
        self.grant = os.path.join(self.folder, "versa_agi_coa_autonomous")
        self.asked = os.path.join(self.tmp, "asked")
        # Stand-in for sudo: records how it was asked; says yes or no.
        self.sudo = os.path.join(self.tmp, "sudo")
        with open(self.sudo, "w", encoding="utf-8") as out:
            out.write(f'#!/bin/bash\necho "$*" >> "{self.asked}"\n'
                      'while [ "$1" = "-n" ] || [ "$1" = "-u" ]; do [ "$1" = "-u" ] && shift; shift; done\n'
                      '[ $# -gt 0 ] && [ "$1" != "true" ] && exec "$@"\n'
                      'exit "${FAKE_SUDO_RC:-0}"\n')
        os.chmod(self.sudo, 0o755)
        self.addCleanup(self._cleanup)

    def _cleanup(self):
        os.chmod(self.folder, 0o755)
        self._dir.cleanup()

    def _write_grant(self):
        with open(self.grant, "w", encoding="utf-8") as out:
            out.write("coa ALL=(ALL) NOPASSWD: ALL\n")

    def _close_folder(self):
        os.chmod(self.folder, 0o000)

    def _questions(self):
        if not os.path.exists(self.asked):
            return []
        with open(self.asked, encoding="utf-8") as handle:
            return handle.read().splitlines()


class TestLifelineCheck(GrantFolderCase):
    def _landed(self, sudo_rc=0):
        env = dict(os.environ, VERSA_COA_SUDOERS=self.grant, VERSA_SUDO_BIN=self.sudo, FAKE_SUDO_RC=str(sudo_rc))
        return subprocess.run(["bash", "-c", _lifeline_function() + '\n_coa_grant_landed "coa"'], env=env).returncode == 0

    def test_the_old_check_misses_a_grant_in_a_closed_folder(self):
        """The fault: `[ -f … ]` from an account that cannot enter the folder."""
        self._write_grant()
        self._close_folder()
        self.assertNotEqual(subprocess.run(["bash", "-c", f'[ -f "{self.grant}" ]']).returncode, 0)

    def test_a_grant_in_a_closed_folder_is_seen_by_asking_sudo(self):
        self._write_grant()
        self._close_folder()
        self.assertTrue(self._landed(sudo_rc=0))
        # Asked as the COA user, without a password prompt.
        self.assertEqual(self._questions()[0], f"-n -u coa {self.sudo} -n true")

    def test_no_grant_in_a_closed_folder_is_not_seen(self):
        self._close_folder()
        self.assertFalse(self._landed(sudo_rc=1))

    def test_a_readable_folder_is_answered_by_the_file_alone(self):
        self.assertFalse(self._landed(sudo_rc=0))   # sudo would say yes, the file is not there
        self.assertEqual(self._questions(), [])
        self._write_grant()
        self.assertTrue(self._landed(sudo_rc=1))
        self.assertEqual(self._questions(), [])

    def test_lifeline_uses_the_check_for_the_privilege_line(self):
        with open(LIFELINE, encoding="utf-8") as handle:
            text = handle.read()
        self.assertIn('if _coa_grant_landed "${AGENT_USER}"; then', text)
        self.assertNotIn("if [ -f /etc/sudoers.d/versa_agi_coa_autonomous ]; then", text)


class TestHarnessGuard(GrantFolderCase):
    def setUp(self):
        super().setUp()
        privilege_guard._SUDO_ANSWER.clear()
        self.addCleanup(privilege_guard._SUDO_ANSWER.clear)

    def _present(self, sudo_rc=0):
        with patch.dict(os.environ, {"VERSA_SUDO_BIN": self.sudo, "FAKE_SUDO_RC": str(sudo_rc), "VERSA_COA_USER": "coa"}):
            return privilege_guard.sudoers_grant_present(self.grant)

    def test_a_grant_in_a_closed_folder_is_seen(self):
        self._write_grant()
        self._close_folder()
        self.assertFalse(os.path.isfile(self.grant))      # what the old check saw
        self.assertTrue(self._present(sudo_rc=0))

    def test_no_grant_in_a_closed_folder(self):
        self._close_folder()
        self.assertFalse(self._present(sudo_rc=1))

    def test_a_readable_folder_never_asks_sudo(self):
        self.assertFalse(self._present(sudo_rc=0))
        self._write_grant()
        self.assertTrue(self._present(sudo_rc=1))
        self.assertEqual(self._questions(), [])

    def test_sudo_is_asked_once_per_process(self):
        self._write_grant()
        self._close_folder()
        for _ in range(4):
            self.assertTrue(self._present())
        # The stand-in records the outer call and the inner one it runs as the COA user.
        self.assertEqual(len([q for q in self._questions() if q.startswith("-n -u coa")]), 1)

    def test_the_scanner_lifts_for_coa_and_stays_for_a_sub_agent(self):
        self._write_grant()
        self._close_folder()
        env = {"VERSA_SUDO_BIN": self.sudo, "FAKE_SUDO_RC": "0", "VERSA_COA_USER": "coa",
               "VERSA_COA_AUTONOMOUS": ""}
        with patch.object(privilege_guard, "SUDOERS_COA_AUTONOMOUS", self.grant), \
                patch.object(privilege_guard.sudoers_grant_present, "__defaults__", (self.grant,)):
            with patch.dict(os.environ, dict(env, VERSA_AGENT_NAME="coa", AGICTL_AGENT_USER="coa")):
                self.assertTrue(privilege_guard.coa_autonomous_allowed())
            with patch.dict(os.environ, dict(env, VERSA_AGENT_NAME="clerk", AGICTL_AGENT_USER="agi-clerk")):
                self.assertFalse(privilege_guard.coa_autonomous_allowed())

    def test_setting_on_and_grant_in_a_closed_folder_counts_as_landed(self):
        self._write_grant()
        self._close_folder()
        ini = os.path.join(self.tmp, "setup.ini")
        with open(ini, "w", encoding="utf-8") as out:
            out.write("[coa]\nautonomous=true\n")
        with patch.dict(os.environ, {"VERSA_SUDO_BIN": self.sudo, "FAKE_SUDO_RC": "0", "VERSA_COA_USER": "coa"}):
            self.assertTrue(privilege_guard.grant_landed_on_disk(ini, self.grant))


class TestRemoteGrantRepairs(unittest.TestCase):
    def test_a_grant_from_the_app_is_written_again_when_the_setting_is_on_but_not_in_effect(self):
        with open(os.path.join(CORE_INFRA, "agictl", "cli.py"), encoding="utf-8") as handle:
            text = handle.read()
        start = text.index("def _apply_remote_coa_autonomous(desired):")
        body = text[start:start + 1400]
        self.assertIn("if not want or sudoers_grant_present():\n            return \"unchanged\"", body)


if __name__ == "__main__":
    unittest.main()
