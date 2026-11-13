"""Repeated identical tool calls (state_long_cycles.md §1.5).

A repeat is the same tool, the same arguments and the same result as an
earlier call among the last 20 tool calls of the cycle. The same call with a
different result is not a repeat (polling is allowed).
"""

from __future__ import annotations

import hashlib
import json
from collections import deque

WINDOW = 20
NOTICE_AT = 3
STOP_AT = 6
LABEL_MAX_CHARS = 200


def _text(value) -> str:
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, sort_keys=True, default=str)
    except (TypeError, ValueError):
        return str(value)


def call_label(tool: str, args) -> str:
    """`{tool} {arguments}` for the message and the cycle summary."""
    if isinstance(args, dict) and set(args) == {"command"}:
        shown = str(args["command"])
    else:
        shown = _text(args if args is not None else "")
    label = f"{tool} {shown}".strip()
    if len(label) > LABEL_MAX_CHARS:
        label = label[:LABEL_MAX_CHARS - 1] + "…"
    return label


def notice_message(label: str, count: int = NOTICE_AT) -> str:
    return (
        f"You have made this call {count} times with the same result: `{label}`. It will not "
        "change. Do something different, or close the cycle with a handoff saying what is blocked."
    )


def stop_summary(label: str, count: int = STOP_AT) -> str:
    return f"[Harness] Stopped: the same call was repeated {count} times: {label}"


class RepeatGuard:
    def __init__(self, window: int = WINDOW, notice_at: int = NOTICE_AT, stop_at: int = STOP_AT):
        self.notice_at = notice_at
        self.stop_at = stop_at
        self._recent: deque = deque(maxlen=window)
        self._labels: dict[str, str] = {}
        self._noticed: set[str] = set()
        self._pending_notice: str | None = None
        self._stop: str | None = None

    def note(self, tool: str, args, result) -> int:
        """Record one finished tool call. Returns how many times it is in the window."""
        key = hashlib.sha256(
            "\x1f".join((str(tool), _text(args), _text(result))).encode("utf-8", "replace")
        ).hexdigest()
        self._recent.append(key)
        self._labels[key] = call_label(tool, args)
        count = sum(1 for k in self._recent if k == key)
        if count >= self.stop_at and self._stop is None:
            self._stop = key
        elif count >= self.notice_at and key not in self._noticed:
            self._pending_notice = key
        return count

    def notice_due(self) -> str | None:
        """The message to send, once per repeated call; None when nothing is due."""
        if self._pending_notice is None or self._stop is not None:
            return None
        return notice_message(self._labels[self._pending_notice], self.notice_at)

    def mark_noticed(self) -> None:
        if self._pending_notice is not None:
            self._noticed.add(self._pending_notice)
            self._pending_notice = None

    def stop_due(self) -> str | None:
        """The cycle summary when the cycle must be closed, else None."""
        if self._stop is None:
            return None
        return stop_summary(self._labels[self._stop], self.stop_at)
