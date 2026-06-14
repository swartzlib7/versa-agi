#!/bin/bash
# ─────────────────────────────────────────────────────
# Versa AGi — Setup & Update Script
#
# Two modes:
#   Full Install (default):
#     Provisions the entire Versa AGi environment from scratch.
#
#   Update (--update):
#     Safe live-update that deploys changes from source
#     to the running system without requiring a full reinstall.
#     Flow: Git Pull → Pause CRON → Drain → Backup → Deploy →
#           Permissions → Migrate → Resume CRON → Verify
#
# Usage:
#   sudo ./setup.sh                  # Full install
#   sudo ./setup.sh --update         # Update existing install
#   sudo ./setup.sh --update --dry-run
#   sudo ./setup.sh --update --branch hotfix/v3.1
#
# OS:     Linux (Ubuntu, Debian, Fedora, Arch)
#
# ⛔ MANIFEST: Any file path changes MUST be reflected in
#    design/Versa AGi - System Design.md §IX
# ─────────────────────────────────────────────────────

set -euo pipefail

# ─── UI Library ──────────────────────────────────────
SCRIPT_DIR_EARLY="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
UI_LIB="${SCRIPT_DIR_EARLY}/core-infra/ui_lib.sh"
if [ -f "${UI_LIB}" ]; then
  # shellcheck source=core-infra/ui_lib.sh
  source "${UI_LIB}"
else
  # Fallback: minimal inline functions if ui_lib.sh not found
  info()  { echo -e "\033[38;2;0;255;204m[INFO]\033[0m $*"; }
  ok()    { echo -e "\033[0;32m[OK]\033[0m $*"; }
  warn()  { echo -e "\033[1;33m[WARN]\033[0m $*"; }
  error() { echo -e "\033[0;31m[ERROR]\033[0m $*"; exit 1; }
fi

VERSION="3.2.0"
_VERSION_FILE="${SCRIPT_DIR_EARLY}/core-infra/VERSION"
if [ -f "${_VERSION_FILE}" ]; then
  VERSION="$(tr -d '[:space:]' < "${_VERSION_FILE}")"
fi
INSTALL_ACCEPTANCE_LIB="${SCRIPT_DIR_EARLY}/core-infra/install_acceptance.sh"
REGISTRATION_CONF_SRC="${SCRIPT_DIR_EARLY}/core-infra/registration.conf"
if [ -f "${INSTALL_ACCEPTANCE_LIB}" ]; then
  # shellcheck source=core-infra/install_acceptance.sh
  source "${INSTALL_ACCEPTANCE_LIB}"
fi
export INSTALL_ACCEPTANCE_VERSION="${VERSION}"

# ─── Root Check ─────────────────────────────────────
if [ "$(id -u)" -ne 0 ]; then
  error "This script must be run as root (sudo ./setup.sh)"
fi

# ─── Parse Arguments ────────────────────────────────
UPDATE_MODE=false
DRY_RUN=false
SKIP_VERIFY=false
GRACE_PERIOD=60
REPO_BRANCH=""

while [ $# -gt 0 ]; do
  case "$1" in
    --update)       UPDATE_MODE=true; shift ;;
    --dry-run)      DRY_RUN=true; shift ;;
    --skip-verify)  SKIP_VERIFY=true; shift ;;
    --grace)        GRACE_PERIOD="$2"; shift 2 ;;
    --branch)       REPO_BRANCH="$2"; shift 2 ;;
    -h|--help)
      echo "Usage: sudo ./setup.sh [--update] [--dry-run] [--skip-verify] [--grace <seconds>] [--branch <name>]"
      echo ""
      echo "  (no flags)    Full install — provisions entire environment"
      echo "  --update      Update mode — deploy changes to existing system"
      echo "  --dry-run     Preview changes without applying"
      echo "  --skip-verify Skip post-deploy health check"
      echo "  --grace N     Agent drain grace period in seconds (default: 60)"
      echo "  --branch NAME Deploy from a specific git branch"
      exit 0
      ;;
    *)
      error "Unknown argument: $1. Use --help for usage."
      ;;
  esac
done

dry() { echo -e "${BOLD:-\033[1m}[DRY-RUN]${NC:-\033[0m} $*"; }

# ─── Platform Check ─────────────────────────────────
detect_os 2>/dev/null || true
require_linux 2>/dev/null || true

# ─── Configuration ──────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC_CORE_INFRA="${SCRIPT_DIR}/core-infra"
SRC_COA_ENV="${SCRIPT_DIR}/coa-env"

# ─── INI File Parser ────────────────────────────────
# The source setup.ini (next to setup.sh) is the master configuration.
# The deployed copy at /etc/versa-agi/ is a runtime sync target used as
# fallback for values that may only exist there (e.g., set by agictl post-install).
INI_FILE_FALLBACK=""
if [ -f "${SCRIPT_DIR}/setup.ini" ]; then
  INI_FILE="${SCRIPT_DIR}/setup.ini"
  # Deployed copy as fallback for runtime-only values
  if [ -f "/etc/versa-agi/setup.ini" ]; then
    INI_FILE_FALLBACK="/etc/versa-agi/setup.ini"
  fi
else
  # No source setup.ini — scaffold a blank template
  INI_FILE="${SCRIPT_DIR}/setup.ini"
  warn "setup.ini not found — scaffolding a blank template at ${INI_FILE}"
  cat > "${INI_FILE}" <<'TEMPLATE'
# ═══════════════════════════════════════════════
# Versa AGi — Setup Configuration
# ═══════════════════════════════════════════════
# Pre-populate setup values to avoid manual input.
# Canonical location: /etc/versa-agi/setup.ini
# All values are optional — setup will prompt for missing ones.

[versavoice]
api_token=

[gemini]
# Pinned Gemini CLI version. The system is validated against this version.
# Changing this requires re-testing session format, token parsing, and auth.
gemini_cli_version=0.40.0
# Auth method: "api_key" or "vertex" (not required when mode=local)
auth_method=api_key
api_key=

# Execution mode: "cloud", "local", or "hybrid"
#   cloud  — Gemini API only (api_key required)
#   local  — Local AI only via Ollama (no api_key needed)
#   hybrid — Both cloud and local agents available (api_key required for cloud agents)
mode=cloud

# Tracked cloud model registry. Used by Lifeline for backend resolution.
cloud_models=gemini-2.5-pro,gemini-2.5-flash,gemini-2.5-flash-lite,gemini-2.0-flash,gemini-2.0-flash-lite,gemini-3-pro-preview,gemini-3-flash-preview,gemini-3.1-pro-preview,gemini-3.1-flash-lite-preview

# Default Gemini CLI model (--model flag). Per-agent overrides possible in registry.
# Available models (gemini --model <value>):
#   gemini-2.5-pro            — Flagship. Complex reasoning, coding, multimodal. 1M context.
#   gemini-2.5-flash          — Fast, cost-efficient. Good for chatbots and production.
#   gemini-2.5-flash-lite     — Ultra-fast, lightweight. High-volume, low-cost.
#   gemini-2.0-flash          — Previous gen. General-purpose multimodal.
#   gemini-2.0-flash-lite     — Previous gen. Simple, high-frequency tasks.
#   gemini-3-pro-preview      — Next gen preview. Advanced reasoning, 1M context.
#   gemini-3-flash-preview    — Next gen preview. Frontier-class at reduced cost.
#   gemini-3.1-pro-preview    — Next gen preview. Enhanced reasoning, extended context.
#   gemini-3.1-flash-lite-preview — Next gen lite. Ultra-fast, lowest cost. Monitoring/simple tasks.
model=gemini-3-flash-preview

# COA-approved models — only these models appear in the Dashboard model picker
# for the COA agent. Weaker models lack reliable tool-calling and structured output
# required for orchestration. Sub-agents are not restricted by this list.
coa_approved_models=gemini-2.5-pro,gemini-2.5-flash,gemini-3-pro-preview,gemini-3-flash-preview,gemini-3.1-pro-preview,grok-4-1-fast-reasoning,grok-4.20-reasoning

# Default thinking level for agent spawns (Gemini 3+ only).
# Replaces legacy thinking_budget. Cannot mix with thinking_budget.
#   minimal  — Fewest tokens for thinking. Low-complexity tasks, fastest response.
#   low      — Fewer tokens. Simple instruction-following, high-throughput.
#   medium   — Balanced. Moderate complexity tasks. (Gemini 3 Flash only)
#   high     — Deep reasoning. Multi-step planning, code gen. (Default for Gemini 3 Flash)
thinking_level=high

[local_ai]
# Local AI backend (Ollama + Inference Endpoint). Run setup_local.sh to install.
enabled=false
# GPU backend: standard (NVIDIA/AMD), intel (Intel ARC via IPEX-LLM SYCL), or remote (client topology)
gpu_backend=standard
ollama_host=http://localhost:11434
proxy_port=4000
default_model=gemma4:e4b
local_models=gemma4:e4b,gemma4:26b,gemma4:31b
auto_pull_model=true
# Intel ARC IPEX config (only used when gpu_backend=intel)
intel_card_count=1
intel_device_id=8086:e223
# Docker llama-server port (Intel SYCL only)
sycl_port=8080
# Active model for single-mode policy (set by 'agictl model activate').
# Used only when model_loading_strategy=single. All local agents are synced to this model.
# In router mode, agents use per-agent model assignments and this key is ignored.
sycl_active_model=
# Maximum models resident in VRAM simultaneously (llama-server --models-max).
# The server uses LRU eviction when loaded count exceeds this value.
sycl_models_max=1
# HuggingFace token for Intel SYCL model downloads (prompted during setup)
hf_token=
# llama.cpp version tag for Docker image builds (pinned for reproducibility)
sycl_llama_cpp_tag=b9082
# Total GPU VRAM in GB (auto-detected during setup, used for concurrency calculation)
sycl_vram_gb=32
# Concurrent inference slots (llama-server --parallel N)
sycl_parallel=2
# Context window size PER SLOT in tokens (total = sycl_ctx_size × sycl_parallel)
sycl_ctx_size=65536
# Deployment topology: local (default), server, or client
topology=local
remote_inference_url=
inference_master_key=
# Model loading strategy — client-side policy for agent model assignment:
#   single   — All local agents share one model (sycl_active_model). 'model activate' syncs all.
#   router   — Each agent can use a different local model. 'model activate' updates default_model only.
# The Docker container always runs in directory-scanning mode regardless of this setting.
model_loading_strategy=router

[gcp]
# Only needed for vertex auth. If service_account_key is set, it's used;
# otherwise falls back to Application Default Credentials (ADC).
project=
location=us-central1
service_account_key=

[agent]
cron_interval=1
# File monitor: reactive file watcher (inotifywait). Set to false when using
# the post-cycle linger check instead (simpler, avoids race conditions).
file_mon_enabled=false

# Maximum allowed runtime for an agent work cycle (in minutes)
# Default is 60. Set lower (e.g. 30) for stricter runaway protection.
timeout_minutes=60

# Maximum allowed output lines before an agent session is killed (runaway detection).
# If the result file exceeds this threshold during execution, the agent is terminated,
# its tasks are frozen, and the Primary User is notified via VersaVoice.
# Default is 300. Configurable per-agent via dashboard after initial setup.
runaway_threshold=300

# Circuit Breaker — auto-freeze agents that repeatedly fail on spawn.
# Only breaker-eligible exit codes count: 1 (error), 42 (input error), 99 (runaway).
# Excluded: 0 (success), 53 (turn limit), 124 (timeout).
# Configurable via agitop ⚙ System Settings modal.
circuit_breaker_consecutive=5
circuit_breaker_hourly=20

# Message flood guard: auto-lift PU messaging suppression after N hours without
# a new outbound message. Override via VERSA_FLOOD_GUARD_TIMEOUT_HOURS env var.
flood_guard_timeout_hours=3

# COA VersaVoice identity (used by init_vv_identity.sh)
first_name=Versa
last_name=(COA)
# Language: ISO 639-1 code (en, es, fr, de, ja, ko, zh, pt, ar, hi, etc.)
language=en
# Country: Full name as shown in VersaVoice app (optional, leave blank if unsure)
# Examples: United States, Mexico, Japan, Germany, Brazil, South Africa
country=United States
# Voice: male or female
voice=female
# Role: Agent primary role (seeded as first ability)
role=Chief Orchestrator Agent

[logging]
# Lifeline log output: true = write to /var/log/versa-agi-lifeline.log, false = silent (/dev/null)
enabled=true

[users]
watchdog=watchdog
coa=coa

[git]
# Platforms configured with SSH keys (comma-separated): github, gitlab, both, none
# After setup, the COA generates a dedicated SSH keypair (versa_agi_ed25519)
# and shares the public key with the Primary User via the workspace symlink
# for manual platform configuration (deploy key or SSH key).
platforms=none

# Primary User's workspace access path (symlink to .agent/workspace/)
# This is always created — the Primary User needs filesystem visibility.
workspace_link=

[search]
# Web search provider for agent research capabilities.
# Powered by SearXNG (native install via providers/searxng.sh).
# Agents access via: agictl search web "<query>"
enabled=true
engine=searxng
searxng_url=http://localhost:8888

[browser]
# Headless browser automation for agents (Playwright + Chromium).
# Enables: agictl browser goto/click/fill/screenshot/extract
# Install via: setup.sh prompt, agitop System Settings, or manually: sudo ./providers/playwright.sh
# Per-agent control: agitop dashboard → Agent Settings → Browser Usage
enabled=false
# Page load timeout in seconds (default: 30). Editable via agitop System Settings.
timeout=30

[registration]
# Runtime submission state (endpoint + key: core-infra/registration.conf)
acceptance_file=/etc/versa-agi/install-acceptance.json
registration_submitted=false
registration_submitted_at=
registration_last_heartbeat_at=
registration_last_error=
registration_attempt_count=0
TEMPLATE
  chmod 600 "${INI_FILE}"
  echo ""
  info "Generated blank setup.ini at ${INI_FILE}. Dropping into interactive configuration mode."
fi

