#!/bin/bash
# ─────────────────────────────────────────────────
# Versa AGi — OpenAI (GPT) Provider
#
# Configures OpenAI API key and model registry in setup.ini.
# No system services required — purely key + config management.
#
# Usage:
#   sudo ./openai.sh              # Install/configure
#   sudo ./openai.sh --uninstall  # Remove config
# ─────────────────────────────────────────────────

set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/_common.sh"
provider_require_root

# ─── Uninstall ──────────────────────────────────────
if [ "${1:-}" = "--uninstall" ]; then
  echo ""
  echo "  ─── OpenAI Provider — Uninstall ────────────"
  echo ""

  # Clear API key and disable
  provider_ini_set third_party openai_enabled false
  provider_ini_set third_party openai_api_key ""
  ok "OpenAI disabled and API key cleared from setup.ini"

  # Update paths.env — re-aggregate remaining third-party models
  if [ -f "${PATHS_ENV}" ]; then
    # Read provider list from setup.ini and aggregate enabled models
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

  # Mark agents using OpenAI models as invalid_config
  AGENTS_DB="/var/lib/versa-agi/agents.db"
  if [ -f "${AGENTS_DB}" ]; then
    sqlite3 "${AGENTS_DB}" "
      UPDATE agents
      SET status='invalid_config',
          status_message='OpenAI provider was removed. Reassign model via agitop.',
          updated_at=datetime('now')
      WHERE model LIKE 'gpt-%'
        AND status != 'invalid_config';
    " 2>/dev/null || true
    ok "Agents using OpenAI models marked as invalid_config"
  fi

  echo ""
  echo "  ✅ OpenAI provider removed."
  echo "  Reassign affected agents in the Dashboard (agitop → Edit Agent)."
  echo ""
  exit 0
fi

# ─── Install / Configure ───────────────────────────
echo ""
echo "  ─── OpenAI Provider — Configure ──────────────"
echo ""
echo "  Provider: OpenAI (GPT)"
echo "  Models:   gpt-5.5-2026-04-23, gpt-5.4-2026-03-05, gpt-5.4-mini-2026-03-17"
echo ""

if [ -z "${VERSA_SETUP_PARENT:-}" ]; then
  read -p "  Proceed with OpenAI configuration? [Y/n]: " -n 1 -r
  echo ""
  if [[ $REPLY =~ ^[Nn]$ ]]; then echo "  OpenAI setup cancelled."; exit 0; fi
  echo ""
fi

# Read or prompt for API key
api_key="$(provider_ini_get third_party openai_api_key '')"
if [ -z "${api_key}" ]; then
  echo "  An OpenAI API key is required. Get one at: https://platform.openai.com/api-keys"
  echo ""
  read -p "  Enter your OpenAI API Key: " api_key
  while [ -z "${api_key}" ]; do read -p "  Enter your OpenAI API Key: " api_key; done
  echo ""
else
  ok "OpenAI API key loaded from setup.ini"
fi

# Write config
provider_ini_set third_party openai_enabled true
provider_ini_set third_party openai_api_key "${api_key}"
ok "setup.ini updated (openai_enabled=true)"
echo "  Models: gpt-5.5-2026-04-23, gpt-5.4-2026-03-05, gpt-5.4-mini-2026-03-17"

# Write API key to inference_endpoint.env for harness injection
INFERENCE_ENV="/etc/versa-agi/inference_endpoint.env"
if [ -f "${INFERENCE_ENV}" ]; then
  if grep -q "^OPENAI_API_KEY=" "${INFERENCE_ENV}"; then
    sed -i "s|^OPENAI_API_KEY=.*|OPENAI_API_KEY=${api_key}|" "${INFERENCE_ENV}"
  else
    echo "OPENAI_API_KEY=${api_key}" >> "${INFERENCE_ENV}"
  fi
else
  echo "OPENAI_API_KEY=${api_key}" > "${INFERENCE_ENV}"
  chmod 600 "${INFERENCE_ENV}"
  chown root:root "${INFERENCE_ENV}"
fi
ok "inference_endpoint.env updated (OPENAI_API_KEY)"

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
echo "  ✅ OpenAI provider ready!"
echo "  Assign agents to OpenAI models via Dashboard (agitop) → Edit Agent"
echo ""
