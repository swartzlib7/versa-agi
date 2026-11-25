"""SearXNG settings and provider flow (state_web_access.md §1.2, WEB-06).

Runs the real function from providers/searxng_settings.sh in bash and reads
the YAML it prints.
"""

import os
import subprocess
import unittest

try:
    import yaml
except ImportError:  # harness venv
    yaml = None

SRC = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
PROVIDERS = os.path.join(SRC, "providers")
SETTINGS_SH = os.path.join(PROVIDERS, "searxng_settings.sh")


def _bash(script: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", "-c", f"set -euo pipefail\nsource '{SETTINGS_SH}'\n{script}"],
        capture_output=True, text=True,
    )


def _settings(*args: str) -> dict:
    quoted = " ".join(f"'{a}'" for a in args)
    out = _bash(f"searxng_settings_yaml {quoted}")
    assert out.returncode == 0, out.stderr
    return yaml.safe_load(out.stdout)


def _engines(doc: dict) -> dict:
    return {e["name"]: e for e in doc["engines"]}


@unittest.skipIf(yaml is None, "PyYAML not installed in this interpreter")
class TestSettingsYaml(unittest.TestCase):
    def test_base_file(self):
        doc = _settings("abc123")
        self.assertIs(doc["use_default_settings"], True)
        self.assertEqual(doc["server"]["secret_key"], "abc123")
        self.assertIs(doc["server"]["limiter"], False)
        self.assertEqual(doc["search"]["formats"], ["html", "json"])   # agictl reads JSON
        self.assertIs(doc["general"]["contact_url"], False)
        self.assertNotIn("redis", doc)
        self.assertNotIn("valkey", doc)
        self.assertEqual(doc["outgoing"], {"request_timeout": 6.0, "max_request_timeout": 10.0})

    def test_more_web_engines_are_turned_on(self):
        engines = _engines(_settings("abc123"))
        self.assertEqual(sorted(engines), ["bing", "google", "qwant", "yahoo"])
        for entry in engines.values():
            self.assertEqual(set(entry), {"name", "disabled"})
            self.assertIs(entry["disabled"], False)

    def test_no_keyed_engine_without_a_key(self):
        engines = _engines(_settings("abc123", "", ""))
        self.assertNotIn("braveapi", engines)
        self.assertNotIn("exaapi", engines)
        self.assertNotIn("brave", engines)      # the default Brave engine stays as shipped

    def test_brave_key_adds_the_api_engine_and_turns_off_the_page_engine(self):
        engines = _engines(_settings("abc123", "BSA-key_1"))
        self.assertEqual(engines["braveapi"], {
            "name": "braveapi", "engine": "braveapi", "shortcut": "brapi",
            "api_key": "BSA-key_1", "results_per_page": 20, "inactive": False,
        })
        self.assertEqual(engines["brave"], {"name": "brave", "disabled": True})
        self.assertNotIn("exaapi", engines)

    def test_exa_key_adds_the_api_engine(self):
        engines = _engines(_settings("abc123", "", "exa-key"))
        self.assertEqual(engines["exaapi"], {
            "name": "exaapi", "engine": "exaapi", "shortcut": "exa", "api_key": "exa-key",
            "results_per_page": 10, "search_type": "auto", "content_mode": "highlights",
            "inactive": False,
        })
        self.assertNotIn("braveapi", engines)
        self.assertNotIn("brave", engines)

    def test_contact_url_is_written_when_given(self):
        doc = _settings("abc123", "", "", "mailto:ops@example.org")
        self.assertEqual(doc["general"]["contact_url"], "mailto:ops@example.org")

    def test_an_all_digit_key_stays_a_string(self):
        engines = _engines(_settings("abc123", "12345", "0077"))
        self.assertEqual(engines["braveapi"]["api_key"], "12345")
        self.assertEqual(engines["exaapi"]["api_key"], "0077")


