"""Unit tests for model context lookup (cloud Auto vs local 4K default)."""

from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from harness import model_context as mc  # noqa: E402


class SuffixFallback(unittest.TestCase):
    def test_vendor_slash_uses_family_prefix(self):
        fake = {"grok": (0, 1_000_000), "gemini": (0, 1_000_000), "gpt": (0, 131_072)}
        with patch.object(mc, "MODEL_CONTEXT_MAP", fake):
            self.assertEqual(mc.get_model_context("x-ai/grok-4.6"), (0, 1_000_000))
            self.assertEqual(mc.get_model_context("x-ai/grok-4.5"), (0, 1_000_000))
            self.assertEqual(
                mc.get_model_context("google/gemini-3-flash-preview"), (0, 1_000_000)
            )

    def test_exact_catalog_row_wins(self):
        fake = {
            "grok": (0, 1_000_000),
            "x-ai/grok-4.6": (0, 256_000),
        }
        with patch.object(mc, "MODEL_CONTEXT_MAP", fake):
            self.assertEqual(mc.get_model_context("x-ai/grok-4.6"), (0, 256_000))

    def test_unknown_local_stays_4096(self):
        with patch.object(mc, "MODEL_CONTEXT_MAP", {"grok": (0, 1_000_000)}):
            self.assertEqual(mc.get_model_context("totally-unknown-local"), (4096, 4096))

    def test_empty_name_is_4096(self):
        self.assertEqual(mc.get_model_context(""), (4096, 4096))

    def test_is_cloud_for_slash_grok(self):
        fake = {"grok": (0, 1_000_000)}
        with patch.object(mc, "MODEL_CONTEXT_MAP", fake):
            self.assertTrue(mc.is_cloud_model("x-ai/grok-4.6"))
            self.assertFalse(mc.is_cloud_model("gemma4:e4b"))


class TrimmerBudget(unittest.TestCase):
    def test_explicit_4k_is_tiny(self):
        # 4096 × 0.80 × 3 = 9830 — the budget that starved first-contact.
        self.assertEqual(mc.get_trimmer_char_limit("x-ai/grok-4.6", 4096), 9830)

    def test_auto_cloud_uses_max_window(self):
        fake = {"x-ai/grok-4.6": (0, 131_072)}
        with patch.object(mc, "MODEL_CONTEXT_MAP", fake):
            self.assertEqual(
                mc.get_trimmer_char_limit("x-ai/grok-4.6", 0),
                int(131_072 * mc.TRIMMER_HEADROOM * mc.TRIMMER_CHARS_PER_TOKEN),
            )


if __name__ == "__main__":
    unittest.main()
