"""What of an earlier reply goes back to the model (state_model_drivers.md §2.7 B3, B11, B12).

A model's reply can carry a record of its reasoning: Claude ``thinking``
blocks, OpenRouter ``reasoning_details``. The record is sent back with the
conversation so a tool-calling turn continues the same reasoning.

Two things make a record unusable:

- **Another model wrote it.** No model reads another model's record.
- **The history before it changed** (``bound`` models only). Claude 5-line
  models reject a record whose earlier conversation is not the one it was
  written in: a new system prompt, a trimmed or summarized start, an edited
  earlier message.

``ReplayGuard.apply`` returns the messages to send with the unusable records
removed. The provider allows removing every record, or the oldest ones in
order, so the guard removes all records up to the point of the change and
keeps the ones written after it. The stored thread is never changed.

A reply also holds parts only the API that wrote it understands: Claude's
server-side search call and its result, the items of OpenAI's Responses
endpoint. A thread can move to another provider between cycles, so those
parts are left out when the request goes to a different API (``wire``). The
reply's text and its tool calls always go.

On Anthropic's own API a provider search can still be running when the model
also calls one of our tools: the search call is in one reply and its result
in the next. Anthropic continues such a reply, but rejects the pair once the
reply is complete and a later request sends it again. ``settle_server_tools``
takes the split pairs out of completed replies and leaves a note with the
sources in their place.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Callable

BOUND = "bound"
SIGNATURE = "signature"

REASONING_BLOCK_TYPES = frozenset({"thinking", "redacted_thinking", "reasoning"})
REASONING_KWARGS = ("reasoning_details", "reasoning_content", "reasoning")
EMPTY_REPLY = "(no text)"
# Anthropic's answer when a record is replayed after its history changed.
BINDING_ERROR_MARK = "bound to a different conversation"

# wire: the request format of the model being called.
WIRE_ANTHROPIC = "anthropic"
WIRE_RESPONSES = "responses"   # OpenAI Responses endpoint
WIRE_GOOGLE = "google"
WIRE_CHAT = "chat"             # Chat Completions: OpenRouter, xAI, OpenAI, local

# Parts of a stored reply that only one wire reads. Tool calls are not lost
# with them: every client reads those from the message's ``tool_calls``.
_ANTHROPIC_PARTS = frozenset({
    "thinking", "redacted_thinking", "tool_use", "server_tool_use", "mcp_tool_use",
    "container_upload",
})
_RESPONSES_PARTS = frozenset({
    "reasoning", "function_call", "refusal", "mcp_list_tools", "mcp_approval_request",
})
# A provider search leaves its call and result in the reply. When they are
# left out, this line stands in for them, so the next model does not take the
# answer for one that was never looked up.
SEARCH_NOTE = (
    "[A web search by the model's provider was used for this reply. "
    "The pages it read are not repeated here."
)
SEARCH_NOTE_SOURCES = 6


def wire_for(family: str, api: str = "") -> str:
    """The wire of a model from its provider family and its endpoint trait."""
    if family == "anthropic":
        return WIRE_ANTHROPIC
    if family == "google":
        return WIRE_GOOGLE
    if api == "responses":
        return WIRE_RESPONSES
    return WIRE_CHAT


def _wire_of_part(part: Any) -> str:
    """The one wire that reads this content part ("" when every wire does)."""
    kind = str(part.get("type") or "") if isinstance(part, dict) else ""
    # Claude server tools answer in "<tool>_tool_result" parts; the Responses
    # endpoint names its server-side steps "<tool>_call".
    if kind in _ANTHROPIC_PARTS or kind.endswith("_tool_result"):
        return WIRE_ANTHROPIC
    if kind in _RESPONSES_PARTS or kind.endswith("_call"):
        return WIRE_RESPONSES
    return ""


def _is_search_part(part: Any) -> bool:
    kind = str(part.get("type") or "") if isinstance(part, dict) else ""
    return kind in ("web_search_tool_result", "web_search_call")


def _search_note(message: Any) -> dict[str, str]:
    from .model_reply import search_sources

    found = [
        f"{title} ({url})" if title else url
        for title, url in search_sources(message)[:SEARCH_NOTE_SOURCES]
    ]
    tail = f" Pages it found: {'; '.join(found)}]" if found else "]"
    return {"type": "text", "text": SEARCH_NOTE + tail}


def _server_call_id(part: Any) -> str:
    if isinstance(part, dict) and part.get("type") == "server_tool_use":
        return str(part.get("id") or "")
    return ""


def _server_result_id(part: Any) -> str:
    kind = str(part.get("type") or "") if isinstance(part, dict) else ""
    if kind.endswith("_tool_result") and kind != "tool_result":
        return str(part.get("tool_use_id") or "")
    return ""


def _split_server_parts(message: Any) -> tuple[bool, bool]:
    """(a call without its result, a result without its call) inside one AI message."""
    content = getattr(message, "content", None)
    if not _is_ai(message) or not isinstance(content, list):
        return False, False
    calls = {_server_call_id(p) for p in content} - {""}
    results = {_server_result_id(p) for p in content} - {""}
    return bool(calls - results), bool(results - calls)


def _without_server_parts(message: Any, keep_waiting: bool = False) -> Any:
    """The reply as text and tool calls: no server tool parts, citations or reasoning.

    ``keep_waiting``: a server tool call that has no result yet stays, so the
    provider can finish it.
    """
    content = message.content
    answered = {_server_result_id(p) for p in content} - {""}
    kept: list[Any] = []
    searched = False
    for part in content:
        call_id = _server_call_id(part)
        if call_id and keep_waiting and call_id not in answered:
            kept.append(part)
            continue
        if call_id or _server_result_id(part):
            searched = searched or _is_search_part(part)
            continue
        if _is_reasoning_block(part):
            continue
        if isinstance(part, dict) and part.get("type") == "text":
            part = {"type": "text", "text": str(part.get("text") or "")}
        kept.append(part)
    if searched:
        kept.insert(0, _search_note(message))
    if not kept and not (getattr(message, "tool_calls", None) or []):
        kept = EMPTY_REPLY
    try:
        return message.model_copy(update={"content": kept})
    except AttributeError:
        return message


def settle_server_tools(messages: list) -> list:
    """Replies without the server tool pairs that are split across replies.

    Anthropic takes a server tool call without its result only in the newest
    reply, while that reply waits for one of our tools. Everywhere else a
    split pair is removed. In the newest reply the waiting call stays and a
    result that answers an earlier reply's call goes.
    """
    split = [_split_server_parts(m) for m in messages]
    if not any(call or result for call, result in split):
        return messages
    last_ai = max((i for i, m in enumerate(messages) if _is_ai(m)), default=-1)
    waiting = (
        last_ai >= 0
        and split[last_ai][0]
        and all(getattr(m, "type", "") == "tool" for m in messages[last_ai + 1:])
    )
    out = []
    for i, message in enumerate(messages):
        call_open, result_orphan = split[i]
        if i == last_ai and waiting:
            out.append(_without_server_parts(message, keep_waiting=True) if result_orphan else message)
        elif call_open or result_orphan:
            out.append(_without_server_parts(message))
        else:
            out.append(message)
    return out


def portable(message: Any, wire: str) -> Any:
    """A copy of an AI message without the parts another API wrote for itself."""
    content = getattr(message, "content", None)
    if not wire or not _is_ai(message) or not isinstance(content, list):
        return message
    kept = [part for part in content if _wire_of_part(part) in ("", wire)]
    if len(kept) == len(content):
        return message
    # Another API wrote this reply: its text goes without that API's own fields.
    kept = [
        {"type": "text", "text": str(part.get("text") or "")}
        if isinstance(part, dict) and part.get("type") == "text" else part
        for part in kept
    ]
    if any(_is_search_part(part) for part in content if _wire_of_part(part) not in ("", wire)):
        kept.append(_search_note(message))
    if not kept and not (getattr(message, "tool_calls", None) or []):
        kept = EMPTY_REPLY
    try:
        return message.model_copy(update={"content": kept})
    except AttributeError:
        return message


def _is_ai(message: Any) -> bool:
    return getattr(message, "type", "") == "ai"


def _is_reasoning_block(part: Any) -> bool:
    return isinstance(part, dict) and part.get("type") in REASONING_BLOCK_TYPES


def has_reasoning(message: Any) -> bool:
    """True when the message carries a reasoning record."""
    content = getattr(message, "content", None)
    if isinstance(content, list) and any(_is_reasoning_block(p) for p in content):
        return True
    extra = getattr(message, "additional_kwargs", None) or {}
    return any(extra.get(key) for key in REASONING_KWARGS)


def strip_reasoning(message: Any) -> Any:
    """A copy of the message without its reasoning record (the original is untouched)."""
    if not has_reasoning(message):
        return message
    content = getattr(message, "content", None)
    if isinstance(content, list):
        content = [p for p in content if not _is_reasoning_block(p)]
        if not content and not (getattr(message, "tool_calls", None) or []):
            content = EMPTY_REPLY
    extra = {
        key: value
        for key, value in (getattr(message, "additional_kwargs", None) or {}).items()
        if key not in REASONING_KWARGS
    }
    try:
        return message.model_copy(update={"content": content, "additional_kwargs": extra})
    except AttributeError:
        return message


def _plain(value: Any) -> Any:
    """Content without reasoning blocks, in a form that serializes the same every time."""
    if isinstance(value, list):
        return [_plain(v) for v in value if not _is_reasoning_block(v)]
    if isinstance(value, dict):
        return {str(k): _plain(v) for k, v in sorted(value.items(), key=lambda kv: str(kv[0]))}
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def fingerprint(message: Any) -> str:
    """What the provider sees of a message apart from its reasoning record."""
    calls = [
        [call.get("id"), call.get("name"), _plain(call.get("args"))]
        for call in (getattr(message, "tool_calls", None) or [])
        if isinstance(call, dict)
    ]
    body = [
        getattr(message, "type", ""),
        _plain(getattr(message, "content", "")),
        calls,
        getattr(message, "tool_call_id", None),
    ]
    raw = json.dumps(body, sort_keys=True, default=str, ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def produced_by(message: Any) -> str:
    """The model id the provider reported for this reply ("" when it reported none)."""
    meta = getattr(message, "response_metadata", None) or {}
    return str(meta.get("model_name") or meta.get("model") or "").strip()


def _model_name(model_id: str) -> str:
    """Provider-reported and catalog ids compared without separators or a vendor prefix."""
    name = (model_id or "").strip().lower().rsplit("/", 1)[-1]
    return name.replace(".", "-")


def same_model(reported: str, api_model: str) -> bool:
    """True when a reply's reported model is the model now being called."""
    left, right = _model_name(reported), _model_name(api_model)
    if not left or not right:
        return False
    return left == right or left.startswith(right) or right.startswith(left)


