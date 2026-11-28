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
        self.assertEqual(self.block.count('"--user-agent"'), 6)   # goto, click, fill, screenshot, extract, run
        self.assertEqual(self.block.count("_browser_setup(user_agent, session)"), 6)
        self.assertEqual(self.block.count("open_page(p, "), 5)                # `run` builds its page in run_body

    def test_goto_pages(self):
        self.assertIn('"--offset"', self.block)
        self.assertIn('"--max-chars"', self.block)
        self.assertIn("browser_script.page_slice(", self.block)


class TestSessionsAndSteps(unittest.TestCase):
    """WEB-14: a named session, and several steps on one page."""

    def test_session_names(self):
        for name in ("shop", "git-hub", "bank_2", "a"):
            self.assertEqual(b.session_error(name), "", name)
        self.assertEqual(b.session_error(""), "")            # no session
        for name in ("My Shop", "../x", "-shop", "a/b", "x" * 41, "Shop"):
            self.assertIn("session name", b.session_error(name), name)

    def test_steps_are_checked_before_a_browser_starts(self):
        import json
        steps, err = b.parse_steps(json.dumps([
            {"do": "fill", "selector": "#user", "value": "me", "extra": 1},
            {"do": "press", "selector": "#user", "key": "Enter"},
            {"do": "goto", "url": "https://example.com/next"},
        ]))
        self.assertEqual(err, "")
        self.assertEqual(steps[0], {"do": "fill", "selector": "#user", "value": "me"})   # unknown fields dropped
        for bad, part in (
            ("not json", "not valid JSON"),
            ("{}", "JSON list"),
            ("[]", "JSON list"),
            ('["click"]', "must be an object"),
            ('[{"do":"zap","selector":"a"}]', '"do" must be one of'),
            ('[{"do":"click"}]', 'needs "selector"'),
            ('[{"do":"click","selector":"  "}]', 'needs "selector"'),
            ('[{"do":"fill","selector":"#a"}]', 'needs "value"'),
            ('[{"do":"goto","url":"file:///etc/passwd"}]', "only http:// and https://"),
            (json.dumps([{"do": "click", "selector": "a"}] * (b.MAX_STEPS + 1)), "at most"),
        ):
            steps, err = b.parse_steps(bad)
            self.assertEqual(steps, [], bad)
            self.assertIn(part, err, bad)
        # An empty value is a real value: it clears a field.
        self.assertEqual(b.parse_steps('[{"do":"fill","selector":"#a","value":""}]')[1], "")

    def test_the_run_script_compiles_whatever_the_page_and_steps_hold(self):
        steps, _ = b.parse_steps('[{"do":"fill","selector":"input[name=\\"q\\"]","value":"it\'s {braces} \\\\ \\n"}]')
        src = b.build_script(b.run_body("https://example.com/{x}'\"", steps, screenshot_dir="/tmp/s's"),
                             session="shop", fallback_user_agent="UA 'x'")
        compile(src, "<run>", "exec")
        self.assertIn("SESSION = 'shop'", src)
        self.assertIn("run_steps(page, json.loads(", src)
        self.assertIn("finish(browser, page)", src)

    def test_a_command_without_a_session_keeps_nothing(self):
        src = b.build_script(GOTO_BODY)
        self.assertIn("SESSION = ''", src)
        compile(src, "<goto>", "exec")

    def test_every_command_ends_through_finish_so_a_session_is_kept(self):
        with open(os.path.join(CORE_INFRA, "agictl", "cli.py"), encoding="utf-8") as handle:
            cli = handle.read()
        start = cli.index('@browser.command("goto")')
        group = cli[start:cli.index('@browser.command("enable")')]
        self.assertNotIn("browser.close()", group)
        self.assertEqual(group.count("finish(browser, page)"), 5)       # goto, click, fill, screenshot, extract
        self.assertEqual(group.count('"--session", "session"'), 6)      # those five and run

    def test_session_output_keys_are_read(self):
        out = 'SESSION:"shop"\nSTEPS_DONE:1\nSTEPS_DONE:2\nSTEP_FAILED:{"step": 3, "do": "click", "error": "x"}\nSESSION_RESET:true\n'
        found = b.parse_output(out)
        self.assertEqual(found["session"], "shop")
        self.assertEqual(found["steps_done"], 2)
        self.assertEqual(found["step_failed"]["step"], 3)
        self.assertTrue(found["session_reset"])


def _chromium_for_this_account() -> bool:
    try:
        import playwright  # noqa: F401
    except ImportError:
        return False
    where = os.environ.get("PLAYWRIGHT_BROWSERS_PATH") or os.path.expanduser("~/.cache/ms-playwright")
    return os.path.isdir(where) and any(n.startswith("chromium") for n in os.listdir(where))