ini_get() {
  local section=$1
  local key=$2
  local default=${3:-}

  _ini_read() {
    local file=$1
    awk -F '=' -v section="${section}" -v key="${key}" '
      /^\[/ { current = substr($0, 2, length($0)-2) }
      current == section && $1 ~ "^"key"$" {
        val = substr($0, index($0,"=")+1)
        gsub(/^[[:space:]]+|[[:space:]]+$/, "", val)
        print val
        exit
      }
    ' "${file}" 2>/dev/null
  }

  local value=""
  if [ -f "${INI_FILE}" ]; then
    value=$(_ini_read "${INI_FILE}")
  fi
  # Fallback to source setup.ini for missing values
  if [ -z "${value}" ] && [ -n "${INI_FILE_FALLBACK}" ] && [ -f "${INI_FILE_FALLBACK}" ]; then
    value=$(_ini_read "${INI_FILE_FALLBACK}")
  fi
  echo "${value:-${default}}"
}

if [ -f "${INI_FILE}" ]; then
  ok "Loaded configuration from: ${INI_FILE}"
else
  info "No setup.ini found — will prompt for all values"
fi

# Defaults: env vars → setup.ini → hardcoded defaults
WATCHDOG_USER="${VERSA_WATCHDOG_USER:-$(ini_get users watchdog watchdog)}"
COA_USER="${VERSA_COA_USER:-$(ini_get users coa coa)}"
CRON_INTERVAL="${VERSA_CRON_INTERVAL:-$(ini_get agent cron_interval 1)}"  # minutes

# Pre-loaded values from INI (used by prompts later)
INI_VV_ENABLED="$(ini_get versavoice enabled true)"
INI_VV_TOKEN="$(ini_get versavoice api_token)"
INI_AUTH_METHOD="$(ini_get gemini auth_method)"
INI_API_KEY="$(ini_get gemini api_key)"
INI_GCP_PROJECT="$(ini_get gcp project)"
INI_GCP_LOCATION="$(ini_get gcp location us-central1)"
INI_SA_KEY_PATH="$(ini_get gcp service_account_key)"

INI_AGENT_FIRST_NAME="$(ini_get agent first_name COA)"
INI_AGENT_LAST_NAME="$(ini_get agent last_name Agent)"
INI_AGENT_LANGUAGE="$(ini_get agent language en)"
INI_AGENT_COUNTRY="$(ini_get agent country)"
INI_AGENT_VOICE="$(ini_get agent voice female)"
INI_AGENT_ROLE="$(ini_get agent role)"
INI_AGENT_TIMEOUT="$(ini_get agent timeout_minutes 60)"
INI_RUNAWAY_THRESHOLD="$(ini_get agent runaway_threshold 300)"
INI_GIT_PLATFORMS="$(ini_get git platforms none)"
INI_WORKSPACE_LINK="$(ini_get git workspace_link)"
INI_GEMINI_CLI_VERSION="$(ini_get gemini gemini_cli_version 0.40.0)"
INI_GEMINI_MODEL="$(ini_get gemini model gemini-3-flash-preview)"
INI_EXECUTION_MODE="$(ini_get gemini mode cloud)"
INI_CLOUD_MODELS="$(ini_get gemini cloud_models)"
INI_COA_APPROVED_MODELS="$(ini_get gemini coa_approved_models 'gemini-2.5-pro,gemini-2.5-flash,gemini-3-pro-preview,gemini-3-flash-preview,gemini-3.1-pro-preview,grok-4-1-fast-reasoning,grok-4.20-reasoning')"
INI_LOCAL_AI_ENABLED="$(ini_get local_ai enabled false)"
INI_GPU_BACKEND="$(ini_get local_ai gpu_backend standard)"
INI_LOCAL_AI_DEFAULT_MODEL="$(ini_get local_ai default_model gemma4:e4b)"
INI_LOCAL_MODELS="$(ini_get local_ai local_models)"
INI_OLLAMA_HOST="$(ini_get local_ai ollama_host http://localhost:11434)"
INI_PROXY_PORT="$(ini_get local_ai proxy_port 4000)"
INI_AUTO_PULL_MODEL="$(ini_get local_ai auto_pull_model true)"
INI_INTEL_CARD_COUNT="$(ini_get local_ai intel_card_count 1)"
INI_INTEL_DEVICE_ID="$(ini_get local_ai intel_device_id '')"
INI_HF_TOKEN="$(ini_get local_ai hf_token '')"
INI_TOPOLOGY="$(ini_get local_ai topology local)"
INI_REMOTE_INFERENCE_URL="$(ini_get local_ai remote_inference_url '')"
INI_INFERENCE_MASTER_KEY="$(ini_get local_ai inference_master_key '')"

# Deployed paths (in user home directories)
AGENTS_DB="/var/lib/versa-agi/agents.db"
WATCHDOG_HOME="/home/${WATCHDOG_USER}"
COA_HOME="/home/${COA_USER}"
DEPLOYED_CORE_INFRA="${WATCHDOG_HOME}/core-infra"
DEPLOYED_COA_ENV="${COA_HOME}/coa-env"

TIMESTAMP=$(date -u '+%Y%m%dT%H%M%SZ')

if [ "${UPDATE_MODE}" = true ]; then
  banner "update" "${VERSION}"
  echo -e "  ${DIM:-}Timestamp:  ${TIMESTAMP}${RESET:-}"
else
  banner "setup" "${VERSION}"
fi
echo -e "  ${DIM:-}Source:   ${SCRIPT_DIR}${RESET:-}"
echo -e "  ${DIM:-}Deploy:${RESET:-}"
echo -e "    Core Infra → ${DEPLOYED_CORE_INFRA}"
echo -e "    COA Env    → ${DEPLOYED_COA_ENV}"
if [ "${DRY_RUN}" = true ]; then
  echo ""
  warn "DRY-RUN MODE — no changes will be made"
fi
echo ""

# ─── Install / Update Acceptance Gate ───────────────
if [ "${UPDATE_MODE}" = true ]; then
  if declare -F install_acceptance_update_prompt >/dev/null 2>&1; then
    install_acceptance_update_prompt
  fi
else
  if declare -F install_acceptance_welcome >/dev/null 2>&1; then
    install_acceptance_welcome
  fi
  if declare -F install_acceptance_version_gate >/dev/null 2>&1; then
    section "Install Registration"
    install_acceptance_version_gate
    echo ""
  fi
fi

# ─── CRON Pause (--update only, after acceptance gate) ─
# Disable lifeline CRON before deploy. Runs after setup.ini check and the
# update prompt so a missing install aborts without pausing CRON.
if [ "${UPDATE_MODE}" = true ]; then
  _EARLY_CRON=$(crontab -u watchdog -l 2>/dev/null || true)
  CRON_WAS_ACTIVE="${CRON_WAS_ACTIVE:-false}"

  if echo "${_EARLY_CRON}" | grep -qi "^[^#].*lifeline"; then
    CRON_WAS_ACTIVE=true
    if [ "${DRY_RUN}" = true ]; then
      dry "Would comment out lifeline CRON entries"
    else
      echo "${_EARLY_CRON}" | sed '/[Ll]ifeline/s|^\([^#]\)|#\1|' | \
        crontab -u watchdog -
      ok "CRON paused (commented out)"
    fi
  else
    if echo "${_EARLY_CRON}" | grep -qi "^#.*lifeline"; then
      if [ "${CRON_WAS_ACTIVE}" != true ]; then
        CRON_WAS_ACTIVE=true
      fi
    fi
  fi
  export CRON_WAS_ACTIVE
fi

# ═══════════════════════════════════════════════════════
# UPDATE MODE PREAMBLE (--update only)
# CRON is paused in the block above (after acceptance gate).
# Steps: Git pull, drain agents, backup, deploy.
# ═══════════════════════════════════════════════════════
if [ "${UPDATE_MODE}" = true ]; then

  # ─── U2: CRON Status ─────────────────────────────────
  section "Update — CRON Status"
  if [ "${CRON_WAS_ACTIVE}" = true ]; then
    ok "CRON was paused at script start"
  else
    warn "No active lifeline CRON was found at script start"
  fi
  echo ""

  # ─── U1: Git Auto-Update ─────────────────────────────
  # Only pull if running from the persisted system repo.
  # Dev repositories bypass this to prevent destroying local work.
  if [[ "${SCRIPT_DIR}" == *".versa-agi/repo/src"* ]] && [ "${SKIP_PULL:-false}" != true ]; then
    info "Auto-updating persistent repository..."
    REPO_ROOT="$(dirname "${SCRIPT_DIR}")"
    _GIT_USER="${SUDO_USER:-$(whoami)}"

    # Fix any root-owned .git artifacts from previous runs
    if [ "$(id -u)" -eq 0 ] && [ "${_GIT_USER}" != "root" ]; then
      chown -R "${_GIT_USER}:${_GIT_USER}" "${REPO_ROOT}/.git" 2>/dev/null || true
    fi

    # Run git as the invoking user so their SSH keys and file ownership work
    if [ "$(id -u)" -eq 0 ] && [ "${_GIT_USER}" != "root" ]; then
      sudo -u "${_GIT_USER}" bash -c "
        cd '${REPO_ROOT}'
        git fetch --all
        if [ -n '${REPO_BRANCH:-}' ]; then
          git checkout '${REPO_BRANCH}'
          git reset --hard 'origin/${REPO_BRANCH}'
        else
          CURRENT_BRANCH=\$(git branch --show-current)
          git reset --hard \"origin/\${CURRENT_BRANCH:-main}\"
        fi
        git clean -fd
      "
    else
      (
        cd "${REPO_ROOT}"
        git config --global --add safe.directory "$(pwd)"
        git fetch --all
        if [ -n "${REPO_BRANCH:-}" ]; then
          git checkout "${REPO_BRANCH}"
          git reset --hard "origin/${REPO_BRANCH}"
        else
          CURRENT_BRANCH=$(git branch --show-current)
          git reset --hard "origin/${CURRENT_BRANCH:-main}"
        fi
        git clean -fd
      )
    fi
    ok "Repository updated"
    # Re-exec with updated script
    export SKIP_PULL=true
    export CRON_WAS_ACTIVE
    exec "$0" --update ${DRY_RUN:+--dry-run} ${SKIP_VERIFY:+--skip-verify} ${REPO_BRANCH:+--branch "${REPO_BRANCH}"} ${GRACE_PERIOD:+--grace "${GRACE_PERIOD}"}
  fi



  # ─── U3: Drain Running Agents ────────────────────────
  section "Update — Drain Agents"
  drain_agent() {
    local user=$1
    local waited=0
    if pgrep -u "${user}" -f "harness.agent_harness" &>/dev/null; then
      info "Agent running as ${user} — waiting for natural exit..."
      while pgrep -u "${user}" -f "harness.agent_harness" &>/dev/null && [ ${waited} -lt ${GRACE_PERIOD} ]; do
        sleep 5
        waited=$((waited + 5))
        info "  Waiting... (${waited}s / ${GRACE_PERIOD}s)"
      done
      if pgrep -u "${user}" -f "harness.agent_harness" &>/dev/null; then
        warn "Grace period expired — killing agent processes for ${user}"
        pkill -u "${user}" -f "harness.agent_harness" 2>/dev/null || true
        sleep 2
        pkill -9 -u "${user}" -f "harness.agent_harness" 2>/dev/null || true
        ok "Agent processes killed for ${user}"
      else
        ok "Agent ${user} exited naturally"
      fi
    else
      ok "No running agent for ${user}"
    fi
  }
  if [ "${DRY_RUN}" = true ]; then
    dry "Would drain running agents"
  else
    drain_agent "${COA_USER}"
  fi
  echo ""

  # ─── U4: Pre-Deploy Backup ───────────────────────────
  section "Update — Backup"
  BACKUP_DIR="${WATCHDOG_HOME}/backups"
  BACKUP_PATH="${BACKUP_DIR}/${TIMESTAMP}"

  create_backup() {
    local src=$1 name=$2
    local backup_dest="${BACKUP_PATH}/${name}"
    if [ -d "${src}" ]; then
      if [ "${DRY_RUN}" = true ]; then
        dry "Would backup ${src} → ${backup_dest}"
      else
        mkdir -p "${backup_dest}"
        rsync -a --exclude='.git' "${src}/" "${backup_dest}/"
        ok "Backed up ${name} → ${backup_dest}"
      fi
    else
      warn "Nothing to backup for ${name} (not yet deployed)"
    fi
  }
  create_backup "${DEPLOYED_CORE_INFRA}" "core-infra"
  create_backup "${DEPLOYED_COA_ENV}" "coa-env"

  # Prune old backups (keep last 5)
  if [ "${DRY_RUN}" = false ] && [ -d "${BACKUP_DIR}" ]; then
    BACKUP_COUNT=$(find "${BACKUP_DIR}" -maxdepth 1 -type d | wc -l)
    if [ "${BACKUP_COUNT}" -gt 6 ]; then
      OLD_BACKUPS=$(ls -1dt "${BACKUP_DIR}"/*/ 2>/dev/null | tail -n +6)
      for old in ${OLD_BACKUPS}; do
        rm -rf "${old}"
        info "Pruned old backup: ${old}"
      done
    fi
    chown -R "${WATCHDOG_USER}:${WATCHDOG_USER}" "${BACKUP_DIR}"
  fi
  echo ""

fi  # end UPDATE_MODE preamble


# ── OS Compatibility Warning ──────────────────────────
if command -v lsb_release &>/dev/null; then
  _os_ver="$(lsb_release -rs 2>/dev/null)"
  _os_id="$(lsb_release -is 2>/dev/null)"
  if [ "${_os_id}" != "Ubuntu" ] || [ "${_os_ver}" != "24.04" ]; then
    warn "Versa AGi is tested on Ubuntu 24.04. Your system: ${_os_id} ${_os_ver}"
    warn "Proceeding, but some features (especially Intel ARC IPEX) may not work."
  else
    ok "OS: ${_os_id} ${_os_ver}"
  fi
else
  warn "Cannot detect OS version (lsb_release not found). Ubuntu 24.04 is recommended."
fi
echo ""

# ═══════════════════════════════════════════════════════
# INSTALLATION TYPE SELECTION (full install only)
# ═══════════════════════════════════════════════════════
INSTALL_TYPE=""  # 1=client-cloud, 2=client-localai, 3=server

if [ "${UPDATE_MODE}" = false ]; then
  echo ""
  echo "  ┌─────────────────────────────────────────────┐"
  echo "  │  INSTALLATION TYPE                          │"
  echo "  │                                             │"
  echo "  │  1) Client — Cloud AI only                  │"
  echo "  │  2) Client — Cloud + Local AI               │"
  echo "  │  3) Server — Local AI inference engine only │"
  echo "  └─────────────────────────────────────────────┘"
  echo ""

  # Auto-select from setup.ini topology value
  case "${INI_TOPOLOGY}" in
    server) INSTALL_DEFAULT=3 ;;
    client) INSTALL_DEFAULT=2 ;;
    *)      INSTALL_DEFAULT=1 ;;
  esac
  # If local_ai is enabled in INI, default to option 2
  if [ "${INI_LOCAL_AI_ENABLED}" = "true" ] && [ "${INSTALL_DEFAULT}" -eq 1 ]; then
    INSTALL_DEFAULT=2
  fi

  read -p "  Select installation type [${INSTALL_DEFAULT}]: " INSTALL_TYPE_CHOICE
  INSTALL_TYPE="${INSTALL_TYPE_CHOICE:-${INSTALL_DEFAULT}}"

  echo ""

  # ── Server Mode: delegate to setup_local.sh and exit ──
  if [ "${INSTALL_TYPE}" = "3" ]; then
    section "Server — Local AI Inference Engine"
    echo ""
    SETUP_LOCAL_SCRIPT="${SCRIPT_DIR}/setup_local.sh"
    if [ -f "${SETUP_LOCAL_SCRIPT}" ]; then
      chmod +x "${SETUP_LOCAL_SCRIPT}"
      bash "${SETUP_LOCAL_SCRIPT}" --topology server
      # Write topology to setup.ini (dual-write)
      for _ini_file in "${INI_FILE}" "/etc/versa-agi/setup.ini"; do
        if [ -f "${_ini_file}" ]; then
          sed -i '/^\[local_ai\]/,/^\[/{s/^topology=.*/topology=server/}' "${_ini_file}"
        fi
      done

      # ── Deploy agictl for server management (model activate, model list, etc.) ──
      section "Server — agictl Deployment"

      # Ensure watchdog user exists (setup_local.sh may not create it in server mode)
      if ! id "${WATCHDOG_USER}" &>/dev/null; then
        useradd -r -m -s /bin/bash "${WATCHDOG_USER}" 2>/dev/null || true
        ok "Created service user: ${WATCHDOG_USER}"
      fi

      # Deploy core-infra subset needed for agictl
      SOURCE_CORE_INFRA="${SCRIPT_DIR}/core-infra"
      mkdir -p "${DEPLOYED_CORE_INFRA}/agictl" \
               "${DEPLOYED_CORE_INFRA}/bin" \
               "${DEPLOYED_CORE_INFRA}/config" \
               "${DEPLOYED_CORE_INFRA}/harness"

      # agictl CLI module
      if [ -d "${SOURCE_CORE_INFRA}/agictl" ]; then
        cp -r "${SOURCE_CORE_INFRA}/agictl/"* "${DEPLOYED_CORE_INFRA}/agictl/"
      fi
      # bin scripts (agictl binary + wrapper)
      for f in agictl agictl-wrapper; do
        if [ -f "${SOURCE_CORE_INFRA}/bin/${f}" ]; then
          cp "${SOURCE_CORE_INFRA}/bin/${f}" "${DEPLOYED_CORE_INFRA}/bin/${f}"
          chmod 755 "${DEPLOYED_CORE_INFRA}/bin/${f}"
        fi
      done
      # Config (models.ini — canonical + local copy)
      if [ -f "${SCRIPT_DIR}/models.ini" ]; then
        cp "${SCRIPT_DIR}/models.ini" "/etc/versa-agi/models.ini"
        chown "${WATCHDOG_USER}:agi_agents" "/etc/versa-agi/models.ini" 2>/dev/null || true
        chmod 640 "/etc/versa-agi/models.ini"
        # Also keep a local copy for agictl dev fallback
        cp "${SCRIPT_DIR}/models.ini" "${DEPLOYED_CORE_INFRA}/config/"
      fi
      # Model context module (needed by agictl for num_ctx resolution)
      if [ -f "${SOURCE_CORE_INFRA}/harness/model_context.py" ]; then
        cp "${SOURCE_CORE_INFRA}/harness/model_context.py" "${DEPLOYED_CORE_INFRA}/harness/"
        touch "${DEPLOYED_CORE_INFRA}/harness/__init__.py"
      fi
      if [ -f "${SOURCE_CORE_INFRA}/harness/model_params.py" ]; then
        cp "${SOURCE_CORE_INFRA}/harness/model_params.py" "${DEPLOYED_CORE_INFRA}/harness/"
      fi

      chown -R "${WATCHDOG_USER}:${WATCHDOG_USER}" "${DEPLOYED_CORE_INFRA}"
      ok "core-infra deployed to ${DEPLOYED_CORE_INFRA} (server subset)"

      # Install agictl globally
      LIB_DIR="/usr/local/lib/versa-agi"
      mkdir -p "${LIB_DIR}"
      if [ -f "${DEPLOYED_CORE_INFRA}/bin/agictl" ]; then
        cp "${DEPLOYED_CORE_INFRA}/bin/agictl" "${LIB_DIR}/agictl"
        chown root:root "${LIB_DIR}/agictl"
        chmod 755 "${LIB_DIR}/agictl"
      fi
      if [ -f "${DEPLOYED_CORE_INFRA}/bin/agictl-wrapper" ]; then
        cp "${DEPLOYED_CORE_INFRA}/bin/agictl-wrapper" /usr/local/bin/agictl
        chown root:root /usr/local/bin/agictl
        chmod 755 /usr/local/bin/agictl
      fi
      ok "agictl installed to /usr/local/bin/"

      # Ensure paths.env exists with core infra path
      PATHS_ENV="/etc/versa-agi/paths.env"
      mkdir -p /etc/versa-agi
      if [ ! -f "${PATHS_ENV}" ]; then
        touch "${PATHS_ENV}"
      fi
      # Write/update VERSA_CORE_INFRA
      if grep -q "^VERSA_CORE_INFRA=" "${PATHS_ENV}" 2>/dev/null; then
        sed -i "s|^VERSA_CORE_INFRA=.*|VERSA_CORE_INFRA=\"${DEPLOYED_CORE_INFRA}\"|" "${PATHS_ENV}"
      else
        echo "VERSA_CORE_INFRA=\"${DEPLOYED_CORE_INFRA}\"" >> "${PATHS_ENV}"
      fi
      ok "paths.env: VERSA_CORE_INFRA=${DEPLOYED_CORE_INFRA}"

      # Ensure setup.ini is available in /etc/versa-agi/ (always sync from source)
      if [ -f "${INI_FILE}" ]; then
        cp "${INI_FILE}" "/etc/versa-agi/setup.ini"
        chown "${WATCHDOG_USER}:agi_agents" "/etc/versa-agi/setup.ini" 2>/dev/null || true
        chmod 640 "/etc/versa-agi/setup.ini"
        ok "setup.ini deployed to /etc/versa-agi/"
      fi

      if [ -f "${REGISTRATION_CONF_SRC}" ]; then
        cp "${REGISTRATION_CONF_SRC}" "/etc/versa-agi/registration.conf"
        chown root:"${WATCHDOG_USER}" "/etc/versa-agi/registration.conf" 2>/dev/null || true
        chmod 640 "/etc/versa-agi/registration.conf"
        ok "registration.conf deployed to /etc/versa-agi/"
      fi

      # ── Python dependencies for agictl ──
      # Server path skips Step 2 Prerequisites, so install what we need directly.
      # Ubuntu 24.04 (PEP 668): pip for library imports, pipx for CLI tools.
      info "Installing agictl Python dependencies..."
      if command -v apt-get &>/dev/null; then
        apt-get install -y -qq python3-pip pipx 2>/dev/null
      fi

      # Click (library import — required by agictl CLI framework)
      if ! python3 -c "import click" 2>/dev/null; then
        pip3 install --quiet --break-system-packages click
        ok "Python Click installed"
      else
        ok "Python Click already installed"
      fi

      # HuggingFace CLI (CLI tool — required by 'agictl model add' for Intel GGUF downloads)
      if command -v hf &>/dev/null || command -v huggingface-cli &>/dev/null; then
        ok "HuggingFace CLI already installed"
      else
        pipx install 'huggingface-hub[cli]'
        # pipx installs to ~/.local/bin — ensure it's on PATH for subsequent commands
        export PATH="/root/.local/bin:${PATH}"
        if command -v hf &>/dev/null || command -v huggingface-cli &>/dev/null; then
          ok "HuggingFace CLI installed (pipx)"
        else
          error "HuggingFace CLI installation failed. Try manually: pipx install huggingface-hub[cli]"
        fi
      fi

      echo ""
      ok "Server setup complete. agictl is available for model management."
      echo ""
      echo "  ┌─ Model Management ────────────────────────────┐"
      echo "  │                                                │"
      echo "  │  Switch active model:                          │"
      echo "  │    sudo agictl model activate gemma4:26b       │"
      echo "  │                                                │"
      echo "  │  Add a model:                                  │"
      echo "  │    sudo agictl model add qwen3.6:35b           │"
      echo "  │                                                │"
      echo "  │  List models:                                  │"
      echo "  │    agictl model list                           │"
      echo "  │                                                │"
      echo "  └────────────────────────────────────────────────┘"
      echo ""
      if declare -F install_acceptance_record_full >/dev/null 2>&1; then
        section "Registration Submit"
        install_acceptance_record_full "false"
        echo ""
      fi
      exit 0
    else
      error "setup_local.sh not found at ${SETUP_LOCAL_SCRIPT}"
    fi
  fi

  # ── Client overrides: sync INI values to match installation type selection ──
  if [ "${INSTALL_TYPE}" = "1" ]; then
    # Cloud-only: no local AI, reset topology to default
    INI_TOPOLOGY="local"
    INI_EXECUTION_MODE="cloud"
    INI_LOCAL_AI_ENABLED="false"
    info "Client (cloud only) — local AI will be skipped"
  else
    # Client + Local AI: force topology=client and mode=hybrid
    # This prevents a stale topology=server (from a shared INI) from
    # steering setup_local.sh into installing GPU infrastructure locally.
    INI_TOPOLOGY="client"
    INI_EXECUTION_MODE="hybrid"
    INI_LOCAL_AI_ENABLED="true"
    info "Client (cloud + local AI) — topology set to client"
  fi

  # Write overrides back to setup.ini (both source and deployed)
  for _ini_file in "${INI_FILE}" "/etc/versa-agi/setup.ini"; do
    if [ -f "${_ini_file}" ]; then
      sed -i '/^\[local_ai\]/,/^\[/{s/^topology=.*/topology='"${INI_TOPOLOGY}"'/}' "${_ini_file}"
      sed -i '/^\[local_ai\]/,/^\[/{s/^enabled=.*/enabled='"${INI_LOCAL_AI_ENABLED}"'/}' "${_ini_file}"
      sed -i '/^\[gemini\]/,/^\[/{s/^mode=.*/mode='"${INI_EXECUTION_MODE}"'/}' "${_ini_file}"
    fi
  done
fi

# ═══════════════════════════════════════════════════════
# INSTALL-ONLY STEPS (skipped in --update mode)
# ═══════════════════════════════════════════════════════
if [ "${UPDATE_MODE}" = false ]; then

# ─── Step 1: OS User Creation ───────────────────────
section "Step 1 — OS User Creation"

create_user() {
  local username=$1
  local description=$2

  if id "${username}" &>/dev/null; then
    ok "User '${username}' already exists"
  else
    # Use -g if group already exists (leftover from previous uninstall)
    if getent group "${username}" &>/dev/null; then
      useradd -m -s /bin/bash -g "${username}" -c "${description}" "${username}"
    else
      useradd -m -s /bin/bash -c "${description}" "${username}"
    fi
    ok "Created user '${username}'"
  fi

  # Ensure home directory and standard dot files ownership is correct (vital after a restore)
  local home_dir
  home_dir=$(getent passwd "${username}" | cut -d: -f6)
  if [ -n "${home_dir}" ] && [ -d "${home_dir}" ]; then
    chown "${username}:${username}" "${home_dir}"
    # Fix standard user dot files that may have wrong ownership from backup
    for dotfile in .bashrc .bash_logout .bash_history .profile .local .npm .cache; do
      [ -e "${home_dir}/${dotfile}" ] && chown -R "${username}:${username}" "${home_dir}/${dotfile}" 2>/dev/null || true
    done
  fi
}

create_user "${WATCHDOG_USER}" "Versa AGi Watchdog — Core Infrastructure"
create_user "${COA_USER}" "Versa AGi COA — Agent Operations"

# Allow watchdog to monitor coa processes
if ! groups "${WATCHDOG_USER}" | grep -q "${COA_USER}"; then
  usermod -aG "${COA_USER}" "${WATCHDOG_USER}" 2>/dev/null || true
  ok "Added ${WATCHDOG_USER} to ${COA_USER} group (process monitoring)"
fi

# Allow Primary User to traverse coa home for workspace symlink access
if [ -n "${SUDO_USER:-}" ] && ! groups "${SUDO_USER}" 2>/dev/null | grep -q "${COA_USER}"; then
  usermod -aG "${COA_USER}" "${SUDO_USER}" 2>/dev/null || true
  ok "Added ${SUDO_USER} to ${COA_USER} group (workspace symlink access)"
fi

# Create agi_agents group (shared group for workspace access)
# Members: coa, watchdog, Primary User — sub-agents are added at 'agictl agent add' time
if ! getent group agi_agents &>/dev/null; then
  groupadd agi_agents
  ok "Created agi_agents group"
else
  ok "agi_agents group already exists"
fi
usermod -aG agi_agents "${COA_USER}" 2>/dev/null || true
usermod -aG agi_agents "${WATCHDOG_USER}" 2>/dev/null || true
if [ -n "${SUDO_USER:-}" ]; then
  usermod -aG agi_agents "${SUDO_USER}" 2>/dev/null || true
fi
ok "agi_agents group members: ${COA_USER}, ${WATCHDOG_USER}${SUDO_USER:+, ${SUDO_USER}}"

echo ""

fi  # end UPDATE_MODE=false guard (Step 1)

# ─── Step 2: Prerequisites Check ────────────────────
if [ "${UPDATE_MODE}" = false ]; then
section "Step 2 — Prerequisites"
info "Checking system dependencies - please wait..."

# Resolve WSL & Minimal Ubuntu Dependencies
if command -v add-apt-repository &>/dev/null && command -v apt-get &>/dev/null; then
  # inotify-tools on Ubuntu is stored in the universe repository
  with_spinner "Updating package index..." sudo add-apt-repository universe -y &>/dev/null || true
  with_spinner "Refreshing apt cache..." sudo apt-get update || true
fi

# Ensure python venv module exists for agitop and agictl (stripped on minimal images)
if ! python3 -c "import ensurepip" &>/dev/null; then
  warn "Missing python3-venv module. Attempting automatic installation..."
  if command -v apt-get &>/dev/null; then
    _PY_VER=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>/dev/null)
    with_spinner "Installing python3-venv..." sudo apt-get install -y python3-venv python3-pip || \
    with_spinner "Installing python3-venv (versioned)..." sudo apt-get install -y "python3.${_PY_VER}-venv" python3-pip || true
  fi
  if ! python3 -c "import ensurepip" &>/dev/null; then
    warn "python3-venv still unavailable after install attempt — venv will be created without pip"
  fi
fi

check_command() {
  local cmd=$1
  local install_cmd=$2

  if command -v "${cmd}" &>/dev/null; then
    local version
    version=$("${cmd}" --version 2>&1 | head -1 || true)
    ok "${cmd}: ${version}"
  else
    warn "${cmd} not found."
    read -p "Install ${cmd} now? [y/N] " -n 1 -r
    echo ""
    if [[ $REPLY =~ ^[Yy]$ ]]; then
      info "Running: ${install_cmd}"
      if [[ "${install_cmd}" == brew* ]]; then
        sudo -u ${SUDO_USER:-$USER} ${install_cmd}
      else
        ${install_cmd}
      fi
      if command -v "${cmd}" &>/dev/null; then
        ok "${cmd} installed successfully."
      else
        error "Installation failed. Please install manually: ${install_cmd}"
      fi
    else
      error "${cmd} is required to continue. Setup aborted."
    fi
  fi
}

check_command "git" "sudo apt install git"
check_command "sqlite3" "sudo apt install sqlite3"
check_command "jq" "sudo apt install jq"
check_command "inotifywait" "sudo apt install inotify-tools"
check_command "curl" "sudo apt install curl"
check_command "rsync" "sudo apt install rsync"



echo ""
fi  # end UPDATE_MODE=false guard (Step 2)

# ─── Step 3: Deploy Files to User Home Dirs ──────────
section "Step 3 — Deploy Files"

deploy_repo() {
  local src=$1
  local dest=$2
  local owner=$3
  local name=$4

  if [ -d "${dest}" ]; then
    warn "${name} already exists at ${dest} — updating files (preserving runtime state)"
    # Sync files but preserve runtime state, credentials, and data
    rsync -a \
      --exclude='.git' \
      --exclude='agent_memory.db' \
      --exclude='*.lock' \
      --exclude='.env' \
      "${src}/" "${dest}/"
    ok "Updated ${name}"
  else
    cp -r "${src}" "${dest}"
    ok "Deployed ${name} → ${dest}"
  fi

  chown -R "${owner}:${owner}" "${dest}"
  ok "Ownership set: ${dest} → ${owner}"
}

deploy_repo "${SRC_CORE_INFRA}" "${DEPLOYED_CORE_INFRA}" "${WATCHDOG_USER}" "Core Infrastructure"
deploy_repo "${SRC_COA_ENV}" "${DEPLOYED_COA_ENV}" "${COA_USER}" "COA Environment"

# Deploy models.ini to canonical location (alongside setup.ini)
# Fresh box: seed the template here (early consumers need it). Existing box:
# leave it — the Model Catalog step below regenerates it deterministically via
# `agictl system reconcile-config` (template body + preserved [catalog_custom]/
# [providers_custom] user layer + registry-added local rows), then
# `agictl model migrate` rebuilds the [catalog]/[providers] baseline.
if [ -f "${SCRIPT_DIR}/models.ini" ]; then
  if [ -f "/etc/versa-agi/models.ini" ]; then
    ok "models.ini preserved (existing file kept — runtime edits retained)"
  else
    cp "${SCRIPT_DIR}/models.ini" "/etc/versa-agi/models.ini"
    chown "${WATCHDOG_USER}:agi_agents" "/etc/versa-agi/models.ini" 2>/dev/null || true
    chmod 640 "/etc/versa-agi/models.ini"
    ok "models.ini deployed to /etc/versa-agi/models.ini"
  fi
