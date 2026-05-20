#!/bin/bash
# ─────────────────────────────────────────────────────
# Versa AGi — Local AI Setup (Experimental)
#
# Installs local model inference backend with direct inference routing.
# Supports two GPU backends:
#   standard — Stock Ollama (NVIDIA CUDA, AMD ROCm)
#   intel    — Docker SYCL (Intel ARC Battlemage/Alchemist via containerized llama.cpp)
#
# Can be run standalone or called from setup.sh.
#
# Usage:  sudo ./setup_local.sh [OPTIONS]
#
# Options (all optional — defaults from setup.ini):
#   --topology TYPE         Deployment topology: local, server, or client
#   --remote-inference-url U  Remote Inference URL (client topology)
#   --inference-master-key K  Inference Server master key (server/client)
#   --gpu-backend TYPE      GPU backend: standard or intel
#   --intel-card-count N    Number of identical Intel ARC GPUs
#   --intel-device-id ID    PCI device ID (e.g. 8086:e212)
#   --ollama-host URL       Ollama API host
#   --proxy-port PORT       Inference Endpoint port
#   --default-model MODEL   Default Ollama model to pull
#   --local-models LIST     Comma-separated model registry
#   --auto-pull BOOL        Auto-pull default model
#   --watchdog-user USER    Watchdog OS user
#   --coa-user USER         COA OS user
#   --paths-env FILE        Path to paths.env
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
  error "This script must be run as root (sudo ./setup_local.sh)"
fi

# ─── Default Values ─────────────────────────────────
GPU_BACKEND="${GPU_BACKEND:-standard}"
INTEL_CARD_COUNT="${INTEL_CARD_COUNT:-1}"
INTEL_DEVICE_ID="${INTEL_DEVICE_ID:-}"
OLLAMA_HOST="${OLLAMA_HOST:-http://localhost:11434}"
PROXY_PORT="${PROXY_PORT:-4000}"
DEFAULT_MODEL="${DEFAULT_MODEL:-gemma4:e4b}"
LOCAL_MODELS="${LOCAL_MODELS:-gemma4:e4b,gemma4:26b,gemma4:31b,qwen3.6:35b}"
AUTO_PULL="${AUTO_PULL:-true}"
WATCHDOG_USER="${WATCHDOG_USER:-watchdog}"
COA_USER="${COA_USER:-coa}"
PATHS_ENV="${PATHS_ENV:-/etc/versa-agi/paths.env}"
TOPOLOGY="${TOPOLOGY:-local}"
REMOTE_INFERENCE_URL="${REMOTE_INFERENCE_URL:-}"
INFERENCE_MASTER_KEY="${INFERENCE_MASTER_KEY:-}"

# ─── Intel SYCL Paths ──────────────────────────────
SYCL_MODEL_DIR="/opt/versa-agi/sycl-models"
SYCL_PORT="${SYCL_PORT:-8080}"
SYCL_CONTAINER="versa-agi-sycl"
SYCL_IMAGE="llama-sycl-server"
SYCL_LLAMA_CPP_TAG="${SYCL_LLAMA_CPP_TAG:-b9082}"
HF_TOKEN="${HF_TOKEN:-}"

# ─── Legacy Cleanup Paths ──────────────────────────
MINIFORGE_DIR="/opt/versa-agi/miniforge3"
IPEX_OLLAMA_DIR_LEGACY="/opt/versa-agi/ipex-ollama"
IPEX_SERVICE_NAME="versa-agi-ollama-ipex"
IPEX_SERVICE_FILE="/etc/systemd/system/${IPEX_SERVICE_NAME}.service"

# ─── Intel SYCL Model Map ──────────────────────────
declare -A SYCL_MODEL_REPO=(
  ["gemma4:e4b"]="unsloth/gemma-4-12B-A2B-it-GGUF"
  ["gemma4:26b"]="unsloth/gemma-4-26B-A4B-it-GGUF"
  ["gemma4:31b"]="unsloth/gemma-4-31B-it-GGUF"
  ["qwen3.6:35b"]="unsloth/Qwen3.6-35B-A3B-GGUF"
)
declare -A SYCL_MODEL_FILE=(
  ["gemma4:e4b"]="gemma-4-12B-A2B-it-UD-Q4_K_M.gguf"
  ["gemma4:26b"]="gemma-4-26B-A4B-it-UD-Q4_K_M.gguf"
  ["gemma4:31b"]="gemma-4-31B-it-UD-Q4_K_M.gguf"
  ["qwen3.6:35b"]="Qwen3.6-35B-A3B-UD-Q4_K_M.gguf"
)

# ─── Parse Arguments ────────────────────────────────
while [[ $# -gt 0 ]]; do
  case "$1" in
    --gpu-backend)        GPU_BACKEND="$2"; shift 2 ;;
    --intel-card-count)   INTEL_CARD_COUNT="$2"; shift 2 ;;
    --intel-device-id)    INTEL_DEVICE_ID="$2"; shift 2 ;;
    --ollama-host)        OLLAMA_HOST="$2"; shift 2 ;;
    --proxy-port)         PROXY_PORT="$2"; shift 2 ;;
    --default-model)      DEFAULT_MODEL="$2"; shift 2 ;;
    --local-models)       LOCAL_MODELS="$2"; shift 2 ;;
    --auto-pull)          AUTO_PULL="$2"; shift 2 ;;
    --watchdog-user)      WATCHDOG_USER="$2"; shift 2 ;;
    --coa-user)           COA_USER="$2"; shift 2 ;;
    --paths-env)          PATHS_ENV="$2"; shift 2 ;;
    --hf-token)           HF_TOKEN="$2"; shift 2 ;;
    --sycl-llama-cpp-tag) SYCL_LLAMA_CPP_TAG="$2"; shift 2 ;;
    --topology)           TOPOLOGY="$2"; shift 2 ;;
    --remote-inference-url) REMOTE_INFERENCE_URL="$2"; shift 2 ;;
    --inference-master-key) INFERENCE_MASTER_KEY="$2"; shift 2 ;;
    *) warn "Unknown option: $1"; shift ;;
  esac
done

# ─── Standalone setup.ini Fallback ──────────────────
# When run directly (not via setup.sh), read pre-populated
# values from the source setup.ini to avoid re-prompting.
_LOCAL_INI=""
if [ -f "${SCRIPT_DIR}/setup.ini" ]; then
  _LOCAL_INI="${SCRIPT_DIR}/setup.ini"
fi
if [ -n "${_LOCAL_INI}" ]; then
  _ini_val() {
    awk -F '=' '/^\['"$1"'\]/{f=1; next} /^\[/{f=0} f && $1=="'"$2"'"{gsub(/^[ \t]+|[ \t]+$/, "", $2); print $2}' "${_LOCAL_INI}" 2>/dev/null
  }
  [ -z "${HF_TOKEN}" ]         && HF_TOKEN="$(_ini_val local_ai hf_token)"
  [ -z "${INTEL_DEVICE_ID}" ]  && INTEL_DEVICE_ID="$(_ini_val local_ai intel_device_id)"
  [ "${INTEL_CARD_COUNT}" = "1" ] && { _v="$(_ini_val local_ai intel_card_count)"; [ -n "$_v" ] && INTEL_CARD_COUNT="$_v"; }
  [ -z "${SYCL_LLAMA_CPP_TAG}" ] || true  # already has default
  # Topology fallback from INI
  if [ "${TOPOLOGY}" = "local" ]; then
    _t="$(_ini_val local_ai topology)"
    [ -n "$_t" ] && TOPOLOGY="$_t"
  fi
  [ -z "${REMOTE_INFERENCE_URL}" ] && REMOTE_INFERENCE_URL="$(_ini_val local_ai remote_inference_url)"
  [ -z "${INFERENCE_MASTER_KEY}" ] && INFERENCE_MASTER_KEY="$(_ini_val local_ai inference_master_key)"
