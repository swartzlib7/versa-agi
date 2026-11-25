#!/usr/bin/env bash
# Sourced by setup.sh, install_acceptance.sh and uninstall.sh.
# The host side of the sleep hold (state_host_services.md §1.6.1, §1.6.2).
#
# Lifeline asks Ubuntu's login service (logind) not to sleep while a cycle
# runs. Two things belong to the installer, because only root can set them:
#   1. the permission: an account without a desktop session may not hold
#      sleep, so one polkit rule allows the watchdog account to;
#   2. the lid: logind's lid switch ignores holds unless
#      LidSwitchIgnoreInhibited=no. Asked on a laptop only, off by default.
# Nothing here depends on a desktop or compositor: it is logind and polkit, as
# a fresh Ubuntu ships them.

SLEEP_HOLD_RULE_FILE="${SLEEP_HOLD_RULE_FILE:-/etc/polkit-1/rules.d/50-versa-agi-sleep.rules}"
LID_DROPIN_FILE="${LID_DROPIN_FILE:-/etc/systemd/logind.conf.d/50-versa-agi-lid.conf}"

# machine_is_laptop
# Returns 0 when this machine has a lid: the chassis is a laptop, convertible
# or tablet, or the kernel reports a lid switch.
machine_is_laptop() {
  local chassis
  chassis=$("${VERSA_HOSTNAMECTL_BIN:-hostnamectl}" chassis 2>/dev/null | tr -d '[:space:]' || true)
  case "${chassis}" in
    laptop|convertible|tablet) return 0 ;;
  esac
  compgen -G "${VERSA_LID_DIR:-/proc/acpi/button/lid}/*" > /dev/null 2>&1
}

# install_sleep_hold_rule <watchdog user> <true|false>
# true: write the rule; false: remove it.
# Returns 0 when something changed, 1 when it was already as asked, 2 when
# this machine has no polkit rules folder (the hold is then not available).
install_sleep_hold_rule() {
  local user="$1" enabled="${2:-true}" tmp
  if [ "${enabled}" != "true" ]; then
    [ -f "${SLEEP_HOLD_RULE_FILE}" ] || return 1
    rm -f "${SLEEP_HOLD_RULE_FILE}"
    return 0
  fi
  [ -d "$(dirname "${SLEEP_HOLD_RULE_FILE}")" ] || return 2
  tmp=$(mktemp)
  cat > "${tmp}" <<RULEEOF
// Written by Versa AGi setup ([agent] sleep_hold=true).
// Lifeline, as ${user}, asks the login service not to sleep while an agent's
// cycle runs. An account without a desktop session needs this rule for that.
polkit.addRule(function(action, subject) {
    if (action.id == "org.freedesktop.login1.inhibit-block-sleep" &&
        subject.user == "${user}") {
        return polkit.Result.YES;
    }
});
RULEEOF
  if cmp -s "${tmp}" "${SLEEP_HOLD_RULE_FILE}" 2>/dev/null; then
    rm -f "${tmp}"
    return 1
  fi
  install -m 644 "${tmp}" "${SLEEP_HOLD_RULE_FILE}"
  rm -f "${tmp}"
  return 0
}

# apply_lid_setting <true|false>
# true: the lid respects the hold (a shut lid waits for a working agent).
# false: remove the setting, if setup wrote it.
# Returns 0 when something changed, 1 when it was already as asked. On a
# change the login service re-reads its settings; it is never restarted,
# because a restart can end desktop sessions.
apply_lid_setting() {
  local enabled="${1:-false}" tmp
  if [ "${enabled}" != "true" ]; then
    [ -f "${LID_DROPIN_FILE}" ] || return 1
    rm -f "${LID_DROPIN_FILE}"
    systemctl reload systemd-logind 2>/dev/null || true
    return 0
  fi
  tmp=$(mktemp)
  cat > "${tmp}" <<LIDEOF
# Written by Versa AGi setup ([agent] lid_waits_for_agent=true).
# A shut lid waits while an agent's cycle holds sleep.
[Login]
LidSwitchIgnoreInhibited=no
LIDEOF
  if cmp -s "${tmp}" "${LID_DROPIN_FILE}" 2>/dev/null; then
    rm -f "${tmp}"
    return 1
  fi
  install -d -m 755 "$(dirname "${LID_DROPIN_FILE}")"
  install -m 644 "${tmp}" "${LID_DROPIN_FILE}"
  rm -f "${tmp}"
  systemctl reload systemd-logind 2>/dev/null || true
  return 0
}
