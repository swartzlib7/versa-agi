#!/bin/bash
# ─────────────────────────────────────────────────────
# Versa AGi - Install Acceptance & Registration Telemetry
#
# Shared helpers for setup.sh (full install + --update).
# See design/Versa AGi - Production Plan.md §6.
# ─────────────────────────────────────────────────────

# Requires: ini_get (from setup.sh), ok/info/warn (ui_lib.sh)
# Optional env: INSTALL_ACCEPTANCE_VERSION, WATCHDOG_USER, DRY_RUN,
#               INSTALL_ACCEPTANCE_JSON, INSTALL_ACCEPTANCE_SETUP_INI

INSTALL_ACCEPTANCE_JSON="${INSTALL_ACCEPTANCE_JSON:-/etc/versa-agi/install-acceptance.json}"
INSTALL_ACCEPTANCE_SETUP_INI="${INSTALL_ACCEPTANCE_SETUP_INI:-/etc/versa-agi/setup.ini}"
INSTALL_ACCEPTANCE_REG_CONF="${INSTALL_ACCEPTANCE_REG_CONF:-/etc/versa-agi/registration.conf}"
INSTALL_ACCEPTANCE_PY="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/install_acceptance.py"
INSTALL_ACCEPTANCE_SOURCE_INI="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/setup.ini"
INSTALL_ACCEPTANCE_STRICT_BLOCK="${INSTALL_ACCEPTANCE_STRICT_BLOCK:-false}"
INSTALL_ACCEPTANCE_TERMS_URL="${INSTALL_ACCEPTANCE_TERMS_URL:-https://versavoice.ai/terms.html}"
INSTALL_ACCEPTANCE_PRIVACY_URL="${INSTALL_ACCEPTANCE_PRIVACY_URL:-https://versavoice.ai/privacy.html}"
INSTALL_ACCEPTANCE_MAX_ATTEMPTS=10

# ─── Update prerequisite ────────────────────────────
install_acceptance_require_existing_install() {
  if [ ! -f "${INSTALL_ACCEPTANCE_SETUP_INI}" ]; then
    error "--update requires an existing installation (${INSTALL_ACCEPTANCE_SETUP_INI} not found). Run 'sudo ./setup.sh' first."
  fi
}

# ─── Load saved email from prior acceptance JSON ────
_install_acceptance_load_existing_email() {
  if [ ! -f "${INSTALL_ACCEPTANCE_JSON}" ]; then
    return
  fi
  python3 - "${INSTALL_ACCEPTANCE_JSON}" <<'PY' 2>/dev/null || true
import json, sys
try:
    with open(sys.argv[1], encoding="utf-8") as f:
        data = json.load(f)
    email = data.get("email")
    if email:
        print(email)
except Exception:
    pass
PY
}

# ─── Accent text (brand cyan — matches box borders) ─
_install_acceptance_accent() {
  : "${BCYAN:=}"
  : "${RESET:=}"
  printf '%b%s%b' "${BCYAN}" "$1" "${RESET}"
}

# ─── Branded copy helpers ───────────────────────────
_install_acceptance_product() {
  _install_acceptance_accent "Versa AGi"
}

_install_acceptance_company() {
  _install_acceptance_accent "VersaVoice AI LLC"
}

_install_acceptance_brand() {
  _install_acceptance_accent "VersaVoice AI"
}

_install_acceptance_link() {
  _install_acceptance_accent "$1"
}

# Prefer ui_lib tty helpers; fallback if sourced without ui_lib.
_install_acceptance_tty_read() {
  if declare -F tty_read >/dev/null 2>&1; then
    tty_read "$@"
  elif [ -r /dev/tty ]; then
    read "$@" </dev/tty
  else
    read "$@"
  fi
}

_install_acceptance_tty_prompt_read() {
  local prompt="$1"
  shift
  if declare -F tty_prompt_read >/dev/null 2>&1; then
    tty_prompt_read "${prompt}" "$@"
    return
  fi
  if [ -w /dev/tty ]; then
    printf '%s' "${prompt}" >/dev/tty
  else
    printf '%s' "${prompt}" >&2
  fi
  _install_acceptance_tty_read "$@"
}

# Trim CR/space from a tty reply (OrbStack often needs Enter; may send \r).
_install_acceptance_trim_reply() {
  printf '%s' "$1" | tr -d '\r' | sed 's/^[[:space:]]*//;s/[[:space:]]*$//'
}

# Line-based y/n (no -n 1 — single-key read is unreliable on OrbStack /dev/tty).
# Echoes normalized reply to stdout when varname omitted; otherwise sets varname.
_install_acceptance_read_yn() {
  local prompt="$1"
  local varname="${2:-}"
  local raw=""
  _install_acceptance_tty_prompt_read "${prompt}" -r raw
  raw="$(_install_acceptance_trim_reply "${raw}")"
  if [ -n "${varname}" ]; then
    printf -v "${varname}" '%s' "${raw}"
  else
    REPLY="${raw}"
  fi
  # Enter already advanced the line on the tty; keep a blank line for layout.
  echo "" >&2
}

