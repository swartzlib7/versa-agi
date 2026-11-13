"""Heartbeat file for Lifeline's stall timer (state_long_cycles.md §1.2).

The harness touches the file when a model call starts, when a reply arrives,
and every 30 seconds while a tool runs, while it waits for mail, and while a
live call is in progress. Nothing touches it while one model call is in
flight, so the file's age is how long that call has been running.
"""

from __future__ import annotations

import os
import threading
import time
from contextlib import contextmanager
from typing import Callable

ENV_FILE = "VERSA_HEARTBEAT_FILE"
INTERVAL_SECONDS = 30.0


class Heartbeat:
    def __init__(self, path: str | None, interval: float = INTERVAL_SECONDS,
                 busy: Callable[[], bool] | None = None):
        self.path = path or ""
        self.interval = interval
        self._busy = busy
        self._holds: set[str] = set()
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @classmethod
    def from_env(cls, env=None, **kw) -> "Heartbeat":
        return cls((env if env is not None else os.environ).get(ENV_FILE), **kw)

    def set_busy(self, busy: Callable[[], bool] | None) -> None:
        """A check that counts as a hold while it is true (a live call in progress)."""
        self._busy = busy

    def beat(self) -> None:
        """Touch the file. Best effort: a failed touch never stops the cycle."""
        if not self.path:
            return
        try:
            with open(self.path, "a", encoding="utf-8"):
                pass
            os.utime(self.path, None)
        except OSError:
            pass

    def set_hold(self, reason: str, on: bool) -> None:
        """While any hold is on, the file is touched every ``interval`` seconds."""
        with self._lock:
            had = bool(self._holds)
            if on:
                self._holds.add(reason)
            else:
                self._holds.discard(reason)
            starting = on and not had
        if starting:
            self.beat()

    @contextmanager
    def holding(self, reason: str):
        self.set_hold(reason, True)
        try:
            yield
        finally:
            self.set_hold(reason, False)

    def held(self) -> bool:
        with self._lock:
            if self._holds:
                return True
        if self._busy is not None:
            try:
                return bool(self._busy())
            except Exception:
                return False
        return False

    def tick(self) -> bool:
        """One pass of the background loop. True when it touched the file."""
        if self.held():
            self.beat()
            return True
        return False

    def start(self) -> None:
        if not self.path or self._thread is not None:
            return
        self.beat()

        def _run():
            while not self._stop.wait(self.interval):
                self.tick()

        self._thread = threading.Thread(target=_run, name="heartbeat", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()


def age_seconds(path: str, since: float, now: float | None = None) -> float:
    """Seconds since the last touch, never counted from before ``since``."""
    now = time.time() if now is None else now
    try:
        touched = os.path.getmtime(path)
    except OSError:
        touched = 0.0
    return max(0.0, now - max(touched, since))
