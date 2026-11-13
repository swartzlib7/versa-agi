"""`agictl search read`, `search status`, search API keys (state_web_access.md §1.2 rules 8–9, §1.3)."""

import io
import json
import os
import stat
import sys
import tempfile
import unittest
import urllib.error
from unittest.mock import patch

CORE_INFRA = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, CORE_INFRA)
sys.path.insert(0, os.path.join(CORE_INFRA, "agictl"))

from agictl import page_text  # noqa: E402

try:
    from click.testing import CliRunner
    import agictl.cli as agictl_cli  # noqa: E402
except ImportError:  # harness venv has no click
    agictl_cli = None

PAGE = """<!doctype html>
<html><head><title>  Speech  Synthesis
 Markup </title><style>p{color:red}</style>
<script>var hidden = "do not show";</script><meta name="description" content="meta text"></head>
<body>
<nav><a href="/home">Home</a> <a href="#top">Top</a> <a href="mailto:a@example.org">Mail</a></nav>
<h1>Phoneme element</h1>
<p>The <code>phoneme</code> element gives a
   pronunciation.&nbsp;See <a href="https://www.w3.org/TR/ssml/">the spec</a>.</p>
<p>Use it when a word is read wrongly: a name, a place, or a word from another language. Give the
sounds in a phonetic alphabet, and keep the written word inside the element so a reader still sees it.</p>
<pre>&lt;phoneme alphabet="ipa"
         ph="təˈmeɪ.toʊ"&gt;tomato&lt;/phoneme&gt;</pre>
<ul><li>alphabet</li><li>ph &amp; more</li></ul>
<table><tr><th>Name</th><th>Use</th></tr><tr><td>ipa</td><td>IPA symbols</td></tr></table>
<noscript>Enable JavaScript</noscript>
<img src="x.png" alt="Diagram of a phoneme">
<script>document.write("never shown")</script>
</body></html>"""


class _Reply(io.BytesIO):
    def __init__(self, body: bytes, content_type="text/html; charset=utf-8", url="https://example.org/final"):
        super().__init__(body)
        self.headers = {"Content-Type": content_type}
        self._url = url

    def geturl(self):
        return self._url

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class TestHtmlToText(unittest.TestCase):
    def setUp(self):
        self.page = page_text.html_to_text(PAGE, "https://example.org/docs/ssml")

    def test_title_and_text(self):
        self.assertEqual(self.page["title"], "Speech Synthesis Markup")
        text = self.page["text"]
        self.assertIn("Phoneme element", text)
        self.assertIn("The phoneme element gives a pronunciation. See the spec.", text)
        self.assertIn("- alphabet\n- ph & more", text)
        self.assertIn("Name | Use\nipa | IPA symbols", text)
        self.assertIn("[image: Diagram of a phoneme]", text)
        # Source line breaks inside a paragraph are spaces; <pre> keeps its lines.
        self.assertIn("Give the sounds in a phonetic alphabet", text)
        self.assertIn('<phoneme alphabet="ipa"\n         ph="t', text)
        self.assertNotIn("\x00", text)

    def test_scripts_styles_and_head_are_dropped(self):
        text = self.page["text"]
        for hidden in ("do not show", "never shown", "color:red", "Enable JavaScript", "meta text",
                       "Speech Synthesis Markup"):
            self.assertNotIn(hidden, text)
        self.assertEqual(self.page["scripts"], 2)

    def test_blocks_start_lines(self):
        lines = self.page["text"].split("\n")
        self.assertIn("Phoneme element", lines)
        self.assertNotIn("", [ln for ln in lines if ln.startswith(" ")])
        self.assertNotIn("\n\n\n", self.page["text"])

    def test_links_are_absolute_and_page_only_links_are_left_out(self):
        self.assertEqual(self.page["links"], [
            {"text": "Home", "url": "https://example.org/home"},
            {"text": "the spec", "url": "https://www.w3.org/TR/ssml/"},
        ])

    def test_links_are_unique_and_capped(self):
        html = "".join(f'<a href="/p{i % 150}">x</a>' for i in range(400))
        links = page_text.html_to_text(html, "https://example.org")["links"]
        self.assertEqual(len(links), page_text.MAX_LINKS)
        self.assertEqual(len({ln["url"] for ln in links}), page_text.MAX_LINKS)

    def test_broken_html_still_gives_text(self):
        page = page_text.html_to_text("<p>one<div>two</span><li>three", "")
        self.assertEqual(page["text"], "one\ntwo\n- three")


