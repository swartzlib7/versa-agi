"""Chat video-input adapter for the OpenRouter ``video_url`` wire shape."""

from __future__ import annotations

import base64
import mimetypes
import os
from typing import Any

ADAPTER_ID = "chat_video_in_content_parts"
DIRECTION = "input"
MODALITY = "video"

VIDEO_EXTENSIONS = {".mp4", ".mkv", ".mov"}
MAX_VIDEO_BYTES = 200 * 1024 * 1024
_MIME_BY_EXT = {
    ".mp4": "video/mp4",
    ".mkv": "video/x-matroska",
    ".mov": "video/quicktime",
}


def _guess_video_mime(path: str) -> str:
    mime, _ = mimetypes.guess_type(path)
    if mime and mime.startswith("video/"):
        return mime
    ext = os.path.splitext(path)[1].lower()
    return _MIME_BY_EXT.get(ext, "video/mp4")


def read_video_base64(path: str) -> tuple[str, str]:
    """Return (mime_type, base64_payload) for a validated local video asset."""

    size = os.path.getsize(path)
    if size > MAX_VIDEO_BYTES:
        raise ValueError(
            f"Video exceeds the 200 MB ingest limit ({size} bytes): {path}"
        )
    with open(path, "rb") as video:
        data = base64.standard_b64encode(video.read()).decode("ascii")
    return _guess_video_mime(path), data


def build_video_url_content_parts(
    path: str,
    *,
    caption: str | None = None,
) -> list[dict[str, Any]]:
    """Return the Z.ai chat-completions video_url content-parts shape."""

    mime, payload = read_video_base64(path)
    data_url = f"data:{mime};base64,{payload}"
    return [
        {"type": "text", "text": caption or f"Video at {path}"},
        {"type": "video_url", "video_url": {"url": data_url}},
    ]


def to_content_parts(
    *,
    path: str,
    caption: str | None = None,
    config: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Convert one validated video asset to model-compatible content parts."""

    del config  # Reserved for future modality-specific adapter options.
    return build_video_url_content_parts(path, caption=caption)
