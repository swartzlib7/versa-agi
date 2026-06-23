"""Unit tests for provider_catalog modality enrichment (Phase F follow-on).

Covers the dependency-light inference added 2026-06-21:
  * Google output/input modality inference (the list API exposes no modality
    flag, so both directions are derived from the model family).
  * The xAI image-generation-models merge in ``_fetch_xai`` (separate listing
    folded into the language-models index, with graceful degradation).

These are pure functions / mocked-HTTP paths — no network, no provider key.

Run:  python -m unittest harness.tests.test_provider_catalog   (from core-infra)
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import provider_catalog as pc  # noqa: E402


class GoogleModalityInference(unittest.TestCase):
    def test_gemini_input_is_full_multimodal(self):
        self.assertEqual(
            pc._google_input_modalities("gemini-2.5-pro"),
            "text,image,audio,video",
        )

    def test_generator_families_take_text_input_only(self):
        self.assertEqual(pc._google_input_modalities("imagen-4.0-generate"), "text")
        self.assertEqual(pc._google_input_modalities("veo-3.0-generate"), "text")

    def test_non_gemini_defaults_to_text_input(self):
        self.assertEqual(pc._google_input_modalities("text-embedding-004"), "text")

    def test_gemini_text_output(self):
        self.assertEqual(pc._google_output_modalities("gemini-2.5-pro"), "text")

    def test_gemini_image_output(self):
        self.assertEqual(
            pc._google_output_modalities("gemini-3.1-flash-image"),
            "text,image",
        )

    def test_imagen_output_is_image_only(self):
        self.assertEqual(pc._google_output_modalities("imagen-4.0-generate"), "image")

    def test_veo_output_is_video(self):
        self.assertEqual(pc._google_output_modalities("veo-3.0-generate"), "video")

    def test_tts_output_is_audio(self):
        self.assertEqual(
            pc._google_output_modalities("gemini-2.5-flash-preview-tts"),
            "audio",
        )

    def test_unknown_output_defaults_to_text(self):
        self.assertEqual(pc._google_output_modalities("text-embedding-004"), "text")


class GoogleSummary(unittest.TestCase):
    def test_image_model_summary(self):
        s = pc._summary_google({
            "name": "models/gemini-3.1-flash-image",
            "displayName": "Gemini Image",
            "inputTokenLimit": 32768,
            "outputTokenLimit": 8192,
            "supportedGenerationMethods": ["generateContent"],
        })
        self.assertEqual(s["input_modalities"], "text,image,audio,video")
        self.assertEqual(s["output_modalities"], "text,image")
        self.assertTrue(s["chat_capable"])
        self.assertEqual(s["context_length"], 32768)
        self.assertEqual(s["output_context_limit"], 8192)

    def test_imagen_summary_not_chat_capable(self):
        s = pc._summary_google({
            "name": "models/imagen-4.0",
            "supportedGenerationMethods": ["predict"],
        })
        self.assertEqual(s["input_modalities"], "text")
        self.assertEqual(s["output_modalities"], "image")
        self.assertFalse(s["chat_capable"])


class XaiImageMerge(unittest.TestCase):
    def _patched_fetch(self, lang_payload, image_payload):
        """Run _fetch_xai with _http_get_json mocked per-URL."""
        calls = {}

        def fake_get(url, headers, timeout=45):
            calls[url] = calls.get(url, 0) + 1
            if url == pc._XAI_IMAGE_URL:
                if isinstance(image_payload, Exception):
                    raise image_payload
                return image_payload
            return lang_payload

        orig = pc._http_get_json
        pc._http_get_json = fake_get
        try:
            return pc._fetch_xai("dummy-key"), calls
        finally:
            pc._http_get_json = orig

    def test_image_models_merged(self):
        out, calls = self._patched_fetch(
            {"models": [{"id": "grok-4", "input_modalities": ["text"],
                         "output_modalities": ["text"]}]},
            {"models": [{"id": "grok-2-image"}]},
        )
        self.assertIn("grok-4", out)
        self.assertIn("grok-2-image", out)
        # image listing may omit modality fields → defaulted text→image
        self.assertEqual(out["grok-2-image"]["input_modalities"], ["text"])
        self.assertEqual(out["grok-2-image"]["output_modalities"], ["image"])
        self.assertEqual(calls.get(pc._XAI_IMAGE_URL), 1)

    def test_language_models_win_on_id_collision(self):
        out, _ = self._patched_fetch(
            {"models": [{"id": "grok-dup", "output_modalities": ["text"]}]},
            {"models": [{"id": "grok-dup"}]},
        )
        # existing language row not overwritten by the image listing
        self.assertEqual(out["grok-dup"]["output_modalities"], ["text"])

    def test_image_endpoint_failure_degrades_gracefully(self):
        out, _ = self._patched_fetch(
            {"models": [{"id": "grok-4"}]},
            OSError("network down"),
        )
        self.assertIn("grok-4", out)
        self.assertEqual(len(out), 1)

    def test_image_summary_is_image_output(self):
        s = pc._summary_xai({
            "id": "grok-2-image",
            "input_modalities": ["text"],
            "output_modalities": ["image"],
        })
        self.assertEqual(s["output_modalities"], "image")
        self.assertFalse(s["chat_capable"])


if __name__ == "__main__":
    unittest.main()
