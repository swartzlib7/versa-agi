"""Browser command helpers (state_web_access.md §1.1, WEB-01..04)."""

import os
import sys
import unittest

CORE_INFRA = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, CORE_INFRA)

from agictl import browser_script as b  # noqa: E402

GOTO_BODY = (
    "with sync_playwright() as p:\n"
    "    browser, page = open_page(p, 'https://example.com/{x}')\n"
    "    emit(\"TITLE\", page.title())\n"
    "    emit(\"CONTENT\", page.locator('body').inner_text(timeout=TIMEOUT_MS))\n"
    "    browser.close()\n"
)


class TestParseOutput(unittest.TestCase):
    def test_whole_page_text_survives(self):
        """WEB-01: the old parser kept the first line of a multi-line page."""
        import json
        page = "Skip to content\nText to Speech\n\nOur models support 70 languages.\nPricing"
        out = "TITLE:" + json.dumps("Docs") + "\nCONTENT:" + json.dumps(page) + "\n"
        found = b.parse_output(out)
        self.assertEqual(found["title"], "Docs")
        self.assertEqual(found["content"], page)
        self.assertEqual(len(found["content"].splitlines()), 5)

    def test_page_text_that_looks_like_a_key_is_not_a_key(self):
        import json
        page = "Intro\nTITLE: not the title\nURL: also text"
        found = b.parse_output("TITLE:" + json.dumps("Real") + "\nCONTENT:" + json.dumps(page))
        self.assertEqual(found["title"], "Real")
        self.assertNotIn("url", found)
        self.assertIn("TITLE: not the title", found["content"])

    def test_extract_list_and_plain_values(self):
        found = b.parse_output('EXTRACT:["a", "b"]\nURL:"https://example.com/x?a=1:2"\nSCREENSHOT:"/tmp/s.png"')
        self.assertEqual(found["extract"], ["a", "b"])
        self.assertEqual(found["url"], "https://example.com/x?a=1:2")
        self.assertEqual(found["screenshot"], "/tmp/s.png")

    def test_unencoded_value_is_kept_as_text(self):
        found = b.parse_output("TITLE:Plain Title\nCONTENT:first line only")
        self.assertEqual(found["title"], "Plain Title")
        self.assertEqual(found["content"], "first line only")

    def test_noise_and_empty_output(self):
        self.assertEqual(b.parse_output(""), {})
        self.assertEqual(b.parse_output("warning without key\nOTHER:x"), {})


class TestPaging(unittest.TestCase):
    def test_short_text_is_whole(self):
        r = b.page_slice("hello", 0, 8000)
        self.assertEqual(r, {"content": "hello", "content_chars": 5, "offset": 0})

    def test_long_text_names_the_next_offset(self):
        text = "x" * 20000
        first = b.page_slice(text, 0, 8000)
        self.assertEqual(len(first["content"]), 8000)
        self.assertTrue(first["truncated"])
        self.assertEqual(first["next_offset"], 8000)
        self.assertEqual(first["content_chars"], 20000)
        last = b.page_slice(text, 16000, 8000)
        self.assertEqual(len(last["content"]), 4000)
        self.assertNotIn("truncated", last)

    def test_pages_join_back_to_the_text(self):
        text = "".join(str(i % 10) for i in range(10500))
        parts, offset = [], 0
        while True:
            r = b.page_slice(text, offset, 4000)
            parts.append(r["content"])
            if not r.get("truncated"):
                break
            offset = r["next_offset"]
        self.assertEqual("".join(parts), text)

    def test_limits(self):
        self.assertEqual(b.clamp_max_chars(10**9), b.MAX_CHARS_LIMIT)
        self.assertEqual(b.clamp_max_chars(0), 1)
        self.assertEqual(b.clamp_max_chars("x"), b.DEFAULT_MAX_CHARS)
        self.assertEqual(b.page_slice("abc", 99, 10)["content"], "")
        self.assertEqual(b.page_slice("abc", -5, 10)["offset"], 0)

    def test_tidy_text(self):
        self.assertEqual(b.tidy_text("a  \n\n\n\n\nb\r\nc\n\n"), "a\n\nb\nc")
        self.assertEqual(b.tidy_text(None), "")


