#!/bin/bash
# ─────────────────────────────────────────────────────
# Versa AGi — Rekey (Gemini API Key Rotation)
#
# Rotates the Gemini API key across all locations:
#   1. /etc/versa-agi/setup.ini            (canonical config)
#   2. /etc/versa-agi/*.env                (agent runtime env)
#   3. ~/.bashrc for each agent OS user    (interactive sessions)
#
# Usage:
#   sudo versa-agi-rekey                   # Interactive prompt
#   sudo versa-agi-rekey --key AIza...     # Non-interactive
#   sudo versa-agi-rekey --dry-run         # Preview only
#
# © 2026 VersaVoice AI LLC — Licensed under BSL-1.1
# ─────────────────────────────────────────────────────

set -euo pipefail

# ─── Configuration ──────────────────────────────────
PERSIST_DIR="/usr/local/lib/versa-agi"
VERSA_ETC="/etc/versa-agi"
AGENTS_DB="/var/lib/versa-agi/agents.db"
VERSION="3.0"

# ─── Colors ─────────────────────────────────────────
BOLD='\033[1m'
DIM='\033[2m'
RESET='\033[0m'
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BCYAN='\033[1;36m'
WHITE='\033[1;37m'
DGRAY='\033[90m'
NC='\033[0m'

ok()    { echo -e "  ${GREEN}✓${NC} $*"; }
fail()  { echo -e "  ${RED}✗${NC} $*"; }
warn()  { echo -e "  ${YELLOW}!${NC} $*"; }
info()  { echo -e "  ${CYAN}●${NC} $*"; }

# ─── Root Check ─────────────────────────────────────
if [ "$(id -u)" -ne 0 ]; then
  fail "This must be run as root."
  echo ""
  echo -e "  ${DIM}Run with: sudo versa-agi-rekey${RESET}"
  echo ""
  exit 1
fi

# ─── Parse Arguments ────────────────────────────────
NEW_KEY=""
DRY_RUN=false

while [ $# -gt 0 ]; do
  case "$1" in
    --key)      NEW_KEY="$2"; shift 2 ;;
    --dry-run)  DRY_RUN=true; shift ;;
    --help|-h)
      echo "Usage: sudo versa-agi-rekey [--key <new-api-key>] [--dry-run]"
      echo ""
      echo "  --key <key>   Provide API key non-interactively"
      echo "  --dry-run     Show what would change without writing"
      exit 0
      ;;
    *) echo "Unknown option: $1. Use --help for usage."; exit 1 ;;
  esac
done

# ─── Banner ─────────────────────────────────────────
echo ""
echo -e "  ${BCYAN}${BOLD}V E R S A   A G i${RESET}"
echo -e "  ${DIM}Agentic General infrastructure${RESET}"
echo ""
echo -e "  ${DGRAY}─── ${BCYAN}Rekey${DGRAY} ──────────────────────────────────${RESET}"
echo -e "  ${DGRAY}v${VERSION}${RESET}"
echo ""

if [ "${DRY_RUN}" = true ]; then
  warn "DRY-RUN MODE — no changes will be made"
  echo ""
fi

# ─── Detect Current Auth Method ─────────────────
INI_FILE="/etc/versa-agi/setup.ini"
# Legacy fallback for pre-migration installs
if [ ! -f "${INI_FILE}" ] && [ -f "${PERSIST_DIR}/setup.ini" ]; then
  INI_FILE="${PERSIST_DIR}/setup.ini"
fi
CURRENT_AUTH_METHOD="api_key"

if [ -f "${INI_FILE}" ]; then
  CURRENT_AUTH_METHOD=$(awk -F '=' '/^\[gemini\]/{found=1} found && /^auth_method=/{print $2; exit}' "${INI_FILE}" 2>/dev/null | tr -d '[:space:]')
  CURRENT_AUTH_METHOD="${CURRENT_AUTH_METHOD:-api_key}"
fi