fi

# ─── Banner ─────────────────────────────────────────
echo ""
echo "═══════════════════════════════════════════════"
echo "  Versa AGi — Local AI Setup (Experimental)"
echo "═══════════════════════════════════════════════"
echo ""
echo "  This will install local AI capability, allowing sub-agents"
echo "  to run on your hardware instead of Google Cloud."
echo ""
echo "  ┌─────────────────────────────────────────┐"
echo "  │  SUPPORTED GPU BACKENDS                 │"
echo "  │                                         │"
echo "  │  1. Standard Ollama                     │"
echo "  │     • NVIDIA (CUDA)                     │"
echo "  │     • AMD (ROCm)                        │"
echo "  │                                         │"
echo "  │  2. Intel ARC — Docker SYCL             │"
echo "  │     • ARC A-series, B-series            │"
echo "  │     • Requires Docker (auto-install)    │"
echo "  │     • Requires Ubuntu 24.04             │"
echo "  │                                         │"
echo "  │  ⚠ Configure hardware & drivers BEFORE  │"
echo "  │    running this installer.              │"
echo "  └─────────────────────────────────────────┘"
echo ""
echo "  The user is responsible for ensuring their hardware"
echo "  meets the requirements for the selected model."
echo ""

# When called from setup.sh, skip the confirmation prompt
if [ -z "${VERSA_SETUP_PARENT:-}" ]; then
  read -p "  Proceed with local AI setup? [y/N]: " -n 1 -r
  echo ""
  if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    echo "  Local AI setup cancelled."
    exit 0
  fi
fi

# ═════════════════════════════════════════════════
# SERVER MODE — Inference-only setup
# ═════════════════════════════════════════════════
SERVER_STATE_FILE="/etc/versa-agi/server_config.json"
CLIENT_STATE_FILE="/etc/versa-agi/client_config.json"

if [ "${TOPOLOGY}" = "server" ]; then
  # ── Idempotent re-run check ──
  if [ -f "${SERVER_STATE_FILE}" ]; then
    echo ""
    echo "  ╭─────────────────────────────────────────────╮"
    echo "  │  Inference Server — Already Configured    │"
    echo "  ├─────────────────────────────────────────────┤"
    # Read and display current config from state file
    _srv_backend=$(jq -r '.gpu_backend // "unknown"' "${SERVER_STATE_FILE}" 2>/dev/null)
    _srv_model=$(jq -r '.active_model // "unknown"' "${SERVER_STATE_FILE}" 2>/dev/null)
    _srv_port=$(jq -r '.proxy_port // 4000' "${SERVER_STATE_FILE}" 2>/dev/null)
    _srv_ip=$(jq -r '.lan_ip // "unknown"' "${SERVER_STATE_FILE}" 2>/dev/null)
    _srv_key=$(jq -r '.inference_master_key // ""' "${SERVER_STATE_FILE}" 2>/dev/null)
    _srv_key_short="${_srv_key:0:8}...${_srv_key: -4}"
    echo "  │  GPU Backend:   ${_srv_backend}$(printf '%*s' $((27 - ${#_srv_backend})) '')│"
    echo "  │  Active Model:  ${_srv_model}$(printf '%*s' $((27 - ${#_srv_model})) '')│"
    echo "  │  Inference Port:  ${_srv_port}$(printf '%*s' $((27 - ${#_srv_port})) '')│"
    echo "  │  LAN URL:       http://${_srv_ip}:${_srv_port}$(printf '%*s' $((18 - ${#_srv_ip} - ${#_srv_port})) '')│"
    echo "  │  Master Key:    ${_srv_key_short}$(printf '%*s' $((27 - ${#_srv_key_short})) '')│"
    echo "  ╰─────────────────────────────────────────────╯"
    echo ""
    read -p "  Reconfigure? [y/N]: " -n 1 -r
    echo ""
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
      ok "Server configuration unchanged."
      exit 0
    fi
    echo ""
  fi

  # Server mode: no agents, no paths.env, no agent DB updates.
  # We override PATHS_ENV to /dev/null to prevent any writes.
  PATHS_ENV="/dev/null"

  # Generate master key if not already set
  if [ -z "${INFERENCE_MASTER_KEY}" ]; then
    INFERENCE_MASTER_KEY=$(openssl rand -hex 16)
    ok "Generated Inference Server master key"
  fi

  # Ensure /etc/versa-agi/ exists for server state + inference_endpoint config
  mkdir -p /etc/versa-agi

  # Server prerequisite: openssh-server (clients SSH-tunnel into this machine)
  if ! systemctl is-active --quiet ssh 2>/dev/null && ! systemctl is-active --quiet sshd 2>/dev/null; then
    info "Installing openssh-server (required for client SSH tunnels)..."
    if command -v apt-get &>/dev/null; then
      apt-get install -y openssh-server >/dev/null 2>&1
      systemctl enable --now ssh 2>/dev/null || systemctl enable --now sshd 2>/dev/null || true
      ok "openssh-server installed and started"
    else
      warn "openssh-server not detected — install manually for client SSH tunnel support"
    fi
  else
    ok "SSH server: running"
  fi

  # Ensure watchdog user has a login shell (required for SSH tunnel)
  _WD_SHELL=$(getent passwd "${WATCHDOG_USER:-watchdog}" 2>/dev/null | cut -d: -f7)
  if [ "${_WD_SHELL}" = "/usr/sbin/nologin" ] || [ "${_WD_SHELL}" = "/bin/false" ]; then
    usermod -s /bin/bash "${WATCHDOG_USER:-watchdog}" 2>/dev/null || true
    ok "watchdog shell set to /bin/bash (SSH tunnel requires valid shell)"
  fi

  # Fall through to GPU backend selection + install (shared with local mode)
  # After install, the server-specific postamble writes state file + banner.
fi

