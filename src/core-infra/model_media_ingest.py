"""Local Utility media bundle planning (TD-LOCAL-MEDIA-001).

A media GGUF is one component of a pipeline. Qwen-Image needs DiT + text
encoder + VAE. This module plans that bundle and guards media import so
files never enter llama-server storage.

Downloads are performed by the caller (CLI). Tests must mock Hub/download.
Do not pull large files on the development laptop.
"""

from __future__ import annotations

import json
import os
import re
import shutil
from dataclasses import asdict, dataclass, field
from typing import Any

from model_hf_ingest import (
    CLASS_CHAT,
    CLASS_MEDIA,
    CLASS_UNKNOWN,
    CLASS_VLM,
    HfIngestError,
    InspectResult,
    inspect_hf_source,
)

RUNTIME_MEDIA = "media"
PROVIDER_SLUG = "local_media"
MEDIA_STORE = "/opt/versa-agi/media-models"
SD_CLI_WRAPPER = "/usr/local/bin/versa-agi-sd-cli"
SDCPP_DEFAULT_IMAGE = "versa-agi-sdcpp:master-820-de298c2"
SDCPP_ENV = "/etc/versa-agi/sdcpp.env"

ROLE_DIT = "dit"
ROLE_TEXT_ENCODER = "text_encoder"
ROLE_CLIP_L = "clip_l"
ROLE_T5XXL = "t5xxl"
ROLE_VAE = "vae"

RECIPE_QWEN_IMAGE = "qwen_image_2512"
CATALOG_KEY_QWEN_IMAGE = "qwen-image-2512"
RECIPE_FLUX = "flux1_dev"
CATALOG_KEY_FLUX = "flux1-dev"

QWEN_DIT_RE = re.compile(r"qwen-image-2512.+\.gguf$", re.I)
QWEN_TE_DEFAULT = "Qwen2.5-VL-7B-Instruct-UD-Q4_K_XL.gguf"
QWEN_TE_REPO = "unsloth/Qwen2.5-VL-7B-Instruct-GGUF"
QWEN_VAE_REPO = "Comfy-Org/Qwen-Image_ComfyUI"
QWEN_VAE_FILE = "split_files/vae/qwen_image_vae.safetensors"

FLUX_DIT_RE = re.compile(r"flux1-dev.+\.gguf$", re.I)
FLUX_DIT_REPO = "unsloth/FLUX.1-dev-GGUF"
FLUX_DIT_FILE = "flux1-dev-Q8_0.gguf"
FLUX_CLIP_REPO = "comfyanonymous/flux_text_encoders"
FLUX_CLIP_FILE = "clip_l.safetensors"
FLUX_T5_REPO = "comfyanonymous/flux_text_encoders"
FLUX_T5_FILE = "t5xxl_fp16.safetensors"
FLUX_VAE_REPO = "black-forest-labs/FLUX.1-dev"
FLUX_VAE_FILE = "ae.safetensors"

STOCK_MEDIA_CATALOG_KEYS = (CATALOG_KEY_QWEN_IMAGE, CATALOG_KEY_FLUX)
CATALOG_LABELS = {
    CATALOG_KEY_QWEN_IMAGE: "Qwen-Image-2512 — Local sd-cli paint",
    CATALOG_KEY_FLUX: "FLUX.1-dev — Local sd-cli paint",
}

def list_hf_media_recipes() -> list[dict[str, Any]]:
    """Recognized Hugging Face recipes the Add Model picker can list.

    Not a search of the Hub. Only rows we have a bundle recipe for.
    """
    return [
        {
            "id": CATALOG_KEY_QWEN_IMAGE,
            "label": "Qwen-Image-2512",
            "source": (
                "hf://unsloth/Qwen-Image-2512-GGUF/qwen-image-2512-Q8_0.gguf"
            ),
            "provider": PROVIDER_SLUG,
            "class": "local",
            "kind": "media",
            "work_modality": "local",
            "input_modalities": "text",
            "output_modalities": "image",
            "classification": CLASS_MEDIA,
            "recipe": RECIPE_QWEN_IMAGE,
        },
        {
            "id": CATALOG_KEY_FLUX,
            "label": "FLUX.1-dev",
            "source": f"hf://{FLUX_DIT_REPO}/{FLUX_DIT_FILE}",
            "provider": PROVIDER_SLUG,
            "class": "local",
            "kind": "media",
            "work_modality": "local",
            "input_modalities": "text",
            "output_modalities": "image",
            "classification": CLASS_MEDIA,
            "recipe": RECIPE_FLUX,
        },
    ]