class TestUserAgentAndSettle(unittest.TestCase):
    def test_user_agent_rules(self):
        self.assertEqual(b.user_agent_error(""), "")
        self.assertEqual(b.user_agent_error(None), "")
        self.assertEqual(b.user_agent_error("VersaResearch/1.0 (+contact)"), "")
        self.assertIn("single line", b.user_agent_error("a\nX-Injected: 1"))
        self.assertIn("at most", b.user_agent_error("x" * 301))

    def test_settle_ms(self):
        self.assertEqual(b.settle_ms("5", 30000), 5000)
        self.assertEqual(b.settle_ms("0", 30000), 0)
        self.assertEqual(b.settle_ms("-3", 30000), 0)
        self.assertEqual(b.settle_ms("120", 30000), 30000)
        self.assertEqual(b.settle_ms("abc", 30000), 5000)
        self.assertEqual(b.settle_ms(None, 30000), 5000)


class TestBuildScript(unittest.TestCase):
    def test_script_compiles_and_carries_the_settings(self):
        script = b.build_script(GOTO_BODY, user_agent="Agent's \"UA\" {1}", timeout_ms=12000, settle_wait_ms=2500)
        compile(script, "<browser>", "exec")
        self.assertIn("TIMEOUT_MS = 12000", script)
        self.assertIn("SETTLE_MS = 2500", script)
        self.assertIn(repr("Agent's \"UA\" {1}"), script)
        self.assertIn("browser.new_page(user_agent=agent)", script)

    def test_default_user_agent_is_not_headless(self):
        script = b.build_script(GOTO_BODY)
        self.assertIn("USER_AGENT = ''", script)
        self.assertIn("Chrome/", script)
        self.assertNotIn("HeadlessChrome", script)

    def test_every_page_settles_and_a_busy_page_is_not_an_error(self):
        script = b.build_script(GOTO_BODY)
        opener = script[script.index("def open_page"):script.index("def emit")]
        self.assertIn("settle(page)", opener)
        settle = script[script.index("def settle"):script.index("def open_page")]
        self.assertIn('wait_for_load_state("networkidle", timeout=SETTLE_MS)', settle)
        self.assertIn("except Exception", settle)
        self.assertIn("if SETTLE_MS <= 0", settle)

    def test_prelude_emit_round_trips_through_the_parser(self):
        import contextlib
        import io
        import json
        scope = {"json": json}
        prelude = b.build_script("")
        exec(prelude[prelude.index("def emit"):], scope)  # emit only: no Playwright needed
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            scope["emit"]("TITLE", "A: title")
            scope["emit"]("CONTENT", "line 1\nline 2\nCONTENT: line 3")
        found = b.parse_output(buf.getvalue())
        self.assertEqual(found["title"], "A: title")
        self.assertEqual(found["content"], "line 1\nline 2\nCONTENT: line 3")


class TestCommandWiring(unittest.TestCase):
    """The commands use the shared prelude and expose the new options."""

    def setUp(self):
        with open(os.path.join(CORE_INFRA, "agictl", "cli.py"), encoding="utf-8") as handle:
            self.text = handle.read()
        start = self.text.index('@browser.command("goto")')
        self.block = self.text[start:self.text.index('@browser.command("enable")')]

    def test_no_command_parses_content_by_line_prefix(self):
        self.assertNotIn('line.startswith("CONTENT:")', self.text)
        self.assertNotIn("text[:8000]", self.text)

    def test_every_command_takes_a_user_agent(self):
        self.assertEqual(self.block.count('"--user-agent"'), 5)
        self.assertEqual(self.block.count("_browser_setup(user_agent)"), 5)
        self.assertEqual(self.block.count("open_page(p, "), 5)

    def test_goto_pages(self):
        self.assertIn('"--offset"', self.block)
        self.assertIn('"--max-chars"', self.block)
        self.assertIn("browser_script.page_slice(", self.block)


if __name__ == "__main__":
    unittest.main()