# ═════════════════════════════════════════════════
# CLIENT REMOTE MODE — Connect to remote inference
# ═════════════════════════════════════════════════
if [ "${TOPOLOGY}" != "server" ]; then
  echo ""
  echo "  Where is the local AI inference running?"
  echo ""
  echo "    1) This machine — GPU installed here (standard/intel)"
  echo "    2) Remote server — Connect to a LAN/DDNS inference server"
  echo ""

  if [ -n "${REMOTE_INFERENCE_URL}" ]; then
    _LOC_DEFAULT=2
  else
    _LOC_DEFAULT=1
  fi

  read -p "  Select [${_LOC_DEFAULT}]: " _LOC_CHOICE
  _LOC_CHOICE=${_LOC_CHOICE:-${_LOC_DEFAULT}}

  if [ "${_LOC_CHOICE}" = "2" ]; then
    # ── Client remote mode: connect to existing server ──

    # Check for existing client config (idempotent re-run)
    if [ -f "${CLIENT_STATE_FILE}" ]; then
      echo ""
      _cli_url=$(jq -r '.remote_url // "unknown"' "${CLIENT_STATE_FILE}" 2>/dev/null)
      _cli_models=$(jq -r '.models // [] | join(",")' "${CLIENT_STATE_FILE}" 2>/dev/null)
      # Quick health check
      _cli_status="✗ Unreachable"
      if curl -sf -o /dev/null -H "Authorization: Bearer ${INFERENCE_MASTER_KEY}" "${_cli_url}/v1/models" 2>/dev/null; then
        _cli_status="✓ Reachable"
      fi
      echo "  ╭─────────────────────────────────────────────╮"
      echo "  │  Remote AI Client — Already Configured   │"
      echo "  ├─────────────────────────────────────────────┤"
      echo "  │  Server URL:    ${_cli_url}"
      echo "  │  Models:        ${_cli_models}"
      echo "  │  Status:        ${_cli_status}"
      echo "  ╰─────────────────────────────────────────────╯"
      echo ""
      read -p "  Reconfigure? [y/N]: " -n 1 -r
      echo ""
      if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        ok "Client configuration unchanged."
        exit 0
      fi
    fi

    # Prompt for remote URL
    if [ -n "${REMOTE_INFERENCE_URL}" ]; then
      _url_default="${REMOTE_INFERENCE_URL}"
    else
      _url_default=""
    fi
    echo ""
    echo "  (Hint: Use port 11434 for Ollama or port 8080 for Intel SYCL)"
    read -p "  Enter remote Inference URL (e.g. http://192.168.1.100:11434): " _remote_url
    REMOTE_INFERENCE_URL="${_remote_url:-${_url_default}}"
    if [ -z "${REMOTE_INFERENCE_URL}" ]; then
      error "Remote URL is required for client topology."
    fi

    # Prompt for master key
    read -p "  Enter Inference Master Key: " _master_key
    INFERENCE_MASTER_KEY="${_master_key:-${INFERENCE_MASTER_KEY}}"

    # Health check
    echo ""
    info "Testing connection to ${REMOTE_INFERENCE_URL}..."
    if curl -sf -o /dev/null "${REMOTE_INFERENCE_URL}/v1/models" 2>/dev/null; then
      ok "Remote server reachable (health: ok)"
    else
      warn "Remote server may not be reachable at ${REMOTE_INFERENCE_URL}"
      warn "Continuing anyway — check server is running and firewall allows connections."
    fi

    # Query /v1/models
    _remote_models=""
    _models_json=$(curl -sf "${REMOTE_INFERENCE_URL}/v1/models" 2>/dev/null || echo "")
    if [ -n "${_models_json}" ]; then
      _remote_models=$(echo "${_models_json}" | jq -r '.data[].id' 2>/dev/null | paste -sd ',')
    fi
    if [ -n "${_remote_models}" ]; then
      ok "Available models: ${_remote_models}"
    else
      warn "Could not query models from server. VERSA_LOCAL_MODELS will be empty."
      _remote_models=""
    fi


    # Inference master key auth block removed

    # ── SSH Tunnel Setup ──────────────────────────────
    # Gemini CLI requires HTTPS for non-localhost URLs.
    # We create an SSH tunnel: localhost:{port} → server:{port}
    # This provides encryption AND satisfies the localhost check.
    echo ""
    section "SSH Tunnel — Secure Connection to Inference Server"

    # Extract server host and port from URL
    _TUNNEL_HOST=$(echo "${REMOTE_INFERENCE_URL}" | sed -E 's|https?://||;s|:[0-9]+$||;s|/.*||')
    _TUNNEL_PORT=$(echo "${REMOTE_INFERENCE_URL}" | grep -oP ':\K[0-9]+$' || echo "11434")
    _SSH_KEY="/home/${WATCHDOG_USER}/.ssh/versa_agi_ed25519"
    _SSH_DIR="/home/${WATCHDOG_USER}/.ssh"
    _TUNNEL_SERVICE="versa-agi-tunnel"

    # Step 1: Generate SSH key for watchdog (if not exists)
    if [ ! -f "${_SSH_KEY}" ]; then
      info "Generating SSH key for ${WATCHDOG_USER}..."
      mkdir -p "${_SSH_DIR}"
      ssh-keygen -t ed25519 -f "${_SSH_KEY}" -N "" -C "${WATCHDOG_USER}@$(hostname)" >/dev/null 2>&1
      chown -R "${WATCHDOG_USER}:${WATCHDOG_USER}" "${_SSH_DIR}"
      chmod 700 "${_SSH_DIR}"
      chmod 600 "${_SSH_KEY}"
      chmod 644 "${_SSH_KEY}.pub"
      ok "SSH key generated: ${_SSH_KEY}"
    else
      ok "SSH key exists: ${_SSH_KEY}"
    fi

    # Step 2: Display public key and wait for user to authorize on server
    _PUB_KEY=$(cat "${_SSH_KEY}.pub")
    echo ""
    echo "  ╭──────────────────────────────────────────────────────────────╮"
    echo "  │  ACTION REQUIRED: Authorize this key on the server          │"
    echo "  ├──────────────────────────────────────────────────────────────┤"
    echo "  │                                                              │"
    echo "  │  On the SERVER (${_TUNNEL_HOST}), run:                       │"
    echo "  │                                                              │"
    echo "  │  sudo mkdir -p /home/${WATCHDOG_USER}/.ssh"
    echo "  │  echo '${_PUB_KEY}' | sudo tee -a /home/${WATCHDOG_USER}/.ssh/authorized_keys"
    echo "  │  sudo chown -R ${WATCHDOG_USER}:${WATCHDOG_USER} /home/${WATCHDOG_USER}/.ssh"
    echo "  │  sudo chmod 700 /home/${WATCHDOG_USER}/.ssh"
    echo "  │  sudo chmod 600 /home/${WATCHDOG_USER}/.ssh/authorized_keys"
    echo "  │                                                              │"
    echo "  ╰──────────────────────────────────────────────────────────────╯"
    echo ""
    read -p "  Press Enter when the key has been added on the server... "

    # Step 3: Test SSH connectivity
    info "Testing SSH connection to ${WATCHDOG_USER}@${_TUNNEL_HOST}..."
    if sudo -u "${WATCHDOG_USER}" ssh -i "${_SSH_KEY}" \
        -o StrictHostKeyChecking=accept-new \
        -o ConnectTimeout=10 \
        -o BatchMode=yes \
        "${WATCHDOG_USER}@${_TUNNEL_HOST}" "echo ok" >/dev/null 2>&1; then
      ok "SSH connection successful"
    else
      warn "SSH connection failed. The tunnel service will be created but may not start."
      warn "Verify: sudo -u ${WATCHDOG_USER} ssh -i ${_SSH_KEY} ${WATCHDOG_USER}@${_TUNNEL_HOST}"
    fi

    # Step 4: Create systemd tunnel service
    info "Creating SSH tunnel service..."
    cat > "/etc/systemd/system/${_TUNNEL_SERVICE}.service" <<TUNNELEOF
