#!/bin/bash
# ─────────────────────────────────────────────────────
# Versa AGi — Shared Health Check Library
#
# Sourced by setup.sh (both install and --update modes) to
# post-deploy validation. Both scripts run the SAME checks.
#
# Usage:
#   source scripts/health_checks.sh
#   run_health_checks   # runs all checks, sets HEALTH_PASS/HEALTH_FAIL
#
# Required vars (set by caller before sourcing):
#   WATCHDOG_USER, COA_USER
#   DEPLOYED_CORE_INFRA, DEPLOYED_COA_ENV
# ─────────────────────────────────────────────────────

# ─── Colors (may already be set by caller) ───────────
GREEN="${GREEN:-\033[0;32m}"
RED="${RED:-\033[0;31m}"
YELLOW="${YELLOW:-\033[1;33m}"
NC="${NC:-\033[0m}"

HEALTH_PASS=0
HEALTH_FAIL=0

# health_check <description> <test_command>
health_check() {
  local desc="$1"
  shift
  if eval "$@" >/dev/null 2>&1; then
    echo -e "  ✅ PASS  ${desc}"
    HEALTH_PASS=$((HEALTH_PASS + 1))
  else
    echo -e "  ❌ FAIL  ${desc}"
    HEALTH_FAIL=$((HEALTH_FAIL + 1))
  fi
}

# ownership_check <path> <expected_owner:group> <description>
ownership_check() {
  local path="$1" expected="$2" desc="$3"
  local actual
  actual=$(stat -c '%U:%G' "$path" 2>/dev/null)
  if [ "$actual" = "$expected" ]; then
    echo -e "  ✅ PASS  ${desc} (${actual})"
    HEALTH_PASS=$((HEALTH_PASS + 1))
  else
    echo -e "  ❌ FAIL  ${desc} — expected ${expected}, got ${actual:-MISSING}"
    HEALTH_FAIL=$((HEALTH_FAIL + 1))
  fi
}

# perms_check <path> <expected_octal> <description>
perms_check() {
  local path="$1" expected="$2" desc="$3"
  local actual
  actual=$(stat -c '%a' "$path" 2>/dev/null)
  if [ "$actual" = "$expected" ]; then
    echo -e "  ✅ PASS  ${desc} (${actual})"
    HEALTH_PASS=$((HEALTH_PASS + 1))
  else
    echo -e "  ❌ FAIL  ${desc} — expected ${expected}, got ${actual:-MISSING}"
    HEALTH_FAIL=$((HEALTH_FAIL + 1))
  fi
}

