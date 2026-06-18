#!/bin/bash
# ─────────────────────────────────────────────────────
# Versa AGi — System Restore
#
# Self-contained restore script embedded in every backup
# archive. Recreates the full Versa AGi system on new
# or existing hardware from a backup archive.
#
# This script is designed to run from the extracted
# archive directory — it reads manifest.json and restores
# all captured data to their original system paths.
#
# Usage:
#   tar xzf versa-agi-backup-*.tar.gz -C /tmp/restore
#   cd /tmp/restore
#   sudo ./restore.sh
#   sudo ./restore.sh --dry-run
#
# © 2026 VersaVoice AI LLC — Licensed under BSL-1.1
# ─────────────────────────────────────────────────────

set -euo pipefail

VERSION="1.0"

# ─── Colors & Helpers ───────────────────────────────
BOLD='\033[1m'
DIM='\033[2m'
RESET='\033[0m'
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BCYAN='\033[1;36m'
NC='\033[0m'

info()    { echo -e "  ${CYAN}●${NC} $*"; }
ok()      { echo -e "  ${GREEN}✓${NC} $*"; }
warn()    { echo -e "  ${YELLOW}!${NC} $*"; }
fail()    { echo -e "  ${RED}✗${NC} $*"; }
section() { echo -e "\n  ${BOLD}═══ $* ═══${RESET}\n"; }

# ─── Root Check ─────────────────────────────────────
if [ "$(id -u)" -ne 0 ]; then
  fail "This script must be run as root (sudo ./restore.sh)"
  exit 1
fi

# ─── Parse Arguments ────────────────────────────────
DRY_RUN=false
AUTO_YES=false

while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run)  DRY_RUN=true; shift ;;
    --yes|-y)   AUTO_YES=true; shift ;;
    --help|-h)
      echo "Usage: sudo ./restore.sh [--dry-run] [--yes]"
      echo ""
      echo "  --dry-run   Preview what would be restored without making changes"
      echo "  --yes       Auto-install missing prerequisites without prompting"
      echo ""
      echo "Run this from the extracted backup directory containing manifest.json."
      exit 0
      ;;
    *) echo "Unknown option: $1. Use --help for usage."; exit 1 ;;
  esac
done

# ─── Locate Archive Data ───────────────────────────
ARCHIVE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MANIFEST="${ARCHIVE_DIR}/manifest.json"

if [ ! -f "${MANIFEST}" ]; then
  fail "manifest.json not found in ${ARCHIVE_DIR}"
  echo -e "  ${DIM}Run this script from the extracted backup directory.${RESET}"
  exit 1
fi

# ─── Read Manifest ─────────────────────────────────
BACKUP_VERSION=$(jq -r '.backup_version' "${MANIFEST}")
BACKUP_TIMESTAMP=$(jq -r '.created_at' "${MANIFEST}")
SOURCE_HOSTNAME=$(jq -r '.source_hostname' "${MANIFEST}")
SOURCE_OS=$(jq -r '.os' "${MANIFEST}")
WATCHDOG_USER=$(jq -r '.watchdog_user' "${MANIFEST}")
COA_USER=$(jq -r '.coa_user' "${MANIFEST}")
PRIMARY_USER=$(jq -r '.primary_user // empty' "${MANIFEST}")
PRIMARY_USER_HOME=$(jq -r '.primary_user_home // empty' "${MANIFEST}")
AGENT_COUNT=$(jq '.agents | length' "${MANIFEST}")
USER_COUNT=$(jq '.os_users | length' "${MANIFEST}")
TOPOLOGY=$(jq -r '.topology // "local"' "${MANIFEST}")

