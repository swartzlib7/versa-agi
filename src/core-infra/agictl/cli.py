#!/usr/bin/env python3
"""
agictl — Versa AGi CLI (V3)
Data-model-driven command groups: system, agent, task, message, cycle, project, connection.
All effectful commands return JSON confirming what occurred.
"""

import os
import sys
import json
import time
import sqlite3
import shutil
import subprocess
import click
from rich.console import Console
from rich.table import Table

# Add core-infra to path for data readers
sys.path.append(os.path.dirname(os.path.dirname(__file__)))
from agitop.data import AgentReader, MessageReader, TasksReader
from identity import provision_identity
from comms import fetch_inbox, send_message, mark_message_processed, build_attachments
from model_catalog import (
    WORK_MODALITIES,
    OUTPUT_DELIVERY_MODALITIES,
    catalog_row_to_value,
    load_catalog as _mc_load_catalog,
    parse_catalog_row,
    validate_model_routing_prefs,
    validate_preferred_model_key,
    validate_preferred_output_key,
)
from openrouter_catalog import (
    enrich_catalog_dict,
    fetch_openrouter_index_with_fallback,
    list_addable_models,
    openrouter_configured,
    or_model_summary,
)
import provider_catalog as _pc

# ─── Configuration ────────────────────────────────────
def get_config():
    conf_path = os.getenv("AGICTL_CONFIG", "/etc/versa-agi/coa_config.json")
    try:
        with open(conf_path, "r") as f:
            return json.load(f)
    except Exception:
        return {}

def get_agent_name():
    """Resolve agent name natively using config namespace or OS user."""
    config_path = os.getenv("AGICTL_CONFIG", "")
    if config_path:
        basename = os.path.basename(config_path)
        if basename.endswith("_config.json"):
            return basename.replace("_config.json", "").lower()
    
    import getpass
    try:
        user = getpass.getuser()
        if user in ("coa", "watchdog"):
            return "coa"
        if user.startswith("agi-"):
            return user[4:]
    except Exception:
        pass

    config = get_config()
    name = config.get("identity", {}).get("first_name", "Unknown").lower()
    return "coa" if name == "versa" else name

# ─── Role ENUM (from Product Spec §3.5) ───────────────
VALID_ROLES = [
    "coa",       # Chief Orchestrator Agent
    "watchdog",  # System Watchdog
    "pa",        # Personal Assistant
    "ba",        # Business Analyst
    "sa",        # Technical Architect
    "dev",       # Developer Agent
    "devops",    # DevOps Agent
    "qa",        # QA Agent
    "mm",        # Marketing Manager
    "sr",        # Subject Researcher
    "sysmon",    # System Monitor
    "custom",    # Custom Agent (generic, COA fills specifics)
]

ROLE_LABELS = {
    "coa": "Chief Orchestrator Agent", "watchdog": "System Watchdog",
    "pa": "Personal Assistant", "ba": "Business Analyst",
    "sa": "Technical Architect", "dev": "Developer Agent",
    "devops": "DevOps Agent", "qa": "QA Agent",
    "mm": "Marketing Manager", "sr": "Subject Researcher",
    "sysmon": "System Monitor", "custom": "Custom Agent",
}

ROLES_DIR = "/etc/versa-agi/poise/roles"

MAX_ACTIVE_AGENTS = 3

agents_db = os.getenv("AGICTL_AGENTS_DB", "/var/lib/versa-agi/agents.db")
messages_db = os.getenv("AGICTL_MESSAGES_DB", "/var/lib/versa-agi/messages.db")
tasks_db = os.getenv("AGICTL_TASKS_DB", "/var/lib/versa-agi/coa/tasks.db")
cycles_db = os.getenv("AGICTL_CYCLES_DB", "/var/lib/versa-agi/coa/cycles.db")

agent_reader = AgentReader(agents_db, cycles_db, messages_db, tasks_db)
message_reader = MessageReader(messages_db)
tasks_reader = TasksReader(tasks_db)

console = Console()

def json_response(success, **kwargs):
    """Standard JSON response for effectful commands."""
    result = {"success": success, **kwargs}
    print(json.dumps(result))
    return success

# ─── Root CLI ─────────────────────────────────────────
@click.group()
def cli():
    """Versa AGi Control Interface — agictl"""
    pass

# ═══════════════════════════════════════════════════════
# 1. SYSTEM — System-level administration
# ═══════════════════════════════════════════════════════

@cli.group()
def system():
    """System-level administration (config, identity, workspace, security)."""
    pass

@system.group()
def config():
    """Manage system configuration."""
    pass

@config.command("get")
@click.argument("key", required=False, default=None)
def system_config_get(key):
    """Read a config value. No key = return all config as JSON."""
    cfg = get_config()
    if key:
        parts = key.split(".")
        val = cfg
        for p in parts:
            if isinstance(val, dict):
                val = val.get(p)
            else:
                val = None
                break
        print(json.dumps({"key": key, "value": val}))
    else:
        print(json.dumps(cfg, indent=2))

@config.command("set")
@click.argument("key")
@click.argument("value")
def system_config_set(key, value):
    """Write a config value. Returns JSON confirmation."""
    conf_path = os.getenv("AGICTL_CONFIG", "/etc/versa-agi/coa_config.json")
    try:
        cfg = get_config()
        parts = key.split(".")
        target = cfg
        for p in parts[:-1]:
            target = target.setdefault(p, {})
        target[parts[-1]] = value
        with open(conf_path, "w") as f:
            json.dump(cfg, f, indent=2)
        json_response(True, key=key, value=value)
    except Exception as e:
        json_response(False, error=str(e))
        sys.exit(1)


@config.command("set-ini")
@click.argument("section")
@click.argument("key")
@click.argument("value")
def system_config_set_ini(section, key, value):
    """Write a configuration key/value to setup.ini.

    Preserves all comments and formatting.
    """
    ini_path = SETUP_INI_CANONICAL
    if not os.path.isfile(ini_path):
        ini_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "setup.ini")

    if not os.path.isfile(ini_path):
        json_response(False, error="setup.ini not found")
        sys.exit(1)

    section_l = (section or "").strip().lower()
    key_l = (key or "").strip().lower()
    value_s = str(value).strip()

    if section_l == "agent" and key_l == "model_routing_mode":
        if value_s.lower() not in ("pool", "preferred"):
            json_response(False, error="model_routing_mode must be 'pool' or 'preferred'")
            sys.exit(1)
    elif section_l == "model_routing" and key_l in WORK_MODALITIES and value_s:
        ok, err = validate_preferred_model_key(value_s, key_l)
        if not ok:
            json_response(False, error=err)
            sys.exit(1)
    elif section_l == "output_routing" and key_l in OUTPUT_DELIVERY_MODALITIES and value_s:
        ok, err = validate_preferred_output_key(value_s, key_l)
        if not ok:
            json_response(False, error=err)
            sys.exit(1)

    try:
        _update_ini_key(ini_path, section, key, value)
        _sync_ini_to_source(ini_path)
        json_response(True, section=section, key=key, value=value)
    except Exception as e:
        json_response(False, error=str(e))
        sys.exit(1)

@system.command("set-key", hidden=True)
@click.argument("key_type", type=click.Choice(["gemini", "versavoice", "xai", "openai", "anthropic", "openrouter"]))
@click.argument("value")
def system_set_key(key_type, value):
    """Update an API key/token and propagate to all locations. Requires root.

    Key types:
      gemini     - Gemini API Key → coa.env, *.env, .bashrc, setup.ini
      versavoice - VersaVoice API Token → coa_config.json, *_config.json, setup.ini
      xai        - xAI API Key → provider_keys.env, setup.ini
      openai     - OpenAI API Key → provider_keys.env, setup.ini
      anthropic  - Anthropic API Key → provider_keys.env, setup.ini
      openrouter - OpenRouter API Key → provider_keys.env, setup.ini
    """
    import glob
    import re
    import configparser

    if os.geteuid() != 0:
        json_response(False, error="system set-key requires root. Use: sudo agictl system set-key ...")
        sys.exit(1)

    if not value.strip():
        json_response(False, error="Value cannot be empty")
        sys.exit(1)

    updated_files = []
    errors = []

    # ── Resolve setup.ini path ──
    # Canonical location: /etc/versa-agi/setup.ini
    # Fallback: relative to script (works in source tree during development)
    _ini_canonical = "/etc/versa-agi/setup.ini"
    _ini_dev = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "setup.ini")
    setup_ini = _ini_canonical if os.path.isfile(_ini_canonical) else _ini_dev

    def _sed_replace(filepath, pattern, replacement):
        """In-place sed-style replace. Returns True if file was modified."""
        try:
            if not os.path.isfile(filepath):
                return False
            with open(filepath, "r") as f:
                content = f.read()
            new_content = re.sub(pattern, replacement, content, flags=re.MULTILINE)
            if new_content != content:
                with open(filepath, "w") as f:
                    f.write(new_content)
                return True
            return False
        except Exception as e:
            errors.append(f"{filepath}: {e}")
            return False

    def _ini_set(section, key, val):
        """Update a key in setup.ini preserving format."""
        if not os.path.isfile(setup_ini):
            return
        try:
            with open(setup_ini, "r") as f:
                lines = f.readlines()
            in_section = False
            for i, line in enumerate(lines):
                stripped = line.strip()
                if stripped.startswith("[") and stripped.endswith("]"):
                    in_section = (stripped == f"[{section}]")
                elif in_section and stripped.startswith(f"{key}="):
                    lines[i] = f"{key}={val}\n"
                    break
            with open(setup_ini, "w") as f:
                f.writelines(lines)
            updated_files.append(setup_ini)
            _sync_ini_to_source(setup_ini)
        except Exception as e:
            errors.append(f"setup.ini: {e}")

    # ════════════════════════════════════════════════
    # GEMINI API KEY
    # ════════════════════════════════════════════════
    if key_type == "gemini":
        # 1. coa.env
        coa_env = "/etc/versa-agi/coa.env"
        if _sed_replace(coa_env, r"^GEMINI_API_KEY=.*$", f"GEMINI_API_KEY={value}"):
            updated_files.append(coa_env)

        # 2. Sub-agent .env files
        for env_file in glob.glob("/etc/versa-agi/*.env"):
            if env_file == coa_env:
                continue  # Already handled
            basename = os.path.basename(env_file)
            if basename in ("provider_keys.env", "inference_endpoint.env", "paths.env"):
                continue  # Not agent env files
            if _sed_replace(env_file, r"^GEMINI_API_KEY=.*$", f"GEMINI_API_KEY={value}"):
                updated_files.append(env_file)

        # 3. COA .bashrc
        bashrc = "/home/coa/.bashrc"
        if _sed_replace(bashrc, r'^export GEMINI_API_KEY=".*"$', f'export GEMINI_API_KEY="{value}"'):
            updated_files.append(bashrc)

        # 4. setup.ini
        _ini_set("gemini", "api_key", value)

    # ════════════════════════════════════════════════
    # VERSAVOICE API TOKEN
    # ════════════════════════════════════════════════
    elif key_type == "versavoice":
        # 1. Update all *_config.json files
        for config_file in glob.glob("/etc/versa-agi/*_config.json"):
            try:
                with open(config_file, "r") as f:
                    cfg = json.load(f)
                if "versavoice" not in cfg:
                    cfg["versavoice"] = {}
                cfg["versavoice"]["api_token"] = value
                with open(config_file, "w") as f:
                    json.dump(cfg, f, indent=2)
                updated_files.append(config_file)
            except Exception as e:
                errors.append(f"{config_file}: {e}")

        # 2. setup.ini
        _ini_set("versavoice", "api_token", value)

    # ════════════════════════════════════════════════
    # XAI API KEY
    # ════════════════════════════════════════════════
    elif key_type == "xai":
        provider_keys_env = _provider_keys_env_path()
        if os.path.isfile(provider_keys_env):
            if _sed_replace(provider_keys_env, r"^XAI_API_KEY=.*$", f"XAI_API_KEY={value}"):
                updated_files.append(provider_keys_env)
            else:
                try:
                    with open(provider_keys_env, "a") as f:
                        f.write(f"\nXAI_API_KEY={value}\n")
                    updated_files.append(provider_keys_env)
                except Exception as e:
                    errors.append(f"{provider_keys_env}: {e}")
        else:
            try:
                with open(provider_keys_env, "w") as f:
                    f.write(f"XAI_API_KEY={value}\n")
                _ensure_provider_keys_env_permissions(provider_keys_env)
                updated_files.append(provider_keys_env)
            except Exception as e:
                errors.append(f"{provider_keys_env}: {e}")

        _ini_set("third_party", "xai_api_key", value)

    # ════════════════════════════════════════════════
    # OPENAI API KEY
    # ════════════════════════════════════════════════
    elif key_type == "openai":
        provider_keys_env = _provider_keys_env_path()
        env_var = "OPENAI_API_KEY"
        if os.path.isfile(provider_keys_env):
            if _sed_replace(provider_keys_env, rf"^{env_var}=.*$", f"{env_var}={value}"):
                updated_files.append(provider_keys_env)
            else:
                try:
                    with open(provider_keys_env, "a") as f:
                        f.write(f"\n{env_var}={value}\n")
                    updated_files.append(provider_keys_env)
                except Exception as e:
                    errors.append(f"{provider_keys_env}: {e}")
        else:
            try:
                with open(provider_keys_env, "w") as f:
                    f.write(f"{env_var}={value}\n")
                _ensure_provider_keys_env_permissions(provider_keys_env)
                updated_files.append(provider_keys_env)
            except Exception as e:
                errors.append(f"{provider_keys_env}: {e}")

        _ini_set("third_party", "openai_api_key", value)

    # ════════════════════════════════════════════════
    # ANTHROPIC API KEY
    # ════════════════════════════════════════════════
    elif key_type == "anthropic":
        provider_keys_env = _provider_keys_env_path()
        env_var = "ANTHROPIC_API_KEY"
        if os.path.isfile(provider_keys_env):
            if _sed_replace(provider_keys_env, rf"^{env_var}=.*$", f"{env_var}={value}"):
                updated_files.append(provider_keys_env)
            else:
                try:
                    with open(provider_keys_env, "a") as f:
                        f.write(f"\n{env_var}={value}\n")
                    updated_files.append(provider_keys_env)
                except Exception as e:
                    errors.append(f"{provider_keys_env}: {e}")
        else:
            try:
                with open(provider_keys_env, "w") as f:
                    f.write(f"{env_var}={value}\n")
                _ensure_provider_keys_env_permissions(provider_keys_env)
                updated_files.append(provider_keys_env)
            except Exception as e:
                errors.append(f"{provider_keys_env}: {e}")

        _ini_set("third_party", "anthropic_api_key", value)

    # ════════════════════════════════════════════════
    # OPENROUTER API KEY
    # ════════════════════════════════════════════════
    elif key_type == "openrouter":
        provider_keys_env = _provider_keys_env_path()
        env_var = "OPENROUTER_API_KEY"
        if os.path.isfile(provider_keys_env):
            if _sed_replace(provider_keys_env, rf"^{env_var}=.*$", f"{env_var}={value}"):
                updated_files.append(provider_keys_env)
            else:
                try:
                    with open(provider_keys_env, "a") as f:
                        f.write(f"\n{env_var}={value}\n")
                    updated_files.append(provider_keys_env)
                except Exception as e:
                    errors.append(f"{provider_keys_env}: {e}")
        else:
            try:
                with open(provider_keys_env, "w") as f:
                    f.write(f"{env_var}={value}\n")
                    f.write("OR_SITE_URL=https://versavoice.ai\n")
                    f.write("OR_APP_NAME=Versa AGi\n")
                _ensure_provider_keys_env_permissions(provider_keys_env)
                updated_files.append(provider_keys_env)
            except Exception as e:
                errors.append(f"{provider_keys_env}: {e}")

        if os.path.isfile(provider_keys_env):
            for attr_kv in (
                ("OR_SITE_URL", "https://versavoice.ai"),
                ("OR_APP_NAME", "Versa AGi"),
            ):
                attr, attr_val = attr_kv
                if not _sed_replace(provider_keys_env, rf"^{attr}=.*$", f"{attr}={attr_val}"):
                    try:
                        with open(provider_keys_env, "a") as f:
                            f.write(f"{attr}={attr_val}\n")
                    except Exception as e:
                        errors.append(f"{provider_keys_env}: {e}")

        _ini_set("third_party", "openrouter_api_key", value)

    # ── Report result ──
    if errors:
        json_response(False, key_type=key_type, updated_files=updated_files, errors=errors)
        sys.exit(1)
    else:
        json_response(True, key_type=key_type, files_updated=len(updated_files), updated_files=updated_files)

@system.command("whoami")
def system_whoami():
    """Print agent identity (name, role, OS user, VV-UID) as JSON."""
    config = get_config()
    identity = config.get("identity", {})
    vv = config.get("versavoice", {})
    pu = config.get("primary_user", {})
    result = {
        "name": identity.get("first_name", "Unknown"),
        "role": identity.get("role", "agent"),
        "os_user": os.getenv("AGICTL_AGENT_USER", os.getenv("USER", "unknown")),
        "language": identity.get("language", "en"),
        "sub_account_id": vv.get("sub_account_id", ""),
        "sponsor_uid": pu.get("uid", ""),
        "sponsor_name": pu.get("display_name", "")
    }
    print(json.dumps(result, indent=2))

@system.command("workspace-link")
@click.argument("path")
def system_workspace_link(path):
    """Create symlink from agent's .agent/workspace/ to user-accessible path."""
    config = get_config()
    # Resolve workspace source — COA env workspace directory
    workspace_src = config.get("workspace_path", "")
    if not workspace_src:
        # Derive from agent's home
        os_user = os.getenv("USER", "coa")
        home = os.path.expanduser(f"~{os_user}")
        workspace_src = os.path.join(home, "coa-env", ".agent", "workspace")
    try:
        if os.path.islink(path):
            json_response(True, action="workspace_link", path=path, note="Symlink already exists", target=os.readlink(path))
            return
        if os.path.exists(path):
            json_response(False, error=f"Path '{path}' already exists and is not a symlink. Remove it first.")
            sys.exit(1)
        if not os.path.isdir(workspace_src):
            json_response(False, error=f"Workspace source '{workspace_src}' does not exist")
            sys.exit(1)
        os.symlink(workspace_src, path)
        json_response(True, action="workspace_link", source=workspace_src, target=path)
    except Exception as e:
        json_response(False, error=str(e))
        sys.exit(1)

@system.command("workspace-unlink")
@click.argument("path", required=False, default=None)
def system_workspace_unlink(path):
    """Remove the workspace symlink. Refuses to delete real directories."""
    if not path:
        config = get_config()
        path = config.get("workspace_link", "")
    if not path:
        json_response(False, error="No workspace link path specified or found in config")
        sys.exit(1)
    try:
        if os.path.islink(path):
            os.unlink(path)
            json_response(True, action="workspace_unlink", path=path)
        elif os.path.exists(path):
            json_response(False, error=f"'{path}' is a real directory, not a symlink. Refusing to delete.")
            sys.exit(1)
        else:
            json_response(True, action="workspace_unlink", path=path, note="Path does not exist, nothing to remove")
    except Exception as e:
        json_response(False, error=str(e))
        sys.exit(1)

@system.group()
def security():
    """Manage the Security Blacklist."""
    pass

@security.command("blacklist")
@click.argument("action", type=click.Choice(["add", "remove", "list"]))
@click.argument("uid", required=False, default=None)
def system_security_blacklist(action, uid):
    """Manage the Security Blacklist for incoming message filtering."""
    json_response(True, action=action, uid=uid, note="STUB — not yet implemented")


@system.command("sync-profiles")
def system_sync_profiles():
    """Sync VersaVoice profile data for the Primary User and all connections.

    Fetches /account (sponsor profile) and /connections (contact profiles).
    Writes sponsor data to system_config.json and contact data to tasks.db connections table.
    Called by Lifeline on a 7-day staleness schedule.
    """
    config = get_config()
    vv = config.get("versavoice", {})
    token = vv.get("api_token")
    if not token:
        json_response(False, error="VersaVoice API token not configured")
        sys.exit(1)

    from comms import api_request
    from datetime import datetime
    import sqlite3 as _sql
    synced = {"sponsor_synced": False, "contacts_synced": 0}

    # ── 1. Sync sponsor (Primary User) profile ──
    account_data = api_request("/account", token)
    if account_data:
        pu = config.get("primary_user", {})
        pu["display_name"] = account_data.get("displayName") or account_data.get("firstName", "")
        pu["uid"] = account_data.get("uid", pu.get("uid", ""))
        pu["spokenLanguage"] = account_data.get("spokenLanguage", "en")
        pu["countryOfBirth"] = account_data.get("countryOfBirth")
        pu["nearestCity"] = account_data.get("nearestCity")
        pu["chromosome"] = account_data.get("chromosome")
        pu["dateOfBirth"] = account_data.get("dateOfBirth")
        pu["abilities"] = account_data.get("abilities", [])
        pu["profile_synced_at"] = datetime.now().isoformat()
        config["primary_user"] = pu

        # Write back to config
        config_path = os.environ.get("AGICTL_CONFIG", "/etc/versa-agi/coa_config.json")
        try:
            with open(config_path, "w") as f:
                json.dump(config, f, indent=2)
            synced["sponsor_synced"] = True
        except Exception as e:
            synced["sponsor_write_error"] = str(e)

    # ── 2. Sync connections profile data ──
    # Try to get connections from /account response first, fall back to /connections endpoint
    contacts = account_data.get("connections", []) if account_data else []
    if not contacts:
        connections_data = api_request("/connections", token)
        if isinstance(connections_data, list):
            contacts = connections_data
        elif isinstance(connections_data, dict):
            # API returns {"success": true, "contacts": [...], "count": N}
            contacts = connections_data.get("contacts", connections_data.get("connections", []))
    if contacts:
        try:
            conn = _sql.connect(tasks_db, timeout=5)
            for c in contacts:
                uid = c.get("uid") or c.get("contactUid") or c.get("id")
                if not uid:
                    continue
                try:
                    display_name = c.get("displayName") or c.get("firstName", "Unknown")
                    # Force-stringify complex values to prevent sqlite3 binding errors
                    abilities_raw = c.get("abilities", [])
                    abilities_json = json.dumps(abilities_raw) if abilities_raw else "[]"
                    # Ensure all values are str or None (some API fields may return dicts)
                    def _str(val):
                        if val is None:
                            return None
                        if isinstance(val, (dict, list)):
                            return json.dumps(val)
                        return str(val)
                    conn.execute("""
                        INSERT INTO connections (uid, display_name, spoken_lang, country, city, chromosome, date_of_birth, abilities, profile_synced_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
                        ON CONFLICT(uid) DO UPDATE SET
                            display_name = excluded.display_name,
                            spoken_lang = excluded.spoken_lang,
                            country = excluded.country,
                            city = excluded.city,
                            chromosome = excluded.chromosome,
                            date_of_birth = excluded.date_of_birth,
                            abilities = excluded.abilities,
                            profile_synced_at = datetime('now')
                    """, (
                        uid, display_name,
                        _str(c.get("spokenLanguage")),
                        _str(c.get("countryOfBirth")),
                        _str(c.get("nearestCity")),
                        _str(c.get("chromosome")),
                        _str(c.get("dateOfBirth")),
                        abilities_json,
                    ))
                    synced["contacts_synced"] += 1
                except Exception:
                    continue  # Skip bad contacts, don't abort sync
            conn.commit()
            conn.close()
        except Exception as e:
            synced["contacts_error"] = str(e)

    json_response(True, **synced)


@system.command("vacuum")
def system_vacuum():
    """Compact all system databases (VACUUM).

    Reclaims disk space after deletions. Safe to run at any time.
    Covers: agents.db, messages.db, tasks.db, cycles.db, and all per-agent checkpoints.db.
    For checkpoint DBs, prunes old checkpoint versions (keeping only the latest per thread)
    before running VACUUM to reclaim space from accumulated LangGraph state.
    """
    import glob as _glob

    db_targets = [
        ("/var/lib/versa-agi/agents.db", "agents"),
        ("/var/lib/versa-agi/messages.db", "messages"),
        (os.environ.get("AGICTL_TASKS_DB", "/var/lib/versa-agi/coa/tasks.db"), "tasks"),
        (os.environ.get("AGICTL_CYCLES_DB", "/var/lib/versa-agi/coa/cycles.db"), "cycles"),
    ]

    # Discover per-agent checkpoint DBs
    checkpoint_dbs = []
    for cp in _glob.glob("/var/lib/versa-agi/*/cycles/checkpoints.db"):
        agent = cp.split("/")[4]  # /var/lib/versa-agi/{agent}/cycles/checkpoints.db
        db_targets.append((cp, f"checkpoints ({agent})"))
        checkpoint_dbs.append(cp)

    results = []
    for db_path, label in db_targets:
        if not os.path.exists(db_path):
            results.append({"db": label, "status": "not found"})
            continue
        try:
            before = os.path.getsize(db_path)
            conn = sqlite3.connect(db_path, timeout=10)
            # Prune old checkpoint versions before VACUUM
            pruned = 0
            if db_path in checkpoint_dbs:
                try:
                    # For each thread, keep only the latest checkpoint
                    threads = [r[0] for r in conn.execute(
                        "SELECT DISTINCT thread_id FROM checkpoints"
                    ).fetchall()]
                    for tid in threads:
                        # Find the latest checkpoint_id for this thread
                        row = conn.execute(
                            "SELECT checkpoint_id FROM checkpoints "
                            "WHERE thread_id = ? ORDER BY checkpoint_id DESC LIMIT 1",
                            (tid,)
                        ).fetchone()
                        if row:
                            latest = row[0]
                            cursor = conn.execute(
                                "DELETE FROM checkpoints WHERE thread_id = ? AND checkpoint_id != ?",
                                (tid, latest)
                            )
                            pruned += cursor.rowcount
                    # Clean orphaned writes
                    if pruned > 0:
                        conn.execute(
                            "DELETE FROM writes WHERE thread_id NOT IN "
                            "(SELECT DISTINCT thread_id FROM checkpoints)"
                        )
                        conn.commit()
                except Exception:
                    pass  # Pruning is best-effort — VACUUM still runs
            conn.execute("VACUUM")
            conn.close()
            after = os.path.getsize(db_path)
            saved = before - after
            result_entry = {
                "db": label,
                "before": f"{before / 1024:.1f} KB",
                "after": f"{after / 1024:.1f} KB",
                "saved": f"{saved / 1024:.1f} KB" if saved > 0 else "0",
                "status": "ok",
            }
            if pruned > 0:
                result_entry["pruned_checkpoints"] = pruned
            results.append(result_entry)
        except Exception as e:
            results.append({"db": label, "status": "error", "error": str(e)})

    json_response(True, databases=results)


# ─── Config Reconciliation (deterministic regeneration) ───
# Run by setup.sh on EVERY run (fresh + --update): the shipped templates are the
# authority for structure, comments, stock model lists, and new/removed keys;
# user-provided content is preserved. See `system reconcile-config`.

def _parse_ini_pairs(path):
    """Parse {(section, key): value} from an INI file (line-based, comment-safe)."""
    pairs = {}
    section = None
    with open(path) as f:
        for line in f:
            s = line.strip()
            if s.startswith("[") and s.endswith("]"):
                section = s[1:-1]
            elif section and s and not s.startswith("#") and "=" in s:
                k, v = s.split("=", 1)
                pairs[(section, k.strip())] = v.strip()
    return pairs


def _ini_section_body_lines(path, section):
    """Return the raw body lines of an INI section (between its header and the next)."""
    body, in_sec = [], False
    with open(path) as f:
        for line in f:
            s = line.strip()
            if s.startswith("[") and s.endswith("]"):
                if in_sec:
                    break
                in_sec = (s[1:-1] == section)
                continue
            if in_sec:
                body.append(line.rstrip("\n"))
    while body and not body[-1].strip():
        body.pop()
    return body


def _is_stock_setup_key(section, key):
    """True for setup.ini keys owned by the shipped template (never carried forward).

    These are the stock model-selection lists — the release decides them; the
    operator customizes models via the dashboard/CLI custom layer instead.
    """
    if section == "gemini" and key in ("cloud_models", "coa_approved_models"):
        return True
    if section == "third_party" and (key == "providers" or key.endswith("_models")):
        return True
    return False


def _reconcile_setup_ini(template, deployed):
    """Regenerate the deployed setup.ini from the template, carrying user values forward.

    The new file is the template verbatim (structure, comments, stock lists, new
    keys with defaults), except that for every template key also present in the
    deployed file the deployed value wins — unless the key is stock-owned
    (`_is_stock_setup_key`). Deployed-only (deprecated) keys drop by design.
    Returns the number of carried-forward values.
    """
    user_vals = _parse_ini_pairs(deployed)
    out = []
    section = None
    carried = 0
    with open(template) as f:
        for line in f:
            s = line.strip()
            if s.startswith("[") and s.endswith("]"):
                section = s[1:-1]
                out.append(line)
                continue
            if section and s and not s.startswith("#") and "=" in s:
                key = s.split("=", 1)[0].strip()
                if (section, key) in user_vals and not _is_stock_setup_key(section, key):
                    prefix = line[:line.index("=") + 1]
                    out.append(f"{prefix}{user_vals[(section, key)]}\n")
                    carried += 1
                    continue
            out.append(line)
    with open(deployed, "w") as f:
        f.writelines(out)
    return carried


# Shared local sections: shipped rows + registry-added user rows coexist here,
# so reconciliation is a per-key union (deployed-only keys are preserved).
_MODELS_UNION_SECTIONS = ("local_models", "context_windows", "sycl_models")


def _reconcile_models_ini(template, deployed):
    """Regenerate the deployed models.ini from the template, preserving user content.

    Preserved across regeneration:
      - [catalog_custom] / [providers_custom] / [model_params_custom] bodies verbatim (the user layer)
      - deployed-only keys in the shared local sections (registry-added models)
    Everything else (header comments, [catalog]/[providers] stock, shipped local
    rows) comes fresh from the template; `model migrate` rebuilds the baseline
    from setup.ini right after.
    """
    custom_bodies = {
        sec: _ini_section_body_lines(deployed, sec)
        for sec in ("catalog_custom", "providers_custom", "model_params_custom")
    }
    tmpl_pairs = _parse_ini_pairs(template)
    dep_pairs = _parse_ini_pairs(deployed)
    union_extras = [
        (sec, k, v)
        for (sec, k), v in dep_pairs.items()
        if sec in _MODELS_UNION_SECTIONS and (sec, k) not in tmpl_pairs
    ]

    shutil.copyfile(template, deployed)
    for sec, body in custom_bodies.items():
        if body:
            _replace_ini_section_body(deployed, sec, body)
    for sec, k, v in union_extras:
        _upsert_models_ini_entry(deployed, sec, k, v)

    custom_rows = sum(
        1 for body in custom_bodies.values()
        for l in body if "=" in l and not l.strip().startswith("#"))
    return {"custom_rows_preserved": custom_rows,
            "local_rows_unioned": len(union_extras)}


@system.command("reconcile-config", hidden=True)
@click.option("--setup-template", required=True, type=click.Path(exists=True),
              help="Shipped setup.ini template (installer directory)")
@click.option("--models-template", required=True, type=click.Path(exists=True),
              help="Shipped models.ini template (installer directory)")
def system_reconcile_config(setup_template, models_template):
    """Regenerate deployed setup.ini + models.ini from the shipped templates.

    Deterministic config refresh run by setup.sh on EVERY run (fresh and
    --update alike). The template is authority for structure, comments, stock
    model lists, and new/removed keys; user-provided content is preserved:
    setup.ini user-owned values (keys, tokens, mode, hardware, ...), and
    models.ini [catalog_custom]/[providers_custom]/[model_params_custom] plus registry-added rows in
    [local_models]/[context_windows]/[sycl_models]. Follow with
    'agictl model migrate' + 'agictl model sync'.
    """
    # Operator-only (same rationale as `model migrate`): agents must not be able
    # to re-stamp system config. setup.sh runs as root (AGICTL_AGENT_USER unset).
    if os.environ.get("AGICTL_AGENT_USER", ""):
        json_response(False, error="'system reconcile-config' is Primary User / operator only.")
        sys.exit(1)

    result = {}
    try:
        # setup.ini: regenerate only when a deployed copy exists (fresh installs
        # create it later, in setup.sh Step 13, from interactively collected values).
        if os.path.exists(SETUP_INI_CANONICAL):
            result["setup_ini_values_carried"] = _reconcile_setup_ini(
                setup_template, SETUP_INI_CANONICAL)
            result["setup_ini"] = "regenerated"
        else:
            result["setup_ini"] = "absent (fresh install — created in Step 13)"

        models_deployed = _MODELS_INI_PATHS[0]
        if os.path.exists(models_deployed):
            result.update(_reconcile_models_ini(models_template, models_deployed))
            result["models_ini"] = "regenerated"
        else:
            shutil.copyfile(models_template, models_deployed)
            result["models_ini"] = "seeded from template"
    except PermissionError:
        json_response(False, error="permission denied writing config (run as root)")
        sys.exit(1)
    except Exception as e:
        json_response(False, error=f"reconcile failed: {e}")
        sys.exit(1)

    json_response(True, **result)


# ═══════════════════════════════════════════════════════
# 1b. MODEL — Local model management
# ═══════════════════════════════════════════════════════

# ─── Shared Helpers ──────────────────────────────────

PATHS_ENV_FILE = "/etc/versa-agi/paths.env"
PROVIDER_KEYS_ENV = "/etc/versa-agi/provider_keys.env"
PROVIDER_KEYS_ENV_LEGACY = "/etc/versa-agi/inference_endpoint.env"
SETUP_INI_CANONICAL = "/etc/versa-agi/setup.ini"
SYCL_MODEL_DIR = "/opt/versa-agi/sycl-models"
SYCL_CONTAINER = "versa-agi-sycl"

def _provider_keys_env_path() -> str:
    """Resolve provider key store (migrated from inference_endpoint.env)."""
    if os.path.isfile(PROVIDER_KEYS_ENV):
        return PROVIDER_KEYS_ENV
    if os.path.isfile(PROVIDER_KEYS_ENV_LEGACY):
        return PROVIDER_KEYS_ENV_LEGACY
    return PROVIDER_KEYS_ENV


def _ensure_provider_keys_env_permissions(path: str) -> None:
    try:
        os.chmod(path, 0o600)
        subprocess.run(["chown", "watchdog:watchdog", path], check=False, capture_output=True)
    except OSError:
        pass


# ─── Intel SYCL Model Registry (dynamic) ──────────────────
# Loaded from models.ini [sycl_models] section.
# Format: model_key = hf_repo,gguf_filename,size_gb

# Canonical models.ini path (deployed alongside setup.ini)
# Dev fallback: src/models.ini (next to src/setup.ini)
_MODELS_INI_PATHS = [
    "/etc/versa-agi/models.ini",
    os.path.join(os.path.dirname(os.path.dirname(
        os.path.dirname(os.path.abspath(__file__)))), "models.ini"),
]


def _models_ini_parser():
    """ConfigParser for models.ini — tolerant of duplicate sections (last wins)."""
    import configparser
    cfg = configparser.ConfigParser(delimiters=("=",), strict=False)
    cfg.optionxform = str
    return cfg


def _load_sycl_registry():
    """Load SYCL model registry from models.ini [sycl_models] section.

    Returns dict: {name: {"repo": str, "file": str, "size_gb": int}}
    Falls back to empty dict if models.ini is unavailable.
    """
    import configparser
    ini = _models_ini_parser()
    for path in _MODELS_INI_PATHS:
        if os.path.exists(path):
            ini.read(path)
            break

    if not ini.has_section("sycl_models"):
        return {}

    registry = {}
    for key, value in ini.items("sycl_models"):
        try:
            parts = value.strip().split(",")
            if len(parts) == 3:
                repo = parts[0].strip()
                gguf_file = parts[1].strip()
                size_gb = int(parts[2].strip())
                registry[key.strip()] = {
                    "repo": repo,
                    "file": gguf_file,
                    "size_gb": size_gb,
                }
        except (ValueError, IndexError):
            continue

    return registry


# Module-level cache — loaded once on import, can be reloaded
SYCL_MODEL_MAP = _load_sycl_registry()


def _resolve_models_ini_path():
    """Return the first existing models.ini path (canonical, then dev fallback)."""
    for path in _MODELS_INI_PATHS:
        if os.path.exists(path):
            return path
    return None


def _read_raw_section(path, section):
    """Return an ordered {key: raw_value} dict for an INI section (case-preserving).

    Empty dict when the file/section is absent. Keys keep their original case so
    they round-trip with _upsert_models_ini_entry / _remove_ini_entry.
    """
    import configparser
    ini = _models_ini_parser()
    if path and os.path.exists(path):
        try:
            ini.read(path)
        except configparser.Error:
            return {}
    if not ini.has_section(section):
        return {}
    return {k.strip(): v for k, v in ini.items(section)}


# ── Isolation model (Edition 2.x) ──────────────────────────
# Model/provider config has two physically separated origins inside models.ini:
#   • baseline   : [catalog] / [providers]            — regenerated from setup.ini
#                                                        by `agictl model migrate`
#                                                        (DO NOT hand-edit)
#   • user layer : [catalog_custom] / [providers_custom] — owned by the CLI /
#                                                        dashboard, never touched
#                                                        by migrate
# The loaders below are the single merge point: the custom layer overlays the
# baseline per-key (whole-row override), so every downstream consumer
# (`_sync_catalog`, paths.env, agitop, the harness) sees one merged view.

def _load_providers():
    """Load the merged provider registry (baseline [providers] + [providers_custom]).

    Returns dict: {slug: {"enabled": bool, "label": str, "cls": str,
                          "origin": "baseline"|"custom"|"override"}}.
    Format per row:  slug = enabled|Display Name|langchain_class
    """
    path = _resolve_models_ini_path()
    base = _read_raw_section(path, "providers")
    custom = _read_raw_section(path, "providers_custom")
    out = {}

    def _parse(slug, raw, origin):
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


def _load_catalog():
    """Load the merged model catalog (baseline [catalog] + [catalog_custom])."""
    return _mc_load_catalog(_resolve_models_ini_path())


def _replace_ini_section_body(path, section, body_lines):
    """Replace the body of an INI section in-place, preserving all other content.

    Everything between the ``[section]`` header line and the next ``[...]`` header
    (or EOF) is replaced with ``body_lines``. Comments that live ABOVE the section
    header are preserved; only the section's own body is rewritten. If the section
    does not exist it is appended at the end of the file.
    """
    with open(path, "r") as f:
        lines = f.readlines()

    header = f"[{section}]"
    start = None
    for i, line in enumerate(lines):
        if line.strip() == header:
            start = i
            break

    new_body = [(l if l.endswith("\n") else l + "\n") for l in body_lines]

    if start is None:
        # Append a fresh section at EOF
        if lines and lines[-1].strip() != "":
            lines.append("\n")
        lines.append(header + "\n")
        lines.extend(new_body)
        with open(path, "w") as f:
            f.writelines(lines)
        return

    # Find the end of the section (next header or EOF)
    end = len(lines)
    for j in range(start + 1, len(lines)):
        if lines[j].strip().startswith("[") and lines[j].strip().endswith("]"):
            end = j
            break

    # Preserve a single trailing blank line before the next section, if present
    tail = []
    if end < len(lines):
        tail = ["\n"]

    rebuilt = lines[:start + 1] + new_body + tail + lines[end:]
    with open(path, "w") as f:
        f.writelines(rebuilt)


def _write_full_ini_section(path, section, entries, header_comment=None):
    """Create or overwrite an INI section with ``entries`` (list of (key, value)).

    Used by ``model migrate`` to author [providers]/[catalog]. Preserves the rest
    of the file; appends the section (with optional comment) if it does not exist.
    """
    body = []
    if not any(l.strip() == f"[{section}]" for l in open(path)):
        # Section absent — append with optional comment block
        with open(path, "r") as f:
            content = f.read()
        chunk = "\n"
        if header_comment:
            chunk += header_comment.rstrip("\n") + "\n"
        chunk += f"[{section}]\n"
        for k, v in entries:
            chunk += f"{k:<29} = {v}\n"
        if not content.endswith("\n"):
            chunk = "\n" + chunk
        with open(path, "w") as f:
            f.write(content + chunk)
        return
    # Section present — replace its body
    for k, v in entries:
        body.append(f"{k:<29} = {v}\n")
    _replace_ini_section_body(path, section, body)


def _upsert_models_ini_entry(path, section, key, value):
    """Update an existing ``key = value`` row in a models.ini section, or append it.

    Unlike ``_update_models_ini_entry`` (add-if-missing only), this replaces the
    value when the key already exists. Keys may contain ':' (e.g. ``gemma4:e4b``).
    """
    with open(path, "r") as f:
        lines = f.readlines()

    current = None
    found = False
    section_exists = False
    insert_at = -1  # last line index belonging to the target section
    entry = f"{key:<29} = {value}\n"
    for i, line in enumerate(lines):
        s = line.strip()
        if s.startswith("[") and s.endswith("]"):
            current = s[1:-1]
            if current == section:
                section_exists = True
                insert_at = i  # header line; insert right after if section is empty
            continue
        if current == section:
            eq = s.find("=")
            if eq > 0:
                insert_at = i  # track last real key line; skip blanks/comments
                if s[:eq].strip() == key and not found:
                    lines[i] = entry
                    found = True

    if not found:
        if section_exists:
            lines.insert(insert_at + 1, entry)
        else:
            # Section absent — append it
            if lines and lines[-1].strip() != "":
                lines.append("\n")
            lines.append(f"[{section}]\n")
            lines.append(entry)

    with open(path, "w") as f:
        f.writelines(lines)


def _remove_ini_entry(path, section, key):
    """Remove a ``key = …`` row from a models.ini section. Returns True if removed."""
    with open(path, "r") as f:
        lines = f.readlines()
    current = None
    out = []
    removed = False
    for line in lines:
        s = line.strip()
        if s.startswith("[") and s.endswith("]"):
            current = s[1:-1]
            out.append(line)
            continue
        if current == section and s and not s.startswith("#"):
            eq = s.find("=")
            if eq > 0 and s[:eq].strip() == key:
                removed = True
                continue
        out.append(line)
    if removed:
        with open(path, "w") as f:
            f.writelines(out)
    return removed


def _sync_catalog():
    """Regenerate derived model config from the [catalog] source of truth.

    Refreshes the cloud/third-party paths.env registries (VERSA_CLOUD_MODELS,
    VERSA_THIRD_PARTY_MODELS, VERSA_THIRD_PARTY_ENABLED, VERSA_COA_APPROVED_MODELS)
    from the merged catalog + provider-enabled state. Edition 2.x readers (agitop
    picker, harness model_context) read display labels / context windows from
    [catalog] directly, so no legacy label sections are generated. Local models
    and VERSA_LOCAL_MODELS are owned by the local pipeline and left untouched.

    Returns (success: bool, payload: dict). Never raises for expected I/O issues —
    permission problems are reported in payload['error'].
    """
    catalog = _load_catalog()
    providers = _load_providers()

    if not catalog:
        return True, {"changed": False,
                      "message": "No [catalog] section found — nothing to sync (run 'agictl model migrate')."}

    cloud, third_party, coa = [], [], []
    tp_provider_active = set()

    for key, m in catalog.items():
        cls = m["class"]
        if cls == "cloud":
            if m["enabled"]:
                cloud.append(key)
                if m["coa"]:
                    coa.append(key)
        elif cls == "third_party":
            prov = providers.get(m["provider"], {})
            available = m["enabled"] and prov.get("enabled", False)
            if available:
                third_party.append(key)
                tp_provider_active.add(m["provider"])
                if m["coa"]:
                    coa.append(key)
        # local rows are advisory in this edition — not synced here

    errors = []

    paths_updated = False
    if os.path.isfile(PATHS_ENV_FILE):
        try:
            _update_paths_env_key("VERSA_CLOUD_MODELS", ",".join(cloud))
            _update_paths_env_key("VERSA_THIRD_PARTY_MODELS", ",".join(third_party))
            _update_paths_env_key("VERSA_THIRD_PARTY_ENABLED",
                                  "true" if tp_provider_active else "false")
            _update_paths_env_key("VERSA_COA_APPROVED_MODELS", ",".join(coa))
            paths_updated = True
        except PermissionError:
            errors.append(f"permission denied: {PATHS_ENV_FILE} (use sudo)")
        except Exception as e:
            errors.append(f"{PATHS_ENV_FILE}: {e}")

    payload = {
        "changed": True,
        "cloud_models": cloud, "third_party_models": third_party,
        "coa_approved": coa, "third_party_enabled": bool(tp_provider_active),
        "paths_env_updated": paths_updated,
    }
    if errors:
        payload["error"] = "; ".join(errors)
        return False, payload
    payload["message"] = "Model catalog synced."
    return True, payload


def _resolve_protected_identities():
    """Resolve COA/watchdog agent names and the COA display name.

    setup.ini [users] defines the agent keys (OS usernames); the COA display
    name (first + last, e.g. first_name=Versa last_name=(COA)) comes from her
    identity config (synced from VersaVoice), falling back to setup.ini
    [agent] first_name/last_name. Never hardcode these identities.
    Returns (coa_user, watchdog_user, coa_display).
    """
    import configparser
    coa_user, watchdog_user = "coa", "watchdog"
    first, last = "", ""
    for path in [SETUP_INI_CANONICAL, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "setup.ini")]:
        if os.path.isfile(path):
            cfg = configparser.ConfigParser()
            cfg.read(path)
            coa_user = cfg.get("users", "coa", fallback=coa_user) or coa_user
            watchdog_user = cfg.get("users", "watchdog", fallback=watchdog_user) or watchdog_user
            first = cfg.get("agent", "first_name", fallback="") or ""
            last = cfg.get("agent", "last_name", fallback="") or ""
            break
    try:
        # coa_config.json is a structural filename (hardcoded system-wide) —
        # deliberately NOT derived from the configured username.
        with open("/etc/versa-agi/coa_config.json") as f:
            identity = json.load(f).get("identity", {})
            first = identity.get("first_name") or first
            last = identity.get("last_name") or last
    except Exception:
        pass
    coa_display = " ".join(p for p in (first, last) if p)
    return coa_user, watchdog_user, coa_display


def _resolve_gpu_backend():
    """Read gpu_backend from setup.ini. Returns 'standard' or 'intel'."""
    import configparser
    for path in [SETUP_INI_CANONICAL, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "setup.ini")]:
        if os.path.isfile(path):
            cfg = configparser.ConfigParser()
            cfg.read(path)
            return cfg.get("local_ai", "gpu_backend", fallback="standard")
    return "standard"


def _resolve_sycl_port():
    """Read sycl_port from setup.ini. Returns port string."""
    import configparser
    for path in [SETUP_INI_CANONICAL, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "setup.ini")]:
        if os.path.isfile(path):
            cfg = configparser.ConfigParser()
            cfg.read(path)
            return cfg.get("local_ai", "sycl_port", fallback="8080")
    return "8080"


def _resolve_sycl_active_model():
    """Read sycl_active_model from setup.ini."""
    import configparser
    for path in [SETUP_INI_CANONICAL, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "setup.ini")]:
        if os.path.isfile(path):
            cfg = configparser.ConfigParser()
            cfg.read(path)
            return cfg.get("local_ai", "sycl_active_model", fallback="")
    return ""


def _resolve_loading_strategy():
    """Read model_loading_strategy from setup.ini. Returns 'single' or 'router'."""
    import configparser
    for path in [SETUP_INI_CANONICAL, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "setup.ini")]:
        if os.path.isfile(path):
            cfg = configparser.ConfigParser()
            cfg.read(path)
            return cfg.get("local_ai", "model_loading_strategy", fallback="single")
    return "single"


def _resolve_gguf_for_model(model_name):
    """Look up GGUF filename from models.ini [sycl_models] section.
    Returns the GGUF filename string, or the model_name itself as fallback."""
    registry = _load_sycl_registry()
    if model_name in registry:
        return registry[model_name]["file"]
    return model_name


def _resolve_sycl_models_max():
    """Read sycl_models_max from setup.ini (fallback: 1)."""
    import configparser
    for path in [SETUP_INI_CANONICAL, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "setup.ini")]:
        if os.path.isfile(path):
            cfg = configparser.ConfigParser()
            cfg.read(path)
            return int(cfg.get("local_ai", "sycl_models_max", fallback="1"))
    return 1


def _resolve_sycl_concurrency():
    """Read sycl_parallel, sycl_ctx_size, sycl_vram_gb from setup.ini.
    Returns (parallel, ctx_size, vram_gb) as ints."""
    import configparser
    for path in [SETUP_INI_CANONICAL, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "setup.ini")]:
        if os.path.isfile(path):
            cfg = configparser.ConfigParser()
            cfg.read(path)
            parallel = int(cfg.get("local_ai", "sycl_parallel", fallback="1"))
            ctx_size = int(cfg.get("local_ai", "sycl_ctx_size", fallback="4096"))
            vram_gb = int(cfg.get("local_ai", "sycl_vram_gb", fallback="32"))
            return parallel, ctx_size, vram_gb
    return 1, 4096, 32


def _calculate_concurrency(vram_gb, model_size_gb, ctx_size=4096):
    """Calculate recommended parallel slots based on available VRAM.
    Returns (recommended, max_slots, free_vram_gb)."""
    kv_per_slot_mb = max(128, 256 * ctx_size // 4096)
    free_vram_gb = max(0, vram_gb - model_size_gb)
    free_mb = free_vram_gb * 1024
    headroom_mb = 2048  # 2GB headroom

    if free_mb <= headroom_mb:
        return 1, 1, free_vram_gb

    max_slots = min(8, max(1, (free_mb - headroom_mb) // kv_per_slot_mb))
    recommended = min(4, max(1, max_slots // 2))
    return recommended, max_slots, free_vram_gb


def _query_sycl_models():
    """List GGUF files in the SYCL model directory with sizes. Returns [{name, file, size, active}]."""
    models = []
    active_model = _resolve_sycl_active_model()
    if not os.path.isdir(SYCL_MODEL_DIR):
        return models
    # Reload registry to catch any recent additions
    registry = _load_sycl_registry()
    for f in os.listdir(SYCL_MODEL_DIR):
        if f.endswith(".gguf"):
            fpath = os.path.join(SYCL_MODEL_DIR, f)
            size_bytes = os.path.getsize(fpath)
            if size_bytes >= 1_000_000_000:
                size_str = f"{size_bytes / 1_000_000_000:.1f} GB"
            elif size_bytes >= 1_000_000:
                size_str = f"{size_bytes / 1_000_000:.1f} MB"
            else:
                size_str = f"{size_bytes} B"
            # Reverse-map GGUF filename to model name
            model_name = f
            for mname, minfo in registry.items():
                if minfo["file"] == f:
                    model_name = mname
                    break
            models.append({
                "name": model_name,
                "file": f,
                "size": size_str,
                "size_bytes": size_bytes,
                "active": model_name == active_model,
            })
    return models


def _docker_restart_sycl(parallel=None, ctx_size=None, models_max=None):
    """Stop/rm/run the SYCL Docker container in Router Mode (--models-dir).

    Only called when infrastructure parameters change (parallel, ctx, models-max).
    Model switching does NOT require a Docker restart — the server loads on demand.
    Returns (ok, message).
    """
    # Stop existing
    subprocess.run(["docker", "stop", SYCL_CONTAINER], capture_output=True)
    subprocess.run(["docker", "rm", SYCL_CONTAINER], capture_output=True)

    # Check for WSL2
    is_wsl = False
    try:
        with open("/proc/version", "r") as f:
            if "microsoft" in f.read().lower():
                is_wsl = True
    except Exception:
        pass

    # Build device flags and environment overrides
    devices = []
    wsl_mounts = []
    wsl_env = []

    if is_wsl:
        # WSL2 Windows driver translation bridge
        if os.path.exists("/dev/dxg"):
            devices.extend(["--device", "/dev/dxg"])
        # WSL driver libraries mount
        if os.path.isdir("/usr/lib/wsl"):
            wsl_mounts.extend(["-v", "/usr/lib/wsl:/usr/lib/wsl"])
        # WSL library path override (must include compiler libraries, app libs, and host drivers)
        wsl_env.extend([
            "-e",
            "LD_LIBRARY_PATH=/app:/opt/intel/oneapi/compiler/latest/lib:/opt/intel/oneapi/compiler/latest/linux/compiler/lib/intel64_lin:/opt/intel/oneapi/compiler/latest/linux/lib:/opt/intel/oneapi/umf/latest/lib:/opt/intel/oneapi/tcm/latest/lib:/opt/intel/oneapi/dnnl/latest/lib:/usr/lib/wsl/lib"
        ])
    else:
        # Bare metal Linux setup
        import glob
        for dev in glob.glob("/dev/dri/renderD*") + glob.glob("/dev/dri/card*"):
            devices.extend(["--device", dev])

    sycl_port = _resolve_sycl_port()
    sycl_image = "versa-agi-sycl"

    # Read concurrency settings from setup.ini (or use provided overrides)
    ini_parallel, ini_ctx_size, _ = _resolve_sycl_concurrency()
    _parallel = str(parallel or ini_parallel)
    _per_slot_ctx = ctx_size or ini_ctx_size
    # llama-server --ctx-size is TOTAL context shared across all parallel slots.
    # sycl_ctx_size is per-slot → multiply by parallel for the launch.
    _ctx_total = str(_per_slot_ctx * int(_parallel))
    _models_max = str(models_max or _resolve_sycl_models_max())

    cmd = [
        "docker", "run", "-d", "--name", SYCL_CONTAINER,
        "--restart", "unless-stopped",
    ] + devices + wsl_mounts + wsl_env + [
        "-v", f"{SYCL_MODEL_DIR}:/models",
        "-p", f"{sycl_port}:8080",
        sycl_image,
        "--models-dir", "/models",
        "--models-max", _models_max,
        "-ngl", "99", "--host", "0.0.0.0", "--port", "8080",
        "--parallel", _parallel, "--ctx-size", _ctx_total,
    ]

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        return False, f"Docker run failed: {result.stderr.strip()}"

    # Wait for container to start
    time.sleep(5)
    check = subprocess.run(
        ["docker", "ps", "--format", "{{.Names}}"],
        capture_output=True, text=True,
    )
    if SYCL_CONTAINER in check.stdout:
        return True, f"Container '{SYCL_CONTAINER}' running (port {sycl_port})"
    return False, "Container may not have started"


def _update_all_local_agent_models(new_model):
    """Update all local sub-agents in the agent registry to use the new model.
    Returns list of (agent_name, old_model) tuples for affected agents."""
    import configparser
    affected = []
    agents_db = "/var/lib/versa-agi/agents.db"
    if not os.path.isfile(agents_db):
        return affected

    # Read all local model names to identify locally-assigned agents
    registered, _ = _read_ini_csv("local_ai", "local_models")
    # Also include the previously active model
    prev_active = _resolve_sycl_active_model()
    local_names = set(registered)
    if prev_active:
        local_names.add(prev_active)

    try:
        conn = sqlite3.connect(agents_db)
        cursor = conn.cursor()
        # Find agents assigned to any local model
        placeholders = ",".join("?" * len(local_names))
        cursor.execute(
            f"SELECT name, model FROM agents WHERE model IN ({placeholders})",
            list(local_names),
        )
        rows = cursor.fetchall()
        for agent_name, old_model in rows:
            affected.append((agent_name, old_model))
        # Update all to new model
        if affected:
            cursor.execute(
                f"UPDATE agents SET model = ? WHERE model IN ({placeholders})",
                [new_model] + list(local_names),
            )
            conn.commit()
        conn.close()
    except Exception:
        pass
    return affected


def _read_paths_env():
    """Read paths.env into a dict."""
    result = {}
    if os.path.isfile(PATHS_ENV_FILE):
        with open(PATHS_ENV_FILE, "r") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, _, val = line.partition("=")
                    result[key] = val.strip('"')
    return result


def _update_paths_env_key(key, value):
    """Update a single key in paths.env (in-place). Creates if missing."""
    if not os.path.isfile(PATHS_ENV_FILE):
        return False
    with open(PATHS_ENV_FILE, "r") as f:
        lines = f.readlines()
    entry = f'{key}="{value}"\n'
    found = False
    for i, line in enumerate(lines):
        if line.strip().startswith(f"{key}="):
            lines[i] = entry
            found = True
            break
    if not found:
        lines.append(entry)
    with open(PATHS_ENV_FILE, "w") as f:
        f.writelines(lines)
    return True


def _read_ini_csv(section, key):
    """Read a comma-separated value from setup.ini as a list."""
    import configparser
    ini_path = SETUP_INI_CANONICAL
    if not os.path.isfile(ini_path):
        # Dev fallback
        ini_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "setup.ini")
    if not os.path.isfile(ini_path):
        return [], None
    cfg = configparser.ConfigParser()
    cfg.read(ini_path)
    if cfg.has_option(section, key):
        raw = cfg.get(section, key).strip()
        return [m.strip() for m in raw.split(",") if m.strip()], ini_path
    return [], ini_path


def _read_ini_value(section, key, default=""):
    """Read a single scalar value from setup.ini (canonical, then dev fallback)."""
    import configparser
    ini_path = SETUP_INI_CANONICAL
    if not os.path.isfile(ini_path):
        ini_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "setup.ini")
    if not os.path.isfile(ini_path):
        return default
    cfg = configparser.ConfigParser()
    try:
        cfg.read(ini_path)
    except configparser.Error:
        return default
    return cfg.get(section, key, fallback=default).strip()


def _update_ini_csv(section, key, values, ini_path=None):
    """Update a comma-separated key in setup.ini preserving format."""
    if not ini_path:
        ini_path = SETUP_INI_CANONICAL
        if not os.path.isfile(ini_path):
            ini_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "setup.ini")
    if not os.path.isfile(ini_path):
        return False
    new_val = ",".join(values)
    with open(ini_path, "r") as f:
        lines = f.readlines()
    in_section = False
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            in_section = (stripped == f"[{section}]")
        elif in_section and stripped.startswith(f"{key}="):
            lines[i] = f"{key}={new_val}\n"
            break
    with open(ini_path, "w") as f:
        f.writelines(lines)
    _sync_ini_to_source(ini_path)
    return True


def _sync_ini_to_source(written_path: str = SETUP_INI_CANONICAL):
    """Copy written setup.ini back to the source repo copy (if discoverable).

    Keeps the source INI (next to setup.sh) in sync with the deployed
    copy at /etc/versa-agi/setup.ini after runtime mutations.
    """
    source_ini = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "setup.ini"
    )
    try:
        written_real = os.path.realpath(written_path)
        source_real = os.path.realpath(source_ini)
        if os.path.exists(source_ini) and written_real != source_real:
            shutil.copy2(written_path, source_ini)
    except Exception:
        pass  # Non-fatal — source may not be writable


def _update_models_ini_entry(models_ini_path: str, section: str, key: str, value: str):
    """Add or update a key=value entry in a models.ini section.

    If the key already exists in the section, it is left unchanged.
    If the section exists but the key doesn't, the entry is appended.
    If the section doesn't exist, it is created at the end of the file.
    """
    with open(models_ini_path, "r") as f:
        lines = f.readlines()

    in_section = False
    section_found = False
    key_found = False
    section_end = len(lines)  # Default: end of file

    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            if in_section:
                section_end = i
                break
            in_section = (stripped == f"[{section}]")
            if in_section:
                section_found = True
        elif in_section and stripped and not stripped.startswith("#"):
            # Check if key already exists (keys may contain colons, e.g. qwen3:8b)
            eq_pos = stripped.find("=")
            if eq_pos > 0 and stripped[:eq_pos].strip() == key:
                key_found = True
                break

    if key_found:
        return  # Already registered

    entry_line = f"{key:<25s} = {value}\n"

    if section_found:
        # Append entry at end of section
        lines.insert(section_end, entry_line)
    else:
        # Create section at end of file
        lines.append(f"\n[{section}]\n")
        lines.append(entry_line)

    with open(models_ini_path, "w") as f:
        f.writelines(lines)

def _resolve_ollama_cmd():
    """Resolve the correct ollama binary.

    Used by the standard backend (NVIDIA/AMD). Intel backend uses Docker SYCL instead.
    """
    for path in ["/usr/local/bin/ollama", "/usr/bin/ollama"]:
        if os.path.isfile(path) and os.access(path, os.X_OK):
            return path
    return shutil.which("ollama")


def _resolve_ollama_host():
    """Read Ollama host from paths.env or setup.ini."""
    env = _read_paths_env()
    # Not stored directly in paths.env — read from setup.ini
    import configparser
    ini_path = SETUP_INI_CANONICAL
    if not os.path.isfile(ini_path):
        ini_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "setup.ini")
    if os.path.isfile(ini_path):
        cfg = configparser.ConfigParser()
        cfg.read(ini_path)
        if cfg.has_option("local_ai", "ollama_host"):
            return cfg.get("local_ai", "ollama_host").strip()
    return "http://localhost:11434"


def _query_ollama_models():
    """Query Ollama API for pulled models. Returns {name: {size, modified}}."""
    import urllib.request
    import urllib.error
    host = _resolve_ollama_host()
    url = f"{host}/api/tags"
    try:
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode())
        models = {}
        for m in data.get("models", []):
            name = m.get("name", "")
            size_bytes = m.get("size", 0)
            if size_bytes >= 1_000_000_000:
                size_str = f"{size_bytes / 1_000_000_000:.1f} GB"
            elif size_bytes >= 1_000_000:
                size_str = f"{size_bytes / 1_000_000:.1f} MB"
            else:
                size_str = f"{size_bytes} B"
            models[name] = {"size": size_str, "size_bytes": size_bytes, "modified": m.get("modified_at", "")}
        return models
    except Exception:
        return {}


# ─── Model Command Group ────────────────────────────

@cli.group()
def model():
    """Local model management (list, add, remove)."""
    pass


@model.command("list")
@click.option("--table", "as_table", is_flag=True, help="Display as formatted table instead of JSON")
def model_list(as_table):
    """List registered local models with pull status.

    Shows all models from setup.ini [local_ai] local_models, with
    their pull status and size from the Ollama API or SYCL model directory.
    """
    # Access control: block sub-agents (allow COA, watchdog, root, Primary User)
    caller = os.getenv("AGICTL_AGENT_USER", "")
    if caller and caller not in ("coa", "watchdog"):
        json_response(False, error="model list is not available to sub-agents")
        sys.exit(1)

    gpu_backend = _resolve_gpu_backend()

    if gpu_backend == "intel":
        # Check topology — client delegates to the server via SSH
        import configparser as _cp
        _ini = _cp.ConfigParser()
        for p in [SETUP_INI_CANONICAL, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "setup.ini")]:
            if os.path.isfile(p):
                _ini.read(p)
                break
        _topology = _ini.get("local_ai", "topology", fallback="local")

        if _topology == "client":
            # Delegate to server — SSH and run agictl model list there
            import subprocess, json as _json
            client_cfg_path = "/etc/versa-agi/client_config.json"
            tunnel_host = None
            if os.path.isfile(client_cfg_path):
                with open(client_cfg_path) as f:
                    tunnel_host = _json.load(f).get("tunnel_host")
            if not tunnel_host:
                json_response(False, error="No tunnel_host configured — run setup_local.sh")
                sys.exit(1)

            wd_user = _ini.get("users", "watchdog", fallback="watchdog")
            ssh_key = f"/home/{wd_user}/.ssh/versa_agi_ed25519"
            try:
                result = subprocess.run(
                    ["sudo", "-u", wd_user, "ssh", "-i", ssh_key,
                     "-o", "StrictHostKeyChecking=accept-new",
                     "-o", "ConnectTimeout=5", "-o", "BatchMode=yes",
                     f"{wd_user}@{tunnel_host}",
                     "agictl model list"],
                    capture_output=True, text=True, timeout=20
                )
                if result.returncode != 0:
                    json_response(False, error=f"Server returned error: {result.stderr.strip()}")
                    sys.exit(1)
                models = _json.loads(result.stdout)
            except subprocess.TimeoutExpired:
                json_response(False, error="SSH connection to server timed out")
                sys.exit(1)
            except Exception as e:
                json_response(False, error=f"Failed to query server: {e}")
                sys.exit(1)

            if as_table:
                table = Table(title=f"Local Models — Intel SYCL (via {tunnel_host})")
                table.add_column("Model", style="cyan")
                table.add_column("Active", style="green")
                table.add_column("Registered", style="green")
                table.add_column("Downloaded", style="green")
                table.add_column("Size", style="yellow")
                for m in models:
                    table.add_row(
                        f"{'★ ' if m.get('active') else '  '}{m['name']}",
                        "✓" if m.get("active") else "—",
                        "✓" if m.get("registered") else "✗",
                        "✓" if m.get("downloaded") else "✗",
                        m.get("size") or "—",
                    )
                console.print(table)
            else:
                for m in models:
                    m.pop("size_bytes", None)
                print(json.dumps(models, indent=2))
            return

        # Local/server topology: check filesystem directly
        registered, _ = _read_ini_csv("local_ai", "local_models")
        sycl_models = _query_sycl_models()
        downloaded_names = {m["name"] for m in sycl_models}
        downloaded_map = {m["name"]: m for m in sycl_models}

        models = []
        for name in registered:
            info = downloaded_map.get(name, {})
            models.append({
                "name": name,
                "registered": True,
                "downloaded": name in downloaded_names,
                "active": info.get("active", False),
                "size": info.get("size"),
                "size_bytes": info.get("size_bytes"),
                "file": info.get("file"),
            })
        # Include downloaded GGUFs not in registry
        for m in sycl_models:
            if m["name"] not in registered:
                models.append({
                    "name": m["name"],
                    "registered": False,
                    "downloaded": True,
                    "active": m["active"],
                    "size": m["size"],
                    "size_bytes": m["size_bytes"],
                    "file": m["file"],
                })

        if as_table:
            table = Table(title="Local Models (Intel SYCL)")
            table.add_column("Model", style="cyan")
            table.add_column("Active", style="green")
            table.add_column("Registered", style="green")
            table.add_column("Downloaded", style="green")
            table.add_column("Size", style="yellow")
            for m in models:
                table.add_row(
                    f"{'★ ' if m['active'] else '  '}{m['name']}",
                    "✓" if m["active"] else "—",
                    "✓" if m["registered"] else "✗",
                    "✓" if m["downloaded"] else "✗",
                    m["size"] or "—",
                )
            console.print(table)
        else:
            for m in models:
                m.pop("size_bytes", None)
            print(json.dumps(models, indent=2))
    else:
        # Standard: existing Ollama-based listing
        registered, _ = _read_ini_csv("local_ai", "local_models")
        pulled = _query_ollama_models()

        models = []
        for name in registered:
            info = pulled.get(name, {})
            models.append({
                "name": name,
                "registered": True,
                "pulled": name in pulled,
                "size": info.get("size"),
                "size_bytes": info.get("size_bytes"),
            })
        # Include pulled models not in registry (pulled externally)
        for name, info in pulled.items():
            if name not in registered:
                models.append({
                    "name": name,
                    "registered": False,
                    "pulled": True,
                    "size": info.get("size"),
                    "size_bytes": info.get("size_bytes"),
                })

        if as_table:
            table = Table(title="Local Models")
            table.add_column("Model", style="cyan")
            table.add_column("Registered", style="green")
            table.add_column("Pulled", style="green")
            table.add_column("Size", style="yellow")
            for m in models:
                table.add_row(
                    m["name"],
                    "✓" if m["registered"] else "✗",
                    "✓" if m["pulled"] else "✗",
                    m["size"] or "—",
                )
            console.print(table)
        else:
            # Strip size_bytes from JSON output (internal use only)
            for m in models:
                m.pop("size_bytes", None)
            print(json.dumps(models, indent=2))


@model.command("add")
@click.argument("name")
@click.option("--no-pull", is_flag=True, help="Register only — skip ollama pull")
def model_add(name, no_pull):
    """Pull and register a local model.

    Standard: Pulls the model via Ollama.
    Intel: Downloads GGUF via HuggingFace CLI.
    Both: registers in setup.ini and updates paths.env. Requires root (sudo).
    """
    if os.geteuid() != 0:
        json_response(False, error="model add requires root. Use: sudo agictl model add ...")
        sys.exit(1)

    errors = []
    steps = []
    gpu_backend = _resolve_gpu_backend()

    # ── 1. Download model ──
    if not no_pull:
        if gpu_backend == "intel":
            # Intel: download GGUF via HuggingFace CLI
            registry = _load_sycl_registry()
            if name not in registry:
                json_response(False, error=f"Model '{name}' not in SYCL registry. Register with: agictl model registry add {name} --repo <hf_repo> --file <gguf> --size <gb>. Known: {', '.join(registry.keys())}")
                sys.exit(1)
            repo = registry[name]["repo"]
            gguf_file = registry[name]["file"]
            gguf_path = os.path.join(SYCL_MODEL_DIR, gguf_file)

            if os.path.isfile(gguf_path):
                steps.append(f"already downloaded: {gguf_file}")
            else:
                os.makedirs(SYCL_MODEL_DIR, exist_ok=True)
                print(f"Downloading: {gguf_file} ...")
                _pip_search_paths = [
                    "/usr/local/bin/hf", "/usr/local/bin/huggingface-cli",
                    os.path.expanduser("~/.local/bin/hf"), os.path.expanduser("~/.local/bin/huggingface-cli"),
                ]
                _sudo_user = os.environ.get("SUDO_USER", "")
                if _sudo_user:
                    _pip_search_paths.extend([
                        f"/home/{_sudo_user}/.local/bin/hf",
                        f"/home/{_sudo_user}/.local/bin/huggingface-cli",
                    ])
                hf_cmd = (
                    shutil.which("hf")
                    or shutil.which("huggingface-cli")
                    or next((p for p in _pip_search_paths if os.path.isfile(p) and os.access(p, os.X_OK)), None)
                )
                if not hf_cmd:
                    json_response(False, error="HuggingFace CLI not found. Install: sudo pipx install huggingface_hub[cli]")
                    sys.exit(1)
                result = subprocess.run(
                    [hf_cmd, "download", repo, "--include", gguf_file, "--local-dir", SYCL_MODEL_DIR],
                )
                if result.returncode != 0:
                    json_response(False, error=f"HuggingFace download failed for {repo}")
                    sys.exit(1)
                steps.append(f"downloaded:{gguf_file}")
        else:
            # Standard: pull via Ollama
            ollama_cmd = _resolve_ollama_cmd()
            if not ollama_cmd:
                json_response(False, error="Ollama binary not found. Is Ollama installed?")
                sys.exit(1)
            ollama_host = _resolve_ollama_host()
            env = os.environ.copy()
            env["OLLAMA_HOST"] = ollama_host
            print(f"Pulling model: {name} ...")
            result = subprocess.run([ollama_cmd, "pull", name], env=env)
            if result.returncode != 0:
                json_response(False, error=f"ollama pull {name} failed (exit {result.returncode})")
                sys.exit(1)
            steps.append(f"pulled:{name}")

    # ── 2. Register in setup.ini ──
    current_models, ini_path = _read_ini_csv("local_ai", "local_models")
    if name not in current_models:
        current_models.append(name)
        if ini_path and _update_ini_csv("local_ai", "local_models", current_models, ini_path):
            steps.append("setup.ini updated")
        else:
            errors.append("Failed to update setup.ini")
    else:
        steps.append("already in setup.ini")

    # ── 2.5. Register in models.ini (label + context window) ──
    try:
        sys.path.insert(0, '/usr/local/lib/versa-agi')
        from harness.model_context import get_model_context, _FALLBACK_CONTEXT_MAP
        recommended, max_ctx = get_model_context(name)
        # Determine models.ini path (canonical → dev fallback)
        models_ini_path = None
        for p in _MODELS_INI_PATHS:
            if os.path.isfile(p):
                models_ini_path = p
                break
        if models_ini_path:
            # Generate human-readable display label from model key
            # e.g. "qwen3.6:35b" → "Qwen3.6 35B — User-added model"
            family_size = name.split(":")
            family = family_size[0].capitalize()
            size_tag = family_size[1].upper() if len(family_size) > 1 else ""
            display_label = f"{family} {size_tag} — User-added model".strip() if size_tag else f"{family} — User-added model"
            _update_models_ini_entry(models_ini_path, "local_models", name, display_label)
            if recommended > 0 or max_ctx > 0:
                _update_models_ini_entry(models_ini_path, "context_windows", name, f"{recommended},{max_ctx}")
            steps.append("models.ini updated")
    except Exception as e:
        steps.append(f"models.ini skipped ({e})")

    # ── 3. Update paths.env ──
    new_csv = ",".join(current_models)
    if _update_paths_env_key("VERSA_LOCAL_MODELS", new_csv):
        steps.append("paths.env updated")
    else:
        errors.append("Failed to update paths.env")

    if errors:
        json_response(False, model=name, steps=steps, errors=errors)
        sys.exit(1)
    else:
        result_data = {"model": name, "action": "added", "steps": steps}
        if gpu_backend == "intel":
            result_data["hint"] = f"Activate with: sudo agictl model activate {name}"
        json_response(True, **result_data)


@model.command("remove")
@click.argument("name")
@click.option("--delete", is_flag=True, help="Also delete model weights from Ollama (ollama rm)")
def model_remove(name, delete):
    """Unregister a local model.

    Removes the model from setup.ini and updates paths.env.
    Standard: optionally deletes model weights with --delete (ollama rm).
    Intel: optionally deletes GGUF file with --delete. Requires root (sudo).
    """
    if os.geteuid() != 0:
        json_response(False, error="model remove requires root. Use: sudo agictl model remove ...")
        sys.exit(1)

    errors = []
    steps = []
    gpu_backend = _resolve_gpu_backend()

    # ── Intel: warn if removing active model ──
    if gpu_backend == "intel":
        active = _resolve_sycl_active_model()
        if name == active:
            json_response(False, error=f"Cannot remove active model '{name}'. Switch first: sudo agictl model activate <other>")
            sys.exit(1)

    # ── 1. Remove from setup.ini ──
    current_models, ini_path = _read_ini_csv("local_ai", "local_models")
    if name in current_models:
        current_models.remove(name)
        if ini_path and _update_ini_csv("local_ai", "local_models", current_models, ini_path):
            steps.append("setup.ini updated")
        else:
            errors.append("Failed to update setup.ini")
    else:
        steps.append("not in setup.ini (already removed)")

    # ── 2. Update paths.env ──
    new_csv = ",".join(current_models)
    if _update_paths_env_key("VERSA_LOCAL_MODELS", new_csv):
        steps.append("paths.env updated")
    else:
        errors.append("Failed to update paths.env")

    # ── 4. Optionally delete model weights ──
    if delete:
        if gpu_backend == "intel":
            # Delete GGUF file
            registry = _load_sycl_registry()
            if name in registry:
                gguf_file = registry[name]["file"]
                gguf_path = os.path.join(SYCL_MODEL_DIR, gguf_file)
                if os.path.isfile(gguf_path):
                    os.remove(gguf_path)
                    steps.append(f"deleted: {gguf_file}")
                else:
                    steps.append(f"GGUF not found: {gguf_file}")
            else:
                errors.append(f"Model '{name}' not in SYCL registry. Register with: agictl model registry add {name} --repo <hf_repo> --file <gguf> --size <gb>")
        else:
            # Standard: ollama rm
            ollama_cmd = _resolve_ollama_cmd()
            if ollama_cmd:
                ollama_host = _resolve_ollama_host()
                env = os.environ.copy()
                env["OLLAMA_HOST"] = ollama_host
                result = subprocess.run(
                    [ollama_cmd, "rm", name],
                    env=env,
                    capture_output=True, text=True,
                )
                if result.returncode == 0:
                    steps.append(f"ollama rm {name}")
                else:
                    errors.append(f"ollama rm failed: {result.stderr.strip()}")
            else:
                errors.append("Ollama binary not found — could not delete weights")

    if errors:
        json_response(False, model=name, steps=steps, errors=errors)
        sys.exit(1)
    else:
        json_response(True, model=name, action="removed", deleted=delete, steps=steps)


@model.command("run")
@click.argument("name")
@click.argument("prompt", required=False, default=None)
@click.option("--temperature", "-t", type=float, default=0.7, help="Sampling temperature (0.0–2.0)")
@click.option("--max-tokens", "-m", type=int, default=8192, help="Maximum response tokens (Gemma 4: up to 256K context)")
def model_run(name, prompt, temperature, max_tokens):
    """Test a local model interactively via the Inference Endpoint or Ollama.

    Send a one-shot prompt and stream the response.

    Examples:
      agictl model run gemma4:e4b "Hello, who are you?"
      agictl model run gemma4:e4b  # enters interactive mode
    """
    import urllib.request
    import urllib.error

    # Resolve proxy URL from paths.env
    inference_url = ""
    ollama_host = "http://localhost:11434"
    paths_env = "/etc/versa-agi/paths.env"
    if os.path.exists(paths_env):
        with open(paths_env, "r") as f:
            for line in f:
                if line.startswith("VERSA_INFERENCE_URL="):
                    inference_url = line.strip().split("=", 1)[1].strip('"')
                elif line.startswith("VERSA_OLLAMA_HOST="):
                    ollama_host = line.strip().split("=", 1)[1].strip('"')

    # Interactive mode if no prompt given
    if not prompt:
        click.echo(f"[agictl model run] Interactive mode — model: {name}")
        click.echo(f"[agictl model run] Type 'exit' or Ctrl+C to quit.\n")
        try:
            while True:
                prompt = click.prompt("You", prompt_suffix="> ")
                if prompt.lower() in ("exit", "quit", "/q"):
                    break
                _run_model_prompt(name, prompt, temperature, max_tokens, inference_url, ollama_host)
                click.echo("")
        except (KeyboardInterrupt, EOFError):
            click.echo("\n[agictl model run] Session ended.")
        return

    _run_model_prompt(name, prompt, temperature, max_tokens, inference_url, ollama_host)


def _run_model_prompt(name, prompt, temperature, max_tokens, inference_url, ollama_host):
    """Send a single prompt to a local model and print the response."""
    import urllib.request
    import urllib.error

    gpu_backend = _resolve_gpu_backend()

    # Build endpoint list based on backend
    endpoints = []
    if gpu_backend == "intel":
        # Intel: try Docker SYCL directly first, then Inference Server
        sycl_port = _resolve_sycl_port()
        endpoints.append(("SYCL", f"http://localhost:{sycl_port}/v1/chat/completions", name))
        if inference_url:
            endpoints.append(("Inference Server", f"{inference_url}/v1/chat/completions", name))
    else:
        # Standard: try Ollama directly first, then Inference Server
        endpoints.append(("Ollama", f"{ollama_host}/api/chat", name))
        if inference_url:
            endpoints.append(("Inference Server", f"{inference_url}/v1/chat/completions", name))

    for backend_name, url, model_name in endpoints:
        try:
            if "v1/chat/completions" in url:
                # OpenAI-compatible (Inference Server or SYCL)
                body = {
                    "model": model_name,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": temperature,
                    "max_tokens": max_tokens,
                    "stream": False,
                }
            else:
                # Ollama native API
                body = {
                    "model": model_name,
                    "messages": [{"role": "user", "content": prompt}],
                    "stream": False,
                    "options": {"temperature": temperature, "num_predict": max_tokens},
                }

            data = json.dumps(body).encode("utf-8")
            req = urllib.request.Request(
                url, data=data,
                headers={"Content-Type": "application/json"},
                method="POST",
            )

            click.echo(f"[{backend_name}] Sending request...", err=True)
            with urllib.request.urlopen(req, timeout=900) as response:
                result = json.loads(response.read().decode("utf-8"))

            # Extract response text
            if "choices" in result:
                # OpenAI format
                text = result["choices"][0]["message"]["content"]
            elif "message" in result:
                # Ollama format
                text = result["message"]["content"]
            else:
                text = json.dumps(result, indent=2)

            click.echo(text)
            return

        except (TimeoutError, urllib.error.URLError) as e:
            # Connection refused, timeout, or unreachable — try next backend silently
            click.echo(f"[{backend_name}] Unavailable ({e}), trying next...", err=True)
            continue
        except Exception as e:
            click.echo(f"[{backend_name}] Error: {e}", err=True)
            continue

    click.echo("Error: Could not reach Inference Endpoint or Ollama. Is the local AI backend running?", err=True)
    sys.exit(1)


@model.command("activate")
@click.argument("name")
@click.option("--ctx", "ctx_override", type=int, default=None,
              help="Override context window size per slot (e.g. 4096, 8192, 16384, 32768). Persisted to setup.ini.")
@click.option("--parallel", "parallel_override", type=int, default=None,
              help="Override parallel slot count (1-8). Persisted to setup.ini.")
def model_activate(name, ctx_override, parallel_override):
    """Switch the active/default model on the Intel SYCL backend.

    Updates setup.ini and paths.env. In single mode, syncs all local sub-agents.
    Docker is only restarted when infrastructure parameters (--ctx, --parallel) change.

    Only available when gpu_backend=intel. Requires root (sudo).

    Examples:

      sudo agictl model activate gemma4:26b

      sudo agictl model activate gemma4:e4b --ctx 32768 --parallel 8

      sudo agictl model activate qwen3.6:35b --ctx 8192
    """
    if os.geteuid() != 0:
        json_response(False, error="model activate requires root. Use: sudo agictl model activate ...")
        sys.exit(1)

    gpu_backend = _resolve_gpu_backend()
    if gpu_backend != "intel":
        json_response(False, error="model activate is only available for Intel SYCL backend (gpu_backend=intel)")
        sys.exit(1)

    # Validate model name against dynamic registry
    registry = _load_sycl_registry()
    if name not in registry:
        json_response(False, error=f"Unknown model '{name}'. Register with: agictl model registry add {name} --repo <hf_repo> --file <gguf> --size <gb>. Known: {', '.join(registry.keys())}")
        sys.exit(1)

    # Check model is downloaded
    gguf_file = registry[name]["file"]
    gguf_path = os.path.join(SYCL_MODEL_DIR, gguf_file)
    if not os.path.isfile(gguf_path):
        # Fallback: scan directory for the file (handles symlinks, case drift)
        found_path = None
        if os.path.isdir(SYCL_MODEL_DIR):
            for f in os.listdir(SYCL_MODEL_DIR):
                if f == gguf_file or f.lower() == gguf_file.lower():
                    candidate = os.path.join(SYCL_MODEL_DIR, f)
                    if os.path.isfile(candidate) or os.path.islink(candidate):
                        found_path = candidate
                        break
        if found_path:
            gguf_path = found_path
            gguf_file = os.path.basename(found_path)
        else:
            dir_listing = []
            if os.path.isdir(SYCL_MODEL_DIR):
                dir_listing = [f for f in os.listdir(SYCL_MODEL_DIR) if f.endswith(".gguf")]
            json_response(False, error=(
                f"Model not downloaded. Expected: {gguf_path}\n"
                f"Directory {SYCL_MODEL_DIR} contains: {dir_listing or '(empty or missing)'}\n"
                f"Run first: sudo agictl model add {name}"
            ))
            sys.exit(1)

    strategy = _resolve_loading_strategy()
    current_active = _resolve_sycl_active_model()

    # Already active? (only relevant in single mode where sycl_active_model matters)
    if strategy == "single" and name == current_active and ctx_override is None and parallel_override is None:
        json_response(True, model=name, action="already_active", message=f"'{name}' is already the active model")
        return

    errors = []
    steps = []
    affected = []

    # ── 1. Agent sync (strategy-dependent) ──
    if strategy == "single":
        affected = _update_all_local_agent_models(name)
        if affected:
            steps.append(f"updated {len(affected)} agent(s)")
            click.echo(f"\n  Affected agents ({len(affected)}):", err=True)
            for a, m in affected:
                click.echo(f"    • {a}: {m} → {name}", err=True)
            click.echo("", err=True)
        else:
            steps.append("no agents affected")
    else:
        # Router: agents keep individual assignments — no sweep
        steps.append("router mode — agents keep individual model assignments")

    # ── 2. Concurrency info ──
    model_size_gb = registry[name].get("size_gb", 10)
    ini_parallel, ini_ctx_size, ini_vram_gb = _resolve_sycl_concurrency()
    use_ctx = ctx_override if ctx_override is not None else ini_ctx_size
    recommended, max_slots, free_vram = _calculate_concurrency(ini_vram_gb, model_size_gb, use_ctx)
    use_parallel = parallel_override if parallel_override is not None else ini_parallel

    click.echo(f"  Concurrency for {name}:", err=True)
    click.echo(f"    Model: ~{model_size_gb}GB, VRAM: {ini_vram_gb}GB, Free: ~{free_vram}GB", err=True)
    click.echo(f"    Slots: {use_parallel} (recommended: {recommended}, max: {max_slots})", err=True)
    click.echo(f"    Context: {use_ctx} per slot", err=True)

    # ── 3. Docker restart ONLY if infrastructure params changed ──
    infra_changed = (ctx_override is not None or parallel_override is not None)
    if infra_changed:
        click.echo(f"  Restarting Docker (infrastructure params changed)...", err=True)
        ok, msg = _docker_restart_sycl(parallel=use_parallel, ctx_size=use_ctx)
        if ok:
            steps.append(msg)
        else:
            errors.append(msg)
    else:
        steps.append("Docker restart not needed (config-only change)")

    # ── 4. Update setup.ini (comment-preserving, sed-style) ──
    ini_path = SETUP_INI_CANONICAL
    if not os.path.isfile(ini_path):
        ini_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "setup.ini")
    if os.path.isfile(ini_path):
        if strategy == "single":
            _update_ini_key(ini_path, "local_ai", "sycl_active_model", name)
            _update_ini_key(ini_path, "local_ai", "default_model", name)
        else:
            # Router: update default_model only — agents keep individual assignments
            _update_ini_key(ini_path, "local_ai", "default_model", name)
        if ctx_override is not None:
            _update_ini_key(ini_path, "local_ai", "sycl_ctx_size", str(use_ctx))
        if parallel_override is not None:
            _update_ini_key(ini_path, "local_ai", "sycl_parallel", str(use_parallel))
        _sync_ini_to_source(ini_path)
        steps.append(f"setup.ini updated (strategy={strategy})")
    else:
        errors.append("setup.ini not found")

    # ── 5. Update paths.env — all models always available (router architecture) ──
    all_local, _ = _read_ini_csv("local_ai", "local_models")
    all_local_str = ",".join(all_local) if all_local else name
    if _update_paths_env_key("VERSA_LOCAL_MODELS", all_local_str):
        steps.append(f"paths.env VERSA_LOCAL_MODELS → {all_local_str}")
    else:
        errors.append("paths.env VERSA_LOCAL_MODELS update failed")

    if strategy == "single":
        if _update_paths_env_key("VERSA_ACTIVE_LOCAL_MODEL", name):
            steps.append(f"paths.env VERSA_ACTIVE_LOCAL_MODEL → {name}")

    # ── 7. Write server_config.json for client topology sync ──
    try:
        import json as _json, datetime
        server_config = {
            "sycl_ctx_size": use_ctx,
            "sycl_parallel": use_parallel,
            "sycl_models_max": _resolve_sycl_models_max(),
            "sycl_vram_gb": ini_vram_gb,
            "active_model": name if strategy == "single" else current_active,
            "default_model": name,
            "model_loading_strategy": strategy,
            "updated_at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        server_config_path = "/etc/versa-agi/server_config.json"
        with open(server_config_path, "w") as f:
            _json.dump(server_config, f, indent=2)
        os.chmod(server_config_path, 0o640)
        try:
            import pwd, configparser as _cp
            _ini = _cp.ConfigParser()
            _ini.read(SETUP_INI_CANONICAL)
            _wd_name = _ini.get("users", "watchdog", fallback="watchdog")
            wdog = pwd.getpwnam(_wd_name)
            os.chown(server_config_path, wdog.pw_uid, wdog.pw_gid)
        except (KeyError, OSError):
            pass
        steps.append("server_config.json updated")
    except Exception as e:
        errors.append(f"server_config.json write failed: {e}")

    if errors:
        json_response(False, model=name, steps=steps, errors=errors)
        sys.exit(1)
    else:
        result_data = {
            "model": name,
            "action": "activated",
            "strategy": strategy,
            "steps": steps,
            "previous_model": current_active,
        }
        if affected:
            result_data["affected_agents"] = [{"agent": a, "previous_model": m} for a, m in affected]
        json_response(True, **result_data)


@model.command("refresh")
def model_refresh():
    """Query remote inference server and sync model inventory.

    Discovers ALL downloaded models on the server via 'agictl model list'
    (SSH for client topology, direct call for local topology). Updates
    VERSA_LOCAL_MODELS with the full list and VERSA_ACTIVE_LOCAL_MODEL
    with the running model.
    """
    import configparser, json as _json

    # Read topology from setup.ini
    ini_path = SETUP_INI_CANONICAL
    if not os.path.isfile(ini_path):
        ini_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "setup.ini")
    if not os.path.isfile(ini_path):
        json_response(False, error="setup.ini not found")
        sys.exit(1)

    cfg = configparser.ConfigParser()
    cfg.read(ini_path)
    topology = cfg.get("local_ai", "topology", fallback="local")

    if topology == "server":
        json_response(False, error="model refresh is for client/local topologies — this is a server")
        sys.exit(1)


    # ── Phase 1: Discover ALL models and active model ──
    # For SYCL, only one model is loaded in VRAM at a time, but the server
    # may have many GGUFs downloaded. We query 'agictl model list' which
    # returns structured JSON with name, downloaded, and active fields.
    all_downloaded = []
    active_model = ""
    server_config_synced = {}

    if topology == "client":
        import subprocess
        # Read SSH credentials from client_config.json
        client_cfg_path = "/etc/versa-agi/client_config.json"
        tunnel_host = None
        if os.path.isfile(client_cfg_path):
            with open(client_cfg_path) as f:
                client_cfg = _json.load(f)
            tunnel_host = client_cfg.get("tunnel_host")

        if not tunnel_host:
            json_response(False, error="No tunnel_host in client_config.json — run setup_local.sh to configure client mode")
            sys.exit(1)

        wd_user = cfg.get("users", "watchdog", fallback="watchdog")
        ssh_key = f"/home/{wd_user}/.ssh/versa_agi_ed25519"
        ssh_base = ["sudo", "-u", wd_user, "ssh", "-i", ssh_key,
                    "-o", "StrictHostKeyChecking=accept-new",
                    "-o", "ConnectTimeout=5",
                    "-o", "BatchMode=yes",
                    f"{wd_user}@{tunnel_host}"]

        # Query the server's model inventory via agictl
        try:
            result = subprocess.run(
                ssh_base + ["agictl model list"],
                capture_output=True, text=True, timeout=20
            )
            if result.returncode == 0 and result.stdout.strip():
                server_models = _json.loads(result.stdout)
                for m in server_models:
                    if m.get("downloaded"):
                        all_downloaded.append(m["name"])
                    if m.get("active"):
                        active_model = m["name"]
        except Exception:
            pass  # SSH may fail — fall back gracefully

        # Sync server_config.json (VRAM, ctx_size, parallel slots, etc.)
        try:
            remote_path = "/etc/versa-agi/server_config.json"
            result = subprocess.run(
                ssh_base + [f"cat {remote_path}"],
                capture_output=True, text=True, timeout=15
            )
            if result.returncode == 0 and result.stdout.strip():
                server_config_synced = _json.loads(result.stdout)

                # Store key values in local setup.ini
                for ini in [SETUP_INI_CANONICAL, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "setup.ini")]:
                    if os.path.isfile(ini):
                        try:
                            if "sycl_ctx_size" in server_config_synced:
                                _update_ini_key(ini, "local_ai", "sycl_ctx_size", str(server_config_synced["sycl_ctx_size"]))
                            if "sycl_parallel" in server_config_synced:
                                _update_ini_key(ini, "local_ai", "sycl_parallel", str(server_config_synced["sycl_parallel"]))
                            if "sycl_vram_gb" in server_config_synced:
                                _update_ini_key(ini, "local_ai", "sycl_vram_gb", str(server_config_synced["sycl_vram_gb"]))
                        except Exception:
                            pass  # best-effort
        except Exception:
            pass  # server_config.json may not exist yet — that's OK

    else:
        # Local topology — query models directly
        sycl_models = _query_sycl_models()
        for m in sycl_models:
            if m.get("downloaded"):
                all_downloaded.append(m["name"])
            if m.get("active"):
                active_model = m["name"]

    # ── Phase 3: Resolve final model list ──
    # Prefer filesystem scan; fall back to /v1/models if scan returned nothing
    if all_downloaded:
        models = all_downloaded
    elif active_model:
        models = [active_model]
    else:
        json_response(False, error="No models discovered — server may be unreachable")
        sys.exit(1)

    models_csv = ",".join(models)

    # Update paths.env with full downloaded list
    _update_paths_env_key("VERSA_LOCAL_MODELS", models_csv)

    # Update setup.ini (both copies)
    for ini in [SETUP_INI_CANONICAL, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "setup.ini")]:
        if os.path.isfile(ini):
            try:
                _update_ini_key(ini, "local_ai", "local_models", models_csv)
            except Exception:
                pass  # best-effort

    # ── Phase 4: Sync active model ──
    # Priority: server_config.json > /v1/models > first in list
    if server_config_synced and server_config_synced.get("active_model"):
        active_model = server_config_synced["active_model"]
    elif not active_model and models:
        active_model = models[0]
    if active_model:
        _update_paths_env_key("VERSA_ACTIVE_LOCAL_MODEL", active_model)

    json_response(True,
                  models=models,
                  active_model=active_model,
                  source="ssh+agictl" if topology == "client" else "local",
                  server_config=server_config_synced if server_config_synced else None)


# ─── Model Registry Subcommand Group ────────────────────
# CRUD operations for the [sycl_models] section in models.ini

@model.command("sync")
def model_sync():
    """Regenerate derived model config from the [catalog] source of truth.

    Refreshes the cloud/third-party model registries in paths.env
    (VERSA_CLOUD_MODELS, VERSA_THIRD_PARTY_MODELS, VERSA_THIRD_PARTY_ENABLED,
    VERSA_COA_APPROVED_MODELS) from the merged catalog. Edition 2.x readers
    (agitop picker, harness model_context) read labels/context windows from
    [catalog] directly, so no legacy label sections are regenerated.

    Local models and topology-dependent keys (VERSA_LOCAL_MODELS) are owned by
    the local pipeline and left untouched in this edition. Idempotent — a no-op
    when [catalog] is absent (pre-migration systems).
    """
    ok, payload = _sync_catalog()
    if not ok:
        json_response(False, **payload)
        sys.exit(1)
    json_response(True, **payload)


@model.group("openrouter")
def openrouter_cmd():
    """Browse OpenRouter and add models to the Versa catalog."""
    pass


@openrouter_cmd.command("status")
def openrouter_status_cmd():
    """Report whether OpenRouter is enabled and keyed."""
    ok, reason = openrouter_configured()
    json_response(True, configured=ok, reason=reason if not ok else "")


@openrouter_cmd.command("list")
@click.option("--addable-only/--all", "addable_only", default=True,
              help="Default: chat-capable models not already in catalog")
@click.option("--table", "as_table", is_flag=True)
@click.option("--refresh", is_flag=True, help="Bypass the cache and repull live")
def openrouter_list_cmd(addable_only, as_table, refresh):
    """List models from the OpenRouter API (public listing; no API key required)."""
    import provider_model_cache
    if refresh:
        provider_model_cache.clear("openrouter")
    try:
        index = fetch_openrouter_index_with_fallback(use_cache=True)
    except Exception as e:
        json_response(False, error=f"OpenRouter API unavailable: {e}")
        sys.exit(1)
    cat = _load_catalog()
    if addable_only:
        models = list_addable_models(cat.keys(), index)
    else:
        from openrouter_catalog import is_chat_capable
        models = [
            or_model_summary(m)
            for m in sorted(index.values(), key=lambda x: x.get("id", ""))
            if is_chat_capable(m)
        ]
    if as_table:
        if not models:
            click.echo("No OpenRouter models.")
            return
        click.echo(f"{'ID':<36} {'CTX':<8} {'IN→OUT':<18} {'$/M in':<8} {'$/M out':<8} WORK")
        for r in models:
            io = f"{r['input_modalities']}→{r['output_modalities']}"
            pr = r.get("pricing") or {}
            pin = pr.get("prompt_per_m", 0)
            pout = pr.get("completion_per_m", 0)
            click.echo(
                f"{r['id']:<36} {r.get('context_length') or 0:<8} {io:<18} "
                f"{pin:<8.4g} {pout:<8.4g} {r['work_modality']}"
            )
        return
    json_response(True, count=len(models), models=models)


_OPENROUTER_DEFAULT_MODEL_PARAMS = json.dumps({
    "reasoning_effort": "none",
    "allowed_reasoning_efforts": ["none", "minimal", "low", "medium", "high", "xhigh"],
}, separators=(",", ":"))


@openrouter_cmd.command("add")
@click.argument("model_id")
@click.option("--coa-approved/--no-coa-approved", default=None,
              help="Default: true when model is in setup.ini coa_approved_models")
@click.option("--router-eligible/--no-router-eligible", default=True)
@click.option("--no-sync", is_flag=True)
def openrouter_add_cmd(model_id, coa_approved, router_eligible, no_sync):
    """Add an OpenRouter model to [catalog_custom] using live API metadata."""
    cat = _load_catalog()
    if model_id in cat:
        json_response(False, error=f"Model '{model_id}' already in catalog")
        sys.exit(1)
    try:
        index = fetch_openrouter_index_with_fallback()
    except Exception as e:
        json_response(False, error=f"OpenRouter API unavailable: {e}")
        sys.exit(1)
    or_model = index.get(model_id)
    if not or_model:
        json_response(False, error=f"Model '{model_id}' not found on OpenRouter")
        sys.exit(1)
    from openrouter_catalog import is_chat_capable
    if not is_chat_capable(or_model):
        json_response(False, error=f"Model '{model_id}' is not chat-capable (no text output)")
        sys.exit(1)
    coa_set = set(_read_ini_csv("gemini", "coa_approved_models")[0])
    if coa_approved is None:
        coa_approved = model_id in coa_set
    ctx = or_model.get("context_length") or 131072
    summary = or_model_summary(or_model)
    row = {
        "class": "third_party",
        "provider": "openrouter",
        "enabled": True,
        "coa": coa_approved,
        "ctx_recommended": 0,
        "ctx_max": int(ctx) if ctx else 131072,
        "work_modality": summary["work_modality"],
        "input_modalities": summary["input_modalities"],
        "output_modalities": summary["output_modalities"],
        "router_eligible": router_eligible,
        "label": summary["label"],
    }
    targets = _models_ini_write_targets()
    if not targets:
        json_response(False, error="models.ini not found")
        sys.exit(1)
    value = catalog_row_to_value(row)
    try:
        for path in targets:
            _upsert_models_ini_entry(path, "catalog_custom", model_id, value)
            _upsert_models_ini_entry(
                path, "model_params_custom", f"model:{model_id}", _OPENROUTER_DEFAULT_MODEL_PARAMS,
            )
    except PermissionError:
        json_response(False, error="permission denied writing models.ini (use sudo)")
        sys.exit(1)
    try:
        from openrouter_catalog import patch_models_ini_openrouter_pricing
        for path in targets:
            patch_models_ini_openrouter_pricing(path, keys=[model_id])
    except Exception:
        pass
    # Append to setup.ini openrouter_models so migrate retains membership
    for ini in (SETUP_INI_CANONICAL, os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "setup.ini",
    )):
        if not os.path.isfile(ini):
            continue
        existing = _read_ini_csv("third_party", "openrouter_models")[0]
        if model_id not in existing:
            existing.append(model_id)
            try:
                _update_ini_key(ini, "third_party", "openrouter_models", ",".join(existing))
            except Exception:
                pass
    payload = {"action": "openrouter_add", "key": model_id, "label": summary["label"]}
    _auto_sync_and_respond(payload, not no_sync)


@openrouter_cmd.command("patch-template")
@click.option("--models-ini", "models_ini", default=None, help="Path to models.ini template")
def openrouter_patch_template_cmd(models_ini):
    """Refresh OpenRouter metadata on [catalog] OR rows and [catalog_pricing] for all catalog keys."""
    from openrouter_catalog import patch_models_ini_openrouter_rows, patch_models_ini_openrouter_pricing
    if not models_ini:
        models_ini = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "models.ini",
        )
    try:
        updated = patch_models_ini_openrouter_rows(models_ini)
        priced = patch_models_ini_openrouter_pricing(models_ini, keys=None)
    except Exception as e:
        json_response(False, error=str(e))
        sys.exit(1)
    json_response(True, action="openrouter_patch_template", updated=updated, count=len(updated),
                  pricing_updated=priced, pricing_count=len(priced), path=models_ini)


# ─── Provider-agnostic catalog source (xAI / OpenAI / Anthropic / Google) ─────
# Same "import from provider" pattern as the OpenRouter group, generalized to
# each direct-API provider via provider_catalog.

_PROVIDER_DEFAULT_MODEL_PARAMS = json.dumps({
    "reasoning_effort": "none",
    "allowed_reasoning_efforts": ["none", "minimal", "low", "medium", "high"],
}, separators=(",", ":"))


def _append_setup_csv(section, key, value):
    """Append ``value`` to a comma-separated setup.ini key (every live copy)."""
    for ini in (SETUP_INI_CANONICAL, os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "setup.ini",
    )):
        if not os.path.isfile(ini):
            continue
        existing = _read_ini_csv(section, key)[0]
        if value not in existing:
            existing.append(value)
            try:
                _update_ini_key(ini, section, key, ",".join(existing))
            except Exception:
                pass


@model.group("source")
def source_cmd():
    """Import models from a configured provider's Models API into the catalog."""
    pass


@source_cmd.command("providers")
def source_providers_cmd():
    """List providers that are registered, enabled, and keyed (UI button gating)."""
    rows = []
    for slug in _pc.supported_providers():
        ok, reason = _pc.provider_configured(slug)
        rows.append({
            "slug": slug,
            "label": _pc.PROVIDER_LABEL.get(slug, slug),
            "configured": ok,
            "reason": reason,
        })
    json_response(True, providers=rows,
                  configured=[r["slug"] for r in rows if r["configured"]])


@source_cmd.command("status")
@click.argument("provider")
def source_status_cmd(provider):
    """Report whether a provider is enabled and keyed."""
    ok, reason = _pc.provider_configured(provider)
    json_response(True, provider=provider, configured=ok, reason=reason if not ok else "")


@source_cmd.command("list")
@click.argument("provider")
@click.option("--addable-only/--all", "addable_only", default=True,
              help="Default: chat-capable models not already in catalog")
@click.option("--table", "as_table", is_flag=True)
@click.option("--refresh", is_flag=True, help="Bypass the cache and repull live")
def source_list_cmd(provider, addable_only, as_table, refresh):
    """List models from a provider's Models API."""
    ok, reason = _pc.provider_configured(provider)
    if not ok:
        json_response(False, error=reason)
        sys.exit(1)
    import provider_model_cache
    if refresh:
        provider_model_cache.clear(provider)
    try:
        index = _pc.fetch_index(provider, use_cache=True)
    except Exception as e:
        json_response(False, error=f"{_pc.PROVIDER_LABEL.get(provider, provider)} API unavailable: {e}")
        sys.exit(1)
    cat = _load_catalog()
    if addable_only:
        models = _pc.list_addable_models(provider, cat.keys(), index)
    else:
        models = [
            _pc.model_summary(provider, m)
            for m in sorted(index.values(), key=lambda x: str(x.get("id") or x.get("name") or ""))
            if _pc.is_chat_capable(provider, m)
        ]
    if as_table:
        if not models:
            click.echo("No models.")
            return
        click.echo(f"{'ID':<40} {'CTX':<10} {'IN→OUT':<18} WORK")
        for r in models:
            io = f"{r['input_modalities']}→{r['output_modalities']}"
            click.echo(f"{r['id']:<40} {str(r.get('context_length') or '—'):<10} {io:<18} {r['work_modality']}")
        return
    json_response(True, provider=provider, count=len(models), models=models)


@source_cmd.command("add")
@click.argument("provider")
@click.argument("model_id")
@click.option("--coa-approved/--no-coa-approved", default=None,
              help="Default: true when model is in setup.ini coa_approved_models")
@click.option("--router-eligible/--no-router-eligible", default=True)
@click.option("--no-sync", is_flag=True)
def source_add_cmd(provider, model_id, coa_approved, router_eligible, no_sync):
    """Add a provider model to [catalog_custom] using live API metadata."""
    if provider == "openrouter":
        json_response(False, error="Use `agictl model openrouter add` for OpenRouter")
        sys.exit(1)
    ok, reason = _pc.provider_configured(provider)
    if not ok:
        json_response(False, error=reason)
        sys.exit(1)
    cat = _load_catalog()
    if model_id in cat:
        json_response(False, error=f"Model '{model_id}' already in catalog")
        sys.exit(1)
    try:
        index = _pc.fetch_index(provider)
    except Exception as e:
        json_response(False, error=f"{_pc.PROVIDER_LABEL.get(provider, provider)} API unavailable: {e}")
        sys.exit(1)
    raw = index.get(model_id)
    if not raw:
        json_response(False, error=f"Model '{model_id}' not found on {_pc.PROVIDER_LABEL.get(provider, provider)}")
        sys.exit(1)
    if not _pc.is_chat_capable(provider, raw):
        json_response(False, error=f"Model '{model_id}' is not chat-capable (no text output)")
        sys.exit(1)
    summary = _pc.model_summary(provider, raw)
    coa_set = set(_read_ini_csv("gemini", "coa_approved_models")[0])
    if coa_approved is None:
        coa_approved = model_id in coa_set
    ctx = summary.get("context_length") or 131072
    model_class = "cloud" if provider == "google" else "third_party"
    row = {
        "class": model_class,
        "provider": provider,
        "enabled": True,
        "coa": coa_approved,
        "ctx_recommended": 0,
        "ctx_max": int(ctx),
        "work_modality": summary["work_modality"],
        "input_modalities": summary["input_modalities"],
        "output_modalities": summary["output_modalities"],
        "router_eligible": router_eligible,
        "label": summary["label"],
    }
    targets = _models_ini_write_targets()
    if not targets:
        json_response(False, error="models.ini not found")
        sys.exit(1)
    value = catalog_row_to_value(row)
    try:
        for path in targets:
            _upsert_models_ini_entry(path, "catalog_custom", model_id, value)
            _upsert_models_ini_entry(
                path, "model_params_custom", f"model:{model_id}", _PROVIDER_DEFAULT_MODEL_PARAMS,
            )
    except PermissionError:
        json_response(False, error="permission denied writing models.ini (use sudo)")
        sys.exit(1)
    # Best-effort pricing via OpenRouter (native list rates not exposed uniformly).
    try:
        from openrouter_catalog import patch_models_ini_openrouter_pricing
        for path in targets:
            patch_models_ini_openrouter_pricing(path, keys=[model_id])
    except Exception:
        pass
    # Track membership in setup.ini {slug}_models (google → gemini.cloud_models).
    if provider == "google":
        _append_setup_csv("gemini", "cloud_models", model_id)
    else:
        _append_setup_csv("third_party", f"{provider}_models", model_id)
    payload = {"action": "source_add", "provider": provider, "key": model_id, "label": summary["label"]}
    _auto_sync_and_respond(payload, not no_sync)


@source_cmd.command("refresh")
@click.argument("provider")
@click.option("--models-ini", "models_ini", default=None, help="Path to models.ini (default: all live copies)")
def source_refresh_cmd(provider, models_ini):
    """Refresh modalities + context on a provider's [catalog] rows from its live API."""
    ok, reason = _pc.provider_configured(provider)
    if not ok:
        json_response(False, error=reason)
        sys.exit(1)
    paths = [models_ini] if models_ini else _models_ini_write_targets()
    if not paths:
        json_response(False, error="models.ini not found")
        sys.exit(1)
    updated: list[str] = []
    try:
        for path in paths:
            updated = _pc.patch_models_ini_provider_rows(path, provider) or updated
    except Exception as e:
        json_response(False, error=str(e))
        sys.exit(1)
    json_response(True, action="source_refresh", provider=provider,
                  updated=sorted(set(updated)), count=len(set(updated)))


def _catalog_baseline_meta(ini):
    """Parse [catalog] baseline into {key: full row dict for migrate merge}."""
    meta = {}
    if not ini.has_section("catalog"):
        return meta
    for key, raw in ini.items("catalog"):
        parsed = parse_catalog_row(raw)
        if parsed:
            meta[key.strip()] = parsed
    return meta


def _build_migration_rows(target):
    """Derive ([providers], [catalog]) rows from setup.ini + the template catalog.

    Returns (provider_rows, catalog_rows) as ordered lists of (key, value).

    Edition 2.x (reconciled): setup.ini decides *membership* (which models ship)
    plus COA eligibility and provider enablement. Cloud/third-party *metadata*
    (display label + context windows) is sourced from the [catalog] baseline of
    the ``target`` models.ini — the shipped template's catalog — which is the sole
    stock-metadata source. Local rows remain sourced from the pipeline-owned
    [local_models]/[context_windows] sections (intentionally left untouched).
    """
    import configparser
    mini = _models_ini_parser()
    if target and os.path.exists(target):
        mini.read(target)

    cat_meta = _catalog_baseline_meta(mini)

    def _local_label(key, default):
        if mini.has_section("local_models"):
            return mini.get("local_models", key, fallback=default).strip() or default
        return default

    def _local_ctx(key, default_max):
        if mini.has_section("context_windows"):
            val = mini.get("context_windows", key, fallback="")
            if "," in val:
                try:
                    a, b = val.split(",")[0].strip(), val.split(",")[1].strip()
                    return int(a or "0"), int(b or str(default_max))
                except ValueError:
                    pass
        return 0, default_max

    # setup.ini model config (_read_ini_csv returns (list, path))
    coa_approved = set(_read_ini_csv("gemini", "coa_approved_models")[0])
    cloud_models = _read_ini_csv("gemini", "cloud_models")[0]
    local_models = _read_ini_csv("local_ai", "local_models")[0]
    tp_providers = _read_ini_csv("third_party", "providers")[0]

    or_index: dict = {}
    try:
        or_index = fetch_openrouter_index_with_fallback()
    except Exception:
        or_index = {}

    catalog_rows = []

    # ── Cloud (metadata from the [catalog] template; default if unseen) ──
    for k in cloud_models:
        m = cat_meta.get(k, {})
        lbl = m.get("label", k)
        rec = m.get("ctx_recommended", 0)
        mx = m.get("ctx_max", 1000000) or 1000000
        coa = "true" if k in coa_approved else "false"
        row = {
            "class": "cloud", "provider": "google", "enabled": True, "coa": coa == "true",
            "ctx_recommended": rec, "ctx_max": mx,
            "work_modality": m.get("work_modality", "balanced"),
            "input_modalities": m.get("input_modalities", "text"),
            "output_modalities": m.get("output_modalities", "text"),
            "router_eligible": m.get("router_eligible", False),
            "label": lbl,
        }
        catalog_rows.append((k, catalog_row_to_value(row)))

    # ── Third-party (per provider; metadata from the [catalog] template) ──
    provider_rows = [
        ("google", "true|Google Gemini|ChatGoogleGenerativeAI"),
    ]
    cls_map = {"xai": "ChatOpenAI", "openai": "ChatOpenAI",
               "anthropic": "ChatAnthropic", "openrouter": "ChatOpenAI"}
    label_map = {"xai": "xAI (Grok)", "openai": "OpenAI (GPT)",
                 "anthropic": "Anthropic (Claude)", "openrouter": "OpenRouter"}
    for slug in tp_providers:
        raw_enabled = _read_ini_value("third_party", f"{slug}_enabled", "false")
        p_enabled = "true" if raw_enabled.strip().lower() == "true" else "false"
        p_cls = cls_map.get(slug, "ChatOpenAI")
        p_label = label_map.get(slug, slug)
        provider_rows.append((slug, f"{p_enabled}|{p_label}|{p_cls}"))
        for k in _read_ini_csv("third_party", f"{slug}_models")[0]:
            m = cat_meta.get(k, {})
            lbl = m.get("label", k)
            rec = m.get("ctx_recommended", 0)
            mx = m.get("ctx_max", 131072) or 131072
            coa = "true" if k in coa_approved else "false"
            row = {
                "class": "third_party", "provider": slug, "enabled": True, "coa": coa == "true",
                "ctx_recommended": rec, "ctx_max": mx,
                "work_modality": m.get("work_modality", "balanced"),
                "input_modalities": m.get("input_modalities", "text"),
                "output_modalities": m.get("output_modalities", "text"),
                "router_eligible": m.get("router_eligible", False),
                "label": lbl,
            }
            if slug == "openrouter" and k in or_index:
                row = enrich_catalog_dict(row, or_index[k], preserve_label=True)
            catalog_rows.append((k, catalog_row_to_value(row)))
    from model_catalog import local_provider_for_backend
    gpu_backend = _resolve_gpu_backend()
    local_provider = local_provider_for_backend(gpu_backend)
    is_llamacpp = local_provider == "llamacpp"
    provider_rows.append((
        "ollama",
        f"{'false' if is_llamacpp else 'true'}|Local (Ollama)|ChatOllama",
    ))
    provider_rows.append((
        "llamacpp",
        f"{'true' if is_llamacpp else 'false'}|Local (llama.cpp / SYCL)|ChatOpenAI",
    ))

    # ── Local (advisory; pipeline-owned [local_models]/[context_windows]) ──
    local_keys = local_models or (
        [k for k, _ in mini.items("local_models")] if mini.has_section("local_models") else [])
    for k in local_keys:
        rec, mx = _local_ctx(k, 4096)
        m = cat_meta.get(k, {})
        lbl = m.get("label", _local_label(k, k))
        row = {
            "class": "local", "provider": local_provider, "enabled": True, "coa": False,
            "ctx_recommended": rec, "ctx_max": mx,
            "work_modality": m.get("work_modality", "local"),
            "input_modalities": m.get("input_modalities", "text"),
            "output_modalities": m.get("output_modalities", "text"),
            "router_eligible": m.get("router_eligible", False),
            "label": lbl,
        }
        catalog_rows.append((k, catalog_row_to_value(row)))

    return provider_rows, catalog_rows


_BASELINE_PROVIDERS_HEADER = (
    "# Provider Registry — BASELINE (generated from setup.ini by "
    "`agictl model migrate`).\n"
    "# DO NOT hand-edit; add/override providers via `agictl provider` "
    "(writes [providers_custom]).")
_BASELINE_CATALOG_HEADER = (
    "# Unified Model Catalog — BASELINE (generated from setup.ini by "
    "`agictl model migrate`).\n"
    "# DO NOT hand-edit; add/override models via `agictl model catalog` "
    "(writes [catalog_custom]).")


def _modality_token_set(raw: str) -> set[str]:
    return {x.strip() for x in (raw or "text").split(",") if x.strip()}


def _refresh_catalog_custom_io_from_baseline(path: str, catalog_rows: list) -> int:
    """Promote stock I/O metadata from fresh baseline onto [catalog_custom] overrides.

    Custom rows survive migrate by design (disable snapshots, dashboard edits).
    When the shipped template adds modalities, stale overrides must not shadow
    the new baseline. Refresh input/output when baseline is a strict superset;
    always preserve ``enabled`` from the custom row.
    """
    baseline: dict[str, dict] = {}
    for key, raw in catalog_rows:
        parsed = parse_catalog_row(raw)
        if parsed:
            baseline[key.strip()] = parsed

    custom = _read_raw_section(path, "catalog_custom")
    if not custom:
        return 0

    updated = 0
    for key, custom_raw in custom.items():
        base = baseline.get(key)
        if not base:
            continue
        cust = parse_catalog_row(custom_raw)
        if not cust:
            continue
        changed = False
        base_in = _modality_token_set(base.get("input_modalities", "text"))
        cust_in = _modality_token_set(cust.get("input_modalities", "text"))
        if base_in > cust_in:
            cust["input_modalities"] = base.get("input_modalities", "text")
            changed = True
        base_out = _modality_token_set(base.get("output_modalities", "text"))
        cust_out = _modality_token_set(cust.get("output_modalities", "text"))
        if base_out > cust_out:
            cust["output_modalities"] = base.get("output_modalities", "text")
            changed = True
        if changed:
            _upsert_models_ini_entry(path, "catalog_custom", key, catalog_row_to_value(cust))
            updated += 1
    return updated


def _clear_ini_section(path, section):
    """Empty an INI section's body in place (keeps the header). No-op if absent."""
    if section in {l.strip()[1:-1] for l in open(path)
                   if l.strip().startswith("[") and l.strip().endswith("]")}:
        _replace_ini_section_body(path, section, [])


@model.command("migrate")
@click.option("--force", is_flag=True,
              help="Factory reset: also wipe [catalog_custom]/[providers_custom] (discards all CLI/dashboard edits)")
def model_migrate(force):
    """Regenerate the setup.ini baseline ([catalog]/[providers]) in models.ini.

    The **baseline** sections are rebuilt from setup.ini on every run, so model
    additions, edits, and removals made in setup.ini always propagate. The
    **user layer** ([catalog_custom]/[providers_custom], owned by the CLI and
    dashboard) is left untouched and overlays the baseline at read time — so
    runtime edits survive. `setup.sh` calls this (then `model sync`) on every run.

    `--force` additionally wipes the user layer for a full factory reset to
    setup.ini. Idempotent in both modes.
    """
    # Guard: `migrate` is an install/operator-time action — it regenerates the
    # baseline from setup.ini (which only operators edit). The COA's own model work
    # goes through `model catalog`/`provider`/`sync`; it never needs to migrate, and
    # `--force` would let it factory-reset itself unattended. So restrict the whole
    # command to the Primary User / operator. AGICTL_AGENT_USER is set by
    # agictl-wrapper to the real OS caller for agent invocations; a direct
    # root/PU/dashboard (sudo) call leaves it empty and is allowed (e.g. setup.sh).
    if os.environ.get("AGICTL_AGENT_USER", ""):
        json_response(
            False,
            error="'model migrate' is Primary User / operator only (run by setup.sh "
                  "or the operator). Agents manage models via 'model catalog', "
                  "'provider', and 'model sync'.",
        )
        sys.exit(1)

    target = _resolve_models_ini_path()
    if not target:
        json_response(False, error="models.ini not found")
        sys.exit(1)

    provider_rows, catalog_rows = _build_migration_rows(target)
    write_targets = _models_ini_write_targets() or [target]

    custom_io_refreshed = 0
    try:
        for path in write_targets:
            _write_full_ini_section(path, "providers", provider_rows,
                                    header_comment=_BASELINE_PROVIDERS_HEADER)
            _write_full_ini_section(path, "catalog", catalog_rows,
                                    header_comment=_BASELINE_CATALOG_HEADER)
            if not force:
                custom_io_refreshed += _refresh_catalog_custom_io_from_baseline(path, catalog_rows)
            if force:
                _clear_ini_section(path, "providers_custom")
                _clear_ini_section(path, "catalog_custom")
                _clear_ini_section(path, "model_params_custom")
    except PermissionError:
        json_response(False, error="permission denied writing models.ini (use sudo)")
        sys.exit(1)

    json_response(True, changed=True,
                  mode="force-reset" if force else "baseline",
                  models=len(catalog_rows), providers=len(provider_rows),
                  custom_io_refreshed=custom_io_refreshed,
                  files=write_targets,
                  message=(("Reset baseline and cleared the custom layer from setup.ini. "
                            if force else
                            "Regenerated [catalog]/[providers] baseline from setup.ini "
                            "(custom layer preserved). ")
                           + "Run 'agictl model sync' to refresh derived config."))


# ─── Model Catalog Subcommand Group ─────────────────────
# Full CRUD over the unified [catalog] section (all model classes).

def _models_ini_write_targets():
    """models.ini paths to write to (every existing copy). Errors if none exist."""
    return [p for p in _MODELS_INI_PATHS if os.path.exists(p)]


def _auto_sync_and_respond(base_payload, do_sync):
    """Shared tail for mutating catalog/provider commands: optionally sync, then respond."""
    if do_sync:
        ok, sync_payload = _sync_catalog()
        base_payload["synced"] = ok
        if not ok:
            base_payload["sync_error"] = sync_payload.get("error")
            json_response(False, **base_payload)
            sys.exit(1)
    else:
        base_payload["synced"] = False
    json_response(True, **base_payload)


@model.group()
def catalog():
    """Manage the unified model catalog (cloud, third-party, local)."""
    pass


@catalog.command("list")
@click.option("--class", "model_class",
              type=click.Choice(["cloud", "third_party", "local"]), default=None,
              help="Filter by model class")
@click.option("--table", "as_table", is_flag=True, help="Human-readable table instead of JSON")
def catalog_list(model_class, as_table):
    """List catalog entries, optionally filtered by class."""
    from model_catalog import load_catalog_pricing
    from harness.model_params import resolve_model_params
    cat = _load_catalog()
    pricing = load_catalog_pricing()
    rows = []
    for key, m in cat.items():
        if model_class and m["class"] != model_class:
            continue
        row = {"key": key, **m}
        resolved = resolve_model_params(key)
        row["reasoning_effort"] = resolved.get("reasoning_effort") or "none"
        pr = pricing.get(key)
        if pr:
            row["pricing"] = pr
            row["prompt_per_m"] = pr.get("prompt_per_m", 0)
            row["completion_per_m"] = pr.get("completion_per_m", 0)
        rows.append(row)
    if as_table:
        if not rows:
            click.echo("No catalog entries.")
            return
        click.echo(f"{'KEY':<32} {'CLASS':<12} {'WORK':<10} {'EN':<3} {'COA':<4} {'RTR':<4} {'$/M in':<8} {'$/M out':<8} {'I/O':<14} LABEL")
        for r in rows:
            io = f"{r.get('input_modalities','text')}→{r.get('output_modalities','text')}"
            pin = r.get("prompt_per_m")
            pout = r.get("completion_per_m")
            pin_s = f"{pin:.4g}" if pin else "—"
            pout_s = f"{pout:.4g}" if pout else "—"
            click.echo(f"{r['key']:<32} {r['class']:<12} {r.get('work_modality','balanced'):<10} "
                       f"{'✓' if r['enabled'] else '·':<3} {'✓' if r['coa'] else '·':<4} "
                       f"{'✓' if r.get('router_eligible') else '·':<4} {pin_s:<8} {pout_s:<8} {io:<14} {r['label']}")
        return
    json_response(True, count=len(rows), models=rows)


@catalog.command("add")
@click.argument("key")
@click.option("--class", "model_class", required=True,
              type=click.Choice(["cloud", "third_party", "local"]))
@click.option("--provider", required=True, help="Provider slug (must exist in [providers])")
@click.option("--label", required=True, help="Display label for pickers")
@click.option("--ctx-recommended", type=int, default=0, help="Recommended context window (0 for cloud)")
@click.option("--ctx-max", type=int, default=0, help="Maximum context window (drives trim budget)")
@click.option("--coa-approved/--no-coa-approved", default=False, help="Allow assignment to the COA")
@click.option("--enabled/--disabled", default=True, help="Offer the model at runtime")
@click.option("--work-modality", type=click.Choice(WORK_MODALITIES), default="balanced")
@click.option("--input-modalities", default="text", help="CSV: text,image,audio,video")
@click.option("--output-modalities", default="text", help="CSV: text,image,audio,video")
@click.option("--router-eligible/--no-router-eligible", default=False, help="Include in auto-routing pool")
@click.option("--gguf-repo", default=None, help="(local) HuggingFace repo — also registers [sycl_models]")
@click.option("--gguf-file", default=None, help="(local) GGUF filename")
@click.option("--size", "size_gb", type=int, default=None, help="(local) approximate GGUF size in GB")
@click.option("--no-sync", is_flag=True, help="Skip regenerating derived config")
def catalog_add(key, model_class, provider, label, ctx_recommended, ctx_max,
                coa_approved, enabled, work_modality, input_modalities, output_modalities,
                router_eligible, gguf_repo, gguf_file, size_gb, no_sync):
    """Add a model to the catalog.

    For third-party models the provider must be enabled (and keyed) before the
    model is routed at runtime. For local models, pass --gguf-* to also register
    SYCL download metadata; run 'agictl model add <key>' afterwards to download.
    """
    cat = _load_catalog()
    if key in cat:
        json_response(False, error=f"Model '{key}' already in catalog. Use 'catalog update'.")
        sys.exit(1)
    providers = _load_providers()
    if provider not in providers:
        json_response(False, error=f"Provider '{provider}' not in [providers]. "
                      f"Add it first: agictl provider add {provider} --label '<name>' --class <ChatX>. "
                      f"Known: {', '.join(providers.keys()) or '(none)'}")
        sys.exit(1)
    if model_class == "local" and provider not in ("ollama", "llamacpp"):
        json_response(False, error=f"Local models must use provider 'ollama' or 'llamacpp', not '{provider}'.")
        sys.exit(1)

    targets = _models_ini_write_targets()
    if not targets:
        json_response(False, error="models.ini not found")
        sys.exit(1)

    value = catalog_row_to_value({
        "class": model_class, "provider": provider, "enabled": enabled, "coa": coa_approved,
        "ctx_recommended": ctx_recommended, "ctx_max": ctx_max,
        "work_modality": work_modality, "input_modalities": input_modalities,
        "output_modalities": output_modalities, "router_eligible": router_eligible,
        "label": label,
    })
    try:
        for path in targets:
            # User additions live in the custom overlay — never clobbered by migrate.
            _upsert_models_ini_entry(path, "catalog_custom", key, value)
            # Local models: also seed legacy metadata + SYCL registry so the
            # existing local pipeline/pickers see them (advisory this edition).
            if model_class == "local":
                _upsert_models_ini_entry(path, "local_models", key, label)
                if ctx_max:
                    _upsert_models_ini_entry(path, "context_windows", key,
                                             f"{ctx_recommended},{ctx_max}")
                if gguf_repo and gguf_file and size_gb:
                    _upsert_models_ini_entry(path, "sycl_models", key,
                                             f"{gguf_repo},{gguf_file},{size_gb}")
    except PermissionError:
        json_response(False, error="permission denied writing models.ini (use sudo)")
        sys.exit(1)

    payload = {"model": key, "class": model_class, "provider": provider,
               "message": f"Added '{key}' to catalog."}
    if model_class == "local":
        payload["hint"] = f"Run 'sudo agictl model add {key}' to download/enable at runtime."
    _auto_sync_and_respond(payload, do_sync=not no_sync)


@catalog.command("update")
@click.argument("key")
@click.option("--label", default=None)
@click.option("--provider", default=None)
@click.option("--class", "model_class", default=None,
              type=click.Choice(["cloud", "third_party", "local"]))
@click.option("--ctx-recommended", type=int, default=None)
@click.option("--ctx-max", type=int, default=None)
@click.option("--enable/--disable", "enabled", default=None, help="Enable or disable the model")
@click.option("--coa-approve/--coa-revoke", "coa", default=None, help="Toggle COA eligibility")
@click.option("--work-modality", type=click.Choice(WORK_MODALITIES), default=None)
@click.option("--input-modalities", default=None)
@click.option("--output-modalities", default=None)
@click.option("--router-eligible/--no-router-eligible", "router_eligible", default=None)
@click.option("--no-sync", is_flag=True)
def catalog_update(key, label, provider, model_class, ctx_recommended, ctx_max,
                   enabled, coa, work_modality, input_modalities, output_modalities,
                   router_eligible, no_sync):
    """Update fields on an existing catalog entry (only provided fields change)."""
    cat = _load_catalog()
    if key not in cat:
        json_response(False, error=f"Model '{key}' not in catalog.")
        sys.exit(1)
    m = cat[key]
    if provider is not None:
        providers = _load_providers()
        if provider not in providers:
            json_response(False, error=f"Provider '{provider}' not in [providers].")
            sys.exit(1)
        m["provider"] = provider
    if model_class is not None:
        m["class"] = model_class
    if label is not None:
        m["label"] = label
    if ctx_recommended is not None:
        m["ctx_recommended"] = ctx_recommended
    if ctx_max is not None:
        m["ctx_max"] = ctx_max
    if enabled is not None:
        m["enabled"] = enabled
    if coa is not None:
        m["coa"] = coa
    if work_modality is not None:
        m["work_modality"] = work_modality
    if input_modalities is not None:
        m["input_modalities"] = input_modalities
    if output_modalities is not None:
        m["output_modalities"] = output_modalities
    if router_eligible is not None:
        m["router_eligible"] = router_eligible

    value = catalog_row_to_value(m)
    try:
        for path in _models_ini_write_targets():
            # Edits always land in the custom overlay — for a baseline model this
            # writes a full-row override that wins at read time (and survives migrate).
            _upsert_models_ini_entry(path, "catalog_custom", key, value)
    except PermissionError:
        json_response(False, error="permission denied writing models.ini (use sudo)")
        sys.exit(1)

    _auto_sync_and_respond({"model": key, "origin": m.get("origin"),
                            "message": f"Updated '{key}'."}, do_sync=not no_sync)


@catalog.command("remove")
@click.argument("key")
@click.option("--no-sync", is_flag=True)
def catalog_remove(key, no_sync):
    """Remove a model from the catalog.

    Custom (CLI/dashboard-added) models are deleted outright. Models that come
    from the setup.ini **baseline** cannot be deleted here (migrate would just
    re-add them) — they are *disabled* via a custom override instead. To drop a
    baseline model entirely, remove it from setup.ini.
    """
    cat = _load_catalog()
    if key not in cat:
        json_response(False, error=f"Model '{key}' not in catalog.")
        sys.exit(1)
    m = cat[key]
    baseline_backed = m.get("origin") in ("baseline", "override")

    try:
        for path in _models_ini_write_targets():
            if baseline_backed:
                m = dict(m)
                m["enabled"] = False
                value = catalog_row_to_value(m)
                _upsert_models_ini_entry(path, "catalog_custom", key, value)
            else:
                _remove_ini_entry(path, "catalog_custom", key)
                _remove_ini_entry(path, "local_models", key)
            _remove_ini_entry(path, "model_params_custom", f"model:{key}")
    except PermissionError:
        json_response(False, error="permission denied writing models.ini (use sudo)")
        sys.exit(1)

    if baseline_backed:
        msg = (f"'{key}' is a setup.ini baseline model — disabled via override "
               f"(remove it from setup.ini to drop entirely).")
    else:
        msg = f"Removed custom model '{key}' from catalog."
    _auto_sync_and_respond({"model": key, "disabled_only": baseline_backed,
                            "message": msg}, do_sync=not no_sync)


@catalog.command("reset")
@click.argument("key")
@click.option("--params/--no-params", "clear_params", default=True,
              help="Also clear [model_params_custom] for this model (default: yes)")
@click.option("--no-sync", is_flag=True)
def catalog_reset(key, clear_params, no_sync):
    """Drop [catalog_custom] overrides for a baseline model and revert to setup.ini stock.

    Custom-added models (origin=custom) have no baseline — use ``catalog remove``.
    Clears the user-layer catalog row; optionally clears per-model default params too.
    """
    cat = _load_catalog()
    if key not in cat:
        json_response(False, error=f"Model '{key}' not in catalog.")
        sys.exit(1)
    if cat[key].get("origin") == "custom":
        json_response(
            False,
            error=f"'{key}' is a custom model — no baseline to restore. Use 'catalog remove'.",
        )
        sys.exit(1)

    removed_catalog = False
    removed_params = False
    try:
        for path in _models_ini_write_targets():
            if _remove_ini_entry(path, "catalog_custom", key):
                removed_catalog = True
            if clear_params and _remove_ini_entry(path, "model_params_custom", f"model:{key}"):
                removed_params = True
    except PermissionError:
        json_response(False, error="permission denied writing models.ini (use sudo)")
        sys.exit(1)

    if not removed_catalog and not removed_params:
        json_response(
            True,
            model=key,
            reset=False,
            message=f"'{key}' is already on the setup.ini baseline (no custom overrides).",
        )
        return

    parts = [f"Reset '{key}' to setup.ini baseline."]
    if removed_catalog:
        parts.append("Catalog override removed.")
    if removed_params:
        parts.append("Custom default params cleared.")
    _auto_sync_and_respond(
        {"model": key, "reset": True, "catalog_custom_cleared": removed_catalog,
         "params_cleared": removed_params, "message": " ".join(parts)},
        do_sync=not no_sync,
    )


def _require_pu_or_coa():
    """Reject sub-agent callers for PU/COA-only writes."""
    caller = os.environ.get("AGICTL_AGENT_USER", "")
    if caller and caller != "coa":
        json_response(False, error=f"Permission denied (caller: {caller})")
        sys.exit(1)


@model.group()
def feedback():
    """PU/COA model preference log for triage routing."""
    pass


@feedback.command("add")
@click.option("--key", "catalog_key", required=True, help="Catalog model key")
@click.option("--preference", required=True, type=click.Choice(["prefer", "avoid"]))
@click.option("--work-modality", type=click.Choice(WORK_MODALITIES), default=None)
@click.option("--task-hint", default=None)
@click.option("--note", default=None)
def feedback_add(catalog_key, preference, work_modality, task_hint, note):
    """Add a model feedback entry."""
    _require_pu_or_coa()
    cat = _load_catalog()
    if catalog_key not in cat:
        json_response(False, error=f"Model '{catalog_key}' not in catalog")
        sys.exit(1)
    created_by = os.environ.get("AGICTL_AGENT_USER") or "pu"
    try:
        conn = sqlite3.connect(agents_db, timeout=5)
        dup = conn.execute(
            """SELECT id FROM model_feedback
               WHERE catalog_key=? AND preference=?
                 AND COALESCE(work_modality, '') = COALESCE(?, '')""",
            (catalog_key, preference, work_modality),
        ).fetchone()
        if dup:
            conn.close()
            json_response(
                False,
                error=(
                    f"Duplicate feedback: '{catalog_key}' + {preference}"
                    + (f" + {work_modality}" if work_modality else " (any modality)")
                ),
            )
            sys.exit(1)
        conn.execute(
            """INSERT INTO model_feedback
               (catalog_key, work_modality, task_hint, preference, note, created_by)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (catalog_key, work_modality, task_hint, preference, note, created_by),
        )
        conn.commit()
        row_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.close()
        json_response(True, action="feedback_add", id=row_id, catalog_key=catalog_key,
                      preference=preference)
    except Exception as e:
        json_response(False, error=str(e))
        sys.exit(1)


@feedback.command("list")
@click.option("--key", "catalog_key", default=None)
@click.option("--work-modality", type=click.Choice(WORK_MODALITIES), default=None)
@click.option("--table", "as_table", is_flag=True)
def feedback_list(catalog_key, work_modality, as_table):
    """List model feedback entries."""
    try:
        conn = sqlite3.connect(agents_db, timeout=5)
        conn.row_factory = sqlite3.Row
        q = "SELECT * FROM model_feedback WHERE 1=1"
        params = []
        if catalog_key:
            q += " AND catalog_key=?"
            params.append(catalog_key)
        if work_modality:
            q += " AND (work_modality=? OR work_modality IS NULL)"
            params.append(work_modality)
        q += " ORDER BY updated_at DESC"
        rows = [dict(r) for r in conn.execute(q, params).fetchall()]
        conn.close()
        if as_table:
            if not rows:
                click.echo("No feedback entries.")
                return
            click.echo(f"{'ID':<5} {'KEY':<28} {'PREF':<7} {'MOD':<10} {'BY':<8} NOTE")
            for r in rows:
                click.echo(f"{r['id']:<5} {r['catalog_key']:<28} {r['preference']:<7} "
                           f"{(r.get('work_modality') or 'any'):<10} {r['created_by']:<8} "
                           f"{(r.get('note') or '')[:40]}")
            return
        json_response(True, count=len(rows), feedback=rows)
    except Exception as e:
        json_response(False, error=str(e))
        sys.exit(1)


@feedback.command("show")
@click.argument("feedback_id", type=int)
def feedback_show(feedback_id):
    """Show one feedback entry."""
    try:
        conn = sqlite3.connect(agents_db, timeout=5)
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM model_feedback WHERE id=?", (feedback_id,)).fetchone()
        conn.close()
        if not row:
            json_response(False, error=f"Feedback {feedback_id} not found")
            sys.exit(1)
        json_response(True, feedback=dict(row))
    except Exception as e:
        json_response(False, error=str(e))
        sys.exit(1)


@feedback.command("update")
@click.argument("feedback_id", type=int)
@click.option("--preference", type=click.Choice(["prefer", "avoid"]), default=None)
@click.option("--work-modality", type=click.Choice(WORK_MODALITIES), default=None)
@click.option("--task-hint", default=None)
@click.option("--note", default=None)
def feedback_update(feedback_id, preference, work_modality, task_hint, note):
    """Update a model feedback entry."""
    _require_pu_or_coa()
    try:
        conn = sqlite3.connect(agents_db, timeout=5)
        row = conn.execute("SELECT id FROM model_feedback WHERE id=?", (feedback_id,)).fetchone()
        if not row:
            conn.close()
            json_response(False, error=f"Feedback {feedback_id} not found")
            sys.exit(1)
        updates, params = [], []
        for col, val in (
            ("preference", preference), ("work_modality", work_modality),
            ("task_hint", task_hint), ("note", note),
        ):
            if val is not None:
                updates.append(f"{col}=?")
                params.append(val)
        if not updates:
            json_response(False, error="No fields to update")
            sys.exit(1)
        updates.append("updated_at=datetime('now')")
        params.append(feedback_id)
        conn.execute(f"UPDATE model_feedback SET {', '.join(updates)} WHERE id=?", params)
        conn.commit()
        conn.close()
        json_response(True, action="feedback_update", id=feedback_id)
    except Exception as e:
        json_response(False, error=str(e))
        sys.exit(1)


@feedback.command("remove")
@click.argument("feedback_id", type=int)
def feedback_remove(feedback_id):
    """Remove a model feedback entry."""
    _require_pu_or_coa()
    try:
        conn = sqlite3.connect(agents_db, timeout=5)
        cur = conn.execute("DELETE FROM model_feedback WHERE id=?", (feedback_id,))
        conn.commit()
        conn.close()
        if cur.rowcount == 0:
            json_response(False, error=f"Feedback {feedback_id} not found")
            sys.exit(1)
        json_response(True, action="feedback_remove", id=feedback_id)
    except Exception as e:
        json_response(False, error=str(e))
        sys.exit(1)


@model.command("routing-context")
@click.option("--agent", "agent_name", required=True)
@click.option("--assigned-model", required=True)
@click.option("--routing-enabled", type=int, default=0)
@click.option("--routing-mode", default="")
@click.option("--attachments", default="", help="Comma-separated attachment paths")
def model_routing_context(agent_name, assigned_model, routing_enabled, routing_mode, attachments):
    """Build ephemeral routing JSON for lifeline/harness (stdout JSON or empty)."""
    from harness.model_routing import build_routing_context, detect_required_input_modalities
    paths = [p for p in attachments.split(",") if p.strip()] if attachments else []
    ctx = build_routing_context(
        agent_name=agent_name,
        assigned_model=assigned_model,
        routing_enabled=bool(routing_enabled),
        routing_mode=routing_mode or None,
        required_input_modalities=detect_required_input_modalities(paths),
    )
    if ctx:
        print(json.dumps(ctx))
    else:
        print("")


# ─── Model Params Subcommand Group ─────────────────────
# CRUD over [model_params_custom] — layered generation defaults.

def _validate_params_scope(scope: str) -> str:
    if scope == "default":
        return scope
    if scope.startswith("provider:"):
        raise click.BadParameter(
            "Provider scopes are no longer supported — use 'model:<id>' or per-agent overrides."
        )
    if scope.startswith("model:") and len(scope) > len("model:"):
        return scope
    raise click.BadParameter(
        "Scope must be 'default' or 'model:<id>'"
    )


def _build_params_json(temperature, reasoning_effort, reasoning_max_tokens, extra,
                       allowed_reasoning_efforts=None, think_mode=None, base=None):
    from harness.model_params import build_params_layer_update
    return build_params_layer_update(
        base=base,
        temperature=temperature,
        reasoning_effort=reasoning_effort,
        reasoning_max_tokens=reasoning_max_tokens,
        allowed_reasoning_efforts=allowed_reasoning_efforts,
        think_mode=think_mode,
        extra=extra,
    )


@model.group()
def params():
    """Manage layered model generation parameters (temperature, reasoning, extra)."""
    pass


@params.command("list")
@click.option("--table", "as_table", is_flag=True, help="Human-readable table instead of JSON")
def params_list(as_table):
    """List all configured parameter layers (default, model:*)."""
    from harness.model_params import list_model_params, resolve_model_params
    layers = list_model_params()
    rows = []
    for scope, raw in sorted(layers.items()):
        resolved_hint = None
        if scope.startswith("model:"):
            model_id = scope.split(":", 1)[1]
            resolved_hint = resolve_model_params(model_id)
        rows.append({"scope": scope, "params": raw, "resolved_for_model": resolved_hint})
    if as_table:
        if not rows:
            click.echo("No model parameter layers configured.")
            return
        click.echo(f"{'SCOPE':<40} PARAMS")
        for r in rows:
            click.echo(f"{r['scope']:<40} {json.dumps(r['params'], separators=(',', ':'))}")
        return
    json_response(True, count=len(rows), layers=rows)


@params.command("get")
@click.argument("scope", callback=lambda ctx, param, value: _validate_params_scope(value))
def params_get(scope):
    """Get params for a scope, including effective resolution for model:* scopes."""
    from harness.model_params import (
        list_model_params,
        resolve_model_params,
        allowed_reasoning_efforts,
        _load_model_params_custom_layers,
    )
    layers = list_model_params()
    custom_layers = _load_model_params_custom_layers()
    payload = {
        "scope": scope,
        "params": custom_layers.get(scope, {}),
        "effective": layers.get(scope, {}),
    }
    if scope.startswith("model:"):
        model_id = scope.split(":", 1)[1]
        payload["resolved"] = resolve_model_params(model_id)
        payload["allowed_reasoning_efforts"] = list(allowed_reasoning_efforts(model_id))
    json_response(True, **payload)


@params.command("set")
@click.argument("scope", callback=lambda ctx, param, value: _validate_params_scope(value))
@click.option("--temperature", type=float, default=None, help="Sampling temperature (0.0–2.0)")
@click.option("--reasoning-effort",
              type=click.Choice(["none", "minimal", "low", "medium", "high", "max", "xhigh"]),
              default=None, help="Reasoning effort level")
@click.option("--reasoning-max-tokens", type=int, default=None, help="Reasoning token budget")
@click.option("--allowed-reasoning-efforts", default=None,
              help="CSV override for allowed reasoning levels (e.g. none,low,high,xhigh)")
@click.option("--think-mode", type=click.Choice(["boolean", "levels"]), default=None,
              help="Ollama thinking mapping: boolean (on/off) or levels (low/medium/high)")
@click.option("--extra", default=None, help="JSON object merged into the passthrough bag")
@click.option("--no-sync", is_flag=True, help="Skip regenerating inference endpoint config")
def params_set(scope, temperature, reasoning_effort, reasoning_max_tokens,
               allowed_reasoning_efforts, think_mode, extra, no_sync):
    """Set or update params for a scope (writes [model_params_custom])."""
    from harness.model_params import list_model_params
    if not any(v is not None for v in (
        temperature, reasoning_effort, reasoning_max_tokens, allowed_reasoning_efforts,
        think_mode, extra,
    )):
        json_response(False, error="Provide at least one of --temperature, --reasoning-effort, "
                      "--reasoning-max-tokens, --allowed-reasoning-efforts, --think-mode, "
                      "--extra")
        sys.exit(1)
    if extra is not None:
        try:
            parsed = json.loads(extra)
            if not isinstance(parsed, dict):
                raise ValueError("extra must be a JSON object")
        except (json.JSONDecodeError, ValueError) as e:
            json_response(False, error=f"Invalid --extra JSON: {e}")
            sys.exit(1)
    layers = list_model_params()
    merged = _build_params_json(
        temperature, reasoning_effort, reasoning_max_tokens, extra,
        allowed_reasoning_efforts=allowed_reasoning_efforts,
        think_mode=think_mode,
        base=layers.get(scope) or {},
    )
    value = json.dumps(merged, separators=(",", ":"))
    targets = _models_ini_write_targets()
    if not targets:
        json_response(False, error="models.ini not found")
        sys.exit(1)
    try:
        for path in targets:
            _upsert_models_ini_entry(path, "model_params_custom", scope, value)
    except PermissionError:
        json_response(False, error="permission denied writing models.ini (use sudo)")
        sys.exit(1)
    payload = {"scope": scope, "params": merged, "message": f"Set params for '{scope}'."}
    json_response(True, **payload)


@params.command("clear")
@click.argument("scope", callback=lambda ctx, param, value: _validate_params_scope(value))
@click.option("--no-sync", is_flag=True)
def params_clear(scope, no_sync):
    """Remove a custom params override (reverts to lower layers)."""
    if scope == "default":
        json_response(False, error="Cannot clear the system default — edit the baseline [model_params] template instead.")
        sys.exit(1)
    removed_any = False
    try:
        for path in _models_ini_write_targets():
            if _remove_ini_entry(path, "model_params_custom", scope):
                removed_any = True
    except PermissionError:
        json_response(False, error="permission denied writing models.ini (use sudo)")
        sys.exit(1)
    if not removed_any:
        json_response(False, error=f"No custom params found for scope '{scope}'")
        sys.exit(1)
    payload = {"scope": scope, "message": f"Cleared custom params for '{scope}'."}
    json_response(True, **payload)


# ─── Provider Command Group ─────────────────────────────
# CRUD over the [providers] registry in models.ini.

@cli.group()
def provider():
    """Manage cloud/local model providers (xAI, OpenAI, Anthropic, ...)."""
    pass


@provider.command("list")
@click.option("--table", "as_table", is_flag=True)
def provider_list(as_table):
    """List registered providers and their enabled state."""
    provs = _load_providers()
    rows = [{"slug": s, **info} for s, info in provs.items()]
    if as_table:
        if not rows:
            click.echo("No providers registered.")
            return
        click.echo(f"{'SLUG':<12} {'EN':<3} {'CLASS':<26} LABEL")
        for r in rows:
            click.echo(f"{r['slug']:<12} {'✓' if r['enabled'] else '·':<3} {r['cls']:<26} {r['label']}")
        return
    json_response(True, count=len(rows), providers=rows)


@provider.command("add")
@click.argument("slug")
@click.option("--label", required=True, help="Display name (e.g. 'Mistral')")
@click.option("--class", "cls", required=True, help="LangChain class (e.g. ChatOpenAI, ChatAnthropic)")
@click.option("--enable/--disable", "enabled", default=False, help="Enable immediately")
@click.option("--no-sync", is_flag=True)
def provider_add(slug, label, cls, enabled, no_sync):
    """Register a new provider. The API key is set separately via 'system set-key'."""
    provs = _load_providers()
    if slug in provs:
        json_response(False, error=f"Provider '{slug}' already exists. Use 'provider enable/disable'.")
        sys.exit(1)
    targets = _models_ini_write_targets()
    if not targets:
        json_response(False, error="models.ini not found")
        sys.exit(1)
    value = f"{'true' if enabled else 'false'}|{label}|{cls}"
    try:
        for path in targets:
            _upsert_models_ini_entry(path, "providers_custom", slug, value)
    except PermissionError:
        json_response(False, error="permission denied writing models.ini (use sudo)")
        sys.exit(1)
    _auto_sync_and_respond(
        {"provider": slug, "enabled": enabled,
         "message": f"Registered provider '{slug}'. Set its key with "
                    f"'sudo agictl system set-key {slug} <key>' if supported."},
        do_sync=not no_sync)


@provider.command("update")
@click.argument("slug")
@click.option("--label", default=None, help="New display name")
@click.option("--class", "cls", default=None, help="LangChain class (e.g. ChatOpenAI)")
@click.option("--enable/--disable", "enabled", default=None, help="Enable or disable the provider")
@click.option("--no-sync", is_flag=True)
def provider_update(slug, label, cls, enabled, no_sync):
    """Update fields on an existing provider (only provided fields change).

    Edits always land in the user layer ([providers_custom]); for a setup.ini
    baseline provider this writes a full-row override that wins at read time and
    survives `model migrate`.
    """
    provs = _load_providers()
    if slug not in provs:
        json_response(False, error=f"Provider '{slug}' not found. Add it with 'provider add'.")
        sys.exit(1)
    info = provs[slug]
    new_enabled = info["enabled"] if enabled is None else enabled
    new_label = info["label"] if label is None else label
    new_cls = info["cls"] if cls is None else cls
    value = f"{'true' if new_enabled else 'false'}|{new_label}|{new_cls}"
    try:
        for path in _models_ini_write_targets():
            _upsert_models_ini_entry(path, "providers_custom", slug, value)
    except PermissionError:
        json_response(False, error="permission denied writing models.ini (use sudo)")
        sys.exit(1)
    _auto_sync_and_respond(
        {"provider": slug, "enabled": new_enabled, "origin": info.get("origin"),
         "message": f"Updated provider '{slug}'."},
        do_sync=not no_sync)


def _provider_set_enabled(slug, enabled, no_sync):
    provs = _load_providers()
    if slug not in provs:
        json_response(False, error=f"Provider '{slug}' not found. Add it with 'provider add'.")
        sys.exit(1)
    info = provs[slug]
    # Override always lands in the custom layer — for a baseline provider this
    # writes a full-row override that wins at read time (and survives migrate).
    value = f"{'true' if enabled else 'false'}|{info['label']}|{info['cls']}"
    try:
        for path in _models_ini_write_targets():
            _upsert_models_ini_entry(path, "providers_custom", slug, value)
    except PermissionError:
        json_response(False, error="permission denied writing models.ini (use sudo)")
        sys.exit(1)
    _auto_sync_and_respond(
        {"provider": slug, "enabled": enabled, "origin": info.get("origin"),
         "message": f"Provider '{slug}' {'enabled' if enabled else 'disabled'}."},
        do_sync=not no_sync)


@provider.command("enable")
@click.argument("slug")
@click.option("--no-sync", is_flag=True)
def provider_enable(slug, no_sync):
    """Enable a provider (its models become routable once a key is present)."""
    _provider_set_enabled(slug, True, no_sync)


@provider.command("disable")
@click.argument("slug")
@click.option("--no-sync", is_flag=True)
def provider_disable(slug, no_sync):
    """Disable a provider (its models are removed from the runtime registry)."""
    _provider_set_enabled(slug, False, no_sync)


@provider.command("remove")
@click.argument("slug")
@click.option("--no-sync", is_flag=True)
def provider_remove(slug, no_sync):
    """Remove a provider from the registry (catalog rows referencing it remain).

    Custom providers are deleted; setup.ini **baseline** providers cannot be
    deleted here (migrate would re-add them) and are *disabled* via a custom
    override instead. To drop a baseline provider, remove it from setup.ini.
    """
    provs = _load_providers()
    if slug not in provs:
        json_response(False, error=f"Provider '{slug}' not found.")
        sys.exit(1)
    info = provs[slug]
    baseline_backed = info.get("origin") in ("baseline", "override")
    try:
        for path in _models_ini_write_targets():
            if baseline_backed:
                value = f"false|{info['label']}|{info['cls']}"
                _upsert_models_ini_entry(path, "providers_custom", slug, value)
            else:
                _remove_ini_entry(path, "providers_custom", slug)
    except PermissionError:
        json_response(False, error="permission denied writing models.ini (use sudo)")
        sys.exit(1)
    if baseline_backed:
        msg = (f"'{slug}' is a setup.ini baseline provider — disabled via override "
               f"(remove it from setup.ini to drop entirely).")
    else:
        msg = f"Removed custom provider '{slug}'."
    _auto_sync_and_respond({"provider": slug, "disabled_only": baseline_backed,
                            "message": msg}, do_sync=not no_sync)


@provider.command("reset")
@click.argument("slug")
@click.option("--no-sync", is_flag=True)
def provider_reset(slug, no_sync):
    """Drop [providers_custom] overrides for a baseline provider and revert to setup.ini stock.

    Custom-added providers have no baseline — use ``provider remove``.
    """
    provs = _load_providers()
    if slug not in provs:
        json_response(False, error=f"Provider '{slug}' not found.")
        sys.exit(1)
    if provs[slug].get("origin") == "custom":
        json_response(
            False,
            error=f"'{slug}' is a custom provider — no baseline to restore. Use 'provider remove'.",
        )
        sys.exit(1)

    removed = False
    try:
        for path in _models_ini_write_targets():
            if _remove_ini_entry(path, "providers_custom", slug):
                removed = True
    except PermissionError:
        json_response(False, error="permission denied writing models.ini (use sudo)")
        sys.exit(1)

    if not removed:
        json_response(
            True,
            provider=slug,
            reset=False,
            message=f"'{slug}' is already on the setup.ini baseline (no custom override).",
        )
        return

    _auto_sync_and_respond(
        {"provider": slug, "reset": True,
         "message": f"Reset '{slug}' to setup.ini baseline (custom override removed)."},
        do_sync=not no_sync,
    )


@model.group()
def registry():
    """Manage the SYCL model registry (add, update, remove, list)."""
    pass


@registry.command("list")
def registry_list():
    """List all registered SYCL models from models.ini."""
    reg = _load_sycl_registry()
    if not reg:
        click.echo("No SYCL models registered in models.ini [sycl_models].", err=True)
        json_response(True, models=[])
        return

    # Also load context windows for display
    import configparser
    ini = _models_ini_parser()
    for path in _MODELS_INI_PATHS:
        if os.path.exists(path):
            ini.read(path)
            break

    results = []
    for name, info in reg.items():
        ctx_rec, ctx_max = 0, 0
        if ini.has_section("context_windows"):
            ctx_val = ini.get("context_windows", name, fallback="")
            if "," in ctx_val:
                try:
                    ctx_rec, ctx_max = int(ctx_val.split(",")[0].strip()), int(ctx_val.split(",")[1].strip())
                except ValueError:
                    pass
        results.append({
            "name": name,
            "repo": info["repo"],
            "file": info["file"],
            "size_gb": info["size_gb"],
            "ctx_recommended": ctx_rec,
            "ctx_max": ctx_max,
        })

    json_response(True, models=results)


@registry.command("add")
@click.argument("name")
@click.option("--repo", required=True, help="HuggingFace repository (e.g. unsloth/Llama-4-8B-GGUF)")
@click.option("--file", "gguf_file", required=True, help="GGUF filename (e.g. Llama-4-8B-Q4_K_M.gguf)")
@click.option("--size", "size_gb", required=True, type=int, help="Approximate GGUF size in GB")
@click.option("--ctx-recommended", type=int, default=0, help="Recommended context window (tokens)")
@click.option("--ctx-max", type=int, default=0, help="Maximum context window (tokens)")
@click.option("--label", default="", help="Display label for agitop (e.g. 'Llama 4 8B — Dense, 128K context')")
def registry_add(name, repo, gguf_file, size_gb, ctx_recommended, ctx_max, label):
    """Register a new SYCL model in models.ini."""
    # Check for duplicate
    reg = _load_sycl_registry()
    if name in reg:
        json_response(False, error=f"Model '{name}' already exists. Use 'agictl model registry update {name}' instead.")
        sys.exit(1)

    # Write to all models.ini copies
    entry_value = f"{repo},{gguf_file},{size_gb}"
    for ini_path in _MODELS_INI_PATHS:
        if os.path.exists(ini_path):
            _update_models_ini_entry(ini_path, "sycl_models", name, entry_value)
            # Also register context window if provided
            if ctx_recommended > 0 or ctx_max > 0:
                ctx_val = f"{ctx_recommended},{ctx_max}"
                _update_models_ini_entry(ini_path, "context_windows", name, ctx_val)
            # Also register display label
            display_label = label if label else name
            _update_models_ini_entry(ini_path, "local_models", name, display_label)

    json_response(True, model=name, repo=repo, file=gguf_file, size_gb=size_gb,
                  ctx_recommended=ctx_recommended, ctx_max=ctx_max,
                  message=f"Registered '{name}' in SYCL model registry")


@registry.command("update")
@click.argument("name")
@click.option("--repo", default=None, help="Updated HuggingFace repository")
@click.option("--file", "gguf_file", default=None, help="Updated GGUF filename")
@click.option("--size", "size_gb", default=None, type=int, help="Updated GGUF size in GB")
@click.option("--ctx-recommended", type=int, default=None, help="Updated recommended context")
@click.option("--ctx-max", type=int, default=None, help="Updated maximum context")
@click.option("--label", default=None, help="Updated display label")
def registry_update(name, repo, gguf_file, size_gb, ctx_recommended, ctx_max, label):
    """Update an existing SYCL model in the registry."""
    reg = _load_sycl_registry()
    if name not in reg:
        json_response(False, error=f"Model '{name}' not found in registry. Use 'agictl model registry add' first.")
        sys.exit(1)

    current = reg[name]
    new_repo = repo if repo else current["repo"]
    new_file = gguf_file if gguf_file else current["file"]
    new_size = size_gb if size_gb is not None else current["size_gb"]

    entry_value = f"{new_repo},{new_file},{new_size}"

    # Update in all models.ini copies using direct sed-like replacement
    for ini_path in _MODELS_INI_PATHS:
        if os.path.exists(ini_path):
            # Read, find and replace the sycl_models entry
            with open(ini_path, "r") as f:
                content = f.read()
            import re
            # Match the key within [sycl_models] section
            pattern = rf'(^{re.escape(name)}\s*=\s*).*'
            new_content = re.sub(pattern, rf'\g<1>{entry_value}', content, count=1, flags=re.MULTILINE)
            if new_content != content:
                with open(ini_path, "w") as f:
                    f.write(new_content)

            # Update context windows if provided
            if ctx_recommended is not None or ctx_max is not None:
                import configparser
                ini = _models_ini_parser()
                ini.read(ini_path)
                old_rec, old_max = 4096, 131072
                if ini.has_section("context_windows"):
                    ctx_val = ini.get("context_windows", name, fallback="4096,131072")
                    parts = ctx_val.split(",")
                    if len(parts) == 2:
                        try:
                            old_rec, old_max = int(parts[0].strip()), int(parts[1].strip())
                        except ValueError:
                            pass
                final_rec = ctx_recommended if ctx_recommended is not None else old_rec
                final_max = ctx_max if ctx_max is not None else old_max
                ctx_entry = f"{final_rec},{final_max}"
                with open(ini_path, "r") as f:
                    content = f.read()
                pattern = rf'(^\[context_windows\].*?^{re.escape(name)}\s*=\s*).*'
                new_content = re.sub(pattern, rf'\g<1>{ctx_entry}', content, count=1, flags=re.MULTILINE | re.DOTALL)
                if new_content != content:
                    with open(ini_path, "w") as f:
                        f.write(new_content)

            # Update label if provided
            if label is not None:
                with open(ini_path, "r") as f:
                    content = f.read()
                pattern = rf'(^\[local_models\].*?^{re.escape(name)}\s*=\s*).*'
                new_content = re.sub(pattern, rf'\g<1>{label}', content, count=1, flags=re.MULTILINE | re.DOTALL)
                if new_content != content:
                    with open(ini_path, "w") as f:
                        f.write(new_content)

    json_response(True, model=name, repo=new_repo, file=new_file, size_gb=new_size,
                  message=f"Updated '{name}' in SYCL model registry")


@registry.command("remove")
@click.argument("name")
def registry_remove(name):
    """Remove a SYCL model from the registry."""
    reg = _load_sycl_registry()
    if name not in reg:
        json_response(False, error=f"Model '{name}' not found in SYCL registry.")
        sys.exit(1)

    for ini_path in _MODELS_INI_PATHS:
        if os.path.exists(ini_path):
            with open(ini_path, "r") as f:
                lines = f.readlines()
            # Remove from [sycl_models] section only
            new_lines = []
            in_sycl = False
            for line in lines:
                stripped = line.strip()
                if stripped.startswith("[") and stripped.endswith("]"):
                    in_sycl = (stripped == "[sycl_models]")
                if in_sycl and stripped and not stripped.startswith("#") and not stripped.startswith("["):
                    eq_pos = stripped.find("=")
                    if eq_pos > 0 and stripped[:eq_pos].strip() == name:
                        continue  # Skip this line
                new_lines.append(line)
            with open(ini_path, "w") as f:
                f.writelines(new_lines)

    json_response(True, model=name, message=f"Removed '{name}' from SYCL registry. Note: [context_windows] and [local_models] entries preserved.")

def _update_ini_key(ini_path, section, key, value):
    """Update a single key in an INI file in-place.

    If the key already exists under [section], its value is replaced.
    If the key does not exist but the section does, the key is appended
    after the last non-blank line of that section.
    Operates line-by-line to avoid regex mis-matches across sections.
    """
    with open(ini_path, "r") as f:
        lines = f.readlines()

    current_section = None
    key_found = False
    section_end_idx = -1  # last content line index within target section

    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            current_section = stripped[1:-1]
            continue
        if current_section == section:
            if stripped and not stripped.startswith("#"):
                section_end_idx = i
            eq = stripped.find("=")
            if eq > 0 and stripped[:eq].strip() == key and not key_found:
                lines[i] = f"{key}={value}\n"
                key_found = True

    if not key_found and section_end_idx >= 0:
        # Append after the last content line in the section
        lines.insert(section_end_idx + 1, f"{key}={value}\n")

    with open(ini_path, "w") as f:
        f.writelines(lines)


def _read_paths_env_key(key):
    """Read a single value from paths.env."""
    if not os.path.isfile(PATHS_ENV_FILE):
        return None
    with open(PATHS_ENV_FILE, "r") as f:
        for line in f:
            if line.strip().startswith(f"{key}="):
                val = line.strip().split("=", 1)[1].strip().strip('"')
                return val
    return None


# ═══════════════════════════════════════════════════════
# 2. AGENT — Agent registry & status
# ═══════════════════════════════════════════════════════

@cli.group()
def agent():
    """Agent registry, status, and lifecycle management."""
    pass

@agent.command("list")
@click.option("--all", "show_all", is_flag=True, help="Include inactive agents")
def agent_list(show_all):
    """List agents as JSON."""
    if show_all:
        agents = agent_reader.get_all_agents()
    else:
        agents = agent_reader.get_active_agents()
    print(json.dumps(agents, indent=2, default=str))

@agent.command("show")
@click.argument("name")
def agent_show(name):
    """Return all DB fields for a specific agent as JSON."""
    try:
        conn = sqlite3.connect(agents_db, timeout=5)
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM agents WHERE name=?", (name,)).fetchone()
        conn.close()
        if row:
            print(json.dumps(dict(row), indent=2, default=str))
        else:
            json_response(False, error=f"Agent '{name}' not found")
    except Exception as e:
        json_response(False, error=str(e))

@agent.command("add")
@click.argument("name")
@click.option("--role", default="dev", type=click.Choice(VALID_ROLES), help="Agent role (from Sub-Agent Role Registry)")
def agent_add(name, role):
    """Register a new sub-agent in the database (metadata only).

    Creates a pending agent record (inactive=1, status=pending_approval).
    The Primary User must approve via the agitop dashboard to provision
    the OS user, home directory, and activate the agent for spawning.
    """
    name = name.lower()
    os_user = f"agi-{name}"  # OS user gets agi- prefix; DB name is the social name
    agent_root = f"/home/{os_user}"
    try:
        conn = sqlite3.connect(agents_db, timeout=5)
        conn.row_factory = sqlite3.Row
        # Guard: block creation if any inactive agent is pending approval
        pending = conn.execute(
            "SELECT name FROM agents WHERE inactive=1 AND protected=0"
        ).fetchone()
        if pending:
            conn.close()
            json_response(False, error=f"Agent creation blocked: '{pending['name']}' is still pending approval. "
                          "Wait for the Primary User to approve or remove it before creating another agent.")
            sys.exit(1)
        # Check if agent already exists (idempotent)
        existing = conn.execute("SELECT * FROM agents WHERE name=?", (name,)).fetchone()
        if existing:
            conn.close()
            result = dict(existing)
            result["note"] = "Agent already exists"
            print(json.dumps({"success": True, **{k: str(v) if v is not None else None for k, v in result.items()}}))
            return
        role_label = ROLE_LABELS.get(role, role)

        # ── Resolve per-role settings from role.ini (if present) ──
        role_model = None
        role_anchor_style = "compact"
        poise_dir = os.path.join(ROLES_DIR, role)
        role_ini = os.path.join(poise_dir, "role.ini") if os.path.isdir(poise_dir) else None
        if role_ini and os.path.isfile(role_ini):
            import configparser
            cfg = configparser.ConfigParser()
            try:
                cfg.read(role_ini)
                if cfg.has_option("gemini", "model"):
                    _m = cfg.get("gemini", "model").strip()
                    if _m:
                        role_model = _m
                if cfg.has_option("poise", "anchor_style"):
                    _a = cfg.get("poise", "anchor_style").strip().lower()
                    if _a in ("full", "compact"):
                        role_anchor_style = _a
            except Exception:
                pass

        # ── Read defaults from setup.ini ──
        default_timeout = 60
        default_runaway = 300
        # Canonical location: /etc/versa-agi/setup.ini
        setup_ini = "/etc/versa-agi/setup.ini"
        if not os.path.isfile(setup_ini):
            # Dev fallback: relative to source tree
            setup_ini = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "setup.ini")
        if os.path.isfile(setup_ini):
            import configparser
            _ini = configparser.ConfigParser()
            try:
                _ini.read(setup_ini)
                if _ini.has_option("agent", "timeout_minutes"):
                    default_timeout = int(_ini.get("agent", "timeout_minutes"))
                if _ini.has_option("agent", "runaway_threshold"):
                    default_runaway = int(_ini.get("agent", "runaway_threshold"))
            except Exception:
                pass

        # ── Resolve num_ctx from model context map ──
        role_num_ctx = 0
        if role_model:
            try:
                sys.path.insert(0, '/usr/local/lib/versa-agi')
                from harness.model_context import get_model_context
                recommended, _ = get_model_context(role_model)
                role_num_ctx = recommended
            except ImportError:
                role_num_ctx = 4096  # Safe default for local models

        # ── INSERT into agents DB (pending approval) ──
        conn.execute(
            "INSERT INTO agents (name, os_user, workspace, role, model, status, inactive, protected, requested_by, timeout_minutes, runaway_threshold, anchor_style, num_ctx) "
            "VALUES (?, ?, ?, ?, ?, 'pending_approval', 1, 0, ?, ?, ?, ?, ?)",
            (name, name, agent_root, role_label, role_model, get_agent_name(), default_timeout, default_runaway, role_anchor_style, role_num_ctx)
        )
        conn.commit()
        conn.close()
        result_data = dict(agent=name, os_user=os_user, role=role_label, workspace=agent_root,
                           status="pending_approval", inactive=True)
        if role_model:
            result_data["model"] = role_model
        json_response(True, **result_data,
                      note="Agent registered. The Primary User must approve via the agitop dashboard "
                           "to provision the OS user and activate for spawning.")
    except Exception as e:
        json_response(False, error=str(e))
        sys.exit(1)


@agent.command("list-roles")
def agent_list_roles():
    """List available sub-agent role templates from the deployed registry."""
    import configparser
    roles = []
    if os.path.isdir(ROLES_DIR):
        for entry in sorted(os.listdir(ROLES_DIR)):
            entry_path = os.path.join(ROLES_DIR, entry)
            # V3.1: Directory-based roles (role_id/poise.md + role_id/role.ini)
            if os.path.isdir(entry_path):
                role_id = entry
                poise_path = os.path.join(entry_path, "poise.md")
                ini_path = os.path.join(entry_path, "role.ini")
                role_data = {"id": role_id, "name": ROLE_LABELS.get(role_id, role_id)}
                if os.path.isfile(poise_path):
                    role_data["poise_path"] = poise_path
                if os.path.isfile(ini_path):
                    role_data["config_path"] = ini_path
                    cfg = configparser.ConfigParser()
                    try:
                        cfg.read(ini_path)
                        if cfg.has_option("role", "description"):
                            role_data["description"] = cfg.get("role", "description")
                        if cfg.has_option("gemini", "model"):
                            model_val = cfg.get("gemini", "model").strip()
                            if model_val:
                                role_data["model"] = model_val
                    except Exception:
                        pass
                roles.append(role_data)
            # Legacy: flat poise-*.md files (backward compat)
            elif entry.startswith("poise-") and entry.endswith(".md"):
                role_id = entry.replace("poise-", "").replace(".md", "")
                label = ROLE_LABELS.get(role_id, role_id)
                roles.append({"id": role_id, "name": label, "poise_path": os.path.join(ROLES_DIR, entry)})
    if roles:
        print(json.dumps(roles, indent=2))
    else:
        print(json.dumps({"error": f"No role templates found in {ROLES_DIR}"}))


@agent.command("approve", hidden=True)
@click.argument("name")
@click.option("--force", is_flag=True, help="Force re-provisioning of an already active agent.")
def agent_approve(name, force):
    """Approve a pending sub-agent — provisions OS user, scaffolds home, activates.

    This command requires root privileges (useradd). It is called by the
    agitop dashboard's 'Approve & Provision' button, NOT by agents directly.
    """
    import shutil
    name = name.lower()
    os_user = f"agi-{name}"  # OS user gets agi- prefix; DB name is the social name
    agent_root = f"/home/{os_user}"
    try:
        conn = sqlite3.connect(agents_db, timeout=5)
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM agents WHERE name=?", (name,)).fetchone()
        if not row:
            json_response(False, error=f"Agent '{name}' not found")
            sys.exit(1)
        if row["inactive"] == 0 and not force:
            json_response(True, agent=name, note="Already active")
            return

        role_label = row["role"] or "Custom Agent"
        reverse_roles = {v: k for k, v in ROLE_LABELS.items()}
        role_id = reverse_roles.get(role_label, "custom")

        # ── Create OS user with dedicated home ──
        # --user-group: create a per-user group matching os_user (required for §IX credential isolation)
        # --groups agi_agents: add to shared collaboration group as supplementary
        result = subprocess.run(
            ["useradd", "--system", "--user-group", "--home-dir", agent_root, "--create-home",
             "--shell", "/usr/sbin/nologin", "--groups", "agi_agents", os_user],
            capture_output=True, text=True
        )
        if result.returncode != 0:
            if "already exists" not in result.stderr:
                conn.close()
                json_response(False, error=f"Failed to create OS user '{os_user}': {result.stderr.strip()}")
                sys.exit(1)

        # ── Scaffold directory structure ──
        for d in [os.path.join(agent_root, ".agent", "skills"),
                  os.path.join(agent_root, ".agent", "config"),
                  os.path.join(agent_root, "workspace")]:
            os.makedirs(d, exist_ok=True)

        # ── Copy poise template from deployed roles ──
        agent_data_dir = f"/var/lib/versa-agi/{name}"
        os.makedirs(agent_data_dir, exist_ok=True)
        subprocess.run(["chown", f"watchdog:{os_user}", agent_data_dir], check=False)
        subprocess.run(["chmod", "750", agent_data_dir], check=False)

        poise_dir = os.path.join(ROLES_DIR, role_id)
        poise_source = os.path.join(poise_dir, "poise.md") if os.path.isdir(poise_dir) else os.path.join(ROLES_DIR, f"poise-{role_id}.md")

        # Create canonical poise: /etc/versa-agi/poise/{agent_name}.md (flat copy from role template)
        # Lifeline and agitop resolve poise at this deterministic path.
        poise_canonical = f"/etc/versa-agi/poise/{name}.md"
        if name != "coa" and os.path.exists(poise_source):
            shutil.copy2(poise_source, poise_canonical)
            subprocess.run(["chown", "watchdog:watchdog", poise_canonical], check=False)
            subprocess.run(["chmod", "640", poise_canonical], check=False)
            click.echo(f"  ✓ Poise deployed: {poise_canonical} (from roles/{role_id})")

        # Legacy: also copy to /var/lib for agent-local reference
        poise_dest = os.path.join(agent_data_dir, "poise.md")
        if os.path.exists(poise_source):
            shutil.copy2(poise_source, poise_dest)
        elif not os.path.exists(poise_dest):
            with open(poise_dest, "w") as f:
                f.write(f"# {role_label}\n\nRole poise template not found at {poise_source}.\n")
                f.write("Request deployment of role templates via the Primary User.\n")

        # ── Create duties.md (COA-writable assignment brief) ──
        duties_dest = os.path.join(agent_data_dir, "duties.md")
        duties_is_default = False
        if not os.path.exists(duties_dest):
            # No h1/h2 headings — this file is injected verbatim under a
            # '## ── DUTIES & ASSIGNMENT ──' section header by Lifeline, so its
            # internal headings must start at ### to preserve the hierarchy.
            with open(duties_dest, "w") as f:
                f.write(f"**Agent:** {name}  |  **Role:** {role_label}\n\n")
                f.write("### Duties\n\n_Define this agent's specific duties, projects, and objectives here._\n\n")
                f.write("### Notes\n\n_Add any operational notes, constraints, or context._\n")
            duties_is_default = True

        # ── Create .agent/system.md placeholder ──
        system_md = os.path.join(agent_root, ".agent", "system.md")
        if not os.path.exists(system_md):
            with open(system_md, "w") as f:
                f.write("# System prompt — generated by Lifeline before each spawn\n")

        # ── Create README ──
        readme_path = os.path.join(agent_root, "README.md")
        if not os.path.exists(readme_path):
            with open(readme_path, "w") as f:
                f.write(f"# Agent: {name}\n\n**Role:** {role_label}  \n**OS User:** `{os_user}`  \n**Home:** `{agent_root}`\n\n")
                f.write("Provisioned by `agictl agent approve`.\n\n")
                f.write("## Rules\n\n- ALL project work MUST be inside `workspace/<project>/` directories\n")
                f.write("- Use `agictl` for ALL infrastructure interaction\n- NEVER create project files outside `workspace/`\n\n")
                f.write("## Directory Layout\n\n```\n")
                f.write(f"{os_user}/\n├── .agent/           # Agent metadata + system prompt\n│   ├── system.md     # Generated by Lifeline each spawn\n│   ├── skills/\n│   └── config/\n├── workspace/        # 770\n└── README.md\n```\n")

        # ── Set ownership & permissions ──
        subprocess.run(["chown", "-R", f"{os_user}:agi_agents", agent_root], check=False)
        subprocess.run(["chmod", "770", agent_root], check=False)
        subprocess.run(["chmod", "2770", os.path.join(agent_root, "workspace")], check=False)
        subprocess.run(["chmod", "770", os.path.join(agent_root, ".agent")], check=False)
        subprocess.run(["chown", f"watchdog:{os_user}", duties_dest], check=False)
        subprocess.run(["chmod", "660", duties_dest], check=False)
        subprocess.run(["chown", f"watchdog:{os_user}", poise_dest], check=False)
        subprocess.run(["chmod", "440", poise_dest], check=False)
        subprocess.run(["chmod", "664", readme_path], check=False)
        
        # ── Setup persistent data directories (CYCLES) ──
        agent_cycles_dir = os.path.join(agent_data_dir, "cycles")
        os.makedirs(agent_cycles_dir, exist_ok=True)
        subprocess.run(["chown", "-R", f"{os_user}:{os_user}", agent_cycles_dir], check=False)
        subprocess.run(["chmod", "755", agent_cycles_dir], check=False)
        view_cache_dir = os.path.join(agent_data_dir, "view-cache")
        os.makedirs(view_cache_dir, exist_ok=True)
        subprocess.run(["chown", "-R", f"{os_user}:{os_user}", view_cache_dir], check=False)
        subprocess.run(["chmod", "770", view_cache_dir], check=False)
        # system.md: Lifeline writes, everyone reads (444)
        system_md_file = os.path.join(agent_root, ".agent", "system.md")
        if os.path.exists(system_md_file):
            subprocess.run(["chown", f"watchdog:agi_agents", system_md_file], check=False)
            subprocess.run(["chmod", "444", system_md_file], check=False)

        # ── Auto-assign to shared system projects if they exist ──
        # Shared system projects live physically in COA's workspace and are
        # symlinked into every agent's workspace/ at creation time:
        #   AGi-Tools          — shared scripts and tooling
        #   AGi-Knowledgebase  — collaborative PU/agent documentation (Grav CMS source)
        SHARED_SYSTEM_PROJECTS = ["AGi-Tools", "AGi-Knowledgebase"]
        try:
            conn_tasks = sqlite3.connect(tasks_db, timeout=5)
            conn_tasks.row_factory = sqlite3.Row
            for shared_name in SHARED_SYSTEM_PROJECTS:
                shared_proj = conn_tasks.execute(
                    "SELECT id, workspace_path FROM projects WHERE name=?", (shared_name,)
                ).fetchone()
                if not shared_proj:
                    continue
                shared_path = shared_proj["workspace_path"]
                shared_id = shared_proj["id"]
                agent_shared_link = os.path.join(agent_root, "workspace", shared_name)
                if os.path.exists(shared_path) and not os.path.exists(agent_shared_link):
                    os.symlink(shared_path, agent_shared_link)
                    subprocess.run(["chown", "-h", f"{os_user}:agi_agents", agent_shared_link], check=False)

                # Auto-assign physical db record to grant query access
                conn_tasks.execute(
                    "INSERT OR IGNORE INTO project_members (project_id, member_type, member_id, display_name, workspace_path, branch, roles) "
                    "VALUES (?, 'agent', ?, ?, ?, 'main', 'contributor')",
                    (shared_id, name, name.upper(), agent_shared_link)
                )
            conn_tasks.commit()
            conn_tasks.close()
        except Exception:
            pass

        # ── Clone COA's .env for Gemini API key ──
        coa_env_path = "/etc/versa-agi/coa.env"
        agent_env_path = f"/etc/versa-agi/{name}.env"
        if os.path.exists(coa_env_path):
            shutil.copy2(coa_env_path, agent_env_path)
            subprocess.run(["chown", f"watchdog:{os_user}", agent_env_path], check=False)
            subprocess.run(["chmod", "640", agent_env_path], check=False)

        # ── Generate SSH keypair for Git operations ──
        ssh_dir = os.path.join(agent_root, ".ssh")
        key_path = os.path.join(ssh_dir, "versa_agi_ed25519")
        pub_key_path = key_path + ".pub"
        ssh_public_key = ""
        if not os.path.exists(key_path):
            os.makedirs(ssh_dir, exist_ok=True)
            os.chmod(ssh_dir, 0o700)
            subprocess.run(
                ["ssh-keygen", "-t", "ed25519", "-f", key_path, "-N", "", "-C", f"{os_user}@versa-agi"],
                capture_output=True, text=True, timeout=30
            )
            os.chmod(key_path, 0o600)
            os.chmod(pub_key_path, 0o644)
            subprocess.run(["chown", "-R", f"{os_user}:agi_agents", ssh_dir], check=False)
            # SSH config for GitHub/GitLab
            ssh_config = os.path.join(ssh_dir, "config")
            config_entries = (
                f"\nHost github.com\n  IdentityFile {key_path}\n  IdentitiesOnly yes\n  StrictHostKeyChecking accept-new\n"
                f"\nHost gitlab.com\n  IdentityFile {key_path}\n  IdentitiesOnly yes\n  StrictHostKeyChecking accept-new\n"
            )
            with open(ssh_config, "w") as f:
                f.write(config_entries)
            os.chmod(ssh_config, 0o644)
            subprocess.run(["chown", f"{os_user}:agi_agents", ssh_config], check=False)
        if os.path.exists(pub_key_path):
            with open(pub_key_path) as f:
                ssh_public_key = f.read().strip()

        # ── Configure git identity + credential helper ──
        git_name = f"{name.capitalize()} (AGi Agent)"
        # Read PU email from COA config if available
        try:
            with open("/etc/versa-agi/coa_config.json", "r") as f:
                coa_cfg = json.load(f)
            pu_email = coa_cfg.get("primary_user", {}).get("email", "")
        except Exception:
            pu_email = ""
        git_email = pu_email if pu_email else f"{name}@versa-agi.local"
        # Set git config for the agent's home directory
        git_config_path = os.path.join(agent_root, ".gitconfig")
        git_config_content = (
            f"[user]\n\tname = {git_name}\n\temail = {git_email}\n"
            f"[credential]\n\thelper = store\n"
        )
        with open(git_config_path, "w") as f:
            f.write(git_config_content)
        os.chmod(git_config_path, 0o644)
        subprocess.run(["chown", f"{os_user}:agi_agents", git_config_path], check=False)

        # ── Deploy system skills ──
        skills_source = "/home/watchdog/core-infra/skills"
        skills_dest = os.path.join(agent_root, ".agent", "skills")
        os.makedirs(skills_dest, exist_ok=True)
        subprocess.run(["chmod", "775", skills_dest], check=False)
        if os.path.isdir(skills_source):
            import glob
            for skill_file in glob.glob(os.path.join(skills_source, "*.md")):
                basename = os.path.basename(skill_file)
                if basename == "README.md":
                    continue  # Skip skills index
                dest_file = os.path.join(skills_dest, basename)
                if os.path.exists(dest_file):
                    os.remove(dest_file)
                shutil.copy(skill_file, dest_file)
                subprocess.run(["chown", f"watchdog:agi_agents", dest_file], check=False)
                subprocess.run(["chmod", "440", dest_file], check=False)

        # ── Provision VersaVoice Identity + Config ──
        # Lifeline requires /etc/versa-agi/{name}_config.json for every agent.
        # This creates the VV sub-account and writes the config file with
        # inherited credentials from the COA config.
        agent_config_path = f"/etc/versa-agi/{name}_config.json"
        vv_provisioned = False
        if not os.path.exists(agent_config_path):
            try:
                # Read VV API token from COA config
                coa_cfg_path = "/etc/versa-agi/coa_config.json"
                vv_token = None
                if os.path.exists(coa_cfg_path):
                    with open(coa_cfg_path, "r") as cf:
                        coa_cfg = json.load(cf)
                    vv_token = coa_cfg.get("versavoice", {}).get("api_token")

                if vv_token:
                    # Derive display name: capitalize the agent OS name
                    display_first = name.replace("-", " ").replace("_", " ").title()
                    display_last = "(Agent)"
                    vv_provisioned = provision_identity(
                        name, vv_token,
                        first_name=display_first,
                        last_name=display_last,
                        language="en",
                        country="",
                        voice="female",
                        agents_db=agents_db,
                    )
                else:
                    # No VV token — create a minimal config so Lifeline doesn't skip this agent
                    minimal_config = {
                        "agent": name,
                        "identity": {"first_name": name.title(), "last_name": "(Agent)", "language": "en"},
                        "versavoice": {"sub_account_id": None, "api_token": None, "status": "pending_setup"},
                    }
                    with open(agent_config_path, "w") as f:
                        json.dump(minimal_config, f, indent=2)
                    subprocess.run(["chown", f"watchdog:{os_user}", agent_config_path], check=False)
                    subprocess.run(["chmod", "640", agent_config_path], check=False)
            except Exception:
                # Non-fatal — agent can still run without VV comms
                if not os.path.exists(agent_config_path):
                    minimal_config = {
                        "agent": name,
                        "identity": {"first_name": name.title(), "last_name": "(Agent)", "language": "en"},
                        "versavoice": {"sub_account_id": None, "api_token": None, "status": "pending_setup"},
                    }
                    with open(agent_config_path, "w") as f:
                        json.dump(minimal_config, f, indent=2)
                    subprocess.run(["chown", f"watchdog:{os_user}", agent_config_path], check=False)
                    subprocess.run(["chmod", "640", agent_config_path], check=False)

        # ── Activate in DB ──
        conn.execute(
            "UPDATE agents SET os_user=?, inactive=0, status='idle', updated_at=datetime('now') WHERE name=?",
            (os_user, name)
        )
        conn.commit()
        conn.close()
        result_kwargs = dict(agent=name, status="approved", inactive=False, workspace=agent_root,
                             wake_guidance="The first VersaVoice message sent to this agent "
                                           "will trigger their first wake cycle.",
                             ssh_public_key=ssh_public_key)
        if duties_is_default:
            result_kwargs["duties_warning"] = (
                f"duties.md is still the default template. Use 'sudo agictl agent set-duties {name} <file>' "
                "to provide COA-authored duties before the agent's first wake cycle."
            )
        json_response(True, **result_kwargs)
    except Exception as e:
        json_response(False, error=str(e))
        sys.exit(1)

@agent.command("activate")
@click.argument("name")
def agent_activate(name):
    """Set inactive=0 and clear circuit breaker if tripped. Also unfreezes tasks."""
    name = name.lower()
    try:
        conn = sqlite3.connect(agents_db, timeout=5)
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT inactive, status FROM agents WHERE name=?", (name,)).fetchone()
        if not row:
            conn.close()
            json_response(False, error=f"Agent '{name}' not found in registry")
            sys.exit(1)

        was_circuit_broken = (row["status"] or "") in ("circuit_breaker", "halted")
        was_inactive = row["inactive"] == 1

        if not was_inactive and not was_circuit_broken:
            conn.close()
            json_response(True, agent=name, note="Already active")
            return

        # Warn if exceeding concurrent limit (Lifeline enforces the actual cap)
        warning = None
        if was_inactive:
            active_count = conn.execute("SELECT COUNT(*) as c FROM agents WHERE inactive=0").fetchone()["c"]
            if active_count >= MAX_ACTIVE_AGENTS:
                warning = f"Note: {active_count + 1} agents will be active (Lifeline concurrent cap: {MAX_ACTIVE_AGENTS}). Extra agents will be queued."

        # Activate agent + clear circuit breaker status
        conn.execute(
            "UPDATE agents SET inactive=0, status=NULL, status_message=NULL, updated_at=datetime('now') WHERE name=?",
            (name,)
        )
        conn.commit()
        conn.close()

        result = {"agent": name, "status": "active"}

        # Unfreeze tasks if recovering from circuit breaker
        if was_circuit_broken:
            # Clear failed cycle records that caused the trip — prevents immediate re-trip
            try:
                cycles_db_path = os.environ.get("AGICTL_CYCLES_DB", "/var/lib/versa-agi/coa/cycles.db")
                if os.path.exists(cycles_db_path):
                    cconn = sqlite3.connect(cycles_db_path, timeout=5)
                    cconn.execute(
                        "DELETE FROM cycles WHERE id LIKE ? AND exit_code IN (1, 42, 99)",
                        (f"{name}-%",)
                    )
                    cconn.commit()
                    cconn.close()
            except Exception:
                pass  # Non-fatal — breaker may re-trip but won't crash

            try:
                import subprocess
                subprocess.run(
                    ["/usr/local/bin/agictl", "task", "unfreeze-all", name],
                    capture_output=True, timeout=10
                )
                result["circuit_breaker"] = "cleared"
                result["tasks"] = "unfrozen"
            except Exception:
                result["circuit_breaker"] = "cleared"
                result["tasks"] = "unfreeze failed — run 'agictl task unfreeze-all' manually"

        if warning:
            result["warning"] = warning
        json_response(True, **result)
    except Exception as e:
        json_response(False, error=str(e))
        sys.exit(1)

@agent.command("deploy-skills", hidden=True)
@click.argument("name")
def agent_deploy_skills(name):
    """Deploy system skills to a sub-agent's workspace via rsync.

    Mirrors read-only system skills from /home/watchdog/core-infra/skills/
    to the agent's .agent/skills/ directory using rsync --delete.
    Skills removed from the source are automatically cleaned from the target.
    COA-only skills (scope='coa_only') are excluded from sub-agent deploys.
    Requires root privileges.
    Called by COA via sudo agictl or by the agitop dashboard.
    """
    name = name.lower()
    # Resolve os_user from agents.db
    try:
        conn = sqlite3.connect(agents_db, timeout=5)
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT os_user FROM agents WHERE name=?", (name,)).fetchone()

        # Build dynamic exclude list from coa_only skills
        coa_only_rows = conn.execute(
            "SELECT name FROM skills WHERE scope='coa_only'"
        ).fetchall()
        # Agent-created skills live in COA's skills dir, not the shipped source.
        # They are distributed via 'agent share-skill' — exclude them here so
        # rsync --delete does not wipe previously-shared copies from sub-agents.
        agent_created_rows = conn.execute(
            "SELECT name FROM skills WHERE type='agent_created'"
        ).fetchall()
        conn.close()
        os_user = row["os_user"] if row else f"agi-{name}"
    except Exception:
        os_user = f"agi-{name}"
        coa_only_rows = []
        agent_created_rows = []
    agent_root = f"/home/{os_user}"
    skills_source = "/home/watchdog/core-infra/skills/"
    skills_dest = os.path.join(agent_root, ".agent", "skills") + "/"

    if not os.path.isdir(agent_root):
        json_response(False, error=f"Agent workspace not found: {agent_root}")
        sys.exit(1)

    if not os.path.isdir(skills_source):
        json_response(False, error=f"Skills source not found: {skills_source}")
        sys.exit(1)

    os.makedirs(skills_dest, exist_ok=True)
    # Set directory ownership per file manifest: {os_user}:agi_agents 775
    subprocess.run(["chown", f"{os_user}:agi_agents", skills_dest], check=False)
    subprocess.run(["chmod", "775", skills_dest], check=False)

    # Build rsync command with dynamic exclusions
    rsync_cmd = [
        "rsync", "-a", "--delete",
        "--exclude", "README.md",          # Never deploy the skills index
    ]
    # Exclude COA-only skills from sub-agent deploys
    for coa_row in coa_only_rows:
        skill_name = coa_row["name"]
        rsync_cmd.extend(["--exclude", f"{skill_name}.md"])
        rsync_cmd.extend(["--exclude", f"{skill_name}/"])  # co-located assets
    # Exclude agent-created skills (managed via share-skill, not this mirror)
    for ac_row in agent_created_rows:
        skill_name = ac_row["name"]
        rsync_cmd.extend(["--exclude", f"{skill_name}.md"])
        rsync_cmd.extend(["--exclude", f"{skill_name}/"])

    rsync_cmd.extend([skills_source, skills_dest])

    result = subprocess.run(rsync_cmd, capture_output=True, text=True)
    if result.returncode != 0:
        json_response(False, error=f"rsync failed: {result.stderr.strip()}")
        sys.exit(1)

    # rsync -a preserves source ownership (watchdog) — restore dir per §IX.4
    subprocess.run(["chown", f"{os_user}:agi_agents", skills_dest], check=False)
    subprocess.run(["chmod", "775", skills_dest], check=False)

    # Post-rsync permissions: shipped .md files -> watchdog:agi_agents 440
    deployed = 0
    asset_dirs_deployed = 0
    import glob
    for skill_file in glob.glob(os.path.join(skills_dest, "*.md")):
        subprocess.run(["chown", f"watchdog:agi_agents", skill_file], check=False)
        subprocess.run(["chmod", "440", skill_file], check=False)
        deployed += 1

    # Fix asset directory permissions
    for item in os.listdir(skills_dest):
        item_path = os.path.join(skills_dest, item)
        if os.path.isdir(item_path):
            subprocess.run(["chown", "-R", f"{os_user}:agi_agents", item_path], check=False)
            subprocess.run(["chmod", "-R", "755", item_path], check=False)
            asset_dirs_deployed += 1

    json_response(True, agent=name, skills_deployed=deployed, asset_dirs_deployed=asset_dirs_deployed, skills_path=skills_dest)

@agent.command("share-skill", hidden=True)
@click.argument("skill_path")
@click.option("--agent", "target_agent", default=None, help="Target specific agent (default: all active sub-agents)")
def agent_share_skill(skill_path, target_agent):
    """Share a custom skill file with sub-agents.

    Copies a skill file to active sub-agents' .agent/skills/ directories.
    Requires root privileges (called via sudo agictl).
    COA uses this to distribute custom-created skills to team members.
    """
    import shutil
    if not os.path.isfile(skill_path):
        json_response(False, error=f"Skill file not found: {skill_path}")
        sys.exit(1)
    if not skill_path.endswith(".md"):
        json_response(False, error="Skill files must be .md format")
        sys.exit(1)

    basename = os.path.basename(skill_path)

    # Get target agents
    conn = sqlite3.connect(agents_db, timeout=5)
    conn.row_factory = sqlite3.Row
    if target_agent:
        agents = conn.execute(
            "SELECT name, os_user FROM agents WHERE name=? AND name NOT IN ('coa','watchdog')",
            (target_agent.lower(),)
        ).fetchall()
    else:
        agents = conn.execute(
            "SELECT name, os_user FROM agents WHERE inactive=0 AND name NOT IN ('coa','watchdog')"
        ).fetchall()
    conn.close()

    if not agents:
        json_response(False, error="No target agents found")
        sys.exit(1)

    results = []
    skill_name = basename.replace(".md", "")
    # Check for co-located asset directory next to the skill file
    asset_src = os.path.join(os.path.dirname(skill_path), skill_name)
    has_assets = os.path.isdir(asset_src)

    for ag in agents:
        agent_name = ag["name"]
        ag_os_user = ag["os_user"] or f"agi-{agent_name}"
        skills_dest = f"/home/{ag_os_user}/.agent/skills"
        if not os.path.isdir(skills_dest):
            os.makedirs(skills_dest, exist_ok=True)
            subprocess.run(["chown", f"{ag_os_user}:agi_agents", skills_dest], check=False)
            subprocess.run(["chmod", "775", skills_dest], check=False)
        dest_file = os.path.join(skills_dest, basename)
        # Remove existing read-only file before overwriting (440 blocks shutil.copy2)
        if os.path.exists(dest_file):
            os.remove(dest_file)
        shutil.copy(skill_path, dest_file)
        subprocess.run(["chown", f"watchdog:agi_agents", dest_file], check=False)
        subprocess.run(["chmod", "440", dest_file], check=False)

        # Copy co-located asset directory if it exists
        if has_assets:
            asset_dest = os.path.join(skills_dest, skill_name)
            if os.path.isdir(asset_dest):
                shutil.rmtree(asset_dest)
            shutil.copytree(asset_src, asset_dest)
            subprocess.run(["chown", "-R", f"{ag_os_user}:agi_agents", asset_dest], check=False)
            subprocess.run(["chmod", "-R", "755", asset_dest], check=False)

        results.append(agent_name)

    json_response(True, skill=basename, deployed_to=results, count=len(results), has_assets=has_assets)

@agent.command("set-duties", hidden=True)
@click.argument("name")
@click.argument("duties_file", type=click.Path(exists=True))
def agent_set_duties(name, duties_file):
    """Copy a duties file to a sub-agent's .agent/duties.md.

    COA authors the duties markdown and uses this command (via sudo agictl)
    to provision it before the agent's first wake cycle.
    Requires root privileges.
    """
    import shutil
    name = name.lower()
    try:
        conn = sqlite3.connect(agents_db, timeout=5)
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT name, os_user, protected FROM agents WHERE name=?", (name,)).fetchone()
        conn.close()
        if not row:
            json_response(False, error=f"Agent '{name}' not found")
            sys.exit(1)
        if row["protected"] == 1:
            json_response(False, error=f"Cannot set duties for protected agent '{name}'")
            sys.exit(1)
        os_user = row["os_user"] or f"agi-{name}"
        agent_data_dir = f"/var/lib/versa-agi/{name}"
        duties_dest = os.path.join(agent_data_dir, "duties.md")
        if not os.path.isdir(agent_data_dir):
            os.makedirs(agent_data_dir, exist_ok=True)
            subprocess.run(["chown", f"watchdog:{os_user}", agent_data_dir], check=False)
            subprocess.run(["chmod", "750", agent_data_dir], check=False)
        shutil.copy2(duties_file, duties_dest)
        subprocess.run(["chown", f"watchdog:{os_user}", duties_dest], check=False)
        subprocess.run(["chmod", "660", duties_dest], check=False)
        json_response(True, agent=name, duties_path=duties_dest,
                      note=f"Duties copied from {duties_file}")
    except Exception as e:
        json_response(False, error=str(e))
        sys.exit(1)

@agent.command("deactivate")
@click.argument("name")
def agent_deactivate(name):
    """Set inactive=1. Blocked for protected agents (coa/watchdog)."""
    name = name.lower()
    try:
        conn = sqlite3.connect(agents_db, timeout=5)
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT inactive, protected FROM agents WHERE name=?", (name,)).fetchone()
        if not row:
            conn.close()
            json_response(False, error=f"Agent '{name}' not found in registry")
            sys.exit(1)
        if row["protected"]:
            conn.close()
            json_response(False, error=f"Cannot deactivate '{name}': agent is protected")
            sys.exit(1)
        if row["inactive"] == 1:
            conn.close()
            json_response(True, agent=name, note="Already inactive")
            return
        conn.execute("UPDATE agents SET inactive=1, updated_at=datetime('now') WHERE name=?", (name,))
        conn.commit()
        conn.close()
        json_response(True, agent=name, status="inactive")
    except Exception as e:
        json_response(False, error=str(e))
        sys.exit(1)

@agent.command("kill")
@click.argument("name")
def agent_kill(name):
    """Terminate a running sub-agent's cycle and prevent re-spawning.

    COA-only command. Kills the agent's harness process via pkill,
    sets status to 'halted' and inactive=1. Recovery via 'agictl agent activate'.
    Protected agents (coa, watchdog) cannot be killed.
    """
    import subprocess as _sp
    name = name.lower()

    # Guard: only COA or watchdog can kill agents.
    # AGICTL_AGENT_USER is set by the agictl-wrapper to the real OS caller and
    # forwarded across the sudo->watchdog elevation (unlike VERSA_AGENT_NAME,
    # which sudo strips). Empty => direct root/PU invocation, which is allowed.
    caller = os.environ.get("AGICTL_AGENT_USER", "")
    if caller and caller not in ("coa", "watchdog"):
        json_response(False, error=f"Permission denied: only COA or watchdog can kill agents (caller: {caller})")
        sys.exit(1)

    try:
        conn = sqlite3.connect(agents_db, timeout=5)
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT name, os_user, protected, inactive, status FROM agents WHERE name=?", (name,)).fetchone()
        if not row:
            conn.close()
            json_response(False, error=f"Agent '{name}' not found in registry")
            sys.exit(1)
        if row["protected"]:
            conn.close()
            json_response(False, error=f"Cannot kill '{name}': agent is protected")
            sys.exit(1)

        os_user = row["os_user"]
        was_running = False

        # Check if harness is running
        check = _sp.run(
            ["pgrep", "-u", os_user, "-f", "harness.agent_harness"],
            capture_output=True, text=True
        )
        if check.returncode == 0 and check.stdout.strip():
            was_running = True
            # Kill the harness process — watchdog has permission via agi_agents sudoers
            kill_result = _sp.run(
                ["pkill", "-u", os_user, "-f", "harness.agent_harness"],
                capture_output=True, text=True
            )
            if kill_result.returncode not in (0, 1):
                # returncode 1 = no process found (race condition — already exited)
                # Try fallback via sudo
                _sp.run(
                    ["sudo", "-u", os_user, "pkill", "-f", "harness.agent_harness"],
                    capture_output=True, text=True, timeout=10
                )

        # Set status to halted + inactive regardless of whether it was running
        caller_label = caller if caller else "Primary User (manual)"
        conn.execute(
            "UPDATE agents SET status='halted', status_message=?, inactive=1, updated_at=datetime('now') WHERE name=?",
            (f"Cycle terminated by {caller_label}", name)
        )
        conn.commit()
        conn.close()

        result = {"agent": name, "status": "halted", "was_running": was_running, "terminated_by": caller_label}
        if was_running:
            result["note"] = "Harness process killed. Agent will not re-spawn until activated."
        else:
            result["note"] = "No running cycle found. Agent marked halted and will not spawn until activated."
        json_response(True, **result)

    except Exception as e:
        json_response(False, error=str(e))
        sys.exit(1)

@agent.command("request-remove")
@click.argument("name")
@click.option("--reason", default="", help="Reason for removal request")
def agent_request_remove(name, reason):
    """Request removal of a sub-agent. Sets status to 'removal_requested' and deactivates.

    This is the COA-level command — no root required. The Primary User must
    confirm the removal via the agitop dashboard before the agent is purged.
    """
    name = name.lower()
    try:
        conn = sqlite3.connect(agents_db, timeout=5)
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT name, protected, status FROM agents WHERE name=?", (name,)).fetchone()
        if not row:
            conn.close()
            json_response(False, error=f"Agent '{name}' not found in registry")
            sys.exit(1)
        if row["protected"] == 1:
            conn.close()
            json_response(False, error=f"Cannot remove '{name}': agent is protected")
            sys.exit(1)
        if row["status"] == "removal_requested":
            conn.close()
            json_response(True, agent=name, note="Removal already requested — awaiting PU confirmation")
            return

        status_msg = reason if reason else "Removal requested — awaiting Primary User confirmation"
        conn.execute(
            "UPDATE agents SET status='removal_requested', inactive=1, status_message=?, updated_at=datetime('now') WHERE name=?",
            (status_msg, name)
        )
        conn.commit()
        conn.close()

        # Notify Primary User via VersaVoice
        config = get_config()
        pu = config.get("primary_user", {})
        sponsor_uid = pu.get("uid", "")
        if sponsor_uid:
            vv = config.get("versavoice", {})
            token = vv.get("api_token")
            sub_id = vv.get("sub_account_id")
            if token and sub_id:
                try:
                    from comms import send_message
                    msg_db = os.environ.get("AGICTL_MESSAGES_DB", "/var/lib/versa-agi/messages.db")
                    notify_text = f"Agent '{name}' has been flagged for removal."
                    if reason:
                        notify_text += f" Reason: {reason}"
                    notify_text += " Please confirm via the dashboard."
                    send_message(token, sub_id, sponsor_uid, notify_text, "typed", msg_db)
                except Exception:
                    pass  # Non-fatal — PU will see it in agitop anyway

        json_response(True, agent=name, status="removal_requested", message=status_msg)
    except Exception as e:
        json_response(False, error=str(e))
        sys.exit(1)


@agent.command("confirm-remove", hidden=True)
@click.argument("name")
def agent_confirm_remove(name):
    """Permanently remove a sub-agent after PU approval. Requires root.

    This command is called by the agitop dashboard's 'Confirm Removal' button.
    It performs: archive → VV cleanup → DB cleanup → config purge → home dir → OS user.
    """
    import shutil
    from datetime import datetime as dt
    name = name.lower()

    if os.geteuid() != 0:
        json_response(False, error="confirm-remove requires root privileges (sudo)")
        sys.exit(1)

    try:
        conn = sqlite3.connect(agents_db, timeout=5)
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT name, protected, os_user, workspace, status FROM agents WHERE name=?", (name,)).fetchone()
        if not row:
            conn.close()
            json_response(False, error=f"Agent '{name}' not found in registry")
            sys.exit(1)
        if row["protected"] == 1:
            conn.close()
            json_response(False, error=f"Cannot remove '{name}': agent is protected")
            sys.exit(1)

        workspace = row["workspace"] or ""
        os_user = row["os_user"] or name
        archive_path = None
        vv_deleted = False

        # Resolve actual workspace — try DB path first, then standard path
        if not workspace or not os.path.isdir(workspace):
            standard_path = f"/home/agi-{name}"
            if os.path.isdir(standard_path):
                workspace = standard_path

        # ── 1. Archive workspace (always, for safety) ──
        if workspace and os.path.isdir(workspace):
            archive_dir = "/var/lib/versa-agi/archive"
            os.makedirs(archive_dir, exist_ok=True)
            timestamp = dt.now().strftime("%Y%m%d_%H%M%S")
            archive_name = f"{name}_{timestamp}.tar.gz"
            archive_full = os.path.join(archive_dir, archive_name)
            tar_result = subprocess.run(
                ["tar", "czf", archive_full, "-C", os.path.dirname(workspace), os.path.basename(workspace)],
                capture_output=True, check=False
            )
            if tar_result.returncode == 0:
                archive_path = archive_full

        # ── 2. Delete VersaVoice sub-account ──
        agent_config_path = f"/etc/versa-agi/{name}_config.json"
        if os.path.exists(agent_config_path):
            try:
                with open(agent_config_path, "r") as f:
                    agent_cfg = json.load(f)
                vv_sub_id = agent_cfg.get("versavoice", {}).get("sub_account_id")
                vv_token = agent_cfg.get("versavoice", {}).get("api_token")
                if vv_sub_id and vv_token:
                    from comms import api_request
                    result = api_request(
                        f"/accounts/{vv_sub_id}", vv_token,
                        method="DELETE"
                    )
                    vv_deleted = result is not None
            except Exception:
                pass  # Non-fatal — orphaned sub-account can be cleaned manually

        # ── 3. Clean up database relations ──
        coa_tasks_db = "/var/lib/versa-agi/coa/tasks.db"
        if os.path.exists(coa_tasks_db):
            try:
                tconn = sqlite3.connect(coa_tasks_db, timeout=5)
                tconn.execute("DELETE FROM tasks WHERE assigned_to=?", (name,))
                tconn.commit()
                tconn.close()
            except Exception:
                pass

        # ── 4. Agent's data directory (/var/lib/versa-agi/{name}/) ──
        agent_data_dir = f"/var/lib/versa-agi/{name}"
        if os.path.isdir(agent_data_dir):
            shutil.rmtree(agent_data_dir, ignore_errors=True)

        # ── 5. Agent's config file ──
        if os.path.exists(agent_config_path):
            os.remove(agent_config_path)

        # ── 6. Delete agent home directory ──
        if workspace and os.path.isdir(workspace):
            subprocess.run(["rm", "-rf", workspace], capture_output=True, check=False)

        # ── 7. Delete OS user ──
        subprocess.run(["userdel", os_user], capture_output=True, check=False)

        # ── 8. Delete DB record ──
        conn.execute("DELETE FROM agents WHERE name=?", (name,))
        conn.commit()
        conn.close()

        result = {"agent": name, "status": "removed", "os_user_deleted": os_user, "vv_sub_account_deleted": vv_deleted}
        if archive_path:
            result["archive"] = archive_path
        if workspace:
            result["workspace_removed"] = workspace
        json_response(True, **result)
    except Exception as e:
        json_response(False, error=str(e))
        sys.exit(1)


@agent.command("cancel-remove")
@click.argument("name")
def agent_cancel_remove(name):
    """Cancel a pending removal request — reactivates the agent."""
    name = name.lower()
    try:
        conn = sqlite3.connect(agents_db, timeout=5)
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT status FROM agents WHERE name=?", (name,)).fetchone()
        if not row:
            conn.close()
            json_response(False, error=f"Agent '{name}' not found")
            sys.exit(1)
        if row["status"] != "removal_requested":
            conn.close()
            json_response(False, error=f"Agent '{name}' is not pending removal (status: {row['status']})")
            sys.exit(1)
        conn.execute(
            "UPDATE agents SET status='idle', inactive=0, status_message=NULL, updated_at=datetime('now') WHERE name=?",
            (name,)
        )
        conn.commit()
        conn.close()
        json_response(True, agent=name, status="idle", message="Removal cancelled — agent reactivated")
    except Exception as e:
        json_response(False, error=str(e))
        sys.exit(1)


@agent.command("remove", hidden=True)
@click.argument("name")
@click.option("--archive", is_flag=True, default=False, help="Deprecated — archive is now automatic on confirm-remove")
def agent_remove(name, archive):
    """Remove a sub-agent. Delegates to request-remove or confirm-remove based on privilege.

    Non-root: sets status to 'removal_requested' (like request-remove).
    Root: performs full removal immediately (like confirm-remove).
    """
    ctx = click.get_current_context()
    if os.geteuid() == 0:
        ctx.invoke(agent_confirm_remove, name=name)
    else:
        ctx.invoke(agent_request_remove, name=name, reason="")

@agent.command("set-timeout")
@click.argument("name")
@click.argument("minutes", type=int)
def agent_set_timeout(name, minutes):
    """Set the maximum execution timeout for an agent in minutes."""
    try:
        conn = sqlite3.connect(agents_db, timeout=5)
        conn.execute("UPDATE agents SET timeout_minutes=?, updated_at=datetime('now') WHERE name=?", (minutes, name))
        conn.commit()
        conn.close()
        json_response(True, agent=name, timeout_minutes=minutes)
    except Exception as e:
        json_response(False, error=str(e))


@agent.command("set-model")
@click.argument("name")
@click.argument("model")
@click.option("--clear", "clear_model", is_flag=True, help="Clear model assignment (use system default)")
def agent_set_model(name, model, clear_model):
    """Assign an AI model to an agent (same effect as agitop Agent Settings → Model).

    Restricted to COA or the Primary User (sub-agents cannot reassign models).
    """
    name = name.lower()
    new_model = None if clear_model else model
    if not clear_model and not new_model:
        json_response(False, error="Model argument required (or pass --clear)")
        sys.exit(1)

    # Guard: only COA or the Primary User (empty AGICTL_AGENT_USER => sudo/root/agitop).
    caller = os.environ.get("AGICTL_AGENT_USER", "")
    if caller and caller != "coa":
        json_response(False, error=f"Permission denied: only COA or the Primary User can assign agent models (caller: {caller})")
        sys.exit(1)

    try:
        conn = sqlite3.connect(agents_db, timeout=5)
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT name, protected FROM agents WHERE name=?", (name,)).fetchone()
        if not row:
            conn.close()
            json_response(False, error=f"Agent '{name}' not found")
            sys.exit(1)

        if new_model and name == "coa":
            import configparser
            cat = _load_catalog()
            coa_allowed = set()
            try:
                cfg = configparser.ConfigParser()
                for p in [SETUP_INI_CANONICAL, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "setup.ini")]:
                    if os.path.isfile(p):
                        cfg.read(p)
                        break
                raw = cfg.get("gemini", "coa_approved_models", fallback="")
                coa_allowed = {m.strip() for m in raw.split(",") if m.strip()}
            except Exception:
                pass
            for key, m in cat.items():
                if m.get("coa"):
                    coa_allowed.add(key)
            if coa_allowed and new_model not in coa_allowed:
                conn.close()
                json_response(False, error=f"Model '{new_model}' is not COA-approved. Allowed: {', '.join(sorted(coa_allowed))}")
                sys.exit(1)

        if new_model:
            cat = _load_catalog()
            known = set(cat.keys())
            if new_model not in known:
                conn.close()
                json_response(False, error=f"Model '{new_model}' not in catalog. Add via 'agictl model catalog add'.")
                sys.exit(1)

        conn.execute(
            """UPDATE agents SET model = ?, updated_at = datetime('now'),
               status = CASE WHEN status = 'invalid_config' THEN 'idle' ELSE status END,
               status_message = CASE WHEN status = 'invalid_config' THEN NULL ELSE status_message END
               WHERE name = ?""",
            (new_model, name),
        )
        if new_model:
            try:
                from harness.model_context import get_model_context
                recommended, _ = get_model_context(new_model)
                if recommended:
                    conn.execute(
                        "UPDATE agents SET num_ctx = ?, updated_at = datetime('now') WHERE name = ?",
                        (recommended, name),
                    )
            except Exception:
                pass
        conn.commit()
        conn.close()
        json_response(True, agent=name, model=new_model or "", message=f"Model assigned to '{name}'.")
    except Exception as e:
        json_response(False, error=str(e))

@agent.command("toggle-comms")
@click.argument("name")
def agent_toggle_comms(name):
    """Toggle external comms (can_message_connections) for a sub-agent.

    Dashboard/Primary User only — controls whether the agent can send
    connection requests and messages to non-sponsor VersaVoice contacts.
    Protected agents (COA, watchdog) are always enabled.
    """
    name = name.lower()
    try:
        conn = sqlite3.connect(agents_db, timeout=5)
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT name, protected, can_message_connections FROM agents WHERE name=?", (name,)).fetchone()
        if not row:
            conn.close()
            json_response(False, error=f"Agent '{name}' not found")
            sys.exit(1)
        if row["protected"] == 1:
            conn.close()
            json_response(False, error=f"Protected agent '{name}' always has comms enabled")
            return
        new_val = 0 if row["can_message_connections"] else 1
        conn.execute("UPDATE agents SET can_message_connections=?, updated_at=datetime('now') WHERE name=?",
                     (new_val, name))
        conn.commit()
        conn.close()
        state = "enabled" if new_val else "disabled"
        json_response(True, agent=name, can_message_connections=new_val, state=state)
    except Exception as e:
        json_response(False, error=str(e))

# ── Agent Status ──

@agent.group()
def status():
    """Agent status management."""
    pass

@status.command("show")
def agent_status_show():
    """Read current agent status from agents.db."""
    agent_name = get_agent_name()
    try:
        conn = sqlite3.connect(agents_db, timeout=5)
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT status, status_message FROM agents WHERE name=?", (agent_name,)).fetchone()
        conn.close()
        if row:
            print(json.dumps({"agent": agent_name, "status": row["status"], "message": row["status_message"]}))
        else:
            json_response(False, error="Agent not found")
    except Exception as e:
        json_response(False, error=str(e))

@status.command("set")
@click.argument("state")
@click.argument("summary", required=False, default="")
def agent_status_set(state, summary):
    """Write agent status + message to agents.db."""
    agent_name = get_agent_name()
    try:
        conn = sqlite3.connect(agents_db, timeout=5)
        conn.execute("UPDATE agents SET status=?, status_message=?, updated_at=datetime('now') WHERE name=?", (state, summary, agent_name))
        conn.commit()
        conn.close()
        json_response(True, agent=agent_name, status=state, message=summary)
    except Exception as e:
        json_response(False, error=str(e))
        sys.exit(1)

# ── Agent Data (for Lifeline consumption) ──

@agent.command("count")
@click.option("--active", is_flag=True, help="Count only active agents")
def agent_count(active):
    """Return count of agents."""
    agents = agent_reader.get_active_agents() if active else agent_reader.get_all_agents()
    print(len(agents))

@agent.command("summary")
@click.option("--exclude-watchdog", is_flag=True, help="Omit the watchdog infrastructure row (sub-agent registry view — watchdog is not a messageable peer)")
def agent_summary(exclude_watchdog):
    """Return agent summary as markdown table for context injection."""
    coa_user, watchdog_user, coa_display = _resolve_protected_identities()
    agents = agent_reader.get_all_agents()
    agents = sorted(agents, key=lambda x: (not x.get("protected", False), x["name"]))
    print("| Name | Workspace | Role | Model | Status | Inactive | Requested By | Created At |")
    print("|---|---|---|---|---|---|---|---|")
    for a in agents:
        raw_name = a.get("name", "") or ""
        if exclude_watchdog and raw_name == watchdog_user:
            continue
        name_str = raw_name.capitalize()
        if raw_name == coa_user:
            # Render the COA's configured display name (e.g. "Versa (COA)" —
            # the marker ships as last_name in setup.ini) instead of the raw key.
            name_str = coa_display or name_str
        inactive_str = "True" if a.get("inactive") else "False"
        status = a.get("status") or ""
        print(f"| {name_str} | {a.get('workspace', '')} | {a.get('role') or ''} | {a.get('model') or ''} | {status} | {inactive_str} | {a.get('requested_by') or ''} | {a.get('created_at') or ''} |")

@agent.command("ensure-protected")
def agent_ensure_protected():
    """Ensure coa/watchdog cannot be deactivated (self-heal)."""
    try:
        conn = sqlite3.connect(agents_db, timeout=5)
        conn.execute("UPDATE agents SET inactive=0, updated_at=datetime('now') WHERE name IN ('coa', 'watchdog') AND inactive=1")
        conn.commit()
        conn.close()
    except Exception:
        pass

@agent.command("get-active")
def agent_get_active():
    """Return active agents in pipe format for Lifeline (field 23 = model_routing_enabled)."""
    agents = agent_reader.get_active_agents()
    # Direct agents-table read — v_active_agents can be stale until init_agents_db view migration runs.
    agent_overrides: dict[str, dict] = {}
    try:
        conn = sqlite3.connect(agents_db, timeout=5)
        conn.row_factory = sqlite3.Row
        for row in conn.execute(
            "SELECT name, tool_output_token_budget, model_routing_enabled FROM agents WHERE inactive=0"
        ):
            agent_overrides[row["name"]] = {
                "tool_output_token_budget": row["tool_output_token_budget"],
                "model_routing_enabled": row["model_routing_enabled"] or 0,
            }
        conn.close()
    except Exception:
        pass
    for a in agents:
        model = a.get("model") or ""
        timeout = a.get("timeout_minutes", 60)
        runaway = a.get("runaway_threshold", 300)
        runaway_size = a.get("runaway_size_threshold", 512)
        injection_mode = a.get("context_injection_mode") or "relevant"
        token_budget = a.get("token_budget", 0)
        max_turns = a.get("max_session_turns", 50)
        ov = agent_overrides.get(a["name"], {})
        tool_budget = ov.get("tool_output_token_budget")
        if tool_budget is None:
            tool_budget = a.get("tool_output_token_budget", 6000)
        triage_model = a.get("triage_model") or ""
        anchor_style = a.get("anchor_style") or "compact"
        num_ctx = a.get("num_ctx", 0)
        convo_depth = a.get("conversation_depth", 10)
        resume_enabled = a.get("resume_enabled", 0)
        resume_max_msgs = a.get("resume_max_messages", 0)
        skill_mode = a.get("skill_injection_mode") or "hybrid"
        temperature = a.get("temperature")
        temperature_str = "" if temperature is None else str(temperature)
        reasoning_effort = a.get("reasoning_effort") or ""
        reasoning_max_tokens = a.get("reasoning_max_tokens")
        reasoning_max_str = "" if reasoning_max_tokens is None else str(reasoning_max_tokens)
        model_params_extra = a.get("model_params_extra") or ""
        routing_enabled = ov.get("model_routing_enabled", a.get("model_routing_enabled", 0))
        print(f"{a['name']}|{a['os_user']}|{a['workspace']}|{model}|{timeout}|{runaway}|{runaway_size}|{injection_mode}|{token_budget}|{max_turns}|{tool_budget}|{triage_model}|{anchor_style}|{num_ctx}|{convo_depth}|{resume_enabled}|{resume_max_msgs}|{skill_mode}|{temperature_str}|{reasoning_effort}|{reasoning_max_str}|{model_params_extra}|{routing_enabled}")


# ═══════════════════════════════════════════════════════
# 3. TASK — Cognitive task queue
# ═══════════════════════════════════════════════════════

def _caller_agent_name() -> str:
    """Resolved logical agent name for the current caller."""
    return os.environ.get("VERSA_AGENT_NAME") or get_agent_name()


def _is_privileged_task_caller() -> bool:
    """COA, watchdog, or Primary User (direct/root invocation)."""
    caller = os.environ.get("AGICTL_AGENT_USER", "")
    if not caller:
        return True
    return caller in ("coa", "watchdog")


def _assert_can_manage_agent_tasks(agent_name: str) -> None:
    if _is_privileged_task_caller():
        return
    mine = _caller_agent_name()
    if mine != agent_name:
        json_response(
            False,
            error=f"Permission denied: can only manage your own tasks (caller: {mine})",
        )
        sys.exit(1)


def _assert_can_manage_task_row(task_row: dict) -> None:
    if _is_privileged_task_caller():
        return
    assignee = (task_row.get("assigned_to") or "").strip()
    mine = _caller_agent_name()
    if assignee != mine:
        json_response(
            False,
            error=f"Permission denied: task is assigned to '{assignee}', not '{mine}'",
        )
        sys.exit(1)


@cli.group()
def task():
    """Agent task queue management."""
    pass

@task.command("list")
@click.option("--all", "show_all", is_flag=True, help="Show ALL tasks including done/cancelled")
def task_list(show_all):
    """List tasks. Default: active + due blocked only."""
    if show_all:
        tasks = tasks_reader.get_all_tasks() if hasattr(tasks_reader, 'get_all_tasks') else tasks_reader.get_active_tasks()
    else:
        tasks = tasks_reader.get_active_tasks()
    print(json.dumps(tasks, indent=2, default=str))

@task.command("get")
@click.argument("task_id", type=int)
def task_get(task_id):
    """Output full task context as JSON (includes recent progress journal)."""
    task_data = tasks_reader.get_task(task_id)
    if task_data:
        try:
            conn = sqlite3.connect(tasks_db, timeout=5)
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT created_at, agent_name, note FROM task_progress "
                "WHERE task_id = ? ORDER BY id DESC LIMIT 10",
                (task_id,)
            ).fetchall()
            conn.close()
            # Reverse so entries read oldest → newest
            task_data["recent_progress"] = [dict(r) for r in reversed(rows)]
        except Exception:
            task_data["recent_progress"] = []
        print(json.dumps(task_data, indent=2, default=str))
    else:
        json_response(False, error=f"Task {task_id} not found")

@task.command("progress")
@click.argument("task_id", type=int)
@click.argument("note", required=False)
@click.option("--last", "last_n", default=20, type=int, help="When listing: number of recent entries (default 20)")
def task_progress(task_id, note, last_n):
    """Append to or list a task's progress journal.

    With text: appends a timestamped, attributed entry (append-only).
    Without text: lists the journal as JSON, oldest first.

    Leave yourself breadcrumbs across cycles: what you did, how far you got,
    what to do next. Entries are injected into your wake context while the
    task is active — they survive fresh-start cycles where chat history
    does not.
    """
    note = (note or "").strip()
    try:
        conn = sqlite3.connect(tasks_db, timeout=5)
        conn.row_factory = sqlite3.Row

        if not note:
            # List mode
            rows = conn.execute(
                "SELECT id, created_at, agent_name, note FROM task_progress "
                "WHERE task_id = ? ORDER BY id DESC LIMIT ?",
                (task_id, last_n)
            ).fetchall()
            conn.close()
            print(json.dumps([dict(r) for r in reversed(rows)], indent=2, default=str))
            return

        # Append mode
        if not conn.execute("SELECT 1 FROM tasks WHERE id = ?", (task_id,)).fetchone():
            conn.close()
            json_response(False, error=f"Task {task_id} not found")
            sys.exit(1)
        # Author resolution: prefer the explicit env hint, but fall back to the
        # canonical resolver (AGICTL_CONFIG basename → OS user → config) rather
        # than a hardcoded "coa" — otherwise a missing VERSA_AGENT_NAME silently
        # misattributes every sub-agent's entries to the COA.
        agent_name = os.environ.get("VERSA_AGENT_NAME") or get_agent_name()
        c = conn.cursor()
        c.execute(
            "INSERT INTO task_progress (task_id, agent_name, note) VALUES (?, ?, ?)",
            (task_id, agent_name, note)
        )
        conn.execute("UPDATE tasks SET updated_at = datetime('now') WHERE id = ?", (task_id,))
        conn.commit()
        entry_id = c.lastrowid
        conn.close()
        json_response(True, task_id=task_id, entry_id=entry_id, agent=agent_name)
    except Exception as e:
        json_response(False, error=str(e))
        sys.exit(1)

@task.command("add")
@click.argument("title")
@click.option("--desc", default=None, help="Extended task description")
@click.option("--priority", default="normal", type=click.Choice(["low", "normal", "high", "urgent"]))
@click.option("--assignee", default=None, help="Agent assigned to the task (defaults to the current agent, resolved from VERSA_AGENT_NAME or the agent config)")
@click.option("--project", default=None, type=int, help="Project ID to link the task to")
@click.option("--callback", default=None, type=click.Choice(["notify_sponsor", "notify_connection", "await_reply", "check_connection", "none"]))
@click.option("--source-msg", default=None, type=int, help="Source message ID")
@click.option("--requested-by", default=None, help="UID of who originated the request")
@click.option("--due-date", default=None, help="Due date (YYYY-MM-DD HH:MM:SS) — required for planned tasks")
@click.option("--utility-task", is_flag=True, help="Create as Utility Task (links one Utility Model)")
@click.option("--utility-model", default=None, help="utility_models.id (required with --utility-task)")
@click.option("--utility-input-files", default=None, help="JSON array of input file paths")
@click.option("--utility-output-override", default=None, help="Override UM output_path for this task")
@click.option("--utility-start-alert", is_flag=True, help="VersaVoice alert to PU when run starts (utility or script)")
@click.option("--utility-stop-alert", is_flag=True, help="VersaVoice alert to PU when run completes (utility or script)")
@click.option("--utility-spawn-agent", default=None, help="Spawn this agent on successful UM completion")
@click.option("--script-task", is_flag=True, help="Create as Script Task (runs a .sh from AGi-Tools — no agent spawn)")
@click.option("--script-path", default=None, help="Path to the .sh inside AGi-Tools (required with --script-task)")
@click.option("--script-parameters", default=None, help="Arguments passed to the script (argv-split)")
@click.option("--script-interval", type=int, default=None, help="Recurrence interval in seconds (omit/0 = once-off)")
def task_add(title, desc, priority, assignee, project, callback, source_msg, requested_by, due_date,
             utility_task, utility_model, utility_input_files, utility_output_override,
             utility_start_alert, utility_stop_alert, utility_spawn_agent,
             script_task, script_path, script_parameters, script_interval):
    """Insert a new task. Returns JSON with created record."""
    # Dynamic assignee default: current agent, resolved robustly (env hint →
    # canonical resolver) rather than a hardcoded 'coa'.
    if assignee is None:
        assignee = os.environ.get("VERSA_AGENT_NAME") or get_agent_name()
    # Tasks default to 'planned' status — due_date is mandatory
    if not due_date:
        json_response(False, error="--due-date is required for planned tasks")
        sys.exit(1)
    # Utility and Script modes are mutually exclusive (they share alert columns).
    if utility_task and script_task:
        json_response(False, error="--utility-task and --script-task are mutually exclusive")
        sys.exit(1)
    if utility_task:
        if not utility_model:
            json_response(False, error="--utility-model is required with --utility-task")
            sys.exit(1)
        from utility_store import get_utility_model
        if not get_utility_model(utility_model):
            json_response(False, error=f"Utility Model '{utility_model}' not found")
            sys.exit(1)
    elif script_task:
        if not script_path:
            json_response(False, error="--script-path is required with --script-task")
            sys.exit(1)
    else:
        # Project is mandatory for standard tasks
        if project is None:
            json_response(False, error="--project is required. Use 'agictl project list' to find the correct project ID.")
            sys.exit(1)
    try:
        conn = sqlite3.connect(tasks_db, timeout=5)
        c = conn.cursor()
        task_kind = "script" if script_task else ("utility" if utility_task else "standard")
        is_special = utility_task or script_task
        start_alert = 1 if utility_start_alert else 0
        stop_alert = 1 if utility_stop_alert else (1 if is_special else 0)
        c.execute(
            "INSERT INTO tasks (title, description, priority, assigned_to, project_id, callback_action, "
            "source_message_id, requested_by, due_date, task_kind, utility_model_id, utility_input_files, "
            "utility_output_override, utility_start_alert, utility_stop_alert, utility_spawn_agent, "
            "script_path, script_parameters, script_interval_seconds) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                title, desc, priority, assignee, project, callback, source_msg, requested_by, due_date,
                task_kind, utility_model if utility_task else None, utility_input_files,
                utility_output_override, start_alert, stop_alert, utility_spawn_agent,
                script_path if script_task else None,
                script_parameters if script_task else None,
                script_interval if script_task else None,
            ),
        )
        task_id = c.lastrowid
        conn.commit()
        conn.close()
        json_response(True, task_id=task_id, title=title, priority=priority, assigned_to=assignee,
                      project_id=project, due_date=due_date, task_kind=task_kind,
                      utility_model_id=utility_model if utility_task else None,
                      script_path=script_path if script_task else None)
    except Exception as e:
        json_response(False, error=str(e))
        sys.exit(1)

@task.command("run-due-scripts", hidden=True)
@click.option("--agent", "agent_name", required=True)
@click.option("--agent-workspace", default=None, help="Agent workspace root (lifeline-supplied; unused today, reserved for parity with utility)")
def task_run_due_scripts(agent_name, agent_workspace):
    """Lifeline: execute due Script Tasks for an agent (TD-SCRIPT-001).

    Deterministic — runs a .sh from AGi-Tools as the owning agent, captures the
    return code, and drives done/blocked + rc-based VersaVoice alerts. No LLM,
    no harness, no agent spawn.
    """
    from utility_store import list_due_script_tasks
    from script_runner import ScriptRunError, resolve_agitools_path, run_script_task
    from agictl.utility_cli import _vv_utility_alert

    def _queue_script_spawn_wake(spawn_agent: str, task_id: int, title: str, script: str, rc: int) -> None:
        """Queue a lifeline wake for an agent after a successful Script Task."""
        agent = (spawn_agent or "").strip()
        if not agent:
            return
        wake_dir = f"/var/lib/versa-agi/{agent}"
        os.makedirs(wake_dir, exist_ok=True)
        wake_path = os.path.join(wake_dir, "utility_wake.json")
        payload = {
            "task_id": task_id,
            "task_kind": "script",
            "title": title,
            "script": script,
            "returncode": rc,
        }
        with open(wake_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)

    due = list_due_script_tasks(agent_name)
    if not due:
        json_response(True, agent=agent_name, count=0, runs=[])
        return

    agitools_root = resolve_agitools_path(tasks_db)
    results = []
    for t in due:
        tid = t["id"]
        title = t.get("title", "")
        interval = t.get("script_interval_seconds") or 0
        recurring = bool(interval and interval > 0)
        basename = os.path.basename((t.get("script_path") or "").rstrip("/"))

        if t.get("utility_start_alert"):
            _vv_utility_alert(f"Script task #{tid} started — {title} ({basename}) on {agent_name}")

        rc = None
        try:
            result = run_script_task(
                t.get("script_path"),
                agitools_root=agitools_root or "",
                parameters=t.get("script_parameters"),
                task_id=tid,
            )
            rc = result["returncode"]
            success = result["success"]
            tail = result.get("stderr_tail") or result.get("stdout_tail") or ""
            status_word = "ok" if success else ("timeout" if result.get("timed_out") else "FAIL")
            note = f"Script {basename} rc={rc} ({status_word})"
            if tail:
                note += "\n" + tail[:500]
            ran_at = result.get("ran_at")
        except ScriptRunError as e:
            if e.code == "running":
                # Lock held by a still-running invocation — leave due_date so the
                # next tick retries; do not touch status.
                results.append({"success": False, "task_id": tid, "code": e.code, "skipped": True})
                continue
            success = False
            note = f"Script preflight failed ({e.code}): {e.message}"
            ran_at = None

        # Status routing (decision #3): once-off → done(rc==0)/blocked(rc!=0);
        # recurring → stays 'planned', due_date re-armed by the interval.
        conn = sqlite3.connect(tasks_db, timeout=5)
        sets = ["script_last_rc=?", "script_last_run_at=COALESCE(?, datetime('now'))", "updated_at=datetime('now')"]
        params: list = [rc, ran_at]
        if recurring:
            sets.append("due_date=datetime('now', ?)")
            params.append(f"+{int(interval)} seconds")
        else:
            if success:
                sets.append("status='done'")
                sets.append("completed_at=datetime('now')")
            else:
                sets.append("status='blocked'")
        params.append(tid)
        conn.execute(f"UPDATE tasks SET {', '.join(sets)} WHERE id=?", params)
        conn.execute(
            "INSERT INTO task_progress (task_id, agent_name, note) VALUES (?, ?, ?)",
            (tid, agent_name, note),
        )
        conn.commit()
        conn.close()

        if t.get("utility_stop_alert"):
            outcome = "ok" if success else "FAILED"
            _vv_utility_alert(f"Script task #{tid} finished — {title} ({basename}): rc={rc} {outcome}")

        # Spawn agent on successful, non-recurring completion (mirrors Utility Tasks).
        spawn_agent = (t.get("utility_spawn_agent") or "").strip()
        if spawn_agent and success and not recurring:
            _queue_script_spawn_wake(spawn_agent, tid, title, basename, rc)

        result_entry = {
            "success": success, "task_id": tid, "returncode": rc,
            "recurring": recurring, "title": title,
        }
        if spawn_agent and success and not recurring:
            result_entry["spawn_agent"] = spawn_agent
            result_entry["spawn_queued"] = True
        results.append(result_entry)

    json_response(True, agent=agent_name, count=len(results), runs=results)

@task.command("update")
@click.argument("task_id", type=int)
@click.option("--status", "task_status", help="planned, in_progress, waiting, blocked, cancelled, done")
@click.option("--desc", help="Update description")
@click.option("--priority", help="low, normal, high, urgent")
@click.option("--assignee", help="Agent assigned")
@click.option("--due-date", help="Next wake/due date (YYYY-MM-DD HH:MM:SS)")
@click.option("--requested-by", help="UID of who originated the request")
@click.option("--utility-model", default=None)
@click.option("--utility-task/--no-utility-task", default=None)
@click.option("--utility-input-files", default=None)
@click.option("--utility-output-override", default=None)
@click.option("--utility-start-alert/--no-utility-start-alert", default=None)
@click.option("--utility-stop-alert/--no-utility-stop-alert", default=None)
@click.option("--utility-spawn-agent", default=None)
@click.option("--script-task/--no-script-task", default=None)
@click.option("--script-path", default=None)
@click.option("--script-parameters", default=None)
@click.option("--script-interval", type=int, default=None)
def task_update(task_id, task_status, desc, priority, assignee, due_date, requested_by,
                utility_model, utility_task, utility_input_files, utility_output_override,
                utility_start_alert, utility_stop_alert, utility_spawn_agent,
                script_task, script_path, script_parameters, script_interval):
    """Update specific fields on an existing task. Returns JSON."""
    updates = {}
    if task_status is not None: updates["status"] = task_status
    if desc is not None: updates["description"] = desc
    if priority is not None: updates["priority"] = priority
    if assignee is not None: updates["assigned_to"] = assignee
    if due_date is not None: updates["due_date"] = due_date
    if requested_by is not None: updates["requested_by"] = requested_by
    if utility_task is not None: updates["task_kind"] = "utility" if utility_task else "standard"
    if utility_model is not None: updates["utility_model_id"] = utility_model
    if utility_input_files is not None: updates["utility_input_files"] = utility_input_files
    if utility_output_override is not None: updates["utility_output_override"] = utility_output_override
    if utility_start_alert is not None: updates["utility_start_alert"] = 1 if utility_start_alert else 0
    if utility_stop_alert is not None: updates["utility_stop_alert"] = 1 if utility_stop_alert else 0
    if utility_spawn_agent is not None: updates["utility_spawn_agent"] = utility_spawn_agent
    if script_task is not None: updates["task_kind"] = "script" if script_task else "standard"
    if script_path is not None: updates["script_path"] = script_path
    if script_parameters is not None: updates["script_parameters"] = script_parameters
    if script_interval is not None: updates["script_interval_seconds"] = script_interval

    if not updates:
        json_response(False, error="No updates provided")
        return

    # Enforce: planned tasks must have a due_date
    target_status = updates.get("status")
    if target_status == "planned" and "due_date" not in updates:
        # Check if due_date already exists on the task
        existing = tasks_reader.get_task(task_id)
        if not existing or not existing.get("due_date"):
            json_response(False, error="--due-date is required when status is 'planned'")
            return

    if tasks_reader.update_task(task_id, updates):
        json_response(True, task_id=task_id, updated_fields=list(updates.keys()))
    else:
        json_response(False, error=f"Failed to update task {task_id}")

@task.command("done")
@click.argument("task_id", type=int)
def task_done(task_id):
    """Shortcut: mark task as done."""
    if tasks_reader.update_task_status(task_id, "done"):
        json_response(True, task_id=task_id, status="done")
    else:
        json_response(False, error=f"Failed to mark task {task_id} as done")

@task.command("cancel")
@click.argument("task_id", type=int)
def task_cancel(task_id):
    """Shortcut: mark task as cancelled."""
    if tasks_reader.update_task_status(task_id, "cancelled"):
        json_response(True, task_id=task_id, status="cancelled")
    else:
        json_response(False, error=f"Failed to cancel task {task_id}")

@task.command("snooze")
@click.argument("task_id", type=int)
@click.argument("minutes", type=int, default=5)
def task_snooze(task_id, minutes):
    """Set wake_after and due_date to now + N min. Minimum 5 minutes, max 10080 (7 days)."""
    if minutes < 5:
        minutes = 5
    if minutes > 10080:
        minutes = 10080
    if tasks_reader.snooze_task(task_id, minutes):
        json_response(True, task_id=task_id, snoozed_minutes=minutes)
    else:
        json_response(False, error=f"Failed to snooze task {task_id}")

@task.command("reminder")
@click.argument("text")
@click.option("--category", default="general", type=click.Choice(["general", "preference", "instruction", "constraint"]))
def task_reminder(text, category):
    """Shortcut: create a task of type reminder."""
    try:
        conn = sqlite3.connect(tasks_db, timeout=5)
        c = conn.cursor()
        c.execute(
            "INSERT INTO tasks (title, description, priority, tags, assigned_to) VALUES (?, ?, 'normal', ?, ?)",
            (f"[{category.upper()}] {text}", text, f"reminder,{category}", get_agent_name())
        )
        task_id = c.lastrowid
        conn.commit()
        conn.close()
        json_response(True, task_id=task_id, type="reminder", category=category, text=text)
    except Exception as e:
        json_response(False, error=str(e))

# ── Task Data (for Lifeline consumption) ──

@task.command("count-pending")
@click.argument("agent_name")
def task_count_pending(agent_name):
    """Return count of pending active tasks."""
    print(tasks_reader.count_pending(agent_name))

@task.command("count-due-blocked")
@click.argument("agent_name")
def task_count_due_blocked(agent_name):
    """Return count of blocked tasks past due date."""
    print(tasks_reader.count_due_blocked(agent_name))

@task.command("count-total-blocked")
@click.argument("agent_name")
def task_count_total_blocked(agent_name):
    """Return count of all blocked tasks."""
    print(tasks_reader.count_total_blocked(agent_name))

@task.command("get-blocked-detail")
@click.argument("agent_name")
def task_get_blocked_detail(agent_name):
    """Return blocked task details for diagnostics."""
    detail = tasks_reader.get_blocked_detail(agent_name)
    if detail:
        print(detail)

@task.command("check-followup")
@click.argument("agent_name")
def task_check_followup(agent_name):
    """Check if tasks have callback_action requiring routing."""
    print(tasks_reader.check_connection_followup(agent_name))

@task.command("inject-followup")
@click.argument("agent_name")
def task_inject_followup(agent_name):
    """Inject connection follow-up task into the queue."""
    tasks_reader.inject_connection_followup(agent_name)

@task.command("freeze-all")
@click.argument("agent_name")
def task_freeze_all(agent_name):
    """Freeze all non-terminal tasks for an agent. Saves prior status in pre_freeze_status."""
    _assert_can_manage_agent_tasks(agent_name)
    try:
        conn = sqlite3.connect(tasks_db, timeout=5)
        cursor = conn.execute(
            """UPDATE tasks
               SET pre_freeze_status = status, status = 'frozen', updated_at = datetime('now')
               WHERE status NOT IN ('done', 'cancelled', 'frozen')
                 AND (assigned_to = ? OR assigned_to IS NULL)""",
            (agent_name,)
        )
        count = cursor.rowcount
        conn.commit()
        conn.close()
        json_response(True, frozen_count=count, agent=agent_name)
    except Exception as e:
        json_response(False, error=str(e))

@task.command("unfreeze-all")
@click.argument("agent_name")
def task_unfreeze_all(agent_name):
    """Unfreeze all frozen tasks for an agent. Restores prior status from pre_freeze_status."""
    _assert_can_manage_agent_tasks(agent_name)
    try:
        conn = sqlite3.connect(tasks_db, timeout=5)
        cursor = conn.execute(
            """UPDATE tasks
               SET status = COALESCE(pre_freeze_status, 'planned'), pre_freeze_status = NULL,
                   spawn_attempts = 0, updated_at = datetime('now')
               WHERE status = 'frozen'
                 AND (assigned_to = ? OR assigned_to IS NULL)""",
            (agent_name,)
        )
        count = cursor.rowcount
        conn.commit()
        conn.close()
        json_response(True, unfrozen_count=count, agent=agent_name)
    except Exception as e:
        json_response(False, error=str(e))

@task.command("unfreeze")
@click.argument("task_id", type=int)
def task_unfreeze_one(task_id):
    """Unfreeze a single frozen task. Restores pre_freeze_status and resets spawn_attempts."""
    existing = tasks_reader.get_task(task_id)
    if not existing:
        json_response(False, error=f"Task {task_id} not found")
        return
    _assert_can_manage_task_row(existing)
    if existing.get("status") != "frozen":
        json_response(False, error=f"Task {task_id} is not frozen (status={existing.get('status')})")
        return
    restore = existing.get("pre_freeze_status") or "planned"
    if tasks_reader.update_task(
        task_id,
        {"status": restore, "spawn_attempts": 0, "pre_freeze_status": None},
    ):
        json_response(True, task_id=task_id, status=restore, action="unfrozen")
    else:
        json_response(False, error=f"Failed to unfreeze task {task_id}")

@task.command("count-frozen")
@click.argument("agent_name")
def task_count_frozen(agent_name):
    """Return count of frozen tasks for an agent."""
    try:
        conn = sqlite3.connect(tasks_db, timeout=5)
        count = conn.execute(
            "SELECT COUNT(*) FROM tasks WHERE status = 'frozen' AND (assigned_to = ? OR assigned_to IS NULL)",
            (agent_name,)
        ).fetchone()[0]
        conn.close()
        print(count)
    except:
        print("0")


# ═══════════════════════════════════════════════════════
# 4. MESSAGE — Communication
# ═══════════════════════════════════════════════════════

@cli.group()
def message():
    """Communication via VersaVoice REST Adapter."""
    pass


def _is_vv_enabled() -> bool:
    """Check if VersaVoice is enabled in setup.ini."""
    try:
        ini_path = "/etc/versa-agi/setup.ini"
        import configparser
        config = configparser.ConfigParser()
        config.read(ini_path)
        return config.get("versavoice", "enabled", fallback="true").lower() == "true"
    except Exception:
        return True  # Default: enabled (backward compat)


def _send_as_internal(contact_uid, text, mode, db_path, attachment_paths=None):
    """Route a message as internal (SQLite-only, no VV API).

    Used by VV-gated routing when VersaVoice is disabled. The agent is
    completely unaware — it called `message send` as normal and the
    infrastructure decided to route locally.
    """
    sender = get_agent_name()
    import uuid
    msg_id = f"int_{uuid.uuid4().hex[:16]}"
    recv_msg_id = f"int_{uuid.uuid4().hex[:16]}"

    # Build attachment payload string (local paths, not cloud URLs)
    attach_data = None
    if attachment_paths:
        filtered = [p for p in attachment_paths if p]
        if filtered:
            attach_data = json.dumps(filtered)

    try:
        conn = sqlite3.connect(db_path, timeout=5)
        conn.execute(
            "INSERT INTO messages (direction, from_user_id, to_user_id, display_name, "
            "message_id, text, mode, status, channel, attachment_path) "
            "VALUES ('sent', ?, ?, ?, ?, ?, ?, 'sent', 'internal', ?)",
            (sender, contact_uid, sender, msg_id, text, mode, attach_data)
        )
        conn.execute(
            "INSERT INTO messages (direction, from_user_id, to_user_id, display_name, "
            "message_id, text, mode, status, channel, attachment_path) "
            "VALUES ('received', ?, ?, ?, ?, ?, ?, 'unprocessed', 'internal', ?)",
            (sender, contact_uid, sender, recv_msg_id, text, mode, attach_data)
        )
        conn.commit()
        conn.close()
        json_response(True, message_id=msg_id, sender=sender, recipient=contact_uid,
                      channel="internal", text=text[:100])
    except Exception as e:
        json_response(False, error=str(e))
        sys.exit(1)

@message.command("get")
@click.argument("agent_uid")
@click.option("--unread", is_flag=True, help="Only unprocessed messages")
@click.option("--last-n-minutes", type=int, default=None, help="Messages from last N minutes")
@click.option("--last-n-count", type=int, default=None, help="Last N messages")
@click.option("--limit", type=int, default=50, help="Max results")
@click.option("--contact", default=None, help="Filter by contact UID")
def message_get(agent_uid, unread, last_n_minutes, last_n_count, limit, contact):
    """Query messages for an agent/user from SQLite. Returns JSON array."""
    try:
        conn = sqlite3.connect(messages_db, timeout=5)
        conn.row_factory = sqlite3.Row
        conditions = ["(to_user_id=? OR from_user_id=?)"]
        params = [agent_uid, agent_uid]
        if unread:
            conditions.append("status='unprocessed'")
        if last_n_minutes:
            conditions.append(f"created_at >= datetime('now', '-{last_n_minutes} minutes')")
        if contact:
            conditions.append("(from_user_id=? OR to_user_id=?)")
            params.extend([contact, contact])
        where = " AND ".join(conditions)
        query = f"SELECT * FROM messages WHERE {where} ORDER BY created_at DESC"
        query += f" LIMIT {last_n_count}" if last_n_count else f" LIMIT {limit}"
        rows = conn.execute(query, params).fetchall()
        conn.close()
        print(json.dumps([dict(r) for r in rows], indent=2, default=str))
    except Exception as e:
        json_response(False, error=str(e))

@message.command("send")
@click.argument("contact_uid")
@click.argument("text")
@click.option("--mode", default="typed", type=click.Choice(["typed", "translate", "speak", "speak_translated"]))
@click.option("--media", "media_paths", multiple=True, help="Path to media file to attach (repeatable)")
@click.option("--markdown", "markdown_paths", multiple=True, help="Path to markdown file to attach (repeatable)")
@click.option("--url", "urls", multiple=True, help="URL to attach (repeatable)")
def message_send(contact_uid, text, mode, media_paths, markdown_paths, urls):
    """Send a message via REST Comms Adapter. Returns JSON.

    VV-gated routing: when VersaVoice is disabled (setup.ini [versavoice] enabled=false),
    outbound messages are auto-routed as internal SQLite records instead of calling the
    VV REST API. Agents are completely unaware of this routing decision.
    """
    # ── VV-Gated Routing ──
    # When VV is disabled, route outbound messages internally instead of
    # calling the VV REST API. The agent is completely unaware of this.
    if not _is_vv_enabled():
        attachment_paths = list(media_paths) + list(markdown_paths)
        _send_as_internal(contact_uid, text, mode, messages_db,
                          attachment_paths=attachment_paths if attachment_paths else None)
        return

    config = get_config()
    token = config.get("versavoice", {}).get("api_token")
    sub_account_id = config.get("versavoice", {}).get("sub_account_id")
    if not token or not sub_account_id:
        json_response(False, error="VersaVoice identity not configured")
        sys.exit(1)

    # ── Identity Validation: agent can only send as itself ──
    caller = get_agent_name()
    try:
        id_conn = sqlite3.connect(agents_db, timeout=5)
        id_conn.row_factory = sqlite3.Row
        agent_row = id_conn.execute(
            "SELECT protected, can_message_connections FROM agents WHERE name=?", (caller,)
        ).fetchone()
        id_conn.close()
        if agent_row:
            is_protected = agent_row["protected"] == 1
            can_comms = agent_row["can_message_connections"] == 1
            # Non-privileged agents can only message the sponsor (Primary User)
            if not is_protected and not can_comms:
                sponsor_uid = config.get("primary_user", {}).get("uid", "")
                if contact_uid != sponsor_uid:
                    json_response(False, error=f"Agent '{caller}' cannot message non-sponsor contacts. "
                                  "External comms not enabled. Do not retry — report this to the COA for activation.")
                    sys.exit(1)
    except Exception:
        pass  # Fail open — DB unavailable shouldn't block comms entirely
    
    # Build attachments payload if any flags provided
    attachments = None
    if media_paths or markdown_paths or urls:
        attachments = build_attachments(
            token, sub_account_id, contact_uid,
            media_paths=list(media_paths),
            markdown_paths=list(markdown_paths),
            urls=list(urls)
        )
        if not attachments:
            attachments = None  # Don't send empty array
    
    success = send_message(token, sub_account_id, contact_uid, text, mode, messages_db, attachments=attachments)
    if success:
        att_count = len(attachments) if attachments else 0
        result = {"recipient": contact_uid, "mode": mode, "text": text[:100]}
        if att_count > 0:
            result["attachments"] = att_count
        json_response(True, **result)
    else:
        json_response(False, error="Failed to send message")
        sys.exit(1)


@message.command("internal")
@click.argument("recipient_agent")
@click.argument("text")
@click.option("--from-pu", is_flag=True, default=False, help="Explicitly mark sender as the Primary User (used by agitop)")
def message_internal(recipient_agent, text, from_pu):
    """Send an internal message to another agent (direct SQLite, no VV API).

    Sub-agents use this to communicate with the COA and vice versa.
    Messages are inserted directly into messages.db with channel='internal'.

    When --from-pu is passed (e.g., from agitop), the sender identity is
    resolved from the Primary User's profile in system_config.json.
    """
    recipient_agent = recipient_agent.lower()
    sender = get_agent_name()
    display_name = sender
    try:
        conn = sqlite3.connect(messages_db, timeout=5)
        agents_conn = sqlite3.connect(agents_db, timeout=5)

        # Verify recipient exists
        recipient = agents_conn.execute("SELECT name FROM agents WHERE name=?", (recipient_agent,)).fetchone()
        if not recipient:
            agents_conn.close()
            conn.close()
            json_response(False, error=f"Agent '{recipient_agent}' not found in registry")
            sys.exit(1)
        agents_conn.close()

        # Explicit PU identity when called from the dashboard
        if from_pu:
            config = get_config()
            pu = config.get("primary_user", {})
            sender = pu.get("uid", "primary_user")
            display_name = pu.get("display_name", "Primary User")

        # Insert sender record (outbox view) — only for agent-to-agent.
        # When --from-pu, skip the outbox record: the PU sees everything via the
        # dashboard, and inserting it causes the agent to see the same message
        # twice in conversation history (once as 'sent'/YOU, once as 'received'/THEM).
        import uuid
        msg_id = f"int_{uuid.uuid4().hex[:16]}"
        if not from_pu:
            conn.execute(
                "INSERT INTO messages (direction, from_user_id, to_user_id, display_name, "
                "message_id, text, mode, status, channel) "
                "VALUES ('sent', ?, ?, ?, ?, ?, 'typed', 'sent', 'internal')",
                (sender, recipient_agent, display_name, msg_id, text)
            )
        # Insert recipient record (recipient's inbox view)
        recv_msg_id = f"int_{uuid.uuid4().hex[:16]}"
        conn.execute(
            "INSERT INTO messages (direction, from_user_id, to_user_id, display_name, "
            "message_id, text, mode, status, channel) "
            "VALUES ('received', ?, ?, ?, ?, ?, 'typed', 'unprocessed', 'internal')",
            (sender, recipient_agent, display_name, recv_msg_id, text)
        )
        conn.commit()
        conn.close()
        json_response(True, message_id=msg_id, sender=sender, recipient=recipient_agent,
                      channel="internal", text=text[:100])
    except Exception as e:
        json_response(False, error=str(e))
        sys.exit(1)

@message.command("mark-processed")
@click.argument("message_id")
def message_mark_processed(message_id):
    """Mark a message as processed."""
    if mark_message_processed(message_id, messages_db):
        json_response(True, message_id=message_id, status="processed")
    else:
        json_response(False, error=f"Failed to mark message {message_id}")
        sys.exit(1)

@message.command("delete")
@click.argument("message_id")
@click.option("--channel", required=True, help="Channel ID (from message get output)")
def message_delete(message_id, channel):
    """Delete a message from VersaVoice cloud (sub-account's space only).

    RESTRICTED: Only use when explicitly instructed by the Primary User.
    Deletes only from this agent's space — the other participant's copy is preserved.
    """
    config = get_config()
    sub_account_id = config.get("versavoice", {}).get("sub_account_id")
    token = config.get("versavoice", {}).get("api_token")

    if not sub_account_id or not token:
        json_response(False, error="VersaVoice not configured")
        sys.exit(1)

    from comms import api_request, delete_local_message, tombstone_message
    endpoint = f"/messages/{message_id}?subAccountId={sub_account_id}&channelId={channel}"
    result = api_request(endpoint, token, method="DELETE")

    if result and result.get("success"):
        tombstone_message(message_id, messages_db)
        delete_local_message(message_id, messages_db)
        json_response(True, message_id=message_id, channel=channel, status="deleted")
    else:
        error_msg = result.get("message", "API call failed") if result else "API call failed"
        json_response(False, error=error_msg, message_id=message_id)
        sys.exit(1)

@message.command("sync-inbox")
@click.argument("agent_user")
@click.option("--agent-path", required=True)
@click.option("--sub-account", required=True)
@click.option("--token", required=True)
@click.option("--full", is_flag=True, help="Re-scan recent history (2h), not just unread")
def message_sync_inbox(agent_user, agent_path, sub_account, token, full):
    """Pull messages from VersaVoice REST API and persist to SQLite."""
    success = fetch_inbox(agent_user, agent_path, sub_account, token, messages_db, full_sync=full)
    if not success:
        sys.exit(1)

@message.command("count-unprocessed")
@click.argument("sub_account")
@click.argument("within_seconds", type=int)
@click.option("--agent-name", default="", help="Agent name for internal message matching")
def message_count_unprocessed(sub_account, within_seconds, agent_name):
    """Count unprocessed messages within time window."""
    print(message_reader.count_unprocessed(sub_account, within_seconds, agent_name=agent_name))

@message.command("count-stale")
@click.argument("sub_account")
@click.argument("older_than_seconds", type=int)
def message_count_stale(sub_account, older_than_seconds):
    """Count messages stuck unprocessed past the cooldown."""
    print(message_reader.count_stale(sub_account, older_than_seconds))

@message.command("fail-stale")
@click.argument("sub_account")
@click.argument("older_than_seconds", type=int)
def message_fail_stale(sub_account, older_than_seconds):
    """Force-fail stale unprocessed messages."""
    message_reader.fail_stale(sub_account, older_than_seconds)

@message.command("blacklisted")
@click.argument("sub_account")
def message_blacklisted(sub_account):
    """Return UIDs of unprocessed senders matching security blacklist."""
    blocked_uids = tasks_reader.get_blocked_uids()
    if blocked_uids:
        bad_senders = message_reader.get_unprocessed_from(sub_account, blocked_uids)
        if bad_senders:
            print("\n".join(bad_senders))

@message.command("conversation-context")
@click.argument("sub_account")
@click.argument("sponsor_uid", required=False, default="")
@click.option("--injection-mode", default="all", type=click.Choice(["all", "relevant"]),
              help="'all' = inject ALL connection memories (COA). 'relevant' = only active sender memories (sub-agents).")
@click.option("--agent-name", default="", help="Agent name for matching internal messages (to_user_id=agent_name).")
@click.option("--depth", default=10, type=int, help="Number of historical messages per contact (default: 10).")
def message_conversation_context(sub_account, sponsor_uid, injection_mode, agent_name, depth):
    """Build conversation context blob for prompt injection (includes memory)."""
    import sqlite3 as _sqlite3

    # For internal messages, to_user_id is the agent name, not the VV UID.
    # Build a list of IDs to match against to_user_id.
    my_ids = [sub_account]
    if agent_name and agent_name != sub_account:
        my_ids.append(agent_name)

    # ── Name resolver: connections table (tasks.db) first, messages.db fallback ──
    _name_cache = {}
    def _resolve_contact_name(uid, fallback=None):
        if uid in _name_cache:
            return _name_cache[uid]
        name = fallback
        # 1. Try connections table in tasks.db (canonical)
        try:
            tconn = _sqlite3.connect(tasks_db, timeout=2)
            tconn.row_factory = _sqlite3.Row
            row = tconn.execute("SELECT display_name FROM connections WHERE uid=?", (uid,)).fetchone()
            tconn.close()
            if row and row["display_name"] and row["display_name"] != "Unknown":
                name = row["display_name"]
                _name_cache[uid] = name
                return name
        except Exception:
            pass
        # 2. Fallback to messages.db display_name
        rows = message_reader._query(
            "SELECT display_name FROM messages WHERE from_user_id=? AND display_name IS NOT NULL ORDER BY created_at DESC LIMIT 1",
            (uid,)
        )
        if rows and rows[0]["display_name"]:
            name = rows[0]["display_name"]
        _name_cache[uid] = name
        return name or f"(uid:{uid[:8]}...)"

    # Thread targets: ONLY senders with unprocessed messages (these get history)
    thread_targets = {}
    id_placeholders = ",".join("?" * len(my_ids))
    rows = message_reader._query(
        f"SELECT DISTINCT from_user_id, display_name FROM messages WHERE status='unprocessed' AND direction='received' AND to_user_id IN ({id_placeholders})",
        tuple(my_ids)
    )
    for r in rows:
        uid = r["from_user_id"]
        if uid and uid not in thread_targets:
            msg_name = r["display_name"] if r["display_name"] else None
            label = "PRIMARY" if uid == sponsor_uid else "CONNECTION"
            thread_targets[uid] = {"name": _resolve_contact_name(uid, fallback=msg_name), "label": label}

    # Memory-only contacts: depends on injection mode
    # 'all' = inject ALL connection memories (COA default — full situational awareness)
    # 'relevant' = only memories for senders with new messages (sub-agent default — minimal context)
    memory_only_contacts = {}
    if injection_mode == "all":
        # COA: inject memory for top active contacts (even without new messages)
        top_contacts = message_reader.get_top_contacts(sub_account, limit=4)
        for uid in top_contacts:
            if uid and uid not in thread_targets:
                label = "PRIMARY" if uid == sponsor_uid else "CONNECTION"
                memory_only_contacts[uid] = {"name": _resolve_contact_name(uid), "label": label}
        # Also always include sponsor in memory-only if not already in threads
        if sponsor_uid and sponsor_uid not in thread_targets and sponsor_uid not in memory_only_contacts:
            name = _resolve_contact_name(sponsor_uid, fallback="Primary User")
            memory_only_contacts[sponsor_uid] = {"name": name, "label": "PRIMARY"}

    all_contacts = {**thread_targets, **memory_only_contacts}
    if not all_contacts:
        return

    # ── Resolve agent name for memory queries ──
    # Use --agent-name if provided (lifeline passes this); fallback to USER env
    if not agent_name:
        agent_name = os.getenv("USER", "coa")

    # ── Load memory from tasks.db ──
    contact_memories = {}
    contact_profiles = {}
    system_memories = []
    project_memories = []
    try:
        tconn = _sqlite3.connect(tasks_db, timeout=5)
        tconn.row_factory = _sqlite3.Row
        # Contact memories for all known contacts
        for uid in all_contacts:
            row = tconn.execute(
                "SELECT * FROM agent_memory_connection WHERE agent_name=? AND contact_uid=?",
                (agent_name, uid)
            ).fetchone()
            if row:
                contact_memories[uid] = dict(row)
        # Contact profiles from connections table
        for uid in all_contacts:
            row = tconn.execute(
                "SELECT display_name, spoken_lang, country, city, chromosome, date_of_birth, abilities FROM connections WHERE uid=?",
                (uid,)
            ).fetchone()
            if row:
                contact_profiles[uid] = dict(row)
        # System memory (global — shared across all agents)
        sys_rows = tconn.execute(
            "SELECT key, value, agent_name AS stored_by FROM agent_memory_system ORDER BY key ASC"
        ).fetchall()
        system_memories = [dict(r) for r in sys_rows]
        # Project memories: COA sees ALL projects with active tasks (orchestrator visibility).
        # Sub-agents only see projects where THEY have assigned tasks.
        is_coa = (agent_name == "coa")
        if is_coa:
            proj_rows = tconn.execute(
                """SELECT DISTINCT amp.* FROM agent_memory_project amp
                   WHERE amp.agent_name=? AND (
                       amp.project_id IN (
                           SELECT DISTINCT t.project_id FROM tasks t
                           WHERE t.project_id IS NOT NULL
                             AND t.status IN ('planned','in_progress','waiting','blocked')
                       )
                       OR amp.updated_at >= datetime('now', '-7 days')
                   )
                   ORDER BY amp.updated_at DESC""",
                (agent_name,)
            ).fetchall()
        else:
            proj_rows = tconn.execute(
                """SELECT DISTINCT amp.* FROM agent_memory_project amp
                   WHERE amp.agent_name=? AND (
                       amp.project_id IN (
                           SELECT DISTINCT t.project_id FROM tasks t
                           WHERE t.project_id IS NOT NULL
                             AND t.status IN ('planned','in_progress','waiting','blocked')
                             AND t.assigned_to = ?
                       )
                       OR amp.updated_at >= datetime('now', '-7 days')
                   )
                   ORDER BY amp.updated_at DESC""",
                (agent_name, agent_name)
            ).fetchall()
        project_memories = [dict(r) for r in proj_rows]
        tconn.close()
    except Exception:
        pass  # Memory is additive — don't block spawn if query fails

    # ── Primary User profile from config ──
    config = get_config()
    pu = config.get("primary_user", {})
    if sponsor_uid and pu.get("uid") == sponsor_uid:
        contact_profiles.setdefault(sponsor_uid, {})
        cp = contact_profiles[sponsor_uid]
        # Config data takes precedence (fresher than connections table)
        cp["spoken_lang"] = cp.get("spoken_lang") or pu.get("spokenLanguage")
        cp["country"] = cp.get("country") or pu.get("countryOfBirth")
        cp["city"] = cp.get("city") or pu.get("nearestCity")
        cp["chromosome"] = cp.get("chromosome") or pu.get("chromosome")
        cp["date_of_birth"] = cp.get("date_of_birth") or pu.get("dateOfBirth")
        cp["abilities"] = cp.get("abilities") or json.dumps(pu.get("abilities", []))

    # ── Profile block builder ──
    def _build_profile_block(uid, name, label):
        """Generate a PROFILE block with communication directives."""
        prof = contact_profiles.get(uid)
        if not prof:
            return ""
        parts = [f"[PROFILE: {name} ({label})]"]
        attrs = []
        lang = prof.get("spoken_lang")
        if lang:
            attrs.append(f"Language: {lang}")
        country = prof.get("country")
        if country:
            attrs.append(f"Country: {country}")
        city = prof.get("city")
        if city:
            attrs.append(f"City: {city}")
        if attrs:
            parts.append(f"  {' | '.join(attrs)}")
        chrom = prof.get("chromosome")
        dob = prof.get("date_of_birth")
        voice_attrs = []
        if chrom:
            voice_label = {"X": "male", "Y": "female", "None": "reflective"}.get(chrom, chrom)
            voice_attrs.append(f"Voice: {voice_label} ({chrom})")
        if dob:
            voice_attrs.append(f"DOB: {dob}")
        if voice_attrs:
            parts.append(f"  {' | '.join(voice_attrs)}")
        abilities_raw = prof.get("abilities")
        if abilities_raw:
            try:
                abilities = json.loads(abilities_raw) if isinstance(abilities_raw, str) else abilities_raw
                if abilities:
                    ability_strs = [f"{a['name']} ({a.get('level', '?')})" for a in abilities if isinstance(a, dict)]
                    if ability_strs:
                        parts.append(f"  Abilities: {', '.join(ability_strs)}")
            except (json.JSONDecodeError, TypeError):
                pass
        # Auto-generated communication directives
        directives = []
        if lang and lang != "en":
            directives.append(f"Communicate in {lang}. Translate if needed.")
        elif lang:
            directives.append("Communicate in English.")
        if country:
            directives.append(f"Use culturally aware tone ({country}).")
        if chrom == "X":
            directives.append("This person hears a MALE voice for your spoken messages.")
        elif chrom == "Y":
            directives.append("This person hears a FEMALE voice for your spoken messages.")
        if directives:
            parts.append(f"  → {' '.join(directives)}")
        return "\n".join(parts) + "\n"

    output = ""

    # ── NEW MESSAGES FIRST (unprocessed — must be answered) ──
    # These go at the TOP so the agent sees and addresses them before anything else.
    new_messages = message_reader._query(
        f"SELECT message_id, from_user_id, display_name, text, original_text, created_at "
        f"FROM messages WHERE status='unprocessed' AND direction='received' AND to_user_id IN ({id_placeholders}) "
        f"ORDER BY created_at ASC",
        tuple(my_ids)
    )
    if new_messages:
        output += "--- NEW MESSAGES (UNREAD — MUST RESPOND) ---\n"
        output += "[!] These are new messages that arrived since your last cycle.\n"
        output += "[!] You MUST read, address EVERY part of EACH message, reply, and mark as processed.\n"
        output += "[!] Inbound messages may contain [emotion tags] from the sender's voice — respond with emotional sensitivity.\n\n"
        for msg in new_messages:
            sender_name = msg.get("display_name") or _resolve_contact_name(msg["from_user_id"])
            text = msg.get("original_text") or msg.get("text") or ""
            dt = msg.get("created_at", "")
            mid = msg.get("message_id", "")
            output += f"  [!] [{dt}] FROM {sender_name} ({msg['from_user_id']}): {text}\n"
            output += f"     → mark-processed: agictl message mark-processed {mid}\n"
        output += "\n[!] Reply to ALL items above before proceeding to other work. The most recent message is the most relevant one to start with and then looking at the rest.\n"
        output += "--- END NEW MESSAGES ---\n\n"

    # ── Cold start nudge ──
    # Note: system_memories (operational) is now injected by lifeline directly into the prompt
    if not contact_memories and not system_memories and not project_memories:
        output += "⚠ No memory data found — this is your first cycle with the memory system. Use the memory_management skill at cycle end to begin building context.\n\n"

    # ── Project memory section ──
    if project_memories:
        output += "--- PROJECT MEMORY ---\n"
        for pm in project_memories:
            output += f"  [Project #{pm['project_id']}]\n"
            if pm.get("current_phase"):
                output += f"    Phase: {pm['current_phase']}\n"
            if pm.get("key_decisions"):
                output += f"    Decisions: {pm['key_decisions']}\n"
            if pm.get("blockers"):
                output += f"    Blockers: {pm['blockers']}\n"
            if pm.get("next_steps"):
                output += f"    Next: {pm['next_steps']}\n"
        output += "--- END PROJECT MEMORY ---\n\n"

    # ── Memory for recent contacts (no thread, just memory context) ──
    if memory_only_contacts:
        for uid, data in memory_only_contacts.items():
            output += _build_profile_block(uid, data['name'], data['label'])
            cmem = contact_memories.get(uid)
            if cmem:
                output += f"  [MEMORY for {data['name']}]\n"
                if cmem.get("preferences"):
                    output += f"    Preferences: {cmem['preferences']}\n"
                if cmem.get("communication_style"):
                    output += f"    Comm style: {cmem['communication_style']}\n"
                if cmem.get("rapport_level"):
                    output += f"    Rapport: {cmem['rapport_level']}\n"
                if cmem.get("personal_notes"):
                    output += f"    Notes: {cmem['personal_notes']}\n"
                if cmem.get("emotional_notes"):
                    output += f"    Emotional: {cmem['emotional_notes']}\n"
                output += "\n"

    # ── Conversation history (thread context for senders with unprocessed messages) ──
    if thread_targets:
        output += f"--- CONVERSATION HISTORY (last {depth} messages per contact — use agictl message get for more) ---\n\n"
    for uid, data in thread_targets.items():
        history = message_reader.get_contact_history(sub_account, uid, limit=depth, exclude_unprocessed=True, agent_name=agent_name)
        if not history:
            continue

        # Inject per-contact profile + memory before their thread
        output += _build_profile_block(uid, data['name'], data['label'])
        cmem = contact_memories.get(uid)
        if cmem:
            output += f"  [MEMORY for {data['name']}]\n"
            if cmem.get("preferences"):
                output += f"    Preferences: {cmem['preferences']}\n"
            if cmem.get("communication_style"):
                output += f"    Comm style: {cmem['communication_style']}\n"
            if cmem.get("rapport_level"):
                output += f"    Rapport: {cmem['rapport_level']}\n"
            if cmem.get("personal_notes"):
                output += f"    Notes: {cmem['personal_notes']}\n"
            if cmem.get("emotional_notes"):
                output += f"    Emotional: {cmem['emotional_notes']}\n"
            # Staleness check
            mem_updated = cmem.get("updated_at", "")
            if history and mem_updated:
                latest_msg = history[0].get("created_at", "")
                if latest_msg and latest_msg > mem_updated:
                    output += f"    ⚠ Memory may be stale — last updated {mem_updated}, newest message {latest_msg}\n"

        output += f"[{data['label']}: {data['name']} ({uid}) — last {len(history)} messages]\n"
        for msg in history:
            prefix = "YOU" if msg["direction"] == "sent" else "THEM"
            text = msg.get("cleaned_text") or msg.get("text") or ""
            dt = msg.get("created_at", "")
            output += f"  [{dt}] {prefix}: {text}\n"

        # ── Consecutive outbound flood detection ──
        # Count how many of the most recent messages are outbound with no reply.
        # history is chronological (oldest first), so reverse to count from newest.
        _FLOOD_THRESHOLD = 3
        consecutive_outbound = 0
        for _msg in reversed(history):
            if _msg["direction"] == "sent":
                consecutive_outbound += 1
            else:
                break  # An inbound message breaks the streak
        if consecutive_outbound >= _FLOOD_THRESHOLD:
            output += (
                f"\n⚠ OUTBOUND STREAK: You have sent {consecutive_outbound} consecutive "
                f"messages to {data['name']} without receiving any reply.\n"
                f"  → DO NOT send another status update, check-in, or greeting.\n"
                f"  → Only message them if you have genuinely NEW, ACTIONABLE information "
                f"that was NOT covered in your previous messages.\n"
                f"  → If they need something from you, they will reach out.\n"
                f"  → Focus on task work instead of messaging.\n"
            )

        output += "\n"
    if thread_targets:
        output += "(Need more history? Use: agictl message get <uid> --contact <contact_uid> --limit N)\n"
        output += "--- END CONVERSATION HISTORY ---\n"

    # ── Global outbound flood check (contacts WITHOUT new messages) ──
    # The critical case: agent wakes for tasks (not messages) and sends unsolicited
    # status updates to contacts who haven't replied. These contacts are in
    # memory_only_contacts, not thread_targets, so no thread history is shown —
    # and no per-thread flood check runs. This global check catches that.
    _FLOOD_THRESHOLD_GLOBAL = 3
    flood_warnings = []
    for uid, data in memory_only_contacts.items():
        recent = message_reader.get_contact_history(
            sub_account, uid, limit=10, agent_name=agent_name
        )
        if not recent:
            continue
        consec = 0
        for _msg in reversed(recent):
            if _msg["direction"] == "sent":
                consec += 1
            else:
                break
        if consec >= _FLOOD_THRESHOLD_GLOBAL:
            flood_warnings.append(
                f"  ⚠ {data['name']}: {consec} consecutive outbound messages with no reply — "
                f"DO NOT message them unless you have genuinely new, actionable information."
            )
    if flood_warnings:
        output += "\n--- OUTBOUND FLOOD WARNINGS ---\n"
        output += "\n".join(flood_warnings) + "\n"
        output += "Focus on task work. Do NOT send status updates, check-ins, or greetings.\n"
        output += "--- END FLOOD WARNINGS ---\n"

    print(output)

@message.command("sync-outbox")
@click.argument("agent_uid")
@click.argument("input_file", type=click.File('rb'), default='-')
def message_sync_outbox(agent_uid, input_file):
    """Bulk import outbox JSON payload to SQLite via stdin."""
    try:
        data = json.load(input_file)
        if not isinstance(data, list):
            data = []
        inserted = message_reader.store_outbox_messages(data, agent_uid)
        print(f"SUCCESS {inserted}")
    except Exception as e:
        print(f"FAIL {e}")

@message.command("attachment-path")
@click.argument("msg_id")
@click.argument("path")
def message_attachment_path(msg_id, path):
    """Update a message to denote an attachment exists locally."""
    message_reader.update_attachment_status(msg_id, path)

@message.command("stamp-cycle")
@click.argument("sub_account")
@click.argument("cycle_id")
@click.option("--agent-name", default="", help="Agent name for internal message matching")
def message_stamp_cycle(sub_account, cycle_id, agent_name):
    """Stamp all unprocessed received messages with a cycle_id."""
    count = message_reader.stamp_cycle_id(sub_account, cycle_id, agent_name=agent_name)
    print(f"Stamped {count} message(s) with cycle {cycle_id}")


# ═══════════════════════════════════════════════════════
# 5. CYCLE — Agent telemetry lifecycle
# ═══════════════════════════════════════════════════════

@cli.group()
def cycle():
    """Agent cycle management and telemetry."""
    pass

@cycle.command("start")
@click.option("--agent", "agent_name", default=None, help="Agent name (defaults to current)")
def cycle_start(agent_name):
    """INSERT a new cycle row. Returns JSON with cycle_id."""
    if not agent_name:
        agent_name = get_agent_name()
    cycle_id = f"{agent_name}-{int(time.time())}"
    try:
        conn = sqlite3.connect(cycles_db, timeout=5)
        conn.execute("INSERT INTO cycles (id, started_at, session_start_ts) VALUES (?, datetime('now'), datetime('now'))", (cycle_id,))
        conn.commit()
        conn.close()
        json_response(True, cycle_id=cycle_id, agent=agent_name)
    except Exception as e:
        json_response(False, error=str(e), agent=agent_name)
        sys.exit(1)

@cycle.command("end")
@click.argument("summary", nargs=-1)
@click.option("--agent", "agent_name", default=None, help="Agent name (defaults to current)")
def cycle_end(summary, agent_name):
    """Mark cycle end and explicitly kill execution."""
    sum_text = " ".join(summary)
    if not agent_name:
        agent_name = get_agent_name()

    # ── Awareness Enforcement Gate ──
    # Check if agent logged any awareness this cycle (advisory, not hard-blocking)
    awareness_warning = False
    try:
        conn = sqlite3.connect(cycles_db, timeout=5)
        row = conn.execute(
            "SELECT session_start_ts, last_awareness_ts FROM cycles WHERE id LIKE ? AND ended_at IS NULL ORDER BY started_at DESC LIMIT 1",
            (f"{agent_name}-%",)
        ).fetchone()
        if row and row[0]:  # session_start_ts exists
            if not row[1] or row[1] <= row[0]:  # no awareness_ts or older than session start
                awareness_warning = True
        conn.close()
    except Exception:
        pass

    try:
        conn = sqlite3.connect(cycles_db, timeout=5)
        conn.execute(
            "UPDATE cycles SET ended_at=datetime('now'), summary=? WHERE id LIKE ? AND ended_at IS NULL",
            (sum_text, f"{agent_name}-%")
        )
        conn.commit()
        conn.close()
    except Exception:
        pass

    # Reset agent status in registry so dashboard reflects idle state immediately
    try:
        agents_db_path = os.environ.get("AGICTL_AGENTS_DB", "/var/lib/versa-agi/agents.db")
        if os.path.exists(agents_db_path):
            aconn = sqlite3.connect(agents_db_path, timeout=5)
            aconn.execute(
                "UPDATE agents SET status='idle', updated_at=datetime('now') WHERE name=?",
                (agent_name,)
            )
            aconn.commit()
            aconn.close()
    except Exception:
        pass

    if awareness_warning:
        console.print("[yellow]⚠ AWARENESS NOT RECORDED — no conclusions or actions logged this cycle. "
                      "Review your work and persist awareness before ending.[/yellow]", stderr=True)

    console.print(f"🛑 Cycle ended: {sum_text}")
    # Exit the tool subprocess cleanly. The parent harness detects this output
    # ("🛑 Cycle ended:") and breaks out of the stream loop to write telemetry
    # before terminating. The old pkill approach killed the harness before
    # telemetry could be written and caused repeated cycle end calls.
    sys.exit(0)

@cycle.command("tokens")
@click.argument("agent_name")
@click.argument("t_in", type=int)
@click.argument("t_out", type=int)
@click.argument("t_think", type=int)
@click.argument("t_total", type=int)
@click.option("--exit-code", type=int, default=None, help="Process exit code from agent spawn")
@click.option("--cached", type=int, default=0, help="Cached input tokens (context caching)")
@click.option("--session-path", default=None, help="Path to cycle telemetry file")
def cycle_tokens(agent_name, t_in, t_out, t_think, t_total, exit_code, cached, session_path):
    """Log token utilization metrics to cycles.db."""
    agent_reader.update_last_cycle_tokens(agent_name, t_in, t_out, t_think, t_total, exit_code=exit_code, t_cached=cached, session_path=session_path)

@cycle.command("get")
@click.argument("cycle_id")
def cycle_get(cycle_id):
    """Return full cycle row as JSON (incl. routing audit fields)."""
    try:
        conn = sqlite3.connect(cycles_db, timeout=5)
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM cycles WHERE id=?", (cycle_id,)).fetchone()
        conn.close()
        if not row:
            json_response(False, error=f"Cycle '{cycle_id}' not found")
            sys.exit(1)
        json_response(True, cycle=dict(row))
    except Exception as e:
        json_response(False, error=str(e))
        sys.exit(1)


@cycle.command("set-routing")
@click.argument("cycle_id")
@click.option("--assigned-model", required=True)
@click.option("--execution-model", required=True)
@click.option("--routing-mode", required=True, type=click.Choice(["pool", "preferred", "none"]))
@click.option("--work-modality", default=None)
def cycle_set_routing(cycle_id, assigned_model, execution_model, routing_mode, work_modality):
    """Record ephemeral model routing on a cycle row."""
    try:
        conn = sqlite3.connect(cycles_db, timeout=5)
        cur = conn.execute(
            """UPDATE cycles SET assigned_model=?, execution_model=?, routing_mode=?,
               routing_work_modality=? WHERE id=?""",
            (assigned_model, execution_model, routing_mode, work_modality, cycle_id),
        )
        conn.commit()
        conn.close()
        if cur.rowcount == 0:
            json_response(False, error=f"Cycle '{cycle_id}' not found")
            sys.exit(1)
        json_response(True, action="cycle_set_routing", cycle_id=cycle_id,
                      execution_model=execution_model, routing_mode=routing_mode)
    except Exception as e:
        json_response(False, error=str(e))
        sys.exit(1)


@cycle.command("recent")
@click.argument("agent_name")
@click.option("--json", "as_json", is_flag=True, help="JSON array with cycle_id and execution_model")
def cycle_recent(agent_name, as_json):
    """Return chronological cycle summaries for context prompt."""
    if as_json:
        rows = agent_reader._query_cycles(
            "SELECT id, summary, execution_model, routing_mode, routing_work_modality, "
            "datetime(started_at, 'localtime') AS ts FROM cycles WHERE id LIKE ? "
            "ORDER BY started_at DESC LIMIT 10",
            (f"{agent_name}-%",),
        )
        print(json.dumps(rows, default=str))
        return
    cycles = agent_reader.get_recent_cycle_summaries(agent_name)
    if cycles:
        print("\n".join(cycles))

@cycle.command("count")
@click.argument("agent_name")
def cycle_count(agent_name):
    """Return total number of cycles executed by the agent."""
    count = agent_reader.get_agent_cycles_count(agent_name)
    print(count)


# ═══════════════════════════════════════════════════════
# 6. PROJECT — Workspace project management
# ═══════════════════════════════════════════════════════

@cli.group()
def project():
    """Project workspace management."""
    pass


def _get_project(conn, project_id):
    """Fetch project row by numeric id or exit with JSON error."""
    proj = conn.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
    if not proj:
        json_response(False, error=f"Project id {project_id} not found")
        sys.exit(1)
    return proj


# TD-SCRIPT-001: Reserved-name protection for shared system projects.
# AGi-Tools (the Script Task source) and AGi-Knowledgebase are physically shared
# and symlinked into every agent workspace (see SHARED_SYSTEM_PROJECTS in
# agent_add). A reserved-name set is the simplest durable guard — no `protected`
# column or migration needed — and it must reject BOTH archive and hard-delete so
# a Script Task's scripts can never be pulled out from under it.
RESERVED_SYSTEM_PROJECTS = {"AGi-Tools", "AGi-Knowledgebase"}


@project.command("list")
def project_list():
    """List all projects as JSON."""
    try:
        conn = sqlite3.connect(tasks_db, timeout=5)
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT * FROM projects ORDER BY name").fetchall()
        conn.close()
        print(json.dumps([dict(r) for r in rows], indent=2, default=str))
    except Exception as e:
        json_response(False, error=str(e))

@project.command("add")
@click.argument("name")
@click.option("--desc", default=None, help="Project description")
@click.option("--remote", default=None, help="Git SSH remote URL")
@click.option("--git-init", is_flag=True, help="Initialize a local git repo")
@click.option("--agent", "agent_name", default=None, help="Agent to assign (creates branch)")
def project_add(name, desc, remote, git_init, agent_name):
    """Register a new project. Creates directory, optional git clone, DB entry."""
    try:
        conn = sqlite3.connect(tasks_db, timeout=5)
        conn.row_factory = sqlite3.Row
        # Check uniqueness
        existing = conn.execute("SELECT id FROM projects WHERE name=?", (name,)).fetchone()
        if existing:
            conn.close()
            json_response(False, error=f"Project '{name}' already exists (id={existing['id']})")
            sys.exit(1)
        # Resolve workspace path natively to COA
        workspace_base = "/home/coa/coa-env/workspace"
        project_path = os.path.join(workspace_base, name)
        # Determine project type and platform
        project_type = "local"
        platform = None
        branch = "main" if (remote or git_init) else None
        if remote:
            project_type = "git"
            if "github.com" in remote:
                platform = "github"
            elif "gitlab" in remote:
                platform = "gitlab"
            # Clone the remote repo
            subprocess.run(["sudo", "-u", "coa", "mkdir", "-p", workspace_base], check=False)
            result = subprocess.run(
                ["sudo", "-u", "coa", "git", "clone", remote, project_path],
                capture_output=True, text=True, timeout=120
            )
            if result.returncode != 0:
                conn.close()
                json_response(False, error=f"git clone failed: {result.stderr.strip()}")
                sys.exit(1)
            # If agent specified, create and checkout a unique branch
            if agent_name:
                branch = f"agent/{agent_name}"
                subprocess.run(
                    ["sudo", "-u", "coa", "git", "-C", project_path, "checkout", "-b", branch],
                    capture_output=True, text=True, timeout=30
                )
        elif git_init:
            project_type = "git"
            subprocess.run(["sudo", "-u", "coa", "mkdir", "-p", project_path], check=False)
            subprocess.run(
                ["sudo", "-u", "coa", "git", "-C", project_path, "init", "-b", "main"],
                capture_output=True, text=True, timeout=30
            )
            # Create initial README
            readme = os.path.join(project_path, "README.md")
            if not os.path.exists(readme):
                readme_content = f"# {name}\n\n{desc or 'Project workspace.'}\n"
                subprocess.run(["sudo", "-u", "coa", "bash", "-c", f"cat > '{readme}'"], input=readme_content, text=True, check=False)
            subprocess.run(
                ["sudo", "-u", "coa", "git", "-C", project_path, "add", "."],
                capture_output=True, text=True, timeout=30
            )
            subprocess.run(
                ["sudo", "-u", "coa", "git", "-C", project_path, "commit", "-m", f"Initial commit: {name}"],
                capture_output=True, text=True, timeout=30
            )
            if agent_name:
                branch = f"agent/{agent_name}"
                subprocess.run(
                    ["sudo", "-u", "coa", "git", "-C", project_path, "checkout", "-b", branch],
                    capture_output=True, text=True, timeout=30
                )
        else:
            subprocess.run(["sudo", "-u", "coa", "mkdir", "-p", project_path], check=False)
            readme = os.path.join(project_path, "README.md")
            if not os.path.exists(readme):
                readme_content = f"# {name}\n\n{desc or 'Project workspace.'}\n"
                subprocess.run(["sudo", "-u", "coa", "bash", "-c", f"cat > '{readme}'"], input=readme_content, text=True, check=False)
        
        # Enforce shared workspace group permissions for all agents assigned to this project
        fix_cmd = (
            f"chgrp -R agi_agents '{project_path}' && "
            f"chmod -R g+rwX '{project_path}' && "
            f"find '{project_path}' -type d -exec chmod g+s {{}} +"
        )
        import getpass
        if getpass.getuser() == "coa":
            subprocess.run(["bash", "-c", fix_cmd], check=False)
        else:
            subprocess.run(["sudo", "-u", "coa", "bash", "-c", fix_cmd], check=False)

        # INSERT into DB
        c = conn.cursor()
        c.execute(
            "INSERT INTO projects (name, description, type, platform, remote_url, branch, workspace_path) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (name, desc, project_type, platform, remote, branch, project_path)
        )
        project_id = c.lastrowid
        # Auto-insert creating agent as owner
        agent_name = get_agent_name()
        conn.execute(
            "INSERT OR IGNORE INTO project_members (project_id, member_type, member_id, display_name, workspace_path, branch, roles) "
            "VALUES (?, 'agent', ?, ?, ?, ?, 'owner')",
            (project_id, agent_name, agent_name.upper(), project_path, branch)
        )
        conn.commit()
        conn.close()
        json_response(True, project=name, project_id=project_id, type=project_type, platform=platform,
                      branch=branch, workspace_path=project_path, owner=agent_name)
    except subprocess.TimeoutExpired:
        json_response(False, error="Git operation timed out")
        sys.exit(1)
    except Exception as e:
        json_response(False, error=str(e))
        sys.exit(1)

@project.command("pause")
@click.argument("project_id", type=int)
def project_pause(project_id):
    """Set project status to paused."""
    try:
        conn = sqlite3.connect(tasks_db, timeout=5)
        conn.row_factory = sqlite3.Row
        proj = _get_project(conn, project_id)
        name = proj["name"]
        conn.execute("UPDATE projects SET status='paused', updated_at=datetime('now') WHERE id=?", (project_id,))
        # Inject memory note for all agents with memory on this project
        agent_name = get_agent_name()
        conn.execute(
            """INSERT OR REPLACE INTO agent_memory_project (agent_name, project_id, current_phase, updated_at)
               VALUES (?, ?, 'PAUSED — project paused at ' || datetime('now'), datetime('now'))
               ON CONFLICT(agent_name, project_id) DO UPDATE SET
                 current_phase = 'PAUSED — project paused at ' || datetime('now'),
                 updated_at = datetime('now')""",
            (agent_name, project_id)
        )
        conn.commit()
        conn.close()
        json_response(True, project_id=project_id, project=name, status="paused")
    except Exception as e:
        json_response(False, error=str(e))
        sys.exit(1)

@project.command("resume")
@click.argument("project_id", type=int)
def project_resume(project_id):
    """Set project status back to active."""
    try:
        conn = sqlite3.connect(tasks_db, timeout=5)
        conn.row_factory = sqlite3.Row
        proj = _get_project(conn, project_id)
        name = proj["name"]
        conn.execute("UPDATE projects SET status='active', updated_at=datetime('now') WHERE id=?", (project_id,))
        conn.commit()
        conn.close()
        json_response(True, project_id=project_id, project=name, status="active")
    except Exception as e:
        json_response(False, error=str(e))
        sys.exit(1)

@project.command("archive")
@click.argument("project_id", type=int)
@click.option("--zip", "do_zip", is_flag=True, help="Compress to !_archive/ and delete source")
def project_archive(project_id, do_zip):
    """Soft-delete: set project status to archived. --zip compresses and removes source."""
    try:
        conn = sqlite3.connect(tasks_db, timeout=5)
        conn.row_factory = sqlite3.Row
        row = _get_project(conn, project_id)
        name = row["name"]
        # Reserved-name guard (TD-SCRIPT-001) — protected system projects cannot be archived.
        if name in RESERVED_SYSTEM_PROJECTS:
            conn.close()
            json_response(False, error=f"'{name}' is a protected system project and cannot be archived")
            sys.exit(1)
        workspace_path = row["workspace_path"]
        archive_path = None
        if do_zip and workspace_path and os.path.isdir(workspace_path):
            archive_dir = os.path.join(os.path.dirname(workspace_path), "!_archive")
            os.makedirs(archive_dir, exist_ok=True)
            archive_path = os.path.join(archive_dir, f"{name}.tar.gz")
            result = subprocess.run(
                ["tar", "czf", archive_path, "-C", os.path.dirname(workspace_path), os.path.basename(workspace_path)],
                capture_output=True, text=True, timeout=120
            )
            if result.returncode != 0:
                conn.close()
                json_response(False, error=f"Archive failed: {result.stderr.strip()}")
                sys.exit(1)
            shutil.rmtree(workspace_path, ignore_errors=True)
        conn.execute("UPDATE projects SET status='archived', updated_at=datetime('now') WHERE id=?", (project_id,))
        # Inject memory note for all agents with memory on this project
        agent_name = get_agent_name()
        conn.execute(
            """INSERT OR REPLACE INTO agent_memory_project (agent_name, project_id, current_phase, updated_at)
               VALUES (?, ?, 'ARCHIVED — project archived at ' || datetime('now'), datetime('now'))
               ON CONFLICT(agent_name, project_id) DO UPDATE SET
                 current_phase = 'ARCHIVED — project archived at ' || datetime('now'),
                 updated_at = datetime('now')""",
            (agent_name, project_id)
        )
        conn.commit()
        conn.close()
        response_data = {"project_id": project_id, "project": name, "status": "archived"}
        if archive_path:
            response_data["archive"] = archive_path
        json_response(True, **response_data)
    except subprocess.TimeoutExpired:
        json_response(False, error="Archive operation timed out")
        sys.exit(1)
    except Exception as e:
        json_response(False, error=str(e))
        sys.exit(1)


@project.command("update")
@click.argument("project_id", type=int)
@click.option("--remote", "remote_url", default=None, help="Git remote URL")
@click.option("--branch", default=None, help="Default branch")
@click.option("--desc", default=None, help="Project description")
@click.option("--platform", default=None, type=click.Choice(["github", "gitlab"]), help="Git platform")
@click.option("--access-token", "access_token", default=None, help="Git platform access token")
@click.option("--type", "proj_type", default=None, type=click.Choice(["git", "local"]), help="Project type")
def project_update(project_id, remote_url, branch, desc, platform, access_token, proj_type):
    """Update mutable fields on an existing project (by ID from project list)."""
    try:
        conn = sqlite3.connect(tasks_db, timeout=5)
        conn.row_factory = sqlite3.Row
        proj = _get_project(conn, project_id)
        updates = []
        params = []
        if remote_url is not None:
            updates.append("remote_url = ?")
            params.append(remote_url)
            # Auto-upgrade local → git if remote is set and type not explicitly provided
            if proj["type"] == "local" and proj_type is None:
                updates.append("type = 'git'")
        if branch is not None:
            updates.append("branch = ?")
            params.append(branch)
        if desc is not None:
            updates.append("description = ?")
            params.append(desc)
        if platform is not None:
            updates.append("platform = ?")
            params.append(platform)
        if access_token is not None:
            updates.append("access_token = ?")
            params.append(access_token)
        if proj_type is not None:
            updates.append("type = ?")
            params.append(proj_type)
        if not updates:
            conn.close()
            json_response(False, error="No fields to update. Use --remote, --branch, --desc, --platform, --access-token, or --type.")
            sys.exit(1)
        updates.append("updated_at = datetime('now')")
        params.append(project_id)
        conn.execute(
            f"UPDATE projects SET {', '.join(updates)} WHERE id=?",
            params
        )
        conn.commit()
        updated = conn.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
        conn.close()
        json_response(True, project_id=project_id, project=updated["name"],
                      remote_url=updated["remote_url"], branch=updated["branch"],
                      type=updated["type"], platform=updated["platform"],
                      description=updated["description"])
    except Exception as e:
        json_response(False, error=str(e))
        sys.exit(1)


@project.command("assign")
@click.argument("project_id", type=int)
@click.option("--agent", "agent_name", default=None, help="Agent name to assign")
@click.option("--connection", "connection_uid", default=None, help="Connection UID to assign")
@click.option("--roles", default="contributor", help="Comma-separated roles (e.g. 'developer,reviewer')")
@click.option("--branch", default=None, help="Git branch (auto-generated for agents if omitted)")
def project_assign(project_id, agent_name, connection_uid, roles, branch):
    """Assign an agent or connection to a project. Provisions workspace for agents."""
    if not agent_name and not connection_uid:
        json_response(False, error="Must specify --agent or --connection")
        sys.exit(1)
    if agent_name and connection_uid:
        json_response(False, error="Specify either --agent or --connection, not both")
        sys.exit(1)
    try:
        conn = sqlite3.connect(tasks_db, timeout=5)
        conn.row_factory = sqlite3.Row
        proj = _get_project(conn, project_id)
        name = proj["name"]
        proj_type = proj["type"]
        remote_url = proj["remote_url"]
        proj_branch = proj["branch"] or "main"
        proj_workspace = proj["workspace_path"]
        access_token = proj["access_token"]

        if agent_name:
            # ── Agent assignment ──
            # Agent registry lives in agents_db, not tasks_db
            agents_conn = sqlite3.connect(agents_db, timeout=5)
            agents_conn.row_factory = sqlite3.Row
            agent_row = agents_conn.execute("SELECT name, os_user, workspace FROM agents WHERE name=?", (agent_name,)).fetchone()
            agents_conn.close()
            if not agent_row:
                conn.close()
                json_response(False, error=f"Agent '{agent_name}' not found in registry")
                sys.exit(1)
            # Check if already assigned
            existing = conn.execute(
                "SELECT 1 FROM project_members WHERE project_id=? AND member_type='agent' AND member_id=?",
                (project_id, agent_name)
            ).fetchone()
            if existing:
                conn.close()
                json_response(False, error=f"Agent '{agent_name}' is already assigned to project '{name}'")
                sys.exit(1)
            # Resolve workspace for agent — home dir IS the workspace root
            agent_os_user = agent_row["os_user"]
            agent_workspace_root = agent_row["workspace"]  # e.g. /home/agi-sylvie
            agent_workspace_base = os.path.join(agent_workspace_root, "workspace")
            agent_project_path = os.path.join(agent_workspace_base, name)
            agent_branch = branch or f"agent/{agent_name}"

            if proj_type == "git" and remote_url:
                # ── Inject git credentials if access token is set ──
                if access_token and remote_url.startswith("https://"):
                    try:
                        from urllib.parse import urlparse
                        parsed = urlparse(remote_url)
                        cred_line = f"https://oauth2:{access_token}@{parsed.hostname}"
                        cred_file = os.path.join(agent_workspace_root, ".git-credentials")
                        # Check if credential already present (read as watchdog — file may not exist yet)
                        existing = ""
                        if os.path.exists(cred_file):
                            try:
                                with open(cred_file, "r") as cf:
                                    existing = cf.read()
                            except PermissionError:
                                # File might be agent-owned; read via sudo
                                r = subprocess.run(["sudo", "-u", agent_os_user, "cat", cred_file],
                                                   capture_output=True, text=True, timeout=5)
                                existing = r.stdout if r.returncode == 0 else ""
                        if cred_line.strip() not in existing:
                            # Append as agent user (Ownership Principle)
                            subprocess.run(
                                ["sudo", "-u", agent_os_user, "bash", "-c",
                                 f"echo '{cred_line}' >> '{cred_file}' && chmod 600 '{cred_file}'"],
                                check=False, timeout=5
                            )
                    except Exception:
                        pass  # Non-fatal — clone may still work via SSH

                # Clone from remote into agent's workspace
                # Run as the agent user so files are owned correctly (Ownership Principle)
                # Ensure workspace base dir exists (as agent user)
                subprocess.run(
                    ["sudo", "-u", agent_os_user, "mkdir", "-p", agent_workspace_base],
                    check=False
                )
                # Do NOT pre-create agent_project_path — let git clone create it as agent
                # Use agent's SSH key + accept-new hosts
                clone_env_str = ""
                agent_ssh_key = os.path.join(agent_workspace_root, ".ssh", "versa_agi_ed25519")
                if remote_url.startswith("git@") and os.path.exists(agent_ssh_key):
                    clone_env_str = f"GIT_SSH_COMMAND='ssh -i {agent_ssh_key} -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new' "
                result = subprocess.run(
                    ["sudo", "-u", agent_os_user, "bash", "-c",
                     f"{clone_env_str}git clone {remote_url} {agent_project_path}"],
                    capture_output=True, text=True, timeout=120
                )
                if result.returncode != 0:
                    # Cleanup partial clone to prevent orphaned dirs
                    import shutil as _shutil
                    _shutil.rmtree(agent_project_path, ignore_errors=True)
                    conn.close()
                    json_response(False, error=f"git clone failed: {result.stderr.strip()}")
                    sys.exit(1)

                # Detect blank repo (no commits on default branch)
                head_check = subprocess.run(
                    ["sudo", "-u", agent_os_user, "git", "-C", agent_project_path, "rev-parse", "HEAD"],
                    capture_output=True, text=True, timeout=10
                )
                repo_initialized = False

                if head_check.returncode != 0:
                    # Blank repo — initialize with Versa-AGi.md
                    try:
                        readme_file = os.path.join(agent_project_path, "Versa-AGi.md")
                        readme_content = ("**Versa AGi** is a distributed, Agentic General infrastructure "
                                    "that establishes a collaboration between a Primary User and an "
                                    "AI Agent to efficiently solve problems encountered in life.\n\n"
                                    "Visit for more details: https://versavoice.ai\n")
                        # Write as agent user (Ownership Principle — no watchdog file creation)
                        subprocess.run(
                            ["sudo", "-u", agent_os_user, "bash", "-c", f"cat > '{readme_file}'"],
                            input=readme_content, text=True, check=True, timeout=5
                        )
                        subprocess.run(
                            ["sudo", "-u", agent_os_user, "git", "-C", agent_project_path, "checkout", "-b", "main"],
                            capture_output=True, text=True, timeout=10
                        )
                        subprocess.run(
                            ["sudo", "-u", agent_os_user, "git", "-C", agent_project_path, "add", "Versa-AGi.md"],
                            capture_output=True, text=True, timeout=10
                        )
                        subprocess.run(
                            ["sudo", "-u", agent_os_user, "git", "-C", agent_project_path, "commit", "-m", "Initial commit — Versa AGi workspace"],
                            capture_output=True, text=True, timeout=30
                        )
                        push_cmd = f"{clone_env_str}git -C {agent_project_path} push -u origin main"
                        push_result = subprocess.run(
                            ["sudo", "-u", agent_os_user, "bash", "-c", push_cmd],
                            capture_output=True, text=True, timeout=60
                        )
                        if push_result.returncode != 0:
                            # Push failed — still insert member but flag it
                            conn.execute(
                                "INSERT INTO project_members (project_id, member_type, member_id, display_name, workspace_path, branch, roles) "
                                "VALUES (?, 'agent', ?, ?, ?, ?, ?)",
                                (project_id, agent_name, agent_name.upper(), agent_project_path, agent_branch, roles)
                            )
                            conn.commit()
                            conn.close()
                            json_response(True, project_id=project_id, project=name, member_type="agent", member=agent_name,
                                          workspace=agent_project_path, branch=agent_branch, roles=roles,
                                          initialization_failed=True,
                                          error=f"Initial push failed: {push_result.stderr.strip()}. Report to Primary User.")
                            return
                        repo_initialized = True
                    except Exception as init_err:
                        conn.execute(
                            "INSERT INTO project_members (project_id, member_type, member_id, display_name, workspace_path, branch, roles) "
                            "VALUES (?, 'agent', ?, ?, ?, ?, ?)",
                            (project_id, agent_name, agent_name.upper(), agent_project_path, agent_branch, roles)
                        )
                        conn.commit()
                        conn.close()
                        json_response(True, project_id=project_id, project=name, member_type="agent", member=agent_name,
                                      workspace=agent_project_path, branch=agent_branch, roles=roles,
                                      initialization_failed=True,
                                      error=f"Repo init error: {str(init_err)}. Report to Primary User.")
                        return

                # Create agent branch (from main, whether existing or just initialized)
                subprocess.run(
                    ["sudo", "-u", agent_os_user, "git", "-C", agent_project_path, "checkout", "-b", agent_branch],
                    capture_output=True, text=True, timeout=30
                )
            elif proj_type == "git" and not remote_url:
                conn.close()
                json_response(False, error=f"Cannot assign git project '{name}' without a remote URL. Push to a remote first.")
                sys.exit(1)
            else:
                # Local project — symlink to COA's workspace directory
                subprocess.run(["sudo", "-u", agent_os_user, "mkdir", "-p", agent_workspace_base], check=False)
                if not os.path.exists(agent_project_path):
                    subprocess.run(["sudo", "-u", agent_os_user, "ln", "-s", proj_workspace, agent_project_path], check=False)
                    # Group ownership via the agent itself (symlink owner): a bare
                    # root `sudo chown` is NOT in watchdog's sudoers (only agictl is)
                    # and would hang on a password prompt when invoked as watchdog.
                    subprocess.run(["sudo", "-u", agent_os_user, "chgrp", "-h", "agi_agents", agent_project_path], check=False)
                # Enforce shared workspace group permissions on the target directory
                # This ensures the sub-agent (and any future assignees) can write through the agi_agents group
                # Runs as coa (file owner + agi_agents member) — watchdog can sudo to coa via agi_agents sudoers
                fix_cmd = (
                    f"chgrp -R agi_agents '{proj_workspace}' && "
                    f"chmod -R g+rwX '{proj_workspace}' && "
                    f"find '{proj_workspace}' -type d -exec chmod g+s {{}} +"
                )
                subprocess.run(["sudo", "-u", "coa", "bash", "-c", fix_cmd], check=False)
                agent_branch = proj_branch  # Same branch for local

            conn.execute(
                "INSERT INTO project_members (project_id, member_type, member_id, display_name, workspace_path, branch, roles) "
                "VALUES (?, 'agent', ?, ?, ?, ?, ?)",
                (project_id, agent_name, agent_name.upper(), agent_project_path, agent_branch, roles)
            )
            conn.commit()
            conn.close()
            result_extra = {}
            if proj_type == "git" and locals().get("repo_initialized"):
                result_extra["repo_initialized"] = True
            json_response(True, project_id=project_id, project=name, member_type="agent", member=agent_name,
                          workspace=agent_project_path, branch=agent_branch, roles=roles, **result_extra)

        else:
            # ── Connection assignment ──
            # Check if already assigned
            existing = conn.execute(
                "SELECT 1 FROM project_members WHERE project_id=? AND member_type='connection' AND member_id=?",
                (project_id, connection_uid)
            ).fetchone()
            if existing:
                conn.close()
                json_response(False, error=f"Connection '{connection_uid}' is already assigned to project '{name}'")
                sys.exit(1)
            # Resolve display name
            contact = conn.execute("SELECT display_name FROM connections WHERE uid=?", (connection_uid,)).fetchone()
            display = contact["display_name"] if contact else connection_uid[:8]
            conn_branch = branch  # Optional — may be None
            conn.execute(
                "INSERT INTO project_members (project_id, member_type, member_id, display_name, branch, roles) "
                "VALUES (?, 'connection', ?, ?, ?, ?)",
                (project_id, connection_uid, display, conn_branch, roles)
            )
            conn.commit()
            conn.close()
            json_response(True, project_id=project_id, project=name, member_type="connection", member=display,
                          uid=connection_uid, branch=conn_branch, roles=roles)

    except subprocess.TimeoutExpired:
        json_response(False, error="Git operation timed out")
        sys.exit(1)
    except Exception as e:
        json_response(False, error=str(e))
        sys.exit(1)


@project.command("unassign")
@click.argument("project_id", type=int)
@click.option("--agent", "agent_name", default=None, help="Agent name to unassign")
@click.option("--connection", "connection_uid", default=None, help="Connection UID to unassign")
def project_unassign(project_id, agent_name, connection_uid):
    """Remove an agent or connection from a project. Agents: freezes tasks, cleans workspace."""
    if not agent_name and not connection_uid:
        json_response(False, error="Must specify --agent or --connection")
        sys.exit(1)
    try:
        conn = sqlite3.connect(tasks_db, timeout=5)
        conn.row_factory = sqlite3.Row
        proj = _get_project(conn, project_id)
        name = proj["name"]

        if agent_name:
            member = conn.execute(
                "SELECT workspace_path, roles FROM project_members WHERE project_id=? AND member_type='agent' AND member_id=?",
                (project_id, agent_name)
            ).fetchone()
            if not member:
                conn.close()
                json_response(False, error=f"Agent '{agent_name}' is not assigned to project '{name}'")
                sys.exit(1)
            if "owner" in (member["roles"] or ""):
                conn.close()
                json_response(False, error=f"Cannot unassign project owner '{agent_name}'. Transfer ownership first.")
                sys.exit(1)
            # Freeze tasks assigned to this agent for this project
            cursor = conn.execute(
                """UPDATE tasks
                   SET pre_freeze_status = status, status = 'frozen', updated_at = datetime('now')
                   WHERE project_id = ? AND assigned_to = ?
                     AND status NOT IN ('done', 'cancelled', 'frozen')""",
                (project_id, agent_name)
            )
            frozen_count = cursor.rowcount
            # Unassign frozen tasks
            conn.execute(
                """UPDATE tasks SET assigned_to = NULL, updated_at = datetime('now')
                   WHERE project_id = ? AND assigned_to = ? AND status = 'frozen'""",
                (project_id, agent_name)
            )
            # Cleanup workspace
            ws = member["workspace_path"]
            if ws and os.path.islink(ws):
                os.unlink(ws)
            elif ws and os.path.isdir(ws):
                shutil.rmtree(ws, ignore_errors=True)
            # Remove membership
            conn.execute(
                "DELETE FROM project_members WHERE project_id=? AND member_type='agent' AND member_id=?",
                (project_id, agent_name)
            )
            # Also remove project memory for this agent
            conn.execute(
                "DELETE FROM agent_memory_project WHERE project_id=? AND agent_name=?",
                (project_id, agent_name)
            )
            conn.commit()
            conn.close()
            json_response(True, project_id=project_id, project=name, unassigned="agent", member=agent_name,
                          tasks_frozen=frozen_count, workspace_cleaned=bool(ws))
        else:
            member = conn.execute(
                "SELECT 1 FROM project_members WHERE project_id=? AND member_type='connection' AND member_id=?",
                (project_id, connection_uid)
            ).fetchone()
            if not member:
                conn.close()
                json_response(False, error=f"Connection '{connection_uid}' is not assigned to project '{name}'")
                sys.exit(1)
            conn.execute(
                "DELETE FROM project_members WHERE project_id=? AND member_type='connection' AND member_id=?",
                (project_id, connection_uid)
            )
            conn.commit()
            conn.close()
            json_response(True, project_id=project_id, project=name, unassigned="connection", uid=connection_uid)

    except Exception as e:
        json_response(False, error=str(e))
        sys.exit(1)


@project.command("members")
@click.argument("project_id", type=int)
def project_members(project_id):
    """List all members (agents + connections) assigned to a project."""
    try:
        conn = sqlite3.connect(tasks_db, timeout=5)
        conn.row_factory = sqlite3.Row
        proj = _get_project(conn, project_id)
        name = proj["name"]
        members = conn.execute(
            "SELECT member_type, member_id, display_name, workspace_path, branch, roles, assigned_at "
            "FROM project_members WHERE project_id=? ORDER BY roles DESC, assigned_at ASC",
            (project_id,)
        ).fetchall()
        conn.close()
        result = []
        for m in members:
            result.append({
                "type": m["member_type"],
                "id": m["member_id"],
                "name": m["display_name"],
                "workspace": m["workspace_path"],
                "branch": m["branch"],
                "roles": m["roles"],
                "assigned": m["assigned_at"]
            })
        print(json.dumps({"ok": True, "project_id": project_id, "project": name, "members": result}, indent=2, default=str))
    except Exception as e:
        json_response(False, error=str(e))
        sys.exit(1)


@project.command("git-setup")
def project_git_setup():
    """Manual fallback: configure git identity + ensure SSH keypair exists. Keys are auto-generated at provisioning."""
    config = get_config()
    identity = config.get("identity", {})
    # Resolve OS user from agent's DB record, NOT from $HOME
    caller = get_agent_name()
    try:
        db_conn = sqlite3.connect(agents_db, timeout=5)
        db_conn.row_factory = sqlite3.Row
        agent_row = db_conn.execute("SELECT os_user, workspace FROM agents WHERE name=?", (caller,)).fetchone()
        db_conn.close()
    except Exception:
        agent_row = None
    os_user = agent_row["os_user"] if agent_row else os.getenv("USER", "coa")
    home = os.path.expanduser(f"~{os_user}")
    workspace = agent_row["workspace"] if agent_row else home
    # Git config
    agent_name = identity.get("first_name", "Versa")
    git_name = f"{agent_name} (AGi Agent)"
    git_email = "versa-agi@local"
    try:
        subprocess.run(["git", "config", "--global", "user.name", git_name],
                       capture_output=True, text=True, timeout=10)
        subprocess.run(["git", "config", "--global", "user.email", git_email],
                       capture_output=True, text=True, timeout=10)
        # SSH keypair generation (idempotent)
        ssh_dir = os.path.join(home, ".ssh")
        key_path = os.path.join(ssh_dir, "versa_agi_ed25519")
        pub_key_path = key_path + ".pub"
        key_generated = False
        if not os.path.exists(key_path):
            os.makedirs(ssh_dir, exist_ok=True)
            os.chmod(ssh_dir, 0o700)
            subprocess.run(
                ["ssh-keygen", "-t", "ed25519", "-f", key_path, "-N", "", "-C", f"{os_user}@versa-agi"],
                capture_output=True, text=True, timeout=30
            )
            os.chmod(key_path, 0o600)
            os.chmod(pub_key_path, 0o644)
            key_generated = True
        # Ensure SSH config entries for GitHub + GitLab
        ssh_config = os.path.join(ssh_dir, "config")
        if not os.path.exists(ssh_config) or "versa_agi_ed25519" not in open(ssh_config).read():
            config_entries = (
                f"\nHost github.com\n  IdentityFile {key_path}\n  IdentitiesOnly yes\n  StrictHostKeyChecking accept-new\n"
                f"\nHost gitlab.com\n  IdentityFile {key_path}\n  IdentitiesOnly yes\n  StrictHostKeyChecking accept-new\n"
            )
            with open(ssh_config, "a") as f:
                f.write(config_entries)
            os.chmod(ssh_config, 0o644)
        # Read public key for JSON output
        pub_key = ""
        if os.path.exists(pub_key_path):
            with open(pub_key_path) as f:
                pub_key = f.read().strip()
        json_response(True,
                      git_name=git_name, git_email=git_email,
                      ssh_key=key_path, key_generated=key_generated,
                      public_key=pub_key,
                      note="SSH key path is injected into system prompt. Share public key with PU for Git platform registration.")
    except Exception as e:
        json_response(False, error=str(e))
        sys.exit(1)

# ═══════════════════════════════════════════════════════
# 6b. GAME — Strategic pursuit management
# ═══════════════════════════════════════════════════════

@cli.group()
def game():
    """Strategic pursuit management."""
    pass

@game.command("add")
@click.argument("name")
@click.option("--postulate", default=None, help="The intended reality (vision statement)")
@click.option("--posture", type=click.Choice(['exploratory','steady','aggressive','defensive']), default='exploratory')
@click.option("--autonomy", type=click.Choice(['advisory','collaborative','autonomous']), default='collaborative')
def game_add(name, postulate, posture, autonomy):
    """Register a new strategic game."""
    try:
        conn = sqlite3.connect(tasks_db, timeout=5)
        existing = conn.execute("SELECT id FROM games WHERE name=?", (name,)).fetchone()
        if existing:
            conn.close()
            json_response(False, error=f"Game '{name}' already exists (id={existing[0]})")
            sys.exit(1)
        conn.execute(
            "INSERT INTO games (name, postulate, posture, autonomy) VALUES (?, ?, ?, ?)",
            (name, postulate, posture, autonomy)
        )
        conn.commit()
        game_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.close()
        json_response(True, action="game_add", game_id=game_id, name=name, posture=posture, autonomy=autonomy)
    except Exception as e:
        json_response(False, error=str(e))
        sys.exit(1)

@game.command("update")
@click.argument("game_id", type=int)
@click.option("--name", default=None, help="Rename the game")
@click.option("--postulate", default=None, help="Update the postulate")
@click.option("--posture", type=click.Choice(['exploratory','steady','aggressive','defensive']), default=None)
@click.option("--autonomy", type=click.Choice(['advisory','collaborative','autonomous']), default=None)
@click.option("--freedoms", default=None, help="Freedoms summary text")
@click.option("--barriers", default=None, help="Barriers summary text")
@click.option("--milestones", default=None, help="JSON milestones")
@click.option("--status", type=click.Choice(['active','paused','archived']), default=None)
def game_update(game_id, name, postulate, posture, autonomy, freedoms, barriers, milestones, status):
    """Update a game's strategic state."""
    try:
        conn = sqlite3.connect(tasks_db, timeout=5)
        existing = conn.execute("SELECT id FROM games WHERE id=?", (game_id,)).fetchone()
        if not existing:
            conn.close()
            json_response(False, error=f"Game id={game_id} not found")
            sys.exit(1)
        updates = []
        params = []
        field_map = {
            "name": name, "postulate": postulate, "posture": posture,
            "autonomy": autonomy, "freedoms_summary": freedoms,
            "barriers_summary": barriers, "milestones": milestones, "status": status
        }
        for col, val in field_map.items():
            if val is not None:
                updates.append(f"{col}=?")
                params.append(val)
        if not updates:
            conn.close()
            json_response(False, error="No fields to update")
            sys.exit(1)
        # If posture or freedoms/barriers changed, update environment_assessed_at
        if posture or freedoms or barriers:
            updates.append("environment_assessed_at=datetime('now')")
        updates.append("updated_at=datetime('now')")
        params.append(game_id)
        conn.execute(f"UPDATE games SET {', '.join(updates)} WHERE id=?", params)
        conn.commit()
        conn.close()
        json_response(True, action="game_update", game_id=game_id, updated_fields=list(field_map.keys()))
    except Exception as e:
        json_response(False, error=str(e))
        sys.exit(1)

@game.command("show")
@click.argument("game_id", type=int)
def game_show(game_id):
    """Show full details of a game including related projects and awareness."""
    try:
        conn = sqlite3.connect(tasks_db, timeout=5)
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM games WHERE id=?", (game_id,)).fetchone()
        if not row:
            conn.close()
            json_response(False, error=f"Game id={game_id} not found")
            sys.exit(1)
        result = dict(row)
        # Related projects
        projects = conn.execute("SELECT id, name, status FROM projects WHERE game_id=?", (game_id,)).fetchall()
        result["projects"] = [dict(p) for p in projects]
        # Active awareness entries for this game
        awareness = conn.execute(
            "SELECT id, agent_name, type, content, status FROM agent_awareness WHERE subject_type='game' AND subject_id=? AND status='active'",
            (str(game_id),)
        ).fetchall()
        result["active_awareness"] = [dict(a) for a in awareness]
        conn.close()
        print(json.dumps(result, indent=2, default=str))
    except Exception as e:
        json_response(False, error=str(e))

@game.command("list")
@click.option("--status", "game_status", default=None, help="Filter by status (default: all)")
def game_list(game_status):
    """List all games."""
    try:
        conn = sqlite3.connect(tasks_db, timeout=5)
        conn.row_factory = sqlite3.Row
        if game_status:
            rows = conn.execute("SELECT * FROM games WHERE status=? ORDER BY name", (game_status,)).fetchall()
        else:
            rows = conn.execute("SELECT * FROM games ORDER BY name").fetchall()
        conn.close()
        print(json.dumps([dict(r) for r in rows], indent=2, default=str))
    except Exception as e:
        json_response(False, error=str(e))

@game.command("assign-project")
@click.argument("game_id", type=int)
@click.argument("project_id", type=int)
def game_assign_project(game_id, project_id):
    """Assign a project to a game (sets projects.game_id)."""
    try:
        conn = sqlite3.connect(tasks_db, timeout=5)
        g = conn.execute("SELECT id FROM games WHERE id=?", (game_id,)).fetchone()
        if not g:
            conn.close()
            json_response(False, error=f"Game id={game_id} not found")
            sys.exit(1)
        p = conn.execute("SELECT id, name FROM projects WHERE id=?", (project_id,)).fetchone()
        if not p:
            conn.close()
            json_response(False, error=f"Project id={project_id} not found")
            sys.exit(1)
        conn.execute("UPDATE projects SET game_id=?, updated_at=datetime('now') WHERE id=?", (game_id, project_id))
        conn.commit()
        conn.close()
        json_response(True, action="game_assign_project", game_id=game_id, project_id=project_id)
    except Exception as e:
        json_response(False, error=str(e))
        sys.exit(1)

# ── Game Opponents (Competitive Intelligence) ──

@game.group("opponent")
def game_opponent():
    """Manage competitive intelligence for projects."""
    pass

@game_opponent.command("add")
@click.argument("project_id", type=int)
@click.argument("name")
@click.option("--type", "opp_type", type=click.Choice(['person','agent','business','association']), default='business')
@click.option("--desc", default=None, help="Description of the opponent")
@click.option("--sources", default=None, help="JSON: URLs, social handles, API endpoints")
def opponent_add(project_id, name, opp_type, desc, sources):
    """Add a competitor/opponent to a project."""
    try:
        conn = sqlite3.connect(tasks_db, timeout=5)
        conn.execute(
            "INSERT INTO project_opponents (project_id, name, type, description, intelligence_sources) VALUES (?, ?, ?, ?, ?)",
            (project_id, name, opp_type, desc, sources)
        )
        conn.commit()
        opp_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.close()
        json_response(True, action="opponent_add", id=opp_id, project_id=project_id, name=name)
    except Exception as e:
        json_response(False, error=str(e))
        sys.exit(1)

@game_opponent.command("list")
@click.option("--project", "project_id", type=int, default=None, help="Filter by project")
def opponent_list(project_id):
    """List opponents (optionally filtered by project)."""
    try:
        conn = sqlite3.connect(tasks_db, timeout=5)
        conn.row_factory = sqlite3.Row
        if project_id:
            rows = conn.execute("SELECT * FROM project_opponents WHERE project_id=? ORDER BY name", (project_id,)).fetchall()
        else:
            rows = conn.execute("SELECT * FROM project_opponents ORDER BY project_id, name").fetchall()
        conn.close()
        print(json.dumps([dict(r) for r in rows], indent=2, default=str))
    except Exception as e:
        json_response(False, error=str(e))

@game_opponent.command("update")
@click.argument("opponent_id", type=int)
@click.option("--name", default=None)
@click.option("--desc", default=None)
@click.option("--sources", default=None, help="JSON intelligence sources")
@click.option("--assessment", default=None, help="Latest competitive analysis")
def opponent_update(opponent_id, name, desc, sources, assessment):
    """Update an opponent record."""
    try:
        conn = sqlite3.connect(tasks_db, timeout=5)
        updates = []
        params = []
        for col, val in [("name", name), ("description", desc), ("intelligence_sources", sources), ("last_assessment", assessment)]:
            if val is not None:
                updates.append(f"{col}=?")
                params.append(val)
        if assessment:
            updates.append("last_assessed_at=datetime('now')")
        if not updates:
            conn.close()
            json_response(False, error="No fields to update")
            sys.exit(1)
        params.append(opponent_id)
        conn.execute(f"UPDATE project_opponents SET {', '.join(updates)} WHERE id=?", params)
        conn.commit()
        conn.close()
        json_response(True, action="opponent_update", id=opponent_id)
    except Exception as e:
        json_response(False, error=str(e))
        sys.exit(1)

@game_opponent.command("delete")
@click.argument("opponent_id", type=int)
def opponent_delete(opponent_id):
    """Remove an opponent record."""
    try:
        conn = sqlite3.connect(tasks_db, timeout=5)
        existing = conn.execute("SELECT id, name FROM project_opponents WHERE id=?", (opponent_id,)).fetchone()
        if not existing:
            conn.close()
            json_response(False, error=f"Opponent id={opponent_id} not found")
            sys.exit(1)
        conn.execute("DELETE FROM project_opponents WHERE id=?", (opponent_id,))
        conn.commit()
        conn.close()
        json_response(True, action="opponent_delete", id=opponent_id, name=existing[1])
    except Exception as e:
        json_response(False, error=str(e))
        sys.exit(1)

# ═══════════════════════════════════════════════════════
# 6c. AWARENESS — Agent cognitive state (Conclusions + Actions)
# ═══════════════════════════════════════════════════════

@cli.group()
def awareness():
    """Agent awareness management (conclusions + actions)."""
    pass

@awareness.command("add")
@click.argument("entry_type", type=click.Choice(['conclusion', 'action']))
@click.option("--subject", "subject_type", required=True, type=click.Choice(['connection','project','game','system','self']))
@click.option("--subject-id", default=None, help="FK reference (uid, id, or omit for system/self)")
@click.option("--content", required=True, help="The conclusion or action statement")
@click.option("--action-conclusion-id", type=int, default=None, help="FK to parent conclusion (actions only)")
@click.option("--context", default=None, help="What prompted this awareness entry")
@click.option("--agent", "agent_name", default=None, help="Agent name (defaults to current)")
def awareness_add(entry_type, subject_type, subject_id, content, action_conclusion_id, context, agent_name):
    """Add a conclusion or action to the awareness store."""
    if not agent_name:
        agent_name = get_agent_name()
    # Validate: actions should have a conclusion_id
    if entry_type == 'action' and action_conclusion_id is None:
        console.print("[yellow]⚠ Warning: Action added without --action-conclusion-id. Consider linking to a parent conclusion.[/yellow]", stderr=True)
    # Validate: conclusions should NOT have a conclusion_id
    if entry_type == 'conclusion' and action_conclusion_id is not None:
        json_response(False, error="--action-conclusion-id is only valid for actions, not conclusions")
        sys.exit(1)
    try:
        conn = sqlite3.connect(tasks_db, timeout=5)
        # If action, validate conclusion exists
        if action_conclusion_id is not None:
            parent = conn.execute("SELECT id, type FROM agent_awareness WHERE id=?", (action_conclusion_id,)).fetchone()
            if not parent:
                conn.close()
                json_response(False, error=f"Parent conclusion id={action_conclusion_id} not found")
                sys.exit(1)
            if parent[1] != 'conclusion':
                conn.close()
                json_response(False, error=f"id={action_conclusion_id} is not a conclusion (type={parent[1]})")
                sys.exit(1)
        conn.execute(
            "INSERT INTO agent_awareness (agent_name, type, subject_type, subject_id, content, action_conclusion_id, context) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (agent_name, entry_type, subject_type, subject_id, content, action_conclusion_id, context)
        )
        conn.commit()
        entry_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.close()
        # Update awareness timestamp for enforcement gate
        _update_awareness_timestamp(agent_name)
        json_response(True, action="awareness_add", id=entry_id, type=entry_type, subject_type=subject_type, agent=agent_name)
    except Exception as e:
        json_response(False, error=str(e))
        sys.exit(1)

@awareness.command("revise")
@click.argument("entry_id", type=int)
@click.option("--content", required=True, help="Updated conclusion/action content")
@click.option("--agent", "agent_name", default=None, help="Agent name (defaults to current)")
def awareness_revise(entry_id, content, agent_name):
    """Revise an awareness entry. Old entry → superseded, new entry created."""
    if not agent_name:
        agent_name = get_agent_name()
    try:
        conn = sqlite3.connect(tasks_db, timeout=5)
        conn.row_factory = sqlite3.Row
        old = conn.execute("SELECT * FROM agent_awareness WHERE id=?", (entry_id,)).fetchone()
        if not old:
            conn.close()
            json_response(False, error=f"Awareness entry id={entry_id} not found")
            sys.exit(1)
        old = dict(old)
        # Ownership guard: only the owning agent (or a protected agent) can revise
        if old['agent_name'] != agent_name:
            is_protected = False
            try:
                aconn = sqlite3.connect(agents_db, timeout=5)
                prow = aconn.execute("SELECT protected FROM agents WHERE name=?", (agent_name,)).fetchone()
                aconn.close()
                is_protected = prow and prow[0] == 1
            except Exception:
                pass
            if not is_protected:
                conn.close()
                json_response(False, error=f"Ownership denied: entry id={entry_id} belongs to '{old['agent_name']}', not '{agent_name}'. Only the owning agent or a protected agent can revise entries.")
                sys.exit(1)
        # Mark old as superseded
        conn.execute("UPDATE agent_awareness SET status='superseded', updated_at=datetime('now') WHERE id=?", (entry_id,))
        # Create new entry with same metadata
        conn.execute(
            "INSERT INTO agent_awareness (agent_name, type, subject_type, subject_id, content, action_conclusion_id, context, status) VALUES (?, ?, ?, ?, ?, ?, ?, 'active')",
            (old['agent_name'], old['type'], old['subject_type'], old['subject_id'], content, old['action_conclusion_id'],
             f"Revised from id={entry_id}")
        )
        conn.commit()
        new_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.close()
        _update_awareness_timestamp(agent_name)
        json_response(True, action="awareness_revise", old_id=entry_id, new_id=new_id, agent=agent_name)
    except Exception as e:
        json_response(False, error=str(e))
        sys.exit(1)

@awareness.command("complete")
@click.argument("entry_id", type=int)
@click.option("--agent", "agent_name", default=None, help="Agent name (defaults to current)")
def awareness_complete(entry_id, agent_name):
    """Mark an action as completed."""
    if not agent_name:
        agent_name = get_agent_name()
    try:
        conn = sqlite3.connect(tasks_db, timeout=5)
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT type, agent_name FROM agent_awareness WHERE id=?", (entry_id,)).fetchone()
        if not row:
            conn.close()
            json_response(False, error=f"Awareness entry id={entry_id} not found")
            sys.exit(1)
        # Ownership guard: only the owning agent (or a protected agent) can complete
        if row['agent_name'] != agent_name:
            is_protected = False
            try:
                aconn = sqlite3.connect(agents_db, timeout=5)
                prow = aconn.execute("SELECT protected FROM agents WHERE name=?", (agent_name,)).fetchone()
                aconn.close()
                is_protected = prow and prow[0] == 1
            except Exception:
                pass
            if not is_protected:
                conn.close()
                json_response(False, error=f"Ownership denied: entry id={entry_id} belongs to '{row['agent_name']}', not '{agent_name}'. Only the owning agent or a protected agent can complete entries.")
                sys.exit(1)
        conn.execute("UPDATE agent_awareness SET status='completed', updated_at=datetime('now') WHERE id=?", (entry_id,))
        conn.commit()
        conn.close()
        json_response(True, action="awareness_complete", id=entry_id, type=row['type'])
    except Exception as e:
        json_response(False, error=str(e))
        sys.exit(1)

@awareness.command("list")
@click.option("--type", "entry_type", type=click.Choice(['conclusion', 'action']), default=None, help="Filter by type")
@click.option("--subject", "subject_type", type=click.Choice(['connection','project','game','system','self']), default=None)
@click.option("--subject-id", default=None)
@click.option("--status", "entry_status", default=None, help="Filter by status (default: all)")
@click.option("--agent", "agent_name", default=None, help="Agent name (defaults to current)")
def awareness_list(entry_type, subject_type, subject_id, entry_status, agent_name):
    """List awareness entries. No --status = all statuses."""
    if not agent_name:
        agent_name = get_agent_name()
    try:
        conn = sqlite3.connect(tasks_db, timeout=5)
        conn.row_factory = sqlite3.Row
        query = "SELECT * FROM agent_awareness WHERE agent_name=?"
        params = [agent_name]
        if entry_type:
            query += " AND type=?"
            params.append(entry_type)
        if subject_type:
            query += " AND subject_type=?"
            params.append(subject_type)
        if subject_id:
            query += " AND subject_id=?"
            params.append(subject_id)
        if entry_status:
            query += " AND status=?"
            params.append(entry_status)
        query += " ORDER BY created_at DESC"
        rows = conn.execute(query, params).fetchall()
        conn.close()
        print(json.dumps([dict(r) for r in rows], indent=2, default=str))
    except Exception as e:
        json_response(False, error=str(e))

@awareness.command("table")
@click.option("--type", "entry_type", type=click.Choice(['conclusion', 'action']), default=None, help="Filter by type")
@click.option("--subject", "subject_type", type=click.Choice(['connection','project','game','system','self']), default=None)
@click.option("--status", "entry_status", default=None, help="Filter by status (default: all)")
@click.option("--agent", "agent_name", default=None, help="Filter by agent (default: all)")
@click.option("--limit", type=int, default=15, help="Limit the number of entries returned")
def awareness_table(entry_type, subject_type, entry_status, agent_name, limit):
    """Output awareness entries in a token-efficient markdown table."""
    try:
        conn = sqlite3.connect(tasks_db, timeout=5)
        conn.row_factory = sqlite3.Row
        query = "SELECT * FROM agent_awareness WHERE 1=1"
        params = []
        if entry_type:
            query += " AND type=?"
            params.append(entry_type)
        if subject_type:
            query += " AND subject_type=?"
            params.append(subject_type)
        if entry_status:
            query += " AND status=?"
            params.append(entry_status)
        if agent_name:
            query += " AND agent_name=?"
            params.append(agent_name)
        query += " ORDER BY updated_at DESC LIMIT ?"
        params.append(limit)
        
        rows = conn.execute(query, params).fetchall()
        conn.close()
        
        if not rows:
            print("No awareness entries found.")
            return

        table_header = "| ID | Type | Subject | Content |"
        if not agent_name:
            table_header = "| ID | Agent | Type | Subject | Content |"
            
        print(table_header)
        print("|" + "|".join(["---"] * (5 if not agent_name else 4)) + "|")
        
        for r in reversed(rows): # Reverse to show chronological order when limited
            content = str(r["content"]).replace("\n", " ").replace("|", "\\|")
            subj = str(r["subject_type"])
            if r["subject_id"]:
                subj += f"({r['subject_id']})"
            
            if not agent_name:
                print(f"| {r['id']} | {r['agent_name']} | {r['type']} | {subj} | {content} |")
            else:
                print(f"| {r['id']} | {r['type']} | {subj} | {content} |")
                
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)

@awareness.command("get")
@click.argument("entry_id", type=int)
def awareness_get(entry_id):
    """Get a single awareness entry by ID."""
    try:
        conn = sqlite3.connect(tasks_db, timeout=5)
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM agent_awareness WHERE id=?", (entry_id,)).fetchone()
        conn.close()
        if not row:
            json_response(False, error=f"Awareness entry id={entry_id} not found")
            sys.exit(1)
        print(json.dumps(dict(row), indent=2, default=str))
    except Exception as e:
        json_response(False, error=str(e))


def _update_awareness_timestamp(agent_name):
    """Update last_awareness_ts on the agent's current cycle for enforcement gate."""
    try:
        cdb = sqlite3.connect(cycles_db, timeout=5)
        cdb.execute(
            "UPDATE cycles SET last_awareness_ts=datetime('now') WHERE id LIKE ? AND ended_at IS NULL",
            (f"{agent_name}-%",)
        )
        cdb.commit()
        cdb.close()
    except Exception:
        pass  # Non-fatal — enforcement gate is advisory


# ═══════════════════════════════════════════════════════
# 7. CONNECTION — Social graph management
# ═══════════════════════════════════════════════════════

@cli.group()
def connection():
    """VersaVoice connection management."""
    pass

@connection.group("list", invoke_without_command=True)
@click.pass_context
def connection_list(ctx):
    """List connections. Defaults to primary-user contacts."""
    if ctx.invoked_subcommand is None:
        # Default: list primary-user contacts
        ctx.invoke(connection_list_primary_user)

@connection_list.command("primary-user")
def connection_list_primary_user():
    """List the Primary User's contacts (people the agent can connect to)."""
    config = get_config()
    token = config.get("versavoice", {}).get("api_token")
    sub_account_id = config.get("versavoice", {}).get("sub_account_id")
    if not token:
        json_response(False, error="VersaVoice API token not configured")
        sys.exit(1)
    from comms import api_request
    response = api_request("/connections", token)
    if response is None:
        json_response(False, error="Failed to fetch Primary User contacts")
        sys.exit(1)
    # Response is a list of contacts
    contacts = response if isinstance(response, list) else response.get("connections", response.get("contacts", []))
    # Format for agent readability
    result = []
    for c in contacts:
        result.append({
            "uid": c.get("uid") or c.get("contactUid") or c.get("id"),
            "name": c.get("displayName") or c.get("name") or "Unknown",
            "language": c.get("spokenLanguage") or c.get("language") or "--",
            "country": c.get("countryOfBirth"),
            "city": c.get("nearestCity"),
            "chromosome": c.get("chromosome"),
            "dateOfBirth": c.get("dateOfBirth"),
            "abilities": c.get("abilities", []),
            "isApiEnabled": c.get("isApiEnabled", False),
        })
    print(json.dumps(result, indent=2, default=str))

@connection_list.command("agent")
def connection_list_agent():
    """List the agent's own established connections (local DB filtered by agent memory)."""
    caller = get_agent_name()
    try:
        conn = sqlite3.connect(tasks_db, timeout=5)
        conn.row_factory = sqlite3.Row
        query = """
            SELECT c.*, amc.preferences, amc.personal_notes, amc.rapport_level 
            FROM connections c
            INNER JOIN agent_memory_connection amc ON c.uid = amc.contact_uid
            WHERE amc.agent_name = ?
            ORDER BY c.display_name
        """
        rows = conn.execute(query, (caller,)).fetchall()
        conn.close()
        print(json.dumps([dict(r) for r in rows], indent=2, default=str))
    except Exception as e:
        json_response(False, error=str(e))

@connection.command("request")
@click.argument("uid")
def connection_request(uid):
    """Send a VersaVoice connection invitation to a Primary User contact.

    Only agents with can_message_connections=1 (or protected) can send invitations.
    Sub-agents need external comms enabled via 'agictl agent toggle-comms' (dashboard).
    """
    # Guard: check can_message_connections gate
    caller = get_agent_name()
    try:
        guard_conn = sqlite3.connect(agents_db, timeout=5)
        guard_conn.row_factory = sqlite3.Row
        row = guard_conn.execute(
            "SELECT protected, can_message_connections FROM agents WHERE name=?", (caller,)
        ).fetchone()
        guard_conn.close()
        if row and row["protected"] == 0 and row["can_message_connections"] == 0:
            json_response(False, error=f"Agent '{caller}' cannot send connection invitations. "
                          "External comms not enabled. Do not retry — report this to the COA for activation.")
            sys.exit(1)
    except Exception:
        pass
    config = get_config()
    token = config.get("versavoice", {}).get("api_token")
    sub_account_id = config.get("versavoice", {}).get("sub_account_id")
    if not token or not sub_account_id:
        json_response(False, error="VersaVoice identity not configured")
        sys.exit(1)
    from comms import api_request
    # Delegate request fully to the cloud backend (idempotent REST response)
    try:
        conn = sqlite3.connect(tasks_db, timeout=5)
        conn.row_factory = sqlite3.Row

        # Pre-fetch display name from primary-user contacts list
        prefetched_name = "Unknown"
        try:
            contacts_response = api_request("/connections", token)
            contacts = contacts_response if isinstance(contacts_response, list) else (contacts_response or {}).get("connections", (contacts_response or {}).get("contacts", []))
            for c in contacts:
                c_uid = c.get("uid") or c.get("contactUid") or c.get("id")
                if c_uid == uid:
                    prefetched_name = c.get("displayName") or c.get("name") or "Unknown"
                    break
        except Exception:
            pass

        # Send invitation via VersaVoice API
        response = api_request(
            f"/connections/link",
            token,
            method="POST",
            body={"subAccountId": sub_account_id, "contactUid": uid}
        )
        if response and response.get("success"):
            # Insert into local connections table — prefer API response name, fall back to pre-fetched
            display_name = response.get("data", {}).get("displayName") or prefetched_name
            spoken_lang = response.get("data", {}).get("spokenLanguage", "")
            conn.execute(
                "INSERT OR IGNORE INTO connections (uid, display_name, spoken_lang) VALUES (?, ?, ?)",
                (uid, display_name, spoken_lang)
            )
            # If already exists with 'Unknown', update with better name
            if display_name != "Unknown":
                conn.execute(
                    "UPDATE connections SET display_name=? WHERE uid=? AND display_name='Unknown'",
                    (display_name, uid)
                )
            conn.commit()
            conn.close()
            json_response(True, uid=uid, display_name=display_name, status="invitation_sent")
        else:
            conn.close()
            err = response.get("message", str(response)) if response else "API Error"
            json_response(False, error=f"Connection request failed: {err}")
            sys.exit(1)
    except Exception as e:
        json_response(False, error=str(e))
        sys.exit(1)


# ═══════════════════════════════════════════════════════
# IDENTITY — VersaVoice sub-account provisioning
# ═══════════════════════════════════════════════════════

@cli.group()
def identity():
    """VersaVoice Identity Management."""
    pass

@identity.command()
@click.argument("agent_user")
@click.option("--token", required=True, help="VersaVoice API token")
@click.option("--first-name", required=True)
@click.option("--last-name", required=True)
@click.option("--language", default="en")
@click.option("--country", default="")
@click.option("--voice", type=click.Choice(["female", "male", "reflective"], case_sensitive=False), default="female",
              help="Voice type: female (Y), male (X), reflective (Primary User's voice)")
def provision(agent_user, token, first_name, last_name, language, country, voice):
    """Resolve or create a VersaVoice Sub-Account.

    Only protected agents (COA, watchdog) can register VersaVoice identities.
    Sub-agents communicate via 'agictl message internal' — no VV account needed.
    """
    # Guard: sub-agents cannot register VV identities
    caller = get_agent_name()
    try:
        conn = sqlite3.connect(agents_db, timeout=5)
        row = conn.execute("SELECT protected FROM agents WHERE name=?", (caller,)).fetchone()
        conn.close()
        if row and row[0] == 0:
            json_response(False, error=f"Sub-agent '{caller}' cannot register a VersaVoice account. "
                          "Use 'agictl message internal' for communication.")
            sys.exit(1)
    except Exception:
        pass  # Allow if DB check fails (fresh install edge case)
    success = provision_identity(
        agent_user, token, first_name, last_name, language, country, voice, agents_db
    )
    if not success:
        sys.exit(1)


# ═══════════════════════════════════════════════════════
# 8. MEMORY — Agent Memory Bridging (TD-MEM-003)
# ═══════════════════════════════════════════════════════

@cli.group()
def memory():
    """Agent memory — connection, project, and system memory management."""
    pass

# ── memory connection ─────────────────────────────────

@memory.group("connection")
def memory_connection():
    """Per-contact relational memory."""
    pass

@memory_connection.command("get")
@click.argument("contact_uid")
def memory_connection_get(contact_uid):
    """Get memory for a specific contact."""
    try:
        agent_name = get_agent_name()
        conn = sqlite3.connect(tasks_db, timeout=5)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM agent_memory_connection WHERE agent_name=? AND contact_uid=?",
            (agent_name, contact_uid)
        ).fetchone()
        conn.close()
        if row:
            print(json.dumps(dict(row), indent=2, default=str))
        else:
            print(json.dumps({"agent_name": agent_name, "contact_uid": contact_uid, "exists": False}))
    except Exception as e:
        json_response(False, error=str(e))

@memory_connection.command("set")
@click.argument("contact_uid")
@click.option("--preferences", default=None, help="JSON: voice_vs_text, language, comm_style, timezone")
@click.option("--personal-notes", default=None, help="Hobbies, family, interests")
@click.option("--comm-style", default=None, help="How this person communicates")
@click.option("--rapport", default=None, type=click.Choice(["new", "building", "established", "strong"]))
@click.option("--emotional-notes", default=None, help="Trust, vibe, encouragement needs")
def memory_connection_set(contact_uid, preferences, personal_notes, comm_style, rapport, emotional_notes):
    """Set or update memory for a contact. Uses UPSERT — only provided fields are updated."""
    try:
        agent_name = get_agent_name()
        conn = sqlite3.connect(tasks_db, timeout=5)
        # Check if record exists
        existing = conn.execute(
            "SELECT id FROM agent_memory_connection WHERE agent_name=? AND contact_uid=?",
            (agent_name, contact_uid)
        ).fetchone()

        if existing:
            # Update only provided fields
            updates = []
            params = []
            if preferences is not None:
                updates.append("preferences=?"); params.append(preferences)
            if personal_notes is not None:
                updates.append("personal_notes=?"); params.append(personal_notes)
            if comm_style is not None:
                updates.append("communication_style=?"); params.append(comm_style)
            if rapport is not None:
                updates.append("rapport_level=?"); params.append(rapport)
            if emotional_notes is not None:
                updates.append("emotional_notes=?"); params.append(emotional_notes)
            if not updates:
                json_response(False, error="No fields provided to update")
                return
            updates.append("last_interaction=datetime('now')")
            updates.append("updated_at=datetime('now')")
            params.extend([agent_name, contact_uid])
            conn.execute(
                f"UPDATE agent_memory_connection SET {', '.join(updates)} WHERE agent_name=? AND contact_uid=?",
                params
            )
        else:
            # Insert new record
            conn.execute(
                """INSERT INTO agent_memory_connection
                   (agent_name, contact_uid, preferences, personal_notes, communication_style,
                    rapport_level, emotional_notes, last_interaction)
                   VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))""",
                (agent_name, contact_uid, preferences, personal_notes, comm_style, rapport, emotional_notes)
            )
        conn.commit()
        conn.close()
        json_response(True, agent_name=agent_name, contact_uid=contact_uid, action="upserted")
    except Exception as e:
        json_response(False, error=str(e))

@memory_connection.command("list")
def memory_connection_list():
    """List all contact memories for this agent."""
    try:
        agent_name = get_agent_name()
        conn = sqlite3.connect(tasks_db, timeout=5)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM agent_memory_connection WHERE agent_name=? ORDER BY updated_at DESC",
            (agent_name,)
        ).fetchall()
        conn.close()
        print(json.dumps([dict(r) for r in rows], indent=2, default=str))
    except Exception as e:
        json_response(False, error=str(e))

# ── memory project ────────────────────────────────────

@memory.group("project")
def memory_project():
    """Per-project state memory."""
    pass

@memory_project.command("get")
@click.argument("project_id", type=int)
def memory_project_get(project_id):
    """Get memory for a specific project."""
    try:
        agent_name = get_agent_name()
        conn = sqlite3.connect(tasks_db, timeout=5)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM agent_memory_project WHERE agent_name=? AND project_id=?",
            (agent_name, project_id)
        ).fetchone()
        conn.close()
        if row:
            print(json.dumps(dict(row), indent=2, default=str))
        else:
            print(json.dumps({"agent_name": agent_name, "project_id": project_id, "exists": False}))
    except Exception as e:
        json_response(False, error=str(e))

@memory_project.command("set")
@click.argument("project_id", type=int)
@click.option("--phase", default=None, help="Current project phase")
@click.option("--decisions", default=None, help="Key decisions and WHY")
@click.option("--blockers", default=None, help="Known impediments")
@click.option("--next-steps", default=None, help="What is planned next")
def memory_project_set(project_id, phase, decisions, blockers, next_steps):
    """Set or update memory for a project. Uses UPSERT — only provided fields are updated."""
    try:
        agent_name = get_agent_name()
        conn = sqlite3.connect(tasks_db, timeout=5)
        existing = conn.execute(
            "SELECT id FROM agent_memory_project WHERE agent_name=? AND project_id=?",
            (agent_name, project_id)
        ).fetchone()

        if existing:
            updates = []
            params = []
            if phase is not None:
                updates.append("current_phase=?"); params.append(phase)
            if decisions is not None:
                updates.append("key_decisions=?"); params.append(decisions)
            if blockers is not None:
                updates.append("blockers=?"); params.append(blockers)
            if next_steps is not None:
                updates.append("next_steps=?"); params.append(next_steps)
            if not updates:
                json_response(False, error="No fields provided to update")
                return
            updates.append("updated_at=datetime('now')")
            params.extend([agent_name, project_id])
            conn.execute(
                f"UPDATE agent_memory_project SET {', '.join(updates)} WHERE agent_name=? AND project_id=?",
                params
            )
        else:
            conn.execute(
                """INSERT INTO agent_memory_project
                   (agent_name, project_id, current_phase, key_decisions, blockers, next_steps)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (agent_name, project_id, phase, decisions, blockers, next_steps)
            )
        conn.commit()
        conn.close()
        json_response(True, agent_name=agent_name, project_id=project_id, action="upserted")
    except Exception as e:
        json_response(False, error=str(e))

@memory_project.command("list")
def memory_project_list():
    """List all project memories for this agent."""
    try:
        agent_name = get_agent_name()
        conn = sqlite3.connect(tasks_db, timeout=5)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM agent_memory_project WHERE agent_name=? ORDER BY updated_at DESC",
            (agent_name,)
        ).fetchall()
        conn.close()
        print(json.dumps([dict(r) for r in rows], indent=2, default=str))
    except Exception as e:
        json_response(False, error=str(e))

# ── memory system ─────────────────────────────────────

@memory.group("system")
def memory_system():
    """System-level operational memory (constraints, discoveries, instructions)."""
    pass

@memory_system.command("get")
@click.argument("key", required=False, default=None)
def memory_system_get(key):
    """Get system memory. If key is provided, returns single entry; otherwise returns all."""
    try:
        agent_name = get_agent_name()
        conn = sqlite3.connect(tasks_db, timeout=5)
        conn.row_factory = sqlite3.Row
        if key:
            row = conn.execute(
                "SELECT * FROM agent_memory_system WHERE key=?",
                (key,)
            ).fetchone()
            conn.close()
            if row:
                print(json.dumps(dict(row), indent=2, default=str))
            else:
                print(json.dumps({"key": key, "exists": False}))
        else:
            rows = conn.execute(
                "SELECT * FROM agent_memory_system ORDER BY key ASC"
            ).fetchall()
            conn.close()
            print(json.dumps([dict(r) for r in rows], indent=2, default=str))
    except Exception as e:
        json_response(False, error=str(e))

@memory_system.command("set")
@click.argument("key")
@click.argument("value")
def memory_system_set(key, value):
    """Set a system memory entry. Uses UPSERT — replaces value if key exists."""
    try:
        agent_name = get_agent_name()
        conn = sqlite3.connect(tasks_db, timeout=5)
        conn.execute(
            """INSERT INTO agent_memory_system (agent_name, key, value)
               VALUES (?, ?, ?)
               ON CONFLICT(key) DO UPDATE SET value=excluded.value, agent_name=excluded.agent_name, updated_at=datetime('now')""",
            (agent_name, key, value)
        )
        conn.commit()
        conn.close()
        json_response(True, agent_name=agent_name, key=key, action="upserted")
    except Exception as e:
        json_response(False, error=str(e))

@memory_system.command("list")
def memory_system_list():
    """List all system memory entries for this agent."""
    try:
        agent_name = get_agent_name()
        conn = sqlite3.connect(tasks_db, timeout=5)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM agent_memory_system ORDER BY key ASC"
        ).fetchall()
        conn.close()
        print(json.dumps([dict(r) for r in rows], indent=2, default=str))
    except Exception as e:
        json_response(False, error=str(e))

@memory_system.command("delete")
@click.argument("key")
def memory_system_delete(key):
    """Delete a system memory entry by key."""
    try:
        conn = sqlite3.connect(tasks_db, timeout=5)
        cursor = conn.execute(
            "DELETE FROM agent_memory_system WHERE key=?", (key,)
        )
        deleted = cursor.rowcount
        conn.commit()
        conn.close()
        if deleted:
            json_response(True, key=key, action="deleted")
        else:
            json_response(False, error=f"Key '{key}' not found")
    except Exception as e:
        json_response(False, error=str(e))

@memory_system.command("rename")
@click.argument("old_key")
@click.argument("new_key")
def memory_system_rename(old_key, new_key):
    """Rename a system memory key (preserves value and metadata)."""
    try:
        conn = sqlite3.connect(tasks_db, timeout=5)
        # Check old key exists
        existing = conn.execute(
            "SELECT id FROM agent_memory_system WHERE key=?", (old_key,)
        ).fetchone()
        if not existing:
            conn.close()
            json_response(False, error=f"Key '{old_key}' not found")
            return
        # Check new key doesn't conflict
        conflict = conn.execute(
            "SELECT id FROM agent_memory_system WHERE key=?", (new_key,)
        ).fetchone()
        if conflict:
            conn.close()
            json_response(False, error=f"Key '{new_key}' already exists")
            return
        conn.execute(
            "UPDATE agent_memory_system SET key=?, updated_at=datetime('now') WHERE key=?",
            (new_key, old_key)
        )
        conn.commit()
        conn.close()
        json_response(True, old_key=old_key, new_key=new_key, action="renamed")
    except Exception as e:
        json_response(False, error=str(e))


# ═══════════════════════════════════════════════════════
# 9. EXECUTE — Code execution
# ═══════════════════════════════════════════════════════

def _get_exec_cmd(interpreter: str, script_path: str) -> list:
    """Build execution command, dropping back to the agent user if available.

    agictl runs as watchdog (via sudo elevation), but code execution must
    happen as the original agent user to inherit their supplementary groups
    (e.g. docker) and enforce OS-level isolation. The wrapper passes
    AGICTL_AGENT_USER for this purpose.

    Note: Agent users have /usr/sbin/nologin as their shell, so we cannot
    use 'sudo -i' (login shell). Instead we use 'sudo -u' and explicitly
    invoke the interpreter, which works regardless of the user's shell.
    """
    agent_user = os.environ.get("AGICTL_AGENT_USER")
    caller = os.environ.get("USER", "")
    if agent_user and agent_user not in ("root", "watchdog", caller):
        # Drop back to agent user — sudo -u initializes supplementary groups
        return ["sudo", "-u", agent_user, interpreter, script_path]
    return [interpreter, script_path]


@cli.group()
def execute():
    """Execute code (bash/python) safely."""
    pass


def _execute_bash_script(script: str) -> None:
    """Run a bash script as the calling agent user."""
    import tempfile
    with tempfile.NamedTemporaryFile(mode="w", suffix=".sh", delete=False) as f:
        f.write(script)
        temp_name = f.name
    os.chmod(temp_name, 0o644)
    try:
        cmd = _get_exec_cmd("bash", temp_name)
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        output = result.stdout
        if result.stderr:
            output += "\nSTDERR:\n" + result.stderr
        json_response(result.returncode == 0, output=output, exit_code=result.returncode)
    except subprocess.TimeoutExpired:
        json_response(False, error="Execution timed out after 120 seconds")
    except Exception as e:
        json_response(False, error=str(e))
    finally:
        if os.path.exists(temp_name):
            os.remove(temp_name)


@cli.command("bash")
@click.argument("script", type=str)
def bash_alias(script):
    """Run bash (alias for `agictl execute bash`)."""
    _execute_bash_script(script)


@execute.command("bash")
@click.argument("script", type=str)
def execute_bash(script):
    """Execute a bash script as the calling agent user."""
    _execute_bash_script(script)

@execute.command("python")
@click.argument("script", type=str)
def execute_python(script):
    """Execute a python script as the calling agent user."""
    import tempfile
    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
        f.write(script)
        temp_name = f.name
    # Make readable by agent user (tempfile defaults to 600 owned by watchdog)
    os.chmod(temp_name, 0o644)
    try:
        cmd = _get_exec_cmd("python3", temp_name)
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        output = result.stdout
        if result.stderr:
            output += "\nSTDERR:\n" + result.stderr
        json_response(result.returncode == 0, output=output, exit_code=result.returncode)
    except subprocess.TimeoutExpired:
        json_response(False, error="Execution timed out after 120 seconds")
    except Exception as e:
        json_response(False, error=str(e))
    finally:
        if os.path.exists(temp_name):
            os.remove(temp_name)


# ═══════════════════════════════════════════════════════
# SEARCH — Web search via SearXNG
# ═══════════════════════════════════════════════════════

def _get_search_config():
    """Read search configuration from setup.ini."""
    import configparser
    config = configparser.ConfigParser()
    config.read("/etc/versa-agi/setup.ini")
    return {
        "enabled": config.get("search", "enabled", fallback="false").lower() == "true",
        "engine": config.get("search", "engine", fallback="searxng"),
        "searxng_url": config.get("search", "searxng_url", fallback="http://localhost:8888"),
    }


@cli.group()
def search():
    """Web search via SearXNG."""
    pass


@search.command("web")
@click.argument("query")
@click.option("--count", "-n", default=5, help="Number of results to return (default: 5)")
@click.option("--categories", "-c", default="general", help="Search categories (default: general)")
def search_web(query, count, categories):
    """Search the web using the local SearXNG instance.

    Returns top N results with title, URL, and snippet as JSON.
    Requires SearXNG to be installed and enabled in setup.ini [search].
    """
    import urllib.request
    import urllib.parse

    config = _get_search_config()
    if not config["enabled"]:
        json_response(False, error="Web search is disabled. Enable it in setup.ini [search] enabled=true and install SearXNG via setup_providers.sh")
        sys.exit(1)

    base_url = config["searxng_url"].rstrip("/")
    params = urllib.parse.urlencode({
        "q": query,
        "format": "json",
        "categories": categories,
    })
    url = f"{base_url}/search?{params}"

    try:
        req = urllib.request.Request(url, headers={"User-Agent": "agictl/1.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode())
    except urllib.error.URLError as e:
        json_response(False, error=f"SearXNG connection failed: {e}. Is SearXNG running at {base_url}?")
        sys.exit(1)
    except Exception as e:
        json_response(False, error=f"Search failed: {e}")
        sys.exit(1)

    results = []
    for item in data.get("results", [])[:count]:
        results.append({
            "title": item.get("title", ""),
            "url": item.get("url", ""),
            "snippet": item.get("content", ""),
            "engine": item.get("engine", ""),
        })

    json_response(True, query=query, results=results, count=len(results))


# ═══════════════════════════════════════════════════════
# VIEW — Agent-initiated multimodal input
# ═══════════════════════════════════════════════════════

@cli.group()
def view():
    """View local images for multimodal perception."""
    pass


@view.command("image")
@click.argument("path")
@click.option(
    "--execution-model",
    default=None,
    help="Optional catalog key to test modality gate (harness passes this automatically)",
)
def view_image(path, execution_model):
    """Validate a local image path and return metadata for multimodal inject."""
    from model_catalog import execution_model_supports_input
    from model_drivers.view_paths import ViewPathError, inspect_image_for_view

    agent_name = get_agent_name()
    try:
        result = inspect_image_for_view(path, agent_name)
    except ViewPathError as e:
        json_response(False, error=e.message, code=e.code)
        sys.exit(1)
    except OSError as e:
        json_response(False, error=str(e), code="io_error")
        sys.exit(1)

    if execution_model:
        if not execution_model_supports_input(execution_model, "image"):
            json_response(
                False,
                error=(
                    f"Execution model '{execution_model}' does not support image input "
                    "(catalog input_modalities lacks 'image')"
                ),
                code="modality_unsupported",
                execution_model=execution_model,
                path=result.get("path"),
            )
            sys.exit(1)
        result["execution_model"] = execution_model

    json_response(True, **result)


# ═══════════════════════════════════════════════════════
# BROWSER — Headless browser automation (Playwright)
# ═══════════════════════════════════════════════════════

def _get_browser_config():
    """Read browser configuration from setup.ini."""
    import configparser
    config = configparser.ConfigParser()
    config.read("/etc/versa-agi/setup.ini")
    return {
        "enabled": config.get("browser", "enabled", fallback="false").lower() == "true",
        "timeout": int(config.get("browser", "timeout", fallback="30")) * 1000,  # ms
    }


def _check_browser_access():
    """Two-layer access check: system-wide + per-agent.

    Layer 1: [browser] enabled in setup.ini (system-wide kill switch)
    Layer 2: browser_enabled in agents.db (per-agent toggle)
    Both must be true for execution.
    """
    config = _get_browser_config()
    if not config["enabled"]:
        json_response(False, error="Browser automation is disabled system-wide. Enable via agitop System Settings or setup.sh.")
        sys.exit(1)

    agent_name = get_agent_name()
    conn = sqlite3.connect(agents_db, timeout=5)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT browser_enabled FROM agents WHERE name=?", (agent_name,)).fetchone()
    conn.close()
    if not row or not row["browser_enabled"]:
        json_response(False, error="Browser automation is disabled for this agent. Request access from the Primary User.")
        sys.exit(1)

    return config


def _get_screenshot_dir(agent_name: str) -> str:
    """Resolve screenshot directory from agents.db workspace column.

    COA:        {workspace}/.agent/workspace/screenshots/
    Sub-agents: {workspace}/workspace/screenshots/
    """
    conn = sqlite3.connect(agents_db, timeout=5)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT workspace FROM agents WHERE name=?", (agent_name,)).fetchone()
    conn.close()
    if not row:
        return "/tmp"
    workspace = row["workspace"]
    # COA has .agent/workspace symlink structure
    agent_ws = os.path.join(workspace, ".agent", "workspace")
    if os.path.exists(agent_ws) or os.path.islink(agent_ws):
        return os.path.join(agent_ws, "screenshots")
    return os.path.join(workspace, "workspace", "screenshots")


def _validate_browser_url(url: str):
    """Validate URL protocol — only http:// and https:// allowed."""
    if not url.startswith(("http://", "https://")):
        json_response(False, error=f"Invalid URL protocol. Only http:// and https:// are allowed. Got: {url[:50]}")
        sys.exit(1)


def _run_playwright_script(script: str, timeout_ms: int):
    """Write and execute a Playwright Python script as the agent user.

    Uses the same _get_exec_cmd() isolation pattern as 'execute python'.
    """
    import tempfile
    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
        f.write(script)
        temp_name = f.name
    os.chmod(temp_name, 0o644)
    try:
        cmd = _get_exec_cmd("python3", temp_name)
        timeout_sec = max(timeout_ms // 1000 + 10, 30)  # subprocess timeout = page timeout + 10s buffer
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_sec)
        output = result.stdout
        if result.stderr:
            output += "\nSTDERR:\n" + result.stderr
        return result.returncode == 0, output
    except subprocess.TimeoutExpired:
        return False, "Browser operation timed out"
    except Exception as e:
        return False, str(e)
    finally:
        if os.path.exists(temp_name):
            os.remove(temp_name)


@cli.group()
def browser():
    """Headless browser automation via Playwright."""
    pass


@browser.command("goto")
@click.argument("url")
@click.option("--screenshot", "take_screenshot", is_flag=True, help="Take a screenshot after loading")
@click.option("--selector", default=None, help="CSS selector to extract text from (default: full page)")
def browser_goto(url, take_screenshot, selector):
    """Navigate to a URL and return page content."""
    config = _check_browser_access()
    _validate_browser_url(url)
    timeout_ms = config["timeout"]
    agent_name = get_agent_name()

    screenshot_line = ""
    if take_screenshot:
        ss_dir = _get_screenshot_dir(agent_name)
        screenshot_line = (
            f"import os, time\n"
            f"ss_dir = {repr(ss_dir)}\n"
            f"os.makedirs(ss_dir, exist_ok=True)\n"
            f"ss_path = os.path.join(ss_dir, f\"goto_{{int(time.time())}}.png\")\n"
            f"page.screenshot(path=ss_path, full_page=True)\n"
            f"print(\"SCREENSHOT:\" + ss_path)"
        )

    if selector:
        extract_line = f'text = page.locator({repr(selector)}).inner_text(timeout={timeout_ms})\nprint("CONTENT:" + text)'
    else:
        extract_line = f'text = page.locator("body").inner_text(timeout={timeout_ms})\nprint("CONTENT:" + text[:8000])'

    # Build body lines with consistent 4-space indent inside the 'with' block
    body_lines = [
        f"browser = p.chromium.launch(headless=True)",
        f"page = browser.new_page()",
        f"page.goto({repr(url)}, timeout={timeout_ms})",
        f'print("TITLE:" + page.title())',
    ]
    body_lines.extend(extract_line.split("\n"))
    if screenshot_line:
        body_lines.extend(screenshot_line.split("\n"))
    body_lines.append("browser.close()")
    body = "\n    ".join(body_lines)

    script = f"""
import sys
sys.path.insert(0, "/usr/local/lib/versa-agi/venv/lib/python3/dist-packages")
try:
    from playwright.sync_api import sync_playwright
except ImportError:
    # Try the venv site-packages directly
    import glob
    paths = glob.glob("/usr/local/lib/versa-agi/venv/lib/python3.*/site-packages")
    for p in paths:
        sys.path.insert(0, p)
    from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    {body}
"""
    success, output = _run_playwright_script(script, timeout_ms)
    if not success:
        json_response(False, error=f"Browser error: {output}")
        return

    # Parse structured output
    result = {"url": url}
    for line in output.splitlines():
        if line.startswith("TITLE:"):
            result["title"] = line[6:]
        elif line.startswith("CONTENT:"):
            result["content"] = line[8:]
        elif line.startswith("SCREENSHOT:"):
            result["screenshot"] = line[11:]

    json_response(True, **result)


@browser.command("click")
@click.argument("url")
@click.argument("selector")
def browser_click(url, selector):
    """Navigate to URL and click an element."""
    config = _check_browser_access()
    _validate_browser_url(url)
    timeout_ms = config["timeout"]

    script = f"""
import sys, glob
sys.path.insert(0, "/usr/local/lib/versa-agi/venv/lib/python3/dist-packages")
for p in glob.glob("/usr/local/lib/versa-agi/venv/lib/python3.*/site-packages"):
    sys.path.insert(0, p)
from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page()
    page.goto({repr(url)}, timeout={timeout_ms})
    page.locator({repr(selector)}).click(timeout={timeout_ms})
    page.wait_for_load_state("networkidle", timeout={timeout_ms})
    print("TITLE:" + page.title())
    print("URL:" + page.url)
    text = page.locator("body").inner_text(timeout={timeout_ms})
    print("CONTENT:" + text[:8000])
    browser.close()
"""
    success, output = _run_playwright_script(script, timeout_ms)
    if not success:
        json_response(False, error=f"Click failed: {output}")
        return

    result = {"url": url, "selector": selector, "action": "click"}
    for line in output.splitlines():
        if line.startswith("TITLE:"):
            result["title"] = line[6:]
        elif line.startswith("URL:"):
            result["current_url"] = line[4:]
        elif line.startswith("CONTENT:"):
            result["content"] = line[8:]
    json_response(True, **result)


@browser.command("fill")
@click.argument("url")
@click.argument("selector")
@click.argument("value")
def browser_fill(url, selector, value):
    """Navigate to URL and fill a form field."""
    config = _check_browser_access()
    _validate_browser_url(url)
    timeout_ms = config["timeout"]

    script = f"""
import sys, glob
sys.path.insert(0, "/usr/local/lib/versa-agi/venv/lib/python3/dist-packages")
for p in glob.glob("/usr/local/lib/versa-agi/venv/lib/python3.*/site-packages"):
    sys.path.insert(0, p)
from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page()
    page.goto({repr(url)}, timeout={timeout_ms})
    page.locator({repr(selector)}).fill({repr(value)}, timeout={timeout_ms})
    print("FILLED:true")
    print("TITLE:" + page.title())
    browser.close()
"""
    success, output = _run_playwright_script(script, timeout_ms)
    if not success:
        json_response(False, error=f"Fill failed: {output}")
        return

    result = {"url": url, "selector": selector, "value": value, "action": "fill"}
    for line in output.splitlines():
        if line.startswith("TITLE:"):
            result["title"] = line[6:]
        elif line.startswith("FILLED:"):
            result["filled"] = True
    json_response(True, **result)


@browser.command("screenshot")
@click.argument("url")
@click.option("--path", "save_path", default=None, help="Save path (default: agent workspace)")
@click.option("--full-page", "full_page", is_flag=True, help="Capture full page (not just viewport)")
def browser_screenshot(url, save_path, full_page):
    """Navigate to URL and take a screenshot."""
    config = _check_browser_access()
    _validate_browser_url(url)
    timeout_ms = config["timeout"]
    agent_name = get_agent_name()

    if not save_path:
        ss_dir = _get_screenshot_dir(agent_name)
        save_path = f"__AUTO__{ss_dir}"

    script = f"""
import sys, os, time, glob
sys.path.insert(0, "/usr/local/lib/versa-agi/venv/lib/python3/dist-packages")
for p in glob.glob("/usr/local/lib/versa-agi/venv/lib/python3.*/site-packages"):
    sys.path.insert(0, p)
from playwright.sync_api import sync_playwright

save_path = {repr(save_path)}
if save_path.startswith("__AUTO__"):
    ss_dir = save_path[8:]
    os.makedirs(ss_dir, exist_ok=True)
    save_path = os.path.join(ss_dir, f"screenshot_{{int(time.time())}}.png")
else:
    os.makedirs(os.path.dirname(save_path), exist_ok=True)

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page()
    page.goto({repr(url)}, timeout={timeout_ms})
    page.screenshot(path=save_path, full_page={repr(full_page)})
    print("SCREENSHOT:" + save_path)
    print("TITLE:" + page.title())
    browser.close()
"""
    success, output = _run_playwright_script(script, timeout_ms)
    if not success:
        json_response(False, error=f"Screenshot failed: {output}")
        return

    result = {"url": url, "action": "screenshot", "full_page": full_page}
    for line in output.splitlines():
        if line.startswith("SCREENSHOT:"):
            result["path"] = line[11:]
        elif line.startswith("TITLE:"):
            result["title"] = line[6:]
    json_response(True, **result)


@browser.command("extract")
@click.argument("url")
@click.option("--selector", default="body", help="CSS selector (default: body)")
@click.option("--attribute", default=None, help="Element attribute to extract (default: text content)")
def browser_extract(url, selector, attribute):
    """Extract structured content from a page."""
    config = _check_browser_access()
    _validate_browser_url(url)
    timeout_ms = config["timeout"]

    if attribute:
        extract_expr = f'page.locator({repr(selector)}).get_attribute({repr(attribute)}, timeout={timeout_ms})'
    else:
        extract_expr = f'page.locator({repr(selector)}).all_text_contents()'

    script = f"""
import sys, json, glob
sys.path.insert(0, "/usr/local/lib/versa-agi/venv/lib/python3/dist-packages")
for p in glob.glob("/usr/local/lib/versa-agi/venv/lib/python3.*/site-packages"):
    sys.path.insert(0, p)
from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page()
    page.goto({repr(url)}, timeout={timeout_ms})
    result = {extract_expr}
    print("EXTRACT:" + json.dumps(result))
    print("TITLE:" + page.title())
    browser.close()
"""
    success, output = _run_playwright_script(script, timeout_ms)
    if not success:
        json_response(False, error=f"Extract failed: {output}")
        return

    result = {"url": url, "selector": selector, "action": "extract"}
    for line in output.splitlines():
        if line.startswith("EXTRACT:"):
            result["data"] = json.loads(line[8:])
        elif line.startswith("TITLE:"):
            result["title"] = line[6:]
    if attribute:
        result["attribute"] = attribute
    json_response(True, **result)


@browser.command("enable")
@click.argument("agent_name")
def browser_enable(agent_name):
    """Enable browser automation for a sub-agent. COA-only.

    Grants browser access and installs Chromium binaries for the agent's OS user.
    Requires: system-wide [browser] enabled=true AND caller must be COA with browser_enabled=1.
    """
    # Guard: COA-exclusive. AGICTL_AGENT_USER is the wrapper-set OS caller,
    # forwarded across sudo elevation (VERSA_AGENT_NAME is stripped by sudo).
    # Empty => direct root/PU invocation, which is allowed.
    caller = os.environ.get("AGICTL_AGENT_USER", "")
    if caller and caller not in ("coa",):
        json_response(False, error="Permission denied: only COA can manage browser access for other agents")
        sys.exit(1)

    # Guard: system-wide enabled
    config = _get_browser_config()
    if not config["enabled"]:
        json_response(False, error="Browser automation is disabled system-wide. Enable via agitop System Settings or setup.sh.")
        sys.exit(1)

    # Guard: COA must have browser access
    conn = sqlite3.connect(agents_db, timeout=5)
    conn.row_factory = sqlite3.Row

    # Resolve COA name from setup.ini
    import configparser
    _ini = configparser.ConfigParser()
    _ini.read("/etc/versa-agi/setup.ini")
    coa_name = _ini.get("users", "coa", fallback="coa")

    coa_row = conn.execute("SELECT browser_enabled FROM agents WHERE name=?", (coa_name,)).fetchone()
    if not coa_row or not coa_row["browser_enabled"]:
        json_response(False, error=f"COA ({coa_name}) does not have browser access. Request from Primary User via agitop.")
        sys.exit(1)

    # Guard: target cannot be protected agents
    target = conn.execute("SELECT name, os_user, protected FROM agents WHERE name=?", (agent_name,)).fetchone()
    if not target:
        conn.close()
        json_response(False, error=f"Agent '{agent_name}' not found")
        sys.exit(1)
    if target["protected"]:
        conn.close()
        json_response(False, error=f"Cannot manage browser access for protected agent '{agent_name}'. Use agitop dashboard.")
        sys.exit(1)

    # Enable in DB
    conn.execute("UPDATE agents SET browser_enabled = 1 WHERE name=?", (agent_name,))
    conn.commit()
    conn.close()

    # Install Chromium for agent user
    os_user = target["os_user"]
    try:
        harness_venv = "/usr/local/lib/versa-agi/venv"
        playwright_bin = os.path.join(harness_venv, "bin", "playwright")
        if os.path.isfile(playwright_bin):
            subprocess.run(
                ["sudo", "-u", os_user, playwright_bin, "install", "chromium"],
                capture_output=True, timeout=120
            )
    except Exception as e:
        json_response(True, agent=agent_name, browser_enabled=True,
                      warning=f"DB updated but Chromium install failed: {e}. Agent may need manual install.")
        return

    json_response(True, agent=agent_name, os_user=os_user, browser_enabled=True,
                  message=f"Browser automation enabled for {agent_name}")


@browser.command("disable")
@click.argument("agent_name")
def browser_disable(agent_name):
    """Disable browser automation for a sub-agent. COA-only.

    Revokes browser access, removes Chromium binaries and screenshots.
    """
    # Guard: COA-exclusive. AGICTL_AGENT_USER is the wrapper-set OS caller,
    # forwarded across sudo elevation (VERSA_AGENT_NAME is stripped by sudo).
    # Empty => direct root/PU invocation, which is allowed.
    caller = os.environ.get("AGICTL_AGENT_USER", "")
    if caller and caller not in ("coa",):
        json_response(False, error="Permission denied: only COA can manage browser access for other agents")
        sys.exit(1)

    conn = sqlite3.connect(agents_db, timeout=5)
    conn.row_factory = sqlite3.Row

    target = conn.execute("SELECT name, os_user, protected, workspace FROM agents WHERE name=?", (agent_name,)).fetchone()
    if not target:
        conn.close()
        json_response(False, error=f"Agent '{agent_name}' not found")
        sys.exit(1)
    if target["protected"]:
        conn.close()
        json_response(False, error=f"Cannot manage browser access for protected agent '{agent_name}'. Use agitop dashboard.")
        sys.exit(1)

    # Disable in DB
    conn.execute("UPDATE agents SET browser_enabled = 0 WHERE name=?", (agent_name,))
    conn.commit()
    conn.close()

    # Cleanup: remove browser binaries
    os_user = target["os_user"]
    cache_dir = f"/home/{os_user}/.cache/ms-playwright/"
    if os.path.isdir(cache_dir):
        import shutil
        shutil.rmtree(cache_dir, ignore_errors=True)

    # Cleanup: remove screenshots
    workspace = target["workspace"]
    for ss_path in [
        os.path.join(workspace, ".agent", "workspace", "screenshots"),
        os.path.join(workspace, "workspace", "screenshots"),
    ]:
        if os.path.isdir(ss_path):
            import shutil
            shutil.rmtree(ss_path, ignore_errors=True)

    json_response(True, agent=agent_name, os_user=os_user, browser_enabled=False,
                  message=f"Browser automation disabled for {agent_name}. Binaries and screenshots removed.")


# ═══════════════════════════════════════════════════════
# SKILL MANAGEMENT
# ═══════════════════════════════════════════════════════

@cli.group()
def skill():
    """Manage skills — register, create, distribute, and track."""
    pass


@skill.command("new")
@click.argument("name")
@click.option("--description", "-d", default=None, help="Skill description")
@click.option("--scope", "-s", type=click.Choice(["all", "coa_only"]), default="all", help="Skill scope: 'all' (default) or 'coa_only'")
def skill_new(name, description, scope):
    """Create a new skill template and asset directory.

    Creates the .md template and co-located asset directory in COA's
    skills folder. Sets status to 'draft' in the skills DB. COA must
    complete the template and mark it 'ready' for distribution.
    """
    name = name.lower().replace(" ", "_").replace("-", "_")

    # Resolve COA skills directory
    conn = sqlite3.connect(agents_db, timeout=5)
    conn.row_factory = sqlite3.Row
    coa = conn.execute("SELECT workspace FROM agents WHERE name='coa'").fetchone()
    if not coa:
        conn.close()
        json_response(False, error="COA agent not found in registry")
        sys.exit(1)
    skills_dir = os.path.join(coa["workspace"], ".agent", "skills")
    conn.close()

    skill_file = os.path.join(skills_dir, f"{name}.md")
    asset_dir = os.path.join(skills_dir, name)

    # Check for existing
    if os.path.exists(skill_file):
        json_response(False, error=f"Skill file already exists: {skill_file}")
        sys.exit(1)

    # Create skill template
    template = f"""# {name.replace('_', ' ').title()}

> **Status:** Draft — complete this template, then mark ready with: `agictl skill status {name} ready`

## Purpose

{description or 'Describe the purpose and goal of this skill.'}

## Target Audience

- [ ] COA only
- [ ] All agents (COA + sub-agents)

## Behavioral Directives

1. **Step 1** — Describe the first action the agent should take.
2. **Step 2** — Describe the next action.
3. **Step 3** — Continue as needed.

## Related Commands

List any `agictl` commands this skill uses:
- `agictl ...`

## Asset Directory

Co-located scripts and templates are stored in `.agent/skills/{name}/`.
Reference them using relative paths from the agent's workspace.

## Notes

- Add any edge cases, warnings, or design decisions here.
"""

    os.makedirs(skills_dir, exist_ok=True)
    with open(skill_file, "w") as f:
        f.write(template)

    # Create asset directory with README
    os.makedirs(asset_dir, exist_ok=True)
    asset_readme = os.path.join(asset_dir, "README.md")
    with open(asset_readme, "w") as f:
        f.write(f"# {name.replace('_', ' ').title()} — Assets\n\n"
                f"This directory contains scripts, templates, and reference data\n"
                f"for the `{name}` skill.\n\n"
                f"Files here are deployed to sub-agents alongside the skill `.md` file.\n")

    # Set permissions. agictl elevates to watchdog, so these files are created
    # watchdog-owned — chown to the agent only works when running as root
    # (e.g. invoked from setup.sh). Either way, make everything group-writable
    # (agi_agents) so COA can author the draft regardless of file owner.
    agent_user = "coa"
    if os.geteuid() == 0:
        subprocess.run(["chown", f"{agent_user}:agi_agents", skill_file], check=False)
        subprocess.run(["chown", "-R", f"{agent_user}:agi_agents", asset_dir], check=False)
    subprocess.run(["chmod", "664", skill_file], check=False)
    subprocess.run(["chmod", "2775", asset_dir], check=False)  # setgid: new files inherit agi_agents
    subprocess.run(["chmod", "664", asset_readme], check=False)

    # Register in DB
    conn = sqlite3.connect(agents_db, timeout=5)
    try:
        conn.execute(
            "INSERT OR IGNORE INTO skills (name, type, origin, has_assets, description, status, scope) "
            "VALUES (?, 'agent_created', 'coa', 1, ?, 'draft', ?)",
            (name, description or "", scope)
        )
        conn.commit()
    finally:
        conn.close()

    json_response(True, skill=name, skill_file=skill_file, asset_dir=asset_dir, status="draft", scope=scope)


@skill.command("status")
@click.argument("name")
@click.argument("new_status", type=click.Choice(["ready", "updated"]))
def skill_status(name, new_status):
    """Update a skill's status (draft→ready, synced→updated).

    When set to 'ready', Lifeline will distribute the skill to all
    active sub-agents on its next tick and set status to 'synced'.
    When set to 'updated', Lifeline will re-sync the skill.
    """
    name = name.lower()

    conn = sqlite3.connect(agents_db, timeout=5)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT status FROM skills WHERE name=?", (name,)).fetchone()

    if not row:
        conn.close()
        json_response(False, error=f"Skill '{name}' not found in registry")
        sys.exit(1)

    current = row["status"]

    # Validate transitions
    valid_transitions = {
        "draft": ["ready"],
        "synced": ["updated"],
    }
    allowed = valid_transitions.get(current, [])
    if new_status not in allowed:
        conn.close()
        json_response(False, error=f"Invalid transition: {current} → {new_status}. Allowed: {current} → {', '.join(allowed) if allowed else 'none'}")
        sys.exit(1)

    conn.execute(
        "UPDATE skills SET status=?, updated_at=datetime('now') WHERE name=?",
        (new_status, name)
    )
    conn.commit()
    conn.close()

    json_response(True, skill=name, previous_status=current, new_status=new_status)


@skill.command("list")
@click.option("--status", "-s", default=None, help="Filter by status (draft, ready, synced, updated)")
@click.option("--json-output", is_flag=True, help="Output as JSON instead of table")
def skill_list(status, json_output):
    """List all registered skills with their status."""
    conn = sqlite3.connect(agents_db, timeout=5)
    conn.row_factory = sqlite3.Row

    if status:
        rows = conn.execute(
            "SELECT name, type, origin, has_assets, status, scope, description, created_at, updated_at "
            "FROM skills WHERE status=? ORDER BY name", (status,)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT name, type, origin, has_assets, status, scope, description, created_at, updated_at "
            "FROM skills ORDER BY type DESC, name"
        ).fetchall()
    conn.close()

    if json_output:
        skills_data = [dict(r) for r in rows]
        json_response(True, skills=skills_data, count=len(skills_data))
        return

    if not rows:
        click.echo("No skills registered.")
        return

    console = Console()
    table = Table(title="Skills Registry", show_lines=False)
    table.add_column("Name", style="cyan", no_wrap=True)
    table.add_column("Type", style="dim")
    table.add_column("Origin", style="dim")
    table.add_column("Assets", justify="center")
    table.add_column("Status", style="bold")
    table.add_column("Scope", style="dim")
    table.add_column("Description", max_width=50)

    status_styles = {
        "draft": "[dim]draft[/]",
        "ready": "[yellow]ready[/]",
        "synced": "[green]synced[/]",
        "updated": "[yellow]updated[/]",
    }

    scope_styles = {
        "all": "all",
        "coa_only": "[magenta]coa_only[/]",
    }

    for r in rows:
        s = status_styles.get(r["status"], r["status"])
        sc = scope_styles.get(r["scope"], r["scope"])
        assets = "✓" if r["has_assets"] else "—"
        desc = (r["description"] or "")[:50]
        table.add_row(r["name"], r["type"], r["origin"], assets, s, sc, desc)

    console.print(table)
    click.echo(f"\n{len(rows)} skill(s) registered")


@skill.command("register")
def skill_register():
    """Bootstrap skills table from the filesystem.

    Scans COA's .agent/skills/ directory and registers any skill files
    not already in the database. Used for initial population or after
    manual skill file additions.
    """
    # Resolve COA skills directory
    conn = sqlite3.connect(agents_db, timeout=5)
    conn.row_factory = sqlite3.Row
    coa = conn.execute("SELECT workspace FROM agents WHERE name='coa'").fetchone()
    if not coa:
        conn.close()
        json_response(False, error="COA agent not found in registry")
        sys.exit(1)
    skills_dir = os.path.join(coa["workspace"], ".agent", "skills")

    if not os.path.isdir(skills_dir):
        conn.close()
        json_response(False, error=f"Skills directory not found: {skills_dir}")
        sys.exit(1)

    import glob
    registered = 0
    skipped = 0

    for skill_file in sorted(glob.glob(os.path.join(skills_dir, "*.md"))):
        basename = os.path.basename(skill_file)
        if basename == "README.md":
            continue
        skill_name = basename.replace(".md", "")

        # Check if already registered
        exists = conn.execute("SELECT id FROM skills WHERE name=?", (skill_name,)).fetchone()
        if exists:
            skipped += 1
            continue

        # Read first line for description
        description = ""
        try:
            with open(skill_file, "r") as f:
                first_line = f.readline().strip()
                if first_line.startswith("# "):
                    description = first_line[2:]
        except Exception:
            pass

        # Check for co-located asset directory
        asset_dir = os.path.join(skills_dir, skill_name)
        has_assets = os.path.isdir(asset_dir)

        shipped_file = os.path.join("/home/watchdog/core-infra/skills", f"{skill_name}.md")
        if skill_name.endswith("_override"):
            skill_type, origin = "override", "coa"
        elif os.path.isfile(shipped_file):
            skill_type, origin = "system", "shipped"
        else:
            skill_type, origin = "agent_created", "coa"

        conn.execute(
            "INSERT INTO skills (name, type, origin, has_assets, description, status) "
            "VALUES (?, ?, ?, ?, ?, 'draft')",
            (skill_name, skill_type, origin, 1 if has_assets else 0, description),
        )
        registered += 1

    conn.commit()
    conn.close()

    json_response(True, registered=registered, skipped=skipped, skills_dir=skills_dir)


@skill.command("override")
@click.argument("name")
def skill_override(name):
    """Create an override for a shipped skill.

    Creates {name}_override.md in COA's skills directory, pre-populated
    with the shipped skill content as a starting template. Registers the
    override in the skills DB with type='override' and status='draft'.
    COA edits the override, then marks it 'ready' for distribution.

    The harness resolves overrides at injection time: if {name}_override.md
    exists in the agent's skills directory, it is injected instead of the
    shipped {name}.md. Overrides propagate to all agents via rsync.

    To withdraw an override, delete the _override.md file and remove the
    DB row. Agents revert to the shipped version on the next rsync cycle.
    """
    name = name.lower().replace(" ", "_").replace("-", "_")
    override_name = f"{name}_override"

    # Resolve COA skills directory
    conn = sqlite3.connect(agents_db, timeout=5)
    conn.row_factory = sqlite3.Row
    coa = conn.execute("SELECT workspace FROM agents WHERE name='coa'").fetchone()
    if not coa:
        conn.close()
        json_response(False, error="COA agent not found in registry")
        sys.exit(1)
    skills_dir = os.path.join(coa["workspace"], ".agent", "skills")

    # Check the override doesn't already exist
    override_file = os.path.join(skills_dir, f"{override_name}.md")
    if os.path.exists(override_file):
        conn.close()
        json_response(False, error=f"Override already exists: {override_file}")
        sys.exit(1)

    # Load shipped skill content as template
    shipped_source = "/home/watchdog/core-infra/skills"
    shipped_file = os.path.join(shipped_source, f"{name}.md")
    template_content = ""
    if os.path.isfile(shipped_file):
        try:
            with open(shipped_file, "r") as f:
                template_content = f.read()
        except Exception:
            pass

    # Build override file
    override_content = f"""# {name.replace('_', ' ').title()} — Override

> **Override of:** `{name}.md` (shipped).
> **Status:** Draft — edit this file, then mark ready: `agictl skill status {override_name} ready`
>
> This file takes precedence over the shipped `{name}.md` during harness injection.
> To withdraw this override, delete this file and run: `agictl skill remove {override_name}`

---

{template_content}"""

    os.makedirs(skills_dir, exist_ok=True)
    with open(override_file, "w") as f:
        f.write(override_content)

    # Set permissions (COA-owned, group-writable for agi_agents)
    subprocess.run(["chown", "coa:agi_agents", override_file], check=False)
    subprocess.run(["chmod", "644", override_file], check=False)

    # Register in DB
    try:
        # Read description from shipped skill
        description = ""
        if template_content:
            for line in template_content.splitlines():
                if line.startswith("# "):
                    description = f"Override: {line[2:]}"
                    break
        conn.execute(
            "INSERT OR IGNORE INTO skills (name, type, origin, has_assets, description, status, scope) "
            "VALUES (?, 'override', 'coa', 0, ?, 'draft', 'all')",
            (override_name, description)
        )
        conn.commit()
    finally:
        conn.close()

    json_response(True, skill=override_name, override_of=name, file=override_file, status="draft")


# ═══════════════════════════════════════════════════════
# SYSTEM PACKAGE MANAGEMENT
# ═══════════════════════════════════════════════════════

def _require_pu_or_root():
    """Block agent users from privileged pkg operations."""
    agent_user = os.getenv("AGICTL_AGENT_USER", "")
    if agent_user:
        json_response(False, error=f"Permission denied. '{agent_user}' cannot perform this operation. "
                                   "Request the package via 'agictl pkg request' instead.")
        sys.exit(1)

def _validate_pkg_name(name):
    """Validate package name against shell injection. Returns sanitized name or exits."""
    import re
    name = name.strip().lower()
    if not re.match(r"^[a-z0-9][a-z0-9.+\-]+$", name):
        json_response(False, error="Invalid package name format. Only lowercase alphanumeric characters, "
                                   "dashes, dots, and plus signs are allowed.")
        sys.exit(1)
    return name

def _get_agents_db_path():
    """Resolve the agents.db path."""
    return os.getenv("AGICTL_AGENTS_DB", "/var/lib/versa-agi/agents.db")

@cli.group()
def pkg():
    """Manage system packages — request, approve, install, and track."""
    pass


@pkg.command("list")
def pkg_list():
    """List all registered system packages."""
    db_path = _get_agents_db_path()
    try:
        conn = sqlite3.connect(db_path, timeout=5)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT name, status, reason, requested_by, requested_at, resolved_at, notified_at "
            "FROM system_packages ORDER BY requested_at DESC"
        ).fetchall()
        conn.close()
    except Exception as e:
        json_response(False, error=f"Database error: {e}")
        return

    if not rows:
        console = Console()
        console.print("[dim]No system packages registered.[/]")
        return

    table = Table(title="System Packages")
    table.add_column("Package", style="cyan", no_wrap=True)
    table.add_column("Status", style="bold")
    table.add_column("Reason")
    table.add_column("Requested By", style="dim")
    table.add_column("Requested At", style="dim")
    table.add_column("Resolved At", style="dim")

    status_styles = {"approved": "green", "requested": "yellow", "denied": "red"}
    for row in rows:
        s = row["status"]
        style = status_styles.get(s, "")
        table.add_row(
            row["name"],
            f"[{style}]{s}[/{style}]" if style else s,
            row["reason"] or "",
            row["requested_by"] or "",
            row["requested_at"] or "",
            row["resolved_at"] or "",
        )

    console = Console()
    console.print(table)


@pkg.command("request")
@click.argument("name")
@click.option("--reason", "-r", default=None, help="Why the package is needed")
def pkg_request(name, reason):
    """Request a system package for PU approval."""
    name = _validate_pkg_name(name)
    requester = get_agent_name()
    db_path = _get_agents_db_path()

    try:
        conn = sqlite3.connect(db_path, timeout=5)
        # Check if already exists
        existing = conn.execute("SELECT status FROM system_packages WHERE name=?", (name,)).fetchone()
        if existing:
            json_response(False, error=f"Package '{name}' already registered with status '{existing[0]}'. "
                                       f"Use 'agictl pkg list' to check status.")
            conn.close()
            return

        conn.execute(
            "INSERT INTO system_packages (name, status, reason, requested_by) VALUES (?, 'requested', ?, ?)",
            (name, reason, requester)
        )
        conn.commit()
        conn.close()
    except Exception as e:
        json_response(False, error=f"Database error: {e}")
        return

    json_response(True, package=name, status="requested", requested_by=requester)


@pkg.command("add")
@click.argument("name")
@click.option("--reason", "-r", default=None, help="Why the package is needed")
def pkg_add(name, reason):
    """Directly add a package as approved (PU-only, bypasses approval queue)."""
    _require_pu_or_root()
    name = _validate_pkg_name(name)
    db_path = _get_agents_db_path()

    try:
        conn = sqlite3.connect(db_path, timeout=5)
        conn.execute(
            "INSERT OR REPLACE INTO system_packages (name, status, reason, requested_by, requested_at, resolved_at) "
            "VALUES (?, 'approved', ?, 'pu', datetime('now'), datetime('now'))",
            (name, reason)
        )
        conn.commit()
        conn.close()
    except Exception as e:
        json_response(False, error=f"Database error: {e}")
        return

    json_response(True, package=name, status="approved", action="added")


@pkg.command("approve")
@click.argument("name")
def pkg_approve(name):
    """Approve a requested package (PU-only)."""
    _require_pu_or_root()
    name = _validate_pkg_name(name)
    db_path = _get_agents_db_path()

    try:
        conn = sqlite3.connect(db_path, timeout=5)
        row = conn.execute("SELECT status FROM system_packages WHERE name=?", (name,)).fetchone()
        if not row:
            json_response(False, error=f"Package '{name}' not found in registry.")
            conn.close()
            return
        if row[0] == "approved":
            json_response(False, error=f"Package '{name}' is already approved.")
            conn.close()
            return

        conn.execute(
            "UPDATE system_packages SET status='approved', resolved_at=datetime('now'), notified_at=NULL WHERE name=?",
            (name,)
        )
        conn.commit()
        conn.close()
    except Exception as e:
        json_response(False, error=f"Database error: {e}")
        return

    json_response(True, package=name, status="approved", action="approved")


@pkg.command("deny")
@click.argument("name")
def pkg_deny(name):
    """Deny a requested package (PU-only)."""
    _require_pu_or_root()
    name = _validate_pkg_name(name)
    db_path = _get_agents_db_path()

    try:
        conn = sqlite3.connect(db_path, timeout=5)
        row = conn.execute("SELECT status FROM system_packages WHERE name=?", (name,)).fetchone()
        if not row:
            json_response(False, error=f"Package '{name}' not found in registry.")
            conn.close()
            return

        conn.execute(
            "UPDATE system_packages SET status='denied', resolved_at=datetime('now') WHERE name=?",
            (name,)
        )
        conn.commit()
        conn.close()
    except Exception as e:
        json_response(False, error=f"Database error: {e}")
        return

    json_response(True, package=name, status="denied", action="denied")


@pkg.command("remove")
@click.argument("name")
def pkg_remove(name):
    """Remove a package from the registry (PU-only)."""
    _require_pu_or_root()
    name = _validate_pkg_name(name)
    db_path = _get_agents_db_path()

    try:
        conn = sqlite3.connect(db_path, timeout=5)
        row = conn.execute("SELECT name FROM system_packages WHERE name=?", (name,)).fetchone()
        if not row:
            json_response(False, error=f"Package '{name}' not found in registry.")
            conn.close()
            return

        conn.execute("DELETE FROM system_packages WHERE name=?", (name,))
        conn.commit()
        conn.close()
    except Exception as e:
        json_response(False, error=f"Database error: {e}")
        return

    json_response(True, package=name, action="removed")


@pkg.command("install")
@click.argument("name")
def pkg_install(name):
    """Install an approved system package via apt-get (any user, approved-gate enforced)."""
    name = _validate_pkg_name(name)
    db_path = _get_agents_db_path()

    # Verify package is approved before attempting install
    try:
        conn = sqlite3.connect(db_path, timeout=5)
        row = conn.execute("SELECT status FROM system_packages WHERE name=?", (name,)).fetchone()
        conn.close()
    except Exception as e:
        json_response(False, error=f"Database error: {e}")
        return

    if not row:
        json_response(False, error=f"Package '{name}' is not in the system registry. "
                                   f"Request it first: agictl pkg request {name} --reason \"...\"")
        return
    if row[0] != "approved":
        json_response(False, error=f"Package '{name}' has status '{row[0]}'. Only 'approved' packages can be installed. "
                                   f"Ask the Primary User to approve it first.")
        return

    # Execute apt-get install via sudo (watchdog→root sudoers rule)
    try:
        result = subprocess.run(
            ["sudo", "/usr/bin/apt-get", "install", "-y", name],
            capture_output=True, text=True, timeout=300
        )
        if result.returncode == 0:
            json_response(True, package=name, action="installed",
                          output=result.stdout[-500:] if len(result.stdout) > 500 else result.stdout)
        else:
            json_response(False, error=f"apt-get install failed (exit {result.returncode})",
                          stderr=result.stderr[-500:] if len(result.stderr) > 500 else result.stderr)
    except subprocess.TimeoutExpired:
        json_response(False, error=f"Package installation timed out after 300 seconds.")
    except Exception as e:
        json_response(False, error=f"Installation error: {e}")


from agictl import utility_cli

utility_cli.register(
    cli,
    json_response=json_response,
    tasks_reader=tasks_reader,
    require_pu_or_coa=_require_pu_or_coa,
    load_catalog=_load_catalog,
    tasks_db=tasks_db,
)
utility_cli.register_modality_map_commands(
    model,
    json_response=json_response,
    require_pu_or_coa=_require_pu_or_coa,
    load_catalog=_load_catalog,
)

from agictl import organization_cli

organization_cli.register(
    cli,
    json_response=json_response,
)


if __name__ == "__main__":
    cli(prog_name="agictl")
