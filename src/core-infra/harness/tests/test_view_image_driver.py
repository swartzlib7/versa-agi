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
sys.path.insert(0, CORE_INFRA)

from harness.agent_harness import _build_view_image_message  # noqa: E402
from model_drivers.view_paths import resolve_view_image_path  # noqa: E402


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
        with self._image() as image:
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
        with self._image() as image:
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


if __name__ == "__main__":
    unittest.main()
