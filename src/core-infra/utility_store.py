"""Persistence helpers for utility_models and utility task fields."""

from __future__ import annotations

import json
import os
import sqlite3
from typing import Any

VALID_OUTPUT_MODALITIES = ("text", "image", "audio", "video")


def _agents_db() -> str:
    return os.environ.get("AGICTL_AGENTS_DB", "/var/lib/versa-agi/agents.db")


def _tasks_db() -> str:
    return os.environ.get("AGICTL_TASKS_DB", "/var/lib/versa-agi/coa/tasks.db")


def _row_to_um(row: sqlite3.Row) -> dict[str, Any]:
    d = dict(row)
    d["enabled"] = bool(d.get("enabled"))
    return d


def list_utility_models(*, enabled_only: bool = False) -> list[dict[str, Any]]:
    db = _agents_db()
    if not os.path.isfile(db):
        return []
    try:
        conn = sqlite3.connect(db, timeout=5)
        conn.row_factory = sqlite3.Row
        q = "SELECT * FROM utility_models"
        if enabled_only:
            q += " WHERE enabled=1"
        q += " ORDER BY id ASC"
        rows = [_row_to_um(r) for r in conn.execute(q).fetchall()]
        conn.close()
        return rows
    except sqlite3.OperationalError:
        return []


def get_utility_model(um_id: str) -> dict[str, Any] | None:
    db = _agents_db()
    if not os.path.isfile(db):
        return None
    conn = sqlite3.connect(db, timeout=5)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM utility_models WHERE id=?", (um_id.strip(),)).fetchone()
    conn.close()
    return _row_to_um(row) if row else None


def add_utility_model(
    *,
    um_id: str,
    label: str,
    catalog_model: str,
    system_prompt: str,
    output_modality: str,
    output_path: str,
    run_as_agent: str = "coa",
    config_json: str | None = None,
    enabled: bool = True,
) -> None:
    om = (output_modality or "text").strip().lower()
    if om not in VALID_OUTPUT_MODALITIES:
        raise ValueError(f"Invalid output_modality: {output_modality}")
    db = _agents_db()
    conn = sqlite3.connect(db, timeout=5)
    conn.execute(
        """INSERT INTO utility_models
           (id, label, catalog_model, system_prompt, output_modality, output_path,
            run_as_agent, config_json, enabled)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            um_id.strip(),
            label.strip(),
            catalog_model.strip(),
            system_prompt,
            om,
            (output_path or "").strip(),
            (run_as_agent or "coa").strip(),
            config_json,
            1 if enabled else 0,
        ),
    )
    conn.commit()
    conn.close()


def update_utility_model(um_id: str, fields: dict[str, Any]) -> bool:
    allowed = {
        "label", "catalog_model", "system_prompt", "output_modality",
        "output_path", "run_as_agent", "config_json", "enabled",
    }
    updates, params = [], []
    for k, v in fields.items():
        if k not in allowed or v is None:
            continue
        if k == "enabled":
            v = 1 if v else 0
        updates.append(f"{k}=?")
        params.append(v)
    if not updates:
        return False
    updates.append("updated_at=datetime('now')")
    params.append(um_id.strip())
    db = _agents_db()
    conn = sqlite3.connect(db, timeout=5)
    cur = conn.execute(
        f"UPDATE utility_models SET {', '.join(updates)} WHERE id=?",
        params,
    )
    conn.commit()
    conn.close()
    return cur.rowcount > 0


def remove_utility_model(um_id: str) -> bool:
    db = _agents_db()
    conn = sqlite3.connect(db, timeout=5)
    cur = conn.execute("DELETE FROM utility_models WHERE id=?", (um_id.strip(),))
    conn.commit()
    conn.close()
    return cur.rowcount > 0


def get_task_utility_fields(task_id: int) -> dict[str, Any] | None:
    db = _tasks_db()
    if not os.path.isfile(db):
        return None
    conn = sqlite3.connect(db, timeout=5)
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        """SELECT id, title, assigned_to, status, due_date, task_kind,
                  utility_model_id, utility_input_files, utility_output_override,
                  utility_start_alert, utility_stop_alert, utility_spawn_agent
           FROM tasks WHERE id=?""",
        (task_id,),
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def list_due_utility_tasks(assignee: str) -> list[dict[str, Any]]:
    db = _tasks_db()
    if not os.path.isfile(db):
        return []
    conn = sqlite3.connect(db, timeout=5)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """SELECT id, title, assigned_to, utility_model_id, utility_input_files,
                  utility_output_override, utility_start_alert, utility_stop_alert,
                  utility_spawn_agent
           FROM tasks
           WHERE task_kind='utility'
             AND assigned_to=?
             AND status='planned'
             AND due_date IS NOT NULL
             AND due_date <= datetime('now')
             AND utility_model_id IS NOT NULL
           ORDER BY due_date ASC""",
        (assignee.strip(),),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def freeze_active_utility_tasks() -> int:
    """Freeze non-terminal utility tasks (e.g. when [utility_models] disabled)."""
    db = _tasks_db()
    if not os.path.isfile(db):
        return 0
    conn = sqlite3.connect(db, timeout=5)
    cur = conn.execute(
        """UPDATE tasks
           SET pre_freeze_status = status, status = 'frozen', updated_at = datetime('now')
           WHERE task_kind = 'utility'
             AND utility_model_id IS NOT NULL
             AND TRIM(utility_model_id) != ''
             AND status NOT IN ('done', 'cancelled', 'frozen')"""
    )
    count = cur.rowcount
    conn.commit()
    conn.close()
    return count


# ─── TD-SCRIPT-001: Script Task persistence helpers ───
# Script Tasks mirror Utility Tasks but the payload is a .sh file in AGi-Tools.
# They reuse the utility alert columns (start/stop) — Utility and Script modes
# are mutually exclusive on any task, so the columns never collide. The agitop
# modal reads script_* fields straight off the task row (SELECT *), so only the
# lifeline due-selection helper lives here.


def list_due_script_tasks(assignee: str) -> list[dict[str, Any]]:
    """Due Script Tasks for an agent — same due gate as utility, task_kind='script'."""
    db = _tasks_db()
    if not os.path.isfile(db):
        return []
    conn = sqlite3.connect(db, timeout=5)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """SELECT id, title, assigned_to, script_path, script_parameters,
                  script_interval_seconds, utility_start_alert, utility_stop_alert,
                  utility_spawn_agent
           FROM tasks
           WHERE task_kind='script'
             AND assigned_to=?
             AND status='planned'
             AND due_date IS NOT NULL
             AND due_date <= datetime('now')
             AND script_path IS NOT NULL
             AND TRIM(script_path) != ''
           ORDER BY due_date ASC""",
        (assignee.strip(),),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def parse_input_files_json(raw: str | None) -> list[str]:
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if isinstance(data, list):
        return [str(x).strip() for x in data if str(x).strip()]
    return []
