"""TD-LOCAL-MEDIA-001 — mocked sd-cli adapter (no live paint, no GGUF download).

Run from core-infra::

    python -m unittest harness.tests.test_local_media_sdcpp
"""

from __future__ import annotations

import os
import subprocess
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
from model_drivers.errors import DriverError  # noqa: E402
from model_drivers.libraries import local_media_image_out_sdcpp as sdcpp  # noqa: E402
from model_drivers.registry import (  # noqa: E402
    ADAPTERS,
    catalog_driver_enrichment,
    resolve_model_driver,
)
from model_media_ingest import resolve_bundle_dir  # noqa: E402
from provider_runtime import resolve_provider_route  # noqa: E402

MODELS_INI = os.path.join(os.path.dirname(CORE_INFRA), "models.ini")

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16


def _bundle(tmp: str) -> str:
    os.makedirs(tmp, exist_ok=True)
    for name in (
        "qwen-image-2512-Q8_0.gguf",
        "Qwen2.5-VL-7B-Instruct-UD-Q4_K_XL.gguf",
        "qwen_image_vae.safetensors",
    ):
        with open(os.path.join(tmp, name), "wb") as fh:
            fh.write(b"stub")
    return tmp


class TestResolveBundleDir(unittest.TestCase):
    def test_rejects_slash(self):
        with self.assertRaises(Exception):
            resolve_bundle_dir("../escape")


class TestSdcppAdapter(unittest.TestCase):
    def test_adapter_registered(self):
        self.assertIn(sdcpp.ADAPTER_ID, ADAPTERS)

    def test_missing_bundle(self):
        with self.assertRaises(DriverError) as ctx:
            sdcpp.generate(prompt="a cat", config={"bundle_dir": "/no/such/bundle"})
        self.assertEqual(ctx.exception.code, "bundle_missing")

    def test_missing_prompt(self):
        with tempfile.TemporaryDirectory() as tmp:
            _bundle(tmp)
            with self.assertRaises(DriverError) as ctx:
                sdcpp.generate(prompt="  ", config={"bundle_dir": tmp})
            self.assertEqual(ctx.exception.code, "prompt_required")

    def test_paints_png_via_mock_runner(self):
        captured: list[list[str]] = []

        def runner(cmd, **_kwargs):
            captured.append(cmd)
            out = cmd[cmd.index("-o") + 1]
            with open(out, "wb") as fh:
                fh.write(PNG)
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        with tempfile.TemporaryDirectory() as tmp:
            _bundle(tmp)
            out_dir = os.path.join(tmp, "out")
            art = sdcpp.generate(
                prompt="a red bicycle",
                config={
                    "bundle_dir": tmp,
                    "out_dir": out_dir,
                    "offload": True,
                    "sd_cli": "versa-agi-sd-cli",
                },
                runner=runner,
            )
        self.assertEqual(art.ext, "png")
        self.assertEqual(art.data[:4], b"\x89PNG")
        cmd = captured[0]
        self.assertEqual(cmd[0], "versa-agi-sd-cli")
        self.assertIn("--offload-to-cpu", cmd)
        self.assertEqual(cmd[cmd.index("-W") + 1], "768")
        self.assertEqual(cmd[cmd.index("-H") + 1], "768")
        self.assertIn("--diffusion-model", cmd)
        self.assertIn("--llm", cmd)
        self.assertIn("--vae", cmd)
        self.assertNotIn("--seed", cmd)

    def test_explicit_seed_is_passed(self):
        captured: list[list[str]] = []

        def runner(cmd, **_kwargs):
            captured.append(cmd)
            out = cmd[cmd.index("-o") + 1]
            with open(out, "wb") as fh:
                fh.write(PNG)
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        with tempfile.TemporaryDirectory() as tmp:
            _bundle(tmp)
            art = sdcpp.generate(
                prompt="a red bicycle",
                config={"bundle_dir": tmp, "seed": 42},
                runner=runner,
            )
        self.assertEqual(captured[0][captured[0].index("--seed") + 1], "42")
        self.assertEqual((art.usage or {}).get("seed"), 42)

    def test_failed_cli(self):
        def runner(_cmd, **_kwargs):
            return subprocess.CompletedProcess(
                args=["versa-agi-sd-cli"], returncode=2, stdout="", stderr="OOM",
            )

        with tempfile.TemporaryDirectory() as tmp:
            _bundle(tmp)
            with self.assertRaises(DriverError) as ctx:
                sdcpp.generate(
                    prompt="x",
                    config={"bundle_dir": tmp, "sd_cli": "versa-agi-sd-cli"},
                    runner=runner,
                )
        self.assertEqual(ctx.exception.code, "generation_failed")


