"""Live voice call settings, call-capable catalog keys, and the call-tool gate.

Shared by the harness (tool exposure), agictl (``model live-call`` + call
plumbing), and agitop (System Settings → Live Call). Behavior contract:
``design/spec/state/state_live_voice_call.md`` §1.4 rules 14–16 and §1.7.
"""

from __future__ import annotations

import configparser
import os
from dataclasses import dataclass, field
from typing import Callable

from model_catalog import (
    SETUP_INI_CANONICAL,
    _read_raw_section,
    load_catalog,
    resolve_models_ini_path,
)

CALL_CAPABLE_SECTION = "catalog_live_call"
CALL_CAPABLE_SITE_SECTION = "catalog_live_call_custom"
LIVE_CALL_SECTION = "live_call"
FEATURE_KEY = "live_call"
VOICE_PROVIDER = "openai"
CALL_AGENT = "coa"

DEFAULT_VOICE_MODEL = "gpt-live-1"
DEFAULT_MAX_MINUTES = 15
DEFAULT_JOIN_TIMEOUT_SECONDS = 45
DEFAULT_CALLS_PER_CYCLE = 1
MAX_MINUTES_RANGE = (1, 55)
JOIN_TIMEOUT_RANGE = (15, 120)
CALLS_PER_CYCLE_RANGE = (1, 5)


def _truthy(value: str | None) -> bool:
    return str(value or "").strip().lower() in ("1", "true", "yes", "on")


def _setup_paths(setup_ini: str | None) -> list[str]:
    if setup_ini:
        return [setup_ini]
    here = os.path.dirname(os.path.abspath(__file__))
    return [SETUP_INI_CANONICAL, os.path.join(os.path.dirname(here), "setup.ini")]


def _setup_value(section: str, key: str, default: str, setup_ini: str | None) -> str:
    for path in _setup_paths(setup_ini):
        if not os.path.isfile(path):
            continue
        cfg = configparser.ConfigParser(delimiters=("=",), strict=False, interpolation=None)
        try:
            cfg.read(path)
        except (configparser.Error, OSError):
            continue
        if cfg.has_option(section, key):
            return cfg.get(section, key, fallback=default).strip()
    return default


def _clamp_int(raw: str, default: int, bounds: tuple[int, int]) -> int:
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        return default
    low, high = bounds
    return max(low, min(high, value))


@dataclass(frozen=True)
class LiveCallSettings:
    enabled: bool
    call_model: str
    voice_model: str
    max_minutes: int
    join_timeout_seconds: int
    narrate_progress: bool = True
    calls_per_cycle: int = DEFAULT_CALLS_PER_CYCLE


def read_settings(setup_ini: str | None = None) -> LiveCallSettings:
    """``[features] live_call`` + ``[live_call]`` from setup.ini."""
    return LiveCallSettings(
        enabled=_truthy(_setup_value("features", FEATURE_KEY, "false", setup_ini)),
        call_model=_setup_value(LIVE_CALL_SECTION, "call_model", "", setup_ini),
        voice_model=_setup_value(LIVE_CALL_SECTION, "voice_model", DEFAULT_VOICE_MODEL, setup_ini)
        or DEFAULT_VOICE_MODEL,
        max_minutes=_clamp_int(
            _setup_value(LIVE_CALL_SECTION, "max_minutes", str(DEFAULT_MAX_MINUTES), setup_ini),
            DEFAULT_MAX_MINUTES,
            MAX_MINUTES_RANGE,
        ),
        join_timeout_seconds=_clamp_int(
            _setup_value(
                LIVE_CALL_SECTION,
                "join_timeout_seconds",
                str(DEFAULT_JOIN_TIMEOUT_SECONDS),
                setup_ini,
            ),
            DEFAULT_JOIN_TIMEOUT_SECONDS,
            JOIN_TIMEOUT_RANGE,
        ),
        narrate_progress=_truthy(
            _setup_value(LIVE_CALL_SECTION, "narrate_progress", "true", setup_ini)
        ),
        calls_per_cycle=_clamp_int(
            _setup_value(
                LIVE_CALL_SECTION, "calls_per_cycle", str(DEFAULT_CALLS_PER_CYCLE), setup_ini
            ),
            DEFAULT_CALLS_PER_CYCLE,
            CALLS_PER_CYCLE_RANGE,
        ),
    )


