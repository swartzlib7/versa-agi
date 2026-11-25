"""Compaction frames (TD-CTX-FRAME-001). Pure helpers; the harness does the I/O.

Contract: versa-agi/design/spec/state/state_spawn_context.md §1.8.
"""

from __future__ import annotations

HARNESS_NOTE = (
    "Earlier turns in this thread were compacted into the frames below, oldest first. "
    "Each frame has a start and an end. If you need something from before the first frame, "
    "use agictl: `cycle frames search`, and the task, message, and memory commands. Do not invent it."
)

TRIGGER = 0.90
SPAN = 0.35
# The active frames in the payload take at most this many characters (§1.8 rule 9).
FRAMES_BLOCK_MAX_CHARS = 40_000


def message_text(msg) -> str:
    content = getattr(msg, "content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for part in content:
            if isinstance(part, str):
                parts.append(part)
            elif isinstance(part, dict) and part.get("type") == "text":
                parts.append(str(part.get("text") or ""))
        return "\n".join(parts)
    return str(content or "")


def is_tool_result(msg) -> bool:
    return getattr(msg, "type", "") == "tool" or type(msg).__name__ == "ToolMessage"


def has_tool_calls(msg) -> bool:
    return bool(getattr(msg, "tool_calls", None))


def select_oldest_span(messages: list, fraction: float = SPAN) -> tuple[list, list]:
    """Oldest fraction of messages, extended so a tool call and its results stay together."""
    if not messages:
        return [], []
    cut = max(1, int(len(messages) * fraction))
    if cut >= len(messages):
        cut = len(messages) - 1 if len(messages) > 1 else 1
    while cut < len(messages) and is_tool_result(messages[cut]):
        cut += 1
    if cut < len(messages) and has_tool_calls(messages[cut - 1]):
        while cut < len(messages) and is_tool_result(messages[cut]):
            cut += 1
    if cut >= len(messages) and len(messages) > 1:
        cut = len(messages) - 1
    return list(messages[:cut]), list(messages[cut:])


def frame_slots(char_budget: int, frame_chars: int) -> int:
    """How many frames fit in the frames block: 35% of the budget, at most
    FRAMES_BLOCK_MAX_CHARS (§1.8 rule 9)."""
    slot = min(int(char_budget * SPAN), FRAMES_BLOCK_MAX_CHARS)
    if frame_chars <= 0:
        return 1
    return max(1, slot // frame_chars)


def framed_ids(rows: list[dict]) -> set[str]:
    """Message ids covered by these frames. Pass every frame of the thread,
    active and rolled: a message is framed once (state_spawn_context.md §1.8)."""
    found = set()
    for row in rows:
        raw = str(row.get("message_ids") or "")
        found.update(part for part in raw.split(",") if part and part != "-")
    return found


def active_frames(rows: list[dict]) -> list[dict]:
    """Frames that go in the payload block. Rolled frames stay covered but are not sent."""
    return [row for row in rows if (row.get("status") or "active") == "active"]


def split_verbatim(messages: list, covered: set[str]) -> list:
    return [m for m in messages if str(getattr(m, "id", "") or "") not in covered]


def note_seen(seen: dict, messages: list, cycle_started_at: str, now: str) -> None:
    """Record when each message id was first seen by the harness.

    On the first call of a cycle every message is stamped with the cycle start:
    the resumed tail is older than that, and the wake arrives with it.
    """
    stamp = now if seen else cycle_started_at
    for m in messages:
        mid = str(getattr(m, "id", "") or "")
        if mid and mid not in seen:
            seen[mid] = stamp


def frame_times(span: list, seen: dict, last_frame_end: str | None,
                cycle_started_at: str, now: str) -> tuple[str, str]:
    """(frame_start, frame_end) for a span (§1.8 rule 3).

    Start is where the thread's newest frame ended. End is when the last
    message of the span was first seen, never earlier than the start.
    """
    start = last_frame_end or cycle_started_at
    last_id = str(getattr(span[-1], "id", "") or "") if span else ""
    end = seen.get(last_id) or now
    if end < start:
        end = start
    return start, end


def utc_stamp(value) -> str:
    """A time as frames store it (`2026-11-19T09:34:40Z`). "" when it cannot be read.

    Takes a datetime or an ISO string, with or without an offset (none means UTC).
    """
    from datetime import datetime, timezone

    if not value:
        return ""
    try:
        when = value if isinstance(value, datetime) else datetime.fromisoformat(
            str(value).strip().replace("Z", "+00:00")
        )
    except (TypeError, ValueError):
        return ""
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return when.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def resume_span(unframed: list, tail: list) -> list:
    """The messages a resume cut removes from the front (§1.8 rule 8).

    `tail` is what the cut keeps. Everything before the tail's first message
    that was already in `unframed` is the span to summarize; a placeholder the
    repair added has a new id and is skipped.
    """
    index = {str(getattr(m, "id", "") or ""): i for i, m in enumerate(unframed)}
    for kept in tail:
        at = index.get(str(getattr(kept, "id", "") or ""))
        if at is not None and str(getattr(kept, "id", "") or ""):
            return list(unframed[:at])
    return []


def resume_frame_times(last_frame_end: str | None, saved_at: str, now: str) -> tuple[str, str]:
    """(frame_start, frame_end) of a frame written at resume (§1.8 rule 8).

    The span is older than this cycle, so it ends when the thread was last
    saved. It starts where the newest frame ended, or at that same time.
    """
    end = saved_at or now
    start = last_frame_end or end
    if end < start:
        end = start
    return start, end


def verbatim_start_types(has_frames_block: bool):
    """Message types the verbatim turns may start at (§1.8 rule 4).

    The frames block is a human message placed first, so the turns after it
    may start at an AI message. Never at a tool result.
    """
    return ("human", "ai") if has_frames_block else "human"
