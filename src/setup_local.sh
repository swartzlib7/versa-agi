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

# ─── Brand accent helpers ───────────────────────────
_local_ai_accent() {
  : "${BCYAN:=}"
  : "${RESET:=}"
  printf '%b%s%b' "${BCYAN}" "$1" "${RESET}"
}

_local_ai_dim() {
  : "${DIM:=}"
  : "${RESET:=}"
  printf '%b%s%b' "${DIM}" "$1" "${RESET}"
}

_local_ai_product() {
  _local_ai_accent "Versa AGi"
}

_local_ai_show_banner() {
  local hdr_line intro_line cloud_line
  local std_line nv_line amd_line intel_hdr intel_a intel_docker intel_ubuntu warn_line resp_line

  hdr_line="$(_local_ai_product) — Local AI Setup ($(_local_ai_dim "Experimental"))"
  intro_line="Install local inference so sub-agents run on your hardware"
  cloud_line="instead of cloud-only execution."
  std_line="1. $(_local_ai_accent "Standard Ollama")"
  nv_line="   • $(_local_ai_accent "NVIDIA") ($(_local_ai_dim "CUDA"))"
  amd_line="   • $(_local_ai_accent "AMD") ($(_local_ai_dim "ROCm"))"
  intel_hdr="2. $(_local_ai_accent "Intel ARC") — $(_local_ai_dim "Docker SYCL")"
  intel_a="   • ARC A-series, B-series (Battlemage, Alchemist)"
  intel_docker="   • Requires Docker (auto-install)"
  intel_ubuntu="   • Requires Ubuntu 24.04"
  : "${YELLOW:=}"
  warn_line="${YELLOW}${GLYPH_WARN:-!}${RESET} Configure hardware & drivers BEFORE"
  resp_line="The user is responsible for ensuring hardware meets model requirements."

  if declare -F text_box >/dev/null 2>&1; then
    text_box "LOCAL AI SETUP" \
      "${hdr_line}" \
      "" \
      "${intro_line}" \
      "${cloud_line}" \
      "" \
      "Supported GPU backends:" \
      "${std_line}" \
      "${nv_line}" \
      "${amd_line}" \
      "" \
      "${intel_hdr}" \
      "${intel_a}" \
      "${intel_docker}" \
      "${intel_ubuntu}" \
      "" \
      "${warn_line}" \
      "running this installer." \
      "" \
      "${resp_line}"
  else
    echo ""
    echo -e "${hdr_line}"
    echo ""
    echo -e "${intro_line} ${cloud_line}"
    echo ""
    echo "  Supported GPU backends:"
    echo -e "  ${std_line}"
    echo -e "  ${nv_line}"
    echo -e "  ${amd_line}"
    echo ""
    echo -e "  ${intel_hdr}"
    echo -e "  ${intel_a}"
    echo -e "  ${intel_docker}"
    echo -e "  ${intel_ubuntu}"
    echo ""
    echo -e "  ${warn_line} running this installer."
    echo ""
    echo -e "  ${resp_line}"
    echo ""
  fi
}


# ─── Root Check ─────────────────────────────────────
if [ "$(id -u)" -ne 0 ]; then
  error "This script must be run as root (sudo ./setup_local.sh)"
fi

# ─── WSL Detection ──────────────────────────────────
# Returns 0 (true) if running inside WSL (any version).
_is_wsl() {
  grep -qiE 'microsoft|wsl' /proc/version 2>/dev/null
}