def is_binding_error(error: BaseException) -> bool:
    """The provider refused a replayed record because the history before it changed."""
    return BINDING_ERROR_MARK in str(error)


class ReplayGuard:
    """Removes reasoning records the model being called cannot accept."""

    def __init__(
        self,
        mode: str,
        api_model: str,
        log: Callable[[str], None] | None = None,
        wire: str = "",
    ):
        self.mode = mode or ""
        self.wire = wire or ""
        self.api_model = api_model or ""
        self._log = log or (lambda _msg: None)
        self._stripped: set[str] = set()
        self._sent: list[str] | None = None  # fingerprints of the last request
        self._strip_all_next = False

    @property
    def active(self) -> bool:
        return bool(self.mode)

    def strip_all_next(self) -> None:
        """Remove every record on the next request (after a binding error)."""
        self._strip_all_next = True

    @staticmethod
    def _key(message: Any, prints: str) -> str:
        return str(getattr(message, "id", None) or prints)

    def apply(self, messages: list) -> list:
        if self.wire == WIRE_ANTHROPIC:
            messages = settle_server_tools(messages)
        if not self.active:
            if not self.wire:
                return messages
            return [
                portable(
                    strip_reasoning(message)
                    if _is_ai(message) and has_reasoning(message)
                    and not same_model(produced_by(message), self.api_model)
                    else message,
                    self.wire,
                )
                for message in messages
            ]
        prints = [fingerprint(m) for m in messages]
        keys = [self._key(m, p) for m, p in zip(messages, prints)]

        for message, key in zip(messages, keys):
            if _is_ai(message) and has_reasoning(message) and not same_model(
                produced_by(message), self.api_model,
            ):
                self._stripped.add(key)

        if self.mode == BOUND:
            first = self._sent is None
            appended = (
                not first
                and len(self._sent) <= len(prints)
                and prints[: len(self._sent)] == self._sent
            )
            if first or self._strip_all_next or not appended:
                held = [
                    key for message, key in zip(messages, keys)
                    if _is_ai(message) and has_reasoning(message) and key not in self._stripped
                ]
                if held:
                    why = (
                        "new cycle" if first
                        else "provider refused a replayed record" if self._strip_all_next
                        else "earlier history changed"
                    )
                    self._log(f"REASONING REPLAY: {len(held)} record(s) left out — {why}")
                self._stripped.update(held)
            self._sent = prints
            self._strip_all_next = False

        return [
            portable(strip_reasoning(message) if key in self._stripped else message, self.wire)
            for message, key in zip(messages, keys)
        ]
