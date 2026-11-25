"""Installing an approved package as root (PERM-A7).

`pkg install` (any agent, as watchdog) checks the approved list and hands the
install to `pkg apply`, which runs as root through watchdog's sudo rule and
checks the list again itself. The rule that let watchdog run apt-get with any
arguments is gone.
"""

from __future__ import annotations

import importlib.util as _importlib_util
import unittest as _unittest

if _importlib_util.find_spec("click") is None:
    raise _unittest.SkipTest("click is not installed in this interpreter; "
                             "the agictl interpreter runs this module")

import os
import sqlite3
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from click.testing import CliRunner

CORE_INFRA = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, CORE_INFRA)
sys.path.insert(0, os.path.join(CORE_INFRA, "agictl"))

import agictl.cli as agictl_cli  # noqa: E402


class PkgApplyCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.tmp.name, "agents.db")
        with sqlite3.connect(self.db) as conn:
            conn.execute("CREATE TABLE system_packages (name TEXT PRIMARY KEY, status TEXT)")
            conn.execute("INSERT INTO system_packages VALUES ('ripgrep', 'approved')")
            conn.execute("INSERT INTO system_packages VALUES ('nmap', 'requested')")
        self.runs = []
        self.patches = [
            patch.object(agictl_cli.caller_identity, "_paths_env_value", lambda *_a, **_k: self.db),
            patch.dict(os.environ, {"AGICTL_AGENTS_DB": self.db}),
            patch.object(agictl_cli.subprocess, "run", self._run),
            patch.object(agictl_cli, "_agictl_root_bin", lambda: "/usr/local/lib/versa-agi/agictl"),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.tmp.cleanup()

    def _run(self, cmd, **kwargs):
        self.runs.append((cmd, kwargs))
        return SimpleNamespace(returncode=0, stdout="done\n", stderr="")

    def _invoke(self, *args, euid):
        with patch.object(agictl_cli.os, "geteuid", lambda: euid):
            return CliRunner().invoke(agictl_cli.cli, list(args))

    def test_apply_refuses_anyone_but_root(self):
        result = self._invoke("pkg", "apply", "ripgrep", euid=1001)
        self.assertEqual(result.exit_code, 2)
        self.assertEqual(self.runs, [])

    def test_apply_installs_an_approved_package_and_nothing_else(self):
        result = self._invoke("pkg", "apply", "ripgrep", euid=0)
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(self.runs[0][0], ["/usr/bin/apt-get", "install", "-y", "--", "ripgrep"])
        self.runs.clear()
        for name in ("nmap", "never-asked-for"):
            result = self._invoke("pkg", "apply", name, euid=0)
            self.assertEqual(result.exit_code, 2, name)
        self.assertEqual(self.runs, [])

    def test_apply_takes_no_option_or_path_as_a_name(self):
        for bad in ("-oDpkg::Pre-Invoke::=id", "./evil.deb", "/tmp/evil.deb", "a b"):
            result = self._invoke("pkg", "apply", "--", bad, euid=0)
            self.assertNotEqual(result.exit_code, 0, bad)
        self.assertEqual(self.runs, [])

    def test_install_hands_over_to_apply_through_watchdogs_rule(self):
        os.environ["AGICTL_AGENT_USER"] = "agi-clerk"
        try:
            result = self._invoke("pkg", "install", "ripgrep", euid=1001)
        finally:
            os.environ.pop("AGICTL_AGENT_USER", None)
        self.assertEqual(result.exit_code, 0, result.output)
        cmd, kwargs = self.runs[0]
        self.assertEqual(cmd, ["sudo", "-n", "/usr/local/lib/versa-agi/agictl",
                               "pkg", "apply", "ripgrep"])
        # The root command is watchdog's own call, not the agent's.
        self.assertNotIn("AGICTL_AGENT_USER", kwargs["env"])

    def test_install_still_refuses_what_is_not_approved(self):
        result = self._invoke("pkg", "install", "nmap", euid=1001)
        self.assertIn("approved", result.output)
        self.assertEqual(self.runs, [])


class Wiring(unittest.TestCase):
    def test_the_caller_is_settled_before_anything_reads_it(self):
        with open(os.path.join(CORE_INFRA, "agictl", "cli.py"), encoding="utf-8") as fh:
            text = fh.read()
        settle = text.index("caller_identity.settle(")
        self.assertLess(settle, text.index("import db_connect"))
        self.assertLess(settle, text.index('agents_db = os.getenv("AGICTL_AGENTS_DB"'))
        self.assertLess(settle, text.index("@click.group()"))


if __name__ == "__main__":
    unittest.main()


class InternalAttachmentCopy(unittest.TestCase):
    """A file sent on the internal channel is copied to the recipient
    (state_agent_account_permissions.md PERM-B4): the sender's home is closed
    to the other agents."""

    def test_recipient_gets_its_own_copy_and_the_sender_keeps_its_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            sender_home = os.path.join(tmp, "agi-sender")
            recipient_home = os.path.join(tmp, "agi-clerk")
            os.makedirs(os.path.join(sender_home, "workspace"))
            os.makedirs(os.path.join(recipient_home, ".agent"))
            source = os.path.join(sender_home, "workspace", "report v2.md")
            with open(source, "w", encoding="utf-8") as fh:
                fh.write("figures")
            agents = os.path.join(tmp, "agents.db")
            with sqlite3.connect(agents) as conn:
                conn.execute("CREATE TABLE agents (name TEXT, workspace TEXT)")
                conn.execute("INSERT INTO agents VALUES ('clerk', ?)", (recipient_home,))
            messages = os.path.join(tmp, "messages.db")
            with sqlite3.connect(messages) as conn:
                conn.execute(
                    "CREATE TABLE messages (direction TEXT, from_user_id TEXT, to_user_id TEXT, "
                    "display_name TEXT, message_id TEXT, text TEXT, mode TEXT, status TEXT, "
                    "channel TEXT, attachment_path TEXT)")
            with patch.object(agictl_cli, "agents_db", agents), \
                    patch.object(agictl_cli, "get_agent_name", lambda: "sender"), \
                    patch.object(agictl_cli, "json_response", lambda *a, **k: None):
                agictl_cli._send_as_internal("clerk", "see attached", "typed", messages,
                                             attachment_paths=[source])
            with sqlite3.connect(messages) as conn:
                rows = dict(conn.execute("SELECT direction, attachment_path FROM messages"))
            import json as _json
            self.assertEqual(_json.loads(rows["sent"]), [source])
            received = _json.loads(rows["received"])[0]
            self.assertTrue(received.startswith(os.path.join(recipient_home, ".agent", "attachments")))
            self.assertEqual(os.path.basename(received), "report_v2.md")
            with open(received, encoding="utf-8") as fh:
                self.assertEqual(fh.read(), "figures")

    def test_a_recipient_that_is_not_an_agent_here_keeps_the_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            agents = os.path.join(tmp, "agents.db")
            with sqlite3.connect(agents) as conn:
                conn.execute("CREATE TABLE agents (name TEXT, workspace TEXT)")
            with patch.object(agictl_cli, "agents_db", agents):
                self.assertIsNone(agictl_cli._local_agent_workspace("someone-else"))


class PackageRequestReachesTheApp(unittest.TestCase):
    """A package request is shown in the VersaVoice app at the next Lifeline
    pass, not at the next hourly sync (found 2026-11-24: a request made two
    minutes after a sync was not in the app)."""

    def test_a_request_sets_the_sync_stamp_back(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "agents.db")
            with sqlite3.connect(db) as conn:
                conn.execute("CREATE TABLE system_packages (name TEXT PRIMARY KEY, status TEXT, "
                             "reason TEXT, requested_by TEXT, requested_at TEXT, resolved_at TEXT, "
                             "notified_at TEXT)")
            stamp = os.path.join(tmp, "stamp")
            with open(stamp, "w", encoding="utf-8") as fh:
                fh.write("1795500000\n")
            with patch.dict(os.environ, {"AGICTL_AGENTS_DB": db, "AGICTL_VV_SYNC_STAMP": stamp}), \
                    patch.object(agictl_cli, "get_agent_name", lambda: "coa"):
                result = CliRunner().invoke(agictl_cli.cli, ["pkg", "request", "tree", "--reason", "test"])
            self.assertIn('"status": "requested"', result.output)
            with open(stamp, encoding="utf-8") as fh:
                self.assertEqual(fh.read().strip(), "0")

    def test_lifeline_syncs_when_the_stamp_is_older_than_the_interval(self):
        with open(os.path.join(CORE_INFRA, "lifeline.sh"), encoding="utf-8") as fh:
            lifeline = fh.read()
        self.assertIn('if [ $((_now_epoch - _last_sync)) -ge "${_vv_sync_secs}" ]; then', lifeline)