class TestReadPage(unittest.TestCase):
    def test_html(self):
        out = page_text.read_page(PAGE.encode(), "text/html; charset=utf-8", "https://example.org/x")
        self.assertEqual(out["title"], "Speech Synthesis Markup")
        self.assertNotIn("hint", out)

    def test_script_built_page_gets_the_browser_hint(self):
        html = '<html><head><title>App</title></head><body><div id="root"></div><script src="/app.js"></script></body></html>'
        out = page_text.read_page(html.encode(), "text/html", "https://app.example.org/")
        self.assertEqual(out["text"], "")
        self.assertIn('browser goto "https://app.example.org/"', out["hint"])

    def test_a_short_real_page_that_also_loads_a_script_has_no_hint(self):
        html = ("<html><body><p>This domain is for use in documentation examples without needing "
                "permission. Avoid relying on it for testing.</p><script src=/s.js></script></body></html>")
        self.assertNotIn("hint", page_text.read_page(html.encode(), "text/html", "https://example.com/"))

    def test_short_page_without_scripts_has_no_hint(self):
        out = page_text.read_page(b"<html><body><p>OK</p></body></html>", "text/html", "https://example.org")
        self.assertEqual(out["text"], "OK")
        self.assertNotIn("hint", out)

    def test_plain_text_json_and_markdown_come_back_as_they_are(self):
        for kind, body in (("text/plain", b"line one\nline two"), ("application/json", b'{"a": 1}'),
                           ("text/markdown; charset=utf-8", b"# Title\n\ntext"),
                           ("application/ld+json", b'{"b": 2}')):
            out = page_text.read_page(body, kind, "https://example.org/f")
            self.assertEqual(out["text"], body.decode())
            self.assertEqual(out["links"], [])

    def test_html_sent_without_a_type_is_still_read(self):
        out = page_text.read_page(b"<!DOCTYPE html><html><body><p>hi</p></body></html>", "", "https://e.org")
        self.assertEqual(out["text"], "hi")

    def test_binary_is_refused_with_its_type(self):
        with self.assertRaises(page_text.FetchError) as ctx:
            page_text.read_page(b"%PDF-1.7", "application/pdf", "https://example.org/a.pdf")
        self.assertIn("application/pdf", str(ctx.exception))

    def test_charset_from_header_then_meta_then_utf8(self):
        self.assertEqual(page_text.charset_of("text/html; charset=ISO-8859-1", b""), "ISO-8859-1")
        self.assertEqual(page_text.charset_of("text/html", b'<meta charset="windows-1252">'), "windows-1252")
        self.assertEqual(page_text.charset_of("text/html", b"<html>"), "utf-8")
        self.assertEqual(page_text.charset_of("text/html; charset=no-such-set", b""), "utf-8")
        latin = "<p>caf\xe9</p>".encode("latin-1")
        self.assertEqual(page_text.read_page(latin, "text/html; charset=latin-1", "https://e.org")["text"], "café")


