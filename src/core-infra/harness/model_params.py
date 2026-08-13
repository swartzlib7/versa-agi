# ─────────────────────────────────────────────────────
# Versa AGi — Abstracted Model Parameters
#
# Layered resolution (agent > model > system) of normalized
# generation params, translated per provider family for LangChain.
#
# Pipeline:
#
#   models.ini JSON  →  normalized params  →  to_native_kwargs()  →  LangChain / API
#        (data)              (stable)              (adapter code)
#
# - Data: [model_params], [model_params_custom], agents.db nullable overrides.
# - Stable: temperature, reasoning_effort, reasoning_max_tokens,
#   allowed_reasoning_efforts, think_mode, extra (passthrough).
# - Adapter: to_native_kwargs(family, model, params) — only place provider API
#   shapes are encoded. Config stays provider-agnostic.
# ─────────────────────────────────────────────────────

from __future__ import annotations

import configparser
import json
import os
from typing import Any

_MODELS_INI_PATHS = [
    "/etc/versa-agi/models.ini",
    os.path.join(os.path.dirname(os.path.dirname(
        os.path.dirname(os.path.abspath(__file__)))), "models.ini"),
]

REASONING_EFFORTS = ("none", "minimal", "low", "medium", "high", "max", "xhigh")

SYSTEM_DEFAULTS: dict[str, Any] = {
    "temperature": 0.2,
    "reasoning_effort": "none",
    "allowed_reasoning_efforts": ["none"],
}

_EFFORT_BUDGET_MAP = {
    "minimal": 1024,
    "low": 4096,
    "medium": 8192,
    "high": 16384,
    "max": 32768,
    "xhigh": 32768,
}

# ChatOllama constructor keys sourced from model ``extra`` (Ollama sampling options).
_OLLAMA_KWARG_KEYS = frozenset({
    "top_p", "top_k", "min_p", "repeat_penalty", "presence_penalty",
    "num_predict", "num_ctx", "seed", "stop", "tfs_z", "mirostat",
    "mirostat_eta", "mirostat_tau",
})

# think_mode in [model_params] model:<key> JSON (provider=ollama only — ignored on llamacpp):
#   boolean — Qwen/DeepSeek-style: reasoning_effort none→off, else on (ChatOllama reasoning=True)
#   levels  — GPT-OSS-style: low|medium|high passed through as reasoning=<level>
_OLLAMA_THINK_MODES = frozenset({"boolean", "levels"})

# Native kwargs applied only on the Ollama (ChatOllama) path.
_OLLAMA_RUNTIME_ONLY_KEYS = frozenset({"reasoning", "num_ctx"})

# Shared sampling keys: Ollama uses directly; llamacpp maps num_predict → max_tokens.
_SYCL_OPENAI_KEYS = frozenset({"temperature", "top_p", "max_tokens", "seed", "stop"})

_PATHS_ENV = "/etc/versa-agi/paths.env"


def _efforts_from_layer(layer: dict[str, Any] | None) -> tuple[str, ...] | None:
    if not layer:
        return None
    custom = layer.get("allowed_reasoning_efforts")
    if isinstance(custom, list) and custom:
        return tuple(str(x) for x in custom)
    return None


def read_gpu_backend() -> str:
    """Read VERSA_GPU_BACKEND from paths.env (standard | intel | remote)."""
    try:
        with open(_PATHS_ENV, encoding="utf-8") as f:
            for line in f:
                if line.startswith("VERSA_GPU_BACKEND="):
                    return line.split("=", 1)[1].strip().strip('"')
    except OSError:
        pass
    return "standard"


def resolve_local_runtime(gpu_backend: str | None = None) -> str:
    """Return ``ollama`` or ``llamacpp`` — active local provider."""
    from model_catalog import resolve_local_provider
    return resolve_local_provider(gpu_backend)


def _thinking_clipped_for_runtime(model_name: str, local_runtime: str | None = None) -> bool:
    """True when think_mode is declared but the active runtime cannot configure it."""
    if not _ollama_think_mode(model_name):
        return False
    return resolve_local_runtime(local_runtime) != "ollama"


