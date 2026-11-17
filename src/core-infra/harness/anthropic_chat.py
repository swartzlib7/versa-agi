"""The Anthropic client, sending a stored reply back the way Anthropic wrote it.

State: design/spec/state/state_model_drivers.md §2.7 B19.

Claude's search tool can run searches from inside a code step. The reply then
holds a ``code_execution`` call, the ``web_search`` calls and results made from
it, and the code step's result. Each nested part carries ``caller``: which code
step made it. The installed client leaves ``caller`` out of a
``server_tool_use`` and a ``web_search_tool_result`` when it sends the reply
back, and Anthropic then answers 400: the code step "was found without a
corresponding ``code_execution_tool_result``". ``HarnessChatAnthropic`` puts
``caller`` back from the stored reply.
"""

from __future__ import annotations

from typing import Any

from langchain_anthropic import ChatAnthropic

CALLER = "caller"
DIRECT = "direct"  # a call the model made itself: the client's own form is accepted


def _nested_part_id(part: Any) -> str:
    """The server tool call a part belongs to ("" for every other part)."""
    if not isinstance(part, dict):
        return ""
    kind = str(part.get("type") or "")
    if kind == "server_tool_use":
        return str(part.get("id") or "")
    if kind.endswith("_tool_result") and kind != "tool_result":
        return str(part.get("tool_use_id") or "")
    return ""


def nested_callers(messages: list) -> dict[str, dict]:
    """Server tool call id -> its ``caller``, for calls made from inside another server tool."""
    found: dict[str, dict] = {}
    for message in messages:
        content = getattr(message, "content", None)
        if getattr(message, "type", "") != "ai" or not isinstance(content, list):
            continue
        for part in content:
            call_id = _nested_part_id(part)
            caller = part.get(CALLER) if call_id else None
            if isinstance(caller, dict) and caller.get("type") not in (None, "", DIRECT):
                found[call_id] = caller
    return found


def restore_callers(wire_messages: list, callers: dict[str, dict]) -> int:
    """Put ``caller`` back on the nested parts of the request's replies. Returns how many."""
    restored = 0
    for wire in wire_messages:
        content = wire.get("content") if isinstance(wire, dict) else None
        if not isinstance(content, list) or wire.get("role") != "assistant":
            continue
        for part in content:
            call_id = _nested_part_id(part)
            if call_id in callers and CALLER not in part:
                part[CALLER] = dict(callers[call_id])
                restored += 1
    return restored


class HarnessChatAnthropic(ChatAnthropic):
    """ChatAnthropic whose requests keep ``caller`` on nested server tool parts."""

    def _get_request_payload(self, input_, *, stop=None, **kwargs) -> dict:
        payload = super()._get_request_payload(input_, stop=stop, **kwargs)
        sent = payload.get("messages")
        if isinstance(sent, list):
            callers = nested_callers(self._convert_input(input_).to_messages())
            if callers:
                restore_callers(sent, callers)
        return payload