[Unit]
Description=Versa AGi Inference SSH Tunnel (${_TUNNEL_HOST}:${_TUNNEL_PORT})
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${WATCHDOG_USER}
ExecStart=/usr/bin/ssh -N -L ${_TUNNEL_PORT}:localhost:${_TUNNEL_PORT} \
  -o ServerAliveInterval=30 \
  -o ServerAliveCountMax=3 \
  -o StrictHostKeyChecking=accept-new \
  -o ExitOnForwardFailure=yes \
  -i ${_SSH_KEY} \
  ${WATCHDOG_USER}@${_TUNNEL_HOST}
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
TUNNELEOF

    systemctl daemon-reload
    systemctl enable "${_TUNNEL_SERVICE}" --quiet 2>/dev/null || true
    systemctl restart "${_TUNNEL_SERVICE}" 2>/dev/null || true
    sleep 2

    if systemctl is-active --quiet "${_TUNNEL_SERVICE}" 2>/dev/null; then
      ok "SSH tunnel active: localhost:${_TUNNEL_PORT} → ${_TUNNEL_HOST}:${_TUNNEL_PORT}"
    else
      warn "SSH tunnel service may not have started — check: systemctl status ${_TUNNEL_SERVICE}"
    fi

    # Step 5: Verify tunnel connectivity (pass master key — Inference Server requires auth)
    if curl -sf -o /dev/null "http://localhost:${_TUNNEL_PORT}/v1/models" 2>/dev/null; then
      ok "Tunnel verified: localhost:${_TUNNEL_PORT} reachable"
    else
      warn "Could not reach localhost:${_TUNNEL_PORT} through tunnel — server may not be running"
    fi

    # Step 6: Update paths.env — use localhost (tunneled) URL, NOT the remote URL
    _TUNNEL_URL="http://localhost:${_TUNNEL_PORT}"
    if [ -f "${PATHS_ENV}" ] && [ "${PATHS_ENV}" != "/dev/null" ]; then
      for kv in \
        "VERSA_LOCAL_AI_ENABLED=\"true\"" \
        "VERSA_EXECUTION_MODE=\"hybrid\"" \
        "VERSA_GPU_BACKEND=\"remote\"" \
        "VERSA_INFERENCE_URL=\"${_TUNNEL_URL}\"" \
        "VERSA_LOCAL_MODELS=\"${_remote_models}\""; do
        KEY="${kv%%=*}"
        if grep -q "^${KEY}=" "${PATHS_ENV}"; then
          sed -i "s|^${KEY}=.*|${kv}|" "${PATHS_ENV}"
        else
          echo "${kv}" >> "${PATHS_ENV}"
        fi
      done
      ok "paths.env updated (INFERENCE_URL → ${_TUNNEL_URL} via SSH tunnel)"
    fi

    # Write client state file
    mkdir -p "$(dirname "${CLIENT_STATE_FILE}")"
    cat > "${CLIENT_STATE_FILE}" <<CLIENTEOF
{
  "topology": "client",
  "remote_url": "${REMOTE_INFERENCE_URL}",
  "tunnel_url": "${_TUNNEL_URL}",
  "tunnel_host": "${_TUNNEL_HOST}",
  "tunnel_port": "${_TUNNEL_PORT}",
  "models": $(echo "${_remote_models}" | jq -R 'split(",")'),
  "configured_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
}
CLIENTEOF
    chmod 640 "${CLIENT_STATE_FILE}"

    # Write to setup.ini (dual-write)
    for _ini_file in "${SCRIPT_DIR}/setup.ini" "/etc/versa-agi/setup.ini"; do
      if [ -f "${_ini_file}" ]; then
        sed -i '/^\[local_ai\]/,/^\[/{s|^topology=.*|topology=client|}' "${_ini_file}"
        sed -i '/^\[local_ai\]/,/^\[/{s|^remote_inference_url=.*|remote_inference_url='"${REMOTE_INFERENCE_URL}"'|}' "${_ini_file}"
        sed -i '/^\[local_ai\]/,/^\[/{s|^inference_master_key=.*|inference_master_key='"${INFERENCE_MASTER_KEY}"'|}' "${_ini_file}"
        sed -i '/^\[local_ai\]/,/^\[/{s/^enabled=.*/enabled=true/}' "${_ini_file}"
        sed -i '/^\[gemini\]/,/^\[/{s/^mode=.*/mode=hybrid/}' "${_ini_file}"
        ok "Updated: ${_ini_file}"
      fi
    done

    echo ""
    echo "  ╭──────────────────────────────────────────────────────╮"
    echo "  │  ✅ Remote AI Client — Configured                    │"
    echo "  ├──────────────────────────────────────────────────────┤"
    echo "  │                                                      │"
    echo "  │  Server:       ${_TUNNEL_HOST}:${_TUNNEL_PORT}"
    echo "  │  SSH Tunnel:   localhost:${_TUNNEL_PORT} → encrypted → server"
    echo "  │  Models:       ${_remote_models}"
    echo "  │  Master Key:   ✓ stored"
    echo "  │  Tunnel Svc:   ${_TUNNEL_SERVICE}.service"
    echo "  │                                                      │"
    echo "  ╰──────────────────────────────────────────────────────╯"
    echo ""
    exit 0
  fi
  # Option 1 selected: fall through to normal local install flow
  echo ""
fi

# ─── GPU Backend Selection ──────────────────────────
echo ""
echo "  GPU Backend:"
echo "    1) Standard Ollama — NVIDIA / AMD (Default)"
echo "    2) Intel ARC — Docker SYCL (Battlemage, Alchemist)"
echo ""

if [ "${GPU_BACKEND}" = "intel" ]; then
  GPU_CHOICE_DEFAULT=2
else
  GPU_CHOICE_DEFAULT=1
fi

read -p "  Selection [${GPU_CHOICE_DEFAULT}]: " GPU_CHOICE
GPU_CHOICE=${GPU_CHOICE:-${GPU_CHOICE_DEFAULT}}

case "${GPU_CHOICE}" in
  2)
    GPU_BACKEND="intel"
    ;;
  *)
    GPU_BACKEND="standard"
    ;;
esac

echo ""
ok "GPU Backend: ${GPU_BACKEND}"
echo ""

# ─── Previous Backend Cleanup ───────────────────────
# When re-running setup with a different backend, clean up the old one.
if [ "${GPU_BACKEND}" = "standard" ]; then
  # Switching to Standard: clean up Intel SYCL container and legacy IPEX
  if docker ps -a --format '{{.Names}}' 2>/dev/null | grep -q "^${SYCL_CONTAINER}$"; then
    info "Switching from Intel → Standard: cleaning up Docker SYCL container..."
    docker stop "${SYCL_CONTAINER}" 2>/dev/null || true
    docker rm "${SYCL_CONTAINER}" 2>/dev/null || true
    ok "Removed Docker container: ${SYCL_CONTAINER}"
  fi
  # Clean up legacy IPEX service if present
  if [ -f "${IPEX_SERVICE_FILE}" ]; then
    info "Cleaning up legacy IPEX service..."
    systemctl stop "${IPEX_SERVICE_NAME}" 2>/dev/null || true
    systemctl disable "${IPEX_SERVICE_NAME}" --quiet 2>/dev/null || true
    rm -f "${IPEX_SERVICE_FILE}"
    systemctl daemon-reload
    ok "Removed ${IPEX_SERVICE_NAME}.service"
  fi
  # Unmask stock Ollama so the standard flow can enable it
  systemctl unmask ollama --quiet 2>/dev/null || true
  # Remove legacy IPEX artifacts
  if [ -d "${MINIFORGE_DIR}" ]; then
    rm -rf "${MINIFORGE_DIR}"
    ok "Removed Miniforge: ${MINIFORGE_DIR}"
  fi
  if [ -d "${IPEX_OLLAMA_DIR_LEGACY}" ]; then
    rm -rf "${IPEX_OLLAMA_DIR_LEGACY}"
    ok "Removed legacy IPEX Ollama: ${IPEX_OLLAMA_DIR_LEGACY}"
  fi
  echo ""
