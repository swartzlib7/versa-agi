#!/bin/bash
# ─────────────────────────────────────────────────────
# Versa AGi — Quick Installer
#
# One-liner installation for Versa AGi infrastructure.
# Downloads the latest release and runs setup.
#
# Usage (preferred — stdin stays a TTY for interactive prompts):
#   curl -fsSL .../install.sh -o /tmp/versa-agi-install.sh && sudo bash /tmp/versa-agi-install.sh
#
# Avoid: curl … | sudo bash  (stdin is the pipe — acceptance prompts hang)
# Note: sudo bash <(curl …) fails in some OrbStack shells — use download-then-run.
#
# Requirements:
#   - Linux (Ubuntu, Debian, Fedora, Arch) — including OrbStack Ubuntu machines
#   - git, curl
#   - Root access (sudo)
#
# © 2026 VersaVoice AI LLC — Licensed under BSL-1.1
# ─────────────────────────────────────────────────────

set -euo pipefail

# ─── Configuration ──────────────────────────────────
REPO_URL="https://github.com/swartzlib7/versa-agi.git"
REPO_BRANCH="main"
INSTALL_DIR="/tmp/versa-agi-install-$$"
VERSION="3.3.7"
TEST_MODE=false

# ─── Parse Arguments ────────────────────────────────
for arg in "$@"; do
  case "$arg" in
    --test)  TEST_MODE=true ;;
    --help|-h)
      echo "Usage (preferred):"
      echo "  curl -fsSL .../install.sh -o /tmp/versa-agi-install.sh && sudo bash /tmp/versa-agi-install.sh"
      echo "  sudo ./install.sh [--test]"
      echo ""
      echo "Avoid: curl … | sudo bash  (breaks interactive prompts)"
      echo ""
      echo "  --test    Show visual output only, skip clone and setup"
      exit 0
      ;;
  esac
done

# ─── Colors (inline — ui_lib.sh not available yet) ──
BOLD='\033[1m'
DIM='\033[2m'
RESET='\033[0m'
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BCYAN='\033[1;36m'
WHITE='\033[1;37m'
DGRAY='\033[90m'
NC='\033[0m'

ok()    { echo -e "  ${GREEN}✓${NC} $*"; }
fail()  { echo -e "  ${RED}✗${NC} $*"; }
warn()  { echo -e "  ${YELLOW}!${NC} $*"; }
info()  { echo -e "  ${CYAN}●${NC} $*"; }

# ─── Cleanup Trap ───────────────────────────────
# The /tmp clone is cleaned up after setup; the persisted copy lives at ~/.versa-agi/repo/
cleanup() {
  if [ -d "${INSTALL_DIR}" ]; then
    rm -rf "${INSTALL_DIR}"
  fi
}
trap cleanup EXIT

echo ""
echo -e "${BCYAN}"
# Try logo.txt (available when run locally), fallback for curl pipe
_LOGO_FILE="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" 2>/dev/null && pwd)/src/core-infra/logo.txt"
if [ -f "${_LOGO_FILE}" ]; then
  cat "${_LOGO_FILE}"
else
  echo "          ·  V E R S A   A G i  ·"
fi
echo -e "${RESET}"
echo -e "         ${BOLD}${WHITE}V E R S A   A G i${RESET}"
echo -e "         ${DIM}Agentic General infrastructure${RESET}"
echo ""
echo -e "  ${DGRAY}─── ${BCYAN}Quick Install${DGRAY} ───────────────────────${RESET}"
echo -e "  ${DGRAY}v${VERSION}${RESET}"
echo ""

# ─── Root Check ─────────────────────────────────────
if [ "$(id -u)" -ne 0 ]; then
  fail "This installer must be run as root."
  echo ""
  echo -e "  ${DIM}Run with:${RESET}"
  echo -e "  ${DIM}curl -fsSL https://raw.githubusercontent.com/swartzlib7/versa-agi/main/install.sh -o /tmp/versa-agi-install.sh${RESET}"
  echo -e "  ${DIM}sudo bash /tmp/versa-agi-install.sh${RESET}"
  echo ""
  exit 1
fi

# Piped install (curl|bash) occupies stdin — interactive acceptance cannot read
# the keyboard. Prefer download-then-run (see --help).
if [ ! -t 0 ] && [ "${TEST_MODE}" != true ]; then
  warn "stdin is not a terminal (likely curl … | sudo bash)."
  warn "Interactive prompts may hang. Prefer:"
  echo ""
  echo -e "  ${BOLD}curl -fsSL https://raw.githubusercontent.com/swartzlib7/versa-agi/main/install.sh -o /tmp/versa-agi-install.sh${RESET}"
  echo -e "  ${BOLD}sudo bash /tmp/versa-agi-install.sh${RESET}"
  echo ""
  echo -e "  ${DIM}Escape hatch: VERSA_INSTALL_ACCEPT=yes sudo -E bash /tmp/versa-agi-install.sh${RESET}"
  echo ""
