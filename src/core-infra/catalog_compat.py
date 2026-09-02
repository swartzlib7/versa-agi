"""Idempotent conversion of legacy setup lists and whole-row custom snapshots."""

from __future__ import annotations

import configparser
import json
import os

from model_catalog import (
    catalog_row_to_value,
    parse_catalog_row,
    parse_csv_keys,
    resolve_models_ini_path,
)
from shipped_models import all_keys as shipped_all_keys

GENERIC_IMPORT_PARAMS = (
    '{"reasoning_effort":"none","allowed_reasoning_efforts":["none","minimal","low","medium","high","max"]}',
    '{"reasoning_effort":"none","allowed_reasoning_efforts":["none","minimal","low","medium","high","xhigh"]}',
)

_LEGACY_LISTS = (
    ("third_party", "google_models", "google"),
    ("third_party", "xai_models", "xai"),
    ("third_party", "openai_models", "openai"),
    ("third_party", "anthropic_models", "anthropic"),
    ("third_party", "openrouter_models", "openrouter"),
)


def _cfg(path: str) -> configparser.ConfigParser:
    cfg = configparser.ConfigParser(delimiters=("=",), strict=False)
    cfg.optionxform = str
    if path and os.path.isfile(path):
        cfg.read(path)
    return cfg


def _get(cfg: configparser.ConfigParser, section: str, key: str, default: str = "") -> str:
    if cfg.has_option(section, key):
        return cfg.get(section, key, fallback=default).strip()
    return default


def _truthy(raw: str) -> bool:
    return raw.strip().lower() in ("true", "1", "yes", "on")


def collect_legacy_enabled(setup_path: str) -> list[str]:
    cfg = _cfg(setup_path)
    slugs: list[str] = []
    for slug in ("google", "xai", "openai", "anthropic", "openrouter"):
        if _truthy(_get(cfg, "third_party", f"{slug}_enabled")):
            slugs.append(slug)
    out: list[str] = []
    seen: set[str] = set()
    for slug in slugs:
        if slug not in seen:
            seen.add(slug)
            out.append(slug)
    return out


def collect_legacy_list_keys(setup_path: str) -> dict[str, list[str]]:
    cfg = _cfg(setup_path)
    by_provider: dict[str, list[str]] = {}
    for section, key, slug in _LEGACY_LISTS:
        items = parse_csv_keys(_get(cfg, section, key))
        if items:
            by_provider.setdefault(slug, [])
            for item in items:
                if item not in by_provider[slug]:
                    by_provider[slug].append(item)
    return by_provider


def _row_diff(old: dict, base: dict) -> dict:
    skip = {"origin"}
    out = {}
    for key, value in old.items():
        if key in skip:
            continue
        if base.get(key) != value:
            out[key] = value
    return out


def _ensure_section_lines(
    path: str,
    section: str,
    pairs: dict[str, str],
    *,
    spaced: bool = True,
) -> None:
    """Append or replace keys in a section without rewriting the whole file."""
    if not path or not os.path.isfile(path) or not pairs:
        return

    def _fmt(key: str, value: str) -> str:
        return f"{key} = {value}\n" if spaced else f"{key}={value}\n"

    with open(path, encoding="utf-8") as handle:
        lines = handle.readlines()
    header = f"[{section}]\n"
    if not any(line.strip() == f"[{section}]" for line in lines):
        if lines and not lines[-1].endswith("\n"):
            lines.append("\n")
        lines.append("\n" + header)
    in_sec = False
    present: set[str] = set()
    out: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            if in_sec:
                for key, value in pairs.items():
                    if key not in present:
                        out.append(_fmt(key, value))
            in_sec = stripped == f"[{section}]"
            out.append(line)
            continue
        if in_sec and "=" in stripped and not stripped.startswith("#"):
            key = stripped.split("=", 1)[0].strip()
            if key in pairs:
                present.add(key)
                out.append(_fmt(key, pairs[key]))
                continue
        out.append(line)
    if in_sec:
        for key, value in pairs.items():
            if key not in present:
                out.append(_fmt(key, value))
    with open(path, "w", encoding="utf-8") as handle:
        handle.writelines(out)


_GEMINI_SPLIT_MOVES = (
    ("mode", "system", "mode"),
    ("model", "system", "model"),
    ("api_key", "third_party", "google_api_key"),
    ("auth_method", "gcp", "auth_method"),
    ("enabled", "third_party", "google_enabled"),
    ("cloud_models", "third_party", "google_models"),
)


