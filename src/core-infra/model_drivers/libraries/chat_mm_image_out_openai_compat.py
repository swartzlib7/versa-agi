"""OpenAI-compatible chat image-output DriverAdapter.

Known wire: message.images[].image_url data-URL (UTIL-002 / DR-CM-01).
"""

from __future__ import annotations

from typing import Any

from model_drivers.artifacts import GeneratedArtifact
from model_drivers.libraries.chat_mm_common import (
    build_openai_user_content,
    message_dict,
    parse_image,
)
from provider_runtime import ProviderRoute

ADAPTER_ID = "chat_mm_image_out_openai_compat"
METHOD_FAMILY = "chat_multimodal"
DIRECTION = "output"
MODALITY = "image"


def generate(
    *,
    client: Any,
    route: ProviderRoute,
    prompt: str,
    input_files: list[dict[str, Any]] | None = None,
    config: dict[str, Any] | None = None,
) -> GeneratedArtifact:
    """Generate and parse an OpenAI-compatible chat image response."""

    resolved_config = config or {}
    extra_body: dict[str, Any] = {"modalities": ["image", "text"]}
    if isinstance(resolved_config.get("image_config"), dict):
        extra_body["image_config"] = dict(resolved_config["image_config"])

    response = client.chat.completions.create(
        model=route.api_model,
        messages=[
            {
                "role": "user",
                "content": build_openai_user_content(
                    prompt,
                    input_files or [],
                    MODALITY,
                ),
            }
        ],
        extra_body=extra_body,
        timeout=180,
        stream=False,
    )
    data, ext, mime, transcript = parse_image(message_dict(response))
    return GeneratedArtifact(data, ext, mime, transcript)
