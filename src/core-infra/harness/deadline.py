"""Time warnings and the harness close at the time limit (state_long_cycles.md §1.3, §1.7).

Lifeline passes ``--deadline-epoch``: the agent's time limit. The harness
closes the cycle there; Lifeline's ``timeout`` is a backstop 5 minutes later.
The agent is told nothing at the start (no pressure). It gets two messages,
each once: wrap up, then close now. Both come earlier for an agent whose
steps are slow.
"""

from __future__ import annotations

import time
from typing import Callable

WRAP_UP = "wrap_up"
CLOSE_NOW = "close_now"

WRAP_UP_SECONDS = 900      # 15 minutes left
CLOSE_NOW_SECONDS = 480    # 8 minutes left
WRAP_UP_STEPS = 6
CLOSE_NOW_STEPS = 3
# No model call is started with less than this much time left (§1.3 rule 1).
NO_NEW_STEP_FACTOR = 1.5
NO_NEW_STEP_MIN_SECONDS = 60


def _minutes(seconds: float) -> int:
    return max(1, int(round(seconds / 60.0)))


def _clock_time(epoch: float) -> str:
    return time.strftime("%H:%M", time.localtime(epoch))


def wrap_up_message(elapsed_s: float, left_s: float, deadline_epoch: float) -> str:
    return (
        f"⏱ TIME CHECK — You have been running for {_minutes(elapsed_s)} minutes. This session "
        f"ends in about {_minutes(left_s)} minutes and is stopped at {_clock_time(deadline_epoch)}. "
        "Start wrapping up: finish what you are doing, tell anyone you are talking with where "
        "things stand, then close with `agictl cycle end \"summary\" --handoff \"Open: … | Next: …\"`. "
        "Unfinished work and conversations continue in your next spawn."
    )


def wrap_up_listening_message(elapsed_s: float, left_s: float, deadline_epoch: float,
                              close_lead_s: float = CLOSE_NOW_SECONDS) -> str:
    """Wrap-up warning while a live chat is open: keep chatting; the window closes at the last warning."""
    return (
        f"⏱ TIME CHECK — You have been running for {_minutes(elapsed_s)} minutes. This live chat "
        f"stays open for about {max(1, _minutes(left_s - close_lead_s))} more minutes; the session "
        f"is stopped at {_clock_time(deadline_epoch)}. Keep chatting, and finish anything you can in "
        "that time. If it matters, let the person know you will pick things up again shortly. "
        "Unfinished work and conversations continue in your next spawn."
    )


def close_now_message(left_s: float, deadline_epoch: float) -> str:
    return (
        f"⏱ TIME CHECK — About {_minutes(left_s)} minutes left before this session is stopped at "
        f"{_clock_time(deadline_epoch)}. Close now: send any last replies, then "
        "`agictl cycle end` with a handoff. Anything unfinished continues in your next spawn. "
        "If the cycle is still open at the limit, the harness closes it for you and keeps your thread."
    )


class DeadlineWarnings:
    """Which time warning is due, and when the harness must close the cycle.

    ``typical_step``: seconds one step takes for this agent (``StepClock.typical``).
    ``mark`` only after the message reached the model.
    """

    def __init__(self, deadline_epoch: float | None, started_epoch: float | None = None,
                 clock: Callable[[], float] = time.time,
                 typical_step: Callable[[], float] | None = None):
        self.deadline = float(deadline_epoch) if deadline_epoch else None
        self._clock = clock
        self.started = started_epoch if started_epoch is not None else clock()
        self._typical = typical_step or (lambda: 0.0)
        self._sent: set[str] = set()

    def _step(self) -> float:
        try:
            return max(0.0, float(self._typical()))
        except (TypeError, ValueError):
            return 0.0

    def wrap_up_lead(self) -> float:
        return max(float(WRAP_UP_SECONDS), WRAP_UP_STEPS * self._step())

    def close_now_lead(self) -> float:
        return max(float(CLOSE_NOW_SECONDS), CLOSE_NOW_STEPS * self._step())

    def no_new_step_lead(self) -> float:
        return max(float(NO_NEW_STEP_MIN_SECONDS), NO_NEW_STEP_FACTOR * self._step())

    def time_left(self) -> float | None:
        return None if self.deadline is None else self.deadline - self._clock()

    def _first_half(self, now: float) -> bool:
        """Nothing is sent, and nothing is closed, before half the limit has passed."""
        return (now - self.started) < (self.deadline - self.started) / 2.0

    def due(self, listening: bool = False) -> tuple[str, str] | None:
        """(stage, message) for the most urgent unsent warning, else None.

        ``listening``: a live chat window is open (state_live_chat.md §1.5.3);
        the wrap-up message then says to keep chatting. The caller replaces
        the close-now message with the window's closing message.
        """
        if self.deadline is None:
            return None
        now = self._clock()
        if self._first_half(now):
            return None
        left = self.deadline - now
        if left <= self.close_now_lead() and CLOSE_NOW not in self._sent:
            return CLOSE_NOW, close_now_message(left, self.deadline)
        if left <= self.wrap_up_lead() and WRAP_UP not in self._sent and CLOSE_NOW not in self._sent:
            if listening:
                return WRAP_UP, wrap_up_listening_message(
                    now - self.started, left, self.deadline, self.close_now_lead())
            return WRAP_UP, wrap_up_message(now - self.started, left, self.deadline)
        return None

    def mark(self, stage: str) -> None:
        self._sent.add(stage)
        if stage == CLOSE_NOW:
            self._sent.add(WRAP_UP)

    def close_due(self) -> bool:
        """True when no further model call may start (§1.3 rules 1–2): the
        limit has passed, or the time left is less than 1.5 typical steps."""
        if self.deadline is None:
            return False
        now = self._clock()
        left = self.deadline - now
        if left <= 0:
            return True
        if self._first_half(now):
            return False
        return left < self.no_new_step_lead()
