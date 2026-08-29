"""System provider presets + site activation + auth adapters.

Generic catalog/runtime code asks an adapter whether credentials exist.
No caller should branch on ``provider == "google"``.
"""

from __future__ import annotations

import json
import os

import configparser

from model_catalog import read_setup_value, resolve_models_ini_path


def _read_raw_section(path: str, section: str) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path or not os.path.isfile(path):
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

COA_ENV = "/etc/versa-agi/coa.env"
GCP_VAULT = "/etc/versa-agi/vault/gcp-credentials.json"
PROVIDER_KEYS_ENV = "/etc/versa-agi/provider_keys.env"

_KEY_ENV_VAR = {
    "google": "GEMINI_API_KEY",
    "xai": "XAI_API_KEY",
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
}

_KEY_SETUP_INI = {
    "google": ("gemini", "api_key"),
    "xai": ("third_party", "xai_api_key"),
    "openai": ("third_party", "openai_api_key"),
    "anthropic": ("third_party", "anthropic_api_key"),
    "openrouter": ("third_party", "openrouter_api_key"),
}


def _stock_path() -> str:
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.abspath(os.path.join(here, "..", "models.ini.stock"))


def _read_library(path: str) -> dict[str, str]:
    raw = _read_raw_section(path, "provider_library")
    if raw:
        return raw
    stock = _stock_path()
    if os.path.isfile(stock) and os.path.realpath(stock) != os.path.realpath(path):
        return _read_raw_section(stock, "provider_library")
    return {}


def parse_provider_library_row(slug: str, raw: str) -> dict:
    parts = [p.strip() for p in (raw or "").split("|")]
    while len(parts) < 6:
        parts.append("")
    return {
        "slug": slug,
        "class": parts[0] or "cloud",
        "label": parts[1] or slug,
        "cls": parts[2],
        "transport": parts[3] or "direct",
        "auth_adapter": parts[4] or "openai_key",
        "endpoint": parts[5],
        "enabled": False,
        "origin": "library",
    }


def provider_row_to_value(row: dict) -> str:
    return (
        f"{'true' if row.get('enabled') else 'false'}|"
        f"{row.get('label') or row.get('slug') or ''}|"
        f"{row.get('cls') or ''}"
    )


