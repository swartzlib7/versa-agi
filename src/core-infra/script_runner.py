"""Deterministic Script Task runner (TD-SCRIPT-001).

Executes an agent-authored ``.sh`` from the shared **AGi-Tools** repository and
captures the return code + output tail. **No LangGraph, no LLM, no agent wake** —
this is pure subprocess execution with return-code-driven alerting.

Mirrors the structure of ``harness/utility_runner.py`` (run-lock + containment)
but the payload is a script file, not a Utility Model run. Kept dependency-light
(no harness/langchain imports) so lifeline can invoke it cheaply per tick.
"""
from __future__ import annotations

import db_connect


import os
import shlex
import sqlite3
import subprocess
from datetime import datetime, timezone
from typing import Any

from model_catalog import read_setup_value

_RUN_LOCK_DIR = "/var/lib/versa-agi/script-runs"

# Default execution bounds — overridable via setup.ini [script_tasks].
_DEFAULT_MAX_RUNTIME = 600
_DEFAULT_TAIL_LINES = 20

# Exit code stamped when the runtime budget is exceeded (mirrors `timeout(1)`).
TIMEOUT_RC = 124


class ScriptRunError(Exception):
    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(message)


def resolve_agitools_path(tasks_db: str) -> str | None:
    """Resolve the physical AGi-Tools workspace path from the projects table.

    The path is **dynamic** — never hardcoded. Today it deploys to
    ``/home/coa/coa-env/workspace/AGi-Tools`` but it is always read from the DB
    so a relocated COA home still resolves correctly. Read-only connect: the
    Script Task path runs as the sub-agent (``sudo -u``); a best-effort read is
    fine because the AGi-Tools record is shared and group-readable.
    """
    if not tasks_db or not os.path.isfile(tasks_db):
        return None
    try:
        con = db_connect.connect_compat(f"file:{tasks_db}?mode=ro", uri=True, timeout=5)
        try:
            row = con.execute(
                "SELECT workspace_path FROM projects WHERE name='AGi-Tools'"
            ).fetchone()
        finally:
            con.close()
        if row and row[0]:
            return os.path.realpath(str(row[0]))
    except Exception:
        pass
    return None


def contain_script_path(script_path: str, agitools_root: str) -> str:
    """Resolve ``script_path`` against AGi-Tools and assert it stays inside.

    Rejects path traversal and symlinked escapes (realpath containment) and
    enforces the ``.sh`` policy (flat scan offers ``.sh`` only). Returns the
    resolved real path on success; raises ``ScriptRunError`` otherwise.
    """
    raw = (script_path or "").strip()
    if not raw:
        raise ScriptRunError("path_missing", "No script_path set on task")
    if "://" in raw:
        raise ScriptRunError("path_invalid", f"Remote URLs are not allowed: {raw}")
    if not agitools_root:
        raise ScriptRunError("agitools_missing", "AGi-Tools project path could not be resolved")

    root_real = os.path.realpath(agitools_root)
    candidate = raw if os.path.isabs(raw) else os.path.join(root_real, raw)
    real = os.path.realpath(candidate)

    # Containment: real must equal root or sit beneath it.
    if real != root_real and not real.startswith(root_real + os.sep):
        raise ScriptRunError("path_escape", f"Script must live inside AGi-Tools: {raw}")
    if not os.path.isfile(real):
        raise ScriptRunError("not_found", f"Script not found: {real}")
    if not real.endswith(".sh"):
        raise ScriptRunError("not_shell", "Only .sh scripts are supported")
    return real


def _acquire_run_lock(task_id: int | None, real_path: str) -> str | None:
    os.makedirs(_RUN_LOCK_DIR, exist_ok=True)
    key = f"task-{task_id}" if task_id else f"script-{abs(hash(real_path))}"
    lock_path = os.path.join(_RUN_LOCK_DIR, f"{key}.lock")
    if os.path.exists(lock_path):
        return None
    with open(lock_path, "w", encoding="utf-8") as f:
        f.write(datetime.now(timezone.utc).isoformat())
    return lock_path