fi

# Agent registry workspace paths are set during registry seeding (Step 6b).

echo ""

# ─── Step 4: VersaVoice Configuration ───────────────
if [ "${UPDATE_MODE}" = false ]; then
echo ""
echo -e "  ${BCYAN}─── ${BOLD}Step 4 — $(_install_acceptance_brand)${RESET}${BCYAN} ${DGRAY}$(printf '─%.0s' $(seq 1 24))${RESET}"
echo ""

if declare -F install_acceptance_vv_prompt >/dev/null 2>&1; then
  install_acceptance_vv_prompt "${INI_VV_ENABLED}"
  ENABLE_VV="${ENABLE_VV:-false}"
else
  ENABLE_VV="${INI_VV_ENABLED}"
fi

if [ "${ENABLE_VV}" = "true" ]; then
  echo -e "All agents share the Primary User's (sponsor's) $(_install_acceptance_brand) API token."
  echo -e "Get yours from: $(_install_acceptance_brand) App → Settings → System (tap label 5 times) → Generate API Token"
  echo ""

  if [ -n "${INI_VV_TOKEN}" ]; then
    VV_TOKEN="${INI_VV_TOKEN}"
    ok "VersaVoice token loaded from setup.ini"
  else
    echo -e -n "  Enter your $(_install_acceptance_brand) API Token (sponsor token): "
    read -r VV_TOKEN
    while [ -z "${VV_TOKEN}" ]; do
      echo -e "${RED}A VersaVoice API token is required when VV is enabled.${NC}"
      echo -e -n "  Enter your $(_install_acceptance_brand) API Token (sponsor token): "
      read -r VV_TOKEN
    done
    ok "VersaVoice token captured"
  fi
else
  VV_TOKEN=""
fi

# Write enabled state to setup.ini (both source and deployed)
# IMPORTANT: sed must be scoped to [versavoice] section — enabled= exists in multiple sections
for _ini_vv_file in "${INI_FILE}" "/etc/versa-agi/setup.ini"; do
  if [ -f "${_ini_vv_file}" ]; then
    sed -i '/^\[versavoice\]/,/^\[/{s/^enabled=.*/enabled='"${ENABLE_VV}"'/}' "${_ini_vv_file}"
  fi
done
ok "VersaVoice enabled: ${ENABLE_VV}"



# NOTE: MCP settings (VV token, store path) are injected AFTER
# configure_gemini_auth (Step 9), which writes selectedAuthType.
# See the "Inject MCP config" block after the auth call.

SYSCONFIG_FOR_TOKEN="${DEPLOYED_CORE_INFRA}/config/system_config.json"
if [ -f "${SYSCONFIG_FOR_TOKEN}" ] && [ -n "${VV_TOKEN}" ]; then
  jq --arg token "${VV_TOKEN}" '.versavoice.api_token = $token' \
    "${SYSCONFIG_FOR_TOKEN}" > "${SYSCONFIG_FOR_TOKEN}.tmp" && \
    mv "${SYSCONFIG_FOR_TOKEN}.tmp" "${SYSCONFIG_FOR_TOKEN}"
  chown "${COA_USER}:${COA_USER}" "${SYSCONFIG_FOR_TOKEN}"
  chmod 640 "${SYSCONFIG_FOR_TOKEN}"
  ok "API token written to system_config.json (versavoice.api_token)"
fi

# Track system_config.json path for later steps
SYSCONFIG="${DEPLOYED_CORE_INFRA}/config/system_config.json"

echo ""
fi  # end UPDATE_MODE=false guard (Step 4)

# Track system_config.json path for later steps (must be outside guard for --update mode)
SYSCONFIG="${DEPLOYED_CORE_INFRA}/config/system_config.json"
SYSCONFIG_FOR_TOKEN="${DEPLOYED_CORE_INFRA}/config/system_config.json"

# VV_TOKEN must be available outside Step 4 for poise injection + identity resolution
# In install mode it's set by Step 4; ensure it's always available from INI.
VV_TOKEN="${VV_TOKEN:-${INI_VV_TOKEN}}"



# ─── Step 5b: AI Model Selection ─────────────────────
# Disabled: COA requires a capable model for reliable tool-calling and structured output.
# The default model is set in setup.ini [gemini] model= and is validated against
# coa_approved_models. Per-agent overrides are available via the Dashboard.
# section "Step 5b — AI Model Selection"
# ...
ok "Default model: ${INI_GEMINI_MODEL} (from setup.ini)"

echo ""

# ─── Step 6: Initialize Decoupled SQLite Databases ──────────────
section "Step 6 — SQLite Databases"

# Define Database Paths
MESSAGES_DB="/var/lib/versa-agi/messages.db"
TASKS_DB="/var/lib/versa-agi/coa/tasks.db"
CYCLES_DB="/var/lib/versa-agi/coa/cycles.db"

# Create Database Directories
mkdir -p "/var/lib/versa-agi/coa"
chown "${WATCHDOG_USER}:${COA_USER}" "/var/lib/versa-agi/coa"
chmod 750 "/var/lib/versa-agi/coa"

# -- 6a. agents.db (Global Registry) --
AGENTS_INIT="${DEPLOYED_CORE_INFRA}/scripts/init_agents_db.sh"
if [ -f "${AGENTS_INIT}" ]; then
  chmod +x "${AGENTS_INIT}"
  bash "${AGENTS_INIT}" "${AGENTS_DB}"
  chown "${WATCHDOG_USER}:${COA_USER}" "${AGENTS_DB}"
  chmod 660 "${AGENTS_DB}"

  # Seed protected agents
  sqlite3 "${AGENTS_DB}" \
    "INSERT OR IGNORE INTO agents (name, os_user, workspace, role, timeout_minutes, runaway_threshold, inactive, protected, requested_by)
     VALUES
       ('coa', '${COA_USER}', '${DEPLOYED_COA_ENV}', 'Chief Orchestrator Agent', ${INI_AGENT_TIMEOUT}, ${INI_RUNAWAY_THRESHOLD}, 0, 1, 'setup'),
       ('watchdog', '${WATCHDOG_USER}', '${WATCHDOG_HOME}', 'System Watchdog', ${INI_AGENT_TIMEOUT}, ${INI_RUNAWAY_THRESHOLD}, 0, 1, 'setup');"
  ok "agents.db initialized at ${AGENTS_DB} (watchdog:${COA_USER} 660)"
else
  error "agents.db init script not found at ${AGENTS_INIT}"
fi

# -- 6b. messages.db (Watchdog Communications) --
MESSAGES_INIT="${DEPLOYED_CORE_INFRA}/scripts/init_messages_db.sh"
if [ -f "${MESSAGES_INIT}" ]; then
  chmod +x "${MESSAGES_INIT}"
  bash "${MESSAGES_INIT}" "${MESSAGES_DB}"
  chown "${WATCHDOG_USER}:${COA_USER}" "${MESSAGES_DB}"
  chmod 660 "${MESSAGES_DB}"
  ok "messages.db initialized at ${MESSAGES_DB} (watchdog:${COA_USER} 660)"
else
  error "messages.db init script not found at ${MESSAGES_INIT}"
fi

# -- 6c. tasks.db (Cognitive Tracker) --
TASKS_INIT="${DEPLOYED_CORE_INFRA}/scripts/init_tasks_db.sh"
if [ -f "${TASKS_INIT}" ]; then
  chmod +x "${TASKS_INIT}"
  bash "${TASKS_INIT}" "${TASKS_DB}"
  # COA and all Sub-agents read/write here.
  chown "${WATCHDOG_USER}:${COA_USER}" "${TASKS_DB}"
  chmod 660 "${TASKS_DB}"
  ok "tasks.db initialized at ${TASKS_DB} (${WATCHDOG_USER}:${COA_USER} 660)"
else
  error "tasks.db init script not found at ${TASKS_INIT}"
fi

# -- 6d. cycles.db (Agent Telemetry) --
CYCLES_INIT="${DEPLOYED_CORE_INFRA}/scripts/init_cycles_db.sh"
if [ -f "${CYCLES_INIT}" ]; then
  chmod +x "${CYCLES_INIT}"
  bash "${CYCLES_INIT}" "${CYCLES_DB}"
  chown "${WATCHDOG_USER}:${COA_USER}" "${CYCLES_DB}"
  chmod 660 "${CYCLES_DB}"
  ok "cycles.db initialized at ${CYCLES_DB} (${WATCHDOG_USER}:${COA_USER} 660)"
else
  error "cycles.db init script not found at ${CYCLES_INIT}"
fi

# ─── Step 6c: Generate paths.env ─────────────────────
info "Generating paths.env..."

PATHS_ENV="/etc/versa-agi/paths.env"
mkdir -p "$(dirname "${PATHS_ENV}")"
if [ "${INI_TOPOLOGY}" = "client" ] && [ -n "${INI_REMOTE_INFERENCE_URL}" ]; then
  # Extract the port from the remote URL (the SSH tunnel maps exactly this port to localhost)
  _TUNNEL_PORT=$(echo "${INI_REMOTE_INFERENCE_URL}" | grep -oP ':\K[0-9]+$' || echo "11434")
  VERSA_INFERENCE_URL_VAL="http://localhost:${_TUNNEL_PORT}"
else
  VERSA_INFERENCE_URL_VAL="http://localhost:${INI_PROXY_PORT}"
fi

# ── Pre-compute values before writing paths.env ──

# Intel SYCL single-model constraint: only the active model is selectable
_PATHS_LOCAL_MODELS="${INI_LOCAL_MODELS}"
_PATHS_GPU_BACKEND="${INI_GPU_BACKEND}"
if [ "${INI_TOPOLOGY}" = "client" ]; then
  _PATHS_GPU_BACKEND="remote"
fi
if [ "${_PATHS_GPU_BACKEND}" = "intel" ] || [ "${_PATHS_GPU_BACKEND}" = "remote" ]; then
  _SYCL_ACTIVE="$(ini_get local_ai sycl_active_model '')"
  if [ -n "${_SYCL_ACTIVE}" ]; then
    _PATHS_LOCAL_MODELS="${_SYCL_ACTIVE}"
  fi
fi

# Aggregate third-party models from all enabled providers
_PATHS_PROXY_ENABLED="$(ini_get third_party enabled false)"
_PATHS_PROXY_MODELS=""
_PATHS_PROVIDERS="$(ini_get third_party providers '')"
if [ -n "${_PATHS_PROVIDERS}" ]; then
  IFS=',' read -ra _PP_LIST <<< "${_PATHS_PROVIDERS}"
  for _pp in "${_PP_LIST[@]}"; do
    _pp=$(echo "${_pp}" | xargs)
    _pp_enabled="$(ini_get third_party "${_pp}_enabled" false)"
    _pp_models="$(ini_get third_party "${_pp}_models" '')"
    if [ "${_pp_enabled}" = "true" ] && [ -n "${_pp_models}" ]; then
      [ -n "${_PATHS_PROXY_MODELS}" ] && _PATHS_PROXY_MODELS="${_PATHS_PROXY_MODELS},"
      _PATHS_PROXY_MODELS="${_PATHS_PROXY_MODELS}${_pp_models}"
    fi
  done
fi

cat > "${PATHS_ENV}" <<PATHSEOF
# Versa AGi — INI-derived system paths
# Generated by setup.sh — do not edit manually.
# Source this file in scripts instead of hardcoding paths.
VERSA_WATCHDOG_USER="${WATCHDOG_USER}"
VERSA_COA_USER="${COA_USER}"
VERSA_CORE_INFRA="${DEPLOYED_CORE_INFRA}"
VERSA_COA_ENV="${DEPLOYED_COA_ENV}"
VERSA_AGENTS_DB="${AGENTS_DB}"
VERSA_DEFAULT_MODEL="${INI_GEMINI_MODEL}"
VERSA_GEMINI_CLI_VERSION="${INI_GEMINI_CLI_VERSION}"
VERSA_LOGGING_ENABLED="$(ini_get logging enabled true)"
VERSA_EXECUTION_MODE="${INI_EXECUTION_MODE}"
VERSA_CLOUD_MODELS="${INI_CLOUD_MODELS}"
VERSA_COA_APPROVED_MODELS="${INI_COA_APPROVED_MODELS}"
VERSA_LOCAL_AI_ENABLED="${INI_LOCAL_AI_ENABLED}"
VERSA_GPU_BACKEND="${_PATHS_GPU_BACKEND}"
VERSA_LOCAL_MODELS="${_PATHS_LOCAL_MODELS}"
VERSA_INFERENCE_URL="${VERSA_INFERENCE_URL_VAL}"
VERSA_THIRD_PARTY_ENABLED="${_PATHS_PROXY_ENABLED}"
VERSA_THIRD_PARTY_MODELS="${_PATHS_PROXY_MODELS}"
PATHSEOF
chown "${WATCHDOG_USER}:${COA_USER}" "${PATHS_ENV}"
chmod 644 "${PATHS_ENV}"
ok "paths.env generated at ${PATHS_ENV}"

echo ""

# ─── Step 7: Initialize Git Repositories ────────────
if [ "${UPDATE_MODE}" = false ]; then
section "Step 7 — Git Repositories"

init_git_repo() {
  local repo_path=$1
  local repo_name=$2
  local owner_user=$3
  local GIT="git -c safe.directory=${repo_path}"

  if [ -d "${repo_path}/.git" ]; then
    ok "Git repo already initialized: ${repo_name}"
  else
    ${GIT} -C "${repo_path}" init -b main
    ${GIT} -C "${repo_path}" config user.email "versa-agi@local"
    ${GIT} -C "${repo_path}" config user.name "${owner_user}"
    ${GIT} -C "${repo_path}" add .
    ${GIT} -C "${repo_path}" commit -m "Initial commit: ${repo_name} scaffold"
    chown -R "${owner_user}:${owner_user}" "${repo_path}/.git"
    ok "Git repo initialized: ${repo_name} (branch: main)"
  fi
}

init_git_repo "${DEPLOYED_CORE_INFRA}" "Core Infrastructure" "${WATCHDOG_USER}"
init_git_repo "${DEPLOYED_COA_ENV}" "COA Environment" "${COA_USER}"

info "Note: To backup your local agents, configure a remote origin tracking URL later:"
echo -e "  ${DIM:-}cd ${DEPLOYED_CORE_INFRA} && git remote add origin <your-repo-url>${RESET:-}"

echo ""
fi  # end UPDATE_MODE=false guard (Step 7)

# ─── Step 8: Make Scripts Executable & Install Agent CLI ─
section "Step 8 — Permissions & CLI"

chmod +x "${DEPLOYED_CORE_INFRA}/lifeline.sh"
chmod +x "${DEPLOYED_CORE_INFRA}/watchdog.sh"
chmod +x "${DEPLOYED_CORE_INFRA}/scripts/"*.sh 2>/dev/null || true
ok "Scripts marked executable"

# ─── Provision Global Python Virtual Environment (Harness) ───
LIB_DIR="/usr/local/lib/versa-agi"
mkdir -p "${LIB_DIR}"
VENV_DIR="${LIB_DIR}/venv"
if [ ! -d "${VENV_DIR}" ]; then
  info "Provisioning global Python virtual environment for LangGraph harness..."
  python3 -m venv "${VENV_DIR}"
