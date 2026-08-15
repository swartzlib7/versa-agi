"""OpenRouter model catalog API — fetch, normalize, and enrich Versa catalog rows."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any

import provider_model_cache
from model_catalog import (
    SETUP_INI_CANONICAL,
    catalog_row_to_value,
    parse_catalog_row,
    read_setup_value,
)

OPENROUTER_API_URL = "https://openrouter.ai/api/v1/models"

_INPUT_OR_TO_VERSA = {
    "text": "text",
    "image": "image",
    "audio": "audio",
    "video": "video",
    # OpenRouter "file" is not in our IO_MODALITIES today (Phase E documents).
}

_OUTPUT_OR_TO_VERSA = {
    "text": "text",
    "image": "image",
    "audio": "audio",
    "video": "video",
}

PROVIDER_KEYS_ENV = "/etc/versa-agi/provider_keys.env"

# OpenRouter vendor slug prefixes for direct-API catalog keys (not provider=openrouter).
_OR_VENDOR_PREFIX = {
    "google": "google",
    "openai": "openai",
    "anthropic": "anthropic",
    "xai": "x-ai",
}


def normalize_input_modalities(raw: list[str] | None) -> str:
    seen: list[str] = []
    for m in raw or []:
        v = _INPUT_OR_TO_VERSA.get((m or "").lower())
        if v and v not in seen:
            seen.append(v)
    return ",".join(seen) if seen else "text"


def normalize_output_modalities(raw: list[str] | None) -> str:
    seen: list[str] = []
    for m in raw or []:
        v = _OUTPUT_OR_TO_VERSA.get((m or "").lower())
        if v and v not in seen:
            seen.append(v)
    return ",".join(seen) if seen else "text"


def is_chat_capable(or_model: dict) -> bool:
    arch = or_model.get("architecture") or {}
    outs = {x.lower() for x in (arch.get("output_modalities") or [])}
    return "text" in outs


def resolve_openrouter_api_key() -> str:
    key = (os.environ.get("OPENROUTER_API_KEY") or "").strip()
    if key:
        return key
    for path in (
        PROVIDER_KEYS_ENV,
        os.path.join(os.path.dirname(__file__), "config", "provider_keys.env"),
    ):
        if not os.path.isfile(path):
            continue
        try:
            with open(path) as f:
                for line in f:
                    if line.startswith("OPENROUTER_API_KEY="):
                        return line.split("=", 1)[1].strip().strip('"').strip("'")
        except OSError:
            continue
    return read_setup_value("third_party", "openrouter_api_key", "").strip()


def openrouter_configured() -> tuple[bool, str]:
    """True when an OpenRouter API key is available (Import / Models API gating).

    Runtime routing still needs the provider enabled in models.ini / setup.ini;
    Import only needs the key (same rule as other ``model source`` providers).
    """
    if not resolve_openrouter_api_key():
        return False, "OpenRouter API key not set (sudo agictl system set-key openrouter …)"
    return True, ""


def fetch_openrouter_index(api_key: str = "") -> dict[str, dict]:
    """Return {model_id: raw OpenRouter model dict}. Works without a key for public listing."""
    url = f"{OPENROUTER_API_URL}?output_modalities=all"
    headers = {"User-Agent": "Versa-AGi/catalog"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = json.load(resp)
    return {m["id"]: m for m in data.get("data", []) if m.get("id")}


def fetch_openrouter_index_with_fallback(use_cache: bool = False) -> dict[str, dict]:
    """Try unauthenticated listing first; retry with stored API key on failure.

    When ``use_cache`` is set, a fresh on-disk cache entry (within the TTL) is
    returned without any network call, and live results are written back to the
    cache for subsequent reads.
    """
    if use_cache:
        cached = provider_model_cache.load("openrouter")
        if cached is not None:
            return cached
    try:
        index = fetch_openrouter_index("")
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, json.JSONDecodeError):
        key = resolve_openrouter_api_key()
        if not key:
            raise
        index = fetch_openrouter_index(key)
    if use_cache:
        provider_model_cache.store("openrouter", index)
    return index


def enrich_catalog_dict(row: dict, or_model: dict, *, preserve_label: bool = True) -> dict:
    """Merge OpenRouter architecture/context into a catalog row dict."""
    out = dict(row)
    arch = or_model.get("architecture") or {}
    out["input_modalities"] = normalize_input_modalities(arch.get("input_modalities"))
    out["output_modalities"] = normalize_output_modalities(arch.get("output_modalities"))
    ctx = or_model.get("context_length")
    if isinstance(ctx, (int, float)) and ctx > 0:
        out["ctx_max"] = int(ctx)
    if not preserve_label or not (out.get("label") or "").strip():
        out["label"] = or_display_label(or_model)
    return out


def or_display_label(or_model: dict) -> str:
    """Product name only. Pickers prepend the catalog Provider."""
    name = (or_model.get("name") or or_model.get("id") or "").strip()
    if name.endswith(" (via OpenRouter)"):
        name = name[: -len(" (via OpenRouter)")].strip()
    return name or or_model.get("id", "")


def infer_work_modality(or_model: dict) -> str:
    mid = (or_model.get("id") or "").lower()
    name = (or_model.get("name") or "").lower()
    if "code" in mid or "code" in name:
        return "code"
    if any(x in mid or x in name for x in ("flash", "lite", "mini", "fast")):
        return "fast"
    if any(x in mid or x in name for x in ("pro", "opus", "reasoning")):
        return "reasoning"
    return "balanced"


def or_model_summary(or_model: dict) -> dict[str, Any]:
    arch = or_model.get("architecture") or {}
    pricing = extract_pricing(or_model)
    ctx = or_model.get("context_length")
    in_n = int(ctx) if isinstance(ctx, (int, float)) and ctx > 0 else None
    return {
        "id": or_model.get("id"),
        "name": or_model.get("name") or or_model.get("id"),
        "context_length": in_n,
        "input_context_limit": in_n,
        "output_context_limit": None,
        "input_modalities": normalize_input_modalities(arch.get("input_modalities")),
        "output_modalities": normalize_output_modalities(arch.get("output_modalities")),
        "work_modality": infer_work_modality(or_model),
        "label": or_display_label(or_model),
        "chat_capable": is_chat_capable(or_model),
        "pricing": pricing,
    }


def _price_float(val) -> float:
    try:
        return float(val or 0)
    except (TypeError, ValueError):
        return 0.0


def extract_pricing(or_model: dict) -> dict[str, float]:
    """Normalize OpenRouter pricing to USD per million tokens.

    OpenRouter API returns USD per *token* (e.g. '0.00000009'); we store per-million
    in [catalog_pricing] for human-readable $/M display and cycle cost math.
    """
    raw = or_model.get("pricing") or {}
    scale = 1_000_000.0
    return {
        "prompt_per_m": _price_float(raw.get("prompt")) * scale,
        "completion_per_m": _price_float(raw.get("completion")) * scale,
        "image_per_m": _price_float(raw.get("image") or raw.get("image_token")) * scale,
        "reasoning_per_m": _price_float(raw.get("internal_reasoning")) * scale,
        "cache_read_per_m": _price_float(raw.get("input_cache_read")) * scale,
    }


def pricing_row_to_value(p: dict, *, fetched_at: str) -> str:
    def _f(key: str) -> float:
        return round(_price_float(p.get(key)), 6)

    return (
        f"{_f('prompt_per_m')}|{_f('completion_per_m')}|"
        f"{_f('image_per_m')}|{_f('reasoning_per_m')}|"
        f"{_f('cache_read_per_m')}|{fetched_at}"
    )


def _catalog_key_variants(catalog_key: str) -> list[str]:
    """Build OpenRouter lookup slug variants from a Versa catalog key."""
    import re

    key = (catalog_key or "").strip()
    if not key:
        return []
    variants = [key]
    no_date = re.sub(r"-\d{4}-\d{2}-\d{2}$", "", key)
    if no_date != key:
        variants.append(no_date)
    m = re.match(r"^(claude-(?:opus|sonnet|haiku))-(\d+)-(\d+)$", key)
    if m:
        variants.append(f"{m.group(1)}-{m.group(2)}.{m.group(3)}")
    out: list[str] = []
    for v in variants:
        if v not in out:
            out.append(v)
    return out


def resolve_openrouter_model(
    catalog_key: str,
    provider: str,
    index: dict[str, dict],
) -> dict | None:
    """Resolve a merged-catalog row to an OpenRouter model dict (best-effort)."""
    provider = (provider or "").strip().lower()
    if provider == "local" or not catalog_key:
        return None
    if catalog_key in index:
        return index[catalog_key]

    variants = _catalog_key_variants(catalog_key)
    prefixes: list[str] = []
    if provider == "openrouter" or "/" in catalog_key:
        prefixes.append("")
    elif provider in _OR_VENDOR_PREFIX:
        prefixes.append(_OR_VENDOR_PREFIX[provider])

    for prefix in prefixes:
        for variant in variants:
            or_id = f"{prefix}/{variant}" if prefix else variant
            if or_id in index:
                return index[or_id]

    for prefix in prefixes:
        head = f"{prefix}/" if prefix else ""
        for or_id, model in index.items():
            if prefix and not or_id.startswith(head):
                continue
            tail = or_id.split("/", 1)[-1]
            for variant in variants:
                if tail == variant:
                    return model
                if tail.replace(".", "-") == variant.replace(".", "-"):
                    return model

    # Family match for dated OpenAI-style keys (gpt-5.5-2026-… → openai/gpt-5.5).
    if provider in _OR_VENDOR_PREFIX:
        prefix = _OR_VENDOR_PREFIX[provider]
        base = variants[-1]
        exact = f"{prefix}/{base}"
        if exact in index:
            return index[exact]
        family_hits = [
            oid for oid in index
            if oid.startswith(f"{prefix}/") and (
                oid.split("/", 1)[1] == base
                or oid.split("/", 1)[1].startswith(base + "-")
            )
        ]
        if family_hits:
            family_hits.sort(key=lambda x: (len(x), x))
            return index[family_hits[0]]
    return None


def patch_models_ini_openrouter_pricing(path: str, keys: list[str] | None = None) -> list[str]:
    """Update [catalog_pricing] for catalog keys using OpenRouter list rates.

    Resolves every merged [catalog]/[catalog_custom] row (except local) to an
    OpenRouter model ID — not only provider=openrouter rows. Unmatched keys are
    left unchanged (no row removed).
    """
    from datetime import datetime, timezone

    if not os.path.isfile(path):
        return []
    index = fetch_openrouter_index_with_fallback()
    fetched_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    with open(path) as f:
        lines = f.readlines()

    cat = _read_catalog_keys_from_ini(lines)
    target_keys = sorted(cat.keys())
    if keys is not None:
        want = set(keys)
        target_keys = [k for k in target_keys if k in want]

    pricing_lines: dict[str, str] = {}
    for key in target_keys:
        provider = cat.get(key, "")
        or_model = resolve_openrouter_model(key, provider, index)
        if not or_model:
            continue
        pricing_lines[key] = pricing_row_to_value(extract_pricing(or_model), fetched_at=fetched_at)

    if not pricing_lines:
        return []

    def _pricing_section_stop(line: str) -> bool:
        s = line.strip()
        if s.startswith("[") and s.endswith("]"):
            return True
        return s.startswith("# ───") and "Model Parameters" in s

    out: list[str] = []
    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]
        stripped = line.strip()
        if stripped == "[catalog_pricing]":
            out.append(line)
            i += 1
            section_body: list[str] = []
            while i < n:
                if _pricing_section_stop(lines[i]):
                    break
                section_body.append(lines[i])
                i += 1
            existing_keys: set[str] = set()
            rebuilt: list[str] = []
            for sl in section_body:
                s = sl.strip()
                if not s or s.startswith("#"):
                    if s.startswith("# ───") and "Model Parameters" in s:
                        continue
                    rebuilt.append(sl)
                    continue
                eq = s.find("=")
                if eq <= 0:
                    rebuilt.append(sl)
                    continue
                key = s[:eq].strip()
                if key in pricing_lines:
                    existing_keys.add(key)
                    pad = " " * max(1, 29 - len(key))
                    rebuilt.append(f"{key}{pad}= {pricing_lines[key]}\n")
                else:
                    rebuilt.append(sl)
            for key, val in sorted(pricing_lines.items()):
                if key not in existing_keys:
                    pad = " " * max(1, 29 - len(key))
                    rebuilt.append(f"{key}{pad}= {val}\n")
            out.extend(rebuilt)
            continue
        out.append(line)
        i += 1

    if "[catalog_pricing]" not in "".join(out):
        out.append("\n[catalog_pricing]\n")
        for key, val in sorted(pricing_lines.items()):
            pad = " " * max(1, 29 - len(key))
            out.append(f"{key}{pad}= {val}\n")

    with open(path, "w") as f:
        f.writelines(out)
    return sorted(pricing_lines.keys())


def _read_catalog_keys_from_ini(lines: list[str]) -> dict[str, str]:
    """Return {catalog_key: provider_slug} from [catalog] and [catalog_custom]."""
    current = None
    out: dict[str, str] = {}
    for line in lines:
        s = line.strip()
        if s.startswith("[") and s.endswith("]"):
            current = s[1:-1]
            continue
        if current not in ("catalog", "catalog_custom") or not s or s.startswith("#"):
            continue
        eq = s.find("=")
        if eq <= 0:
            continue
        key = s[:eq].strip()
        raw = s[eq + 1:].strip()
        parsed = parse_catalog_row(raw)
        if parsed:
            out[key] = parsed.get("provider", "")
    return out


def list_addable_models(
    catalog_keys: set[str] | list[str],
    or_index: dict[str, dict] | None = None,
) -> list[dict]:
    """OpenRouter chat models not already in the merged catalog."""
    keys = set(catalog_keys)
    index = or_index if or_index is not None else fetch_openrouter_index_with_fallback()
    rows = []
    for mid, raw in sorted(index.items()):
        if mid in keys:
            continue
        if not is_chat_capable(raw):
            continue
        rows.append(or_model_summary(raw))
    return rows


def patch_models_ini_openrouter_rows(path: str, keys: list[str] | None = None) -> list[str]:
    """Update [catalog] OpenRouter rows in a models.ini file from the live API.

    Returns list of keys that were updated. Used to refresh the shipped template.
    """
    if not os.path.isfile(path):
        return []
    index = fetch_openrouter_index_with_fallback()
    with open(path) as f:
        lines = f.readlines()

    current = None
    updated: list[str] = []
    out: list[str] = []
    for line in lines:
        s = line.strip()
        if s.startswith("[") and s.endswith("]"):
            current = s[1:-1]
            out.append(line)
            continue
        if current != "catalog" or not s or s.startswith("#"):
            out.append(line)
            continue
        eq = s.find("=")
        if eq <= 0:
            out.append(line)
            continue
        key = s[:eq].strip()
        if keys is not None and key not in keys:
            out.append(line)
            continue
        raw_val = s[eq + 1:].strip()
        parsed = parse_catalog_row(raw_val)
        if not parsed or parsed.get("provider") != "openrouter":
            out.append(line)
            continue
        or_model = index.get(key)
        if not or_model:
            out.append(line)
            continue
        enriched = enrich_catalog_dict(parsed, or_model, preserve_label=True)
        new_val = catalog_row_to_value(enriched)
        pad = " " * max(1, 29 - len(key))
        out.append(f"{key}{pad}= {new_val}\n")
        updated.append(key)

    if updated:
        with open(path, "w") as f:
            f.writelines(out)
    return updated
