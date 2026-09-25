"""VersaVoice identity reuse: Normal COA vs Sentinel host-stable key.

Run from core-infra::

    python -m unittest harness.tests.test_identity_provision
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock

CORE_INFRA = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)
sys.path.insert(0, CORE_INFRA)
sys.path.insert(0, os.path.join(CORE_INFRA, "agictl"))

import identity  # noqa: E402
from identity import (  # noqa: E402
    SENTINEL_AGENT_KEY_RE,
    SHARED_COA_AGENT_KEY,
    _api_unavailable,
    _bound_agent_key,
    _find_sub_account,
    _should_reuse_config_id,
    derive_sentinel_agent_key,
    provision_identity,
    read_host_material,
)


HOME = {
    "subAccountId": "home-uid",
    "firstName": "Versa",
    "lastName": "(abc123)",
    "agiInstallEmail": "pu@example.com",
    "agiAgentKey": "coa",
}


class TestSentinelAgentKey(unittest.TestCase):
    def test_derive_is_stable_and_prefixed(self) -> None:
        a = derive_sentinel_agent_key("machine-one")
        b = derive_sentinel_agent_key("machine-one")
        self.assertEqual(a, b)
        self.assertTrue(SENTINEL_AGENT_KEY_RE.match(a), a)
        self.assertNotEqual(a, derive_sentinel_agent_key("machine-two"))

    def test_derive_rejects_empty(self) -> None:
        with self.assertRaises(ValueError):
            derive_sentinel_agent_key("  ")

    def test_read_host_material_prefers_machine_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "machine-id")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("  abcdef0123456789\n")
            self.assertEqual(read_host_material(path), "abcdef0123456789")
            self.assertTrue(
                SENTINEL_AGENT_KEY_RE.match(
                    derive_sentinel_agent_key(read_host_material(path))
                )
            )

    def test_read_host_material_fallback_is_nonempty(self) -> None:
        missing = os.path.join(tempfile.gettempdir(), "no-such-machine-id")
        self.assertTrue(read_host_material(missing))


class TestFindSubAccount(unittest.TestCase):
    def test_normal_coa_reuses_email_plus_coa_key(self) -> None:
        found = _find_sub_account(
            {"subAccounts": [HOME]},
            "Office",
            "(abc123)",
            "pu@example.com",
            SHARED_COA_AGENT_KEY,
        )
        self.assertEqual(found, "home-uid")

    def test_sentinel_key_does_not_reuse_home_coa(self) -> None:
        found = _find_sub_account(
            {"subAccounts": [HOME]},
            "Office",
            "(abc123)",
            "pu@example.com",
            derive_sentinel_agent_key("other-host"),
        )
        self.assertIsNone(found)

    def test_normal_coa_name_fallback_when_email_differs(self) -> None:
        found = _find_sub_account(
            {"subAccounts": [HOME]},
            "Versa",
            "(abc123)",
            "other@example.com",
            SHARED_COA_AGENT_KEY,
        )
        self.assertEqual(found, "home-uid")

    def test_sentinel_skips_name_fallback(self) -> None:
        found = _find_sub_account(
            {"subAccounts": [HOME]},
            "Versa",
            "(abc123)",
            "other@example.com",
            derive_sentinel_agent_key("other-host"),
        )
        self.assertIsNone(found)

    def test_sentinel_reuses_own_email_plus_host_key(self) -> None:
        host_key = derive_sentinel_agent_key("sentinel-box")
        sent = {
            "subAccountId": "sent-uid",
            "firstName": "Office",
            "lastName": "(abc123)",
            "agiInstallEmail": "pu@example.com",
            "agiAgentKey": host_key,
        }
        found = _find_sub_account(
            {"subAccounts": [HOME, sent]},
            "Office",
            "(abc123)",
            "pu@example.com",
            host_key,
        )
        self.assertEqual(found, "sent-uid")


class TestConfigIdReuse(unittest.TestCase):
    def test_normal_coa_always_reuses_live_config_id(self) -> None:
        self.assertTrue(_should_reuse_config_id(SHARED_COA_AGENT_KEY, "coa"))
        self.assertTrue(_should_reuse_config_id(SHARED_COA_AGENT_KEY, None))
        self.assertTrue(_should_reuse_config_id(SHARED_COA_AGENT_KEY, "other"))

    def test_sentinel_rejects_home_coa_config_id(self) -> None:
        host_key = derive_sentinel_agent_key("sentinel-box")
        self.assertFalse(_should_reuse_config_id(host_key, SHARED_COA_AGENT_KEY))
        self.assertFalse(_should_reuse_config_id(host_key, None))

    def test_sentinel_reuses_own_config_id(self) -> None:
        host_key = derive_sentinel_agent_key("sentinel-box")
        self.assertTrue(_should_reuse_config_id(host_key, host_key))

    def test_bound_agent_key_reads_listed_sub(self) -> None:
        host_key = derive_sentinel_agent_key("sentinel-box")
        account = {"subAccounts": [HOME, {"subAccountId": "sent-uid", "agiAgentKey": host_key}]}
        self.assertEqual(_bound_agent_key(account, "home-uid"), "coa")
        self.assertEqual(_bound_agent_key(account, "sent-uid"), host_key)
        self.assertIsNone(_bound_agent_key(account, "missing"))


class TestProvisionApiStatus(unittest.TestCase):
    """Bound config id vs VersaVoice API outage (SETUP-TOK-01)."""

    BOUND = "bound-uid"

    def setUp(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.written: list[str] = []
        self.responses: dict[tuple[str, str], tuple[object, int]] = {}
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE TABLE agents (name TEXT, os_user TEXT)")
        conn.execute("INSERT INTO agents VALUES ('coa', 'coa')")
        config = json.dumps({"versavoice": {"sub_account_id": self.BOUND}})
        patches = [
            mock.patch.object(identity.os.path, "exists", return_value=True),
            mock.patch.object(identity.db_connect, "connect_compat", return_value=conn),
            mock.patch("builtins.open", mock.mock_open(read_data=config)),
            mock.patch.object(identity, "api_request", side_effect=self._api),
            mock.patch.object(identity, "_sync_display_name"),
            mock.patch.object(
                identity, "_write_identity",
                side_effect=lambda sub_id, *a, **k: self.written.append(sub_id),
            ),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def _api(self, endpoint, token, method="GET", body=None, with_status=False):
        self.calls.append((method, endpoint))
        result, status = self.responses.get((method, endpoint), (None, 404))
        return (result, status) if with_status else result

    def _provision(self) -> bool:
        return provision_identity(
            "coa", "tok", "Versa", "(abc123)", "en", "", "female",
            "/fake/agents.db", install_email="pu@example.com", agent_key="coa",
        )

    def _registered(self) -> bool:
        return ("POST", "/accounts/register") in self.calls

    def test_throttled_keeps_bound_id_and_never_registers(self) -> None:
        self.responses[("GET", f"/accounts/{self.BOUND}")] = (None, 429)
        self.assertFalse(self._provision())
        self.assertFalse(self._registered())
        self.assertNotIn(("GET", "/account"), self.calls)
        self.assertEqual(self.written, [])

    def test_network_error_keeps_bound_id(self) -> None:
        self.responses[("GET", f"/accounts/{self.BOUND}")] = (None, 0)
        self.assertFalse(self._provision())
        self.assertFalse(self._registered())

    def test_deleted_sub_account_scans_then_registers(self) -> None:
        self.responses[("GET", "/account")] = ({"subAccounts": []}, 200)
        self.responses[("POST", "/accounts/register")] = ({"subAccountId": "new-uid"}, 201)
        self.assertTrue(self._provision())
        self.assertTrue(self._registered())
        self.assertEqual(self.written, ["new-uid"])

    def test_scan_outage_after_404_does_not_register(self) -> None:
        self.responses[("GET", "/account")] = (None, 503)
        self.assertFalse(self._provision())
        self.assertFalse(self._registered())

    def test_live_bound_id_resolves(self) -> None:
        self.responses[("GET", f"/accounts/{self.BOUND}")] = (
            {"firstName": "Versa", "lastName": "(abc123)"}, 200,
        )
        self.assertTrue(self._provision())
        self.assertEqual(self.written, [self.BOUND])
        self.assertFalse(self._registered())

    def test_unavailable_codes(self) -> None:
        for code in (0, 408, 429, 500, 503):
            self.assertTrue(_api_unavailable(code), code)
        for code in (200, 401, 403, 404):
            self.assertFalse(_api_unavailable(code), code)


if __name__ == "__main__":
    unittest.main()
