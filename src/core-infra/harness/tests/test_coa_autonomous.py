"""Unit tests for COA Autonomous Mode privilege grant.

Run from core-infra:
  python -m unittest harness.tests.test_coa_autonomous
"""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from privilege_guard import (  # noqa: E402
    coa_autonomous_allowed,
    privilege_escalation_hit,
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


_MIN_POISE = """## CONTEXT MAP — how to read this prompt

{FIRST_CONTACT}

{COA_PRIVILEGE}
"""


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
