#!/bin/bash
# ─────────────────────────────────────────────────────
# Versa AGi — Shared Cognitive Tracker Initialization
#
# Creates the tasks.db schema.
# Location: /var/lib/versa-agi/coa/tasks.db
# Access: COA (via agictl)
# ─────────────────────────────────────────────────────

set -euo pipefail

DB_PATH="${1:-/var/lib/versa-agi/coa/tasks.db}"

echo "Initializing tasks database: ${DB_PATH}"

# Migration: task_notes was renamed to task_progress before first release of
# the feature — rename in place if an early deployment created the old table.
sqlite3 "${DB_PATH}" "ALTER TABLE task_notes RENAME TO task_progress;" 2>/dev/null || true
sqlite3 "${DB_PATH}" "DROP INDEX IF EXISTS idx_task_notes_task;" 2>/dev/null || true

sqlite3 "${DB_PATH}" <<'SQL'
CREATE TABLE IF NOT EXISTS tasks (
  id                INTEGER PRIMARY KEY AUTOINCREMENT,
  title             TEXT NOT NULL,
  description       TEXT,
  status            TEXT DEFAULT 'planned' CHECK(status IN ('planned', 'in_progress', 'waiting', 'blocked', 'frozen', 'cancelled', 'done')),
  priority          TEXT DEFAULT 'normal' CHECK(priority IN ('low', 'normal', 'high', 'urgent')),
  requested_by      TEXT,
  assigned_to       TEXT,
  assigned_by       TEXT,
  project_id        INTEGER,
  tags              TEXT,
  due_date          DATETIME,
  callback_action   TEXT CHECK(callback_action IN ('notify_sponsor', 'notify_connection', 'await_reply', 'check_connection', 'none')),
  source_message_id INTEGER,
  wake_after        TIMESTAMP,
  wake_cycle_count  INTEGER DEFAULT 0,
  spawn_attempts    INTEGER DEFAULT 0,
  created_at        DATETIME NOT NULL DEFAULT (datetime('now')),
  updated_at        DATETIME NOT NULL DEFAULT (datetime('now')),
  completed_at      DATETIME,
  pre_freeze_status TEXT
);

CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
CREATE INDEX IF NOT EXISTS idx_tasks_priority ON tasks(priority);
CREATE INDEX IF NOT EXISTS idx_tasks_tags ON tasks(tags);

-- ── Task Progress (append-only per-task progress journal) ──
-- Agents leave themselves breadcrumbs across stateless cycles: what was done,
-- how far they got, what's next. Replaces lossy description overwrites and
-- removes the need for cross-cycle chat history as a progress carrier.
CREATE TABLE IF NOT EXISTS task_progress (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  task_id     INTEGER NOT NULL,
  agent_name  TEXT,
  note        TEXT NOT NULL,
  created_at  DATETIME NOT NULL DEFAULT (datetime('now')),
  FOREIGN KEY (task_id) REFERENCES tasks(id)
);

CREATE INDEX IF NOT EXISTS idx_task_progress_task ON task_progress(task_id);

-- ── Games (Strategic Pursuit Container) ──

