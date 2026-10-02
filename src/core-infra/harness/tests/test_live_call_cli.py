"""agictl live call surfaces: model live-call, message calls, hidden call-bridge guard."""

from __future__ import annotations

import configparser
import json
import os
import sys
import tempfile
import textwrap
import unittest
from unittest.mock import patch

from click.testing import CliRunner

CORE_INFRA = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, CORE_INFRA)
sys.path.insert(0, os.path.join(CORE_INFRA, "agictl"))

import agictl.cli as agictl_cli  # noqa: E402
import call_log_store  # noqa: E402

MODELS_INI = textwrap.dedent("""
    [catalog]
    gemini-3.7-flash = cloud|google|true|true|0|1048576|balanced|text,image,audio,video|text|true|Gemini 3.7 Flash
    grok-4.6         = cloud|xai|true|true|0|500000|reasoning|text,image|text|true|Grok 4.6
    gpt-5.6-luna     = cloud|openai|true|true|0|1050000|fast|text,image|text|true|GPT-5.6 Luna

    [catalog_live_call]
    gemini-3.7-flash = true
    grok-4.6         = true

    [catalog_live_call_custom]
""")


class _Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.models = os.path.join(self.tmp.name, "models.ini")
        with open(self.models, "w") as f:
            f.write(MODELS_INI)
        self.messages = os.path.join(self.tmp.name, "messages.db")
        self.runner = CliRunner()
        self._patches = [
            patch.object(agictl_cli, "_MODELS_INI_PATHS", [self.models]),
            patch.object(agictl_cli, "messages_db", self.messages),
            patch.dict(os.environ, {"VERSA_MODELS_INI": self.models}),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in reversed(self._patches):
            p.stop()
        self.tmp.cleanup()

    def invoke(self, args, env=None, stdin=None):
        clean = {k: v for k, v in os.environ.items() if k not in ("AGICTL_AGENT_USER", "VERSA_CALL_BRIDGE")}
        clean.update(env or {})
        with patch.dict(os.environ, clean, clear=True):
            return self.runner.invoke(agictl_cli.cli, args, input=stdin)

    def site_section(self):
        cfg = configparser.ConfigParser(delimiters=("=",), strict=False)
        cfg.optionxform = str
        cfg.read(self.models)
        return dict(cfg.items("catalog_live_call_custom")) if cfg.has_section("catalog_live_call_custom") else {}


class TestModelLiveCall(_Base):
    def test_set_and_unset_write_site_layer(self):
        res = self.invoke(["model", "live-call", "set", "gpt-5.6-luna"])
        self.assertEqual(res.exit_code, 0, res.output)
        self.assertEqual(self.site_section(), {"gpt-5.6-luna": "true"})

        res = self.invoke(["model", "live-call", "unset", "grok-4.6"])
        self.assertEqual(res.exit_code, 0, res.output)
        payload = json.loads(res.output.strip().splitlines()[-1])
        self.assertEqual(payload["effective"], ["gemini-3.7-flash", "gpt-5.6-luna"])

        res = self.invoke(["model", "live-call", "set", "grok-4.6"])
        self.assertEqual(res.exit_code, 0, res.output)
        self.assertEqual(self.site_section(), {"gpt-5.6-luna": "true"})

    def test_agents_cannot_change_the_list(self):
        res = self.invoke(["model", "live-call", "set", "gpt-5.6-luna"], env={"AGICTL_AGENT_USER": "coa"})
        self.assertNotEqual(res.exit_code, 0)
        self.assertIn("Primary User", res.output)
        self.assertEqual(self.site_section(), {})

    def test_unknown_key_refused(self):
        res = self.invoke(["model", "live-call", "set", "nope-1"])
        self.assertNotEqual(res.exit_code, 0)
        self.assertIn("not in the live catalog", res.output)


class TestCallsRead(_Base):
    def setUp(self):
        super().setUp()
        call_log_store.insert_attempt(self.messages, call_id="call_1", agent_name="coa", status="calling",
                                      reason="Blocked task")
        call_log_store.update_call(
            self.messages, "call_1", status="ended", close_reason="remote_hangup", voice_seconds=42,
            transcript_json=[{"speaker": "pu", "text": "Move it to Thursday."},
                             {"speaker": "assistant", "text": "Done — Thursday."}],
            delegations_json=[{"delegation_id": "item_1", "tools": ["agictl_task"]}],
        )

    def test_list_and_show_for_coa(self):
        res = self.invoke(["message", "calls", "list"], env={"AGICTL_AGENT_USER": "coa"})
        self.assertEqual(res.exit_code, 0, res.output)
        rows = json.loads(res.output)
        self.assertEqual(rows[0]["call_id"], "call_1")
        self.assertEqual(rows[0]["voice_seconds"], 42)

        res = self.invoke(["message", "calls", "show", "call_1"], env={"AGICTL_AGENT_USER": "coa"})
        self.assertEqual(res.exit_code, 0, res.output)
        shown = json.loads(res.output)
        self.assertEqual(shown["transcript"], "PU: Move it to Thursday.\nCOA: Done — Thursday.")
        self.assertEqual(shown["delegations"][0]["tools"], ["agictl_task"])

    def test_sub_agent_refused(self):
        res = self.invoke(["message", "calls", "list"], env={"AGICTL_AGENT_USER": "agi-web-dev"})
        self.assertNotEqual(res.exit_code, 0)
        self.assertIn("COA only", res.output)


class TestBridgeGuard(_Base):
    def test_refused_without_harness_marker(self):
        for env in ({"AGICTL_AGENT_USER": "coa"}, {"VERSA_CALL_BRIDGE": "harness"},
                    {"AGICTL_AGENT_USER": "agi-web-dev", "VERSA_CALL_BRIDGE": "harness"}):
            res = self.invoke(["message", "call-bridge", "open", "--reason", "x"], env=env)
            self.assertNotEqual(res.exit_code, 0)
            self.assertIn("no terminal command", res.output)

    def test_summary_saved_by_harness_bridge(self):
        call_log_store.insert_attempt(self.messages, call_id="call_9", agent_name="coa",
                                      status="calling", reason="x")
        env = {"AGICTL_AGENT_USER": "coa", "VERSA_CALL_BRIDGE": "harness"}
        res = self.invoke(["message", "call-bridge", "summary", "call_9"], env=env,
                          stdin="Discussed task 12;\n QA moved to Friday.")
        self.assertEqual(res.exit_code, 0, res.output)
        self.assertEqual(call_log_store.get_call(self.messages, "call_9")["summary"],
                         "Discussed task 12; QA moved to Friday.")
        res = self.invoke(["message", "call-bridge", "summary", "call_9"],
                          env={"AGICTL_AGENT_USER": "coa"}, stdin="forged")
        self.assertNotEqual(res.exit_code, 0)

    def test_status_reads_callee_call_document(self):
        call_log_store.insert_attempt(self.messages, call_id="call_7", agent_name="coa", status="live",
                                      reason="x", callee_uid="uid_ashok")
        conf = os.path.join(self.tmp.name, "coa_config.json")
        with open(conf, "w") as f:
            json.dump({"versavoice": {"api_token": "tok", "sub_account_id": "sub_1"}}, f)
        env = {"AGICTL_AGENT_USER": "coa", "VERSA_CALL_BRIDGE": "harness", "AGICTL_CONFIG": conf}
        import comms
        with patch.object(comms, "call_status",
                          return_value={"success": True, "data": {"status": "ended"}}) as read:
            res = self.invoke(["message", "call-bridge", "status", "call_7"], env=env)
        self.assertEqual(res.exit_code, 0, res.output)
        self.assertEqual(json.loads(res.output.strip().splitlines()[-1])["status"], "ended")
        read.assert_called_once_with("tok", "sub_1", "call_7", callee_uid="uid_ashok")
        self.assertEqual(call_log_store.get_call(self.messages, "call_7")["status"], "live")

    def test_bridge_hidden_from_help(self):
        res = self.invoke(["message", "--help"])
        self.assertNotIn("call-bridge", res.output)
        self.assertIn("calls", res.output)


if __name__ == "__main__":
    unittest.main()
