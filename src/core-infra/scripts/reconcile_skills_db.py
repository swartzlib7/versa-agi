#!/usr/bin/env python3
"""Idempotent skills table reconcile from shipped skill files."""

from __future__ import annotations

import configparser
import glob
import os
import sqlite3
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
CORE_INFRA = SCRIPT_DIR.parent
SKILLS_SRC = CORE_INFRA / "skills"
SCOPE_INI = CORE_INFRA / "config" / "skills_scope.ini"
DEFAULT_DB = "/var/lib/versa-agi/agents.db"


def _load_scopes() -> dict[str, str]:
    scopes: dict[str, str] = {}
    if not SCOPE_INI.is_file():
        return scopes
    cfg = configparser.ConfigParser()
    cfg.read(SCOPE_INI)
    if cfg.has_section("scope"):
        for key, val in cfg.items("scope"):
            scopes[key.strip()] = val.strip() or "all"
    return scopes


def reconcile(db_path: str = DEFAULT_DB) -> tuple[int, int, int]:
    if not os.path.isfile(db_path):
        print(f"ERROR: agents.db not found at {db_path}", file=sys.stderr)
        return 0, 0, 1

    scopes = _load_scopes()
    inserted = 0
    updated = 0
    deleted = 0

    conn = sqlite3.connect(db_path, timeout=10)
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS skills ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "name TEXT NOT NULL UNIQUE, "
            "type TEXT NOT NULL DEFAULT 'system', "
            "origin TEXT NOT NULL DEFAULT 'shipped', "
            "has_assets BOOLEAN DEFAULT 0, "
            "description TEXT, "
            "status TEXT NOT NULL DEFAULT 'synced', "
            "scope TEXT NOT NULL DEFAULT 'all', "
            "created_at DATETIME NOT NULL DEFAULT (datetime('now')), "
            "updated_at DATETIME NOT NULL DEFAULT (datetime('now'))"
            ")"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_skills_status ON skills(status)"
        )
        conn.execute(
            "ALTER TABLE skills ADD COLUMN scope TEXT NOT NULL DEFAULT 'all'"
        )
    except sqlite3.OperationalError:
        pass

    db_names = {
        row[0]
        for row in conn.execute("SELECT name FROM skills WHERE origin='shipped'").fetchall()
    }
    fs_names: set[str] = set()

    if SKILLS_SRC.is_dir():
        for skill_file in sorted(glob.glob(str(SKILLS_SRC / "*.md"))):
            basename = os.path.basename(skill_file)
            if basename.lower() == "readme.md":
                continue
            skill_name = basename[:-3]
            fs_names.add(skill_name)

            description = ""
            try:
                with open(skill_file, encoding="utf-8") as f:
                    first_line = f.readline().strip()
                    if first_line.startswith("# "):
                        description = first_line[2:]
            except OSError:
                pass

            asset_dir = SKILLS_SRC / skill_name
            has_assets = 1 if asset_dir.is_dir() else 0
            scope = scopes.get(skill_name, "all")

            if skill_name in db_names:
                conn.execute(
                    "UPDATE skills SET description=?, scope=?, has_assets=?, "
                    "updated_at=datetime('now') WHERE name=? AND origin='shipped'",
                    (description, scope, has_assets, skill_name),
                )
                updated += 1
            else:
                conn.execute(
                    "INSERT INTO skills (name, type, origin, has_assets, description, status, scope) "
                    "VALUES (?, 'system', 'shipped', ?, ?, 'ready', ?)",
                    (skill_name, has_assets, description, scope),
                )
                inserted += 1

    orphans = db_names - fs_names
    for orphan in orphans:
        conn.execute(
            "DELETE FROM skills WHERE name=? AND origin='shipped'", (orphan,)
        )
        deleted += 1

    conn.commit()
    conn.close()
    return inserted, updated, deleted


def main() -> int:
    db_path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_DB
    inserted, updated, deleted = reconcile(db_path)
    print(
        f"Skills reconcile: {inserted} inserted, {updated} updated, {deleted} removed"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
