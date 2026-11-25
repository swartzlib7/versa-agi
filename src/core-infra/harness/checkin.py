"""30-minute check-in during a long cycle (state_long_cycles.md §1.6).

The harness writes one line to the cycle row at every check-in. The message
to the agent is a separate step and is held back in some states; the line is
written either way.
"""

from __future__ import annotations

import time
from typing import Callable

INTERVAL_SECONDS = 1800


def checkin_message(minutes: int) -> str:
    return (
        f"CHECK-IN — you have been running for {minutes} minutes. For each task you are working "
        "on, record where you are: `agictl task progress <id> \"DONE: … | NEXT: …\"`. Then carry on."
    )


def checkin_line(minutes: int, steps: int, tokens: int, last_tool: str,
                 now: float | None = None) -> str:
    stamp = time.strftime("%Y-%m-%d %H:%M", time.localtime(now if now is not None else time.time()))
    tool = (last_tool or "none").replace("\n", " ").strip()
    if len(tool) > 160:
        tool = tool[:159] + "…"
    return f"{stamp} | {minutes} min | {steps} steps | {tokens} tokens | last tool: {tool}"


class CheckIns:
    def __init__(self, started_epoch: float | None = None, interval: float = INTERVAL_SECONDS,
                 clock: Callable[[], float] = time.time):
        self._clock = clock
        self.started = started_epoch if started_epoch is not None else clock()
        self.interval = interval
        self._done = 0

    def due(self) -> int | None:
        """Minutes run so far when a check-in is due, else None."""
        if self.interval <= 0:
            return None
        elapsed = self._clock() - self.started
        if int(elapsed // self.interval) > self._done:
            return max(1, int(round(elapsed / 60.0)))
        return None

    def mark(self) -> None:
        """This check-in is done. A missed interval is not made up later."""
        self._done = int((self._clock() - self.started) // self.interval)
