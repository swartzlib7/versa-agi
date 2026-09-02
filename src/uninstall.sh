#!/bin/bash
# ─────────────────────────────────────────────────────
# Versa AGi — Uninstall Script
#
# ⛔ MANIFEST: Any file path changes MUST be reflected in
#    design/Versa AGi - System Design.md §IX
#
# Cleanly removes Versa AGi from the system:
#   1. Stops Sentinel service and removes CRON entry
#   2. Kills active agent processes
#   3. Removes workspace symlinks
#   4. Optionally removes OS users
#
# --purge mode: Nukes everything — deployed files,
#   databases, VersaVoice sub-account, backups, users.
#
# ⚠️  NO BACKUPS ARE MADE. Back up data BEFORE running.
#
# Does NOT delete the installation source files.
#
# Usage:  sudo ./uninstall.sh              # Interactive
#         sudo ./uninstall.sh --purge      # Full nuke
#         sudo ./uninstall.sh --dry-run    # Preview only
# OS:     Linux (Ubuntu, Debian, Fedora, Arch)
# ─────────────────────────────────────────────────────

set -euo pipefail

# ─── UI Library ──────────────────────────────────────
_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
UI_LIB="${_SCRIPT_DIR}/core-infra/ui_lib.sh"
# Fallback: persisted location (/usr/local/lib/versa-agi/)
[ ! -f "${UI_LIB}" ] && UI_LIB="${_SCRIPT_DIR}/ui_lib.sh"
if [ -f "${UI_LIB}" ]; then
  source "${UI_LIB}"
else
  # Inline fallback — color variables + function stubs
  BOLD='\033[1m'
  DIM='\033[2m'
  RESET='\033[0m'
  RED='\033[0;31m'
  GREEN='\033[0;32m'
  YELLOW='\033[1;33m'
  CYAN='\033[0;36m'
  BCYAN='\033[1;36m'
  WHITE='\033[1;37m'
  NC='\033[0m'
  info()  { echo -e "\033[38;2;0;255;204m[INFO]\033[0m $*"; }
  ok()    { echo -e "\033[0;32m[OK]\033[0m $*"; }
  warn()  { echo -e "\033[1;33m[WARN]\033[0m $*"; }
  error() { echo -e "\033[0;31m[ERROR]\033[0m $*"; exit 1; }
  section() { echo -e "\n\033[1;37m═══ $* ═══\033[0m\n"; }
  banner()  { echo -e "\n\033[1;36m  Versa AGi — ${1:-Uninstall} v${2:-3.0}\033[0m\n"; }
  summary_card() { echo -e "\n\033[1;37m$1\033[0m"; shift; for item in "$@"; do echo "  $item"; done; }
  step_arrow() { echo -e "  → $*"; }
  license_notice() { echo -e "  \033[2mLicensed under BSL-1.1 · © $(date +%Y) VersaVoice AI LLC\033[0m"; }
fi

# ─── Root Check ─────────────────────────────────────
if [ "$(id -u)" -ne 0 ]; then
  error "This script must be run as root (sudo ./uninstall.sh)"
fi

# ─── Configuration ──────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PURGE_MODE=false
DRY_RUN=false

# ─── Parse Arguments ────────────────────────────────
while [ $# -gt 0 ]; do
  case "$1" in
    --purge)    PURGE_MODE=true; shift ;;
    --dry-run)  DRY_RUN=true; shift ;;
    --help|-h)
      echo "Usage: sudo ./uninstall.sh [--purge] [--dry-run]"
      echo ""
      echo "  --purge     Complete destruction of all Versa AGi data."
      echo "              Removes: deployed files, databases, VersaVoice"
      echo "              sub-account, backups, log files, and OS users."
      echo "              Requires explicit confirmation."
      echo "  --dry-run   Show what would be removed without acting."
      exit 0
      ;;
    *) echo "Unknown option: $1. Use --help for usage."; exit 1 ;;
  esac
done

# ─── INI File Parser ────────────────────────────────
INI_FILE=""
if [ -f "/etc/versa-agi/setup.ini" ]; then
  INI_FILE="/etc/versa-agi/setup.ini"
elif [ -f "${SCRIPT_DIR}/setup.ini" ]; then
  INI_FILE="${SCRIPT_DIR}/setup.ini"
