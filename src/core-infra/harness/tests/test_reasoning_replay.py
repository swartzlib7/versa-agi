"""Reasoning records that may not go back to the model are left out (§2.7 B3, B4)."""

from __future__ import annotations

import os
import sys
import unittest

CORE_INFRA = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, CORE_INFRA)

from harness.reasoning_replay import (  # noqa: E402
    BOUND,
    EMPTY_REPLY,
    SEARCH_NOTE,
    SIGNATURE,
    WIRE_ANTHROPIC,
    WIRE_CHAT,
    WIRE_GOOGLE,
    WIRE_RESPONSES,
    ReplayGuard,
    portable,
    settle_server_tools,
    wire_for,
    fingerprint,
    has_reasoning,
    is_binding_error,
    is_server_tool_error,
    same_model,
    strip_reasoning,
)

MODEL = "claude-fable-5-2"


class Msg:
    """A stand-in with the fields the guard reads from a LangChain message."""

    def __init__(self, type_, content, id_=None, model="", tool_calls=None, extra=None,
                 tool_call_id=None):
        self.type = type_
        self.content = content
        self.id = id_
        self.tool_calls = tool_calls or []
        self.additional_kwargs = extra or {}
        self.response_metadata = {"model_name": model} if model else {}
        self.tool_call_id = tool_call_id

    def model_copy(self, update):
        copy = Msg(self.type, self.content, self.id, tool_calls=self.tool_calls,
                   extra=self.additional_kwargs, tool_call_id=self.tool_call_id)
        copy.response_metadata = dict(self.response_metadata)
        for key, value in update.items():
            setattr(copy, key, value)
        return copy


def human(text, id_):
    return Msg("human", text, id_)


def thinking_reply(id_, text="answer", model=MODEL, tool_calls=None):
    content = [{"type": "thinking", "thinking": "…", "signature": "sig-" + id_},
               {"type": "text", "text": text}]
    return Msg("ai", content, id_, model=model, tool_calls=tool_calls)


def kept(messages):
    """Ids of the replies that still carry a reasoning record."""
    return [m.id for m in messages if m.type == "ai" and has_reasoning(m)]


class TestStripAndFingerprint(unittest.TestCase):
    def test_strip_removes_blocks_and_openrouter_details_only(self):
        msg = Msg("ai", [{"type": "thinking", "thinking": "x", "signature": "s"},
                         {"type": "redacted_thinking", "data": "d"},
                         {"type": "text", "text": "hi"},
                         {"type": "tool_use", "id": "t1", "name": "add", "input": {}}],
                  "a1", extra={"reasoning_details": [{"type": "reasoning.text"}], "refusal": None})
        out = strip_reasoning(msg)
        self.assertEqual([p["type"] for p in out.content], ["text", "tool_use"])
        self.assertEqual(out.additional_kwargs, {"refusal": None})
        self.assertTrue(has_reasoning(msg))  # the original is untouched
        self.assertEqual(len(msg.content), 4)

    def test_a_reply_that_was_only_reasoning_keeps_a_placeholder(self):
        msg = Msg("ai", [{"type": "thinking", "thinking": "x", "signature": "s"}], "a1")
        self.assertEqual(strip_reasoning(msg).content, EMPTY_REPLY)

    def test_a_tool_call_reply_may_have_no_other_content(self):
        msg = Msg("ai", [{"type": "thinking", "thinking": "x", "signature": "s"}], "a1",
                  tool_calls=[{"id": "t1", "name": "add", "args": {"a": 1}}])
        self.assertEqual(strip_reasoning(msg).content, [])

    def test_a_message_without_reasoning_is_returned_as_is(self):
        msg = human("hello", "h1")
        self.assertIs(strip_reasoning(msg), msg)

    def test_fingerprint_ignores_the_reasoning_record(self):
        msg = thinking_reply("a1")
        self.assertEqual(fingerprint(msg), fingerprint(strip_reasoning(msg)))

    def test_fingerprint_sees_an_edit(self):
        self.assertNotEqual(fingerprint(human("with image", "h1")), fingerprint(human("image removed", "h1")))


