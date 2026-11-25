#!/usr/bin/env bash
# Sourced by setup_local.sh (client remote flow) and setup.sh --update.
# Installs the tunnel runner and writes versa-agi-tunnel.service around it.
# Retry schedule lives in the runner (state_host_services.md §1.1); systemd
# Restart= only covers the runner itself crashing.

TUNNEL_RUNNER="/usr/local/lib/versa-agi/versa_agi_tunnel.sh"
TUNNEL_UNIT_FILE="/etc/systemd/system/versa-agi-tunnel.service"

# install_tunnel_service <src_core_infra> <host> <port> <ssh_key> <user>
# Returns 0 when the runner or unit changed, 1 when both were already current.
install_tunnel_service() {
  local src="$1" host="$2" port="$3" key="$4" user="$5"
  local changed=1 tmp
  install -d -m 755 "$(dirname "${TUNNEL_RUNNER}")"
  if ! cmp -s "${src}/scripts/versa_agi_tunnel.sh" "${TUNNEL_RUNNER}" 2>/dev/null; then
    install -m 755 "${src}/scripts/versa_agi_tunnel.sh" "${TUNNEL_RUNNER}"
    changed=0
  fi
  tmp=$(mktemp)
  cat > "${tmp}" <<TUNNELEOF
[Unit]
Description=Versa AGi Inference SSH Tunnel (${host}:${port})
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${user}
ExecStart=${TUNNEL_RUNNER} -L ${port} -i ${key} ${user}@${host}
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
TUNNELEOF
  if ! cmp -s "${tmp}" "${TUNNEL_UNIT_FILE}" 2>/dev/null; then
    install -m 644 "${tmp}" "${TUNNEL_UNIT_FILE}"
    changed=0
  fi
  rm -f "${tmp}"
  systemctl daemon-reload 2>/dev/null || true
  return "${changed}"
}

# Prints "host port key user" from an installed unit (old ssh ExecStart or the
# runner form). Prints nothing when there is no unit or it cannot be parsed.
read_tunnel_service() {
  [ -f "${TUNNEL_UNIT_FILE}" ] || return 0
  local exec_line port key target
  exec_line=$(sed -n '/^ExecStart=/,/^[A-Za-z]*=/p' "${TUNNEL_UNIT_FILE}" | grep -v '^Restart' | tr -d '\\\n')
  port=$(printf '%s' "${exec_line}" | grep -oP -- '-L\s+\K[0-9]+' | head -1)
  key=$(printf '%s' "${exec_line}" | grep -oP -- '-i\s+\K\S+' | head -1)
  target=$(printf '%s' "${exec_line}" | grep -oP '\S+@\S+' | tail -1)
  [ -n "${port}" ] && [ -n "${key}" ] && [ -n "${target}" ] || return 0
  printf '%s %s %s %s\n' "${target#*@}" "${port}" "${key}" "${target%@*}"
}
