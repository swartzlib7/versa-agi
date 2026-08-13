"""Shared model catalog parsing for agictl and harness routing."""

from __future__ import annotations

import os
import configparser

WORK_MODALITIES = ("fast", "balanced", "reasoning", "code", "local")
IO_MODALITIES = ("text", "image", "audio", "video")
# Non-text outputs routed via utility agents / generation APIs (Phase F)
OUTPUT_DELIVERY_MODALITIES = ("image", "audio", "video")

SETUP_INI_CANONICAL = "/etc/versa-agi/setup.ini"


def resolve_models_ini_path() -> str:
    candidates = [
        os.environ.get("VERSA_MODELS_INI"),
        "/etc/versa-agi/models.ini",
        os.path.join(os.path.dirname(os.path.dirname(__file__)), "models.ini"),
    ]
    for path in candidates:
        if path and os.path.isfile(path):
            return path
    return candidates[1]


def _read_raw_section(path: str, section: str) -> dict[str, str]:
    out: dict[str, str] = {}
    if not os.path.isfile(path):
        return out
    cfg = configparser.ConfigParser(delimiters=("=",), strict=False)
    cfg.optionxform = str
    cfg.read(path)
    if not cfg.has_section(section):
        return out
    for key, val in cfg.items(section):
        k = key.strip()
        if k and not k.startswith("#"):
            out[k] = val.strip()
    return out


def parse_catalog_row(raw: str) -> dict | None:
    """Parse a catalog pipe row (7- or 11-field) into a normalized dict."""
    parts = [p.strip() for p in raw.split("|")]
    if len(parts) < 7:
        return None
    try:
        ctx_rec = int(parts[4] or "0")
        ctx_max = int(parts[5] or "0")
    except ValueError:
        ctx_rec, ctx_max = 0, 0

    if len(parts) >= 11:
        work_modality = parts[6] or "balanced"
        input_modalities = parts[7] or "text"
        output_modalities = parts[8] or "text"
        router_eligible = parts[9].lower() == "true"
        label = "|".join(parts[10:]).strip()
    else:
        work_modality = "balanced"
        input_modalities = "text"
        output_modalities = "text"
        router_eligible = False
        label = "|".join(parts[6:]).strip()

    return {
        "class": parts[0],
        "provider": parts[1],
        "enabled": parts[2].lower() == "true",
        "coa": parts[3].lower() == "true",
        "ctx_recommended": ctx_rec,
        "ctx_max": ctx_max,
        "work_modality": work_modality,
        "input_modalities": input_modalities,
        "output_modalities": output_modalities,
        "router_eligible": router_eligible,
        "label": label,
    }


def catalog_row_to_value(m: dict) -> str:
    return (
        f"{m['class']}|{m['provider']}|{'true' if m['enabled'] else 'false'}|"
        f"{'true' if m['coa'] else 'false'}|{m['ctx_recommended']}|{m['ctx_max']}|"
        f"{m.get('work_modality', 'balanced')}|{m.get('input_modalities', 'text')}|"
        f"{m.get('output_modalities', 'text')}|"
        f"{'true' if m.get('router_eligible') else 'false'}|{m['label']}"
    )


LOCAL_PROVIDER_SLUGS = ("ollama", "llamacpp")


def local_provider_for_backend(gpu_backend: str) -> str:
    """Map inference stack (setup.ini ``gpu_backend``) to provider slug.

    ``standard`` → ``ollama``, ``intel`` → ``llamacpp``. Does not interpret
    ``remote`` (topology) — use :func:`resolve_local_provider` for runtime.
    """
    backend = (gpu_backend or "").strip().lower()
    if backend in ("ollama", "llamacpp"):
        return backend
    if backend == "intel":
        return "llamacpp"
    return "ollama"


def read_paths_gpu_backend() -> str:
    """Read VERSA_GPU_BACKEND from paths.env (standard | intel | remote)."""
    path = "/etc/versa-agi/paths.env"
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                if line.startswith("VERSA_GPU_BACKEND="):
                    return line.split("=", 1)[1].strip().strip('"')
    except OSError:
        pass
    return read_setup_value("local_ai", "gpu_backend", "standard")


def resolve_local_provider(gpu_backend: str | None = None) -> str:
    """Active local provider slug (``ollama`` | ``llamacpp``).

    When paths.env reports ``remote`` (client topology), the remote server's
    stack comes from setup.ini ``gpu_backend`` — a remote Ollama box is still
    ``ollama``, not ``llamacpp``.
    """
    backend = (gpu_backend or read_paths_gpu_backend()).strip().lower()
    if backend == "remote":
        backend = read_setup_value("local_ai", "gpu_backend", "standard").strip().lower()
    return local_provider_for_backend(backend)