MEDIA_NEXT_STEP = (
    "This source is a media generation pipeline. Inspect the bundle with "
    "`agictl model media inspect <source>`, then import on the GPU host with "
    "`sudo agictl model media import <source> --name <key> --runtime media`. "
    "After the bundle is on the GPU host and sd-cli is installed "
    "(`sudo ./setup.sh --update`), paint with "
    "`agictl model media generate --name <key> --prompt '…'` "
    "(default 768²; add `--offload` if VRAM is tight). "
    "Do not download it into SYCL/llama-server storage. "
    "TD-LOCAL-MEDIA-001."
)


@dataclass
class BundleComponent:
    role: str
    repo: str
    filename: str
    required: bool = True
    validate: str = "gguf"  # gguf | safetensors | any
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class BundlePlan:
    recipe: str
    catalog_key_hint: str
    provider: str
    runtime: str
    store_dir: str
    components: list[BundleComponent] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "recipe": self.recipe,
            "catalog_key_hint": self.catalog_key_hint,
            "provider": self.provider,
            "runtime": self.runtime,
            "store_dir": self.store_dir,
            "components": [c.to_dict() for c in self.components],
            "warnings": list(self.warnings),
            "notes": list(self.notes),
        }


def media_import_block_reason(
    classification: str,
    runtime: str,
    *,
    confirm_unknown: bool = False,
) -> str | None:
    if runtime != RUNTIME_MEDIA:
        return (
            f"Unsupported runtime '{runtime}'. Media import requires --runtime media. "
            "Chat GGUFs use: sudo agictl model sycl import … --runtime chat"
        )
    if classification in (CLASS_CHAT, CLASS_VLM):
        return (
            "This source is a chat GGUF, not a media bundle. "
            "Use: sudo agictl model sycl import … --runtime chat"
        )
    if classification == CLASS_UNKNOWN and not confirm_unknown:
        return (
            "Classification is unknown. Re-inspect, or pass --confirm-unknown "
            "with --runtime media if you are sure this is a media pipeline."
        )
    if classification not in (CLASS_MEDIA, CLASS_UNKNOWN):
        return f"Cannot import classification '{classification}' as a media bundle."
    return None


def topology_media_import_block_reason(topology: str) -> str | None:
    topo = (topology or "local").strip().lower()
    if topo == "client":
        return (
            "Media import runs on the GPU host (topology=local or server). "
            "Import there. Client media refresh is not shipped yet."
        )
    if topo not in ("local", "server"):
        return f"Unsupported topology '{topo}'. Media import requires local or server."
    return None


