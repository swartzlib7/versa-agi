"""Compatibility entrypoint for non-text Utility Model generation.

Exact catalog Models dispatch through registered output DriverAdapters. Models
without an exact executable output binding fail cleanly with ``no_driver``.

- **image** — ``message.images[].image_url.url`` is a base64 ``data:`` URL
  (verified against OpenRouter image-generation docs, e.g. ``google/gemini-3.1-flash-image``).
- **audio** — streamed pcm16 plus optional transcript, packaged locally.
- **native Google image** — ``generateContent`` response ``parts[].inlineData``.

**Video** generation is intentionally not wired — there is no video-*output* model in
the catalog (video-capable rows are video *input* → text), and video generation uses a
separate async/polling API rather than chat completions.
"""

from __future__ import annotations

from harness.utility_runner import UtilityRunError
from model_drivers.errors import DriverError
from model_drivers.libraries import (
    chat_mm_audio_out_pcm16,
    chat_mm_image_out_google_generate_content,
    chat_mm_image_out_openai_compat,
)
from model_drivers.libraries.chat_mm_common import (
    AUDIO_FORMAT_MIME as _AUDIO_FORMAT_MIME,
    MIME_EXT as _MIME_EXT,
    build_openai_user_content as _build_user_content,
    decode_data_url as _decode_data_url,
    message_dict as _message_dict,
    package_audio as _package_audio,
    parse_audio as _parse_audio,
    parse_image as _parse_image,
    pcm16_to_wav as _pcm16_to_wav,
    stream_audio as _stream_audio,
    transcode_wav as _transcode_wav,
)
from model_drivers.registry import resolve_model_driver
from provider_runtime import (
    ProviderRuntimeError,
    create_google_genai_client,
    create_openai_sdk_client,
    resolve_provider_route,
)

def generate_media(
    catalog_model: str,
    output_modality: str,
    *,
    prompt: str,
    input_files: list[dict] | None = None,
    config: dict | None = None,
) -> tuple[bytes, str, str, str | None]:
    """Generate an image/audio artifact.

    Returns ``(data, ext, mime, transcript_or_None)``. ``config`` (UM ``config_json``)
    may carry ``image_config`` (aspect_ratio/image_size), ``voice``, and ``audio_format``.
    """
    normalized_modality = (output_modality or "").strip().lower()
    if normalized_modality not in ("image", "audio"):
        raise UtilityRunError(
            "driver_pending",
            f"No generation path for modality '{output_modality}'",
        )

    try:
        resolved = resolve_model_driver(
            catalog_model,
            "output",
            normalized_modality,
        )
        if resolved is None:
            raise UtilityRunError(
                "no_driver",
                f"No exact executable ModelDriver for {catalog_model} "
                f"output {normalized_modality}",
            )
        route = resolve_provider_route(catalog_model)
        adapter_id = resolved.adapter.adapter_id
        entrypoint = resolved.adapter.entrypoint
        effective_config = {
            **resolved.binding.config,
            **dict(config or {}),
        }

        if (
            adapter_id
            == chat_mm_image_out_google_generate_content.ADAPTER_ID
        ):
            client = create_google_genai_client(route)
        elif adapter_id in (
            chat_mm_image_out_openai_compat.ADAPTER_ID,
            chat_mm_audio_out_pcm16.ADAPTER_ID,
        ):
            client = create_openai_sdk_client(route)
        else:
            raise UtilityRunError(
                "no_driver",
                f"No Provider client factory for DriverAdapter '{adapter_id}'",
            )

        artifact = entrypoint(
            client=client,
            route=route,
            prompt=prompt,
            input_files=input_files or [],
            config=effective_config,
        )
    except ProviderRuntimeError as error:
        raise UtilityRunError(error.code, error.message) from error
    except DriverError as error:
        raise UtilityRunError(error.code, error.message) from error
    except UtilityRunError:
        raise
    except Exception as error:  # noqa: BLE001
        raise UtilityRunError(
            "generation_failed",
            f"{type(error).__name__}: {error}",
        ) from error
    return artifact.as_tuple()
