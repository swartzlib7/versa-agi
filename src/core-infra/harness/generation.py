"""Generation invocation for non-text Utility Model output (image, audio).

Calls an OpenAI-compatible provider (OpenRouter / OpenAI / xAI) via the ``openai``
SDK and returns the decoded artifact bytes. Response shapes follow the documented
OpenRouter / OpenAI multimodal output:

- **image** — ``message.images[].image_url.url`` is a base64 ``data:`` URL
  (verified against OpenRouter image-generation docs, e.g. ``google/gemini-3.1-flash-image``).
- **audio** — ``message.audio.data`` is base64; the request ``audio.format`` sets the
  container (OpenAI audio-output shape, e.g. ``openai/gpt-audio``).

**Video** generation is intentionally not wired — there is no video-*output* model in
the catalog (video-capable rows are video *input* → text), and video generation uses a
separate async/polling API rather than chat completions.
"""

from __future__ import annotations

import base64
import io
import os
import shutil
import subprocess
import wave
from typing import Any

from harness.utility_runner import UtilityRunError

_OR_HEADERS = {"HTTP-Referer": "https://versavoice.ai", "X-Title": "Versa AGi"}

# OpenAI/OpenRouter streamed audio is delivered as 16-bit signed little-endian
# PCM, mono, at 24 kHz. (Streaming only supports format='pcm16'; container
# formats like mp3/ogg are rejected — so we receive raw PCM and package it here.)
_PCM16_RATE = 24000
_PCM16_CHANNELS = 1
_PCM16_SAMPLE_WIDTH = 2
# Containers we can produce from PCM. 'wav' is native (stdlib); the rest need
# ffmpeg (codec name keyed for the transcode call).
_FFMPEG_CODEC = {"ogg": "libopus", "opus": "libopus", "mp3": "libmp3lame", "flac": "flac", "m4a": "aac"}


_MIME_EXT = {
    "image/png": "png", "image/jpeg": "jpg", "image/jpg": "jpg",
    "image/webp": "webp", "image/gif": "gif",
    "audio/wav": "wav", "audio/x-wav": "wav", "audio/mpeg": "mp3",
    "audio/mp3": "mp3", "audio/ogg": "ogg", "audio/flac": "flac",
}
_AUDIO_FORMAT_MIME = {
    "wav": "audio/wav", "mp3": "audio/mpeg", "ogg": "audio/ogg",
    "flac": "audio/flac", "opus": "audio/ogg", "m4a": "audio/mp4",
}

# Watchdog-owned provider key store (root:watchdog... actually watchdog:watchdog 600).
# Both Utility-run entry points (agent-invoked ``agictl utility run`` and the
# lifeline ``run-due-tasks`` task path) funnel through the ``agictl`` wrapper,
# which elevates to the ``watchdog`` user via ``sudo -u watchdog`` before this
# code executes. Watchdog owns ``provider_keys.env``, so it can read the secret
# here — the agent user never opens the file directly (Zero-Trust preserved).
_PROVIDER_KEYS_ENV = "/etc/versa-agi/provider_keys.env"
_PROVIDER_KEYS_ENV_LEGACY = "/etc/versa-agi/inference_endpoint.env"


def _provider_key(var_name: str) -> str | None:
    """Resolve a provider API key, preferring the inherited environment.

    The LangGraph harness spawn already receives keys exported by lifeline, so a
    present ``os.environ`` value wins. Utility runs reach this code as ``watchdog``
    without those exports, so fall back to the watchdog-owned key store.
    """
    val = os.getenv(var_name)
    if val:
        return val
    for path in (_PROVIDER_KEYS_ENV, _PROVIDER_KEYS_ENV_LEGACY):
        try:
            with open(path, "r") as f:
                for raw in f:
                    line = raw.strip()
                    if not line or line.startswith("#"):
                        continue
                    if line.startswith("export "):
                        line = line[len("export "):].lstrip()
                    name, sep, value = line.partition("=")
                    if not sep or name.strip() != var_name:
                        continue
                    value = value.strip().strip('"').strip("'")
                    if value:
                        return value
        except OSError:
            continue
    return None