# ─── Banner ─────────────────────────────────────────
echo ""
echo -e "  ${BCYAN}${BOLD}V E R S A   A G i${RESET}"
echo -e "  ${DIM}Agentic General infrastructure${RESET}"
echo ""
echo -e "  ${DIM}─── ${BCYAN}Restore${DIM} ──────────────────────────────────${RESET}"
echo -e "  ${DIM}v${VERSION}${RESET}"
echo ""
echo -e "  ${DIM}Source:       ${SOURCE_HOSTNAME} (${SOURCE_OS})${RESET}"
echo -e "  ${DIM}Backup:      ${BACKUP_TIMESTAMP}${RESET}"
echo -e "  ${DIM}Agents:      ${AGENT_COUNT}${RESET}"
echo -e "  ${DIM}OS Users:    ${USER_COUNT}${RESET}"
echo -e "  ${DIM}Watchdog:    ${WATCHDOG_USER}${RESET}"
echo -e "  ${DIM}COA:         ${COA_USER}${RESET}"
echo -e "  ${DIM}Topology:    ${TOPOLOGY}${RESET}"
echo -e "  ${DIM}Dry Run:     ${DRY_RUN}${RESET}"
echo ""

if [ "${DRY_RUN}" = true ]; then
  warn "DRY-RUN MODE — no changes will be made"
  echo ""
fi

# ─── Step 1: OS Compatibility ──────────────────────
section "Step 1 — Compatibility Check"

if [ "$(uname)" != "Linux" ]; then
  fail "Restore is only supported on Linux."
  exit 1
fi
ok "Linux detected: $(lsb_release -d -s 2>/dev/null || cat /etc/os-release 2>/dev/null | grep PRETTY_NAME | cut -d= -f2 | tr -d '"')"

echo ""


# ─── Step 2: Recreate OS Groups & Users ────────────
section "Step 2 — OS Users & Groups"

# Create agi_agents group
if getent group agi_agents &>/dev/null; then
  ok "Group agi_agents already exists"
else
  if [ "${DRY_RUN}" = true ]; then
    info "Would create group: agi_agents"
  else
    groupadd agi_agents
    ok "Created group: agi_agents"
  fi
fi

# Recreate users from manifest
jq -c '.os_users[]' "${MANIFEST}" | while read -r user_json; do
  _name=$(echo "${user_json}" | jq -r '.name')
  _uid=$(echo "${user_json}" | jq -r '.uid')
  _gid=$(echo "${user_json}" | jq -r '.gid')
  _groups=$(echo "${user_json}" | jq -r '.groups')
  _shell=$(echo "${user_json}" | jq -r '.shell')
  _home=$(echo "${user_json}" | jq -r '.home')

  # Ensure primary group exists (even if user already exists — group may have been deleted by uninstall)
  if [ "${DRY_RUN}" = false ]; then
    if ! getent group "${_name}" &>/dev/null; then
      groupadd -g "${_gid}" "${_name}" 2>/dev/null || groupadd "${_name}" 2>/dev/null || true
      ok "Created group: ${_name} (gid=${_gid})"
    fi
  fi

  if id "${_name}" &>/dev/null; then
    ok "User ${_name} already exists (uid=$(id -u "${_name}"))"
  else
    if [ "${DRY_RUN}" = true ]; then
      info "Would create user: ${_name} (uid=${_uid}, home=${_home}, shell=${_shell})"
    else
      _err=""
      if _err=$(useradd \
        --uid "${_uid}" \
        --gid "${_name}" \
        --home-dir "${_home}" \
        --shell "${_shell}" \
        --create-home \
        --no-user-group \
        "${_name}" 2>&1); then
        ok "Created user: ${_name} (uid=${_uid})"
      elif _err=$(useradd \
        --gid "${_name}" \
        --home-dir "${_home}" \
        --shell "${_shell}" \
        --create-home \
        --no-user-group \
        "${_name}" 2>&1); then
        ok "Created user: ${_name} (uid auto-assigned)"
      else
        fail "FAILED to create user: ${_name} — ${_err}"
      fi
    fi
  fi

  # Add to supplementary groups
  if [ "${DRY_RUN}" = false ] && [ -n "${_groups}" ]; then
    IFS=',' read -ra grp_list <<< "${_groups}"
    for grp in "${grp_list[@]}"; do
      grp=$(echo "${grp}" | tr -d '[:space:]')
      if getent group "${grp}" &>/dev/null && [ "${grp}" != "${_name}" ]; then
        usermod -aG "${grp}" "${_name}" 2>/dev/null || true
      fi
    done
  fi
done

