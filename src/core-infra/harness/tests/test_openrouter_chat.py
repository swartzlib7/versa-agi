"""OpenRouter client: reasoning records and search sources survive a turn (§2.7 B7)."""

from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import patch

CORE_INFRA = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, CORE_INFRA)

try:
    from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage, ToolMessage
    from langchain_core.outputs import ChatGenerationChunk
    from langchain_openai import ChatOpenAI
except ImportError as exc:  # the system interpreter has no LangChain
    raise unittest.SkipTest(f"LangChain not available: {exc}")

from harness.openrouter_chat import (  # noqa: E402
    ANNOTATIONS_KEY,
    REASONING_KEY,
    OpenRouterChat,
    _DELTA_KEY,
    _keep_cache_write,
    citation_sources,
    is_retention_refusal,
    merge_reasoning_deltas,
    sources_footer,
)
from harness import prompt_cache  # noqa: E402
from provider_runtime import create_langchain_client, resolve_provider_route  # noqa: E402

CLAUDE_RECORD = [{"type": "reasoning.text", "text": "plan", "signature": "sig",
                  "format": "anthropic-claude-v1", "index": 0}]
GEMINI_RECORD = [{"type": "reasoning.encrypted", "data": "abc", "format": "google-gemini-v1",
                  "id": "call_1", "index": 0}]
CITATION = {"type": "url_citation",
            "url_citation": {"url": "https://www.python.org/downloads/", "title": "python.org"}}


def client() -> OpenRouterChat:
    return OpenRouterChat(model="google/gemini-4-hydrogen", api_key="test-key",
                          base_url="https://openrouter.ai/api/v1")


class TestMergeDeltas(unittest.TestCase):
    def test_pieces_of_one_item_are_joined(self):
        merged = merge_reasoning_deltas([
            {"type": "reasoning.text", "text": "pl", "format": "anthropic-claude-v1", "index": 0},
            {"type": "reasoning.text", "text": "an", "format": "anthropic-claude-v1", "index": 0},
            {"type": "reasoning.text", "signature": "sig", "format": "anthropic-claude-v1", "index": 0},
        ])
        self.assertEqual(merged, CLAUDE_RECORD)

    def test_items_with_different_indexes_stay_apart_and_in_order(self):
        merged = merge_reasoning_deltas([
            {"type": "reasoning.text", "text": "a", "index": 0},
            {"type": "reasoning.encrypted", "data": "x", "index": 1},
            {"type": "reasoning.text", "text": "b", "index": 0},
            {"type": "reasoning.encrypted", "data": "y", "index": 1},
        ])
        self.assertEqual([(i["type"], i.get("text") or i.get("data")) for i in merged],
                         [("reasoning.text", "ab"), ("reasoning.encrypted", "xy")])

    def test_a_piece_without_an_index_is_its_own_item(self):
        merged = merge_reasoning_deltas([{"type": "reasoning.summary", "summary": "a"},
                                         {"type": "reasoning.summary", "summary": "b"}])
        self.assertEqual(len(merged), 2)

    def test_the_format_and_type_are_not_repeated(self):
        merged = merge_reasoning_deltas([dict(GEMINI_RECORD[0], data="ab"),
                                         dict(GEMINI_RECORD[0], data="c")])
        self.assertEqual(merged, [dict(GEMINI_RECORD[0], data="abc")])


class TestSources(unittest.TestCase):
    def test_footer_lists_pages_the_text_does_not_link(self):
        footer = sources_footer("Python 3.15.2 was released.", [CITATION, CITATION])
        self.assertEqual(footer, "\n\nSources:\n- python.org — https://www.python.org/downloads/")

    def test_no_footer_when_the_text_already_has_the_link(self):
        self.assertEqual(sources_footer("See https://www.python.org/downloads/ .", [CITATION]), "")

    def test_no_footer_without_citations(self):
        self.assertEqual(sources_footer("text", None), "")
        self.assertEqual(citation_sources([{"type": "file_citation"}]), [])


