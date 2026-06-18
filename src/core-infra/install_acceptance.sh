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

# ─── Box-style input line (matches text_box body) ───
_install_acceptance_input_line() {
  local label="$1"
  local value="${2:-}"
  : "${BCYAN:=}"
  : "${RESET:=}"
  echo -e -n "  ${BCYAN}│${RESET}  ${label}"
  if [ -n "${value}" ]; then
    echo -e -n "["
    printf '%s' "${value}"
    echo -e -n "]: "
  else
    echo -e -n ": "
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
      read -r reply
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
      read -r reply
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

  read -p "  Accept and continue? [y/N] " -n 1 -r
  echo ""
  if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    info "Setup cancelled - no changes made."
    exit 0
  fi

  INSTALL_ACCEPTANCE_AT_UTC="$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
  export INSTALL_ACCEPTANCE_AT_UTC

  install_acceptance_email_prompt
}

# ─── Below-min version block messaging ──────────────
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

  read -p "  Continue? [Y/n] " -n 1 -r
  echo ""
  if [[ $REPLY =~ ^[Nn]$ ]]; then
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
    echo -e -n "  Enable $(_install_acceptance_brand)? [y/N] "
    read -n 1 -r
    echo ""
    if [[ $REPLY =~ ^[Yy]$ ]]; then
      export ENABLE_VV="true"
    else
      export ENABLE_VV="false"
    fi
  else
    echo -e -n "  Enable $(_install_acceptance_brand)? [Y/n] "
    read -n 1 -r
    echo ""
    if [[ $REPLY =~ ^[Nn]$ ]]; then
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

  mkdir -p /etc/versa-agi
  python3 - "${INSTALL_ACCEPTANCE_JSON}" <<'PY' "${event}" "${install_mode}" "${accepted_at}" "${email}" "${versavoice_enabled}" "${version}" "${platform}" "${hostname_hash}" "${ip_value}"
import json, sys
out, event, install_mode, accepted_at, email, vv, version, platform, hostname_hash, ip_value = sys.argv[1:11]
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

  # Already registered — preserve submitted flag; heartbeat only when >7 days stale
  if [ "${already_submitted}" = "true" ]; then
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
