#!/bin/bash
# ─────────────────────────────────────────────────
# Versa AGi — Third-Party Cloud Providers Uninstall
#
# Removes cloud proxy configuration and disables third-party models.
# Does NOT remove the Inference Server venv (may be used by local AI).
#
# Usage:  sudo ./uninstall_proxy.sh
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
  error "This script must be run as root (sudo ./uninstall_proxy.sh)"
fi

echo ""
echo "═══════════════════════════════════════════════"
echo "  Versa AGi — Third-Party Cloud Providers Uninstall"
echo "═══════════════════════════════════════════════"
echo ""
echo "  This will disable cloud third-party models and remove"
echo "  provider API keys. The Inference Server venv is preserved"
echo "  (it may be used by local AI)."
echo ""

read -p "  Proceed with Third-Party Cloud Providers removal? [y/N]: " -n 1 -r
echo ""
if [[ ! $REPLY =~ ^[Yy]$ ]]; then
  echo "  Cancelled."
  exit 0
fi

echo ""

# ─── Paths ──────────────────────────────────────────
PATHS_ENV="/etc/versa-agi/paths.env"
INFERENCE_ENV="/etc/versa-agi/inference_endpoint.env"
INFERENCE_CONFIG="/etc/versa-agi/inference_endpoint_config.yaml"
AGENTS_DB="/var/lib/versa-agi/agents.db"

# ─── Step 1: Disable in paths.env ──────────────────
if [ -f "${PATHS_ENV}" ]; then
  sed -i 's/^VERSA_THIRD_PARTY_ENABLED=.*/VERSA_THIRD_PARTY_ENABLED="false"/' "${PATHS_ENV}"
  sed -i 's/^VERSA_THIRD_PARTY_MODELS=.*/VERSA_THIRD_PARTY_MODELS=""/' "${PATHS_ENV}"
  ok "paths.env updated (THIRD_PARTY_ENABLED=false, THIRD_PARTY_MODELS cleared)"
fi

# ─── Step 2: Remove API keys from inference_endpoint.env ──────
if [ -f "${INFERENCE_ENV}" ]; then
  # Remove all provider keys (XAI_API_KEY, OPENAI_API_KEY, ANTHROPIC_API_KEY, OPENROUTER_API_KEY, etc.)
  sed -i '/^XAI_API_KEY=/d' "${INFERENCE_ENV}"
  sed -i '/^OPENAI_API_KEY=/d' "${INFERENCE_ENV}"
  sed -i '/^ANTHROPIC_API_KEY=/d' "${INFERENCE_ENV}"
  sed -i '/^OPENROUTER_API_KEY=/d' "${INFERENCE_ENV}"
  ok "Provider API keys removed from ${INFERENCE_ENV}"
fi

# ─── Step 3: Regenerate inference_endpoint_config.yaml ─────────
# Only keep local models if local AI is enabled
if [ -f "${PATHS_ENV}" ]; then
  LOCAL_ENABLED=$(grep '^VERSA_LOCAL_AI_ENABLED=' "${PATHS_ENV}" 2>/dev/null | cut -d'"' -f2)
  LOCAL_MODELS=$(grep '^VERSA_LOCAL_MODELS=' "${PATHS_ENV}" 2>/dev/null | cut -d'"' -f2)
  OLLAMA_HOST=$(grep '^VERSA_OLLAMA_HOST=' "${PATHS_ENV}" 2>/dev/null | cut -d'"' -f2)
  OLLAMA_HOST="${OLLAMA_HOST:-http://localhost:11434}"

  if [ "${LOCAL_ENABLED}" = "true" ] && [ -n "${LOCAL_MODELS}" ]; then
    {
      echo "model_list:"
      IFS=',' read -ra LM <<< "${LOCAL_MODELS}"
      for model in "${LM[@]}"; do
        model=$(echo "${model}" | xargs)
        echo "  - model_name: ${model}"
        echo "    inference_endpoint_params:"
        echo "      model: ollama/${model}"
        echo "      api_base: ${OLLAMA_HOST}"
      done
    } > "${INFERENCE_CONFIG}"
    ok "inference_endpoint_config.yaml regenerated (local models only)"
    # Restart proxy for local models
    systemctl restart versa-agi-inference_endpoint 2>/dev/null || true
  else
    # No local models either — stop Inference Server if nothing needs it
    info "No local models configured — Inference Endpoint not needed"
    # Don't stop it — setup.sh --update will handle lifecycle
  fi