# ─── Box-style input line (matches text_box body) ───
_install_acceptance_input_line() {
  local label="$1"
  local value="${2:-}"
  local out=""
  : "${BCYAN:=}"
  : "${RESET:=}"
  out="  ${BCYAN}│${RESET}  ${label}"
  if [ -n "${value}" ]; then
    out="${out}["
    out="${out}${value}"
    out="${out}]: "
  else
    out="${out}: "
  fi
  if [ -w /dev/tty ]; then
    echo -e -n "${out}" >/dev/tty
  else
    echo -e -n "${out}"
  fi
}

# ─── Email format check (user@domain.tld) ───────────
_install_acceptance_valid_email() {
  local candidate="$1"
  [ -n "${candidate}" ] || return 1
  python3 -c 'import re, sys; sys.exit(0 if re.match(r"^[^\s@]+@[^\s@]+\.[^\s@]+$", sys.argv[1]) else 1)' \
    "${candidate}" 2>/dev/null
}

# ─── Optional email prompt (install + update) ───────
install_acceptance_email_prompt() {
  local existing reply done=false shown=false
  existing="$(_install_acceptance_load_existing_email)"

  while [ "${done}" = false ]; do
    if [ "${shown}" = false ]; then
      if declare -F text_box >/dev/null 2>&1; then
        if [ -n "${existing}" ]; then
          text_box "OPTIONAL CONTACT" \
            "Enter your email for release notes and community updates." \
            "" \
            "Press Enter to keep the address on file." \
            "Type - to clear it, or enter a new address."
        else
          text_box "OPTIONAL CONTACT" \
            "Enter your email for release notes and community updates." \
            "Press Enter to skip."
        fi
      else
        echo ""
        echo "Optional: Enter your email for release notes and community updates"
        if [ -n "${existing}" ]; then
          echo "(press Enter to keep on file, type - to clear, or enter a new address)"
        else
          echo "(press Enter to skip)"
        fi
      fi
      shown=true
    fi

    if [ -n "${existing}" ]; then
      _install_acceptance_input_line "Email" "${existing}"
      _install_acceptance_tty_read -r reply
      case "${reply}" in
        "") INSTALL_ACCEPTANCE_EMAIL="${existing}"; done=true ;;
        "-") INSTALL_ACCEPTANCE_EMAIL=""; done=true ;;
        *)
          if _install_acceptance_valid_email "${reply}"; then
            INSTALL_ACCEPTANCE_EMAIL="${reply}"
            done=true
          else
            warn "Invalid email. Use user@domain.tld, Enter to keep current, or - to clear."
          fi
          ;;
      esac
    else
      _install_acceptance_input_line "Email"
      _install_acceptance_tty_read -r reply
      case "${reply}" in
        "") INSTALL_ACCEPTANCE_EMAIL=""; done=true ;;
        "-")
          warn "No email on file to clear. Enter a valid address or press Enter to skip."
          ;;
        *)
          if _install_acceptance_valid_email "${reply}"; then
            INSTALL_ACCEPTANCE_EMAIL="${reply}"
            done=true
          else
            warn "Invalid email. Use user@domain.tld or press Enter to skip."
          fi
          ;;
      esac
    fi
  done

  export INSTALL_ACCEPTANCE_EMAIL
  echo ""
}

