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


class TestRefreshOnUpdate(unittest.TestCase):
    """`setup.sh --update` refreshes a search service that is already there (WEB-13).

    Until 3.10.7 the SearXNG provider ran only in the install branch of setup,
    so an update never wrote the settings again. The function is cut out of
    setup.sh and run in bash with stand-ins for docker and the provider script.
    """

    @classmethod
    def setUpClass(cls):
        with open(os.path.join(SRC, "setup.sh"), encoding="utf-8") as handle:
            cls.setup = handle.read()
        start = cls.setup.index("# ── search refresh: begin ──")
        cls.block = cls.setup[start:cls.setup.index("# ── search refresh: end ──")]

    def _run(self, *, enabled="true", docker=True, container=True, settings=False, dry=False, fails=False):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "providers"))
            marker = os.path.join(tmp, "ran")
            with open(os.path.join(tmp, "providers", "searxng.sh"), "w", encoding="utf-8") as fh:
                fh.write(f'echo "parent=${{VERSA_SETUP_PARENT:-}}" > "{marker}"\n' + ("exit 3\n" if fails else ""))
            settings_file = os.path.join(tmp, "settings.yml")
            if settings:
                with open(settings_file, "w", encoding="utf-8"):
                    pass
            bin_dir = os.path.join(tmp, "bin")
            os.makedirs(bin_dir)
            if docker:
                with open(os.path.join(bin_dir, "docker"), "w", encoding="utf-8") as fh:
                    fh.write("#!/bin/bash\n" + ('echo searxng\n' if container else "exit 0\n"))
                os.chmod(os.path.join(bin_dir, "docker"), 0o755)
            script = (
                "set -euo pipefail\n"
                f'SCRIPT_DIR="{tmp}"; SEARXNG_SETTINGS_FILE="{settings_file}"; DRY_RUN={"true" if dry else "false"}\n'
                f'PATH="{bin_dir}:/usr/bin:/bin"\n'
                + ("" if docker else "hash -r\n")
                + f'ini_get() {{ echo "{enabled}"; }}\n'
                'info() { echo "INFO $*"; }; warn() { echo "WARN $*"; }; dry() { echo "DRY $*"; }\n'
                + self.block + "\nrefresh_search_on_update\necho END\n"
            )
            out = subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=30)
            ran = ""
            if os.path.exists(marker):
                with open(marker, encoding="utf-8") as fh:
                    ran = fh.read().strip()
            return out, ran

    def test_an_existing_container_is_refreshed_without_questions(self):
        out, ran = self._run()
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(ran, "parent=1")          # the script asks nothing when setup is its parent

    def test_an_existing_settings_file_is_enough(self):
        out, ran = self._run(container=False, settings=True)
        self.assertEqual(ran, "parent=1", out.stdout + out.stderr)

    def test_search_switched_off_is_left_alone(self):
        out, ran = self._run(enabled="false")
        self.assertEqual(ran, "")
        self.assertIn("Web search is off", out.stdout)

    def test_an_update_never_installs_docker_or_a_first_container(self):
        which = subprocess.run(["bash", "-c", "PATH=/usr/bin:/bin command -v docker"], capture_output=True)
        if which.returncode != 0:                  # no system docker to leak through the stand-in PATH
            out, ran = self._run(docker=False)
            self.assertEqual(ran, "")
            self.assertIn("Docker is not installed", out.stdout)
        out, ran = self._run(container=False, settings=False)
        self.assertEqual(ran, "")
        self.assertIn("no SearXNG container here", out.stdout)

    def test_a_dry_run_changes_nothing(self):
        out, ran = self._run(dry=True)
        self.assertEqual(ran, "")
        self.assertIn("DRY Would refresh SearXNG", out.stdout)

    def test_a_failed_refresh_does_not_stop_the_update(self):
        out, ran = self._run(fails=True)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("SearXNG refresh failed", out.stdout)
        self.assertIn("END", out.stdout)

    def test_it_is_called_on_an_update_and_outside_the_install_only_step(self):
        install_only_ends = self.setup.index("fi  # end UPDATE_MODE=false guard (Step 9)")
        call = self.setup.index('  section "Update — Web Search"\n  refresh_search_on_update\n')
        self.assertGreater(call, install_only_ends)
        guard = self.setup.rfind('if [ "${UPDATE_MODE}" = true ]; then', 0, call)
        self.assertEqual(self.setup[guard:call].count("\n"), 1)   # the call sits directly under the update guard
        # The provider scripts are in place before the refresh runs them.
        self.assertLess(self.setup.index('cp "${SCRIPT_DIR}/providers/${provider_script}"'), call)