def _release_run_lock(lock_path: str | None) -> None:
    if lock_path and os.path.isfile(lock_path):
        try:
            os.remove(lock_path)
        except OSError:
            pass


def _stamp_last_run() -> None:
    """Record the completion time of the most recent real Script run.

    Written by the runner (always elevated to ``watchdog`` via the ``agictl``
    wrapper, the owner of ``_RUN_LOCK_DIR``) as a single world-readable marker so
    the dashboard can show a "last run" timestamp without elevation. Best-effort.
    """
    try:
        os.makedirs(_RUN_LOCK_DIR, exist_ok=True)
        with open(os.path.join(_RUN_LOCK_DIR, ".last"), "w", encoding="utf-8") as f:
            f.write(datetime.now(timezone.utc).isoformat())
    except OSError:
        pass


def _tail(text: str | None, max_lines: int) -> str:
    if not text:
        return ""
    lines = text.rstrip("\n").splitlines()
    if len(lines) <= max_lines:
        return "\n".join(lines)
    return "\n".join(lines[-max_lines:])


def run_script_task(
    script_path: str,
    *,
    agitools_root: str,
    parameters: str | None = None,
    task_id: int | None = None,
    max_runtime_seconds: int | None = None,
    output_tail_lines: int | None = None,
) -> dict[str, Any]:
    """Execute a Script Task. Returns a result dict (never raises on non-zero rc).

    Raises ``ScriptRunError`` only for pre-flight failures (containment, lock).
    A non-zero return code or a timeout is reported in the result dict so the
    caller can drive ``done`` / ``blocked`` routing and rc-based alerting.
    """
    if max_runtime_seconds is None:
        max_runtime_seconds = int(
            read_setup_value("script_tasks", "max_runtime_seconds", str(_DEFAULT_MAX_RUNTIME))
            or _DEFAULT_MAX_RUNTIME
        )
    if output_tail_lines is None:
        output_tail_lines = int(
            read_setup_value("script_tasks", "output_tail_lines", str(_DEFAULT_TAIL_LINES))
            or _DEFAULT_TAIL_LINES
        )

    real = contain_script_path(script_path, agitools_root)

    lock = _acquire_run_lock(task_id, real)
    if lock is None:
        raise ScriptRunError("running", "A run is already in progress for this script/task")

    # Parameters are split into argv (shell=False) — no shell injection surface.
    try:
        arg_list = shlex.split(parameters or "")
    except ValueError as e:
        _release_run_lock(lock)
        raise ScriptRunError("bad_params", f"Invalid parameters: {e}") from e

    started = datetime.now(timezone.utc)
    timed_out = False
    try:
        proc = subprocess.run(
            ["/bin/bash", real, *arg_list],
            cwd=agitools_root,
            capture_output=True,
            text=True,
            timeout=max_runtime_seconds,
        )
        rc = proc.returncode
        stdout, stderr = proc.stdout, proc.stderr
    except subprocess.TimeoutExpired as e:
        timed_out = True
        rc = TIMEOUT_RC
        stdout = e.stdout.decode() if isinstance(e.stdout, bytes) else (e.stdout or "")
        stderr = e.stderr.decode() if isinstance(e.stderr, bytes) else (e.stderr or "")
    finally:
        _stamp_last_run()
        _release_run_lock(lock)

    duration = (datetime.now(timezone.utc) - started).total_seconds()
    return {
        "success": rc == 0,
        "returncode": rc,
        "script_path": real,
        "timed_out": timed_out,
        "duration_seconds": round(duration, 3),
        "stdout_tail": _tail(stdout, output_tail_lines),
        "stderr_tail": _tail(stderr, output_tail_lines),
        "task_id": task_id,
        "ran_at": started.strftime("%Y-%m-%d %H:%M:%S"),
    }
