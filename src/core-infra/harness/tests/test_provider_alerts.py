"""Tests for Provider 403 / quota classification and PU alert text."""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from provider_alerts import (  # noqa: E402
    KIND_FORBIDDEN,
    KIND_NONE,
    KIND_OVERLOAD,
    KIND_QUOTA,
    KIND_RATE_LIMIT,
    classify_text,
    format_pu_message,
)

WSL_403 = """
[2026-08-28 23:51:10] TRIAGE MODEL: grok-4.5
TRIAGE: LLM call failed — Error code: 403 - {'code': 'permission-denied', 'error': 'Your team da0bdda8-e6a6-49a1-8814-08586540930d has either used all available credits or reached its monthly spending limit. To continue making API requests, please purchase more credits or raise your spending limit.'}. Defaulting to pass-through.
[2026-08-28 23:51:10] EXECUTION MODEL: grok-4.5 (assigned=grok-4.5, mode=none)
[2026-08-28 23:51:11] LLM ROUTE (execution/grok-4.5): catalog_provider=xai client=ChatOpenAI endpoint=https://api.x.ai/v1
[2026-08-28 23:51:11] FATAL EXCEPTION: Error code: 403 - {'code': 'permission-denied', 'error': 'Your team da0bdda8-e6a6-49a1-8814-08586540930d has either used all available credits or reached its monthly spending limit. To continue making API requests, please purchase more credits or raise your spending limit.'}
The cycle crashed or hit recursion limit (1 steps).
"""


class ClassifyProviderResult(unittest.TestCase):
    def test_xai_403_credits(self):
        info = classify_text(WSL_403)
        self.assertEqual(info["kind"], KIND_FORBIDDEN)
        self.assertTrue(info["fatal"])
        self.assertEqual(info["model"], "grok-4.5")
        self.assertEqual(info["provider"], "xai")
        self.assertIn("used all available credits", info["detail"])
        self.assertIn("monthly spending limit", info["detail"])

    def test_bare_403_in_prompt_is_ignored(self):
        info = classify_text("Wake reason: user asked about HTTP status 403 in a doc.\n")
        self.assertEqual(info["kind"], KIND_NONE)

    def test_daily_quota(self):
        info = classify_text("TerminalQuotaError: free_tier_requests exhausted daily quota\n")
        self.assertEqual(info["kind"], KIND_QUOTA)

    def test_rate_limit_429(self):
        info = classify_text("Error code: 429 - Too Many Requests\n")
        self.assertEqual(info["kind"], KIND_RATE_LIMIT)

    def test_overload_503(self):
        info = classify_text("Error code: 503 — model is overloaded / UNAVAILABLE\n")
        self.assertEqual(info["kind"], KIND_OVERLOAD)

    def test_pu_message_includes_details(self):
        msg = format_pu_message("coa", classify_text(WSL_403))
        self.assertIn("403", msg)
        self.assertIn("Agent: coa", msg)
        self.assertIn("Model: grok-4.5", msg)
        self.assertIn("Provider: xai", msg)
        self.assertIn("used all available credits", msg)
        self.assertIn("1 hour", msg)

    def test_rate_limit_has_no_pu_message(self):
        self.assertEqual(
            format_pu_message("coa", classify_text("Error code: 429 - Too Many Requests\n")),
            "",
        )


if __name__ == "__main__":
    unittest.main()
