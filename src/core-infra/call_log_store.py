"""Call log (``messages.db`` table ``calls``) — one row per COA call-tool attempt.

Written only by the hidden ``agictl message call-bridge`` plumbing that harness
call mode drives; read by ``agictl message calls list|show`` and agitop.
Contract: ``design/spec/state/state_live_voice_call.md`` §1.8.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any

import db_connect

CALL_STATUSES = (
    "calling",
    "connecting",
    "live",
    "ended",
    "offline",
    "missed",
    "declined",
    "failed",
)
OPEN_STATUSES = ("calling", "connecting", "live")

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS calls (
  call_id            TEXT PRIMARY KEY,
  agent_name         TEXT NOT NULL,
  pu_uid             TEXT,
  channel_id         TEXT,
  cycle_id           TEXT,
  reason             TEXT,
  status             TEXT NOT NULL CHECK(status IN ('calling','connecting','live','ended','offline','missed','declined','failed')),
  close_reason       TEXT,
  openai_session_id  TEXT,
  voice_model        TEXT,
  call_model         TEXT,
  created_at         TEXT NOT NULL,
  joined_at          TEXT,
  ended_at           TEXT,
  voice_seconds      INTEGER,
  transcript_json    TEXT,
  delegations_json   TEXT,
  summary            TEXT
);
CREATE INDEX IF NOT EXISTS idx_calls_agent_created ON calls(agent_name, created_at);
CREATE INDEX IF NOT EXISTS idx_calls_status ON calls(status);
"""

_UPDATABLE = {
    "status",
    "close_reason",
    "openai_session_id",
    "channel_id",
    "joined_at",
    "ended_at",
    "voice_seconds",
    "transcript_json",
    "delegations_json",
    "summary",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# Columns added after the first release — ADD COLUMN on older tables.
_ADDED_COLUMNS = (("summary", "TEXT"),)


def _connect(db_path: str) -> sqlite3.Connection:
    conn = db_connect.connect_compat(db_path, timeout=5)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA_SQL)
    have = {row[1] for row in conn.execute("PRAGMA table_info(calls)")}
    for name, sql_type in _ADDED_COLUMNS:
        if name not in have:
            conn.execute(f"ALTER TABLE calls ADD COLUMN {name} {sql_type}")
    conn.commit()
    return conn


def insert_attempt(
    db_path: str,
    *,
    call_id: str,
    agent_name: str,
    status: str,
    reason: str,
    pu_uid: str = "",
    channel_id: str = "",
    cycle_id: str = "",
    call_model: str = "",
    voice_model: str = "",
    close_reason: str | None = None,
) -> None:
    if status not in CALL_STATUSES:
        raise ValueError(f"invalid call status '{status}'")
    now = utc_now()
    ended_at = now if status not in OPEN_STATUSES else None
    conn = _connect(db_path)
    try:
        conn.execute(
            "INSERT INTO calls (call_id, agent_name, pu_uid, channel_id, cycle_id, reason, status, "
            "close_reason, voice_model, call_model, created_at, ended_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                call_id, agent_name, pu_uid or None, channel_id or None, cycle_id or None,
                reason, status, close_reason, voice_model or None, call_model or None,
                now, ended_at,
            ),
        )
        conn.commit()
    finally:
        conn.close()


def update_call(db_path: str, call_id: str, **fields: Any) -> bool:
    cols = {k: v for k, v in fields.items() if k in _UPDATABLE and v is not None}
    if not cols:
        return False
    if "status" in cols and cols["status"] not in CALL_STATUSES:
        raise ValueError(f"invalid call status '{cols['status']}'")
    for key in ("transcript_json", "delegations_json"):
        if key in cols and not isinstance(cols[key], str):
            cols[key] = json.dumps(cols[key], ensure_ascii=False)
    assignments = ", ".join(f"{k} = ?" for k in cols)
    conn = _connect(db_path)
    try:
        cur = conn.execute(
            f"UPDATE calls SET {assignments} WHERE call_id = ?",
            (*cols.values(), call_id),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def expire_stale_open_calls(db_path: str, agent_name: str, older_than_seconds: int) -> int:
    """Rows left open by a harness that died mid-call → ``failed`` / ``unconfirmed``."""
    cutoff = (datetime.now(timezone.utc) - timedelta(seconds=older_than_seconds)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    conn = _connect(db_path)
    try:
        cur = conn.execute(
            "UPDATE calls SET status = 'failed', close_reason = COALESCE(close_reason, 'unconfirmed'), "
            "ended_at = COALESCE(ended_at, ?) "
            f"WHERE agent_name = ? AND status IN ({','.join('?' for _ in OPEN_STATUSES)}) "
            "AND created_at < ?",
            (utc_now(), agent_name, *OPEN_STATUSES, cutoff),
        )
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


def has_open_call(db_path: str, agent_name: str) -> bool:
    conn = _connect(db_path)
    try:
        row = conn.execute(
            f"SELECT 1 FROM calls WHERE agent_name = ? AND status IN ({','.join('?' for _ in OPEN_STATUSES)}) LIMIT 1",
            (agent_name, *OPEN_STATUSES),
        ).fetchone()
        return row is not None
    finally:
        conn.close()


_LIST_COLUMNS = (
    "call_id, agent_name, status, close_reason, reason, created_at, joined_at, "
    "ended_at, voice_seconds, call_model, cycle_id, summary"
)


def list_calls(db_path: str, agent_name: str | None = None, limit: int = 20) -> list[dict]:
    conn = _connect(db_path)
    try:
        if agent_name:
            rows = conn.execute(
                f"SELECT {_LIST_COLUMNS} FROM calls WHERE agent_name = ? ORDER BY created_at DESC LIMIT ?",
                (agent_name, int(limit)),
            ).fetchall()
        else:
            rows = conn.execute(
                f"SELECT {_LIST_COLUMNS} FROM calls ORDER BY created_at DESC LIMIT ?",
                (int(limit),),
            ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_call(db_path: str, call_id: str) -> dict | None:
    conn = _connect(db_path)
    try:
        row = conn.execute("SELECT * FROM calls WHERE call_id = ?", (call_id,)).fetchone()
    finally:
        conn.close()
    if not row:
        return None
    out = dict(row)
    for key in ("transcript_json", "delegations_json"):
        raw = out.pop(key, None)
        try:
            out[key.replace("_json", "")] = json.loads(raw) if raw else []
        except json.JSONDecodeError:
            out[key.replace("_json", "")] = []
    return out


def transcript_text(segments: list[dict], pu_label: str = "PU", agent_label: str = "COA") -> str:
    """Speaker-labelled transcript lines for ``calls show`` and the call-ended digest."""
    lines = []
    for seg in segments or []:
        text = str(seg.get("text") or "").strip()
        if not text:
            continue
        who = pu_label if seg.get("speaker") == "pu" else agent_label
        lines.append(f"{who}: {text}")
    return "\n".join(lines)
