"""Watchdog-side reads for one spawned agent's mail and registry.

The harness runs as the agent user and must not open messages.db or tasks.db.
agictl calls these after the wrapper has elevated to watchdog.
"""

from __future__ import annotations

import os
import sqlite3

import db_connect

_MESSAGE_COLS = (
    "message_id, from_user_id, to_user_id, display_name, text, original_text, "
    "created_at, raw_payload, status, direction, has_attachments, attachment_path"
)
_MESSAGE_COLS_NO_PAYLOAD = (
    "message_id, from_user_id, to_user_id, display_name, text, original_text, "
    "created_at, status, direction, has_attachments, attachment_path"
)


def ids_for_spawned_agent(agent_name: str, config_agent: str, sub_account: str) -> list[str]:
    """Identity union for one agent.

    The VersaVoice sub-account is included only when the loaded config belongs
    to that same agent. A coa config must not attach coa's UID to another agent.
    """
    name = (agent_name or "").strip()
    ids: list[str] = []
    if name and (config_agent or "").strip().lower() == name.lower():
        sub = (sub_account or "").strip()
        if sub and sub not in ids:
            ids.append(sub)
    if name and name not in ids:
        ids.append(name)
    return ids


def received_unread(db_path: str, ids: list[str]) -> list[dict]:
    """Unprocessed received rows addressed to these ids, oldest first."""
    wanted = [i for i in ids if i]
    if not db_path or not wanted or not os.path.isfile(db_path):
        return []
    placeholders = ",".join("?" * len(wanted))
    where = (
        "WHERE status='unprocessed' AND direction='received' "
        f"AND to_user_id IN ({placeholders}) ORDER BY created_at ASC"
    )
    conn = db_connect.connect(db_path, timeout=5, row_factory=True)
    try:
        try:
            rows = conn.execute(f"SELECT {_MESSAGE_COLS} FROM messages {where}", tuple(wanted)).fetchall()
        except sqlite3.OperationalError:
            rows = conn.execute(
                f"SELECT {_MESSAGE_COLS_NO_PAYLOAD} FROM messages {where}", tuple(wanted)
            ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def registry_for_agent(db_path: str, agent_name: str) -> dict:
    """Projects and open tasks for this agent. COA sees the fleet."""
    empty = {"projects": [], "tasks": []}
    name = (agent_name or "").strip()
    if not db_path or not name or not os.path.isfile(db_path):
        return empty
    is_coa = name.lower() == "coa"
    conn = db_connect.connect(db_path, timeout=5, row_factory=True)
    try:
        if is_coa:
            projects = conn.execute(
                "SELECT id, name, COALESCE(description, '') AS description FROM projects "
                "WHERE status NOT IN ('archived') ORDER BY id"
            ).fetchall()
            tasks = conn.execute(
                "SELECT id, title, COALESCE(description, '') AS description, project_id, status, assigned_to "
                "FROM tasks WHERE status NOT IN ('done', 'cancelled', 'frozen') ORDER BY id"
            ).fetchall()
        else:
            projects = conn.execute(
                "SELECT DISTINCT p.id, p.name, COALESCE(p.description, '') AS description "
                "FROM projects p "
                "LEFT JOIN project_members pm ON pm.project_id = p.id "
                "  AND pm.member_type='agent' AND pm.member_id=? "
                "LEFT JOIN tasks t ON t.project_id = p.id AND t.assigned_to=? "
                "  AND t.status NOT IN ('done', 'cancelled', 'frozen') "
                "WHERE p.status NOT IN ('archived') AND (pm.member_id IS NOT NULL OR t.id IS NOT NULL) "
                "ORDER BY p.id",
                (name, name),
            ).fetchall()
            tasks = conn.execute(
                "SELECT id, title, COALESCE(description, '') AS description, project_id, status, assigned_to "
                "FROM tasks WHERE assigned_to=? AND status NOT IN ('done', 'cancelled', 'frozen') "
                "ORDER BY id",
                (name,),
            ).fetchall()
        return {
            "projects": [dict(row) for row in projects],
            "tasks": [dict(row) for row in tasks],
        }
    finally:
        conn.close()
