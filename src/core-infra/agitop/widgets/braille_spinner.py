"""Rich/cli-spinners dots2 braille animation helpers for agitop."""

from __future__ import annotations

DOTS2 = "⣾⣽⣻⢿⡿⣟⣯⣷"
DOTS2_INTERVAL_S = 0.08


def dots2_char(tick: int) -> str:
    return DOTS2[tick % len(DOTS2)]


def dots2_markup(tick: int, label: str, color: str = "cyan") -> str:
    ch = dots2_char(tick)
    if label:
        return f"[{color}]{ch}[/] {label}"
    return f"[{color}]{ch}[/]"


def parse_cycle_agent(cycle_id: str | None) -> str | None:
    """Extract agent name from cycle_id (format: agent-EPOCH)."""
    if not cycle_id or "-" not in cycle_id:
        return None
    return cycle_id.rsplit("-", 1)[0]
