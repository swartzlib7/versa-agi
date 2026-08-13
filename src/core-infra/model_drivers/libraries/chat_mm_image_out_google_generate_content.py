"""Native Google ``generateContent`` image-output DriverAdapter."""

from __future__ import annotations

import base64
import mimetypes
from typing import Any

from model_drivers.artifacts import GeneratedArtifact
from model_drivers.errors import DriverError
from model_drivers.libraries.chat_mm_common import MIME_EXT
from provider_runtime import ProviderRoute

ADAPTER_ID = "chat_mm_image_out_google_generate_content"
METHOD_FAMILY = "chat_multimodal"
DIRECTION = "output"
MODALITY = "image"


def _value(value: Any, key: str, default: Any = None) -> Any:
    if isinstance(value, dict):
        return value.get(key, default)
    return getattr(value, key, default)


def _input_mime(item: dict[str, Any]) -> str:
    declared = str(item.get("mime") or "").strip()
    if declared:
        return declared
    guessed = mimetypes.guess_type(str(item.get("path") or ""))[0]
    if guessed:
        return guessed
    ext = str(item.get("ext") or "png").lower().lstrip(".")
    return "image/jpeg" if ext in ("jpg", "jpeg") else f"image/{ext}"


def _contents(prompt: str, input_files: list[dict[str, Any]]) -> list[dict[str, Any]]:
    parts: list[dict[str, Any]] = [{"text": prompt}]
    for item in input_files:
        if item.get("modality") != "image":
            continue
        with open(item["path"], "rb") as handle:
            data = handle.read()
        parts.append(
            {
                "inline_data": {
                    "mime_type": _input_mime(item),
                    "data": data,
                }
            }
        )
    return [{"role": "user", "parts": parts}]


def _response_parts(response: Any) -> list[Any]:
    direct = _value(response, "parts")
    if direct:
        return list(direct)
    candidates = _value(response, "candidates", []) or []
    if not candidates:
        return []
    content = _value(candidates[0], "content")
    return list(_value(content, "parts", []) or [])


def _parse_response(response: Any) -> GeneratedArtifact:
    transcript_parts: list[str] = []
    for part in _response_parts(response):
        text = _value(part, "text")
        if text:
            transcript_parts.append(str(text))
        inline_data = _value(part, "inline_data")
        if inline_data is None:
            inline_data = _value(part, "inlineData")
        if inline_data is None:
            continue
        raw_data = _value(inline_data, "data")
        mime = str(
            _value(
                inline_data,
                "mime_type",
                _value(inline_data, "mimeType", "image/png"),
            )
            or "image/png"
        )
        if isinstance(raw_data, str):
            try:
                data = base64.b64decode(raw_data)
            except Exception as error:  # noqa: BLE001
                raise DriverError(
                    "bad_response",
                    f"Could not decode Google image bytes: {error}",
                ) from error
        elif isinstance(raw_data, (bytes, bytearray)):
            data = bytes(raw_data)
        else:
            raise DriverError(
                "bad_response",
                "Google image response contained invalid inlineData",
            )
        return GeneratedArtifact(
            data=data,
            ext=MIME_EXT.get(mime, "png"),
            mime=mime,
            transcript=("".join(transcript_parts) or None),
        )
    raise DriverError("no_artifact", "Model returned no image")


def generate(
    *,
    client: Any,
    route: ProviderRoute,
    prompt: str,
    input_files: list[dict[str, Any]] | None = None,
    config: dict[str, Any] | None = None,
) -> GeneratedArtifact:
    """Invoke native Google image generation and parse ``parts[].inlineData``."""

    resolved_config = config or {}
    generation_config: dict[str, Any] = {
        "response_modalities": ["TEXT", "IMAGE"],
    }
    if isinstance(resolved_config.get("image_config"), dict):
        generation_config["image_config"] = dict(resolved_config["image_config"])

    try:
        response = client.models.generate_content(
            model=route.api_model,
            contents=_contents(prompt, input_files or []),
            config=generation_config,
        )
    except DriverError:
        raise
    except Exception as error:  # noqa: BLE001
        raise DriverError(
            "generation_failed",
            f"{type(error).__name__}: {error}",
        ) from error
    return _parse_response(response)
