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


class TestUtilityDriverFiltering(unittest.TestCase):
    def test_non_text_output_requires_exact_driver_coverage(self):
        rows = [
            {
                "key": "bound",
                "vendor": "Provider",
                "in_csv": "text",
                "out_csv": "text,image",
                "inputs": {"text"},
                "outputs": {"text", "image"},
                "driver_outputs": {"image"},
                "driver_summary": "output:image◆",
            },
            {
                "key": "hollow",
                "vendor": "Provider",
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

    def test_text_output_remains_native(self):
        row = {
            "key": "text-model",
            "vendor": "Provider",
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
        ):
            choices = _load_output_model_choices("image")
        self.assertIn(("bound — Bound ◆image", "bound"), choices)
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