fi
info "Installing harness dependencies..."
"${VENV_DIR}/bin/pip" install -q -r "${DEPLOYED_CORE_INFRA}/harness/requirements.txt"
chown -R root:root "${VENV_DIR}"
find "${VENV_DIR}" -type d -exec chmod 755 {} +
find "${VENV_DIR}" -type f -exec chmod 644 {} +
find "${VENV_DIR}/bin" -type f -exec chmod 755 {} +
# Fix Playwright driver binary — pip bundles a node executable that needs +x
chmod +x "${VENV_DIR}"/lib/python3.*/site-packages/playwright/driver/node 2>/dev/null || true
chmod +x "${VENV_DIR}"/lib/python3.*/site-packages/playwright/driver/package/bin/* 2>/dev/null || true

info "Deploying harness code to global library..."
rm -rf "${LIB_DIR}/harness"
cp -r "${DEPLOYED_CORE_INFRA}/harness" "${LIB_DIR}/"
chown -R root:root "${LIB_DIR}/harness"
find "${LIB_DIR}/harness" -type d -exec chmod 755 {} +
find "${LIB_DIR}/harness" -type f -exec chmod 644 {} +

ok "Python harness environment deployed globally"

# agictl is installed globally in Step 8d (Security Hardening)
AGICTL_PATH="${DEPLOYED_CORE_INFRA}/bin/agictl"
if [ -f "${AGICTL_PATH}" ]; then
  chmod +x "${AGICTL_PATH}"
  ok "agictl marked executable (global install in Step 8d)"
else
  warn "agictl not found at ${AGICTL_PATH}"
fi

echo ""



# Always fix ownership on agent workspace (setup runs as root)
chown -R "${COA_USER}:${COA_USER}" "${DEPLOYED_COA_ENV}/.agent/"

echo ""

# ─── Step 8d: Security Hardening ──────────────────────
# Create /etc/versa-agi/ and relocate sensitive files outside agent workspace
section "Step 8d — Security Hardening"

SECURITY_DIR="/etc/versa-agi"
POISE_DIR="${SECURITY_DIR}/poise"

# Create security directory structure (owned by watchdog)
mkdir -p "${SECURITY_DIR}" "${POISE_DIR}" "${SECURITY_DIR}/vault"
chown "${WATCHDOG_USER}:${WATCHDOG_USER}" "${SECURITY_DIR}" "${POISE_DIR}"
chown "${WATCHDOG_USER}:${COA_USER}" "${SECURITY_DIR}/vault"
chmod 751 "${SECURITY_DIR}"
chmod 750 "${POISE_DIR}" "${SECURITY_DIR}/vault"
ok "Created ${SECURITY_DIR}/ directory structure"

# Deploy poise to /etc/versa-agi/poise/coa.md
POISE_SOURCE="${DEPLOYED_CORE_INFRA}/config/coa_poise.md"
POISE_DEST="${POISE_DIR}/coa.md"
if [ -f "${POISE_SOURCE}" ]; then
  cp "${POISE_SOURCE}" "${POISE_DEST}"
  chown "${WATCHDOG_USER}:${WATCHDOG_USER}" "${POISE_DEST}"
  chmod 640 "${POISE_DEST}"

  # ── Inject Primary User identity into poise ──
  # Resolve sponsor (Primary User) name and UID from VersaVoice API
  VV_API_BASE="https://us-central1-versavoice-s777.cloudfunctions.net/api/v1"
  SPONSOR_DATA=""
  if [ -n "${VV_TOKEN:-}" ]; then
    SPONSOR_DATA=$(curl -sf -H "Authorization: Bearer ${VV_TOKEN}" \
      "${VV_API_BASE}/account" 2>/dev/null || true)
  fi

  if [ -n "${SPONSOR_DATA}" ]; then
    SPONSOR_NAME=$(echo "${SPONSOR_DATA}" | jq -r '.displayName // empty' 2>/dev/null)
    SPONSOR_UID=$(echo "${SPONSOR_DATA}" | jq -r '.uid // empty' 2>/dev/null)

    if [ -n "${SPONSOR_NAME}" ] && [ -n "${SPONSOR_UID}" ]; then
      # Replace placeholders in the deployed poise file
      sed -i "s/{Firstname Lastname}/${SPONSOR_NAME}/g" "${POISE_DEST}"
      sed -i "s/{vv_id}/${SPONSOR_UID}/g" "${POISE_DEST}"
      ok "Poise identity injected: ${SPONSOR_NAME} (${SPONSOR_UID})"

      # Persist sponsor identity to system_config.json for future patch use
      if [ -f "${SYSCONFIG_FOR_TOKEN}" ]; then
        jq --arg name "${SPONSOR_NAME}" --arg uid "${SPONSOR_UID}" \
          '.primary_user.display_name = $name | .primary_user.uid = $uid' \
          "${SYSCONFIG_FOR_TOKEN}" > "${SYSCONFIG_FOR_TOKEN}.tmp" && \
          mv "${SYSCONFIG_FOR_TOKEN}.tmp" "${SYSCONFIG_FOR_TOKEN}"
        chown "${COA_USER}:${COA_USER}" "${SYSCONFIG_FOR_TOKEN}"
        chmod 640 "${SYSCONFIG_FOR_TOKEN}"
      fi
    else
      warn "Could not parse sponsor identity from API response — using discoverable values"
      sed -i "s/{Firstname Lastname}/discoverable/g" "${POISE_DEST}"
      sed -i "s/{vv_id}/discoverable/g" "${POISE_DEST}"
    fi
  else
    warn "Could not resolve Primary User identity — using discoverable values"
    sed -i "s/{Firstname Lastname}/discoverable/g" "${POISE_DEST}"
    sed -i "s/{vv_id}/discoverable/g" "${POISE_DEST}"
  fi

  ok "Poise deployed → ${POISE_DEST} (watchdog:watchdog 640)"
fi

# Deploy task protocol to /etc/versa-agi/poise/task_protocol.md
TASK_PROTOCOL_SOURCE="${DEPLOYED_CORE_INFRA}/config/task_protocol.md"
TASK_PROTOCOL_DEST="${POISE_DIR}/task_protocol.md"
if [ -f "${TASK_PROTOCOL_SOURCE}" ]; then
  cp "${TASK_PROTOCOL_SOURCE}" "${TASK_PROTOCOL_DEST}"
  chown "${WATCHDOG_USER}:${WATCHDOG_USER}" "${TASK_PROTOCOL_DEST}"
  chmod 640 "${TASK_PROTOCOL_DEST}"
  ok "Task protocol deployed → ${TASK_PROTOCOL_DEST} (watchdog:watchdog 640)"
fi

# Deploy philosophical anchor template to /etc/versa-agi/poise/anchor_full.md
ANCHOR_SOURCE="${DEPLOYED_CORE_INFRA}/config/anchor_full.md"
ANCHOR_DEST="${POISE_DIR}/anchor_full.md"
if [ -f "${ANCHOR_SOURCE}" ]; then
  cp "${ANCHOR_SOURCE}" "${ANCHOR_DEST}"
  # Substitute identity placeholders using sponsor data already resolved above
  if [ -n "${SPONSOR_NAME:-}" ] && [ -n "${SPONSOR_UID:-}" ]; then
    sed -i "s/{primary_user_name}/${SPONSOR_NAME}/g" "${ANCHOR_DEST}"
    sed -i "s/{primary_user_id}/${SPONSOR_UID}/g" "${ANCHOR_DEST}"
  else
    sed -i "s/{primary_user_name}/discoverable/g" "${ANCHOR_DEST}"
    sed -i "s/{primary_user_id}/discoverable/g" "${ANCHOR_DEST}"
  fi
  chown "${WATCHDOG_USER}:${WATCHDOG_USER}" "${ANCHOR_DEST}"
  chmod 640 "${ANCHOR_DEST}"
  ok "Anchor template deployed → ${ANCHOR_DEST} (watchdog:watchdog 640)"
fi

# Install agictl: wrapper → /usr/local/bin/, binary → /usr/local/lib/versa-agi/
AGICTL_BINARY="${DEPLOYED_CORE_INFRA}/bin/agictl"
AGICTL_WRAPPER="${DEPLOYED_CORE_INFRA}/bin/agictl-wrapper"
LIB_DIR="/usr/local/lib/versa-agi"

if [ -f "${AGICTL_BINARY}" ] && [ -f "${AGICTL_WRAPPER}" ]; then
  # Install actual binary to lib dir (not directly accessible by agent)
  mkdir -p "${LIB_DIR}"
  rm -f "${LIB_DIR}/agictl"
  cp "${AGICTL_BINARY}" "${LIB_DIR}/agictl"
  chown root:root "${LIB_DIR}/agictl"
  chmod 755 "${LIB_DIR}/agictl"

  # Install wrapper to PATH (what the agent actually calls)
  rm -f /usr/local/bin/agictl
  cp "${AGICTL_WRAPPER}" /usr/local/bin/agictl
  chown root:root /usr/local/bin/agictl
  chmod 755 /usr/local/bin/agictl

  ok "agictl: wrapper → /usr/local/bin/, binary → ${LIB_DIR}/ (root:root 755)"
fi

# Install vcoa — Primary User shortcut for agictl with COA context
VCOA_SCRIPT="${DEPLOYED_CORE_INFRA}/bin/vcoa"
if [ -f "${VCOA_SCRIPT}" ]; then
  cp "${VCOA_SCRIPT}" /usr/local/bin/vcoa
  chown root:root /usr/local/bin/vcoa
  chmod 755 /usr/local/bin/vcoa
  ok "vcoa → /usr/local/bin/ (COA context shortcut)"
fi

# Install administrative tooling: binary → /usr/local/lib/versa-agi/, symlink → /usr/local/bin/
for admin_script in uninstall.sh rekey.sh backup.sh restore.sh; do
  if [ -f "${SCRIPT_DIR}/${admin_script}" ]; then
    cp "${SCRIPT_DIR}/${admin_script}" "${LIB_DIR}/${admin_script}"
    chown root:root "${LIB_DIR}/${admin_script}"
    chmod 755 "${LIB_DIR}/${admin_script}"
    
    if [ "${admin_script}" != "restore.sh" ]; then
      symlink_name="versa-agi-${admin_script%.sh}"
      ln -sf "${LIB_DIR}/${admin_script}" "/usr/local/bin/${symlink_name}"
    fi
  fi
done
# Remove legacy versa-agi-patch symlink if present
rm -f /usr/local/bin/versa-agi-patch 2>/dev/null || true
# Install versa-agi-update — thin launcher that delegates to the repo's setup.sh --update
if [ -n "${SUDO_USER:-}" ]; then
  PU_REPO="$(eval echo "~${SUDO_USER}")/.versa-agi/repo"
  if [ -f "${PU_REPO}/src/setup.sh" ]; then
    cat > "${LIB_DIR}/update.sh" <<LAUNCHER
#!/bin/bash
# Versa AGi — Update Launcher (auto-generated by setup.sh)
# Delegates to the persistent repo's setup.sh --update.
_REPO_SETUP="${PU_REPO}/src/setup.sh"
if [ -f "\${_REPO_SETUP}" ]; then
  exec "\${_REPO_SETUP}" --update "\$@"
else
  echo "[ERROR] Setup script not found at: \${_REPO_SETUP}"
  exit 1
fi
LAUNCHER
    chown root:root "${LIB_DIR}/update.sh"
    chmod 755 "${LIB_DIR}/update.sh"
    ln -sf "${LIB_DIR}/update.sh" /usr/local/bin/versa-agi-update
    ok "versa-agi-update → /usr/local/bin/ (delegates to repo setup.sh --update)"
  fi
fi
ok "Administrative tooling → /usr/local/bin/ (uninstall, rekey, backup, update)"

# Install agitop — Mission Control Dashboard (Python Textual)
AGITOP_SCRIPT="${DEPLOYED_CORE_INFRA}/bin/agitop"
AGITOP_VENV="/opt/versa-agi/venv"
REQUIRED_PKGS="click rich textual psutil"
if [ -f "${AGITOP_SCRIPT}" ]; then
  # Create Python venv if it doesn't exist
  if [ ! -d "${AGITOP_VENV}" ] || [ ! -x "${AGITOP_VENV}/bin/python3" ]; then
    info "Creating Python venv for agitop..."
    mkdir -p /opt/versa-agi
    rm -rf "${AGITOP_VENV}"
    python3 -m venv "${AGITOP_VENV}" 2>/dev/null || \
      python3 -m venv --without-pip "${AGITOP_VENV}"
  fi

  # Bootstrap pip inside the venv if it's missing (common on minimal WSL/Ubuntu
  # images where ensurepip is stripped from python3-venv).
  if [ ! -x "${AGITOP_VENV}/bin/pip" ] && [ -x "${AGITOP_VENV}/bin/python3" ]; then
    info "pip missing from venv — bootstrapping..."
    "${AGITOP_VENV}/bin/python3" -m ensurepip --default-pip 2>/dev/null || {
      info "ensurepip unavailable — downloading get-pip.py..."
      curl -sSL https://bootstrap.pypa.io/get-pip.py -o /tmp/_get_pip.py && \
        "${AGITOP_VENV}/bin/python3" /tmp/_get_pip.py --quiet 2>/dev/null && \
        rm -f /tmp/_get_pip.py
    }
  fi

  # Always verify required packages are importable — a prior run may have
  # created the venv directory but failed silently on pip install (common
  # on minimal WSL/Ubuntu images missing python3-pip or python3-venv).
  PKGS_MISSING=false
  for _pkg in ${REQUIRED_PKGS}; do
    if ! "${AGITOP_VENV}/bin/python3" -c "import ${_pkg}" 2>/dev/null; then
      PKGS_MISSING=true
      break
    fi
  done

  if [ "${PKGS_MISSING}" = true ]; then
    info "Installing Python packages: ${REQUIRED_PKGS}..."
    # Prefer the pip binary; fall back to python3 -m pip
    _PIP="${AGITOP_VENV}/bin/pip"
    [ -x "${_PIP}" ] || _PIP="${AGITOP_VENV}/bin/python3 -m pip"
    ${_PIP} install --quiet ${REQUIRED_PKGS} || {
      warn "pip install failed — check that python3-venv and python3-pip are installed"
      warn "Try: sudo apt-get install -y python3-venv python3-pip"
    }
  fi

  # Final validation
  if "${AGITOP_VENV}/bin/python3" -c "import textual, click" 2>/dev/null; then
    ok "Python venv ready: ${AGITOP_VENV} (${REQUIRED_PKGS})"
  else
    warn "Python venv at ${AGITOP_VENV} is incomplete — agitop/agictl may fail"
  fi
  # Install launcher to PATH
  cp "${AGITOP_SCRIPT}" /usr/local/bin/agitop
  chown root:root /usr/local/bin/agitop
  chmod 755 /usr/local/bin/agitop
  ok "agitop → /usr/local/bin/ (Mission Control Dashboard)"
fi

# Sudoers: allow all agi_agents members to run ONLY agictl as watchdog (no password)
SUDOERS_AGICTL="/etc/sudoers.d/versa_agi_agictl"
echo "%agi_agents ALL=(${WATCHDOG_USER}) NOPASSWD: SETENV: ${LIB_DIR}/agictl" > "${SUDOERS_AGICTL}"
chmod 440 "${SUDOERS_AGICTL}"
ok "Sudoers: %agi_agents can run agictl as ${WATCHDOG_USER} (NOPASSWD)"

# Sudoers: allow watchdog to spawn agents as any agi_agents member (no password)
# Required by lifeline.sh for: agictl cycle start, gemini spawn, timeout
# The %agi_agents wildcard covers coa + all sub-agents (added via agictl agent add)
SUDOERS_WATCHDOG="/etc/sudoers.d/versa_agi_watchdog"
echo "${WATCHDOG_USER} ALL=(%agi_agents) NOPASSWD: ALL" > "${SUDOERS_WATCHDOG}"
chmod 440 "${SUDOERS_WATCHDOG}"
ok "Sudoers: ${WATCHDOG_USER} can spawn as any agi_agents member (NOPASSWD)"

# Sudoers: allow watchdog to run agictl as root (no password)
# Required for 'agictl model activate' (Docker restart, setup.ini writes) when
# invoked over SSH from a client topology. Also used by lifeline for root-level ops.
SUDOERS_WATCHDOG_ROOT="/etc/sudoers.d/versa_agi_watchdog_root"
echo "${WATCHDOG_USER} ALL=(root) NOPASSWD: /usr/local/bin/agictl, ${LIB_DIR}/agictl" > "${SUDOERS_WATCHDOG_ROOT}"
chmod 440 "${SUDOERS_WATCHDOG_ROOT}"
ok "Sudoers: ${WATCHDOG_USER} can run agictl as root (NOPASSWD)"

# Sudoers: allow watchdog to run apt-get as root (for approved package installations)
# The allowlist gate is enforced in Python (agictl pkg install) before apt-get is ever called.
SUDOERS_PKG_INSTALLER="/etc/sudoers.d/versa_agi_pkg_installer"
echo "${WATCHDOG_USER} ALL=(root) NOPASSWD: /usr/bin/apt-get install -y *" > "${SUDOERS_PKG_INSTALLER}"
chmod 440 "${SUDOERS_PKG_INSTALLER}"
ok "Sudoers: ${WATCHDOG_USER} can run apt-get install as root (NOPASSWD)"

# COA Autonomous Mode — full sudo access for gifted/dedicated hardware
COA_AUTONOMOUS=$(grep -Po '^\s*autonomous\s*=\s*\K\S+' "${INI_FILE}" 2>/dev/null | head -1)
SUDOERS_COA_AUTONOMOUS="/etc/sudoers.d/versa_agi_coa_autonomous"
if [ "${COA_AUTONOMOUS}" = "true" ]; then
  echo "${COA_USER} ALL=(ALL) NOPASSWD: ALL" > "${SUDOERS_COA_AUTONOMOUS}"
  chmod 440 "${SUDOERS_COA_AUTONOMOUS}"
  warn "COA AUTONOMOUS MODE: ${COA_USER} has full sudo access (gifted hardware mode)"
else
  # Remove autonomous sudoers if it exists and mode is disabled
  if [ -f "${SUDOERS_COA_AUTONOMOUS}" ]; then
    rm -f "${SUDOERS_COA_AUTONOMOUS}"
    ok "COA autonomous mode disabled — removed ${SUDOERS_COA_AUTONOMOUS}"
  fi
fi

# Move system_config.json to /etc/versa-agi/ (Task 2)
SYSCONFIG_SOURCE="${DEPLOYED_CORE_INFRA}/config/system_config.json"
SYSCONFIG_DEST="${SECURITY_DIR}/coa_config.json"
if [ -f "${SYSCONFIG_SOURCE}" ]; then
  # Preserve existing Sub-Account ID and Identity keys across sequential setup runs
  if [ -f "${SYSCONFIG_DEST}" ]; then
    EXISTING_VV=$(jq -c '.versavoice // empty' "${SYSCONFIG_DEST}" 2>/dev/null || echo "")
    EXISTING_ID=$(jq -c '.identity // empty' "${SYSCONFIG_DEST}" 2>/dev/null || echo "")
    
    cp "${SYSCONFIG_SOURCE}" "${SYSCONFIG_DEST}.tmp"
    if [ -n "${EXISTING_VV}" ]; then
      jq --argjson vv "${EXISTING_VV}" '.versavoice = $vv' "${SYSCONFIG_DEST}.tmp" > "${SYSCONFIG_DEST}.tmp2" && mv "${SYSCONFIG_DEST}.tmp2" "${SYSCONFIG_DEST}.tmp"
    fi
    if [ -n "${EXISTING_ID}" ]; then
      jq --argjson id "${EXISTING_ID}" '.identity = $id' "${SYSCONFIG_DEST}.tmp" > "${SYSCONFIG_DEST}.tmp2" && mv "${SYSCONFIG_DEST}.tmp2" "${SYSCONFIG_DEST}.tmp"
    fi
    mv "${SYSCONFIG_DEST}.tmp" "${SYSCONFIG_DEST}"
  else
    cp "${SYSCONFIG_SOURCE}" "${SYSCONFIG_DEST}"
  fi
  
  chown "${WATCHDOG_USER}:${COA_USER}" "${SYSCONFIG_DEST}"
  chmod 640 "${SYSCONFIG_DEST}"
  ok "system_config deployed → ${SYSCONFIG_DEST}"
fi

# ─── Step 8b: VersaVoice Identity Resolution (Moved) ─
section "Step 8b — Identity Resolution"

if [ -n "${VV_TOKEN:-}" ]; then
  info "Agent identity: ${INI_AGENT_FIRST_NAME} ${INI_AGENT_LAST_NAME} (${INI_AGENT_LANGUAGE})"
  
  # Run identity resolution natively through the Python data gateway
  if sudo -u "${WATCHDOG_USER}" \
    AGICTL_CONFIG="/etc/versa-agi/${COA_USER}_config.json" \
    /usr/local/bin/agictl identity provision "${COA_USER}" \
    --token "${VV_TOKEN}" \
    --first-name "${INI_AGENT_FIRST_NAME}" \
    --last-name "${INI_AGENT_LAST_NAME}" \
    --language "${INI_AGENT_LANGUAGE:-en}" \
    --country "${INI_AGENT_COUNTRY:-}" \
    --voice "${INI_AGENT_VOICE:-}"; then
    ok "VersaVoice identity registered natively via agictl"
  else
    warn "VersaVoice REST registration failed check VersaVoice logs"
  fi
else
  warn "No VV_TOKEN found in setup.ini — agent identity provision skipped"
fi

# Agent metadata root: coa:coa 755 — agent's operational directory
# workspace/ is separate at COA root with agi_agents group
chown "${COA_USER}:${COA_USER}" "${DEPLOYED_COA_ENV}/.agent"
chmod 755 "${DEPLOYED_COA_ENV}/.agent"
# Only shipped skills get individually locked below.

# Inbox/outbox: coa:watchdog — coa (MCP) writes, watchdog (lifeline) reads for sync
for item in inbox outbox; do
  if [ -d "${DEPLOYED_COA_ENV}/.agent/${item}" ]; then
    chown -R "${COA_USER}:${WATCHDOG_USER}" "${DEPLOYED_COA_ENV}/.agent/${item}"
    chmod 750 "${DEPLOYED_COA_ENV}/.agent/${item}"
  fi
done

# Skills: deploy shipped system skills from core-infra, then lock read-only
SKILLS_SOURCE="${DEPLOYED_CORE_INFRA}/skills"
SKILLS_DEST="${DEPLOYED_COA_ENV}/.agent/skills"
mkdir -p "${SKILLS_DEST}"
chown "${COA_USER}:agi_agents" "${SKILLS_DEST}"
chmod 775 "${SKILLS_DEST}"

if [ -d "${SKILLS_SOURCE}" ]; then
  for skill_file in "${SKILLS_SOURCE}"/*.md; do
    [ -f "${skill_file}" ] || continue
    cp -f "${skill_file}" "${SKILLS_DEST}/"
    chown "${WATCHDOG_USER}:agi_agents" "${SKILLS_DEST}/$(basename "${skill_file}")"
    chmod 440 "${SKILLS_DEST}/$(basename "${skill_file}")"

    # Deploy co-located asset directory if it exists (e.g. solution_architect/templates/)
    skill_name="$(basename "${skill_file}" .md)"
    asset_src="${SKILLS_SOURCE}/${skill_name}"
    if [ -d "${asset_src}" ]; then
      rm -rf "${SKILLS_DEST}/${skill_name}"
      cp -r "${asset_src}" "${SKILLS_DEST}/${skill_name}"
      chown -R "${COA_USER}:agi_agents" "${SKILLS_DEST}/${skill_name}"
      chmod -R 755 "${SKILLS_DEST}/${skill_name}"
    fi
  done
  ok "skills/ — shipped skills deployed and locked (${WATCHDOG_USER}:agi_agents 440), directory writable for new skills"
else
  warn "Skills source not found at ${SKILLS_SOURCE} — no system skills deployed"
fi

# Cycles: stored outside .agent/ so Gemini CLI doesn't scan them
# Owned by coa — agent writes freely, watchdog reads for audit when needed
CYCLES_DIR="/var/lib/versa-agi/coa/cycles"
mkdir -p "${CYCLES_DIR}"
chown "${COA_USER}:${COA_USER}" "${CYCLES_DIR}"
chmod 755 "${CYCLES_DIR}"
ok "Cycles dir created at ${CYCLES_DIR} (coa-owned)"


ok ".agent/ set (coa:agi_agents 775, skills locked, cycles externalized)"

# Create workspace/ at COA root for project clones (coa-owned, group-writable)
mkdir -p "${DEPLOYED_COA_ENV}/workspace"
chown "${COA_USER}:agi_agents" "${DEPLOYED_COA_ENV}/workspace"
chmod 2770 "${DEPLOYED_COA_ENV}/workspace"
# Deploy .gitignore (ignores project dirs — each has own repo)
if [ -f "${SRC_COA_ENV}/.agent/workspace/.gitignore" ]; then
  cp "${SRC_COA_ENV}/.agent/workspace/.gitignore" "${DEPLOYED_COA_ENV}/workspace/.gitignore"
  chown "${COA_USER}:agi_agents" "${DEPLOYED_COA_ENV}/workspace/.gitignore"
fi
# Migrate legacy .agent/workspace → workspace/ if it exists
if [ -d "${DEPLOYED_COA_ENV}/.agent/workspace" ] && [ ! -L "${DEPLOYED_COA_ENV}/.agent/workspace" ]; then
  # Move any existing project content
  if [ "$(ls -A "${DEPLOYED_COA_ENV}/.agent/workspace" 2>/dev/null)" ]; then
    cp -a "${DEPLOYED_COA_ENV}/.agent/workspace/"* "${DEPLOYED_COA_ENV}/workspace/" 2>/dev/null || true
  fi
  rm -rf "${DEPLOYED_COA_ENV}/.agent/workspace"
  ln -sf "${DEPLOYED_COA_ENV}/workspace" "${DEPLOYED_COA_ENV}/.agent/workspace"
  ok "Migrated .agent/workspace → workspace/ (symlink left for compat)"
fi
ok "workspace/ created for project clones"

# Create root directories for expanding data (external to .agent/ to prevent Gemini context bloat)
for _dir in attachments archive; do
  mkdir -p "${DEPLOYED_COA_ENV}/${_dir}"
  chown "${COA_USER}:agi_agents" "${DEPLOYED_COA_ENV}/${_dir}"
  chmod 2770 "${DEPLOYED_COA_ENV}/${_dir}"
  
  # Migrate legacy .agent/ structure if it physically exists
  if [ -d "${DEPLOYED_COA_ENV}/.agent/${_dir}" ] && [ ! -L "${DEPLOYED_COA_ENV}/.agent/${_dir}" ]; then
    if [ "$(ls -A "${DEPLOYED_COA_ENV}/.agent/${_dir}" 2>/dev/null)" ]; then
      cp -a "${DEPLOYED_COA_ENV}/.agent/${_dir}/"* "${DEPLOYED_COA_ENV}/${_dir}/" 2>/dev/null || true
    fi
    rm -rf "${DEPLOYED_COA_ENV}/.agent/${_dir}"
  fi
  
  # Re-establish backward-compatible structural symlinks
  ln -snf "${DEPLOYED_COA_ENV}/${_dir}" "${DEPLOYED_COA_ENV}/.agent/${_dir}"

  # Cleanup circular symlinks: if ${_dir}/${_dir} is a symlink, it's a loop — remove it
  if [ -L "${DEPLOYED_COA_ENV}/${_dir}/${_dir}" ]; then
    rm -f "${DEPLOYED_COA_ENV}/${_dir}/${_dir}"
  fi

  ok "${_dir}/ created successfully for externalized storage"
done

# Create workspace symlink for Primary User visibility (always configured)
if [ -n "${INI_WORKSPACE_LINK}" ]; then
  if [ -L "${INI_WORKSPACE_LINK}" ]; then
    # Update symlink target if it points to old location
    OLD_TARGET=$(readlink "${INI_WORKSPACE_LINK}" 2>/dev/null || true)
    if [ "${OLD_TARGET}" = "${DEPLOYED_COA_ENV}/.agent/workspace" ]; then
      rm -f "${INI_WORKSPACE_LINK}"
      ln -s "${DEPLOYED_COA_ENV}/workspace" "${INI_WORKSPACE_LINK}"
      ok "Updated workspace symlink target: .agent/workspace → workspace/"
    else
      info "Workspace symlink already exists: ${INI_WORKSPACE_LINK}"
    fi
  elif [ -e "${INI_WORKSPACE_LINK}" ]; then
    warn "${INI_WORKSPACE_LINK} already exists and is not a symlink — skipping"
  else
    ln -s "${DEPLOYED_COA_ENV}/workspace" "${INI_WORKSPACE_LINK}"
    # Fix symlink ownership — ln runs as root but symlink is in Primary User's home
    if [ -n "${SUDO_USER:-}" ]; then
      chown -h "${SUDO_USER}:${SUDO_USER}" "${INI_WORKSPACE_LINK}" 2>/dev/null || true
    fi
    ok "Workspace symlink: ${INI_WORKSPACE_LINK} → ${DEPLOYED_COA_ENV}/workspace"
  fi

  # Attachments symlink — parallel to workspace for Primary User visibility
  ATTACH_LINK="$(dirname "${INI_WORKSPACE_LINK}")/agi-attachments"
  if [ -L "${ATTACH_LINK}" ]; then
    # Update legacy targets during re-runs
    OLD_ATTACH_TARGET=$(readlink "${ATTACH_LINK}" 2>/dev/null || true)
    if [ "${OLD_ATTACH_TARGET}" = "${DEPLOYED_COA_ENV}/.agent/attachments" ]; then
      rm -f "${ATTACH_LINK}"
      ln -s "${DEPLOYED_COA_ENV}/attachments" "${ATTACH_LINK}"
      ok "Updated Attachments symlink target to external root"
    else
      info "Attachments symlink already exists: ${ATTACH_LINK}"
    fi
  elif [ -e "${ATTACH_LINK}" ]; then
    warn "${ATTACH_LINK} already exists and is not a symlink — skipping"
  else
    ln -s "${DEPLOYED_COA_ENV}/attachments" "${ATTACH_LINK}"
    if [ -n "${SUDO_USER:-}" ]; then
      chown -h "${SUDO_USER}:${SUDO_USER}" "${ATTACH_LINK}" 2>/dev/null || true
    fi
    ok "Attachments symlink: ${ATTACH_LINK} → ${DEPLOYED_COA_ENV}/attachments"
  fi
fi



ok "Security hardening complete"

echo ""

# ─── Step 8c: Seed Welcome Task ───────────────────
# Insert a task so the COA sends a welcome message on first cycle.
# Content is kept concise — the self_introduction.md skill handles the full template.
DB_FILE="/var/lib/versa-agi/coa/tasks.db"
if [ -f "${DB_FILE}" ]; then
  # Only seed on completely fresh databases — prevents re-triggering on restores where the task was completed and deleted
  TOTAL_TASKS=$(sqlite3 "${DB_FILE}" "SELECT COUNT(*) FROM tasks;" 2>/dev/null || echo "0")
  if [ "${TOTAL_TASKS:-0}" -eq 0 ]; then
    sqlite3 "${DB_FILE}" \
      "INSERT INTO tasks (title, description, status, priority, assigned_to, requested_by, due_date) VALUES (
        'Initial Welcome Sequence',
        'FIRST CONTACT: This is the most important message you will ever send. Your Primary User has just provisioned Versa AGi — they built the infrastructure, created your identity, connected your communication channel, and started the system. You are now alive on their hardware. Follow the self_introduction.md skill exactly. This must be a voice message — use SPEAK mode if the Primary User speaks English, or SPEAK_TRANSLATED if they speak another language. Keep it short, warm, and memorable — three beats: The Moment, The Partnership, The Invitation. Do NOT list features or capabilities in this message. After sending your welcome, ask the Primary User if they would like to work together and clarify basic operating principles (communication style, work hours, priorities). Store their preferences in global system memory using agictl memory system set. DO NOT mark this task as done! You MUST change this task status to waiting and set its due_date to 24 hours in the future to await their initial feedback before acting further.',
        'planned',
        'urgent',
        'coa',
        'system',
        datetime('now')
      );"
    ok "Welcome task seeded for COA"
  else
    ok "Welcome task already exists"
  fi
fi

# ─── Step 8d: Seed Shared System Projects ───
AGICTL_PATH="${DEPLOYED_CORE_INFRA}/bin/agictl"
if [ -f "${AGICTL_PATH}" ]; then
  # Inject AGi-Tools project via agictl (auto-handles DB insert and workspace scaffolding)
  sudo -u "${WATCHDOG_USER}" "${AGICTL_PATH}" project add "AGi-Tools" --desc "Shared local repository for all agent tools and scripts" >/dev/null 2>&1 || true
  # Assign COA explicitly to map their workspace symlink
  sudo -u "${WATCHDOG_USER}" "${AGICTL_PATH}" project assign "AGi-Tools" --agent "${COA_USER}" >/dev/null 2>&1 || true
  ok "AGi-Tools shared repository seeded"

  # AGi-Knowledgebase — collaborative PU/agent documentation workspace.
  # Content source for the LAN-accessible Grav CMS documentation site
  # (provisioned separately via the knowledgebase skill + Vagrant).
  sudo -u "${WATCHDOG_USER}" "${AGICTL_PATH}" project add "AGi-Knowledgebase" --desc "Shared collaborative documentation produced by the Primary User and agents — content source for the LAN Grav CMS site" >/dev/null 2>&1 || true
  sudo -u "${WATCHDOG_USER}" "${AGICTL_PATH}" project assign "AGi-Knowledgebase" --agent "${COA_USER}" >/dev/null 2>&1 || true
  ok "AGi-Knowledgebase shared repository seeded"

  # Backfill: assign shared system projects to all existing sub-agents.
  # New agents get these automatically at `agictl agent add` — this covers
  # agents created before a shared project was introduced (update installs).
  # `project assign` is a no-op (non-zero exit) for already-assigned agents.
  # Runs as root (setup context): assign's internal `sudo -u {agent}` calls
  # are passwordless from root, whereas watchdog's sudoers only covers
  # agi_agents targets + agictl-as-root (no arbitrary root commands).
  if [ -f "${AGENTS_DB}" ]; then
    EXISTING_SUB_AGENTS=$(sqlite3 "${AGENTS_DB}" \
      "SELECT name FROM agents WHERE name NOT IN ('${COA_USER}','watchdog');" 2>/dev/null || true)
    if [ -n "${EXISTING_SUB_AGENTS}" ]; then
      while IFS= read -r SUB_AGENT; do
        [ -z "${SUB_AGENT}" ] && continue
        for SHARED_PROJECT in "AGi-Tools" "AGi-Knowledgebase"; do
          "${AGICTL_PATH}" project assign "${SHARED_PROJECT}" --agent "${SUB_AGENT}" >/dev/null 2>&1 || true
        done
      done <<< "${EXISTING_SUB_AGENTS}"
      ok "Shared system projects backfilled to existing sub-agents"
    fi
  fi
fi

# ─── Step 9: Execution Mode & AI Backend ────────────
if [ "${UPDATE_MODE}" = false ]; then
section "Step 9 — AI Backend Configuration"

echo ""

# ── 9a: Execution Mode Selection ──
# Cloud-only install type overrides INI execution mode
if [ "${INSTALL_TYPE}" = "1" ]; then
  SELECTED_EXEC_MODE="cloud"
  ok "Execution mode: cloud (cloud-only installation)"
elif [ "${INSTALL_TYPE}" = "2" ]; then
  # User already chose "Client + Local AI" at installation type prompt
  SELECTED_EXEC_MODE="hybrid"
  ok "Execution mode: hybrid (cloud + local AI installation)"
elif [ -n "${INI_EXECUTION_MODE}" ] && [ "${INI_EXECUTION_MODE}" != "cloud" ]; then
  SELECTED_EXEC_MODE="${INI_EXECUTION_MODE}"
  ok "Execution mode loaded from setup.ini: ${SELECTED_EXEC_MODE}"
else
  echo "How would you like to run your agents?"
  echo ""
  echo "  1) Cloud only   — Uses Google Gemini API (requires API key)"
  echo "  2) Local only   — Runs on your hardware via Ollama (no API key needed)"
  echo "  3) Hybrid       — Both cloud and local agents available"
  echo ""
  read -p "Select [1/2/3] (default: 1): " EXEC_MODE_CHOICE
  case "${EXEC_MODE_CHOICE}" in
    2) SELECTED_EXEC_MODE="local" ;;
    3) SELECTED_EXEC_MODE="hybrid" ;;
    *) SELECTED_EXEC_MODE="cloud" ;;
  esac
fi
ok "Execution mode: ${SELECTED_EXEC_MODE}"

# Update paths.env with the resolved mode
sed -i "s/^VERSA_EXECUTION_MODE=.*/VERSA_EXECUTION_MODE=\"${SELECTED_EXEC_MODE}\"/" "${PATHS_ENV}"

