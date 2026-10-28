"""agictl search web: an empty result names the engines SearXNG could not use.

Run from core-infra:
  python -m unittest harness.tests.test_search_web
"""

from __future__ import annotations

import io
import json
import os
import sys
import unittest
from unittest.mock import patch

from click.testing import CliRunner

CORE_INFRA = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)
sys.path.insert(0, CORE_INFRA)
sys.path.insert(0, os.path.join(CORE_INFRA, "agictl"))

import agictl.cli as agictl_cli  # noqa: E402


class _Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _run(payload):
    config = {"enabled": True, "engine": "searxng", "searxng_url": "http://searx.test"}
    body = json.dumps(payload).encode()
    with patch.object(agictl_cli, "_get_search_config", return_value=config), \
            patch("urllib.request.urlopen", return_value=_Response(body)):
        result = CliRunner().invoke(agictl_cli.cli, ["search", "web", "local inference"])
    assert result.exit_code == 0, result.output
    return json.loads(result.output)


class TestSearchWeb(unittest.TestCase):
    def test_empty_result_lists_unresponsive_engines(self):
        out = _run({
            "results": [],
            "unresponsive_engines": [
                ["brave", "Suspended: too many requests"],
                ["duckduckgo", "CAPTCHA"],
            ],
        })
        self.assertTrue(out["success"])
        self.assertEqual(out["count"], 0)
        self.assertEqual(out["unresponsive_engines"], [
            {"engine": "brave", "reason": "Suspended: too many requests"},
            {"engine": "duckduckgo", "reason": "CAPTCHA"},
        ])

    def test_results_omit_unresponsive_engines(self):
        out = _run({
            "results": [{"title": "T", "url": "https://x.test", "content": "S", "engine": "wikipedia"}],
            "unresponsive_engines": [["brave", "Suspended: too many requests"]],
        })
        self.assertEqual(out["count"], 1)
        self.assertNotIn("unresponsive_engines", out)

    def test_empty_result_without_engine_errors(self):
        out = _run({"results": [], "unresponsive_engines": []})
        self.assertEqual(out["count"], 0)
        self.assertNotIn("unresponsive_engines", out)


if __name__ == "__main__":
    unittest.main()