def _resolve_openai_compatible(model_name: str) -> tuple[str, str, dict]:
    """Return (base_url, api_key, default_headers) for an OpenAI-compatible provider.

    Mirrors the provider routing in ``agent_harness.get_llm`` for the OpenAI-compatible
    families (OpenRouter ``vendor/model``, OpenAI ``gpt-*``, xAI ``grok-*``). Direct
    Google/Anthropic generation would need their own SDKs and is not wired.
    """
    if "/" in model_name:
        key = _provider_key("OPENROUTER_API_KEY")
        if not key:
            raise UtilityRunError("no_key", "OPENROUTER_API_KEY required for OpenRouter generation models")
        return "https://openrouter.ai/api/v1", key, dict(_OR_HEADERS)
    if model_name.startswith("gpt"):
        key = _provider_key("OPENAI_API_KEY")
        if not key:
            raise UtilityRunError("no_key", "OPENAI_API_KEY required for OpenAI generation models")
        return "https://api.openai.com/v1", key, {}
    if model_name.startswith("grok"):
        key = _provider_key("XAI_API_KEY")
        if not key:
            raise UtilityRunError("no_key", "XAI_API_KEY required for xAI generation models")
        return "https://api.x.ai/v1", key, {}
    raise UtilityRunError(
        "provider_unsupported",
        f"Generation for '{model_name}' is not wired — use an OpenRouter-namespaced generation model",
    )


def _build_user_content(prompt: str, input_files: list[dict], output_modality: str):
    """Build the user message content; attach image inputs for image-to-image."""
    parts: list[dict] = [{"type": "text", "text": prompt}]
    if output_modality == "image":
        for item in input_files or []:
            if item.get("modality") == "image":
                with open(item["path"], "rb") as f:
                    b64 = base64.b64encode(f.read()).decode("ascii")
                ext = item.get("ext", "png")
                parts.append({
                    "type": "image_url",
                    "image_url": {"url": f"data:image/{ext};base64,{b64}"},
                })
    # A single text part can be sent as a plain string (widest compatibility).
    return prompt if len(parts) == 1 else parts


def _decode_data_url(url: str) -> tuple[bytes, str]:
    if not url or not url.startswith("data:"):
        raise UtilityRunError("bad_response", "Expected a base64 data URL in the model response")
    header, _, b64 = url.partition(",")
    mime = header[5:].split(";")[0].strip() or "application/octet-stream"
    try:
        return base64.b64decode(b64), mime
    except Exception as e:  # noqa: BLE001 - surface as a clean run error
        raise UtilityRunError("bad_response", f"Could not decode base64 artifact: {e}")


def _message_dict(resp: Any) -> dict:
    """Normalize the first choice's assistant message to a dict (extras included)."""
    try:
        return resp.choices[0].message.model_dump()
    except Exception:  # noqa: BLE001 - fall back to attribute access
        msg = resp.choices[0].message
        return {
            "images": getattr(msg, "images", None),
            "audio": getattr(msg, "audio", None),
            "content": getattr(msg, "content", None),
        }


def _parse_image(msg: dict) -> tuple[bytes, str, str, None]:
    images = msg.get("images") or []
    if not images:
        raise UtilityRunError("no_artifact", "Model returned no image")
    url = ((images[0] or {}).get("image_url") or {}).get("url") or ""
    data, mime = _decode_data_url(url)
    return data, _MIME_EXT.get(mime, "png"), mime, None


def _parse_audio(msg: dict, req_format: str) -> tuple[bytes, str, str, str | None]:
    audio = msg.get("audio") or {}
    b64 = audio.get("data")
    if not b64:
        raise UtilityRunError("no_artifact", "Model returned no audio")
    try:
        data = base64.b64decode(b64)
    except Exception as e:  # noqa: BLE001
        raise UtilityRunError("bad_response", f"Could not decode audio: {e}")
    fmt = (req_format or "mp3").lower()
    return data, fmt, _AUDIO_FORMAT_MIME.get(fmt, "audio/mpeg"), audio.get("transcript")


def _stream_audio(stream: Any) -> tuple[bytes, str | None]:
    """Accumulate a streamed audio response into raw PCM bytes.

    OpenRouter/OpenAI require ``stream: true`` for audio output, and streaming
    only supports ``format='pcm16'``. The base64 PCM payload arrives as
    ``delta.audio.data`` fragments across chunks (concatenate the base64 *before*
    decoding) and the spoken text as ``delta.audio.transcript`` fragments. Returns
    ``(pcm_bytes, transcript)``; the caller packages the PCM into a container.
    """
    b64_parts: list[str] = []
    transcript_parts: list[str] = []
    for chunk in stream:
        try:
            choices = chunk.choices or []
            if not choices:
                continue
            delta = choices[0].delta
            audio = getattr(delta, "audio", None)
            if audio is None and hasattr(delta, "model_dump"):
                audio = (delta.model_dump() or {}).get("audio")
            if not audio:
                continue
            if not isinstance(audio, dict):
                audio = audio.model_dump() if hasattr(audio, "model_dump") else {}
            if audio.get("data"):
                b64_parts.append(audio["data"])
            if audio.get("transcript"):
                transcript_parts.append(audio["transcript"])
        except Exception:  # noqa: BLE001 - tolerate per-chunk shape drift
            continue
    if not b64_parts:
        raise UtilityRunError("no_artifact", "Model returned no audio")
    try:
        pcm = base64.b64decode("".join(b64_parts))
    except Exception as e:  # noqa: BLE001
        raise UtilityRunError("bad_response", f"Could not decode audio: {e}")
    return pcm, ("".join(transcript_parts) or None)


