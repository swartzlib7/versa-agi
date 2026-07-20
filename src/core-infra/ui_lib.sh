#!/bin/bash
# ─────────────────────────────────────────────────────
# Versa AGi — UI Library (ui_lib.sh)
#
# Shared terminal output functions for Versa AGi CLI
# tools. Sourced by setup.sh, uninstall.sh,
# backup.sh, restore.sh, and install.sh.
#
# Usage:  source "$(dirname "${BASH_SOURCE[0]}")/ui_lib.sh"
# ─────────────────────────────────────────────────────

# ─── Color Palette ──────────────────────────────────
# Brand: Cyan/Teal waveform on dark background
BOLD='\033[1m'
DIM='\033[2m'
RESET='\033[0m'
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[38;2;0;255;204m'
BCYAN='\033[1;38;2;0;255;204m'    # Bright/Bold cyan — brand primary
WHITE='\033[1;37m'
DGRAY='\033[90m'      # Dark gray — secondary text
NC='\033[0m'

# ─── Unicode Glyphs ────────────────────────────────
# These degrade gracefully in terminals without Unicode
GLYPH_OK="✓"
GLYPH_FAIL="✗"
GLYPH_WARN="!"
GLYPH_SKIP="○"
GLYPH_STEP="●"
GLYPH_ARROW="►"

# ─── OS Detection ──────────────────────────────────

detect_os() {
  VERSA_OS=""
  VERSA_DISTRO=""
  VERSA_OS_VERSION=""
  VERSA_PKG_MGR=""

  case "$(uname -s)" in
    Linux)
      VERSA_OS="linux"
      if [ -f /etc/os-release ]; then
        # shellcheck disable=SC1091
        . /etc/os-release
        case "${ID:-}" in
          ubuntu)       VERSA_DISTRO="ubuntu" ;;
          debian)       VERSA_DISTRO="debian" ;;
          fedora)       VERSA_DISTRO="fedora" ;;
          centos|rhel)  VERSA_DISTRO="rhel" ;;
          arch|manjaro) VERSA_DISTRO="arch" ;;
          *)            VERSA_DISTRO="${ID:-unknown}" ;;
        esac
        VERSA_OS_VERSION="${VERSION_ID:-unknown}"
      else
        VERSA_DISTRO="unknown"
        VERSA_OS_VERSION="unknown"
      fi
      # Detect package manager
      if command -v apt-get &>/dev/null; then
        VERSA_PKG_MGR="apt"
      elif command -v dnf &>/dev/null; then
        VERSA_PKG_MGR="dnf"
      elif command -v yum &>/dev/null; then
        VERSA_PKG_MGR="yum"
      elif command -v pacman &>/dev/null; then
        VERSA_PKG_MGR="pacman"
      fi
      ;;
    *)
      VERSA_OS="unsupported"
      VERSA_DISTRO="unsupported"
      ;;
  esac

  export VERSA_OS VERSA_DISTRO VERSA_OS_VERSION VERSA_PKG_MGR
}

# Host runtime class for agent CYCLE PARAMETERS (native Linux vs WSL).
# Sets: HOST_CLASS, HOST_ARCH, HOST_VIRT, WINDOWS_INTEROP, HOST_OS_PRETTY,
#        HOST_NESTED_VIRT_POLICY
detect_host_runtime() {
  HOST_ARCH="$(uname -m 2>/dev/null || echo unknown)"
  HOST_CLASS="native_linux"
  HOST_VIRT="none"
  WINDOWS_INTEROP="false"
  HOST_NESTED_VIRT_POLICY="not_required_for_normal_dev"
  HOST_OS_PRETTY=""

  if [ -f /etc/os-release ]; then
    # shellcheck disable=SC1091
    . /etc/os-release
    HOST_OS_PRETTY="${PRETTY_NAME:-${NAME:-Linux} ${VERSION_ID:-}}"
  fi
  if [ -z "${HOST_OS_PRETTY}" ]; then
    if [ -n "${VERSA_DISTRO:-}" ] && [ "${VERSA_DISTRO}" != "unsupported" ]; then
      HOST_OS_PRETTY="${VERSA_DISTRO}"
      [ -n "${VERSA_OS_VERSION:-}" ] && [ "${VERSA_OS_VERSION}" != "unknown" ] && \
        HOST_OS_PRETTY="${HOST_OS_PRETTY} ${VERSA_OS_VERSION}"
    else
      HOST_OS_PRETTY="Linux"
    fi
  fi

  local _is_wsl=false
  if [ -n "${WSL_DISTRO_NAME:-}" ] || grep -qiE 'microsoft|wsl' /proc/version 2>/dev/null; then
    _is_wsl=true
  fi

  if [ "${_is_wsl}" = true ]; then
    if [ -e /proc/sys/fs/binfmt_misc/WSLInterop ] || [ -n "${WSL_INTEROP:-}" ]; then
      HOST_CLASS="wsl2"
      HOST_VIRT="wsl2"
    else
      HOST_CLASS="wsl1"
      HOST_VIRT="wsl1"
    fi
    HOST_NESTED_VIRT_POLICY="avoid_nested_vms_prefer_windows_hypervisor"
    if [ -d /mnt/c/Windows ]; then
      WINDOWS_INTEROP="true"
    fi
  else
    # Optional refinement when systemd-detect-virt is available.
    # Note: systemd-detect-virt exits non-zero for "none" — do not use || echo
    # or the word "none" is duplicated onto stdout.
    if command -v systemd-detect-virt &>/dev/null; then
      local _sv
      _sv="$(systemd-detect-virt 2>/dev/null)" || true
      _sv="$(printf '%s' "${_sv}" | tr -d '[:space:]')"
      case "${_sv}" in
        none|"" ) HOST_VIRT="none" ;;
        * ) HOST_VIRT="${_sv}" ;;
      esac
    fi
  fi

  export HOST_CLASS HOST_ARCH HOST_VIRT WINDOWS_INTEROP HOST_OS_PRETTY HOST_NESTED_VIRT_POLICY
}

