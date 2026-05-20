#!/bin/bash
# ─────────────────────────────────────────────────
# Versa AGi — Provider Common Helpers
#
# Shared by all provider scripts. Source this first.
# Usage: source "$(dirname "${BASH_SOURCE[0]}")/_common.sh"
# ─────────────────────────────────────────────────

# Resolve paths
PROVIDERS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRIPT_DIR="$(dirname "${PROVIDERS_DIR}")"

# ─── UI Library ──────────────────────────────────────
UI_LIB="${SCRIPT_DIR}/core-infra/ui_lib.sh"
if [ -f "${UI_LIB}" ]; then
  source "${UI_LIB}"
else
  info()  { echo -e "\033[38;2;0;255;204m[INFO]\033[0m $*"; }
  ok()    { echo -e "\033[0;32m[OK]\033[0m $*"; }
  warn()  { echo -e "\033[1;33m[WARN]\033[0m $*"; }
  error() { echo -e "\033[0;31m[ERROR]\033[0m $*"; exit 1; }
fi

# ─── Root Check ─────────────────────────────────────
provider_require_root() {
  if [ "$(id -u)" -ne 0 ]; then
    error "This script must be run as root (sudo)"
  fi
}

# ─── INI Reader ─────────────────────────────────────
# Usage: ini_get <section> <key> [default]
provider_ini_get() {
  local section="$1" key="$2" default="${3:-}"
  local ini_file=""
  if [ -f "/etc/versa-agi/setup.ini" ]; then
    ini_file="/etc/versa-agi/setup.ini"
  elif [ -f "${SCRIPT_DIR}/setup.ini" ]; then
    ini_file="${SCRIPT_DIR}/setup.ini"
  fi
  if [ -z "${ini_file}" ]; then echo "${default}"; return; fi
  local value
  value=$(awk -F= -v sec="${section}" -v k="${key}" \
    '/^\[/{s=($0 == "["sec"]")} s && $1 ~ "^"k"$" {gsub(/^[[:space:]]+|[[:space:]]+$/,"",$2); print $2; exit}' \
    "${ini_file}" 2>/dev/null)
  echo "${value:-${default}}"
}

# ─── INI Writer ─────────────────────────────────────
# Usage: ini_set <section> <key> <value>
# Writes to BOTH deployed and source INI files.
provider_ini_set() {
  local section="$1" key="$2" value="$3"
  local _files=()
  [ -f "/etc/versa-agi/setup.ini" ] && _files+=("/etc/versa-agi/setup.ini")
  [ -f "${SCRIPT_DIR}/setup.ini" ] && [ "${SCRIPT_DIR}/setup.ini" != "/etc/versa-agi/setup.ini" ] && _files+=("${SCRIPT_DIR}/setup.ini")
  for ini_file in "${_files[@]}"; do
    if grep -A 100 "^\[${section}\]" "${ini_file}" 2>/dev/null | grep -q "^${key}="; then
      sed -i "/^\[${section}\]/,/^\[/{s/^${key}=.*/${key}=${value}/}" "${ini_file}" 2>/dev/null || true
    fi
  done
}

# ─── Paths ──────────────────────────────────────────
PATHS_ENV="${PATHS_ENV:-/etc/versa-agi/paths.env}"