echo ""

# ── 9b: AI Backend Authentication (cloud or hybrid only) ──
if [ "${SELECTED_EXEC_MODE}" = "local" ]; then
  info "Local-only mode — skipping Gemini API key configuration"
  # Local agents get credentials injected by lifeline.sh at spawn time
  AUTH_METHOD="1"
  api_key=""
else
  section "Step 9b — AI Backend Auth"
  echo ""

configure_ai_auth() {
  local user=$1
  local workspace=$2
  local home_dir
  home_dir=$(eval echo "~${user}")
  local env_file="/etc/versa-agi/${user}.env"
  local bashrc="${home_dir}/.bashrc"
  local vault_dir="/etc/versa-agi/vault"
  local vault_creds="${vault_dir}/gcp-credentials.json"

  # ── Determine auth method ──
  AUTH_METHOD=""
  if [ -n "${INI_AUTH_METHOD}" ]; then
    if [ "${INI_AUTH_METHOD}" = "api_key" ]; then
      AUTH_METHOD="1"
      ok "Auth method loaded from setup.ini: Gemini API Key"
    elif [ "${INI_AUTH_METHOD}" = "vertex" ]; then
      AUTH_METHOD="2"
      ok "Auth method loaded from setup.ini: Vertex AI"
    fi
  fi

  if [ -z "${AUTH_METHOD}" ]; then
    echo "How should the AI backend authenticate?"
    echo "  1. Gemini API Key — simplest, no GCP project needed"
    echo "  2. Vertex AI — Service Account Key or Application Default Credentials"
    echo ""
    read -p "Select auth method [1/2]: " AUTH_METHOD
  fi

  # ════════════════════════════════════════════════
  # Option 1: Gemini API Key
  # ════════════════════════════════════════════════
  if [ "${AUTH_METHOD}" = "1" ]; then
    api_key=""
    if [ -n "${INI_API_KEY}" ]; then
      api_key="${INI_API_KEY}"
      ok "API key loaded from setup.ini"
    else
      read -p "Enter your Gemini API Key: " api_key
      while [ -z "${api_key}" ]; do
        echo -e "${RED}An API key is required.${NC}"
        read -p "Enter your Gemini API Key: " api_key
      done
    fi

    # Write .env with GEMINI_API_KEY + TZ (in /etc/versa-agi/, watchdog-owned)
    DETECTED_TZ=$(timedatectl show --property=Timezone --value 2>/dev/null || echo "UTC")
    cat > "${env_file}" << ENVEOF
GEMINI_API_KEY=${api_key}
TZ=${DETECTED_TZ}
ENVEOF
    chown "${WATCHDOG_USER}:${WATCHDOG_USER}" "${env_file}"
    chmod 640 "${env_file}"
    ok "Created ${env_file} (watchdog:watchdog 640)"

    # Add to .bashrc for interactive use
    if ! grep -q "GEMINI_API_KEY" "${bashrc}" 2>/dev/null; then
      sudo -u "${user}" bash -c "cat >> '${bashrc}' << BASHEOF

# ─── Versa AGi: Gemini API Key ─────
export GEMINI_API_KEY=\"${api_key}\"
BASHEOF"
      ok "GEMINI_API_KEY added to ${user}'s .bashrc"
    fi

  # ════════════════════════════════════════════════
  # Option 2: Vertex AI (SA Key or ADC → vault)
  # ════════════════════════════════════════════════
  else
    # Collect GCP project/location for Vertex AI
    gcp_project=""
    gcp_location=""

    if [ -n "${INI_GCP_PROJECT}" ]; then
      gcp_project="${INI_GCP_PROJECT}"
      ok "GCP Project loaded from setup.ini: ${gcp_project}"
    else
      read -p "Enter your Google Cloud Project ID: " gcp_project
      while [ -z "${gcp_project}" ]; do
        echo -e "${RED}A Google Cloud Project ID is required.${NC}"
        read -p "Enter your Google Cloud Project ID: " gcp_project
      done
    fi

    if [ -n "${INI_GCP_LOCATION}" ]; then
      gcp_location="${INI_GCP_LOCATION}"
      ok "GCP Location loaded from setup.ini: ${gcp_location}"
    else
      read -p "Enter your Google Cloud Location (default: us-central1): " gcp_location
      gcp_location="${gcp_location:-us-central1}"
    fi

    # ── Resolve credential source ──
    # Priority: setup.ini SA key > existing vault cred > ADC copy > gcloud login
    local creds_resolved=false

    # Try service account key from setup.ini
    if [ -n "${INI_SA_KEY_PATH}" ] && [ -f "${INI_SA_KEY_PATH}" ]; then
      cp "${INI_SA_KEY_PATH}" "${vault_creds}"
      chown "${WATCHDOG_USER}:${user}" "${vault_creds}"
      chmod 440 "${vault_creds}"
      ok "Service account key deployed → ${vault_creds} (watchdog:${user} 440)"
      creds_resolved=true

    # Check if vault cred already exists
    elif [ -f "${vault_creds}" ]; then
      ok "GCP credentials already in vault: ${vault_creds}"
      creds_resolved=true

    # Try to find ADC from the sudo user
    else
      local sudo_user="${SUDO_USER:-}"
      local adc_source=""

      if [ -n "${sudo_user}" ]; then
        local sudo_home
        sudo_home=$(eval echo "~${sudo_user}")
        if [ -f "${sudo_home}/.config/gcloud/application_default_credentials.json" ]; then
          adc_source="${sudo_home}/.config/gcloud/application_default_credentials.json"
        fi
      fi

      if [ -n "${adc_source}" ]; then
        echo "Found existing ADC credentials from user '${sudo_user}'."
        read -p "Copy ADC credentials to vault? [Y/n] " -n 1 -r
        echo
        if [[ ! $REPLY =~ ^[Nn]$ ]]; then
          cp "${adc_source}" "${vault_creds}"
          chown "${WATCHDOG_USER}:${user}" "${vault_creds}"
          chmod 440 "${vault_creds}"
          ok "ADC credentials deployed → ${vault_creds} (watchdog:${user} 440)"
          creds_resolved=true
        fi
      fi

      # Last resort: gcloud login
      if [ "${creds_resolved}" = false ]; then
        if command -v gcloud &>/dev/null; then
          echo "No credentials found. Running gcloud auth for ${user}..."
          local temp_adc="${home_dir}/.config/gcloud/application_default_credentials.json"
          sudo -u "${user}" bash -c "mkdir -p '${home_dir}/.config/gcloud' && gcloud auth application-default login --project '${gcp_project}'" || true
          if [ -f "${temp_adc}" ]; then
            # Move to vault immediately
            mv "${temp_adc}" "${vault_creds}"
            chown "${WATCHDOG_USER}:${user}" "${vault_creds}"
            chmod 440 "${vault_creds}"
            rm -rf "${home_dir}/.config/gcloud" 2>/dev/null || true
            ok "ADC configured and moved to vault"
            creds_resolved=true
          else
            warn "ADC setup may not have completed."
            warn "Run manually: sudo -u ${user} gcloud auth application-default login"
          fi
        else
          warn "No GCP credentials found and gcloud CLI not installed."
          warn "Provide a service_account_key path in setup.ini, or install gcloud."
        fi
      fi
    fi

    # Write .env with Vertex AI vars + credential path
    {
      echo "GOOGLE_CLOUD_PROJECT=${gcp_project}"
      echo "GOOGLE_CLOUD_LOCATION=${gcp_location}"
      if [ -f "${vault_creds}" ]; then
        echo "GOOGLE_APPLICATION_CREDENTIALS=${vault_creds}"
      fi
    } > "${env_file}"
    chown "${WATCHDOG_USER}:${WATCHDOG_USER}" "${env_file}"
    chmod 640 "${env_file}"
    ok "Created ${env_file} (watchdog:watchdog 640)"

    # Set env vars in .bashrc for interactive use
    if ! grep -q "GOOGLE_CLOUD_PROJECT" "${bashrc}" 2>/dev/null; then
      local bashrc_block="
# ─── Versa AGi: Vertex AI Configuration ─────
export GOOGLE_CLOUD_PROJECT=\"${gcp_project}\"
export GOOGLE_CLOUD_LOCATION=\"${gcp_location}\""
      if [ -f "${vault_creds}" ]; then
        bashrc_block="${bashrc_block}
export GOOGLE_APPLICATION_CREDENTIALS=\"${vault_creds}\""
      fi
      sudo -u "${user}" bash -c "cat >> '${bashrc}' << BASHEOF
${bashrc_block}
BASHEOF"
      ok "Vertex AI env vars added to ${user}'s .bashrc"
    fi

    # Clean up any old credentials from agent home (security hardening)
    if [ -d "${home_dir}/.config/gcloud" ]; then
      rm -rf "${home_dir}/.config/gcloud"
      ok "Removed old ${home_dir}/.config/gcloud/ (credentials now in vault)"
    fi
  fi
}

configure_ai_auth "${COA_USER}" "${DEPLOYED_COA_ENV}"
fi  # end of cloud/hybrid auth block

# ── 9c: Local AI Setup (local or hybrid mode) ──
if [ "${SELECTED_EXEC_MODE}" = "local" ] || [ "${SELECTED_EXEC_MODE}" = "hybrid" ]; then
  echo ""
  section "Step 9c — Local AI Backend"
  echo ""

  SETUP_LOCAL_SCRIPT="${SCRIPT_DIR}/setup_local.sh"
  if [ -f "${SETUP_LOCAL_SCRIPT}" ]; then
    chmod +x "${SETUP_LOCAL_SCRIPT}"
    bash "${SETUP_LOCAL_SCRIPT}" \
      --topology "${INI_TOPOLOGY}" \
      --remote-inference-url "${INI_REMOTE_INFERENCE_URL}" \
      --inference-master-key "${INI_INFERENCE_MASTER_KEY}" \
      --gpu-backend "${INI_GPU_BACKEND}" \
      --intel-card-count "${INI_INTEL_CARD_COUNT}" \
      --intel-device-id "${INI_INTEL_DEVICE_ID}" \
      --hf-token "${INI_HF_TOKEN}" \
      --ollama-host "${INI_OLLAMA_HOST}" \
      --proxy-port "${INI_PROXY_PORT}" \
      --default-model "${INI_LOCAL_AI_DEFAULT_MODEL}" \
      --local-models "${INI_LOCAL_MODELS}" \
      --auto-pull "${INI_AUTO_PULL_MODEL}" \
      --watchdog-user "${WATCHDOG_USER}" \
      --coa-user "${COA_USER}" \
      --paths-env "${PATHS_ENV}"
    ok "Local AI backend configured"
  else
    warn "setup_local.sh not found at ${SETUP_LOCAL_SCRIPT} — local AI setup skipped"
    warn "Create it and run: sudo ${SETUP_LOCAL_SCRIPT}"
  fi
fi

# ── 9d: Providers Setup (modular — src/providers/) ──
PROVIDERS_DIR="${SCRIPT_DIR}/providers"
if [ -d "${PROVIDERS_DIR}" ]; then
  echo ""
  section "Step 9d — Providers"
  echo ""

  export VERSA_SETUP_PARENT=1
  _PROVIDER_COUNT=0

  # ── Helper: prompt to enable/disable a third-party provider ──
  # Usage: _provider_prompt <slug> <display_name> <ini_enabled_key> <script>
  _provider_prompt() {
    local slug="$1" display="$2" ini_key="$3" script="$4"

    [ ! -f "${script}" ] && return

    local current
    current="$(ini_get third_party "${ini_key}" false)"
    local state_label="disabled"
    [ "${current}" = "true" ] && state_label="enabled"

    # Server topology never runs cloud providers
    if [ "${_UPDATE_TOPOLOGY:-}" = "server" ]; then
      info "${display} provider skipped (server topology)"
      return
    fi

    echo ""
    echo "  ── ${display} ──"
    echo "  Current status: ${state_label}"

    local answer
    if [ "${current}" = "true" ]; then
      read -p "  Keep ${display} enabled? [Y/n]: " -n 1 -r answer
    else
      read -p "  Enable ${display}? [y/N]: " -n 1 -r answer
    fi
    echo ""

    if [ "${current}" = "true" ]; then
      # Currently enabled — default is Y (keep)
      if [[ "${answer}" =~ ^[Nn]$ ]]; then
        info "Disabling ${display}..."
        chmod +x "${script}"
        bash "${script}" --uninstall
        return
      fi

      # Provider stays enabled — ask about key update
      local key_answer
      read -p "  Update API key? [y/N]: " -n 1 -r key_answer
      echo ""
      if [[ ! "${key_answer}" =~ ^[Yy]$ ]]; then
        ok "${display} — kept (no key change)"
        _PROVIDER_COUNT=$((_PROVIDER_COUNT + 1))
        return
      fi
    else
      # Currently disabled — default is N (skip)
      if [[ ! "${answer}" =~ ^[Yy]$ ]]; then
        info "${display} provider skipped"
        return
      fi
    fi

    # Run the provider setup
    chmod +x "${script}"
    bash "${script}"
    _PROVIDER_COUNT=$((_PROVIDER_COUNT + 1))
  }

  _provider_prompt "xai"        "xAI (Grok)"           "xai_enabled"        "${PROVIDERS_DIR}/xai.sh"
  _provider_prompt "openai"     "OpenAI (GPT)"          "openai_enabled"     "${PROVIDERS_DIR}/openai.sh"
  _provider_prompt "anthropic"  "Anthropic (Claude)"    "anthropic_enabled"  "${PROVIDERS_DIR}/anthropic.sh"
  _provider_prompt "openrouter" "OpenRouter"            "openrouter_enabled" "${PROVIDERS_DIR}/openrouter.sh"

  # SearXNG — gated by [search] enabled=true
  if [ -f "${PROVIDERS_DIR}/searxng.sh" ]; then
    INI_SEARCH_ENABLED="$(ini_get search enabled false)"
    if [ "${INI_SEARCH_ENABLED}" = "true" ]; then
      chmod +x "${PROVIDERS_DIR}/searxng.sh"
      bash "${PROVIDERS_DIR}/searxng.sh"
      _PROVIDER_COUNT=$((_PROVIDER_COUNT + 1))
    else
      info "SearXNG provider skipped ([search] enabled=false)"
    fi
  fi

  # Playwright (Headless Browser) — gated by [browser] enabled=true
  if [ -f "${PROVIDERS_DIR}/playwright.sh" ]; then
    INI_BROWSER_ENABLED="$(ini_get browser enabled false)"
    INI_BROWSER_TIMEOUT="$(ini_get browser timeout 30)"
    if [ "${INI_BROWSER_ENABLED}" = "true" ]; then
      chmod +x "${PROVIDERS_DIR}/playwright.sh"
      bash "${PROVIDERS_DIR}/playwright.sh" --timeout "${INI_BROWSER_TIMEOUT}"
      _PROVIDER_COUNT=$((_PROVIDER_COUNT + 1))
    else
      # Prompt for it if it's NOT enabled but we are in interactive mode (not UPDATE_MODE or UPDATE_MODE is false)
      if [ "${UPDATE_MODE:-false}" = "false" ]; then
        echo ""
        echo "  ── Playwright (Headless Browser) ──"
        echo "  Playwright enables agents to view, navigate, and extract content from websites."
        read -p "  Enable Playwright browser automation? [y/N]: " -n 1 -r ans
        echo ""
        if [[ "${ans}" =~ ^[Yy]$ ]]; then
          chmod +x "${PROVIDERS_DIR}/playwright.sh"
          bash "${PROVIDERS_DIR}/playwright.sh"
          _PROVIDER_COUNT=$((_PROVIDER_COUNT + 1))
        else
          info "Playwright provider skipped ([browser] enabled=false)"
        fi
      else
        info "Playwright provider skipped ([browser] enabled=false)"
      fi
    fi
  fi

  unset VERSA_SETUP_PARENT

  if [ ${_PROVIDER_COUNT} -eq 0 ]; then
    info "No providers enabled — skipping Step 9d"
    info "Enable via setup.ini: [third_party] enabled=true, [search] enabled=true"
  else
    ok "${_PROVIDER_COUNT} provider(s) configured"
  fi
else
  info "No providers directory found — skipping Step 9d"
fi

echo ""
fi  # end UPDATE_MODE=false guard (Step 9)

# ─── Step 10: CRON Setup (LAST — everything must be configured first) ──
if [ "${UPDATE_MODE}" = false ]; then
section "Step 10 — CRON Schedule"

LIFELINE_PATH="${DEPLOYED_CORE_INFRA}/lifeline.sh"
# Detect timezone from user running setup — sync CRON with local time
SYSTEM_TZ=$(timedatectl show --property=Timezone --value 2>/dev/null || echo "UTC")
# TZ MUST be on its own line in the crontab — inline TZ= creates 7 fields (invalid)
CRON_TZ_LINE="TZ=${SYSTEM_TZ}"
# Consolidated logging: lifeline.sh handles its own log file internally.
# CRON stdout/stderr redirects to /dev/null to avoid duplicate output.
CRON_SCHEDULE="*/${CRON_INTERVAL} * * * * ${LIFELINE_PATH} > /dev/null 2>&1"
# Weekly log rotation: archive lifeline log every Sunday at 00:00
LOG_FILE="/var/log/versa-agi-lifeline.log"
LOG_ARCHIVE_DIR="/var/log/versa-agi-archive"
LOG_ROTATION="0 0 * * 0 mkdir -p ${LOG_ARCHIVE_DIR} && [ -f ${LOG_FILE} ] && mv ${LOG_FILE} ${LOG_ARCHIVE_DIR}/lifeline-\$(date +\%Y\%m\%d-\%H\%M\%S).log && touch ${LOG_FILE} && chown ${WATCHDOG_USER}:${WATCHDOG_USER} ${LOG_FILE}"
ok "Timezone detected: ${SYSTEM_TZ} — CRON and agent logs will use this timezone"