class TestSameModel(unittest.TestCase):
    def test_same_model_across_id_spellings(self):
        self.assertTrue(same_model("claude-fable-5-2", "claude-fable-5-2"))
        self.assertTrue(same_model("anthropic/claude-opus-5.5", "anthropic/claude-opus-5.5"))
        self.assertTrue(same_model("google/gemini-4-hydrogen-20261001", "google/gemini-4-hydrogen"))

    def test_other_models_and_missing_names(self):
        self.assertFalse(same_model("claude-opus-5-5", "claude-fable-5-2"))
        self.assertFalse(same_model("", "claude-fable-5-2"))


class TestBoundGuard(unittest.TestCase):
    def guard(self, log=None):
        return ReplayGuard(BOUND, MODEL, log=log)

    def test_a_resumed_thread_loses_every_earlier_record(self):
        """A new cycle has a new system prompt: nothing from before may be replayed."""
        history = [human("wake", "h1"), thinking_reply("a1"), human("next wake", "h2")]
        lines = []
        out = self.guard(lines.append).apply(history)
        self.assertEqual(kept(out), [])
        self.assertEqual(kept(history), ["a1"])  # the stored thread is unchanged
        self.assertIn("new cycle", lines[0])

    def test_records_written_in_this_cycle_are_replayed_while_history_only_grows(self):
        guard = self.guard()
        history = [human("wake", "h1")]
        guard.apply(history)
        history += [thinking_reply("a1", tool_calls=[{"id": "t1", "name": "add", "args": {}}]),
                    Msg("tool", "42", "t1m", tool_call_id="t1")]
        self.assertEqual(kept(guard.apply(history)), ["a1"])
        history += [thinking_reply("a2"), human("time warning", "h2")]
        self.assertEqual(kept(guard.apply(history)), ["a1", "a2"])

    def test_a_trimmed_start_drops_the_records_so_far_and_keeps_later_ones(self):
        guard = self.guard()
        history = [human("wake", "h1")]
        guard.apply(history)
        history += [thinking_reply("a1"), human("more", "h2")]
        guard.apply(history)
        trimmed = history[1:]  # the oldest message left the window
        self.assertEqual(kept(guard.apply(trimmed)), [])
        trimmed = trimmed + [thinking_reply("a2"), human("again", "h3")]
        self.assertEqual(kept(guard.apply(trimmed)), ["a2"])

    def test_a_frames_block_at_the_start_counts_as_a_change(self):
        guard = self.guard()
        history = [human("wake", "h1")]
        guard.apply(history)
        history += [thinking_reply("a1"), human("more", "h2")]
        guard.apply(history)
        framed = [human("FRAMES: summary of earlier turns", None)] + history[1:]
        self.assertEqual(kept(guard.apply(framed)), [])

    def test_an_edited_earlier_message_counts_as_a_change(self):
        """An image payload is removed from an earlier message once it has been seen."""
        guard = self.guard()
        history = [human("wake", "h1"), human("image: <base64>", "v1")]
        guard.apply(history)
        history += [thinking_reply("a1"), human("more", "h2")]
        guard.apply(history)
        history[1] = human("image removed", "v1")
        self.assertEqual(kept(guard.apply(history)), [])

    def test_a_dropped_record_stays_dropped(self):
        """What was sent once must be sent the same way again."""
        guard = self.guard()
        history = [human("wake", "h1"), thinking_reply("a0"), human("go", "h2")]
        first = guard.apply(history)
        history += [thinking_reply("a1"), human("more", "h3")]
        second = guard.apply(history)
        self.assertEqual(kept(second), ["a1"])
        self.assertEqual([fingerprint(m) for m in second[: len(first)]],
                         [fingerprint(m) for m in first])

    def test_another_models_record_is_always_left_out(self):
        guard = self.guard()
        history = [human("wake", "h1")]
        guard.apply(history)
        history += [thinking_reply("call1", model="gemini-3.7-flash"), thinking_reply("a1"),
                    human("more", "h2")]
        self.assertEqual(kept(guard.apply(history)), ["a1"])

    def test_after_a_binding_error_the_next_request_carries_none(self):
        guard = self.guard()
        history = [human("wake", "h1")]
        guard.apply(history)
        history += [thinking_reply("a1"), human("more", "h2")]
        self.assertEqual(kept(guard.apply(history)), ["a1"])
        guard.strip_all_next()
        self.assertEqual(kept(guard.apply(history)), [])
        history += [thinking_reply("a2"), human("again", "h3")]
        self.assertEqual(kept(guard.apply(history)), ["a2"])


