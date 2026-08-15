"""Dependency-light MD-2 ◆/◇ and Utility selection UI tests."""

from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import patch

CORE_INFRA = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)
sys.path.insert(0, CORE_INFRA)

from agitop.panels.model_routing_modal import _load_output_model_choices  # noqa: E402
from agitop.panels.provider_pick_modal import _model_row_cells  # noqa: E402
from agitop.panels.utility_model_editor_modal import (  # noqa: E402
    _catalog_option_label,
    _filter_catalog_choices,
)
from model_catalog import format_catalog_picker_label  # noqa: E402


class TestCatalogPickerLabel(unittest.TestCase):
    def test_openai_vs_openrouter_twins(self):
        self.assertEqual(
            format_catalog_picker_label("OpenAI", "GPT-5.6 Terra", "gpt-5.6-terra"),
            "OpenAI: GPT-5.6 Terra (gpt-5.6-terra)",
        )
        self.assertEqual(
            format_catalog_picker_label(
                "OpenRouter", "GPT-5.6 Terra", "openai/gpt-5.6-terra"
            ),
            "OpenRouter: GPT-5.6 Terra (openai/gpt-5.6-terra)",
        )


class TestUtilityDriverFiltering(unittest.TestCase):
    def test_non_text_output_requires_exact_driver_coverage(self):
        rows = [
            {
                "key": "bound",
                "label": "Bound",
                "provider_label": "Provider",
                "in_csv": "text",
                "out_csv": "text,image",
                "inputs": {"text"},
                "outputs": {"text", "image"},
                "driver_outputs": {"image"},
                "driver_summary": "output:image◆",
            },
            {
                "key": "hollow",
                "label": "Hollow",
                "provider_label": "Provider",
                "in_csv": "text",
                "out_csv": "text,image",
                "inputs": {"text"},
                "outputs": {"text", "image"},
                "driver_outputs": set(),
                "driver_summary": "output:image◇",
            },
        ]
        choices = _filter_catalog_choices(rows, "text", "image")
        self.assertEqual([value for _label, value in choices], ["bound"])
        self.assertIn("output:image◆", _catalog_option_label(rows[0]))
        self.assertIn("Provider: Bound (bound)", _catalog_option_label(rows[0]))

    def test_text_output_remains_native(self):
        row = {
            "key": "text-model",
            "label": "Text",
            "provider_label": "Provider",
            "in_csv": "text",
            "out_csv": "text",
            "inputs": {"text"},
            "outputs": {"text"},
            "driver_outputs": set(),
            "driver_summary": "text-native",
        }
        choices = _filter_catalog_choices([row], "text", "text")
        self.assertEqual(choices[0][1], "text-model")


class TestOutputRoutingDriverFiltering(unittest.TestCase):
    def test_output_defaults_offer_only_exact_driver_models(self):
        catalog = [
            {
                "key": "bound",
                "label": "Bound",
                "provider": "openai",
                "enabled": True,
                "coa": False,
                "output_modalities": "text,image",
                "driver_coverage": {"input": [], "output": ["image"]},
            },
            {
                "key": "hollow",
                "label": "Hollow",
                "enabled": True,
                "coa": True,
                "output_modalities": "text,image",
                "driver_coverage": {"input": [], "output": []},
            },
        ]
        with patch(
            "agitop.panels.model_routing_modal._catalog_list",
            return_value=catalog,
        ), patch(
            "model_catalog.load_providers",
            return_value={"openai": {"label": "OpenAI"}},
        ):
            choices = _load_output_model_choices("image")
        self.assertIn(("OpenAI: Bound (bound) ◆image", "bound"), choices)
        self.assertNotIn("hollow", {value for _label, value in choices})


class TestProviderImportBadges(unittest.TestCase):
    def test_addable_non_text_capabilities_are_hollow(self):
        cells = _model_row_cells(
            {
                "id": "vendor/new-model",
                "label": "New",
                "input_modalities": "text,image",
                "output_modalities": "text,audio",
            }
        )
        self.assertIn("input:image◇", cells[-1])
        self.assertIn("output:audio◇", cells[-1])


if __name__ == "__main__":
    unittest.main()
