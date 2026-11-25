"""The judging rules of run_tests.py: when three interpreters' runs count as clean."""

from __future__ import annotations

import os
import sys
import unittest

CORE_INFRA = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, CORE_INFRA)

from harness.tests import run_tests  # noqa: E402

A = "harness.tests.test_a.TestA.test_one"
B = "harness.tests.test_b.TestB.test_two"
SKIP_B = run_tests.MODULE_SKIP + "harness.tests.test_b"
PAID = "harness.tests.test_model_driver_live.TestLiveModelDrivers.test_selected_output_bindings"


class TestJudge(unittest.TestCase):
    def test_everything_passing_somewhere_is_clean(self):
        results = {"harness": {A: ["ok", ""], SKIP_B: ["skip", "click is not installed"]},
                   "agictl": {A: ["skip", "LangChain not available"], B: ["ok", ""]}}
        self.assertEqual(run_tests.judge(results), [])

    def test_a_failure_on_one_interpreter_is_not_hidden_by_a_pass_on_another(self):
        results = {"harness": {A: ["fail", "Traceback\nAssertionError: 1 != 2"]}, "agictl": {A: ["ok", ""]}}
        problems = run_tests.judge(results)
        self.assertEqual(len(problems), 1)
        self.assertIn("FAIL on harness", problems[0])
        self.assertIn("AssertionError: 1 != 2", problems[0])

    def test_an_error_is_reported_with_its_interpreter(self):
        results = {"system": {"unittest.loader._FailedTest.harness.tests.test_a": ["error", "ModuleNotFoundError: x"]}}
        self.assertIn("ERROR on system", run_tests.judge(results)[0])

    def test_a_test_skipped_everywhere_is_reported(self):
        results = {"harness": {A: ["skip", "needs click"]}, "agictl": {A: ["skip", "needs LangChain"]}}
        problems = run_tests.judge(results)
        self.assertEqual(len(problems), 1)
        self.assertTrue(problems[0].startswith("PASSES NOWHERE: " + A))
        self.assertIn("needs click", problems[0])

    def test_a_module_skipped_everywhere_is_reported(self):
        results = {"harness": {SKIP_B: ["skip", "click"]}, "agictl": {SKIP_B: ["skip", "textual"]}}
        self.assertEqual(run_tests.judge(results), ["RUNS NOWHERE: module harness.tests.test_b is skipped on every interpreter"])

    def test_the_paid_probes_may_run_nowhere(self):
        results = {"harness": {PAID: ["skip", "paid live probes require VERSA_DRIVER_LIVE_TEST=1"]},
                   "agictl": {run_tests.MODULE_SKIP + "harness.tests.test_model_driver_live": ["skip", "langchain_core"]}}
        self.assertEqual(run_tests.judge(results), [])

    def test_the_interpreters_are_the_three_the_product_runs_on(self):
        self.assertEqual([name for name, _ in run_tests.INTERPRETERS], ["harness", "agictl", "system"])


if __name__ == "__main__":
    unittest.main()
