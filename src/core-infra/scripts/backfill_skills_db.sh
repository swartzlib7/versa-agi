#!/bin/bash
# ─────────────────────────────────────────────────────
# Versa AGi — Skills DB Backfill (One-Time)
#
# Populates the skills table in agents.db from shipped
# skill files. Run once on existing dev systems.
#
# Usage:  sudo bash backfill_skills_db.sh [DB_PATH]
# ─────────────────────────────────────────────────────

set -euo pipefail

DB_PATH="${1:-/var/lib/versa-agi/agents.db}"

if [ ! -f "${DB_PATH}" ]; then
  echo "ERROR: agents.db not found at ${DB_PATH}"
  exit 1
fi

# Ensure skills table exists
sqlite3 "${DB_PATH}" <<'SQL'
CREATE TABLE IF NOT EXISTS skills (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL UNIQUE,
    type            TEXT NOT NULL DEFAULT 'system',
    origin          TEXT NOT NULL DEFAULT 'shipped',
    has_assets      BOOLEAN DEFAULT 0,
    description     TEXT,
    status          TEXT NOT NULL DEFAULT 'synced',
    created_at      DATETIME NOT NULL DEFAULT (datetime('now')),
    updated_at      DATETIME NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_skills_status ON skills(status);
SQL

echo "Backfilling skills table from shipped skills..."

# ─── Shipped Skills with Descriptions (from triage hardcoded list) ───
declare -A SKILLS=(
  ["agent_management"]="Managing sub-agents — provisioning, configuration, and lifecycle"
  ["agent_onboarding"]="Onboarding new agents — workspace setup, identity, and orientation"
  ["cli_reference"]="CLI command reference for COA — full agictl command catalog"
  ["cli_reference_agent"]="CLI command reference for sub-agents — scoped agictl subset"
  ["communication"]="Message crafting and response protocols — tone, structure, and intent"
  ["connection_lifecycle"]="Managing VersaVoice connections — invitations, follow-ups, and status"
  ["connection_request_approval"]="Processing incoming connection requests — accept/reject workflow"
  ["founder_story"]="Sharing the VersaVoice origin story with new contacts"
  ["git_operations"]="Git operations — clone, commit, push, branch management, and PR workflow"
  ["memory_management"]="Managing agent memory — connections, projects, and system context"
  ["message_relay"]="Relaying messages between users and agents across communication channels"
  ["project_management"]="Project setup, assignment, workspace management, and WBS generation"
  ["reminder_management"]="Creating and managing time-based reminders and scheduled notifications"
  ["requirements_elicitation"]="5W1H analysis for new work requests — what to build (scope, motivation, actors)"
  ["security_protocol"]="Security-sensitive operations — credential handling, permission boundaries"
  ["self_introduction"]="Introducing the agent to new contacts with appropriate context"
  ["shared_tooling"]="Using the shared AGi-Tools workspace for cross-agent utility scripts"
  ["task_routing"]="Routing tasks between agents based on role and capability"
  ["task_scheduling"]="Task management — create, update, snooze, prioritize, and status tracking"
  ["work_initiation"]="New project setup or starting new work streams — scaffold and kickoff"
)

INSERTED=0
SKIPPED=0

for skill_name in "${!SKILLS[@]}"; do
  description="${SKILLS[$skill_name]}"

  # Check if already exists
  EXISTS=$(sqlite3 "${DB_PATH}" "SELECT COUNT(*) FROM skills WHERE name='${skill_name}';")
  if [ "${EXISTS}" -gt 0 ]; then
    SKIPPED=$((SKIPPED + 1))
    continue
  fi

  sqlite3 "${DB_PATH}" \
    "INSERT INTO skills (name, type, origin, has_assets, description, status)
     VALUES ('${skill_name}', 'system', 'shipped', 0, '${description}', 'ready');"
  INSERTED=$((INSERTED + 1))
done

echo "Backfill complete: ${INSERTED} inserted, ${SKIPPED} skipped (already exist)"
echo ""
sqlite3 "${DB_PATH}" "SELECT name, type, status FROM skills ORDER BY name;" | column -t -s '|'
