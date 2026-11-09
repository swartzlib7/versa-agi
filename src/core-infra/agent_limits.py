"""Per-agent run limits: defaults and the Timeout range (state_live_chat.md §1.5).

One source for agictl, agitop and setup. lifeline.sh and init_agents_db.sh
are bash and carry the same numbers; keep them in step with this file.
"""

from __future__ import annotations

DEFAULT_TIMEOUT_MINUTES = 45
DEFAULT_RUNAWAY_LINES = 2500
DEFAULT_RUNAWAY_SIZE_KB = 2048

# Timeout is the safety kill for a stuck run. 30 keeps room for a live call
# (longest choice 25 min) inside a fresh run; 60 bounds a stuck run.
TIMEOUT_MIN_MINUTES = 30
TIMEOUT_MAX_MINUTES = 60


def timeout_error(minutes) -> str:
    """Empty when ``minutes`` is a valid Timeout, else a message for the user."""
    try:
        value = int(str(minutes).strip())
    except (TypeError, ValueError):
        return "Timeout must be a whole number of minutes."
    if not TIMEOUT_MIN_MINUTES <= value <= TIMEOUT_MAX_MINUTES:
        return (f"Timeout must be between {TIMEOUT_MIN_MINUTES} and "
                f"{TIMEOUT_MAX_MINUTES} minutes (got {value}).")
    return ""


def clamp_timeout(minutes) -> int:
    """Nearest valid Timeout; the default when ``minutes`` is not a number."""
    try:
        value = int(str(minutes).strip())
    except (TypeError, ValueError):
        return DEFAULT_TIMEOUT_MINUTES
    return max(TIMEOUT_MIN_MINUTES, min(TIMEOUT_MAX_MINUTES, value))


# Live Chat listening window, [live_chat] idle_minutes (state_live_chat.md D5).
LISTEN_MINUTES_CHOICES = (0, 5, 10, 15, 30)
DEFAULT_LISTEN_MINUTES = 15


def listen_minutes_error(minutes) -> str:
    """Empty when ``minutes`` is an allowed listening setting, else a message."""
    allowed = ", ".join(str(c) for c in LISTEN_MINUTES_CHOICES)
    try:
        value = int(str(minutes).strip())
    except (TypeError, ValueError):
        return f"Chat listening must be one of {allowed} minutes (0 = Off)."
    if value not in LISTEN_MINUTES_CHOICES:
        return f"Chat listening must be one of {allowed} minutes (0 = Off), got {value}."
    return ""


def snap_listen_minutes(minutes) -> int:
    """Allowed listening setting nearest ``minutes`` (ties go shorter); default when not a number."""
    try:
        value = int(str(minutes).strip())
    except (TypeError, ValueError):
        return DEFAULT_LISTEN_MINUTES
    return min(LISTEN_MINUTES_CHOICES, key=lambda c: (abs(c - value), c))