elif [ -f "$(dirname "${SCRIPT_DIR}")/setup.ini" ]; then
  INI_FILE="$(dirname "${SCRIPT_DIR}")/setup.ini"
fi

ini_get() {
  local section="$1" key="$2" default="${3:-}"
  if [ -z "${INI_FILE}" ]; then echo "${default}"; return; fi
  local value
  value=$(awk -F '=' -v section="$section" -v key="$key" '
    /^\[/ { current_section = $0; gsub(/[\[\]]/, "", current_section) }
    current_section == section && $1 ~ "^\\s*"key"\\s*$" {
      val = $2
      for(i=3;i<=NF;i++) val = val "=" $i
      gsub(/^[[:space:]]+|[[:space:]]+$/, "", val)
      gsub(/#.*$/, "", val)
      gsub(/[[:space:]]+$/, "", val)
      print val
      exit
    }
  ' "${INI_FILE}")
  echo "${value:-$default}"
}

# Read configuration (setup.ini first, env var fallback)
WATCHDOG_USER=$(ini_get "users" "watchdog" "${VERSA_WATCHDOG_USER:-watchdog}")
COA_USER=$(ini_get "users" "coa" "${VERSA_COA_USER:-coa}")
VV_TOKEN=$(ini_get "versavoice" "api_token" "")
# Fallback: read token from deployed system config (curl installs don't persist setup.ini tokens)
if [ -z "${VV_TOKEN}" ] && [ -f "/etc/versa-agi/coa_config.json" ]; then
  VV_TOKEN=$(jq -r '.versavoice.api_token // empty' "/etc/versa-agi/coa_config.json" 2>/dev/null || true)
fi
WORKSPACE_LINK=$(ini_get "git" "workspace_link" "")
PRIMARY_USER="${SUDO_USER:-}"

# VersaVoice API
VV_API_BASE="https://us-central1-versavoice-s777.cloudfunctions.net/api/v1"

# ─── Banner ─────────────────────────────────────────
banner "uninstall" "3.0"

if [ "${PURGE_MODE}" = true ]; then
  echo -e "${RED}${BOLD}┌─────────────────────────────────────────${NC}"
  echo -e "${RED}${BOLD}│          ⚠  DESTRUCTIVE PURGE  ⚠        ${NC}"
  echo -e "${RED}${BOLD}│                                         ${NC}"
  echo -e "${RED}${BOLD}│  This will PERMANENTLY DESTROY:         ${NC}"
  echo -e "${RED}${BOLD}│    • All deployed files & databases     ${NC}"
  echo -e "${RED}${BOLD}│    • VersaVoice sub-account (API)       ${NC}"
  echo -e "${RED}${BOLD}│    • Backup snapshots                   ${NC}"
  echo -e "${RED}${BOLD}│    • Log files & temp files             ${NC}"
  printf "${RED}${BOLD}│    • OS users (%-8s %-8s)       ${NC}\n" "${WATCHDOG_USER}," "${COA_USER}"
  echo -e "${RED}${BOLD}│    • CRON entries & symlinks            ${NC}"
  echo -e "${RED}${BOLD}│    • agitop venv (/opt/versa-agi/)      ${NC}"
  echo -e "${RED}${BOLD}│                                         ${NC}"
  echo -e "${RED}${BOLD}│  ⚠ NO BACKUPS ARE MADE.                 ${NC}"
  echo -e "${RED}${BOLD}│    Back up your data BEFORE proceeding. ${NC}"
  echo -e "${RED}${BOLD}│  THIS ACTION IS IRREVERSIBLE.           ${NC}"
  echo -e "${RED}${BOLD}└─────────────────────────────────────────${NC}"
  echo ""

  if [ "${DRY_RUN}" = true ]; then
    echo -e "${YELLOW}DRY RUN — showing what would be removed, no changes made.${NC}"
    echo ""
  else
    echo -e "${RED}Type 'PURGE' to confirm total destruction:${NC}"
    read -r CONFIRM
    if [ "${CONFIRM}" != "PURGE" ]; then
      echo "Cancelled. (Expected 'PURGE')"
      exit 0
    fi
    echo ""
  fi
else
  echo "This will:"
  echo "  • Stop the Sentinel service and remove the CRON entry"
  echo "  • Remove the CRON entry for the Lifeline"
  echo "  • Kill any active agent harness processes"
  echo "  • Remove workspace symlinks"
  echo "  • Optionally remove OS users (watchdog, coa)"
  echo ""
  echo "This will NOT delete source files or databases."
  echo ""
  read -p "Proceed with uninstall? [y/N] " -n 1 -r
  echo
  if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    echo "Cancelled."
    exit 0
  fi
fi

echo ""

# ─── Step 1: Remove CRON Entry ──────────────────────
section "Step 1 — Remove CRON"

if [ "${DRY_RUN}" = true ]; then
  if crontab -u "${WATCHDOG_USER}" -l 2>/dev/null | grep -q "lifeline.sh"; then
    info "Would remove: CRON entry for ${WATCHDOG_USER}"
  fi
else
  EXISTING_CRON=$(crontab -u "${WATCHDOG_USER}" -l 2>/dev/null || true)
  if echo "${EXISTING_CRON}" | grep -q "lifeline.sh"; then
    echo "${EXISTING_CRON}" | grep -v "lifeline.sh" | grep -v "^TZ=" | crontab -u "${WATCHDOG_USER}" - 2>/dev/null || \
      crontab -u "${WATCHDOG_USER}" -r 2>/dev/null || true
    ok "CRON entry removed for ${WATCHDOG_USER}"
  else
    ok "No CRON entry found (already clean)"
  fi
fi

# Stop and disable Sentinel service
if [ "${DRY_RUN}" = true ]; then
  if systemctl is-enabled --quiet versa-agi-sentinel 2>/dev/null; then
    info "Would remove: Sentinel systemd service"
  fi
else
  if systemctl is-active --quiet versa-agi-sentinel 2>/dev/null; then
    systemctl stop versa-agi-sentinel
    ok "Sentinel service stopped"
  fi
  if systemctl is-enabled --quiet versa-agi-sentinel 2>/dev/null; then
    systemctl disable versa-agi-sentinel --quiet 2>/dev/null || true
    ok "Sentinel service disabled"
  fi
  if [ -f /etc/systemd/system/versa-agi-sentinel.service ]; then
    rm -f /etc/systemd/system/versa-agi-sentinel.service
    systemctl daemon-reload
    ok "Sentinel service unit removed"
  else
    ok "No Sentinel service found"
  fi
fi

echo ""

# ─── Step 2: Kill Active Processes ──────────────────
section "Step 2 — Stop Processes"

kill_user_processes() {
  local user=$1
  local count

  if id "${user}" &>/dev/null; then
    count=$(pgrep -u "${user}" -c 2>/dev/null | tr -d '[:space:]' || echo "0")
    if [ "${count:-0}" -gt 0 ]; then
      pkill -u "${user}" 2>/dev/null || true
      sleep 2
      # Force kill any remaining
      pkill -9 -u "${user}" 2>/dev/null || true
      ok "Killed ${count} process(es) for ${user}"
    else
      ok "No active processes for ${user}"
    fi
  fi
}

kill_user_processes "${COA_USER}"
kill_user_processes "${WATCHDOG_USER}"

# Clean up lock files
rm -f /tmp/versa_agi_*.lock
ok "Lock files cleaned"

echo ""

# ─── Step 3: Remove Deployed Files ──────────────────
section "Step 3 — Remove Files"

DEPLOYED_CORE_INFRA="/home/${WATCHDOG_USER}/core-infra"
DEPLOYED_COA_ENV="/home/${COA_USER}/coa-env"

# Read VersaVoice sub-account ID BEFORE deleting (for purge warning)
SYSTEM_CONFIG="/etc/versa-agi/coa_config.json"
SUB_ACCOUNT_ID=""

if [ -f "${SYSTEM_CONFIG}" ]; then
  SUB_ACCOUNT_ID=$(jq -r '.versavoice.sub_account_id // empty' "${SYSTEM_CONFIG}" 2>/dev/null || true)
fi

if [ "${PURGE_MODE}" = true ]; then
  # Purge: no prompts — nuke everything
  if [ -d "${DEPLOYED_CORE_INFRA}" ]; then
    rm -rf "${DEPLOYED_CORE_INFRA}"
    ok "Removed ${DEPLOYED_CORE_INFRA}"
  fi
  if [ -d "${DEPLOYED_COA_ENV}" ]; then
    rm -rf "${DEPLOYED_COA_ENV}"
    ok "Removed ${DEPLOYED_COA_ENV}"
  fi
else
  echo ""
  echo "Deployed directories:"
  echo "  ${DEPLOYED_CORE_INFRA}"
  echo "  ${DEPLOYED_COA_ENV}"
  echo ""

  read -p "Remove deployed directories? (databases will be lost) [y/N] " -n 1 -r
  echo

  if [[ $REPLY =~ ^[Yy]$ ]]; then
    if [ -d "${DEPLOYED_CORE_INFRA}" ]; then
      rm -rf "${DEPLOYED_CORE_INFRA}"
      ok "Removed ${DEPLOYED_CORE_INFRA}"
    fi
    if [ -d "${DEPLOYED_COA_ENV}" ]; then
      rm -rf "${DEPLOYED_COA_ENV}"
      ok "Removed ${DEPLOYED_COA_ENV}"
    fi
  else
    warn "Kept deployed directories"
  fi
fi

echo ""

# ─── Step 4: Remove Log File ────────────────────────
section "Step 4 — Log Files"

if [ -f /var/log/versa-agi-lifeline.log ]; then
  rm /var/log/versa-agi-lifeline.log
  ok "Removed /var/log/versa-agi-lifeline.log"
fi
if [ -f /var/log/versa-agi-sentinel.log ]; then
  rm /var/log/versa-agi-sentinel.log
  ok "Removed /var/log/versa-agi-sentinel.log"
fi
if [ -d /var/log/versa-agi-archive ]; then
  rm -rf /var/log/versa-agi-archive
  ok "Removed /var/log/versa-agi-archive/"
fi
if [ ! -f /var/log/versa-agi-lifeline.log ] && [ ! -f /var/log/versa-agi-sentinel.log ] && [ ! -d /var/log/versa-agi-archive ]; then
  ok "No log files found"
fi

echo ""

# ─── Step 4b: Remove Sudoers Entry ──────────────────
section "Step 4b — Sudoers"

SUDOERS_FILE="/etc/sudoers.d/versa_agi_${WATCHDOG_USER}"
if [ -f "${SUDOERS_FILE}" ]; then
  rm -f "${SUDOERS_FILE}"
  ok "Removed ${SUDOERS_FILE}"
else
  ok "No sudoers entry found"
fi

echo ""

# ─── Step 4c: Remove agictl ──────────────────────
section "Step 4c — Remove agictl"

# CLI wrappers
if [ -f /usr/local/bin/agictl ] || [ -L /usr/local/bin/agictl ]; then
  rm -f /usr/local/bin/agictl
  ok "Removed /usr/local/bin/agictl (wrapper)"
fi
if [ -f /usr/local/bin/vcoa ]; then
  rm -f /usr/local/bin/vcoa
  ok "Removed /usr/local/bin/vcoa"
fi
if [ -f /usr/local/bin/versa-agi-update ] || [ -L /usr/local/bin/versa-agi-update ]; then
  rm -f /usr/local/bin/versa-agi-update
  ok "Removed /usr/local/bin/versa-agi-update"
fi
if [ -f /usr/local/bin/versa-agi-ide ] || [ -L /usr/local/bin/versa-agi-ide ]; then
  rm -f /usr/local/bin/versa-agi-ide
  ok "Removed /usr/local/bin/versa-agi-ide"
fi
if [ -f /etc/ssh/sshd_config.d/versa-agi-ide.conf ]; then
  rm -f /etc/ssh/sshd_config.d/versa-agi-ide.conf
  ok "Removed sshd IDE drop-in"
fi
rm -rf /etc/ssh/versa-agi-ide /etc/versa-agi/ide_ssh 2>/dev/null || true
rm -f /home/coa/coa-env/.agent/versa-agi_ide.md /var/lib/versa-agi/coa/ide_state.json 2>/dev/null || true
# Legacy: remove versa-agi-patch if still present
if [ -f /usr/local/bin/versa-agi-patch ] || [ -L /usr/local/bin/versa-agi-patch ]; then
  rm -f /usr/local/bin/versa-agi-patch
  ok "Removed legacy /usr/local/bin/versa-agi-patch"
fi
if [ -f /usr/local/bin/versa-agi-rekey ] || [ -L /usr/local/bin/versa-agi-rekey ]; then
  rm -f /usr/local/bin/versa-agi-rekey
  ok "Removed /usr/local/bin/versa-agi-rekey"
fi
# Persisted lib (uninstall, update launcher, rekey, ui_lib, setup.ini)
if [ -d /usr/local/lib/versa-agi ]; then
  rm -rf /usr/local/lib/versa-agi
  ok "Removed /usr/local/lib/versa-agi/"
fi
# Sudoers
if [ -f /etc/sudoers.d/versa_agi_agictl ]; then
  rm -f /etc/sudoers.d/versa_agi_agictl
  ok "Removed agictl sudoers entry"
fi

echo ""

# ─── Step 4d: Remove agitop ─────────────────────────
section "Step 4d — Remove agitop"

if [ -f /usr/local/bin/agitop ] || [ -L /usr/local/bin/agitop ]; then
  rm -f /usr/local/bin/agitop
  ok "Removed /usr/local/bin/agitop"
fi
if [ -d /opt/versa-agi ]; then
  rm -rf /opt/versa-agi
  ok "Removed /opt/versa-agi/ (Python venv)"
else
  ok "No agitop venv found"
fi

echo ""

# ─── Step 4e: Remove Backups (purge only) ───────────
if [ "${PURGE_MODE}" = true ]; then
  info "Step 4d: Removing backup snapshots..."

  BACKUP_DIR="/home/${WATCHDOG_USER}/backups"
  if [ -d "${BACKUP_DIR}" ]; then
    rm -rf "${BACKUP_DIR}"
    ok "Removed ${BACKUP_DIR}"
  else
    ok "No backups directory found"
  fi

  echo ""

  # Also remove /etc/versa-agi/ (security directory)
  info "Step 4e: Removing security directory..."
  if [ -d "/etc/versa-agi" ]; then
    rm -rf /etc/versa-agi
    ok "Removed /etc/versa-agi/"
  else
    ok "No security directory found"
  fi

  # Remove agent database directory
  info "Step 4f: Removing agent database..."
  if [ -d "/var/lib/versa-agi" ]; then
    rm -rf /var/lib/versa-agi
    ok "Removed /var/lib/versa-agi/"
  else
    ok "No agent database directory found"
  fi

  echo ""
fi

# ─── Step 4e: Delete VersaVoice Sub-Account (purge) ─
if [ "${PURGE_MODE}" = true ]; then
  info "Step 4e: VersaVoice sub-account cleanup..."

  if [ -n "${SUB_ACCOUNT_ID}" ] && [ -n "${VV_TOKEN}" ]; then
    if [ "${DRY_RUN}" = true ]; then
      info "Would delete: VersaVoice sub-account ${SUB_ACCOUNT_ID} (full Cloud cleanup)"
    else
      info "Deleting sub-account: ${SUB_ACCOUNT_ID} (Firestore, Storage, billing, channels, Auth)..."
      DELETE_RESULT=$(curl -sf -X DELETE \
        -H "Authorization: Bearer ${VV_TOKEN}" \
        "${VV_API_BASE}/accounts/${SUB_ACCOUNT_ID}" 2>/dev/null || true)
      if [ -n "${DELETE_RESULT}" ]; then
        ok "Sub-account deleted: ${SUB_ACCOUNT_ID}"
      else
        warn "Could not delete sub-account ${SUB_ACCOUNT_ID} — may already be removed or API unreachable"
      fi
    fi
  elif [ -n "${SUB_ACCOUNT_ID}" ]; then
    warn "No API token available — cannot delete sub-account ${SUB_ACCOUNT_ID}"
    warn "Delete manually via VersaVoice admin tools."
  else
    ok "No sub-account ID found — nothing to clean up"
  fi

  echo ""
fi

# ─── Step 5: Remove OS Users ────────────────────────
section "Step 5 — OS Users"

if [ "${PURGE_MODE}" = true ]; then
  # Purge: remove users without prompting
  for user in "${COA_USER}" "${WATCHDOG_USER}"; do
    if id "${user}" &>/dev/null; then
      userdel -r "${user}" 2>/dev/null || userdel "${user}" 2>/dev/null || true
      ok "Removed user: ${user}"
    else
      ok "User '${user}' not found (already removed)"
    fi
  done
else
  echo ""
  echo "Remove OS users? This will delete their home directories."
  echo "  Users: ${WATCHDOG_USER}, ${COA_USER}"
  echo ""

  read -p "Remove OS users? [y/N] " -n 1 -r
  echo

  if [[ $REPLY =~ ^[Yy]$ ]]; then
    for user in "${COA_USER}" "${WATCHDOG_USER}"; do
      if id "${user}" &>/dev/null; then
        userdel -r "${user}" 2>/dev/null || userdel "${user}" 2>/dev/null || true
        ok "Removed user: ${user}"
      else
        ok "User '${user}' not found (already removed)"
      fi
    done
  else
    warn "Kept OS users. Remove manually with: sudo userdel -r ${WATCHDOG_USER} && sudo userdel -r ${COA_USER}"
  fi
fi

echo ""

# ─── Step 6: Temp Files & Group Membership ─────────
section "Step 6 — Cleanup"

if [ "${DRY_RUN}" = true ]; then
  TEMP_COUNT=$(ls /tmp/versa_agi_* /tmp/versa-agi-* 2>/dev/null | wc -l || echo "0")
  [ "${TEMP_COUNT}" -gt 0 ] && info "Would remove: ${TEMP_COUNT} temp files from /tmp/"
  if [ -n "${PRIMARY_USER}" ] && id -nG "${PRIMARY_USER}" 2>/dev/null | grep -qw "${COA_USER}"; then
    info "Would remove: ${PRIMARY_USER} from ${COA_USER} group"
  fi
else
  rm -f /tmp/versa_agi_* /tmp/versa-agi-* 2>/dev/null
  rm -rf /home/${WATCHDOG_USER}/.gemini 2>/dev/null || true
  rm -rf /home/${COA_USER}/.gemini 2>/dev/null || true
  # Clean deprecated .gemini cache for all dynamically created agent users
  for agent_dir in /home/agi-*/; do
    if [ -d "${agent_dir}" ]; then
      rm -rf "${agent_dir}.gemini" 2>/dev/null || true
    fi
  done
  ok "Temp files and legacy .gemini caches cleaned for all agents"

  if [ -n "${PRIMARY_USER}" ] && id -nG "${PRIMARY_USER}" 2>/dev/null | grep -qw "${COA_USER}"; then
    gpasswd -d "${PRIMARY_USER}" "${COA_USER}" 2>/dev/null || true
    ok "Removed ${PRIMARY_USER} from ${COA_USER} group"
  fi
fi

# Remove workspace symlink
if [ -n "${WORKSPACE_LINK}" ] && [ -L "${WORKSPACE_LINK}" ]; then
  if [ "${DRY_RUN}" = true ]; then
    info "Would remove: workspace symlink ${WORKSPACE_LINK}"
  else
    rm -f "${WORKSPACE_LINK}"
    ok "Removed workspace symlink: ${WORKSPACE_LINK}"
  fi
fi

echo ""

# ─── Summary ─────────────────────────────────────────
if [ "${DRY_RUN}" = true ]; then
  summary_card "Dry Run Complete" \
    "Status:No changes made"
elif [ "${PURGE_MODE}" = true ]; then
  summary_card "PURGE Complete" \
    "Status:All data destroyed"
else
  summary_card "Uninstall Complete" \
    "Status:Core components removed"
fi

# Context-aware reinstall guidance
if [ -f "${_SCRIPT_DIR}/core-infra/ui_lib.sh" ]; then
  # Running from source repo
  echo -e "  ${DIM:-}Source files were NOT deleted.${RESET:-}"
  step_arrow "To reinstall: ${BOLD:-}sudo ./setup.sh${RESET:-}"
else
  # Running from persisted location (curl install)
  step_arrow "To reinstall: ${BOLD:-}curl -fsSL https://raw.githubusercontent.com/swartzlib7/versa-agi/main/install.sh | sudo bash${RESET:-}"
fi
echo ""
license_notice
echo ""
