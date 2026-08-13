"""Chat multimodal audio-output DriverAdapter.

Wire: streamed pcm16 audio parts + optional transcript (DR-CM-02, DR-CM-03).
"""

from __future__ import annotations

from typing import Any

from model_catalog import read_setup_value
from model_drivers.artifacts import GeneratedArtifact
from model_drivers.libraries.chat_mm_common import package_audio, stream_audio
from provider_runtime import ProviderRoute

ADAPTER_ID = "chat_mm_audio_out_pcm16"
METHOD_FAMILY = "chat_multimodal"
DIRECTION = "output"
MODALITY = "audio"


def generate(
    *,
    client: Any,
    route: ProviderRoute,
    prompt: str,
    input_files: list[dict[str, Any]] | None = None,
    config: dict[str, Any] | None = None,
) -> GeneratedArtifact:
    """Generate streamed pcm16 and package it in the requested container."""

    del input_files
    resolved_config = config or {}
    audio_format = str(
        resolved_config.get("audio_format")
        or read_setup_value("audio_processing", "format", "wav")
    ).lower()
    voice = (
        resolved_config.get("voice")
        or read_setup_value("audio_processing", "voice", "alloy")
    )

    response = client.chat.completions.create(
        model=route.api_model,
        messages=[{"role": "user", "content": prompt}],
        extra_body={
            "modalities": ["text", "audio"],
            "audio": {"voice": voice, "format": "pcm16"},
        },
        timeout=180,
        stream=True,
    )
    pcm, transcript = stream_audio(response)
    data, ext, mime = package_audio(pcm, audio_format)
    return GeneratedArtifact(data, ext, mime, transcript)
