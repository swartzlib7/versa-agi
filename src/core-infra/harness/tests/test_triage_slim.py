"""Unit tests for purpose-shaped triage (inbox + registry + catalog + preamble).

Run from core-infra:
  python -m unittest harness.tests.test_triage_slim
"""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from harness.conversation_trim import (  # noqa: E402
    CONVERSATION_CONTEXT_MAX_CHARS,
    NEW_MESSAGES_MARK,
    trim_conversation_preserving_unread,
)
from harness.triage import (  # noqa: E402
    TRIAGE_PROMPT,
    TriageResult,
    _ALWAYS_IN_PROMPT,
    _record_inputs_used,
    already_loaded_skill_names,
    adverse_signals,
    build_triage_context,
    format_loadable_skills_block,
)


class TestBuildTriageContext(unittest.TestCase):
    def test_provenance_header_and_no_execution_order(self):
        result = TriageResult(
            classification="work_request",
            confidence=0.9,
            strategy_notes="Inbound asks for feature X; recommend git + SE.",
            task_actions=["acknowledge-sender", "implement-feature-x"],
            skills_to_inject=["git_operations.md"],
            signal_results={"direction_clarity": True, "purpose_clarity": False},
            inputs_used=["wake", "inbox", "registry", "skills-catalog"],
            correlations=[{"project_id": 26, "task_id": 273, "note": "page builder", "certainty": 0.9}],
            ack_advice={"posture": "reply", "note": "human assigned work", "certainty": 0.95},
        )
        text = build_triage_context(result)
        self.assertIn("TRIAGE RESULT (advisory)", text)
        self.assertIn("Triage node", text)
        self.assertIn("Inputs used: wake | inbox | registry | skills-catalog", text)
        self.assertIn("Not used by triage:", text)
        self.assertIn("Advisory:", text)
        self.assertIn("Inbound asks for feature X", text)
        self.assertIn("Classification: **work_request**", text)
        self.assertIn("Skills recommended", text)
        self.assertIn("Ack advice:", text)
        self.assertIn("Correlations:", text)
        self.assertNotIn("Execution Order", text)
        self.assertNotIn("COMMUNICATE FIRST", text)
        self.assertNotIn("mark-processed", text.lower())
        self.assertNotIn("games-digest", text)

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

    def test_attachment_flag_does_not_auto_view(self):
        result = TriageResult(
            classification="follow_up",
            confidence=0.8,
            has_attachments=True,
            inputs_used=["wake", "skills-catalog", "inbox", "attachment-enrich"],
        )
        text = build_triage_context(result)
        self.assertIn("attachment", text.lower())
        self.assertIn("poise", text.lower())
        self.assertIn("Do not view or load", text)
        self.assertIn("explicitly asked", text)
        self.assertNotIn("before replying", text)

    def test_informational_advisory_distinguishes_human_vs_peer(self):
        result = TriageResult(
            classification="informational",
            confidence=0.95,
            strategy_notes="Likely peer-agent standing-by; consider silence.",
            inputs_used=["wake", "inbox", "skills-catalog"],
            ack_advice={"posture": "silent", "note": "ack-loop risk", "certainty": 0.8},
        )
        text = build_triage_context(result)
        self.assertIn("advisory", text.lower())
        self.assertIn("Suggested posture:", text)
        self.assertIn("human", text.lower())
        self.assertIn("peer-agent", text.lower())
        self.assertNotIn("Do not send", text)
        self.assertNotIn("mark-processed", text.lower())

    def test_preamble_says_advisory_not_orders(self):
        result = TriageResult(
            classification="follow_up",
            confidence=0.9,
            strategy_notes="Suggest brief warm ack to human sender.",
            inputs_used=["wake", "inbox", "skills-catalog"],
        )
        text = build_triage_context(result)
        self.assertIn("not orders", text.lower())


class TestRecordInputsUsed(unittest.TestCase):
    def test_inbox_and_registry_not_games(self):
        used = _record_inputs_used(
            wake_prompt="hi",
            inbox_context="1 unread inbound",
            registry_context="#26 | Builder",
            routing_context={"mode": "pool"},
        )
        self.assertIn("wake", used)
        self.assertIn("inbox", used)
        self.assertIn("registry", used)
        self.assertIn("routing", used)
        self.assertNotIn("games-digest", used)
        self.assertNotIn("conversation(last-N)", used)
        self.assertNotIn("active-tasks", used)


class TestPromptContract(unittest.TestCase):
    def test_prompt_has_inbox_not_games(self):
        self.assertIn("{inbox_context}", TRIAGE_PROMPT)
        self.assertIn("{registry_context}", TRIAGE_PROMPT)
        self.assertNotIn("{games_context}", TRIAGE_PROMPT)
        self.assertNotIn("last 5 messages", TRIAGE_PROMPT)
        self.assertIn("no new human substance", TRIAGE_PROMPT)