# Add Primary User to agi_agents if known
if [ -n "${PRIMARY_USER}" ] && id "${PRIMARY_USER}" &>/dev/null; then
  if [ "${DRY_RUN}" = true ]; then
    info "Would add ${PRIMARY_USER} to agi_agents"
  else
    usermod -aG agi_agents "${PRIMARY_USER}" 2>/dev/null || true
    ok "${PRIMARY_USER} added to agi_agents"
  fi
fi

echo ""

# ─── Step 3: Restore Data ─────────────────────────
section "Step 3 — Restore Data"

# Helper: restore a path from the archive staging
restore_path() {
  local rel_path="$1"
  local label="${2:-${rel_path}}"
  local src="${ARCHIVE_DIR}${rel_path}"

  if [ ! -e "${src}" ]; then
    if [ -n "${label}" ]; then info "Skipped (not in archive): ${label}"; fi
    return 0
  fi

  if [ "${DRY_RUN}" = true ]; then
    if [ -d "${src}" ]; then
      local size
      size=$(du -sh "${src}" 2>/dev/null | cut -f1)
      info "Would restore: ${rel_path} (${size})"
    else
      info "Would restore: ${rel_path}"
    fi
    return 0
  fi

  mkdir -p "$(dirname "${rel_path}")"

  if [ -L "${src}" ]; then
    # Restore symlink
    cp -a "${src}" "${rel_path}"
  elif [ -d "${src}" ]; then
    mkdir -p "${rel_path}"
    rsync -a --force "${src}/" "${rel_path}/"
  else
    cp -a "${src}" "${rel_path}"
  fi

  ok "Restored: ${label}"
  return 0
}

# 4a: Repository (~/.versa-agi)
info "4a: Repository..."
if [ -n "${PRIMARY_USER}" ]; then
  PU_HOME=$(eval echo "~${PRIMARY_USER}")
  restore_path "${PU_HOME}/.versa-agi/" "Repository (~/.versa-agi/)"
else
  restore_path "/root/.versa-agi/" "Repository (~/.versa-agi/)"
fi

# 4a.1: Persistent data
info "4a.1: Databases & agent state..."
restore_path "/var/lib/versa-agi" "Persistent data (/var/lib/versa-agi/)"

# 4b: Configuration
info "4b: Configuration..."
restore_path "/etc/versa-agi" "System config (/etc/versa-agi/)"

# 4c: Logs
info "4c: Logs..."
restore_path "/var/log/versa-agi-lifeline.log" "Lifeline log"
restore_path "/var/log/versa-agi-sentinel.log" "Sentinel log"
restore_path "/var/log/versa-agi-archive" "Archived logs"

# 4d: Watchdog home
info "4d: Watchdog home..."
restore_path "/home/${WATCHDOG_USER}" "Watchdog home"

# 4e: COA home
info "4e: COA home..."
restore_path "/home/${COA_USER}" "COA home"

# 4f: Sub-agent homes
info "4f: Sub-agent homes..."
jq -c '.agents[]' "${MANIFEST}" | while read -r agent_json; do
  _name=$(echo "${agent_json}" | jq -r '.name')
  _os_user=$(echo "${agent_json}" | jq -r '.os_user')
  # Skip system agents (restored via watchdog/coa home)
  if [ "${_name}" = "coa" ] || [ "${_name}" = "watchdog" ]; then continue; fi
  SA_HOME="/home/${_os_user}"
  restore_path "${SA_HOME}" "Sub-agent: ${_name} (${SA_HOME}/)"
done

# 4g: System binaries
info "4g: System binaries..."
for bin_path in \
  /usr/local/bin/agictl \
  /usr/local/bin/agitop \
  /usr/local/bin/vcoa \
  /usr/local/bin/versa-agi-uninstall \
  /usr/local/bin/versa-agi-backup \
  /usr/local/bin/versa-agi-update \
  /usr/local/bin/versa-agi-rekey; do
  restore_path "${bin_path}"
done
restore_path "/usr/local/lib/versa-agi" "Persisted lib"
ok "System binaries restored"

# 4h: Systemd units
info "4h: Systemd units..."
restore_path "/etc/systemd/system/versa-agi-sentinel.service" "Sentinel service"
if [ "${DRY_RUN}" = false ]; then
  systemctl daemon-reload
  ok "systemd daemon reloaded"
fi



