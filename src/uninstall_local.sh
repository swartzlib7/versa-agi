#!/bin/bash
# ─────────────────────────────────────────────────────
# Versa AGi — Uninstall Local AI (Experimental)
#
# Cleanly removes local AI capability and deactivates
# any agents currently configured with local models.
#
# Usage:  sudo ./uninstall_local.sh
#
# What it removes:
#   - Docker SYCL container + image (if Intel backend was used)
#   - versa-agi-ollama-ipex.service (legacy — if IPEX backend was used)
#   - Legacy IPEX Miniforge (/opt/versa-agi/miniforge3/)
#   - Legacy IPEX Ollama binary (/opt/versa-agi/ipex-ollama/)
#   - SYCL model directory (/opt/versa-agi/sycl-models/)
#   - versa-agi-inference_endpoint.service (systemd)
#   - Inference Server venv (/opt/versa-agi/inference_endpoint/)
#   - Inference Server config (/etc/versa-agi/inference_endpoint_config.yaml)
#   - Updates paths.env (LOCAL_AI_ENABLED=false)
#
# What it preserves:
#   - Standard Ollama (user may use it for other purposes)
#   - Downloaded models (managed by Ollama)
#   - agents.db schema (no columns to remove)
# ─────────────────────────────────────────────────────

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
  error "This script must be run as root (sudo ./uninstall_local.sh)"
fi

# ─── Paths ──────────────────────────────────────────
INFERENCE_VENV="/opt/versa-agi/inference_endpoint"
INFERENCE_CONFIG="/etc/versa-agi/inference_endpoint_config.yaml"
INFERENCE_SERVICE="/etc/systemd/system/versa-agi-inference_endpoint.service"
IPEX_SERVICE_NAME="versa-agi-ollama-ipex"  # Legacy cleanup
IPEX_SERVICE_FILE="/etc/systemd/system/${IPEX_SERVICE_NAME}.service"
MINIFORGE_DIR="/opt/versa-agi/miniforge3"  # Legacy cleanup
IPEX_OLLAMA_DIR_LEGACY="/opt/versa-agi/ipex-ollama"  # Legacy cleanup
SYCL_CONTAINER="versa-agi-sycl"
SYCL_MODEL_DIR="/opt/versa-agi/sycl-models"
PATHS_ENV="/etc/versa-agi/paths.env"
AGENTS_DB="/var/lib/versa-agi/agents.db"

# Read local models list from paths.env
LOCAL_MODELS=""
if [ -f "${PATHS_ENV}" ]; then
  LOCAL_MODELS=$(grep "^VERSA_LOCAL_MODELS=" "${PATHS_ENV}" 2>/dev/null | cut -d'"' -f2 || true)
fi

# ─── Banner ─────────────────────────────────────────
echo ""
echo "═══════════════════════════════════════════════"
echo "  Versa AGi — Uninstall Local AI (Experimental)"
echo "═══════════════════════════════════════════════"
echo ""
echo "  This will remove local AI capability and deactivate"
echo "  any agents currently configured with local models."
echo ""
echo "  Agents with local models will be set to inactive."
echo "  You will need to re-assign them a cloud model via"
echo "  Dashboard or agictl before reactivating."
echo ""

read -p "  Proceed? [y/N]: " -n 1 -r
echo ""
if [[ ! $REPLY =~ ^[Yy]$ ]]; then
  echo "  Uninstall cancelled."
  exit 0
fi

echo ""

# ─── Step 1: Stop Docker SYCL Container ─────────────
if docker ps -a --format '{{.Names}}' 2>/dev/null | grep -q "^${SYCL_CONTAINER}$"; then
  docker stop "${SYCL_CONTAINER}" 2>/dev/null || true
  docker rm "${SYCL_CONTAINER}" 2>/dev/null || true
  ok "Removed Docker SYCL container: ${SYCL_CONTAINER}"
else
  info "No Docker SYCL container found"
fi

# Remove SYCL Docker image
if docker images --format '{{.Repository}}' 2>/dev/null | grep -q "^llama-sycl-server$"; then
  docker rmi llama-sycl-server 2>/dev/null || true
  ok "Removed Docker image: llama-sycl-server"
fi