class TestHelpers(unittest.TestCase):
    def test_safe_value(self):
        for good in ("", "BSA-key_1.x", "mailto:ops@example.org", "https://example.org/contact"):
            self.assertEqual(_bash(f"searxng_safe_value '{good}'").returncode, 0, good)
        for bad in ('a"b', "a b", "a$b", "a#b", "a\\nb", "a`b"):
            out = subprocess.run(
                ["bash", "-c", f"source '{SETTINGS_SH}'; searxng_safe_value \"$1\"", "x", bad],
                capture_output=True, text=True,
            )
            self.assertEqual(out.returncode, 1, bad)

    def test_existing_secret_is_read_back(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "settings.yml")
            self.assertEqual(_bash(f"searxng_existing_secret '{path}'").stdout.strip(), "")
            made = _bash(f"searxng_settings_yaml deadbeef01 > '{path}'; searxng_existing_secret '{path}'")
            self.assertEqual(made.stdout.strip(), "deadbeef01")
            # A file written by the earlier installer (no "managed" header) reads back too.
            with open(path, "w", encoding="utf-8") as fh:
                fh.write('server:\n  secret_key: "cafe99"\n  limiter: false\n')
            self.assertEqual(_bash(f"searxng_existing_secret '{path}'").stdout.strip(), "cafe99")

    def test_the_same_inputs_give_the_same_file(self):
        a = _bash("searxng_settings_yaml k1 b1 e1 mailto:a@example.org").stdout
        b = _bash("searxng_settings_yaml k1 b1 e1 mailto:a@example.org").stdout
        self.assertEqual(a, b)
        self.assertTrue(a.startswith("# Managed by Versa AGi"))


class TestProviderFlow(unittest.TestCase):
    """searxng.sh: what an update does (source checks; it needs root and Docker to run)."""

    @classmethod
    def setUpClass(cls):
        with open(os.path.join(PROVIDERS, "searxng.sh"), encoding="utf-8") as fh:
            cls.text = fh.read()

    def test_parses(self):
        for name in ("searxng.sh", "searxng_settings.sh"):
            out = subprocess.run(["bash", "-n", os.path.join(PROVIDERS, name)], capture_output=True, text=True)
            self.assertEqual(out.returncode, 0, out.stderr)

    def test_settings_and_image_are_refreshed_before_the_already_running_exit(self):
        write = self.text.index('searxng_settings_yaml "${SECRET_KEY}"')
        pull = self.text.index('docker pull "${SEARXNG_IMAGE}"')
        early_exit = self.text.index("SearXNG already running on port")
        self.assertLess(write, early_exit)
        self.assertLess(pull, early_exit)
        guard = self.text[self.text.index('if [ "${HTTP_CODE}" = "200" ] && [ "${SETTINGS_CHANGED}" = false ]'):early_exit]
        self.assertIn('[ "${IMAGE_NEW}" = "${IMAGE_RUNNING}" ]', guard)

    def test_secret_is_kept_and_keys_come_from_setup_ini(self):
        self.assertIn('SECRET_KEY="$(searxng_existing_secret "${SETTINGS_FILE}")"', self.text)
        self.assertIn('provider_ini_get search brave_api_key', self.text)
        self.assertIn('provider_ini_get search exa_api_key', self.text)
        self.assertIn('provider_ini_get search contact_url', self.text)
        self.assertIn('chmod 640 "${SETTINGS_FILE}"', self.text)

    def test_redis_is_gone(self):
        self.assertNotIn("--link", self.text)
        self.assertNotIn("redis-server", self.text)
        self.assertIn('docker rm -f "${REDIS_CONTAINER}"', self.text)

    def test_stock_config_has_the_keys_blank(self):
        with open(os.path.join(SRC, "setup.ini.stock"), encoding="utf-8") as fh:
            stock = fh.read()
        section = stock[stock.index("[search]"):stock.index("[browser]")]
        for key in ("brave_api_key=\n", "exa_api_key=\n", "contact_url=\n"):
            self.assertIn(key, section)


if __name__ == "__main__":
    unittest.main()