class TestFetch(unittest.TestCase):
    def test_only_http_and_https(self):
        for bad in ("file:///etc/passwd", "ftp://example.org/x", "javascript:alert(1)", "example.org", "http://"):
            self.assertTrue(page_text.url_error(bad), bad)
            with self.assertRaises(page_text.FetchError):
                page_text.fetch(bad, opener=lambda *a, **k: self.fail("must not be fetched"))
        self.assertEqual(page_text.url_error("https://example.org/a?b=c"), "")

    def test_returns_final_url_type_and_body_with_a_browser_user_agent(self):
        seen = {}

        def opener(request, timeout=None):
            seen["ua"] = request.get_header("User-agent")
            seen["timeout"] = timeout
            return _Reply(b"<p>x</p>")

        url, kind, body = page_text.fetch("https://example.org/start", opener=opener)
        self.assertEqual((url, kind, body), ("https://example.org/final", "text/html; charset=utf-8", b"<p>x</p>"))
        self.assertIn("Chrome/", seen["ua"])
        self.assertEqual(seen["timeout"], page_text.TIMEOUT_SECONDS)
        page_text.fetch("https://example.org/", user_agent="MyAgent/1.0", opener=opener)
        self.assertEqual(seen["ua"], "MyAgent/1.0")

    def test_body_is_capped(self):
        _, _, body = page_text.fetch("https://example.org/", max_bytes=10,
                                     opener=lambda r, timeout=None: _Reply(b"x" * 500))
        self.assertEqual(len(body), 10)

    def test_site_errors_become_plain_messages(self):
        def http_error(request, timeout=None):
            raise urllib.error.HTTPError(request.full_url, 403, "Forbidden", {}, None)

        def unreachable(request, timeout=None):
            raise urllib.error.URLError("Name or service not known")

        with self.assertRaises(page_text.FetchError) as ctx:
            page_text.fetch("https://example.org/", opener=http_error)
        self.assertIn("HTTP 403", str(ctx.exception))
        with self.assertRaises(page_text.FetchError) as ctx:
            page_text.fetch("https://nowhere.invalid/", opener=unreachable)
        self.assertIn("Could not reach", str(ctx.exception))
        with self.assertRaises(page_text.FetchError):
            page_text.fetch("https://example.org/", opener=lambda r, timeout=None: (_ for _ in ()).throw(TimeoutError("slow")))

    def test_a_redirect_to_another_scheme_is_refused(self):
        with self.assertRaises(page_text.FetchError):
            page_text.fetch("https://example.org/",
                            opener=lambda r, timeout=None: _Reply(b"x", url="file:///etc/passwd"))


@unittest.skipIf(agictl_cli is None, "click not installed in this interpreter")
class TestSearchReadCommand(unittest.TestCase):
    def _read(self, args, body=PAGE.encode(), kind="text/html; charset=utf-8", enabled=True):
        config = {"enabled": enabled, "engine": "searxng", "searxng_url": "http://localhost:8888"}
        with patch.object(agictl_cli, "_get_search_config", return_value=config), \
                patch.object(agictl_cli, "_get_browser_config", return_value={"user_agent": ""}), \
                patch.object(page_text, "fetch", return_value=("https://example.org/final", kind, body)) as fetch:
            result = CliRunner().invoke(agictl_cli.cli, ["search", "read"] + args)
        return result, fetch

    def test_page_text_with_the_paging_fields(self):
        result, fetch = self._read(["https://example.org/start"])
        self.assertEqual(result.exit_code, 0, result.output)
        out = json.loads(result.output)
        self.assertTrue(out["success"])
        self.assertEqual(out["url"], "https://example.org/final")
        self.assertEqual(out["title"], "Speech Synthesis Markup")
        self.assertEqual(out["source"], "fetch")
        self.assertIn("Phoneme element", out["content"])
        self.assertEqual(out["offset"], 0)
        self.assertEqual(out["content_chars"], len(out["content"]))
        self.assertNotIn("links", out)
        self.assertNotIn("truncated", out)
        self.assertEqual(fetch.call_args.args[0], "https://example.org/start")

    def test_long_page_is_read_in_parts(self):
        body = ("<html><body>" + "".join(f"<p>paragraph {i} " + "word " * 40 + "</p>" for i in range(200))
                + "</body></html>").encode()
        first = json.loads(self._read(["https://example.org/long"], body=body)[0].output)
        self.assertTrue(first["truncated"])
        self.assertEqual(len(first["content"]), 8000)
        second = json.loads(self._read(["https://example.org/long", "--offset", str(first["next_offset"]),
                                        "--max-chars", "500"], body=body)[0].output)
        self.assertEqual(second["offset"], 8000)
        self.assertEqual(len(second["content"]), 500)
        self.assertEqual(first["content_chars"], second["content_chars"])

    def test_links_only_when_asked(self):
        out = json.loads(self._read(["https://example.org/start", "--links"])[0].output)
        self.assertEqual([ln["url"] for ln in out["links"]],
                         ["https://example.org/home", "https://www.w3.org/TR/ssml/"])

    def test_script_page_carries_the_hint(self):
        html = b'<html><body><div id="app"></div><script src="a.js"></script></body></html>'
        out = json.loads(self._read(["https://example.org/app"], body=html)[0].output)
        self.assertTrue(out["success"])
        self.assertIn("browser goto", out["hint"])

    def test_binary_and_disabled_search_fail_plainly(self):
        result, _ = self._read(["https://example.org/a.pdf"], body=b"%PDF", kind="application/pdf")
        self.assertEqual(result.exit_code, 1)
        self.assertIn("application/pdf", json.loads(result.output)["error"])
        result, fetch = self._read(["https://example.org/"], enabled=False)
        self.assertEqual(result.exit_code, 1)
        self.assertIn("disabled", json.loads(result.output)["error"])
        fetch.assert_not_called()

    def test_user_agent_is_passed_and_checked(self):
        _, fetch = self._read(["https://example.org/", "--user-agent", "MyAgent/2.0"])
        self.assertEqual(fetch.call_args.kwargs["user_agent"], "MyAgent/2.0")
        result, fetch = self._read(["https://example.org/", "--user-agent", "a\nb"])
        self.assertEqual(result.exit_code, 1)
        fetch.assert_not_called()


