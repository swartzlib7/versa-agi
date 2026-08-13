"""Executable chat multimodal output DriverAdapter regressions."""

from __future__ import annotations

import base64
import io
import os
import sys
import tempfile
import unittest
import wave
from types import SimpleNamespace
from unittest.mock import patch

CORE_INFRA = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)
sys.path.insert(0, CORE_INFRA)

from harness import generation  # noqa: E402
from harness.utility_runner import UtilityRunError  # noqa: E402
from model_drivers.errors import DriverError  # noqa: E402
from model_drivers.libraries import (  # noqa: E402
    chat_mm_audio_out_pcm16 as audio_adapter,
    chat_mm_image_out_google_generate_content as google_image_adapter,
    chat_mm_image_out_openai_compat as openai_image_adapter,
)
from provider_runtime import ProviderRoute  # noqa: E402


def _route(provider: str, model: str) -> ProviderRoute:
    client_type = (
        "ChatGoogleGenerativeAI" if provider == "google" else "ChatOpenAI"
    )
    return ProviderRoute(
        catalog_key=model,
        model={"provider": provider},
        provider_slug=provider,
        provider={"cls": client_type},
        client_type=client_type,
        endpoint="google-generativeai"
        if provider == "google"
        else "https://openrouter.ai/api/v1",
        api_model=model,
    )


class _OpenAIClient:
    def __init__(self, response):
        self.captured: dict = {}
        self._response = response
        self.chat = SimpleNamespace(
            completions=SimpleNamespace(create=self._create)
        )

    def _create(self, **kwargs):
        self.captured.update(kwargs)
        return self._response


class TestOpenAICompatibleImageAdapter(unittest.TestCase):
    def test_data_url_response_and_image_config(self):
        raw = b"\x89PNG-driver"
        url = "data:image/png;base64," + base64.b64encode(raw).decode("ascii")
        message = SimpleNamespace(
            model_dump=lambda: {"images": [{"image_url": {"url": url}}]}
        )
        client = _OpenAIClient(
            SimpleNamespace(choices=[SimpleNamespace(message=message)])
        )

        artifact = openai_image_adapter.generate(
            client=client,
            route=_route("openrouter", "google/gemini-3.1-flash-image"),
            prompt="Draw a lighthouse",
            config={"image_config": {"aspect_ratio": "16:9"}},
        )

        self.assertEqual(artifact.data, raw)
        self.assertEqual((artifact.ext, artifact.mime), ("png", "image/png"))
        self.assertEqual(
            client.captured["extra_body"]["image_config"],
            {"aspect_ratio": "16:9"},
        )
        self.assertFalse(client.captured["stream"])

    def test_image_input_becomes_data_url_content_part(self):
        raw = b"result"
        url = "data:image/webp;base64," + base64.b64encode(raw).decode("ascii")
        message = SimpleNamespace(
            model_dump=lambda: {"images": [{"image_url": {"url": url}}]}
        )
        client = _OpenAIClient(
            SimpleNamespace(choices=[SimpleNamespace(message=message)])
        )
        with tempfile.NamedTemporaryFile(suffix=".png") as image:
            image.write(b"\x89PNG-input")
            image.flush()
            openai_image_adapter.generate(
                client=client,
                route=_route("openrouter", "google/gemini-3.1-flash-image"),
                prompt="Edit this",
                input_files=[
                    {"path": image.name, "modality": "image", "ext": "png"}
                ],
            )

        content = client.captured["messages"][0]["content"]
        self.assertEqual(content[0], {"type": "text", "text": "Edit this"})
        self.assertTrue(
            content[1]["image_url"]["url"].startswith("data:image/png;base64,")
        )


class TestAudioAdapter(unittest.TestCase):
    def test_streamed_pcm16_packages_wav_and_transcript(self):
        pcm = b"\x01\x02\x03\x04"
        encoded = base64.b64encode(pcm).decode("ascii")

        def stream():
            midpoint = len(encoded) // 2
            for data, transcript in (
                (encoded[:midpoint], "hello "),
                (encoded[midpoint:], "world"),
            ):
                audio = {"data": data, "transcript": transcript}
                yield SimpleNamespace(
                    choices=[
                        SimpleNamespace(
                            delta=SimpleNamespace(audio=audio)
                        )
                    ]
                )

        client = _OpenAIClient(stream())
        artifact = audio_adapter.generate(
            client=client,
            route=_route("openrouter", "openai/gpt-audio"),
            prompt="Say hello",
            config={"audio_format": "wav", "voice": "verse"},
        )

        self.assertEqual((artifact.ext, artifact.mime), ("wav", "audio/wav"))
        self.assertEqual(artifact.transcript, "hello world")
        self.assertTrue(client.captured["stream"])
        self.assertEqual(
            client.captured["extra_body"]["audio"],
            {"voice": "verse", "format": "pcm16"},
        )
        with wave.open(io.BytesIO(artifact.data), "rb") as wav_file:
            self.assertEqual(
                wav_file.readframes(wav_file.getnframes()),
                pcm,
            )

    def test_missing_ffmpeg_falls_back_to_wav(self):
        with patch(
            "model_drivers.libraries.chat_mm_common.shutil.which",
            return_value=None,
        ):
            data, ext, mime = audio_adapter.package_audio(b"\x00\x00", "ogg")
        self.assertTrue(data.startswith(b"RIFF"))
        self.assertEqual((ext, mime), ("wav", "audio/wav"))


