#!/bin/bash
# ─────────────────────────────────────────────────────
# Versa AGi — Global Registry Database Initialization
#
# Creates the shared agents.db schema for agent management.
# Location: /var/lib/versa-agi/agents.db
# Access: Lifeline (watchdog) + all agents (via agictl)
# ─────────────────────────────────────────────────────

set -euo pipefail

DB_PATH="${1:-/var/lib/versa-agi/agents.db}"

echo "Initializing agent registry database: ${DB_PATH}"

sqlite3 "${DB_PATH}" <<'SQL'
CREATE TABLE IF NOT EXISTS agents (
  id                INTEGER PRIMARY KEY AUTOINCREMENT,
  name              TEXT NOT NULL UNIQUE,
  os_user           TEXT NOT NULL,
  workspace         TEXT NOT NULL,
  role              TEXT,
  status            TEXT,
  status_message    TEXT,
  model             TEXT,
  timeout_minutes   INTEGER DEFAULT 60,
  runaway_threshold INTEGER DEFAULT 300,
  runaway_size_threshold INTEGER DEFAULT 2048,
  inactive          BOOLEAN DEFAULT 0,
  protected         BOOLEAN DEFAULT 0,
  can_message_connections BOOLEAN DEFAULT 0,
  context_injection_mode TEXT DEFAULT 'relevant',
  token_budget          INTEGER DEFAULT 0,
  max_session_turns     INTEGER DEFAULT 50,
  tool_output_token_budget INTEGER DEFAULT 1500,
  triage_model      TEXT,
  session_retention_enabled BOOLEAN DEFAULT 1,
  session_retention_max_age TEXT DEFAULT '14d',
  session_retention_max_count INTEGER DEFAULT 45,
  num_ctx           INTEGER DEFAULT 0,
  conversation_depth INTEGER DEFAULT 10,
  resume_enabled    BOOLEAN DEFAULT 1,
  resume_max_messages INTEGER DEFAULT 0,
  requested_by      TEXT,
  requested_by_name TEXT,
  created_at        DATETIME NOT NULL DEFAULT (datetime('now')),
  updated_at        DATETIME NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_agents_inactive ON agents(inactive);
CREATE INDEX IF NOT EXISTS idx_agents_name ON agents(name);

-- Active agents (used by Lifeline for spawning)
CREATE VIEW IF NOT EXISTS v_active_agents AS
SELECT name, os_user, workspace, model, triage_model, role, timeout_minutes, runaway_threshold, runaway_size_threshold, context_injection_mode, token_budget, max_session_turns, session_retention_enabled, anchor_style, num_ctx, conversation_depth, resume_enabled, resume_max_messages
FROM agents
WHERE inactive = 0
ORDER BY name ASC;

-- All agents with status summary
CREATE VIEW IF NOT EXISTS v_agent_registry AS
SELECT name, os_user, workspace, timeout_minutes, runaway_threshold, runaway_size_threshold, inactive, protected, can_message_connections, model, triage_model, role,
       context_injection_mode, token_budget, max_session_turns, tool_output_token_budget,
       session_retention_enabled, session_retention_max_age, session_retention_max_count,
       anchor_style, num_ctx, conversation_depth, resume_enabled, resume_max_messages,
       status, status_message,
       requested_by, requested_by_name, created_at
FROM agents
ORDER BY protected DESC, name ASC;

-- ─── Skills Registry ────────────────────────────────
-- Tracks all skills (shipped + agent-created) and their sync status.
-- Lifeline polls for status != 'synced' (except 'draft') on each tick
-- and distributes to all active sub-agents.
CREATE TABLE IF NOT EXISTS skills (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL UNIQUE,
    type            TEXT NOT NULL DEFAULT 'system',    -- 'system' | 'agent_created'
    origin          TEXT NOT NULL DEFAULT 'shipped',   -- 'shipped' | 'coa' | agent name
    has_assets      BOOLEAN DEFAULT 0,
    description     TEXT,
    status          TEXT NOT NULL DEFAULT 'synced',    -- 'draft' | 'ready' | 'synced' | 'updated'
    created_at      DATETIME NOT NULL DEFAULT (datetime('now')),
    updated_at      DATETIME NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_skills_status ON skills(status);
SQL

echo "Registry database initialized: ${DB_PATH}"
echo "Tables: agents, skills"