elif [ "${GPU_BACKEND}" = "intel" ] && systemctl is-active --quiet ollama 2>/dev/null; then
  info "Switching from Standard → Intel: stock Ollama will be masked during Intel setup."
  echo ""
fi

# ─── Ollama Host Selection ──────────────────────────
echo "  Where is Ollama running?"
echo "    1) Install locally in this Linux machine (Default)"
echo "    2) Host machine (OrbStack) - http://host.orb.internal:11434"
echo "    3) Host machine (Lima)     - http://host.lima.internal:11434"
echo "    4) Custom URL"
echo ""
read -p "  Selection [1]: " OLLAMA_CHOICE
OLLAMA_CHOICE=${OLLAMA_CHOICE:-1}

INSTALL_OLLAMA="true"

case "${OLLAMA_CHOICE}" in
  2)
    OLLAMA_HOST="http://host.orb.internal:11434"
    INSTALL_OLLAMA="false"
    ;;
  3)
    OLLAMA_HOST="http://host.lima.internal:11434"
    INSTALL_OLLAMA="false"
    ;;
  4)
    read -p "  Enter Custom Ollama URL (e.g. http://192.168.1.100:11434): " OLLAMA_HOST
    INSTALL_OLLAMA="false"
    ;;
  *)
    OLLAMA_HOST="http://localhost:11434"
    ;;
esac

echo ""
ok "Using Ollama Host: ${OLLAMA_HOST}"
echo ""