class TestRequest(unittest.TestCase):
    def test_the_record_goes_back_with_the_message_that_carried_it(self):
        history = [
            HumanMessage("add 17 and 25"),
            AIMessage(content="", additional_kwargs={REASONING_KEY: GEMINI_RECORD},
                      tool_calls=[{"id": "call_1", "name": "add", "args": {"a": 17, "b": 25}}]),
            ToolMessage("42", tool_call_id="call_1"),
            AIMessage(content="42"),
        ]
        payload = client()._get_request_payload(history)
        sent = payload["messages"]
        self.assertEqual(sent[1][REASONING_KEY], GEMINI_RECORD)
        self.assertEqual(sent[1]["tool_calls"][0]["function"]["name"], "add")
        self.assertNotIn(REASONING_KEY, sent[0])
        self.assertNotIn(REASONING_KEY, sent[3])

    def test_plain_chatopenai_drops_it(self):
        """The reason this subclass exists."""
        history = [HumanMessage("hi"),
                   AIMessage(content="x", additional_kwargs={REASONING_KEY: CLAUDE_RECORD}),
                   HumanMessage("more")]
        plain = ChatOpenAI(model="m", api_key="k", base_url="https://openrouter.ai/api/v1")
        self.assertNotIn(REASONING_KEY, plain._get_request_payload(history)["messages"][1])


class TestReply(unittest.TestCase):
    def response(self, message):
        return {"id": "gen-1", "model": "google/gemini-4-hydrogen", "object": "chat.completion",
                "created": 1, "choices": [{"index": 0, "finish_reason": "stop", "message": message}],
                "usage": {"prompt_tokens": 5, "completion_tokens": 2, "total_tokens": 7}}

    def test_a_complete_reply_keeps_the_record(self):
        result = client()._create_chat_result(self.response(
            {"role": "assistant", "content": "42", REASONING_KEY: GEMINI_RECORD}))
        message = result.generations[0].message
        self.assertEqual(message.additional_kwargs[REASONING_KEY], GEMINI_RECORD)
        self.assertEqual(message.content, "42")

    def test_a_searched_reply_gets_its_sources(self):
        result = client()._create_chat_result(self.response(
            {"role": "assistant", "content": "Python 3.15.2.", ANNOTATIONS_KEY: [CITATION]}))
        message = result.generations[0].message
        self.assertTrue(message.content.endswith("- python.org — https://www.python.org/downloads/"))
        self.assertEqual(message.additional_kwargs[ANNOTATIONS_KEY], [CITATION])

    def test_a_reply_without_either_is_untouched(self):
        result = client()._create_chat_result(self.response({"role": "assistant", "content": "ok"}))
        message = result.generations[0].message
        self.assertEqual(message.content, "ok")
        self.assertNotIn(REASONING_KEY, message.additional_kwargs)


class TestStream(unittest.TestCase):
    def chunk(self, text="", delta=None, meta=None):
        message = AIMessageChunk(content=text, response_metadata=dict(meta or {}))
        if delta:
            message.additional_kwargs[_DELTA_KEY] = delta
        return ChatGenerationChunk(message=message, generation_info=dict(meta or {}) or None)

    def run_stream(self, chunks):
        with patch.object(ChatOpenAI, "_stream", return_value=iter(chunks)):
            out = list(client()._stream([HumanMessage("hi")]))
        total = out[0]
        for piece in out[1:]:
            total = total + piece
        return out, total.message

    def test_streamed_pieces_become_one_record_on_the_reply(self):
        done = {"finish_reason": "stop", "model_name": "google/gemini-4-hydrogen"}
        out, message = self.run_stream([
            self.chunk(delta={REASONING_KEY: [dict(GEMINI_RECORD[0], data="ab")]}),
            self.chunk("4", delta={REASONING_KEY: [dict(GEMINI_RECORD[0], data="c")]}),
            self.chunk("2", meta=done),
            self.chunk(meta=done),  # the usage chunk repeats the finish reason
        ])
        self.assertEqual(len(out), 4)
        self.assertEqual(message.content, "42")
        self.assertEqual(message.additional_kwargs[REASONING_KEY], GEMINI_RECORD)
        self.assertNotIn(_DELTA_KEY, message.additional_kwargs)
        self.assertEqual(message.response_metadata["finish_reason"], "stop")
        self.assertEqual(message.response_metadata["model_name"], "google/gemini-4-hydrogen")

    def test_streamed_sources_are_written_under_the_reply(self):
        _out, message = self.run_stream([
            self.chunk("Python 3.15.2."),
            self.chunk(delta={ANNOTATIONS_KEY: [CITATION]}),
        ])
        self.assertTrue(message.content.startswith("Python 3.15.2."))
        self.assertTrue(message.content.endswith("https://www.python.org/downloads/"))

    def test_an_empty_stream_yields_nothing(self):
        with patch.object(ChatOpenAI, "_stream", return_value=iter([])):
            self.assertEqual(list(client()._stream([HumanMessage("hi")])), [])


