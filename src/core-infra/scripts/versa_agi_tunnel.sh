#!/usr/bin/env bash
# SSH tunnel to the remote inference server (client topology).
# Run by versa-agi-tunnel.service as the watchdog user.
#
#   versa_agi_tunnel.sh -L <port> -i <key> <user>@<host>
#
# The -L / user@host shape matches the old ssh ExecStart, which setup.sh
# --update still parses to rebuild client_config.json.
#
# Retry schedule while the server is unreachable (state_host_services.md §1.1):
#   first retry after 5 s, then every minute for 30 minutes, then every 30 minutes.
# A connection that stayed up 5 minutes counts as recovered; the next drop
# starts the schedule again.
set -u

PORT=""
KEY=""
TARGET=""
while [ $# -gt 0 ]; do
  case "$1" in
    -L) PORT="$2"; shift 2 ;;
    -i) KEY="$2"; shift 2 ;;
    *)  TARGET="$1"; shift ;;
  esac
done
if [ -z "${PORT}" ] || [ -z "${KEY}" ] || [ -z "${TARGET}" ]; then
  echo "usage: $0 -L <port> -i <key> <user>@<host>" >&2
  exit 2
fi

FIRST_RETRY_SECONDS="${TUNNEL_FIRST_RETRY_SECONDS:-5}"
FAST_RETRY_SECONDS="${TUNNEL_FAST_RETRY_SECONDS:-60}"
FAST_WINDOW_SECONDS="${TUNNEL_FAST_WINDOW_SECONDS:-1800}"
SLOW_RETRY_SECONDS="${TUNNEL_SLOW_RETRY_SECONDS:-1800}"
STABLE_SECONDS="${TUNNEL_STABLE_SECONDS:-300}"
SSH_BIN="${TUNNEL_SSH_BIN:-/usr/bin/ssh}"
SLEEP_BIN="${TUNNEL_SLEEP_BIN:-sleep}"
MAX_ATTEMPTS="${TUNNEL_MAX_ATTEMPTS:-0}"

# Tests point TUNNEL_CLOCK_FILE at a fake clock their sleep stub advances.
clock() {
  if [ -n "${TUNNEL_CLOCK_FILE:-}" ]; then cat "${TUNNEL_CLOCK_FILE}"; else date +%s; fi
}

down_since=""
slow_announced=false
attempts=0

while true; do
  started=$(clock)
  "${SSH_BIN}" -N -L "${PORT}:localhost:${PORT}" \
    -o ServerAliveInterval=30 \
    -o ServerAliveCountMax=3 \
    -o ConnectTimeout=10 \
    -o BatchMode=yes \
    -o StrictHostKeyChecking=accept-new \
    -o ExitOnForwardFailure=yes \
    -i "${KEY}" \
    "${TARGET}"
  rc=$?
  attempts=$((attempts + 1))
  now=$(clock)

  if [ -z "${down_since}" ] || [ $((now - started)) -ge "${STABLE_SECONDS}" ]; then
    down_since=${now}
    slow_announced=false
    echo "Tunnel to ${TARGET} closed (exit ${rc}). Retrying every minute for 30 minutes."
    wait_seconds=${FIRST_RETRY_SECONDS}
  elif [ $((now - down_since)) -lt "${FAST_WINDOW_SECONDS}" ]; then
    wait_seconds=${FAST_RETRY_SECONDS}
  else
    if [ "${slow_announced}" = false ]; then
      echo "Tunnel to ${TARGET} still down after 30 minutes. Retrying every $((SLOW_RETRY_SECONDS / 60)) minutes."
      slow_announced=true
    fi
    wait_seconds=${SLOW_RETRY_SECONDS}
  fi

  if [ "${MAX_ATTEMPTS}" -gt 0 ] && [ "${attempts}" -ge "${MAX_ATTEMPTS}" ]; then
    exit 0
  fi
  "${SLEEP_BIN}" "${wait_seconds}"
done
