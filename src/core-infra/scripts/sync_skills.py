#!/usr/bin/env python3
"""
sync_skills.py

Deploy shipped skills to all active agents. Executed by setup.sh during the
--update flow (behind the operator prompt).

1. Sub-agents — `agictl agent deploy-skills <name>` per active agent
   (rsync --delete mirror; excludes coa_only and agent-created skills).
2. COA — merge shipped skills into the COA skills dir (never --delete;
   the dir also holds agent_created and override skills that must survive).

Poise deployment lives in sync_poise.py (runs unconditionally in both setup
flows). The skills DB registry is reconciled by reconcile_skills_db.py.
Requires root.
"""

import os
import sys
import sqlite3
import subprocess
import glob

_CORE_INFRA = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _CORE_INFRA not in sys.path:
    sys.path.insert(0, _CORE_INFRA)
import db_connect  # noqa: E402


def sync():
    if os.geteuid() != 0:
        print("sync_skills requires root privileges.")
        sys.exit(1)

    core_infra = "/home/watchdog/core-infra"
    paths_env = "/etc/versa-agi/paths.env"
    if os.path.isfile(paths_env):
        with open(paths_env, "r") as f:
            for line in f:
                if line.startswith("VERSA_CORE_INFRA="):
                    core_infra = line.strip().split("=")[1].strip('"')

    if not os.path.isdir(core_infra):
        print(f"Core infra not found at {core_infra}")
        sys.exit(1)

    import configparser
    ini = configparser.ConfigParser()
    setup_ini_path = "/etc/versa-agi/setup.ini"
    if not os.path.isfile(setup_ini_path):
        setup_ini_path = os.path.join(os.path.dirname(core_infra), "setup.ini")

    ini.read(setup_ini_path)
    watchdog_user = ini.get("users", "watchdog", fallback="watchdog")
    coa_user = ini.get("users", "coa", fallback="coa")

    results = {
        "skills_deployed_to": [],
        "errors": []
    }

    # 1. Sync skills to active sub-agents via agictl (single rsync implementation)
    agents_db = os.getenv("AGICTL_AGENTS_DB", "/var/lib/versa-agi/agents.db")
    try:
        conn = db_connect.connect_compat(agents_db, timeout=5)
        conn.row_factory = sqlite3.Row
        active_agents = conn.execute(
            "SELECT name FROM agents WHERE status != 'removed' AND name NOT IN (?, ?)",
            (watchdog_user, coa_user)
        ).fetchall()
        conn.close()

        for agent in active_agents:
            name = agent["name"]
            try:
                res = subprocess.run(
                    ["/usr/local/bin/agictl", "agent", "deploy-skills", name],
                    capture_output=True, text=True
                )
                if res.returncode == 0:
                    results["skills_deployed_to"].append(name)
                else:
                    results["errors"].append(f"Failed to deploy skills to {name}: {res.stderr.strip()}")
            except Exception as e:
                results["errors"].append(f"Skills deploy error for {name}: {e}")

    except Exception as e:
        results["errors"].append(f"Agent DB error: {e}")

    # 2. Sync COA skills directly (merge shipped copies — never --delete; COA dir
    # also holds agent_created and override skills that must survive setup --update)
    coa_skills_dest = f"/home/{coa_user}/coa-env/.agent/skills/"
    try:
        _conn = db_connect.connect_compat(agents_db, timeout=5)
        _row = _conn.execute("SELECT workspace FROM agents WHERE name='coa'").fetchone()
        _conn.close()
        if _row and _row[0]:
            coa_skills_dest = os.path.join(_row[0], ".agent", "skills") + os.sep
    except Exception:
        pass
    coa_skills_src = os.path.join(core_infra, "skills/")
    shipped_md_names = set()
    if os.path.isdir(coa_skills_src):
        shipped_md_names = {
            os.path.basename(p)
            for p in glob.glob(os.path.join(coa_skills_src, "*.md"))
            if os.path.basename(p).lower() != "readme.md"
        }
    shipped_dir_names = set()
    if os.path.isdir(coa_skills_src):
        shipped_dir_names = {
            d for d in os.listdir(coa_skills_src)
            if os.path.isdir(os.path.join(coa_skills_src, d))
        }
    if os.path.isdir(coa_skills_src) and os.path.isdir(os.path.dirname(coa_skills_dest.rstrip(os.sep))):
        try:
            os.makedirs(coa_skills_dest, exist_ok=True)
            subprocess.run(["chown", f"{coa_user}:agi_agents", coa_skills_dest], check=False)
            subprocess.run(["chmod", "775", coa_skills_dest], check=False)

            rsync_cmd = [
                "rsync", "-a",
                "--exclude", "README.md",
                coa_skills_src, coa_skills_dest
            ]
            res = subprocess.run(rsync_cmd, capture_output=True, text=True)
            if res.returncode == 0:
                subprocess.run(["chown", f"{coa_user}:agi_agents", coa_skills_dest], check=False)
                subprocess.run(["chmod", "775", coa_skills_dest], check=False)
                for skill_file in glob.glob(os.path.join(coa_skills_dest, "*.md")):
                    if os.path.basename(skill_file) not in shipped_md_names:
                        continue  # agent_created / override — leave COA-owned perms
                    subprocess.run(["chown", f"{watchdog_user}:agi_agents", skill_file], check=False)
                    subprocess.run(["chmod", "440", skill_file], check=False)
                for item in os.listdir(coa_skills_dest):
                    item_path = os.path.join(coa_skills_dest, item)
                    if os.path.isdir(item_path) and item in shipped_dir_names:
                        subprocess.run(["chown", "-R", f"{coa_user}:agi_agents", item_path], check=False)
                        subprocess.run(["chmod", "-R", "755", item_path], check=False)

                results["skills_deployed_to"].append(coa_user)
            else:
                results["errors"].append(f"Failed to deploy skills to COA: {res.stderr.strip()}")
        except Exception as e:
            results["errors"].append(f"COA skills deploy error: {e}")

    if results["errors"]:
        print("Completed with errors:")
        for err in results["errors"]:
            print(f"  ✗ {err}")

    # ── Verbose output ──
    skills_count = len(results["skills_deployed_to"])
    print(f"\nDeployed Skills to Agents: {skills_count}")
    if skills_count > 0:
        for s in results["skills_deployed_to"]:
            print(f"  ✓ {s}")

    if results["errors"]:
        sys.exit(1)


if __name__ == "__main__":
    sync()