class TestCacheWriteCount(unittest.TestCase):
    """The tokens written to the provider's cache reach the reply's usage record (§2.7 B16).

    The client keeps the tokens read and drops the tokens written; on a streamed
    reply nothing else holds them (found in the first live cycle, 2026-11-15).
    """

    USAGE = {"prompt_tokens": 8025, "completion_tokens": 66, "total_tokens": 8091,
             "prompt_tokens_details": {"cached_tokens": 0, "cache_write_tokens": 8021}}

    def test_a_complete_reply(self):
        response = {"id": "gen-1", "model": "anthropic/claude-opus-5.5", "object": "chat.completion",
                    "created": 1, "usage": self.USAGE,
                    "choices": [{"index": 0, "finish_reason": "stop",
                                 "message": {"role": "assistant", "content": "ok"}}]}
        message = client()._create_chat_result(response).generations[0].message
        self.assertEqual(message.usage_metadata["input_token_details"]["cache_creation"], 8021)
        self.assertEqual(prompt_cache.cached_tokens(message), (0, 8021))

    def test_a_streamed_reply_gets_it_from_its_last_piece(self):
        pieces = [
            {"id": "gen-1", "model": "anthropic/claude-opus-5.5", "object": "chat.completion.chunk",
             "created": 1, "choices": [{"index": 0, "delta": {"role": "assistant", "content": "ok"}}]},
            {"id": "gen-1", "model": "anthropic/claude-opus-5.5", "object": "chat.completion.chunk",
             "created": 1, "choices": [], "usage": self.USAGE},
        ]
        chat = client()
        total = None
        for piece in pieces:
            generation_chunk = chat._convert_chunk_to_generation_chunk(piece, AIMessageChunk, None)
            if generation_chunk is None:
                continue
            total = generation_chunk if total is None else total + generation_chunk
        self.assertEqual(total.message.content, "ok")
        self.assertEqual(prompt_cache.cached_tokens(total.message), (0, 8021))

    def test_a_read_only_reply_and_a_reply_with_no_usage_are_left_alone(self):
        read = dict(self.USAGE, prompt_tokens_details={"cached_tokens": 8021, "cache_write_tokens": 0})
        response = {"id": "gen-1", "model": "anthropic/claude-opus-5.5", "object": "chat.completion",
                    "created": 1, "usage": read,
                    "choices": [{"index": 0, "finish_reason": "stop",
                                 "message": {"role": "assistant", "content": "ok"}}]}
        message = client()._create_chat_result(response).generations[0].message
        self.assertEqual(prompt_cache.cached_tokens(message), (8021, 0))
        self.assertNotIn("cache_creation", message.usage_metadata["input_token_details"])
        _keep_cache_write(message, None)
        _keep_cache_write(AIMessageChunk(content=""), self.USAGE)   # no usage record to write into


