"""Unit tests for COA Autonomous Mode privilege grant.

Run from core-infra:
  python -m unittest harness.tests.test_coa_autonomous
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from privilege_guard import (  # noqa: E402
    coa_autonomous_allowed,
    coa_autonomous_changed_since_sync,
    grant_landed_on_disk,
    install_role_change_allowed,
    privilege_escalation_hit,
    refuse_agent_coa_autonomous,
    sudoers_line,
)

_SCRIPTS = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "scripts")
sys.path.insert(0, _SCRIPTS)
from apply_coa_install_role import apply  # noqa: E402


class TestPrivilegeGuard(unittest.TestCase):
    def test_hit_sudo(self):
        self.assertEqual(privilege_escalation_hit('bash "sudo apt-get update"'), "sudo")

    def test_hit_su(self):
        self.assertEqual(privilege_escalation_hit("su - root"), "su")

    def test_clean_command(self):
        self.assertIsNone(privilege_escalation_hit('bash "ls -la"'))

    def test_allowed_only_coa_with_flag(self):
        self.assertTrue(
            coa_autonomous_allowed({"VERSA_COA_AUTONOMOUS": "1", "VERSA_AGENT_NAME": "coa"})
        )
        self.assertFalse(
            coa_autonomous_allowed({"VERSA_COA_AUTONOMOUS": "1", "VERSA_AGENT_NAME": "web-dev"})
        )
        self.assertFalse(
            coa_autonomous_allowed({"VERSA_COA_AUTONOMOUS": "", "VERSA_AGENT_NAME": "coa"})
        )
        self.assertFalse(coa_autonomous_allowed({}))

    def test_sudoers_line(self):
        self.assertEqual(sudoers_line("coa"), "coa ALL=(ALL) NOPASSWD: ALL\n")
        self.assertEqual(sudoers_line("agi-box"), "agi-box ALL=(ALL) NOPASSWD: ALL\n")
        with self.assertRaises(ValueError):
            sudoers_line("coa; rm -rf /")

    def test_grant_landed_on_disk(self):
        with tempfile.TemporaryDirectory() as tmp:
            ini = Path(tmp) / "setup.ini"
            sudoers = Path(tmp) / "versa_agi_coa_autonomous"
            ini.write_text("[coa]\nautonomous=true\n", encoding="utf-8")
            self.assertFalse(grant_landed_on_disk(str(ini), str(sudoers)))
            sudoers.write_text("coa ALL=(ALL) NOPASSWD: ALL\n", encoding="utf-8")
            self.assertTrue(grant_landed_on_disk(str(ini), str(sudoers)))
            ini.write_text("[coa]\nautonomous=false\n", encoding="utf-8")
            self.assertFalse(grant_landed_on_disk(str(ini), str(sudoers)))

    def test_coa_user_stamp_without_env_flag_is_not_enough_in_tests(self):
        """Passed env dict must not consult live disk (wrapper-strip tests)."""
        self.assertFalse(
            coa_autonomous_allowed({"AGICTL_AGENT_USER": "coa"})
        )

    def test_live_sudoers_file_lifts_without_env_flag(self):
        """Live process: sudoers file + COA stamp lifts the text scanner."""
        from unittest.mock import patch

        with patch.dict(
            os.environ,
            {"VERSA_AGENT_NAME": "coa", "VERSA_COA_AUTONOMOUS": ""},
            clear=False,
        ), patch("privilege_guard.os.path.isfile", return_value=True):
            self.assertTrue(coa_autonomous_allowed())

    def test_live_no_sudoers_keeps_scanner(self):
        from unittest.mock import patch

        with patch.dict(
            os.environ,
            {"VERSA_AGENT_NAME": "coa", "VERSA_COA_AUTONOMOUS": ""},
            clear=False,
        ), patch("privilege_guard.os.path.isfile", return_value=False):
            self.assertFalse(coa_autonomous_allowed())


_MIN_POISE = """## CONTEXT MAP — how to read this prompt

{FIRST_CONTACT}

{COA_PRIVILEGE}
"""


class TestAgentDisarmPolicy(unittest.TestCase):
    def test_pu_may_enable_and_disable(self):
        self.assertIsNone(refuse_agent_coa_autonomous(True, None, "coa"))
        self.assertIsNone(refuse_agent_coa_autonomous(False, "", "coa"))

    def test_coa_cannot_enable(self):
        err = refuse_agent_coa_autonomous(True, "coa", "coa")
        self.assertIsNotNone(err)
        self.assertIn("cannot enable", err)

    def test_coa_may_disarm(self):
        self.assertIsNone(refuse_agent_coa_autonomous(False, "coa", "coa"))

    def test_sub_agent_cannot_disarm(self):
        err = refuse_agent_coa_autonomous(False, "agi-web", "coa")
        self.assertIsNotNone(err)
        self.assertIn("Only the COA", err)


class TestSyncChangeDetection(unittest.TestCase):
    def test_no_record_pushes(self):
        self.assertTrue(coa_autonomous_changed_since_sync(False, None))

    def test_agitop_grant_pushes(self):
        self.assertTrue(coa_autonomous_changed_since_sync(True, False))

    def test_disarm_pushes(self):
        self.assertTrue(coa_autonomous_changed_since_sync(False, True))

    def test_unchanged_pulls(self):
        self.assertFalse(coa_autonomous_changed_since_sync(False, False))
        self.assertFalse(coa_autonomous_changed_since_sync(True, True))


class TestInstallRolePromote(unittest.TestCase):
    def test_sentinel_to_normal(self):
        ok, err = install_role_change_allowed("sentinel", "normal")
        self.assertTrue(ok)
        self.assertEqual(err, "")

    def test_normal_to_sentinel_refused(self):
        ok, err = install_role_change_allowed("normal", "sentinel")
        self.assertFalse(ok)
        self.assertIn("No downgrade", err)

    def test_same_role_ok(self):
        ok, _ = install_role_change_allowed("sentinel", "sentinel")
        self.assertTrue(ok)

    def test_invalid_role(self):
        ok, err = install_role_change_allowed("normal", "mobile")
        self.assertFalse(ok)
        self.assertIn("normal", err)


class TestApplyCoaInstallRole(unittest.TestCase):
    def test_normal_keeps_placeholders(self):
        out = apply(_MIN_POISE, "normal")
        self.assertIn("{COA_PRIVILEGE}", out)
        self.assertNotIn("SENTINEL MODE", out)

    def test_missing_privilege_placeholder_fails(self):
        with self.assertRaises(SystemExit):
            apply("## CONTEXT MAP — how to read this prompt\n{FIRST_CONTACT}\n", "normal")

    def test_sentinel_inserts_banner(self):
        out = apply(_MIN_POISE, "sentinel")
        self.assertIn("SENTINEL MODE", out)
        self.assertIn("{COA_PRIVILEGE}", out)


if __name__ == "__main__":
    unittest.main()