def load_providers(path: str | None = None) -> dict[str, dict]:
    """Load merged provider registry (baseline [providers] + [providers_custom])."""
    path = path or resolve_models_ini_path()
    base = _read_raw_section(path, "providers")
    custom = _read_raw_section(path, "providers_custom")
    out: dict[str, dict] = {}

    def _parse(slug: str, raw: str, origin: str) -> None:
        parts = [p.strip() for p in raw.split("|")]
        enabled = parts[0].lower() == "true" if parts else False
        label = parts[1] if len(parts) > 1 else slug
        cls = parts[2] if len(parts) > 2 else ""
        out[slug] = {"enabled": enabled, "label": label, "cls": cls, "origin": origin}

    for slug, raw in base.items():
        _parse(slug, raw, "baseline")
    for slug, raw in custom.items():
        _parse(slug, raw, "override" if slug in base else "custom")
    return out


def provider_is_enabled(slug: str, providers: dict[str, dict] | None = None) -> bool:
    """True when slug exists in merged [providers*] and is enabled."""
    providers = providers or load_providers()
    return bool(providers.get(slug, {}).get("enabled"))


def load_catalog(path: str | None = None) -> dict[str, dict]:
    """Load merged catalog (baseline + custom)."""
    path = path or resolve_models_ini_path()
    base = _read_raw_section(path, "catalog")
    custom = _read_raw_section(path, "catalog_custom")
    out: dict[str, dict] = {}

    def _ingest(key: str, raw: str, origin: str) -> None:
        parsed = parse_catalog_row(raw)
        if parsed:
            parsed["origin"] = origin
            out[key] = parsed

    for key, raw in base.items():
        _ingest(key, raw, "baseline")
    for key, raw in custom.items():
        _ingest(key, raw, "override" if key in base else "custom")
    return out


def read_setup_value(section: str, key: str, default: str = "") -> str:
    for path in (SETUP_INI_CANONICAL, os.path.join(os.path.dirname(os.path.dirname(__file__)), "setup.ini")):
        if not os.path.isfile(path):
            continue
        cfg = configparser.ConfigParser(delimiters=("=",), strict=False)
        cfg.read(path)
        if cfg.has_option(section, key):
            return cfg.get(section, key, fallback=default).strip()
    return default


def validate_preferred_model_key(key: str, work_modality: str, cat: dict | None = None) -> tuple[bool, str]:
    """Validate a single [model_routing] preferred catalog key."""
    key = (key or "").strip()
    if not key:
        return True, ""
    cat = cat or load_catalog()
    m = cat.get(key)
    if not m:
        return False, f"Preferred model '{key}' for {work_modality} is not in catalog"
    if not m.get("enabled"):
        return False, f"Preferred model '{key}' for {work_modality} is disabled"
    if not m.get("router_eligible"):
        return False, f"Preferred model '{key}' for {work_modality} is not router-eligible"
    return True, ""


def model_output_includes(m: dict, output_modality: str) -> bool:
    outputs = {x.strip() for x in m.get("output_modalities", "text").split(",") if x.strip()}
    return output_modality in outputs


def parse_input_modalities(m: dict) -> set[str]:
    """Return normalized input modality tokens from a catalog row."""
    raw = m.get("input_modalities", "text") or "text"
    return {x.strip() for x in raw.split(",") if x.strip()}


def model_supports_input_modality(m: dict | None, modality: str) -> bool:
    """True when catalog row declares the given input modality."""
    if not m:
        return False
    return modality in parse_input_modalities(m)


def catalog_entry_for_model(model_name: str, cat: dict | None = None) -> dict | None:
    """Resolve a catalog row by key (exact match)."""
    cat = cat or load_catalog()
    return cat.get((model_name or "").strip())


def execution_model_supports_input(
    execution_model: str,
    modality: str,
    cat: dict | None = None,
) -> bool:
    """Check native text or exact executable non-text input support."""
    entry = catalog_entry_for_model(execution_model, cat)
    normalized = (modality or "").strip().lower()
    if not model_supports_input_modality(entry, normalized):
        return False
    if normalized == "text":
        return True
    from model_drivers.registry import resolve_model_driver

    return resolve_model_driver(
        execution_model,
        "input",
        normalized,
        catalog=cat,
    ) is not None


