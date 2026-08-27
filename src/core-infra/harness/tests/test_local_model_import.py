"""TD-LOCAL-ADD-UX-001 — HF inspect, media refusal, chat-only SYCL import.

Source tests mock Hub/network/download. Do not download GGUFs on the
development machine.

Run from core-infra::

    python -m unittest harness.tests.test_local_model_import
"""

from __future__ import annotations

import json
import os
import shutil
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
    CLASS_VLM,
    MEDIA_NEXT_STEP,
    HfIngestError,
    HfFile,
    InspectResult,
    activation_block_reason,
    activate_needs_docker_restart,
    activate_needs_mmproj_reload,
    builtin_probe_png,
    ensure_sycl_vlm_subdir,
    sycl_vlm_layout_ready,
    catalog_input_modalities_after_probe,
    chat_image_content_parts,
    drop_name_from_csv,
    load_probe_image,
    ensure_name_in_csv,
    paired_mmproj_name,
    plan_sycl_additionals,
    plan_sycl_remove,
    resolve_activate_parallel,
    select_mmproj_file,
    install_paired_file,
    sycl_remove_block_reason,
    atomic_move_into,
    classify_hf_model,
    inspect_hf_source,
    load_sycl_meta,
    meta_value,
    migrate_skip_reason,
    parse_hf_source,
    size_gb_from_bytes,
    size_gb_from_path,
    sycl_import_block_reason,
    topology_import_block_reason,
    validate_gguf_file,
)


QWEN_IMAGE_URI = "hf://unsloth/Qwen-Image-2512-GGUF/qwen-image-2512-Q8_0.gguf"
MINIMAX_URI = "hf://unsloth/MiniMax-H3-GGUF/minimax_h3_fl2va_pruned-Q8_0.gguf"
GEMMA_URI = "hf://unsloth/gemma-4-E4B-it-GGUF/gemma-4-E4B-it-Q4_K_M.gguf"
QWEN38_URI = "hf://unsloth/Qwen3.8-27B-GGUF/Qwen3.8-27B-UD-Q6_K_XL.gguf"


def _hub(repo, pipeline, tags, files):
    def fetch(url: str):
        if url.endswith(f"/api/models/{repo}"):
            return {
                "id": repo,
                "pipeline_tag": pipeline,
                "tags": tags,
                "siblings": [
                    {"rfilename": name, "size": size} for name, size in files
                ],
            }
        if "/tree/" in url:
            return [{"path": name, "type": "file", "size": size} for name, size in files]
        raise AssertionError(f"unexpected fetch {url}")

    return fetch


class TestParseHfSource(unittest.TestCase):
    def test_hf_uri_with_file(self):
        src = parse_hf_source(QWEN_IMAGE_URI)
        self.assertEqual(src.repo_id, "unsloth/Qwen-Image-2512-GGUF")
        self.assertEqual(src.filename, "qwen-image-2512-Q8_0.gguf")

    def test_web_blob_url(self):
        src = parse_hf_source(
            "https://huggingface.co/unsloth/gemma-4-E4B-it-GGUF/blob/main/gemma-4-E4B-it-Q4_K_M.gguf"
        )
        self.assertEqual(src.repo_id, "unsloth/gemma-4-E4B-it-GGUF")
        self.assertEqual(src.revision, "main")
        self.assertEqual(src.filename, "gemma-4-E4B-it-Q4_K_M.gguf")

    def test_web_resolve_url(self):
        src = parse_hf_source(
            "https://huggingface.co/unsloth/MiniMax-H3-GGUF/resolve/main/minimax_h3_fl2va_pruned-Q8_0.gguf"
        )
        self.assertEqual(src.filename, "minimax_h3_fl2va_pruned-Q8_0.gguf")

    def test_bare_repo(self):
        src = parse_hf_source("unsloth/gemma-4-E4B-it-GGUF")
        self.assertEqual(src.repo_id, "unsloth/gemma-4-E4B-it-GGUF")
        self.assertIsNone(src.filename)

    def test_rejects_non_hf_host(self):
        with self.assertRaises(HfIngestError) as ctx:
            parse_hf_source("https://github.com/MiniMaxAI/MiniMax-H3")
        self.assertEqual(ctx.exception.code, "rejected_host")

    def test_rejects_empty(self):
        with self.assertRaises(HfIngestError) as ctx:
            parse_hf_source("  ")
        self.assertEqual(ctx.exception.code, "empty_source")


