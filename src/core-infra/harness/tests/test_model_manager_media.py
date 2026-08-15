"""ME-6 — Model Manager media wizard helpers (no live download)."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest

CORE_INFRA = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)
sys.path.insert(0, CORE_INFRA)
sys.path.insert(0, os.path.join(CORE_INFRA, "agitop", "panels"))

from media_wizard import (  # noqa: E402
    _LOCAL_CATALOG_PROVIDERS,
    build_gpu_host_agictl_cmd,
    catalog_prefill_from_hf_recipe,
    media_form_prefill,
    media_import_failure_hint,
    media_import_ui_block,
    media_wizard_summary,
    read_local_ai_topology,
)
from model_hf_ingest import CLASS_MEDIA, gguf_registry_blocked  # noqa: E402
from model_media_ingest import (  # noqa: E402
    CATALOG_KEY_QWEN_IMAGE,
    list_hf_media_recipes,
)


def _media_payload():
    return {
        "classification": CLASS_MEDIA,
        "media_import_ok": True,
        "source": {"repo_id": "unsloth/Qwen-Image-2512-GGUF"},
        "bundle": {
            "recipe": "qwen_image_2512",
            "catalog_key_hint": "qwen-image-2512",
            "provider": "local_media",
            "store_dir": "/opt/versa-agi/media-models/qwen-image-2512",
            "components": [
                {"role": "dit", "filename": "qwen-image-2512-Q8_0.gguf"},
                {"role": "text_encoder", "filename": "Qwen2.5-VL-7B-Instruct-UD-Q4_K_XL.gguf"},
                {"role": "vae", "filename": "split_files/vae/qwen_image_vae.safetensors"},
            ],
            "warnings": ["Q4_K quants can paint black"],
        },
    }


class TestMediaWizardHelpers(unittest.TestCase):
    def test_prefill(self):
        prefill = media_form_prefill(_media_payload())
        self.assertEqual(prefill["key"], "qwen-image-2512")
        self.assertEqual(prefill["provider"], "local_media")
        self.assertEqual(prefill["class"], "local")
        self.assertEqual(prefill["output_modalities"], "image")
        self.assertFalse(prefill["router_eligible"])

    def test_summary_lists_roles(self):
        text = media_wizard_summary(_media_payload(), topology="local")
        self.assertIn("dit:", text)
        self.assertIn("text_encoder:", text)
        self.assertIn("vae:", text)
        self.assertIn("Media Import", text)
        self.assertIn("Q4_K", text)

    def test_sycl_save_still_blocked(self):
        err = gguf_registry_blocked({"classification": CLASS_MEDIA, "next_step": "TD-LOCAL-MEDIA-001"})
        self.assertIsNotNone(err)
        self.assertIn("TD-LOCAL-MEDIA-001", err)

    def test_local_media_is_allowed_provider(self):
        self.assertIn("local_media", _LOCAL_CATALOG_PROVIDERS)

    def test_cli_on_client_still_refuses_local_download(self):
        reason = media_import_ui_block("client")
        self.assertIsNotNone(reason)
        self.assertIn("GPU host", reason)
        self.assertIsNone(media_import_ui_block("local"))
        self.assertIsNone(media_import_ui_block("server"))

    def test_client_summary_says_ssh(self):
        text = media_wizard_summary(_media_payload(), topology="client")
        self.assertIn("SSH", text)
        self.assertNotIn("Client media refresh is not shipped", text)

    def test_gpu_host_cmd_local(self):
        cmd = build_gpu_host_agictl_cmd(
            ["model", "media", "import", "hf://x/y", "--name", "qwen-image-2512", "--runtime", "media"],
            topology="local",
        )
        self.assertEqual(cmd[:2], ["sudo", "agictl"])
        self.assertNotIn("ssh", cmd)

    def test_gpu_host_cmd_client_ssh(self):
        source = "hf://unsloth/Qwen-Image-2512-GGUF/qwen-image-2512-Q8_0.gguf"
        cmd = build_gpu_host_agictl_cmd(
            ["model", "media", "import", source, "--name", "qwen-image-2512", "--runtime", "media"],
            topology="client",
            tunnel_host="192.168.4.114",
            ssh_key="/home/watchdog/.ssh/versa_agi_ed25519",
        )
        self.assertEqual(cmd[:4], ["sudo", "-u", "watchdog", "ssh"])
        self.assertIn("watchdog@192.168.4.114", cmd)
        self.assertIn("UserKnownHostsFile=/dev/null", cmd)
        self.assertIn("LogLevel=ERROR", cmd)
        remote = cmd[-1]
        self.assertIn("sudo -n agictl", remote)
        self.assertIn("--runtime media", remote)
        self.assertIn("qwen-image-2512", remote)

    def test_import_failure_hint_sudoers(self):
        hint = media_import_failure_hint(
            "sudo: a terminal is required to read the password\nsudo: a password is required"
        )
        self.assertIn("model media", hint)
        self.assertIn("--update", hint)

    def test_gpu_host_cmd_client_needs_host(self):
        with self.assertRaises(ValueError):
            build_gpu_host_agictl_cmd(["model", "media", "runtime"], topology="client")

    def test_hf_recipe_list(self):
        rows = list_hf_media_recipes()
        self.assertTrue(rows)
        qwen = next(r for r in rows if r["id"] == CATALOG_KEY_QWEN_IMAGE)
        self.assertEqual(qwen["provider"], "local_media")
        self.assertTrue(qwen["source"].startswith("hf://"))
        self.assertEqual(qwen["kind"], "media")
        prefill = catalog_prefill_from_hf_recipe(qwen)
        self.assertEqual(prefill["key"], CATALOG_KEY_QWEN_IMAGE)
        self.assertEqual(prefill["provider"], "local_media")
        self.assertTrue(prefill["hf_source"].startswith("hf://"))
        self.assertEqual(prefill["kind"], "media")
        self.assertFalse(any(r["id"] == "krea2-turbo" for r in rows))
        from model_media_ingest import CATALOG_KEY_FLUX

        flux = next(r for r in rows if r["id"] == CATALOG_KEY_FLUX)
        self.assertIn("flux1-dev-Q8_0.gguf", flux["source"])
        self.assertEqual(catalog_prefill_from_hf_recipe(flux)["key"], CATALOG_KEY_FLUX)

    def test_read_topology(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "setup.ini")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("[local_ai]\ntopology = client\n")
            self.assertEqual(read_local_ai_topology(path), "client")


if __name__ == "__main__":
    unittest.main()