class TestOtherModes(unittest.TestCase):
    def test_signature_mode_keeps_own_records_after_a_change(self):
        """Gemini thought signatures are not tied to earlier history."""
        guard = ReplayGuard(SIGNATURE, "google/gemini-4-hydrogen")
        own = Msg("ai", "ok", "a1", model="google/gemini-4-hydrogen",
                  extra={"reasoning_details": [{"type": "reasoning.encrypted", "data": "x"}]})
        other = Msg("ai", "ok", "a0", model="anthropic/claude-opus-5.5",
                    extra={"reasoning_details": [{"type": "reasoning.text", "text": "x"}]})
        out = guard.apply([human("resumed wake", "h1"), other, own, human("go", "h2")])
        self.assertEqual(kept(out), ["a1"])

    def test_no_mode_changes_nothing(self):
        guard = ReplayGuard("", "gpt-5.6-terra")
        history = [human("wake", "h1"), thinking_reply("a1", model="other")]
        self.assertIs(guard.apply(history), history)
        self.assertFalse(guard.active)


class TestBindingError(unittest.TestCase):
    def test_anthropics_message_is_recognised(self):
        error = ValueError(
            "Error code: 400 - messages.1.content.0: Invalid `signature` in `thinking` block. "
            "The block is bound to a different conversation. Remove the block, or set …"
        )
        self.assertTrue(is_binding_error(error))
        self.assertFalse(is_binding_error(ValueError("Error code: 429 - rate limited")))


def types(message):
    return [p["type"] for p in message.content] if isinstance(message.content, list) else message.content


def responses_reply(id_="r1"):
    """A reply as OpenAI's Responses endpoint stores it: reasoning, a search, text, a call."""
    return Msg("ai", [
        {"type": "reasoning", "id": "rs_1", "encrypted_content": "E"},
        {"type": "web_search_call", "id": "ws_1", "status": "completed"},
        {"type": "text", "text": "found it", "annotations": [
            {"type": "url_citation", "url": "https://a.example", "title": "A"}]},
        {"type": "function_call", "id": "fc_1", "call_id": "c1", "name": "add", "arguments": "{}"},
    ], id_, model="gpt-6-sol", tool_calls=[{"id": "c1", "name": "add", "args": {}}])


def claude_search_reply(id_="c1"):
    """A Claude reply whose provider search finished inside the reply."""
    return Msg("ai", [
        {"type": "thinking", "thinking": "…", "signature": "s"},
        {"type": "server_tool_use", "id": "srv1", "name": "web_search", "input": {}},
        {"type": "web_search_tool_result", "tool_use_id": "srv1",
         "content": [{"type": "web_search_result", "url": "https://b.example", "title": "B"}]},
        {"type": "text", "text": "the answer", "citations": [{"type": "web_search_result_location"}]},
    ], id_, model=MODEL)