# ─── Systemd Pre-flight for WSL ─────────────────────
# Ollama's own install.sh calls systemctl without guards. On WSL without
# systemd enabled this exits 2 and aborts the whole script under set -e.
# We detect this early and offer to auto-configure, rather than letting
# the user hit a cryptic exit 2 mid-install.
_check_systemd_wsl() {
  _is_wsl || return 0  # not WSL — nothing to do

  # Check if systemd is actually PID 1 (i.e. properly enabled in WSL)
  if [ "$(cat /proc/1/comm 2>/dev/null)" = "systemd" ]; then
    ok "WSL systemd: active"
    return 0
  fi

  # systemd not running — show clear guidance
  echo ""
  if declare -F warn &>/dev/null; then
    warn "WSL detected — systemd is NOT enabled"
  else
    echo "  ! WSL detected — systemd is NOT enabled"
  fi
  echo ""
  echo "  Ollama (and the Versa AGi inference service) requires systemd to"
  echo "  run as a background service. Without it, the install will fail."
  echo ""
  echo "  To enable systemd in WSL, add this to /etc/wsl.conf:"
  echo ""
  echo "    [boot]"
  echo "    systemd=true"
  echo ""
  echo "  Then restart WSL from PowerShell:  wsl --shutdown"
  echo ""

  read -p "  Auto-configure /etc/wsl.conf now and exit for WSL restart? [Y/n]: " _wsl_ans
  _wsl_ans="${_wsl_ans:-Y}"

  if [[ "${_wsl_ans}" =~ ^[Yy]$ ]]; then
    # Idempotent write — only add the block if not already present
    if grep -q '^\[boot\]' /etc/wsl.conf 2>/dev/null; then
      if grep -q '^systemd=' /etc/wsl.conf 2>/dev/null; then
        sed -i 's/^systemd=.*/systemd=true/' /etc/wsl.conf
      else
        sed -i '/^\[boot\]/a systemd=true' /etc/wsl.conf
      fi
    else
      printf '\n[boot]\nsystemd=true\n' >> /etc/wsl.conf
    fi
    ok "/etc/wsl.conf updated — systemd=true"
    echo ""
    echo "  ► Next steps:"
    echo "    1. Exit WSL"
    echo "    2. In PowerShell run:  wsl --shutdown"
    echo "    3. Reopen WSL and re-run this installer"
    echo ""
    exit 0
  else
    echo ""
    if declare -F error &>/dev/null; then
      error "Systemd is required. Enable it in /etc/wsl.conf and re-run after restarting WSL."
    else
      echo "  ✗ Systemd is required. Enable it in /etc/wsl.conf and re-run after restarting WSL."
      exit 1
    fi
  fi
}

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
SYCL_IMAGE="versa-agi-sycl"
SYCL_LLAMA_CPP_TAG="${SYCL_LLAMA_CPP_TAG:-b9082}"
HF_TOKEN="${HF_TOKEN:-}"
SYCL_PARALLEL="${SYCL_PARALLEL:-1}"
SYCL_CTX_SIZE="${SYCL_CTX_SIZE:-4096}"
SYCL_VRAM_GB="${SYCL_VRAM_GB:-}"
SYCL_MODELS_MAX="${SYCL_MODELS_MAX:-1}"
MODEL_LOADING_STRATEGY="${MODEL_LOADING_STRATEGY:-router}"

# ─── Legacy Cleanup Paths ──────────────────────────
MINIFORGE_DIR="/opt/versa-agi/miniforge3"
IPEX_OLLAMA_DIR_LEGACY="/opt/versa-agi/ipex-ollama"
IPEX_SERVICE_NAME="versa-agi-ollama-ipex"
IPEX_SERVICE_FILE="/etc/systemd/system/${IPEX_SERVICE_NAME}.service"

# ─── Intel SYCL Model Registry ──────────────────────
# Loaded dynamically from models.ini via manage_registry.sh
# Populates: _REG_NAMES[], _REG_REPOS[], _REG_FILES[], _REG_SIZES[], _REG_COUNT
_MANAGE_REGISTRY_SCRIPT="${SCRIPT_DIR}/manage_registry.sh"

# Load registry arrays (silently — no interactive menu here)
_load_sycl_registry_arrays() {
  if [ -f "${_MANAGE_REGISTRY_SCRIPT}" ]; then
    source "${_MANAGE_REGISTRY_SCRIPT}" --list >/dev/null 2>&1
    # After sourcing, _REG_* arrays are populated
  else
    warn "manage_registry.sh not found at ${_MANAGE_REGISTRY_SCRIPT}"
    _REG_NAMES=()
    _REG_REPOS=()
    _REG_FILES=()
    _REG_SIZES=()
    _REG_COUNT=0
  fi
}

# Helper: look up a registry value by model name (set -e safe)
_reg_repo_for() { local n="$1"; local i; for i in $(seq 0 $((_REG_COUNT - 1))); do if [ "${_REG_NAMES[$i]}" = "$n" ]; then echo "${_REG_REPOS[$i]}"; return; fi; done; }
_reg_file_for() { local n="$1"; local i; for i in $(seq 0 $((_REG_COUNT - 1))); do if [ "${_REG_NAMES[$i]}" = "$n" ]; then echo "${_REG_FILES[$i]}"; return; fi; done; }
_reg_size_for() { local n="$1"; local i; for i in $(seq 0 $((_REG_COUNT - 1))); do if [ "${_REG_NAMES[$i]}" = "$n" ]; then echo "${_REG_SIZES[$i]}"; return; fi; done; echo "10"; }
# Helper: reverse lookup — GGUF filename → friendly model key.
# Used by client mode to translate /v1/models GGUF names back to
# the canonical keys used by models.ini, agitop, and lifeline.
_reg_name_for_file() { local f="$1"; local i; for i in $(seq 0 $((_REG_COUNT - 1))); do if [ "${_REG_FILES[$i]}" = "$f" ]; then echo "${_REG_NAMES[$i]}"; return; fi; done; }

