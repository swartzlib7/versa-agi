#!/bin/bash
# ─────────────────────────────────────────────────────
# Versa AGi — System Backup
#
# ⛔ MANIFEST: This script is governed by:
#    design/Versa AGi - Backup Manifest.md
#    All capture/exclude changes MUST be reflected there first.
#
# Creates a complete system-level backup of all Versa AGi
# infrastructure, enabling hardware migration without data loss.
#
# Captures entire directories with a global exclude list.
# Warns and aborts if any home directory exceeds 200MB (after excludes).
#
# POST-RESTORE: sudo ./setup.sh MUST be re-run to rebuild
# excluded platform-specific artifacts (venvs, models, deps).
#
# Usage:
#   sudo versa-agi-backup                           # Default output
#   sudo versa-agi-backup --output /path/backup.tar.gz
#   sudo versa-agi-backup --dry-run                 # Preview only
#
# © 2026 VersaVoice AI LLC — Licensed under BSL-1.1
# ─────────────────────────────────────────────────────

set -euo pipefail

VERSION="2.0"

# ─── Global Exclude List ────────────────────────────
# See: design/Versa AGi - Backup Manifest.md § Global Exclude List
# These patterns are excluded from ALL home directory captures.
GLOBAL_EXCLUDES=".ollama .gemini .cache .npm node_modules __pycache__ .local venv .vagrant .vagrant.d VirtualBox VMs"

# ─── Size Gate Threshold ────────────────────────────
SIZE_GATE_MB=200

# ─── UI Library ──────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
UI_LIB="${SCRIPT_DIR}/core-infra/ui_lib.sh"
# Fallback: deployed location (when running from /usr/local/bin/)
[ ! -f "${UI_LIB}" ] && UI_LIB="/home/watchdog/core-infra/ui_lib.sh"
[ ! -f "${UI_LIB}" ] && UI_LIB="/usr/local/lib/versa-agi/ui_lib.sh"
if [ -f "${UI_LIB}" ]; then
  source "${UI_LIB}"
else
  BOLD='\033[1m'
  DIM='\033[2m'
  RESET='\033[0m'
  RED='\033[0;31m'
  GREEN='\033[0;32m'
  YELLOW='\033[1;33m'
  CYAN='\033[0;36m'
  BCYAN='\033[1;36m'
  NC='\033[0m'
  info()  { echo -e "  ${CYAN}●${NC} $*"; }
  ok()    { echo -e "  ${GREEN}✓${NC} $*"; }
  warn()  { echo -e "  ${YELLOW}!${NC} $*"; }
  error() { echo -e "  ${RED}✗${NC} $*"; exit 1; }
  section() { echo -e "\n  ${BOLD}═══ $* ═══${RESET}\n"; }
  banner()  { echo -e "\n  ${BCYAN}${BOLD}V E R S A   A G i${RESET}\n  ${DIM}Agentic General infrastructure${RESET}\n\n  ${DIM}─── ${BCYAN}${1:-Backup}${DIM} ──────────────────────────────────${RESET}\n  ${DIM}v${2:-1.0}${RESET}\n"; }
fi

# ─── Root Check ─────────────────────────────────────
if [ "$(id -u)" -ne 0 ]; then
  error "This script must be run as root (sudo versa-agi-backup)"
fi

# ─── Parse Arguments ────────────────────────────────
OUTPUT_PATH=""
DRY_RUN=false

while [ $# -gt 0 ]; do
  case "$1" in
    --output|-o) OUTPUT_PATH="$2"; shift 2 ;;
    --dry-run)   DRY_RUN=true; shift ;;
    --help|-h)
      echo "Usage: sudo versa-agi-backup [--output /path/backup.tar.gz] [--dry-run]"
      echo ""
      echo "  --output, -o   Output archive path (default: ~/versa-agi-backup-TIMESTAMP.tar.gz)"
      echo "  --dry-run      Preview what would be captured without creating archive"
      exit 0
      ;;
    *) echo "Unknown option: $1. Use --help for usage."; exit 1 ;;
  esac
done

# ─── Read Configuration ────────────────────────────
INI_FILE="/etc/versa-agi/setup.ini"
if [ ! -f "${INI_FILE}" ]; then
  error "setup.ini not found at ${INI_FILE}. Is Versa AGi installed?"