@unittest.skipIf(agictl_cli is None, "click not installed in this interpreter")
class TestSearchStatusAndKeys(unittest.TestCase):
    def test_probe_counts_results_per_engine(self):
        data = {"results": [{"engine": "google"}, {"engine": "google"}, {"engine": "braveapi"}, {}],
                "unresponsive_engines": [["duckduckgo", "CAPTCHA"], ["bad"]]}
        self.assertEqual(agictl_cli.search_probe(data), {
            "results": 4, "engines": {"google": 2, "braveapi": 1, "unknown": 1},
            "unresponsive_engines": [{"engine": "duckduckgo", "reason": "CAPTCHA"}],
        })
        self.assertEqual(agictl_cli.search_probe({})["results"], 0)

    def test_status_never_prints_a_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            ini = os.path.join(tmp, "setup.ini")
            with open(ini, "w", encoding="utf-8") as fh:
                fh.write("[search]\nenabled=false\nbrave_api_key=BSA-secret-123\nexa_api_key=\n")
            import configparser
            real = configparser.ConfigParser.read

            def read_tmp(self, filenames, encoding=None):
                return real(self, ini, encoding=encoding)

            with patch.object(configparser.ConfigParser, "read", read_tmp):
                result = CliRunner().invoke(agictl_cli.cli, ["search", "status"])
        self.assertEqual(result.exit_code, 0, result.output)
        out = json.loads(result.output)
        self.assertEqual(out["api_keys_set"], {"brave": True, "exa": False})
        self.assertFalse(out["enabled"])
        self.assertNotIn("BSA-secret-123", result.output)

    def test_key_shape(self):
        for good in ("", "BSA-abc_123.x", "a" * 256):
            self.assertTrue(agictl_cli.search_key_ok(good), good)
        for bad in ("two words", 'quo"te', "semi;colon", "dollar$", "a" * 257, "new\nline"):
            self.assertFalse(agictl_cli.search_key_ok(bad), bad)

    def test_set_key_types_and_aliases(self):
        self.assertEqual(agictl_cli.SEARCH_KEY_INI, {"brave_search": "brave_api_key", "exa": "exa_api_key"})
        self.assertEqual(agictl_cli.SEARCH_KEY_ALIASES["brave"], "brave_search")
        set_key = agictl_cli.system.commands["set-key"]
        choices = set_key.params[0].type.choices
        self.assertIn("brave_search", choices)
        self.assertIn("exa", choices)

    def test_sub_agent_cannot_set_a_search_key(self):
        with patch.object(agictl_cli, "get_agent_name", return_value="web-dev"), \
                patch.object(agictl_cli.os, "geteuid", return_value=1001), \
                patch.object(agictl_cli.subprocess, "run") as run:
            result = CliRunner().invoke(agictl_cli.cli, ["search", "set-key", "brave", "KEY123"])
        self.assertEqual(result.exit_code, 1)
        self.assertIn("Only COA", json.loads(result.output)["error"])
        run.assert_not_called()

    def test_coa_sets_a_key_through_the_root_command_and_the_key_is_not_echoed(self):
        class Proc:
            returncode = 0
            stderr = ""
            stdout = json.dumps({"success": True, "key_set": True, "applied": True,
                                 "note": "Test search: 12 results from braveapi, google"})

        with patch.object(agictl_cli, "get_agent_name", return_value="coa"), \
                patch.object(agictl_cli.os, "geteuid", return_value=1001), \
                patch.object(agictl_cli, "_agictl_root_bin", return_value="/usr/local/lib/versa-agi/agictl"), \
                patch.object(agictl_cli.subprocess, "run", return_value=Proc()) as run:
            result = CliRunner().invoke(agictl_cli.cli, ["search", "set-key", "brave", "KEY123"])
        self.assertEqual(result.exit_code, 0, result.output)
        cmd = run.call_args.args[0]
        self.assertEqual(cmd, ["sudo", "-n", "/usr/local/lib/versa-agi/agictl", "system", "set-key",
                               "brave_search", "KEY123"])
        self.assertNotIn("AGICTL_AGENT_USER", run.call_args.kwargs["env"])
        out = json.loads(result.output)
        self.assertEqual((out["service"], out["key_set"], out["applied"]), ("brave", True, True))
        self.assertNotIn("KEY123", result.output)

    def test_a_bad_key_is_refused_before_anything_runs(self):
        with patch.object(agictl_cli, "get_agent_name", return_value="coa"), \
                patch.object(agictl_cli.subprocess, "run") as run:
            result = CliRunner().invoke(agictl_cli.cli, ["search", "set-key", "exa", "not a key"])
        self.assertEqual(result.exit_code, 1)
        run.assert_not_called()

    def test_apply_runs_the_deployed_script_and_reports_the_test_search(self):
        with tempfile.TemporaryDirectory() as tmp:
            script = os.path.join(tmp, "searxng.sh")
            with open(script, "w", encoding="utf-8") as fh:
                fh.write('#!/bin/bash\necho "parent=${VERSA_SETUP_PARENT}"\n'
                         'echo -e "\\033[0;32m[OK]\\033[0m Test search: 9 results from google, exaapi"\n')
            os.chmod(script, os.stat(script).st_mode | stat.S_IEXEC)
            on = {"enabled": True, "engine": "searxng", "searxng_url": "x"}
            with patch.object(agictl_cli, "_get_search_config", return_value=on):
                self.assertEqual(agictl_cli._apply_search_settings(script),
                                 (True, "[OK] Test search: 9 results from google, exaapi"))
            with patch.object(agictl_cli, "_get_search_config", return_value=dict(on, enabled=False)):
                applied, note = agictl_cli._apply_search_settings(script)
                self.assertFalse(applied)
                self.assertIn("turned off", note)
            with open(script, "w", encoding="utf-8") as fh:
                fh.write("#!/bin/bash\nexit 3\n")
            with patch.object(agictl_cli, "_get_search_config", return_value=on):
                applied, note = agictl_cli._apply_search_settings(script)
                self.assertFalse(applied)
                self.assertIn("Key saved", note)
            applied, note = agictl_cli._apply_search_settings(os.path.join(tmp, "missing.sh"))
            self.assertFalse(applied)
            self.assertIn("next update", note)


