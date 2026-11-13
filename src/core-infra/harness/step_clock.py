"""How long this agent's steps take (state_long_cycles.md §1.7 rules 1–2).

A step is one model reply plus the tool calls it asked for. The harness calls
``note_reply`` at every model reply; a step's time is the gap to the next one.
Time waiting for mail and time inside live-call turns is left out.
"""

from __future__ import annotations

import statistics
import time
from collections import deque
from typing import Callable

WINDOW = 8
MIN_SAMPLES = 3
DEFAULT_STEP_SECONDS = 60.0
DEFAULT_LOCAL_STEP_SECONDS = 120.0


class StepClock:
    def __init__(self, local: bool = False, clock: Callable[[], float] = time.time):
        self._clock = clock
        self.default = DEFAULT_LOCAL_STEP_SECONDS if local else DEFAULT_STEP_SECONDS
        self._last: float | None = None
        self._times: deque = deque(maxlen=WINDOW)

    def start(self) -> None:
        """A model call is about to start with nothing to measure from (cycle
        start, or after a wait). The gap to its reply counts as a step."""
        if self._last is None:
            self._last = self._clock()

    def skip(self) -> None:
        """Time from here to the next ``start`` or reply is not step time."""
        self._last = None

    def note_reply(self, counted: bool = True) -> None:
        """A model reply arrived. ``counted=False`` for a live-call turn."""
        now = self._clock()
        if not counted:
            self._last = None
            return
        if self._last is not None:
            self._times.append(max(0.0, now - self._last))
        self._last = now

    @property
    def samples(self) -> int:
        return len(self._times)

    def typical(self) -> float:
        """Median of the last 8 step times; the default until 3 are measured."""
        if len(self._times) < MIN_SAMPLES:
            return self.default
        return float(statistics.median(self._times))