class TestLoadableCatalog(unittest.TestCase):
    def test_always_loaded_not_reoffered_names(self):
        names = already_loaded_skill_names("coa")
        self.assertTrue(_ALWAYS_IN_PROMPT <= names)
        self.assertIn("memory_management.md", names)
        self.assertIn("communication_basic.md", names)

    def test_catalog_block_header(self):
        block = format_loadable_skills_block("coa")
        self.assertIn("SKILLS AVAILABLE TO LOAD", block)
        self.assertIn("cat .agent/skills/", block)


class TestConversationTrim(unittest.TestCase):
    def test_default_limit_is_40k(self):
        self.assertEqual(CONVERSATION_CONTEXT_MAX_CHARS, 40000)

    def test_preserves_unread_when_over_limit(self):
        unread = (
            f"{NEW_MESSAGES_MARK}\n"
            "[!] KEEP THIS UNREAD BODY FROM THE PRIMARY USER — IDE hold and snapshot.\n"
            "--- END NEW MESSAGES ---\n"
        )
        old = "OLD HISTORY " * 2000
        blob = old + unread
        out = trim_conversation_preserving_unread(blob, max_chars=800)
        self.assertIn("KEEP THIS UNREAD BODY", out)
        self.assertIn(NEW_MESSAGES_MARK, out)
        self.assertLess(len(out), len(blob))
        self.assertTrue(out.endswith(unread) or unread in out)

    def test_head_cut_would_have_dropped_unread(self):
        unread = f"{NEW_MESSAGES_MARK}\nUNREAD TAIL UNIQUE TOKEN xyzzy\n"
        blob = ("HEAD " * 500) + unread
        naive = blob[:200]
        self.assertNotIn("xyzzy", naive)
        preserved = trim_conversation_preserving_unread(blob, max_chars=400)
        self.assertIn("xyzzy", preserved)

    def test_unread_alone_over_limit_is_kept(self):
        unread = NEW_MESSAGES_MARK + "\n" + ("BIG UNREAD " * 200)
        out = trim_conversation_preserving_unread(unread, max_chars=100)
        self.assertEqual(out, unread)


class TestAdverseSignals(unittest.TestCase):
    def test_pending_question_false_is_not_adverse(self):
        signals = {
            "direction_clarity": True,
            "purpose_clarity": True,
            "contradiction_check": False,
            "task_correlation": True,
            "project_correlation": True,
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
                "task_correlation": True,
                "project_correlation": True,
                "pending_question": False,
                "parallel_work_viable": True,
                "risk_assessment": False,
            },
            inputs_used=["wake", "inbox", "skills-catalog"],
        )
        text = build_triage_context(result)
        self.assertNotIn("pending_question", text)
        self.assertNotIn("Adverse signals:", text)
        self.assertNotIn("Negative signals:", text)


class TestMalformedTriageJson(unittest.TestCase):
    """Model returns wrong JSON types (e.g. ack_advice as a bare string)."""

    def _run(self, payload: dict) -> TriageResult:
        try:
            import langchain_core  # noqa: F401
        except ImportError:
            self.skipTest("langchain_core not installed")
        from unittest import mock
        import json as _json
        from harness import triage as triage_mod

        class _Llm:
            def invoke(self, _msgs):
                return type("R", (), {"content": _json.dumps(payload)})()

        with mock.patch.object(triage_mod, "loadable_skills_catalog", return_value=""), \
             mock.patch.object(triage_mod, "already_loaded_skill_names", return_value=set()), \
             mock.patch.object(triage_mod, "_gated_skill_filenames", return_value=set()):
            return triage_mod.run_triage(
                _Llm(), "wake", inbox_context="inbox", registry_context="registry",
            )

    def test_string_ack_advice_becomes_posture(self):
        result = self._run({"classification": "work_request", "ack_advice": "reply"})
        self.assertEqual(result.ack_advice, {"posture": "reply"})
        self.assertIn("Ack advice: reply", build_triage_context(result))

    def test_wrong_types_do_not_crash_context(self):
        result = self._run({
            "classification": "work_request",
            "confidence": "high",
            "ack_advice": ["reply"],
            "signal_results": "ok",
            "task_actions": "none",
            "correlations": "none",
            "skills_to_inject": "work_initiation",
            "skills_recommended": "x",
        })
        self.assertEqual(result.confidence, 0.5)
        self.assertEqual(result.ack_advice, {})
        self.assertEqual(result.signal_results, {})
        self.assertEqual(result.task_actions, [])
        self.assertEqual(result.correlations, [])
        self.assertEqual(result.skills_to_inject, ["work_initiation.md"])
        build_triage_context(result)


if __name__ == "__main__":
    unittest.main()
