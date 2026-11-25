#!/bin/bash
# ─────────────────────────────────────────────────
# Versa AGi — Playwright Provider (Headless Chromium)
#
# Installs Playwright + Chromium for headless browser
# automation. Agents access via agictl browser commands.
#
# Requirements: Python 3.10+, pip
# Browser profile: ~/.cache/ms-playwright/ (per-user)
#
# Usage:
#   sudo ./playwright.sh                     # Install
#   sudo ./playwright.sh --uninstall         # Remove
#   sudo ./playwright.sh --timeout 60        # Install with custom timeout
#
# Per-agent functions (sourced by agictl/dashboard):
#   playwright_install_for_user <os_user>
#   playwright_uninstall_for_user <os_user>
# ─────────────────────────────────────────────────

set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/_common.sh"
provider_require_root

# ─── Constants ──────────────────────────────────────
PLAYWRIGHT_MIN_VERSION="1.40.0"
HARNESS_VENV="/usr/local/lib/versa-agi/venv"
AGENTS_DB="${AGENTS_DB:-/var/lib/versa-agi/agents.db}"
DEFAULT_TIMEOUT=30

# ─── Parse Arguments ────────────────────────────────
ACTION="install"
CUSTOM_TIMEOUT="${DEFAULT_TIMEOUT}"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --uninstall) ACTION="uninstall"; shift ;;
    --timeout)   CUSTOM_TIMEOUT="${2:-${DEFAULT_TIMEOUT}}"; shift 2 ;;
    *)           shift ;;
  esac
done

# ─── Per-User Functions ─────────────────────────────
# These are designed to be sourced by other scripts (agictl, dashboard).

playwright_install_for_user() {
  local agent_user="$1"
  local playwright_bin="${HARNESS_VENV}/bin/playwright"
  if [ ! -f "${playwright_bin}" ]; then
    echo "  [WARN] Playwright not found in harness venv. Skipping browser install for ${agent_user}."
    return 1
  fi
  sudo -u "${agent_user}" "${playwright_bin}" install chromium 2>&1 | tail -3
  return 0
}

playwright_uninstall_for_user() {
  local agent_user="$1"
  # Remove browser binaries
  rm -rf "/home/${agent_user}/.cache/ms-playwright/" 2>/dev/null || true
  # Remove screenshots
  local workspace
  workspace=$(sqlite3 "${AGENTS_DB}" "SELECT workspace FROM agents WHERE os_user='${agent_user}';" 2>/dev/null || true)
  if [ -n "${workspace}" ]; then
    # COA: workspace/.agent/workspace/screenshots/
    if [ -d "${workspace}/.agent/workspace/screenshots" ]; then
      rm -rf "${workspace}/.agent/workspace/screenshots"
    fi
    # Sub-agents: workspace/workspace/screenshots/
    if [ -d "${workspace}/workspace/screenshots" ]; then
      rm -rf "${workspace}/workspace/screenshots"
    fi
  fi
  return 0
}

# ─── Uninstall ──────────────────────────────────────
if [ "${ACTION}" = "uninstall" ]; then
  echo ""
  echo "  ─── Playwright Provider — Uninstall ──────────"
  echo ""

  if [ -z "${VERSA_SETUP_PARENT:-}" ]; then
    read -p "  Remove Playwright browser binaries from all agents? [y/N]: " -n 1 -r
    echo ""
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then echo "  Cancelled."; exit 0; fi
    echo ""
  fi

  # Remove browser binaries from all agents with browser_enabled=1
  if [ -f "${AGENTS_DB}" ]; then
    while IFS='|' read -r _name _os_user; do
      if [ -n "${_os_user}" ]; then
        playwright_uninstall_for_user "${_os_user}"
        sqlite3 "${AGENTS_DB}" "UPDATE agents SET browser_enabled = 0 WHERE os_user = '${_os_user}';" 2>/dev/null || true
        ok "Cleaned up browser for ${_name} (${_os_user})"
      fi
    done < <(sqlite3 "${AGENTS_DB}" "SELECT name, os_user FROM agents WHERE browser_enabled = 1;" 2>/dev/null || true)
  fi

  # Disable in setup.ini
  provider_ini_set browser enabled false
  ok "Browser disabled in setup.ini"

  echo ""
  echo "  ✅ Playwright removed."
  echo "  Agents will no longer have browser automation capability."
  echo "  System dependencies (shared libs) are left in place — harmless."
  echo ""
  exit 0
