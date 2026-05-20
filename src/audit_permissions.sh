#!/bin/bash
# ─────────────────────────────────────────────────────
# Versa AGi — OPS Manifest Permissions Audit
# Audits deployed file system against System Design §IX
# Run as: sudo ./audit_permissions.sh
# ─────────────────────────────────────────────────────

set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'
BOLD='\033[1m'
DIM='\033[2m'

PASS=0
FAIL=0
WARN=0

check() {
  local path="$1"
  local expected_owner="$2"
  local expected_mode="$3"
  local label="${4:-}"

  if [ ! -e "${path}" ]; then
    echo -e "  ${YELLOW}SKIP${NC}  ${path} — does not exist ${DIM}(${label})${NC}"
    WARN=$((WARN + 1))
    return
  fi

  local actual_owner actual_mode
  # Use stat -L to dereference symlinks (Linux symlinks always report 777)
  actual_owner=$(stat -L -c '%U:%G' "${path}" 2>/dev/null)
  actual_mode=$(stat -L -c '%a' "${path}" 2>/dev/null)

  local owner_ok=true
  local mode_ok=true

  if [ "${actual_owner}" != "${expected_owner}" ]; then
    owner_ok=false
  fi
  if [ "${actual_mode}" != "${expected_mode}" ]; then
    mode_ok=false
  fi

  if [ "${owner_ok}" = true ] && [ "${mode_ok}" = true ]; then
    echo -e "  ${GREEN}PASS${NC}  ${path} ${DIM}(${expected_owner} ${expected_mode})${NC}"
    PASS=$((PASS + 1))
  else
    echo -e "  ${RED}FAIL${NC}  ${path}"
    if [ "${owner_ok}" = false ]; then
      echo -e "        owner: expected ${BOLD}${expected_owner}${NC}, got ${RED}${actual_owner}${NC}"
    fi
    if [ "${mode_ok}" = false ]; then
      echo -e "        mode:  expected ${BOLD}${expected_mode}${NC}, got ${RED}${actual_mode}${NC}"
    fi
    if [ -n "${label}" ]; then
      echo -e "        ${DIM}(${label})${NC}"
    fi
    FAIL=$((FAIL + 1))
  fi
}

echo ""
echo -e "${BOLD}═══ Versa AGi OPS Manifest Audit (System Design §IX) ═══${NC}"
echo ""

# ──────────────────────────────────────────────────────
# §IX.2 — /etc/versa-agi/
# ──────────────────────────────────────────────────────
echo -e "${BOLD}── /etc/versa-agi/ — Configuration & Security ──${NC}"
check "/etc/versa-agi"                      "watchdog:watchdog"  "751"  "Config root dir"
check "/etc/versa-agi/coa_config.json"      "watchdog:coa"       "640"  "COA runtime config"
check "/etc/versa-agi/coa.env"              "watchdog:coa"       "640"  "COA env vars"
check "/etc/versa-agi/paths.env"            "watchdog:coa"       "644"  "System paths"
check "/etc/versa-agi/setup.ini"            "watchdog:agi_agents"  "640"  "Setup config"

# Sub-agent configs: watchdog:{name} 640
for cfg in /etc/versa-agi/*_config.json; do
  [ -f "${cfg}" ] || continue
  name=$(basename "${cfg}" _config.json)
  [ "${name}" = "coa" ] && continue
  check "${cfg}" "watchdog:${name}" "640" "Sub-agent ${name} config"
done

check "/etc/versa-agi/poise"              "watchdog:watchdog"  "750"  "Poise dir"
check "/etc/versa-agi/poise/coa.md"       "watchdog:watchdog"  "640"  "COA poise"
check "/etc/versa-agi/poise/task_protocol.md" "watchdog:watchdog" "640" "Task protocol"
check "/etc/versa-agi/vault"              "watchdog:coa"       "750"  "Vault dir"

# Poise roles
if [ -d "/etc/versa-agi/poise/roles" ]; then
  check "/etc/versa-agi/poise/roles"      "watchdog:coa"       "750"  "Roles dir"
  for role_dir in /etc/versa-agi/poise/roles/*/; do
    [ -d "${role_dir}" ] || continue
    role_name=$(basename "${role_dir}")
    check "${role_dir}" "watchdog:coa" "750" "Role dir: ${role_name}"
    check "${role_dir}poise.md" "watchdog:coa" "440" "Role poise: ${role_name}"
    check "${role_dir}role.ini" "watchdog:coa" "440" "Role ini: ${role_name}"
  done
fi

echo ""

