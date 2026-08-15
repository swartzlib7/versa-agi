"""TD-LOCAL-MEDIA-001 — media bundle plan, guards, mocked import.

Do not download Qwen-Image / VAE files on the development machine.

Run from core-infra::

    python -m unittest harness.tests.test_local_media_import
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

CORE_INFRA = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)
sys.path.insert(0, CORE_INFRA)

from model_hf_ingest import (  # noqa: E402
    CLASS_CHAT,
    CLASS_MEDIA,
    CLASS_UNKNOWN,
    HfFile,
    InspectResult,
    HfSource,
)
from model_media_ingest import (  # noqa: E402
    CATALOG_KEY_FLUX,
    CATALOG_KEY_QWEN_IMAGE,
    FLUX_CLIP_FILE,
    FLUX_DIT_FILE,
    FLUX_T5_FILE,
    FLUX_VAE_FILE,
    RECIPE_FLUX,
    MEDIA_STORE,
    RECIPE_QWEN_IMAGE,
    ROLE_DIT,
    ROLE_TEXT_ENCODER,
    ROLE_VAE,
    inspect_media_source,
    list_hf_media_recipes,
    load_bundle_manifest,
    media_import_block_reason,
    media_usage,
    plan_media_bundle,
    recipe_generate_defaults,
    remove_media_bundle_dir,
    rename_media_bundle_dir,
    topology_media_import_block_reason,
    validate_component_file,
)

QWEN_URI = "hf://unsloth/Qwen-Image-2512-GGUF/qwen-image-2512-Q8_0.gguf"
GEMMA_URI = "hf://unsloth/gemma-4-E4B-it-GGUF/gemma-4-E4B-it-Q4_K_M.gguf"


def _inspect(classification, filename, repo="unsloth/Qwen-Image-2512-GGUF"):
    return InspectResult(
        source=HfSource(repo_id=repo, filename=filename, original=f"hf://{repo}/{filename}"),
        classification=classification,
        selected_file=HfFile(path=filename, size=20_000_000_000),
        files=[HfFile(path=filename, size=20_000_000_000)],
    )


class TestMediaGuards(unittest.TestCase):
    def test_refuse_chat_as_media(self):
        reason = media_import_block_reason(CLASS_CHAT, "media")
        self.assertIsNotNone(reason)
        self.assertIn("sycl import", reason)

    def test_refuse_wrong_runtime(self):
        reason = media_import_block_reason(CLASS_MEDIA, "chat")
        self.assertIsNotNone(reason)
        self.assertIn("--runtime media", reason)

    def test_media_ok(self):
        self.assertIsNone(media_import_block_reason(CLASS_MEDIA, "media"))

    def test_unknown_needs_confirm(self):
        self.assertIsNotNone(media_import_block_reason(CLASS_UNKNOWN, "media"))
        self.assertIsNone(
            media_import_block_reason(CLASS_UNKNOWN, "media", confirm_unknown=True)
        )

    def test_client_topology_blocked(self):
        reason = topology_media_import_block_reason("client")
        self.assertIsNotNone(reason)
        self.assertIn("GPU host", reason)

    def test_local_and_server_ok_any_backend(self):
        self.assertIsNone(topology_media_import_block_reason("local"))
        self.assertIsNone(topology_media_import_block_reason("server"))


class TestQwenBundlePlan(unittest.TestCase):
    def test_three_roles(self):
        plan = plan_media_bundle(
            _inspect(CLASS_MEDIA, "qwen-image-2512-Q8_0.gguf"),
            dest_key="qwen-image-2512",
        )
        self.assertIsNotNone(plan)
        self.assertEqual(plan.recipe, RECIPE_QWEN_IMAGE)
        self.assertEqual(plan.provider, "local_media")
        self.assertEqual(plan.runtime, "sd-cli")
        self.assertEqual(plan.store_dir, os.path.join(MEDIA_STORE, "qwen-image-2512"))
        roles = [c.role for c in plan.components]
        self.assertEqual(roles, [ROLE_DIT, ROLE_TEXT_ENCODER, ROLE_VAE])
        vae = plan.components[2]
        self.assertEqual(vae.validate, "safetensors")
        self.assertIn("qwen_image_vae.safetensors", vae.filename)
        te = plan.components[1]
        self.assertEqual(te.repo, "unsloth/Qwen2.5-VL-7B-Instruct-GGUF")

    def test_default_dest_key(self):
        plan = plan_media_bundle(
            _inspect(CLASS_MEDIA, "qwen-image-2512-Q8_0.gguf"),
        )
        self.assertEqual(plan.catalog_key_hint, CATALOG_KEY_QWEN_IMAGE)
        self.assertEqual(plan.store_dir, os.path.join(MEDIA_STORE, CATALOG_KEY_QWEN_IMAGE))

    def test_q4k_warns(self):
        plan = plan_media_bundle(
            _inspect(CLASS_MEDIA, "qwen-image-2512-Q4_K_M.gguf"),
        )
        self.assertTrue(any("Q4_K" in w or "black" in w for w in plan.warnings))

    def test_chat_has_no_plan(self):
        plan = plan_media_bundle(
            _inspect(CLASS_CHAT, "gemma-4-E4B-it-Q4_K_M.gguf", "unsloth/gemma-4-E4B-it-GGUF"),
        )
        self.assertIsNone(plan)


class TestFluxBundlePlan(unittest.TestCase):
    def test_pinned_four_roles(self):
        plan = plan_media_bundle(
            _inspect(CLASS_MEDIA, "flux1-dev-Q4_K_M.gguf", "unsloth/FLUX.1-dev-GGUF"),
        )
        self.assertIsNotNone(plan)
        self.assertEqual(plan.recipe, RECIPE_FLUX)
        self.assertEqual(plan.catalog_key_hint, CATALOG_KEY_FLUX)
        roles = [c.role for c in plan.components]
        self.assertEqual(roles, ["dit", "clip_l", "t5xxl", "vae"])
        dit, clip_l, t5, vae = plan.components
        self.assertEqual(dit.filename, FLUX_DIT_FILE)
        self.assertEqual(dit.repo, "unsloth/FLUX.1-dev-GGUF")
        self.assertEqual(clip_l.filename, FLUX_CLIP_FILE)
        self.assertEqual(t5.filename, FLUX_T5_FILE)
        self.assertEqual(vae.filename, FLUX_VAE_FILE)
        self.assertEqual(vae.validate, "safetensors")
        self.assertTrue(any("non-commercial" in w.lower() for w in plan.warnings))
        self.assertTrue(any("gated" in w.lower() for w in plan.warnings))

    def test_recipe_defaults(self):
        qwen = recipe_generate_defaults("qwen-image-2512")
        self.assertEqual(qwen["steps"], 40)
        self.assertEqual(qwen["cfg_scale"], 2.5)
        flux = recipe_generate_defaults("flux1-dev")
        self.assertEqual(flux["steps"], 20)
        self.assertEqual(flux["cfg_scale"], 1.0)


class TestInspectMediaSource(unittest.TestCase):
    def test_qwen_plan_from_hub_mock(self):
        def fetch(url: str):
            if url.endswith("/api/models/unsloth/Qwen-Image-2512-GGUF"):
                return {
                    "id": "unsloth/Qwen-Image-2512-GGUF",
                    "pipeline_tag": "text-to-image",
                    "tags": ["text-to-image", "gguf"],
                    "siblings": [
                        {"rfilename": "qwen-image-2512-Q8_0.gguf", "size": 20_000_000_000},
                    ],
                }
            if "/tree/" in url:
                return [{"path": "qwen-image-2512-Q8_0.gguf", "type": "file", "size": 20_000_000_000}]
            raise AssertionError(url)

        with patch("model_hf_ingest.default_fetch_json", fetch):
            payload = inspect_media_source(QWEN_URI, dest_key="qwen-image-2512")
        self.assertEqual(payload["classification"], CLASS_MEDIA)
        self.assertTrue(payload["media_import_ok"])
        self.assertEqual(len(payload["bundle"]["components"]), 3)
        self.assertIn("TD-LOCAL-MEDIA-001", payload["next_step"])
        self.assertIn("model media inspect", payload["next_step"])

    def test_chat_blocked_for_media_import(self):
        def fetch(url: str):
            if url.endswith("/api/models/unsloth/gemma-4-E4B-it-GGUF"):
                return {
                    "id": "unsloth/gemma-4-E4B-it-GGUF",
                    "pipeline_tag": "text-generation",
                    "tags": ["conversational"],
                    "siblings": [
                        {"rfilename": "gemma-4-E4B-it-Q4_K_M.gguf", "size": 5_000_000_000},
                    ],
                }
            if "/tree/" in url:
                return [{"path": "gemma-4-E4B-it-Q4_K_M.gguf", "type": "file", "size": 5_000_000_000}]
            raise AssertionError(url)

        with patch("model_hf_ingest.default_fetch_json", fetch):
            payload = inspect_media_source(GEMMA_URI)
        self.assertFalse(payload["media_import_ok"])
        self.assertIn("sycl import", payload.get("media_import_block", ""))


class TestValidateComponent(unittest.TestCase):
    def test_gguf_magic(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "dit.gguf")
            with open(path, "wb") as fh:
                fh.write(b"GGUF" + b"\x00" * 8)
            validate_component_file(path, "gguf")

    def test_vae_rejects_gguf_magic(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "vae.safetensors")
            with open(path, "wb") as fh:
                fh.write(b"GGUF" + b"\x00" * 8)
            with self.assertRaises(Exception):
                validate_component_file(path, "safetensors")


class TestUnionSection(unittest.TestCase):
    def test_media_bundles_reconciled(self):
        sys.path.insert(0, os.path.join(CORE_INFRA, "agictl"))
        from cli import _MODELS_UNION_SECTIONS  # noqa: WPS433

        self.assertIn("media_bundles", _MODELS_UNION_SECTIONS)


class TestMediaUsage(unittest.TestCase):
    def test_default_key(self):
        payload = media_usage()
        self.assertEqual(payload["catalog_key"], CATALOG_KEY_QWEN_IMAGE)
        self.assertEqual(payload["default_width"], 768)
        self.assertIn("local_media_qwen_image_2512.md", payload["skill"])
        self.assertIn("huggingface.co/unsloth/Qwen-Image-2512-GGUF", payload["sources"]["card"])
        self.assertIn("text-in-image", payload["prompt_tips"])

    def test_alias_old_key(self):
        self.assertEqual(media_usage("qwen-image")["catalog_key"], CATALOG_KEY_QWEN_IMAGE)

    def test_unknown(self):
        with self.assertRaises(Exception):
            media_usage("minimax-h3")

    def test_krea2_gone(self):
        with self.assertRaises(Exception):
            media_usage("krea2-turbo")

    def test_flux(self):
        payload = media_usage("flux1-dev")
        self.assertEqual(payload["catalog_key"], CATALOG_KEY_FLUX)
        self.assertEqual(payload["steps"], 20)
        self.assertEqual(payload["cfg_scale"], 1.0)
        self.assertIn("local_media_flux1_dev.md", payload["skill"])
        self.assertIn(FLUX_DIT_FILE, payload["dit"])

    def test_recipe_list_has_qwen_and_flux(self):
        ids = [row["id"] for row in list_hf_media_recipes()]
        self.assertIn(CATALOG_KEY_QWEN_IMAGE, ids)
        self.assertNotIn("krea2-turbo", ids)
        self.assertIn(CATALOG_KEY_FLUX, ids)


class TestMediaRename(unittest.TestCase):
    def test_moves_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            old = os.path.join(tmp, "qwen-image")
            os.makedirs(old)
            open(os.path.join(old, "bundle.json"), "w").close()
            result = rename_media_bundle_dir("qwen-image", "qwen-image-2512", store=tmp)
            self.assertEqual(result["dir_action"], "move")
            self.assertTrue(os.path.isdir(os.path.join(tmp, "qwen-image-2512")))
            self.assertFalse(os.path.isdir(old))

    def test_already_renamed(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "qwen-image-2512"))
            result = rename_media_bundle_dir("qwen-image", "qwen-image-2512", store=tmp)
            self.assertEqual(result["dir_action"], "already")

    def test_collision(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "qwen-image"))
            os.makedirs(os.path.join(tmp, "qwen-image-2512"))
            with self.assertRaises(Exception):
                rename_media_bundle_dir("qwen-image", "qwen-image-2512", store=tmp)

    def test_remove_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = os.path.join(tmp, "krea2-turbo")
            os.makedirs(target)
            open(os.path.join(target, "bundle.json"), "w").close()
            result = remove_media_bundle_dir("krea2-turbo", store=tmp)
            self.assertEqual(result["dir_action"], "removed")
            self.assertFalse(os.path.isdir(target))

    def test_remove_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = remove_media_bundle_dir("krea2-turbo", store=tmp)
            self.assertEqual(result["dir_action"], "missing")

    def test_load_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "bundle.json")
            with open(path, "w", encoding="utf-8") as fh:
                json.dump({"recipe": "qwen_image_2512", "class": "media_pipeline"}, fh)
            self.assertEqual(load_bundle_manifest(tmp)["recipe"], "qwen_image_2512")


if __name__ == "__main__":
    unittest.main()