fi

# ─── Step 4: Mark proxy agents as invalid_config ───
if [ -f "${AGENTS_DB}" ]; then
  sqlite3 "${AGENTS_DB}" "
    UPDATE agents
    SET status='invalid_config',
        status_message='Cloud proxy was disabled. Change model to a cloud or local variant.',
        updated_at=datetime('now')
    WHERE model IN (SELECT model FROM agents WHERE status != 'invalid_config')
      AND model NOT IN (
        SELECT value FROM (
          SELECT trim(value) as value
          FROM json_each('[\"' || replace((
            SELECT replace(value, '\"', '')
            FROM (SELECT value FROM pragma_table_info('agents') LIMIT 0)
          ), ',', '\",\"') || '\"]')
        )
      );
  " 2>/dev/null || true
  # Simpler fallback: just set any agent with a known third-party model to invalid
  sqlite3 "${AGENTS_DB}" "
    UPDATE agents
    SET status='invalid_config',
        status_message='Cloud proxy was disabled. Change model to a cloud or local variant.',
        updated_at=datetime('now')
    WHERE model IN ('grok-4-1-fast-reasoning', 'grok-4.20-reasoning',
                    'moonshotai/kimi-k2.7-code', 'deepseek/deepseek-v4-flash')
      OR model LIKE 'gpt-%'
      OR model LIKE 'claude-%'
      AND status != 'invalid_config';
  " 2>/dev/null || true
  ok "Proxy-assigned agents marked as invalid_config"
fi

# ─── Step 5: Update setup.ini ──────────────────────
# Update both source (master) and deployed (runtime) copies.
_INI_FILES=()
if [ -f "${SCRIPT_DIR}/setup.ini" ]; then
  _INI_FILES+=("${SCRIPT_DIR}/setup.ini")
fi
if [ -f "/etc/versa-agi/setup.ini" ] && [ "/etc/versa-agi/setup.ini" != "${SCRIPT_DIR}/setup.ini" ]; then
  _INI_FILES+=("/etc/versa-agi/setup.ini")
fi

for SETUP_INI in "${_INI_FILES[@]}"; do
  sed -i '/^\[third_party\]/,/^\[/{s/^xai_enabled=.*/xai_enabled=false/}' "${SETUP_INI}"
  sed -i '/^\[third_party\]/,/^\[/{s/^openai_enabled=.*/openai_enabled=false/}' "${SETUP_INI}"
  sed -i '/^\[third_party\]/,/^\[/{s/^anthropic_enabled=.*/anthropic_enabled=false/}' "${SETUP_INI}"
  sed -i '/^\[third_party\]/,/^\[/{s/^openrouter_enabled=.*/openrouter_enabled=false/}' "${SETUP_INI}"
  # Clear API keys from setup.ini for security
  sed -i '/^\[third_party\]/,/^\[/{s/^xai_api_key=.*/xai_api_key=/}' "${SETUP_INI}"
  sed -i '/^\[third_party\]/,/^\[/{s/^openai_api_key=.*/openai_api_key=/}' "${SETUP_INI}"
  sed -i '/^\[third_party\]/,/^\[/{s/^anthropic_api_key=.*/anthropic_api_key=/}' "${SETUP_INI}"
  sed -i '/^\[third_party\]/,/^\[/{s/^openrouter_api_key=.*/openrouter_api_key=/}' "${SETUP_INI}"
  ok "Updated: ${SETUP_INI}"
done

# ─── Done ───────────────────────────────────────────
echo ""
echo "  ✅ Third-Party Cloud Providers removed."
echo ""
echo "  Agents previously using third-party models are now marked"
echo "  as invalid_config. Reassign them in the Dashboard."
echo ""
echo "  To re-enable: sudo $(dirname "${BASH_SOURCE[0]}")/setup_proxy.sh"
echo ""
