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

# Trim CR/space from a tty reply (OrbStack may send \r).
_install_acceptance_trim_reply() {
  printf '%s' "$1" | tr -d '\r' | sed 's/^[[:space:]]*//;s/[[:space:]]*$//'
}

# Prompt + line read in THIS function (locals + nested read across /dev/tty
# redirects are unreliable). Prefer stdin when it is a tty — OrbStack often
# delivers keys there while /dev/tty hangs. Fall back to /dev/tty for curl|bash
# only when that device can actually be opened.
_install_acceptance_read_line() {
  local prompt="$1"
  local varname="${2:-REPLY}"
  # Distinct from caller locals — a nested `local raw` would shadow printf -v.
  local _ia_buf=""
  local use_tty=0

  if [ -t 0 ]; then
    printf '%s' "${prompt}" >&2
    read -r _ia_buf || true
  else
    # Probe open — `[ -w /dev/tty ]` can be true while open still fails.
    if { : >/dev/tty; } 2>/dev/null; then
      use_tty=1
    fi
    if [ "${use_tty}" -eq 1 ]; then
      printf '%s' "${prompt}" >/dev/tty
      if ! read -r _ia_buf </dev/tty 2>/dev/null; then
        printf '%s' "${prompt}" >&2
        read -r _ia_buf || true
      fi
    else
      printf '%s' "${prompt}" >&2
      read -r _ia_buf || true
    fi
  fi

  _ia_buf="$(_install_acceptance_trim_reply "${_ia_buf}")"
  printf -v "${varname}" '%s' "${_ia_buf}"
  echo "" >&2
}

# Line-based y/n (type y/n then Enter — not single-key -n 1).
_install_acceptance_read_yn() {
  local prompt="$1"
  local varname="${2:-REPLY}"
  _install_acceptance_read_line "${prompt}" "${varname}"
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
  if [ -t 0 ]; then
    echo -e -n "${out}" >&2
  elif [ -w /dev/tty ]; then
    echo -e -n "${out}" >/dev/tty 2>/dev/null || echo -e -n "${out}" >&2
  else
    echo -e -n "${out}" >&2
  fi
}

# ─── Email format check (user@domain.tld) ───────────
_install_acceptance_valid_email() {
  local candidate="$1"
  [ -n "${candidate}" ] || return 1
  python3 -c 'import re, sys; sys.exit(0 if re.match(r"^[^\s@]+@[^\s@]+\.[^\s@]+$", sys.argv[1]) else 1)' \
    "${candidate}" 2>/dev/null
}

# Derive bare 6-char call sign from email local-part: first3 + last3.
# Local-part < 6 after alnum strip: pad from sha256(email) then take first3+last3.
_install_acceptance_derive_call_sign() {
  local email="$1"
  python3 -c '
import hashlib, re, sys
email = (sys.argv[1] or "").strip().lower()
local = email.split("@", 1)[0]
local = re.sub(r"[^a-z0-9]", "", local)
alphabet = "abcdefghijklmnopqrstuvwxyz0123456789"
if len(local) < 6:
    h = hashlib.sha256(email.encode()).hexdigest()
    pad = []
    for i in range(0, len(h), 2):
        pad.append(alphabet[int(h[i:i+2], 16) % len(alphabet)])
        if len(local) + len(pad) >= 6:
            break
    local = (local + "".join(pad))[:6]
print(local[:3] + local[-3:])
' "${email}" 2>/dev/null
}

# Normalize user/default call sign to bare alnum (2–12); empty on failure.
_install_acceptance_bare_call_sign() {
  local raw="$1"
  python3 -c '
import re, sys
s = (sys.argv[1] or "").strip().lower()
s = s.strip("()[]{}")
s = re.sub(r"[^a-z0-9]", "", s)
sys.exit(1) if not (2 <= len(s) <= 12) else print(s)
' "${raw}" 2>/dev/null
}