def qwen_image_plan(*, dit_repo: str, dit_filename: str, dest_key: str) -> BundlePlan:
    warnings: list[str] = []
    if re.search(r"Q4_K", dit_filename, re.I):
        warnings.append(
            "Q4_K quants of Qwen-Image-2512 can paint black in sd.cpp "
            "(activation overflow). Prefer Q8_0 or Q5_0."
        )
    return BundlePlan(
        recipe=RECIPE_QWEN_IMAGE,
        catalog_key_hint=dest_key or CATALOG_KEY_QWEN_IMAGE,
        provider=PROVIDER_SLUG,
        runtime="sd-cli",
        store_dir=os.path.join(MEDIA_STORE, dest_key or CATALOG_KEY_QWEN_IMAGE),
        components=[
            BundleComponent(
                role=ROLE_DIT,
                repo=dit_repo,
                filename=dit_filename,
                validate="gguf",
                note="Diffusion transformer weights (Unsloth Qwen-Image-2512 GGUF)",
            ),
            BundleComponent(
                role=ROLE_TEXT_ENCODER,
                repo=QWEN_TE_REPO,
                filename=QWEN_TE_DEFAULT,
                validate="gguf",
                note="Qwen2.5-VL 7B Instruct — not Qwen3-VL",
            ),
            BundleComponent(
                role=ROLE_VAE,
                repo=QWEN_VAE_REPO,
                filename=QWEN_VAE_FILE,
                validate="safetensors",
                note="Comfy-Org Qwen-Image VAE (safetensors, not GGUF)",
            ),
        ],
        warnings=warnings,
        notes=[
            "Pinned runtime is stable-diffusion.cpp sd-cli (not llama-server).",
            "Import does not create a Utility Profile or ModelDriver ◆.",
        ],
    )


def flux1_plan(*, dest_key: str) -> BundlePlan:
    return BundlePlan(
        recipe=RECIPE_FLUX,
        catalog_key_hint=dest_key or CATALOG_KEY_FLUX,
        provider=PROVIDER_SLUG,
        runtime="sd-cli",
        store_dir=os.path.join(MEDIA_STORE, dest_key or CATALOG_KEY_FLUX),
        components=[
            BundleComponent(
                role=ROLE_DIT,
                repo=FLUX_DIT_REPO,
                filename=FLUX_DIT_FILE,
                validate="gguf",
                note="Pinned Unsloth FLUX.1-dev Q8_0 — inspect of another quant still plans this file",
            ),
            BundleComponent(
                role=ROLE_CLIP_L,
                repo=FLUX_CLIP_REPO,
                filename=FLUX_CLIP_FILE,
                validate="safetensors",
                note="sd.cpp --clip_l (not --llm)",
            ),
            BundleComponent(
                role=ROLE_T5XXL,
                repo=FLUX_T5_REPO,
                filename=FLUX_T5_FILE,
                validate="safetensors",
                note="sd.cpp --t5xxl official companion (fp16)",
            ),
            BundleComponent(
                role=ROLE_VAE,
                repo=FLUX_VAE_REPO,
                filename=FLUX_VAE_FILE,
                validate="safetensors",
                note="Official FLUX ae.safetensors (not a Qwen/Wan VAE)",
            ),
        ],
        warnings=[
            "FLUX.1 [dev] is a non-commercial license. See black-forest-labs LICENSE.md.",
            "VAE is on gated Hub repo black-forest-labs/FLUX.1-dev. Import needs [local_ai] hf_token and a license accept on that card.",
        ],
        notes=[
            "Pinned runtime is stable-diffusion.cpp sd-cli. Defaults: 20 steps, CFG 1.0, --clip-on-cpu.",
            "Import does not create a Utility Profile or ◆.",
        ],
    )


def _is_flux(repo: str, filename: str) -> bool:
    low = (repo or "").lower()
    return low == FLUX_DIT_REPO.lower() or bool(FLUX_DIT_RE.search(filename or ""))


def plan_media_bundle(
    inspected: InspectResult,
    *,
    dest_key: str = "",
) -> BundlePlan | None:
    """Return a bundle plan when we recognize the media recipe."""
    selected = inspected.selected_file
    filename = (selected.path if selected else inspected.source.filename) or ""
    repo = inspected.source.repo_id
    if _is_flux(repo, filename):
        if inspected.classification not in (CLASS_MEDIA, CLASS_UNKNOWN) and not FLUX_DIT_RE.search(filename):
            return None
        return flux1_plan(dest_key=dest_key or CATALOG_KEY_FLUX)
    if inspected.classification != CLASS_MEDIA and not QWEN_DIT_RE.search(filename):
        return None
    if QWEN_DIT_RE.search(filename) or "Qwen-Image" in repo or "qwen-image" in repo.lower():
        return qwen_image_plan(
            dit_repo=repo,
            dit_filename=filename or "qwen-image-2512-Q8_0.gguf",
            dest_key=dest_key or CATALOG_KEY_QWEN_IMAGE,
        )
    return None