# ─── Step 1b: Stop Legacy IPEX Ollama Service (migration cleanup) ──
if [ -f "${IPEX_SERVICE_FILE}" ]; then
  if systemctl is-active --quiet "${IPEX_SERVICE_NAME}" 2>/dev/null; then
    systemctl stop "${IPEX_SERVICE_NAME}"
    ok "Stopped ${IPEX_SERVICE_NAME} service"
  fi
  systemctl disable "${IPEX_SERVICE_NAME}" --quiet 2>/dev/null || true
  rm -f "${IPEX_SERVICE_FILE}"
  systemctl daemon-reload
  ok "Removed and disabled ${IPEX_SERVICE_NAME}.service"

  # Unmask stock Ollama service (was masked when IPEX service was installed)
  systemctl unmask ollama --quiet 2>/dev/null || true
  ok "Unmasked stock Ollama service"
else
  info "No legacy IPEX Ollama service found"
fi

# ─── Step 2: Stop and Disable Inference Server Service ──────
if systemctl is-active --quiet versa-agi-inference_endpoint 2>/dev/null; then
  systemctl stop versa-agi-inference_endpoint
  ok "Stopped versa-agi-inference_endpoint service"
else
  info "Inference Server service was not running"
fi

if [ -f "${INFERENCE_SERVICE}" ]; then
  systemctl disable versa-agi-inference_endpoint --quiet 2>/dev/null || true
  rm -f "${INFERENCE_SERVICE}"
  systemctl daemon-reload
  ok "Removed and disabled versa-agi-inference_endpoint.service"
fi

# ─── Step 2: Deactivate Agents with Local Models ───
if [ -f "${AGENTS_DB}" ] && [ -n "${LOCAL_MODELS}" ]; then
  info "Checking for agents with local models..."

  IFS=',' read -ra MODELS <<< "${LOCAL_MODELS}"
  DEACTIVATED=0

  for model in "${MODELS[@]}"; do
    model=$(echo "${model}" | xargs)
    # Find agents using this model
    AGENTS=$(sqlite3 "${AGENTS_DB}" \
      "SELECT name FROM agents WHERE model='${model}' AND inactive=0;" 2>/dev/null || true)

    for agent in ${AGENTS}; do
      sqlite3 "${AGENTS_DB}" \
        "UPDATE agents SET inactive=1, status='inactive', \
         status_message='Deactivated: local AI removed. Re-assign a cloud model to reactivate.', \
         updated_at=datetime('now') WHERE name='${agent}';" 2>/dev/null || true
      ok "  → ${agent} (${model}) → inactive"
      DEACTIVATED=$((DEACTIVATED + 1))
    done
  done

  if [ "${DEACTIVATED}" -eq 0 ]; then
    info "No agents were using local models"
  else
    ok "Deactivated ${DEACTIVATED} agent(s)"
  fi

  # Clear invalid_config status from any agents (resolved by removal)
  sqlite3 "${AGENTS_DB}" \
    "UPDATE agents SET status='idle', status_message=NULL, updated_at=datetime('now') \
     WHERE status='invalid_config';" 2>/dev/null || true
fi

# ─── Step 3: Remove Inference Server Venv ───────────────────
if [ -d "${INFERENCE_VENV}" ]; then
  rm -rf "${INFERENCE_VENV}"
  ok "Removed Inference Server venv: ${INFERENCE_VENV}"
fi

# ─── Step 4: Remove Inference Server Config ────────────────
if [ -f "${INFERENCE_CONFIG}" ]; then
  rm -f "${INFERENCE_CONFIG}"
  ok "Removed Inference Server config: ${INFERENCE_CONFIG}"
fi

# ─── Step 5: Remove SYCL Models + Legacy IPEX Artifacts ──
if [ -d "${SYCL_MODEL_DIR}" ]; then
  rm -rf "${SYCL_MODEL_DIR}"
  ok "Removed SYCL model directory: ${SYCL_MODEL_DIR}"
fi

if [ -d "${MINIFORGE_DIR}" ]; then
  rm -rf "${MINIFORGE_DIR}"
  ok "Removed legacy Miniforge: ${MINIFORGE_DIR}"
fi