class TestWire(unittest.TestCase):
    def test_wire_of_a_model(self):
        self.assertEqual(wire_for("anthropic"), WIRE_ANTHROPIC)
        self.assertEqual(wire_for("google"), WIRE_GOOGLE)
        self.assertEqual(wire_for("openai_compat", "responses"), WIRE_RESPONSES)
        self.assertEqual(wire_for("openai_compat"), WIRE_CHAT)
        self.assertEqual(wire_for("local"), WIRE_CHAT)

    def test_a_reply_goes_back_whole_on_the_wire_that_wrote_it(self):
        reply = responses_reply()
        self.assertIs(portable(reply, WIRE_RESPONSES), reply)
        claude = claude_search_reply()
        self.assertIs(portable(claude, WIRE_ANTHROPIC), claude)

    def test_responses_parts_stay_behind_on_other_wires(self):
        for wire in (WIRE_ANTHROPIC, WIRE_GOOGLE, WIRE_CHAT):
            out = portable(responses_reply(), wire)
            self.assertEqual(types(out), ["text", "text"], wire)
            self.assertEqual(out.tool_calls, [{"id": "c1", "name": "add", "args": {}}], wire)

    def test_claude_parts_stay_behind_on_other_wires(self):
        for wire in (WIRE_RESPONSES, WIRE_GOOGLE, WIRE_CHAT):
            out = portable(claude_search_reply(), wire)
            self.assertEqual(types(out), ["text", "text"], wire)

    def test_a_dropped_search_leaves_a_note_with_what_it_returned(self):
        out = portable(claude_search_reply(), WIRE_CHAT)
        note = out.content[-1]["text"]
        self.assertTrue(note.startswith(SEARCH_NOTE))
        self.assertIn("B (https://b.example)", note)
        out = portable(responses_reply(), WIRE_ANTHROPIC)
        self.assertIn("A (https://a.example)", out.content[-1]["text"])

    def test_no_note_when_nothing_was_searched(self):
        reply = Msg("ai", [{"type": "thinking", "thinking": "x", "signature": "s"},
                           {"type": "text", "text": "hi"}], "a1", model=MODEL)
        self.assertEqual(types(portable(reply, WIRE_CHAT)), ["text"])

    def test_a_reply_left_with_nothing_still_has_content(self):
        only = Msg("ai", [{"type": "reasoning", "id": "rs", "encrypted_content": "E"}], "a1")
        self.assertEqual(portable(only, WIRE_CHAT).content, EMPTY_REPLY)
        call = Msg("ai", [{"type": "function_call", "id": "f", "call_id": "c", "name": "add"}], "a2",
                   tool_calls=[{"id": "c", "name": "add", "args": {}}])
        self.assertEqual(portable(call, WIRE_CHAT).content, [])

    def test_text_and_human_messages_are_never_touched(self):
        plain = Msg("ai", "just text", "a1")
        person = human("hello", "h1")
        self.assertIs(portable(plain, WIRE_CHAT), plain)
        self.assertIs(portable(person, WIRE_CHAT), person)

    def test_the_guard_applies_the_wire_with_no_replay_mode(self):
        guard = ReplayGuard("", "grok-4.7", wire=WIRE_CHAT)
        out = guard.apply([human("wake", "h1"), claude_search_reply()])
        self.assertEqual(types(out[1]), ["text", "text"])

    def test_the_guard_applies_the_wire_with_a_replay_mode(self):
        guard = ReplayGuard(SIGNATURE, "gemini-4-carbon", wire=WIRE_GOOGLE)
        out = guard.apply([human("wake", "h1"), responses_reply()])
        self.assertEqual(types(out[1]), ["text", "text"])


def waiting_reply(id_, call="srv1", answers=None, model=MODEL):
    """A Claude reply that called one of our tools while a provider search was running."""
    content = []
    if answers:
        content.append({"type": "code_execution_tool_result", "tool_use_id": answers, "content": {}})
        content.append({"type": "web_search_tool_result", "tool_use_id": answers + "-ws",
                        "content": [{"type": "web_search_result", "url": "https://c.example", "title": "C"}]})
        content.append({"type": "server_tool_use", "id": answers + "-ws", "name": "web_search", "input": {}})
    content.append({"type": "thinking", "thinking": "…", "signature": "s-" + id_})
    if call:
        content.append({"type": "server_tool_use", "id": call, "name": "code_execution", "input": {}})
    content.append({"type": "tool_use", "id": "t-" + id_, "name": "add", "input": {}})
    return Msg("ai", content, id_, model=model,
               tool_calls=[{"id": "t-" + id_, "name": "add", "args": {}}])


