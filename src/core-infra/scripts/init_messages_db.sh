#!/bin/bash
# ─────────────────────────────────────────────────────
# Versa AGi — System Communications Database Initialization
#
# Creates the messages.db schema.
# Location: /var/lib/versa-agi/messages.db
# Access: Watchdog (Owner) / COA (Group Read/Write)
# ─────────────────────────────────────────────────────

set -euo pipefail

DB_PATH="${1:-/var/lib/versa-agi/messages.db}"

echo "Initializing messages database: ${DB_PATH}"

sqlite3 "${DB_PATH}" <<'SQL'
CREATE TABLE IF NOT EXISTS messages (
  id                INTEGER PRIMARY KEY AUTOINCREMENT,
  direction         TEXT CHECK(direction IN ('sent', 'received')),
  from_user_id      TEXT,
  to_user_id        TEXT,
  display_name      TEXT,
  message_id        TEXT,
  channel_id        TEXT,
  text              TEXT,
  original_text     TEXT,
  mode              TEXT,
  status            TEXT CHECK(status IN ('pending', 'queued', 'sent', 'delivered', 'error', 'unprocessed', 'processed')),
  error_message     TEXT,
  raw_payload       TEXT,
  has_attachments   BOOLEAN DEFAULT 0,
  attachment_path   TEXT,
  channel           TEXT DEFAULT 'vv' CHECK(channel IN ('vv', 'internal')),
  created_at        DATETIME NOT NULL DEFAULT (datetime('now')),
  cycle_id          TEXT
);

CREATE INDEX IF NOT EXISTS idx_messages_direction ON messages(direction);
CREATE INDEX IF NOT EXISTS idx_messages_status ON messages(status);
CREATE INDEX IF NOT EXISTS idx_messages_users ON messages(from_user_id, to_user_id);
CREATE INDEX IF NOT EXISTS idx_messages_channel ON messages(channel);

CREATE TABLE IF NOT EXISTS deleted_message_ids (
  message_id  TEXT PRIMARY KEY,
  deleted_at  DATETIME NOT NULL DEFAULT (datetime('now'))
);

-- Live voice call log (COA → Primary User). Same schema as call_log_store.py.
CREATE TABLE IF NOT EXISTS calls (
  call_id            TEXT PRIMARY KEY,
  agent_name         TEXT NOT NULL,
  pu_uid             TEXT,
  callee_uid         TEXT,
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
SQL

echo "Messages database initialized: ${DB_PATH}"
echo "Tables: messages, deleted_message_ids, calls"