require_linux() {
  if [ "${VERSA_OS}" != "linux" ]; then
    echo ""
    echo -e "  ${RED}${GLYPH_FAIL}${NC} ${BOLD}Unsupported platform: ${VERSA_OS}${RESET}"
    echo ""
    echo -e "  ${DIM}Versa AGi supports Linux (Ubuntu, Debian, Fedora, Arch).${RESET}"
    echo ""
    exit 1
  fi
}

# ─── Banner ─────────────────────────────────────────

_banner_product_version() {
  # Canonical app version — read from core-infra/VERSION next to this library.
  local vf product_version="${1:-}"
  vf="$(dirname "${BASH_SOURCE[0]}")/VERSION"
  if [ -f "${vf}" ]; then
    product_version="$(tr -d '[:space:]' < "${vf}")"
  elif [ -z "${product_version}" ] && [ -n "${VERSION:-}" ]; then
    product_version="${VERSION}"
  fi
  printf '%s' "${product_version}"
}

banner() {
  local mode="${1:-}"      # "setup", "update", "uninstall", "install", "backup"
  local product_version
  product_version="$(_banner_product_version "${2:-}")"

  # Detect OS if not already done
  [ -z "${VERSA_OS:-}" ] && detect_os

  # Determine terminal width for centering (default 60)
  local cols
  cols=$(tput cols 2>/dev/null || echo 60)
  [ "${cols}" -lt 50 ] && cols=50

  echo ""
  echo -e "${BCYAN}"
  # Read logo from external file (single source of truth)
  local logo_file
  logo_file="$(dirname "${BASH_SOURCE[0]}")/logo.txt"
  if [ -f "${logo_file}" ]; then
    cat "${logo_file}"
  else
    echo "       · ▒▒▓▓████▓▓▒▒ ·"
    echo "       V E R S A  A G i"
  fi
  echo -e "${RESET}"
  if [ -n "${product_version}" ]; then
    echo -e "         ${BCYAN}${BOLD}V E R S A   A G i${RESET}  ${BCYAN}v${product_version}${RESET}"
  else
    echo -e "         ${BCYAN}${BOLD}V E R S A   A G i${RESET}"
  fi
  echo -e "         ${DIM}Agentic General infrastructure${RESET}"
  echo ""

  # Mode subtitle
  case "${mode}" in
    setup)     echo -e "  ${DGRAY}─── ${BCYAN}Environment Setup${DGRAY} ────────────────────${RESET}" ;;
    update)    echo -e "  ${DGRAY}─── ${BCYAN}System Update${DGRAY} ───────────────────────${RESET}" ;;
    uninstall) echo -e "  ${DGRAY}─── ${BCYAN}System Removal${DGRAY} ──────────────────────${RESET}" ;;
    install)   echo -e "  ${DGRAY}─── ${BCYAN}Quick Install${DGRAY} ───────────────────────${RESET}" ;;
    *)         echo -e "  ${DGRAY}────────────────────────────────────────${RESET}" ;;
  esac

  # Platform line (OS — separate from product version above)
  if [ -n "${VERSA_DISTRO}" ] && [ "${VERSA_DISTRO}" != "unsupported" ]; then
    local os_label="${VERSA_DISTRO}"
    [ -n "${VERSA_OS_VERSION}" ] && [ "${VERSA_OS_VERSION}" != "unknown" ] && os_label="${os_label} ${VERSA_OS_VERSION}"
    echo -e "  ${DGRAY}Platform: ${os_label}${RESET}"
  fi
  echo ""
}