class TestClassifyAndInspect(unittest.TestCase):
    def test_qwen_image_is_media(self):
        result = inspect_hf_source(
            QWEN_IMAGE_URI,
            fetch_json=_hub(
                "unsloth/Qwen-Image-2512-GGUF",
                "text-to-image",
                ["gguf", "text-to-image", "diffusers"],
                [("qwen-image-2512-Q8_0.gguf", 20 * 1024**3)],
            ),
        )
        self.assertEqual(result.classification, CLASS_MEDIA)
        self.assertEqual(result.size_gb, 20)
        self.assertTrue(
            any("component" in r.lower() or "missing" in r.lower() for r in result.reasons)
        )
        self.assertIsNotNone(sycl_import_block_reason(result.classification, "chat"))

    def test_minimax_h3_is_media(self):
        result = inspect_hf_source(
            MINIMAX_URI,
            fetch_json=_hub(
                "unsloth/MiniMax-H3-GGUF",
                "any-to-any",
                ["gguf", "video", "diffusers"],
                [("minimax_h3_fl2va_pruned-Q8_0.gguf", 18 * 1024**3)],
            ),
        )
        self.assertEqual(result.classification, CLASS_MEDIA)
        self.assertIn("TD-LOCAL-MEDIA-001", result.next_step)

    def test_gemma_chat_gguf(self):
        result = inspect_hf_source(
            GEMMA_URI,
            fetch_json=_hub(
                "unsloth/gemma-4-E4B-it-GGUF",
                "text-generation",
                ["gguf", "conversational", "text-generation"],
                [("gemma-4-E4B-it-Q4_K_M.gguf", 5 * 1024**3)],
            ),
        )
        self.assertEqual(result.classification, CLASS_CHAT)

    def test_qwen38_is_vlm_text_only_import(self):
        result = inspect_hf_source(
            QWEN38_URI,
            fetch_json=_hub(
                "unsloth/Qwen3.8-27B-GGUF",
                "image-text-to-text",
                ["gguf", "conversational"],
                [
                    ("Qwen3.8-27B-UD-Q6_K_XL.gguf", 23 * 1024**3),
                    ("mmproj-F16.gguf", 800 * 1024**2),
                ],
            ),
        )
        self.assertEqual(result.classification, CLASS_VLM)
        self.assertIsNone(sycl_import_block_reason(result.classification, "chat"))
        self.assertTrue(any("mmproj" in w.lower() or "text-only" in w.lower() for w in result.warnings))

    def test_vlm_mmproj(self):
        result = inspect_hf_source(
            "hf://org/vlm/model-Q4.gguf",
            fetch_json=_hub(
                "org/vlm",
                "image-text-to-text",
                ["gguf"],
                [("model-Q4.gguf", 4 * 1024**3), ("mmproj-F16.gguf", 700 * 1024**2)],
            ),
        )
        self.assertEqual(result.classification, CLASS_VLM)
        self.assertIsNone(sycl_import_block_reason(result.classification, "chat"))
        self.assertTrue(any("mmproj" in w.lower() for w in result.warnings))
        self.assertIn("sycl import", result.next_step or "")
        self.assertNotIn("vision import", result.next_step or "")

    def test_unknown_requires_confirm(self):
        result = inspect_hf_source(
            "hf://org/mystery/weights.gguf",
            fetch_json=_hub("org/mystery", None, [], [("weights.gguf", 1024)]),
        )
        self.assertEqual(result.classification, CLASS_UNKNOWN)
        self.assertIsNotNone(sycl_import_block_reason(result.classification, "chat"))
        self.assertIsNone(
            sycl_import_block_reason(result.classification, "chat", confirm_unknown=True)
        )

    def test_filename_safety_net_without_hub_tags(self):
        kind, _reasons = classify_hf_model(
            pipeline_tag=None,
            tags=[],
            architecture=None,
            files=[],
            selected_file="qwen-image-2512-Q8_0.gguf",
        )
        self.assertEqual(kind, CLASS_MEDIA)

    def test_names_alone_do_not_make_chat(self):
        kind, _reasons = classify_hf_model(
            pipeline_tag="text-to-image",
            tags=["gguf"],
            architecture=None,
            files=[HfFile("something-chat-looking.gguf", 10)],
            selected_file="something-chat-looking.gguf",
        )
        self.assertEqual(kind, CLASS_MEDIA)