fi

# ─── Platform Detection ────────────────────────────
VERSA_OS=""
VERSA_DISTRO=""
VERSA_OS_VERSION=""

case "$(uname -s)" in
  Linux)
    VERSA_OS="linux"
    if [ -f /etc/os-release ]; then
      . /etc/os-release
      VERSA_DISTRO="${ID:-unknown}"
      VERSA_OS_VERSION="${VERSION_ID:-unknown}"
    fi
    ;;
  Darwin)
    VERSA_OS="macos"
    VERSA_DISTRO="macos"
    VERSA_OS_VERSION="$(sw_vers -productVersion 2>/dev/null || echo 'unknown')"
    ;;
  *)
    VERSA_OS="unsupported"
    ;;
esac

if [ "${VERSA_OS}" != "linux" ] && [ "${VERSA_OS}" != "macos" ]; then
  fail "Unsupported platform: $(uname -s)"
  echo ""
  echo -e "  ${DIM}Versa AGi currently supports Linux.${RESET}"
  echo ""
  exit 1
fi

if [ "${VERSA_OS}" = "macos" ]; then
  echo ""
  fail "macOS support is currently in development."
  echo -e "  ${DIM}Native MacBook architecture is built but pending release."
  echo -e "  Status: Coming soon. Please deploy natively to Linux.${RESET}"
  echo ""
  exit 1
fi

ok "Platform: ${VERSA_DISTRO} ${VERSA_OS_VERSION}"

# ─── Prerequisites ──────────────────────────────────
echo ""
echo -e "  ${DGRAY}─── ${WHITE}${BOLD}Prerequisites${RESET}${DGRAY} ──────────────────────────${RESET}"
echo ""

MISSING=0

# Detect package manager
PKG_INSTALL=""
if command -v apt &>/dev/null; then
  PKG_INSTALL="apt install -y"
elif command -v dnf &>/dev/null; then
  PKG_INSTALL="dnf install -y"
elif command -v pacman &>/dev/null; then
  PKG_INSTALL="pacman -S --noconfirm"
fi

if [ -z "${PKG_INSTALL}" ]; then
  fail "No supported package manager found (apt, dnf, pacman)"
  exit 1
fi

# Check and auto-install each prerequisite
auto_install() {
  local cmd=$1
  local pkg=$2

  if command -v "${cmd}" &>/dev/null; then
    ok "${cmd}: $(${cmd} --version 2>&1 | head -1 || true)"
  else
    warn "${cmd} not found — installing..."
    if ${PKG_INSTALL} "${pkg}" >/dev/null 2>&1; then
      ok "${cmd} installed"
    else
      fail "Failed to install ${pkg}"
      MISSING=1
    fi
  fi
}

# On apt systems, refresh the package index exactly once before any install.
# Fresh WSL instances have an empty cache — without this every apt install fails.
if command -v apt &>/dev/null; then
  _APT_UPDATED=0
  _needs_update() {
    # Return 0 (true) if any required command is missing
    for _chk in git curl sqlite3 jq inotifywait xclip; do
      command -v "${_chk}" &>/dev/null || return 0
    done
    return 1
  }
  if _needs_update; then
    echo -e "  ${DGRAY}Updating package index...${RESET}"
    apt-get update -qq 2>/dev/null || apt update -qq 2>/dev/null || true
    _APT_UPDATED=1
  fi
fi

auto_install "git" "git"
auto_install "curl" "curl"
auto_install "sqlite3" "sqlite3"
auto_install "jq" "jq"
auto_install "inotifywait" "inotify-tools"
# agitop Copy All / clipboard (xsel is an acceptable alternative if already present)
if command -v xsel &>/dev/null && ! command -v xclip &>/dev/null; then
  ok "xsel: clipboard support present"
else
  auto_install "xclip" "xclip"
fi

if [ "${MISSING}" -eq 1 ]; then
  echo ""
  fail "Missing prerequisites. Install the above manually and re-run."
  exit 1
fi



# Python3 Venv (required for LangGraph agent harness)
if dpkg -s python3-venv &>/dev/null 2>&1 || rpm -q python3-venv &>/dev/null 2>&1 || pacman -Qs python &>/dev/null 2>&1; then
  ok "python3-venv (or equivalent) installed"