# ─── Step 4: Rebuild Python Environments ──────────
section "Step 4 — Rebuild Environments"

# agitop venv
if [ "${DRY_RUN}" = true ]; then
  info "Would rebuild agitop venv at /opt/versa-agi/venv/"
else
  info "Rebuilding agitop venv..."
  mkdir -p /opt/versa-agi
  python3 -m venv /opt/versa-agi/venv
  /opt/versa-agi/venv/bin/pip install --quiet click rich textual psutil Pillow 2>/dev/null
  ok "agitop venv rebuilt (/opt/versa-agi/venv/)"
fi

echo ""

# ─── Step 5: Fix Permissions ──────────────────────
section "Step 5 — Permissions"

if [ "${DRY_RUN}" = true ]; then
  info "Would fix file ownership and permissions across all restored paths"
else
  # /etc/versa-agi/ — match manifest §IX.2
  if [ -f "/etc/versa-agi/setup.ini" ]; then
    chown "${WATCHDOG_USER}:${WATCHDOG_USER}" /etc/versa-agi/setup.ini
    chmod 600 /etc/versa-agi/setup.ini
  fi
  if [ -f "/etc/versa-agi/coa_config.json" ]; then
    chown "${WATCHDOG_USER}:${COA_USER}" /etc/versa-agi/coa_config.json
    chmod 640 /etc/versa-agi/coa_config.json
  fi
  if [ -f "/etc/versa-agi/paths.env" ]; then
    chown "${WATCHDOG_USER}:${COA_USER}" /etc/versa-agi/paths.env
    chmod 644 /etc/versa-agi/paths.env
  fi
  if [ -f "/etc/versa-agi/coa.env" ]; then
    chown "${WATCHDOG_USER}:${COA_USER}" /etc/versa-agi/coa.env
    chmod 640 /etc/versa-agi/coa.env
  fi
  for cfg in /etc/versa-agi/*_config.json; do
    if [ -f "${cfg}" ]; then
      _agent_name=$(basename "${cfg}" _config.json)
      # Resolve os_user from manifest (name → os_user)
      _cfg_os_user=$(jq -r --arg name "${_agent_name}" '.agents[] | select(.name == $name) | .os_user // empty' "${MANIFEST}")
      if [ -n "${_cfg_os_user}" ] && id "${_cfg_os_user}" &>/dev/null; then
        chown "${WATCHDOG_USER}:${_cfg_os_user}" "${cfg}"
      else
        chown "${WATCHDOG_USER}:${COA_USER}" "${cfg}"
      fi
      chmod 640 "${cfg}"
    fi
  done
  ok "Config permissions set (/etc/versa-agi/)"

  # Topology state files (server_config.json, client_config.json)
  for _state_file in /etc/versa-agi/server_config.json /etc/versa-agi/client_config.json; do
    if [ -f "${_state_file}" ]; then
      chown root:root "${_state_file}"
      chmod 640 "${_state_file}"
    fi
  done

  # /var/lib/versa-agi/ — databases (per System Design §IX.2)
  if [ -d "/var/lib/versa-agi" ]; then
    # Parent directory and root-level DBs: watchdog:coa
    chown "${WATCHDOG_USER}:${COA_USER}" /var/lib/versa-agi
    chmod 750 /var/lib/versa-agi
    for db in /var/lib/versa-agi/*.db; do
      if [ -f "${db}" ]; then
        chown "${WATCHDOG_USER}:${COA_USER}" "${db}"
        chmod 660 "${db}"
      fi
    done

    # coa/ directory: watchdog:coa 750 (traversable)
    # coa/ DBs: watchdog:coa 660 (consistent with agents.db/messages.db)
    # coa/ non-DB contents: coa:coa (agent-writable cycles dir, status, etc.)
    if [ -d "/var/lib/versa-agi/coa" ]; then
      chown -R "${COA_USER}:${COA_USER}" /var/lib/versa-agi/coa
      chown "${WATCHDOG_USER}:${COA_USER}" /var/lib/versa-agi/coa
      chmod 750 /var/lib/versa-agi/coa
      for db in /var/lib/versa-agi/coa/*.db; do
        if [ -f "${db}" ]; then
          chown "${WATCHDOG_USER}:${COA_USER}" "${db}"
          chmod 660 "${db}"
        fi
      done
    fi

    # Sub-agent data directories: full ownership correction
    # Parent dir: watchdog:{os_user} 750 — agent must traverse to write cycles.
    # cycles/: agent-writable (lifeline spawns harness as agent user).
    # poise + duties: watchdog-readable (lifeline cat's poise for system.md generation).
    # Resolve os_user from manifest for each sub-agent data dir
    for agent_dir in /var/lib/versa-agi/*/; do
      [ -d "${agent_dir}" ] || continue
      _dir_name=$(basename "${agent_dir}")
      # Skip coa/ (handled above) and non-agent dirs
      [ "${_dir_name}" = "coa" ] && continue
      [ "${_dir_name}" = "archive" ] && continue
      [ "${_dir_name}" = "config" ] && continue
      # Resolve os_user from manifest (name → os_user)
      _resolved_os_user=$(jq -r --arg name "${_dir_name}" '.agents[] | select(.name == $name) | .os_user // empty' "${MANIFEST}")
      [ -z "${_resolved_os_user}" ] && continue
      # Parent dir: watchdog:{os_user} 750
      if id "${_resolved_os_user}" &>/dev/null; then
        chown "${WATCHDOG_USER}:${_resolved_os_user}" "${agent_dir}" && chmod 750 "${agent_dir}"
        # cycles/: agent-writable (lifeline spawns gemini as agent user)
        if [ -d "${agent_dir}cycles" ]; then
          chown -R "${_resolved_os_user}:${_resolved_os_user}" "${agent_dir}cycles" && chmod 755 "${agent_dir}cycles"
        fi
        [ -f "${agent_dir}last_prompt.txt" ] && chown "${WATCHDOG_USER}:${_resolved_os_user}" "${agent_dir}last_prompt.txt" && chmod 640 "${agent_dir}last_prompt.txt"
        [ -f "${agent_dir}poise.md" ]  && chown "${WATCHDOG_USER}:${_resolved_os_user}" "${agent_dir}poise.md"  && chmod 640 "${agent_dir}poise.md"
        [ -f "${agent_dir}duties.md" ] && chown "${WATCHDOG_USER}:${_resolved_os_user}" "${agent_dir}duties.md" && chmod 640 "${agent_dir}duties.md"
      fi
    done

    # Fix archive/ dir (may be s7-owned from restore)
    if [ -d "/var/lib/versa-agi/archive" ]; then
      chown -R "${WATCHDOG_USER}:${WATCHDOG_USER}" /var/lib/versa-agi/archive
      chmod 755 /var/lib/versa-agi/archive
      find /var/lib/versa-agi/archive -type f -exec chmod 644 {} + 2>/dev/null || true
    fi
    [ -f "/var/lib/versa-agi/registration-status.json" ] && chown "${WATCHDOG_USER}:${WATCHDOG_USER}" /var/lib/versa-agi/registration-status.json && chmod 640 /var/lib/versa-agi/registration-status.json
    [ -f "/etc/versa-agi/provider_keys.env" ] && chown "${WATCHDOG_USER}:${WATCHDOG_USER}" /etc/versa-agi/provider_keys.env && chmod 600 /etc/versa-agi/provider_keys.env
    [ -f "/etc/versa-agi/install-acceptance.json" ] && chown "${WATCHDOG_USER}:${WATCHDOG_USER}" /etc/versa-agi/install-acceptance.json && chmod 640 /etc/versa-agi/install-acceptance.json
    [ -f "/etc/versa-agi/registration.conf" ] && chown "${WATCHDOG_USER}:${WATCHDOG_USER}" /etc/versa-agi/registration.conf && chmod 640 /etc/versa-agi/registration.conf

    ok "Database permissions set (/var/lib/versa-agi/)"
  fi

  # Watchdog home
  if [ -d "/home/${WATCHDOG_USER}" ]; then
    chown -R "${WATCHDOG_USER}:${WATCHDOG_USER}" "/home/${WATCHDOG_USER}"
    ok "Watchdog home ownership set"
  fi

  # COA home — first reclaim broadly, then apply manifest-specific ownership
  if [ -d "/home/${COA_USER}" ]; then
    # Broad: reclaim home directory and standard dot files for coa
    chown "${COA_USER}:${COA_USER}" "/home/${COA_USER}"
    for dotfile in .bashrc .bash_logout .bash_history .profile .local .npm .cache .nv snap; do
      [ -e "/home/${COA_USER}/${dotfile}" ] && chown -R "${COA_USER}:${COA_USER}" "/home/${COA_USER}/${dotfile}" 2>/dev/null || true
    done
    # SSH directory (coa:coa 700 — private key isolation)
    if [ -d "/home/${COA_USER}/.ssh" ]; then
      chown -R "${COA_USER}:${COA_USER}" "/home/${COA_USER}/.ssh"
      chmod 700 "/home/${COA_USER}/.ssh"
      find "/home/${COA_USER}/.ssh" -type f -name "*versa_agi*" -exec chmod 600 {} + 2>/dev/null || true
      find "/home/${COA_USER}/.ssh" -type f -name "*.pub" -exec chmod 644 {} + 2>/dev/null || true
    fi
    # COA environment — broad reclaim then fine-grained per manifest §IX.4
    if [ -d "/home/${COA_USER}/coa-env" ]; then
      # Broad: reclaim coa-env root and general files (coa:coa)
      chown "${COA_USER}:${COA_USER}" "/home/${COA_USER}/coa-env"
      # Fix standard files at coa-env root
      for f in .gitignore .git raw_msgs.txt; do
        [ -e "/home/${COA_USER}/coa-env/${f}" ] && chown -R "${COA_USER}:${COA_USER}" "/home/${COA_USER}/coa-env/${f}" 2>/dev/null || true
      done
      # Fine-grained per manifest §IX.4
      # workspace: coa:agi_agents 2770 (setgid for shared projects)
      [ -d "/home/${COA_USER}/coa-env/workspace" ] && chown "${COA_USER}:agi_agents" "/home/${COA_USER}/coa-env/workspace" && chmod 2770 "/home/${COA_USER}/coa-env/workspace" 2>/dev/null || true
      # External data dirs: watchdog:coa 2750 (setgid for sub-dirs)
      for _dir in attachments archive; do
        if [ -d "/home/${COA_USER}/coa-env/${_dir}" ]; then
          chown -R "${WATCHDOG_USER}:${COA_USER}" "/home/${COA_USER}/coa-env/${_dir}"
          chmod 2750 "/home/${COA_USER}/coa-env/${_dir}"
        fi
      done
    fi
    ok "COA home ownership set"
  fi

  # Sub-agent homes
  jq -c '.agents[]' "${MANIFEST}" | while read -r agent_json; do
    _name=$(echo "${agent_json}" | jq -r '.name')
    _os_user=$(echo "${agent_json}" | jq -r '.os_user')
    if [ "${_name}" = "coa" ] || [ "${_name}" = "watchdog" ]; then continue; fi
    SA_HOME="/home/${_os_user}"
    if [ -d "${SA_HOME}" ]; then
      chown -R "${_os_user}:agi_agents" "${SA_HOME}"
      chmod 770 "${SA_HOME}"
      # .agent/ + skills/ + workspace/
      [ -d "${SA_HOME}/.agent" ]        && chown "${_os_user}:agi_agents" "${SA_HOME}/.agent"        && chmod 770 "${SA_HOME}/.agent"
      [ -d "${SA_HOME}/.agent/skills" ] && chown "${_os_user}:agi_agents" "${SA_HOME}/.agent/skills" && chmod 775 "${SA_HOME}/.agent/skills"
      [ -d "${SA_HOME}/workspace" ]     && chown "${_os_user}:agi_agents" "${SA_HOME}/workspace"     && chmod 770 "${SA_HOME}/workspace"
      # §IX.4 git identity, SSH keypair, credentials
      [ -f "${SA_HOME}/README.md" ]       && chown "${_os_user}:agi_agents" "${SA_HOME}/README.md"       && chmod 664 "${SA_HOME}/README.md"
      [ -f "${SA_HOME}/.gitconfig" ]      && chown "${_os_user}:agi_agents" "${SA_HOME}/.gitconfig"      && chmod 644 "${SA_HOME}/.gitconfig"
      [ -f "${SA_HOME}/.git-credentials" ] && chown "${_os_user}:agi_agents" "${SA_HOME}/.git-credentials" && chmod 600 "${SA_HOME}/.git-credentials"
      if [ -d "${SA_HOME}/.ssh" ]; then
        chown -R "${_os_user}:agi_agents" "${SA_HOME}/.ssh"
        chmod 700 "${SA_HOME}/.ssh"
        # Private keys: 600, public keys: 644, config: 644
        find "${SA_HOME}/.ssh" -maxdepth 1 -type f -name '*.pub' -exec chmod 644 {} + 2>/dev/null || true
        find "${SA_HOME}/.ssh" -maxdepth 1 -type f -name 'config' -exec chmod 644 {} + 2>/dev/null || true
        find "${SA_HOME}/.ssh" -maxdepth 1 -type f ! -name '*.pub' ! -name 'config' ! -name 'known_hosts*' -exec chmod 600 {} + 2>/dev/null || true
        find "${SA_HOME}/.ssh" -maxdepth 1 -type f -name 'known_hosts*' -exec chmod 644 {} + 2>/dev/null || true
      fi
      ok "Sub-agent ${_name} ownership set"
    fi
  done

  # System binaries
  for bin_path in /usr/local/bin/agictl /usr/local/bin/agitop /usr/local/bin/vcoa; do
    if [ -f "${bin_path}" ]; then
      chown root:root "${bin_path}"
      chmod 755 "${bin_path}"
    fi
  done
  if [ -d "/usr/local/lib/versa-agi" ]; then
    chown -R root:root /usr/local/lib/versa-agi
    chmod -R 755 /usr/local/lib/versa-agi
  fi
  ok "System binary permissions set"

  # Core-infra executables
  if [ -d "/home/${WATCHDOG_USER}/core-infra" ]; then
    chmod +x "/home/${WATCHDOG_USER}/core-infra/lifeline.sh" 2>/dev/null || true
    chmod +x "/home/${WATCHDOG_USER}/core-infra/sentinel.sh" 2>/dev/null || true
    chmod +x "/home/${WATCHDOG_USER}/core-infra/watchdog.sh" 2>/dev/null || true
    chmod +x "/home/${WATCHDOG_USER}/core-infra/scripts/"*.sh 2>/dev/null || true
    chmod +x "/home/${WATCHDOG_USER}/core-infra/bin/"* 2>/dev/null || true
    ok "Core-infra executables set"
  fi
