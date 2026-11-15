"""Prompt caching where a model has to be asked for it (state_model_drivers.md §2.7 B16).

Every shipped route but Claude's bills repeated input at the cached price
without being asked. Claude does it only for what a request marks, on
Anthropic's API and through OpenRouter alike. Three marks go out:

1. the end of the fixed part of the system prompt (the same from cycle to cycle),
2. the end of the system prompt (the same for the whole cycle),
3. the end of the conversation: one field at the top of the request, which the
   provider moves forward as the conversation grows (model_params.to_native_kwargs).

A mark is not part of the conversation: it does not disturb a reasoning
record (reasoning_replay.py). What restarts the cache is what changes the
bytes before a mark: a new system prompt, trim, a compaction frame, a viewed
image removed, a settled search pair.
"""

from __future__ import annotations

import hashlib
from typing import Any

from langchain_core.messages import SystemMessage

from harness import model_traits

MARK = model_traits.CACHE_MARK
# Where the per-cycle data starts in a system prompt (lifeline.sh, coa_poise.md).
LIVE_BOUNDARY = "## ── LIVE SITUATION"


def session_id(agent: str, thread_id: str) -> str:
    """An opaque id for one agent's thread, for a route that pins a conversation by id (§2.7 B18).

    A hash: it names nothing about the agent or its work to the provider.
    """
    return hashlib.sha256(f"versa-agi|{agent}|{thread_id}".encode("utf-8")).hexdigest()[:32]


def system_prompt(traits: model_traits.ModelTraits, text: str) -> str | SystemMessage:
    """The system prompt as the agent graph takes it: marked blocks, or the text as it is."""
    if traits.cache != model_traits.CACHE_MARKS or not (text or "").strip():
        return text
    at = text.find(LIVE_BOUNDARY)
    parts = [text[:at], text[at:]] if at > 0 else [text]
    return SystemMessage(content=[
        {"type": "text", "text": part, "cache_control": dict(MARK)} for part in parts if part
    ])


def _details(message: Any) -> dict[str, Any]:
    usage = getattr(message, "usage_metadata", None) or {}
    details = usage.get("input_token_details") if isinstance(usage, dict) else None
    return details if isinstance(details, dict) else {}


def _openai_style_write(message: Any) -> int:
    """`cache_write_tokens` of an OpenAI-style usage block (OpenRouter reports it for Claude)."""
    meta = getattr(message, "response_metadata", None) or {}
    usage = meta.get("token_usage") if isinstance(meta, dict) else None
    details = usage.get("prompt_tokens_details") if isinstance(usage, dict) else None
    value = details.get("cache_write_tokens") if isinstance(details, dict) else 0
    return int(value or 0)


def cached_tokens(message: Any) -> tuple[int, int]:
    """(read from the cache, written to it) input tokens one reply reports."""
    details = _details(message)
    read = int(details.get("cache_read") or 0)
    write = int(details.get("cache_creation") or 0) or _openai_style_write(message)
    return read, write


def cache_totals(messages: list) -> tuple[int, int]:
    """(read, written) over every reply in the list."""
    read = write = 0
    for message in messages or []:
        r, w = cached_tokens(message)
        read += r
        write += w
    return read, write


def cache_log_line(prompt_tokens: int, read: int, write: int, pings: int = 0) -> str:
    """One cycle-log line ("" when no reply reported cached input)."""
    if not read and not write:
        return ""
    share = f" ({round(100 * read / prompt_tokens)}%)" if prompt_tokens > 0 else ""
    kept = f"; {pings} keep-alive ping{'s' if pings != 1 else ''}" if pings else ""
    return (f"CACHE: {read:,} of {prompt_tokens:,} input tokens read from the cache{share}; "
            f"{write:,} written{kept}")
