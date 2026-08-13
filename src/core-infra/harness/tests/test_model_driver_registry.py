"""Unit tests for TD-DRIVER-001 MD-0R exact ModelDriver resolution.

Run from core-infra::

    python -m unittest harness.tests.test_model_driver_registry
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from unittest.mock import patch

CORE_INFRA = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)
REPOSITORY_MODELS_INI = os.path.join(os.path.dirname(CORE_INFRA), "models.ini")
MODELS_INI = REPOSITORY_MODELS_INI if os.path.isfile(REPOSITORY_MODELS_INI) else None
sys.path.insert(0, CORE_INFRA)

from model_catalog import (  # noqa: E402
    load_catalog,
    load_providers,
    model_output_includes,
    parse_input_modalities,
)
from model_drivers import registry as reg  # noqa: E402
from model_drivers.message_adapters import build_image_content_parts  # noqa: E402
from harness.model_routing import _model_supports_inputs  # noqa: E402

try:  # Full Harness environment; minimal dashboard venv may omit this package.
    from langchain_google_genai.chat_models import _convert_to_parts
    _HAS_GOOGLE_LANGCHAIN = True
except ModuleNotFoundError:
    _convert_to_parts = None
    _HAS_GOOGLE_LANGCHAIN = False


EXPECTED_INPUT_BINDINGS = {
    # DR-CM-04
    "gpt-5.5-2026-04-23": "openai",
    "gpt-5.4-2026-03-05": "openai",
    "gpt-5.4-mini-2026-03-17": "openai",
    # DR-CM-06
    "gemini-2.5-pro": "google",
    "gemini-2.5-flash": "google",
    "gemini-3-flash-preview": "google",
    "gemini-3.1-pro-preview": "google",
    "gemini-3.1-flash-lite": "google",
    # DR-CM-08
    "grok-4.5": "xai",
    # DR-CM-07
    "openai/gpt-5.6-luna": "openrouter",
    # DR-CM-11
    "x-ai/grok-4.5": "openrouter",
}

EXPECTED_OUTPUT_BINDINGS = {
    # DR-CM-01
    "google/gemini-3.1-flash-image": (
        "openrouter",
        "image",
        "chat_mm_image_out_openai_compat",
    ),
    # DR-CM-02
    "openai/gpt-audio": (
        "openrouter",
        "audio",
        "chat_mm_audio_out_pcm16",
    ),
    # DR-CM-03
    "gpt-audio-1.5": (
        "openai",
        "audio",
        "chat_mm_audio_out_pcm16",
    ),
    # DR-CM-05
    "gemini-3.1-flash-image": (
        "google",
        "image",
        "chat_mm_image_out_google_generate_content",
    ),
    # DR-CM-09
    "openai/gpt-5.4-image-2": (
        "openrouter",
        "image",
        "chat_mm_image_out_openai_compat",
    ),
    # DR-CM-10
    "openai/gpt-audio-mini": (
        "openrouter",
        "audio",
        "chat_mm_audio_out_pcm16",
    ),
}


class RegistryTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Source tree: use versa-agi/src/models.ini. Installed tree: passing
        # None uses model_catalog.resolve_models_ini_path() → /etc/versa-agi.
        cls.catalog = load_catalog(MODELS_INI)
        cls.providers = load_providers(MODELS_INI)

    def resolve(self, catalog_key: str, direction: str, modality: str):
        return reg.resolve_model_driver(
            catalog_key,
            direction,
            modality,
            catalog=self.catalog,
            providers=self.providers,
        )


class TestExactResolution(RegistryTestCase):
    def test_each_existing_catalog_input_binding_resolves(self):
        for catalog_key, provider_slug in EXPECTED_INPUT_BINDINGS.items():
            with self.subTest(catalog_key=catalog_key):
                resolved = self.resolve(catalog_key, "input", "image")
                self.assertIsNotNone(resolved)
                assert resolved is not None
                self.assertEqual(resolved.model["provider"], provider_slug)
                self.assertEqual(
                    resolved.binding.adapter_id,
                    "chat_image_in_content_parts",
                )
                self.assertEqual(
                    resolved.adapter.adapter_id,
                    "chat_image_in_content_parts",
                )
                self.assertTrue(callable(resolved.adapter.entrypoint))

    def test_provider_is_derived_from_exact_catalog_model(self):
        resolved = self.resolve("gemini-2.5-pro", "in", "image")
        self.assertIsNotNone(resolved)
        assert resolved is not None
        self.assertEqual(resolved.model["provider"], "google")
        self.assertEqual(resolved.provider["label"], "Google Gemini")
        self.assertEqual(resolved.binding.direction, "input")

    def test_each_exact_output_binding_resolves(self):
        for catalog_key, expected in EXPECTED_OUTPUT_BINDINGS.items():
            provider_slug, modality, adapter_id = expected
            with self.subTest(catalog_key=catalog_key):
                resolved = self.resolve(catalog_key, "output", modality)
                self.assertIsNotNone(resolved)
                assert resolved is not None
                self.assertEqual(resolved.model["provider"], provider_slug)
                self.assertEqual(resolved.binding.adapter_id, adapter_id)
                self.assertEqual(resolved.adapter.adapter_id, adapter_id)
                self.assertTrue(callable(resolved.adapter.entrypoint))

    def test_similar_family_names_do_not_resolve(self):
        for catalog_key in (
            "gpt-5.5",
            "gpt-5.4-mini",
            "gemini-2.5-pro-preview",
            "gemini-9-flash",
            "grok-4-1-fast",
            "grok-anything",
        ):
            with self.subTest(catalog_key=catalog_key):
                self.assertIsNone(self.resolve(catalog_key, "input", "image"))

    def test_retired_exact_models_do_not_resolve(self):
        for catalog_key in (
            "gemini-3-pro-preview",
            "gemini-3.1-flash-lite-preview",
            "grok-4-1-fast-reasoning",
            "grok-4.3",
            "grok-4.20-reasoning",
        ):
            with self.subTest(catalog_key=catalog_key):
                self.assertIsNone(self.resolve(catalog_key, "input", "image"))

    def test_unknown_catalog_model_does_not_resolve(self):
        self.assertIsNone(
            self.resolve("openai/not-in-catalog", "input", "image")
        )

    def test_catalog_override_removing_capability_does_not_resolve(self):
        catalog = dict(self.catalog)
        catalog["gemini-2.5-pro"] = {
            **catalog["gemini-2.5-pro"],
            "input_modalities": "text",
        }
        self.assertIsNone(
            reg.resolve_model_driver(
                "gemini-2.5-pro",
                "input",
                "image",
                catalog=catalog,
                providers=self.providers,
            )
        )
        self.assertEqual(
            reg.model_driver_coverage(
                "gemini-2.5-pro",
                catalog=catalog,
                providers=self.providers,
            ),
            {"input": set(), "output": set()},
        )

    def test_output_capability_removal_suppresses_exact_binding(self):
        catalog = dict(self.catalog)
        catalog["openai/gpt-audio"] = {
            **catalog["openai/gpt-audio"],
            "output_modalities": "text",
        }
        self.assertIsNone(
            reg.resolve_model_driver(
                "openai/gpt-audio",
                "output",
                "audio",
                catalog=catalog,
                providers=self.providers,
            )
        )

    def test_missing_catalog_provider_does_not_resolve(self):
        self.assertIsNone(
            reg.resolve_model_driver(
                "gemini-2.5-pro",
                "input",
                "image",
                catalog=self.catalog,
                providers={},
            )
        )

    def test_wrong_direction_or_modality_does_not_resolve(self):
        self.assertIsNone(self.resolve("gemini-2.5-pro", "output", "image"))
        self.assertIsNone(self.resolve("gemini-2.5-pro", "input", "audio"))
        self.assertIsNone(self.resolve("gemini-2.5-pro", "sideways", "image"))

    def test_text_never_resolves(self):
        self.assertIsNone(self.resolve("gemini-2.5-pro", "input", "text"))
        self.assertIsNone(self.resolve("gemini-2.5-pro", "output", "text"))

    def test_missing_adapter_does_not_resolve(self):
        with patch.dict(reg.ADAPTERS, {}, clear=True):
            self.assertIsNone(self.resolve("gemini-2.5-pro", "input", "image"))

    def test_resolved_binding_config_cannot_mutate_global_registry(self):
        resolved = self.resolve("gemini-2.5-pro", "input", "image")
        self.assertIsNotNone(resolved)
        assert resolved is not None
        resolved.binding.config["caller_value"] = True

        resolved_again = self.resolve("gemini-2.5-pro", "input", "image")
        self.assertIsNotNone(resolved_again)
        assert resolved_again is not None
        self.assertEqual(resolved_again.binding.config, {})


class TestRegistryIntegrity(RegistryTestCase):
    def test_only_expected_exact_bindings_are_shipped(self):
        actual = {
            (binding.catalog_key, binding.direction, binding.modality)
            for binding in reg.list_model_drivers()
        }
        expected = {
            (catalog_key, "input", "image")
            for catalog_key in EXPECTED_INPUT_BINDINGS
        }
        expected.update(
            (catalog_key, "output", details[1])
            for catalog_key, details in EXPECTED_OUTPUT_BINDINGS.items()
        )
        self.assertEqual(actual, expected)

    def test_every_binding_targets_catalog_capability_and_executable_adapter(self):
        for binding in reg.list_model_drivers():
            with self.subTest(catalog_key=binding.catalog_key):
                model = self.catalog.get(binding.catalog_key)
                self.assertIsNotNone(model)
                assert model is not None
                self.assertIn(model["provider"], self.providers)
                if binding.direction == "input":
                    self.assertIn(binding.modality, parse_input_modalities(model))
                else:
                    self.assertTrue(model_output_includes(model, binding.modality))
                adapter = reg.ADAPTERS.get(binding.adapter_id)
                self.assertIsNotNone(adapter)
                assert adapter is not None
                self.assertTrue(callable(adapter.entrypoint))
                self.assertEqual(adapter.direction, binding.direction)
                self.assertEqual(adapter.modality, binding.modality)

    def test_only_executable_adapters_are_registered(self):
        self.assertEqual(
            set(reg.ADAPTERS),
            {
                "chat_image_in_content_parts",
                "chat_mm_image_out_openai_compat",
                "chat_mm_audio_out_pcm16",
                "chat_mm_image_out_google_generate_content",
            },
        )
        for stale_id in (
            "chat_mm_image_in_google",
            "chat_mm_image_in_openai_compat",
        ):
            self.assertNotIn(stale_id, reg.ADAPTERS)

    def test_retired_broad_and_site_api_is_absent(self):
        self.assertFalse(hasattr(reg, "resolve_driver"))
        self.assertFalse(hasattr(reg, "list_libraries"))
        self.assertFalse(hasattr(reg, "load_library_module"))


class TestCoverageAndAdvice(RegistryTestCase):
    def coverage(self, catalog_key: str):
        return reg.model_driver_coverage(
            catalog_key,
            catalog=self.catalog,
            providers=self.providers,
        )

    def test_coverage_uses_exact_executable_resolution(self):
        self.assertEqual(
            self.coverage("gemini-2.5-pro"),
            {"input": {"image"}, "output": set()},
        )
        self.assertEqual(
            self.coverage("gemini-2.5-pro-preview"),
            {"input": set(), "output": set()},
        )
        self.assertEqual(
            self.coverage("google/gemini-3.1-flash-image"),
            {"input": set(), "output": {"image"}},
        )
        self.assertEqual(
            self.coverage("openai/gpt-audio"),
            {"input": set(), "output": {"audio"}},
        )

    def test_advice_omits_supported_input_and_reports_output_gaps(self):
        hints = reg.advise_driver_gaps(
            "gemini-2.5-pro",
            input_modalities=["text", "image"],
            output_modalities=["text", "image", "video"],
            catalog=self.catalog,
            providers=self.providers,
        )
        self.assertEqual(len(hints), 2)
        self.assertTrue(any("output image" in hint for hint in hints))
        self.assertTrue(any("output video" in hint for hint in hints))
        self.assertTrue(all("Build Guide" in hint for hint in hints))
        self.assertTrue(all("agictl model driver map" not in hint for hint in hints))

    def test_catalog_enrichment_distinguishes_filled_and_hollow_capabilities(self):
        luna = reg.catalog_driver_enrichment(
            "openai/gpt-5.6-luna",
            self.catalog["openai/gpt-5.6-luna"],
            catalog=self.catalog,
            providers=self.providers,
        )
        self.assertEqual(luna["driver_badges"]["input"]["image"], "◆")
        self.assertIn("input:image◆", luna["driver_summary"])

        claude = reg.catalog_driver_enrichment(
            "claude-sonnet-4-6",
            self.catalog["claude-sonnet-4-6"],
            catalog=self.catalog,
            providers=self.providers,
        )
        self.assertEqual(claude["driver_badges"]["input"]["image"], "◇")
        self.assertIn("input:image◇", claude["driver_summary"])

    def test_spawn_routing_requires_exact_non_text_input_driver(self):
        self.assertTrue(
            _model_supports_inputs(
                "openai/gpt-5.6-luna",
                self.catalog["openai/gpt-5.6-luna"],
                ["text", "image"],
                self.catalog,
                self.providers,
            )
        )
        self.assertFalse(
            _model_supports_inputs(
                "claude-sonnet-4-6",
                self.catalog["claude-sonnet-4-6"],
                ["text", "image"],
                self.catalog,
                self.providers,
            )
        )


class TestExecutableInputAdapter(RegistryTestCase):
    def test_adapter_returns_canonical_image_url_content_parts(self):
        resolved = self.resolve("gemini-2.5-pro", "input", "image")
        self.assertIsNotNone(resolved)
        assert resolved is not None

        with tempfile.NamedTemporaryFile(suffix=".png") as image:
            image.write(b"\x89PNG\r\n")
            image.flush()
            parts = resolved.adapter.entrypoint(
                path=image.name,
                caption="Inspect this image",
                config={},
            )

        self.assertEqual(parts[0], {"type": "text", "text": "Inspect this image"})
        self.assertEqual(parts[1]["type"], "image_url")
        self.assertTrue(
            parts[1]["image_url"]["url"].startswith("data:image/png;base64,")
        )

    def test_all_cloud_provider_paths_keep_the_canonical_shape(self):
        with tempfile.NamedTemporaryFile(suffix=".png") as image:
            image.write(b"\x89PNG\r\n")
            image.flush()
            cloud_parts = {
                provider: build_image_content_parts(
                    image.name,
                    provider,
                    caption="Compare",
                )
                for provider in ("google", "openai", "xai", "openrouter")
            }
            local = build_image_content_parts(
                image.name,
                "local",
                caption="Compare",
            )

        canonical = cloud_parts["openai"]
        self.assertTrue(all(parts == canonical for parts in cloud_parts.values()))
        self.assertIsInstance(canonical[1]["image_url"], dict)
        self.assertIsInstance(local[1]["image_url"], str)

    @unittest.skipUnless(
        _HAS_GOOGLE_LANGCHAIN,
        "langchain-google-genai is not installed",
    )
    def test_google_langchain_converts_canonical_data_url_to_inline_data(self):
        with tempfile.NamedTemporaryFile(suffix=".png") as image:
            image.write(b"\x89PNG\r\n")
            image.flush()
            canonical = build_image_content_parts(
                image.name,
                "google",
                caption="Convert",
            )

        parts = _convert_to_parts(canonical, model="gemini-3.1-flash-lite")
        self.assertEqual(parts[0].text, "Convert")
        self.assertEqual(parts[1].inline_data.mime_type, "image/png")
        self.assertEqual(parts[1].inline_data.data, b"\x89PNG\r\n")

    def test_each_exact_binding_executes_the_shared_adapter_shape(self):
        with tempfile.NamedTemporaryFile(suffix=".png") as image:
            image.write(b"\x89PNG\r\n")
            image.flush()
            for catalog_key in EXPECTED_INPUT_BINDINGS:
                with self.subTest(catalog_key=catalog_key):
                    resolved = self.resolve(catalog_key, "input", "image")
                    self.assertIsNotNone(resolved)
                    assert resolved is not None
                    parts = resolved.adapter.entrypoint(
                        path=image.name,
                        caption=f"Inspect with {catalog_key}",
                        config=resolved.binding.config,
                    )
                    self.assertEqual(
                        parts[0],
                        {
                            "type": "text",
                            "text": f"Inspect with {catalog_key}",
                        },
                    )
                    self.assertTrue(
                        parts[1]["image_url"]["url"].startswith(
                            "data:image/png;base64,"
                        )
                    )


if __name__ == "__main__":
    unittest.main()