def _effective_allowed_reasoning_efforts(
    model_name: str,
    local_runtime: str | None = None,
) -> tuple[str, ...]:
    """Catalog-allowed efforts, clipped when thinking is Ollama-only on llamacpp."""
    efforts = allowed_reasoning_efforts(model_name)
    if _thinking_clipped_for_runtime(model_name, local_runtime):
        return tuple(e for e in efforts if e == "none")
    return efforts


def allowed_reasoning_efforts(model_name: str) -> tuple[str, ...]:
    """Allowed reasoning_effort values from [model_params*] model:<key> or default layer."""
    layers = _load_model_params_layers()
    if model_name:
        efforts = _efforts_from_layer(layers.get(f"model:{model_name}"))
        if efforts:
            return efforts
    efforts = _efforts_from_layer(layers.get("default"))
    if efforts:
        return efforts
    return ("none",)


def reasoning_effort_select_options(
    model_name: str,
    local_runtime: str | None = None,
) -> list[tuple[str, str]]:
    """Textual Select options: Inherit + model-allowed effort levels."""
    opts: list[tuple[str, str]] = [("Inherit", "")]
    for effort in _effective_allowed_reasoning_efforts(model_name, local_runtime):
        opts.append((effort, effort))
    return opts


def supports_reasoning_config(
    model_name: str,
    local_runtime: str | None = None,
) -> bool:
    """True when the model exposes more than ``none`` for reasoning effort."""
    return any(
        e != "none"
        for e in _effective_allowed_reasoning_efforts(model_name, local_runtime)
    )


def effective_agent_reasoning_effort(
    model_name: str,
    agent_effort: str | None,
    local_runtime: str | None = None,
) -> str:
    """Select value for UI: agent override when allowed, else inherit (empty)."""
    if not agent_effort:
        return ""
    allowed = set(_effective_allowed_reasoning_efforts(model_name, local_runtime))
    return agent_effort if agent_effort in allowed else ""


def sanitize_agent_param_fields(
    model_name: str,
    *,
    reasoning_effort: str | None = None,
    reasoning_max_tokens: int | None = None,
    temperature: float | None = None,
    model_params_extra: str | dict | None = None,
) -> dict[str, Any]:
    """Drop agent overrides that are invalid for ``model_name`` (None = inherit)."""
    updates: dict[str, Any] = {}
    allowed = set(_effective_allowed_reasoning_efforts(model_name))
    if reasoning_effort is not None and reasoning_effort not in allowed:
        updates["reasoning_effort"] = None
        if reasoning_max_tokens is not None:
            updates["reasoning_max_tokens"] = None
    elif reasoning_max_tokens is not None and not supports_reasoning_config(model_name):
        updates["reasoning_max_tokens"] = None
    return updates


def get_model_catalog_hints(model_name: str) -> dict[str, str]:
    """Read-only catalog metadata for UI hints (modalities, work tier)."""
    try:
        import sys
        core_infra = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        if core_infra not in sys.path:
            sys.path.insert(0, core_infra)
        from model_catalog import catalog_entry_for_model, load_catalog, load_providers
        from model_drivers.registry import catalog_driver_enrichment

        catalog = load_catalog()
        row = catalog_entry_for_model(model_name, catalog) or {}
        driver_meta = catalog_driver_enrichment(
            model_name,
            row,
            catalog=catalog,
            providers=load_providers(),
        )
        return {
            "work_modality": row.get("work_modality", "balanced") or "balanced",
            "input_modalities": row.get("input_modalities", "text") or "text",
            "output_modalities": row.get("output_modalities", "text") or "text",
            "driver_summary": driver_meta["driver_summary"],
        }
    except Exception:
        return {
            "work_modality": "balanced",
            "input_modalities": "text",
            "output_modalities": "text",
            "driver_summary": "text-native",
        }


def _resolve_models_ini_path() -> str:
    for path in _MODELS_INI_PATHS:
        if os.path.exists(path):
            return path
    return _MODELS_INI_PATHS[-1]


def _read_ini_section(path: str, section: str) -> dict[str, str]:
    ini = configparser.ConfigParser(delimiters=("=",), strict=False)
    if not os.path.exists(path):
        return {}
    ini.read(path)
    if not ini.has_section(section):
        return {}
    return {k.strip(): v.strip() for k, v in ini.items(section)}