class TestFactory(unittest.TestCase):
    def route(self, provider):
        providers = {"openrouter": {"cls": "ChatOpenAI"}, "xai": {"cls": "ChatOpenAI"}}
        return resolve_provider_route("m", catalog={"m": {"provider": provider}}, providers=providers)

    def test_openrouter_gets_the_subclass_and_stream_usage(self):
        llm = create_langchain_client(self.route("openrouter"),
                                      native_params={"streaming": True}, key_resolver=lambda _s: "k")
        self.assertIsInstance(llm, OpenRouterChat)
        self.assertTrue(llm.streaming)
        self.assertTrue(llm.stream_usage)

    def test_other_openai_compatible_providers_keep_chatopenai(self):
        llm = create_langchain_client(self.route("xai"), native_params={}, key_resolver=lambda _s: "k")
        self.assertIs(type(llm), ChatOpenAI)


ZDR_ERROR = RuntimeError(
    "Error code: 404 - {'error': {'message': 'No endpoints found for anthropic/claude-opus-5.5. "
    "0 endpoints … ZDR violation (account settings): 4 endpoints excluded', 'code': 404}}"
)


def searching_client() -> OpenRouterChat:
    return OpenRouterChat(
        model="anthropic/claude-opus-5.5", api_key="test-key",
        base_url="https://openrouter.ai/api/v1",
        extra_body={"reasoning": {"effort": "high"}, "plugins": [{"id": "web", "engine": "native"}]},
    )


class TestSearchRefusedByPrivacySetting(unittest.TestCase):
    """Web search is on by default; a zero-retention account must still get an answer."""

    def test_the_refusal_is_recognised(self):
        self.assertTrue(is_retention_refusal(ZDR_ERROR))
        self.assertTrue(is_retention_refusal(ValueError("No endpoints found matching your data policy")))
        self.assertFalse(is_retention_refusal(ValueError("Error code: 429 - rate limited")))

    def test_the_call_is_made_again_without_the_search(self):
        llm = searching_client()
        reply = object()
        with patch.object(ChatOpenAI, "_generate", side_effect=[ZDR_ERROR, reply]) as call, \
                patch("builtins.print") as said:
            self.assertIs(llm._generate([HumanMessage("hi")]), reply)
        self.assertEqual(call.call_count, 2)
        self.assertIn("WEB SEARCH: off for this cycle", said.call_args[0][0])
        payload = llm._get_request_payload([HumanMessage("hi")])
        self.assertEqual(payload["extra_body"], {"reasoning": {"effort": "high"}})

    def test_the_search_stays_off_and_a_second_refusal_is_raised(self):
        llm = searching_client()
        with patch.object(ChatOpenAI, "_generate", side_effect=[ZDR_ERROR, ZDR_ERROR]), \
                patch("builtins.print"):
            with self.assertRaises(RuntimeError):
                llm._generate([HumanMessage("hi")])

    def test_another_error_is_raised_and_the_search_stays_on(self):
        llm = searching_client()
        with patch.object(ChatOpenAI, "_generate", side_effect=ValueError("429 rate limited")):
            with self.assertRaises(ValueError):
                llm._generate([HumanMessage("hi")])
        self.assertIn("plugins", llm._get_request_payload([HumanMessage("hi")])["extra_body"])

    def test_a_client_that_is_not_searching_raises_the_refusal(self):
        with patch.object(ChatOpenAI, "_generate", side_effect=ZDR_ERROR):
            with self.assertRaises(RuntimeError):
                client()._generate([HumanMessage("hi")])

    def test_a_streamed_call_is_made_again_too(self):
        def refused():
            raise ZDR_ERROR
            yield  # pragma: no cover — makes this a generator

        piece = ChatGenerationChunk(message=AIMessageChunk(content="ok"))
        llm = searching_client()
        with patch.object(ChatOpenAI, "_stream", side_effect=[refused(), iter([piece])]) as call, \
                patch("builtins.print"):
            out = list(llm._stream([HumanMessage("hi")]))
        self.assertEqual(call.call_count, 2)
        self.assertEqual([c.message.content for c in out], ["ok"])


if __name__ == "__main__":
    unittest.main()
