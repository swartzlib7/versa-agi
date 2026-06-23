"""Provider-agnostic model catalog sources.

Fetch and normalize each provider's *Models* API into a single common shape so
the agitop "Import from <Provider>" modal and the ``agictl model source`` CLI can
treat every configured provider uniformly — the same pattern OpenRouter already
uses (:mod:`openrouter_catalog`), generalized to the direct-API providers.

Supported slugs: ``google``, ``xai``, ``openai``, ``anthropic``, ``openrouter``.

Metadata richness differs per provider (verified against current docs, 2026-06):

  ============  ==========================  =========  =========  =======
  Provider      Endpoint                    Modality   Context    Pricing
  ============  ==========================  =========  =========  =======
  openrouter    /api/v1/models              in+out     yes        yes
  xai           /v1/language-models +        in+out     ~          yes
                /v1/image-generation-models
  anthropic     /v1/models (capabilities)   image_in   yes        no
  google        /v1beta/models              infer      yes        no
  openai        /v1/models                  infer      no         no
  ============  ==========================  =========  =========  =======

For providers whose native listing is sparse (OpenAI: id-only; Google: no
modality flag), modalities are *inferred* from the model family and may be
cross-filled / priced via OpenRouter at add/refresh time. xAI's image
generators are listed separately and merged in. Every source emits the same
summary dict shape as :func:`openrouter_catalog.or_model_summary`.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable

from model_catalog import (
    catalog_row_to_value,
    load_providers,
    parse_catalog_row,
    read_setup_value,
)
from openrouter_catalog import (
    PROVIDER_KEYS_ENV,
    infer_work_modality,
    list_addable_models as _or_list_addable,
    normalize_input_modalities,
    normalize_output_modalities,
    openrouter_configured,
    or_model_summary,
)

# Slugs handled natively here (openrouter delegates to openrouter_catalog).
DIRECT_PROVIDERS = ("google", "xai", "openai", "anthropic")
ALL_PROVIDERS = DIRECT_PROVIDERS + ("openrouter",)

PROVIDER_LABEL = {
    "google": "Google",
    "xai": "xAI",
    "openai": "OpenAI",
    "anthropic": "Anthropic",
    "openrouter": "OpenRouter",
}

# Env var holding each provider's API key (provider_keys.env / process env).
_KEY_ENV_VAR = {
    "google": "GEMINI_API_KEY",
    "xai": "XAI_API_KEY",
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
}

# setup.ini location of each provider's key (section, option).
_KEY_SETUP_INI = {
    "google": ("gemini", "api_key"),
    "xai": ("third_party", "xai_api_key"),
    "openai": ("third_party", "openai_api_key"),
    "anthropic": ("third_party", "anthropic_api_key"),
    "openrouter": ("third_party", "openrouter_api_key"),
}

_COA_ENV = "/etc/versa-agi/coa.env"

_ZERO_PRICING = {
    "prompt_per_m": 0.0,
    "completion_per_m": 0.0,
    "image_per_m": 0.0,
    "reasoning_per_m": 0.0,
    "cache_read_per_m": 0.0,
}


# ── Key resolution ──────────────────────────────────────────────────────────
def resolve_provider_api_key(slug: str) -> str:
    """Resolve a provider API key from env → provider_keys.env → coa.env → setup.ini."""
    env_var = _KEY_ENV_VAR.get(slug)
    if not env_var:
        return ""
    key = (os.environ.get(env_var) or "").strip()
    if key:
        return key
    search = [
        PROVIDER_KEYS_ENV,
        os.path.join(os.path.dirname(__file__), "config", "provider_keys.env"),
    ]
    if slug == "google":
        search.insert(0, _COA_ENV)
    for path in search:
        if not os.path.isfile(path):
            continue
        try:
            with open(path) as f:
                for line in f:
                    if line.startswith(f"{env_var}="):
                        return line.split("=", 1)[1].strip().strip('"').strip("'")
        except OSError:
            continue
    section, option = _KEY_SETUP_INI.get(slug, ("", ""))
    return read_setup_value(section, option, "").strip() if section else ""


def provider_configured(slug: str, providers: dict | None = None) -> tuple[bool, str]:
    """True when the provider is registered+enabled and has a usable API key."""
    if slug == "openrouter":
        return openrouter_configured()
    if slug not in ALL_PROVIDERS:
        return False, f"Unsupported provider '{slug}'"
    providers = providers if providers is not None else load_providers()
    prov = providers.get(slug)
    if not prov:
        return False, f"Provider '{slug}' is not in the registry"
    if not prov.get("enabled"):
        return False, f"Provider '{slug}' is disabled"
    if not resolve_provider_api_key(slug):
        return False, f"{PROVIDER_LABEL.get(slug, slug)} API key not set"
    return True, ""


def configured_providers() -> list[str]:
    """Slugs that are registered, enabled, and keyed (UI button gating)."""
    providers = load_providers()
    return [s for s in ALL_PROVIDERS if provider_configured(s, providers)[0]]


# ── HTTP ────────────────────────────────────────────────────────────────────
def _http_get_json(url: str, headers: dict[str, str], timeout: int = 45) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "Versa-AGi/catalog", **headers})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.load(resp)


def _mk_summary(
    slug: str,
    model_id: str,
    label: str,
    context_length: int | None,
    input_modalities: str,
    output_modalities: str,
    pricing: dict[str, float],
    *,
    chat_capable: bool = True,
    input_context_limit: int | None = None,
    output_context_limit: int | None = None,
) -> dict[str, Any]:
    name = label or model_id
    suffix = f" ({PROVIDER_LABEL.get(slug, slug)})"
    disp = name if name.endswith(suffix) else f"{name}{suffix}"
    in_ctx = input_context_limit if input_context_limit is not None else context_length
    return {
        "id": model_id,
        "name": name,
        "context_length": in_ctx,
        "input_context_limit": in_ctx,
        "output_context_limit": output_context_limit,
        "input_modalities": input_modalities or "text",
        "output_modalities": output_modalities or "text",
        "work_modality": infer_work_modality({"id": model_id, "name": name}),
        "label": disp,
        "chat_capable": chat_capable,
        "pricing": pricing or dict(_ZERO_PRICING),
    }


# ── xAI — /v1/language-models (modalities + pricing) ────────────────────────
_XAI_URL = "https://api.x.ai/v1/language-models"
_XAI_IMAGE_URL = "https://api.x.ai/v1/image-generation-models"


def _fetch_xai(key: str) -> dict[str, dict]:
    headers = {"Authorization": f"Bearer {key}"}
    out: dict[str, dict] = {m["id"]: m for m in
                            _http_get_json(_XAI_URL, headers).get("models", [])
                            if m.get("id")}
    # Image-generation models live on a separate listing (not in
    # /v1/language-models) — merge them so the catalog isn't language-only.
    # Degrade gracefully if the endpoint is unavailable.
    try:
        img = _http_get_json(_XAI_IMAGE_URL, headers)
    except (urllib.error.URLError, OSError, ValueError):
        img = {}
    for m in img.get("models", []):
        mid = m.get("id")
        if not mid or mid in out:
            continue
        # These listings may omit modality fields; default to text→image.
        m.setdefault("input_modalities", ["text"])
        m.setdefault("output_modalities", ["image"])
        out[mid] = m
    return out


def _xai_pricing(m: dict) -> dict[str, float]:
    # REST docs: integer price is "USD cents per 100 million tokens" → $/M = v / 10000.
    def perm(v) -> float:
        try:
            return round(float(v or 0) / 10000.0, 6)
        except (TypeError, ValueError):
            return 0.0

    return {
        "prompt_per_m": perm(m.get("prompt_text_token_price")),
        "completion_per_m": perm(m.get("completion_text_token_price")),
        "image_per_m": perm(m.get("prompt_image_token_price")),
        "reasoning_per_m": 0.0,
        "cache_read_per_m": perm(m.get("cached_prompt_text_token_price")),
    }


def _summary_xai(m: dict) -> dict:
    return _mk_summary(
        "xai", m["id"], m.get("id", ""), None,
        normalize_input_modalities(m.get("input_modalities")),
        normalize_output_modalities(m.get("output_modalities")),
        _xai_pricing(m),
        chat_capable=_chat_xai(m),
    )


def _chat_xai(m: dict) -> bool:
    return "text" in {str(x).lower() for x in (m.get("output_modalities") or [])}


# ── Anthropic — /v1/models (capabilities: image_input, max_input_tokens) ─────
_ANTHROPIC_URL = "https://api.anthropic.com/v1/models?limit=1000"
_ANTHROPIC_VERSION = "2023-06-01"
_CLAUDE_VISION_PREFIXES = (
    "claude-3", "claude-4", "claude-opus-4", "claude-sonnet-4", "claude-haiku-4",
)


def _fetch_anthropic(key: str) -> dict[str, dict]:
    headers = {"x-api-key": key, "anthropic-version": _ANTHROPIC_VERSION}
    out: dict[str, dict] = {}
    url = _ANTHROPIC_URL
    for _ in range(10):  # paginate defensively
        data = _http_get_json(url, headers)
        for m in data.get("data", []):
            if m.get("id"):
                out[m["id"]] = m
        if not data.get("has_more") or not data.get("last_id"):
            break
        url = f"{_ANTHROPIC_URL}&after_id={data['last_id']}"
    return out


def _summary_anthropic(m: dict) -> dict:
    caps = m.get("capabilities") or {}
    mods = ["text"]
    if caps:
        if (caps.get("image_input") or {}).get("supported"):
            mods.append("image")
    else:  # legacy listing without capabilities → infer from Claude family
        if (m.get("id") or "").lower().startswith(_CLAUDE_VISION_PREFIXES):
            mods.append("image")
    in_ctx = m.get("max_input_tokens")
    out_ctx = m.get("max_tokens")
    in_n = int(in_ctx) if isinstance(in_ctx, (int, float)) and in_ctx > 0 else None
    out_n = int(out_ctx) if isinstance(out_ctx, (int, float)) and out_ctx > 0 else None
    return _mk_summary(
        "anthropic", m["id"], m.get("display_name") or m.get("id", ""),
        in_n, ",".join(mods), "text", dict(_ZERO_PRICING),
        input_context_limit=in_n, output_context_limit=out_n,
    )


def _chat_anthropic(m: dict) -> bool:
    return True


# ── Google — /v1beta/models (inputTokenLimit, supportedGenerationMethods) ────
_GOOGLE_URL = "https://generativelanguage.googleapis.com/v1beta/models?pageSize=1000"


def _fetch_google(key: str) -> dict[str, dict]:
    out: dict[str, dict] = {}
    page = ""
    for _ in range(10):
        url = f"{_GOOGLE_URL}&key={key}" + (f"&pageToken={page}" if page else "")
        data = _http_get_json(url, {})
        for m in data.get("models", []):
            name = (m.get("name") or "").split("/")[-1]
            if name:
                out[name] = m
        page = data.get("nextPageToken") or ""
        if not page:
            break
    return out


def _google_input_modalities(name: str) -> str:
    """Infer input modalities from the model family (list API has no flag)."""
    n = name.lower()
    if n.startswith("imagen") or n.startswith("veo"):
        return "text"  # text-to-image / text-to-video generators take text only
    if n.startswith("gemini"):
        # Gemini families are natively multimodal on input (text+image+audio+video).
        return "text,image,audio,video"
    return "text"


def _google_output_modalities(name: str) -> str:
    """Infer output modalities — the list API reports none, so derive from family.

    Gemini image models (``…-image…``) return text+image; Imagen returns image
    only; native TTS models return audio; Veo returns video; everything else is
    text. Cross-filled / corrected via OpenRouter at add/refresh time.
    """
    n = name.lower()
    if n.startswith("imagen"):
        return "image"
    if "image" in n:  # e.g. gemini-2.5-flash-image (returns prose + image)
        return "text,image"
    if n.startswith("veo"):
        return "video"
    if "-tts" in n or n.endswith("tts") or "native-audio" in n:
        return "audio"
    return "text"


def _summary_google(m: dict) -> dict:
    name = (m.get("name") or "").split("/")[-1]
    in_ctx = m.get("inputTokenLimit")
    out_ctx = m.get("outputTokenLimit")
    in_n = int(in_ctx) if isinstance(in_ctx, (int, float)) and in_ctx > 0 else None
    out_n = int(out_ctx) if isinstance(out_ctx, (int, float)) and out_ctx > 0 else None
    # The list API exposes no modality flag; infer both directions from family.
    in_mods = _google_input_modalities(name)
    out_mods = _google_output_modalities(name)
    return _mk_summary(
        "google", name, m.get("displayName") or name,
        in_n, in_mods, out_mods, dict(_ZERO_PRICING),
        chat_capable=_chat_google(m),
        input_context_limit=in_n, output_context_limit=out_n,
    )


def _chat_google(m: dict) -> bool:
    return "generateContent" in set(m.get("supportedGenerationMethods") or [])


# ── OpenAI — /v1/models (id-only; modality/context inferred) ─────────────────
_OPENAI_URL = "https://api.openai.com/v1/models"
_OPENAI_NONCHAT = (
    "embedding", "whisper", "tts", "dall-e", "davinci", "babbage", "moderation",
    "audio", "realtime", "image", "transcribe", "search", "computer-use", "codex",
)
_OPENAI_VISION_PREFIXES = ("gpt-4o", "gpt-4.1", "gpt-5", "o3", "o4", "chatgpt-4o")


def _fetch_openai(key: str) -> dict[str, dict]:
    data = _http_get_json(_OPENAI_URL, {"Authorization": f"Bearer {key}"})
    return {m["id"]: m for m in data.get("data", []) if m.get("id")}


def _chat_openai(m: dict) -> bool:
    mid = (m.get("id") or "").lower()
    if any(tok in mid for tok in _OPENAI_NONCHAT):
        return False
    return mid.startswith(("gpt-", "o1", "o3", "o4", "chatgpt"))


def _summary_openai(m: dict) -> dict:
    mid = m["id"]
    has_vision = mid.lower().startswith(_OPENAI_VISION_PREFIXES) and "audio" not in mid.lower()
    return _mk_summary(
        "openai", mid, mid, None,
        "text,image" if has_vision else "text", "text", dict(_ZERO_PRICING),
        chat_capable=_chat_openai(m),
    )


# ── Source registry ─────────────────────────────────────────────────────────
@dataclass(frozen=True)
class Source:
    slug: str
    fetch: Callable[[str], dict[str, dict]]
    summary: Callable[[dict], dict]
    chat: Callable[[dict], bool]


_SOURCES: dict[str, Source] = {
    "xai": Source("xai", _fetch_xai, _summary_xai, _chat_xai),
    "anthropic": Source("anthropic", _fetch_anthropic, _summary_anthropic, _chat_anthropic),
    "google": Source("google", _fetch_google, _summary_google, _chat_google),
    "openai": Source("openai", _fetch_openai, _summary_openai, _chat_openai),
}


def supported_providers() -> tuple[str, ...]:
    return ALL_PROVIDERS


def fetch_index(slug: str) -> dict[str, dict]:
    """Return ``{model_id: raw provider dict}`` for a provider."""
    if slug == "openrouter":
        from openrouter_catalog import fetch_openrouter_index_with_fallback
        return fetch_openrouter_index_with_fallback()
    src = _SOURCES.get(slug)
    if not src:
        raise ValueError(f"Unsupported provider '{slug}'")
    key = resolve_provider_api_key(slug)
    if not key:
        raise RuntimeError(f"{PROVIDER_LABEL.get(slug, slug)} API key not set")
    return src.fetch(key)


def model_summary(slug: str, raw: dict) -> dict:
    if slug == "openrouter":
        return or_model_summary(raw)
    src = _SOURCES.get(slug)
    if not src:
        raise ValueError(f"Unsupported provider '{slug}'")
    return src.summary(raw)


def is_chat_capable(slug: str, raw: dict) -> bool:
    if slug == "openrouter":
        from openrouter_catalog import is_chat_capable as _or_chat
        return _or_chat(raw)
    src = _SOURCES.get(slug)
    return bool(src and src.chat(raw))


def list_addable_models(
    slug: str,
    catalog_keys: set[str] | list[str],
    index: dict[str, dict] | None = None,
) -> list[dict]:
    """Chat-capable provider models not already present in the merged catalog."""
    if slug == "openrouter":
        return _or_list_addable(catalog_keys, index)
    keys = set(catalog_keys)
    idx = index if index is not None else fetch_index(slug)
    rows = []
    for mid, raw in sorted(idx.items()):
        if mid in keys or not is_chat_capable(slug, raw):
            continue
        rows.append(model_summary(slug, raw))
    return rows


# ── models.ini row refresh (modalities + context from native source) ─────────
def patch_models_ini_provider_rows(
    path: str, slug: str, keys: list[str] | None = None,
) -> list[str]:
    """Refresh input/output modalities + ctx_max on a provider's [catalog] rows.

    Mirrors :func:`openrouter_catalog.patch_models_ini_openrouter_rows` but uses
    the provider's own Models API as the authority. Only rows whose ``provider``
    matches ``slug`` are touched; labels are preserved. Returns updated keys.
    """
    if not os.path.isfile(path):
        return []
    index = fetch_index(slug)
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
        parsed = parse_catalog_row(s[eq + 1:].strip())
        if not parsed or parsed.get("provider") != slug:
            out.append(line)
            continue
        raw = index.get(key)
        if not raw:
            out.append(line)
            continue
        summary = model_summary(slug, raw)
        parsed["input_modalities"] = summary["input_modalities"]
        parsed["output_modalities"] = summary["output_modalities"]
        if summary.get("context_length"):
            parsed["ctx_max"] = int(summary["context_length"])
        new_val = catalog_row_to_value(parsed)
        pad = " " * max(1, 29 - len(key))
        out.append(f"{key}{pad}= {new_val}\n")
        updated.append(key)

    if updated:
        with open(path, "w") as f:
            f.writelines(out)
    return updated
