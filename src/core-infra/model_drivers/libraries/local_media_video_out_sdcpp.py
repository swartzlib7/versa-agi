"""Local Utility video output via pinned sd-cli (TD-LOCAL-MEDIA-001 / ME-LTX).

LTX-2.5 uses ``sd-cli -M vid_gen`` with DiT + conv video VAE + audio VAE +
Gemma 4 LTX text encoder. Not ComfyUI. Not llama-server. Do not reuse the
image paint adapter — the wire shape is different (``vid_gen``, ``--audio-vae``,
``--video-frames``).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from types import SimpleNamespace
from typing import Any, Callable

from model_drivers.artifacts import GeneratedArtifact
from model_drivers.errors import DriverError
from model_media_ingest import (
    align_ltx_frames,
    align_ltx_spatial,
    recipe_generate_defaults,
)

ADAPTER_ID = "local_media_video_out_sdcpp"
METHOD_FAMILY = "local_media"
DIRECTION = "output"
MODALITY = "video"

_SUBPROCESS_RUN = subprocess.run

DEFAULT_SD_CLI = "versa-agi-sd-cli"
DEFAULT_SAMPLER = "euler"
DEFAULT_NEGATIVE = (
    "worst quality, low quality, blurry, distorted, artifacts"
)


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


def _manifest_role(bundle_dir: str, role: str) -> str:
    manifest_path = os.path.join(bundle_dir, "bundle.json")
    if not os.path.isfile(manifest_path):
        return ""
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
    return ""


def _find_role(bundle_dir: str, role: str, filename: str) -> str:
    if filename:
        direct = os.path.join(bundle_dir, os.path.basename(filename))
        if os.path.isfile(direct):
            return direct
    from_manifest = _manifest_role(bundle_dir, role)
    if from_manifest:
        return from_manifest
    names = [
        name for name in os.listdir(bundle_dir)
        if os.path.isfile(os.path.join(bundle_dir, name))
    ]
    if role == "dit":
        for name in names:
            low = name.lower()
            if name.endswith(".gguf") and "ltx-2.5" in low and "distilled" in low:
                return os.path.join(bundle_dir, name)
    if role == "text_encoder":
        preferred = []
        fallback = []
        for name in names:
            low = name.lower()
            if "gemma4" not in low or "ltx-2.5" not in low:
                continue
            path = os.path.join(bundle_dir, name)
            if name.endswith(".gguf"):
                preferred.append(path)
            elif name.endswith(".safetensors"):
                fallback.append(path)
        if preferred:
            return preferred[0]
        if fallback:
            return fallback[0]
    if role == "vae":
        for name in names:
            low = name.lower()
            if "audio" in low:
                continue
            if name.endswith(".safetensors") and "video-vae-conv" in low:
                return os.path.join(bundle_dir, name)
    if role == "audio_vae":
        for name in names:
            low = name.lower()
            if name.endswith(".safetensors") and "audio-vae" in low:
                return os.path.join(bundle_dir, name)
    raise DriverError("bundle_incomplete", f"Missing {role} in {bundle_dir}")


def _run_sd_cli(
    cmd: list[str],
    runner: Callable[..., Any] | None,
    *,
    width: int,
    height: int,
    frames: int,
    steps: int,
) -> Any:
    """Tests inject ``runner`` or patch ``subprocess.run``. Live runs stream."""
    if runner is not None:
        return runner(cmd, check=False, capture_output=True, text=True)
    if subprocess.run is not _SUBPROCESS_RUN:
        return subprocess.run(cmd, check=False, capture_output=True, text=True)
    print(
        f"sd-cli -M vid_gen {width}×{height} × {frames} frames, {steps} steps. "
        "Loading the text encoder can take several minutes with no step output.",
        flush=True,
    )
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=0,
    )
    chunks: list[str] = []
    stdout = proc.stdout
    if stdout is None:
        returncode = proc.wait()
        return SimpleNamespace(returncode=returncode, stdout="", stderr="")
    while True:
        piece = stdout.read(4096)
        if not piece:
            break
        chunks.append(piece)
        sys.stdout.write(piece)
        sys.stdout.flush()
    returncode = proc.wait()
    return SimpleNamespace(returncode=returncode, stdout="".join(chunks), stderr="")


def _first_image_path(input_files: list[dict[str, Any]] | None) -> str:
    for item in input_files or []:
        if isinstance(item, dict):
            path = str(item.get("path") or "").strip()
        else:
            path = str(item or "").strip()
        if path and os.path.isfile(path):
            return path
    return ""


def generate(
    *,
    client: Any = None,
    route: Any = None,
    prompt: str,
    input_files: list[dict[str, Any]] | None = None,
    config: dict[str, Any] | None = None,
    runner: Callable[..., subprocess.CompletedProcess[str]] | None = None,
) -> GeneratedArtifact:
    """Generate a WebM with sd-cli ``-M vid_gen``. ``config`` must include ``bundle_dir``."""

    del client, route
    cfg = dict(config or {})
    bundle_dir = cfg.get("bundle_dir") or ""
    if not bundle_dir or not os.path.isdir(bundle_dir):
        raise DriverError("bundle_missing", "Local media bundle directory is missing.")
    prompt = (prompt or "").strip()
    if not prompt:
        raise DriverError("prompt_required", "A video prompt is required.")

    recipe = str(cfg.get("recipe") or _bundle_recipe(bundle_dir) or "")
    defaults = recipe_generate_defaults(recipe)
    dit = _find_role(bundle_dir, "dit", cfg.get("dit") or "")
    vae = _find_role(bundle_dir, "vae", cfg.get("vae") or "")
    audio_vae = _find_role(bundle_dir, "audio_vae", cfg.get("audio_vae") or "")
    text_encoder = _find_role(
        bundle_dir, "text_encoder", cfg.get("text_encoder") or "",
    )
    width = align_ltx_spatial(int(cfg["width"] if "width" in cfg else defaults["width"]))
    height = align_ltx_spatial(int(cfg["height"] if "height" in cfg else defaults["height"]))
    steps = int(cfg["steps"] if "steps" in cfg else defaults["steps"])
    cfg_scale = float(cfg["cfg_scale"] if "cfg_scale" in cfg else defaults["cfg_scale"])
    frames = align_ltx_frames(
        int(cfg["video_frames"] if "video_frames" in cfg else defaults["video_frames"])
    )
    fps = int(cfg["fps"] if "fps" in cfg else defaults["fps"])
    sd_cli = cfg.get("sd_cli") or DEFAULT_SD_CLI
    seed = int(cfg["seed"]) if "seed" in cfg and cfg["seed"] is not None else None
    offload = bool(cfg["offload"] if "offload" in cfg else defaults.get("offload", True))
    negative = str(cfg.get("negative_prompt") or DEFAULT_NEGATIVE)
    out_dir = cfg.get("out_dir") or tempfile.mkdtemp(prefix="versa-media-vid-")
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, cfg.get("out_name") or "out.webm")
    start_image = str(cfg.get("init_img") or "").strip() or _first_image_path(input_files)

    cmd = [
        sd_cli,
        "-M", "vid_gen",
        "--diffusion-model", dit,
        "--vae", vae,
        "--audio-vae", audio_vae,
        "--llm", text_encoder,
        "--cfg-scale", str(cfg_scale),
        "--sampling-method", str(cfg.get("sampling_method") or DEFAULT_SAMPLER),
        "--steps", str(steps),
        "-H", str(height),
        "-W", str(width),
        "--video-frames", str(frames),
        "--fps", str(fps),
        "-p", prompt,
        "-n", negative,
        "--diffusion-fa",
        "--vae-tiling",
        "-o", out_path,
    ]
    if start_image:
        cmd.extend(["-i", start_image])
    if seed is not None:
        cmd.extend(["--seed", str(seed)])
    if offload:
        cmd.append("--offload-to-cpu")

    result = _run_sd_cli(cmd, runner, width=width, height=height, frames=frames, steps=steps)
    if result.returncode != 0:
        err = (result.stderr or result.stdout or "").strip()[-800:]
        raise DriverError("generation_failed", f"sd-cli failed ({result.returncode}): {err}")
    if not os.path.isfile(out_path):
        raise DriverError("generation_failed", "sd-cli exited 0 but wrote no WebM.")
    with open(out_path, "rb") as fh:
        data = fh.read()
    if len(data) < 32:
        raise DriverError("generation_failed", "sd-cli output is too small to be a WebM.")
    return GeneratedArtifact(
        data,
        "webm",
        "video/webm",
        None,
        usage={"seed": seed, "video_frames": frames, "fps": fps},
    )
