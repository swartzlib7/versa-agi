"""Web search query cache (state_web_access.md §1.2 rule 10, WEB-07)."""

import json
import os
import sys
import tempfile
import unittest

CORE_INFRA = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, CORE_INFRA)

from agictl import search_cache as sc  # noqa: E402

RESULTS = [{"title": f"T{i}", "url": f"https://example.org/{i}", "snippet": "s", "engine": "bing"} for i in range(60)]


class TestSearchCache(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = os.path.join(self.tmp.name, "cache")

    def test_the_same_query_is_answered_from_the_last_answer(self):
        self.assertTrue(sc.put("Python release notes", "general", RESULTS[:5], 10, cache_dir=self.dir, now=1000))
        kept, age = sc.get("python   RELEASE notes", "general", 10, cache_dir=self.dir, now=1090)
        self.assertEqual([r["url"] for r in kept], [r["url"] for r in RESULTS[:5]])
        self.assertEqual(age, 90)

    def test_an_answer_older_than_the_setting_is_asked_again(self):
        sc.put("q", "general", RESULTS[:3], 10, cache_dir=self.dir, now=1000)
        self.assertEqual(sc.get("q", "general", 10, cache_dir=self.dir, now=1000 + 601), (None, 0))

    def test_another_category_is_another_question(self):
        sc.put("q", "general", RESULTS[:3], 10, cache_dir=self.dir, now=1000)
        self.assertEqual(sc.get("q", "news", 10, cache_dir=self.dir, now=1001), (None, 0))

    def test_an_empty_answer_is_never_kept(self):
        """A blocked search must be tried again, not remembered as "nothing found"."""
        self.assertFalse(sc.put("q", "general", [], 10, cache_dir=self.dir, now=1000))
        self.assertEqual(sc.get("q", "general", 10, cache_dir=self.dir, now=1001), (None, 0))

    def test_zero_minutes_switches_it_off(self):
        self.assertFalse(sc.put("q", "general", RESULTS[:3], 0, cache_dir=self.dir, now=1000))
        self.assertFalse(os.path.isdir(self.dir))
        sc.put("q", "general", RESULTS[:3], 10, cache_dir=self.dir, now=1000)
        self.assertEqual(sc.get("q", "general", 0, cache_dir=self.dir, now=1001), (None, 0))

    def test_the_setting_is_read_as_minutes(self):
        self.assertEqual(sc.minutes_setting("15"), 15)
        self.assertEqual(sc.minutes_setting("0"), 0)
        self.assertEqual(sc.minutes_setting(""), sc.DEFAULT_MINUTES)
        self.assertEqual(sc.minutes_setting("soon"), sc.DEFAULT_MINUTES)
        self.assertEqual(sc.minutes_setting("-5"), 0)
        self.assertEqual(sc.minutes_setting("99999"), sc.MAX_MINUTES)

    def test_more_results_are_kept_than_the_first_count_asked_for(self):
        sc.put("q", "general", RESULTS, 10, cache_dir=self.dir, now=1000)
        kept, _ = sc.get("q", "general", 10, cache_dir=self.dir, now=1001)
        self.assertEqual(len(kept), sc.KEEP_RESULTS)

    def test_files_are_private_and_old_ones_are_removed(self):
        sc.put("old", "general", RESULTS[:2], 10, cache_dir=self.dir, now=1000)
        old = [n for n in os.listdir(self.dir)][0]
        os.utime(os.path.join(self.dir, old), (1000, 1000))
        sc.put("new", "general", RESULTS[:2], 10, cache_dir=self.dir, now=1000 + 3600)
        names = os.listdir(self.dir)
        self.assertEqual(len(names), 1)
        self.assertNotEqual(names[0], old)
        self.assertEqual(os.stat(os.path.join(self.dir, names[0])).st_mode & 0o777, 0o600)
        self.assertEqual(os.stat(self.dir).st_mode & 0o777, 0o700)

    def test_a_damaged_file_is_a_miss_and_a_folder_that_cannot_be_made_is_not_an_error(self):
        os.makedirs(self.dir)
        sc.put("q", "general", RESULTS[:2], 10, cache_dir=self.dir, now=1000)
        name = os.listdir(self.dir)[0]
        with open(os.path.join(self.dir, name), "w", encoding="utf-8") as handle:
            handle.write("{not json")
        self.assertEqual(sc.get("q", "general", 10, cache_dir=self.dir, now=1001), (None, 0))
        blocked = os.path.join(self.tmp.name, "file")
        with open(blocked, "w", encoding="utf-8") as handle:
            handle.write("x")
        self.assertFalse(sc.put("q", "general", RESULTS[:2], 10, cache_dir=os.path.join(blocked, "sub"), now=1000))


class TestWiring(unittest.TestCase):
    def test_setup_makes_the_folder_for_watchdog_and_the_audit_checks_it(self):
        src = os.path.dirname(CORE_INFRA)
        with open(os.path.join(src, "setup.sh"), encoding="utf-8") as handle:
            setup = handle.read()
        self.assertIn('chown -R "${WATCHDOG_USER}:${WATCHDOG_USER}" /var/lib/versa-agi/search-cache && chmod 700', setup)
        with open(os.path.join(src, "audit_permissions.sh"), encoding="utf-8") as handle:
            self.assertIn('"/var/lib/versa-agi/search-cache"   "watchdog:watchdog"  "700"', handle.read())

    def test_the_stock_setting_is_ten_minutes(self):
        import configparser
        ini = configparser.ConfigParser()
        ini.read(os.path.join(os.path.dirname(CORE_INFRA), "setup.ini.stock"))
        self.assertEqual(sc.minutes_setting(ini.get("search", "cache_minutes")), 10)


try:
    import click  # noqa: F401
    HAVE_CLICK = True
except ImportError:
    HAVE_CLICK = False


@unittest.skipUnless(HAVE_CLICK, "click is not installed in this interpreter; the agictl interpreter runs this")
class TestSearchWebCommand(unittest.TestCase):
    """`agictl search web` asks once and answers a repeat from the cache."""

    def setUp(self):
        from unittest import mock
        from click.testing import CliRunner
        sys.path.insert(0, os.path.join(CORE_INFRA, "agictl"))
        import agictl.cli as cli
        self.cli = cli
        self.runner = CliRunner()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.calls = 0
        test = self

        class Reply:
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def read(self):
                return json.dumps({"results": [{"title": "A", "url": "https://a.example", "content": "x", "engine": "bing"},
                                               {"title": "B", "url": "https://b.example", "content": "y", "engine": "bing"}]}).encode()

        def fake_urlopen(req, timeout=0):
            test.calls += 1
            return Reply()

        for patcher in (
            mock.patch.object(cli, "_get_search_config", lambda: {"enabled": True, "engine": "searxng",
                                                                  "searxng_url": "http://localhost:8888",
                                                                  "cache_minutes": self.minutes}),
            mock.patch.object(sc, "CACHE_DIR", os.path.join(self.tmp.name, "c")),
            mock.patch("urllib.request.urlopen", fake_urlopen),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        # get/put read CACHE_DIR as a default argument: point the defaults at the temp folder too.
        for fn in (sc.get, sc.put, sc.prune):
            saved = dict(fn.__kwdefaults__)
            fn.__kwdefaults__["cache_dir"] = os.path.join(self.tmp.name, "c")
            self.addCleanup(lambda f=fn, s=saved: f.__kwdefaults__.update(s))
        self.minutes = "10"

    def ask(self, *args):
        res = self.runner.invoke(self.cli.cli, ["search", "web", "python release notes", *args])
        return json.loads(res.output)

    def test_a_repeat_is_not_sent_again_and_says_so(self):
        first = self.ask()
        again = self.ask("--count", "1")
        self.assertEqual(self.calls, 1)
        self.assertNotIn("cached", first)
        self.assertTrue(again["cached"])
        self.assertEqual(again["count"], 1)
        self.assertIn("cached_age_seconds", again)

    def test_fresh_asks_again(self):
        self.ask()
        fresh = self.ask("--fresh")
        self.assertEqual(self.calls, 2)
        self.assertNotIn("cached", fresh)

    def test_cache_minutes_zero_always_asks(self):
        self.minutes = "0"
        self.ask()
        self.ask()
        self.assertEqual(self.calls, 2)


if __name__ == "__main__":
    unittest.main()