# ─── Section Headers ────────────────────────────────

section() {
  local title="$1"
  echo ""
  echo -e "  ${BCYAN}─── ${WHITE}${BOLD}${title}${RESET}${BCYAN} ${DGRAY}$(printf '─%.0s' $(seq 1 $((38 - ${#title}))))${RESET}"
  echo ""
}

# ─── Step Output Functions ──────────────────────────

step_ok() {
  echo -e "  ${GREEN}${GLYPH_OK}${NC} $*"
}

step_fail() {
  echo -e "  ${RED}${GLYPH_FAIL}${NC} $*"
}

step_warn() {
  echo -e "  ${YELLOW}${GLYPH_WARN}${NC} $*"
}

step_skip() {
  echo -e "  ${DGRAY}${GLYPH_SKIP} $*${RESET}"
}

step_info() {
  echo -e "  ${CYAN}${GLYPH_STEP}${NC} $*"
}

step_arrow() {
  echo -e "  ${BCYAN}${GLYPH_ARROW}${NC} $*"
}

# ─── Backward-Compatible Wrappers ───────────────────
# These match the existing setup.sh signatures

info()  { echo -e "  ${CYAN}${GLYPH_STEP}${NC} $*"; }
ok()    { echo -e "  ${GREEN}${GLYPH_OK}${NC} $*"; }
warn()  { echo -e "  ${YELLOW}${GLYPH_WARN}${NC} $*"; }
fail()  { echo -e "  ${RED}${GLYPH_FAIL}${NC} $*"; }

# error() exits — keep consistent with existing scripts
error() {
  echo -e "  ${RED}${GLYPH_FAIL} ERROR:${NC} $*" >&2
  exit 1
}

# ─── Summary Card ───────────────────────────────────

summary_card() {
  # Usage: summary_card "Title" "key1:value1" "key2:value2" ...
  local title="$1"
  shift

  echo ""
  echo -e "  ${BCYAN}╭────────────────────────────────────────╮${RESET}"
  echo -e "  ${BCYAN}│${RESET}  ${BOLD}${WHITE}${title}${RESET}"
  echo -e "  ${BCYAN}├────────────────────────────────────────┤${RESET}"

  for pair in "$@"; do
    local key="${pair%%:*}"
    local val="${pair#*:}"
    printf "  ${BCYAN}│${RESET}  ${DIM}%-16s${RESET} %s\n" "${key}" "${val}"
  done

  echo -e "  ${BCYAN}╰────────────────────────────────────────╯${RESET}"
  echo ""
}

# ─── Text Box (multi-line agreement / notice) ───────
# Usage: text_box "Title" "line 1" "" "line 3" ...
# Empty string lines render as blank rows inside the box.

text_box() {
  local title="$1"
  shift
  local width=56
  local i rule

  : "${BCYAN:=}"
  : "${BOLD:=}"
  : "${WHITE:=}"
  : "${RESET:=}"

  _tb_rule() {
    local left="$1" mid="$2" right="$3"
    rule=""
    for ((i=0; i<width; i++)); do rule+="${mid}"; done
    echo -e "  ${BCYAN}${left}${rule}${right}${RESET}"
  }

  _tb_line() {
    local content="$1"
    echo -e "  ${BCYAN}│${RESET}  ${content}"
  }

  echo ""
  _tb_rule "╭" "─" "╮"
  if [ -n "${title}" ]; then
    echo -e "  ${BCYAN}│${RESET}  ${BOLD}${WHITE}${title}${RESET}"
    _tb_rule "├" "─" "┤"
  fi
  for line in "$@"; do
    _tb_line "${line}"
  done
  _tb_rule "╰" "─" "╯"
  echo ""
}

# ─── Controlling-TTY I/O ────────────────────────────
# curl|bash, nested sudo, and some OrbStack reinstall sessions leave stdin as a
# pipe or a fd that does not receive keystrokes. `read` then blocks with no
# usable prompt and Ctrl+C may not reach the script. Prefer /dev/tty when present.

tty_read() {
  if [ -r /dev/tty ]; then
    read "$@" </dev/tty
  else
    read "$@"
  fi
}