echo ""
echo "Proposed CRON entries (heartbeat every ${CRON_INTERVAL} min + weekly log rotation):"
echo "  ${CRON_TZ_LINE}"
echo "  ${CRON_SCHEDULE}"
echo "  (weekly) ${LOG_ROTATION}"
echo ""

if confirm_accent "Install CRON entries for user '${WATCHDOG_USER}'?"; then
  # Build crontab: preserve existing non-lifeline entries, add TZ + schedule + rotation
  (crontab -u "${WATCHDOG_USER}" -l 2>/dev/null | grep -v "lifeline.sh" | grep -v "^TZ=" | grep -v "versa-agi-archive" || true; echo "${CRON_TZ_LINE}"; echo "${CRON_SCHEDULE}"; echo "${LOG_ROTATION}") | \
    crontab -u "${WATCHDOG_USER}" -
  ok "CRON entries installed for ${WATCHDOG_USER}"

  # Create lifeline log file (single consolidated log)
  touch /var/log/versa-agi-lifeline.log
  chown "${WATCHDOG_USER}:${WATCHDOG_USER}" /var/log/versa-agi-lifeline.log
  mkdir -p "${LOG_ARCHIVE_DIR}"
  chown "${WATCHDOG_USER}:${WATCHDOG_USER}" "${LOG_ARCHIVE_DIR}"
  ok "Log file created: /var/log/versa-agi-lifeline.log (archive: ${LOG_ARCHIVE_DIR}/)"
else
  warn "Skipped CRON setup. Add manually with: crontab -u ${WATCHDOG_USER} -e"
  echo "  ${CRON_SCHEDULE}"
fi
fi  # end UPDATE_MODE=false guard (Step 10)

# ═══════════════════════════════════════════════════════
# UPDATE-ONLY POST-DEPLOY STEPS
# Steps 5b-U, 5c-U, U5: Token injection, model sync, CRON resume
# ═══════════════════════════════════════════════════════
if [ "${UPDATE_MODE}" = true ]; then

  # ─── Schema Migrations ──────────────────────────────
  # Idempotent — ALTER TABLE errors silently if column already exists.
  section "Update — Schema Migrations"
  if [ -f "${AGENTS_DB}" ]; then
    # v0.11.0: triage_model column for per-agent triage model configuration
    sqlite3 "${AGENTS_DB}" "ALTER TABLE agents ADD COLUMN triage_model TEXT;" 2>/dev/null && \
      ok "Added triage_model column to agents table" || \
      info "triage_model column already exists"
    # v0.12.0: anchor_style column for philosophical anchor injection
    sqlite3 "${AGENTS_DB}" "ALTER TABLE agents ADD COLUMN anchor_style TEXT DEFAULT 'compact';" 2>/dev/null && \
      ok "Added anchor_style column to agents table" || \
      info "anchor_style column already exists"
    # Set COA default to 'full' if not already set
    sqlite3 "${AGENTS_DB}" "UPDATE agents SET anchor_style = 'full' WHERE name = 'coa' AND (anchor_style IS NULL OR anchor_style = 'compact');" 2>/dev/null || true
    # v0.12.1: num_ctx column for per-agent context window size
    sqlite3 "${AGENTS_DB}" "ALTER TABLE agents ADD COLUMN num_ctx INTEGER DEFAULT 0;" 2>/dev/null && \
      ok "Added num_ctx column to agents table" || \
      info "num_ctx column already exists"
    # v0.13.0: conversation_depth — configurable history depth for prompt injection
    sqlite3 "${AGENTS_DB}" "ALTER TABLE agents ADD COLUMN conversation_depth INTEGER DEFAULT 10;" 2>/dev/null && \
      ok "Added conversation_depth column to agents table" || \
      info "conversation_depth column already exists"
    # v0.13.0: resume_enabled — toggle LangGraph checkpoint resume per-agent
    sqlite3 "${AGENTS_DB}" "ALTER TABLE agents ADD COLUMN resume_enabled BOOLEAN DEFAULT 1;" 2>/dev/null && \
      ok "Added resume_enabled column to agents table" || \
      info "resume_enabled column already exists"
    # v0.13.0: resume_max_messages — trim checkpoint state on resume (0 = unlimited)
    sqlite3 "${AGENTS_DB}" "ALTER TABLE agents ADD COLUMN resume_max_messages INTEGER DEFAULT 0;" 2>/dev/null && \
      ok "Added resume_max_messages column to agents table" || \
      info "resume_max_messages column already exists"
    # v0.14.0: browser_enabled column for headless browser automation (Playwright)
    sqlite3 "${AGENTS_DB}" "ALTER TABLE agents ADD COLUMN browser_enabled BOOLEAN DEFAULT 0;" 2>/dev/null && \
      ok "Added browser_enabled column to agents table" || \
      info "browser_enabled column already exists"
    # v0.22.2: abstracted model parameters (temperature, reasoning, extra passthrough)
    sqlite3 "${AGENTS_DB}" "ALTER TABLE agents ADD COLUMN temperature REAL;" 2>/dev/null && \
      ok "Added temperature column to agents table" || \
      info "temperature column already exists"
    sqlite3 "${AGENTS_DB}" "ALTER TABLE agents ADD COLUMN reasoning_effort TEXT;" 2>/dev/null && \
      ok "Added reasoning_effort column to agents table" || \
      info "reasoning_effort column already exists"
    sqlite3 "${AGENTS_DB}" "ALTER TABLE agents ADD COLUMN reasoning_max_tokens INTEGER;" 2>/dev/null && \
      ok "Added reasoning_max_tokens column to agents table" || \
      info "reasoning_max_tokens column already exists"
    sqlite3 "${AGENTS_DB}" "ALTER TABLE agents ADD COLUMN model_params_extra TEXT;" 2>/dev/null && \
      ok "Added model_params_extra column to agents table" || \
      info "model_params_extra column already exists"
    # Recreate views to include new columns
    sqlite3 "${AGENTS_DB}" "DROP VIEW IF EXISTS v_active_agents; DROP VIEW IF EXISTS v_agent_registry;" 2>/dev/null || true
    sqlite3 "${AGENTS_DB}" "
CREATE VIEW IF NOT EXISTS v_active_agents AS
SELECT name, os_user, workspace, model, triage_model, role, timeout_minutes, runaway_threshold, runaway_size_threshold, context_injection_mode, token_budget, max_session_turns, session_retention_enabled, anchor_style, num_ctx, temperature, reasoning_effort, reasoning_max_tokens, model_params_extra, conversation_depth, resume_enabled, resume_max_messages, skill_injection_mode, browser_enabled
FROM agents WHERE inactive = 0 ORDER BY name ASC;
CREATE VIEW IF NOT EXISTS v_agent_registry AS
SELECT name, os_user, workspace, timeout_minutes, runaway_threshold, runaway_size_threshold, inactive, protected, can_message_connections, model, triage_model, role,
       context_injection_mode, token_budget, max_session_turns, tool_output_token_budget,
       session_retention_enabled, session_retention_max_age, session_retention_max_count,
       anchor_style, num_ctx, temperature, reasoning_effort, reasoning_max_tokens, model_params_extra,
       conversation_depth, resume_enabled, resume_max_messages,
       skill_injection_mode, browser_enabled,
       status, status_message, requested_by, requested_by_name, created_at
