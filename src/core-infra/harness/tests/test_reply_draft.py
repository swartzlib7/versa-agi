"""The reply as the model writes it (state_realtime_link.md §2.2).

Run:  cd core-infra
      python3 -m unittest harness.tests.test_reply_draft
"""

from __future__ import annotations

import json
import os
import shlex
import sys
import tempfile
import unittest

CORE_INFRA = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, CORE_INFRA)

from harness import reply_draft as rd  # noqa: E402

UID = "pu_7Hq2abcDEF"


def args_json(command: str) -> str:
    return json.dumps({"command": command})


class TestPartialJson(unittest.TestCase):
    def test_nothing_until_the_value_starts(self):
        for buffer in ("", "{", '{"comm', '{"command"', '{"command": '):
            self.assertIsNone(rd.partial_json_string(buffer))
        self.assertEqual(rd.partial_json_string('{"command": "'), ("", False))

    def test_every_prefix_of_a_real_argument_decodes_to_a_prefix_of_the_value(self):
        command = 'message send %s "She said \\"hi\\" — naïve café, 日本語, tab\there\nnew line 😀" --mode typed' % UID
        whole = args_json(command)
        last = ""
        for cut in range(len(whole) + 1):
            got = rd.partial_json_string(whole[:cut])
            if got is None:
                continue
            value, closed = got
            self.assertTrue(command.startswith(value), (cut, value))
            self.assertTrue(value.startswith(last) or last.startswith(value))
            last = value
        self.assertEqual(rd.partial_json_string(whole), (command, True))

    def test_an_escape_cut_in_half_waits_for_the_rest(self):
        self.assertEqual(rd.partial_json_string('{"command": "a\\'), ("a", False))
        self.assertEqual(rd.partial_json_string('{"command": "a\\u00e'), ("a", False))
        self.assertEqual(rd.partial_json_string('{"command": "a\\u00e9'), ("aé", False))
        self.assertEqual(rd.partial_json_string('{"command": "a\\ud83d'), ("a", False))
        self.assertEqual(rd.partial_json_string('{"command": "a\\ud83d\\ude00b'), ("a😀b", False))

    def test_other_json_shapes_do_not_raise(self):
        for buffer in ('{"command": 5}', '{"other": "x"}', '[]', '{"command": "\\ud83d\\u0041"', '{"command": "\\uZZZZ"'):
            rd.partial_json_string(buffer)


class TestPartialSend(unittest.TestCase):
    def test_a_send_gives_recipient_and_text_as_it_grows(self):
        command = f'message send {UID} "Good morning. The report is ready." --mode typed'
        text = "Good morning. The report is ready."
        seen = []
        for cut in range(len(command) + 1):
            draft = rd.partial_send(command[:cut])
            if draft is None:
                continue
            self.assertEqual(draft.to, UID)
            self.assertTrue(text.startswith(draft.text), (cut, draft.text))
            seen.append(draft.text)
        self.assertEqual(seen[0], "G")
        self.assertEqual(seen[-1], text)
        self.assertEqual(seen, sorted(seen, key=len))

    def test_nothing_before_the_text_starts(self):
        for command in ("", "mess", "message", "message send", f"message send {UID[:5]}", f"message send {UID}",
                        f"message send {UID} ", f'message send {UID} "'):
            self.assertIsNone(rd.partial_send(command), command)

    def test_the_finished_text_is_what_the_shell_would_pass(self):
        for text in ('He said "yes" and left.', "It's fine — don't worry.", "Cost: $5 `not code` \\ done",
                     "two\nlines", "--not an option"):
            command = f"message send {UID} {shlex.quote(text)} --mode typed"
            self.assertEqual(shlex.split(command)[3], text)
            draft = rd.partial_send(command, closed=True)
            self.assertEqual((draft.to, draft.text, draft.complete), (UID, text, True), command)
            double = 'message send %s "%s"' % (UID, text.replace("\\", "\\\\").replace('"', '\\"'))
            self.assertEqual(shlex.split(double)[3], text)
            self.assertEqual(rd.partial_send(double, closed=True).text, text)

    def test_options_before_the_text_are_skipped_with_their_values(self):
        draft = rd.partial_send(f'message send --mode typed {UID} --markdown workspace/a.md "Here it is', closed=False)
        self.assertEqual((draft.to, draft.text, draft.complete), (UID, "Here it is", False))
        draft = rd.partial_send(f'message send --mode=speak {UID} "Hello')
        self.assertEqual(draft.text, "Hello")
        self.assertIsNone(rd.partial_send(f'message send {UID} --mo'))

    def test_send_without_the_leading_word(self):
        self.assertEqual(rd.partial_send(f'send {UID} "Hi').text, "Hi")

    def test_other_commands_give_nothing(self):
        for command in (f'message get {UID} --unread', 'message internal clerk "note for you"',
                        f'message mark-processed {UID}', f'message sendx {UID} "x"', f'task add "send {UID} hi"',
                        'message send "not an id!" "text"', f'message send {UID} "   '):
            self.assertIsNone(rd.partial_send(command, closed=True), command)

    def test_a_long_text_is_cut(self):
        draft = rd.partial_send(f'message send {UID} "{"x" * 20000}')
        self.assertEqual(len(draft.text), rd.MAX_TEXT_CHARS)

    def test_no_prefix_of_odd_input_raises(self):
        for command in ('message send \\', "message send 'abc", 'message send a "b\\', 'message send a b c d', '   ', '""'):
            for cut in range(len(command) + 1):
                rd.partial_send(command[:cut])
                rd.partial_send(command[:cut], closed=True)