fi

echo ""

# ─── Step 7: Primary User Symlinks ────────────────
section "Step 7 — Primary User Setup"

if [ -n "${PRIMARY_USER}" ] && [ -n "${PRIMARY_USER_HOME}" ]; then
  # Restore PU symlinks from manifest
  jq -c '.primary_user_symlinks[]' "${MANIFEST}" 2>/dev/null | while read -r link_json; do
    _path=$(echo "${link_json}" | jq -r '.path')
    _target=$(echo "${link_json}" | jq -r '.target')
    if [ "${DRY_RUN}" = true ]; then
      info "Would create symlink: ${_path} → ${_target}"
    else
      ln -sfn "${_target}" "${_path}"
      chown -h "${PRIMARY_USER}:${PRIMARY_USER}" "${_path}"
      ok "Symlink: ${_path} → ${_target}"
    fi
  done

  # Ensure ~/.versa-agi/ exists with setup.ini symlink
  PU_VERSA_DIR="${PRIMARY_USER_HOME}/.versa-agi"
  if [ "${DRY_RUN}" = true ]; then
    info "Would ensure ${PU_VERSA_DIR}/ with setup.ini symlink"
  else
    mkdir -p "${PU_VERSA_DIR}"
    chown "${PRIMARY_USER}:${PRIMARY_USER}" "${PU_VERSA_DIR}"
    ln -sf /etc/versa-agi/setup.ini "${PU_VERSA_DIR}/setup.ini"
    chown -h "${PRIMARY_USER}:${PRIMARY_USER}" "${PU_VERSA_DIR}/setup.ini"
    ok "~/.versa-agi/ ensured with setup.ini symlink"
  fi
