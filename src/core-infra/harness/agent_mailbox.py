"""Read one spawned agent's mail and registry through agictl.

The harness process is the agent user. messages.db and tasks.db are
660 watchdog:coa, so a direct sqlite open fails for a sub-agent. agictl
elevates to watchdog. A failed read returns nothing and does not raise.
"""

from __future__ import annotations

import json
import os
import subprocess

def identity_ids(agent_name: str, config_path: str | None = None) -> list[str]:
    """Sub-account UID plus the internal agent name. Either may be to_user_id."""
    ids: list[str] = []
    path = config_path if config_path is not None else os.environ.get("AGICTL_CONFIG", "")
    if path and os.path.isfile(path):
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
            sub = ((data.get("versavoice") or {}).get("sub_account_id") or "").strip()
            if sub:
                ids.append(sub)
        except (OSError, json.JSONDecodeError, TypeError):
            pass
    name = (agent_name or "").strip()
    if name and name not in ids:
        ids.append(name)
    return ids


def rows_for_agent(rows: list, ids: list[str]) -> list[dict]:
    """Keep unprocessed received rows addressed to this agent's ids."""
    wanted = {i for i in ids if i}
    kept: list[dict] = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        if (row.get("status") or "") != "unprocessed":
            continue
        if (row.get("direction") or "") != "received":
            continue
        if (row.get("to_user_id") or "") not in wanted:
            continue
        kept.append(row)
    return kept


def _run_json(argv: list[str], runner=None):
    """Return parsed JSON, or None when the command fails."""
    try:
        if runner is not None:
            raw = runner(argv)
        else:
            proc = subprocess.run(argv, capture_output=True, text=True, timeout=20)
            if proc.returncode != 0:
                return None
            raw = proc.stdout
        if raw is None:
            return None
        return json.loads(raw or "null")
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError, TypeError, ValueError):
        return None


def load_received_unread(agent_name: str, runner=None) -> list[dict] | None:
    """Unread received rows for this agent. None means the read failed."""
    name = (agent_name or "").strip()
    if not name:
        return []
    data = _run_json(
        ["agictl", "message", "received-unread", "--agent", name],
        runner=runner,
    )
    if data is None:
        return None
    if not isinstance(data, list):
        return None
    return [row for row in data if isinstance(row, dict)]


def load_registry(agent_name: str, runner=None) -> dict | None:
    """Project/task registry for this agent. None means the read failed."""
    name = (agent_name or "").strip()
    if not name:
        return {"projects": [], "tasks": []}
    data = _run_json(
        ["agictl", "task", "triage-registry", "--agent", name],
        runner=runner,
    )
    if not isinstance(data, dict) or "projects" not in data or "tasks" not in data:
        return None
    return data