def recipe_generate_defaults(name: str) -> dict[str, Any]:
    """Paint knobs for a catalog key. Turbo CFG 0 must not be treated as missing."""
    key = (name or "").strip()
    if key in (CATALOG_KEY_FLUX, RECIPE_FLUX):
        return {"width": 768, "height": 768, "steps": 20, "cfg_scale": 1.0}
    return {"width": 768, "height": 768, "steps": 40, "cfg_scale": 2.5}


def inspect_media_source(source: str, *, dest_key: str = "") -> dict[str, Any]:
    inspected = inspect_hf_source(source)
    payload = inspected.to_dict()
    plan = plan_media_bundle(inspected, dest_key=dest_key)
    payload["bundle"] = plan.to_dict() if plan else None
    if inspected.classification == CLASS_MEDIA:
        payload["next_step"] = MEDIA_NEXT_STEP
    guard = media_import_block_reason(inspected.classification, RUNTIME_MEDIA)
    payload["media_import_ok"] = guard is None and plan is not None
    if guard:
        payload["media_import_block"] = guard
    return payload


def validate_component_file(path: str, validate: str) -> None:
    if not os.path.isfile(path):
        raise HfIngestError(f"Downloaded file missing: {path}", "missing_file")
    if validate == "gguf":
        from model_hf_ingest import validate_gguf_file

        validate_gguf_file(path)
        return
    if validate == "safetensors":
        with open(path, "rb") as fh:
            head = fh.read(8)
        if head[:4] == b"GGUF":
            raise HfIngestError(
                f"{os.path.basename(path)} looks like GGUF; expected safetensors VAE.",
                "not_safetensors",
            )
        if os.path.getsize(path) < 8:
            raise HfIngestError(f"{os.path.basename(path)} is too small.", "not_safetensors")


def bundle_manifest(plan: BundlePlan, *, source: str, revision: str | None) -> dict[str, Any]:
    return {
        "recipe": plan.recipe,
        "provider": plan.provider,
        "runtime": plan.runtime,
        "source": source,
        "revision": revision or "main",
        "components": [c.to_dict() for c in plan.components],
        "class": CLASS_MEDIA,
    }


def media_bundle_value(manifest: dict[str, Any]) -> str:
    return json.dumps(manifest, separators=(",", ":"), sort_keys=True)


def load_bundle_manifest(bundle_dir: str) -> dict[str, Any]:
    path = os.path.join(bundle_dir, "bundle.json")
    if not os.path.isfile(path):
        raise HfIngestError(f"No bundle.json in {bundle_dir}", "missing_manifest")
    with open(path, encoding="utf-8") as fh:
        parsed = json.load(fh)
    if not isinstance(parsed, dict):
        raise HfIngestError("bundle.json is not an object.", "bad_manifest")
    return parsed


def load_media_bundles(models_ini_path: str | None) -> dict[str, dict[str, Any]]:
    if not models_ini_path or not os.path.isfile(models_ini_path):
        return {}
    import configparser

    cfg = configparser.ConfigParser(delimiters=("=",), strict=False)
    cfg.optionxform = str
    try:
        cfg.read(models_ini_path)
    except configparser.Error:
        return {}
    if not cfg.has_section("media_bundles"):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for key, raw in cfg.items("media_bundles"):
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            out[key.strip()] = parsed
    return out


def resolve_bundle_dir(name: str, store: str = MEDIA_STORE) -> str:
    key = (name or "").strip()
    if not key or "/" in key or key in (".", ".."):
        raise HfIngestError("Invalid media bundle name.", "bad_bundle_name")
    return os.path.join(store, key)