def _drop_ini_section(path: str, section: str) -> bool:
    """Remove ``[section]`` and its body. Returns True when a header was dropped."""
    with open(path, encoding="utf-8") as handle:
        lines = handle.readlines()
    out: list[str] = []
    in_sec = False
    dropped = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            in_sec = stripped == f"[{section}]"
            if in_sec:
                dropped = True
                continue
        if in_sec:
            continue
        out.append(line)
    if dropped:
        with open(path, "w", encoding="utf-8") as handle:
            handle.writelines(out)
    return dropped


def migrate_gemini_section_split(setup_path: str) -> dict:
    """One-shot [gemini] split into [system] / [third_party] / [gcp].

    Move a key only when the target is absent or empty, then delete [gemini]
    wholesale. Idempotent: a site that already has [system] and no [gemini]
    is a no-op. Transient google_enabled / google_models land on slots
    collect_legacy_enabled / _LEGACY_LISTS already read.
    """
    stats = {"moved": [], "skipped": [], "deleted": False}
    if not setup_path or not os.path.isfile(setup_path):
        return stats
    cfg = _cfg(setup_path)
    if not cfg.has_section("gemini"):
        return stats

    to_write: dict[str, dict[str, str]] = {}
    for src_key, dest_sec, dest_key in _GEMINI_SPLIT_MOVES:
        src = _get(cfg, "gemini", src_key)
        if not src:
            continue
        dest = _get(cfg, dest_sec, dest_key)
        if dest:
            stats["skipped"].append(f"{dest_sec}.{dest_key}")
            continue
        to_write.setdefault(dest_sec, {})[dest_key] = src
        stats["moved"].append(f"{dest_sec}.{dest_key}")

    for dest_sec, pairs in to_write.items():
        _ensure_section_lines(setup_path, dest_sec, pairs, spaced=False)

    stats["deleted"] = _drop_ini_section(setup_path, "gemini")
    return stats


def migrate_legacy_site_state(
    *,
    setup_path: str,
    models_path: str | None = None,
) -> dict:
    """Carry legacy activation/lists/custom snapshots into site overlays.

    Idempotent. Safe to run before stock reconcile overwrites setup lists.
    """
    models_path = models_path or resolve_models_ini_path()
    stats = {
        "providers_enabled": [],
        "selected": [],
        "overrides": [],
        "params_cleared": [],
    }
    if not os.path.isfile(models_path):
        return stats

    cfg = _cfg(models_path)
    shipped = set(shipped_all_keys(models_path))
    library: dict[str, dict] = {}
    if cfg.has_section("catalog_library"):
        for key, raw in cfg.items("catalog_library"):
            parsed = parse_catalog_row(raw)
            if parsed:
                library[key.strip()] = parsed

    enabled = collect_legacy_enabled(setup_path)
    existing_site = parse_csv_keys(_get(cfg, "providers_site", "enabled"))
    merged_enabled = list(dict.fromkeys([*existing_site, *enabled]))
    if merged_enabled:
        _ensure_section_lines(models_path, "providers_site", {"enabled": ",".join(merged_enabled)})
        stats["providers_enabled"] = merged_enabled

    selected: dict[str, str] = {}
    if cfg.has_section("catalog_selected"):
        for key, raw in cfg.items("catalog_selected"):
            if key.strip() and not key.startswith("#"):
                selected[key.strip()] = raw.strip() or "true"
    for _slug, keys in collect_legacy_list_keys(setup_path).items():
        for key in keys:
            if key in shipped:
                continue
            if key in library:
                selected[key] = "true"
    if selected:
        _ensure_section_lines(models_path, "catalog_selected", selected)
        stats["selected"] = list(selected)

    overrides: dict[str, str] = {}
    if cfg.has_section("catalog_overrides"):
        for key, raw in cfg.items("catalog_overrides"):
            if key.strip():
                overrides[key.strip()] = raw.strip()
    drop_custom: list[str] = []
    if cfg.has_section("catalog_custom"):
        for key, raw in cfg.items("catalog_custom"):
            k = key.strip()
            if k not in library:
                continue
            parsed = parse_catalog_row(raw)
            if not parsed:
                continue
            diff = _row_diff(parsed, library[k])
            drop_custom.append(k)
            if diff:
                overrides[k] = json.dumps(diff, separators=(",", ":"))
            selected[k] = "true"
    if selected:
        _ensure_section_lines(models_path, "catalog_selected", selected)
    if overrides:
        _ensure_section_lines(models_path, "catalog_overrides", overrides)
        stats["overrides"] = list(overrides)

    if drop_custom and cfg.has_section("catalog_custom"):
        with open(models_path, encoding="utf-8") as handle:
            lines = handle.readlines()
        out = []
        in_custom = False
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("[") and stripped.endswith("]"):
                in_custom = stripped == "[catalog_custom]"
                out.append(line)
                continue
            if in_custom and "=" in stripped and not stripped.startswith("#"):
                if stripped.split("=", 1)[0].strip() in drop_custom:
                    continue
            out.append(line)
        with open(models_path, "w", encoding="utf-8") as handle:
            handle.writelines(out)

    cleared = []
    if cfg.has_section("model_params_custom"):
        with open(models_path, encoding="utf-8") as handle:
            lines = handle.readlines()
        out = []
        in_sec = False
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("[") and stripped.endswith("]"):
                in_sec = stripped == "[model_params_custom]"
                out.append(line)
                continue
            if in_sec and "=" in stripped and not stripped.startswith("#"):
                key, val = [p.strip() for p in stripped.split("=", 1)]
                compact = val.replace(" ", "")
                if compact in {item.replace(" ", "") for item in GENERIC_IMPORT_PARAMS}:
                    catalog_key = key[6:] if key.startswith("model:") else key
                    if catalog_key in library:
                        cleared.append(key)
                        continue
            out.append(line)
        with open(models_path, "w", encoding="utf-8") as handle:
            handle.writelines(out)
        stats["params_cleared"] = cleared

    if cfg.has_section("providers_custom"):
        presets = {}
        if cfg.has_section("provider_library"):
            from provider_registry import parse_provider_library_row
            for slug, raw in cfg.items("provider_library"):
                presets[slug.strip()] = parse_provider_library_row(slug.strip(), raw)
        provider_overrides = {}
        provider_custom = {}
        for slug, raw in cfg.items("providers_custom"):
            slug = slug.strip()
            parts = [p.strip() for p in raw.split("|")]
            if slug in presets:
                patch = {}
                if len(parts) > 1 and parts[1] and parts[1] != presets[slug].get("label"):
                    patch["label"] = parts[1]
                if len(parts) > 2 and parts[2] and parts[2] != presets[slug].get("cls"):
                    patch["cls"] = parts[2]
                if patch:
                    provider_overrides[slug] = json.dumps(patch, separators=(",", ":"))
            else:
                provider_custom[slug] = raw.strip()
        if provider_overrides:
            _ensure_section_lines(models_path, "provider_overrides", provider_overrides)
        if provider_custom:
            _ensure_section_lines(models_path, "provider_custom", provider_custom)

    return stats