# ─── Concurrency Calculator ────────────────────────
# Calculates recommended parallel slots based on available VRAM.
# Sets: _CALC_RECOMMENDED, _CALC_MAX, _CALC_FREE_VRAM_GB
_calculate_concurrency() {
  local vram_gb="${1:-0}"
  local model_size_gb="${2:-0}"
  local ctx_size="${3:-4096}"

  # KV cache per slot estimate: ~256MB at ctx_size 4096 for typical Q4_K_M models
  # Scale linearly with context size
  local kv_per_slot_mb=$(( 256 * ctx_size / 4096 ))
  [ "${kv_per_slot_mb}" -lt 128 ] && kv_per_slot_mb=128

  _CALC_FREE_VRAM_GB=$(( vram_gb - model_size_gb ))
  [ "${_CALC_FREE_VRAM_GB}" -lt 0 ] && _CALC_FREE_VRAM_GB=0

  local free_mb=$(( _CALC_FREE_VRAM_GB * 1024 ))
  local headroom_mb=2048  # 2GB headroom for runtime overhead

  if [ "${free_mb}" -le "${headroom_mb}" ]; then
    _CALC_MAX=1
    _CALC_RECOMMENDED=1
    return
  fi

  _CALC_MAX=$(( (free_mb - headroom_mb) / kv_per_slot_mb ))
  [ "${_CALC_MAX}" -lt 1 ] && _CALC_MAX=1
  [ "${_CALC_MAX}" -gt 8 ] && _CALC_MAX=8

  # Conservative recommendation: half of max, minimum 1, maximum 4
  _CALC_RECOMMENDED=$(( _CALC_MAX / 2 ))
  [ "${_CALC_RECOMMENDED}" -lt 1 ] && _CALC_RECOMMENDED=1
  [ "${_CALC_RECOMMENDED}" -gt 4 ] && _CALC_RECOMMENDED=4
  return 0
}

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
  [ "${SYCL_PARALLEL}" = "1" ] && { _v="$(_ini_val local_ai sycl_parallel)"; [ -n "$_v" ] && SYCL_PARALLEL="$_v"; }
  [ "${SYCL_CTX_SIZE}" = "4096" ] && { _v="$(_ini_val local_ai sycl_ctx_size)"; [ -n "$_v" ] && SYCL_CTX_SIZE="$_v"; }
  [ -z "${SYCL_VRAM_GB}" ] && SYCL_VRAM_GB="$(_ini_val local_ai sycl_vram_gb)"
  [ "${SYCL_MODELS_MAX}" = "1" ] && { _v="$(_ini_val local_ai sycl_models_max)"; [ -n "$_v" ] && SYCL_MODELS_MAX="$_v"; }
  { _v="$(_ini_val local_ai model_loading_strategy)"; [ -n "$_v" ] && MODEL_LOADING_STRATEGY="$_v"; }
fi