if [ -d "${IPEX_OLLAMA_DIR_LEGACY}" ]; then
  rm -rf "${IPEX_OLLAMA_DIR_LEGACY}"
  ok "Removed legacy IPEX Ollama binary: ${IPEX_OLLAMA_DIR_LEGACY}"
fi

# ─── Step 5: Update paths.env ──────────────────────
if [ -f "${PATHS_ENV}" ]; then
  sed -i 's/^VERSA_LOCAL_AI_ENABLED=.*/VERSA_LOCAL_AI_ENABLED="false"/' "${PATHS_ENV}"
  sed -i 's/^VERSA_GPU_BACKEND=.*/VERSA_GPU_BACKEND="standard"/' "${PATHS_ENV}"
  # Reset execution mode to cloud — local AI is being removed
  CURRENT_MODE=$(grep "^VERSA_EXECUTION_MODE=" "${PATHS_ENV}" 2>/dev/null | cut -d'"' -f2 || true)
  if [ "${CURRENT_MODE}" = "local" ] || [ "${CURRENT_MODE}" = "hybrid" ]; then
    sed -i 's/^VERSA_EXECUTION_MODE=.*/VERSA_EXECUTION_MODE="cloud"/' "${PATHS_ENV}"
    ok "Execution mode reset to cloud (was ${CURRENT_MODE})"
  fi
  ok "Updated paths.env (LOCAL_AI_ENABLED=false, GPU_BACKEND=standard)"
fi

# ─── Step 6: Update setup.ini ──────────────────────
# Update both source (master) and deployed (runtime) copies.
_INI_FILES=()
if [ -f "${SCRIPT_DIR}/setup.ini" ]; then
  _INI_FILES+=("${SCRIPT_DIR}/setup.ini")
fi
if [ -f "/etc/versa-agi/setup.ini" ] && [ "/etc/versa-agi/setup.ini" != "${SCRIPT_DIR}/setup.ini" ]; then
  _INI_FILES+=("/etc/versa-agi/setup.ini")
fi

for SETUP_INI in "${_INI_FILES[@]}"; do
  sed -i '/^\[local_ai\]/,/^\[/{s/^enabled=.*/enabled=false/}' "${SETUP_INI}"
  sed -i '/^\[local_ai\]/,/^\[/{s/^gpu_backend=.*/gpu_backend=standard/}' "${SETUP_INI}"
  # Reset execution mode to cloud — local AI is being removed
  CURRENT_INI_MODE=$(awk -F '=' '/^\[gemini\]/{f=1; next} /^\[/{f=0} f && $1=="mode"{print $2}' "${SETUP_INI}" 2>/dev/null | xargs)
  if [ "${CURRENT_INI_MODE}" = "local" ] || [ "${CURRENT_INI_MODE}" = "hybrid" ]; then
    sed -i '/^\[gemini\]/,/^\[/{s/^mode=.*/mode=cloud/}' "${SETUP_INI}"
    ok "setup.ini [gemini] mode reset to cloud (was ${CURRENT_INI_MODE})"
  fi
  ok "Updated: ${SETUP_INI}"
done

# ─── Done ───────────────────────────────────────────
echo ""
echo "  ✅ Local AI removed."
echo ""
echo "  ⚠ Standard Ollama and downloaded models left in place."
echo "    To remove: sudo systemctl stop ollama && sudo rm -rf /usr/local/lib/ollama"
echo "  ⚠ Legacy IPEX/Miniforge components removed (if present)."
echo "  ⚠ Docker SYCL container and models removed (if present)."
echo ""

if [ "${DEACTIVATED:-0}" -gt 0 ]; then
  echo "  Deactivated agents must be re-configured before reactivation:"
  echo "    1. Assign a cloud model via Dashboard: agitop → Edit Agent → Change model"
  echo "    2. Then activate the agent via Dashboard or CLI: agictl agent activate <name>"
  echo ""
fi

if [ "${CURRENT_MODE:-}" = "local" ] || [ "${CURRENT_INI_MODE:-}" = "local" ]; then
  echo "  ⚠ System was in local-only mode. Execution mode has been"
  echo "    reset to cloud. A Gemini API key is now required."
  echo "    Run setup.sh to configure if needed."
  echo ""
fi