else
  info "Installing python3-venv..."
  ${PKG_INSTALL} python3-venv >/dev/null 2>&1 || true
  ok "python3-venv installed"
fi

# ─── Test Mode Indicator ────────────────────────────
if [ "${TEST_MODE}" = true ]; then
  echo ""
  echo -e "  ${YELLOW}!${NC} ${BOLD}TEST MODE${RESET} ${DIM}— visual preview only, no changes${RESET}"
fi

# ─── Clone Repository ──────────────────────────────
echo ""
echo -e "  ${DGRAY}─── ${WHITE}${BOLD}Download${RESET}${DGRAY} ─────────────────────────────────${RESET}"
echo ""

if [ "${TEST_MODE}" = true ]; then
  ok "Would clone: ${REPO_URL}"
  ok "Branch: ${REPO_BRANCH}"
  ok "Target: ${INSTALL_DIR}"
else
  info "Cloning ${REPO_URL}..."
  if git clone --depth 1 --branch "${REPO_BRANCH}" "${REPO_URL}" "${INSTALL_DIR}" 2>/dev/null; then
    ok "Repository cloned to ${INSTALL_DIR}"
  else
    fail "Failed to clone repository"
    echo -e "     ${DIM}Check your network connection and try again.${RESET}"
    echo -e "     ${DIM}URL: ${REPO_URL}${RESET}"
    exit 1
  fi
fi

# ─── Run Setup ──────────────────────────────────────
echo ""
echo -e "  ${DGRAY}─── ${WHITE}${BOLD}Setup${RESET}${DGRAY} ────────────────────────────────────${RESET}"
echo ""

if [ "${TEST_MODE}" = true ]; then
  ok "Would run: ${INSTALL_DIR}/src/setup.sh"
  echo ""
  echo -e "  ${BCYAN}╭────────────────────────────────────────╮${RESET}"
  echo -e "  ${BCYAN}│${RESET}  ${BOLD}${WHITE}Test Complete${RESET}"
  echo -e "  ${BCYAN}├────────────────────────────────────────┤${RESET}"
  echo -e "  ${BCYAN}│${RESET}  ${DIM}Status          ${RESET} Visual preview only"
  echo -e "  ${BCYAN}│${RESET}  ${DIM}Platform        ${RESET} ${VERSA_DISTRO} ${VERSA_OS_VERSION}"
  echo -e "  ${BCYAN}│${RESET}  ${DIM}Repo            ${RESET} ${REPO_URL}"
  echo -e "  ${BCYAN}│${RESET}  ${DIM}Branch          ${RESET} ${REPO_BRANCH}"
  echo -e "  ${BCYAN}╰────────────────────────────────────────╯${RESET}"
  echo ""
  echo -e "  ${DIM}Licensed under BSL-1.1 · © $(date +%Y) VersaVoice AI LLC${RESET}"
  echo -e "  ${DIM}https://github.com/swartzlib7/versa-agi${RESET}"
  echo ""
