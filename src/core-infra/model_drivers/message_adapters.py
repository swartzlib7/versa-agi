"""Build provider-native multimodal HumanMessage content parts."""

from __future__ import annotations

import base64
import mimetypes
import os
from typing import Any

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}


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
    """Return (mime_type, base64_payload) for a local image file."""
    with open(path, "rb") as f:
        data = base64.standard_b64encode(f.read()).decode("ascii")
    return _guess_image_mime(path), data


def build_image_content_parts(
    path: str,
    provider_family: str,
    *,
    caption: str | None = None,
) -> list[dict[str, Any]]:
    """Return LangChain-compatible content parts for one local image."""
    mime, b64 = read_image_base64(path)
    data_url = f"data:{mime};base64,{b64}"
    parts: list[dict[str, Any]] = []
    text = caption or f"Image at {path}"
    parts.append({"type": "text", "text": text})

    if provider_family == "google":
        # Gemini (langchain-google-genai) image input: OpenAI-style image_url
        # data-URI block — the documented path for still images (the `media`
        # block is documented for video/PDF/audio). Both decode to identical
        # inline_data bytes in langchain-google-genai 4.x, so this is the
        # canonical/documented form, not a behavioral fix.
        parts.append({"type": "image_url", "image_url": {"url": data_url}})
    elif provider_family == "local":
        # ChatOllama — data URI string form
        parts.append({"type": "image_url", "image_url": data_url})
    else:
        # openai_compat, anthropic, llamacpp — OpenAI-style image_url block
        parts.append({"type": "image_url", "image_url": {"url": data_url}})

    return parts


def build_trimmed_text_part(path: str, *, caption: str | None = None) -> list[dict[str, Any]]:
    """Text-only replacement after surgical trim of injected modality blocks."""
    text = caption or f"[Viewed image — payload trimmed from checkpoint: {path}]"
    return [{"type": "text", "text": text}]


def content_has_image_parts(content: object) -> bool:
    """True when message content includes an image modality block."""
    if not isinstance(content, list):
        return False
    for part in content:
        if not isinstance(part, dict):
            continue
        ptype = part.get("type", "")
        if ptype in ("image_url", "media", "image"):
            return True
        if ptype == "image_url" and part.get("image_url"):
            return True
    return False


def trim_image_parts_from_message(content: object, path: str) -> list[dict[str, Any]]:
    """Replace multimodal image blocks with a text placeholder."""
    if isinstance(content, str):
        return build_trimmed_text_part(path, caption=content)
    if isinstance(content, list):
        text_bits = [
            p.get("text", "")
            for p in content
            if isinstance(p, dict) and p.get("type") == "text" and p.get("text")
        ]
        caption = text_bits[0] if text_bits else None
        return build_trimmed_text_part(path, caption=caption)
    return build_trimmed_text_part(path)