# ═════════════════════════════════════════════════════
# GPU BACKEND: INTEL (Docker SYCL)
# ═════════════════════════════════════════════════════
if [ "${GPU_BACKEND}" = "intel" ] && [ "${INSTALL_OLLAMA}" = "true" ]; then

  # ── OS Check ──
  if command -v lsb_release &>/dev/null; then
    _os_ver="$(lsb_release -rs 2>/dev/null)"
    _os_id="$(lsb_release -is 2>/dev/null)"
    if [ "${_os_id}" != "Ubuntu" ] || [ "${_os_ver}" != "24.04" ]; then
      warn "Intel ARC Docker SYCL is tested on Ubuntu 24.04. Your system: ${_os_id} ${_os_ver}"
      warn "Continuing, but driver issues may occur."
    fi
  fi

  # ── DRI Check ──
  if ! ls /dev/dri/render* &>/dev/null; then
    warn "No /dev/dri/render* devices found. Intel GPU drivers may not be loaded."
    warn "Ensure your GPU drivers are installed before proceeding."
  else
    ok "DRI render devices detected: $(ls /dev/dri/render* 2>/dev/null | wc -l)"
  fi

  # ── Device ID ──
  if [ -z "${INTEL_DEVICE_ID}" ]; then
    echo ""
    echo "  Intel ARC GPU Configuration"
    echo "  ─────────────────────────────────────────"
    echo "  To find your GPU PCI device ID, run:"
    echo "    lspci -nn | grep -i 'VGA\|Display'"
    echo ""
    echo "  Example output:"
    echo "    03:00.0 VGA compatible controller [0300]: Intel ... [8086:e212]"
    echo "    The device ID is the last part: 8086:e212"
    echo ""
    read -p "  Enter PCI device ID (e.g. 8086:e212): " INTEL_DEVICE_ID
    if [ -z "${INTEL_DEVICE_ID}" ]; then
      error "Device ID is required for Intel ARC setup."
    fi
  fi
  ok "Device ID: ${INTEL_DEVICE_ID}"

  # ── Card Count ──
  echo ""
  echo "  How many identical Intel ARC cards are installed?"
  echo "  ⚠ Same card models are recommended. Mixed GPU configurations are untested."
  echo ""
  read -p "  Card count [${INTEL_CARD_COUNT}]: " _card_input
  INTEL_CARD_COUNT=${_card_input:-${INTEL_CARD_COUNT}}
  ok "Card count: ${INTEL_CARD_COUNT}"

  # ── Step 1: Install Docker ──
  echo ""
  info "Step 1: Docker Engine"
  if command -v docker &>/dev/null && systemctl is-active --quiet docker 2>/dev/null; then
    DOCKER_VER=$(docker --version 2>&1 | head -1)
    ok "Docker already installed: ${DOCKER_VER}"
  elif command -v docker &>/dev/null; then
    info "Docker installed but not running. Starting..."
    systemctl enable docker --quiet 2>/dev/null || true
    systemctl start docker 2>/dev/null || true
    sleep 2
    if systemctl is-active --quiet docker; then
      ok "Docker daemon started"
    else
      error "Docker daemon failed to start. Check: systemctl status docker"
    fi
  else
    info "Installing Docker Engine via official APT repository..."
    install -m 0755 -d /etc/apt/keyrings
    curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
    chmod a+r /etc/apt/keyrings/docker.asc
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] \
      https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | \
      tee /etc/apt/sources.list.d/docker.list > /dev/null
    apt-get update -qq
    apt-get install -y -qq docker-ce docker-ce-cli containerd.io docker-buildx-plugin
    systemctl enable docker --quiet 2>/dev/null || true
    systemctl start docker 2>/dev/null || true
    sleep 2
    if command -v docker &>/dev/null && systemctl is-active --quiet docker; then
      DOCKER_VER=$(docker --version 2>&1 | head -1)
      ok "Docker installed: ${DOCKER_VER}"
    else
      error "Docker installation failed"
    fi
  fi

  # ── Step 2: Install HuggingFace CLI ──
  info "Step 2: HuggingFace CLI"
  # Resolve hf command: 'hf' (v1.14+) or 'huggingface-cli' (legacy)
  HF_CMD=""
  if command -v hf &>/dev/null; then
    HF_CMD="hf"
  elif command -v huggingface-cli &>/dev/null; then
    HF_CMD="huggingface-cli"
  fi

  if [ -n "${HF_CMD}" ]; then
    ok "HuggingFace CLI already installed (${HF_CMD})"
  else
    # Ubuntu 24.04 (PEP 668): pipx is the recommended path for CLI tools
    if ! command -v pipx &>/dev/null; then
      apt-get install -y -qq pipx 2>/dev/null || error "Failed to install pipx. Run: sudo apt install pipx"
    fi
    pipx install 'huggingface-hub[cli]' || error "Failed to install huggingface-hub. Try: pipx install huggingface-hub[cli]"
    export PATH="/root/.local/bin:${PATH}"
    # Resolve command name (v1.14+ uses 'hf', older uses 'huggingface-cli')
    if command -v hf &>/dev/null; then
      HF_CMD="hf"
    elif command -v huggingface-cli &>/dev/null; then
      HF_CMD="huggingface-cli"
    else
      error "HuggingFace CLI not found after install. Check: pipx list"
    fi
    ok "HuggingFace CLI installed (${HF_CMD})"
  fi

  # ── Step 3: HuggingFace Token ──
  info "Step 3: HuggingFace Authentication"
  if [ -z "${HF_TOKEN}" ]; then
    echo ""
    echo "  ─────────────────────────────────────────"
    echo "  A HuggingFace token is needed for model downloads."
    echo "  Create one at: https://huggingface.co/settings/tokens"
    echo "    1. Click \"Create new token\""
    echo "    2. Name: versa-agi (or anything)"
    echo "    3. Type: Read"
    echo "    4. Copy the token (starts with hf_)"
    echo ""
    read -p "  Enter HF Token: " HF_TOKEN
    if [ -z "${HF_TOKEN}" ]; then
      error "HuggingFace token is required for Intel SYCL model downloads."
    fi
  fi
  ${HF_CMD} auth login --token "${HF_TOKEN}" 2>/dev/null || \
    ${HF_CMD} login --token "${HF_TOKEN}" --add-to-git-credential 2>/dev/null || true
  ok "HuggingFace authenticated"

  # ── Step 4: Build Docker SYCL Image ──
  info "Step 4: Docker SYCL Image"
  if docker image inspect "${SYCL_IMAGE}" &>/dev/null; then
    ok "Docker image '${SYCL_IMAGE}' already exists (use --rebuild-image to force)"
  else
    _LLAMA_BUILD_DIR="/tmp/llama-cpp-sycl-build-$$"
    info "Cloning llama.cpp (${SYCL_LLAMA_CPP_TAG})..."
    git clone --depth 1 --branch "${SYCL_LLAMA_CPP_TAG}" \
      https://github.com/ggml-org/llama.cpp "${_LLAMA_BUILD_DIR}" 2>/dev/null || \
      error "Failed to clone llama.cpp at tag ${SYCL_LLAMA_CPP_TAG}"

    # Use our pinned Dockerfile
    cp "${SCRIPT_DIR}/docker/intel-sycl.Dockerfile" "${_LLAMA_BUILD_DIR}/.devops/intel.Dockerfile"

    info "Building Docker image (this may take 5-10 minutes)..."
    docker build -t "${SYCL_IMAGE}" \
      --build-arg="GGML_SYCL_F16=ON" \
      --target server \
      -f "${_LLAMA_BUILD_DIR}/.devops/intel.Dockerfile" \
      "${_LLAMA_BUILD_DIR}" || error "Docker image build failed"

    rm -rf "${_LLAMA_BUILD_DIR}"
    ok "Docker image built: ${SYCL_IMAGE}"
  fi

  # ── Step 5: Disable stock Ollama service ──
  info "Step 5: Disable stock Ollama service (replaced by Docker SYCL)"
  if systemctl is-active --quiet ollama 2>/dev/null; then
    systemctl stop ollama 2>/dev/null || true
    ok "Stopped stock Ollama service"
  fi
  if systemctl is-enabled --quiet ollama 2>/dev/null; then
    systemctl disable ollama --quiet 2>/dev/null || true
    ok "Disabled stock Ollama service"
  fi
  systemctl mask ollama --quiet 2>/dev/null || true
  ok "Masked stock Ollama service (Docker SYCL replaces it)"
  # Also clean up legacy IPEX service if present
  if [ -f "${IPEX_SERVICE_FILE}" ]; then
    systemctl stop "${IPEX_SERVICE_NAME}" 2>/dev/null || true
    systemctl disable "${IPEX_SERVICE_NAME}" --quiet 2>/dev/null || true
    rm -f "${IPEX_SERVICE_FILE}"
    systemctl daemon-reload
    ok "Removed legacy ${IPEX_SERVICE_NAME}.service"
  fi

  # ── Step 5b: Ensure service user has GPU device access ──
  for grp in render video; do
    if getent group "${grp}" >/dev/null 2>&1; then
      if ! id -nG "${WATCHDOG_USER}" 2>/dev/null | grep -qw "${grp}"; then
        usermod -aG "${grp}" "${WATCHDOG_USER}"
        ok "${WATCHDOG_USER} added to '${grp}' group (GPU device access)"
      fi
    fi
  done

  # ── Step 6: Model Selection ──
  info "Step 6: Select Default Model"
  echo ""
  echo "  Select the model to run on your Intel ARC GPU:"
  echo ""
  echo "    1) gemma4:e4b    — 8B params, ~5 GB   (small, fast)"
  echo "    2) gemma4:26b    — 26B MoE, ~16 GB    (recommended)"
  echo "    3) gemma4:31b    — 31B dense, ~18 GB   (powerful)"
  echo "    4) qwen3.6:35b   — 35B MoE, ~21 GB    (multilingual)"
  echo ""
  echo "  All models use Q4_K_M quantization optimized for 16GB VRAM."
  echo "  Only one model is loaded at a time. Switch with:"
  echo "    sudo agictl model activate <name>"
  echo ""

  # Determine default selection number
  case "${DEFAULT_MODEL}" in
    gemma4:e4b)   _MODEL_DEFAULT=1 ;;
    gemma4:26b)   _MODEL_DEFAULT=2 ;;
    gemma4:31b)   _MODEL_DEFAULT=3 ;;
    qwen3.6:35b)  _MODEL_DEFAULT=4 ;;
    *)            _MODEL_DEFAULT=2 ;;
  esac

  read -p "  Selection [${_MODEL_DEFAULT}]: " _MODEL_CHOICE
  _MODEL_CHOICE=${_MODEL_CHOICE:-${_MODEL_DEFAULT}}

  case "${_MODEL_CHOICE}" in
    1) DEFAULT_MODEL="gemma4:e4b" ;;
    2) DEFAULT_MODEL="gemma4:26b" ;;
    3) DEFAULT_MODEL="gemma4:31b" ;;
    4) DEFAULT_MODEL="qwen3.6:35b" ;;
    *) DEFAULT_MODEL="gemma4:26b" ;;
  esac
  ok "Selected model: ${DEFAULT_MODEL}"

  SYCL_ACTIVE_MODEL="${DEFAULT_MODEL}"
  SYCL_ACTIVE_GGUF="${SYCL_MODEL_FILE[${DEFAULT_MODEL}]}"

  # ── Step 7: Download Model ──
  info "Step 7: Download Model"
  mkdir -p "${SYCL_MODEL_DIR}"

  _HF_REPO="${SYCL_MODEL_REPO[${DEFAULT_MODEL}]}"
  _HF_FILE="${SYCL_MODEL_FILE[${DEFAULT_MODEL}]}"

  if [ -f "${SYCL_MODEL_DIR}/${_HF_FILE}" ]; then
    ok "Model already downloaded: ${_HF_FILE}"
  else
    info "Downloading: ${_HF_FILE} (this may take several minutes)..."
    ${HF_CMD} download "${_HF_REPO}" \
      --include "${_HF_FILE}" \
      --local-dir "${SYCL_MODEL_DIR}" || \
      error "Failed to download model from ${_HF_REPO}"
    ok "Model downloaded to ${SYCL_MODEL_DIR}/"
  fi

  # ── Step 8: Start Docker SYCL Container ──
  info "Step 8: Start SYCL Server"

  # Stop existing container if running
  if docker ps -a --format '{{.Names}}' 2>/dev/null | grep -q "^${SYCL_CONTAINER}$"; then
    docker stop "${SYCL_CONTAINER}" 2>/dev/null || true
    docker rm "${SYCL_CONTAINER}" 2>/dev/null || true
  fi

  # Build device flags from DRI devices
  DOCKER_DEVICES=""
  for dev in /dev/dri/renderD* /dev/dri/card*; do
    if [ -e "${dev}" ]; then
      DOCKER_DEVICES="${DOCKER_DEVICES} --device ${dev}"
    fi
  done

  docker run -d --name "${SYCL_CONTAINER}" \
    --restart unless-stopped \
    ${DOCKER_DEVICES} \
    -v "${SYCL_MODEL_DIR}:/models" \
    -p "${SYCL_PORT}:8080" \
    "${SYCL_IMAGE}" \
    -m "/models/${SYCL_ACTIVE_GGUF}" \
    -ngl 99 --host 0.0.0.0 --port 8080

  sleep 5

  if docker ps --format '{{.Names}}' 2>/dev/null | grep -q "^${SYCL_CONTAINER}$"; then
    ok "Docker container '${SYCL_CONTAINER}' running (port ${SYCL_PORT})"
  else
    warn "Docker container may not have started — check: docker logs ${SYCL_CONTAINER}"
  fi

