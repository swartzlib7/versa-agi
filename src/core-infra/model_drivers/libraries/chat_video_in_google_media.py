"""Native Google video-input adapter (LangChain ``media`` → ``inline_data``)."""

from __future__ import annotations

import mimetypes
import os
from typing import Any

ADAPTER_ID = "chat_video_in_google_media"
DIRECTION = "input"
MODALITY = "video"

VIDEO_EXTENSIONS = {".mp4", ".mkv", ".mov", ".webm", ".mpeg", ".mpg", ".avi"}
MAX_VIDEO_BYTES = 20 * 1024 * 1024
_MIME_BY_EXT = {
    ".mp4": "video/mp4",
    ".mkv": "video/x-matroska",
    ".mov": "video/quicktime",
    ".webm": "video/webm",
    ".mpeg": "video/mpeg",
    ".mpg": "video/mpg",
    ".avi": "video/avi",
}


def _guess_video_mime(path: str) -> str:
    mime, _ = mimetypes.guess_type(path)
    if mime and mime.startswith("video/"):
        return mime
    ext = os.path.splitext(path)[1].lower()
    return _MIME_BY_EXT.get(ext, "video/mp4")


def read_video_bytes(path: str) -> tuple[str, bytes]:
    """Return (mime_type, raw_bytes) for a validated local video asset."""

    size = os.path.getsize(path)
    if size > MAX_VIDEO_BYTES:
        raise ValueError(
            f"Video exceeds the 20 MB native Google inline limit ({size} bytes): {path}"
        )
    with open(path, "rb") as video:
        data = video.read()
    return _guess_video_mime(path), data


def build_google_media_content_parts(
    path: str,
    *,
    caption: str | None = None,
) -> list[dict[str, Any]]:
    """Return LangChain Google ``media`` parts (converted to ``inline_data``)."""

    mime, payload = read_video_bytes(path)
    return [
        {"type": "text", "text": caption or f"Video at {path}"},
        {"type": "media", "mime_type": mime, "data": payload},
    ]


def to_content_parts(
    *,
    path: str,
    caption: str | None = None,
    config: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Convert one validated video asset to native-Google-compatible parts."""

    del config
    return build_google_media_content_parts(path, caption=caption)