def call_capable_keys(models_ini: str | None = None) -> set[str]:
    """Shipped ``[catalog_live_call]`` keys, adjusted by the site layer.

    Site ``[catalog_live_call_custom]``: ``key = true`` adds, ``key = false``
    removes a shipped key.
    """
    path = models_ini or resolve_models_ini_path()
    keys = {k for k, v in _read_raw_section(path, CALL_CAPABLE_SECTION).items() if _truthy(v)}
    for key, raw in _read_raw_section(path, CALL_CAPABLE_SITE_SECTION).items():
        if _truthy(raw):
            keys.add(key)
        else:
            keys.discard(key)
    return keys


def is_call_capable(key: str, models_ini: str | None = None) -> bool:
    return (key or "").strip() in call_capable_keys(models_ini)


def default_key_check(provider_slug: str) -> bool:
    """True when the provider has a usable API key.

    Runtime resolver first (harness env + provider_keys.env — the only one deployed
    next to the harness); then Model Manager's import test where it is installed
    (agictl / agitop), which also knows the Gemini credential stores.
    """
    try:
        from provider_runtime import resolve_provider_api_key

        if resolve_provider_api_key(provider_slug):
            return True
    except Exception:
        pass
    try:
        from provider_catalog import provider_configured
    except ImportError:
        return False
    try:
        return bool(provider_configured(provider_slug)[0])
    except Exception:
        return False


def provider_labels(models_ini: str | None = None) -> dict[str, str]:
    """Provider slug → display label, as Model Manager shows it."""
    try:
        from provider_registry import load_merged_providers

        return {slug: row.get("label") or slug
                for slug, row in load_merged_providers(models_ini).items()}
    except Exception:
        return {}


def model_display(key: str, entry: dict | None, labels: dict[str, str]) -> str:
    """``Provider — Model name`` (the catalog label up to its description)."""
    entry = entry or {}
    slug = entry.get("provider") or ""
    name = (entry.get("label") or key).split(" — ")[0].strip() or key
    provider = labels.get(slug) or slug
    return f"{provider} — {name}" if provider else name


def selectable_call_models(
    *,
    models_ini: str | None = None,
    catalog: dict | None = None,
    key_check: Callable[[str], bool] = default_key_check,
) -> list[dict]:
    """Call-capable catalog keys that are enabled and whose provider is keyed.

    Feeds the setup pick and the agitop call-model picker, ordered like Model
    Manager (provider, then model name).
    """
    cat = catalog if catalog is not None else load_catalog(models_ini)
    labels = provider_labels(models_ini)
    out: list[dict] = []
    keyed: dict[str, bool] = {}
    for key in call_capable_keys(models_ini):
        entry = cat.get(key)
        if not entry or not entry.get("enabled"):
            continue
        slug = entry.get("provider") or ""
        if slug not in keyed:
            keyed[slug] = key_check(slug)
        if keyed[slug]:
            out.append({
                "key": key,
                "label": entry.get("label") or key,
                "provider": slug,
                "display": model_display(key, entry, labels),
            })
    out.sort(key=lambda r: r["display"].casefold())
    return out


@dataclass
class GateResult:
    ok: bool
    reasons: list[str] = field(default_factory=list)
    settings: LiveCallSettings | None = None
    call_model: str = ""
    call_provider: str = ""

    def summary(self) -> str:
        return "ready" if self.ok else "; ".join(self.reasons)