def tool_result(id_):
    return Msg("tool", "5", "tr-" + id_, tool_call_id="t-" + id_)


def code_search_reply(id_="k1"):
    """A Claude reply whose search ran from a code step, whole, followed by one of our tools."""
    return Msg("ai", [
        {"type": "text", "text": "Looking."},
        {"type": "server_tool_use", "id": "code1", "name": "code_execution", "input": {}},
        {"type": "server_tool_use", "id": "ws1", "name": "web_search", "input": {}},
        {"type": "web_search_tool_result", "tool_use_id": "ws1",
         "content": [{"type": "web_search_result", "url": "https://d.example", "title": "D"}]},
        {"type": "code_execution_tool_result", "tool_use_id": "code1", "content": {}},
        {"type": "tool_use", "id": "t-" + id_, "name": "add", "input": {}},
    ], id_, model=MODEL, tool_calls=[{"id": "t-" + id_, "name": "add", "args": {}}])


class TestDropServerTools(unittest.TestCase):
    """After Anthropic refused a replayed server tool call (§2.7 B19)."""

    ERROR = ("Error code: 400 - messages.55: `code_execution` tool use with id `srvtoolu_x` was found "
             "without a corresponding `code_execution_tool_result` block")

    def test_the_refusal_is_recognized(self):
        self.assertTrue(is_server_tool_error(Exception(self.ERROR)))
        self.assertFalse(is_server_tool_error(Exception(
            "`tool_use` ids were found without `tool_result` blocks immediately after")))
        self.assertFalse(is_server_tool_error(Exception("Error code: 529 - overloaded")))

    def test_a_whole_pair_stays_until_the_provider_refuses(self):
        history = [human("wake", "h1"), code_search_reply(), tool_result("k1"), human("next", "h2")]
        guard = ReplayGuard("", MODEL, wire=WIRE_ANTHROPIC)
        self.assertEqual(types(guard.apply(history)[1]), types(history[1]))
        self.assertFalse(guard.server_tools_dropped)

    def test_after_a_refusal_completed_replies_go_as_text_note_and_tool_calls(self):
        history = [human("wake", "h1"), code_search_reply(), tool_result("k1"), human("next", "h2")]
        guard = ReplayGuard("", MODEL, wire=WIRE_ANTHROPIC)
        guard.drop_server_tools()
        self.assertTrue(guard.server_tools_dropped)
        out = guard.apply(history)
        self.assertEqual(types(out[1]), ["text", "text", "tool_use"])
        self.assertTrue(out[1].content[0]["text"].startswith(SEARCH_NOTE))
        self.assertIn("https://d.example", out[1].content[0]["text"])
        self.assertEqual(len(history[1].content), 6)  # the stored reply is untouched
        self.assertEqual(types(guard.apply(history)[1]), ["text", "text", "tool_use"])  # for the rest of the cycle

    def test_a_call_the_newest_reply_still_waits_for_stays(self):
        history = [human("wake", "h1"), code_search_reply(), tool_result("k1"),
                   waiting_reply("a2"), tool_result("a2")]
        out = settle_server_tools(history, every=True)
        self.assertEqual(types(out[1]), ["text", "text", "tool_use"])
        self.assertIn("server_tool_use", types(out[3]))

    def test_it_applies_to_anthropics_own_api_only(self):
        history = [human("wake", "h1"), code_search_reply(), tool_result("k1"), human("next", "h2")]
        guard = ReplayGuard("", MODEL, wire="")
        guard.drop_server_tools()
        self.assertIs(guard.apply(history), history)