class _GoogleClient:
    def __init__(self, response):
        self.captured: dict = {}
        self.models = SimpleNamespace(generate_content=self._generate)
        self._response = response

    def _generate(self, **kwargs):
        self.captured.update(kwargs)
        return self._response


class TestGoogleGenerateContentAdapter(unittest.TestCase):
    def test_inline_data_response_and_native_request(self):
        raw = b"\x89PNG-google"
        response = {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {"text": "generated"},
                            {
                                "inlineData": {
                                    "mimeType": "image/png",
                                    "data": base64.b64encode(raw).decode("ascii"),
                                }
                            },
                        ]
                    }
                }
            ]
        }
        client = _GoogleClient(response)

        artifact = google_image_adapter.generate(
            client=client,
            route=_route("google", "gemini-3.1-flash-image"),
            prompt="Generate a fox",
            config={
                "image_config": {
                    "aspect_ratio": "1:1",
                    "image_size": "2K",
                }
            },
        )

        self.assertEqual(artifact.data, raw)
        self.assertEqual((artifact.ext, artifact.mime), ("png", "image/png"))
        self.assertEqual(artifact.transcript, "generated")
        self.assertEqual(
            client.captured["config"]["response_modalities"],
            ["TEXT", "IMAGE"],
        )
        self.assertEqual(
            client.captured["config"]["image_config"]["image_size"],
            "2K",
        )

    def test_image_input_uses_inline_data_not_image_url(self):
        response = {
            "parts": [
                {
                    "inline_data": {
                        "mime_type": "image/jpeg",
                        "data": b"jpeg-output",
                    }
                }
            ]
        }
        client = _GoogleClient(response)
        with tempfile.NamedTemporaryFile(suffix=".png") as image:
            image.write(b"\x89PNG-input")
            image.flush()
            artifact = google_image_adapter.generate(
                client=client,
                route=_route("google", "gemini-3.1-flash-image"),
                prompt="Edit",
                input_files=[
                    {"path": image.name, "modality": "image", "ext": "png"}
                ],
            )

        inline = client.captured["contents"][0]["parts"][1]["inline_data"]
        self.assertEqual(inline["mime_type"], "image/png")
        self.assertEqual(inline["data"], b"\x89PNG-input")
        self.assertEqual((artifact.ext, artifact.mime), ("jpg", "image/jpeg"))

    def test_missing_inline_data_is_clean_error(self):
        client = _GoogleClient({"candidates": [{"content": {"parts": []}}]})
        with self.assertRaises(DriverError) as raised:
            google_image_adapter.generate(
                client=client,
                route=_route("google", "gemini-3.1-flash-image"),
                prompt="Generate",
            )
        self.assertEqual(raised.exception.code, "no_artifact")


class TestGenerationDispatch(unittest.TestCase):
    def test_exact_native_google_binding_uses_google_client(self):
        raw = b"native-google"
        client = _GoogleClient(
            {
                "parts": [
                    {
                        "inline_data": {
                            "mime_type": "image/png",
                            "data": raw,
                        }
                    }
                ]
            }
        )
        route = _route("google", "gemini-3.1-flash-image")
        with (
            patch.object(
                generation,
                "resolve_provider_route",
                return_value=route,
            ),
            patch.object(
                generation,
                "create_google_genai_client",
                return_value=client,
            ) as google_factory,
            patch.object(
                generation,
                "create_openai_sdk_client",
            ) as openai_factory,
        ):
            data, ext, mime, transcript = generation.generate_media(
                "gemini-3.1-flash-image",
                "image",
                prompt="Generate",
            )

        self.assertEqual((data, ext, mime), (raw, "png", "image/png"))
        self.assertIsNone(transcript)
        google_factory.assert_called_once_with(route)
        openai_factory.assert_not_called()

    def test_unbound_openai_compatible_model_returns_no_driver(self):
        with (
            patch.object(generation, "resolve_model_driver", return_value=None),
            patch.object(
                generation,
                "resolve_provider_route",
            ) as route_resolver,
        ):
            with self.assertRaises(UtilityRunError) as raised:
                generation.generate_media(
                    "site/custom-image",
                    "image",
                    prompt="Generate",
                )
        self.assertEqual(raised.exception.code, "no_driver")
        route_resolver.assert_not_called()


if __name__ == "__main__":
    unittest.main()
