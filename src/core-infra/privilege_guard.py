"""Privilege-escalation patterns shared by the harness and execute CLI.

COA Autonomous Mode lifts the text scanner when the caller is COA and the
sudoers grant file exists (that is what the OS will run). Lifeline may also
export VERSA_COA_AUTONOMOUS; a passed env dict is honored as-is (tests).
"""
from __future__ import annotations

import os
import re

BLOCKED_PATTERNS = (
    "sudo ",
    "sudo\t",
    " sudo ",
    "su ",
    "su\t",
    " su ",
    "newgrp ",
    "pkexec ",
    "gpasswd ",
    "usermod ",
)

_TRUTHY = frozenset({"1", "true", "yes", "on"})

SETUP_INI = "/etc/versa-agi/setup.ini"
SUDOERS_COA_AUTONOMOUS = "/etc/sudoers.d/versa_agi_coa_autonomous"


def privilege_escalation_hit(text: str) -> str | None:
    """Return the matched pattern if *text* looks like privilege escalation."""
    if not text:
        return None
    lowered = text.lower()
    for pattern in BLOCKED_PATTERNS:
        if pattern in lowered or lowered.startswith(pattern.strip()):
            return pattern.strip()
    return None


def _ini_value(section: str, key: str, ini_path: str = SETUP_INI) -> str:
    try:
        current = ""
        with open(ini_path, encoding="utf-8") as fh:
            for line in fh:
                stripped = line.strip()
                if stripped.startswith("[") and stripped.endswith("]"):
                    current = stripped[1:-1].strip().lower()
                    continue
                if current != section:
                    continue
                eq = stripped.find("=")
                if eq <= 0:
                    continue
                if stripped[:eq].strip() == key:
                    return stripped[eq + 1 :].strip()
    except OSError:
        return ""
    return ""


def _coa_os_user() -> str:
    env_u = (os.environ.get("VERSA_COA_USER") or "").strip().lower()
    if env_u:
        return env_u
    ini_u = _ini_value("users", "coa").lower()
    return ini_u or "coa"


def _is_coa_caller(src: dict) -> bool:
    coa = _coa_os_user()
    aliases = {"coa", coa}
    name = (src.get("VERSA_AGENT_NAME") or "").strip().lower()
    stamp = (src.get("AGICTL_AGENT_USER") or "").strip().lower()
    return name in aliases or stamp in aliases


def grant_landed_on_disk(
    ini_path: str = SETUP_INI,
    sudoers_path: str = SUDOERS_COA_AUTONOMOUS,
) -> bool:
    """True when INI is on and the sudoers file exists."""
    if not os.path.isfile(sudoers_path):
        return False
    return _ini_value("coa", "autonomous", ini_path).lower() == "true"


def sudoers_grant_present(sudoers_path: str = SUDOERS_COA_AUTONOMOUS) -> bool:
    """The OS grant — scanner lift follows this file, not INI readability."""
    return os.path.isfile(sudoers_path)


def coa_autonomous_allowed(env: dict | None = None) -> bool:
    """True only for COA when the Autonomous grant is in effect.

    Lifeline exports ``VERSA_COA_AUTONOMOUS``. When *env* is omitted (live
    process), the sudoers file is enough — that is what ``sudo`` will honor.
    Requiring INI∧file left the scanner on after a landed grant when INI
    could not be read or lagged the file. A passed dict is honored as-is
    (tests) and never reads disk.
    """
    src = env if env is not None else os.environ
    if not _is_coa_caller(src):
        return False
    flag = (src.get("VERSA_COA_AUTONOMOUS") or "").strip().lower()
    if flag in _TRUTHY:
        return True
    if env is not None:
        return False
    return sudoers_grant_present()


_COA_USER_RE = re.compile(r"^[a-z_][a-z0-9_-]*$")


def sudoers_line(coa_user: str) -> str:
    """One visudo-safe sudoers line for the COA OS user."""
    user = (coa_user or "").strip()
    if not _COA_USER_RE.fullmatch(user):
        raise ValueError(f"invalid COA OS user {user!r}")
    return f"{user} ALL=(ALL) NOPASSWD: ALL\n"