# ─── Welcome (full install only) ────────────────────
install_acceptance_welcome() {
  local hdr_line company_line intro_line terms_hdr cloud_line
  local link_terms link_privacy commercial_line

  hdr_line="$(_install_acceptance_product) — Agentic General infrastructure"
  company_line="by $(_install_acceptance_company)"
  intro_line="$(_install_acceptance_product) gives you a persistent, local AI team:"
  terms_hdr="$(_install_acceptance_brand) Terms of Service and Privacy Policy apply"
  cloud_line="when using $(_install_acceptance_brand) cloud features:"
  link_terms="  $(_install_acceptance_link "${INSTALL_ACCEPTANCE_TERMS_URL}")"
  link_privacy="  $(_install_acceptance_link "${INSTALL_ACCEPTANCE_PRIVACY_URL}")"
  commercial_line="  • Commercial production use: contact $(_install_acceptance_accent "business@versavoice.ai")"

  if declare -F text_box >/dev/null 2>&1; then
    text_box "INSTALL ACCEPTANCE" \
      "${hdr_line}" \
      "${company_line}" \
      "" \
      "${intro_line}" \
      "agents with memory, scheduling, and real execution" \
      "— working with you and your team, globally." \
      "" \
      "This setup will:" \
      "  • Provision your local AI agent infrastructure" \
      "  • Create dedicated OS users (per-agent sandboxes)" \
      "  • Store configuration under /etc and /var/lib/versa-agi" \
      "" \
      "Your agents run inside isolated OS-user sandboxes" \
      "— never as root." \
      "" \
      "Licensed under BSL-1.1 (LICENSE.md in this repository)." \
      "  • Personal / non-commercial production use: permitted" \
      "${commercial_line}" \
      "" \
      "${terms_hdr}" \
      "${cloud_line}" \
      "${link_terms}" \
      "${link_privacy}"
  else
    : "${BCYAN:=}"
    : "${RESET:=}"
    echo ""
    echo -e "$(_install_acceptance_product) — Agentic General infrastructure"
    echo -e "by $(_install_acceptance_company)"
    echo ""
    echo -e "$(_install_acceptance_product) gives you a persistent, local AI team: agents with memory,"
    echo "scheduling, and real execution — working with you and your team, globally."
    echo ""
    echo "This setup will:"
    echo "  • Provision your local AI agent infrastructure on this machine"
    echo "  • Create dedicated OS users so each agent runs in its own sandbox"
    echo "  • Store configuration under /etc/versa-agi and /var/lib/versa-agi"
    echo ""
    echo "Your agents run inside isolated OS-user sandboxes — never as root."
    echo ""
    echo "Licensed under BSL-1.1 (LICENSE.md in this repository)."
    echo "  • Personal and non-commercial production use: permitted"
    echo -e "  • Commercial production use: contact ${BCYAN}business@versavoice.ai${RESET}"
    echo ""
    echo -e "$(_install_acceptance_brand) Terms of Service and Privacy Policy apply when using"
    echo -e "$(_install_acceptance_brand) cloud features:"
    echo -e "  $(_install_acceptance_link "${INSTALL_ACCEPTANCE_TERMS_URL}")"
    echo -e "  $(_install_acceptance_link "${INSTALL_ACCEPTANCE_PRIVACY_URL}")"
    echo ""
  fi

  _install_acceptance_read_yn "  Accept and continue? [y/N] "
  if [[ ! $REPLY =~ ^[Yy]([Ee][Ss])?$ ]]; then
    info "Setup cancelled - no changes made."
    exit 0
  fi

  INSTALL_ACCEPTANCE_AT_UTC="$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
  export INSTALL_ACCEPTANCE_AT_UTC

  install_acceptance_email_prompt
}

# ─── Feature flags (setup.ini [features]) ───────────
# The optional dashboard surfaces are prompted here (right after the license /
# update acceptance, D34). Each defaults DISABLED. Answers are captured into
# VERSA_FEATURE_* env vars now (near the license, where the Organization
# disclaimer belongs) and written to the deployed setup.ini [features] later by
# install_acceptance_persist_features (after the file is deployed + reconciled).

# Read one [features] key from the deployed setup.ini (default when absent).
_install_acceptance_features_get() {
  local key="$1" default="${2:-false}"
  local ini="${INSTALL_ACCEPTANCE_SETUP_INI}"
  [ -f "${ini}" ] || { echo "${default}"; return; }
  awk -F= -v section="features" -v key="${key}" -v def="${default}" '
    /^\[/ { gsub(/[][]/, "", $0); current=$0 }
    current == section && $1 == key { v=substr($0, index($0, "=") + 1); gsub(/[ \t]/,"",v); print v; found=1; exit }
    END { if (!found) print def }
  ' "${ini}" 2>/dev/null || echo "${default}"
}

# yes/true → "true", else "false". Whitespace/newline tolerant (the value may
# arrive with a stray newline from a command-substituted prompt).
_install_acceptance_norm_bool() {
  case "$(printf '%s' "$1" | tr -d '[:space:]' | tr '[:upper:]' '[:lower:]')" in
    1|true|yes|on|y) echo "true" ;;
    *) echo "false" ;;
  esac
}

# Prompt [y/N] (or [Y/n] when current is enabled). Echoes true/false.
_install_acceptance_feature_ask() {
  local prompt="$1" current="$2" reply hint default_yes=false
  if [ "${current}" = "true" ]; then
    hint="[Y/n]"; default_yes=true
  else
    hint="[y/N]"
  fi
  # Line-based (y + Enter). Breaks MUST go to stderr — this runs in $().
  _install_acceptance_read_yn "  ${prompt} ${hint} " reply
  if [ -z "${reply}" ]; then
    [ "${default_yes}" = true ] && echo "true" || echo "false"
    return
  fi
  [[ ${reply} =~ ^[Yy]([Ee][Ss])?$ ]] && echo "true" || echo "false"
}

