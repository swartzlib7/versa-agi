"""Exact ModelDriver bindings and executable DriverAdapters.

TD-DRIVER-001 / MD-0R deliberately keeps the core registry static:

- ``MODEL_DRIVERS`` binds one exact catalog Model, direction, and modality.
- ``ADAPTERS`` contains executable shipped adapter entrypoints only.
- Provider metadata is derived from the catalog Model during resolution.

There are no model-family matchers, stub adapters, site overlays, dynamic
imports, or plugin lifecycle concerns in this core path.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable

from model_catalog import (
    load_catalog,
    load_providers,
    model_output_includes,
    parse_input_modalities,
)
from model_drivers.libraries import (
    chat_image_in_content_parts,
    chat_mm_audio_out_pcm16,
    chat_mm_image_out_google_generate_content,
    chat_mm_image_out_openai_compat,
    chat_video_in_content_parts,
    chat_video_in_google_media,
    local_media_image_out_sdcpp,
)


@dataclass(frozen=True)
class ModelDriver:
    """Exact Model-specific binding to a reusable adapter."""

    catalog_key: str
    direction: str
    modality: str
    adapter_id: str
    config: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class DriverAdapter:
    """Executable adapter metadata shared by compatible Model bindings."""

    adapter_id: str
    direction: str
    modality: str
    entrypoint: Callable[..., Any]


@dataclass(frozen=True)
class ResolvedModelDriver:
    """Catalog Model, derived Provider, exact binding, and executable adapter."""

    model: dict[str, Any]
    provider: dict[str, Any]
    binding: ModelDriver
    adapter: DriverAdapter


def _normalize_direction(direction: str) -> str:
    value = (direction or "").strip().lower()
    if value in ("in", "input"):
        return "input"
    if value in ("out", "output"):
        return "output"
    return value


def _normalize_modality(modality: str) -> str:
    return (modality or "").strip().lower()


ADAPTERS: dict[str, DriverAdapter] = {
    chat_image_in_content_parts.ADAPTER_ID: DriverAdapter(
        adapter_id=chat_image_in_content_parts.ADAPTER_ID,
        direction=chat_image_in_content_parts.DIRECTION,
        modality=chat_image_in_content_parts.MODALITY,
        entrypoint=chat_image_in_content_parts.to_content_parts,
    ),
    chat_video_in_content_parts.ADAPTER_ID: DriverAdapter(
        adapter_id=chat_video_in_content_parts.ADAPTER_ID,
        direction=chat_video_in_content_parts.DIRECTION,
        modality=chat_video_in_content_parts.MODALITY,
        entrypoint=chat_video_in_content_parts.to_content_parts,
    ),
    chat_video_in_google_media.ADAPTER_ID: DriverAdapter(
        adapter_id=chat_video_in_google_media.ADAPTER_ID,
        direction=chat_video_in_google_media.DIRECTION,
        modality=chat_video_in_google_media.MODALITY,
        entrypoint=chat_video_in_google_media.to_content_parts,
    ),
    chat_mm_image_out_openai_compat.ADAPTER_ID: DriverAdapter(
        adapter_id=chat_mm_image_out_openai_compat.ADAPTER_ID,
        direction=chat_mm_image_out_openai_compat.DIRECTION,
        modality=chat_mm_image_out_openai_compat.MODALITY,
        entrypoint=chat_mm_image_out_openai_compat.generate,
    ),
    chat_mm_audio_out_pcm16.ADAPTER_ID: DriverAdapter(
        adapter_id=chat_mm_audio_out_pcm16.ADAPTER_ID,
        direction=chat_mm_audio_out_pcm16.DIRECTION,
        modality=chat_mm_audio_out_pcm16.MODALITY,
        entrypoint=chat_mm_audio_out_pcm16.generate,
    ),
    chat_mm_image_out_google_generate_content.ADAPTER_ID: DriverAdapter(
        adapter_id=chat_mm_image_out_google_generate_content.ADAPTER_ID,
        direction=chat_mm_image_out_google_generate_content.DIRECTION,
        modality=chat_mm_image_out_google_generate_content.MODALITY,
        entrypoint=chat_mm_image_out_google_generate_content.generate,
    ),
    local_media_image_out_sdcpp.ADAPTER_ID: DriverAdapter(
        adapter_id=local_media_image_out_sdcpp.ADAPTER_ID,
        direction=local_media_image_out_sdcpp.DIRECTION,
        modality=local_media_image_out_sdcpp.MODALITY,
        entrypoint=local_media_image_out_sdcpp.generate,
    ),
}


def _binding(
    catalog_key: str,
    *,
    direction: str = "input",
    modality: str = "image",
    adapter_id: str = chat_image_in_content_parts.ADAPTER_ID,
) -> ModelDriver:
    return ModelDriver(
        catalog_key=catalog_key,
        direction=direction,
        modality=modality,
        adapter_id=adapter_id,
    )


_MODEL_DRIVER_ROWS: tuple[ModelDriver, ...] = (
    # DR-CM-04 — direct OpenAI image input.
    _binding("gpt-5.5-2026-04-23"),
    _binding("gpt-5.4-2026-03-05"),
    _binding("gpt-5.4-mini-2026-03-17"),
    # DR-CM-06 — native Google image input.
    _binding("gemini-2.5-pro"),
    _binding("gemini-2.5-flash"),
    _binding("gemini-3-flash-preview"),
    _binding("gemini-3.1-pro-preview"),
    _binding("gemini-3.1-flash-lite"),
    # DR-CM-13 — shipped Gemini 3.7 Flash image + native video.
    _binding("gemini-3.7-flash"),
    _binding(
        "gemini-3.7-flash",
        modality="video",
        adapter_id=chat_video_in_google_media.ADAPTER_ID,
    ),
    # DR-CM-08 — direct xAI image input (Grok 4.5 only).
    _binding("grok-4.5"),
    # DR-CM-07 — promoted OpenRouter image input used by active agents.
    _binding("openai/gpt-5.6-luna"),
    # DR-CM-11 — OpenRouter Grok 4.5 image input.
    _binding("x-ai/grok-4.5"),
    # DR-CM-12 — OpenRouter GLM 5.3 Flash image + video input.
    _binding("z-ai/glm-5.3-flash"),
    _binding(
        "z-ai/glm-5.3-flash",
        modality="video",
        adapter_id=chat_video_in_content_parts.ADAPTER_ID,
    ),
    # DR-CM-13 — OpenRouter Gemini 3.7 Flash image + video_url.
    _binding("google/gemini-3.7-flash"),
    _binding(
        "google/gemini-3.7-flash",
        modality="video",
        adapter_id=chat_video_in_content_parts.ADAPTER_ID,
    ),
    # DR-LOC-01 — local SYCL chat vision (llama-server content parts).
    _binding("qwen3.6:35b"),
    _binding("qwen3.8:27b"),
    # DR-CM-01 — OpenRouter image output.
    _binding(
        "google/gemini-3.1-flash-image",
        direction="output",
        modality="image",
        adapter_id=chat_mm_image_out_openai_compat.ADAPTER_ID,
    ),
    # DR-CM-02 — OpenRouter audio output.
    _binding(
        "openai/gpt-audio",
        direction="output",
        modality="audio",
        adapter_id=chat_mm_audio_out_pcm16.ADAPTER_ID,
    ),
    # DR-CM-03 — direct OpenAI audio output.
    _binding(
        "gpt-audio-1.5",
        direction="output",
        modality="audio",
        adapter_id=chat_mm_audio_out_pcm16.ADAPTER_ID,
    ),
    # DR-CM-05 — native Google image output.
    _binding(
        "gemini-3.1-flash-image",
        direction="output",
        modality="image",
        adapter_id=chat_mm_image_out_google_generate_content.ADAPTER_ID,
    ),
    # DR-CM-09 — promoted OpenRouter image output used by active Utility profiles.
    _binding(
        "openai/gpt-5.4-image-2",
        direction="output",
        modality="image",
        adapter_id=chat_mm_image_out_openai_compat.ADAPTER_ID,
    ),
    # DR-CM-10 — promoted OpenRouter audio output used by active Utility profiles.
    _binding(
        "openai/gpt-audio-mini",
        direction="output",
        modality="audio",
        adapter_id=chat_mm_audio_out_pcm16.ADAPTER_ID,
    ),
    # DR-LOC-02 / MD-4b — local Utility image output (sd-cli).
    _binding(
        "qwen-image-2512",
        direction="output",
        modality="image",
        adapter_id=local_media_image_out_sdcpp.ADAPTER_ID,
    ),
    _binding(
        "flux1-dev",
        direction="output",
        modality="image",
        adapter_id=local_media_image_out_sdcpp.ADAPTER_ID,
    ),
)


def _index_bindings(
    rows: Iterable[ModelDriver],
) -> dict[tuple[str, str, str], ModelDriver]:
    indexed: dict[tuple[str, str, str], ModelDriver] = {}
    for binding in rows:
        key = (binding.catalog_key, binding.direction, binding.modality)
        if key in indexed:
            raise ValueError(f"Duplicate ModelDriver binding: {key!r}")
        indexed[key] = binding
    return indexed


MODEL_DRIVERS: dict[tuple[str, str, str], ModelDriver] = _index_bindings(
    _MODEL_DRIVER_ROWS
)


def list_adapters() -> list[DriverAdapter]:
    """Return shipped executable adapters in stable ID order."""

    return [ADAPTERS[key] for key in sorted(ADAPTERS)]


def list_model_drivers() -> list[ModelDriver]:
    """Return exact bindings in stable key order."""

    return [_copy_binding(MODEL_DRIVERS[key]) for key in sorted(MODEL_DRIVERS)]


def _copy_binding(binding: ModelDriver) -> ModelDriver:
    """Return a caller-owned binding so config cannot mutate global state."""

    return ModelDriver(
        catalog_key=binding.catalog_key,
        direction=binding.direction,
        modality=binding.modality,
        adapter_id=binding.adapter_id,
        config=deepcopy(binding.config),
    )


def _model_declares_modality(
    model: dict[str, Any],
    direction: str,
    modality: str,
) -> bool:
    if direction == "input":
        return modality in parse_input_modalities(model)
    return model_output_includes(model, modality)


def resolve_model_driver(
    catalog_key: str,
    direction: str,
    modality: str,
    *,
    catalog: dict[str, dict[str, Any]] | None = None,
    providers: dict[str, dict[str, Any]] | None = None,
) -> ResolvedModelDriver | None:
    """Resolve one exact executable ModelDriver binding.

    ``None`` is returned for text, unknown Models, missing Providers, absent
    bindings, and bindings whose adapter is not executable or contract-compatible.
    Provider is never supplied by the caller; it comes from the catalog Model.
    """

    key = (catalog_key or "").strip()
    normalized_direction = _normalize_direction(direction)
    normalized_modality = _normalize_modality(modality)
    if (
        not key
        or normalized_modality in ("", "text")
        or normalized_direction not in ("input", "output")
    ):
        return None

    resolved_catalog = load_catalog() if catalog is None else catalog
    model = resolved_catalog.get(key)
    if not model:
        return None
    if not _model_declares_modality(
        model,
        normalized_direction,
        normalized_modality,
    ):
        return None

    provider_slug = str(model.get("provider", "") or "").strip()
    if not provider_slug:
        return None
    resolved_providers = load_providers() if providers is None else providers
    provider = resolved_providers.get(provider_slug)
    if not provider:
        return None

    binding = MODEL_DRIVERS.get((key, normalized_direction, normalized_modality))
    if binding is None:
        return None
    adapter = ADAPTERS.get(binding.adapter_id)
    if adapter is None or not callable(adapter.entrypoint):
        return None
    if (
        adapter.direction != binding.direction
        or adapter.modality != binding.modality
    ):
        return None

    return ResolvedModelDriver(
        model=dict(model),
        provider=dict(provider),
        binding=_copy_binding(binding),
        adapter=adapter,
    )


def model_driver_coverage(
    catalog_key: str,
    *,
    catalog: dict[str, dict[str, Any]] | None = None,
    providers: dict[str, dict[str, Any]] | None = None,
) -> dict[str, set[str]]:
    """Return exact executable non-text support for one catalog Model."""

    resolved_catalog = load_catalog() if catalog is None else catalog
    resolved_providers = load_providers() if providers is None else providers
    coverage: dict[str, set[str]] = {"input": set(), "output": set()}
    for direction in ("input", "output"):
        for modality in ("image", "audio", "video"):
            if resolve_model_driver(
                catalog_key,
                direction,
                modality,
                catalog=resolved_catalog,
                providers=resolved_providers,
            ):
                coverage[direction].add(modality)
    return coverage


def catalog_driver_enrichment(
    catalog_key: str,
    model: dict[str, Any],
    *,
    catalog: dict[str, dict[str, Any]] | None = None,
    providers: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Return JSON-safe ◆/◇ support metadata for one catalog Model."""

    coverage = model_driver_coverage(
        catalog_key,
        catalog=catalog,
        providers=providers,
    )
    declared = {
        "input": {
            modality
            for modality in parse_input_modalities(model)
            if modality != "text"
        },
        "output": {
            modality
            for modality in ("image", "audio", "video")
            if model_output_includes(model, modality)
        },
    }
    badges: dict[str, dict[str, str]] = {"input": {}, "output": {}}
    summary: list[str] = []
    for direction in ("input", "output"):
        for modality in sorted(declared[direction]):
            badge = "◆" if modality in coverage[direction] else "◇"
            badges[direction][modality] = badge
            summary.append(f"{direction}:{modality}{badge}")
    return {
        "driver_coverage": {
            "input": sorted(coverage["input"]),
            "output": sorted(coverage["output"]),
        },
        "driver_badges": badges,
        "driver_summary": " ".join(summary) or "text-native",
    }


def advise_driver_gaps(
    catalog_key: str,
    input_modalities: Iterable[str] | None = None,
    output_modalities: Iterable[str] | None = None,
    *,
    catalog: dict[str, dict[str, Any]] | None = None,
    providers: dict[str, dict[str, Any]] | None = None,
) -> list[str]:
    """Return soft COA hints for declared capability without exact support."""

    coverage = model_driver_coverage(
        catalog_key,
        catalog=catalog,
        providers=providers,
    )
    hints: list[str] = []
    guide = "Versa AGi - Model Driver Build Guide.md"

    def _check(direction: str, modalities: Iterable[str] | None) -> None:
        for raw in modalities or ():
            modality = _normalize_modality(raw)
            if not modality or modality == "text":
                continue
            if modality in coverage[direction]:
                continue
            hints.append(
                f"No exact executable ModelDriver for {catalog_key} "
                f"{direction} {modality} (◇). COA: evaluate the wire shape and "
                f"prepare a source adapter/binding contribution per {guide}."
            )

    _check("input", input_modalities)
    _check("output", output_modalities)
    return hints