fi

# ─── Pre-flight Check ──────────────────────────────
echo ""
echo "  ─── Playwright Provider — Install ──────────────"
echo ""
echo "  Headless Chromium browser automation for agents."
echo "  Agents browse, extract content, fill forms, and take screenshots."
echo ""
echo "  Browser profile: ~/.cache/ms-playwright/ (per-user)"
echo "  Timeout: ${CUSTOM_TIMEOUT}s"
echo ""

if [ -z "${VERSA_SETUP_PARENT:-}" ]; then
  read -p "  Install Playwright + Chromium? [Y/n]: " -n 1 -r
  echo ""
  if [[ $REPLY =~ ^[Nn]$ ]]; then echo "  Playwright setup cancelled."; exit 0; fi
  echo ""
fi

# ─── Step 1: Install Python package in harness venv ─
if [ ! -d "${HARNESS_VENV}" ]; then
  error "Harness venv not found at ${HARNESS_VENV}. Run setup.sh first."
fi

info "Installing Playwright Python package..."
"${HARNESS_VENV}/bin/pip" install "playwright>=${PLAYWRIGHT_MIN_VERSION}" --quiet 2>&1 | tail -3
ok "Playwright package installed in harness venv"

# ─── Step 1b: Fix Playwright driver permissions ─────
# pip install may not preserve execute bits on the bundled node binary
# (depends on umask). Fix before any Playwright CLI commands.
PLAYWRIGHT_DRIVER_DIR="${HARNESS_VENV}/lib/python3.*/site-packages/playwright/driver"
for driver_dir in ${PLAYWRIGHT_DRIVER_DIR}; do
  if [ -d "${driver_dir}" ]; then
    chmod +x "${driver_dir}/node" 2>/dev/null || true
    chmod +x "${driver_dir}/package/bin/"* 2>/dev/null || true
  fi
done

# ─── Step 2: Install system dependencies ────────────
# Safe shared libraries — no confirmation needed.
info "Installing system dependencies for Chromium..."
"${HARNESS_VENV}/bin/playwright" install-deps chromium 2>&1 | tail -5
ok "System dependencies installed"

# ─── Step 3: Install Chromium for COA ───────────────
COA_USER="$(provider_ini_get users coa coa)"
info "Installing Chromium browser for ${COA_USER}..."
if playwright_install_for_user "${COA_USER}"; then
  ok "Chromium installed for ${COA_USER}"
else
  warn "Could not install Chromium for ${COA_USER} — install manually later"
fi

# ─── Step 4: Update setup.ini ───────────────────────
provider_ini_set browser enabled true
provider_ini_set browser timeout "${CUSTOM_TIMEOUT}"
ok "setup.ini updated: [browser] enabled=true, timeout=${CUSTOM_TIMEOUT}"

# ─── Step 5: Verify ────────────────────────────────
info "Verifying Playwright installation..."
VERIFY_SCRIPT=$(cat <<'PYEOF'
import sys
try:
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto("data:text/html,<h1>Versa AGi Browser Test</h1>")
        title = page.title()
        browser.close()
        print(f"OK: Headless Chromium verified (title: {title})")
        sys.exit(0)
except Exception as e:
    print(f"FAIL: {e}", file=sys.stderr)
    sys.exit(1)
PYEOF
)

# Run verification as COA user
VERIFY_RESULT=$(sudo -u "${COA_USER}" "${HARNESS_VENV}/bin/python3" -c "${VERIFY_SCRIPT}" 2>&1) || true
if echo "${VERIFY_RESULT}" | grep -q "^OK:"; then
  ok "${VERIFY_RESULT}"
else
  warn "Verification failed: ${VERIFY_RESULT}"
  warn "Browser may still work — check 'agictl browser goto \"https://example.com\"'"
fi

echo ""
echo "  ✅ Playwright installed and ready!"
echo ""
echo "  Agents can now browse the web via:"
echo "    agictl browser goto \"https://example.com\""
echo "    agictl browser screenshot \"https://example.com\" --full-page"
echo "    agictl browser extract \"https://example.com\" --selector \"h1\""
echo ""
echo "  Enable per-agent: agitop dashboard → Agent Settings → Browser Usage"
echo ""