def evaluate_gate(
    agent_name: str,
    *,
    settings: LiveCallSettings | None = None,
    models_ini: str | None = None,
    catalog: dict | None = None,
    key_check: Callable[[str], bool] = default_key_check,
) -> GateResult:
    """Every condition that must hold before the call tool is offered (rules 14 + 16)."""
    settings = settings or read_settings()
    reasons: list[str] = []
    if (agent_name or "").strip().lower() != CALL_AGENT:
        reasons.append("only COA can place calls")
    if not settings.enabled:
        reasons.append("Live Call is off ([features] live_call)")
    if not key_check(VOICE_PROVIDER):
        reasons.append("OpenAI provider has no API key (GPT-Live voice)")

    call_model = settings.call_model
    call_provider = ""
    if not call_model:
        reasons.append("no call model set ([live_call] call_model)")
    else:
        if not is_call_capable(call_model, models_ini):
            reasons.append(f"call model '{call_model}' is not call-capable")
        cat = catalog if catalog is not None else load_catalog(models_ini)
        entry = cat.get(call_model)
        if not entry:
            reasons.append(f"call model '{call_model}' is not in the catalog")
        elif not entry.get("enabled"):
            reasons.append(f"call model '{call_model}' is disabled")
        else:
            call_provider = entry.get("provider") or ""
            if not key_check(call_provider):
                reasons.append(f"call model provider '{call_provider}' has no API key")

    return GateResult(
        ok=not reasons,
        reasons=reasons,
        settings=settings,
        call_model=call_model,
        call_provider=call_provider,
    )


def validate_setting(section: str, key: str, value: str, *, models_ini: str | None = None,
                     catalog: dict | None = None) -> tuple[bool, str, str]:
    """Validate one Live Call setup.ini write → (ok, error, normalized value)."""
    section, key, value = section.strip().lower(), key.strip().lower(), str(value).strip()
    if section == "features" and key == FEATURE_KEY:
        if value.lower() not in ("true", "false"):
            return False, "features.live_call must be 'true' or 'false'", value
        return True, "", value.lower()
    if section != LIVE_CALL_SECTION:
        return True, "", value
    if key == "call_model":
        if not value:
            return True, "", ""
        if not is_call_capable(value, models_ini):
            return False, f"'{value}' is not a call-capable model (models.ini [catalog_live_call])", value
        cat = catalog if catalog is not None else load_catalog(models_ini)
        entry = cat.get(value)
        if not entry or not entry.get("enabled"):
            return False, f"'{value}' is not an enabled model in the live catalog", value
        return True, "", value
    if key == "voice_model":
        return (True, "", value) if value else (False, "voice_model cannot be empty", value)
    if key == "narrate_progress":
        if value.lower() not in ("true", "false"):
            return False, "narrate_progress must be 'true' or 'false'", value
        return True, "", value.lower()
    bounds = {"max_minutes": MAX_MINUTES_RANGE, "join_timeout_seconds": JOIN_TIMEOUT_RANGE,
              "calls_per_cycle": CALLS_PER_CYCLE_RANGE}.get(key)
    if bounds:
        try:
            number = int(value)
        except ValueError:
            return False, f"{key} must be a whole number", value
        if not bounds[0] <= number <= bounds[1]:
            return False, f"{key} must be between {bounds[0]} and {bounds[1]}", value
        return True, "", str(number)
    return False, f"unknown live_call key '{key}'", value


def site_layer_value(key: str, enabled: bool, shipped: set[str]) -> str | None:
    """Value to write in ``[catalog_live_call_custom]`` for a toggle, or None to clear.

    A key that matches the shipped state needs no site row.
    """
    if enabled == (key in shipped):
        return None
    return "true" if enabled else "false"


def shipped_call_capable_keys(models_ini: str | None = None) -> set[str]:
    path = models_ini or resolve_models_ini_path()
    return {k for k, v in _read_raw_section(path, CALL_CAPABLE_SECTION).items() if _truthy(v)}
