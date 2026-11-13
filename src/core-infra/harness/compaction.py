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
    """How many frames fit in the 35% budget slice."""
    slot = int(char_budget * SPAN)
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


def verbatim_start_types(has_frames_block: bool):
    """Message types the verbatim turns may start at (§1.8 rule 4).

    The frames block is a human message placed first, so the turns after it
    may start at an AI message. Never at a tool result.
    """
    return ("human", "ai") if has_frames_block else "human"