def _pcm16_to_wav(pcm: bytes) -> bytes:
    """Wrap raw PCM16 (mono, 24 kHz) in a WAV container — stdlib, no deps."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(_PCM16_CHANNELS)
        wf.setsampwidth(_PCM16_SAMPLE_WIDTH)
        wf.setframerate(_PCM16_RATE)
        wf.writeframes(pcm)
    return buf.getvalue()


def _transcode_wav(wav: bytes, target_fmt: str) -> bytes | None:
    """Transcode WAV bytes to ``target_fmt`` via ffmpeg. Returns None if ffmpeg
    is unavailable or the encode fails (caller falls back to WAV)."""
    codec = _FFMPEG_CODEC.get(target_fmt)
    if not codec or not shutil.which("ffmpeg"):
        return None
    try:
        proc = subprocess.run(
            ["ffmpeg", "-hide_banner", "-loglevel", "error",
             "-f", "wav", "-i", "pipe:0",
             "-c:a", codec, "-f", target_fmt, "pipe:1"],
            input=wav, capture_output=True, timeout=120,
        )
        if proc.returncode == 0 and proc.stdout:
            return proc.stdout
    except Exception:  # noqa: BLE001
        return None
    return None


def _package_audio(pcm: bytes, req_format: str) -> tuple[bytes, str, str]:
    """Package raw streamed PCM16 into the requested container.

    Returns ``(data, ext, mime)``. ``wav`` is produced natively; ``ogg``/``mp3``/
    ``flac`` are transcoded via ffmpeg when available, otherwise we fall back to
    WAV (the artifact is still valid, just a different container).
    """
    fmt = (req_format or "wav").lower()
    wav = _pcm16_to_wav(pcm)
    if fmt in ("wav", "pcm16", "pcm"):
        return wav, "wav", "audio/wav"
    encoded = _transcode_wav(wav, fmt)
    if encoded is not None:
        return encoded, fmt, _AUDIO_FORMAT_MIME.get(fmt, "audio/ogg")
    # ffmpeg missing / failed — fall back to the always-valid WAV container.
    return wav, "wav", "audio/wav"




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
    config = config or {}
    base_url, api_key, headers = _resolve_openai_compatible(catalog_model)

    from openai import OpenAI

    client = OpenAI(base_url=base_url, api_key=api_key, default_headers=headers)
    content = _build_user_content(prompt, input_files or [], output_modality)

    extra: dict[str, Any] = {}
    audio_format = "wav"
    if output_modality == "image":
        extra["modalities"] = ["image", "text"]
        if isinstance(config.get("image_config"), dict):
            extra["image_config"] = config["image_config"]
    elif output_modality == "audio":
        # Container/voice resolution: per-UM config_json wins, else the global
        # [audio_processing] defaults (agitop → System Settings → Audio Processing).
        from model_catalog import read_setup_value

        audio_format = str(
            config.get("audio_format") or read_setup_value("audio_processing", "format", "wav")
        ).lower()
        voice = config.get("voice") or read_setup_value("audio_processing", "voice", "alloy")
        extra["modalities"] = ["text", "audio"]
        # Streaming audio ONLY supports format='pcm16' — container formats are
        # rejected, so we always request pcm16 and encode locally.
        extra["audio"] = {"voice": voice, "format": "pcm16"}
    else:
        raise UtilityRunError("driver_pending", f"No generation path for modality '{output_modality}'")

    # Audio output must be streamed (OpenRouter rejects non-streamed audio with
    # "Audio output requires stream: true"); image stays a single response.
    stream = output_modality == "audio"
    try:
        resp = client.chat.completions.create(
            model=catalog_model,
            messages=[{"role": "user", "content": content}],
            extra_body=extra,
            timeout=180,
            stream=stream,
        )
    except UtilityRunError:
        raise
    except Exception as e:  # noqa: BLE001 - normalize SDK/HTTP errors
        raise UtilityRunError("generation_failed", f"{type(e).__name__}: {e}")

    if output_modality == "audio":
        pcm, transcript = _stream_audio(resp)
        data, ext, mime = _package_audio(pcm, audio_format)
        return data, ext, mime, transcript
    msg = _message_dict(resp)
    return _parse_image(msg)