# ──────────────────────────────────────────────────────
# §IX.2 — /var/lib/versa-agi/
# ──────────────────────────────────────────────────────
echo -e "${BOLD}── /var/lib/versa-agi/ — Persistent Data ──${NC}"
check "/var/lib/versa-agi"                "watchdog:watchdog"  "755"  "Data root"
check "/var/lib/versa-agi/agents.db"      "watchdog:coa"       "660"  "Agent registry"
check "/var/lib/versa-agi/messages.db"    "watchdog:coa"       "660"  "Messages DB"
check "/var/lib/versa-agi/coa/tasks.db"   "watchdog:coa"       "660"  "Tasks DB"
check "/var/lib/versa-agi/coa/cycles.db"  "watchdog:coa"       "660"  "Cycles DB"
check "/var/lib/versa-agi/coa"            "watchdog:coa"       "750"  "COA data dir"
check "/var/lib/versa-agi/coa/cycles"     "coa:coa"            "755"  "COA cycles dir"
check "/var/lib/versa-agi/coa/status.json" "watchdog:coa"      "640"  "COA status (§IX.2)"
check "/var/lib/versa-agi/coa/last_prompt.txt" "watchdog:coa"  "640"  "Last prompt (§IX.2)"
check "/var/lib/versa-agi/archive"           "watchdog:watchdog"  "755"  "Archive dir"
check "/var/lib/versa-agi/config"            "watchdog:watchdog" "755" "Config dir"

echo ""

# ──────────────────────────────────────────────────────
# §IX.2 — /var/log/
# ──────────────────────────────────────────────────────
echo -e "${BOLD}── /var/log/ — Logs ──${NC}"
check "/var/log/versa-agi-lifeline.log"   "watchdog:watchdog"  "644"  "Lifeline log"
echo ""

# ──────────────────────────────────────────────────────
# §IX.2 — /usr/local/
# ──────────────────────────────────────────────────────
echo -e "${BOLD}── /usr/local/ — System Binaries ──${NC}"
check "/usr/local/bin/agictl"             "root:root"          "755"  "agictl wrapper"
check "/usr/local/lib/versa-agi/agictl"   "root:root"          "755"  "agictl binary"
check "/usr/local/bin/agitop"             "root:root"          "755"  "agitop launcher"
check "/usr/local/bin/versa-agi-backup"   "root:root"          "755"  "Backup symlink"
check "/usr/local/bin/versa-agi-uninstall" "root:root"         "755"  "Uninstall symlink"
check "/usr/local/bin/versa-agi-update"   "root:root"          "755"  "Update symlink"
check "/usr/local/bin/versa-agi-rekey"    "root:root"          "755"  "Rekey symlink"
echo ""

# ──────────────────────────────────────────────────────
# §IX.3 — Core Infrastructure
# ──────────────────────────────────────────────────────
echo -e "${BOLD}── /home/watchdog/core-infra/ — Core Infrastructure ──${NC}"
CI="/home/watchdog/core-infra"
check "${CI}"                              "watchdog:watchdog"  "755"  "Core infra root"
check "${CI}/lifeline.sh"                  "watchdog:watchdog"  "755"  "Lifeline script"
check "${CI}/watchdog.sh"                  "watchdog:watchdog"  "755"  "Watchdog script"
check "${CI}/config/coa_poise.md"          "watchdog:watchdog"  "640"  "Poise source"
check "${CI}/config/task_protocol.md"      "watchdog:watchdog"  "640"  "Task protocol source"
check "${CI}/bin/agictl"                   "watchdog:watchdog"  "755"  "agictl source"
check "${CI}/bin/agictl-wrapper"           "watchdog:watchdog"  "755"  "agictl wrapper source"
echo ""

# ──────────────────────────────────────────────────────
# §IX.4 — COA Environment
# ──────────────────────────────────────────────────────
echo -e "${BOLD}── /home/coa/coa-env/ — COA Environment ──${NC}"
COA="/home/coa/coa-env"
# Traversal chain: sub-agents must be able to reach workspace/ via symlinks
check "/home/coa"                          "coa:coa"            "755"  "COA home (traversable by agi_agents)"
check "${COA}"                             "coa:coa"            "755"  "COA env (traversable by agi_agents)"
check "${COA}/.agent"                      "coa:coa"            "755"  ".agent dir"
check "${COA}/.agent/poise.md"             "watchdog:coa"       "640"  ".agent/poise.md (copied by Lifeline)"
check "${COA}/.agent/skills"               "coa:coa"            "755"  "Skills dir"
check "${COA}/workspace"                   "coa:agi_agents"     "2770"  "Workspace dir (setgid for cross-agent collaboration §3.6)"
check "${COA}/attachments"                "coa:agi_agents"     "2770" "Attachments dir (setgid, matches workspace/)"
echo ""