if [ "${CURRENT_AUTH_METHOD}" != "api_key" ]; then
  fail "Current auth method is '${CURRENT_AUTH_METHOD}', not 'api_key'."
  echo -e "     ${DIM}Rekey only applies to Gemini API Key authentication.${RESET}"
  echo -e "     ${DIM}For Vertex AI credentials, update the service account key manually.${RESET}"
  echo ""
  exit 1
fi

# ─── Show Current Key (masked) ──────────────────────
CURRENT_KEY=""
if [ -f "${INI_FILE}" ]; then
  CURRENT_KEY=$(awk -F '=' '/^\[gemini\]/{found=1} found && /^api_key=/{print $2; exit}' "${INI_FILE}" 2>/dev/null | tr -d '[:space:]')
fi

if [ -n "${CURRENT_KEY}" ]; then
  MASKED="${CURRENT_KEY:0:8}...${CURRENT_KEY: -4}"
  info "Current key: ${MASKED}"
else
  warn "No current key found in setup.ini"
fi

# ─── Prompt for New Key ────────────────────────────
if [ -z "${NEW_KEY}" ]; then
  echo ""
  read -p "  Enter new Gemini API Key: " NEW_KEY
  while [ -z "${NEW_KEY}" ]; do
    echo -e "  ${RED}An API key is required.${NC}"
    read -p "  Enter new Gemini API Key: " NEW_KEY
  done
fi

NEW_MASKED="${NEW_KEY:0:8}...${NEW_KEY: -4}"
info "New key: ${NEW_MASKED}"
echo ""

# ─── Collect Agent Users ────────────────────────────
# Read from agents.db (authoritative registry of all agents + OS users)
AGENT_USERS=()
if [ -f "${AGENTS_DB}" ] && command -v sqlite3 &>/dev/null; then
  while IFS= read -r user; do
    [ -n "${user}" ] && AGENT_USERS+=("${user}")
  done < <(sqlite3 "${AGENTS_DB}" "SELECT DISTINCT os_user FROM agents;" 2>/dev/null || true)
fi