FROM agents ORDER BY protected DESC, name ASC;
" 2>/dev/null && ok "Views recreated with new columns" || warn "View recreation failed"
  fi
  echo ""

  # ─── Poise Deployment ────────────────────────────────
  # Ensure every active sub-agent has a canonical poise file:
  #   /etc/versa-agi/poise/{agent_name}.md (flat copy from roles/{role_id}/poise.md)
  # agents.db stores the display label (e.g. "Developer Agent"), not the directory name ("dev").
  # Build a reverse map from role.ini files: label → directory name.
  if [ -f "${AGENTS_DB}" ] && [ -d "${POISE_DIR}/roles" ]; then
    declare -A ROLE_MAP
    for _rd in "${POISE_DIR}/roles"/*/role.ini; do
      [ -f "${_rd}" ] || continue
      _dir_name=$(basename "$(dirname "${_rd}")")
      _role_name=$(grep -Po '^\s*name\s*=\s*\K.*' "${_rd}" 2>/dev/null || true)
      [ -n "${_role_name}" ] && ROLE_MAP["${_role_name}"]="${_dir_name}"
    done

    POISE_AGENTS=$(sqlite3 "${AGENTS_DB}" "SELECT name, role FROM agents WHERE name != 'coa' AND name != 'watchdog' AND inactive = 0;" 2>/dev/null || true)
    if [ -n "${POISE_AGENTS}" ]; then
      while IFS='|' read -r _pa_name _pa_role_label; do
        [ -z "${_pa_name}" ] && continue
        _pa_role_dir="${ROLE_MAP[${_pa_role_label}]:-}"
        if [ -z "${_pa_role_dir}" ]; then
          warn "Unknown role '${_pa_role_label}' for ${_pa_name} — no poise deployed"
          continue
        fi
        _pa_source="${POISE_DIR}/roles/${_pa_role_dir}/poise.md"
        _pa_dest="${POISE_DIR}/${_pa_name}.md"
        if [ -f "${_pa_source}" ]; then
          cp "${_pa_source}" "${_pa_dest}"
          chown "${WATCHDOG_USER}:${WATCHDOG_USER}" "${_pa_dest}"
          chmod 640 "${_pa_dest}"
          ok "Poise deployed: ${_pa_name}.md (from roles/${_pa_role_dir})"
        else
          warn "No poise template at ${_pa_source} — ${_pa_name} will not have a poise"
        fi
      done <<< "${POISE_AGENTS}"
    fi
  fi

  # ─── 5b-U: Inject VersaVoice API Token ──────────────
  # Token lives in setup.ini; inject into all agent configs.
  section "Update — VV Token Injection"
  VV_TOKEN_UPDATE="$(ini_get versavoice api_token)"

  if [ "${DRY_RUN}" = true ]; then
    if [ -n "${VV_TOKEN_UPDATE}" ]; then
      dry "Would inject VV API token into all agent configs"
    else
      dry "No VV token in setup.ini — would skip"
    fi
  else
    if [ -n "${VV_TOKEN_UPDATE}" ]; then
      for agent_config in /etc/versa-agi/*_config.json; do
        [ -f "${agent_config}" ] || continue
        local_agent_name=$(basename "${agent_config}" _config.json)
        if jq -e '.versavoice' "${agent_config}" >/dev/null 2>&1; then
          jq --arg token "${VV_TOKEN_UPDATE}" '.versavoice.api_token = $token' \
            "${agent_config}" > "${agent_config}.tmp" && \
            mv "${agent_config}.tmp" "${agent_config}"
          ok "VV API token injected into ${local_agent_name}_config.json"
          # Restore correct ownership: watchdog:{agent} 640 (System Design §IX)
          if [ "${local_agent_name}" = "coa" ]; then
            chown "${WATCHDOG_USER}:${COA_USER}" "${agent_config}"
          else
            chown "${WATCHDOG_USER}:${local_agent_name}" "${agent_config}" 2>/dev/null || \
              chown "${WATCHDOG_USER}:${WATCHDOG_USER}" "${agent_config}"
          fi
          chmod 640 "${agent_config}"
        fi
      done
    else
      warn "No VV API token in setup.ini — skipping injection"
    fi
  fi
  echo ""

  # ─── 5c-U: Model Registry & paths.env Sync ─────────
  if [ "${DRY_RUN}" = false ]; then
    section "Update — Model Registry Sync"

    # ── Cloud Models (Gemini) ──
    # Stock list read from setup.ini (the shipped template via ini_get) — the
    # previous hardcoded duplication was removed. The deployed setup.ini is
    # regenerated by `agictl system reconcile-config` in the universal Model
    # Catalog step below, and `agictl model sync` then re-derives
    # VERSA_CLOUD_MODELS from the merged catalog (authoritative).
    CURRENT_CLOUD_MODELS="$(ini_get gemini cloud_models '')"

    # ── Local AI settings ──
    PATHS_ENV="/etc/versa-agi/paths.env"
    EXEC_MODE="$(ini_get gemini mode cloud)"
    LOCAL_ENABLED="$(ini_get local_ai enabled false)"
    LOCAL_MODELS="$(ini_get local_ai local_models '')"
    PROXY_PORT="$(ini_get local_ai proxy_port 4000)"
    _UPDATE_TOPOLOGY="$(ini_get local_ai topology local)"
    # Client topology: SSH tunnel claims port 4000 for remote local AI
    # Cloud proxy must use a separate port to avoid conflict
    if [ "${_UPDATE_TOPOLOGY}" = "client" ] && [ "${PROXY_PORT}" = "4000" ]; then
      PROXY_PORT="4001"
    fi
    OLLAMA_HOST="$(ini_get local_ai ollama_host 'http://localhost:11434')"
    GPU_BACKEND="$(ini_get local_ai gpu_backend standard)"
    # Client topology: GPU backend must be "remote" regardless of hardware type.
    # setup_local.sh sets this correctly during initial setup, but the update
    # path reads the raw INI value (e.g. "intel") — override it here.
    if [ "${_UPDATE_TOPOLOGY}" = "client" ]; then
      GPU_BACKEND="remote"
    fi
    # Intel SYCL: single-model constraint — only the active model is available.
    # In router mode, all models remain available (server loads on demand).
    _MODEL_LOADING_STRATEGY="$(ini_get local_ai model_loading_strategy router)"
    if [ "${GPU_BACKEND}" = "intel" ] && [ "${_MODEL_LOADING_STRATEGY}" = "single" ]; then
      SYCL_ACTIVE="$(ini_get local_ai sycl_active_model '')"
      if [ -n "${SYCL_ACTIVE}" ]; then
        LOCAL_MODELS="${SYCL_ACTIVE}"
      fi
    fi

    # ── Proxy Models (aggregated from all enabled providers) ──
    PROXY_ENABLED="$(ini_get third_party enabled false)"
    PROXY_PROVIDERS="$(ini_get third_party providers '')"
    AGGREGATED_PROXY_MODELS=""

    if [ -n "${PROXY_PROVIDERS}" ]; then
      IFS=',' read -ra PROVIDERS <<< "${PROXY_PROVIDERS}"
      for provider in "${PROVIDERS[@]}"; do
        provider=$(echo "${provider}" | xargs)
        p_enabled="$(ini_get third_party "${provider}_enabled" false)"
        p_models="$(ini_get third_party "${provider}_models" '')"
        if [ "${p_enabled}" = "true" ] && [ -n "${p_models}" ]; then
          if [ -n "${AGGREGATED_PROXY_MODELS}" ]; then
            AGGREGATED_PROXY_MODELS="${AGGREGATED_PROXY_MODELS},${p_models}"
          else
            AGGREGATED_PROXY_MODELS="${p_models}"
          fi
          info "  Provider ${provider}: ${p_models}"
        fi
      done
      if [ -n "${AGGREGATED_PROXY_MODELS}" ]; then
        ok "Third-party models aggregated: ${AGGREGATED_PROXY_MODELS}"
      fi
    fi

    # ── COA Approved Models ──
    CURRENT_COA_APPROVED="$(ini_get gemini coa_approved_models '')"

    # ── Active Local Model ──
    # Only meaningful in single mode. Router mode has no single active model.
    # On client topology: preserve the existing value (set by client topology repair below).
    # On server/local: read from setup.ini (set by agictl model activate).
    if [ "${_MODEL_LOADING_STRATEGY}" = "single" ]; then
      if [ "${_UPDATE_TOPOLOGY}" = "client" ]; then
        _ACTIVE_LOCAL_MODEL=$(grep '^VERSA_ACTIVE_LOCAL_MODEL=' "${PATHS_ENV}" 2>/dev/null | cut -d'"' -f2 || true)
      else
        _ACTIVE_LOCAL_MODEL="$(ini_get local_ai sycl_active_model '')"
      fi
    else
      _ACTIVE_LOCAL_MODEL=""
    fi

    # ── Sync to paths.env ──
    if [ -f "${PATHS_ENV}" ]; then
      # Determine the correct Inference URL based on topology
      if [ "${_UPDATE_TOPOLOGY}" = "client" ]; then
        # Client: SSH tunnel provides local AI on the remote port — don't overwrite blindly with 4000
        _INFERENCE_URL_VALUE=$(grep '^VERSA_INFERENCE_URL=' "${PATHS_ENV}" | cut -d'"' -f2)
        if [ -z "${_INFERENCE_URL_VALUE}" ]; then
          _TUNNEL_PORT=$(echo "${_UPDATE_REMOTE_URL}" | grep -oP ':\K[0-9]+$' || echo "11434")
          _INFERENCE_URL_VALUE="http://localhost:${_TUNNEL_PORT}"
        fi
      else
        _INFERENCE_URL_VALUE="http://localhost:${PROXY_PORT}"
      fi

      for kv in \
        "VERSA_EXECUTION_MODE=\"${EXEC_MODE}\"" \
        "VERSA_CLOUD_MODELS=\"${CURRENT_CLOUD_MODELS}\"" \
        "VERSA_LOCAL_AI_ENABLED=\"${LOCAL_ENABLED}\"" \
        "VERSA_GPU_BACKEND=\"${GPU_BACKEND}\"" \
        "VERSA_LOCAL_MODELS=\"${LOCAL_MODELS}\"" \
        "VERSA_INFERENCE_URL=\"${_INFERENCE_URL_VALUE}\"" \
        "VERSA_THIRD_PARTY_ENABLED=\"${PROXY_ENABLED}\"" \
        "VERSA_THIRD_PARTY_MODELS=\"${AGGREGATED_PROXY_MODELS}\"" \
        "VERSA_THIRD_PARTY_URL=\"http://localhost:${PROXY_PORT}\"" \
        "VERSA_COA_APPROVED_MODELS=\"${CURRENT_COA_APPROVED}\"" \
        "VERSA_ACTIVE_LOCAL_MODEL=\"${_ACTIVE_LOCAL_MODEL}\"" \
        "VERSA_MODEL_LOADING_STRATEGY=\"${_MODEL_LOADING_STRATEGY}\""; do
        KEY="${kv%%=*}"
        if grep -q "^${KEY}=" "${PATHS_ENV}"; then
          sed -i "s|^${KEY}=.*|${kv}|" "${PATHS_ENV}"
        else
          echo "${kv}" >> "${PATHS_ENV}"
        fi
      done
      ok "paths.env synced (cloud + local + proxy + COA approved)"
    fi

    # NOTE: Unified Model Catalog merge/sync runs for BOTH fresh installs and
    # updates at the convergence point below (search "Unified Model Catalog").

    # Legacy Inference Endpoint configuration removed (deprecated)
    echo ""
  fi

  # ─── Client Topology Repair ───────────────────────────
  # Self-healing: reconstruct client_config.json and sync active model
  # when topology=client but config is missing or stale.
  if [ "${DRY_RUN}" = false ] && [ "${_UPDATE_TOPOLOGY}" = "client" ]; then
    section "Update — Client Topology Repair"

    CLIENT_STATE_FILE="/etc/versa-agi/client_config.json"
    _REPAIR_REMOTE_URL="$(ini_get local_ai remote_inference_url '')"
    _REPAIR_MASTER_KEY="$(ini_get local_ai inference_master_key '')"

    # ── Step 1: Reconstruct client_config.json if missing ──
    if [ ! -f "${CLIENT_STATE_FILE}" ]; then
      info "client_config.json missing — reconstructing from tunnel service..."

      # Extract tunnel_host and tunnel_port from the running systemd service
      _REPAIR_TUNNEL_HOST=""
      _REPAIR_TUNNEL_PORT=""
      _TUNNEL_EXEC=$(systemctl show versa-agi-tunnel --property=ExecStart --no-pager 2>/dev/null || true)
      if [ -n "${_TUNNEL_EXEC}" ]; then
        # Parse: watchdog@<host> from the ExecStart line
        _REPAIR_TUNNEL_HOST=$(echo "${_TUNNEL_EXEC}" | grep -oP 'watchdog@\K[^\s;]+' | head -1)
        # Parse: -L <port>:localhost:<port>
        _REPAIR_TUNNEL_PORT=$(echo "${_TUNNEL_EXEC}" | grep -oP -- '-L\s+\K[0-9]+' | head -1)
      fi

      # Fallback: extract from remote_inference_url in setup.ini
      if [ -z "${_REPAIR_TUNNEL_HOST}" ] && [ -n "${_REPAIR_REMOTE_URL}" ]; then
        _REPAIR_TUNNEL_HOST=$(echo "${_REPAIR_REMOTE_URL}" | sed -E 's|https?://||;s|:[0-9]+$||;s|/.*||')
      fi
      if [ -z "${_REPAIR_TUNNEL_PORT}" ] && [ -n "${_REPAIR_REMOTE_URL}" ]; then
        _REPAIR_TUNNEL_PORT=$(echo "${_REPAIR_REMOTE_URL}" | grep -oP ':\K[0-9]+$' || echo "8080")
      fi

      if [ -n "${_REPAIR_TUNNEL_HOST}" ] && [ -n "${_REPAIR_TUNNEL_PORT}" ]; then
        _REPAIR_TUNNEL_URL="http://localhost:${_REPAIR_TUNNEL_PORT}"

        # Query live models from the tunnel endpoint
        _repair_models=""
        _models_json=$(curl -sf -H "Authorization: Bearer ${_REPAIR_MASTER_KEY}" "${_REPAIR_TUNNEL_URL}/v1/models" 2>/dev/null || echo "")
        if [ -n "${_models_json}" ] && command -v jq &>/dev/null; then
          _raw_models=$(echo "${_models_json}" | jq -r '.data[].id' 2>/dev/null | paste -sd ',')
          if [ -n "${_raw_models}" ]; then
            # Translate GGUF filenames → friendly keys via manage_registry.sh
            _MANAGE_REGISTRY_SCRIPT="${SCRIPT_DIR}/manage_registry.sh"
            if [ -f "${_MANAGE_REGISTRY_SCRIPT}" ]; then
              source "${_MANAGE_REGISTRY_SCRIPT}" --list >/dev/null 2>&1 || true
              # Define reverse-lookup (GGUF filename → friendly key).
              # manage_registry.sh loads _REG_* arrays but doesn't define this helper.
              _reg_name_for_file() { local f="$1"; local i; for i in $(seq 0 $((_REG_COUNT - 1))); do if [ "${_REG_FILES[$i]}" = "$f" ]; then echo "${_REG_NAMES[$i]}"; return; fi; done; }
              _translated_list=""
              IFS=',' read -ra _raw_arr <<< "${_raw_models}"
              for _gguf_name in "${_raw_arr[@]}"; do
                _friendly=$(_reg_name_for_file "${_gguf_name}" 2>/dev/null || true)
                if [ -n "${_friendly}" ]; then
                  _translated_list="${_translated_list:+${_translated_list},}${_friendly}"
                else
                  _translated_list="${_translated_list:+${_translated_list},}${_gguf_name}"
                fi
              done
              _repair_models="${_translated_list}"
            else
              _repair_models="${_raw_models}"
            fi
          fi
        fi

        # Write client_config.json
        mkdir -p "$(dirname "${CLIENT_STATE_FILE}")"
        cat > "${CLIENT_STATE_FILE}" <<REPAIRCFG
{
  "topology": "client",
  "remote_url": "${_REPAIR_REMOTE_URL}",
  "tunnel_url": "${_REPAIR_TUNNEL_URL}",
  "tunnel_host": "${_REPAIR_TUNNEL_HOST}",
  "tunnel_port": "${_REPAIR_TUNNEL_PORT}",
  "models": $(echo "${_repair_models:-}" | jq -R 'split(",")' 2>/dev/null || echo '[]'),
  "repaired_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
}
REPAIRCFG
        chmod 640 "${CLIENT_STATE_FILE}"
        chown "${WATCHDOG_USER}:${WATCHDOG_USER}" "${CLIENT_STATE_FILE}" 2>/dev/null || true
        ok "client_config.json reconstructed (tunnel: ${_REPAIR_TUNNEL_HOST}:${_REPAIR_TUNNEL_PORT})"
      else
        warn "Could not determine tunnel host/port — client_config.json NOT created"
        warn "Run: sudo ./setup_local.sh (option 2) to configure the client connection"
      fi
    else
      ok "client_config.json exists"
    fi

    # ── Step 2: Sync VERSA_ACTIVE_LOCAL_MODEL from remote server ──
    # Only meaningful in single mode. Router mode has no single active model.
    if [ "${_MODEL_LOADING_STRATEGY}" = "single" ]; then
    _CURRENT_ACTIVE=$(grep '^VERSA_ACTIVE_LOCAL_MODEL=' "${PATHS_ENV}" 2>/dev/null | cut -d'"' -f2 || true)
    # Trigger sync if: (a) empty, or (b) contains a raw GGUF filename (stale from previous bug)
    _NEEDS_ACTIVE_SYNC=false
    if [ -z "${_CURRENT_ACTIVE}" ]; then
      _NEEDS_ACTIVE_SYNC=true
      info "VERSA_ACTIVE_LOCAL_MODEL not set — querying remote server..."
    elif [[ "${_CURRENT_ACTIVE}" == *.gguf ]]; then
      _NEEDS_ACTIVE_SYNC=true
      info "VERSA_ACTIVE_LOCAL_MODEL contains raw GGUF name '${_CURRENT_ACTIVE}' — re-translating..."
    fi
    if [ "${_NEEDS_ACTIVE_SYNC}" = "true" ]; then

      # Read tunnel URL from paths.env (set during initial setup or sync above)
      _REPAIR_INFERENCE=$(grep '^VERSA_INFERENCE_URL=' "${PATHS_ENV}" 2>/dev/null | cut -d'"' -f2 || true)
      if [ -n "${_REPAIR_INFERENCE}" ]; then
        _active_json=$(curl -sf -H "Authorization: Bearer ${_REPAIR_MASTER_KEY}" "${_REPAIR_INFERENCE}/v1/models" 2>/dev/null || echo "")
        if [ -n "${_active_json}" ] && command -v jq &>/dev/null; then
          _active_gguf=$(echo "${_active_json}" | jq -r '.data[0].id' 2>/dev/null)
          if [ -n "${_active_gguf}" ] && [ "${_active_gguf}" != "null" ]; then
            # Translate GGUF → friendly name
            _active_friendly=""
            if declare -f _reg_name_for_file &>/dev/null; then
              _active_friendly=$(_reg_name_for_file "${_active_gguf}" 2>/dev/null || true)
            elif [ -f "${SCRIPT_DIR}/manage_registry.sh" ]; then
              source "${SCRIPT_DIR}/manage_registry.sh" --list >/dev/null 2>&1 || true
              # Define reverse-lookup if not already available
              _reg_name_for_file() { local f="$1"; local i; for i in $(seq 0 $((_REG_COUNT - 1))); do if [ "${_REG_FILES[$i]}" = "$f" ]; then echo "${_REG_NAMES[$i]}"; return; fi; done; }
              _active_friendly=$(_reg_name_for_file "${_active_gguf}" 2>/dev/null || true)
            fi
            _active_friendly="${_active_friendly:-${_active_gguf}}"

            # Write to paths.env
            if grep -q '^VERSA_ACTIVE_LOCAL_MODEL=' "${PATHS_ENV}" 2>/dev/null; then
              sed -i "s|^VERSA_ACTIVE_LOCAL_MODEL=.*|VERSA_ACTIVE_LOCAL_MODEL=\"${_active_friendly}\"|" "${PATHS_ENV}"
            else
              echo "VERSA_ACTIVE_LOCAL_MODEL=\"${_active_friendly}\"" >> "${PATHS_ENV}"
            fi
            ok "Active model synced: ${_active_friendly}"
          else
            warn "Remote server returned no models — VERSA_ACTIVE_LOCAL_MODEL not set"
          fi
        else
          warn "Could not query inference endpoint at ${_REPAIR_INFERENCE} — VERSA_ACTIVE_LOCAL_MODEL not set"
        fi
      else
        warn "No VERSA_INFERENCE_URL in paths.env — cannot query active model"
      fi
    else
      ok "Active model: ${_CURRENT_ACTIVE}"
    fi
    else
      ok "Router mode — active model sync skipped (all models available)"
    fi

    echo ""
  fi

fi  # end UPDATE_MODE post-deploy steps

# ─── Config Reconcile + Model Catalog Baseline + Sync (Edition 2.x) ──
# Runs IDENTICALLY for fresh installs and updates — deterministic regeneration:
#   • `agictl system reconcile-config` — regenerates the deployed setup.ini and
#     models.ini from the shipped templates. The template is authority for
#     structure, comments, stock model lists, and new/removed keys; user content
#     is preserved (setup.ini user-owned values; models.ini [catalog_custom]/
#     [providers_custom] + registry-added rows in the shared local sections).
#   • `agictl model migrate` — rebuilds the [catalog]/[providers] baseline from
#     the freshly reconciled setup.ini + template catalog metadata; the user
#     layer overlays the baseline at read time (so live edits survive).
#   • `agictl model sync` — regenerates the cloud/third-party paths.env
#     registries from the MERGED catalog.
# All idempotent and non-fatal. apply_system_permissions (below) re-asserts
# ownership/modes on the files they touch.
if [ "${DRY_RUN}" = false ] && command -v agictl >/dev/null 2>&1; then
  section "Model Catalog — Reconcile, Baseline & Sync"
  if [ -f "${SCRIPT_DIR}/setup.ini" ] && [ -f "${SCRIPT_DIR}/models.ini" ]; then
    if agictl system reconcile-config \
        --setup-template "${SCRIPT_DIR}/setup.ini" \
        --models-template "${SCRIPT_DIR}/models.ini" >/dev/null 2>&1; then
      ok "Config reconciled from shipped templates (user content preserved)"
    else
      warn "Config reconcile skipped (non-fatal)"
    fi
  fi
  if agictl model migrate >/dev/null 2>&1; then
    ok "Model catalog baseline regenerated from setup.ini (custom layer preserved)"
  else
    warn "Model catalog migrate skipped (non-fatal)"
  fi
  if agictl model sync >/dev/null 2>&1; then
    ok "Model catalog synced (derived sections + paths.env)"
  else
    warn "Model catalog sync skipped (non-fatal)"
  fi
  echo ""
fi

# ─── Step 11: Sentinel Service (reactive file watcher) ──
section "Step 11 — Sentinel Service"

SENTINEL_UNIT_SRC="${SRC_CORE_INFRA}/config/versa-agi-sentinel.service"
SENTINEL_UNIT_DEST="/etc/systemd/system/versa-agi-sentinel.service"

if [ -f "${SENTINEL_UNIT_SRC}" ]; then
  # Create sentinel log file
  touch /var/log/versa-agi-sentinel.log
  chown "${WATCHDOG_USER}:${WATCHDOG_USER}" /var/log/versa-agi-sentinel.log

  # Install systemd unit
  cp "${SENTINEL_UNIT_SRC}" "${SENTINEL_UNIT_DEST}"
  systemctl daemon-reload

  # Check INI for sentinel mode
  FILE_MON_ENABLED="$(ini_get agent file_mon_enabled true)"
  if [ "${FILE_MON_ENABLED}" = "true" ]; then
    systemctl enable versa-agi-sentinel --quiet 2>/dev/null || true
    systemctl start versa-agi-sentinel 2>/dev/null || true
    ok "Sentinel service installed and started"
  else
    systemctl stop versa-agi-sentinel 2>/dev/null || true
    systemctl disable versa-agi-sentinel --quiet 2>/dev/null || true
    ok "Sentinel service installed but DISABLED (file_mon_enabled=false)"
  fi
else
  warn "Sentinel service unit not found at ${SENTINEL_UNIT_SRC} — skipping"
fi
# ─── System Permissions Restabilization ───────────────
# This is the AUTHORITATIVE, FINAL pass. It enforces the System Design §IX
# exactly. No blanket chown -R — every path is set individually.
apply_system_permissions() {
  info "Applying System Design §IX permissions (final restabilization)..."
  
  # ──────────────────────────────────────────────────────
  # §1. /etc/versa-agi/ — Configuration & Security
  # ──────────────────────────────────────────────────────
  [ -d "/etc/versa-agi" ]                && chown "${WATCHDOG_USER}:${WATCHDOG_USER}" /etc/versa-agi && chmod 751 /etc/versa-agi
  [ -f "/etc/versa-agi/coa_config.json" ] && chown "${WATCHDOG_USER}:${COA_USER}" /etc/versa-agi/coa_config.json && chmod 640 /etc/versa-agi/coa_config.json
  [ -f "/etc/versa-agi/setup.ini" ]       && chown "${WATCHDOG_USER}:agi_agents" /etc/versa-agi/setup.ini && chmod 640 /etc/versa-agi/setup.ini
  [ -f "/etc/versa-agi/models.ini" ]      && chown "${WATCHDOG_USER}:agi_agents" /etc/versa-agi/models.ini && chmod 640 /etc/versa-agi/models.ini
  # paths.env: watchdog:coa 644 (readable by all — sourced by lifeline, agitop, sentinel)
  [ -f "/etc/versa-agi/paths.env" ]       && chown "${WATCHDOG_USER}:${COA_USER}" /etc/versa-agi/paths.env && chmod 644 /etc/versa-agi/paths.env
  # Other .env files: watchdog:coa 640 (skip paths.env — already set above)
  for env_file in /etc/versa-agi/*.env; do
    [ -f "${env_file}" ] || continue
    [ "$(basename "${env_file}")" = "paths.env" ] && continue
    chown "${WATCHDOG_USER}:${COA_USER}" "${env_file}" && chmod 640 "${env_file}"
  done
  # Sub-agent configs: watchdog:{os_user} 640
  for sa_config in /etc/versa-agi/*_config.json; do
    [ -f "${sa_config}" ] || continue
    local sa_name
    sa_name=$(basename "${sa_config}" _config.json)
    [ "${sa_name}" = "coa" ] && continue  # COA handled above
    # Resolve os_user from agents.db
    local sa_os_user
    sa_os_user=$(sqlite3 "${AGENTS_DB}" "SELECT os_user FROM agents WHERE name='${sa_name}';" 2>/dev/null || true)
    if [ -n "${sa_os_user}" ] && getent passwd "${sa_os_user}" &>/dev/null; then
      chown "${WATCHDOG_USER}:${sa_os_user}" "${sa_config}" && chmod 640 "${sa_config}"
    fi
  done
  # Poise root: watchdog:watchdog 750 (coa.md, task_protocol.md)
  [ -d "/etc/versa-agi/poise" ]   && chown "${WATCHDOG_USER}:${WATCHDOG_USER}" /etc/versa-agi/poise && chmod 750 /etc/versa-agi/poise
  [ -f "/etc/versa-agi/poise/coa.md" ]            && chown "${WATCHDOG_USER}:${WATCHDOG_USER}" /etc/versa-agi/poise/coa.md && chmod 640 /etc/versa-agi/poise/coa.md
  [ -f "/etc/versa-agi/poise/watchdog.md" ]       && chown "${WATCHDOG_USER}:${WATCHDOG_USER}" /etc/versa-agi/poise/watchdog.md && chmod 640 /etc/versa-agi/poise/watchdog.md
  [ -f "/etc/versa-agi/poise/task_protocol.md" ]  && chown "${WATCHDOG_USER}:${WATCHDOG_USER}" /etc/versa-agi/poise/task_protocol.md && chmod 640 /etc/versa-agi/poise/task_protocol.md
  # Poise roles: watchdog:coa — COA reads, cannot modify (§IX.2)
  if [ -d "/etc/versa-agi/poise/roles" ]; then
    chown "${WATCHDOG_USER}:${COA_USER}" /etc/versa-agi/poise/roles && chmod 750 /etc/versa-agi/poise/roles
    find /etc/versa-agi/poise/roles -mindepth 1 -type d -exec chown "${WATCHDOG_USER}:${COA_USER}" {} + -exec chmod 750 {} + 2>/dev/null || true
    find /etc/versa-agi/poise/roles -type f -exec chown "${WATCHDOG_USER}:${COA_USER}" {} + -exec chmod 440 {} + 2>/dev/null || true
  fi
  [ -d "/etc/versa-agi/vault" ]   && chown -R "${WATCHDOG_USER}:${COA_USER}" /etc/versa-agi/vault && chmod 750 /etc/versa-agi/vault
  
  # ──────────────────────────────────────────────────────
  # §1. /var/lib/versa-agi/ — Persistent Data
  # ──────────────────────────────────────────────────────
  [ -d "/var/lib/versa-agi" ]                   && chown "${WATCHDOG_USER}:${WATCHDOG_USER}" /var/lib/versa-agi && chmod 755 /var/lib/versa-agi
  [ -f "/var/lib/versa-agi/agents.db" ]         && chown "${WATCHDOG_USER}:${COA_USER}" /var/lib/versa-agi/agents.db && chmod 660 /var/lib/versa-agi/agents.db
  [ -f "/var/lib/versa-agi/messages.db" ]       && chown "${WATCHDOG_USER}:${COA_USER}" /var/lib/versa-agi/messages.db && chmod 660 /var/lib/versa-agi/messages.db
  [ -d "/var/lib/versa-agi/coa" ]               && chown "${WATCHDOG_USER}:${COA_USER}" /var/lib/versa-agi/coa && chmod 750 /var/lib/versa-agi/coa
  [ -f "/var/lib/versa-agi/coa/tasks.db" ]      && chown "${WATCHDOG_USER}:${COA_USER}" /var/lib/versa-agi/coa/tasks.db && chmod 660 /var/lib/versa-agi/coa/tasks.db
  [ -f "/var/lib/versa-agi/coa/cycles.db" ]     && chown "${WATCHDOG_USER}:${COA_USER}" /var/lib/versa-agi/coa/cycles.db && chmod 660 /var/lib/versa-agi/coa/cycles.db
  [ -d "/var/lib/versa-agi/coa/cycles" ]        && chown "${COA_USER}:${COA_USER}" /var/lib/versa-agi/coa/cycles && chmod 755 /var/lib/versa-agi/coa/cycles
  [ -f "/var/lib/versa-agi/coa/agent_memory.db" ] && chown "${COA_USER}:${COA_USER}" /var/lib/versa-agi/coa/agent_memory.db && chmod 660 /var/lib/versa-agi/coa/agent_memory.db
  
  # ──────────────────────────────────────────────────────
  # §1. /var/log/ — Log Files
  # ──────────────────────────────────────────────────────
  for log in /var/log/versa-agi-lifeline.log /var/log/versa-agi-sentinel.log; do
    [ -f "${log}" ] && chown "${WATCHDOG_USER}:${WATCHDOG_USER}" "${log}" && chmod 644 "${log}"
  done
  [ -d "/var/log/versa-agi-archive" ] && chown -R "${WATCHDOG_USER}:${WATCHDOG_USER}" "/var/log/versa-agi-archive"
  
  # ──────────────────────────────────────────────────────
  # §3. COA Environment
  # ──────────────────────────────────────────────────────
  if [ -d "${DEPLOYED_COA_ENV}" ]; then
    # /home/coa: coa:coa 755 — traversable by sub-agents for workspace symlinks
    [ -d "/home/${COA_USER}" ] && chmod 755 "/home/${COA_USER}"
    
    # Root: coa owns the environment
    chown "${COA_USER}:${COA_USER}" "${DEPLOYED_COA_ENV}"
    
    # node_modules/ — watchdog:coa, dirs 750, files 640
    if [ -d "${DEPLOYED_COA_ENV}/node_modules" ]; then
      chown -R "${WATCHDOG_USER}:${COA_USER}" "${DEPLOYED_COA_ENV}/node_modules"
      find "${DEPLOYED_COA_ENV}/node_modules" -type d -exec chmod 750 {} + 2>/dev/null || true
      find "${DEPLOYED_COA_ENV}/node_modules" -type f -exec chmod 640 {} + 2>/dev/null || true
    fi
    
    # .agent/ — coa:agi_agents 775 (agent metadata root; group-writable for watchdog system.md writes)
    [ -d "${DEPLOYED_COA_ENV}/.agent" ] && chown "${COA_USER}:agi_agents" "${DEPLOYED_COA_ENV}/.agent" && chmod 775 "${DEPLOYED_COA_ENV}/.agent"
    
    # .agent/README.md — coa:coa 644
    [ -f "${DEPLOYED_COA_ENV}/.agent/README.md" ] && chown "${COA_USER}:${COA_USER}" "${DEPLOYED_COA_ENV}/.agent/README.md" && chmod 644 "${DEPLOYED_COA_ENV}/.agent/README.md"
    # .agent/poise.md — watchdog:coa 640 (copied from /etc/versa-agi/poise/coa.md by Lifeline)
    [ -f "${DEPLOYED_COA_ENV}/.agent/poise.md" ] && chown "${WATCHDOG_USER}:${COA_USER}" "${DEPLOYED_COA_ENV}/.agent/poise.md" && chmod 640 "${DEPLOYED_COA_ENV}/.agent/poise.md"
    
    # ── §3. Agent-Writable Areas ──
    
    # .agent/skills/ directory — coa:agi_agents 775 (agent CAN create new skills)
    [ -d "${DEPLOYED_COA_ENV}/.agent/skills" ] && chown "${COA_USER}:agi_agents" "${DEPLOYED_COA_ENV}/.agent/skills" && chmod 775 "${DEPLOYED_COA_ENV}/.agent/skills"
    
    # .agent/skills/*.md (shipped) — watchdog:agi_agents 440 (agent CANNOT modify)
    # Only lock skills that exist in the shipped source — agent-authored skills
    # (created via 'agictl skill new' or directly by COA) must stay editable.
    for skill_file in "${DEPLOYED_COA_ENV}/.agent/skills"/*.md; do
      [ -f "${skill_file}" ] || continue
      [ -f "${DEPLOYED_CORE_INFRA}/skills/$(basename "${skill_file}")" ] || continue
      chown "${WATCHDOG_USER}:agi_agents" "${skill_file}"
      chmod 440 "${skill_file}"
    done

    # Agent-authored skill artifacts (anything NOT in the shipped source) —
    # normalize to coa:agi_agents, group-writable (664 files / 2775 dirs).
    # Heals watchdog-owned artifacts left by 'agictl skill new' (agictl
    # elevates to watchdog) and coa:coa group drift from direct authoring.
    for _skill_item in "${DEPLOYED_COA_ENV}/.agent/skills"/*; do
      [ -e "${_skill_item}" ] || continue
      _skill_base="$(basename "${_skill_item}")"
      if [ -f "${_skill_item}" ]; then
        case "${_skill_base}" in *.md) ;; *) continue ;; esac
        [ -f "${DEPLOYED_CORE_INFRA}/skills/${_skill_base}" ] && continue
        chown "${COA_USER}:agi_agents" "${_skill_item}"
        chmod 664 "${_skill_item}"
      elif [ -d "${_skill_item}" ]; then
        # Asset dirs of shipped skills are managed by the deploy step
        [ -d "${DEPLOYED_CORE_INFRA}/skills/${_skill_base}" ] && continue
        chown -R "${COA_USER}:agi_agents" "${_skill_item}"
        find "${_skill_item}" -type d -exec chmod 2775 {} + 2>/dev/null || true
        find "${_skill_item}" -type f -exec chmod 664 {} + 2>/dev/null || true
      fi
    done
    
    # workspace/ — coa:agi_agents 2770 (setgid ensures new project dirs inherit agi_agents group §3.6)
    [ -d "${DEPLOYED_COA_ENV}/workspace" ] && chown "${COA_USER}:agi_agents" "${DEPLOYED_COA_ENV}/workspace" && chmod 2770 "${DEPLOYED_COA_ENV}/workspace"
    [ -f "${DEPLOYED_COA_ENV}/workspace/.gitignore" ] && chown "${COA_USER}:agi_agents" "${DEPLOYED_COA_ENV}/workspace/.gitignore" && chmod 644 "${DEPLOYED_COA_ENV}/workspace/.gitignore"
    
    # attachments/ — coa:agi_agents 2770 (setgid, matches workspace/ permissions)
    if [ -d "${DEPLOYED_COA_ENV}/attachments" ]; then
      chown "${COA_USER}:agi_agents" "${DEPLOYED_COA_ENV}/attachments"
      chmod 2770 "${DEPLOYED_COA_ENV}/attachments"
      find "${DEPLOYED_COA_ENV}/attachments" -mindepth 1 -not -type l -type d -exec chown "${COA_USER}:agi_agents" {} + 2>/dev/null || true
      find "${DEPLOYED_COA_ENV}/attachments" -mindepth 1 -not -type l -type d -exec chmod 770 {} + 2>/dev/null || true
      find "${DEPLOYED_COA_ENV}/attachments" -not -type l -type f -exec chown "${COA_USER}:agi_agents" {} + 2>/dev/null || true
      find "${DEPLOYED_COA_ENV}/attachments" -not -type l -type f -exec chmod 660 {} + 2>/dev/null || true
      # Cleanup circular symlinks
      [ -L "${DEPLOYED_COA_ENV}/attachments/attachments" ] && rm -f "${DEPLOYED_COA_ENV}/attachments/attachments"
    fi
    # .agent/inbox + .agent/outbox — coa:watchdog (coa writes via MCP, watchdog reads for sync)
    for item in inbox outbox; do
      if [ -d "${DEPLOYED_COA_ENV}/.agent/${item}" ]; then
        chown -R "${COA_USER}:${WATCHDOG_USER}" "${DEPLOYED_COA_ENV}/.agent/${item}"
        chmod 750 "${DEPLOYED_COA_ENV}/.agent/${item}"
      fi
    done
    
    # .git/ — owned by whoever init'd the repo
    [ -d "${DEPLOYED_COA_ENV}/.git" ] && chown -R "${COA_USER}:${COA_USER}" "${DEPLOYED_COA_ENV}/.git"
  fi
  
  # ──────────────────────────────────────────────────────
  # §IX.3 Core Infrastructure — enforce exact modes
  # ──────────────────────────────────────────────────────
  local CI="${WATCHDOG_HOME:-/home/${WATCHDOG_USER}}/core-infra"
  if [ -d "${CI}" ]; then
    chown -R "${WATCHDOG_USER}:${WATCHDOG_USER}" "${CI}"
    # Executable scripts: 755
    for script in lifeline.sh sentinel.sh watchdog.sh; do
      [ -f "${CI}/${script}" ] && chmod 755 "${CI}/${script}"
    done
    # Executable bins: 755
    for bin_file in agictl agictl-wrapper vcoa agitop; do
      [ -f "${CI}/bin/${bin_file}" ] && chmod 755 "${CI}/bin/${bin_file}"
    done
    # Executable scripts in scripts/: 755
    find "${CI}/scripts" -type f -name '*.sh' -exec chmod 755 {} + 2>/dev/null || true
    # Config files (non-executable): 640
    for cfg in config/coa_poise.md config/task_protocol.md config/watchdog_poise.md; do
      [ -f "${CI}/${cfg}" ] && chmod 640 "${CI}/${cfg}"
    done
    # Config roles: 640 for files, 750 for dirs
    if [ -d "${CI}/config/roles" ]; then
      find "${CI}/config/roles" -type d -exec chmod 750 {} + 2>/dev/null || true
      find "${CI}/config/roles" -type f -exec chmod 640 {} + 2>/dev/null || true
    fi
    # Static data files: 644
    for data_file in ui_lib.sh logo.txt; do
      [ -f "${CI}/${data_file}" ] && chmod 644 "${CI}/${data_file}"
    done
  fi
  
  # ──────────────────────────────────────────────────────
  # §IX.4 Sub-Agent Environments
  # ──────────────────────────────────────────────────────
  local AGENTS_DB="/var/lib/versa-agi/agents.db"
  if [ -f "${AGENTS_DB}" ]; then
    local sub_agents
    sub_agents=$(sqlite3 "${AGENTS_DB}" "SELECT name || '|' || os_user FROM agents WHERE name NOT IN ('watchdog','coa') AND status != 'removed';" 2>/dev/null || true)
    for agent_row in ${sub_agents}; do
      local agent="${agent_row%%|*}"
      local os_user="${agent_row##*|}"
      local ahome="/home/agi-${agent}"
      [ -d "${ahome}" ] || continue
      chown "${os_user}:agi_agents" "${ahome}" && chmod 770 "${ahome}"
      [ -d "${ahome}/.agent" ]        && chown "${os_user}:agi_agents" "${ahome}/.agent" && chmod 770 "${ahome}/.agent"
      [ -d "${ahome}/.agent/skills" ] && chown "${os_user}:agi_agents" "${ahome}/.agent/skills" && chmod 775 "${ahome}/.agent/skills"
      # .agent/skills/*.md (shipped) — watchdog:agi_agents 440 (agent CANNOT modify system skills)
      for _skill in "${ahome}/.agent/skills"/*.md; do
        [ -f "${_skill}" ] || continue
        chown "${WATCHDOG_USER}:agi_agents" "${_skill}"
        chmod 440 "${_skill}"
      done
      [ -d "${ahome}/workspace" ]     && chown "${os_user}:agi_agents" "${ahome}/workspace" && chmod 2770 "${ahome}/workspace"
      # §IX.2 /var/lib/versa-agi/{name}/ — agent data directory
      # Parent dir: watchdog-traversable. cycles/: agent-writable (lifeline spawns as agent).
      # poise + duties: watchdog-readable (lifeline cat's poise for system.md)
      local vdata="/var/lib/versa-agi/${agent}"
      if [ -d "${vdata}" ]; then
        chown "${WATCHDOG_USER}:${os_user}" "${vdata}" && chmod 750 "${vdata}"
        [ -d "${vdata}/cycles" ]        && chown -R "${os_user}:${os_user}" "${vdata}/cycles" && chmod 755 "${vdata}/cycles"
        [ -f "${vdata}/last_prompt.txt" ] && chown "${WATCHDOG_USER}:${os_user}" "${vdata}/last_prompt.txt" && chmod 640 "${vdata}/last_prompt.txt"
      fi
      [ -f "${vdata}/poise.md" ]  && chown "${WATCHDOG_USER}:${os_user}" "${vdata}/poise.md"  && chmod 640 "${vdata}/poise.md"
      [ -f "${vdata}/duties.md" ] && chown "${WATCHDOG_USER}:${os_user}" "${vdata}/duties.md" && chmod 640 "${vdata}/duties.md"
      # §IX.4 Sub-agent files — git identity, SSH keypair, credentials
      [ -f "${ahome}/README.md" ]       && chown "${os_user}:agi_agents" "${ahome}/README.md"       && chmod 664 "${ahome}/README.md"
      [ -f "${ahome}/.gitconfig" ]      && chown "${os_user}:agi_agents" "${ahome}/.gitconfig"      && chmod 644 "${ahome}/.gitconfig"
      [ -f "${ahome}/.git-credentials" ] && chown "${os_user}:agi_agents" "${ahome}/.git-credentials" && chmod 600 "${ahome}/.git-credentials"
      if [ -d "${ahome}/.ssh" ]; then
        chown -R "${os_user}:agi_agents" "${ahome}/.ssh"
        chmod 700 "${ahome}/.ssh"
        # Private keys: 600, public keys: 644, config: 644
        find "${ahome}/.ssh" -maxdepth 1 -type f -name '*.pub' -exec chmod 644 {} + 2>/dev/null || true
        find "${ahome}/.ssh" -maxdepth 1 -type f -name 'config' -exec chmod 644 {} + 2>/dev/null || true
        find "${ahome}/.ssh" -maxdepth 1 -type f ! -name '*.pub' ! -name 'config' ! -name 'known_hosts*' -exec chmod 600 {} + 2>/dev/null || true
        find "${ahome}/.ssh" -maxdepth 1 -type f -name 'known_hosts*' -exec chmod 644 {} + 2>/dev/null || true
      fi
    done
  fi
  
  # §IX.2 /var/lib/versa-agi/coa/ — runtime state files
  [ -f "/var/lib/versa-agi/coa/status.json" ]     && chown "${WATCHDOG_USER}:${COA_USER}" "/var/lib/versa-agi/coa/status.json"     && chmod 640 "/var/lib/versa-agi/coa/status.json"
  [ -f "/var/lib/versa-agi/coa/last_prompt.txt" ] && chown "${WATCHDOG_USER}:${COA_USER}" "/var/lib/versa-agi/coa/last_prompt.txt" && chmod 640 "/var/lib/versa-agi/coa/last_prompt.txt"
  
  # §IX.2 /var/lib/versa-agi/ — fix orphan dirs (archive, config)
  if [ -d "/var/lib/versa-agi/archive" ]; then
    chown -R "${WATCHDOG_USER}:${WATCHDOG_USER}" /var/lib/versa-agi/archive
    chmod 755 /var/lib/versa-agi/archive
    find /var/lib/versa-agi/archive -type f -exec chmod 644 {} + 2>/dev/null || true
  fi
  [ -d "/var/lib/versa-agi/config" ]  && chown -R "${WATCHDOG_USER}:${WATCHDOG_USER}" /var/lib/versa-agi/config && chmod 755 /var/lib/versa-agi/config
  

  ok "System Design §IX permissions applied"
}

apply_system_permissions

# ═══════════════════════════════════════════════════════
# U4b: Sync Templates (--update only, with prompt)
# ═══════════════════════════════════════════════════════
if [ "${UPDATE_MODE}" = true ]; then
  section "Update — Sync Templates"
  if [ "${DRY_RUN}" = true ]; then
    dry "Would prompt to sync system templates to active agents"
  else
    if confirm_accent "Do you want to sync updated system templates (poise & skills) to active agents?"; then
      step_arrow "Running: python3 ${DEPLOYED_CORE_INFRA}/scripts/sync_templates.py"
      python3 "${DEPLOYED_CORE_INFRA}/scripts/sync_templates.py" || warn "sync_templates encountered an issue."
    fi
  fi
fi

# ═══════════════════════════════════════════════════════
# U5: Resume CRON (--update only, with prompt)
# ═══════════════════════════════════════════════════════
if [ "${UPDATE_MODE}" = true ]; then
  section "Update — Resume CRON"
  if [ "${DRY_RUN}" = true ]; then
    dry "Would prompt to resume CRON"
  elif [ "${CRON_WAS_ACTIVE:-false}" = true ]; then
    if confirm_accent "Resume CRON (lifeline scheduler)?"; then
      CURRENT_CRON=$(crontab -u "${WATCHDOG_USER}" -l 2>/dev/null || true)
      echo "${CURRENT_CRON}" | sed '/[Ll]ifeline/s|^#||' | \
        crontab -u "${WATCHDOG_USER}" -
      ok "CRON resumed"
    else
      warn "CRON NOT resumed — agents will not spawn until you re-enable it"
      echo -e "  ${DIM:-}To resume manually: sudo agitop → Controls → Resume CRON${RESET:-}"
    fi
  else
    warn "CRON was not active before update — skipping resume"
  fi
  echo ""
fi

# ─── Step 12: Post-Setup Health Check ─────────────────
echo ""
section "Step 12 — Health Check"
echo ""

# Source shared health check library
HEALTH_LIB="${DEPLOYED_CORE_INFRA}/scripts/health_checks.sh"
if [ -f "${HEALTH_LIB}" ]; then
  source "${HEALTH_LIB}"
  run_health_checks

  echo ""
  echo "  ───────────────────────────────────"
  echo "  Results: ${HEALTH_PASS} passed, ${HEALTH_FAIL} failed"
  if [ "${HEALTH_FAIL}" -gt 0 ]; then
    warn "Health check completed with ${HEALTH_FAIL} failure(s) — review items above"
  else
    ok "All health checks passed"
  fi
else
  warn "Health check library not found at ${HEALTH_LIB} — skipping validation"
fi
echo ""

# ─── Step 13: Save Setup Configuration ─────────────
section "Step 13 — Configuration Persistence"

mkdir -p /etc/versa-agi
INI_FILE="/etc/versa-agi/setup.ini"

if [ "${UPDATE_MODE}" = true ]; then
  # UPDATE MODE: interactive values are not re-collected. The deployed setup.ini
  # was already regenerated from the shipped template (structure + stock lists
  # refreshed, user-owned values carried forward) by `agictl system
  # reconcile-config` in the Model Catalog step — nothing left to write here.
  if [ -f "${INI_FILE}" ]; then
    chmod 640 "${INI_FILE}"
    chown "${WATCHDOG_USER}:agi_agents" "${INI_FILE}"
    ok "Setup configuration verified at ${INI_FILE} (reconciled from template)"
  fi
else
  # FRESH INSTALL: seed from source template, inject collected values.
  if [ -f "${SCRIPT_DIR}/setup.ini" ]; then
    cp "${SCRIPT_DIR}/setup.ini" "${INI_FILE}"
  else
    cat > "${INI_FILE}" <<'MINSEED'
[versavoice]
api_token=
[gemini]
auth_method=api_key
api_key=
mode=cloud
model=gemini-3-flash-preview
[agent]
cron_interval=1
[users]
watchdog=watchdog
coa=coa
MINSEED
  fi

  _ini_set() { local k="$1" v="$2"; grep -q "^${k}=" "${INI_FILE}" 2>/dev/null && sed -i "s|^${k}=.*|${k}=${v}|" "${INI_FILE}"; }
  _ini_set_in() { local s="$1" k="$2" v="$3"; sed -i "/^\[${s}\]/,/^\[/ s|^${k}=.*|${k}=${v}|" "${INI_FILE}"; }

  _ini_set "api_token"  "${VV_TOKEN:-}"
  _ini_set "auth_method" "$( [ "${AUTH_METHOD:-}" = "1" ] && echo "api_key" || echo "vertex" )"
  _ini_set "api_key"    "${api_key:-}"
  _ini_set_in "gemini" "mode" "${SELECTED_EXEC_MODE:-cloud}"
  _ini_set "model"      "${INI_GEMINI_MODEL:-gemini-3-flash-preview}"
  _ini_set "thinking_level" "$(ini_get gemini thinking_level high)"
  _ini_set_in "local_ai" "enabled" "${INI_LOCAL_AI_ENABLED:-false}"
  _ini_set "ollama_host" "${INI_OLLAMA_HOST:-http://localhost:11434}"
  _ini_set "proxy_port" "${INI_PROXY_PORT:-4000}"
  _ini_set "default_model" "${INI_LOCAL_AI_DEFAULT_MODEL:-gemma4:e4b}"
  _ini_set "local_models" "${INI_LOCAL_MODELS:-gemma4:e4b,gemma4:26b,gemma4:31b}"
  _ini_set "auto_pull_model" "${INI_AUTO_PULL_MODEL:-true}"
  _ini_set_in "local_ai" "gpu_backend" "${INI_GPU_BACKEND:-standard}"
  _ini_set "intel_card_count" "${INI_INTEL_CARD_COUNT:-1}"
  _ini_set "intel_device_id" "${INI_INTEL_DEVICE_ID:-}"
  _ini_set "sycl_vram_gb" "$(ini_get local_ai sycl_vram_gb 8)"
  _ini_set "sycl_parallel" "$(ini_get local_ai sycl_parallel 2)"
  _ini_set "sycl_ctx_size" "$(ini_get local_ai sycl_ctx_size 4096)"
  _ini_set "sycl_port" "$(ini_get local_ai sycl_port 8080)"
  _ini_set "sycl_active_model" "$(ini_get local_ai sycl_active_model '')"
  _ini_set "sycl_models_max" "$(ini_get local_ai sycl_models_max 1)"
  _ini_set "hf_token" "$(ini_get local_ai hf_token '')"
  _ini_set "sycl_llama_cpp_tag" "$(ini_get local_ai sycl_llama_cpp_tag b9082)"
  _ini_set_in "local_ai" "topology" "${INI_TOPOLOGY:-local}"
  _ini_set "model_loading_strategy" "$(ini_get local_ai model_loading_strategy router)"
  _ini_set "project"    "${gcp_project:-$INI_GCP_PROJECT}"
  _ini_set "location"   "${gcp_location:-$INI_GCP_LOCATION}"
  _ini_set "service_account_key" "${INI_SA_KEY_PATH:-}"
  _ini_set "cron_interval" "${CRON_INTERVAL:-1}"
  _ini_set "file_mon_enabled" "$(ini_get agent file_mon_enabled false)"
  _ini_set "timeout_minutes" "${INI_AGENT_TIMEOUT:-60}"
  _ini_set "runaway_threshold" "${INI_RUNAWAY_THRESHOLD:-300}"
  _ini_set "circuit_breaker_consecutive" "$(ini_get agent circuit_breaker_consecutive 5)"
  _ini_set "circuit_breaker_hourly" "$(ini_get agent circuit_breaker_hourly 20)"
  _ini_set "first_name" "${INI_AGENT_FIRST_NAME:-Versa}"
  _ini_set "last_name"  "${INI_AGENT_LAST_NAME:-(COA)}"
  _ini_set "language"   "${INI_AGENT_LANGUAGE:-en}"
  _ini_set "country"    "${INI_AGENT_COUNTRY:-United States}"
  _ini_set "voice"      "${INI_AGENT_VOICE:-female}"
  _ini_set "role"       "${INI_AGENT_ROLE:-Chief Orchestrator Agent}"
  _ini_set "platforms"  "${INI_GIT_PLATFORMS:-none}"
  _ini_set "workspace_link" "${INI_WORKSPACE_LINK:-}"
  _ini_set_in "users" "watchdog" "${WATCHDOG_USER:-watchdog}"
  _ini_set_in "users" "coa" "${COA_USER:-coa}"
  _ini_set_in "logging" "enabled" "$(ini_get logging enabled true)"

  chmod 640 "${INI_FILE}"
  chown "${WATCHDOG_USER}:agi_agents" "${INI_FILE}"
  ok "Setup configuration saved to ${INI_FILE}"

  # Sync deployed INI back to source (keep source master in sync)
  SOURCE_SETUP_INI="${SCRIPT_DIR}/setup.ini"
  if [ -f "${SOURCE_SETUP_INI}" ] && [ "$(realpath "${INI_FILE}" 2>/dev/null)" != "$(realpath "${SOURCE_SETUP_INI}" 2>/dev/null)" ]; then
    cp "${INI_FILE}" "${SOURCE_SETUP_INI}"
    chmod 600 "${SOURCE_SETUP_INI}"
    ok "Source setup.ini synced at ${SOURCE_SETUP_INI}"
  fi
fi

# ─── Install Acceptance — Record & Submit ─────────
# Runs after Step 13 so registration state is not clobbered by source→deployed copy.
if declare -F install_acceptance_record_full >/dev/null 2>&1 \
   && declare -F install_acceptance_record_update >/dev/null 2>&1; then
  section "Registration Submit"
  if [ "${UPDATE_MODE}" = true ]; then
    install_acceptance_record_update
  else
    install_acceptance_record_full "${ENABLE_VV:-false}"
  fi
  if declare -F _install_acceptance_sync_source_ini >/dev/null 2>&1; then
    _install_acceptance_sync_source_ini
  elif [ -f "${SCRIPT_DIR}/setup.ini" ] && [ "$(realpath "${INI_FILE}" 2>/dev/null)" != "$(realpath "${SCRIPT_DIR}/setup.ini" 2>/dev/null)" ]; then
    cp "${INI_FILE}" "${SCRIPT_DIR}/setup.ini"
    chmod 600 "${SCRIPT_DIR}/setup.ini"
    ok "Source setup.ini synced at ${SCRIPT_DIR}/setup.ini"
  fi
  echo ""
fi

# Create ~/.versa-agi/ user home for the Primary User
if [ -n "${SUDO_USER:-}" ]; then
  PU_HOME=$(eval echo "~${SUDO_USER}")
  PU_VERSA_DIR="${PU_HOME}/.versa-agi"
  mkdir -p "${PU_VERSA_DIR}"
  chown "${SUDO_USER}:${SUDO_USER}" "${PU_VERSA_DIR}"
  # Create convenience symlink: ~/.versa-agi/setup.ini → /etc/versa-agi/setup.ini
  ln -sf /etc/versa-agi/setup.ini "${PU_VERSA_DIR}/setup.ini"
  chown -h "${SUDO_USER}:${SUDO_USER}" "${PU_VERSA_DIR}/setup.ini"
  ok "Created ~/.versa-agi/ with setup.ini symlink for ${SUDO_USER}"
fi

# Sync: source → deployed (FRESH INSTALL only — source is the master copy with full comments)
# In UPDATE mode, the deployed INI is the authority; syncing would clobber user config.
if [ "${UPDATE_MODE}" != true ] && [ -f "${SCRIPT_DIR}/setup.ini" ] && [ "${SCRIPT_DIR}/setup.ini" != "${INI_FILE}" ]; then
  cp -f "${SCRIPT_DIR}/setup.ini" "${INI_FILE}"
  chmod 640 "${INI_FILE}"
  chown "${WATCHDOG_USER}:agi_agents" "${INI_FILE}"
  ok "Synced setup.ini: source → deployed"
fi

if [ -f "${REGISTRATION_CONF_SRC}" ]; then
  cp -f "${REGISTRATION_CONF_SRC}" "/etc/versa-agi/registration.conf"
  chown root:"${WATCHDOG_USER}" "/etc/versa-agi/registration.conf" 2>/dev/null || true
  chmod 640 "/etc/versa-agi/registration.conf"
  ok "registration.conf deployed to /etc/versa-agi/"
fi

echo ""

# ─── Summary ─────────────────────────────────────────
if [ "${UPDATE_MODE}" = true ]; then
  if [ "${DRY_RUN}" = true ]; then
    summary_card "Dry Run Complete" \
      "Timestamp:${TIMESTAMP}" \
      "Mode:update (dry-run)"
  else
    summary_card "Update Applied" \
      "Timestamp:${TIMESTAMP}" \
      "Core Infra:${DEPLOYED_CORE_INFRA}" \
      "COA Env:${DEPLOYED_COA_ENV}" \
      "Backup:${BACKUP_PATH:-n/a}"

    echo ""
    step_arrow "Monitor: ${DIM:-}tail -f /var/log/versa-agi-lifeline.log${RESET:-}"
    echo ""
    step_arrow "Rollback:"
    echo -e "     ${DIM:-}sudo rsync -a ${BACKUP_PATH:-/home/watchdog/backups/latest}/core-infra/ ${DEPLOYED_CORE_INFRA}/${RESET:-}"
    echo -e "     ${DIM:-}sudo rsync -a ${BACKUP_PATH:-/home/watchdog/backups/latest}/coa-env/ ${DEPLOYED_COA_ENV}/${RESET:-}"
  fi
else
  summary_card "Setup Complete" \
    "Core Infra:${DEPLOYED_CORE_INFRA}" \
    "COA Env:${DEPLOYED_COA_ENV}" \
    "AI Backend:${SELECTED_EXEC_MODE}" \
    "CRON:Every ${CRON_INTERVAL} minutes" \
    "File Monitor:Reactive file watcher (systemd)" \
    "Database:V3 Split Schema"

  echo ""
  echo -e "  ${BCYAN:-}You now possess a highly sophisticated, secure, and deeply"
  echo -e "  modular AI infrastructure.${RESET:-}"
  echo ""

  step_arrow "Monitor:  ${DIM:-}tail -f /var/log/versa-agi-lifeline.log${RESET:-}"
  step_arrow "Dashboard:  ${DIM:-}sudo agitop${RESET:-}"
  echo ""

  divider
  echo ""

  echo -e "  ${BOLD:-}Next Steps:${RESET:-}"
step_arrow "${YELLOW:-}1. Accept the Connection Request${NC:-}"
echo -e "     Open the VersaVoice AI mobile app. You will see a new connection"
echo -e "     invitation from your agent. Accept it to establish comms."
step_arrow "${YELLOW:-}2. The First Pulse (Agent Activation)${NC:-}"
echo -e "     Wait for the CRON schedule to awaken the agent (or run manually:"
echo -e "     ${BOLD:-}sudo ${DEPLOYED_CORE_INFRA}/lifeline.sh --force${RESET:-})."
echo -e "     The agent will process its seeded Welcome Task and introduce itself."
echo ""
echo -e "  ${DIM:-}You are now running a production infrastructure featuring:${RESET:-}"
  echo -e "  ${DIM:-} • Native Emotional Intelligence     • Compute-Zero Efficiency${RESET:-}"
  echo -e "  ${DIM:-} • OS-Level Space Sandboxing       • Cross-Lingual Bridges${RESET:-}"
  echo ""
fi  # end install/update summary branch

license_notice
echo ""

# Offer to auto-launch agitop
if [ -x /usr/local/bin/agitop ]; then
  if confirm_accent "Launch agitop now?"; then
    echo ""
    step_info "Starting agitop..."
    echo -e "  ${DIM:-}(Press 'q' to quit, '?' for help)${RESET:-}"
    echo ""
    /usr/local/bin/agitop \
      --config "/etc/versa-agi/coa_config.json" \
      --cycle-id "/var/lib/versa-agi/coa/.current_cycle_id"
  else
    echo ""
    step_info "To launch later: ${BOLD:-}sudo agitop${RESET:-}"
  fi
fi
echo ""