@unittest.skipUnless(_chromium_for_this_account(), "no Playwright Chromium for this account (agents have their own)")
class TestLiveSession(unittest.TestCase):
    """The real thing: a sign-in on a local page, kept from one command to the next.

    The page signs in with a cookie that has no end date, the kind a browser
    drops when it closes. That is the case a kept profile folder would lose.
    """

    @classmethod
    def setUpClass(cls):
        import http.server
        import tempfile
        import threading
        import urllib.parse

        class Site(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _send(self, body, code=200, headers=()):
                self.send_response(code)
                for key, value in headers:
                    self.send_header(key, value)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(body.encode())

            def do_GET(self):
                if self.path.startswith("/login"):
                    self._send('<form method="post" action="/login"><input id="user" name="user">'
                               '<input id="pass" name="pass" type="password">'
                               '<button type="submit">Go</button></form>')
                    return
                who = ""
                for part in (self.headers.get("Cookie") or "").split(";"):
                    key, _, value = part.strip().partition("=")
                    if key == "sid":
                        who = value
                self._send(f"<p>{'Signed in as ' + who if who else 'Not signed in'}</p>")

            def do_POST(self):
                data = urllib.parse.parse_qs(self.rfile.read(int(self.headers.get("Content-Length", 0))).decode())
                user = (data.get("user") or [""])[0]
                if (data.get("pass") or [""])[0] == "secret" and user:
                    self._send("", 303, [("Set-Cookie", f"sid={user}; Path=/; HttpOnly"), ("Location", "/home")])
                else:
                    self._send("<p>Wrong password</p>", 401)

        cls.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Site)
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.tmp = tempfile.TemporaryDirectory()
        cls.root = os.path.join(cls.tmp.name, "sessions")

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.tmp.cleanup()

    def run_script(self, body, **kw):
        import subprocess
        src = b.build_script(body, timeout_ms=15000, settle_wait_ms=1000, session_root=self.root, **kw)
        # The prelude adds the deployed harness venv to the path; here the test's own interpreter has Playwright.
        path = os.path.join(self.tmp.name, "script.py")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(src)
        out = subprocess.run([sys.executable, path], capture_output=True, text=True, timeout=120)
        self.assertEqual(out.returncode, 0, out.stderr[-600:])
        return b.parse_output(out.stdout)

    def home(self, **kw):
        body = ("with sync_playwright() as p:\n"
                f"    browser, page = open_page(p, {self.base + '/home'!r})\n"
                "    emit('CONTENT', page.locator('body').inner_text(timeout=TIMEOUT_MS))\n"
                "    finish(browser, page)\n")
        return self.run_script(body, **kw)

    def test_a_sign_in_is_kept_for_the_session_and_for_no_other(self):
        import json
        steps, err = b.parse_steps(json.dumps([
            {"do": "fill", "selector": "#user", "value": "dana"},
            {"do": "fill", "selector": "#pass", "value": "secret"},
            {"do": "click", "selector": "button[type=submit]"},
        ]))
        self.assertEqual(err, "")
        found = self.run_script(b.run_body(self.base + "/login", steps), session="shop")
        self.assertEqual(found["steps_done"], 3)
        self.assertTrue(found["url"].endswith("/home"))
        self.assertIn("Signed in as dana", found["content"])

        self.assertIn("Signed in as dana", self.home(session="shop")["content"])     # a later command
        self.assertIn("Not signed in", self.home()["content"])                       # no session: clean browser
        self.assertIn("Not signed in", self.home(session="other")["content"])        # another session

        folder = os.path.join(self.root, "shop")
        self.assertEqual(os.stat(folder).st_mode & 0o777, 0o700)
        self.assertEqual(os.stat(os.path.join(folder, "state.json")).st_mode & 0o777, 0o600)
        with open(os.path.join(folder, "user_agent"), encoding="utf-8") as handle:
            self.assertNotIn("Headless", handle.read())

    def test_a_step_that_fails_is_named_and_the_session_is_left_as_it_was(self):
        steps, _ = b.parse_steps('[{"do":"fill","selector":"#user","value":"x"},{"do":"click","selector":"#missing"}]')
        src_kw = {"session": "half"}
        import subprocess
        src = b.build_script(b.run_body(self.base + "/login", steps), timeout_ms=3000, settle_wait_ms=500,
                             session_root=self.root, **src_kw)
        path = os.path.join(self.tmp.name, "fail.py")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(src)
        out = subprocess.run([sys.executable, path], capture_output=True, text=True, timeout=120)
        found = b.parse_output(out.stdout)
        self.assertEqual(found["steps_done"], 1)
        self.assertEqual(found["step_failed"]["step"], 2)
        self.assertEqual(found["step_failed"]["do"], "click")
        self.assertFalse(os.path.exists(os.path.join(self.root, "half", "state.json")))

    def test_a_state_file_that_cannot_be_read_starts_the_session_again(self):
        folder = os.path.join(self.root, "broken")
        os.makedirs(folder)
        with open(os.path.join(folder, "state.json"), "w", encoding="utf-8") as handle:
            handle.write("{not json")
        found = self.home(session="broken")
        self.assertTrue(found.get("session_reset"))
        self.assertIn("Not signed in", found["content"])


if __name__ == "__main__":
    unittest.main()
