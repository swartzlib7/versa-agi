#!/bin/bash
# ─────────────────────────────────────────────────────
# versa-agi-update — refresh persist repo, then setup.sh --update
#
# Copied to /usr/local/lib/versa-agi/update.sh on every install/--update
# (same path as backup/rekey). Do not generate a one-off stub.
#
# Pulls ~/.versa-agi/repo first so the setup.sh that runs is already
# current. Sets SKIP_PULL so setup.sh does not pull-and-re-exec with
# whatever stale flags the previous copy had.
#
# Usage:
#   sudo versa-agi-update
#   sudo versa-agi-update --dry-run
#
# © 2026 VersaVoice AI LLC — Licensed under BSL-1.1
# ─────────────────────────────────────────────────────

set -euo pipefail

_pu="${SUDO_USER:-${VERSA_PRIMARY_USER:-}}"
if [ -z "${_pu}" ] || [ "${_pu}" = "root" ]; then
  _pu="$(logname 2>/dev/null || true)"
fi
if [ -n "${_pu}" ]; then
  _home="$(eval echo "~${_pu}")"
else
  _home="${HOME}"
fi

_REPO="${_home}/.versa-agi/repo"
_SETUP="${_REPO}/src/setup.sh"

if [ -d "${_REPO}/.git" ]; then
  _git_user="${SUDO_USER:-$(whoami)}"
  echo "  Refreshing ${_REPO}…"
  if [ "$(id -u)" -eq 0 ] && [ "${_git_user}" != "root" ]; then
    chown -R "${_git_user}:${_git_user}" "${_REPO}/.git" 2>/dev/null || true
    sudo -u "${_git_user}" bash -c "
      cd '${_REPO}' || exit 1
      git fetch --all
      CURRENT_BRANCH=\$(git branch --show-current)
      git reset --hard \"origin/\${CURRENT_BRANCH:-main}\"
      git clean -fd
    "
  else
    (
      cd "${_REPO}"
      git fetch --all
      CURRENT_BRANCH="$(git branch --show-current)"
      git reset --hard "origin/${CURRENT_BRANCH:-main}"
      git clean -fd
    )
  fi
fi

if [ ! -f "${_SETUP}" ]; then
  echo "[ERROR] Setup script not found at: ${_SETUP}"
  echo "Expected the install-time clone at ~/.versa-agi/repo/ — re-run install.sh."
  exit 1
fi

export SKIP_PULL=true
exec "${_SETUP}" --update "$@"
