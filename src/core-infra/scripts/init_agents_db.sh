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
  temperature       REAL,
  reasoning_effort  TEXT,
  reasoning_max_tokens INTEGER,
  model_params_extra TEXT,
  conversation_depth INTEGER DEFAULT 10,
  resume_enabled    BOOLEAN DEFAULT 0,
  resume_max_messages INTEGER DEFAULT 0,
  skill_injection_mode TEXT DEFAULT 'hybrid',
  anchor_style      TEXT DEFAULT 'compact',
  browser_enabled   BOOLEAN DEFAULT 0,
  requested_by      TEXT,
  requested_by_name TEXT,
  created_at        DATETIME NOT NULL DEFAULT (datetime('now')),
  updated_at        DATETIME NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_agents_inactive ON agents(inactive);
CREATE INDEX IF NOT EXISTS idx_agents_name ON agents(name);

-- Active agents (used by Lifeline for spawning)
CREATE VIEW IF NOT EXISTS v_active_agents AS
SELECT name, os_user, workspace, model, triage_model, role, timeout_minutes, runaway_threshold, runaway_size_threshold, context_injection_mode, token_budget, max_session_turns, session_retention_enabled, anchor_style, num_ctx, temperature, reasoning_effort, reasoning_max_tokens, model_params_extra, conversation_depth, resume_enabled, resume_max_messages, skill_injection_mode, browser_enabled
FROM agents
WHERE inactive = 0
ORDER BY name ASC;

-- All agents with status summary
CREATE VIEW IF NOT EXISTS v_agent_registry AS
SELECT name, os_user, workspace, timeout_minutes, runaway_threshold, runaway_size_threshold, inactive, protected, can_message_connections, model, triage_model, role,
       context_injection_mode, token_budget, max_session_turns, tool_output_token_budget,
       session_retention_enabled, session_retention_max_age, session_retention_max_count,
       anchor_style, num_ctx, temperature, reasoning_effort, reasoning_max_tokens, model_params_extra,
       conversation_depth, resume_enabled, resume_max_messages,
       skill_injection_mode, browser_enabled,
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
    type            TEXT NOT NULL DEFAULT 'system',    -- 'system' | 'agent_created' | 'override'
    origin          TEXT NOT NULL DEFAULT 'shipped',   -- 'shipped' | 'coa' | agent name
    has_assets      BOOLEAN DEFAULT 0,
    description     TEXT,
    status          TEXT NOT NULL DEFAULT 'synced',    -- 'draft' | 'ready' | 'synced' | 'updated'
    scope           TEXT NOT NULL DEFAULT 'all',       -- 'all' | 'coa_only'
    created_at      DATETIME NOT NULL DEFAULT (datetime('now')),
    updated_at      DATETIME NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_skills_status ON skills(status);

-- ─── System Packages Registry ────────────────────────
-- Tracks system-level packages (apt) requested by agents or the PU.
-- Agents request → PU approves/denies → Lifeline notifies agent (one-shot).
CREATE TABLE IF NOT EXISTS system_packages (
    name          TEXT PRIMARY KEY,
    status        TEXT NOT NULL,           -- 'approved', 'requested', 'denied'
    reason        TEXT,                    -- Why the package is needed
    requested_by  TEXT,                    -- Agent name or 'pu'
    requested_at  DATETIME NOT NULL DEFAULT (datetime('now')),
    resolved_at   DATETIME,
    notified_at   DATETIME                -- One-shot: set after Lifeline injects notice
);
SQL

# ─── Schema Migration: system_packages table ───
sqlite3 "${DB_PATH}" <<'SQL'
CREATE TABLE IF NOT EXISTS system_packages (
    name          TEXT PRIMARY KEY,
    status        TEXT NOT NULL,
    reason        TEXT,
    requested_by  TEXT,
    requested_at  DATETIME NOT NULL DEFAULT (datetime('now')),
    resolved_at   DATETIME,
    notified_at   DATETIME
);
SQL

# ─── Schema Migration: system_packages notified_at column ───
sqlite3 "${DB_PATH}" "ALTER TABLE system_packages ADD COLUMN notified_at DATETIME;" 2>/dev/null || true