# Upsert a key in [agent] of a setup.ini (create key if missing).
_install_acceptance_upsert_agent_key() {
  local file="$1" key="$2" val="$3"
  [ -f "${file}" ] || return 0
  if grep -q "^${key}=" "${file}" 2>/dev/null; then
    sed -i "s|^${key}=.*|${key}=${val}|" "${file}"
  elif grep -q "^first_name=" "${file}" 2>/dev/null; then
    sed -i "/^first_name=/a ${key}=${val}" "${file}"
  elif grep -q "^\[agent\]" "${file}" 2>/dev/null; then
    sed -i "/^\[agent\]/a ${key}=${val}" "${file}"
  else
    return 0
  fi
}

# Write call_sign + last_name to deployed + source setup.ini immediately so
# --update reconcile-config carries them forward (Step 13 also persists on update).
_install_acceptance_persist_call_sign() {
  local bare="${INSTALL_ACCEPTANCE_CALL_SIGN:-}"
  local last="${INI_AGENT_LAST_NAME:-}"
  local f
  [ -n "${bare}" ] || return 0
  [ -n "${last}" ] || last="(${bare})"
  for f in /etc/versa-agi/setup.ini "${SCRIPT_DIR:-}/setup.ini"; do
    [ -n "${f}" ] && [ -f "${f}" ] || continue
    _install_acceptance_upsert_agent_key "${f}" "call_sign" "${bare}"
    _install_acceptance_upsert_agent_key "${f}" "last_name" "${last}"
  done
}

# ─── Required email prompt (install + update) ───────
# Install email is siloed from the VersaVoice sponsor email — used for
# call-sign derivation, COA VV reuse (agiInstallEmail), and registration telemetry.
install_acceptance_email_prompt() {
  local existing reply done=false shown=false
  existing="$(_install_acceptance_load_existing_email)"

  # Non-interactive: keep prior email, or INSTALL_ACCEPTANCE_EMAIL if pre-set.
  case "$(printf '%s' "${VERSA_INSTALL_ACCEPT:-}" | tr '[:upper:]' '[:lower:]')" in
    1|true|yes|y)
      if [ -n "${INSTALL_ACCEPTANCE_EMAIL:-}" ] && _install_acceptance_valid_email "${INSTALL_ACCEPTANCE_EMAIL}"; then
        :
      elif [ -n "${existing}" ]; then
        INSTALL_ACCEPTANCE_EMAIL="${existing}"
      else
        echo "ERROR: Email is required. Set INSTALL_ACCEPTANCE_EMAIL=user@domain.tld for non-interactive setup." >&2
        exit 1
      fi
      export INSTALL_ACCEPTANCE_EMAIL
      return
      ;;
  esac

  while [ "${done}" = false ]; do
    if [ "${shown}" = false ]; then
      if declare -F text_box >/dev/null 2>&1; then
        if [ -n "${existing}" ]; then
          text_box "CONTACT EMAIL (REQUIRED)" \
            "Enter your email for release notes, community updates," \
            "and to identify this install's COA on VersaVoice." \
            "" \
            "Press Enter to keep the address on file, or enter a new address."
        else
          text_box "CONTACT EMAIL (REQUIRED)" \
            "Enter your email for release notes, community updates," \
            "and to identify this install's COA on VersaVoice." \
            "This email is kept separate from your VersaVoice account email."
        fi
      else
        echo ""
        echo "Required: Enter your email for release notes and COA identity"
        if [ -n "${existing}" ]; then
          echo "(press Enter to keep on file, or enter a new address)"
        fi
      fi
      shown=true
    fi

    if [ -n "${existing}" ]; then
      _install_acceptance_input_line "Email" "${existing}"
      _install_acceptance_read_line "" reply
      case "${reply}" in
        "") INSTALL_ACCEPTANCE_EMAIL="${existing}"; done=true ;;
        *)
          if _install_acceptance_valid_email "${reply}"; then
            INSTALL_ACCEPTANCE_EMAIL="${reply}"
            done=true
          else
            warn "Invalid email. Use user@domain.tld, or press Enter to keep current."
          fi
          ;;
      esac
    else
      _install_acceptance_input_line "Email"
      _install_acceptance_read_line "" reply
      case "${reply}" in
        "")
          warn "Email is required. Enter a valid user@domain.tld address."
          ;;
        *)
          if _install_acceptance_valid_email "${reply}"; then
            INSTALL_ACCEPTANCE_EMAIL="${reply}"
            done=true
          else
            warn "Invalid email. Use user@domain.tld."
          fi
          ;;
      esac
    fi
  done

  export INSTALL_ACCEPTANCE_EMAIL
  echo ""
}