class Clock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


class TestReplyDraft(unittest.TestCase):
    def setUp(self):
        self.records = []
        self.clock = Clock()
        self.draft = rd.ReplyDraft("/nowhere/reply_draft", "coa-1", write=lambda path, record: self.records.append(record),
                                   clock=self.clock, wall=lambda: 1795000000.0)

    def feed(self, command: str, index=1, piece=7, step=0.0):
        whole = args_json(command)
        self.draft.tool_chunk(index, rd.TOOL_NAME, "")
        for start in range(0, len(whole), piece):
            self.clock.now += step
            self.draft.tool_chunk(index, None, whole[start:start + piece])

    def test_the_text_is_written_as_it_grows_and_once_more_at_the_end(self):
        self.draft.start_call()
        self.feed(f'message send {UID} "Good morning. The report is ready and attached." --mode typed', step=0.1)
        self.draft.end_call()
        texts = [record["text"] for record in self.records]
        self.assertGreater(len(texts), 2)
        self.assertEqual(texts[-1], "Good morning. The report is ready and attached.")
        self.assertEqual(texts, sorted(texts, key=len))
        self.assertTrue(all(record["to"] == UID and not record["done"] and record["cycle"] == "coa-1" for record in self.records))
        self.assertEqual([record["seq"] for record in self.records], list(range(1, len(self.records) + 1)))
        self.assertEqual(len({record["draft"] for record in self.records}), 1)

    def test_writes_are_at_most_one_per_gap(self):
        self.draft.start_call()
        self.feed(f'message send {UID} "{"word " * 200}"', piece=3, step=0.01)
        self.draft.end_call()
        elapsed = self.clock.now - 100.0
        self.assertLessEqual(len(self.records), int(elapsed / rd.MIN_WRITE_GAP_SECONDS) + 2)

    def test_finish_marks_the_draft_done_once(self):
        self.draft.start_call()
        self.feed(f'message send {UID} "Hello"')
        self.draft.end_call()
        self.draft.finish()
        self.draft.finish()
        self.assertEqual([record["done"] for record in self.records][-2:], [False, True])
        self.assertEqual(self.records[-1]["text"], "")
        self.assertEqual(sum(record["done"] for record in self.records), 1)

    def test_finish_with_nothing_written_writes_nothing(self):
        self.draft.start_call()
        self.draft.tool_chunk(0, "agictl_task", '{"command": "task list"}')
        self.draft.end_call()
        self.draft.finish()
        self.assertEqual(self.records, [])

    def test_other_tools_and_other_message_commands_are_ignored(self):
        self.draft.start_call()
        self.draft.tool_chunk(0, "agictl_execute", args_json(f'message send {UID} "not through this tool"'))
        self.draft.tool_chunk(1, rd.TOOL_NAME, args_json(f'message get {UID} --unread'))
        self.draft.end_call()
        self.assertEqual(self.records, [])

    def test_a_second_message_in_the_same_step_is_a_new_draft(self):
        self.draft.start_call()
        self.feed(f'message send {UID} "First."', index=1)
        self.clock.now += 1
        self.feed('message send other_Uid9 "Second."', index=2)
        self.draft.end_call()
        first = [record for record in self.records if record["to"] == UID]
        second = [record for record in self.records if record["to"] == "other_Uid9"]
        self.assertTrue(first and second)
        self.assertNotEqual(first[0]["draft"], second[0]["draft"])

    def test_a_retried_model_call_starts_the_text_again(self):
        self.draft.start_call()
        self.feed(f'message send {UID} "First attempt that was cut', piece=200)
        self.draft.start_call()
        self.clock.now += 1
        self.feed(f'message send {UID} "Second attempt."', piece=200)
        self.draft.end_call()
        self.assertEqual(self.records[-1]["text"], "Second attempt.")

    def test_a_write_that_fails_never_raises(self):
        def broken(path, record):
            raise OSError("disk full")
        draft = rd.ReplyDraft("/nowhere/x", write=broken)
        draft.start_call()
        draft.tool_chunk(1, rd.TOOL_NAME, args_json(f'message send {UID} "Hello"'))
        draft.end_call()
        draft.finish()
        self.assertGreater(draft.failed, 0)

    def test_the_file_is_replaced_whole(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "reply_draft")
            draft = rd.ReplyDraft(path, "coa-9")
            draft.start_call()
            draft.tool_chunk(1, rd.TOOL_NAME, args_json(f'message send {UID} "Hello there"'))
            draft.end_call()
            with open(path, encoding="utf-8") as handle:
                record = json.load(handle)
            self.assertEqual((record["to"], record["text"], record["done"], record["v"]), (UID, "Hello there", False, 1))
            self.assertEqual(os.listdir(tmp), ["reply_draft"])
            draft.finish()
            with open(path, encoding="utf-8") as handle:
                self.assertTrue(json.load(handle)["done"])

    def test_no_file_from_lifeline_means_no_draft(self):
        old = os.environ.pop("VERSA_REPLY_DRAFT_FILE", None)
        try:
            self.assertIsNone(rd.from_environment("coa-1"))
            os.environ["VERSA_REPLY_DRAFT_FILE"] = "/no/such/folder/reply_draft"
            self.assertIsNone(rd.from_environment("coa-1"))
        finally:
            os.environ.pop("VERSA_REPLY_DRAFT_FILE", None)
            if old is not None:
                os.environ["VERSA_REPLY_DRAFT_FILE"] = old


