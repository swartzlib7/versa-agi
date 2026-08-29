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


_STALE_TEXT_ONLY_MMPROJ_LABEL = " (text-only until mmproj)"


def catalog_label_after_probe(label: str | None) -> str:
    """Drop the pre-probe parenthetical once vision is live on the row."""
    return (label or "").replace(_STALE_TEXT_ONLY_MMPROJ_LABEL, "").strip()


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
        label = catalog_label_after_probe("|".join(parts[10:]))
    else:
        work_modality = "balanced"
        input_modalities = "text"
        output_modalities = "text"
        router_eligible = False
        label = catalog_label_after_probe("|".join(parts[6:]))

    model_class = parts[0]
    if model_class == "third_party":
        model_class = "cloud"
    return {
        "class": model_class,
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
    """Load merged provider registry (library + site activation + overlays)."""
    from provider_registry import load_merged_providers

    return load_merged_providers(path)


def provider_display_label(
    slug: str,
    providers: dict[str, dict] | None = None,
) -> str:
    """Human Provider name from merged ``[providers*]``, falling back to the slug."""
    providers = providers if providers is not None else load_providers()
    info = providers.get((slug or "").strip()) or {}
    return (info.get("label") or slug or "?").strip() or "?"


def format_catalog_picker_label(
    provider_label: str,
    model_label: str,
    catalog_key: str,
) -> str:
    """Picker text: ``{Provider}: {model label} ({catalog_key})``."""
    key = (catalog_key or "").strip()
    prov = (provider_label or "").strip() or "?"
    name = (model_label or "").strip() or key or "?"
    if not key:
        return f"{prov}: {name}"
    return f"{prov}: {name} ({key})"


def provider_is_enabled(slug: str, providers: dict[str, dict] | None = None) -> bool:
    """True when slug exists in merged [providers*] and is enabled."""
    providers = providers or load_providers()
    return bool(providers.get(slug, {}).get("enabled"))


def parse_csv_keys(raw: str) -> list[str]:
    """Split a comma CSV into stripped keys, dropping empties."""
    return [part.strip() for part in (raw or "").split(",") if part.strip()]


def assigned_local_model_keys(
    *,
    setup_csv: str | None = None,
    paths_env_path: str | None = None,
) -> list[str]:
    """Local chat keys the site has assigned (setup.ini + paths.env).

    Does not read ``[catalog_library]``. Unused stock library rows stay out of
    the live catalog until they appear in ``local_models`` / ``VERSA_LOCAL_MODELS``.
    """
    keys: list[str] = []
    seen: set[str] = set()

    def _add(raw: str) -> None:
        for key in parse_csv_keys(raw):
            if key not in seen:
                seen.add(key)
                keys.append(key)

    if setup_csv is not None:
        _add(setup_csv)
    else:
        _add(read_setup_value("local_ai", "local_models", ""))
    env_path = (
        "/etc/versa-agi/paths.env" if paths_env_path is None else paths_env_path
    )
    if env_path:
        try:
            with open(env_path, encoding="utf-8") as handle:
                for line in handle:
                    if line.startswith("VERSA_LOCAL_MODELS="):
                        _add(line.split("=", 1)[1].strip().strip('"'))
                        break
        except OSError:
            pass
    return keys


def _local_ctx_from_windows(raw: str, default_max: int = 4096) -> tuple[int, int]:
    if "," not in (raw or ""):
        return 0, default_max
    try:
        rec_s, max_s = raw.split(",")[0].strip(), raw.split(",")[1].strip()
        return int(rec_s or "0"), int(max_s or str(default_max))
    except ValueError:
        return 0, default_max


def fill_assigned_local_catalog(
    out: dict[str, dict],
    path: str,
    assigned: list[str] | set[str],
    *,
    gpu_backend: str | None = None,
) -> None:
    """Add assigned local keys that ``[catalog]`` / custom have not ingested yet.

    Prefers ``[catalog_library]``. If the library has no row (custom SYCL import),
    synthesize a local chat row from ``[local_models]`` / ``[sycl_models]`` labels
    and ``[context_windows]``.
    """
    if not assigned:
        return
    library = _read_raw_section(path, "catalog_library")
    labels = _read_raw_section(path, "local_models")
    windows = _read_raw_section(path, "context_windows")
    sycl = _read_raw_section(path, "sycl_models")
    provider = resolve_local_provider(gpu_backend)
    for key in assigned:
        if key in out:
            continue
        raw = library.get(key)
        if raw:
            parsed = parse_catalog_row(raw)
            if parsed:
                parsed["origin"] = "library"
                out[key] = parsed
                continue
        if key not in labels and key not in sycl:
            continue
        rec, mx = _local_ctx_from_windows(windows.get(key, ""))
        out[key] = {
            "class": "local",
            "provider": provider,
            "enabled": True,
            "coa": False,
            "ctx_recommended": rec,
            "ctx_max": mx,
            "work_modality": "local",
            "input_modalities": "text",
            "output_modalities": "text",
            "router_eligible": False,
            "label": labels.get(key, key),
            "origin": "local_assigned",
        }


def assigned_local_catalog_rows_to_upsert(
    path: str | None = None,
    *,
    assigned_local: list[str] | None = None,
    gpu_backend: str | None = None,
) -> list[tuple[str, str]]:
    """``[catalog]`` values for assigned local keys that are not in the live sections."""
    path = path or resolve_models_ini_path()
    have = set(_read_raw_section(path, "catalog")) | set(
        _read_raw_section(path, "catalog_custom")
    )
    keys = (
        list(assigned_local)
        if assigned_local is not None
        else assigned_local_model_keys()
    )
    filled: dict[str, dict] = {}
    fill_assigned_local_catalog(
        filled,
        path,
        [key for key in keys if key not in have],
        gpu_backend=gpu_backend,
    )
    return [(key, catalog_row_to_value(row)) for key, row in filled.items()]


def load_catalog(
    path: str | None = None,
    *,
    assigned_local: list[str] | set[str] | None = None,
) -> dict[str, dict]:
    """Load merged catalog (baseline + custom).

    Assigned local chat keys (``local_models`` / ``VERSA_LOCAL_MODELS``) that
    ``model migrate`` has not copied into ``[catalog]`` yet are filled from
    ``[catalog_library]`` or synthesized from local pipeline sections. Unused
    library rows stay out.
    """
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
    apply_catalog_overrides(out, path)
    keys = (
        list(assigned_local)
        if assigned_local is not None
        else assigned_local_model_keys()
    )
    fill_assigned_local_catalog(out, path, keys)
    return out


def apply_catalog_overrides(out: dict[str, dict], path: str) -> None:
    """Apply sparse [catalog_overrides] JSON patches onto resolved rows."""
    import json

    for key, raw in _read_raw_section(path, "catalog_overrides").items():
        if key not in out:
            continue
        try:
            patch = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(patch, dict):
            out[key].update(patch)
            out[key]["origin"] = "override"


def selected_catalog_keys(path: str | None = None) -> list[str]:
    path = path or resolve_models_ini_path()
    return [
        key for key, raw in _read_raw_section(path, "catalog_selected").items()
        if raw.strip().lower() not in ("false", "0", "no")
    ]


def collect_model_references(
    *,
    agents_db: str | None = None,
    paths_env: str | None = None,
    setup_ini: str | None = None,
) -> list[str]:
    """Catalog keys assigned or preferred anywhere on the site."""
    keys: list[str] = []
    seen: set[str] = set()

    def _add(raw: str | None) -> None:
        for key in parse_csv_keys(raw or ""):
            if key not in seen:
                seen.add(key)
                keys.append(key)

    setup_ini = setup_ini or SETUP_INI_CANONICAL
    _add(read_setup_value("gemini", "model", ""))
    for modality in WORK_MODALITIES:
        _add(read_setup_value("model_routing", modality, ""))

    env_path = "/etc/versa-agi/paths.env" if paths_env is None else paths_env
    if env_path and os.path.isfile(env_path):
        try:
            with open(env_path, encoding="utf-8") as handle:
                for line in handle:
                    if line.startswith("VERSA_DEFAULT_MODEL="):
                        _add(line.split("=", 1)[1].strip().strip('"'))
        except OSError:
            pass

    db = agents_db or os.environ.get("AGICTL_AGENTS_DB", "/var/lib/versa-agi/agents.db")
    if db and os.path.isfile(db):
        try:
            import sqlite3

            conn = sqlite3.connect(db, timeout=5)
            cols = [r[1] for r in conn.execute("PRAGMA table_info(agents)").fetchall()]
            for col in ("model", "triage_model"):
                if col in cols:
                    for (value,) in conn.execute(f"SELECT {col} FROM agents WHERE {col} IS NOT NULL"):
                        _add(value)
            conn.close()
        except Exception:
            pass
    return keys


def compute_live_catalog_keys(
    path: str | None = None,
    *,
    references: list[str] | None = None,
) -> list[str]:
    """Keys migrate should write into live [catalog] (minus [catalog_removed])."""
    from provider_registry import load_merged_providers
    from shipped_models import keys_for_provider

    path = path or resolve_models_ini_path()
    removed = set(_read_raw_section(path, "catalog_removed"))
    providers = load_merged_providers(path)
    out: list[str] = []
    seen: set[str] = set()

    def _add(key: str) -> None:
        if key and key not in seen and key not in removed:
            seen.add(key)
            out.append(key)

    for slug, row in providers.items():
        if not row.get("enabled"):
            continue
        if row.get("class") == "local":
            continue
        for key in keys_for_provider(slug, path):
            _add(key)
    for key in selected_catalog_keys(path):
        _add(key)
    for key in _read_raw_section(path, "catalog_custom"):
        _add(key)
    refs = collect_model_references() if references is None else references
    library = _read_raw_section(path, "catalog_library")
    custom = _read_raw_section(path, "catalog_custom")
    overrides = _read_raw_section(path, "catalog_overrides")
    for key in refs:
        if key in library or key in custom or key in overrides:
            _add(key)
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
