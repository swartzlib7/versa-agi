#!/usr/bin/env python3
"""
sync_poise.py

Single deterministic deployer for sub-agent and watchdog poise files.
Executed by setup.sh on BOTH fresh install and --update — identical behavior.

1. Roles registry — weave `config/roles/agent_poise.md` (shared skeleton)
   with each `config/roles/<role_id>/poise.md` (fragment) into
   `/etc/versa-agi/poise/roles/<role_id>/poise.md` (+ `role.ini` copy),
   then prune registry entries with no repo counterpart.
2. Watchdog poise — `config/watchdog_poise.md` → `/etc/versa-agi/poise/<watchdog>.md`.
3. Active agents — refresh `/etc/versa-agi/poise/<agent>.md` from the woven
   registry (role label → role_id via role.ini; unknown labels map to `custom`).

Fixed, non-negotiable layout: missing sources or a failed weave exit non-zero
so setup aborts rather than deploying a partial/stale registry.
COA/task-protocol/anchor poises are deployed by setup.sh directly (they carry
Primary-User identity injection). Duties files are never touched here.

Requires root.
"""

import os
import sys
import sqlite3
import shutil
import subprocess
import configparser

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from weave_poise import weave

POISE_DIR = "/etc/versa-agi/poise"


def _fail(msg: str) -> None:
    print(f"sync_poise: ERROR: {msg}", file=sys.stderr)
    sys.exit(1)


def _resolve_env():
    # setup.sh passes these explicitly (authoritative on fresh installs where
    # /etc/versa-agi/setup.ini is not yet deployed); the INI is the fallback
    # for standalone invocations on a provisioned host.
    core_infra = os.getenv("VERSA_CORE_INFRA", "")
    watchdog_user = os.getenv("VERSA_WATCHDOG_USER", "")
    coa_user = os.getenv("VERSA_COA_USER", "")

    if not core_infra:
        core_infra = "/home/watchdog/core-infra"
        paths_env = "/etc/versa-agi/paths.env"
        if os.path.isfile(paths_env):
            with open(paths_env, "r") as f:
                for line in f:
                    if line.startswith("VERSA_CORE_INFRA="):
                        core_infra = line.strip().split("=")[1].strip('"')
    if not os.path.isdir(core_infra):
        _fail(f"core infra not found at {core_infra}")

    if not watchdog_user or not coa_user:
        ini = configparser.ConfigParser()
        setup_ini_path = "/etc/versa-agi/setup.ini"
        if not os.path.isfile(setup_ini_path):
            _fail("cannot resolve system users: /etc/versa-agi/setup.ini missing and "
                  "VERSA_WATCHDOG_USER/VERSA_COA_USER not set")
        ini.read(setup_ini_path)
        watchdog_user = watchdog_user or ini.get("users", "watchdog", fallback="watchdog")
        coa_user = coa_user or ini.get("users", "coa", fallback="coa")
    return core_infra, watchdog_user, coa_user


