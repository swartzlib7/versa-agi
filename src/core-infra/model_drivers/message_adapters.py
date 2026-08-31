"""Build provider-native multimodal HumanMessage content parts."""

from __future__ import annotations

from typing import Any

from model_drivers.libraries.chat_image_in_content_parts import (
    build_image_url_content_parts,
    read_image_base64,
)

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}


def build_image_content_parts(
    path: str,
    provider_family: str,
    *,
    caption: str | None = None,
) -> list[dict[str, Any]]:
    """Return LangChain-compatible content parts for one local image."""
    if provider_family != "local":
        # Google, OpenAI-compatible, Anthropic, and llama.cpp currently accept
        # the same nested image_url data-URI shape.
        return build_image_url_content_parts(path, caption=caption)

    mime, b64 = read_image_base64(path)
    data_url = f"data:{mime};base64,{b64}"
    parts: list[dict[str, Any]] = []
    text = caption or f"Image at {path}"
    parts.append({"type": "text", "text": text})

    # ChatOllama uses the data URI string form.
    parts.append({"type": "image_url", "image_url": data_url})

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
        if ptype in ("image_url", "media", "image", "video_url"):
            return True
        if ptype == "image_url" and part.get("image_url"):
            return True
        if ptype == "video_url" and part.get("video_url"):
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
