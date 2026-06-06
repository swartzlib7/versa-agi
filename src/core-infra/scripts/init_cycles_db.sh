#!/bin/bash
# ─────────────────────────────────────────────────────
# Versa AGi — Agent Telemetry Database Initialization
#
# Creates the cycles.db schema.
# Location: /var/lib/versa-agi/coa/cycles.db
# Access: Watchdog (via agictl)
# ─────────────────────────────────────────────────────

set -euo pipefail

DB_PATH="${1:-/var/lib/versa-agi/coa/cycles.db}"

echo "Initializing cycles database: ${DB_PATH}"

sqlite3 "${DB_PATH}" <<'SQL'
CREATE TABLE IF NOT EXISTS cycles (
  id            TEXT PRIMARY KEY,
  started_at    DATETIME NOT NULL DEFAULT (datetime('now')),
  ended_at      DATETIME,
  exit_code     INTEGER,
  summary       TEXT,
  system_prompt TEXT,
  spawn_prompt  TEXT,
  messages_sent INTEGER DEFAULT 0,
  messages_recv INTEGER DEFAULT 0,
  tasks_done    INTEGER DEFAULT 0,
  tokens_input  INTEGER DEFAULT 0,
  tokens_output INTEGER DEFAULT 0,
  tokens_thinking INTEGER DEFAULT 0,
  tokens_cached INTEGER DEFAULT 0,
  tokens_total  INTEGER DEFAULT 0,
  json_output_path TEXT,
  session_start_ts DATETIME,
  last_awareness_ts DATETIME,
  errors        TEXT
);

CREATE TABLE IF NOT EXISTS config (
  key           TEXT PRIMARY KEY,
  value         TEXT NOT NULL,
  updated_at    DATETIME NOT NULL DEFAULT (datetime('now'))
);
SQL

# Add token tracking columns to cycles if upgrading from earlier schema
if ! sqlite3 "${DB_PATH}" "SELECT tokens_input FROM cycles LIMIT 0;" 2>/dev/null; then
  sqlite3 "${DB_PATH}" "ALTER TABLE cycles ADD COLUMN tokens_input INTEGER DEFAULT 0;" 2>/dev/null
  sqlite3 "${DB_PATH}" "ALTER TABLE cycles ADD COLUMN tokens_output INTEGER DEFAULT 0;" 2>/dev/null
  sqlite3 "${DB_PATH}" "ALTER TABLE cycles ADD COLUMN tokens_thinking INTEGER DEFAULT 0;" 2>/dev/null
  sqlite3 "${DB_PATH}" "ALTER TABLE cycles ADD COLUMN tokens_total INTEGER DEFAULT 0;" 2>/dev/null
  echo "Migration: added token tracking columns to cycles"
fi

# Add awareness enforcement gate columns if upgrading from earlier schema
sqlite3 "${DB_PATH}" "ALTER TABLE cycles ADD COLUMN session_start_ts DATETIME;" 2>/dev/null || true
sqlite3 "${DB_PATH}" "ALTER TABLE cycles ADD COLUMN last_awareness_ts DATETIME;" 2>/dev/null || true

echo "Cycles database initialized: ${DB_PATH}"
echo "Tables: cycles, config"