# ─── Main Health Check Suite ────────────────────────
run_health_checks() {
  HEALTH_PASS=0
  HEALTH_FAIL=0

  echo ""
  echo "  ── Core Infrastructure ──"
  health_check "Lifeline script exists" "[ -x '${DEPLOYED_CORE_INFRA}/lifeline.sh' ]"
  health_check "agictl binary exists" "[ -x '/usr/local/lib/versa-agi/agictl' ]"
  health_check "agictl wrapper exists" "[ -x '/usr/local/bin/agictl' ]"
  health_check "CRON installed for ${WATCHDOG_USER}" "crontab -u ${WATCHDOG_USER} -l 2>/dev/null | grep -q lifeline"

  echo ""
  echo "  ── COA Workspace ──"
  ownership_check "${DEPLOYED_COA_ENV}/.agent" "${COA_USER}:agi_agents" "COA .agent/ owned by ${COA_USER}:agi_agents"
  health_check "Skills directory exists" "[ -d '${DEPLOYED_COA_ENV}/.agent/skills' ]"
  health_check "Workspace directory exists" "[ -d '${DEPLOYED_COA_ENV}/workspace' ] || [ -L '${DEPLOYED_COA_ENV}/.agent/workspace' ]"
  ownership_check "${DEPLOYED_COA_ENV}/workspace" "${COA_USER}:agi_agents" "COA workspace/ owned by ${COA_USER}:agi_agents"
  health_check "agi_agents group exists" "getent group agi_agents &>/dev/null"

  # ── Sub-Agent Environments ──
  local AGENTS_DB="/var/lib/versa-agi/agents.db"
  if [ -f "${AGENTS_DB}" ]; then
    local sub_agents
    sub_agents=$(sqlite3 "${AGENTS_DB}" "SELECT name || '|' || os_user || '|' || inactive FROM agents WHERE protected=0;" 2>/dev/null || true)
    if [ -n "${sub_agents}" ]; then
      echo ""
      echo "  ── Sub-Agent Environments ──"
      while IFS='|' read -r agent_name agent_os_user agent_inactive; do
        [ -z "${agent_name}" ] && continue
        # Skip unapproved agents — no home dir expected until PU approves via dashboard
        if [ "${agent_inactive}" = "1" ]; then
          echo -e "  ⏳ SKIP  ${agent_name}: pending approval (not yet provisioned)"
          continue
        fi
        local agent_home="/home/${agent_os_user}"
        health_check "${agent_name}: home exists" "[ -d '${agent_home}' ]"
        if [ -d "${agent_home}" ]; then
          health_check "${agent_name}: OS user in agi_agents" "id -nG '${agent_os_user}' 2>/dev/null | grep -qw agi_agents"
          ownership_check "${agent_home}/.agent" "${agent_os_user}:agi_agents" "${agent_name}: .agent/ owned by ${agent_os_user}:agi_agents"
          # Credential isolation: config file must be watchdog:{os_user} 640
          local agent_config="/etc/versa-agi/${agent_name}_config.json"
          if [ -f "${agent_config}" ]; then
            ownership_check "${agent_config}" "watchdog:${agent_os_user}" "${agent_name}: config credential isolation"
            perms_check "${agent_config}" "640" "${agent_name}: config permissions"
          fi
        fi
      done <<< "${sub_agents}"
    fi
  fi

  echo ""
  echo "  ── Persistent Data ──"
  health_check "Agents DB exists" "[ -f '/var/lib/versa-agi/agents.db' ]"
  health_check "Messages DB exists" "[ -f '/var/lib/versa-agi/messages.db' ]"
  health_check "Tasks DB exists" "[ -f '/var/lib/versa-agi/coa/tasks.db' ]"
  health_check "Cycles DB exists" "[ -f '/var/lib/versa-agi/coa/cycles.db' ]"
  
  ownership_check "/var/lib/versa-agi/coa" "${WATCHDOG_USER}:${COA_USER}" "Data dir traversable by ${COA_USER}"
  perms_check "/var/lib/versa-agi/coa" "750" "Data dir permissions"
  ownership_check "/var/lib/versa-agi/coa/cycles" "${COA_USER}:${COA_USER}" "Cycles dir owned by ${COA_USER}"
  perms_check "/var/lib/versa-agi/coa/cycles" "755" "Cycles dir permissions"
  
  ownership_check "/var/lib/versa-agi/messages.db" "${WATCHDOG_USER}:${COA_USER}" "Messages DB bounded to ${WATCHDOG_USER}:${COA_USER}"
  perms_check "/var/lib/versa-agi/messages.db" "660" "Messages DB (shared)"

  echo ""
  echo "  ── Security ──"
  health_check "Config deployed" "[ -f '/etc/versa-agi/coa_config.json' ]"
  health_check "Poise deployed" "[ -f '/etc/versa-agi/poise/coa.md' ]"
  # Gemini credentials are optional (Step 9b opt-in). Assert only what is
  # present — never FAIL (and abort setup) when Gemini was skipped.
  # Bug: missing GEMINI_API_KEY used to fall through to "GCP credentials exist".
  local has_gemini_key=false
  local has_gcp=false
  if [ -f "/etc/versa-agi/coa.env" ] \
     && grep -qE '^[[:space:]]*GEMINI_API_KEY=[^[:space:]#]+' "/etc/versa-agi/coa.env" 2>/dev/null; then
    has_gemini_key=true
  fi
  if [ -f "/etc/versa-agi/vault/gcp-credentials.json" ]; then
    has_gcp=true
  fi
  if [ "${has_gemini_key}" = true ]; then
    health_check "Gemini API key configured" "true"
  elif [ "${has_gcp}" = true ]; then
    health_check "GCP credentials exist" "true"
  else
    echo -e "  ⏳ SKIP  Gemini credentials (optional — not configured)"
  fi

  echo ""
  echo "  ───────────────────────────────────"
  echo "  Results: ${HEALTH_PASS} passed, ${HEALTH_FAIL} failed"

  if [ "${HEALTH_FAIL}" -gt 0 ]; then
    echo -e "  ${YELLOW}[WARN]${NC} Health check completed with ${HEALTH_FAIL} failure(s) — review items above"
    return 1
  else
    echo -e "  ${GREEN}[OK]${NC} All health checks passed"
    return 0
  fi
}
