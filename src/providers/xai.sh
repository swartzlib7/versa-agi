#!/bin/bash
# ─────────────────────────────────────────────────
# Versa AGi — xAI (Grok) Provider
#
# Configures xAI API key and model registry in setup.ini.
# No system services required — purely key + config management.
#
# Usage:
#   sudo ./xai.sh              # Install/configure
#   sudo ./xai.sh --uninstall  # Remove config
# ─────────────────────────────────────────────────

set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/_common.sh"
provider_require_root

# ─── Uninstall ──────────────────────────────────────
if [ "${1:-}" = "--uninstall" ]; then
  echo ""
  echo "  ─── xAI Provider — Uninstall ──────────────"
  echo ""

  # Clear API key and disable
  provider_ini_set third_party xai_enabled false
  provider_ini_set third_party xai_api_key ""
  ok "xAI disabled and API key cleared from setup.ini"

  # Update paths.env — remove third-party models
  if [ -f "${PATHS_ENV}" ]; then
    sed -i 's/^VERSA_THIRD_PARTY_ENABLED=.*/VERSA_THIRD_PARTY_ENABLED="false"/' "${PATHS_ENV}"
    sed -i 's/^VERSA_THIRD_PARTY_MODELS=.*/VERSA_THIRD_PARTY_MODELS=""/' "${PATHS_ENV}"
    ok "paths.env updated (THIRD_PARTY_ENABLED=false)"
  fi

  # Mark agents using xAI models as invalid_config
  AGENTS_DB="/var/lib/versa-agi/agents.db"
  if [ -f "${AGENTS_DB}" ]; then
    sqlite3 "${AGENTS_DB}" "
      UPDATE agents
      SET status='invalid_config',
          status_message='xAI provider was removed. Reassign model via agitop.',
          updated_at=datetime('now')
      WHERE model IN (
        'grok-4.5',
        'grok-4.3',
        'grok-4.20-reasoning',
        'grok-4-1-fast-reasoning'
      )
        AND status != 'invalid_config';
    " 2>/dev/null || true
    ok "Agents using xAI models marked as invalid_config"
  fi

  echo ""
  echo "  ✅ xAI provider removed."
  echo "  Reassign affected agents in the Dashboard (agitop → Edit Agent)."
  echo ""
  exit 0
fi

# ─── Install / Configure ───────────────────────────
echo ""
echo "  ─── xAI Provider — Configure ───────────────"
echo ""
echo "  Provider: xAI (Grok)"
echo "  Models:   grok-4.5"
echo ""

if [ -z "${VERSA_SETUP_PARENT:-}" ]; then
  read -p "  Proceed with xAI configuration? [Y/n]: " -n 1 -r
  echo ""
  if [[ $REPLY =~ ^[Nn]$ ]]; then echo "  xAI setup cancelled."; exit 0; fi
  echo ""
fi

# Read or prompt for API key
api_key="$(provider_ini_get third_party xai_api_key '')"
if [ -z "${api_key}" ]; then
  echo "  An xAI API key is required. Get one at: https://console.x.ai"
  echo ""
  read -p "  Enter your xAI API Key: " api_key
  while [ -z "${api_key}" ]; do read -p "  Enter your xAI API Key: " api_key; done
  echo ""
else
  ok "xAI API key loaded from setup.ini"
fi

# Write config
provider_ini_set third_party xai_enabled true
provider_ini_set third_party xai_api_key "${api_key}"
ok "setup.ini updated (xai_enabled=true)"
echo "  Models: grok-4.5"

# Update paths.env
if [ -f "${PATHS_ENV}" ]; then
  # Aggregate all enabled third-party models
  PROVIDERS="$(provider_ini_get third_party providers 'xai,openai,anthropic')"
  AGGREGATED=""
  IFS=',' read -ra PROVIDER_LIST <<< "${PROVIDERS}"
  for provider_slug in "${PROVIDER_LIST[@]}"; do
    p_enabled="$(provider_ini_get third_party "${provider_slug}_enabled" false)"
    p_models="$(provider_ini_get third_party "${provider_slug}_models" '')"
    if [ "${p_enabled}" = "true" ] && [ -n "${p_models}" ]; then
      [ -n "${AGGREGATED}" ] && AGGREGATED="${AGGREGATED},"
      AGGREGATED="${AGGREGATED}${p_models}"
    fi
  done

  for kv in "VERSA_THIRD_PARTY_ENABLED=\"true\"" "VERSA_THIRD_PARTY_MODELS=\"${AGGREGATED}\""; do
    KEY="${kv%%=*}"
    if grep -q "^${KEY}=" "${PATHS_ENV}"; then
      sed -i "s|^${KEY}=.*|${kv}|" "${PATHS_ENV}"
    else
      echo "${kv}" >> "${PATHS_ENV}"
    fi
  done
  ok "paths.env updated (THIRD_PARTY_ENABLED=true)"
fi

provider_ini_set third_party enabled true
ok "setup.ini updated (third_party enabled)"

echo ""
ok "xAI provider ready"
echo ""
