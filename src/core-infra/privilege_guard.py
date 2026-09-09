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


def privilege_escalation_hit(text: str) -> str | None:
    """Return the matched pattern if *text* looks like privilege escalation."""
    if not text:
        return None
    lowered = text.lower()
    for pattern in BLOCKED_PATTERNS:
        if pattern in lowered or lowered.startswith(pattern.strip()):
            return pattern.strip()
    return None


def coa_autonomous_allowed(env: dict | None = None) -> bool:
    """True only for COA when Lifeline exported a landed grant."""
    src = env if env is not None else os.environ
    flag = (src.get("VERSA_COA_AUTONOMOUS") or "").strip().lower()
    if flag not in _TRUTHY:
        return False
    name = (src.get("VERSA_AGENT_NAME") or "").strip().lower()
    return name == "coa"


_COA_USER_RE = re.compile(r"^[a-z_][a-z0-9_-]*$")


def sudoers_line(coa_user: str) -> str:
    """One visudo-safe sudoers line for the COA OS user."""
    user = (coa_user or "").strip()
    if not _COA_USER_RE.fullmatch(user):
        raise ValueError(f"invalid COA OS user {user!r}")
    return f"{user} ALL=(ALL) NOPASSWD: ALL\n"