else
  warn "No Primary User in manifest — skipping symlink setup"
fi

echo ""

# ─── Step 8: Health Check ─────────────────────────
section "Step 8 — Health Check"

HEALTH_LIB="/home/${WATCHDOG_USER}/core-infra/scripts/health_checks.sh"
if [ -f "${HEALTH_LIB}" ] && [ "${DRY_RUN}" = false ]; then
  info "Running health checks..."
  DEPLOYED_CORE_INFRA="/home/${WATCHDOG_USER}/core-infra"
  DEPLOYED_COA_ENV="/home/${COA_USER}/coa-env"
  source "${HEALTH_LIB}"
  run_health_checks "${WATCHDOG_USER}" "${COA_USER}" "/home/${WATCHDOG_USER}/core-infra" "/home/${COA_USER}/coa-env" || true
else
  if [ "${DRY_RUN}" = true ]; then
    info "Would run health checks"
  else
    warn "Health check library not found — skipping"
  fi
fi

echo ""

# ─── Summary ──────────────────────────────────────
echo ""
echo -e "  ${BCYAN}${BOLD}╭────────────────────────────────────────╮${RESET}"
echo -e "  ${BCYAN}${BOLD}│  Restore Complete                      │${RESET}"
echo -e "  ${BCYAN}${BOLD}├────────────────────────────────────────┤${RESET}"
echo -e "  ${BCYAN}${BOLD}│${RESET}  Source           ${SOURCE_HOSTNAME}"
echo -e "  ${BCYAN}${BOLD}│${RESET}  Backup Date      ${BACKUP_TIMESTAMP}"
echo -e "  ${BCYAN}${BOLD}│${RESET}  Agents           ${AGENT_COUNT}"
echo -e "  ${BCYAN}${BOLD}│${RESET}  OS Users         ${USER_COUNT}"
echo -e "  ${BCYAN}${BOLD}╰────────────────────────────────────────╯${RESET}"
echo ""
if [ "${DRY_RUN}" = false ]; then
  SETUP_SCRIPT=""
  if [ -n "${PRIMARY_USER}" ] && [ -f "$(eval echo "~${PRIMARY_USER}")/.versa-agi/repo/src/setup.sh" ]; then
    SETUP_SCRIPT="$(eval echo "~${PRIMARY_USER}")/.versa-agi/repo/src/setup.sh"
  elif [ -f "/root/.versa-agi/repo/src/setup.sh" ]; then
    SETUP_SCRIPT="/root/.versa-agi/repo/src/setup.sh"
  fi

  if [ -n "${SETUP_SCRIPT}" ]; then
    # Place setup.ini in src/ as requested before setup.sh is called
    if [ -f "/etc/versa-agi/setup.ini" ]; then
      cp "/etc/versa-agi/setup.ini" "$(dirname "${SETUP_SCRIPT}")/setup.ini"
      if [ -n "${PRIMARY_USER}" ]; then
        chown "${PRIMARY_USER}:${PRIMARY_USER}" "$(dirname "${SETUP_SCRIPT}")/setup.ini" 2>/dev/null || true
      fi
    fi

    if [ "${AUTO_YES}" = true ]; then
      echo -e "  ${BCYAN}${BOLD}▶ Automatically invoking setup.sh...${RESET}"
      echo ""
      bash "${SETUP_SCRIPT}" --yes
    else
      echo -e "  ${YELLOW}${BOLD}⚠  RESTORE INCOMPLETE.${RESET}"
      echo -e "  ${DIM}Data files have been extracted successfully, but the environment requires finalizing.${RESET}"
      echo -e "  ${DIM}You MUST run setup to restore system binaries, dependencies, and CRON:${RESET}"
      echo ""
      echo -e "  ${BOLD}  sudo ${SETUP_SCRIPT}${RESET}"
      echo ""
    fi
  else
    echo -e "  ${YELLOW}${BOLD}⚠  RESTORE INCOMPLETE.${RESET}"
    echo -e "  ${DIM}Data files extracted, but setup.sh could not be found.${RESET}"
    echo -e "  ${DIM}Please locate and run setup.sh manually to finalize your environment.${RESET}"
    echo ""
  fi
fi