# ═════════════════════════════════════════════════════
# GPU BACKEND: STANDARD (NVIDIA / AMD)
# ═════════════════════════════════════════════════════
elif [ "${INSTALL_OLLAMA}" = "true" ]; then

  if command -v ollama &>/dev/null; then
    OLLAMA_VER=$(ollama --version 2>&1 | head -1)
    ok "Ollama already installed: ${OLLAMA_VER}"
  else
    info "Installing Ollama..."
    curl -fsSL https://ollama.com/install.sh | sh
    if command -v ollama &>/dev/null; then
      ok "Ollama installed successfully"
    else
      error "Ollama installation failed"
    fi
  fi

  # Ensure Ollama service is running
  systemctl enable ollama --quiet 2>/dev/null || true
  systemctl start ollama 2>/dev/null || true
  sleep 2
  if systemctl is-active --quiet ollama; then
    ok "Ollama service is running"
  else
    warn "Ollama service may not be running — check: systemctl status ollama"
  fi

else
  info "Skipping local Ollama installation (using remote host)"
fi

# ─── Step: Pull Default Model (Standard backend only) ──
# Intel backend handles model download in Step 7 above.
if [ "${AUTO_PULL}" = "true" ] && [ "${GPU_BACKEND}" = "standard" ]; then
  OLLAMA_CMD=""
  if command -v ollama &>/dev/null; then
    OLLAMA_CMD="ollama"
  fi

  if [ -n "${OLLAMA_CMD}" ]; then
    info "Pulling model: ${DEFAULT_MODEL} (this may take several minutes)..."
    export OLLAMA_HOST="${OLLAMA_HOST}"
    if ${OLLAMA_CMD} pull "${DEFAULT_MODEL}"; then
      ok "Model pulled: ${DEFAULT_MODEL}"
    else
      warn "Model pull failed for ${DEFAULT_MODEL} — you can pull manually: ollama pull ${DEFAULT_MODEL}"
    fi
  else
    warn "Ollama CLI not found. Please ensure the model '${DEFAULT_MODEL}' is pulled on your host machine."
  fi
elif [ "${GPU_BACKEND}" = "standard" ]; then
  info "Auto-pull disabled — ensure models are pulled on the target host."
fi

# ─── Inference Server Deprecated ──────────────────────────────
# The Python LangGraph Harness connects to local models natively.
# No proxy installation or service configuration is required.

# ═════════════════════════════════════════════════
# SERVER POSTAMBLE — firewall, master key, state file
# ═════════════════════════════════════════════════
if [ "${TOPOLOGY}" = "server" ]; then
  # Firewall: open inference port for LAN access
  _INF_PORT="11434"
  [ "${GPU_BACKEND}" = "intel" ] && _INF_PORT="${SYCL_PORT}"
  if command -v ufw &>/dev/null; then
    if ufw status 2>/dev/null | grep -q "active"; then
      ufw allow "${_INF_PORT}/tcp" comment "Versa AGi Inference API" >/dev/null 2>&1 || true
      ok "Firewall: port ${_INF_PORT}/tcp opened"
    else
      info "UFW is installed but not active — skipping firewall rule"
    fi
  else
    info "UFW not installed — ensure port ${_INF_PORT} is accessible on your network"
  fi

  # Detect LAN IP
  _LAN_IP=$(hostname -I 2>/dev/null | awk '{print $1}')
  _LAN_IP=${_LAN_IP:-"0.0.0.0"}

  # Determine active model for state file
  if [ "${GPU_BACKEND}" = "intel" ]; then
    _ACTIVE_MODEL="${SYCL_ACTIVE_MODEL}"
  else
    _ACTIVE_MODEL="${DEFAULT_MODEL}"
  fi

  # Write server state file
  mkdir -p "$(dirname "${SERVER_STATE_FILE}")"
  cat > "${SERVER_STATE_FILE}" <<SRVEOF
{
  "topology": "server",
  "gpu_backend": "${GPU_BACKEND}",
  "active_model": "${_ACTIVE_MODEL}",
  "lan_ip": "${_LAN_IP}",
  "configured_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
}
SRVEOF
  chmod 640 "${SERVER_STATE_FILE}"
  ok "Server state saved: ${SERVER_STATE_FILE}"

  # Write topology + master key to setup.ini (dual-write)
  for SETUP_INI in "${SCRIPT_DIR}/setup.ini" "/etc/versa-agi/setup.ini"; do
    if [ -f "${SETUP_INI}" ]; then
      sed -i '/^\[local_ai\]/,/^\[/{s/^enabled=.*/enabled=true/}' "${SETUP_INI}"
      sed -i '/^\[local_ai\]/,/^\[/{s/^gpu_backend=.*/gpu_backend='"${GPU_BACKEND}"'/}' "${SETUP_INI}"
      sed -i '/^\[local_ai\]/,/^\[/{s|^topology=.*|topology=server|}' "${SETUP_INI}"
      sed -i '/^\[local_ai\]/,/^\[/{s/^default_model=.*/default_model='"${DEFAULT_MODEL}"'/}' "${SETUP_INI}"
      if [ "${GPU_BACKEND}" = "intel" ]; then
        sed -i '/^\[local_ai\]/,/^\[/{s/^intel_card_count=.*/intel_card_count='"${INTEL_CARD_COUNT}"'/}' "${SETUP_INI}"
        sed -i '/^\[local_ai\]/,/^\[/{s/^intel_device_id=.*/intel_device_id='"${INTEL_DEVICE_ID}"'/}' "${SETUP_INI}"
        sed -i '/^\[local_ai\]/,/^\[/{s|^sycl_port=.*|sycl_port='"${SYCL_PORT}"'|}' "${SETUP_INI}"
        sed -i '/^\[local_ai\]/,/^\[/{s/^sycl_active_model=.*/sycl_active_model='"${SYCL_ACTIVE_MODEL}"'/}' "${SETUP_INI}"
        sed -i '/^\[local_ai\]/,/^\[/{s/^hf_token=.*/hf_token='"${HF_TOKEN}"'/}' "${SETUP_INI}"
        sed -i '/^\[local_ai\]/,/^\[/{s/^sycl_llama_cpp_tag=.*/sycl_llama_cpp_tag='"${SYCL_LLAMA_CPP_TAG}"'/}' "${SETUP_INI}"
      fi
      # Server mode: disable cloud proxy — inference server only serves local models
      sed -i '/^\[cloud_models\]/,/^\[/{s/^enabled=.*/enabled=false/}' "${SETUP_INI}"
      ok "Updated: ${SETUP_INI}"
    fi
  done

  # Print server-ready banner
  echo ""
  echo "  ╭──────────────────────────────────────────────────────╮"
  echo "  │  ✅ Inference Server Ready                          │"
  echo "  ├──────────────────────────────────────────────────────┤"
  echo "  │                                                      │"
  if [ "${GPU_BACKEND}" = "intel" ]; then
    _INF_PORT="${SYCL_PORT}"
  else
    _INF_PORT="11434"
  fi
  echo "  │  GPU Backend:   ${GPU_BACKEND}"
  echo "  │  Active Model:  ${_ACTIVE_MODEL}"
  echo "  │  Inference Port:  ${_INF_PORT}"
  echo "  │  LAN URL:       http://${_LAN_IP}:${_INF_PORT}"
  echo "  │  Master Key:    ${INFERENCE_MASTER_KEY}"
  echo "  │                                                      │"
  echo "  │  On the client machine, run setup.sh and select:     │"
  echo "  │    Option 2 (Client — Cloud + Local AI)              │"
  echo "  │    Then: Remote server → paste URL + key above       │"
  echo "  │                                                      │"
  echo "  ╰──────────────────────────────────────────────────────╯"
  echo ""
  exit 0