class TestSplitServerTools(unittest.TestCase):
    """Anthropic: a search call in one reply and its result in the next (§2.7 B12)."""

    def test_nothing_split_nothing_changed(self):
        history = [human("wake", "h1"), claude_search_reply(), human("go", "h2")]
        self.assertIs(settle_server_tools(history), history)

    def test_the_newest_reply_may_still_wait_for_its_search(self):
        history = [human("wake", "h1"), waiting_reply("a1"), tool_result("a1")]
        out = settle_server_tools(history)
        self.assertIs(out[1], history[1])

    def test_a_split_pair_is_removed_once_the_reply_is_complete(self):
        history = [human("wake", "h1"), waiting_reply("a1"), tool_result("a1"),
                   waiting_reply("a2", call="", answers="srv1"), tool_result("a2"),
                   human("next", "h2")]
        out = settle_server_tools(history)
        self.assertEqual(types(out[1]), ["tool_use"])
        self.assertEqual(types(out[3]), ["text", "tool_use"])
        self.assertTrue(out[3].content[0]["text"].startswith(SEARCH_NOTE))
        self.assertIn("https://c.example", out[3].content[0]["text"])
        self.assertEqual(len(history[1].content), 3)  # the stored reply is untouched

    def test_in_a_chain_only_the_waiting_call_of_the_newest_reply_stays(self):
        history = [human("wake", "h1"), waiting_reply("a1"), tool_result("a1"),
                   waiting_reply("a2", call="srv2", answers="srv1"), tool_result("a2")]
        out = settle_server_tools(history)
        self.assertEqual(types(out[1]), ["tool_use"])
        self.assertEqual(types(out[3]), ["text", "server_tool_use", "tool_use"])
        self.assertEqual(out[3].content[1]["id"], "srv2")

    def test_a_waiting_reply_followed_by_a_person_is_complete(self):
        history = [human("wake", "h1"), waiting_reply("a1"), tool_result("a1"), human("go", "h2")]
        self.assertEqual(types(settle_server_tools(history)[1]), ["tool_use"])

    def test_the_guard_settles_only_on_anthropics_wire(self):
        history = [human("wake", "h1"), waiting_reply("a1"), tool_result("a1"), human("go", "h2")]
        on = ReplayGuard("", "claude-opus-4-8", wire=WIRE_ANTHROPIC).apply(list(history))
        self.assertEqual(types(on[1]), ["tool_use"])
        off = ReplayGuard("", "gpt-6-sol", wire=WIRE_RESPONSES).apply(list(history))
        self.assertEqual(types(off[1]), [])  # every part is Claude's own; the tool call still goes
        self.assertEqual(len(off[1].tool_calls), 1)

    def test_a_bound_model_sees_the_settled_reply_as_changed_history(self):
        log = []
        guard = ReplayGuard(BOUND, MODEL, log=log.append, wire=WIRE_ANTHROPIC)
        first = [human("wake", "h1"), waiting_reply("a1"), tool_result("a1")]
        guard.apply(first)  # new cycle
        later = first + [thinking_reply("a2", tool_calls=[{"id": "t-a2", "name": "add", "args": {}}]),
                         tool_result("a2")]
        out = guard.apply(later)
        self.assertEqual(kept(out), [])
        self.assertTrue(any("earlier history changed" in line for line in log))


class TestAnotherModelsRecord(unittest.TestCase):
    """A record another model wrote never goes, whatever the replay rule of the model called."""

    def test_no_replay_rule_still_leaves_another_models_record_out(self):
        guard = ReplayGuard("", "x-ai/grok-4.7", wire=WIRE_CHAT)
        own = Msg("ai", "mine", "a2", model="x-ai/grok-4.7",
                  extra={"reasoning_details": [{"type": "reasoning.encrypted", "data": "g"}]})
        other = Msg("ai", "theirs", "a1", model="anthropic/claude-opus-5.5",
                    extra={"reasoning_details": [{"type": "reasoning.text", "text": "plan"}]})
        out = guard.apply([human("wake", "h1"), other, own, human("go", "h2")])
        self.assertEqual(kept(out), ["a2"])
        self.assertTrue(has_reasoning(other))  # the stored reply is untouched

    def test_its_own_records_go_back_in_every_cycle(self):
        guard = ReplayGuard("", "claude-opus-4-8", wire=WIRE_ANTHROPIC)
        history = [human("wake", "h1"), thinking_reply("a1", model="claude-opus-4-8"),
                   human("go", "h2")]
        self.assertEqual(kept(guard.apply(history)), ["a1"])


if __name__ == "__main__":
    unittest.main()
