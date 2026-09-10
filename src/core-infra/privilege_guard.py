"""Privilege-escalation patterns shared by the harness and execute CLI.

COA Autonomous Mode lifts the block only when Lifeline exported
VERSA_COA_AUTONOMOUS (INI true + sudoers file present) and the caller is COA.
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


def _is_coa_caller(src: dict) -> bool:
    name = (src.get("VERSA_AGENT_NAME") or "").strip().lower()
    if name == "coa":
        return True
    return (src.get("AGICTL_AGENT_USER") or "").strip().lower() == "coa"


def grant_landed_on_disk(
    ini_path: str = SETUP_INI,
    sudoers_path: str = SUDOERS_COA_AUTONOMOUS,
) -> bool:
    """True when INI is on and the sudoers file exists (the real grant)."""
    if not os.path.isfile(sudoers_path):
        return False
    try:
        section = ""
        with open(ini_path, encoding="utf-8") as fh:
            for line in fh:
                stripped = line.strip()
                if stripped.startswith("[") and stripped.endswith("]"):
                    section = stripped[1:-1].strip().lower()
                    continue
                if section != "coa":
                    continue
                eq = stripped.find("=")
                if eq <= 0:
                    continue
                if stripped[:eq].strip() == "autonomous":
                    return stripped[eq + 1 :].strip().lower() == "true"
    except OSError:
        return False
    return False


def coa_autonomous_allowed(env: dict | None = None) -> bool:
    """True only for COA when the Autonomous grant is in effect.

    Lifeline exports ``VERSA_COA_AUTONOMOUS``. The agictl-wrapper sudo hop
    used to drop that flag, so execute CLI still scanned ``sudo`` out of
    scripts COA was allowed to run. When *env* is omitted (live process),
    fall back to the on-disk grant so a stripped environment does not
    re-block writes. A passed dict is honored as-is (tests).
    """
    src = env if env is not None else os.environ
    if not _is_coa_caller(src):
        return False
    flag = (src.get("VERSA_COA_AUTONOMOUS") or "").strip().lower()
    if flag in _TRUTHY:
        return True
    if env is not None:
        return False
    return grant_landed_on_disk()


_COA_USER_RE = re.compile(r"^[a-z_][a-z0-9_-]*$")


def sudoers_line(coa_user: str) -> str:
    """One visudo-safe sudoers line for the COA OS user."""
    user = (coa_user or "").strip()
    if not _COA_USER_RE.fullmatch(user):
        raise ValueError(f"invalid COA OS user {user!r}")
    return f"{user} ALL=(ALL) NOPASSWD: ALL\n"
