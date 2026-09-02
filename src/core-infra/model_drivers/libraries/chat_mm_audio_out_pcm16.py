"""Chat multimodal audio-output DriverAdapter.

Wire: streamed pcm16 audio parts + optional transcript (DR-CM-02, DR-CM-03,
DR-CM-10). ``voice`` here is a *generation* parameter for multimodal TTS output
and is provider-specific (OpenAI uses ``alloy``/``verse``/…), so the exact
ModelDriver config supplies it and a Utility Profile ``config_json`` overrides
it. There is no default: a binding that omits it is a binding error.

This is unrelated to the Versa AGi **agent voice** (`[agent] voice` /
``agictl identity provision --voice``: male (X), female (Y), reflective =
the PU's cloned voice). That is a separate product capability for spoken
modes, not a ModelDriver concern.

Container packaging is generic policy from ``[audio_processing]``.
"""

from __future__ import annotations

from typing import Any

from model_drivers.artifacts import GeneratedArtifact
from model_drivers.audio_processing import resolve_container
from model_drivers.errors import DriverError
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
    audio_format, _packaging = resolve_container(
        resolved_config.get("audio_format"),
    )
    voice = str(resolved_config.get("voice") or "").strip()
    if not voice:
        raise DriverError(
            "voice_required",
            "No 'voice' for this audio binding. TTS voice IDs are "
            "provider-specific: set one on the exact ModelDriver config in "
            "model_drivers/registry.py, or in the Utility Profile config_json.",
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