def _section_keys(cfg: configparser.ConfigParser, section: str) -> set[str]:
    if not cfg.has_section(section):
        return set()
    return {key.strip() for key, _ in cfg.items(section) if key.strip() and not key.startswith("#")}


def snapshot_vanishing_presets(
    *,
    models_path: str,
    template_path: str,
) -> list[str]:
    """Copy referenced/customized library rows that the next stock replace will drop.

    Must run before reconcile overwrites ``[catalog_library]``. Untouched
    unreferenced keys are not snapshotted and retire with the release.
    """
    if not os.path.isfile(models_path) or not os.path.isfile(template_path):
        return []
    current = _cfg(models_path)
    incoming = _cfg(template_path)
    old_lib: dict[str, str] = {}
    if current.has_section("catalog_library"):
        for key, raw in current.items("catalog_library"):
            if key.strip() and not key.startswith("#"):
                old_lib[key.strip()] = raw.strip()
    new_lib = _section_keys(incoming, "catalog_library")
    vanishing = [key for key in old_lib if key not in new_lib]
    if not vanishing:
        return []

    from model_catalog import collect_model_references, selected_catalog_keys

    keep = set(collect_model_references())
    keep.update(selected_catalog_keys(models_path))
    keep.update(_section_keys(current, "catalog_overrides"))
    keep.update(_section_keys(current, "catalog_custom"))
    if current.has_section("model_params_custom"):
        for key in _section_keys(current, "model_params_custom"):
            keep.add(key[6:] if key.startswith("model:") else key)

    already_custom = _section_keys(current, "catalog_custom")
    snapshotted: list[str] = []
    custom_pairs: dict[str, str] = {}
    params_pairs: dict[str, str] = {}
    for key in vanishing:
        if key not in keep or key in already_custom:
            continue
        raw = old_lib[key]
        parsed = parse_catalog_row(raw)
        if not parsed:
            continue
        if current.has_option("catalog_overrides", key):
            try:
                parsed.update(json.loads(current.get("catalog_overrides", key)))
            except json.JSONDecodeError:
                pass
        custom_pairs[key] = catalog_row_to_value(parsed)
        param_key = f"model:{key}"
        if current.has_option("model_params", param_key) and not current.has_option(
            "model_params_custom", param_key
        ):
            params_pairs[param_key] = current.get("model_params", param_key).strip()
        snapshotted.append(key)
    if custom_pairs:
        _ensure_section_lines(models_path, "catalog_custom", custom_pairs)
    if params_pairs:
        _ensure_section_lines(models_path, "model_params_custom", params_pairs)
    return snapshotted
