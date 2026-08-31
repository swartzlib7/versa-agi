"""CM-2 regressions for exact ModelDriver-backed VIEW INJECT."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

CORE_INFRA = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)
SRC_MODELS = os.path.join(os.path.dirname(CORE_INFRA), "models.ini")
sys.path.insert(0, CORE_INFRA)

from harness.agent_harness import _build_view_image_message  # noqa: E402
from model_catalog import load_catalog, load_providers  # noqa: E402
from model_drivers.libraries.chat_video_in_content_parts import (  # noqa: E402
    to_content_parts as video_to_content_parts,
)
from model_drivers.libraries.chat_video_in_google_media import (  # noqa: E402
    to_content_parts as video_to_google_media,
)
from model_drivers.view_paths import (  # noqa: E402
    resolve_view_image_path,
    resolve_view_video_path,
)


def _src_catalog_patches():
    catalog = load_catalog(SRC_MODELS)
    providers = load_providers(SRC_MODELS)
    return (
        patch("model_drivers.registry.load_catalog", return_value=catalog),
        patch("model_drivers.registry.load_providers", return_value=providers),
    )


class TestViewImageDriverDispatch(unittest.TestCase):
    def _image(self):
        image = tempfile.NamedTemporaryFile(suffix=".png")
        image.write(b"\x89PNG\r\n")
        image.flush()
        return image

    def test_view_paths_imports_image_helpers_from_adapter_library(self) -> None:
        """Regression: helpers moved out of message_adapters; view_paths must follow."""
        with self._image() as image:
            resolved = resolve_view_image_path(image.name)
        self.assertEqual(resolved, os.path.realpath(image.name))

    def test_bound_model_invokes_resolved_adapter(self) -> None:
        parts = [
            {"type": "text", "text": "resolved"},
            {
                "type": "image_url",
                "image_url": {"url": "data:image/png;base64,AAAA"},
            },
        ]
        entrypoint = Mock(return_value=parts)
        resolved = SimpleNamespace(
            binding=SimpleNamespace(config={"detail": "high"}),
            adapter=SimpleNamespace(
                adapter_id="chat_image_in_content_parts",
                entrypoint=entrypoint,
            ),
        )

        with self._image() as image:
            payload = {
                "path": image.name,
                "execution_model": "gemini-2.5-pro",
                "bytes": 8,
            }
            with (
                patch(
                    "model_drivers.registry.resolve_model_driver",
                    return_value=resolved,
                ) as resolve,
                patch("harness.agent_harness.tlog") as log,
            ):
                message = _build_view_image_message(payload, "google")

        self.assertIsNotNone(message)
        assert message is not None
        self.assertEqual(message.content, parts)
        resolve.assert_called_once_with(
            "gemini-2.5-pro",
            "input",
            "image",
        )
        entrypoint.assert_called_once_with(
            path=payload["path"],
            caption=f"Agent requested view of image at {payload['path']}",
            config={"detail": "high"},
        )
        self.assertTrue(
            any("adapter=chat_image_in_content_parts" in str(call) for call in log.call_args_list)
        )

    def test_unbound_model_returns_no_driver(self) -> None:
        with self._image() as image:
            payload = {
                "path": image.name,
                "execution_model": "claude-sonnet-4-6",
            }
            with (
                patch(
                    "model_drivers.registry.resolve_model_driver",
                    return_value=None,
                ),
                patch("harness.agent_harness.tlog") as log,
            ):
                message = _build_view_image_message(payload, "anthropic")

        self.assertIsNone(message)
        self.assertTrue(
            any("no_driver" in str(call) for call in log.call_args_list)
        )

    def test_real_bound_model_returns_canonical_content_parts(self) -> None:
        with self._image() as image, _src_catalog_patches()[0], _src_catalog_patches()[1]:
            message = _build_view_image_message(
                {
                    "path": image.name,
                    "execution_model": "gemini-2.5-pro",
                },
                "google",
            )

        self.assertIsNotNone(message)
        assert message is not None
        self.assertTrue(
            message.content[1]["image_url"]["url"].startswith(
                "data:image/png;base64,"
            )
        )

    def test_promoted_active_openrouter_model_is_bound(self) -> None:
        with self._image() as image, _src_catalog_patches()[0], _src_catalog_patches()[1]:
            message = _build_view_image_message(
                {
                    "path": image.name,
                    "execution_model": "openai/gpt-5.6-luna",
                },
                "openai_compat",
            )

        self.assertIsNotNone(message)
        assert message is not None
        self.assertTrue(
            message.content[1]["image_url"]["url"].startswith(
                "data:image/png;base64,"
            )
        )


class TestViewVideoDriverDispatch(unittest.TestCase):
    def _video(self):
        video = tempfile.NamedTemporaryFile(suffix=".mp4")
        video.write(b"\x00\x00\x00\x18ftypmp42")
        video.flush()
        return video

    def test_view_paths_accepts_mp4(self) -> None:
        with self._video() as video:
            resolved = resolve_view_video_path(video.name)
        self.assertEqual(resolved, os.path.realpath(video.name))

    def test_adapter_emits_video_url_data_uri(self) -> None:
        with self._video() as video:
            parts = video_to_content_parts(path=video.name, caption="clip")
        self.assertEqual(parts[0]["text"], "clip")
        self.assertEqual(parts[1]["type"], "video_url")
        self.assertTrue(
            parts[1]["video_url"]["url"].startswith("data:video/")
        )

    def test_bound_glm_flash_returns_video_parts(self) -> None:
        with self._video() as video, _src_catalog_patches()[0], _src_catalog_patches()[1]:
            message = _build_view_image_message(
                {
                    "path": video.name,
                    "execution_model": "z-ai/glm-5.3-flash",
                    "modality": "video",
                },
                "openai_compat",
            )

        self.assertIsNotNone(message)
        assert message is not None
        self.assertEqual(message.content[1]["type"], "video_url")
        self.assertTrue(
            message.content[1]["video_url"]["url"].startswith("data:video/")
        )

    def test_adapter_emits_google_media_parts(self) -> None:
        with self._video() as video:
            parts = video_to_google_media(path=video.name, caption="clip")
        self.assertEqual(parts[0]["text"], "clip")
        self.assertEqual(parts[1]["type"], "media")
        self.assertEqual(parts[1]["mime_type"], "video/mp4")
        self.assertEqual(parts[1]["data"], b"\x00\x00\x00\x18ftypmp42")

    def test_bound_gemini_37_google_returns_media_parts(self) -> None:
        with self._video() as video, _src_catalog_patches()[0], _src_catalog_patches()[1]:
            message = _build_view_image_message(
                {
                    "path": video.name,
                    "execution_model": "gemini-3.7-flash",
                    "modality": "video",
                },
                "google",
            )

        self.assertIsNotNone(message)
        assert message is not None
        self.assertEqual(message.content[1]["type"], "media")
        self.assertEqual(message.content[1]["mime_type"], "video/mp4")

    def test_bound_gemini_37_openrouter_returns_video_url(self) -> None:
        with self._video() as video, _src_catalog_patches()[0], _src_catalog_patches()[1]:
            message = _build_view_image_message(
                {
                    "path": video.name,
                    "execution_model": "google/gemini-3.7-flash",
                    "modality": "video",
                },
                "openai_compat",
            )

        self.assertIsNotNone(message)
        assert message is not None
        self.assertEqual(message.content[1]["type"], "video_url")
        self.assertTrue(
            message.content[1]["video_url"]["url"].startswith("data:video/")
        )


if __name__ == "__main__":
    unittest.main()