def rename_media_bundle_dir(
    old: str,
    new: str,
    *,
    store: str = MEDIA_STORE,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Move ``store/<old>`` to ``store/<new>``. Does not edit models.ini."""
    old_key = (old or "").strip()
    new_key = (new or "").strip()
    old_dir = resolve_bundle_dir(old_key, store)
    new_dir = resolve_bundle_dir(new_key, store)
    if old_key == new_key:
        raise HfIngestError("Old and new bundle names are the same.", "same_name")
    old_exists = os.path.isdir(old_dir)
    new_exists = os.path.isdir(new_dir)
    if new_exists and old_exists:
        raise HfIngestError(f"Destination already exists: {new_dir}", "dest_exists")
    if not old_exists and not new_exists:
        raise HfIngestError(f"Bundle directory missing: {old_dir}", "missing_bundle")
    action = "already" if new_exists else "move"
    if action == "move" and not dry_run:
        os.makedirs(store, exist_ok=True)
        os.rename(old_dir, new_dir)
    return {
        "old": old_key,
        "new": new_key,
        "old_dir": old_dir,
        "new_dir": new_dir,
        "dir_action": action,
        "dry_run": dry_run,
    }


def remove_media_bundle_dir(
    name: str,
    *,
    store: str = MEDIA_STORE,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Delete ``store/<name>``. Does not edit models.ini."""
    key = (name or "").strip()
    bundle_dir = resolve_bundle_dir(key, store)
    exists = os.path.isdir(bundle_dir)
    if exists and not dry_run:
        shutil.rmtree(bundle_dir)
    return {
        "name": key,
        "dir": bundle_dir,
        "dir_action": "missing" if not exists else ("dry_run" if dry_run else "removed"),
        "dry_run": dry_run,
    }


def read_sdcpp_image() -> str:
    if os.path.isfile(SDCPP_ENV):
        with open(SDCPP_ENV, encoding="utf-8") as fh:
            for line in fh:
                if line.startswith("VERSA_SDCPP_IMAGE="):
                    return line.split("=", 1)[1].strip() or SDCPP_DEFAULT_IMAGE
    return SDCPP_DEFAULT_IMAGE


def media_runtime_status() -> dict[str, Any]:
    """Host checks for pinned sd-cli. Does not start a container."""
    image = read_sdcpp_image()
    wrapper_ok = os.path.isfile(SD_CLI_WRAPPER) and os.access(SD_CLI_WRAPPER, os.X_OK)
    image_ok = False
    docker_err = ""
    try:
        import subprocess

        result = subprocess.run(
            ["docker", "image", "inspect", image],
            check=False,
            capture_output=True,
            text=True,
        )
        image_ok = result.returncode == 0
        if not image_ok:
            docker_err = (result.stderr or result.stdout or "").strip()[-240:]
    except FileNotFoundError:
        docker_err = "docker not found"
    except OSError as exc:
        docker_err = str(exc)
    return {
        "wrapper": SD_CLI_WRAPPER,
        "wrapper_ok": wrapper_ok,
        "image": image,
        "image_ok": image_ok,
        "docker_error": docker_err,
        "store": MEDIA_STORE,
        "ready": wrapper_ok and image_ok,
    }


def media_usage(name: str = "") -> dict[str, Any]:
    """How-to-use facts for a local media catalog key. Not token/cost usage."""
    key = (name or CATALOG_KEY_QWEN_IMAGE).strip()
    if key in (CATALOG_KEY_FLUX, RECIPE_FLUX):
        return _flux_usage()
    if key not in (CATALOG_KEY_QWEN_IMAGE, "qwen-image"):
        known = ", ".join(STOCK_MEDIA_CATALOG_KEYS)
        raise HfIngestError(
            f"No usage sheet for '{key}'. Known: {known}",
            "unknown_usage",
        )
    return {
        "catalog_key": CATALOG_KEY_QWEN_IMAGE,
        "recipe": RECIPE_QWEN_IMAGE,
        "runtime": "sd-cli",
        "store": os.path.join(MEDIA_STORE, CATALOG_KEY_QWEN_IMAGE),
        "skill": "local_media_qwen_image_2512.md",
        "default_width": 768,
        "default_height": 768,
        "optional_size": 1024,
        "preferred_dit_quant": "Q8_0",
        "avoid_dit_quant": "Q4_K (sd.cpp can paint black)",
        "steps": 40,
        "gpu_sharing": (
            "Paint does not use llama-server slots. Both share the same GPU memory. "
            "768² Q8 worked on Intel B70 32 GB with versa-agi-sycl still up. "
            "1024² crashed (exit 139) in that setup. Use --offload or stop chat first."
        ),
        "commands": {
            "usage": f"agictl model media usage {CATALOG_KEY_QWEN_IMAGE}",
            "generate": (
                f"agictl model media generate --name {CATALOG_KEY_QWEN_IMAGE} "
                "--prompt '…'"
            ),
            "utility": f"agictl utility run {CATALOG_KEY_QWEN_IMAGE}",
        },
        "sources": {
            "card": "https://huggingface.co/unsloth/Qwen-Image-2512-GGUF",
            "sdcpp": (
                "https://unsloth.ai/docs/models/tutorials/"
                "qwen-image-2512/stable-diffusion.cpp"
            ),
        },
        "prompt_tips": (
            "Long specific scene (subject, setting, light, camera). "
            "Quote any text to render. English or Chinese. "
            "Strengths: human realism, natural detail, text-in-image. "
            "No --negative on generate; put avoid-cues in the brief. "
            "We omit --seed unless you pass one (sd-cli then uses 42). "
            "Seed locks a variation; the brief is the subject."
        ),
        "summary": (
            "Local Utility paint (not chat). Default 768², Q8_0 DiT, sd-cli. "
            "Read this before generating. Client runs copy the PNG back here."
        ),
    }


def _flux_usage() -> dict[str, Any]:
    return {
        "catalog_key": CATALOG_KEY_FLUX,
        "recipe": RECIPE_FLUX,
        "runtime": "sd-cli",
        "store": os.path.join(MEDIA_STORE, CATALOG_KEY_FLUX),
        "skill": "local_media_flux1_dev.md",
        "default_width": 768,
        "default_height": 768,
        "preferred_dit_quant": "Q8_0",
        "dit": f"{FLUX_DIT_REPO}/{FLUX_DIT_FILE}",
        "clip_l": f"{FLUX_CLIP_REPO}/{FLUX_CLIP_FILE}",
        "t5xxl": f"{FLUX_T5_REPO}/{FLUX_T5_FILE}",
        "vae": f"{FLUX_VAE_REPO}/{FLUX_VAE_FILE}",
        "steps": 20,
        "cfg_scale": 1.0,
        "gpu_sharing": (
            "Paint does not use llama-server slots. Both share the same GPU memory. "
            "Flux Q8_0 plus T5 fp16 is large. Default --clip-on-cpu. "
            "Use --offload or stop chat if VRAM is tight."
        ),
        "commands": {
            "usage": f"agictl model media usage {CATALOG_KEY_FLUX}",
            "generate": (
                f"agictl model media generate --name {CATALOG_KEY_FLUX} "
                "--prompt '…'"
            ),
            "utility": f"agictl utility run {CATALOG_KEY_FLUX}",
        },
        "sources": {
            "card": "https://huggingface.co/unsloth/FLUX.1-dev-GGUF",
            "sdcpp": "https://github.com/leejet/stable-diffusion.cpp/blob/master/docs/flux.md",
            "license": "https://huggingface.co/black-forest-labs/FLUX.1-dev/blob/main/LICENSE.md",
        },
        "prompt_tips": (
            "Long specific scene (subject, setting, light, camera). "
            "Quote any text to render. CFG 1.0 is the sd.cpp Flux recommendation. "
            "We omit --seed unless you pass one (sd-cli then uses 42)."
        ),
        "summary": (
            "Local Utility paint (not chat). FLUX.1-dev Q8_0, 20 steps, CFG 1.0, sd-cli. "
            "Non-commercial license. Client runs copy the PNG back here."
        ),
    }
