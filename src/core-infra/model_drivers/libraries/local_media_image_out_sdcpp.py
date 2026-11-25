"""Local Utility image output via pinned sd-cli (TD-LOCAL-MEDIA-001).

Does not use a cloud chat client or llama-server.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from typing import Any, Callable

from model_drivers.artifacts import GeneratedArtifact
from model_drivers.errors import DriverError
from model_media_ingest import recipe_generate_defaults

ADAPTER_ID = "local_media_image_out_sdcpp"
METHOD_FAMILY = "local_media"
DIRECTION = "output"
MODALITY = "image"

DEFAULT_SD_CLI = "versa-agi-sd-cli"
DEFAULT_SAMPLER = "euler"
DEFAULT_SHIFT = 3


def _bundle_recipe(bundle_dir: str) -> str:
    path = os.path.join(bundle_dir, "bundle.json")
    if not os.path.isfile(path):
        return ""
    try:
        with open(path, encoding="utf-8") as fh:
            parsed = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return ""
    if isinstance(parsed, dict):
        return str(parsed.get("recipe") or "")
    return ""


def _find_role(bundle_dir: str, role: str, filename: str) -> str:
    if filename:
        direct = os.path.join(bundle_dir, os.path.basename(filename))
        if os.path.isfile(direct):
            return direct
    manifest_path = os.path.join(bundle_dir, "bundle.json")
    if os.path.isfile(manifest_path):
        try:
            with open(manifest_path, encoding="utf-8") as fh:
                parsed = json.load(fh)
            for component in (parsed.get("components") or []) if isinstance(parsed, dict) else []:
                if component.get("role") != role:
                    continue
                path = os.path.join(
                    bundle_dir, os.path.basename(str(component.get("filename") or ""))
                )
                if os.path.isfile(path):
                    return path
        except (OSError, json.JSONDecodeError, TypeError):
            pass
    names = [
        name for name in os.listdir(bundle_dir)
        if os.path.isfile(os.path.join(bundle_dir, name))
    ]
    if role == "dit":
        for name in names:
            low = name.lower()
            if name.endswith(".gguf") and (
                "qwen-image-2512" in low or "flux1-dev" in low
            ):
                return os.path.join(bundle_dir, name)
    if role == "text_encoder":
        for name in names:
            low = name.lower()
            if "mmproj" in low:
                continue
            if name.endswith(".gguf") and (
                "qwen3vl" in low or "qwen3-vl" in low or "qwen2.5-vl" in low
            ):
                return os.path.join(bundle_dir, name)
    if role == "clip_l":
        for name in names:
            if "clip_l" in name.lower() or name.lower() == "clip_l.safetensors":
                return os.path.join(bundle_dir, name)
    if role == "t5xxl":
        for name in names:
            if "t5xxl" in name.lower() or "t5-xxl" in name.lower():
                return os.path.join(bundle_dir, name)
    if role == "vae":
        for name in names:
            low = name.lower()
            if name.endswith(".safetensors") and (
                "vae" in low or low == "ae.safetensors" or low.startswith("ae.")
            ):
                return os.path.join(bundle_dir, name)
    raise DriverError("bundle_incomplete", f"Missing {role} in {bundle_dir}")


def generate(
    *,
    client: Any = None,
    route: Any = None,
    prompt: str,
    input_files: list[dict[str, Any]] | None = None,
    config: dict[str, Any] | None = None,
    runner: Callable[..., subprocess.CompletedProcess[str]] | None = None,
) -> GeneratedArtifact:
    """Paint a PNG with sd-cli. ``config`` must include ``bundle_dir``."""

    del client, route, input_files
    cfg = dict(config or {})
    bundle_dir = cfg.get("bundle_dir") or ""
    if not bundle_dir or not os.path.isdir(bundle_dir):
        raise DriverError("bundle_missing", "Local media bundle directory is missing.")
    prompt = (prompt or "").strip()
    if not prompt:
        raise DriverError("prompt_required", "A paint prompt is required.")

    recipe = str(cfg.get("recipe") or _bundle_recipe(bundle_dir) or "")
    defaults = recipe_generate_defaults(recipe)
    dit = _find_role(bundle_dir, "dit", cfg.get("dit") or "")
    vae = _find_role(bundle_dir, "vae", cfg.get("vae") or "")
    width = int(cfg["width"] if "width" in cfg else defaults["width"])
    height = int(cfg["height"] if "height" in cfg else defaults["height"])
    steps = int(cfg["steps"] if "steps" in cfg else defaults["steps"])
    cfg_scale = float(cfg["cfg_scale"] if "cfg_scale" in cfg else defaults["cfg_scale"])
    sd_cli = cfg.get("sd_cli") or DEFAULT_SD_CLI
    seed = int(cfg["seed"]) if "seed" in cfg and cfg["seed"] is not None else None
    out_dir = cfg.get("out_dir") or tempfile.mkdtemp(prefix="versa-media-gen-")
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, cfg.get("out_name") or "out.png")
    is_flux = recipe in ("flux1_dev", "flux1-dev") or "flux1-dev" in dit.lower()
    if is_flux:
        encoders = [
            "--clip_l", _find_role(bundle_dir, "clip_l", cfg.get("clip_l") or ""),
            "--t5xxl", _find_role(bundle_dir, "t5xxl", cfg.get("t5xxl") or ""),
        ]
    else:
        encoders = [
            "--llm", _find_role(bundle_dir, "text_encoder", cfg.get("text_encoder") or ""),
        ]

    cmd = [
        sd_cli,
        "--diffusion-model", dit,
        "--vae", vae,
        *encoders,
        "--cfg-scale", str(cfg_scale),
        "--sampling-method", str(cfg.get("sampling_method") or DEFAULT_SAMPLER),
        "--steps", str(steps),
        "-H", str(height),
        "-W", str(width),
        "-p", prompt,
        "-o", out_path,
    ]
    if is_flux:
        if cfg.get("clip_on_cpu", True):
            cmd.append("--clip-on-cpu")
    else:
        cmd.extend([
            "--diffusion-fa",
            "--flow-shift", str(cfg.get("flow_shift") or DEFAULT_SHIFT),
        ])
    if seed is not None:
        cmd.extend(["--seed", str(seed)])
    if cfg.get("offload"):
        cmd.append("--offload-to-cpu")

    run = runner or subprocess.run
    result = run(cmd, check=False, capture_output=True, text=True)
    if result.returncode != 0:
        err = (result.stderr or result.stdout or "").strip()[-800:]
        raise DriverError("generation_failed", f"sd-cli failed ({result.returncode}): {err}")
    if not os.path.isfile(out_path):
        raise DriverError("generation_failed", "sd-cli exited 0 but wrote no PNG.")
    with open(out_path, "rb") as fh:
        data = fh.read()
    if len(data) < 8 or data[:4] != b"\x89PNG":
        raise DriverError("generation_failed", "sd-cli output is not a PNG.")
    return GeneratedArtifact(data, "png", "image/png", None, usage={"seed": seed})
