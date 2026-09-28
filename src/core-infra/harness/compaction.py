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
    found = set()
    for row in rows:
        raw = str(row.get("message_ids") or "")
        found.update(part for part in raw.split(",") if part)
    return found


def split_verbatim(messages: list, covered: set[str]) -> list:
    return [m for m in messages if str(getattr(m, "id", "") or "") not in covered]