# ─── COA call sign prompt (parenthesized last_name) ─
install_acceptance_call_sign_prompt() {
  local email="${INSTALL_ACCEPTANCE_EMAIL:-}"
  local derived default_bare current_last bare reply done=false shown=false

  if [ -z "${email}" ] || ! _install_acceptance_valid_email "${email}"; then
    echo "ERROR: Call sign requires a valid INSTALL_ACCEPTANCE_EMAIL." >&2
    exit 1
  fi

  derived="$(_install_acceptance_derive_call_sign "${email}")"
  [ -n "${derived}" ] || derived="versa0"

  current_last="${INI_AGENT_LAST_NAME:-}"
  default_bare="$(_install_acceptance_bare_call_sign "${current_last}" || true)"
  # Re-derive when unset, legacy (COA), or last_name still matches prior email's derived value.
  if [ -z "${default_bare}" ] || [ "${current_last}" = "(COA)" ] || [ "${current_last}" = "COA" ]; then
    default_bare="${derived}"
  elif [ -n "${INSTALL_ACCEPTANCE_PREV_EMAIL:-}" ] \
       && [ "${email}" != "${INSTALL_ACCEPTANCE_PREV_EMAIL}" ]; then
    local prev_derived
    prev_derived="$(_install_acceptance_derive_call_sign "${INSTALL_ACCEPTANCE_PREV_EMAIL}")"
    if [ "${default_bare}" = "${prev_derived}" ]; then
      default_bare="${derived}"
    fi
  fi

  case "$(printf '%s' "${VERSA_INSTALL_ACCEPT:-}" | tr '[:upper:]' '[:lower:]')" in
    1|true|yes|y)
      bare="${INSTALL_ACCEPTANCE_CALL_SIGN:-${default_bare}}"
      bare="$(_install_acceptance_bare_call_sign "${bare}" || echo "${derived}")"
      INSTALL_ACCEPTANCE_CALL_SIGN="${bare}"
      INI_AGENT_LAST_NAME="(${bare})"
      export INSTALL_ACCEPTANCE_CALL_SIGN INI_AGENT_LAST_NAME
      return
      ;;
  esac

  while [ "${done}" = false ]; do
    if [ "${shown}" = false ]; then
      if declare -F text_box >/dev/null 2>&1; then
        text_box "COA CALL SIGN" \
          "Your Chief Orchestrator appears externally as: Versa (${default_bare})" \
          "The call sign is always shown in parentheses (replaces the old (COA) last name)." \
          "" \
          "Press Enter to accept the default, or type a custom 2–12 character call sign."
      else
        echo ""
        echo "COA call sign — external name will be: Versa (${default_bare})"
        echo "(press Enter to accept, or type a custom 2–12 character call sign)"
      fi
      shown=true
    fi

    _install_acceptance_input_line "Call sign" "${default_bare}"
    _install_acceptance_read_line "" reply
    case "${reply}" in
      "") bare="${default_bare}"; done=true ;;
      *)
        bare="$(_install_acceptance_bare_call_sign "${reply}" || true)"
        if [ -n "${bare}" ]; then
          done=true
        else
          warn "Call sign must be 2–12 letters/digits (parentheses optional)."
        fi
        ;;
    esac
  done

  INSTALL_ACCEPTANCE_CALL_SIGN="${bare}"
  INI_AGENT_LAST_NAME="(${bare})"
  export INSTALL_ACCEPTANCE_CALL_SIGN INI_AGENT_LAST_NAME
  _install_acceptance_persist_call_sign
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

  # Escape hatch when the OrbStack/session TTY cannot deliver keystrokes:
  #   VERSA_INSTALL_ACCEPT=yes sudo ./setup.sh
  case "$(printf '%s' "${VERSA_INSTALL_ACCEPT:-}" | tr '[:upper:]' '[:lower:]')" in
    1|true|yes|y)
      REPLY="y"
      info "VERSA_INSTALL_ACCEPT set — skipping interactive accept prompt"
      ;;
    *)
      _install_acceptance_read_yn "  Accept and continue? [y/N] (then Enter) "
      ;;
  esac
  if [[ ! $REPLY =~ ^[Yy]([Ee][Ss])?$ ]]; then
    info "Setup cancelled - no changes made."
    exit 0
  fi

  INSTALL_ACCEPTANCE_AT_UTC="$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
  export INSTALL_ACCEPTANCE_AT_UTC

  INSTALL_ACCEPTANCE_PREV_EMAIL="$(_install_acceptance_load_existing_email)"
  export INSTALL_ACCEPTANCE_PREV_EMAIL
  install_acceptance_email_prompt
  install_acceptance_call_sign_prompt
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

  # Fresh install: always prompt [y/N] (OFF). --update carries prior choice.
  # Do not seed the prompt from stock/source setup.ini=true (that made Enter
  # mean Yes while the copy said "defaults to OFF").
  local org_current util_current script_current output_current
  if [ "${UPDATE_MODE:-false}" = true ]; then
    org_current="$(_install_acceptance_features_get organization_ui false)"
    util_current="$(_install_acceptance_features_get utility_models_ui false)"
    script_current="$(_install_acceptance_features_get script_tasks_ui false)"
    output_current="$(_install_acceptance_features_get output_routing_ui false)"
  else
    org_current="false"
    util_current="false"
    script_current="false"
    output_current="false"
  fi

  # Organization — disclaimer first, then the gated prompt.
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
    "Enable Utility Models UI?" "${util_current}")"

  echo ""
  _install_acceptance_feature_note \
    "Script Tasks — schedule deterministic shell scripts from your AGi-Tools"
  _install_acceptance_feature_cont \
    "repo, run by lifeline on a cadence (no agent spawn, no LLM)."
  export VERSA_FEATURE_SCRIPT_TASKS_UI="$(_install_acceptance_feature_ask \
    "Enable Script Tasks UI?" "${script_current}")"

  echo ""
  _install_acceptance_feature_note \
    "Output Routing — route an agent's output generation (e.g. image/audio) to"
  _install_acceptance_feature_cont \
    "a chosen model per cycle (the Output Routing tab in Model Routing)."
  export VERSA_FEATURE_OUTPUT_ROUTING_UI="$(_install_acceptance_feature_ask \
    "Enable Output Routing UI?" "${output_current}")"
}

