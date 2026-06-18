#!/bin/bash
# ─────────────────────────────────────────────────
# Versa AGi — OpenRouter Provider
#
# Configures OpenRouter API key and model registry in setup.ini.
# Keys are written to provider_keys.env for direct harness injection.
#
# Usage:
#   sudo ./openrouter.sh              # Install/configure
#   sudo ./openrouter.sh --uninstall  # Remove config
# ─────────────────────────────────────────────────

set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/_common.sh"
provider_require_root

OPENROUTER_MODELS="moonshotai/kimi-k2.7-code,deepseek/deepseek-v4-flash"
INFERENCE_ENV="/etc/versa-agi/provider_keys.env"

_openrouter_write_attribution_env() {
  local env_file="$1"
  for kv in \
    "OR_SITE_URL=https://versavoice.ai" \
    "OR_APP_NAME=Versa AGi"; do
    local key="${kv%%=*}"
    local val="${kv#*=}"
    if [ -f "${env_file}" ] && grep -q "^${key}=" "${env_file}"; then
      sed -i "s|^${key}=.*|${key}=${val}|" "${env_file}"
    elif [ -f "${env_file}" ]; then
      echo "${key}=${val}" >> "${env_file}"
    else
      echo "${key}=${val}" >> "${env_file}"
    fi
  done
}

# ─── Uninstall ──────────────────────────────────────
if [ "${1:-}" = "--uninstall" ]; then
  echo ""
  echo "  ─── OpenRouter Provider — Uninstall ────────"
  echo ""

  provider_ini_set third_party openrouter_enabled false
  provider_ini_set third_party openrouter_api_key ""
  ok "OpenRouter disabled and API key cleared from setup.ini"

  if [ -f "${INFERENCE_ENV}" ]; then
    sed -i '/^OPENROUTER_API_KEY=/d' "${INFERENCE_ENV}"
    ok "OPENROUTER_API_KEY removed from ${INFERENCE_ENV}"
  fi

  if [ -f "${PATHS_ENV}" ]; then
    PROVIDERS="$(provider_ini_get third_party providers 'xai,openai,anthropic,openrouter')"
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

  AGENTS_DB="/var/lib/versa-agi/agents.db"
  if [ -f "${AGENTS_DB}" ]; then
    sqlite3 "${AGENTS_DB}" "
      UPDATE agents
      SET status='invalid_config',
          status_message='OpenRouter provider was removed. Reassign model via agitop.',
          updated_at=datetime('now')
      WHERE model IN ('moonshotai/kimi-k2.7-code', 'deepseek/deepseek-v4-flash')
        AND status != 'invalid_config';
    " 2>/dev/null || true
    ok "Agents using OpenRouter models marked as invalid_config"
  fi

  echo ""
  echo "  ✅ OpenRouter provider removed."
  echo "  Reassign affected agents in the Dashboard (agitop → Edit Agent)."
  echo ""
  exit 0
fi

# ─── Install / Configure ───────────────────────────
echo ""
echo "  ─── OpenRouter Provider — Configure ────────"
echo ""
echo "  Provider: OpenRouter (multi-vendor aggregator)"
echo "  Models:   ${OPENROUTER_MODELS}"
echo ""
echo "  ⚠ Spend is metered by OpenRouter prepaid credits (per token)."
echo "  Get a key at: https://openrouter.ai/keys"
echo ""

if [ -z "${VERSA_SETUP_PARENT:-}" ]; then
  read -p "  Proceed with OpenRouter configuration? [Y/n]: " -n 1 -r
  echo ""
  if [[ $REPLY =~ ^[Nn]$ ]]; then echo "  OpenRouter setup cancelled."; exit 0; fi
  echo ""
fi

api_key="$(provider_ini_get third_party openrouter_api_key '')"
if [ -z "${api_key}" ]; then
  echo "  An OpenRouter API key is required."
  echo ""
  read -p "  Enter your OpenRouter API Key: " api_key
  while [ -z "${api_key}" ]; do read -p "  Enter your OpenRouter API Key: " api_key; done
  echo ""
else
  ok "OpenRouter API key loaded from setup.ini"
fi

provider_ini_set third_party openrouter_enabled true
provider_ini_set third_party openrouter_api_key "${api_key}"
ok "setup.ini updated (openrouter_enabled=true)"
echo "  Models: ${OPENROUTER_MODELS}"

if [ -f "${INFERENCE_ENV}" ]; then
  if grep -q "^OPENROUTER_API_KEY=" "${INFERENCE_ENV}"; then
    sed -i "s|^OPENROUTER_API_KEY=.*|OPENROUTER_API_KEY=${api_key}|" "${INFERENCE_ENV}"
  else
    echo "OPENROUTER_API_KEY=${api_key}" >> "${INFERENCE_ENV}"
  fi
else
  echo "OPENROUTER_API_KEY=${api_key}" > "${INFERENCE_ENV}"
  chmod 600 "${INFERENCE_ENV}"
  chown root:root "${INFERENCE_ENV}"
fi
_openrouter_write_attribution_env "${INFERENCE_ENV}"
ok "provider_keys.env updated (OPENROUTER_API_KEY, OR_SITE_URL, OR_APP_NAME)"

if [ -f "${PATHS_ENV}" ]; then
  PROVIDERS="$(provider_ini_get third_party providers 'xai,openai,anthropic,openrouter')"
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
echo "  ✅ OpenRouter provider ready!"
echo "  Assign agents to OpenRouter models via Dashboard (agitop) → Edit Agent"
echo "  Add more models via agitop → Model Manager or agictl model catalog add"
echo ""
