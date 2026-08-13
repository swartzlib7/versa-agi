"""Shared parsing and packaging helpers for chat multimodal output adapters."""

from __future__ import annotations

import base64
import io
import shutil
import subprocess
import wave
from typing import Any

from model_drivers.errors import DriverError

PCM16_RATE = 24000
PCM16_CHANNELS = 1
PCM16_SAMPLE_WIDTH = 2

FFMPEG_CODEC = {
    "ogg": "libopus",
    "opus": "libopus",
    "mp3": "libmp3lame",
    "flac": "flac",
    "m4a": "aac",
}

MIME_EXT = {
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/jpg": "jpg",
    "image/webp": "webp",
    "image/gif": "gif",
    "audio/wav": "wav",
    "audio/x-wav": "wav",
    "audio/mpeg": "mp3",
    "audio/mp3": "mp3",
    "audio/ogg": "ogg",
    "audio/flac": "flac",
}

AUDIO_FORMAT_MIME = {
    "wav": "audio/wav",
    "mp3": "audio/mpeg",
    "ogg": "audio/ogg",
    "flac": "audio/flac",
    "opus": "audio/ogg",
    "m4a": "audio/mp4",
}


def build_openai_user_content(
    prompt: str,
    input_files: list[dict],
    output_modality: str,
):
    """Build OpenAI-compatible content, including image-to-image inputs."""

    parts: list[dict] = [{"type": "text", "text": prompt}]
    if output_modality == "image":
        for item in input_files or []:
            if item.get("modality") != "image":
                continue
            with open(item["path"], "rb") as handle:
                encoded = base64.b64encode(handle.read()).decode("ascii")
            ext = str(item.get("ext") or "png").lower()
            parts.append(
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/{ext};base64,{encoded}"},
                }
            )
    return prompt if len(parts) == 1 else parts


def decode_data_url(url: str) -> tuple[bytes, str]:
    if not url or not url.startswith("data:"):
        raise DriverError(
            "bad_response",
            "Expected a base64 data URL in the model response",
        )
    header, _, encoded = url.partition(",")
    mime = header[5:].split(";")[0].strip() or "application/octet-stream"
    try:
        return base64.b64decode(encoded), mime
    except Exception as error:  # noqa: BLE001
        raise DriverError(
            "bad_response",
            f"Could not decode base64 artifact: {error}",
        ) from error


def message_dict(response: Any) -> dict:
    """Normalize the first OpenAI-compatible assistant message to a dict."""

    try:
        return response.choices[0].message.model_dump()
    except Exception:  # noqa: BLE001
        message = response.choices[0].message
        return {
            "images": getattr(message, "images", None),
            "audio": getattr(message, "audio", None),
            "content": getattr(message, "content", None),
        }


def parse_image(message: dict) -> tuple[bytes, str, str, None]:
    images = message.get("images") or []
    if not images:
        raise DriverError("no_artifact", "Model returned no image")
    url = ((images[0] or {}).get("image_url") or {}).get("url") or ""
    data, mime = decode_data_url(url)
    return data, MIME_EXT.get(mime, "png"), mime, None


def parse_audio(
    message: dict,
    requested_format: str,
) -> tuple[bytes, str, str, str | None]:
    audio = message.get("audio") or {}
    encoded = audio.get("data")
    if not encoded:
        raise DriverError("no_artifact", "Model returned no audio")
    try:
        data = base64.b64decode(encoded)
    except Exception as error:  # noqa: BLE001
        raise DriverError(
            "bad_response",
            f"Could not decode audio: {error}",
        ) from error
    output_format = (requested_format or "mp3").lower()
    return (
        data,
        output_format,
        AUDIO_FORMAT_MIME.get(output_format, "audio/mpeg"),
        audio.get("transcript"),
    )


def stream_audio(stream: Any) -> tuple[bytes, str | None]:
    """Accumulate streamed base64 pcm16 and transcript fragments."""

    encoded_parts: list[str] = []
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
                encoded_parts.append(audio["data"])
            if audio.get("transcript"):
                transcript_parts.append(audio["transcript"])
        except Exception:  # noqa: BLE001
            continue
    if not encoded_parts:
        raise DriverError("no_artifact", "Model returned no audio")
    try:
        pcm = base64.b64decode("".join(encoded_parts))
    except Exception as error:  # noqa: BLE001
        raise DriverError(
            "bad_response",
            f"Could not decode audio: {error}",
        ) from error
    return pcm, ("".join(transcript_parts) or None)


def pcm16_to_wav(pcm: bytes) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav_file:
        wav_file.setnchannels(PCM16_CHANNELS)
        wav_file.setsampwidth(PCM16_SAMPLE_WIDTH)
        wav_file.setframerate(PCM16_RATE)
        wav_file.writeframes(pcm)
    return buffer.getvalue()


def transcode_wav(wav_data: bytes, target_format: str) -> bytes | None:
    codec = FFMPEG_CODEC.get(target_format)
    if not codec or not shutil.which("ffmpeg"):
        return None
    try:
        process = subprocess.run(
            [
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "error",
                "-f",
                "wav",
                "-i",
                "pipe:0",
                "-c:a",
                codec,
                "-f",
                target_format,
                "pipe:1",
            ],
            input=wav_data,
            capture_output=True,
            timeout=120,
        )
        if process.returncode == 0 and process.stdout:
            return process.stdout
    except Exception:  # noqa: BLE001
        return None
    return None


def package_audio(pcm: bytes, requested_format: str) -> tuple[bytes, str, str]:
    """Package pcm16 as WAV or transcode, preserving WAV fallback."""

    output_format = (requested_format or "wav").lower()
    wav_data = pcm16_to_wav(pcm)
    if output_format in ("wav", "pcm16", "pcm"):
        return wav_data, "wav", "audio/wav"
    encoded = transcode_wav(wav_data, output_format)
    if encoded is not None:
        return (
            encoded,
            output_format,
            AUDIO_FORMAT_MIME.get(output_format, "audio/ogg"),
        )
    return wav_data, "wav", "audio/wav"
