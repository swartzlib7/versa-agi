"""OpenRouter chat client: keeps what ``ChatOpenAI`` drops.

``ChatOpenAI`` speaks plain Chat Completions and discards two OpenRouter
fields on the assistant message:

- ``reasoning_details`` — the model's reasoning record. OpenRouter expects it
  back unchanged on the next request so a tool-calling turn continues the same
  reasoning (Claude signatures, Gemini thought signatures).
- ``annotations`` — the sources of a provider web search.

This subclass stores ``reasoning_details`` on the message
(``additional_kwargs``), sends it back with that message, and writes the
sources under the reply so the agent and the cycle log both have them.

Web search is on by default. An OpenRouter account that allows only
zero-retention endpoints is refused a model whose search needs retention
(seen for Claude Opus 5.5). The client then asks once more without the search
and leaves it off for the rest of the cycle.
"""

from __future__ import annotations

from datetime import datetime
from itertools import chain
from typing import Any, Iterator

from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGenerationChunk, ChatResult
from langchain_openai import ChatOpenAI
from pydantic import PrivateAttr

REASONING_KEY = "reasoning_details"
ANNOTATIONS_KEY = "annotations"
_DELTA_KEY = "__openrouter_delta__"
# String fields of a reasoning item that arrive in pieces when streamed.
_PIECE_FIELDS = ("text", "summary", "data")
SOURCES_HEADING = "Sources:"
SEARCH_PLUGINS_KEY = "plugins"
# OpenRouter's answers when the account's privacy setting leaves no endpoint.
_RETENTION_REFUSALS = ("ZDR violation", "data policy")


def is_retention_refusal(error: BaseException) -> bool:
    """The account's privacy setting excluded every endpoint for this request."""
    text = str(error)
    return any(mark in text for mark in _RETENTION_REFUSALS)