class TestQwenImageMe5(unittest.TestCase):
    def test_binding_and_diamond(self):
        catalog = load_catalog(MODELS_INI)
        providers = load_providers(MODELS_INI)
        self.assertEqual(catalog["qwen-image-2512"]["provider"], "local_media")
        self.assertIn("image", catalog["qwen-image-2512"]["output_modalities"])
        self.assertIn("local_media", providers)
        resolved = resolve_model_driver(
            "qwen-image-2512",
            "output",
            "image",
            catalog=catalog,
            providers=providers,
        )
        self.assertIsNotNone(resolved)
        self.assertEqual(resolved.adapter.adapter_id, sdcpp.ADAPTER_ID)
        enrich = catalog_driver_enrichment(
            "qwen-image-2512",
            catalog["qwen-image-2512"],
            catalog=catalog,
            providers=providers,
        )
        self.assertEqual(enrich["driver_badges"]["output"]["image"], "◆")

    def test_flux_binding_and_diamond(self):
        catalog = load_catalog(MODELS_INI)
        providers = load_providers(MODELS_INI)
        self.assertEqual(catalog["flux1-dev"]["provider"], "local_media")
        resolved = resolve_model_driver(
            "flux1-dev",
            "output",
            "image",
            catalog=catalog,
            providers=providers,
        )
        self.assertIsNotNone(resolved)
        self.assertEqual(resolved.adapter.adapter_id, sdcpp.ADAPTER_ID)

    def test_flux_uses_clip_and_t5(self):
        captured: list[list[str]] = []

        def runner(cmd, **_kwargs):
            captured.append(cmd)
            out = cmd[cmd.index("-o") + 1]
            with open(out, "wb") as fh:
                fh.write(PNG)
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        with tempfile.TemporaryDirectory() as tmp:
            for name in (
                "flux1-dev-Q8_0.gguf",
                "clip_l.safetensors",
                "t5xxl_fp16.safetensors",
                "ae.safetensors",
            ):
                with open(os.path.join(tmp, name), "wb") as fh:
                    fh.write(b"stub")
            with open(os.path.join(tmp, "bundle.json"), "w", encoding="utf-8") as fh:
                fh.write('{"recipe":"flux1_dev","components":[]}')
            sdcpp.generate(
                prompt="a red bicycle",
                config={"bundle_dir": tmp, "out_dir": os.path.join(tmp, "out")},
                runner=runner,
            )
        cmd = captured[0]
        self.assertEqual(cmd[cmd.index("--steps") + 1], "20")
        self.assertEqual(cmd[cmd.index("--cfg-scale") + 1], "1.0")
        self.assertIn("--clip_l", cmd)
        self.assertIn("--t5xxl", cmd)
        self.assertNotIn("--llm", cmd)
        self.assertIn("--clip-on-cpu", cmd)
        self.assertNotIn("--flow-shift", cmd)

    def test_local_media_route(self):
        route = resolve_provider_route(
            "qwen-image-2512",
            catalog={"qwen-image-2512": {"provider": "local_media", "enabled": True}},
            providers={"local_media": {"cls": "", "enabled": True}},
        )
        self.assertTrue(route.local)
        self.assertEqual(route.provider_slug, "local_media")
        self.assertEqual(route.endpoint, "")

    def test_generate_media_mocked(self):
        catalog = load_catalog(MODELS_INI)
        providers = load_providers(MODELS_INI)

        def fake_run(cmd, **_kwargs):
            out = cmd[cmd.index("-o") + 1]
            with open(out, "wb") as fh:
                fh.write(PNG)
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        with tempfile.TemporaryDirectory() as tmp:
            bundle = _bundle(tmp)
            with (
                patch(
                    "model_drivers.registry.load_catalog",
                    return_value=catalog,
                ),
                patch(
                    "model_drivers.registry.load_providers",
                    return_value=providers,
                ),
                patch.object(sdcpp.subprocess, "run", side_effect=fake_run),
            ):
                data, ext, mime, _transcript = generate_media(
                    "qwen-image-2512",
                    "image",
                    prompt="a crayon cat",
                    config={"bundle_dir": bundle, "width": 768, "height": 768},
                )
        self.assertEqual(ext, "png")
        self.assertEqual(mime, "image/png")
        self.assertEqual(data[:4], b"\x89PNG")


if __name__ == "__main__":
    unittest.main()