# Fallback: derive from .env filenames if DB unavailable
if [ ${#AGENT_USERS[@]} -eq 0 ]; then
  for env_file in "${VERSA_ETC}"/*.env; do
    [ -f "${env_file}" ] || continue
    base=$(basename "${env_file}" .env)
    [ "${base}" = "paths" ] && continue
    AGENT_USERS+=("${base}")
  done
fi

UPDATED=0

# ─── Step 1: Update setup.ini ──────────────────────
echo -e "  ${DGRAY}─── ${WHITE}${BOLD}Step 1${RESET}${DGRAY} — setup.ini ────────────────────────${RESET}"
echo ""

if [ -f "${INI_FILE}" ]; then
  if [ "${DRY_RUN}" = true ]; then
    info "Would update: ${INI_FILE}"
  else
    sed -i "s|^api_key=.*|api_key=${NEW_KEY}|" "${INI_FILE}"
    ok "Updated ${INI_FILE}"
    UPDATED=$((UPDATED + 1))
  fi
else
  warn "setup.ini not found at ${INI_FILE} — skipping"
fi

echo ""

# ─── Step 2: Update /etc/versa-agi/*.env ───────────
echo -e "  ${DGRAY}─── ${WHITE}${BOLD}Step 2${RESET}${DGRAY} — Agent .env files ─────────────────${RESET}"
echo ""

for env_file in "${VERSA_ETC}"/*.env; do
  [ -f "${env_file}" ] || continue
  base=$(basename "${env_file}")
  [ "${base}" = "paths.env" ] && continue

  if grep -q "^GEMINI_API_KEY=" "${env_file}" 2>/dev/null; then
    if [ "${DRY_RUN}" = true ]; then
      info "Would update: ${env_file}"
    else
      sed -i "s|^GEMINI_API_KEY=.*|GEMINI_API_KEY=${NEW_KEY}|" "${env_file}"
      ok "Updated ${env_file}"
      UPDATED=$((UPDATED + 1))
    fi
  else
    info "Skipped ${base} (no GEMINI_API_KEY — likely Vertex or local)"
  fi
done

echo ""

# ─── Step 3: Update .bashrc for agent users ────────
echo -e "  ${DGRAY}─── ${WHITE}${BOLD}Step 3${RESET}${DGRAY} — Agent .bashrc files ──────────────${RESET}"
echo ""

for user in "${AGENT_USERS[@]}"; do
  # Skip watchdog — it does not run agent harness interactively
  [ "${user}" = "watchdog" ] && continue

  home_dir=$(eval echo "~${user}" 2>/dev/null)
  bashrc="${home_dir}/.bashrc"

  if [ ! -f "${bashrc}" ]; then
    info "No .bashrc for ${user} — skipping"
    continue
  fi

  if grep -q "GEMINI_API_KEY" "${bashrc}" 2>/dev/null; then
    if [ "${DRY_RUN}" = true ]; then
      info "Would update: ${bashrc}"
    else
      sed -i "s|export GEMINI_API_KEY=.*|export GEMINI_API_KEY=\"${NEW_KEY}\"|" "${bashrc}"
      ok "Updated ${bashrc}"
      UPDATED=$((UPDATED + 1))
    fi
  else
    info "No GEMINI_API_KEY in ${user}'s .bashrc — skipping"
  fi
done

echo ""

# ─── Step 4: Restart Sentinel (if active) ──────────
echo -e "  ${DGRAY}─── ${WHITE}${BOLD}Step 4${RESET}${DGRAY} — Service restart ──────────────────${RESET}"
echo ""

if systemctl is-active --quiet versa-agi-sentinel 2>/dev/null; then
  if [ "${DRY_RUN}" = true ]; then
    info "Would restart: versa-agi-sentinel"
  else
    systemctl restart versa-agi-sentinel
    ok "Sentinel service restarted"
  fi
else
  ok "Sentinel not running (CRON-based lifeline will pick up new key automatically)"
fi

echo ""

# ─── Summary ─────────────────────────────────────────
if [ "${DRY_RUN}" = true ]; then
  echo -e "  ${BCYAN}╭────────────────────────────────────────╮${RESET}"
  echo -e "  ${BCYAN}│${RESET}  ${BOLD}${WHITE}Dry Run Complete${RESET}"
  echo -e "  ${BCYAN}├────────────────────────────────────────┤${RESET}"
  echo -e "  ${BCYAN}│${RESET}  ${DIM}Status${RESET}   No changes made"
  echo -e "  ${BCYAN}│${RESET}  ${DIM}New key${RESET}  ${NEW_MASKED}"
  echo -e "  ${BCYAN}╰────────────────────────────────────────╯${RESET}"
else
  echo -e "  ${BCYAN}╭────────────────────────────────────────╮${RESET}"
  echo -e "  ${BCYAN}│${RESET}  ${BOLD}${WHITE}Rekey Complete${RESET}"
  echo -e "  ${BCYAN}├────────────────────────────────────────┤${RESET}"
  echo -e "  ${BCYAN}│${RESET}  ${DIM}Updated${RESET}  ${UPDATED} file(s)"
  echo -e "  ${BCYAN}│${RESET}  ${DIM}New key${RESET}  ${NEW_MASKED}"
  echo -e "  ${BCYAN}╰────────────────────────────────────────╯${RESET}"
  echo ""
  echo -e "  ${DIM}The new key will take effect on the next agent cycle.${RESET}"
  echo -e "  ${DIM}Active sessions (if any) will continue using the old key${RESET}"
  echo -e "  ${DIM}until they complete.${RESET}"
fi
echo ""
echo -e "  ${DIM}Licensed under BSL-1.1 · © $(date +%Y) VersaVoice AI LLC${RESET}"
echo ""