def _read_env_file_key(path: str, env_var: str) -> str:
    if not path or not os.path.isfile(path):
        return ""
    try:
        with open(path, encoding="utf-8") as handle:
            for line in handle:
                if line.startswith(f"{env_var}="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    except OSError:
        return ""
    return ""


def _adapter_google_gemini(_slug: str) -> bool:
    if (os.environ.get("GEMINI_API_KEY") or "").strip():
        return True
    val = _read_env_file_key(COA_ENV, "GEMINI_API_KEY")
    if val and not val.startswith("#"):
        return True
    if os.path.isfile(GCP_VAULT):
        return True
    return bool(read_setup_value("gemini", "api_key", "").strip())


def _adapter_api_key(slug: str) -> bool:
    env_var = _KEY_ENV_VAR.get(slug)
    if env_var and (os.environ.get(env_var) or "").strip():
        return True
    if env_var:
        for path in (PROVIDER_KEYS_ENV, os.path.join(os.path.dirname(__file__), "config", "provider_keys.env")):
            val = _read_env_file_key(path, env_var)
            if val:
                return True
    section, option = _KEY_SETUP_INI.get(slug, ("", ""))
    return bool(section and read_setup_value(section, option, "").strip())


def _adapter_local(_slug: str) -> bool:
    return read_setup_value("local_ai", "enabled", "false").strip().lower() == "true"


AUTH_ADAPTERS = {
    "google_gemini": _adapter_google_gemini,
    "openai_key": _adapter_api_key,
    "anthropic_key": _adapter_api_key,
    "local": _adapter_local,
}


def credentials_present(row: dict) -> bool:
    adapter = AUTH_ADAPTERS.get(row.get("auth_adapter") or "")
    if not adapter:
        return False
    return bool(adapter(row.get("slug") or ""))


def parse_enabled_csv(raw: str) -> list[str]:
    return [part.strip() for part in (raw or "").split(",") if part.strip()]


def site_enabled_slugs(path: str | None = None) -> list[str]:
    path = path or resolve_models_ini_path()
    site = _read_raw_section(path, "providers_site")
    if "enabled" in site:
        return parse_enabled_csv(site.get("enabled", ""))
    return []


def set_site_enabled(path: str, slug: str, enabled: bool) -> None:
    current = site_enabled_slugs(path)
    slug = (slug or "").strip()
    if not slug:
        return
    if enabled and slug not in current:
        current.append(slug)
    if not enabled:
        current = [item for item in current if item != slug]
    from catalog_compat import _ensure_section_lines
    _ensure_section_lines(path, "providers_site", {"enabled": ",".join(current)})


def _apply_json_override(row: dict, raw: str) -> dict:
    try:
        patch = json.loads(raw)
    except json.JSONDecodeError:
        return row
    if not isinstance(patch, dict):
        return row
    out = dict(row)
    for key, value in patch.items():
        out[key] = value
    return out


def load_provider_presets(path: str | None = None) -> dict[str, dict]:
    """Merged provider cards (library + custom + sparse overrides). Enabled is unset."""
    path = path or resolve_models_ini_path()
    out: dict[str, dict] = {}
    for slug, raw in _read_library(path).items():
        out[slug] = parse_provider_library_row(slug, raw)
    for slug, raw in _read_raw_section(path, "provider_custom").items():
        parts = [p.strip() for p in raw.split("|")]
        if len(parts) >= 6 and parts[3] and parts[4]:
            row = parse_provider_library_row(slug, raw)
        else:
            row = parse_provider_library_row(
                slug,
                f"cloud|{parts[1] if len(parts) > 1 else slug}|{parts[2] if len(parts) > 2 else ''}|openai_compat|openai_key|",
            )
            if parts:
                row["enabled"] = parts[0].lower() == "true"
        row["origin"] = "custom"
        out[slug] = row
    for slug, raw in _read_raw_section(path, "provider_overrides").items():
        if slug in out:
            out[slug] = _apply_json_override(out[slug], raw)
            out[slug]["origin"] = "override"
    return out


def load_merged_providers(path: str | None = None) -> dict[str, dict]:
    """Provider cards with generated ``enabled`` (site activation ∩ credentials)."""
    path = path or resolve_models_ini_path()
    out = load_provider_presets(path)
    enabled = set(site_enabled_slugs(path))
    live = _read_raw_section(path, "providers")
    legacy_custom = _read_raw_section(path, "providers_custom")

    if not out:
        def _parse_legacy(slug: str, raw: str, origin: str) -> None:
            parts = [p.strip() for p in raw.split("|")]
            out[slug] = {
                "slug": slug,
                "enabled": parts[0].lower() == "true" if parts else False,
                "label": parts[1] if len(parts) > 1 else slug,
                "cls": parts[2] if len(parts) > 2 else "",
                "class": "local" if slug in ("ollama", "llamacpp", "local_media") else "cloud",
                "transport": "direct" if slug == "google" else "openai_compat",
                "auth_adapter": "google_gemini" if slug == "google" else (
                    "local" if slug in ("ollama", "llamacpp", "local_media") else
                    "anthropic_key" if slug == "anthropic" else "openai_key"
                ),
                "endpoint": "",
                "origin": origin,
            }

        for slug, raw in live.items():
            _parse_legacy(slug, raw, "baseline")
        for slug, raw in legacy_custom.items():
            _parse_legacy(slug, raw, "override" if slug in out else "custom")
        return out

    for slug, row in out.items():
        if row.get("class") == "local":
            row["enabled"] = credentials_present(row)
        else:
            row["enabled"] = slug in enabled and credentials_present(row)
    return out