fi

# ─── Step: Update paths.env ────────────────────────
if [ -f "${PATHS_ENV}" ]; then
  # For Intel SYCL: only the active model is available at runtime (single-model constraint)
  if [ "${GPU_BACKEND}" = "intel" ]; then
    RUNTIME_MODELS="${SYCL_ACTIVE_MODEL}"
  else
    RUNTIME_MODELS="${LOCAL_MODELS}"
  fi
  for kv in \
    "VERSA_LOCAL_AI_ENABLED=\"true\"" \
    "VERSA_EXECUTION_MODE=\"hybrid\"" \
    "VERSA_GPU_BACKEND=\"${GPU_BACKEND}\"" \
    "VERSA_LOCAL_MODELS=\"${RUNTIME_MODELS}\""; do
    KEY="${kv%%=*}"
    if grep -q "^${KEY}=" "${PATHS_ENV}"; then
      sed -i "s|^${KEY}=.*|${kv}|" "${PATHS_ENV}"
    else
      echo "${kv}" >> "${PATHS_ENV}"
    fi
  done
  ok "paths.env updated (LOCAL_AI_ENABLED=true, mode=hybrid, backend=${GPU_BACKEND})"
fi

# ─── Step: Update setup.ini ────────────────────────
# Update both source (master) and deployed (runtime) copies.
_INI_FILES=()
# Source copy first (master)
if [ -f "${SCRIPT_DIR}/setup.ini" ]; then
  _INI_FILES+=("${SCRIPT_DIR}/setup.ini")
fi
# Deployed copy (runtime sync)
if [ -f "/etc/versa-agi/setup.ini" ] && [ "/etc/versa-agi/setup.ini" != "${SCRIPT_DIR}/setup.ini" ]; then
  _INI_FILES+=("/etc/versa-agi/setup.ini")
fi

for SETUP_INI in "${_INI_FILES[@]}"; do
  sed -i '/^\[local_ai\]/,/^\[/{s/^enabled=.*/enabled=true/}' "${SETUP_INI}"
  sed -i '/^\[local_ai\]/,/^\[/{s/^gpu_backend=.*/gpu_backend='"${GPU_BACKEND}"'/}' "${SETUP_INI}"
  sed -i '/^\[local_ai\]/,/^\[/{s|^topology=.*|topology=local|}' "${SETUP_INI}"
  sed -i '/^\[gemini\]/,/^\[/{s/^mode=.*/mode=hybrid/}' "${SETUP_INI}"
  sed -i '/^\[local_ai\]/,/^\[/{s/^default_model=.*/default_model='"${DEFAULT_MODEL}"'/}' "${SETUP_INI}"
  if [ "${GPU_BACKEND}" = "intel" ]; then
    sed -i '/^\[local_ai\]/,/^\[/{s/^intel_card_count=.*/intel_card_count='"${INTEL_CARD_COUNT}"'/}' "${SETUP_INI}"
    sed -i '/^\[local_ai\]/,/^\[/{s/^intel_device_id=.*/intel_device_id='"${INTEL_DEVICE_ID}"'/}' "${SETUP_INI}"
    sed -i '/^\[local_ai\]/,/^\[/{s|^sycl_port=.*|sycl_port='"${SYCL_PORT}"'|}' "${SETUP_INI}"
    sed -i '/^\[local_ai\]/,/^\[/{s/^sycl_active_model=.*/sycl_active_model='"${SYCL_ACTIVE_MODEL}"'/}' "${SETUP_INI}"
    sed -i '/^\[local_ai\]/,/^\[/{s/^hf_token=.*/hf_token='"${HF_TOKEN}"'/}' "${SETUP_INI}"
    sed -i '/^\[local_ai\]/,/^\[/{s/^sycl_llama_cpp_tag=.*/sycl_llama_cpp_tag='"${SYCL_LLAMA_CPP_TAG}"'/}' "${SETUP_INI}"
  fi
  ok "Updated: ${SETUP_INI}"
done

# ─── Done ───────────────────────────────────────────
echo ""
echo "  ✅ Local AI backend ready! (Experimental)"
echo ""
if [ "${GPU_BACKEND}" = "intel" ]; then
  echo "  Backend: Intel ARC (Docker SYCL)"
  echo "  Container: ${SYCL_CONTAINER}"
  echo "  Active model: ${SYCL_ACTIVE_MODEL}"
  echo "  Server: http://localhost:${SYCL_PORT}"
  echo ""
  echo "  ┌─ Model Management ────────────────────────────┐"
  echo "  │                                                │"
  echo "  │  Add a model:                                  │"
  echo "  │    sudo agictl model add gemma4:31b            │"
  echo "  │                                                │"
  echo "  │  Switch active model:                          │"
  echo "  │    sudo agictl model activate qwen3.6:35b      │"
  echo "  │                                                │"
  echo "  │  List models:                                  │"
  echo "  │    agictl model list                           │"
  echo "  │                                                │"
  echo "  │  Test inference:                               │"
  echo "  │    agictl model run gemma4:26b \"Hello!\"        │"
  echo "  └────────────────────────────────────────────────┘"
else
  echo "  Backend: Standard Ollama (NVIDIA/AMD)"
  echo ""
  echo "  ┌─ Next Steps ────────────────────────────────────┐"
  echo "  │                                                  │"
  echo "  │  Pull additional models:                         │"
  echo "  │    sudo agictl model add gemma4:26b              │"
  echo "  │                                                  │"
  echo "  │  List registered models:                         │"
  echo "  │    agictl model list                             │"
  echo "  │                                                  │"
  echo "  │  Remove a model:                                 │"
  echo "  │    sudo agictl model remove gemma4:26b           │"
  echo "  │                                                  │"
  echo "  │  Models appear in: agitop → Edit Agent           │"
  echo "  │                    → Model selector (🖥 icon)     │"
  echo "  └──────────────────────────────────────────────────┘"
fi
echo ""
echo "  To remove: sudo $(dirname "${BASH_SOURCE[0]}")/uninstall_local.sh"
echo ""