else
  # Linux (including OrbStack Ubuntu). Native macOS exits earlier; never diverge
  # onto a missing mac_setup.sh path.
  SETUP_SCRIPT="${INSTALL_DIR}/src/setup.sh"

  if [ ! -f "${SETUP_SCRIPT}" ]; then
    fail "setup.sh not found in cloned repository"
    exit 1
  fi

  chmod +x "${SETUP_SCRIPT}"
  info "Launching setup..."
  echo ""
  # Install acceptance (welcome, optional email, registration telemetry) runs inside setup.sh
  # via core-infra/install_acceptance.sh — shared for install and --update paths.

  # Hand off to setup.sh. Only force /dev/tty when stdin is not already a
  # terminal (curl|bash). On OrbStack, always redirecting to /dev/tty can
  # show prompts while keystrokes stay on the session PTY — accept hangs.
  #
  # Capture RC without aborting: set -e would otherwise skip repo persist when
  # setup exits non-zero — leaving ~/.versa-agi/ with only the setup.ini symlink.
  set +e
  if [ -t 0 ]; then
    bash "${SETUP_SCRIPT}"
  elif [ -r /dev/tty ]; then
    bash "${SETUP_SCRIPT}" < /dev/tty
  else
    bash "${SETUP_SCRIPT}"
  fi
  SETUP_RC=$?
  set -e

  # Resolve Primary User home (sudo → explicit env → login name → root).
  PU_USER="${SUDO_USER:-${VERSA_PRIMARY_USER:-}}"
  if [ -z "${PU_USER}" ]; then
    PU_USER="$(logname 2>/dev/null || true)"
  fi
  if [ -z "${PU_USER}" ]; then
    PU_USER="root"
  fi
  PU_HOME=$(eval echo "~${PU_USER}")
  REPO_PERSIST_DIR="${PU_HOME}/.versa-agi/repo"

  # Persist repo clone for updates (always — even when setup failed partway).
  # The /tmp clone is removed by the EXIT trap; without this copy, ~/.versa-agi/
  # only has the setup.ini symlink that setup.sh creates.
  if [ -d "${INSTALL_DIR}" ]; then
    rm -rf "${REPO_PERSIST_DIR}" 2>/dev/null || true
    mkdir -p "$(dirname "${REPO_PERSIST_DIR}")"
    cp -a "${INSTALL_DIR}" "${REPO_PERSIST_DIR}"
    if [ "${PU_USER}" != "root" ]; then
      chown -R "${PU_USER}:${PU_USER}" "${PU_HOME}/.versa-agi" 2>/dev/null || \
        chown -R "${PU_USER}:${PU_USER}" "${REPO_PERSIST_DIR}" 2>/dev/null || true
    fi
    ok "Repository persisted to ${REPO_PERSIST_DIR}"
  else
    warn "Install clone missing — could not persist ~/.versa-agi/repo/"
  fi

  # Persist tooling scripts (the /tmp clone is cleaned up on exit)
  if [ ! -d "${INSTALL_DIR}/src" ]; then
    warn "Install clone incomplete — skipping /usr/local tooling install"
    if [ "${SETUP_RC}" -ne 0 ]; then
      exit "${SETUP_RC}"
    fi
    exit 1
  fi

  PERSIST_DIR="/usr/local/lib/versa-agi"
  mkdir -p "${PERSIST_DIR}"
  cp "${INSTALL_DIR}/src/uninstall.sh" "${PERSIST_DIR}/uninstall.sh"
  cp "${INSTALL_DIR}/src/rekey.sh" "${PERSIST_DIR}/rekey.sh"
  cp "${INSTALL_DIR}/src/backup.sh" "${PERSIST_DIR}/backup.sh"
  cp "${INSTALL_DIR}/src/restore.sh" "${PERSIST_DIR}/restore.sh"
  cp "${INSTALL_DIR}/src/core-infra/ui_lib.sh" "${PERSIST_DIR}/ui_lib.sh" 2>/dev/null || true
  # setup.ini is now at /etc/versa-agi/setup.ini (canonical) — no longer persisted here
  chmod +x "${PERSIST_DIR}/uninstall.sh"
  chmod +x "${PERSIST_DIR}/rekey.sh"
  chmod +x "${PERSIST_DIR}/backup.sh"

  ln -sf "${PERSIST_DIR}/uninstall.sh" /usr/local/bin/versa-agi-uninstall
  ln -sf "${PERSIST_DIR}/rekey.sh" /usr/local/bin/versa-agi-rekey
  ln -sf "${PERSIST_DIR}/backup.sh" /usr/local/bin/versa-agi-backup
  # versa-agi-update — thin launcher that delegates to repo's setup.sh --update
  cat > "${PERSIST_DIR}/update.sh" <<LAUNCHER
#!/bin/bash
# Versa AGi — Update Launcher (auto-generated by install.sh)
_REPO_SETUP="${REPO_PERSIST_DIR}/src/setup.sh"
if [ -f "\${_REPO_SETUP}" ]; then
  exec "\${_REPO_SETUP}" --update "\$@"
else
  echo "[ERROR] Setup script not found at: \${_REPO_SETUP}"
  echo "Expected the install-time clone at ~/.versa-agi/repo/ — re-run install.sh."
  exit 1
fi
LAUNCHER
  chmod 755 "${PERSIST_DIR}/update.sh"
  ln -sf "${PERSIST_DIR}/update.sh" /usr/local/bin/versa-agi-update
  ok "Uninstall available at: sudo versa-agi-uninstall [--purge]"
  ok "Update available at:    sudo versa-agi-update [--dry-run]"
  ok "Rekey available at:     sudo versa-agi-rekey"
  ok "Backup available at:    sudo versa-agi-backup [--dry-run]"

  if [ "${SETUP_RC}" -ne 0 ]; then
    warn "setup.sh exited with status ${SETUP_RC} — repo was still persisted to ${REPO_PERSIST_DIR}"
    exit "${SETUP_RC}"
  fi
fi