CREATE TABLE IF NOT EXISTS games (
  id                      INTEGER PRIMARY KEY AUTOINCREMENT,
  name                    TEXT NOT NULL UNIQUE,
  postulate               TEXT,
  milestones              TEXT,
  posture                 TEXT DEFAULT 'exploratory' CHECK(posture IN ('exploratory','steady','aggressive','defensive')),
  autonomy                TEXT DEFAULT 'collaborative' CHECK(autonomy IN ('advisory','collaborative','autonomous')),
  freedoms_summary        TEXT,
  barriers_summary        TEXT,
  environment_assessed_at DATETIME,
  status                  TEXT DEFAULT 'active' CHECK(status IN ('active','paused','archived')),
  created_at              DATETIME DEFAULT (datetime('now')),
  updated_at              DATETIME DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_games_status ON games(status);

CREATE TABLE IF NOT EXISTS projects (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  name            TEXT NOT NULL UNIQUE,
  description     TEXT,
  type            TEXT NOT NULL DEFAULT 'local' CHECK(type IN ('git', 'local')),
  platform        TEXT CHECK(platform IN ('github', 'gitlab', NULL)),
  remote_url      TEXT,
  access_token    TEXT,
  branch          TEXT DEFAULT 'main',
  workspace_path  TEXT NOT NULL,
  game_id         INTEGER,
  status          TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active', 'paused', 'archived')),
  created_at      DATETIME NOT NULL DEFAULT (datetime('now')),
  updated_at      DATETIME NOT NULL DEFAULT (datetime('now')),
  FOREIGN KEY (game_id) REFERENCES games(id)
);

CREATE INDEX IF NOT EXISTS idx_projects_status ON projects(status);

CREATE TABLE IF NOT EXISTS connections (
  uid               TEXT PRIMARY KEY,
  display_name      TEXT NOT NULL,
  spoken_lang       TEXT,
  country           TEXT,
  city              TEXT,
  chromosome        TEXT,
  date_of_birth     TEXT,
  abilities         TEXT,
  relationship      TEXT,
  notes             TEXT,
  comm_preferences  TEXT,
  first_seen        DATETIME NOT NULL DEFAULT (datetime('now')),
  last_contact      DATETIME,
  profile_synced_at DATETIME
);
SQL

# ─── Schema Migrations ───
# Safely apply schema migrations for older databases (ignore errors if columns already exist)
for col_def in \
  "due_date DATETIME" \
  "callback_action TEXT CHECK(callback_action IN ('notify_sponsor', 'notify_connection', 'await_reply', 'check_connection', 'none'))" \
  "source_message_id INTEGER" \
  "wake_after TIMESTAMP" \
  "wake_cycle_count INTEGER DEFAULT 0" \
  "spawn_attempts INTEGER DEFAULT 0" \
  "completed_at DATETIME" \
  "pre_freeze_status TEXT" \
; do
  sqlite3 "${DB_PATH}" "ALTER TABLE tasks ADD COLUMN ${col_def};" 2>/dev/null || true
done

# ─── Projects: game_id FK migration (for existing databases) ───
sqlite3 "${DB_PATH}" "ALTER TABLE projects ADD COLUMN game_id INTEGER;" 2>/dev/null || true

# ─── Connections: comm_preferences migration ───
sqlite3 "${DB_PATH}" "ALTER TABLE connections ADD COLUMN comm_preferences TEXT;" 2>/dev/null || true

# ─── Project Members: participation + comm_channels migration ───
sqlite3 "${DB_PATH}" "ALTER TABLE project_members ADD COLUMN participation TEXT DEFAULT 'team_player';" 2>/dev/null || true
sqlite3 "${DB_PATH}" "ALTER TABLE project_members ADD COLUMN comm_channels TEXT;" 2>/dev/null || true

sqlite3 "${DB_PATH}" <<'SQL'
CREATE VIEW IF NOT EXISTS v_active_tasks AS
SELECT * FROM tasks
WHERE status = 'in_progress'
   OR (status IN ('planned', 'waiting', 'blocked') AND due_date IS NOT NULL AND due_date <= datetime('now'))
ORDER BY
  CASE priority
    WHEN 'urgent' THEN 1
    WHEN 'high' THEN 2
    WHEN 'normal' THEN 3
    WHEN 'low' THEN 4
  END,
  created_at ASC;

CREATE VIEW IF NOT EXISTS v_due_blocked_tasks AS
SELECT id, title, callback_action, source_message_id, wake_cycle_count, due_date as wake_after
FROM tasks
WHERE status = 'blocked'
  AND due_date IS NOT NULL
  AND due_date <= datetime('now')
ORDER BY due_date ASC;

CREATE VIEW IF NOT EXISTS v_active_projects AS
SELECT id, name, type, platform, branch, workspace_path, status
FROM projects
WHERE status = 'active'
ORDER BY name ASC;

-- ── Agent Memory Bridging Tables (TD-MEM-003) ──

CREATE TABLE IF NOT EXISTS agent_memory_connection (
  id                  INTEGER PRIMARY KEY AUTOINCREMENT,
  agent_name          TEXT NOT NULL,
  contact_uid         TEXT NOT NULL,
  preferences         TEXT,
  personal_notes      TEXT,
  communication_style TEXT,
  rapport_level       TEXT CHECK(rapport_level IN ('new', 'building', 'established', 'strong')),
  emotional_notes     TEXT,
  last_interaction    DATETIME,
  created_at          DATETIME DEFAULT (datetime('now')),
  updated_at          DATETIME DEFAULT (datetime('now')),
  UNIQUE(agent_name, contact_uid)
);

CREATE INDEX IF NOT EXISTS idx_mem_conn_agent ON agent_memory_connection(agent_name);
CREATE INDEX IF NOT EXISTS idx_mem_conn_contact ON agent_memory_connection(contact_uid);

CREATE TABLE IF NOT EXISTS agent_memory_project (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  agent_name      TEXT NOT NULL,
  project_id      INTEGER NOT NULL,
  current_phase   TEXT,
  key_decisions   TEXT,
  blockers        TEXT,
  next_steps      TEXT,
  created_at      DATETIME DEFAULT (datetime('now')),
  updated_at      DATETIME DEFAULT (datetime('now')),
  UNIQUE(agent_name, project_id)
);

CREATE INDEX IF NOT EXISTS idx_mem_proj_agent ON agent_memory_project(agent_name);

CREATE TABLE IF NOT EXISTS agent_memory_system (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  agent_name      TEXT NOT NULL,
  key             TEXT NOT NULL UNIQUE,
  value           TEXT NOT NULL,
  created_at      DATETIME DEFAULT (datetime('now')),
  updated_at      DATETIME DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_mem_sys_key ON agent_memory_system(key);

-- ── Project Members (Multi-Agent / Connection Assignment) ──

CREATE TABLE IF NOT EXISTS project_members (
  project_id      INTEGER NOT NULL,
  member_type     TEXT NOT NULL CHECK(member_type IN ('agent', 'connection')),
  member_id       TEXT NOT NULL,
  display_name    TEXT,
  workspace_path  TEXT,
  branch          TEXT,
  roles           TEXT DEFAULT 'contributor',
  participation   TEXT DEFAULT 'team_player' CHECK(participation IN ('team_player','sponsor','observer')),
  comm_channels   TEXT,
  assigned_at     DATETIME DEFAULT (datetime('now')),
  PRIMARY KEY (project_id, member_type, member_id),
  FOREIGN KEY (project_id) REFERENCES projects(id)
);

CREATE INDEX IF NOT EXISTS idx_pm_project ON project_members(project_id);
CREATE INDEX IF NOT EXISTS idx_pm_member ON project_members(member_type, member_id);

-- ── Agent Awareness (Conclusions + Actions) ──

CREATE TABLE IF NOT EXISTS agent_awareness (
  id                    INTEGER PRIMARY KEY AUTOINCREMENT,
  agent_name            TEXT NOT NULL,
  type                  TEXT NOT NULL CHECK(type IN ('conclusion','action')),
  subject_type          TEXT NOT NULL CHECK(subject_type IN ('connection','project','game','system','self')),
  subject_id            TEXT,
  content               TEXT NOT NULL,
  action_conclusion_id  INTEGER,
  context               TEXT,
  status                TEXT DEFAULT 'active' CHECK(status IN ('active','revised','superseded','completed')),
  created_at            DATETIME DEFAULT (datetime('now')),
  updated_at            DATETIME DEFAULT (datetime('now')),
  FOREIGN KEY (action_conclusion_id) REFERENCES agent_awareness(id)
);

CREATE INDEX IF NOT EXISTS idx_awareness_agent ON agent_awareness(agent_name);
CREATE INDEX IF NOT EXISTS idx_awareness_type ON agent_awareness(agent_name, type, status);
CREATE INDEX IF NOT EXISTS idx_awareness_subject ON agent_awareness(subject_type, subject_id);

-- ── Project Opponents (Competitive Intelligence) ──

CREATE TABLE IF NOT EXISTS project_opponents (
  id                    INTEGER PRIMARY KEY AUTOINCREMENT,
  project_id            INTEGER NOT NULL,
  name                  TEXT NOT NULL,
  type                  TEXT CHECK(type IN ('person','agent','business','association')),
  description           TEXT,
  intelligence_sources  TEXT,
  last_assessment       TEXT,
  last_assessed_at      DATETIME,
  created_at            DATETIME DEFAULT (datetime('now')),
  FOREIGN KEY (project_id) REFERENCES projects(id)
);

CREATE INDEX IF NOT EXISTS idx_opponents_project ON project_opponents(project_id);

-- ── Default Game (First Installation Seed) ──

INSERT OR IGNORE INTO games (name, postulate, posture, autonomy)
VALUES ('Get to know and understand your PU',
        'Establish a deep working relationship with the Primary User — understand their goals, preferences, communication style, and life context',
        'exploratory',
        'advisory');

-- Clean up any pre-existing orphaned project memberships or memories
DELETE FROM project_members WHERE project_id NOT IN (SELECT id FROM projects);
DELETE FROM agent_memory_project WHERE project_id NOT IN (SELECT id FROM projects);
SQL

echo "Tasks database initialized: ${DB_PATH}"
echo "Tables: tasks, games, projects, connections, project_members, agent_memory_connection, agent_memory_project, agent_memory_system, agent_awareness, project_opponents"