def deploy_roles_registry(core_infra: str, watchdog_user: str, coa_user: str) -> dict:
    """Weave repo fragments into the deployed registry; prune stale entries."""
    roles_src = os.path.join(core_infra, "config", "roles")
    roles_dest = os.path.join(POISE_DIR, "roles")
    base_path = os.path.join(roles_src, "agent_poise.md")

    if not os.path.isdir(roles_src):
        _fail(f"roles source missing: {roles_src}")
    if not os.path.isfile(base_path):
        _fail(f"poise skeleton missing: {base_path}")
    with open(base_path, "r", encoding="utf-8") as f:
        base_text = f.read()

    os.makedirs(roles_dest, exist_ok=True)
    role_ids = sorted(
        d for d in os.listdir(roles_src)
        if os.path.isdir(os.path.join(roles_src, d))
    )
    if not role_ids:
        _fail(f"no role directories found in {roles_src}")

    label_map = {}  # role.ini name= label → role_id (for agent refresh below)
    for role_id in role_ids:
        src_dir = os.path.join(roles_src, role_id)
        fragment_path = os.path.join(src_dir, "poise.md")
        if not os.path.isfile(fragment_path):
            _fail(f"malformed role '{role_id}': {fragment_path} missing")
        with open(fragment_path, "r", encoding="utf-8") as f:
            fragment_text = f.read()
        try:
            woven = weave(base_text, fragment_text)
        except ValueError as e:
            _fail(f"weave failed for role '{role_id}': {e}")

        dest_dir = os.path.join(roles_dest, role_id)
        os.makedirs(dest_dir, exist_ok=True)
        dest_poise = os.path.join(dest_dir, "poise.md")
        # Registry files are read-only (440) — remove before rewrite
        if os.path.exists(dest_poise):
            os.remove(dest_poise)
        with open(dest_poise, "w", encoding="utf-8") as f:
            f.write(woven)

        ini_src = os.path.join(src_dir, "role.ini")
        if os.path.isfile(ini_src):
            ini_dest = os.path.join(dest_dir, "role.ini")
            if os.path.exists(ini_dest):
                os.remove(ini_dest)
            shutil.copy2(ini_src, ini_dest)
            cfg = configparser.ConfigParser()
            try:
                cfg.read(ini_src)
                if cfg.has_option("role", "name"):
                    label_map[cfg.get("role", "name").strip()] = role_id
            except Exception:
                pass
        print(f"  woven: roles/{role_id}/poise.md ({len(woven)} chars)")

    # Prune registry entries with no repo counterpart — the registry is a
    # system-managed mirror of config/roles (also clears legacy flat files).
    for entry in sorted(os.listdir(roles_dest)):
        if entry not in role_ids:
            entry_path = os.path.join(roles_dest, entry)
            if os.path.isdir(entry_path):
                shutil.rmtree(entry_path)
            else:
                os.remove(entry_path)
            print(f"  pruned stale registry entry: {entry}")

    # §IX.2 — COA reads, cannot modify
    subprocess.run(["chown", "-R", f"{watchdog_user}:{coa_user}", roles_dest], check=False)
    subprocess.run(["chmod", "750", roles_dest], check=False)
    subprocess.run(["find", roles_dest, "-mindepth", "1", "-type", "d",
                    "-exec", "chmod", "750", "{}", "+"], check=False)
    subprocess.run(["find", roles_dest, "-type", "f",
                    "-exec", "chmod", "440", "{}", "+"], check=False)
    print(f"Roles registry deployed: {len(role_ids)} roles → {roles_dest}")
    return label_map


def deploy_watchdog_poise(core_infra: str, watchdog_user: str) -> None:
    """config/watchdog_poise.md → /etc/versa-agi/poise/<watchdog>.md."""
    src = os.path.join(core_infra, "config", "watchdog_poise.md")
    if not os.path.isfile(src):
        _fail(f"watchdog poise source missing: {src}")
    dest = os.path.join(POISE_DIR, f"{watchdog_user}.md")
    shutil.copy2(src, dest)
    subprocess.run(["chown", f"{watchdog_user}:{watchdog_user}", dest], check=False)
    subprocess.run(["chmod", "640", dest], check=False)
    print(f"Watchdog poise deployed → {dest}")


def refresh_agent_poises(label_map: dict, watchdog_user: str, coa_user: str) -> None:
    """Refresh every non-removed sub-agent's canonical poise from the registry."""
    agents_db = os.getenv("AGICTL_AGENTS_DB", "/var/lib/versa-agi/agents.db")
    if not os.path.isfile(agents_db):
        print("No agents.db — skipping agent poise refresh (fresh install)")
        return

    try:
        conn = sqlite3.connect(agents_db, timeout=5)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT name, role FROM agents WHERE status != 'removed' AND name NOT IN (?, ?)",
            (watchdog_user, coa_user),
        ).fetchall()
        conn.close()
    except Exception as e:
        _fail(f"agents.db read failed: {e}")

    refreshed = 0
    for row in rows:
        name = row["name"]
        role_label = (row["role"] or "").strip()
        # COA-defined custom role labels have no registry dir — they map to
        # the custom template (same rule as 'agent approve').
        role_id = label_map.get(role_label, "custom")
        src = os.path.join(POISE_DIR, "roles", role_id, "poise.md")
        if not os.path.isfile(src):
            _fail(f"registry template missing for agent '{name}' (role '{role_id}'): {src}")
        dest = os.path.join(POISE_DIR, f"{name}.md")
        shutil.copy2(src, dest)
        subprocess.run(["chown", f"{watchdog_user}:{watchdog_user}", dest], check=False)
        subprocess.run(["chmod", "640", dest], check=False)
        print(f"  refreshed: {name}.md ← roles/{role_id}")
        refreshed += 1
    print(f"Agent poises refreshed: {refreshed}")


def main() -> None:
    if os.geteuid() != 0:
        _fail("requires root privileges")
    core_infra, watchdog_user, coa_user = _resolve_env()
    label_map = deploy_roles_registry(core_infra, watchdog_user, coa_user)
    deploy_watchdog_poise(core_infra, watchdog_user)
    refresh_agent_poises(label_map, watchdog_user, coa_user)


if __name__ == "__main__":
    main()
