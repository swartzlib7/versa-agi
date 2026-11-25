"""`agictl system config get|set` and an agent's account (PERM-A9).

An agent's config file holds the Primary User's VersaVoice API token. The file is
closed to agent accounts, and the tool that reads it for them must not hand the
token over either.
"""

from __future__ import annotations

import importlib.util as _importlib_util
import unittest as _unittest

if _importlib_util.find_spec("click") is None:
    raise _unittest.SkipTest("click is not installed in this interpreter; "
                             "the agictl interpreter runs this module")

import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

from click.testing import CliRunner

CORE_INFRA = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, CORE_INFRA)
sys.path.insert(0, os.path.join(CORE_INFRA, "agictl"))

import agictl.cli as agictl_cli  # noqa: E402

CONFIG = {
    "identity": {"first_name": "Clerk", "language": "en"},
    "versavoice": {"sub_account_id": "sub_1", "api_token": "vv-token-value", "status": "active"},
    "primary_user": {"uid": "pu_1", "display_name": "Sam"},
    "providers": {"openai_api_key": "sk-value", "empty_api_key": "", "label": "OpenAI"},
    "notes": [{"password": "pw-value", "host": "mail"}],
}
SECRETS = ("vv-token-value", "sk-value", "pw-value")


class ConfigCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = os.path.join(self.tmp.name, "clerk_config.json")
        with open(self.path, "w", encoding="utf-8") as handle:
            json.dump(CONFIG, handle)

    def run_as(self, caller, *args):
        env = {"AGICTL_CONFIG": self.path}
        if caller:
            env["AGICTL_AGENT_USER"] = caller
        with patch.dict(os.environ, env, clear=False):
            if not caller:
                os.environ.pop("AGICTL_AGENT_USER", None)
            return CliRunner().invoke(agictl_cli.cli, ["system", "config", *args])

    def stored(self):
        with open(self.path, encoding="utf-8") as handle:
            return json.load(handle)


class TestConfigGet(ConfigCase):
    def test_an_agent_gets_the_whole_config_without_any_secret(self):
        for caller in ("agi-clerk", "coa"):
            out = self.run_as(caller, "get").output
            for secret in SECRETS:
                self.assertNotIn(secret, out, caller)
            shown = json.loads(out)
            self.assertEqual(shown["versavoice"]["api_token"], agictl_cli.CONFIG_SECRET_HIDDEN)
            self.assertEqual(shown["providers"]["openai_api_key"], agictl_cli.CONFIG_SECRET_HIDDEN)
            self.assertEqual(shown["notes"][0]["password"], agictl_cli.CONFIG_SECRET_HIDDEN)
            # What is not a secret is unchanged, and an empty secret stays empty.
            self.assertEqual(shown["versavoice"]["sub_account_id"], "sub_1")
            self.assertEqual(shown["primary_user"], CONFIG["primary_user"])
            self.assertEqual(shown["providers"]["empty_api_key"], "")
            self.assertEqual(shown["providers"]["label"], "OpenAI")

    def test_an_agent_asking_for_the_token_by_name_is_refused_it(self):
        out = self.run_as("agi-clerk", "get", "versavoice.api_token").output
        self.assertNotIn("vv-token-value", out)
        self.assertTrue(json.loads(out)["restricted"])

    def test_an_agent_asking_for_the_section_gets_it_without_the_token(self):
        out = self.run_as("agi-clerk", "get", "versavoice").output
        self.assertNotIn("vv-token-value", out)
        self.assertEqual(json.loads(out)["value"]["sub_account_id"], "sub_1")

    def test_the_keys_the_harness_reads_still_arrive(self):
        for key in ("primary_user", "identity"):
            value = json.loads(self.run_as("agi-clerk", "get", key).output)["value"]
            self.assertEqual(value, CONFIG[key])

    def test_watchdog_and_root_get_the_real_values(self):
        shown = json.loads(self.run_as("", "get").output)
        self.assertEqual(shown, CONFIG)
        one = json.loads(self.run_as("", "get", "versavoice.api_token").output)
        self.assertEqual(one["value"], "vv-token-value")


class TestConfigSet(ConfigCase):
    def test_an_agent_cannot_change_its_account_its_owner_or_a_secret(self):
        for key in ("versavoice.api_token", "versavoice.sub_account_id", "primary_user.uid",
                    "primary_user.display_name", "providers.openai_api_key", "extra.password"):
            result = self.run_as("agi-clerk", "set", key, "changed")
            self.assertNotEqual(result.exit_code, 0, key)
            self.assertEqual(self.stored(), CONFIG, key)

    def test_an_agent_can_still_set_an_ordinary_value(self):
        result = self.run_as("agi-clerk", "set", "identity.country", "US")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(self.stored()["identity"]["country"], "US")
        self.assertEqual(self.stored()["versavoice"], CONFIG["versavoice"])

    def test_watchdog_and_root_can_set_anything(self):
        result = self.run_as("", "set", "versavoice.status", "paused")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(self.stored()["versavoice"]["status"], "paused")


if __name__ == "__main__":
    unittest.main()