# Write a single [features] key into the deployed setup.ini (create section if
# absent), then sync to source. Used by install_acceptance_persist_features.
_install_acceptance_features_set() {
  local key="$1" value="$2"
  local ini="${INSTALL_ACCEPTANCE_SETUP_INI}"
  if [ ! -f "${ini}" ]; then
    return 1
  fi
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
  return 0
}

# Persist the captured feature choices to the deployed setup.ini [features].
# Called AFTER setup.ini is deployed + reconciled, so the values are the final
# word (and the next --update reconcile carries them forward — they are not
# stock-owned keys). Always non-fatal under setup.sh `set -e`.
install_acceptance_persist_features() {
  if [ "${DRY_RUN:-false}" = true ]; then
    info "[DRY-RUN] Would persist feature flags to setup.ini"
    return 0
  fi
  [ -n "${VERSA_FEATURE_ORGANIZATION_UI:-}" ] || return 0   # prompts never ran

  local ini="${INSTALL_ACCEPTANCE_SETUP_INI}"
  if [ ! -f "${ini}" ]; then
    # Fresh OrbStack/partial deploys can hit this before setup.ini lands —
    # never abort the whole install for feature-flag persistence.
    warn "Cannot persist feature flags — ${ini} not found (non-fatal; re-run setup --update)"
    return 0
  fi

  _install_acceptance_features_set organization_ui   "$(_install_acceptance_norm_bool "${VERSA_FEATURE_ORGANIZATION_UI:-false}")" \
    || { warn "Failed to set organization_ui (non-fatal)"; return 0; }
  _install_acceptance_features_set utility_models_ui "$(_install_acceptance_norm_bool "${VERSA_FEATURE_UTILITY_MODELS_UI:-false}")" \
    || warn "Failed to set utility_models_ui (non-fatal)"
  _install_acceptance_features_set script_tasks_ui   "$(_install_acceptance_norm_bool "${VERSA_FEATURE_SCRIPT_TASKS_UI:-false}")" \
    || warn "Failed to set script_tasks_ui (non-fatal)"
  _install_acceptance_features_set output_routing_ui "$(_install_acceptance_norm_bool "${VERSA_FEATURE_OUTPUT_ROUTING_UI:-false}")" \
    || warn "Failed to set output_routing_ui (non-fatal)"
  _install_acceptance_sync_source_ini || true
  ok "Feature flags saved to setup.ini [features]"
  return 0
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

  INSTALL_ACCEPTANCE_PREV_EMAIL="$(_install_acceptance_load_existing_email)"
  export INSTALL_ACCEPTANCE_PREV_EMAIL
  install_acceptance_email_prompt
  install_acceptance_call_sign_prompt
}

