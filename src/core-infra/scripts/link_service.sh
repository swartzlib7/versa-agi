#!/usr/bin/env bash
# Sourced by setup.sh (fresh install and --update).
# Installs the link service runner and writes versa-agi-link.service around it
# (state_realtime_link.md RL-05). The runner dials out to VersaVoice and holds
# one request open; nothing listens for inbound connections. Its retry
# schedule lives in the runner; systemd Restart= only covers the runner
# itself crashing.

LINK_RUNNER="${LINK_RUNNER:-/usr/local/lib/versa-agi/versa_agi_link.py}"
LINK_UNIT_FILE="${LINK_UNIT_FILE:-/etc/systemd/system/versa-agi-link.service}"

# install_link_service <src_core_infra> <user> <installed_core_infra>
# Returns 0 when the runner or unit changed, 1 when both were already current.
install_link_service() {
  local src="$1" user="$2" core_infra="$3"
  local changed=1 tmp
  install -d -m 755 "$(dirname "${LINK_RUNNER}")"
  if ! cmp -s "${src}/scripts/versa_agi_link.py" "${LINK_RUNNER}" 2>/dev/null; then
    install -m 755 "${src}/scripts/versa_agi_link.py" "${LINK_RUNNER}"
    changed=0
  fi
  tmp=$(mktemp)
  cat > "${tmp}" <<LINKEOF
[Unit]
Description=Versa AGi link (mail signals pushed from VersaVoice)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${user}
Environment=VERSA_CORE_INFRA=${core_infra}
Environment=PATH=/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin
ExecStart=/usr/bin/python3 ${LINK_RUNNER}
Restart=always
RestartSec=10
# An agent woken by a mail signal is started by this service, so its cycle
# lives in this unit's control group. Stopping or restarting the service (an
# update, a crash of the runner) must end the runner only, never that cycle.
KillMode=process
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
LINKEOF
  if ! cmp -s "${tmp}" "${LINK_UNIT_FILE}" 2>/dev/null; then
    install -m 644 "${tmp}" "${LINK_UNIT_FILE}"
    changed=0
  fi
  rm -f "${tmp}"
  systemctl daemon-reload 2>/dev/null || true
  return "${changed}"
}
