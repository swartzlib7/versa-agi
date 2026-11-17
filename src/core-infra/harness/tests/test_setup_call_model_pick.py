"""Setup's call model pick: best first, suggested by which provider keys exist
(state_live_voice_call.md §3.13). Runs the shell functions of install_acceptance.sh."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest

CORE_INFRA = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SCRIPT = os.path.join(CORE_INFRA, "install_acceptance.sh")
STOCK = os.path.join(os.path.dirname(CORE_INFRA), "models.ini.stock")

RUN = r'''
source "$SCRIPT"
INSTALL_ACCEPTANCE_SOURCE_INI="$WORK/setup.ini"
INSTALL_ACCEPTANCE_SETUP_INI="$WORK/deployed.ini"
_install_acceptance_read_line() { printf -v "$2" '%s' "$REPLY_TEXT"; }
"$@"
'''


@unittest.skipUnless(shutil.which("bash") and os.path.isfile(SCRIPT) and os.path.isfile(STOCK),
                     "needs bash and the source tree")
class TestCallModelPick(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.work = self._dir.name
        shutil.copy(STOCK, os.path.join(self.work, "models.ini.stock"))

    def keys(self, **keys):
        lines = ["[third_party]"] + [f"{name}_api_key={value}" for name, value in keys.items()]
        with open(os.path.join(self.work, "setup.ini"), "w") as handle:
            handle.write("\n".join(lines) + "\n")

    def run_shell(self, *command, reply=""):
        env = dict(os.environ, SCRIPT=SCRIPT, WORK=self.work, REPLY_TEXT=reply)
        done = subprocess.run(["bash", "-c", RUN, "bash", *command], env=env, capture_output=True,
                              text=True, timeout=60)
        self.assertEqual(done.returncode, 0, done.stderr)
        return done.stdout.strip(), done.stderr

    def pick(self, current="", reply=""):
        return self.run_shell("_install_acceptance_live_call_model_pick", current, reply=reply)

    def test_list_is_in_order_of_preference(self):
        out, _ = self.run_shell("_install_acceptance_live_call_keys")
        rows = [line.split("\t") for line in out.split("\n")]
        self.assertEqual([row[1] for row in rows][:4],
                         ["claude-sonnet-5-5", "anthropic/claude-sonnet-5.5",
                          "gemini-4-hydrogen", "google/gemini-4-hydrogen"])
        self.assertEqual(rows[0], ["Anthropic — Claude Sonnet 5.5", "claude-sonnet-5-5", "anthropic"])
        self.assertEqual(rows[1], ["OpenRouter — Claude Sonnet 5.5", "anthropic/claude-sonnet-5.5", "openrouter"])
        self.assertEqual(rows[2][0], "Google — Gemini 4 Hydrogen")

    def test_keyed_providers_are_read_from_setup_ini(self):
        self.keys(anthropic="sk-a", openrouter="", google="g-1", xai="  ")
        out, _ = self.run_shell("_install_acceptance_keyed_providers")
        self.assertEqual(sorted(out.split()), ["anthropic", "google"])

    def test_provider_key_and_openrouter_suggests_the_providers_own_version(self):
        self.keys(anthropic="sk-a", openrouter="sk-or")
        chosen, shown = self.pick()
        self.assertEqual(chosen, "claude-sonnet-5-5")
        self.assertIn("the\n    provider's own version is suggested.", shown)
        self.assertIn("1) Anthropic — Claude Sonnet 5.5\n", shown)
        self.assertIn("3) Google — Gemini 4 Hydrogen (no API key yet)", shown)

    def test_openrouter_only_suggests_the_openrouter_version(self):
        self.keys(openrouter="sk-or")
        chosen, shown = self.pick()
        self.assertEqual(chosen, "anthropic/claude-sonnet-5.5")
        self.assertIn("1) Anthropic — Claude Sonnet 5.5 (no API key yet)", shown)

    def test_google_only_suggests_hydrogen(self):
        self.keys(google="g-1")
        self.assertEqual(self.pick()[0], "gemini-4-hydrogen")

    def test_no_key_known_offers_the_first_and_marks_nothing(self):
        self.keys()
        chosen, shown = self.pick()
        self.assertEqual(chosen, "claude-sonnet-5-5")
        self.assertNotIn("no API key yet", shown)

    def test_an_update_keeps_the_model_in_use(self):
        self.keys(anthropic="sk-a")
        self.assertEqual(self.pick(current="grok-4.6")[0], "grok-4.6")

    def test_a_typed_number_wins_and_zero_means_later(self):
        self.keys(anthropic="sk-a")
        self.assertEqual(self.pick(reply="5")[0], "claude-opus-5-5")
        self.assertEqual(self.pick(reply="0")[0], "")


if __name__ == "__main__":
    unittest.main()
