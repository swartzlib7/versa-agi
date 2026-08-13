#!/bin/bash
# ─────────────────────────────────────────────────
# Versa AGi — Third-Party Cloud Providers
#
# Configures external LLM providers (xAI, etc) by securely
# storing their API keys in setup.ini for the Python Harness.
#
# Usage:  sudo ./setup_proxy.sh
# ─────────────────────────────────────────────────

set -euo pipefail

# ─── UI Library ──────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
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
if [ "$(id -u)" -ne 0 ]; then
  error "This script must be run as root (sudo ./setup_proxy.sh)"
fi

# ─── INI Reader/Writer ──────────────────────────────
ini_get() {
  local section="$1" key="$2" default="${3:-}"
  local ini_file=""
  if [ -f "${SCRIPT_DIR}/setup.ini" ]; then ini_file="${SCRIPT_DIR}/setup.ini"; elif [ -f "/etc/versa-agi/setup.ini" ]; then ini_file="/etc/versa-agi/setup.ini"; fi
  if [ -z "${ini_file}" ]; then echo "${default}"; return; fi
  local value=$(awk -F= -v sec="${section}" -v k="${key}" '/^\[/{s=($0 == "["sec"]")} s && $1 ~ "^"k"$" {gsub(/^[[:space:]]+|[[:space:]]+$/,"",$2); print $2; exit}' "${ini_file}" 2>/dev/null)
  echo "${value:-${default}}"
}

ini_set() {
  local section="$1" key="$2" value="$3"
  local _files=()
  [ -f "${SCRIPT_DIR}/setup.ini" ] && _files+=("${SCRIPT_DIR}/setup.ini")
  [ -f "/etc/versa-agi/setup.ini" ] && [ "/etc/versa-agi/setup.ini" != "${SCRIPT_DIR}/setup.ini" ] && _files+=("/etc/versa-agi/setup.ini")
  for ini_file in "${_files[@]}"; do
    if grep -A 100 "^\[${section}\]" "${ini_file}" | grep -q "^${key}="; then
      sed -i "/^\[${section}\]/,/^\[/{s/^${key}=.*/${key}=${value}/}" "${ini_file}" 2>/dev/null || true
    fi
  done
}

PATHS_ENV="${PATHS_ENV:-/etc/versa-agi/paths.env}"

# ─── Banner ─────────────────────────────────────────
echo ""
echo "═══════════════════════════════════════════════"
echo "  Versa AGi — Third-Party Cloud Providers"
echo "═══════════════════════════════════════════════"
echo ""
echo "  This configures external LLM providers."
echo ""
echo "  ┌─────────────────────────────────────────┐"
echo "  │  AVAILABLE PROVIDERS                    │"
echo "  │                                         │"
echo "  │  1) xAI (Grok)                          │"
echo "  │     • grok-4.5                          │"
echo "  └─────────────────────────────────────────┘"
echo ""

if [ -z "${VERSA_SETUP_PARENT:-}" ]; then
  read -p "  Proceed with Third-Party Cloud Provider setup? [y/N]: " -n 1 -r
  echo ""
  if [[ ! $REPLY =~ ^[Yy]$ ]]; then echo "  Third-Party Cloud Provider setup cancelled."; exit 0; fi
fi
echo ""

configure_xai() {
  info "Configuring xAI (Grok)..."
  local api_key="$(ini_get third_party xai_api_key '')"
  if [ -z "${api_key}" ]; then
    echo ""
    echo "  An xAI API key is required. Get one at: https://console.x.ai"
    echo ""
    read -p "  Enter your xAI API Key: " api_key
    while [ -z "${api_key}" ]; do read -p "  Enter your xAI API Key: " api_key; done
    echo ""
  else
    ok "xAI API key loaded from setup.ini"
  fi

  ini_set third_party xai_enabled true
  ini_set third_party xai_api_key "${api_key}"
  ok "setup.ini updated (xai_enabled=true)"
  echo "  Models: grok-4.5"
}

PROVIDERS="$(ini_get third_party providers '')"
if [ -z "${VERSA_SETUP_PARENT:-}" ] && [ -z "${PROVIDERS}" ]; then
  echo "  Select provider(s) to configure:"
  echo "    1) xAI (Grok)"
  echo ""
  read -p "  Selection [1]: " PROVIDER_CHOICE
  PROVIDER_CHOICE="${PROVIDER_CHOICE:-1}"
  case "${PROVIDER_CHOICE}" in 1) PROVIDERS="xai" ;; *) PROVIDERS="xai" ;; esac
  echo ""
elif [ -z "${PROVIDERS}" ]; then
  PROVIDERS="xai"
fi

IFS=',' read -ra SELECTED <<< "${PROVIDERS}"
for provider in "${SELECTED[@]}"; do
  provider=$(echo "${provider}" | xargs)
  case "${provider}" in xai) configure_xai ;; *) warn "Unknown provider: ${provider} — skipping" ;; esac
done

# ─── Update paths.env ──────────────────────
if [ -f "${PATHS_ENV}" ]; then
  AGGREGATED=""
  for provider in "${SELECTED[@]}"; do
    provider=$(echo "${provider}" | xargs)
    p_enabled="$(ini_get third_party "${provider}_enabled" false)"
    p_models="$(ini_get third_party "${provider}_models" '')"
    if [ "${p_enabled}" = "true" ] && [ -n "${p_models}" ]; then
      [ -n "${AGGREGATED}" ] && AGGREGATED="${AGGREGATED},"
      AGGREGATED="${AGGREGATED}${p_models}"
    fi
  done

  for kv in "VERSA_THIRD_PARTY_ENABLED=\"true\"" "VERSA_THIRD_PARTY_MODELS=\"${AGGREGATED}\""; do
    KEY="${kv%%=*}"
    if grep -q "^${KEY}=" "${PATHS_ENV}"; then sed -i "s|^${KEY}=.*|${kv}|" "${PATHS_ENV}"; else echo "${kv}" >> "${PATHS_ENV}"; fi
  done
  ok "paths.env updated (THIRD_PARTY_ENABLED=true)"
fi

ini_set third_party enabled true
ini_set third_party providers "${PROVIDERS}"
ok "setup.ini updated (third_party enabled)"

echo ""
echo "  ✅ Third-Party Providers ready!"
echo "  Assign agents to third-party models via Dashboard (agitop) → Edit Agent"
echo ""
