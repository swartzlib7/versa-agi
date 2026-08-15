"""Client media SSH + PNG return helpers (no live SSH, no GGUF download)."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

CORE_INFRA = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)
sys.path.insert(0, CORE_INFRA)

from harness.generation import generate_media  # noqa: E402
from model_catalog import load_catalog, load_providers  # noqa: E402
from model_media_remote import (  # noqa: E402
    build_gpu_host_scp_cmd,
    build_media_generate_args,
    format_elapsed,
    is_ssh_noise,
    local_bundle_ready,
    parse_agictl_json,
    remote_media_generate,
    split_progress_lines,
)

MODELS_INI = os.path.join(os.path.dirname(CORE_INFRA), "models.ini")
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16


class TestProgressHelpers(unittest.TestCase):
    def test_split_cr_progress(self):
        lines, rest = split_progress_lines("Downloading dit: a.gguf\r  10%\r  40%\n")
        self.assertEqual(lines, ["Downloading dit: a.gguf", "10%", "40%"])
        self.assertEqual(rest, "")

    def test_elapsed(self):
        self.assertEqual(format_elapsed(9), "9s")
        self.assertEqual(format_elapsed(75), "1m 15s")

    def test_ssh_known_hosts_is_noise(self):
        self.assertTrue(
            is_ssh_noise(
                "Warning: Permanently added '192.168.4.114' (ED25519) to the list of known hosts."
            )
        )
        self.assertFalse(is_ssh_noise("Downloading dit: flux1-dev-Q8_0.gguf"))

    def test_parse_trailing_json(self):
        data = parse_agictl_json("Downloading dit: x\n{\"success\": true, \"path\": \"/tmp/a.png\"}\n")
        self.assertTrue(data["success"])
        self.assertEqual(data["path"], "/tmp/a.png")


class TestGenerateArgs(unittest.TestCase):
    def test_cfg_zero_is_sent(self):
        args = build_media_generate_args(
            "flux1-dev",
            "a scene",
            "/tmp/out.png",
            cfg_scale=0,
        )
        self.assertEqual(args[args.index("--cfg-scale") + 1], "0")

    def test_seed_is_sent(self):
        args = build_media_generate_args(
            "flux1-dev",
            "a scene",
            "/tmp/out.png",
            seed=42,
        )
        self.assertEqual(args[args.index("--seed") + 1], "42")
        self.assertNotIn("--seed", build_media_generate_args("flux1-dev", "a", "/tmp/o.png"))

    def test_scp_argv(self):
        cmd = build_gpu_host_scp_cmd(
            "/tmp/versa-agi-media-out/qwen.png",
            "/tmp/local.png",
            tunnel_host="192.168.4.114",
            ssh_key="/home/watchdog/.ssh/versa_agi_ed25519",
        )
        self.assertEqual(cmd[:4], ["sudo", "-u", "watchdog", "scp"])
        self.assertIn("LogLevel=ERROR", cmd)
        self.assertIn("watchdog@192.168.4.114:/tmp/versa-agi-media-out/qwen.png", cmd)
        self.assertEqual(cmd[-1], "/tmp/local.png")


class TestLocalBundleReady(unittest.TestCase):
    def test_missing_dir(self):
        self.assertFalse(local_bundle_ready(""))
        self.assertFalse(local_bundle_ready("/no/such/media-bundle"))

    def test_gguf_stub(self):
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "x.gguf"), "wb") as fh:
                fh.write(b"stub")
            self.assertTrue(local_bundle_ready(tmp))


class TestRemoteGenerate(unittest.TestCase):
    def test_ssh_then_scp_writes_dest(self):
        calls = []

        def run_fn(cmd, timeout=0):
            calls.append(cmd)
            if "scp" in cmd:
                dest = cmd[-1]
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                with open(dest, "wb") as fh:
                    fh.write(PNG)
                return SimpleNamespace(returncode=0, stdout="", stderr="")
            if cmd[-1].startswith("rm -f"):
                return SimpleNamespace(returncode=0, stdout="", stderr="")
            return SimpleNamespace(
                returncode=0,
                stdout='{"success": true, "path": "/tmp/remote.png", "width": 768}\n',
                stderr="",
            )

        with tempfile.TemporaryDirectory() as tmp:
            dest = os.path.join(tmp, "out.png")
            payload = remote_media_generate(
                "qwen-image-2512",
                "a crayon cat",
                dest,
                topology="client",
                tunnel_host="192.168.4.114",
                run_fn=run_fn,
            )
            self.assertTrue(payload["success"])
            self.assertTrue(payload["returned"])
            self.assertEqual(payload["path"], dest)
            self.assertGreaterEqual(len(calls), 2)
            self.assertIn("ssh", calls[0])
            self.assertIn("model media generate", calls[0][-1])
            self.assertIn("scp", calls[1])
            with open(dest, "rb") as fh:
                self.assertEqual(fh.read()[:4], b"\x89PNG")


class TestGenerateMediaClient(unittest.TestCase):
    def test_client_without_bundle_returns_remote_png(self):
        catalog = load_catalog(MODELS_INI)
        providers = load_providers(MODELS_INI)

        def fake_remote(name, prompt, dest, **_kwargs):
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            with open(dest, "wb") as fh:
                fh.write(PNG)
            return {"success": True, "path": dest, "bytes": len(PNG)}

        with (
            patch("model_drivers.registry.load_catalog", return_value=catalog),
            patch("model_drivers.registry.load_providers", return_value=providers),
            patch("model_media_remote.is_client_topology", return_value=True),
            patch("model_media_remote.local_bundle_ready", return_value=False),
            patch("model_media_remote.remote_media_generate", side_effect=fake_remote),
        ):
            data, ext, mime, _transcript = generate_media(
                "qwen-image-2512",
                "image",
                prompt="a crayon cat",
                config={"width": 768, "height": 768},
            )
        self.assertEqual(ext, "png")
        self.assertEqual(mime, "image/png")
        self.assertEqual(data[:4], b"\x89PNG")


if __name__ == "__main__":
    unittest.main()
