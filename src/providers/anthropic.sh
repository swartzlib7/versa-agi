#!/bin/bash
# ─────────────────────────────────────────────────
# Versa AGi — Anthropic (Claude) Provider
#
# Configures Anthropic API key and model registry in setup.ini.
# No system services required — purely key + config management.
#
# Usage:
#   sudo ./anthropic.sh              # Install/configure
#   sudo ./anthropic.sh --uninstall  # Remove config
# ─────────────────────────────────────────────────

set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/_common.sh"
provider_require_root

# ─── Uninstall ──────────────────────────────────────
if [ "${1:-}" = "--uninstall" ]; then
  echo ""
  echo "  ─── Anthropic Provider — Uninstall ─────────"
  echo ""

  # Clear API key and disable
  provider_ini_set third_party anthropic_enabled false
  provider_ini_set third_party anthropic_api_key ""
  ok "Anthropic disabled and API key cleared from setup.ini"

  # Update paths.env — re-aggregate remaining third-party models
  if [ -f "${PATHS_ENV}" ]; then
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

    if [ -z "${AGGREGATED}" ]; then
      sed -i 's/^VERSA_THIRD_PARTY_ENABLED=.*/VERSA_THIRD_PARTY_ENABLED="false"/' "${PATHS_ENV}"
      sed -i 's/^VERSA_THIRD_PARTY_MODELS=.*/VERSA_THIRD_PARTY_MODELS=""/' "${PATHS_ENV}"
      ok "paths.env updated (THIRD_PARTY_ENABLED=false — no providers remain)"
    else
      for kv in "VERSA_THIRD_PARTY_ENABLED=\"true\"" "VERSA_THIRD_PARTY_MODELS=\"${AGGREGATED}\""; do
        KEY="${kv%%=*}"
        if grep -q "^${KEY}=" "${PATHS_ENV}"; then
          sed -i "s|^${KEY}=.*|${kv}|" "${PATHS_ENV}"
        else
          echo "${kv}" >> "${PATHS_ENV}"
        fi
      done
      ok "paths.env updated (remaining models: ${AGGREGATED})"
    fi
  fi

  # Mark agents using Anthropic models as invalid_config
  AGENTS_DB="/var/lib/versa-agi/agents.db"
  if [ -f "${AGENTS_DB}" ]; then
    sqlite3 "${AGENTS_DB}" "
      UPDATE agents
      SET status='invalid_config',
          status_message='Anthropic provider was removed. Reassign model via agitop.',
          updated_at=datetime('now')
      WHERE model LIKE 'claude-%'
        AND status != 'invalid_config';
    " 2>/dev/null || true
    ok "Agents using Anthropic models marked as invalid_config"
  fi

  echo ""
  echo "  ✅ Anthropic provider removed."
  echo "  Reassign affected agents in the Dashboard (agitop → Edit Agent)."
  echo ""
  exit 0
fi

# ─── Install / Configure ───────────────────────────
echo ""
echo "  ─── Anthropic Provider — Configure ───────────"
echo ""
echo "  Provider: Anthropic (Claude)"
echo "  Models:   claude-opus-4-8, claude-sonnet-4-6"
echo ""

if [ -z "${VERSA_SETUP_PARENT:-}" ]; then
  read -p "  Proceed with Anthropic configuration? [Y/n]: " -n 1 -r
  echo ""
  if [[ $REPLY =~ ^[Nn]$ ]]; then echo "  Anthropic setup cancelled."; exit 0; fi
  echo ""
fi

# Read or prompt for API key
api_key="$(provider_ini_get third_party anthropic_api_key '')"
if [ -z "${api_key}" ]; then
  echo "  An Anthropic API key is required. Get one at: https://console.anthropic.com/settings/keys"
  echo ""
  read -p "  Enter your Anthropic API Key: " api_key
  while [ -z "${api_key}" ]; do read -p "  Enter your Anthropic API Key: " api_key; done
  echo ""
else
  ok "Anthropic API key loaded from setup.ini"
fi

# Write config
provider_ini_set third_party anthropic_enabled true
provider_ini_set third_party anthropic_api_key "${api_key}"
ok "setup.ini updated (anthropic_enabled=true)"
echo "  Models: claude-opus-4-8, claude-sonnet-4-6"

# Write API key to provider_keys.env for harness injection
INFERENCE_ENV="/etc/versa-agi/provider_keys.env"
if [ -f "${INFERENCE_ENV}" ]; then
  if grep -q "^ANTHROPIC_API_KEY=" "${INFERENCE_ENV}"; then
    sed -i "s|^ANTHROPIC_API_KEY=.*|ANTHROPIC_API_KEY=${api_key}|" "${INFERENCE_ENV}"
  else
    echo "ANTHROPIC_API_KEY=${api_key}" >> "${INFERENCE_ENV}"
  fi
else
  echo "ANTHROPIC_API_KEY=${api_key}" > "${INFERENCE_ENV}"
  chmod 600 "${INFERENCE_ENV}"
  chown root:root "${INFERENCE_ENV}"
fi
ok "provider_keys.env updated (ANTHROPIC_API_KEY)"

# Update paths.env — aggregate all enabled third-party models
if [ -f "${PATHS_ENV}" ]; then
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
echo "  ✅ Anthropic provider ready!"
echo "  Assign agents to Anthropic models via Dashboard (agitop) → Edit Agent"
echo ""
