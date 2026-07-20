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

# ─── Docker Engine (shared with setup_local Intel SYCL path) ──
# Ensures docker CLI + running daemon. Installs via Docker's official APT
# repo on Ubuntu/Debian when missing. Idempotent.
provider_ensure_docker() {
  if command -v docker &>/dev/null && systemctl is-active --quiet docker 2>/dev/null; then
    ok "Docker already installed: $(docker --version 2>&1 | head -1)"
    return 0
  fi

  if command -v docker &>/dev/null; then
    info "Docker installed but not running — starting daemon..."
    systemctl enable docker --quiet 2>/dev/null || true
    systemctl start docker 2>/dev/null || true
    sleep 2
    if systemctl is-active --quiet docker 2>/dev/null || docker info &>/dev/null; then
      ok "Docker daemon started"
      return 0
    fi
    error "Docker daemon failed to start. Check: systemctl status docker"
  fi

  info "Installing Docker Engine (official APT repository)..."
  if ! command -v curl &>/dev/null; then
    apt-get update -qq
    apt-get install -y -qq curl ca-certificates 2>/dev/null || true
  fi

  local arch codename distro_id
  arch="$(dpkg --print-architecture 2>/dev/null || echo amd64)"
  # shellcheck disable=SC1091
  . /etc/os-release 2>/dev/null || true
  distro_id="${ID:-ubuntu}"
  codename="${VERSION_CODENAME:-}"
  case "${distro_id}" in
    ubuntu|debian) ;;
    *)
      error "Automatic Docker install supports Ubuntu/Debian only (got: ${distro_id}). Install Docker manually, then re-run."
      ;;
  esac
  if [ -z "${codename}" ]; then
    error "Could not detect OS codename for Docker APT repo. Install Docker manually, then re-run."
  fi

  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL "https://download.docker.com/linux/${distro_id}/gpg" -o /etc/apt/keyrings/docker.asc
  chmod a+r /etc/apt/keyrings/docker.asc
  echo "deb [arch=${arch} signed-by=/etc/apt/keyrings/docker.asc] \
https://download.docker.com/linux/${distro_id} ${codename} stable" | \
    tee /etc/apt/sources.list.d/docker.list > /dev/null
  apt-get update -qq
  apt-get install -y -qq docker-ce docker-ce-cli containerd.io docker-buildx-plugin \
    || error "Docker APT install failed"
  systemctl enable docker --quiet 2>/dev/null || true
  systemctl start docker 2>/dev/null || true
  sleep 2

  if command -v docker &>/dev/null && { systemctl is-active --quiet docker 2>/dev/null || docker info &>/dev/null; }; then
    ok "Docker installed: $(docker --version 2>&1 | head -1)"
    return 0
  fi
  error "Docker installation failed. Check: systemctl status docker"
}