class TestWiring(unittest.TestCase):
    def _read(self, *rel):
        with open(os.path.join(CORE_INFRA, *rel), encoding="utf-8") as fh:
            return fh.read()

    def test_setup_deploys_the_provider_scripts(self):
        setup = self._read("..", "setup.sh")
        block = setup[setup.index("# SearXNG provider scripts:"):setup.index("# Install administrative tooling:")]
        self.assertIn('for provider_script in _common.sh searxng.sh searxng_settings.sh; do', block)
        self.assertIn('"${LIB_DIR}/providers/${provider_script}"', block)
        self.assertIn("chown root:root", block)

    def test_new_install_turns_the_browser_on_for_coa_and_an_update_leaves_it(self):
        """WEB-12: run setup's own function against a temp agents.db, in both modes."""
        import sqlite3
        import subprocess
        setup = self._read("..", "setup.sh")
        start = setup.index("  _coa_browser_on_for_new_install() {")
        func = setup[start:setup.index("\n  }\n", start) + 5]
        self.assertEqual(setup.count("_coa_browser_on_for_new_install\n"), 2)   # ini path and prompt path
        for update_mode, expected in (("false", 1), ("true", 0)):
            with tempfile.TemporaryDirectory() as tmp:
                db = os.path.join(tmp, "agents.db")
                with sqlite3.connect(db) as conn:
                    conn.execute("CREATE TABLE agents (name TEXT, browser_enabled BOOLEAN DEFAULT 0, updated_at TEXT)")
                    conn.executemany("INSERT INTO agents (name) VALUES (?)", [("coa",), ("web-dev",)])
                script = (f"set -euo pipefail\nok() {{ :; }}\nwarn() {{ :; }}\nUPDATE_MODE={update_mode}\n"
                          f"AGENTS_DB='{db}'\n{func}\n_coa_browser_on_for_new_install\n")
                out = subprocess.run(["bash", "-c", script], capture_output=True, text=True)
                self.assertEqual(out.returncode, 0, out.stderr)
                with sqlite3.connect(db) as conn:
                    rows = dict(conn.execute("SELECT name, browser_enabled FROM agents"))
                self.assertEqual(rows, {"coa": expected, "web-dev": 0}, update_mode)

    def test_setup_does_not_ask_for_search_keys(self):
        setup = self._read("..", "setup.sh")
        self.assertNotIn("brave_api_key", setup)
        self.assertNotIn("exa_api_key", setup)

    def test_dashboard_has_the_two_key_fields_and_saves_through_set_key(self):
        modal = self._read("agitop", "panels", "api_keys_modal.py")
        for needed in ('id="input-brave-search-key"', 'id="input-exa-key"', '("brave_search", "input-brave-search-key")',
                       '("exa", "input-exa-key")', 'SEARCH_KEY_TYPES = ("brave_search", "exa")',
                       '_read_search_key("brave_api_key")', '_read_search_key("exa_api_key")'):
            self.assertIn(needed, modal)

    def test_harness_search_tool_takes_a_command(self):
        harness = self._read("harness", "agent_harness.py")
        self.assertIn("def search_tool_command(command: str = \"\", query: str = \"\", count: int = 5)", harness)
        self.assertIn("'search read \"<url>\"'", harness)
        self.assertIn("return command if command.startswith(\"search \") else \"\"", harness)

    def test_skill_states_the_order_of_preference(self):
        skill = self._read("skills", "web_research.md")
        self.assertTrue(skill.startswith("# Skill: Web Research"))
        first, second, third = (skill.index("search web"), skill.index("search read"), skill.index("browser goto"))
        self.assertLess(first, second)
        self.assertLess(second, third)
        for needed in ("search status", "search set-key", "next_offset", "hint", "unresponsive_engines"):
            self.assertIn(needed, skill)


if __name__ == "__main__":
    unittest.main()
