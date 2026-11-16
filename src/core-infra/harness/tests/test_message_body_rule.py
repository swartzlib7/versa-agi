"""What agents are told about the text of a message, and the flags they are told to use
(state_message_body.md).

Read from source text, so these run without click or langchain installed.
"""

from __future__ import annotations

import ast
import glob
import os
import re
import unittest

CORE_INFRA = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SKILLS = os.path.join(CORE_INFRA, "skills")
BASIC = os.path.join(SKILLS, "communication_basic.md")
FULL = os.path.join(SKILLS, "communication.md")
CLI = os.path.join(CORE_INFRA, "agictl", "cli.py")
HARNESS = os.path.join(CORE_INFRA, "harness", "agent_harness.py")
LIFELINE = os.path.join(CORE_INFRA, "lifeline.sh")
OLD_FLAG = "--markdown-paths"


def _read(path: str) -> str:
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def _send_command_source() -> str:
    """The decorators and body of `message send` in agictl/cli.py."""
    text = _read(CLI)
    start = text.index('@message.command("send")')
    return text[start:text.index("@message.command(", start + 1)]


def _send_options() -> dict[str, bool]:
    """Option name -> hidden, for every option `message send` defines."""
    options = {}
    for line in _send_command_source().split("\n"):
        if line.startswith("@click.option("):
            for name in re.findall(r'"(--[a-z-]+)"', line.split("help=")[0]):
                options[name] = "hidden=True" in line
    return options


def _shipped_texts() -> dict[str, str]:
    """Every shipped skill, prompt file and the Lifeline system prompt."""
    paths = glob.glob(os.path.join(SKILLS, "*.md"))
    paths += glob.glob(os.path.join(CORE_INFRA, "config", "**", "*.md"), recursive=True)
    paths.append(LIFELINE)
    return {os.path.relpath(path, CORE_INFRA): _read(path) for path in paths}


def _send_commands(text: str) -> list[str]:
    """Each `agictl message send …` command in a text, with `\\` line continuations joined."""
    joined = re.sub(r"\\\n\s*", " ", text)
    return [line[line.index("message send"):] for line in joined.split("\n") if "agictl message send" in line]


class TestFlagsNamedInShippedText(unittest.TestCase):
    def test_send_defines_the_attachment_flags(self):
        options = _send_options()
        for flag in ("--media", "--file", "--markdown", "--url"):
            self.assertIn(flag, options)
            self.assertFalse(options[flag], f"{flag} must show in help")

    def test_no_shipped_text_names_the_old_flag(self):
        for name, text in _shipped_texts().items():
            self.assertNotIn(OLD_FLAG, text, f"{name} names {OLD_FLAG}; the flag is --markdown")

    def test_every_flag_on_a_send_command_exists(self):
        options = set(_send_options())
        seen = 0
        for name, text in _shipped_texts().items():
            if not name.endswith(".md"):
                continue  # Lifeline's own shell commands run on into other commands
            for command in _send_commands(text):
                # A send command ends where the line's code ends: a backtick, a pipe or a comment.
                command = re.split(r"[`|#]", command)[0]
                for flag in re.findall(r"(?<![\w-])--[a-z][a-z-]*", command):
                    seen += 1
                    self.assertIn(flag, options, f"{name}: `message send` has no {flag}")
        self.assertGreater(seen, 10, "the scan must find the send examples in the skills")


class TestOldSpellingStillWorks(unittest.TestCase):
    def test_old_flag_is_a_hidden_option(self):
        options = _send_options()
        self.assertIn(OLD_FLAG, options)
        self.assertTrue(options[OLD_FLAG], "the old spelling must not show in help")

    def test_old_flag_values_join_the_markdown_attachments(self):
        source = _send_command_source()
        self.assertIn("markdown_paths = tuple(markdown_paths) + tuple(markdown_paths_old)", source)
        merge = source.index("markdown_paths = tuple(markdown_paths)")
        self.assertLess(merge, source.index("_is_vv_enabled()"), "joined before either send route reads them")


class TestAlwaysPresentRule(unittest.TestCase):
    def setUp(self):
        text = _read(BASIC)
        start = text.index("## How to Send Messages")
        self.section = text[start:text.index("## How to Read Messages")]

    def test_rule_sits_where_the_send_command_is_taught(self):
        self.assertIn("Code never goes in it", self.section)
        self.assertIn("typed included", self.section)

    def test_rule_names_the_real_flags_and_shows_a_send_with_a_file(self):
        self.assertIn("`--markdown <path>`", self.section)
        self.assertIn("`--url <link>`", self.section)
        commands = _send_commands(self.section)
        self.assertTrue(any("--markdown " in command for command in commands), commands)

    def test_rule_gives_the_reason_for_typed_messages(self):
        self.assertIn("cannot copy it cleanly", self.section)
        self.assertIn("select, copy and save", self.section)


class TestFullSkill(unittest.TestCase):
    def test_reason_covers_typed_messages(self):
        text = _read(FULL)
        self.assertIn("It holds for typed messages as much as spoken ones.", text)

    def test_links_are_never_inline(self):
        text = _read(FULL)
        self.assertNotIn("include URLs inline", text)
        self.assertIn("attach links with `--url`; never write them in the message text", text)


class TestMessageTool(unittest.TestCase):
    """The model reads the tool description at the point of sending."""

    def setUp(self):
        tree = ast.parse(_read(HARNESS))
        self.docstring = ""
        self.field = ""
        for node in tree.body:
            if isinstance(node, ast.FunctionDef) and node.name == "agictl_message":
                self.docstring = ast.get_docstring(node) or ""
            if isinstance(node, ast.ClassDef) and node.name == "MessageInput":
                self.field = ast.unparse(node)

    def test_tool_description_states_the_rule_and_shows_an_attachment(self):
        self.assertIn("never go in the text, typed or spoken", self.docstring)
        self.assertIn("--markdown workspace/script.md", self.docstring)
        self.assertNotIn(OLD_FLAG, self.docstring)

    def test_argument_description_states_the_rule_and_shows_an_attachment(self):
        self.assertIn("never put code, commands, logs or", self.field)
        self.assertIn("--markdown <path>", self.field)
        self.assertIn("--markdown workspace/script.md", self.field)


class TestLifelinePrompt(unittest.TestCase):
    def test_per_cycle_prompt_names_the_real_flag(self):
        line = [line for line in _read(LIFELINE).split("\n") if "**Message addressing**" in line]
        self.assertEqual(len(line), 1)
        self.assertIn("--markdown <path>", line[0])
        self.assertIn("never in the message text", line[0])


if __name__ == "__main__":
    unittest.main()
