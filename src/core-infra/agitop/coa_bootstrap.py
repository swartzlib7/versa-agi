"""COA first-login provider + model bootstrap helpers (WU-02 / WU-03).

Pure helpers used by ApiKeysModal (bootstrap mode) and agitop on_mount tripwire.
Persistence lives under /etc/versa-agi/ (version-safe JSON).
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

_CORE = Path(__file__).resolve().parents[1]
if str(_CORE) not in sys.path:
    sys.path.insert(0, str(_CORE))

# ── Persistence ─────────────────────────────────────────────────────────────
STATE_PATH = Path("/etc/versa-agi/coa_bootstrap.json")
PATHS_ENV = Path("/etc/versa-agi/paths.env")
AGENTS_DB = Path("/var/lib/versa-agi/agents.db")
SETUP_INI = Path("/etc/versa-agi/setup.ini")
COA_ENV = Path("/etc/versa-agi/coa.env")
GCP_VAULT = Path("/etc/versa-agi/vault/gcp-credentials.json")

# ── Recommended COA models per provider (locked product lists) ──────────────
# Values are catalog keys. Labels are for modal display.
# Source of truth: models.ini [shipped_models] via shipped_models.py.
from shipped_models import recommended_pairs  # noqa: E402
from shipped_models import load_offerings as _load_shipped_offerings  # noqa: E402

COA_SHIPPED = [(label, keys) for _, label, keys in _load_shipped_offerings()]

RECOMMENDED: dict[str, list[tuple[str, str]]] = {}
for _label, _keys in COA_SHIPPED:
    for _prov, _key in _keys.items():
        RECOMMENDED.setdefault(_prov, []).append((_key, _label))

PROVIDER_LABELS = {
    "google": "Google",
    "xai": "xAI",
    "openai": "OpenAI",
    "anthropic": "Anthropic",
    "openrouter": "OpenRouter",
}

# Bootstrap Step A chip → set-key slug
PROVIDER_SETKEY = {
    "google": "gemini",
    "xai": "xai",
    "openai": "openai",
    "anthropic": "anthropic",
    "openrouter": "openrouter",
}


def recommended_keys(provider: str, path: str | None = None) -> list[str]:
    """Catalog keys Recommended for COA for a provider slug."""
    return [k for k, _ in recommended_pairs(provider, path)]


def recommended_options(provider: str) -> list[tuple[str, str]]:
    """(label, catalog_key) for Select widgets.

    Only keys that are already in the live catalog — never offer a
    Recommended row the harness cannot route.
    """
    from model_catalog import format_catalog_picker_label

    prov = PROVIDER_LABELS.get(provider, provider)
    items = recommended_pairs(provider)
    try:
        from model_catalog import load_catalog
        cat = load_catalog() or {}
    except Exception:
        cat = {}
    if cat is not None:
        items = [
            (key, label)
            for key, label in items
            if key in cat and (cat.get(key) or {}).get("coa")
        ]
    return [
        (format_catalog_picker_label(prov, label, key), key)
        for key, label in items
    ]


def all_recommended_keys() -> set[str]:
    keys: set[str] = set()
    for items in RECOMMENDED.values():
        keys.update(k for k, _ in items)
    return keys


# ── State I/O ───────────────────────────────────────────────────────────────
def load_bootstrap_state(path: Path | None = None) -> dict:
    p = path or STATE_PATH
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def save_bootstrap_state(state: dict, path: Path | None = None) -> None:
    p = path or STATE_PATH
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(p)


def mark_bootstrap_done(path: Path | None = None) -> None:
    state = load_bootstrap_state(path)
    state["done"] = True
    state.pop("remind_later", None)
    save_bootstrap_state(state, path)


def mark_bootstrap_remind_later(path: Path | None = None) -> None:
    state = load_bootstrap_state(path)
    state["remind_later"] = True
    state["done"] = False
    save_bootstrap_state(state, path)


def is_bootstrap_done(path: Path | None = None) -> bool:
    return bool(load_bootstrap_state(path).get("done"))


def is_remind_later(path: Path | None = None) -> bool:
    return bool(load_bootstrap_state(path).get("remind_later"))


# ── Environment probes ──────────────────────────────────────────────────────
def _read_paths_env(key: str, default: str = "", paths_env: Path | None = None) -> str:
    p = paths_env or PATHS_ENV
    try:
        with p.open(encoding="utf-8") as f:
            for line in f:
                if line.startswith(f"{key}="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    except OSError:
        pass
    return default


def gemini_enabled(
    setup_ini: Path | None = None,
    models_ini: Path | None = None,
) -> bool | None:
    """Return True/False when Google is site-enabled; None when unset.

    ``setup_ini`` is unused (legacy Google toggle left setup.ini). Kept so
    callers that still pass it do not break.
    """
    del setup_ini
    try:
        from model_catalog import resolve_models_ini_path
        from provider_registry import _read_raw_section, site_enabled_slugs
        path = str(models_ini) if models_ini is not None else resolve_models_ini_path()
        site = _read_raw_section(path, "providers_site")
        if "enabled" in site:
            return "google" in site_enabled_slugs(path)
    except Exception:
        pass
    return None


def gemini_credentials_present(
    *,
    coa_env: Path | None = None,
    vault: Path | None = None,
) -> bool:
    coa = coa_env or COA_ENV
    try:
        if coa.is_file():
            for line in coa.read_text(encoding="utf-8").splitlines():
                if line.strip().startswith("GEMINI_API_KEY="):
                    val = line.split("=", 1)[1].strip().strip('"').strip("'")
                    if val and not val.startswith("#"):
                        return True
    except OSError:
        pass
    return (vault or GCP_VAULT).is_file()


def gemini_usable(
    *,
    coa_env: Path | None = None,
    vault: Path | None = None,
    setup_ini: Path | None = None,
    models_ini: Path | None = None,
) -> bool:
    """True when Gemini credentials exist and provider is enabled.

    Legacy installs with no ``[providers_site] enabled=`` key: credentials
    alone count as usable.
    """
    if not gemini_credentials_present(coa_env=coa_env, vault=vault):
        return False
    flag = gemini_enabled(setup_ini=setup_ini, models_ini=models_ini)
    if flag is None:
        return True
    return flag


def usable_providers(
    *,
    setup_ini: Path | None = None,
    coa_env: Path | None = None,
    vault: Path | None = None,
) -> list[str]:
    """Catalog provider slugs that can run COA models (keyed; Gemini also enabled)."""
    out: list[str] = []
    try:
        from provider_registry import load_merged_providers
        for slug, row in load_merged_providers().items():
            if row.get("class") != "local" and row.get("enabled"):
                out.append(slug)
    except Exception:
        pass
    if gemini_usable(coa_env=coa_env, vault=vault, setup_ini=setup_ini) and "google" not in out:
        out.append("google")
    try:
        from provider_catalog import configured_providers
        keyed = set(configured_providers())
    except Exception:
        keyed = set()
    for slug in ("xai", "openai", "anthropic", "openrouter"):
        if slug in keyed and slug not in out:
            out.append(slug)

    seen: set[str] = set()
    ordered: list[str] = []
    for s in out:
        if s not in seen:
            seen.add(s)
            ordered.append(s)
    return ordered


def _model_in_live_catalog(model: str) -> bool:
    """True when ``model`` is in the merged live catalog.

    On catalog-load failure, return True so a transient read error does not
    force bootstrap in a loop. An empty successful load still returns False
    for a missing key.
    """
    if not model:
        return False
    try:
        from model_catalog import load_catalog
        cat = load_catalog()
    except Exception:
        return True
    if cat is None:
        return True
    return model in cat


COA_HOLD_STATUS = "invalid_config"
COA_HOLD_MESSAGE = "Assign a COA model via first-login setup"


def hold_coa_without_model(con: sqlite3.Connection) -> bool:
    """Mark COA unspawnable when it has no assigned model.

    COA is protected, so ``inactive=1`` is not allowed (Lifeline
    ``ensure-protected`` would clear it). ``invalid_config`` is the hold
    Lifeline already understands; first-login assign / set-model clears it.
    """
    cols = {row[1] for row in con.execute("PRAGMA table_info(agents)")}
    if "status" not in cols:
        return False
    row = con.execute(
        "SELECT model, status FROM agents WHERE name='coa' LIMIT 1"
    ).fetchone()
    if not row:
        return False
    if (row[0] or "").strip():
        return False
    status = (row[1] or "").strip()
    if status in ("circuit_breaker", "halted", COA_HOLD_STATUS):
        return False
    con.execute(
        "UPDATE agents SET status=?, status_message=?, updated_at=datetime('now') "
        "WHERE name='coa'",
        (COA_HOLD_STATUS, COA_HOLD_MESSAGE),
    )
    return True


def heal_coa_assignment(
    *,
    agents_db: Path | None = None,
    fresh_install: bool = False,
) -> dict:
    """After catalog migrate: keep COA on a live key and cloud Auto ctx.

    - Fresh install + model missing from catalog → clear model (bootstrap assigns).
    - Empty or cleared COA model → hold spawn until first-login assign.
    - Cloud catalog row (recommended 0) stuck on the 4K local default → num_ctx=0.
    Does not rewrite a deliberate non-4096 window on ``--update``.
    """
    db = agents_db or AGENTS_DB
    result: dict = {"changed": False, "actions": [], "model": ""}
    if not db.is_file():
        result["actions"].append("no_agents_db")
        return result
    try:
        con = sqlite3.connect(str(db), timeout=5)
        try:
            row = con.execute(
                "SELECT model, num_ctx FROM agents WHERE name='coa' LIMIT 1"
            ).fetchone()
            if not row:
                result["actions"].append("no_coa_row")
                return result
            model = (row[0] or "").strip()
            try:
                num_ctx = int(row[1] or 0)
            except (TypeError, ValueError):
                num_ctx = 0
            result["model"] = model
            if not model:
                result["actions"].append("coa_model_empty")
                if hold_coa_without_model(con):
                    con.commit()
                    result["changed"] = True
                    result["actions"].append("held_pending_model")
                return result
            in_cat = _model_in_live_catalog(model)
            if not in_cat:
                if fresh_install:
                    con.execute(
                        "UPDATE agents SET model='', num_ctx=0, "
                        "updated_at=datetime('now') WHERE name='coa'"
                    )
                    result["changed"] = True
                    result["actions"].append("cleared_missing_catalog_model")
                    result["model"] = ""
                    if hold_coa_without_model(con):
                        result["actions"].append("held_pending_model")
                    con.commit()
                else:
                    result["actions"].append("missing_catalog_model")
                return result
            try:
                from harness.model_context import get_model_context
                recommended, _ = get_model_context(model)
            except Exception:
                return result
            if recommended == 0 and num_ctx == 4096:
                con.execute(
                    "UPDATE agents SET num_ctx=0, updated_at=datetime('now') "
                    "WHERE name='coa'"
                )
                con.commit()
                result["changed"] = True
                result["actions"].append("reset_cloud_num_ctx_auto")
            else:
                result["actions"].append("ok")
        finally:
            con.close()
    except sqlite3.Error as exc:
        result["actions"].append(f"db_error:{exc}")
    return result


def _coa_model(agents_db: Path | None = None) -> str:
    db = agents_db or AGENTS_DB
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=2)
        try:
            row = con.execute(
                "SELECT model FROM agents WHERE name='coa' LIMIT 1"
            ).fetchone()
            return (row[0] or "").strip() if row else ""
        finally:
            con.close()
    except Exception:
        return ""


def provider_for_model(model: str) -> str:
    """Best-effort catalog provider for a model key."""
    if not model:
        return ""
    for prov, items in RECOMMENDED.items():
        if any(k == model for k, _ in items):
            return prov
    if model.startswith("gemini-"):
        return "google"
    if model.startswith("grok-"):
        return "xai"
    if model.startswith("gpt-"):
        return "openai"
    if model.startswith("claude-"):
        return "anthropic"
    if "/" in model:
        return "openrouter"
    return ""


def needs_coa_bootstrap(
    *,
    state_path: Path | None = None,
    agents_db: Path | None = None,
    paths_env: Path | None = None,
    setup_ini: Path | None = None,
    coa_env: Path | None = None,
    vault: Path | None = None,
) -> bool:
    """True when first-login bootstrap should run.

    False when COA has an explicit model on a keyed provider (healthy),
    regardless of the done flag. Remind-later does not clear need.
    """
    usable = usable_providers(setup_ini=setup_ini, coa_env=coa_env, vault=vault)
    coa_model = _coa_model(agents_db)
    default_model = _read_paths_env("VERSA_DEFAULT_MODEL", "", paths_env=paths_env)

    # Healthy: explicit COA model on a keyed provider *and* in the live catalog.
    # Setup can sqlite-assign a Recommended key before migrate injects it;
    # that must not count as done (harness resolve_provider_route will fail).
    if coa_model:
        prov = provider_for_model(coa_model)
        if prov and prov in usable and _model_in_live_catalog(coa_model):
            return False
        # Explicit model but provider not keyed, or key not in catalog
        return True

    # No usable provider → always need bootstrap
    if not usable:
        return True

    # Empty COA model — fall through to system default
    if not default_model:
        return True
    prov = provider_for_model(default_model)
    if not prov or prov not in usable:
        return True

    # Default is on a keyed provider and COA inherits it — still nudge once
    # unless already marked done (user completed bootstrap earlier).
    return not is_bootstrap_done(state_path)


def should_auto_prompt_bootstrap(**kwargs) -> bool:
    """Tripwire: need bootstrap and not dismissed via remind-later."""
    if not needs_coa_bootstrap(**kwargs):
        return False
    if is_remind_later(kwargs.get("state_path")):
        return False
    return True


def should_show_remind_banner(**kwargs) -> bool:
    """System panel banner while remind-later and still needs bootstrap."""
    return is_remind_later(kwargs.get("state_path")) and needs_coa_bootstrap(**kwargs)


def sync_system_default_model(
    model: str,
    *,
    paths_env: Path | None = None,
    setup_ini: Path | None = None,
) -> list[str]:
    """Write VERSA_DEFAULT_MODEL + setup.ini [system] model=. Returns updated paths."""
    updated: list[str] = []
    model = (model or "").strip()
    paths = paths_env or PATHS_ENV
    if paths.is_file() or paths.parent.is_dir():
        try:
            lines = paths.read_text(encoding="utf-8").splitlines() if paths.is_file() else []
            out, found = [], False
            for line in lines:
                if line.startswith("VERSA_DEFAULT_MODEL="):
                    out.append(f'VERSA_DEFAULT_MODEL="{model}"')
                    found = True
                else:
                    out.append(line)
            if not found:
                out.append(f'VERSA_DEFAULT_MODEL="{model}"')
            paths.parent.mkdir(parents=True, exist_ok=True)
            paths.write_text("\n".join(out) + "\n", encoding="utf-8")
            updated.append(str(paths))
        except OSError:
            pass

    setup = setup_ini or SETUP_INI
    if setup.is_file():
        try:
            lines = setup.read_text(encoding="utf-8").splitlines()
            section = None
            replaced = False
            for i, line in enumerate(lines):
                s = line.strip()
                if s.startswith("[") and s.endswith("]"):
                    section = s[1:-1]
                    continue
                if section == "system" and s.startswith("model="):
                    lines[i] = f"model={model}"
                    replaced = True
                    break
            if replaced:
                setup.write_text("\n".join(lines) + "\n", encoding="utf-8")
                updated.append(str(setup))
        except OSError:
            pass
    return updated


def assign_coa_model(model: str) -> tuple[bool, str]:
    """Run ``agictl agent set-model coa <model>`` and sync system default.

    Returns (ok, message).
    """
    import json as _json
    import subprocess

    model = (model or "").strip()
    if not model:
        return False, "Model required"
    try:
        proc = subprocess.run(
            ["sudo", "agictl", "agent", "set-model", "coa", model],
            capture_output=True,
            text=True,
            timeout=30,
        )
        result = _json.loads(proc.stdout) if proc.stdout.strip() else {}
        if not result.get("success"):
            err = result.get("error") or proc.stderr.strip() or "set-model failed"
            return False, err
    except Exception as exc:
        return False, str(exc)

    sync_system_default_model(model)
    mark_bootstrap_done()
    return True, f"COA model set to {model}"


def read_role_model(role_ini_path: str | Path) -> str:
    """Read [model] model= from a role.ini (blank = inherit system default)."""
    import configparser

    cfg = configparser.ConfigParser()
    try:
        cfg.read(role_ini_path)
    except Exception:
        return ""
    if cfg.has_option("model", "model"):
        return cfg.get("model", "model").strip()
    return ""