# ─── Banner ─────────────────────────────────────────
_local_ai_show_banner

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
    # Reset GPU config so detection prompts always run on reconfigure
    INTEL_DEVICE_ID=""
    INTEL_CARD_COUNT="1"
    SYCL_PARALLEL=""
    SYCL_CTX_SIZE=""
    SYCL_VRAM_GB=""
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
  _WD_SHELL=$(getent passwd "${WATCHDOG_USER:-watchdog}" 2>/dev/null | cut -d: -f7 || true)
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

    # Prompt for master key (auto-inject 'versa-sk' for Intel SYCL on port 8080)
    if [[ "${REMOTE_INFERENCE_URL}" == *":8080"* ]]; then
      if [ -z "${INFERENCE_MASTER_KEY}" ]; then
        INFERENCE_MASTER_KEY="versa-sk"
        info "Intel SYCL detected (port 8080) — auto-injecting default master key: ${INFERENCE_MASTER_KEY}"
      else
        info "Intel SYCL detected (port 8080) — keeping existing master key: ${INFERENCE_MASTER_KEY}"
      fi
    else
      read -p "  Enter Inference Master Key: " _master_key
      INFERENCE_MASTER_KEY="${_master_key:-${INFERENCE_MASTER_KEY}}"
    fi

    # Health check
    echo ""
    info "Testing connection to ${REMOTE_INFERENCE_URL}..."
    if curl -sf -o /dev/null -H "Authorization: Bearer ${INFERENCE_MASTER_KEY}" "${REMOTE_INFERENCE_URL}/v1/models" 2>/dev/null; then
      ok "Remote server reachable (health: ok)"
    else
      warn "Remote server may not be reachable at ${REMOTE_INFERENCE_URL}"
      warn "Continuing anyway — check server is running and firewall allows connections."
    fi

    # Query /v1/models — the server returns GGUF filenames (e.g. Qwen3.6-35B-A3B-UD-Q4_K_M.gguf)
    # but the rest of the system uses friendly keys (e.g. qwen3.6:35b).
    # We translate via the [sycl_models] registry in models.ini.
    _remote_models=""
    _models_json=$(curl -sf -H "Authorization: Bearer ${INFERENCE_MASTER_KEY}" "${REMOTE_INFERENCE_URL}/v1/models" 2>/dev/null || echo "")
    if [ -n "${_models_json}" ]; then
      _raw_models=$(echo "${_models_json}" | jq -r '.data[].id' 2>/dev/null | paste -sd ',')
      if [ -n "${_raw_models}" ]; then
        # Load SYCL registry for GGUF → friendly key translation
        _load_sycl_registry_arrays
        _translated_list=""
        IFS=',' read -ra _raw_arr <<< "${_raw_models}"
        for _gguf_name in "${_raw_arr[@]}"; do
          _friendly=$(_reg_name_for_file "${_gguf_name}")
          if [ -n "${_friendly}" ]; then
            _translated_list="${_translated_list:+${_translated_list},}${_friendly}"
          else
            # No registry match — keep the raw name as fallback
            warn "Model '${_gguf_name}' not found in sycl_models registry — using raw name"
            _translated_list="${_translated_list:+${_translated_list},}${_gguf_name}"
          fi
        done
        _remote_models="${_translated_list}"
      fi
    fi
    if [ -n "${_remote_models}" ]; then
      ok "Available models: ${_remote_models}"
    else
      warn "Could not query models from server. VERSA_LOCAL_MODELS will be empty."
      _remote_models=""
    fi

    # server_config.json sync moved to after SSH tunnel setup (Step 5b)


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

    systemctl daemon-reload 2>/dev/null || true
    systemctl enable "${_TUNNEL_SERVICE}" --quiet 2>/dev/null || true
    systemctl restart "${_TUNNEL_SERVICE}" 2>/dev/null || true
    sleep 2

    if systemctl is-active --quiet "${_TUNNEL_SERVICE}" 2>/dev/null; then
      ok "SSH tunnel active: localhost:${_TUNNEL_PORT} → ${_TUNNEL_HOST}:${_TUNNEL_PORT}"
    else
      warn "SSH tunnel service may not have started — check: systemctl status ${_TUNNEL_SERVICE}"
    fi

    # Step 5: Verify tunnel connectivity (pass master key — Inference Server requires auth)
    if curl -sf -o /dev/null -H "Authorization: Bearer ${INFERENCE_MASTER_KEY}" "http://localhost:${_TUNNEL_PORT}/v1/models" 2>/dev/null; then
      ok "Tunnel verified: localhost:${_TUNNEL_PORT} reachable"
    else
      warn "Could not reach localhost:${_TUNNEL_PORT} through tunnel — server may not be running"
    fi

    # Step 5b: Sync server_config.json via SSH
    # The inference server doesn't serve this file — we read it directly
    # from the server filesystem over the existing SSH connection.
    info "Syncing server inference configuration..."
    _SRV_CFG_REMOTE="/etc/versa-agi/server_config.json"
    _srv_cfg=""
    if _srv_cfg=$(sudo -u "${WATCHDOG_USER}" ssh -i "${_SSH_KEY}" \
        -o StrictHostKeyChecking=accept-new \
        -o ConnectTimeout=5 \
        -o BatchMode=yes \
        "${WATCHDOG_USER}@${_TUNNEL_HOST}" "cat ${_SRV_CFG_REMOTE}" 2>/dev/null); then
      _srv_ctx=$(echo "${_srv_cfg}" | jq -r '.sycl_ctx_size // empty' 2>/dev/null)
      _srv_par=$(echo "${_srv_cfg}" | jq -r '.sycl_parallel // empty' 2>/dev/null)
      _srv_vram=$(echo "${_srv_cfg}" | jq -r '.sycl_vram_gb // empty' 2>/dev/null)

      if [ -n "${_srv_ctx}" ]; then
        ok "Server config synced: ctx=${_srv_ctx}, parallel=${_srv_par:-?}, vram=${_srv_vram:-?}GB"
        # Write to local setup.ini
        for _ini_file in "${SCRIPT_DIR}/setup.ini" "/etc/versa-agi/setup.ini"; do
          if [ -f "${_ini_file}" ]; then
            [ -n "${_srv_ctx}" ] && sed -i '/^\[local_ai\]/,/^\[/{s/^sycl_ctx_size=.*/sycl_ctx_size='"${_srv_ctx}"'/}' "${_ini_file}"
            [ -n "${_srv_par}" ] && sed -i '/^\[local_ai\]/,/^\[/{s/^sycl_parallel=.*/sycl_parallel='"${_srv_par}"'/}' "${_ini_file}"
            [ -n "${_srv_vram}" ] && sed -i '/^\[local_ai\]/,/^\[/{s/^sycl_vram_gb=.*/sycl_vram_gb='"${_srv_vram}"'/}' "${_ini_file}"
          fi
        done
      else
        info "Server config found but missing inference parameters"
      fi
    else
      info "Server config not available (optional — run 'sudo agictl model refresh' later)"
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
    chown "${WATCHDOG_USER}:${WATCHDOG_USER}" "${CLIENT_STATE_FILE}" 2>/dev/null || true

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

# ─── WSL systemd Pre-flight ─────────────────────────
# Must run before GPU backend install — Ollama's own installer calls
# systemctl without guards and will abort under set -euo pipefail on
# WSL instances where systemd is not enabled.
_check_systemd_wsl

# ─── GPU Backend Selection ──────────────────────────
if declare -F section >/dev/null 2>&1; then
  section "GPU Backend"
