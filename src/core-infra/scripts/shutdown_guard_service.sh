#!/usr/bin/env bash
# Sourced by setup.sh (fresh install and --update).
# Installs the shutdown guard script and writes versa-agi-shutdown-guard.service
# around it (state_host_services.md §1.4.1).
#
# The unit does its work when it is stopped: at shutdown or reboot systemd runs
# ExecStop, and the script asks every agent in a cycle to hand off. Units stop
# in the reverse of their start order, so the After= list is what keeps the
# network, name lookup, cron (Lifeline), the link service and the model servers
# running until the guard has finished.
#
# The caller enables and starts the unit and must never restart it: a restart
# runs ExecStop. (The script itself acts only while the system is stopping.)

SHUTDOWN_GUARD_SCRIPT="${SHUTDOWN_GUARD_SCRIPT:-/usr/local/lib/versa-agi/shutdown_guard.sh}"
SHUTDOWN_GUARD_UNIT_FILE="${SHUTDOWN_GUARD_UNIT_FILE:-/etc/systemd/system/versa-agi-shutdown-guard.service}"

# install_shutdown_guard <src_core_infra>
# Returns 0 when the script or unit changed, 1 when both were already current.
install_shutdown_guard() {
  local src="$1"
  local changed=1 tmp
  install -d -m 755 "$(dirname "${SHUTDOWN_GUARD_SCRIPT}")"
  if ! cmp -s "${src}/scripts/shutdown_guard.sh" "${SHUTDOWN_GUARD_SCRIPT}" 2>/dev/null; then
    install -m 755 "${src}/scripts/shutdown_guard.sh" "${SHUTDOWN_GUARD_SCRIPT}"
    changed=0
  fi
  tmp=$(mktemp)
  cat > "${tmp}" <<GUARDEOF
[Unit]
Description=Versa AGi shutdown guard (agents hand off before the computer goes down)
After=network.target network-online.target NetworkManager.service systemd-networkd.service systemd-resolved.service wpa_supplicant.service cron.service versa-agi-link.service versa-agi-tunnel.service ollama.service docker.service

[Service]
Type=oneshot
RemainAfterExit=yes
# A flag left from the shutdown before this boot.
ExecStart=/bin/rm -f /var/lib/versa-agi/shutdown_pending
ExecStop=${SHUTDOWN_GUARD_SCRIPT}
# The longest grace (300 s) plus the wait for Lifeline's end reports.
TimeoutStopSec=360
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
GUARDEOF
  if ! cmp -s "${tmp}" "${SHUTDOWN_GUARD_UNIT_FILE}" 2>/dev/null; then
    install -m 644 "${tmp}" "${SHUTDOWN_GUARD_UNIT_FILE}"
    changed=0
  fi
  rm -f "${tmp}"
  systemctl daemon-reload 2>/dev/null || true
  return "${changed}"
}