def _parse_params_json(raw: str) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, TypeError):
        return {}


def _load_model_params_custom_layers() -> dict[str, dict[str, Any]]:
    """Load [model_params_custom] only (operator overrides, not baseline)."""
    path = _resolve_models_ini_path()
    custom = _read_ini_section(path, "model_params_custom")
    out: dict[str, dict[str, Any]] = {}
    for key, raw in custom.items():
        if key.startswith("provider:"):
            continue
        out[key] = _parse_params_json(raw)
    return out


def _load_model_params_layers() -> dict[str, dict[str, Any]]:
    """Load merged [model_params] + [model_params_custom] as parsed JSON dicts."""
    path = _resolve_models_ini_path()
    base = _read_ini_section(path, "model_params")
    custom = _read_ini_section(path, "model_params_custom")
    out: dict[str, dict[str, Any]] = {}
    for key, raw in base.items():
        if key.startswith("provider:"):
            continue
        out[key] = _parse_params_json(raw)
    for key, raw in custom.items():
        if key.startswith("provider:"):
            continue
        parsed = _parse_params_json(raw)
        if key in out:
            out[key] = _merge_layers(out[key], parsed)
        else:
            out[key] = parsed
    return out


def _load_catalog_provider(model_name: str) -> str | None:
    path = _resolve_models_ini_path()
    for section in ("catalog", "catalog_custom"):
        rows = _read_ini_section(path, section)
        if model_name in rows:
            parts = rows[model_name].split("|")
            if len(parts) >= 2:
                return parts[1].strip() or None
    return None


def _load_provider_cls(provider_slug: str | None) -> str:
    if not provider_slug:
        return ""
    path = _resolve_models_ini_path()
    for section in ("providers", "providers_custom"):
        rows = _read_ini_section(path, section)
        if provider_slug in rows:
            parts = rows[provider_slug].split("|")
            if len(parts) >= 3:
                return parts[2].strip()
    return ""


def _merge_layers(*layers: dict[str, Any] | None) -> dict[str, Any]:
    """Shallow-merge param layers; ``extra`` bags deep-merge."""
    merged: dict[str, Any] = {}
    extra: dict[str, Any] = {}
    for layer in layers:
        if not layer:
            continue
        for key, val in layer.items():
            if key == "extra":
                if isinstance(val, dict):
                    extra.update(val)
                continue
            if val is not None:
                merged[key] = val
    if extra:
        merged["extra"] = extra
    return merged


def agent_overrides_from_values(
    temperature: float | None = None,
    reasoning_effort: str | None = None,
    reasoning_max_tokens: int | None = None,
    model_params_extra: str | dict | None = None,
) -> dict[str, Any] | None:
    """Build an agent override dict from explicit values (None = inherit)."""
    overrides: dict[str, Any] = {}
    if temperature is not None:
        overrides["temperature"] = temperature
    if reasoning_effort:
        overrides["reasoning_effort"] = reasoning_effort
    if reasoning_max_tokens is not None:
        overrides["reasoning_max_tokens"] = reasoning_max_tokens
    if model_params_extra:
        if isinstance(model_params_extra, dict):
            overrides["extra"] = model_params_extra
        else:
            parsed = _parse_params_json(model_params_extra)
            if parsed:
                overrides["extra"] = parsed
    return overrides or None