def merge_reasoning_deltas(deltas: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Streamed reasoning pieces → the items a non-streamed reply carries.

    Pieces with the same ``index`` are one item: text, summary and data are
    joined in arrival order; every other field keeps its first non-empty value.
    A piece without an index is its own item.
    """
    merged: list[dict[str, Any]] = []
    by_index: dict[Any, dict[str, Any]] = {}
    for piece in deltas:
        if not isinstance(piece, dict):
            continue
        index = piece.get("index")
        item = by_index.get(index) if index is not None else None
        if item is None or item.get("type") != piece.get("type", item.get("type")):
            item = dict(piece)
            merged.append(item)
            if index is not None:
                by_index[index] = item
            continue
        for key, value in piece.items():
            if value is None or value == "":
                continue
            if key in _PIECE_FIELDS and isinstance(value, str) and isinstance(item.get(key), str):
                item[key] += value
            elif item.get(key) in (None, ""):
                item[key] = value
    return merged


def citation_sources(annotations: Any) -> list[tuple[str, str]]:
    """(title, url) for each distinct ``url_citation``, in first-seen order."""
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for note in annotations or []:
        if not isinstance(note, dict) or note.get("type") != "url_citation":
            continue
        cite = note.get("url_citation") if isinstance(note.get("url_citation"), dict) else note
        url = str(cite.get("url") or "").strip()
        if not url or url in seen:
            continue
        seen.add(url)
        out.append((str(cite.get("title") or "").strip(), url))
    return out


def sources_footer(text: str, annotations: Any) -> str:
    """A ``Sources:`` list of the cited pages the reply text does not already link."""
    lines = [
        f"- {title} — {url}" if title else f"- {url}"
        for title, url in citation_sources(annotations)
        if url not in (text or "")
    ]
    if not lines:
        return ""
    return "\n\n" + SOURCES_HEADING + "\n" + "\n".join(lines)


class OpenRouterChat(ChatOpenAI):
    """``ChatOpenAI`` for OpenRouter with reasoning records and search sources kept."""

    _search_refused: bool = PrivateAttr(default=False)

    def _searching(self) -> bool:
        body = self.extra_body if isinstance(self.extra_body, dict) else {}
        return bool(body.get(SEARCH_PLUGINS_KEY)) and not self._search_refused

    def _give_up_search(self, error: BaseException) -> bool:
        """True when the call should be made again without the provider search."""
        if not self._searching() or not is_retention_refusal(error):
            return False
        self._search_refused = True
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        print(
            f"[{stamp}] WEB SEARCH: off for this cycle. The OpenRouter account allows only "
            f"zero-retention endpoints, and {self.model_name} has none with its web search. "
            "Allow data-retaining endpoints at https://openrouter.ai/settings/privacy, or "
            "turn the model's web search off.",
            flush=True,
        )
        return True

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        try:
            return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)
        except Exception as error:  # noqa: BLE001 — anything else is raised again
            if not self._give_up_search(error):
                raise
            return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)

    def _get_request_payload(self, input_, *, stop=None, **kwargs) -> dict:
        payload = super()._get_request_payload(input_, stop=stop, **kwargs)
        body = payload.get("extra_body")
        if self._search_refused and isinstance(body, dict) and SEARCH_PLUGINS_KEY in body:
            payload["extra_body"] = {k: v for k, v in body.items() if k != SEARCH_PLUGINS_KEY}
        sent = payload.get("messages")
        messages = self._convert_input(input_).to_messages()
        if isinstance(sent, list) and len(sent) == len(messages):
            for message, wire in zip(messages, sent):
                if not isinstance(message, AIMessage) or not isinstance(wire, dict):
                    continue
                details = message.additional_kwargs.get(REASONING_KEY)
                if details:
                    wire[REASONING_KEY] = details
        return payload

    def _create_chat_result(self, response, generation_info=None) -> ChatResult:
        result = super()._create_chat_result(response, generation_info)
        raw = response if isinstance(response, dict) else response.model_dump()
        choices = raw.get("choices") or []
        for generation, choice in zip(result.generations, choices):
            wire = (choice or {}).get("message") or {}
            _keep_openrouter_fields(
                generation.message, wire.get(REASONING_KEY), wire.get(ANNOTATIONS_KEY),
            )
            _keep_cache_write(generation.message, raw.get("usage"))
        return result

    def _convert_chunk_to_generation_chunk(self, chunk, default_chunk_class, base_generation_info):
        generation_chunk = super()._convert_chunk_to_generation_chunk(
            chunk, default_chunk_class, base_generation_info,
        )
        if generation_chunk is None:
            return None
        # The usage block rides on the last piece of a streamed reply.
        _keep_cache_write(generation_chunk.message, chunk.get("usage") or chunk.get("chunk", {}).get("usage"))
        choices = chunk.get("choices") or chunk.get("chunk", {}).get("choices") or []
        delta = (choices[0] or {}).get("delta") if choices else None
        if isinstance(delta, dict):
            kept = {
                key: delta[key]
                for key in (REASONING_KEY, ANNOTATIONS_KEY)
                if isinstance(delta.get(key), list) and delta[key]
            }
            if kept:
                generation_chunk.message.additional_kwargs[_DELTA_KEY] = kept
        return generation_chunk

    def _stream(self, *args: Any, **kwargs: Any) -> Iterator[ChatGenerationChunk]:
        reasoning: list[dict[str, Any]] = []
        annotations: list[dict[str, Any]] = []
        text_parts: list[str] = []
        held: ChatGenerationChunk | None = None
        said: set[str] = set()
        # The refusal arrives before the first piece, so nothing has been passed on yet.
        source = super()._stream(*args, **kwargs)
        try:
            first = next(source, None)
        except Exception as error:  # noqa: BLE001 — anything else is raised again
            if not self._give_up_search(error):
                raise
            source = super()._stream(*args, **kwargs)
            first = next(source, None)
        if first is None:
            return
        for generation_chunk in chain([first], source):
            _say_once(generation_chunk, said)
            delta = generation_chunk.message.additional_kwargs.pop(_DELTA_KEY, None) or {}
            reasoning.extend(delta.get(REASONING_KEY) or [])
            annotations.extend(delta.get(ANNOTATIONS_KEY) or [])
            content = generation_chunk.message.content
            if isinstance(content, str):
                text_parts.append(content)
            if held is not None:
                yield held
            held = generation_chunk
        if held is None:
            return
        # The last chunk carries the whole record, so nothing is merged piecewise.
        details = merge_reasoning_deltas(reasoning)
        if details:
            held.message.additional_kwargs[REASONING_KEY] = details
        if annotations:
            held.message.additional_kwargs[ANNOTATIONS_KEY] = annotations
            footer = sources_footer("".join(text_parts), annotations)
            if footer and isinstance(held.message.content, str):
                held.message.content += footer
        yield held


# Reply facts OpenRouter repeats on more than one streamed chunk. Joined as
# text they would read "stopstop"; the first one stands.
_ONCE_FIELDS = ("finish_reason", "model_name", "system_fingerprint", "service_tier", "model_provider")


def _say_once(generation_chunk: ChatGenerationChunk, said: set[str]) -> None:
    """Drop a reply fact from a chunk when an earlier chunk already carried it."""
    for holder in (generation_chunk.generation_info, generation_chunk.message.response_metadata):
        if not isinstance(holder, dict):
            continue
        for name in _ONCE_FIELDS:
            if name in holder and name in said:
                holder.pop(name, None)
    for holder in (generation_chunk.generation_info, generation_chunk.message.response_metadata):
        if isinstance(holder, dict):
            said.update(name for name in _ONCE_FIELDS if holder.get(name))


def _keep_cache_write(message, usage) -> None:
    """Carry the tokens written to the provider's cache into the reply's usage record.

    OpenRouter reports them as ``prompt_tokens_details.cache_write_tokens``. The
    client keeps the tokens read (``cache_read``) and drops this one; on a
    streamed reply nothing else holds it. It is stored where Anthropic's own
    client puts it (``input_token_details.cache_creation``), so one reader
    serves both routes (harness/prompt_cache.py).
    """
    if not isinstance(usage, dict):
        return
    details = usage.get("prompt_tokens_details")
    written = details.get("cache_write_tokens") if isinstance(details, dict) else None
    record = getattr(message, "usage_metadata", None)
    if not written or not isinstance(record, dict):
        return
    kept = dict(record.get("input_token_details") or {})
    kept["cache_creation"] = int(written)
    record["input_token_details"] = kept


def _keep_openrouter_fields(message, reasoning_details, annotations) -> None:
    """Store the reasoning record and the search sources on a complete reply."""
    if not isinstance(message, AIMessage):
        return
    if reasoning_details:
        message.additional_kwargs[REASONING_KEY] = reasoning_details
    if annotations:
        message.additional_kwargs[ANNOTATIONS_KEY] = annotations
        if isinstance(message.content, str):
            message.content += sources_footer(message.content, annotations)