# ─── Schema Migration: scope column ───
# Safely add scope column for older databases (ignore if already exists).
# Must run BEFORE the scope index — on existing DBs without scope,
# the CREATE TABLE is a no-op so the column doesn't exist yet.
sqlite3 "${DB_PATH}" "ALTER TABLE skills ADD COLUMN scope TEXT NOT NULL DEFAULT 'all';" 2>/dev/null || true

# Now safe to create the scope index (column guaranteed to exist)
sqlite3 "${DB_PATH}" "CREATE INDEX IF NOT EXISTS idx_skills_scope ON skills(scope);" 2>/dev/null || true

# ─── Schema Migration: anchor_style column ───
sqlite3 "${DB_PATH}" "ALTER TABLE agents ADD COLUMN anchor_style TEXT DEFAULT 'compact';" 2>/dev/null || true

# ─── Schema Migration: browser_enabled column ───
sqlite3 "${DB_PATH}" "ALTER TABLE agents ADD COLUMN browser_enabled BOOLEAN DEFAULT 0;" 2>/dev/null || true

# ─── Schema Migration: skill_injection_mode column ───
sqlite3 "${DB_PATH}" "ALTER TABLE agents ADD COLUMN skill_injection_mode TEXT DEFAULT 'hybrid';" 2>/dev/null || true

# ─── Schema Migration: abstracted model parameters (Iteration 22) ───
sqlite3 "${DB_PATH}" "ALTER TABLE agents ADD COLUMN temperature REAL;" 2>/dev/null || true
sqlite3 "${DB_PATH}" "ALTER TABLE agents ADD COLUMN reasoning_effort TEXT;" 2>/dev/null || true
sqlite3 "${DB_PATH}" "ALTER TABLE agents ADD COLUMN reasoning_max_tokens INTEGER;" 2>/dev/null || true
sqlite3 "${DB_PATH}" "ALTER TABLE agents ADD COLUMN model_params_extra TEXT;" 2>/dev/null || true

# ─── Schema Migration: resume_enabled default change (1 → 0) ───
# REMOVED (Iteration 23): the unconditional `UPDATE agents SET resume_enabled=0
# WHERE resume_enabled=1` ran on EVERY setup --update, silently resetting any
# agent the Primary User had deliberately switched ON via the dashboard.
# The Iteration 19 legacy-default migration has long since been applied; new
# agents get 0 via the schema default.

# ─── View Migration: drop and recreate views to include new columns ───
# Views are cheap to recreate and must reflect the current column set.
sqlite3 "${DB_PATH}" <<'VIEWS'
DROP VIEW IF EXISTS v_active_agents;
CREATE VIEW v_active_agents AS
SELECT name, os_user, workspace, model, triage_model, role, timeout_minutes, runaway_threshold, runaway_size_threshold, context_injection_mode, token_budget, max_session_turns, session_retention_enabled, anchor_style, num_ctx, temperature, reasoning_effort, reasoning_max_tokens, model_params_extra, conversation_depth, resume_enabled, resume_max_messages, skill_injection_mode, browser_enabled
FROM agents
WHERE inactive = 0
ORDER BY name ASC;

DROP VIEW IF EXISTS v_agent_registry;
CREATE VIEW v_agent_registry AS
SELECT name, os_user, workspace, timeout_minutes, runaway_threshold, runaway_size_threshold, inactive, protected, can_message_connections, model, triage_model, role,
       context_injection_mode, token_budget, max_session_turns, tool_output_token_budget,
       session_retention_enabled, session_retention_max_age, session_retention_max_count,
       anchor_style, num_ctx, temperature, reasoning_effort, reasoning_max_tokens, model_params_extra,
       conversation_depth, resume_enabled, resume_max_messages,
       skill_injection_mode, browser_enabled,
       status, status_message,
       requested_by, requested_by_name, created_at
FROM agents
ORDER BY protected DESC, name ASC;
VIEWS

echo "Registry database initialized: ${DB_PATH}"
echo "Tables: agents, skills, system_packages"