# ──────────────────────────────────────────────────────
# §IX.4 — Sub-Agent Environments
# ──────────────────────────────────────────────────────
echo -e "${BOLD}── Sub-Agent Environments ──${NC}"
# Find sub-agents from agents.db
AGENTS_DB="/var/lib/versa-agi/agents.db"
if [ -f "${AGENTS_DB}" ]; then
  SUB_AGENTS=$(sqlite3 "${AGENTS_DB}" "SELECT name FROM agents WHERE name NOT IN ('watchdog','coa') AND status != 'removed';" 2>/dev/null || true)
  for agent in ${SUB_AGENTS}; do
    echo -e "  ${BOLD}[${agent}]${NC}"
    AHOME="/home/agi-${agent}"
    check "${AHOME}"                        "${agent}:agi_agents"   "770"  "Home dir"
    check "${AHOME}/.agent"                 "${agent}:agi_agents"   "770"  ".agent dir"
    check "${AHOME}/.agent/skills"          "${agent}:agi_agents"   "775"  "Skills dir"
    check "${AHOME}/workspace"              "${agent}:agi_agents"   "770"  "Workspace dir"
    check "/etc/versa-agi/${agent}_config.json" "watchdog:${agent}" "640" "Config JSON"
    # §IX.2 /var/lib/versa-agi/{name}/ — agent data directory
    check "/var/lib/versa-agi/${agent}"              "watchdog:${agent}"   "750" "Data dir"
    check "/var/lib/versa-agi/${agent}/cycles"        "${agent}:${agent}"   "755" "Cycles dir"
    check "/var/lib/versa-agi/${agent}/last_prompt.txt" "watchdog:${agent}" "640" "Last prompt"
    check "/var/lib/versa-agi/${agent}/poise.md"      "watchdog:${agent}"   "640" "Poise template"
    check "/var/lib/versa-agi/${agent}/duties.md"     "watchdog:${agent}"   "640" "Duties file"
    # §IX.4 Sub-agent files — git identity, SSH keypair
    check "${AHOME}/README.md"              "${agent}:agi_agents"   "664"  "README"
    check "${AHOME}/.gitconfig"             "${agent}:agi_agents"   "644"  "Git config"
    check "${AHOME}/.git-credentials"       "${agent}:agi_agents"   "600"  "Git credentials"
    check "${AHOME}/.ssh"                   "${agent}:agi_agents"   "700"  "SSH dir (keys enforced by restab: private=600, pub=644)"
    echo ""
  done
else
  echo -e "  ${YELLOW}SKIP${NC}  agents.db not found — cannot enumerate sub-agents"
fi

# ──────────────────────────────────────────────────────
# §IX.5 — CRON & Sudoers
# ──────────────────────────────────────────────────────
echo -e "${BOLD}── CRON & Sudoers ──${NC}"
check "/etc/sudoers.d/versa_agi_agictl"   "root:root"          "440"  "agictl sudoers"
check "/etc/sudoers.d/versa_agi_watchdog" "root:root"          "440"  "watchdog sudoers"

# Check agi_agents group membership
echo ""
echo -e "${BOLD}── agi_agents Group Membership ──${NC}"
AGI_MEMBERS=$(getent group agi_agents 2>/dev/null | cut -d: -f4 || true)
echo -e "  Members: ${AGI_MEMBERS:-<none>}"
for expected in coa watchdog; do
  if echo ",${AGI_MEMBERS}," | grep -q ",${expected},"; then
    echo -e "  ${GREEN}PASS${NC}  ${expected} is in agi_agents"
    PASS=$((PASS + 1))
  else
    echo -e "  ${RED}FAIL${NC}  ${expected} is NOT in agi_agents"
    FAIL=$((FAIL + 1))
  fi
done
for agent in ${SUB_AGENTS:-}; do
  if echo ",${AGI_MEMBERS}," | grep -q ",${agent},"; then
    echo -e "  ${GREEN}PASS${NC}  ${agent} is in agi_agents"
    PASS=$((PASS + 1))
  else
    echo -e "  ${RED}FAIL${NC}  ${agent} is NOT in agi_agents"
    FAIL=$((FAIL + 1))
  fi
done

# Check SUDO_USER in agi_agents
SUDO_USER_CHECK="${SUDO_USER:-}"
if [ -n "${SUDO_USER_CHECK}" ]; then
  if echo ",${AGI_MEMBERS}," | grep -q ",${SUDO_USER_CHECK},"; then
    echo -e "  ${GREEN}PASS${NC}  ${SUDO_USER_CHECK} (Primary User) is in agi_agents"
    PASS=$((PASS + 1))
  else
    echo -e "  ${RED}FAIL${NC}  ${SUDO_USER_CHECK} (Primary User) is NOT in agi_agents"
    FAIL=$((FAIL + 1))
  fi
fi

echo ""
echo -e "${BOLD}═══════════════════════════════════════════${NC}"
echo -e "  ${GREEN}PASS${NC}: ${PASS}  ${RED}FAIL${NC}: ${FAIL}  ${YELLOW}SKIP${NC}: ${WARN}"
echo -e "${BOLD}═══════════════════════════════════════════${NC}"
echo ""

if [ "${FAIL}" -gt 0 ]; then
  exit 1
fi
