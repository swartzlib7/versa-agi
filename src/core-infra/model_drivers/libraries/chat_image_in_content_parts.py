"""Shared chat image-input adapter for the canonical image_url wire shape."""

from __future__ import annotations

import base64
import mimetypes
import os
from typing import Any

ADAPTER_ID = "chat_image_in_content_parts"
DIRECTION = "input"
MODALITY = "image"


def _guess_image_mime(path: str) -> str:
    mime, _ = mimetypes.guess_type(path)
    if mime and mime.startswith("image/"):
        return mime
    ext = os.path.splitext(path)[1].lower()
    fallback = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".gif": "image/gif",
        ".webp": "image/webp",
        ".bmp": "image/bmp",
    }
    return fallback.get(ext, "image/png")


def read_image_base64(path: str) -> tuple[str, str]:
    """Return (mime_type, base64_payload) for a validated image asset."""

    with open(path, "rb") as image:
        data = base64.standard_b64encode(image.read()).decode("ascii")
    return _guess_image_mime(path), data


def build_image_url_content_parts(
    path: str,
    *,
    caption: str | None = None,
) -> list[dict[str, Any]]:
    """Return the canonical nested image_url content-parts shape."""

    mime, payload = read_image_base64(path)
    data_url = f"data:{mime};base64,{payload}"
    return [
        {"type": "text", "text": caption or f"Image at {path}"},
        {"type": "image_url", "image_url": {"url": data_url}},
    ]


def to_content_parts(
    *,
    path: str,
    caption: str | None = None,
    config: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Convert one validated image asset to model-compatible content parts."""

    del config  # Reserved for future modality-specific adapter options.
    return build_image_url_content_parts(path, caption=caption)
