"""Touches the heartbeat while a streamed model reply arrives (state_long_cycles.md §1.2).

A reply that is streamed delivers pieces for as long as the model works, so a
long reply is told apart from a call that has stopped answering: the stall
timer counts from the last piece, not from the start of the call.
"""

from __future__ import annotations

import time
from typing import Any, Callable

from langchain_core.callbacks import BaseCallbackHandler

MIN_GAP_SECONDS = 5.0


class StreamHeartbeat(BaseCallbackHandler):
    """Calls ``beat`` when a piece of a streamed reply arrives, at most every few seconds."""

    def __init__(self, beat: Callable[[], None], min_gap: float = MIN_GAP_SECONDS,
                 clock: Callable[[], float] = time.monotonic):
        self._beat = beat
        self._min_gap = min_gap
        self._clock = clock
        self._last = float("-inf")
        self.pieces = 0

    def on_llm_new_token(self, token: str, **kwargs: Any) -> None:
        self.pieces += 1
        now = self._clock()
        if now - self._last >= self._min_gap:
            self._last = now
            self._beat()