else
  echo ""
  echo "  GPU Backend:"
fi
echo -e "    1) $(_local_ai_accent "Standard Ollama") — $(_local_ai_accent "NVIDIA") / $(_local_ai_accent "AMD") ($(_local_ai_dim "Default"))"
echo -e "    2) $(_local_ai_accent "Intel ARC") — $(_local_ai_dim "Docker SYCL") (Battlemage, Alchemist)"
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
case "${GPU_BACKEND}" in
  intel)
    ok "GPU Backend: $(_local_ai_accent "Intel ARC") ($(_local_ai_dim "Docker SYCL"))"
    ;;
  *)
    ok "GPU Backend: $(_local_ai_accent "NVIDIA") / $(_local_ai_accent "AMD") ($(_local_ai_dim "Standard Ollama"))"
    ;;
esac
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
    systemctl daemon-reload 2>/dev/null || true
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

# ─── Ollama Host Selection (Standard backend only) ────
if [ "${GPU_BACKEND}" = "intel" ]; then
  # Intel SYCL uses Docker llama-server, not Ollama — skip Ollama configuration entirely
  INSTALL_OLLAMA="false"
  OLLAMA_HOST=""
  info "Intel SYCL backend selected — Ollama not used (Docker SYCL replaces it)"
  echo ""
else
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
fi