class TestGuards(unittest.TestCase):
    def test_client_topology_blocked(self):
        reason = topology_import_block_reason("client", "intel")
        self.assertIsNotNone(reason)
        self.assertIn("model refresh", reason)

    def test_local_intel_allowed(self):
        self.assertIsNone(topology_import_block_reason("local", "intel"))
        self.assertIsNone(topology_import_block_reason("server", "intel"))

    def test_standard_backend_blocked(self):
        reason = topology_import_block_reason("local", "standard")
        self.assertIn("Ollama", reason)

    def test_media_activation_blocked(self):
        self.assertIsNotNone(activation_block_reason({"class": CLASS_MEDIA}))
        self.assertIsNone(activation_block_reason(None))
        self.assertIsNone(activation_block_reason({"class": CLASS_CHAT}))

    def test_migrate_skips_media(self):
        self.assertEqual(migrate_skip_reason({"class": CLASS_MEDIA}), "media_pipeline")
        self.assertIsNone(migrate_skip_reason({"class": CLASS_CHAT}))


class TestAtomicImportAndMeta(unittest.TestCase):
    def test_gguf_magic_and_atomic_move(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = os.path.join(tmp, "src.gguf")
            dest_dir = os.path.join(tmp, "sycl")
            with open(src, "wb") as fh:
                fh.write(b"GGUF" + b"\x00" * 16)
            validate_gguf_file(src)
            dest = atomic_move_into(src, dest_dir)
            self.assertTrue(os.path.isfile(dest))
            with open(dest, "rb") as fh:
                self.assertEqual(fh.read(4), b"GGUF")

    def test_non_gguf_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = os.path.join(tmp, "not.gguf")
            with open(src, "wb") as fh:
                fh.write(b"PK\x03\x04")
            with self.assertRaises(HfIngestError) as ctx:
                validate_gguf_file(src)
            self.assertEqual(ctx.exception.code, "not_gguf")

    def test_partial_failure_leaves_no_dest(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = os.path.join(tmp, "src.gguf")
            dest_dir = os.path.join(tmp, "sycl")
            os.makedirs(dest_dir)
            with open(src, "wb") as fh:
                fh.write(b"GGUF" + b"\x00" * 8)
            with patch("model_hf_ingest.shutil.copy2", side_effect=OSError("disk")):
                with self.assertRaises(OSError):
                    atomic_move_into(src, dest_dir)
            self.assertFalse(os.path.isfile(os.path.join(dest_dir, "src.gguf")))
            leftovers = [n for n in os.listdir(dest_dir) if n.endswith(".partial")]
            self.assertEqual(leftovers, [])

    def test_meta_roundtrip_and_reconcile_union_section(self):
        sys.path.insert(0, os.path.join(CORE_INFRA, "agictl"))
        from cli import _MODELS_UNION_SECTIONS, _reconcile_models_ini  # noqa: WPS433

        self.assertIn("sycl_model_meta", _MODELS_UNION_SECTIONS)
        with tempfile.TemporaryDirectory() as tmp:
            template = os.path.join(tmp, "template.ini")
            deployed = os.path.join(tmp, "deployed.ini")
            with open(template, "w") as fh:
                fh.write("[sycl_models]\nstock = org/stock,stock.gguf,5\n\n[sycl_model_meta]\n")
            shutil.copyfile(template, deployed)
            meta = {
                "class": CLASS_CHAT,
                "repo": "unsloth/gemma-4-E4B-it-GGUF",
                "file": "gemma-4-E4B-it-Q4_K_M.gguf",
                "source": GEMMA_URI,
            }
            with open(deployed, "a") as fh:
                fh.write("\ncustom:key = org/custom,custom.gguf,4\n")
                fh.write("\n[sycl_model_meta]\n")
                fh.write(f"custom:key = {meta_value(meta)}\n")
            _reconcile_models_ini(template, deployed)
            loaded = load_sycl_meta(deployed)
            self.assertEqual(loaded["custom:key"]["class"], CLASS_CHAT)
            with open(deployed) as fh:
                text = fh.read()
            self.assertIn("custom:key", text)
            self.assertIn("stock = org/stock", text)


class TestMediaRefusalZeroMutation(unittest.TestCase):
    def test_block_reason_is_media_next_step(self):
        reason = sycl_import_block_reason(CLASS_MEDIA, "chat")
        self.assertEqual(reason, MEDIA_NEXT_STEP)

    def test_ui_helper_blocks_media_gguf_save(self):
        from model_hf_ingest import gguf_registry_blocked  # noqa: WPS433

        err = gguf_registry_blocked({"classification": CLASS_MEDIA, "next_step": MEDIA_NEXT_STEP})
        self.assertIsNotNone(err)
        self.assertIn("TD-LOCAL-MEDIA-001", err)
        self.assertIsNone(gguf_registry_blocked({"classification": CLASS_CHAT}))
        self.assertIsNotNone(gguf_registry_blocked({"classification": CLASS_UNKNOWN}))
        self.assertIsNone(
            gguf_registry_blocked(
                {"classification": CLASS_UNKNOWN}, confirm_unknown=True
            )
        )


class TestCliInspectImport(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, os.path.join(CORE_INFRA, "agictl"))
        from cli import cli as _cli  # noqa: WPS433

        cls.cli = _cli

    def _media_result(self):
        return InspectResult(
            source=parse_hf_source(QWEN_IMAGE_URI),
            classification=CLASS_MEDIA,
            reasons=["media"],
            next_step=MEDIA_NEXT_STEP,
            selected_file=HfFile("qwen-image-2512-Q8_0.gguf", 20 * 1024**3),
        )

    def test_hf_inspect_command(self):
        from click.testing import CliRunner

        fake = InspectResult(
            source=parse_hf_source(GEMMA_URI),
            classification=CLASS_CHAT,
            reasons=["chat"],
        )
        with patch("cli.inspect_hf_source", return_value=fake):
            out = CliRunner().invoke(self.cli, ["model", "hf", "inspect", GEMMA_URI])
        self.assertEqual(out.exit_code, 0, out.output)
        payload = json.loads(out.output.strip().splitlines()[-1])
        self.assertTrue(payload["success"])
        self.assertEqual(payload["classification"], CLASS_CHAT)

    def test_sycl_import_media_does_not_download(self):
        from click.testing import CliRunner

        with patch("cli.inspect_hf_source", return_value=self._media_result()), patch(
            "cli._resolve_topology", return_value="local"
        ), patch("cli._resolve_gpu_backend", return_value="intel"), patch(
            "cli.os.geteuid", return_value=0
        ), patch("cli.default_hf_download") as download:
            out = CliRunner().invoke(
                self.cli,
                [
                    "model", "sycl", "import", QWEN_IMAGE_URI,
                    "--name", "qwen-image", "--runtime", "chat",
                ],
            )
        self.assertNotEqual(out.exit_code, 0, out.output)
        download.assert_not_called()
        payload = json.loads(out.output.strip().splitlines()[-1])
        self.assertFalse(payload["success"])
        self.assertFalse(payload.get("mutated", True))
        self.assertEqual(payload["classification"], CLASS_MEDIA)

    def test_sycl_import_client_topology_blocked(self):
        from click.testing import CliRunner

        with patch("cli._resolve_topology", return_value="client"), patch(
            "cli._resolve_gpu_backend", return_value="intel"
        ), patch("cli.default_hf_download") as download, patch(
            "cli.inspect_hf_source"
        ) as inspect:
            out = CliRunner().invoke(
                self.cli,
                ["model", "sycl", "import", GEMMA_URI, "--name", "gemma4:e4b", "--runtime", "chat"],
            )
        self.assertNotEqual(out.exit_code, 0, out.output)
        download.assert_not_called()
        inspect.assert_not_called()


class TestHfDownloadToken(unittest.TestCase):
    def test_passes_setup_token_in_env(self):
        from model_hf_ingest import default_hf_download

        captured: dict[str, object] = {}

        def fake_run(cmd, **kwargs):
            captured["env"] = kwargs.get("env") or {}
            dest = cmd[cmd.index("--local-dir") + 1]
            path = os.path.join(dest, "ae.safetensors")
            os.makedirs(dest, exist_ok=True)
            open(path, "wb").close()
            return type("R", (), {"returncode": 0})()

        with tempfile.TemporaryDirectory() as tmp, patch(
            "model_hf_ingest.read_hf_token", return_value="hf_test_token"
        ), patch("model_hf_ingest.subprocess.run", side_effect=fake_run):
            path = default_hf_download(
                "black-forest-labs/FLUX.1-dev", "ae.safetensors", tmp, "hf"
            )
            self.assertTrue(os.path.isfile(path))
        env = captured["env"]
        self.assertEqual(env["HF_TOKEN"], "hf_test_token")
        self.assertEqual(env["HUGGING_FACE_HUB_TOKEN"], "hf_test_token")

    def test_gated_failure_names_license(self):
        from model_hf_ingest import default_hf_download

        def fake_run(*_args, **_kwargs):
            return type("R", (), {"returncode": 1})()

        with tempfile.TemporaryDirectory() as tmp, patch(
            "model_hf_ingest.read_hf_token", return_value="hf_test_token"
        ), patch("model_hf_ingest.subprocess.run", side_effect=fake_run):
            with self.assertRaises(HfIngestError) as ctx:
                default_hf_download(
                    "black-forest-labs/FLUX.1-dev", "ae.safetensors", tmp, "hf"
                )
        self.assertIn("gated", ctx.exception.message)
        self.assertIn("license", ctx.exception.message)


class TestActivateSizeAndRestart(unittest.TestCase):
    def test_hub_null_size_falls_back(self):
        self.assertEqual(size_gb_from_bytes(None, fallback=1), 1)

    def test_on_disk_size_beats_hub_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "weights.gguf")
            with open(path, "wb") as fh:
                fh.truncate(3 * 1024 ** 3)
            self.assertEqual(size_gb_from_path(path, fallback=1), 3)

    def test_activate_keeps_key_in_local_models_csv(self):
        self.assertEqual(
            ensure_name_in_csv(["gemma4:e4b", "qwen3.6:35b"], "qwen3.8:27b"),
            ["gemma4:e4b", "qwen3.6:35b", "qwen3.8:27b"],
        )
        self.assertEqual(
            ensure_name_in_csv(["qwen3.8:27b"], "qwen3.8:27b"),
            ["qwen3.8:27b"],
        )

    def test_restart_when_loaded_gguf_changes(self):
        self.assertTrue(
            activate_needs_docker_restart(
                model_changed=True, ctx_override=None, parallel_override=None,
            )
        )
        self.assertFalse(
            activate_needs_docker_restart(
                model_changed=False, ctx_override=None, parallel_override=None,
            )
        )
        self.assertTrue(
            activate_needs_docker_restart(
                model_changed=False, ctx_override=8192, parallel_override=None,
            )
        )
        self.assertTrue(
            activate_needs_docker_restart(
                model_changed=False,
                ctx_override=None,
                parallel_override=None,
                mmproj_reload=True,
            )
        )
        self.assertTrue(
            activate_needs_docker_restart(
                model_changed=False,
                ctx_override=None,
                parallel_override=None,
                parallel_changed=True,
            )
        )

    def test_activate_clamps_ini_slots_to_recommendation(self):
        self.assertEqual(resolve_activate_parallel(4, 2, None), 2)
        self.assertEqual(resolve_activate_parallel(4, 2, 3), 3)
        self.assertEqual(resolve_activate_parallel(2, 4, None), 2)


class TestSyclImagePin(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, os.path.join(CORE_INFRA, "agictl"))
        import cli as _cli  # noqa: WPS433

        cls.cli = _cli

    def test_prefers_versioned_image_when_present(self):
        with patch.object(self.cli, "_resolve_sycl_llama_cpp_tag", return_value="b10430"), patch.object(
            self.cli, "_docker_image_exists", return_value=True
        ):
            self.assertEqual(self.cli._resolve_sycl_image(), "versa-agi-sycl:b10430")

    def test_falls_back_to_unversioned_when_pin_missing(self):
        with patch.object(self.cli, "_resolve_sycl_llama_cpp_tag", return_value="b10430"), patch.object(
            self.cli, "_docker_image_exists", return_value=False
        ):
            self.assertEqual(self.cli._resolve_sycl_image(), "versa-agi-sycl")

    def test_activate_merge_keeps_identity_keys(self):
        merged = self.cli._merge_server_config_dict(
            {"sycl_ctx_size": 16384, "active_model": "qwen3.8:27b"},
            {
                "sycl_ctx_size": 65536,
                "sycl_parallel": 2,
                "active_model": "qwen3.6:35b",
            },
            {
                "gpu_backend": "intel",
                "lan_ip": "192.168.4.114",
                "topology": "server",
                "proxy_port": 8080,
            },
        )
        self.assertEqual(merged["gpu_backend"], "intel")
        self.assertEqual(merged["lan_ip"], "192.168.4.114")
        self.assertEqual(merged["active_model"], "qwen3.6:35b")
        self.assertEqual(merged["sycl_ctx_size"], 65536)
        self.assertEqual(merged["proxy_port"], 8080)

    def test_activate_merge_does_not_overwrite_good_lan_ip(self):
        merged = self.cli._merge_server_config_dict(
            {"lan_ip": "192.168.4.114", "gpu_backend": "intel"},
            {"active_model": "qwen3.6:35b"},
            {"lan_ip": "10.0.0.1", "gpu_backend": "intel"},
        )
        self.assertEqual(merged["lan_ip"], "192.168.4.114")


class TestSyclRemoveHelpers(unittest.TestCase):
    def test_drop_name_from_csv(self):
        self.assertEqual(drop_name_from_csv(["a", "b", "a"], "a"), ["b"])
        self.assertEqual(drop_name_from_csv(["qwen3.8:27b"], "qwen3.6:35b"), ["qwen3.8:27b"])
        self.assertEqual(drop_name_from_csv(["", "x"], "x"), [])

    def test_shared_gguf_not_deleted(self):
        registry = {
            "a": {"file": "same.gguf"},
            "b": {"file": "same.gguf"},
        }
        with tempfile.TemporaryDirectory() as td:
            with open(os.path.join(td, "same.gguf"), "wb") as fh:
                fh.write(b"x")
            plan = plan_sycl_remove("a", registry, td)
        self.assertEqual(plan["shared_keys"], ["b"])
        self.assertFalse(plan["delete_gguf"])
        self.assertTrue(plan["gguf_exists"])

    def test_unique_gguf_marked_for_delete(self):
        registry = {"leftover:e4b": {"file": "only.gguf"}}
        with tempfile.TemporaryDirectory() as td:
            with open(os.path.join(td, "only.gguf"), "wb") as fh:
                fh.write(b"x")
            plan = plan_sycl_remove("leftover:e4b", registry, td)
        self.assertEqual(plan["shared_keys"], [])
        self.assertTrue(plan["delete_gguf"])

    def test_block_media_active_and_agents(self):
        self.assertIn(
            "media bundle",
            sycl_remove_block_reason("qwen-image-2512", media_keys=["qwen-image-2512"]),
        )
        self.assertIn(
            "active",
            sycl_remove_block_reason("qwen3.8:27b", active_model="qwen3.8:27b").lower(),
        )
        self.assertIn(
            "clerk",
            sycl_remove_block_reason("x", assigned_agents=["clerk"]),
        )
        self.assertIsNone(
            sycl_remove_block_reason(
                "x",
                assigned_agents=["clerk"],
                confirm_agent_assignments=True,
            )
        )


class TestSyclRemoveCli(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, os.path.join(CORE_INFRA, "agictl"))
        from click.testing import CliRunner
        import cli as _cli_mod  # noqa: WPS433

        cls.mod = _cli_mod
        cls.cli = _cli_mod.cli
        cls.runner = CliRunner

    def test_refuses_active_on_gpu_host(self):
        from click.testing import CliRunner

        with patch("cli._resolve_topology", return_value="local"), patch(
            "cli._resolve_gpu_backend", return_value="intel"
        ), patch("cli.os.geteuid", return_value=0), patch(
            "cli._resolve_sycl_active_model", return_value="gemma4:e4b"
        ), patch("cli._agents_assigned_to_model", return_value=[]), patch(
            "cli._media_catalog_keys", return_value=[]
        ):
            out = CliRunner().invoke(self.cli, ["model", "sycl", "remove", "gemma4:e4b"])
        self.assertNotEqual(out.exit_code, 0, out.output)
        payload = json.loads(out.output.strip().splitlines()[-1])
        self.assertFalse(payload["success"])
        self.assertIn("active", payload["error"].lower())

    def test_refuses_media_key(self):
        from click.testing import CliRunner

        with patch("cli._resolve_topology", return_value="local"), patch(
            "cli.os.geteuid", return_value=0
        ), patch("cli._media_catalog_keys", return_value=["qwen-image-2512"]), patch(
            "cli._agents_assigned_to_model", return_value=[]
        ), patch("cli._resolve_sycl_active_model", return_value=""):
            out = CliRunner().invoke(
                self.cli, ["model", "sycl", "remove", "qwen-image-2512"]
            )
        self.assertNotEqual(out.exit_code, 0, out.output)
        payload = json.loads(out.output.strip().splitlines()[-1])
        self.assertIn("media", payload["error"].lower())

    def test_client_refuses_agents_before_ssh(self):
        from click.testing import CliRunner

        with patch("cli._resolve_topology", return_value="client"), patch(
            "cli.os.geteuid", return_value=0
        ), patch("cli._media_catalog_keys", return_value=[]), patch(
            "cli._agents_assigned_to_model", return_value=["clerk"]
        ), patch("cli._sycl_remove_on_gpu_host") as remote:
            out = CliRunner().invoke(self.cli, ["model", "sycl", "remove", "leftover:e4b"])
        self.assertNotEqual(out.exit_code, 0, out.output)
        remote.assert_not_called()

    def test_client_sshes_then_clears_local(self):
        from click.testing import CliRunner

        with patch("cli._resolve_topology", return_value="client"), patch(
            "cli.os.geteuid", return_value=0
        ), patch("cli._media_catalog_keys", return_value=[]), patch(
            "cli._agents_assigned_to_model", return_value=["clerk"]
        ), patch(
            "cli._sycl_remove_on_gpu_host",
            return_value={"success": True, "action": "removed", "steps": ["deleted x.gguf"]},
        ) as remote, patch(
            "cli._clear_sycl_inventory", return_value=["setup.ini"]
        ) as clear, patch("cli._sync_catalog", return_value=(True, {})):
            out = CliRunner().invoke(
                self.cli,
                [
                    "model",
                    "sycl",
                    "remove",
                    "leftover:e4b",
                    "--confirm-agent-assignments",
                ],
            )
        self.assertEqual(out.exit_code, 0, out.output)
        payload = json.loads(out.output.strip().splitlines()[-1])
        self.assertTrue(payload["success"])
        self.assertEqual(payload["action"], "removed")
        remote.assert_called_once()
        clear.assert_called_once_with("leftover:e4b")

    def test_gpu_sudo_password_error_hints_update(self):
        self.assertIn(
            "setup.sh --update",
            self.mod._gpu_host_sudo_error("sudo: a password is required"),
        )
        self.assertEqual(self.mod._gpu_host_sudo_error("disk full"), "disk full")


class TestSyclAdditionals(unittest.TestCase):
    def test_select_prefers_f16(self):
        files = [
            HfFile("mmproj-BF16.gguf", 10),
            HfFile("mmproj-F16.gguf", 20),
            HfFile("Qwen3.6-35B-A3B-UD-Q4_K_M.gguf", 99),
        ]
        picked = select_mmproj_file(files)
        self.assertEqual(picked.path, "mmproj-F16.gguf")

    def test_paired_name(self):
        self.assertEqual(
            paired_mmproj_name("Qwen3.6-35B-A3B-UD-Q4_K_M.gguf"),
            "mmproj-Qwen3.6-35B-A3B-UD-Q4_K_M.gguf",
        )

    def test_plan_mmproj_additional(self):
        files = [HfFile("mmproj-F16.gguf", 899 * 1024**2)]
        with tempfile.TemporaryDirectory() as td:
            plan = plan_sycl_additionals(
                main_file="Qwen3.6-35B-A3B-UD-Q4_K_M.gguf",
                dest_dir=td,
                inspect_files=files,
            )
        self.assertEqual(len(plan), 1)
        self.assertEqual(plan[0]["role"], "mmproj")
        self.assertEqual(plan[0]["source"], "mmproj-F16.gguf")
        self.assertEqual(plan[0]["file"], "mmproj-F16.gguf")
        self.assertTrue(plan[0]["path"].endswith(
            "Qwen3.6-35B-A3B-UD-Q4_K_M/mmproj-F16.gguf"
        ))
        self.assertFalse(plan[0]["exists"])

    def test_plan_mmproj_additional_qwen38(self):
        files = [HfFile("mmproj-F16.gguf", 928 * 1024**2)]
        with tempfile.TemporaryDirectory() as td:
            plan = plan_sycl_additionals(
                main_file="Qwen3.8-27B-UD-Q6_K_XL.gguf",
                dest_dir=td,
                inspect_files=files,
            )
        self.assertEqual(len(plan), 1)
        self.assertEqual(plan[0]["role"], "mmproj")
        self.assertEqual(plan[0]["source"], "mmproj-F16.gguf")
        self.assertTrue(plan[0]["path"].endswith(
            "Qwen3.8-27B-UD-Q6_K_XL/mmproj-F16.gguf"
        ))
        self.assertFalse(plan[0]["exists"])

    def test_no_mmproj_means_no_additionals(self):
        self.assertEqual(
            plan_sycl_additionals(
                main_file="only-chat.gguf",
                dest_dir="/tmp",
                inspect_files=[HfFile("only-chat.gguf", 10)],
            ),
            [],
        )

    def test_install_renames_to_paired(self):
        with tempfile.TemporaryDirectory() as td:
            src = os.path.join(td, "mmproj-F16.gguf")
            with open(src, "wb") as fh:
                fh.write(b"GGUF" + b"\0" * 8)
            dest_dir = os.path.join(td, "models")
            os.mkdir(dest_dir)
            final = install_paired_file(
                src, dest_dir, "mmproj-Qwen3.6-35B-A3B-UD-Q4_K_M.gguf",
            )
            self.assertEqual(
                os.path.basename(final),
                "mmproj-Qwen3.6-35B-A3B-UD-Q4_K_M.gguf",
            )
            self.assertTrue(os.path.isfile(final))


class TestSyclVisionProbe(unittest.TestCase):
    def test_reload_once_until_router_flag(self):
        main = "Qwen3.6-35B-A3B-UD-Q4_K_M.gguf"
        with tempfile.TemporaryDirectory() as td:
            open(os.path.join(td, f"mmproj-{main}"), "wb").close()
            self.assertTrue(activate_needs_mmproj_reload(None, td, main))
            # Flat leftover still needs a relocate even if router flag was set.
            self.assertTrue(
                activate_needs_mmproj_reload({"mmproj_router": True}, td, main)
            )
        self.assertFalse(activate_needs_mmproj_reload(None, "/nope", main))

    def test_subdir_layout_ready_skips_reload(self):
        main = "Qwen3.6-35B-A3B-UD-Q4_K_M.gguf"
        with tempfile.TemporaryDirectory() as td:
            sub = os.path.join(td, "Qwen3.6-35B-A3B-UD-Q4_K_M")
            os.mkdir(sub)
            open(os.path.join(sub, main), "wb").close()
            open(os.path.join(sub, "mmproj-F16.gguf"), "wb").close()
            self.assertTrue(sycl_vlm_layout_ready(td, main))
            self.assertFalse(
                activate_needs_mmproj_reload({"mmproj_router": True}, td, main)
            )

    def test_ensure_moves_flat_pair_into_stem_dir(self):
        main = "Qwen3.6-35B-A3B-UD-Q4_K_M.gguf"
        with tempfile.TemporaryDirectory() as td:
            open(os.path.join(td, main), "wb").close()
            open(os.path.join(td, f"mmproj-{main}"), "wb").close()
            layout = ensure_sycl_vlm_subdir(td, main)
            self.assertTrue(layout["ready"])
            self.assertTrue(os.path.isfile(os.path.join(
                td, "Qwen3.6-35B-A3B-UD-Q4_K_M", main,
            )))
            self.assertTrue(os.path.isfile(os.path.join(
                td, "Qwen3.6-35B-A3B-UD-Q4_K_M", "mmproj-F16.gguf",
            )))
            self.assertFalse(os.path.isfile(os.path.join(td, main)))

    def test_catalog_gains_image(self):
        self.assertEqual(catalog_input_modalities_after_probe("text"), "text,image")
        self.assertEqual(
            catalog_input_modalities_after_probe("text,image"), "text,image"
        )

    def test_catalog_label_drops_pre_probe_note(self):
        from model_catalog import catalog_label_after_probe, parse_catalog_row

        stale = "Qwen 3.8 27B — hybrid thinking, 256K context (text-only until mmproj)"
        clean = "Qwen 3.8 27B — hybrid thinking, 256K context"
        self.assertEqual(catalog_label_after_probe(stale), clean)
        self.assertEqual(catalog_label_after_probe(clean), clean)
        row = parse_catalog_row(
            "local|llamacpp|true|false|32768|262144|local|text,image|text|false|" + stale
        )
        self.assertEqual(row["label"], clean)

    def test_builtin_png_and_parts(self):
        png = builtin_probe_png()
        self.assertTrue(png.startswith(b"\x89PNG"))
        data, mime = load_probe_image("probe")
        self.assertEqual(mime, "image/png")
        self.assertEqual(data[:8], b"\x89PNG\r\n\x1a\n")
        parts = chat_image_content_parts("color?", data, mime)
        self.assertEqual(parts[0]["type"], "text")
        self.assertTrue(parts[1]["image_url"]["url"].startswith("data:image/png;base64,"))


if __name__ == "__main__":
    unittest.main()
