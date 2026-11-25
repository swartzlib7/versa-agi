"""Preserve unread NEW MESSAGES when shrinking a conversation-context blob."""

NEW_MESSAGES_MARK = "--- NEW MESSAGES (UNREAD — MUST RESPOND) ---"
_TRIM_NOTICE = (
    "... (older conversation trimmed — use agictl message get for full context) ---\n"
)

# Lifeline / conversation-context default (chars). Not an agitop setting.
CONVERSATION_CONTEXT_MAX_CHARS = 40000


def trim_conversation_preserving_unread(
    text: str, max_chars: int = CONVERSATION_CONTEXT_MAX_CHARS
) -> str:
    """If over max_chars, shrink older prefix; never head-cut the unread block.

    When unread alone exceeds max_chars, keep the unread block in full.
    When there is no unread marker, keep the tail (most recent).
    """
    if not text or max_chars <= 0 or len(text) <= max_chars:
        return text

    idx = text.find(NEW_MESSAGES_MARK)
    if idx >= 0:
        suffix = text[idx:]
        if len(suffix) >= max_chars:
            return suffix
        budget = max_chars - len(suffix) - len(_TRIM_NOTICE)
        prefix = text[:idx]
        if budget < 200:
            return suffix
        if len(prefix) > budget:
            prefix = _TRIM_NOTICE + prefix[-budget:]
        return prefix + suffix

    keep = max_chars - len(_TRIM_NOTICE)
    if keep < 200:
        return text[-max_chars:]
    return _TRIM_NOTICE + text[-keep:]
