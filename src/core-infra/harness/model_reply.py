"""What a model reply says beyond its text: a refusal, the sources of a web search."""

from __future__ import annotations

from typing import Any

# Stop reasons that mean the provider declined to answer.
_REFUSAL_REASONS = frozenset({"refusal", "content_filter"})


def _metadata(message: Any) -> dict[str, Any]:
    return getattr(message, "response_metadata", None) or {}


def _has_text(message: Any) -> bool:
    content = getattr(message, "content", "")
    if isinstance(content, str):
        return bool(content.strip())
    for part in content or []:
        if isinstance(part, str) and part.strip():
            return True
        if isinstance(part, dict) and part.get("type") == "text" and str(part.get("text") or "").strip():
            return True
    return False


def _openai_refusal(message: Any) -> bool:
    """A ``refusal`` part (Responses endpoint) or field (Chat Completions)."""
    extra = getattr(message, "additional_kwargs", None) or {}
    if extra.get("refusal"):
        return True
    content = getattr(message, "content", None)
    return isinstance(content, list) and any(
        isinstance(part, dict) and part.get("type") == "refusal" for part in content
    )


def refusal_reason(message: Any) -> str | None:
    """Why the provider declined, or None when the reply is an ordinary one.

    Anthropic answers HTTP 200 with ``stop_reason: "refusal"``; OpenAI-style
    routes report ``finish_reason: "content_filter"``; OpenAI's own API puts a
    ``refusal`` part in the reply. A reply that still carries a tool call or
    text is not treated as a refusal.
    """
    meta = _metadata(message)
    reason = str(meta.get("stop_reason") or meta.get("finish_reason") or "").strip().lower()
    if reason not in _REFUSAL_REASONS:
        # OpenAI states a refusal as its own part of the reply, with no stop reason.
        if _openai_refusal(message) and not getattr(message, "tool_calls", None) \
                and not _has_text(message):
            return "refusal"
        return None
    if getattr(message, "tool_calls", None):
        return None
    if reason != "refusal" and _has_text(message):
        return None
    details = meta.get("stop_details")
    category = ""
    if isinstance(details, dict):
        category = str(details.get("category") or "").strip()
    return category or reason


# A second refusal in a row closes the cycle: the model declined the note too.
REFUSALS_BEFORE_CLOSE = 2
# Cycle-log line Lifeline looks for to tell the Primary User (provider_alerts).
REFUSAL_CLOSED_MARK = "REFUSAL CLOSED:"


def refusal_note(reason: str) -> str:
    """Told to the agent after its model declined a reply; the reply itself is discarded."""
    return (
        f"[Harness] The model provider declined your last reply (reason: {reason}). That reply "
        "was discarded. Do not make the declined request again in other words. If the rest of "
        "the work can go on without it, carry on. If it cannot, tell the person who asked that "
        "the model declined the request and why, note it on the task, and end the cycle. If the "
        "model declines again, the harness closes this cycle and the Primary User is told."
    )


def refusal_summary(reason: str, model: str) -> str:
    """Cycle summary written when the harness closes a cycle the model declined twice."""
    return (
        f"[Harness] {model} declined this request twice (reason: {reason}), the second time "
        "after being told. The cycle was closed with nothing further done and the Primary User "
        "is told. Review the request, or assign another model, before it is tried again."
    )


def refusal_closed_line(reason: str, model: str) -> str:
    """The cycle-log line for a cycle closed on a repeated refusal."""
    return f"{REFUSAL_CLOSED_MARK} {model} declined twice (reason: {reason})"


# Added to the system prompt when the model's own web search sits beside our tools.
PROVIDER_SEARCH_HINT = (
    "\n\nWEB SEARCH: your model can search the web itself (the web_search tool). Run a search "
    "in a step of its own, not together with other tool calls, and state what you found in "
    "your reply before you go on."
)


def search_sources(message: Any) -> list[tuple[str, str]]:
    """(title, url) of the pages a provider web search cited in this reply."""
    out: list[tuple[str, str]] = []
    seen: set[str] = set()

    def add(title: Any, url: Any) -> None:
        link = str(url or "").strip()
        if link and link not in seen:
            seen.add(link)
            out.append((str(title or "").strip(), link))

    extra = getattr(message, "additional_kwargs", None) or {}
    for note in extra.get("annotations") or []:  # OpenRouter
        if isinstance(note, dict) and note.get("type") == "url_citation":
            cite = note.get("url_citation") if isinstance(note.get("url_citation"), dict) else note
            add(cite.get("title"), cite.get("url"))

    content = getattr(message, "content", None)
    if isinstance(content, list):
        for part in content:
            if not isinstance(part, dict):
                continue
            if part.get("type") == "text":  # OpenAI Responses: citations on the text
                for note in part.get("annotations") or []:
                    if isinstance(note, dict) and note.get("type") == "url_citation":
                        add(note.get("title"), note.get("url"))
                continue
            if part.get("type") != "web_search_tool_result":  # Anthropic
                continue
            results = part.get("content")
            for item in results if isinstance(results, list) else []:
                if isinstance(item, dict):
                    add(item.get("title"), item.get("url"))
    return out


def search_log_line(message: Any, limit: int = 5) -> str:
    """One cycle-log line naming the sources of a provider web search ("" when none)."""
    sources = search_sources(message)
    if not sources:
        return ""
    shown = ", ".join(url for _title, url in sources[:limit])
    more = f" (+{len(sources) - limit} more)" if len(sources) > limit else ""
    return f"WEB SEARCH: {len(sources)} source(s) — {shown}{more}"
