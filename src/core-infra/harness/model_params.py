# ─────────────────────────────────────────────────────
# Versa AGi — Abstracted Model Parameters
#
# Layered resolution (agent > model > system) of normalized
# generation params, translated per provider family for LangChain / LiteLLM.
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

REASONING_EFFORTS = ("none", "minimal", "low", "medium", "high", "max")

SYSTEM_DEFAULTS: dict[str, Any] = {
    "temperature": 0.2,
    "reasoning_effort": "none",
}

_EFFORT_BUDGET_MAP = {
    "minimal": 1024,
    "low": 4096,
    "medium": 8192,
    "high": 16384,
    "max": 32768,
}


def _resolve_models_ini_path() -> str:
    for path in _MODELS_INI_PATHS:
        if os.path.exists(path):
            return path
    return _MODELS_INI_PATHS[-1]


def _read_ini_section(path: str, section: str) -> dict[str, str]:
    ini = configparser.ConfigParser(delimiters=("=",))
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
        out[key] = _parse_params_json(raw)
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

    resolved = _merge_layers(
        SYSTEM_DEFAULTS,
        layers.get("default"),
        layers.get(f"model:{model_name}"),
        agent_overrides,
    )

    effort = resolved.get("reasoning_effort") or "none"
    if effort not in REASONING_EFFORTS:
        effort = "none"
    resolved["reasoning_effort"] = effort
    if "extra" not in resolved:
        resolved["extra"] = {}
    return resolved


def detect_provider_family(
    model_name: str,
    provider_slug: str | None = None,
) -> str:
    """Return one of: openai_compat, anthropic, google, local."""
    if model_name.startswith("gemini"):
        return "google"
    if model_name.startswith("claude"):
        return "anthropic"
    if model_name.startswith("gpt") or model_name.startswith("grok") or "/" in model_name:
        return "openai_compat"

    slug = provider_slug or _load_catalog_provider(model_name)
    cls = _load_provider_cls(slug)
    if cls == "ChatAnthropic":
        return "anthropic"
    if cls == "ChatGoogleGenerativeAI":
        return "google"
    if cls in ("ChatOpenAI", "ChatOllama"):
        return "openai_compat" if cls == "ChatOpenAI" else "local"
    return "local"


def _reasoning_active(params: dict[str, Any]) -> bool:
    effort = params.get("reasoning_effort") or "none"
    return effort != "none" or bool(params.get("reasoning_max_tokens"))


def _effort_budget(effort: str) -> int:
    return _EFFORT_BUDGET_MAP.get(effort, 8192)


def to_native_kwargs(
    family: str,
    model_name: str,
    params: dict[str, Any],
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

    if family == "anthropic":
        if thinking_on:
            model_kwargs["thinking"] = {
                "type": "enabled",
                "budget_tokens": budget or _effort_budget(effort),
            }
        elif temp is not None:
            kwargs["temperature"] = temp
    elif family == "google":
        if temp is not None:
            kwargs["temperature"] = temp
        if thinking_on:
            model_kwargs["thinking_budget"] = budget or _effort_budget(effort)
    elif family == "openai_compat":
        if temp is not None and not (thinking_on and "/" not in model_name):
            kwargs["temperature"] = temp
        if "/" in model_name and thinking_on:
            reasoning: dict[str, Any] = {}
            if effort != "none":
                reasoning["effort"] = effort
            if budget:
                reasoning["max_tokens"] = budget
            if reasoning:
                extra_body["reasoning"] = reasoning
        elif thinking_on and effort != "none":
            kwargs["reasoning_effort"] = effort
    elif family == "local":
        if temp is not None:
            kwargs["temperature"] = temp

    if extra:
        if family in ("anthropic", "google"):
            model_kwargs.update(extra)
        elif family == "local":
            kwargs.update(extra)
        else:
            extra_body.update(extra)

    if extra_body:
        kwargs["extra_body"] = extra_body
    if model_kwargs:
        kwargs["model_kwargs"] = model_kwargs
    return kwargs


def to_litellm_endpoint_extras(
    family: str,
    model_name: str,
    params: dict[str, Any],
) -> list[str]:
    """Emit YAML lines (indented) for LiteLLM inference_endpoint_params extras."""
    native = to_native_kwargs(family, model_name, params)
    lines: list[str] = []

    def _emit_scalar(key: str, val: Any, indent: int) -> None:
        pad = " " * indent
        if isinstance(val, bool):
            lines.append(f"{pad}{key}: {'true' if val else 'false'}\n")
        elif isinstance(val, str):
            lines.append(f'{pad}{key}: "{val}"\n')
        else:
            lines.append(f"{pad}{key}: {val}\n")

    def _emit_dict(d: dict[str, Any], indent: int) -> None:
        pad = " " * indent
        for key, val in d.items():
            if isinstance(val, dict):
                lines.append(f"{pad}{key}:\n")
                _emit_dict(val, indent + 2)
            else:
                _emit_scalar(key, val, indent)

    for key, val in native.items():
        if key == "extra_body" and isinstance(val, dict):
            lines.append("      extra_body:\n")
            _emit_dict(val, 8)
        elif key == "model_kwargs" and isinstance(val, dict):
            for mk, mv in val.items():
                if isinstance(mv, dict):
                    lines.append(f"      {mk}:\n")
                    _emit_dict(mv, 8)
                else:
                    _emit_scalar(mk, mv, 6)
        else:
            _emit_scalar(key, val, 6)
    return lines


def list_model_params() -> dict[str, dict[str, Any]]:
    """Return all configured param layers (merged baseline + custom)."""
    return _load_model_params_layers()
