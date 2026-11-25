#!/usr/bin/env bash
# ─────────────────────────────────────────────────────
# Versa AGi — shutdown guard
#
# ExecStop of versa-agi-shutdown-guard.service, run as root when the computer
# shuts down or reboots (state_host_services.md §1.4.1). Agents that are in a
# cycle are asked to hand off, and Lifeline reports each cycle's end to
# VersaVoice, before the network and the model servers go down.
#
# Installed to /usr/local/lib/versa-agi/shutdown_guard.sh by
# scripts/shutdown_guard_service.sh.
#
# The VERSA_* variables below exist for the tests.
# ─────────────────────────────────────────────────────

set -u

STATE_DIR="${VERSA_STATE_DIR:-/var/lib/versa-agi}"
SETUP_INI="${VERSA_SETUP_INI:-/etc/versa-agi/setup.ini}"
LOG_FILE="${VERSA_LIFELINE_LOG:-/var/log/versa-agi-lifeline.log}"
AGICTL_BIN="${VERSA_AGICTL_BIN:-/usr/local/bin/agictl}"
SYSTEMCTL_BIN="${VERSA_SYSTEMCTL_BIN:-systemctl}"
PGREP_BIN="${VERSA_PGREP_BIN:-pgrep}"
PKILL_BIN="${VERSA_PKILL_BIN:-pkill}"
SLEEP_BIN="${VERSA_SLEEP_BIN:-sleep}"
# Seconds Lifeline gets to report a cycle's end once its harness is gone.
END_WAIT_SECONDS="${VERSA_SHUTDOWN_END_WAIT:-20}"
# The harness's own Python process: its command line starts with the
# interpreter. The shell, `timeout` and `sudo` that started it carry the same
# words further along and end when it ends; a process that only mentions the
# module name (an editor, a search) must not hold the shutdown.
HARNESS_PATTERN='^[^ ]*python[0-9.]* -m harness\.agent_harness --agent '
FLAG="${STATE_DIR}/shutdown_pending"

log() {
  printf '[%s] [SHUTDOWN-GUARD] %s\n' "$(date '+%Y-%m-%dT%H:%M:%S%z')" "$*" >> "${LOG_FILE}" 2>/dev/null || true
}

clock() {
  if [ -n "${VERSA_GUARD_CLOCK_FILE:-}" ]; then
    cat "${VERSA_GUARD_CLOCK_FILE}"
  else
    date +%s
  fi
}

# Names of the agents with a running harness, one per line, read from the
# harness command line (`--agent coa`).
running_agents() {
  "${PGREP_BIN}" -af "${HARNESS_PATTERN}" 2>/dev/null \
    | sed -nE "s/^[0-9]+ [^ ]*python[0-9.]* -m harness\.agent_harness --agent '?([A-Za-z0-9][A-Za-z0-9_-]*)'?( .*)?$/\1/p" | sort -u
}

harness_running() {
  "${PGREP_BIN}" -f "${HARNESS_PATTERN}" > /dev/null 2>&1
}

# ─── Only at a real shutdown ─────────────────────────
# systemd also runs ExecStop when the unit is stopped by hand or removed by
# uninstall. Agents are only closed when the whole system is going down.
if [ "$("${SYSTEMCTL_BIN}" is-system-running 2>/dev/null || true)" != "stopping" ]; then
  exit 0
fi

GRACE=$(grep -Po '^\s*shutdown_grace_seconds\s*=\s*\K[0-9]+' "${SETUP_INI}" 2>/dev/null | head -1 || true)
case "${GRACE}" in
  ''|*[!0-9]*) GRACE=120 ;;
esac
if [ "${GRACE}" -eq 0 ]; then
  log "SHUTDOWN: the guard is switched off (shutdown_grace_seconds=0) — agents are not asked to hand off"
  exit 0
fi
[ "${GRACE}" -lt 30 ] && GRACE=30
[ "${GRACE}" -gt 300 ] && GRACE=300

# Lifeline starts no agent while this flag is newer than the boot.
date +%s > "${FLAG}" 2>/dev/null || true
chmod 644 "${FLAG}" 2>/dev/null || true

AGENTS=$(running_agents)
if [ -z "${AGENTS}" ]; then
  log "SHUTDOWN: the computer is shutting down — no agent is in a cycle"
  exit 0
fi

DEADLINE=$(( $(clock) + GRACE ))
log "SHUTDOWN: the computer is shutting down — asking $(printf '%s\n' "${AGENTS}" | paste -sd' ' -) to hand off within ${GRACE} s"
for agent in ${AGENTS}; do
  "${AGICTL_BIN}" agent chat-close "${agent}" --reason shutdown --deadline-epoch "${DEADLINE}" > /dev/null 2>&1 \
    || log "SHUTDOWN: ${agent} could not be asked to close its cycle"
done

while harness_running && [ "$(clock)" -lt "${DEADLINE}" ]; do
  "${SLEEP_BIN}" 2
done

if harness_running; then
  log "SHUTDOWN: $(running_agents | paste -sd' ' -) did not close within ${GRACE} s — stopping; the thread is repaired at the next spawn"
  "${PKILL_BIN}" -TERM -f "${HARNESS_PATTERN}" 2>/dev/null || true
  tries=0
  while harness_running && [ "${tries}" -lt 5 ]; do
    "${SLEEP_BIN}" 1
    tries=$(( tries + 1 ))
  done
else
  log "SHUTDOWN: every agent closed its cycle"
fi

# Lifeline reports each end to VersaVoice once the harness is gone, then
# removes the agent's activity_open marker.
END_DEADLINE=$(( $(clock) + END_WAIT_SECONDS ))
while true; do
  OWED=""
  for agent in ${AGENTS}; do
    [ -e "${STATE_DIR}/${agent}/activity_open" ] && OWED="${OWED}${agent} "
  done
  [ -z "${OWED}" ] && break
  [ "$(clock)" -ge "${END_DEADLINE}" ] && break
  "${SLEEP_BIN}" 1
done
if [ -n "${OWED}" ]; then
  log "SHUTDOWN: Lifeline did not report the end of ${OWED}within ${END_WAIT_SECONDS} s — VersaVoice shows it asleep after 3 minutes without a proof of life"
else
  log "SHUTDOWN: Lifeline reported every cycle's end"
fi
exit 0