def validate_preferred_output_key(key: str, output_modality: str, cat: dict | None = None) -> tuple[bool, str]:
    """Validate a single [output_routing] preferred catalog key."""
    key = (key or "").strip()
    if not key:
        return True, ""
    cat = cat or load_catalog()
    m = cat.get(key)
    if not m:
        return False, f"Output model '{key}' for {output_modality} is not in catalog"
    if not m.get("enabled"):
        return False, f"Output model '{key}' for {output_modality} is disabled"
    if not model_output_includes(m, output_modality):
        return False, (
            f"Output model '{key}' does not declare output_modality '{output_modality}' "
            f"(has: {m.get('output_modalities', 'text')})"
        )
    from model_drivers.registry import resolve_model_driver

    if resolve_model_driver(
        key,
        "output",
        output_modality,
        catalog=cat,
    ) is None:
        return False, (
            f"Output model '{key}' has no exact executable ModelDriver "
            f"for output {output_modality}"
        )
    return True, ""


def read_output_routing_map() -> dict[str, str]:
    """Load [output_routing] preferred generation models (image/audio/video)."""
    return {
        om: read_setup_value("output_routing", om, "").strip()
        for om in OUTPUT_DELIVERY_MODALITIES
    }


def validate_output_routing_prefs(cat: dict | None = None) -> tuple[bool, str]:
    """Reject [output_routing] keys that fail catalog output-modality checks."""
    cat = cat or load_catalog()
    for om in OUTPUT_DELIVERY_MODALITIES:
        key = read_setup_value("output_routing", om, "").strip()
        ok, err = validate_preferred_output_key(key, om, cat)
        if not ok:
            return False, err
    return True, ""


def validate_model_routing_prefs(cat: dict | None = None) -> tuple[bool, str]:
    """Reject [model_routing] preferred keys that fail eligibility checks."""
    cat = cat or load_catalog()
    for wm in WORK_MODALITIES:
        key = read_setup_value("model_routing", wm, "").strip()
        ok, err = validate_preferred_model_key(key, wm, cat)
        if not ok:
            return False, err
    return True, ""


def model_supports_auto_routing(model_name: str, agent_name: str = "", cat: dict | None = None) -> bool:
    """True when an assigned catalog key may keep auto model routing enabled."""
    key = (model_name or "").strip()
    if not key:
        return False
    cat = cat or load_catalog()
    m = cat.get(key)
    if not m or not m.get("enabled"):
        return False
    if not m.get("router_eligible"):
        return False
    if agent_name == "coa" and not m.get("coa"):
        return False
    return True


def parse_pricing_row(raw: str) -> dict | None:
    """Parse [catalog_pricing] row: prompt|completion|image|reasoning|cache_read|fetched_at."""
    parts = [p.strip() for p in (raw or "").split("|")]
    if len(parts) < 2:
        return None
    try:
        return {
            "prompt_per_m": float(parts[0] or 0),
            "completion_per_m": float(parts[1] or 0),
            "image_per_m": float(parts[2] or 0) if len(parts) > 2 else 0.0,
            "reasoning_per_m": float(parts[3] or 0) if len(parts) > 3 else 0.0,
            "cache_read_per_m": float(parts[4] or 0) if len(parts) > 4 else 0.0,
            "fetched_at": parts[5] if len(parts) > 5 else "",
            "source": "openrouter_catalog",
        }
    except ValueError:
        return None


def load_catalog_pricing(path: str | None = None) -> dict[str, dict]:
    """Load [catalog_pricing] from models.ini (user layer only)."""
    path = path or resolve_models_ini_path()
    rows = _read_raw_section(path, "catalog_pricing")
    out: dict[str, dict] = {}
    for key, raw in rows.items():
        parsed = parse_pricing_row(raw)
        if parsed:
            out[key] = parsed
    return out


def estimate_cycle_cost_usd(
    execution_model: str,
    tokens_input: int,
    tokens_output: int,
    *,
    tokens_thinking: int = 0,
    tokens_cached: int = 0,
    pricing: dict[str, dict] | None = None,
) -> tuple[float | None, str | None]:
    """Estimate USD cost from catalog list rates (TD-COST-001)."""
    pricing = pricing if pricing is not None else load_catalog_pricing()
    rates = pricing.get((execution_model or "").strip())
    if not rates:
        return None, None
    billable_in = max(int(tokens_input or 0) - int(tokens_cached or 0), 0)
    cost = (
        (billable_in / 1_000_000) * rates.get("prompt_per_m", 0)
        + (int(tokens_output or 0) / 1_000_000) * rates.get("completion_per_m", 0)
        + (int(tokens_thinking or 0) / 1_000_000) * rates.get("reasoning_per_m", 0)
        + (int(tokens_cached or 0) / 1_000_000) * rates.get("cache_read_per_m", 0)
    )
    return round(cost, 6), rates.get("source", "openrouter_catalog")