# The Organization EXPERIMENTAL + sudo-power disclaimer (second confirmation).
_install_acceptance_org_disclaimer() {
  if declare -F text_box >/dev/null 2>&1; then
    text_box "EXPERIMENTAL FEATURE — ORGANIZATION" \
      "Would you like to enable the Organization surface? It's a" \
      "read-write business/accounting workspace in agitop." \
      "" \
      "Heads-up:" \
      "  • This feature is EXPERIMENTAL and evolving." \
      "  • agitop runs as root, and the Organization writes" \
      "    go through the same tools your agents use." \
      "  • Anyone with \`sudo\` on this machine can therefore" \
      "    read and change this data — and, more broadly," \
      "    control Versa AGi, its agents, and all features." \
      "" \
      "Keep \`sudo\` access to people you trust. You can turn" \
      "this off any time by re-running setup."
  else
    echo ""
    echo "  EXPERIMENTAL FEATURE — ORGANIZATION"
    echo "  Would you like to enable the Organization surface? It's an"
    echo "  experimental read-write business/accounting workspace."
    echo "  agitop runs as root and Organization writes use the same tools your"
    echo "  agents use, so anyone with sudo on this machine can read/change this"
    echo "  data and control Versa AGi. Keep sudo access to people you trust."
    echo ""
  fi
}

# A short explanatory note shown above a plain feature prompt (matches the
# Organization pattern: explain what it is, then ask). First line gets a brand
# bullet; continuation lines are passed plainly and printed aligned underneath.
_install_acceptance_feature_note() {
  : "${BCYAN:=}"
  : "${RESET:=}"
  echo -e "  ${BCYAN}•${RESET} $1"
}

_install_acceptance_feature_cont() {
  echo -e "    $1"
}

# Prompt for every feature, capturing answers to VERSA_FEATURE_* env vars.
install_acceptance_feature_prompts() {
  if [ "${DRY_RUN:-false}" = true ]; then
    info "[DRY-RUN] Would prompt for optional feature flags"
    return 0
  fi

  if declare -F text_box >/dev/null 2>&1; then
    text_box "OPTIONAL FEATURES" \
      "Enable optional dashboard surfaces. Each defaults to OFF." \
      "You can change these any time by re-running setup."
  else
    echo ""
    echo "Optional features (each defaults to OFF; re-run setup to change):"
    echo ""
  fi

  # Organization — disclaimer first, then the gated prompt.
  local org_current
  org_current="$(_install_acceptance_features_get organization_ui false)"
  _install_acceptance_org_disclaimer
  export VERSA_FEATURE_ORGANIZATION_UI="$(_install_acceptance_feature_ask \
    "Would you like to enable the Organization surface?" "${org_current}")"

  # Plain features — a short note explaining each, then the prompt.
  echo ""
  _install_acceptance_feature_note \
    "Utility Models — run fixed-prompt AI models that produce artifacts (text,"
  _install_acceptance_feature_cont \
    "image, audio) on a schedule or on demand, without a full agent cycle."
  export VERSA_FEATURE_UTILITY_MODELS_UI="$(_install_acceptance_feature_ask \
    "Enable Utility Models UI?" "$(_install_acceptance_features_get utility_models_ui false)")"

  echo ""
  _install_acceptance_feature_note \
    "Script Tasks — schedule deterministic shell scripts from your AGi-Tools"
  _install_acceptance_feature_cont \
    "repo, run by lifeline on a cadence (no agent spawn, no LLM)."
  export VERSA_FEATURE_SCRIPT_TASKS_UI="$(_install_acceptance_feature_ask \
    "Enable Script Tasks UI?" "$(_install_acceptance_features_get script_tasks_ui false)")"

  echo ""
  _install_acceptance_feature_note \
    "Output Routing — route an agent's output generation (e.g. image/audio) to"
  _install_acceptance_feature_cont \
    "a chosen model per cycle (the Output Routing tab in Model Routing)."
  export VERSA_FEATURE_OUTPUT_ROUTING_UI="$(_install_acceptance_feature_ask \
    "Enable Output Routing UI?" "$(_install_acceptance_features_get output_routing_ui false)")"
}

