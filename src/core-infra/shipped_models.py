"""Neutral shipped selection — loaded from models.ini [shipped_models].

Offering order is file order. Each row maps a product label to catalog keys
per provider. COA eligibility is not stored here.
"""

from __future__ import annotations

import json
import os

from model_catalog import _read_raw_section, resolve_models_ini_path


def _stock_path() -> str:
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.abspath(os.path.join(here, "..", "models.ini.stock"))


def load_offerings(path: str | None = None) -> list[tuple[str, str, dict[str, str]]]:
    """Return [(offering_id, label, {provider: catalog_key}), ...] in picker order."""
    ini = path or resolve_models_ini_path()
    raw = _read_raw_section(ini, "shipped_models")
    if not raw and os.path.isfile(_stock_path()):
        raw = _read_raw_section(_stock_path(), "shipped_models")
    out: list[tuple[str, str, dict[str, str]]] = []
    for offering_id, value in raw.items():
        try:
            data = json.loads(value)
        except json.JSONDecodeError:
            continue
        label = str(data.get("label") or offering_id).strip()
        keys = {
            str(prov).strip(): str(key).strip()
            for prov, key in (data.get("keys") or {}).items()
            if str(prov).strip() and str(key).strip()
        }
        if keys:
            out.append((offering_id, label, keys))
    return out


def keys_for_provider(provider: str, path: str | None = None) -> list[str]:
    """Catalog keys to activate for one provider, in picker order."""
    return [keys[provider] for _, _, keys in load_offerings(path) if provider in keys]


def all_keys(path: str | None = None) -> list[str]:
    """Every shipped catalog key (native + OpenRouter), first-seen order."""
    out: list[str] = []
    seen: set[str] = set()
    for _, _, keys in load_offerings(path):
        for key in keys.values():
            if key not in seen:
                seen.add(key)
                out.append(key)
    return out


def recommended_pairs(provider: str, path: str | None = None) -> list[tuple[str, str]]:
    """(catalog_key, label) for a provider, picker order."""
    return [
        (keys[provider], label)
        for _, label, keys in load_offerings(path)
        if provider in keys
    ]
