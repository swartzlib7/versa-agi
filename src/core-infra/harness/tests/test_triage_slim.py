"""Unit tests for triage slim-down (altitude + provenance preamble).

Run from core-infra:
  python -m unittest harness.tests.test_triage_slim
"""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from harness.triage import (  # noqa: E402
    TriageResult,
    adverse_signals,
    build_triage_context,
    _record_inputs_used,
)


class TestBuildTriageContext(unittest.TestCase):
    def test_provenance_header_and_no_execution_order(self):
        result = TriageResult(
            classification="work_request",
            confidence=0.9,
            strategy_notes="Implement feature X; inject git + SE skills.",
            task_actions=["acknowledge-sender", "implement-feature-x"],
            skills_to_inject=["communication.md", "git_operations.md"],
            signal_results={"direction_clarity": True, "purpose_clarity": False},
            inputs_used=["wake", "active-tasks", "skills-catalog", "games-digest"],
        )
        text = build_triage_context(result)
        self.assertIn("TRIAGE RESULT (advisory)", text)
        self.assertIn("Triage node", text)
        self.assertIn("Inputs used: wake | active-tasks | skills-catalog | games-digest", text)
        self.assertIn("Not used by triage:", text)
        self.assertIn("Strategic brief:", text)
        self.assertIn("Implement feature X", text)
        self.assertIn("Classification: **work_request**", text)
        self.assertNotIn("Execution Order", text)
        self.assertNotIn("COMMUNICATE FIRST", text)
        self.assertNotIn("mark-processed", text.lower())

    def test_clarification_note_is_one_line(self):
        result = TriageResult(
            classification="clarification_needed",
            confidence=0.4,
            strategy_notes="Need scope for deadline.",
            inputs_used=["wake", "skills-catalog"],
        )
        text = build_triage_context(result)
        self.assertIn("clarification_needed", text)
        self.assertIn("requirements_elicitation", text)
        self.assertNotIn("### Execution Order", text)

    def test_attachment_flag_points_to_skills(self):
        result = TriageResult(
            classification="follow_up",
            confidence=0.8,
            has_attachments=True,
            inputs_used=["wake", "skills-catalog", "attachment-enrich"],
        )
        text = build_triage_context(result)
        self.assertIn("attachment", text.lower())
        self.assertIn("poise", text.lower())


class TestRecordInputsUsed(unittest.TestCase):
    def test_games_and_routing_flags(self):
        used = _record_inputs_used(
            wake_prompt="hi",
            tasks_context="task 1",
            conversation_context="(none)",
            games_context="Game #1: Launch",
            routing_context={"mode": "pool"},
        )
        self.assertIn("wake", used)
        self.assertIn("active-tasks", used)
        self.assertIn("games-digest", used)
        self.assertIn("routing", used)
        self.assertNotIn("conversation(last-N)", used)


class TestAdverseSignals(unittest.TestCase):
    def test_pending_question_false_is_not_adverse(self):
        signals = {
            "direction_clarity": True,
            "purpose_clarity": True,
            "contradiction_check": False,
            "historical_context": True,
            "task_correlation": True,
            "project_correlation": True,
            "memory_conflict": False,
            "pending_question": False,
            "parallel_work_viable": True,
            "risk_assessment": False,
        }
        self.assertEqual(adverse_signals(signals), [])

    def test_pending_question_true_is_adverse(self):
        self.assertEqual(
            adverse_signals({"pending_question": True, "direction_clarity": True}),
            ["pending_question"],
        )

    def test_missing_clarity_is_adverse(self):
        self.assertIn(
            "direction_clarity",
            adverse_signals({"direction_clarity": False, "pending_question": False}),
        )

    def test_preamble_omits_spurious_pending_question(self):
        result = TriageResult(
            classification="follow_up",
            confidence=0.93,
            strategy_notes="Clear follow-up.",
            signal_results={
                "direction_clarity": True,
                "purpose_clarity": True,
                "contradiction_check": False,
                "historical_context": True,
                "task_correlation": True,
                "project_correlation": True,
                "memory_conflict": False,
                "pending_question": False,
                "parallel_work_viable": True,
                "risk_assessment": False,
            },
            inputs_used=["wake", "skills-catalog"],
        )
        text = build_triage_context(result)
        self.assertNotIn("pending_question", text)
        self.assertNotIn("Adverse signals:", text)
        self.assertNotIn("Negative signals:", text)


if __name__ == "__main__":
    unittest.main()