def resolve_model_params(
    model_name: str,
    agent_overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Resolve normalized params: agent > model > system."""
    layers = _load_model_params_layers()

    resolved = strip_unknown_params_keys(_merge_layers(
        SYSTEM_DEFAULTS,
        layers.get("default"),
        layers.get(f"model:{model_name}"),
        agent_overrides,
    ))

    effort = resolved.get("reasoning_effort") or "none"
    if effort not in REASONING_EFFORTS:
        effort = "none"
    allowed = set(allowed_reasoning_efforts(model_name))
    if effort not in allowed:
        model_default = (layers.get(f"model:{model_name}") or {}).get("reasoning_effort")
        if model_default in allowed:
            effort = model_default
        else:
            effort = "none"
    resolved["reasoning_effort"] = effort
    if "extra" not in resolved:
        resolved["extra"] = {}
    return resolved


_LOCAL_PROVIDER_CLASSES = {"ollama": "ChatOllama", "llamacpp": "ChatOpenAI"}


def detect_provider_family(
    model_name: str,
    provider_slug: str | None = None,
) -> str:
    """Return one of: openai_compat, anthropic, google, local."""
    slug = provider_slug or _load_catalog_provider(model_name)
    cls = _load_provider_cls(slug) or _LOCAL_PROVIDER_CLASSES.get(slug or "", "")
    if cls == "ChatAnthropic":
        return "anthropic"
    if cls == "ChatGoogleGenerativeAI":
        return "google"
    if cls in ("ChatOpenAI", "ChatOllama"):
        return "openai_compat" if cls == "ChatOpenAI" else "local"

    # Compatibility fallback for callers evaluating a Model before catalog add.
    if model_name.startswith("gemini"):
        return "google"
    if model_name.startswith("claude"):
        return "anthropic"
    if model_name.startswith(("gpt", "grok")) or "/" in model_name:
        return "openai_compat"
    return "local"


def _reasoning_active(params: dict[str, Any]) -> bool:
    effort = params.get("reasoning_effort") or "none"
    return effort != "none" or bool(params.get("reasoning_max_tokens"))


def _anthropic_adaptive_thinking_model(model_name: str) -> bool:
    """Claude Opus 4.7+ — sampling params 400; use adaptive thinking + effort."""
    if not model_name.startswith("claude-opus-4-"):
        return False
    suffix = model_name.rsplit("-", 1)[-1]
    try:
        return int(suffix) >= 7
    except ValueError:
        return model_name in {"claude-opus-4-7", "claude-opus-4-8"}


def _anthropic_effort_from_reasoning(effort: str) -> str:
    """Map normalized reasoning_effort → Anthropic output_config.effort."""
    if effort in ("minimal", "none"):
        return "low"
    return effort


def _effort_budget(effort: str) -> int:
    return _EFFORT_BUDGET_MAP.get(effort, 8192)


def _model_param_layer(model_name: str) -> dict[str, Any]:
    return _load_model_params_layers().get(f"model:{model_name}") or {}


def _ollama_think_mode(model_name: str) -> str | None:
    """Return think_mode when the model declares Ollama thinking support."""
    mode = (_model_param_layer(model_name).get("think_mode") or "").strip().lower()
    return mode if mode in _OLLAMA_THINK_MODES else None


def _ollama_think_native(effort: str, think_mode: str) -> bool | str:
    """Map normalized reasoning_effort to ChatOllama ``reasoning`` (Ollama ``think``)."""
    if effort == "none":
        return False
    if think_mode == "levels":
        if effort in ("low", "medium", "high"):
            return effort
        # Coarse map for cloud-style effort names on level-based models.
        if effort in ("minimal", "low"):
            return "low"
        if effort in ("medium",):
            return "medium"
        return "high"
    return True


def apply_native_for_local_runtime(native: dict[str, Any], runtime: str) -> dict[str, Any]:
    """Shape LangChain kwargs for the active local backend (Ollama vs llamacpp)."""
    if runtime == "ollama":
        return dict(native)
    out: dict[str, Any] = {}
    for key, val in native.items():
        if key in _OLLAMA_RUNTIME_ONLY_KEYS:
            continue
        if key == "num_predict":
            out["max_tokens"] = val
        elif key in _SYCL_OPENAI_KEYS:
            out[key] = val
        elif key not in ("extra_body", "model_kwargs", "reasoning_effort"):
            # Ollama-only sampling (top_k, penalties, …) — not forwarded to llamacpp.
            continue
    return out


def to_native_kwargs(
    family: str,
    model_name: str,
    params: dict[str, Any],
    provider_slug: str | None = None,
) -> dict[str, Any]:
    """Translate normalized params to LangChain constructor kwargs."""
    kwargs: dict[str, Any] = {}
    extra_body: dict[str, Any] = {}
    model_kwargs: dict[str, Any] = {}

    temp = params.get("temperature")
    effort = params.get("reasoning_effort") or "none"
    budget = params.get("reasoning_max_tokens")
    extra = params.get("extra") or {}
    thinking_on = _reasoning_active(params)
    slug = provider_slug or _load_catalog_provider(model_name)

    if family == "anthropic":
        adaptive = _anthropic_adaptive_thinking_model(model_name)
        if thinking_on:
            if adaptive:
                model_kwargs["thinking"] = {"type": "adaptive"}
                if effort != "none":
                    model_kwargs["output_config"] = {
                        "effort": _anthropic_effort_from_reasoning(effort),
                    }
            else:
                model_kwargs["thinking"] = {
                    "type": "enabled",
                    "budget_tokens": budget or _effort_budget(effort),
                }
        elif temp is not None and not adaptive:
            kwargs["temperature"] = temp
    elif family == "google":
        if temp is not None:
            kwargs["temperature"] = temp
        if thinking_on:
            model_kwargs["thinking_budget"] = budget or _effort_budget(effort)
    elif family == "openai_compat":
        is_local_llamacpp = slug == "llamacpp"
        is_openrouter = slug == "openrouter"
        if temp is not None and not (thinking_on and not is_openrouter):
            if not (is_local_llamacpp and _ollama_think_mode(model_name)):
                kwargs["temperature"] = temp
        if is_openrouter and thinking_on:
            reasoning: dict[str, Any] = {}
            if effort != "none":
                reasoning["effort"] = effort
            if budget:
                reasoning["max_tokens"] = budget
            if reasoning:
                extra_body["reasoning"] = reasoning
        elif thinking_on and effort != "none" and not is_local_llamacpp:
            kwargs["reasoning_effort"] = effort
    elif family == "local":
        if temp is not None:
            kwargs["temperature"] = temp
        if slug != "llamacpp":
            think_mode = _ollama_think_mode(model_name)
            if think_mode:
                kwargs["reasoning"] = _ollama_think_native(effort, think_mode)
        ollama_extra: dict[str, Any] = {}
        for key, val in extra.items():
            if key in _OLLAMA_KWARG_KEYS:
                kwargs[key] = val
            else:
                ollama_extra[key] = val
        if ollama_extra:
            kwargs.update(ollama_extra)

    if extra and family not in ("anthropic", "google", "local"):
        if slug == "llamacpp":
            for key, val in extra.items():
                if key == "num_predict":
                    kwargs["max_tokens"] = val
                elif key in _SYCL_OPENAI_KEYS:
                    kwargs[key] = val
        else:
            extra_body.update(extra)
    elif extra and family in ("anthropic", "google"):
        model_kwargs.update(extra)

    if extra_body:
        kwargs["extra_body"] = extra_body
    if model_kwargs:
        kwargs["model_kwargs"] = model_kwargs
    return kwargs


def list_model_params() -> dict[str, dict[str, Any]]:
    """Return all configured param layers (merged baseline + custom)."""
    return _load_model_params_layers()


# Top-level keys in [model_params] JSON layers.
STRUCTURED_TOP_LEVEL_KEYS = frozenset({
    "temperature",
    "reasoning_effort",
    "reasoning_max_tokens",
    "allowed_reasoning_efforts",
    "think_mode",
    "extra",
})


def efforts_to_csv(efforts: Any) -> str:
    """Serialize allowed_reasoning_efforts for CSV input fields."""
    if not isinstance(efforts, list):
        return ""
    return ",".join(str(x).strip() for x in efforts if str(x).strip())


def efforts_from_csv(raw: str) -> list[str] | None:
    """Parse CSV allowed_reasoning_efforts; None when blank (inherit)."""
    text = (raw or "").strip()
    if not text:
        return None
    return [x.strip() for x in text.split(",") if x.strip()]


def strip_unknown_params_keys(params: dict[str, Any] | None) -> dict[str, Any]:
    """Keep only supported top-level param keys (drops legacy overflow keys on save)."""
    if not params:
        return {}
    return {k: v for k, v in params.items() if k in STRUCTURED_TOP_LEVEL_KEYS}


def normalize_custom_params(
    params: dict[str, Any] | None,
    model_name: str = "",
) -> tuple[dict[str, Any], list[str]]:
    """Validate and sanitize a params layer; return warnings for stripped values."""
    warnings: list[str] = []
    out = strip_unknown_params_keys(params)

    temp = out.get("temperature")
    if temp is not None:
        try:
            t = float(temp)
            if t < 0.0 or t > 2.0:
                warnings.append(f"temperature {t} out of range 0–2; removed")
                out.pop("temperature", None)
            else:
                out["temperature"] = t
        except (TypeError, ValueError):
            warnings.append("invalid temperature; removed")
            out.pop("temperature", None)

    effort = out.get("reasoning_effort")
    if effort is not None:
        effort = str(effort).strip().lower()
        if effort not in REASONING_EFFORTS:
            warnings.append(f"invalid reasoning_effort '{effort}'; removed")
            out.pop("reasoning_effort", None)
        else:
            out["reasoning_effort"] = effort

    allowed = out.get("allowed_reasoning_efforts")
    if allowed is not None:
        if isinstance(allowed, str):
            allowed = efforts_from_csv(allowed) or []
        if isinstance(allowed, list):
            cleaned = [
                str(x).strip().lower()
                for x in allowed
                if str(x).strip() and str(x).strip().lower() in REASONING_EFFORTS
            ]
            dropped = len(allowed) - len(cleaned)
            if dropped:
                warnings.append(f"dropped {dropped} invalid allowed_reasoning_efforts value(s)")
            if not cleaned:
                warnings.append("allowed_reasoning_efforts empty after cleanup; removed")
                out.pop("allowed_reasoning_efforts", None)
            else:
                out["allowed_reasoning_efforts"] = cleaned
        else:
            warnings.append("allowed_reasoning_efforts must be a list; removed")
            out.pop("allowed_reasoning_efforts", None)

    if model_name and out.get("reasoning_effort"):
        allowed_set = set(out.get("allowed_reasoning_efforts") or allowed_reasoning_efforts(model_name))
        if out["reasoning_effort"] not in allowed_set:
            warnings.append(
                f"reasoning_effort '{out['reasoning_effort']}' not allowed for model; removed"
            )
            out.pop("reasoning_effort", None)

    rmt = out.get("reasoning_max_tokens")
    if rmt is not None:
        try:
            iv = int(rmt)
            if iv < 0:
                warnings.append("reasoning_max_tokens must be >= 0; removed")
                out.pop("reasoning_max_tokens", None)
            else:
                out["reasoning_max_tokens"] = iv
        except (TypeError, ValueError):
            warnings.append("invalid reasoning_max_tokens; removed")
            out.pop("reasoning_max_tokens", None)

    think = out.get("think_mode")
    if think is not None:
        think = str(think).strip().lower()
        if think not in _OLLAMA_THINK_MODES:
            warnings.append(f"invalid think_mode '{think}'; removed")
            out.pop("think_mode", None)
        else:
            out["think_mode"] = think

    extra = out.get("extra")
    if extra is not None and not isinstance(extra, dict):
        warnings.append("extra must be a JSON object; removed")
        out.pop("extra", None)

    return out, warnings


def build_params_layer_update(
    *,
    base: dict[str, Any] | None = None,
    temperature: float | None = None,
    reasoning_effort: str | None = None,
    reasoning_max_tokens: int | None = None,
    allowed_reasoning_efforts: str | list[str] | None = None,
    think_mode: str | None = None,
    extra: str | dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build an updated params layer (agictl model params set / UI save path)."""
    data = strip_unknown_params_keys(base)
    if temperature is not None:
        data["temperature"] = temperature
    if reasoning_effort is not None:
        data["reasoning_effort"] = reasoning_effort
    if reasoning_max_tokens is not None:
        data["reasoning_max_tokens"] = reasoning_max_tokens
    if allowed_reasoning_efforts is not None:
        if isinstance(allowed_reasoning_efforts, str):
            data["allowed_reasoning_efforts"] = efforts_from_csv(allowed_reasoning_efforts) or []
        else:
            data["allowed_reasoning_efforts"] = [
                str(x).strip() for x in allowed_reasoning_efforts if str(x).strip()
            ]
    if think_mode is not None:
        data["think_mode"] = think_mode
    if extra is not None:
        if isinstance(extra, dict):
            parsed_extra = extra
        else:
            parsed_extra = _parse_params_json(extra)
            if extra and not parsed_extra:
                raise ValueError("extra must be a JSON object")
        data["extra"] = parsed_extra
    return strip_unknown_params_keys(data)