# Write prompt to the controlling tty (fallback: stderr), then tty_read.
# Usage: tty_prompt_read "Prompt text: " [-n 1] [-r] [varname]
tty_prompt_read() {
  local prompt="$1"
  shift
  if [ -w /dev/tty ]; then
    printf '%s' "${prompt}" >/dev/tty
  else
    printf '%s' "${prompt}" >&2
  fi
  tty_read "$@"
}

# ─── Confirmation Prompt ────────────────────────────

confirm() {
  local prompt="${1:-Continue?}"
  local default="${2:-n}"  # "y" or "n"
  local reply=""

  local hint="[y/N]"
  [ "${default}" = "y" ] && hint="[Y/n]"

  echo ""
  # Line-based (type y/n then Enter) — `read -n 1` is unreliable on OrbStack.
  tty_prompt_read "  ${prompt} ${hint} " -r reply
  reply="$(printf '%s' "${reply}" | tr -d '\r' | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')"
  echo
  REPLY="${reply}"

  if [ "${default}" = "y" ]; then
    [[ ! ${REPLY} =~ ^[Nn]([Oo])?$ ]]
  else
    [[ ${REPLY} =~ ^[Yy]([Ee][Ss])?$ ]]
  fi
}

# Highlighted confirmation (brand cyan) — CRON and agitop launch gates
confirm_accent() {
  local prompt="${1:-Continue?}"
  local default="${2:-n}"  # "y" or "n"
  local reply=""

  : "${BCYAN:=}"
  : "${RESET:=}"

  local hint="[y/N]"
  [ "${default}" = "y" ] && hint="[Y/n]"

  echo ""
  if [ -w /dev/tty ]; then
    echo -e -n "  ${BCYAN}${prompt}${RESET} ${hint} " >/dev/tty
  else
    echo -e -n "  ${BCYAN}${prompt}${RESET} ${hint} "
  fi
  tty_read -r reply
  reply="$(printf '%s' "${reply}" | tr -d '\r' | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')"
  echo
  REPLY="${reply}"

  if [ "${default}" = "y" ]; then
    [[ ! ${REPLY} =~ ^[Nn]([Oo])?$ ]]
  else
    [[ ${REPLY} =~ ^[Yy]([Ee][Ss])?$ ]]
  fi
}

# ─── Progress Bar (Simple) ──────────────────────────

progress_step() {
  local current=$1
  local total=$2
  local label="${3:-}"

  local pct=$((current * 100 / total))
  local filled=$((pct / 5))
  local empty=$((20 - filled))

  printf "\r  ${BCYAN}[${GREEN}"
  printf '█%.0s' $(seq 1 ${filled} 2>/dev/null) || true
  printf "${DGRAY}"
  printf '░%.0s' $(seq 1 ${empty} 2>/dev/null) || true
  printf "${BCYAN}]${NC} %3d%%  ${DIM}%s${RESET}" "${pct}" "${label}"

  # Newline when complete
  [ "${current}" -eq "${total}" ] && echo ""
}

# ─── Spinner (Background Command) ──────────────────
# Usage: with_spinner "message" command arg1 arg2 ...
# Runs command in background with animated braille spinner.
# Shows ✓ on success, ✗ on failure.

with_spinner() {
  local msg="$1"
  shift

  # Run command in background, capture PID
  "$@" &>/dev/null &
  local cmd_pid=$!

  local frames=('⠋' '⠙' '⠹' '⠸' '⠼' '⠴' '⠦' '⠧' '⠇' '⠏')
  local i=0

  # Animate while process runs
  while kill -0 "${cmd_pid}" 2>/dev/null; do
    printf "\r  ${CYAN}${frames[$i]}${NC} ${DIM}${msg}${RESET}"
    i=$(( (i + 1) % ${#frames[@]} ))
    sleep 0.1
  done

  # Check exit code
  wait "${cmd_pid}"
  local exit_code=$?

  if [ ${exit_code} -eq 0 ]; then
    printf "\r  ${GREEN}${GLYPH_OK}${NC} ${msg}  \n"
  else
    printf "\r  ${RED}${GLYPH_FAIL}${NC} ${msg} (exit: ${exit_code})  \n"
  fi

  return ${exit_code}
}

# ─── Divider ────────────────────────────────────────

divider() {
  echo -e "  ${DGRAY}────────────────────────────────────────${RESET}"
}

# ─── License Notice ─────────────────────────────────

license_notice() {
  echo -e "  ${DIM}Licensed under BSL-1.1 · © $(date +%Y) VersaVoice AI LLC${RESET}"
  echo -e "  ${DIM}https://github.com/swartzlib7/versa-agi${RESET}"
}