fi

ini_get() {
  local section=$1 key=$2 default=${3:-}
  local value
  value=$(awk -F '=' -v section="${section}" -v key="${key}" '
    /^\[/ { current = substr($0, 2, length($0)-2) }
    current == section && $1 ~ "^"key"$" {
      val = substr($0, index($0,"=")+1)
      gsub(/^[[:space:]]+|[[:space:]]+$/, "", val)
      print val
      exit
    }
  ' "${INI_FILE}" 2>/dev/null)
  echo "${value:-${default}}"
}

WATCHDOG_USER="$(ini_get users watchdog watchdog)"
COA_USER="$(ini_get users coa coa)"
TOPOLOGY="$(ini_get local_ai topology local)"
PRIMARY_USER="${SUDO_USER:-}"
TIMESTAMP=$(date -u '+%Y%m%dT%H%M%SZ')

# Derive home directories
WATCHDOG_HOME="/home/${WATCHDOG_USER}"
COA_HOME="/home/${COA_USER}"

# Default output path
if [ -z "${OUTPUT_PATH}" ]; then
  if [ -n "${PRIMARY_USER}" ]; then
    PU_HOME=$(eval echo "~${PRIMARY_USER}")
    OUTPUT_PATH="${PU_HOME}/.versa-agi-backups/versa-agi-backup-${TIMESTAMP}.tar.gz"
  else
    OUTPUT_PATH="/root/.versa-agi-backups/versa-agi-backup-${TIMESTAMP}.tar.gz"
  fi
fi

# ─── Discover Sub-Agents ───────────────────────────
AGENTS_DB="/var/lib/versa-agi/agents.db"
SUB_AGENT_USERS=()
SUB_AGENT_NAMES=()

if [ -f "${AGENTS_DB}" ]; then
  while IFS='|' read -r ag_name ag_user; do
    [ -z "${ag_name}" ] && continue
    # Skip protected system agents (coa, watchdog)
    if [ "${ag_name}" != "coa" ] && [ "${ag_name}" != "watchdog" ]; then
      SUB_AGENT_NAMES+=("${ag_name}")
      SUB_AGENT_USERS+=("${ag_user}")
    fi
  done < <(sqlite3 "${AGENTS_DB}" "SELECT name, os_user FROM agents;" 2>/dev/null || true)
fi

# ─── Banner ─────────────────────────────────────────
banner "Backup" "${VERSION}"

echo ""
echo -e "  ${DIM:-}Timestamp:   ${TIMESTAMP}${RESET:-}"
echo -e "  ${DIM:-}Output:      ${OUTPUT_PATH}${RESET:-}"
echo -e "  ${DIM:-}Watchdog:    ${WATCHDOG_USER}${RESET:-}"
echo -e "  ${DIM:-}COA:         ${COA_USER}${RESET:-}"
echo -e "  ${DIM:-}Sub-Agents:  ${#SUB_AGENT_NAMES[@]} (${SUB_AGENT_NAMES[*]:-none})${RESET:-}"
echo -e "  ${DIM:-}Topology:    ${TOPOLOGY}${RESET:-}"
echo -e "  ${DIM:-}Dry Run:     ${DRY_RUN}${RESET:-}"
echo ""

if [ "${DRY_RUN}" = true ]; then
  warn "DRY-RUN MODE — no changes will be made"
  echo ""
fi

# ─── Step 1: Pause CRON ────────────────────────────
section "Step 1 — Pause CRON"

EXISTING_CRON=$(crontab -u "${WATCHDOG_USER}" -l 2>/dev/null || true)
CRON_WAS_ACTIVE=false

if echo "${EXISTING_CRON}" | grep -q "^[^#].*lifeline.sh"; then
  CRON_WAS_ACTIVE=true
  if [ "${DRY_RUN}" = true ]; then
    info "Would pause lifeline CRON entry"
  else
    echo "${EXISTING_CRON}" | sed 's|^\(.*/lifeline\.sh.*\)$|#\1|' | \
      crontab -u "${WATCHDOG_USER}" -
    ok "CRON paused (commented out)"
  fi
else
  if echo "${EXISTING_CRON}" | grep -q "^#.*lifeline.sh"; then
    warn "CRON already paused"
    CRON_WAS_ACTIVE=true
  else
    warn "No lifeline CRON entry found"
  fi
fi

echo ""

# ─── Step 2: Drain Active Agents ───────────────────
section "Step 2 — Drain Agents"

GRACE_PERIOD=60

drain_agent() {
  local user=$1
  local waited=0

  if ! id "${user}" &>/dev/null; then
    return
  fi

  if pgrep -u "${user}" -f "harness.agent_harness" &>/dev/null; then
    info "Agent running as ${user} — waiting for natural exit..."
    while pgrep -u "${user}" -f "harness.agent_harness" &>/dev/null && [ ${waited} -lt ${GRACE_PERIOD} ]; do
      sleep 5
      waited=$((waited + 5))
      info "  Waiting... (${waited}s / ${GRACE_PERIOD}s)"
    done

    if pgrep -u "${user}" -f "harness.agent_harness" &>/dev/null; then
      warn "Grace period expired — killing agent processes for ${user}"
      pkill -u "${user}" -f "harness.agent_harness" 2>/dev/null || true
      sleep 2
      pkill -9 -u "${user}" -f "harness.agent_harness" 2>/dev/null || true
      ok "Agent processes killed for ${user}"
    else
      ok "Agent ${user} exited naturally"
    fi
  else
    ok "No running agent for ${user}"
  fi
}

if [ "${DRY_RUN}" = true ]; then
  info "Would drain running agents"
else
  drain_agent "${COA_USER}"
  for sa_user in "${SUB_AGENT_USERS[@]+${SUB_AGENT_USERS[@]}}"; do
    drain_agent "${sa_user}"
  done
fi

echo ""

# ─── Step 3: Stage Backup ─────────────────────────
section "Step 3 — Stage Data"

STAGING_DIR="/tmp/versa-agi-backup-${TIMESTAMP}"

if [ "${DRY_RUN}" = false ]; then
  mkdir -p "${STAGING_DIR}"
fi

# Helper: capture a path into the staging directory
# Preserves full path structure under staging dir
capture() {
  local src_path="$1"
  local label="${2:-}"
  local excludes="${3:-}"

  if [ ! -e "${src_path}" ]; then
    if [ -n "${label}" ]; then info "Skipped (not found): ${label}"; fi
    return 0
  fi

  if [ "${DRY_RUN}" = true ]; then
    if [ -d "${src_path}" ]; then
      local size
      size=$(du -sh "${src_path}" 2>/dev/null | cut -f1)
      info "Would capture: ${src_path} (${size})"
    elif [ -L "${src_path}" ]; then
      info "Would capture: ${src_path} → $(readlink "${src_path}")"
    else
      info "Would capture: ${src_path}"
    fi
    return 0
  fi

  # Create the parent directory structure in staging
  local dest="${STAGING_DIR}${src_path}"
  mkdir -p "$(dirname "${dest}")"

  if [ -L "${src_path}" ]; then
    # Preserve symlinks as symlinks
    cp -a "${src_path}" "${dest}"
  elif [ -d "${src_path}" ]; then
    # Build rsync --exclude flags from space-separated list
    local rsync_args=()
    if [ -n "${excludes}" ]; then
      for _exc in ${excludes}; do
        rsync_args+=(--exclude="${_exc}")
      done
    fi
    rsync -a "${rsync_args[@]+${rsync_args[@]}}" "${src_path}/" "${dest}/"
  else
    cp -a "${src_path}" "${dest}"
  fi

  if [ -n "${label}" ]; then ok "Captured: ${label}"; fi
  return 0
}

# Helper: size gate — warns and aborts if a home directory is too large
# Usage: size_gate "/home/coa" "coa" "exclude1 exclude2"
size_gate() {
  local dir_path="$1"
  local user_label="$2"
  local excludes="$3"

  if [ ! -d "${dir_path}" ]; then
    return 0
  fi

  # Build rsync-compatible du with excludes
  local du_excludes=()
  for _exc in ${excludes}; do
    du_excludes+=(--exclude="${_exc}")
  done

  local size_bytes
  size_bytes=$(du -sb "${du_excludes[@]+${du_excludes[@]}}" "${dir_path}" 2>/dev/null | tail -1 | cut -f1)
  size_bytes=${size_bytes:-0}

  local size_mb=$(( size_bytes / 1048576 ))
  local size_human
  size_human=$(numfmt --to=iec "${size_bytes}" 2>/dev/null || echo "${size_mb}MB")

  if [ "${size_mb}" -gt "${SIZE_GATE_MB}" ]; then
    echo ""
    warn "${user_label} home is ${size_human} (after excludes) — exceeds ${SIZE_GATE_MB}MB threshold"
    info "Top 5 largest subdirectories:"
    du -h "${du_excludes[@]+${du_excludes[@]}}" --max-depth=2 "${dir_path}" 2>/dev/null | \
      sort -rh | head -6 | tail -5 | while read -r _sz _dir; do
        info "  ${_sz}  ${_dir}"
      done
    echo ""

    if [ "${DRY_RUN}" = true ]; then
      warn "DRY-RUN: Would prompt for confirmation here"
      return 0
    fi

    read -p "  Continue with backup? [y/N]: " -n 1 -r
    echo ""
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
      echo ""
      error "Backup ABORTED. Please investigate and free space in ${dir_path}, then re-run the backup."
    fi
  else
    ok "${user_label} home: ${size_human} (within ${SIZE_GATE_MB}MB threshold)"
  fi
}

# ── 3a: SQLite Databases ──
info "3a: Databases..."
capture "/var/lib/versa-agi/" "Persistent data (/var/lib/versa-agi/)"

# ── 3b: Configuration ──
info "3b: Configuration..."
capture "/etc/versa-agi/" "System config (/etc/versa-agi/)"

# ── 3c: Log files ──
info "3c: Logs..."
capture "/var/log/versa-agi-lifeline.log" "Lifeline log"
capture "/var/log/versa-agi-sentinel.log" "Sentinel log"
capture "/var/log/versa-agi-archive/" "Archived logs"

# ── 3d: Watchdog home ──
info "3d: Watchdog home..."
size_gate "${WATCHDOG_HOME}" "Watchdog" "${GLOBAL_EXCLUDES}"
capture "${WATCHDOG_HOME}/" "Watchdog home (${WATCHDOG_HOME}/)" "${GLOBAL_EXCLUDES}"

# ── 3e: COA home ──
info "3e: COA home..."
size_gate "${COA_HOME}" "COA" "${GLOBAL_EXCLUDES}"
capture "${COA_HOME}/" "COA home (${COA_HOME}/)" "${GLOBAL_EXCLUDES}"

# ── 3f: Sub-agent homes ──
info "3f: Sub-agent homes..."
for sa_user in "${SUB_AGENT_USERS[@]+${SUB_AGENT_USERS[@]}}"; do
  SA_HOME="/home/${sa_user}"
  [ -d "${SA_HOME}" ] || continue  # Skip if home doesn't exist
  size_gate "${SA_HOME}" "Sub-agent ${sa_user}" "${GLOBAL_EXCLUDES}"
  capture "${SA_HOME}/" "Sub-agent: ${sa_user} (${SA_HOME}/)" "${GLOBAL_EXCLUDES}"
done

# ── 3g: System binaries ──
info "3g: System binaries..."
for bin_path in \
  /usr/local/bin/agictl \
  /usr/local/bin/agitop \
  /usr/local/bin/vcoa \
  /usr/local/bin/versa-agi-uninstall \
  /usr/local/bin/versa-agi-backup \
  /usr/local/bin/versa-agi-update \
  /usr/local/bin/versa-agi-rekey; do
  capture "${bin_path}"
done
capture "/usr/local/lib/versa-agi/" "Persisted lib (/usr/local/lib/versa-agi/)" "${GLOBAL_EXCLUDES}"
ok "System binaries captured"

# ── 3h: Systemd units ──
info "3h: Systemd units..."
capture "/etc/systemd/system/versa-agi-sentinel.service" "Sentinel service unit"
capture "/etc/systemd/system/versa-agi-tunnel.service" "SSH tunnel service unit"

# ── 3i: Sudoers ──
info "3i: Sudoers..."
capture "/etc/sudoers.d/versa_agi_watchdog" "Watchdog sudoers"
capture "/etc/sudoers.d/versa_agi_agictl" "agictl sudoers"

# ── 3j: CRON tab ──
info "3j: CRON tab..."
if [ "${DRY_RUN}" = true ]; then
  info "Would capture watchdog CRON tab"
else
  _cron_out=$(crontab -u "${WATCHDOG_USER}" -l 2>/dev/null || true)
  if [ -n "${_cron_out}" ]; then
    echo "${_cron_out}" > "${STAGING_DIR}/cron_watchdog.txt"
    ok "Captured: watchdog CRON tab → cron_watchdog.txt"
  else
    info "No CRON tab for ${WATCHDOG_USER}"
  fi
fi


# ── 3k: Primary User data ──
info "3k: Primary User data..."
if [ -n "${PRIMARY_USER}" ]; then
  PU_HOME=$(eval echo "~${PRIMARY_USER}")

  # ~/.versa-agi/ — repo clone and setup.ini symlink
  capture "${PU_HOME}/.versa-agi/" "~/.versa-agi/"

  # Convenience symlinks (workspace, attachments)
  for link_name in agi-workspace agi-attachments; do
    if [ -L "${PU_HOME}/${link_name}" ]; then
      capture "${PU_HOME}/${link_name}"
    fi
  done
  ok "Primary User data captured"
else
  warn "No SUDO_USER — Primary User data skipped"
fi

echo ""

# ─── Step 4: Generate Manifest ─────────────────────
section "Step 4 — Manifest"

if [ "${DRY_RUN}" = true ]; then
  info "Would generate manifest.json"
else
  # Collect OS user metadata
  USER_ACCOUNTS="[]"
  for user in "${WATCHDOG_USER}" "${COA_USER}" "${SUB_AGENT_USERS[@]+${SUB_AGENT_USERS[@]}}"; do
    if id "${user}" &>/dev/null; then
      _uid=$(id -u "${user}")
      _gid=$(id -g "${user}")
      _groups=$(id -Gn "${user}" | tr ' ' ',')
      _shell=$(getent passwd "${user}" | cut -d: -f7)
      _home=$(getent passwd "${user}" | cut -d: -f6)
      USER_ACCOUNTS=$(echo "${USER_ACCOUNTS}" | jq \
        --arg name "${user}" \
        --arg uid "${_uid}" \
        --arg gid "${_gid}" \
        --arg groups "${_groups}" \
        --arg shell "${_shell}" \
        --arg home "${_home}" \
        '. += [{"name": $name, "uid": ($uid | tonumber), "gid": ($gid | tonumber), "groups": $groups, "shell": $shell, "home": $home}]')
    fi
  done

  # Collect agent metadata
  AGENT_LIST="[]"
  if [ -f "${AGENTS_DB}" ]; then
    while IFS='|' read -r ag_name ag_user ag_status ag_model; do
      [ -z "${ag_name}" ] && continue
      AGENT_LIST=$(echo "${AGENT_LIST}" | jq \
        --arg name "${ag_name}" \
        --arg os_user "${ag_user}" \
        --arg status "${ag_status}" \
        --arg model "${ag_model}" \
        '. += [{"name": $name, "os_user": $os_user, "status": $status, "model": $model}]')
    done < <(sqlite3 "${AGENTS_DB}" "SELECT name, os_user, status, model FROM agents;" 2>/dev/null || true)
  fi

  # Primary User symlinks
  PU_SYMLINKS="[]"
  if [ -n "${PRIMARY_USER}" ]; then
    PU_HOME=$(eval echo "~${PRIMARY_USER}")
    for link_name in agi-workspace agi-attachments; do
      if [ -L "${PU_HOME}/${link_name}" ]; then
        _target=$(readlink "${PU_HOME}/${link_name}")
        PU_SYMLINKS=$(echo "${PU_SYMLINKS}" | jq \
          --arg path "${PU_HOME}/${link_name}" \
          --arg target "${_target}" \
          '. += [{"path": $path, "target": $target}]')
      fi
    done
  fi

  # Build manifest
  _os_string=$(lsb_release -d -s 2>/dev/null || grep PRETTY_NAME /etc/os-release 2>/dev/null | cut -d= -f2 | tr -d '"' || echo "unknown")
  jq -n \
    --arg version "${VERSION}" \
    --arg timestamp "${TIMESTAMP}" \
    --arg hostname "$(hostname)" \
    --arg os "${_os_string}" \
    --arg kernel "$(uname -r)" \
    --arg watchdog_user "${WATCHDOG_USER}" \
    --arg coa_user "${COA_USER}" \
    --arg primary_user "${PRIMARY_USER}" \
    --arg primary_user_home "$(eval echo "~${PRIMARY_USER}" 2>/dev/null || echo "")" \
    --arg topology "${TOPOLOGY}" \
    --argjson agents "${AGENT_LIST}" \
    --argjson users "${USER_ACCOUNTS}" \
    --argjson pu_symlinks "${PU_SYMLINKS}" \
    '{
      backup_version: $version,
      created_at: $timestamp,
      source_hostname: $hostname,
      os: $os,
      kernel: $kernel,
      watchdog_user: $watchdog_user,
      coa_user: $coa_user,
      primary_user: $primary_user,
      primary_user_home: $primary_user_home,
      topology: $topology,
      agents: $agents,
      os_users: $users,
      primary_user_symlinks: $pu_symlinks
    }' > "${STAGING_DIR}/manifest.json"

  ok "Manifest generated → manifest.json"
  info "  Agents: $(echo "${AGENT_LIST}" | jq length)"
  info "  OS Users: $(echo "${USER_ACCOUNTS}" | jq length)"
fi

echo ""

# ─── Step 5: Embed Restore Script ──────────────────
section "Step 5 — Embed Restore"

RESTORE_SOURCE="${SCRIPT_DIR}/restore.sh"
# Fallback: if running from deployed location, try system library
[ ! -f "${RESTORE_SOURCE}" ] && RESTORE_SOURCE="/usr/local/lib/versa-agi/restore.sh"
# Fallback: try source tree
[ ! -f "${RESTORE_SOURCE}" ] && RESTORE_SOURCE="/home/${WATCHDOG_USER}/core-infra/../restore.sh"

if [ "${DRY_RUN}" = true ]; then
  if [ -f "${RESTORE_SOURCE}" ]; then
    info "Would embed restore.sh into archive"
  else
    warn "restore.sh not found at ${RESTORE_SOURCE} — would skip embedding"
  fi
else
  if [ -f "${RESTORE_SOURCE}" ]; then
    cp "${RESTORE_SOURCE}" "${STAGING_DIR}/restore.sh"
    chmod +x "${STAGING_DIR}/restore.sh"
    ok "restore.sh embedded in archive"
  else
    warn "restore.sh not found — archive will not include self-contained restore"
    info "Place restore.sh alongside this archive for restoration."
  fi
fi

echo ""

# ─── Step 6: Create Archive ───────────────────────
section "Step 6 — Archive"

if [ "${DRY_RUN}" = true ]; then
  info "Would create archive at: ${OUTPUT_PATH}"
  # Show estimated size
  TOTAL_SIZE=0
  for path in /var/lib/versa-agi /etc/versa-agi /var/log/versa-agi-lifeline.log \
    /var/log/versa-agi-sentinel.log /var/log/versa-agi-archive \
    "${WATCHDOG_HOME}" "${COA_HOME}" /usr/local/lib/versa-agi; do
    if [ -e "${path}" ]; then
      _s=$(du -sb "${path}" 2>/dev/null | cut -f1)
      TOTAL_SIZE=$((TOTAL_SIZE + ${_s:-0}))
    fi
  done
  for sa_user in "${SUB_AGENT_USERS[@]+${SUB_AGENT_USERS[@]}}"; do
    SA_HOME="/home/${sa_user}"
    if [ -d "${SA_HOME}" ]; then
      _s=$(du -sb "${SA_HOME}" 2>/dev/null | cut -f1)
      TOTAL_SIZE=$((TOTAL_SIZE + ${_s:-0}))
    fi
  done
  info "Estimated uncompressed size: $(numfmt --to=iec ${TOTAL_SIZE} 2>/dev/null || echo "${TOTAL_SIZE} bytes")"
else
  # Create the archive
  mkdir -p "$(dirname "${OUTPUT_PATH}")"

  # Clean up orphaned staging dirs from previously cancelled runs
  for _old_staging in /tmp/versa-agi-backup-*/; do
    [ -d "${_old_staging}" ] && [ "${_old_staging%/}" != "${STAGING_DIR}" ] && \
      rm -rf "${_old_staging}" && info "Cleaned orphan: ${_old_staging}"
  done

  # Show staging size so the user knows what to expect
  STAGING_SIZE=$(du -sh "${STAGING_DIR}" | cut -f1)
  info "Staging: ${STAGING_SIZE} (${STAGING_DIR}) → compressing..."

  # Archive with progress feedback
  _tar_start=$(date +%s)
  if command -v pv &>/dev/null; then
    # pv gives a real progress bar
    tar cf - -C "${STAGING_DIR}" . | pv -s "$(du -sb "${STAGING_DIR}" | cut -f1)" | gzip > "${OUTPUT_PATH}"
  else
    # Fallback: verbose tar piped through a dot counter
    tar czf "${OUTPUT_PATH}" -C "${STAGING_DIR}" . --checkpoint=500 \
      --checkpoint-action=exec='printf "."' 2>/dev/null
    echo ""  # newline after dots
  fi
  _tar_end=$(date +%s)
  _tar_elapsed=$(( _tar_end - _tar_start ))

  # Set ownership to Primary User if available
  if [ -n "${PRIMARY_USER}" ]; then
    chown "${PRIMARY_USER}:${PRIMARY_USER}" "${OUTPUT_PATH}"
  fi

  ARCHIVE_SIZE=$(du -sh "${OUTPUT_PATH}" | cut -f1)
  ok "Archive created: ${OUTPUT_PATH} (${ARCHIVE_SIZE}, ${_tar_elapsed}s)"

  # Cleanup staging
  rm -rf "${STAGING_DIR}"
  ok "Staging directory cleaned"
fi

echo ""

# ─── Summary ──────────────────────────────────────
echo ""
echo -e "  ${BCYAN:-}${BOLD:-}╭────────────────────────────────────────╮${RESET:-}"
echo -e "  ${BCYAN:-}${BOLD:-}│  Backup Complete                       │${RESET:-}"
echo -e "  ${BCYAN:-}${BOLD:-}├────────────────────────────────────────┤${RESET:-}"
echo -e "  ${BCYAN:-}${BOLD:-}│${RESET:-}  Timestamp        ${TIMESTAMP}"
echo -e "  ${BCYAN:-}${BOLD:-}│${RESET:-}  Agents            ${#SUB_AGENT_NAMES[@]} sub-agents + COA + Watchdog"
if [ "${DRY_RUN}" = false ]; then
echo -e "  ${BCYAN:-}${BOLD:-}│${RESET:-}  Archive           ${OUTPUT_PATH}"
echo -e "  ${BCYAN:-}${BOLD:-}│${RESET:-}  Size              ${ARCHIVE_SIZE:-n/a}"
fi
echo -e "  ${BCYAN:-}${BOLD:-}├────────────────────────────────────────┤${RESET:-}"
echo -e "  ${BCYAN:-}${BOLD:-}│${RESET:-}  ${YELLOW:-}⚠ CRON is PAUSED.${RESET:-}"
echo -e "  ${BCYAN:-}${BOLD:-}│${RESET:-}  ${DIM:-}Resume: agitop → Controls → Resume CRON${RESET:-}"
echo -e "  ${BCYAN:-}${BOLD:-}├────────────────────────────────────────┤${RESET:-}"
echo -e "  ${BCYAN:-}${BOLD:-}│${RESET:-}  ${YELLOW:-}⚠ POST-RESTORE: You MUST re-run${RESET:-}"
echo -e "  ${BCYAN:-}${BOLD:-}│${RESET:-}  ${BOLD:-}  sudo ./setup.sh${RESET:-}"
echo -e "  ${BCYAN:-}${BOLD:-}│${RESET:-}  ${DIM:-}  to rebuild venvs, models, and deps.${RESET:-}"
echo -e "  ${BCYAN:-}${BOLD:-}╰────────────────────────────────────────╯${RESET:-}"

echo ""

if [ "${DRY_RUN}" = false ]; then
  echo -e "  ${DIM:-}To restore on new hardware:${RESET:-}"
  echo -e "  ${BOLD:-}  sudo tar -xzpf $(basename "${OUTPUT_PATH}") -C /tmp/restore && cd /tmp/restore && sudo ./restore.sh${RESET:-}"
fi
echo ""