# Write a single [features] key into the deployed setup.ini (create section if
# absent), then sync to source. Used by install_acceptance_persist_features.
_install_acceptance_features_set() {
  local key="$1" value="$2"
  local ini="${INSTALL_ACCEPTANCE_SETUP_INI}"
  [ -f "${ini}" ] || return 1
  if ! grep -q '^\[features\]' "${ini}" 2>/dev/null; then
    printf '\n[features]\n' >> "${ini}"
  fi
  if awk -v k="${key}" '
      /^\[/ { gsub(/[][]/,"",$0); sec=$0 }
      sec=="features" && $0 ~ "^"k"=" { found=1 }
      END { exit(found?0:1) }' "${ini}" 2>/dev/null; then
    sed -i "/^\[features\]/,/^\[/ s|^${key}=.*|${key}=${value}|" "${ini}"
  else
    sed -i "/^\[features\]/a ${key}=${value}" "${ini}"
  fi
}

# Persist the captured feature choices to the deployed setup.ini [features].
# Called AFTER setup.ini is deployed + reconciled, so the values are the final
# word (and the next --update reconcile carries them forward — they are not
# stock-owned keys).
install_acceptance_persist_features() {
  if [ "${DRY_RUN:-false}" = true ]; then
    info "[DRY-RUN] Would persist feature flags to setup.ini"
    return 0
  fi
  [ -n "${VERSA_FEATURE_ORGANIZATION_UI:-}" ] || return 0   # prompts never ran
  _install_acceptance_features_set organization_ui   "$(_install_acceptance_norm_bool "${VERSA_FEATURE_ORGANIZATION_UI:-false}")"
  _install_acceptance_features_set utility_models_ui "$(_install_acceptance_norm_bool "${VERSA_FEATURE_UTILITY_MODELS_UI:-false}")"
  _install_acceptance_features_set script_tasks_ui   "$(_install_acceptance_norm_bool "${VERSA_FEATURE_SCRIPT_TASKS_UI:-false}")"
  _install_acceptance_features_set output_routing_ui "$(_install_acceptance_norm_bool "${VERSA_FEATURE_OUTPUT_ROUTING_UI:-false}")"
  _install_acceptance_sync_source_ini
  ok "Feature flags saved to setup.ini [features]"
}
_install_acceptance_emit_version_block() {
  local result="$1"
  local line first=true
  : "${GLYPH_FAIL:=✗}"
  : "${RED:=\033[0;31m}"
  : "${NC:=\033[0m}"
  while IFS= read -r line; do
    if [ "${first}" = true ]; then
      echo -e "  ${RED}${GLYPH_FAIL} ERROR:${NC} ${line}" >&2
      first=false
    else
      echo -e "         ${line}" >&2
    fi
  done < <(python3 "${INSTALL_ACCEPTANCE_PY}" format-block "${result}" 2>/dev/null)
  exit 1
}

# ─── Pre-install version gate (full install only) ───
install_acceptance_version_gate() {
  local rc=0 result

  if [ "${DRY_RUN:-false}" = true ]; then
    info "[DRY-RUN] Would check install version policy"
    return 0
  fi

  if [ ! -f "${INSTALL_ACCEPTANCE_PY}" ]; then
    warn "Install registration module not found — skipping version gate"
    return 0
  fi

  set +e
  result="$(python3 "${INSTALL_ACCEPTANCE_PY}" gate --json 2>/dev/null)"
  rc=$?
  set -e

  if [ -z "${result}" ]; then
    warn "Version policy check skipped (offline — will retry after install)"
    return 0
  fi

  if [ "${rc}" -eq 2 ]; then
    _install_acceptance_emit_version_block "${result}"
  fi

  ok "Version policy check passed"
}

# ─── Update acknowledgment (--update only) ──────────
install_acceptance_update_prompt() {
  local update_line
  install_acceptance_require_existing_install

  update_line="$(_install_acceptance_product) update — registering this event (BSL-1.1)."

  if declare -F text_box >/dev/null 2>&1; then
    text_box "UPDATE ACCEPTANCE" \
      "${update_line}" \
      "" \
      "Continuing will record this update and deploy changes to" \
      "your existing installation."
  else
    echo ""
    echo -e "${update_line}"
    echo ""
  fi

  _install_acceptance_read_yn "  Continue? [Y/n] "
  if [[ $REPLY =~ ^[Nn]([Oo])?$ ]]; then
    info "Update cancelled."
    exit 0
  fi
  INSTALL_ACCEPTANCE_AT_UTC="$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
  export INSTALL_ACCEPTANCE_AT_UTC

  install_acceptance_email_prompt
}

# ─── VersaVoice AI enable prompt (Step 4 — single ask) ─
install_acceptance_vv_prompt() {
  local default_enabled="${1:-true}"
  local intro_line1 intro_line2 intro_line3
  local policy_line usage_line link_terms link_privacy

  intro_line1="$(_install_acceptance_brand) enables cloud messaging between you, your agents and your connections"
  intro_line2="via the $(_install_acceptance_brand) mobile/web app. This is optional — agents can communicate"
  intro_line3="locally via the agitop dashboard without a VersaVoice account."
  usage_line="  • Messaging uses your $(_install_acceptance_brand) usage minutes"
  policy_line="  • $(_install_acceptance_brand) Terms of Service and Privacy Policy apply:"
  link_terms="    $(_install_acceptance_link "${INSTALL_ACCEPTANCE_TERMS_URL}")"
  link_privacy="    $(_install_acceptance_link "${INSTALL_ACCEPTANCE_PRIVACY_URL}")"

  if declare -F text_box >/dev/null 2>&1; then
    text_box "VERSAVOICE AI CLOUD" \
      "${intro_line1}" \
      "${intro_line2}" \
      "${intro_line3}" \
      "" \
      "  • Messages may leave this machine" \
      "  • Agents share the sponsor API token you provide" \
      "${usage_line}" \
      "${policy_line}" \
      "${link_terms}" \
      "${link_privacy}"
  else
    echo ""
    echo -e "${intro_line1}"
    echo -e "${intro_line2}"
    echo "${intro_line3}"
    echo ""
    echo "  • Messages may leave this machine"
    echo "  • Agents share the sponsor API token you provide"
    echo -e "${usage_line}"
    echo -e "${policy_line}"
    echo -e "${link_terms}"
    echo -e "${link_privacy}"
    echo ""
  fi

  if [ "${default_enabled}" = "false" ]; then
    _install_acceptance_read_yn "  Enable $(_install_acceptance_brand)? [y/N] "
    if [[ $REPLY =~ ^[Yy]([Ee][Ss])?$ ]]; then
      export ENABLE_VV="true"
    else
      export ENABLE_VV="false"
    fi
  else
    _install_acceptance_read_yn "  Enable $(_install_acceptance_brand)? [Y/n] "
    if [[ $REPLY =~ ^[Nn]([Oo])?$ ]]; then
      export ENABLE_VV="false"
    else
      export ENABLE_VV="true"
    fi
  fi

  if [ "${ENABLE_VV}" = "false" ]; then
    info "$(_install_acceptance_brand) disabled — agents will use local messaging only"
    info "You can enable VV later via the agitop dashboard toggle"
  fi
}

# ─── INI helpers ([registration] section) ───────────
_install_acceptance_ini_ensure() {
  local ini="${INSTALL_ACCEPTANCE_SETUP_INI}"
  if [ ! -f "${ini}" ]; then
    return 1
  fi
  if ! grep -q '^\[registration\]' "${ini}" 2>/dev/null; then
    cat >> "${ini}" <<'REGSECTION'

[registration]
# Runtime submission state (endpoint + key live in registration.conf)
acceptance_file=/etc/versa-agi/install-acceptance.json
registration_submitted=false
registration_submitted_at=
registration_last_heartbeat_at=
registration_last_error=
registration_attempt_count=0
REGSECTION
  fi
}

_install_acceptance_sync_source_ini() {
  local deployed="${INSTALL_ACCEPTANCE_SETUP_INI}"
  local source="${INSTALL_ACCEPTANCE_SOURCE_INI}"
  if [ ! -f "${deployed}" ] || [ ! -f "${source}" ]; then
    return 0
  fi
  if [ "$(realpath "${source}" 2>/dev/null)" = "$(realpath "${deployed}" 2>/dev/null)" ]; then
    return 0
  fi
  cp "${deployed}" "${source}" 2>/dev/null || true
  chmod 600 "${source}" 2>/dev/null || true
}

_install_acceptance_ini_set() {
  local key="$1"
  local value="$2"
  local ini="${INSTALL_ACCEPTANCE_SETUP_INI}"
  _install_acceptance_ini_ensure || return 1
  if grep -q "^${key}=" "${ini}" 2>/dev/null; then
    sed -i "/^\[registration\]/,/^\[/ s|^${key}=.*|${key}=${value}|" "${ini}"
  else
    sed -i "/^\[registration\]/a ${key}=${value}" "${ini}"
  fi
  _install_acceptance_sync_source_ini
}

_install_acceptance_ini_get() {
  local key="$1"
  local default="${2:-}"
  local ini="${INSTALL_ACCEPTANCE_SETUP_INI}"
  if [ ! -f "${ini}" ]; then
    echo "${default}"
    return
  fi
  awk -F= -v section="registration" -v key="${key}" -v def="${default}" '
    /^\[/ { gsub(/[][]/, "", $0); current=$0 }
    current == section && $1 == key { print substr($0, index($0, "=") + 1); found=1; exit }
    END { if (!found) print def }
  ' "${ini}" 2>/dev/null || echo "${default}"
}

# ─── Payload builders ───────────────────────────────
_install_acceptance_platform() {
  if [ -f /etc/os-release ]; then
    # shellcheck disable=SC1091
    . /etc/os-release
    echo "${ID:-unknown}-${VERSION_ID:-unknown}"
  else
    echo "unknown"
  fi
}

_install_acceptance_hostname_hash() {
  local host
  host="$(hostname 2>/dev/null || echo unknown)"
  if command -v sha256sum >/dev/null 2>&1; then
    printf '%s' "${host}" | sha256sum | awk '{print "sha256:" $1}'
  elif command -v shasum >/dev/null 2>&1; then
    printf '%s' "${host}" | shasum -a 256 | awk '{print "sha256:" $1}'
  else
    echo "sha256:unavailable"
  fi
}

_install_acceptance_public_ip() {
  local ip=""
  if command -v curl >/dev/null 2>&1; then
    ip="$(curl -fsS --max-time 5 https://api.ipify.org 2>/dev/null || true)"
  fi
  if [ -n "${ip}" ]; then
    echo "${ip}"
  else
    echo "null"
  fi
}

_install_acceptance_write_json() {
  local event="$1"
  local install_mode="$2"
  local accepted_at="$3"
  local email="$4"
  local versavoice_enabled="$5"
  local version="${INSTALL_ACCEPTANCE_VERSION:-unknown}"
  local platform ip hostname_hash ip_value

  platform="$(_install_acceptance_platform)"
  ip_value="$(_install_acceptance_public_ip)"
  hostname_hash="$(_install_acceptance_hostname_hash)"

  if [ "${ip_value}" = "null" ]; then
    ip_value=""
  fi

  # Feature set — the operator's enabled/disabled choices ride along with the
  # registration data (D34). Prefer the just-captured VERSA_FEATURE_* answers;
  # fall back to the persisted setup.ini [features] values.
  local feat_org feat_util feat_script feat_output
  feat_org="$(_install_acceptance_norm_bool "${VERSA_FEATURE_ORGANIZATION_UI:-$(_install_acceptance_features_get organization_ui false)}")"
  feat_util="$(_install_acceptance_norm_bool "${VERSA_FEATURE_UTILITY_MODELS_UI:-$(_install_acceptance_features_get utility_models_ui false)}")"
  feat_script="$(_install_acceptance_norm_bool "${VERSA_FEATURE_SCRIPT_TASKS_UI:-$(_install_acceptance_features_get script_tasks_ui false)}")"
  feat_output="$(_install_acceptance_norm_bool "${VERSA_FEATURE_OUTPUT_ROUTING_UI:-$(_install_acceptance_features_get output_routing_ui false)}")"

  mkdir -p /etc/versa-agi
  python3 - "${INSTALL_ACCEPTANCE_JSON}" <<'PY' "${event}" "${install_mode}" "${accepted_at}" "${email}" "${versavoice_enabled}" "${version}" "${platform}" "${hostname_hash}" "${ip_value}" "${feat_org}" "${feat_util}" "${feat_script}" "${feat_output}"
import json, sys
(out, event, install_mode, accepted_at, email, vv, version, platform,
 hostname_hash, ip_value, feat_org, feat_util, feat_script, feat_output) = sys.argv[1:15]
payload = {
    "event": event,
    "product": "versa-agi",
    "company": "VersaVoice AI LLC",
    "version": version,
    "install_mode": install_mode,
    "accepted_at_utc": accepted_at,
    "ip_address": ip_value or None,
    "email": email or None,
    "email_provided": bool(email),
    "license": "BSL-1.1",
    "platform": platform,
    "hostname_hash": hostname_hash,
    "versavoice_enabled": vv == "true",
    "features": {
        "organization_ui": feat_org == "true",
        "utility_models_ui": feat_util == "true",
        "script_tasks_ui": feat_script == "true",
        "output_routing_ui": feat_output == "true",
    },
}
with open(out, "w", encoding="utf-8") as f:
    json.dump(payload, f, indent=2)
    f.write("\n")
PY

  chown "${WATCHDOG_USER}:${WATCHDOG_USER}" "${INSTALL_ACCEPTANCE_JSON}" 2>/dev/null || true
  chmod 640 "${INSTALL_ACCEPTANCE_JSON}"
}

# ─── POST submission (delegates to install_acceptance.py) ─
install_acceptance_submit() {
  local strict_flag="" rc=0 result msg

  if [ "${DRY_RUN:-false}" = true ]; then
    info "[DRY-RUN] Would submit install acceptance telemetry"
    return 0
  fi

  if [ ! -f "${INSTALL_ACCEPTANCE_PY}" ]; then
    warn "Install registration module not found"
    return 0
  fi

  if [ "${INSTALL_ACCEPTANCE_STRICT_BLOCK}" = true ]; then
    strict_flag="--strict-block"
  fi

  set +e
  result="$(python3 "${INSTALL_ACCEPTANCE_PY}" submit ${strict_flag} --json 2>/dev/null)"
  rc=$?
  set -e

  if [ -z "${result}" ]; then
    warn "Install registration deferred (will retry via agitop)"
    return 0
  fi

  if [ "${rc}" -eq 2 ]; then
    _install_acceptance_emit_version_block "${result}"
  fi

  if python3 -c 'import json,sys; d=json.loads(sys.argv[1]); sys.exit(0 if d.get("registration_submitted","false").lower()=="true" else 1)' "${result}" 2>/dev/null; then
    ok "Install registration submitted"
    _install_acceptance_sync_source_ini
    return 0
  fi

  msg="$(python3 -c 'import json,sys; d=json.loads(sys.argv[1]); print(d.get("message",""))' "${result}" 2>/dev/null || true)"
  if [ -n "${msg}" ]; then
    warn "Install registration deferred: ${msg} (will retry via agitop)"
  else
    warn "Install registration deferred (will retry via agitop)"
  fi
}

# ─── Record full install event (after install completes) ─
install_acceptance_record_full() {
  local versavoice_enabled="${1:-false}"
  local accepted_at="${INSTALL_ACCEPTANCE_AT_UTC:-$(date -u '+%Y-%m-%dT%H:%M:%SZ')}"
  local email="${INSTALL_ACCEPTANCE_EMAIL:-}"

  if [ "${DRY_RUN:-false}" = true ]; then
    info "[DRY-RUN] Would record install acceptance"
    return 0
  fi

  _install_acceptance_write_json \
    "install_acceptance" "full" "${accepted_at}" "${email}" "${versavoice_enabled}"

  _install_acceptance_ini_set acceptance_file "${INSTALL_ACCEPTANCE_JSON}"
  _install_acceptance_ini_set registration_submitted "false"

  INSTALL_ACCEPTANCE_STRICT_BLOCK=true
  install_acceptance_submit
  INSTALL_ACCEPTANCE_STRICT_BLOCK=false
}

# ─── Record update event ────────────────────────────
install_acceptance_record_update() {
  local versavoice_enabled
  local accepted_at="${INSTALL_ACCEPTANCE_AT_UTC:-$(date -u '+%Y-%m-%dT%H:%M:%SZ')}"
  local email="${INSTALL_ACCEPTANCE_EMAIL:-}"
  local event="update_acceptance"
  local install_mode="update"
  local already_submitted
  local last_heartbeat hb_epoch stale_epoch heartbeat_needed=false
  local submitted_at

  if [ "${DRY_RUN:-false}" = true ]; then
    info "[DRY-RUN] Would record update acceptance"
    return 0
  fi

  already_submitted="$(_install_acceptance_ini_get registration_submitted "false")"
  versavoice_enabled="$(ini_get versavoice enabled false)"

  # Legacy bug: --update cleared registration_submitted despite a prior successful submit
  if [ "${already_submitted}" != "true" ]; then
    submitted_at="$(_install_acceptance_ini_get registration_submitted_at "")"
    if [ -n "${submitted_at}" ]; then
      _install_acceptance_ini_set registration_submitted "true"
      already_submitted="true"
      ok "Registration status restored from prior successful submit"
    fi
  fi

  # Legacy install: no prior acceptance file - treat as first registration
  if [ ! -f "${INSTALL_ACCEPTANCE_JSON}" ]; then
    event="install_acceptance"
    install_mode="full"
  fi

  _install_acceptance_write_json \
    "${event}" "${install_mode}" "${accepted_at}" "${email}" "${versavoice_enabled}"

  _install_acceptance_ini_set acceptance_file "${INSTALL_ACCEPTANCE_JSON}"

  # Already registered (and not an update event) — preserve submitted flag; heartbeat only when >7 days stale
  if [ "${already_submitted}" = "true" ] && [ "${event}" != "update_acceptance" ]; then
    last_heartbeat="$(_install_acceptance_ini_get registration_last_heartbeat_at "")"
    if [ -z "${last_heartbeat}" ]; then
      heartbeat_needed=true
    else
      hb_epoch=$(date -d "${last_heartbeat}" +%s 2>/dev/null || echo 0)
      stale_epoch=$(date -d '7 days ago' +%s)
      if [ "${hb_epoch}" -lt "${stale_epoch}" ]; then
        heartbeat_needed=true
      fi
    fi
    if [ "${heartbeat_needed}" = true ]; then
      if python3 "${INSTALL_ACCEPTANCE_PY}" heartbeat 2>/dev/null; then
        ok "Registration heartbeat sent (update)"
      else
        warn "Registration active — heartbeat deferred (offline or endpoint unavailable)"
      fi
    else
      ok "Registration already active (heartbeat not due)"
    fi
    _install_acceptance_sync_source_ini
    return 0
  fi

  # Not yet registered — fresh retry budget after update acceptance
  _install_acceptance_ini_set registration_attempt_count "0"
  install_acceptance_submit
}
