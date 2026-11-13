"""What the harness writes when it closes a cycle itself (state_long_cycles.md §1.3, §1.5, §1.8)."""

from __future__ import annotations

EXIT_STEP_BUDGET = 53
EXIT_TIME_LIMIT = 54
EXIT_LOOP = 55

HANDOFF_REPLY_CHARS = 600


def time_limit_summary(minutes: int, steps: int) -> str:
    return (
        f"[Harness] Time limit reached ({minutes} min, {steps} steps). "
        "Work continues on the next spawn."
    )


def message_text(content) -> str:
    """Plain text of a message's content (string, or a list of content blocks)."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and block.get("type") in (None, "text"):
                parts.append(str(block.get("text") or ""))
        return "\n".join(p for p in parts if p)
    return "" if content is None else str(content)


def harness_handoff(last_reply: str, last_tool: str) -> str:
    """Handoff written when the agent saved none: its last plain-text reply
    (first 600 characters) and its last tool call."""
    reply = " ".join((last_reply or "").split())
    if len(reply) > HANDOFF_REPLY_CHARS:
        reply = reply[:HANDOFF_REPLY_CHARS - 1] + "…"
    parts = ["[Harness] The cycle was closed for you, with no handoff from you"]
    parts.append(f"Your last reply: {reply}" if reply else "You had written no reply")
    parts.append(f"Your last tool call: {last_tool}" if last_tool else "No tool call was made")
    return " | ".join(parts)


def last_plain_reply(messages) -> str:
    """Text of the newest AI message that has any."""
    for m in reversed(list(messages or [])):
        if getattr(m, "type", "") == "ai":
            text = message_text(getattr(m, "content", "")).strip()
            if text and not text.startswith("FATAL EXCEPTION"):
                return text
    return ""
