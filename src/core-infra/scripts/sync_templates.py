#!/usr/bin/env python3
"""
sync_templates.py

Force synchronize shipped poise and skill templates to all active agents.
Executed by setup.sh during the --update flow.
"""

import os
import sys
import sqlite3
import shutil
import subprocess
import glob
import json

def sync():
    if os.geteuid() != 0:
        print("sync_templates requires root privileges.")
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
        "poise_updated": [],
        "skills_deployed_to": [],
        "errors": []
    }

    # 1. Sync Core Poise Templates
    poise_dir = "/etc/versa-agi/poise"
    config_dir = os.path.join(core_infra, "config")
    
    core_poises = {
        "coa_poise.md": f"{coa_user}.md",
        "task_protocol.md": "task_protocol.md",
        "watchdog_poise.md": f"{watchdog_user}.md"
    }
    for src_name, dest_name in core_poises.items():
        src_path = os.path.join(config_dir, src_name)
        dest_path = os.path.join(poise_dir, dest_name)
        if os.path.isfile(src_path):
            try:
                shutil.copy2(src_path, dest_path)
                subprocess.run(["chown", f"{watchdog_user}:{watchdog_user}", dest_path], check=False)
                subprocess.run(["chmod", "640", dest_path], check=False)
                results["poise_updated"].append(dest_name)
            except Exception as e:
                results["errors"].append(f"Failed to sync {src_name}: {e}")

    # 2. Sync Sub-Agent Poise Templates & Skills
    agents_db = os.getenv("AGICTL_AGENTS_DB", "/var/lib/versa-agi/agents.db")
    ROLE_LABELS = {
        "coa": "Chief Orchestrator Agent", "watchdog": "System Watchdog",
        "pa": "Personal Assistant", "ba": "Business Analyst",
        "sa": "Technical Architect", "dev": "Developer Agent",
        "devops": "DevOps Agent", "qa": "QA Agent",
        "mm": "Marketing Manager", "sr": "Subject Researcher",
        "sysmon": "System Monitor", "custom": "Custom Agent",
    }
    
    try:
        conn = sqlite3.connect(agents_db, timeout=5)
        conn.row_factory = sqlite3.Row
        active_agents = conn.execute(
            "SELECT name, role FROM agents WHERE status != 'removed' AND name NOT IN (?, ?)",
            (watchdog_user, coa_user)
        ).fetchall()
        conn.close()

        for agent in active_agents:
            name = agent["name"]
            role_label = agent["role"] or "Custom Agent"
            reverse_roles = {v: k for k, v in ROLE_LABELS.items()}
            role_id = reverse_roles.get(role_label, "custom")
            
            poise_source = os.path.join(config_dir, "roles", role_id, "poise.md")
            if not os.path.isfile(poise_source):
                poise_source = os.path.join(config_dir, "roles", f"poise-{role_id}.md")
            
            if os.path.isfile(poise_source):
                dest_path = os.path.join(poise_dir, f"{name}.md")
                try:
                    shutil.copy2(poise_source, dest_path)
                    subprocess.run(["chown", f"{watchdog_user}:{watchdog_user}", dest_path], check=False)
                    subprocess.run(["chmod", "640", dest_path], check=False)
                    results["poise_updated"].append(f"{name}.md")
                except Exception as e:
                    results["errors"].append(f"Failed to sync poise for {name}: {e}")

            # Sync Skills via agictl (which relies on existing tooling)
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

    # 3. Sync COA Skills directly
    coa_skills_dest = f"/home/{coa_user}/coa-env/.agent/skills/"
    coa_skills_src = os.path.join(core_infra, "skills/")
    if os.path.isdir(coa_skills_src) and os.path.isdir(os.path.dirname(coa_skills_dest)):
        try:
            os.makedirs(coa_skills_dest, exist_ok=True)
            subprocess.run(["chown", f"{coa_user}:agi_agents", coa_skills_dest], check=False)
            subprocess.run(["chmod", "775", coa_skills_dest], check=False)
            
            rsync_cmd = [
                "rsync", "-a", "--delete",
                "--exclude", "README.md",
                coa_skills_src, coa_skills_dest
            ]
            res = subprocess.run(rsync_cmd, capture_output=True, text=True)
            if res.returncode == 0:
                for skill_file in glob.glob(os.path.join(coa_skills_dest, "*.md")):
                    subprocess.run(["chown", f"{watchdog_user}:agi_agents", skill_file], check=False)
                    subprocess.run(["chmod", "440", skill_file], check=False)
                for item in os.listdir(coa_skills_dest):
                    item_path = os.path.join(coa_skills_dest, item)
                    if os.path.isdir(item_path):
                        subprocess.run(["chown", "-R", f"{coa_user}:agi_agents", item_path], check=False)
                        subprocess.run(["chmod", "-R", "755", item_path], check=False)
                        
                results["skills_deployed_to"].append(coa_user)
            else:
                results["errors"].append(f"Failed to deploy skills to COA: {res.stderr.strip()}")
        except Exception as e:
            results["errors"].append(f"COA skills deploy error: {e}")

    # 4. Sync Database (Register new shipped skills, remove deleted shipped skills)
    db_inserted = 0
    db_deleted = 0
    try:
        conn = sqlite3.connect(agents_db, timeout=5)
        
        # Get current shipped skills from DB
        db_skills = conn.execute("SELECT name FROM skills WHERE origin='shipped'").fetchall()
        db_skill_names = {r[0] for r in db_skills}
        
        # Get current shipped skills from filesystem
        fs_skills = set()
        if os.path.isdir(coa_skills_src):
            for skill_file in glob.glob(os.path.join(coa_skills_src, "*.md")):
                basename = os.path.basename(skill_file)
                if basename.lower() == "readme.md":
                    continue
                skill_name = basename[:-3]
                fs_skills.add(skill_name)
                
                # If not in DB, insert it
                if skill_name not in db_skill_names:
                    description = ""
                    try:
                        with open(skill_file, "r") as f:
                            first_line = f.readline().strip()
                            if first_line.startswith("# "):
                                description = first_line[2:]
                    except Exception:
                        pass
                    
                    asset_dir = os.path.join(coa_skills_src, skill_name)
                    has_assets = 1 if os.path.isdir(asset_dir) else 0
                    
                    conn.execute(
                        "INSERT INTO skills (name, type, origin, has_assets, description, status) "
                        "VALUES (?, 'system', 'shipped', ?, ?, 'synced')",
                        (skill_name, has_assets, description)
                    )
                    db_inserted += 1
                    
        # Remove skills from DB that no longer exist on filesystem
        orphans = db_skill_names - fs_skills
        for orphan in orphans:
            conn.execute("DELETE FROM skills WHERE name=? AND origin='shipped'", (orphan,))
            db_deleted += 1
            
        conn.commit()
        conn.close()
    except Exception as e:
        results["errors"].append(f"DB skills sync error: {e}")

    if results["errors"]:
        print("Completed with errors:")
        for err in results["errors"]:
            print(f" - {err}")
    
    print(f"Synced Poise Templates: {len(results['poise_updated'])}")
    print(f"Deployed Skills to Agents: {len(results['skills_deployed_to'])}")
    print(f"Database Skills Synced: +{db_inserted} / -{db_deleted}")

if __name__ == "__main__":
    sync()