# ─── VersaVoice AI enable prompt (Step 4 — single ask) ─
# VV account is REQUIRED for install (2026-07). Optional skip parked below —
# revisit later; do not re-enable without fixing local-only downstream gaps.
install_acceptance_vv_prompt() {
  local default_enabled="${1:-true}"
  local intro_line1 intro_line2 intro_line3
  local policy_line usage_line link_terms link_privacy

  intro_line1="$(_install_acceptance_brand) enables cloud messaging between you, your agents and your connections"
  intro_line2="via the $(_install_acceptance_brand) mobile/web app. A $(_install_acceptance_brand) account"
  intro_line3="and sponsor API token are required to install and run Versa AGi."
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

  # ── Parked: optional VV enable/disable ask ──
  # if [ "${default_enabled}" = "false" ]; then
  #   _install_acceptance_read_yn "  Enable $(_install_acceptance_brand)? [y/N] "
  #   if [[ $REPLY =~ ^[Yy]([Ee][Ss])?$ ]]; then
  #     export ENABLE_VV="true"
  #   else
  #     export ENABLE_VV="false"
  #   fi
  # else
  #   _install_acceptance_read_yn "  Enable $(_install_acceptance_brand)? [Y/n] "
  #   if [[ $REPLY =~ ^[Nn]([Oo])?$ ]]; then
  #     export ENABLE_VV="false"
  #   else
  #     export ENABLE_VV="true"
  #   fi
  # fi
  # if [ "${ENABLE_VV}" = "false" ]; then
  #   info "$(_install_acceptance_brand) disabled — agents will use local messaging only"
  #   info "You can enable VV later via the agitop dashboard toggle"
  # fi

  export ENABLE_VV="true"
  if declare -F ok >/dev/null 2>&1; then
    ok "$(_install_acceptance_brand) required — continue with your sponsor API token"
  else
    info "$(_install_acceptance_brand) required — continue with your sponsor API token"
  fi
  : "${default_enabled}"  # retained for parked optional-ask signature
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
  # Repo-tree source is Primary User–owned (setup runs as root via sudo).
  if [ -n "${SUDO_USER:-}" ]; then
    chown "${SUDO_USER}:${SUDO_USER}" "${source}" 2>/dev/null || true
  fi
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
  local call_sign="${INSTALL_ACCEPTANCE_CALL_SIGN:-}"
  python3 - "${INSTALL_ACCEPTANCE_JSON}" <<'PY' "${event}" "${install_mode}" "${accepted_at}" "${email}" "${versavoice_enabled}" "${version}" "${platform}" "${hostname_hash}" "${ip_value}" "${feat_org}" "${feat_util}" "${feat_script}" "${feat_output}" "${call_sign}"
import json, sys
(out, event, install_mode, accepted_at, email, vv, version, platform,
 hostname_hash, ip_value, feat_org, feat_util, feat_script, feat_output,
 call_sign) = sys.argv[1:16]
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
    "coa_call_sign": call_sign or None,
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