# ═════════════════════════════════════════════════════
# GPU BACKEND: INTEL (Docker SYCL)
# ═════════════════════════════════════════════════════
if [ "${GPU_BACKEND}" = "intel" ]; then

  # ── OS Check ──
  if command -v lsb_release &>/dev/null; then
    _os_ver="$(lsb_release -rs 2>/dev/null)"
    _os_id="$(lsb_release -is 2>/dev/null)"
    if [ "${_os_id}" != "Ubuntu" ] || [ "${_os_ver}" != "24.04" ]; then
      warn "Intel ARC Docker SYCL is tested on Ubuntu 24.04. Your system: ${_os_id} ${_os_ver}"
      warn "Continuing, but driver issues may occur."
    fi
  fi

  # ── DRI / DXG Check ──
  if _is_wsl; then
    # WSL2 exposes GPU via /dev/dxg (DirectX Graphics), not /dev/dri
    if [ -e /dev/dxg ]; then
      ok "WSL2 GPU device: /dev/dxg (DirectX bridge)"
    else
      warn "/dev/dxg not found — Windows GPU driver may not be loaded."
      warn "Ensure Intel ARC drivers are installed on the Windows host."
    fi
  else
    if ! ls /dev/dri/render* &>/dev/null; then
      warn "No /dev/dri/render* devices found. Intel GPU drivers may not be loaded."
      warn "Ensure your GPU drivers are installed before proceeding."
    else
      ok "DRI render devices detected: $(ls /dev/dri/render* 2>/dev/null | wc -l)"
    fi
  fi

  # ── GPU Auto-Detection ──
  echo ""
  echo "  Intel ARC GPU Configuration"
  echo "  ─────────────────────────────────────────"

  if _is_wsl; then
    # ── WSL2 path: no lspci, GPU is virtualised via /dev/dxg ──
    echo ""
    info "WSL2 detected — PCI enumeration unavailable (GPU accessed via /dev/dxg)"
    INTEL_DEVICE_ID="wsl-dxg"
    ok "Device ID: ${INTEL_DEVICE_ID} (WSL2 — /dev/dxg bridge)"

    # Card count: WSL2 virtualises all physical GPUs behind a single /dev/dxg
    INTEL_CARD_COUNT=1
    ok "Card count: ${INTEL_CARD_COUNT} (WSL2 virtual GPU device)"

  else
    # ── Bare-metal path: full lspci detection ──
    # Auto-detect Intel GPUs via lspci
    _GPU_LIST=()
    _GPU_IDS=()
    while IFS= read -r line; do
      [ -z "${line}" ] && continue
      # Extract device ID [8086:xxxx] and description
      _dev_id=$(echo "${line}" | grep -oP '\[8086:[0-9a-f]+\]' | tr -d '[]')
      _desc=$(echo "${line}" | sed 's/ \[.*$//' | sed 's/^[0-9:.]\+ //')
      if [ -n "${_dev_id}" ] && [ -n "${_desc}" ]; then
        _GPU_LIST+=("${_desc} [${_dev_id}]")
        _GPU_IDS+=("${_dev_id}")
      fi
    done < <(lspci -nn -d 8086::0300 2>/dev/null; lspci -nn -d 8086::0380 2>/dev/null)

    if [ "${#_GPU_LIST[@]}" -gt 0 ]; then
      echo ""
      echo "  Detected Intel GPUs:"
      for i in "${!_GPU_LIST[@]}"; do
        echo "    $((i + 1))) ${_GPU_LIST[$i]}"
      done
      echo "    $((${#_GPU_LIST[@]} + 1))) Enter manually"
      echo ""

      _DEFAULT_SEL=1
      # If INTEL_DEVICE_ID is pre-set, try to find it in the list
      if [ -n "${INTEL_DEVICE_ID}" ]; then
        for i in "${!_GPU_IDS[@]}"; do
          if [ "${_GPU_IDS[$i]}" = "${INTEL_DEVICE_ID}" ]; then
            _DEFAULT_SEL=$((i + 1))
            break
          fi
        done
      fi

      read -p "  Select GPU [${_DEFAULT_SEL}]: " _gpu_choice
      _gpu_choice=${_gpu_choice:-${_DEFAULT_SEL}}

      if [ "${_gpu_choice}" -le "${#_GPU_LIST[@]}" ] 2>/dev/null; then
        INTEL_DEVICE_ID="${_GPU_IDS[$((_gpu_choice - 1))]}"
      else
        # Manual entry
        read -p "  Enter PCI device ID (e.g. 8086:e223): " INTEL_DEVICE_ID
      fi
    else
      echo ""
      echo "  No Intel GPUs detected via lspci."
      echo "  To find your GPU PCI device ID, run:"
      echo "    lspci -nn | grep -i 'VGA\|Display'"
      echo ""
      _default_id="${INTEL_DEVICE_ID:-8086:e223}"
      read -p "  Enter PCI device ID [${_default_id}]: " _manual_id
      INTEL_DEVICE_ID="${_manual_id:-${_default_id}}"
    fi

    if [ -z "${INTEL_DEVICE_ID}" ]; then
      error "Device ID is required for Intel ARC setup."
    fi
    ok "Device ID: ${INTEL_DEVICE_ID}"

    # ── Card Count ──
    # Count how many cards match the selected device ID
    _DETECTED_COUNT=$(lspci -nn -d "${INTEL_DEVICE_ID}" 2>/dev/null | wc -l)
    [ "${_DETECTED_COUNT}" -lt 1 ] && _DETECTED_COUNT=1
    INTEL_CARD_COUNT="${_DETECTED_COUNT}"

    echo ""
    echo "  Identical cards detected: ${_DETECTED_COUNT}"
    read -p "  Card count [${INTEL_CARD_COUNT}]: " _card_input
    INTEL_CARD_COUNT=${_card_input:-${INTEL_CARD_COUNT}}
    ok "Card count: ${INTEL_CARD_COUNT}"
  fi

  # ── VRAM ──
  echo ""
  _default_vram="${SYCL_VRAM_GB:-32}"
  read -p "  Total GPU VRAM in GB [${_default_vram}]: " _vram_input
  SYCL_VRAM_GB=${_vram_input:-${_default_vram}}
  ok "VRAM: ${SYCL_VRAM_GB}GB"

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
    systemctl daemon-reload 2>/dev/null || true
    ok "Removed legacy ${IPEX_SERVICE_NAME}.service"
  fi

  # ── Step 5b: Ensure service user has GPU device access ──
  # In server mode the watchdog user may not exist yet (created by setup.sh
  # after setup_local.sh returns) — skip gracefully if absent.
  if id "${WATCHDOG_USER}" &>/dev/null; then
    for grp in render video; do
      if getent group "${grp}" >/dev/null 2>&1; then
        if ! id -nG "${WATCHDOG_USER}" 2>/dev/null | grep -qw "${grp}"; then
          usermod -aG "${grp}" "${WATCHDOG_USER}" 2>/dev/null || true
          ok "${WATCHDOG_USER} added to '${grp}' group (GPU device access)"
        fi
      fi
    done
  else
    info "User '${WATCHDOG_USER}' does not exist yet — GPU group membership will be set by setup.sh"
  fi

  # ── Step 6: Model Registry & Selection ──
  info "Step 6: SYCL Model Registry & Selection"
  _load_sycl_registry_arrays

  # Show registry + allow management via manage_registry.sh
  if [ -f "${_MANAGE_REGISTRY_SCRIPT}" ]; then
    source "${_MANAGE_REGISTRY_SCRIPT}" --inline
    # After interactive menu, arrays are up-to-date
  fi

  if [ "${_REG_COUNT}" -eq 0 ]; then
    err "No SYCL models registered. Add models to models.ini [sycl_models] first."
    exit 1
  fi

  echo ""
  echo "  Select the model to run on your Intel ARC GPU:"
  echo ""
  for i in $(seq 0 $((_REG_COUNT - 1))); do
    printf "    %d) %-16s — ~%sGB\n" "$((i + 1))" "${_REG_NAMES[$i]}" "${_REG_SIZES[$i]}"
  done
  echo ""
  echo "  Only one model is loaded at a time. Switch with:"
  echo "    sudo agictl model activate <name>"
  echo ""

  # Determine default selection number
  _MODEL_DEFAULT=1
  for i in $(seq 0 $((_REG_COUNT - 1))); do
    if [ "${_REG_NAMES[$i]}" = "${DEFAULT_MODEL}" ]; then
      _MODEL_DEFAULT=$((i + 1))
      break
    fi
  done

  read -p "  Selection [${_MODEL_DEFAULT}]: " _MODEL_CHOICE
  _MODEL_CHOICE=${_MODEL_CHOICE:-${_MODEL_DEFAULT}}

  # Validate and resolve selection
  _CHOICE_IDX=$((_MODEL_CHOICE - 1))
  if [ "${_CHOICE_IDX}" -lt 0 ] || [ "${_CHOICE_IDX}" -ge "${_REG_COUNT}" ]; then
    warn "Invalid selection. Defaulting to first model."
    _CHOICE_IDX=0
  fi
  DEFAULT_MODEL="${_REG_NAMES[$_CHOICE_IDX]}"
  ok "Selected model: ${DEFAULT_MODEL}"

  SYCL_ACTIVE_MODEL="${DEFAULT_MODEL}"
  SYCL_ACTIVE_GGUF="$(_reg_file_for "${DEFAULT_MODEL}")"

  # ── Step 6b: Concurrency Configuration ──
  _MODEL_SIZE_GB="$(_reg_size_for "${DEFAULT_MODEL}")"
  _calculate_concurrency "${SYCL_VRAM_GB:-32}" "${_MODEL_SIZE_GB}" "${SYCL_CTX_SIZE:-4096}"

  echo ""
  echo "  Concurrency Configuration"
  echo "  ─────────────────────────────────────────"
  echo ""
  echo "  Model:       ${DEFAULT_MODEL} (~${_MODEL_SIZE_GB}GB)"
  echo "  GPU VRAM:    ${SYCL_VRAM_GB:-32}GB"
  echo "  Free VRAM:   ~${_CALC_FREE_VRAM_GB}GB (after model load)"
  echo ""
  echo "  Recommended: ${_CALC_RECOMMENDED} parallel slots"
  echo "  Maximum:     ${_CALC_MAX} parallel slots"
  echo ""
  echo "  Each slot is an independent inference session."
  echo "  llama-server divides --ctx-size across slots, so total = per_slot × parallel."
  echo ""
  echo "  ${_CALC_RECOMMENDED} slots × ${SYCL_CTX_SIZE:-4096} per slot = $(( _CALC_RECOMMENDED * ${SYCL_CTX_SIZE:-4096} )) total context."
  echo ""

  read -p "  Parallel slots [${_CALC_RECOMMENDED}]: " _par_input
  SYCL_PARALLEL=${_par_input:-${_CALC_RECOMMENDED}}
  read -p "  Context size per slot [${SYCL_CTX_SIZE:-4096}]: " _ctx_input
  SYCL_CTX_SIZE=${_ctx_input:-${SYCL_CTX_SIZE:-4096}}
  SYCL_CTX_TOTAL=$(( SYCL_CTX_SIZE * SYCL_PARALLEL ))
  ok "Concurrency: ${SYCL_PARALLEL} slots × ${SYCL_CTX_SIZE}/slot = ${SYCL_CTX_TOTAL} total ctx"

  # ── Step 7: Download Model ──
  info "Step 7: Download Model"
  mkdir -p "${SYCL_MODEL_DIR}"

  _HF_REPO="$(_reg_repo_for "${DEFAULT_MODEL}")"
  _HF_FILE="$(_reg_file_for "${DEFAULT_MODEL}")"

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

  # Build device flags — WSL2 uses /dev/dxg, bare-metal uses /dev/dri
  DOCKER_DEVICES=""
  DOCKER_WSL_MOUNTS=""
  DOCKER_WSL_ENV=""
  if _is_wsl; then
    # WSL2: Windows GPU driver bridge via DirectX Graphics kernel device
    if [ -e /dev/dxg ]; then
      DOCKER_DEVICES="--device /dev/dxg"
    else
      warn "/dev/dxg not found — Docker container may not have GPU access"
    fi
    # Mount the WSL driver libraries so the container can talk to the host GPU
    if [ -d /usr/lib/wsl ]; then
      DOCKER_WSL_MOUNTS="-v /usr/lib/wsl:/usr/lib/wsl"
    fi
    DOCKER_WSL_ENV="-e LD_LIBRARY_PATH=/usr/lib/wsl/lib"
    info "WSL2 Docker: /dev/dxg + /usr/lib/wsl driver bridge"
  else
    # Bare-metal: pass all DRI render/card devices
    for dev in /dev/dri/renderD* /dev/dri/card*; do
      if [ -e "${dev}" ]; then
        DOCKER_DEVICES="${DOCKER_DEVICES} --device ${dev}"
      fi
    done
  fi

  # llama-server --ctx-size is TOTAL context shared across all parallel slots.
  # sycl_ctx_size is per-slot → multiply by parallel for the Docker launch.
  _DOCKER_CTX_TOTAL=$(( ${SYCL_CTX_SIZE:-4096} * ${SYCL_PARALLEL:-1} ))

  # Always launch in Router Mode (--models-dir) — the server auto-discovers
  # all GGUFs in the directory and loads them on demand with LRU eviction.
  # Client-side model_loading_strategy (single/router) controls agent behavior,
  # not how the Docker container runs.
  docker run -d --name "${SYCL_CONTAINER}" \
    --restart unless-stopped \
    ${DOCKER_DEVICES} \
    ${DOCKER_WSL_MOUNTS} \
    ${DOCKER_WSL_ENV} \
    -v "${SYCL_MODEL_DIR}:/models" \
    -p "${SYCL_PORT}:8080" \
    "${SYCL_IMAGE}" \
    --models-dir /models \
    --models-max "${SYCL_MODELS_MAX:-1}" \
    -ngl 99 --host 0.0.0.0 --port 8080 \
    --parallel "${SYCL_PARALLEL:-1}" --ctx-size "${_DOCKER_CTX_TOTAL}"

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
  "default_model": "${DEFAULT_MODEL}",
  "model_loading_strategy": "${MODEL_LOADING_STRATEGY}",
  "lan_ip": "${_LAN_IP}",
  "sycl_ctx_size": ${SYCL_CTX_SIZE:-4096},
  "sycl_parallel": ${SYCL_PARALLEL:-1},
  "sycl_models_max": ${SYCL_MODELS_MAX:-1},
  "sycl_vram_gb": ${SYCL_VRAM_GB:-32},
  "configured_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
}
SRVEOF
  chmod 640 "${SERVER_STATE_FILE}"
  chown "${WATCHDOG_USER}:${WATCHDOG_USER}" "${SERVER_STATE_FILE}" 2>/dev/null || true
  ok "Server state saved: ${SERVER_STATE_FILE}"

  # ── Sudoers: allow watchdog passwordless 'agictl model activate' ──
  # This enables remote model activation from client dashboards via SSH.
  _SUDOERS_FILE="/etc/sudoers.d/versa-agi-model-activate"
  _AGICTL_PATH=$(command -v agictl 2>/dev/null || echo "/usr/bin/agictl")
  _SUDOERS_LINE="${WATCHDOG_USER} ALL=(root) NOPASSWD: ${_AGICTL_PATH} model activate *"
  if [ ! -f "${_SUDOERS_FILE}" ] || ! grep -qF "model activate" "${_SUDOERS_FILE}" 2>/dev/null; then
    echo "${_SUDOERS_LINE}" > "${_SUDOERS_FILE}"
    chmod 0440 "${_SUDOERS_FILE}"
    if visudo -cf "${_SUDOERS_FILE}" >/dev/null 2>&1; then
      ok "Sudoers: ${WATCHDOG_USER} can run 'sudo agictl model activate' without password"
    else
      rm -f "${_SUDOERS_FILE}"
      warn "Sudoers syntax check failed — removed. Add manually if needed."
    fi
  else
    ok "Sudoers: model activate entry already configured"
  fi

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
        sed -i '/^\[local_ai\]/,/^\[/{s/^sycl_vram_gb=.*/sycl_vram_gb='"${SYCL_VRAM_GB}"'/}' "${SETUP_INI}"
        sed -i '/^\[local_ai\]/,/^\[/{s/^sycl_parallel=.*/sycl_parallel='"${SYCL_PARALLEL}"'/}' "${SETUP_INI}"
        sed -i '/^\[local_ai\]/,/^\[/{s/^sycl_ctx_size=.*/sycl_ctx_size='"${SYCL_CTX_SIZE}"'/}' "${SETUP_INI}"
      fi
      # Server mode: disable third-party cloud proxy — inference server only serves local models
      sed -i '/^\[third_party\]/,/^\[/{s/^enabled=.*/enabled=false/}' "${SETUP_INI}"
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
  # All downloaded models are always available — Docker runs in Router Mode
  # (--models-dir) so the server auto-discovers GGUFs on demand.
  RUNTIME_MODELS="${LOCAL_MODELS}"
  for kv in \
    "VERSA_LOCAL_AI_ENABLED=\"true\"" \
    "VERSA_EXECUTION_MODE=\"hybrid\"" \
    "VERSA_GPU_BACKEND=\"${GPU_BACKEND}\"" \
    "VERSA_LOCAL_MODELS=\"${RUNTIME_MODELS}\"" \
    "VERSA_MODEL_LOADING_STRATEGY=\"${MODEL_LOADING_STRATEGY}\""; do
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
    sed -i '/^\[local_ai\]/,/^\[/{s/^sycl_vram_gb=.*/sycl_vram_gb='"${SYCL_VRAM_GB}"'/}' "${SETUP_INI}"
    sed -i '/^\[local_ai\]/,/^\[/{s/^sycl_parallel=.*/sycl_parallel='"${SYCL_PARALLEL}"'/}' "${SETUP_INI}"
    sed -i '/^\[local_ai\]/,/^\[/{s/^sycl_ctx_size=.*/sycl_ctx_size='"${SYCL_CTX_SIZE}"'/}' "${SETUP_INI}"
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


