#!/usr/bin/env bash
# Sourced by setup.sh.
# What setup installs at a shipped version (config/shipped_tools.ini,
# state_host_services.md §1.7): rclone, FUSE, and the Python packages. Nothing here may stop an install or an update:
# every function reports and returns, and setup carries on.
#
# The VERSA_* variables exist for the tests.

SHIPPED_TOOLS_INI="${SHIPPED_TOOLS_INI:-}"
RCLONE_DEST="${VERSA_RCLONE_DEST:-/usr/local/bin/rclone}"

# shipped_tool_value <ini> <section> <key>
shipped_tool_value() {
  awk -v section="[$2]" -v key="$3" '
    /^[[:space:]]*\[/ { inside = ($0 == section); next }
    inside && $0 !~ /^[[:space:]]*#/ {
      split($0, pair, "=")
      name = pair[1]; gsub(/^[[:space:]]+|[[:space:]]+$/, "", name)
      if (name == key) {
        value = substr($0, index($0, "=") + 1)
        gsub(/^[[:space:]]+|[[:space:]]+$/, "", value)
        print value; exit
      }
    }' "$1" 2>/dev/null
}

# shipped_python_pins <ini> <section>
# The section's packages as pip constraint lines (`name==version`). Keys that
# start with "_" describe the section and are left out.
shipped_python_pins() {
  awk -v section="[$2]" '
    /^[[:space:]]*\[/ { inside = ($0 == section); next }
    inside && $0 !~ /^[[:space:]]*(#|$)/ {
      at = index($0, "=")
      if (at == 0) next
      name = substr($0, 1, at - 1); value = substr($0, at + 1)
      gsub(/^[[:space:]]+|[[:space:]]+$/, "", name)
      gsub(/^[[:space:]]+|[[:space:]]+$/, "", value)
      if (name == "" || value == "" || substr(name, 1, 1) == "_") next
      print name "==" value
    }' "$1" 2>/dev/null
}

# pip_install_pinned <ini> <section> <pip install command …>
# Runs the command with the section's versions as constraints. When that does
# not work on this machine (another Python, no network for a version that
# differs, no build of a pinned version), runs it again without them.
# Returns 0 installed at the shipped versions, 1 installed without the pins,
# 2 the install failed both ways.
pip_install_pinned() {
  local ini="$1" section="$2" pins
  shift 2
  pins=$(mktemp) || return 2
  shipped_python_pins "${ini}" "${section}" > "${pins}"
  if [ -s "${pins}" ] && "$@" -c "${pins}" > /dev/null 2>&1; then
    rm -f "${pins}"
    return 0
  fi
  rm -f "${pins}"
  if "$@" > /dev/null 2>&1; then
    return 1
  fi
  return 2
}

# shipped_tools_arch — amd64, arm64, or nothing for a machine rclone is not pinned for.
shipped_tools_arch() {
  case "${VERSA_TOOLS_MACHINE:-$(uname -m)}" in
    x86_64|amd64) echo "amd64" ;;
    aarch64|arm64) echo "arm64" ;;
  esac
}

# install_fuse3
# Returns 0 installed now, 1 already there, 2 could not be installed.
install_fuse3() {
  if command -v "${VERSA_FUSERMOUNT_BIN:-fusermount3}" > /dev/null 2>&1; then
    return 1
  fi
  if command -v "${VERSA_APT_BIN:-apt-get}" > /dev/null 2>&1 \
     && "${VERSA_APT_BIN:-apt-get}" install -y -qq fuse3 > /dev/null 2>&1 \
     && command -v "${VERSA_FUSERMOUNT_BIN:-fusermount3}" > /dev/null 2>&1; then
    return 0
  fi
  return 2
}

# install_rclone <shipped_tools.ini>
# Installs rclone's own build at the pinned version, after its checksum matches.
# Returns 0 installed now, 1 already at that version, 2 not installed.
# SHIPPED_TOOL_NOTE says what happened, for setup's line.
install_rclone() {
  local ini="$1" version url arch sum have tmp zip got unpacked
  SHIPPED_TOOL_NOTE=""
  version=$(shipped_tool_value "${ini}" rclone version)
  url=$(shipped_tool_value "${ini}" rclone url)
  arch=$(shipped_tools_arch)
  if [ -z "${version}" ] || [ -z "${url}" ]; then
    SHIPPED_TOOL_NOTE="no rclone version in ${ini}"
    return 2
  fi
  if [ -z "${arch}" ]; then
    SHIPPED_TOOL_NOTE="rclone is not pinned for this kind of machine ($(uname -m))"
    return 2
  fi
  sum=$(shipped_tool_value "${ini}" rclone "sha256_${arch}")
  if [ -z "${sum}" ]; then
    SHIPPED_TOOL_NOTE="no checksum for rclone ${version} on ${arch}"
    return 2
  fi
  if [ -x "${RCLONE_DEST}" ]; then
    have=$("${RCLONE_DEST}" version 2>/dev/null | head -1 | awk '{print $2}')
    if [ "${have}" = "${version}" ]; then
      SHIPPED_TOOL_NOTE="rclone ${version}"
      return 1
    fi
  fi
  url="${url//\{version\}/${version}}"
  url="${url//\{arch\}/${arch}}"
  tmp=$(mktemp -d) || { SHIPPED_TOOL_NOTE="no temporary folder"; return 2; }
  zip="${tmp}/rclone.zip"
  if ! "${VERSA_CURL_BIN:-curl}" -fsSL --max-time 180 -o "${zip}" "${url}" 2>/dev/null; then
    rm -rf "${tmp}"
    SHIPPED_TOOL_NOTE="rclone ${version} could not be downloaded from ${url}"
    return 2
  fi
  got=$(sha256sum "${zip}" 2>/dev/null | awk '{print $1}')
  if [ "${got}" != "${sum}" ]; then
    rm -rf "${tmp}"
    SHIPPED_TOOL_NOTE="rclone ${version}: the download does not match its checksum, so it was not installed"
    return 2
  fi
  # python3 is already required by setup; unzip may not be installed.
  if ! python3 -m zipfile -e "${zip}" "${tmp}/out" > /dev/null 2>&1; then
    rm -rf "${tmp}"
    SHIPPED_TOOL_NOTE="rclone ${version}: the download could not be unpacked"
    return 2
  fi
  unpacked=$(find "${tmp}/out" -type f -name rclone | head -1)
  if [ -z "${unpacked}" ]; then
    rm -rf "${tmp}"
    SHIPPED_TOOL_NOTE="rclone ${version}: no rclone program in the download"
    return 2
  fi
  if ! install -m 755 "${unpacked}" "${RCLONE_DEST}" 2>/dev/null; then
    rm -rf "${tmp}"
    SHIPPED_TOOL_NOTE="rclone ${version} could not be written to ${RCLONE_DEST}"
    return 2
  fi
  rm -rf "${tmp}"
  SHIPPED_TOOL_NOTE="rclone ${version}"
  return 0
}
