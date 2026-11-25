"""The work-cycle rules every agent gets (state_model_drivers.md §2.7 B15)."""

from __future__ import annotations

import os
import unittest

CORE_INFRA = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
COA_POISE = os.path.join(CORE_INFRA, "config", "coa_poise.md")
AGENT_POISE = os.path.join(CORE_INFRA, "config", "roles", "agent_poise.md")
PLAN_RULE = "**Say your plan before you act.**"
LIVE_SITUATION = "## ── LIVE SITUATION"


def _rule(path: str) -> tuple[str, int, int]:
    """The plan rule's line, its position, and where the per-cycle data starts (-1 when absent)."""
    with open(path, encoding="utf-8") as handle:
        text = handle.read()
    lines = [line for line in text.split("\n") if line.startswith(PLAN_RULE)]
    return (lines[0] if len(lines) == 1 else ""), text.find(PLAN_RULE), text.find(LIVE_SITUATION)


class TestPlanRule(unittest.TestCase):
    def test_coa_and_sub_agents_get_the_same_rule(self):
        coa, _, _ = _rule(COA_POISE)
        agent, _, _ = _rule(AGENT_POISE)
        self.assertTrue(coa, "coa_poise.md must carry the plan rule once")
        self.assertEqual(coa, agent)

    def test_rule_says_what_to_write_and_that_it_is_not_sent(self):
        rule, _, _ = _rule(COA_POISE)
        self.assertIn("several tool calls", rule)
        self.assertIn("what you are going to do and why", rule)
        self.assertIn("when the plan changes", rule)
        self.assertIn("agictl message send", rule)

    def test_rule_is_in_the_fixed_part_of_the_prompt(self):
        """Before the per-cycle data, so the prompt prefix stays the same from cycle to cycle."""
        _, at, live = _rule(COA_POISE)
        self.assertGreater(live, 0)
        self.assertLess(at, live)


if __name__ == "__main__":
    unittest.main()
