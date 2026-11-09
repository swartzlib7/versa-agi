"""Time warnings before Lifeline's hard stop (state_live_chat.md §1.5.3).

Lifeline passes ``--deadline-epoch``: the moment its ``timeout`` kills this
run. The agent is told nothing at the start (no pressure). At 10 minutes left
and at 5 minutes left it gets one message each. A run that is first checked
inside the last 5 minutes gets only the 5-minute message.
"""

from __future__ import annotations

import time
from typing import Callable

TEN_MINUTES = 600
FIVE_MINUTES = 300


def _minutes(seconds: float) -> int:
    return max(1, int(round(seconds / 60.0)))


def _clock_time(epoch: float) -> str:
    return time.strftime("%H:%M", time.localtime(epoch))


def ten_minute_message(elapsed_s: float, left_s: float, deadline_epoch: float) -> str:
    return (
        f"⏱ TIME CHECK — You have been running for {_minutes(elapsed_s)} minutes. This session "
        f"ends in about {_minutes(left_s)} minutes and is stopped hard at {_clock_time(deadline_epoch)}. "
        "Start wrapping up: finish what you are doing, tell anyone you are talking with where "
        "things stand, then close with `agictl cycle end \"summary\" --handoff \"Open: … | Next: …\"`. "
        "Unfinished work and conversations continue in your next spawn."
    )


def ten_minute_listening_message(elapsed_s: float, left_s: float, deadline_epoch: float) -> str:
    """10-minute warning while a live chat is open: keep chatting; the window closes at 5."""
    return (
        f"⏱ TIME CHECK — You have been running for {_minutes(elapsed_s)} minutes. This live chat "
        f"stays open for about {max(1, _minutes(left_s) - 5)} more minutes; the session is stopped "
        f"hard at {_clock_time(deadline_epoch)}. Keep chatting, and finish anything you can in that "
        "time. If it matters, let the person know you will pick things up again shortly. "
        "Unfinished work and conversations continue in your next spawn."
    )


def five_minute_message(left_s: float, deadline_epoch: float) -> str:
    return (
        f"⏱ TIME CHECK — About {_minutes(left_s)} minutes left before this session is stopped at "
        f"{_clock_time(deadline_epoch)}. Close now: send any last replies, then "
        "`agictl cycle end` with a handoff. Anything unfinished continues in your next spawn."
    )


class DeadlineWarnings:
    """Which time warning is due. ``mark`` only after the message reached the model."""

    def __init__(self, deadline_epoch: float | None, started_epoch: float | None = None,
                 clock: Callable[[], float] = time.time):
        self.deadline = float(deadline_epoch) if deadline_epoch else None
        self._clock = clock
        self.started = started_epoch if started_epoch is not None else clock()
        self._sent: set[int] = set()

    def due(self, listening: bool = False) -> tuple[int, str] | None:
        """(stage, message) for the most urgent unsent warning, else None.

        ``listening``: a live chat window is open (state_live_chat.md §1.5.3);
        the 10-minute message then says to keep chatting. The caller replaces
        the 5-minute message with the window's closing message.
        """
        if self.deadline is None:
            return None
        now = self._clock()
        left = self.deadline - now
        if left <= FIVE_MINUTES and FIVE_MINUTES not in self._sent:
            return FIVE_MINUTES, five_minute_message(left, self.deadline)
        if left <= TEN_MINUTES and TEN_MINUTES not in self._sent and FIVE_MINUTES not in self._sent:
            if listening:
                return TEN_MINUTES, ten_minute_listening_message(now - self.started, left, self.deadline)
            return TEN_MINUTES, ten_minute_message(now - self.started, left, self.deadline)
        return None

    def mark(self, stage: int) -> None:
        self._sent.add(stage)
        if stage == FIVE_MINUTES:
            self._sent.add(TEN_MINUTES)