class TestCallback(unittest.TestCase):
    """With the real LangChain message types (the harness Python)."""

    def setUp(self):
        try:
            from langchain_core.messages import AIMessageChunk
            from langchain_core.outputs import ChatGenerationChunk
        except ImportError as error:
            self.skipTest(f"langchain_core is not in this interpreter: {error}")
        self.AIMessageChunk, self.ChatGenerationChunk = AIMessageChunk, ChatGenerationChunk
        self.records = []
        self.draft = rd.ReplyDraft("/nowhere/x", write=lambda path, record: self.records.append(record), min_gap=0)
        self.callback = rd.callback_for(self.draft)

    def piece(self, index, name, args, text=""):
        message = self.AIMessageChunk(content=text, tool_call_chunks=[
            {"name": name, "args": args, "id": "toolu_1" if name else None, "index": index, "type": "tool_call_chunk"}])
        return self.ChatGenerationChunk(message=message)

    def test_tool_input_pieces_become_the_draft(self):
        whole = args_json(f'message send {UID} "Hello from the stream." --mode typed')
        self.callback.on_chat_model_start({}, [[]])
        self.callback.on_llm_new_token("I will answer now.", chunk=self.ChatGenerationChunk(
            message=self.AIMessageChunk(content="I will answer now.")))
        self.callback.on_llm_new_token("", chunk=self.piece(1, rd.TOOL_NAME, ""))
        for start in range(0, len(whole), 9):
            self.callback.on_llm_new_token("", chunk=self.piece(1, None, whole[start:start + 9]))
        self.callback.on_llm_end(None)
        self.assertEqual(self.records[-1]["text"], "Hello from the stream.")
        self.assertNotIn("I will answer now.", " ".join(record["text"] for record in self.records))

    def test_a_token_without_a_chunk_is_ignored(self):
        self.callback.on_llm_new_token("plain")
        self.callback.on_llm_new_token("plain", chunk=object())
        self.callback.on_llm_error(RuntimeError("x"))
        self.assertEqual(self.records, [])


class TestWiring(unittest.TestCase):
    def test_the_cycle_model_gets_the_callback_and_the_tool_step_closes_the_draft(self):
        with open(os.path.join(CORE_INFRA, "harness", "agent_harness.py"), encoding="utf-8") as handle:
            source = handle.read()
        self.assertIn("reply_draft = reply_draft_mod.from_environment(cycle_id)", source)
        self.assertIn("cycle_callbacks.append(reply_draft_mod.callback_for(reply_draft))", source)
        self.assertIn("extra_callbacks=cycle_callbacks)", source)
        tools = source.index('elif "tools" in chunk:')
        self.assertIn("reply_draft.finish()", source[tools:tools + 200])

    def test_the_tool_this_follows_is_the_harness_tool(self):
        with open(os.path.join(CORE_INFRA, "harness", "agent_harness.py"), encoding="utf-8") as handle:
            source = handle.read()
        self.assertIn(f'@tool("{rd.TOOL_NAME}", args_schema=MessageInput)', source)
        self.assertIn("command: str = Field(", source)


if __name__ == "__main__":
    unittest.main()
