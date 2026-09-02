"""Package generated audio into a container (harness-wide policy).

Mirrors ``image_processing`` for the audio modality: ``[audio_processing]`` in
setup.ini holds only *generic* packaging policy. Provider-specific generation
knobs (OpenAI TTS ``voice``, for example) belong to the exact ModelDriver
``config``, not here.
"""

from __future__ import annotations

import configparser
import os
from dataclasses import dataclass

_SETUP_INI_PATHS = (
    "/etc/versa-agi/setup.ini",
    os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "setup.ini",
    ),
)

# WAV is written from PCM16 directly; the rest shell out to ffmpeg.
NATIVE_CONTAINER = "wav"
SUPPORTED_CONTAINERS = ("wav", "ogg", "mp3", "flac")


@dataclass(frozen=True)
class AudioProcessingConfig:
    enabled: bool = True
    container: str = "ogg"


def _setup_ini_path() -> str:
    for path in _SETUP_INI_PATHS:
        if os.path.isfile(path):
            return path
    return _SETUP_INI_PATHS[0]


def load_audio_processing_config() -> AudioProcessingConfig:
    """Read [audio_processing] from setup.ini (defaults when missing)."""
    defaults = AudioProcessingConfig()
    path = _setup_ini_path()
    if not os.path.isfile(path):
        return defaults
    parser = configparser.ConfigParser()
    try:
        parser.read(path, encoding="utf-8")
    except Exception:
        return defaults
    if not parser.has_section("audio_processing"):
        return defaults
    sec = parser["audio_processing"]

    raw_enabled = sec.get("enabled", str(defaults.enabled)).strip().lower()
    container = sec.get("format", defaults.container).strip().lower()
    if container not in SUPPORTED_CONTAINERS:
        container = defaults.container
    return AudioProcessingConfig(
        enabled=raw_enabled in ("1", "true", "yes", "on"),
        container=container,
    )


def resolve_container(
    requested: str | None = None,
    *,
    config: AudioProcessingConfig | None = None,
) -> tuple[str, dict]:
    """Return (container, metadata) for packaging PCM16.

    ``requested`` is the per-run override (ModelDriver config or Utility Profile
    ``config_json``). When processing is disabled the native container wins, so
    a site can opt out of ffmpeg entirely without editing every profile.
    """
    cfg = config or load_audio_processing_config()
    asked = (requested or "").strip().lower() or cfg.container
    if asked not in SUPPORTED_CONTAINERS:
        asked = cfg.container
    meta: dict = {
        "processing_enabled": cfg.enabled,
        "requested_container": asked,
    }
    if not cfg.enabled and asked != NATIVE_CONTAINER:
        meta["processing_skipped"] = "audio_processing disabled: native container"
        return NATIVE_CONTAINER, meta
    return asked, meta
