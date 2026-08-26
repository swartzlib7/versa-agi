"""Hugging Face inspect + chat-only SYCL import helpers (TD-LOCAL-ADD-UX-001).

A ``.gguf`` is a weight container, not a llama.cpp chat model. This module
classifies Hub sources before any SYCL / catalog mutation so media pipelines
(Qwen-Image, MiniMax-H3, diffusion/VAE bundles) never enter llama-server
storage or agent pickers.
"""

from __future__ import annotations

import base64
import configparser
import json
import os
import re
import shutil
import struct
import subprocess
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import zlib
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Iterable

CLASS_CHAT = "chat_gguf"
CLASS_VLM = "chat_vlm_mmproj"
CLASS_MEDIA = "media_pipeline"
CLASS_UNKNOWN = "unknown"

RUNTIME_CHAT = "chat"

HF_HOSTS = {"huggingface.co", "hf.co", "www.huggingface.co"}
HF_API_ROOT = "https://huggingface.co/api/models"
GGUF_MAGIC = b"GGUF"

MEDIA_PIPELINE_TAGS = {
    "text-to-image",
    "image-to-image",
    "unconditional-image-generation",
    "text-to-video",
    "image-to-video",
    "text-to-audio",
    "text-to-3d",
    "image-to-3d",
    "image-feature-extraction",
}
CHAT_PIPELINE_TAGS = {
    "text-generation",
    "conversational",
    "text2text-generation",
}
VLM_PIPELINE_TAGS = {
    "image-text-to-text",
    "visual-question-answering",
    "video-text-to-text",
}
MEDIA_ARCH_RE = re.compile(
    r"(qwenimage|flux|stablediffusion|stable[\s_-]?diffusion|sd3|sdxl|"
    r"unet2d|autoencoderkl|hunyuanvideo|wanvideo|ltxvideo|minimax.?h3|krea2)",
    re.I,
)
STRONG_MEDIA_FILE_RE = re.compile(
    r"(qwen-image|flux[\s._-]|sd3|sdxl|stable-diffusion|[\s._-]unet[\s._-]|"
    r"[\s._-]vae[\s._-]|vae_|_vae|[\s._-]dit[\s._-]|fl2va|hunyuan-video|"
    r"wan[-_]?video|ltx[-_]?video|minimax[_-]?h3|krea2)",
    re.I,
)
MMPROJ_RE = re.compile(r"(^|/)mmproj[^/]*\.gguf$", re.I)
GGUF_RE = re.compile(r"\.gguf$", re.I)

MEDIA_NEXT_STEP = (
    "This source is a media generation pipeline (image/video/audio weights), "
    "not a llama.cpp chat GGUF. Do not download it into SYCL/llama-server "
    "storage or register it as a local chat model. Next: "
    "`agictl model media inspect <source>` then "
    "`sudo agictl model media import … --runtime media` on the GPU host "
    "(TD-LOCAL-MEDIA-001)."
)


class HfIngestError(Exception):
    """User-facing ingest failure with a stable error code."""

    def __init__(self, message: str, code: str = "ingest_error"):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass
class HfSource:
    repo_id: str
    revision: str | None = None
    filename: str | None = None
    original: str = ""

    def as_hf_uri(self) -> str:
        rev = f"@{self.revision}" if self.revision else ""
        path = f"/{self.filename}" if self.filename else ""
        return f"hf://{self.repo_id}{rev}{path}"


@dataclass
class HfFile:
    path: str
    size: int | None = None
    sha256: str | None = None


@dataclass
class InspectResult:
    source: HfSource
    classification: str
    reasons: list[str] = field(default_factory=list)
    pipeline_tag: str | None = None
    tags: list[str] = field(default_factory=list)
    architecture: str | None = None
    files: list[HfFile] = field(default_factory=list)
    selected_file: HfFile | None = None
    companion_files: list[str] = field(default_factory=list)
    size_gb: int | None = None
    warnings: list[str] = field(default_factory=list)
    next_step: str | None = None

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["source"] = asdict(self.source)
        payload["files"] = [asdict(f) for f in self.files]
        payload["selected_file"] = asdict(self.selected_file) if self.selected_file else None
        return payload


FetchJson = Callable[[str], Any]
Downloader = Callable[[str, str, str, str], str]


def parse_hf_source(raw: str) -> HfSource:
    """Normalize HF web URLs, ``hf://`` URIs, and bare ``org/repo[/file]``."""
    text = (raw or "").strip()
    if not text:
        raise HfIngestError("Hugging Face source is empty.", "empty_source")

    if "://" in text:
        parsed = urllib.parse.urlparse(text)
        scheme = (parsed.scheme or "").lower()
        if scheme == "hf":
            return _parse_hf_uri(text)
        if scheme not in ("http", "https"):
            raise HfIngestError(
                f"Unsupported URL scheme '{scheme}'. Use https://huggingface.co/… or hf://org/repo.",
                "unsupported_scheme",
            )
        host = (parsed.netloc or "").split("@")[-1].split(":")[0].lower()
        if host not in HF_HOSTS:
            raise HfIngestError(
                f"Rejected host '{host}'. Only huggingface.co (or hf://) sources are accepted.",
                "rejected_host",
            )
        parts = [p for p in parsed.path.split("/") if p]
        if len(parts) < 2:
            raise HfIngestError(
                "Hugging Face URL must include org/repo.",
                "invalid_source",
            )
        org, repo = parts[0], parts[1]
        revision = None
        filename = None
        if len(parts) >= 4 and parts[2] in ("blob", "resolve", "raw"):
            revision = parts[3]
            filename = "/".join(parts[4:]) or None
        elif len(parts) >= 3 and parts[2] not in (
            "tree", "commits", "discussions", "settings",
        ):
            filename = "/".join(parts[2:])
        query_rev = urllib.parse.parse_qs(parsed.query).get("rev") or urllib.parse.parse_qs(
            parsed.query
        ).get("revision")
        if query_rev:
            revision = query_rev[0]
        return HfSource(
            repo_id=f"{org}/{repo}",
            revision=revision,
            filename=filename,
            original=text,
        )

    if text.startswith("hf:"):
        return _parse_hf_uri(text if text.startswith("hf://") else "hf://" + text[3:].lstrip("/"))

    return _parse_bare_repo(text)


def _parse_hf_uri(text: str) -> HfSource:
    body = text[5:] if text.startswith("hf://") else text
    body = body.lstrip("/")
    parts = [p for p in body.split("/") if p]
    if len(parts) < 2:
        raise HfIngestError(
            "hf:// URI must be hf://org/repo or hf://org/repo/file.gguf.",
            "invalid_source",
        )
    org = parts[0]
    repo_token = parts[1]
    revision = None
    if "@" in repo_token:
        repo, revision = repo_token.split("@", 1)
    else:
        repo = repo_token
    filename = "/".join(parts[2:]) or None
    return HfSource(
        repo_id=f"{org}/{repo}",
        revision=revision or None,
        filename=filename,
        original=text,
    )


def _parse_bare_repo(text: str) -> HfSource:
    if text.startswith("/") or "\\" in text or " " in text:
        raise HfIngestError(
            "Bare source must look like org/repo or org/repo/file.gguf.",
            "invalid_source",
        )
    parts = [p for p in text.split("/") if p]
    if len(parts) < 2:
        raise HfIngestError(
            "Bare source must look like org/repo or org/repo/file.gguf.",
            "invalid_source",
        )
    org, repo = parts[0], parts[1]
    filename = "/".join(parts[2:]) or None
    return HfSource(repo_id=f"{org}/{repo}", filename=filename, original=text)


def default_fetch_json(url: str, timeout: int = 30) -> Any:
    req = urllib.request.Request(url, headers={"User-Agent": "versa-agi-model-ingest/1"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
    return json.loads(raw.decode("utf-8"))


def inspect_hf_source(
    raw: str,
    *,
    fetch_json: FetchJson | None = None,
) -> InspectResult:
    """Read-only Hub inspect + conservative runtime classification."""
    source = parse_hf_source(raw)
    fetcher = fetch_json or default_fetch_json
    revision = source.revision or "main"
    model_info: dict[str, Any] = {}
    files: list[HfFile] = []
    architecture = None
    inspect_warnings: list[str] = []

    try:
        model_info = fetcher(f"{HF_API_ROOT}/{source.repo_id}") or {}
        if not isinstance(model_info, dict):
            model_info = {}
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, json.JSONDecodeError, OSError) as exc:
        inspect_warnings.append(f"Hub model metadata unavailable: {exc}")
        model_info = {}

    files = _files_from_model_info(model_info)
    if not files:
        try:
            tree = fetcher(f"{HF_API_ROOT}/{source.repo_id}/tree/{revision}") or []
            files = _files_from_tree(tree)
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, json.JSONDecodeError, OSError, TypeError) as exc:
            inspect_warnings.append(f"Hub file tree unavailable: {exc}")

    if any(f.path == "config.json" for f in files):
        try:
            cfg = fetcher(
                f"https://huggingface.co/{source.repo_id}/resolve/{revision}/config.json"
            )
            if isinstance(cfg, dict):
                architecture = _architecture_from_config(cfg)
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, json.JSONDecodeError, OSError):
            pass

    selected = _select_file(files, source.filename)
    tags = _collect_tags(model_info)
    pipeline_tag = model_info.get("pipeline_tag") if isinstance(model_info.get("pipeline_tag"), str) else None
    classification, reasons = classify_hf_model(
        pipeline_tag=pipeline_tag,
        tags=tags,
        architecture=architecture,
        files=files,
        selected_file=selected.path if selected else source.filename,
    )
    size_gb = _size_gb(selected.size if selected else None)
    companions = [f.path for f in files if f.path != (selected.path if selected else None)]
    warnings = list(inspect_warnings)
    next_step = None
    if classification == CLASS_MEDIA:
        next_step = MEDIA_NEXT_STEP
        if selected and _incomplete_media_bundle(files, selected.path):
            reasons.append(
                "Selected GGUF is only one media component; encoder/VAE/processor files are missing."
            )
    elif classification == CLASS_VLM:
        warnings.append(
            "Vision projector (mmproj) detected. Chat import stores the main GGUF "
            "and the projector. Catalog stays text-only until vision probe."
        )
        next_step = (
            "On topology=local or server (Intel SYCL): "
            "sudo agictl model sycl import <source> --name <key> --runtime chat. "
            "Re-import of an existing key downloads a missing projector. "
            "Do not set input_modalities=image until vision probe passes."
        )
    elif classification == CLASS_UNKNOWN:
        next_step = (
            "Classification is uncertain. Chat SYCL import requires --runtime chat "
            "and --confirm-unknown."
        )
    elif classification == CLASS_CHAT:
        next_step = (
            "Chat GGUF. On topology=local or server (Intel SYCL): "
            "sudo agictl model sycl import <source> --name <key> --runtime chat"
        )

    return InspectResult(
        source=source,
        classification=classification,
        reasons=reasons,
        pipeline_tag=pipeline_tag,
        tags=tags,
        architecture=architecture,
        files=files,
        selected_file=selected,
        companion_files=companions[:40],
        size_gb=size_gb,
        warnings=warnings,
        next_step=next_step,
    )


def classify_hf_model(
    *,
    pipeline_tag: str | None,
    tags: Iterable[str],
    architecture: str | None,
    files: Iterable[HfFile],
    selected_file: str | None,
) -> tuple[str, list[str]]:
    """Conservative classifier: media wins; names alone are not sufficient unless Hub is empty."""
    reasons: list[str] = []
    tagset = {t.lower() for t in tags if t}
    file_list = list(files)
    names = [f.path for f in file_list]
    if selected_file:
        names.append(selected_file)
    ggufs = [n for n in names if GGUF_RE.search(n or "")]
    has_mmproj = any(MMPROJ_RE.search(n or "") for n in names)
    media_name = any(STRONG_MEDIA_FILE_RE.search(n or "") for n in names)
    media_arch = bool(architecture and MEDIA_ARCH_RE.search(architecture))
    pipe = (pipeline_tag or "").lower()

    media_tag = pipe in MEDIA_PIPELINE_TAGS or bool(tagset & MEDIA_PIPELINE_TAGS)
    if pipe == "any-to-any" and (
        media_name or "video" in tagset or "image-generation" in tagset or "diffusers" in tagset
    ):
        media_tag = True
        reasons.append("pipeline_tag=any-to-any with media tags/files")

    if media_tag:
        reasons.append(f"media pipeline_tag/tags ({pipe or ','.join(sorted(tagset & MEDIA_PIPELINE_TAGS))})")
    if media_arch:
        reasons.append(f"media architecture '{architecture}'")
    if media_name and (media_tag or media_arch or not (pipe or tagset)):
        reasons.append("media-component filename/layout")

    if media_tag or media_arch or (media_name and not (pipe in CHAT_PIPELINE_TAGS or pipe in VLM_PIPELINE_TAGS)):
        if media_name and not (media_tag or media_arch or pipe or tagset):
            reasons.append("strong media filename while Hub metadata is empty (safety net)")
        return CLASS_MEDIA, reasons or ["media pipeline signals"]

    vlm_tag = pipe in VLM_PIPELINE_TAGS or bool(tagset & VLM_PIPELINE_TAGS)
    if has_mmproj or vlm_tag:
        if has_mmproj:
            reasons.append("mmproj-*.gguf present")
        if vlm_tag:
            reasons.append(f"VLM pipeline_tag/tags ({pipe or 'image-text-to-text'})")
        return CLASS_VLM, reasons

    chat_tag = pipe in CHAT_PIPELINE_TAGS or bool(tagset & CHAT_PIPELINE_TAGS)
    if chat_tag and ggufs and not media_name:
        reasons.append(f"chat pipeline_tag/tags ({pipe or 'text-generation'})")
        return CLASS_CHAT, reasons
    if chat_tag and ggufs and media_name:
        # Chat tags plus a media-looking filename — do not guess.
        reasons.append("mixed chat tags and media-looking filename")
        return CLASS_UNKNOWN, reasons
    if ggufs and not media_name and not has_mmproj and (pipe or tagset):
        reasons.append("GGUF present without media/VLM signals")
        return CLASS_CHAT, reasons

    reasons.append("insufficient Hub/layout evidence")
    return CLASS_UNKNOWN, reasons


def gguf_registry_blocked(inspect: dict | None, confirm_unknown: bool = False) -> str | None:
    """UI/CLI helper: block SYCL GGUF save/import from an inspect payload."""
    if not inspect:
        return None
    return sycl_import_block_reason(
        inspect.get("classification") or CLASS_UNKNOWN,
        RUNTIME_CHAT,
        confirm_unknown=confirm_unknown,
    )


def sycl_import_block_reason(
    classification: str,
    runtime: str,
    *,
    confirm_unknown: bool = False,
) -> str | None:
    """Return an error if this inspect result must not enter SYCL chat storage."""
    if runtime != RUNTIME_CHAT:
        return (
            f"Unsupported runtime '{runtime}'. Phase 1 only accepts --runtime chat. "
            "Media runtimes are TD-LOCAL-MEDIA-001."
        )
    if classification == CLASS_MEDIA:
        return MEDIA_NEXT_STEP
    if classification == CLASS_UNKNOWN and not confirm_unknown:
        return (
            "Classification is unknown. Re-inspect, or pass --confirm-unknown "
            "with --runtime chat if you are sure this is a llama.cpp chat GGUF."
        )
    return None


def activation_block_reason(meta: dict[str, Any] | None) -> str | None:
    """Refuse activate for media (and unconfirmed unknown) metadata rows."""
    if not meta:
        return None
    kind = meta.get("class") or meta.get("classification")
    if kind == CLASS_MEDIA:
        return (
            "This registry key is classified as a media pipeline and cannot be "
            "activated as a llama-server chat model."
        )
    if kind == CLASS_UNKNOWN and not meta.get("confirm_unknown"):
        return (
            "This registry key was imported as unknown. Pass "
            "--confirm-unknown to activate as chat, or remove it."
        )
    return None


def migrate_skip_reason(meta: dict[str, Any] | None) -> str | None:
    """Media keys must not become enabled local catalog/chat picker rows."""
    if not meta:
        return None
    kind = meta.get("class") or meta.get("classification")
    if kind == CLASS_MEDIA:
        return "media_pipeline"
    return None


def load_sycl_meta(models_ini_path: str | None) -> dict[str, dict[str, Any]]:
    if not models_ini_path or not os.path.isfile(models_ini_path):
        return {}
    import configparser

    cfg = configparser.ConfigParser(delimiters=("=",), strict=False)
    cfg.optionxform = str
    try:
        cfg.read(models_ini_path)
    except configparser.Error:
        return {}
    if not cfg.has_section("sycl_model_meta"):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for key, raw in cfg.items("sycl_model_meta"):
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            out[key.strip()] = parsed
    return out


def meta_value(meta: dict[str, Any]) -> str:
    return json.dumps(meta, separators=(",", ":"), sort_keys=True)


def size_gb_from_bytes(size: int | None, fallback: int | None = None) -> int:
    gb = _size_gb(size)
    if gb is not None:
        return gb
    if fallback is not None:
        return fallback
    return 0


def size_gb_from_path(path: str, fallback: int | None = None) -> int:
    """Prefer on-disk GGUF bytes over a Hub/registry guess (Hub size is often null)."""
    try:
        if path and os.path.isfile(path):
            return size_gb_from_bytes(os.path.getsize(path), fallback=fallback)
    except OSError:
        pass
    return fallback if fallback is not None else 0


def ensure_name_in_csv(values: list[str], name: str) -> list[str]:
    """Return values with name appended if missing (activate must stay registered)."""
    out = [v for v in values if v]
    key = (name or "").strip()
    if key and key not in out:
        out.append(key)
    return out


def select_mmproj_file(files: Iterable[HfFile] | None) -> HfFile | None:
    """Prefer Unsloth ``mmproj-F16.gguf``, then BF16, then any mmproj-*.gguf."""
    found = [f for f in (files or []) if f and MMPROJ_RE.search(f.path or "")]
    if not found:
        return None

    def _rank(item: HfFile) -> tuple[int, str]:
        name = os.path.basename(item.path or "").lower()
        if name == "mmproj-f16.gguf":
            return (0, name)
        if "f16" in name:
            return (1, name)
        if "bf16" in name:
            return (2, name)
        return (3, name)

    return sorted(found, key=_rank)[0]


def paired_mmproj_name(main_file: str) -> str:
    """Legacy flat name. Pin ``b10430`` does not pair this in ``--models-dir``."""
    base = os.path.basename(main_file or "").strip()
    if not base:
        return ""
    return f"mmproj-{base}"


MMPROJ_STORE_NAME = "mmproj-F16.gguf"


def gguf_stem(main_file: str) -> str:
    return os.path.basename(main_file or "").removesuffix(".gguf")


def sycl_vlm_subdir(dest_dir: str, main_file: str) -> str:
    """``dest_dir/<stem>/`` — llama-server pairs mmproj only inside this folder."""
    stem = gguf_stem(main_file)
    if not dest_dir or not stem:
        return ""
    return os.path.join(dest_dir, stem)


def list_mmproj_paths(folder: str) -> list[str]:
    if not folder or not os.path.isdir(folder):
        return []
    return sorted(
        os.path.join(folder, name)
        for name in os.listdir(folder)
        if MMPROJ_RE.search(name)
    )


def sycl_main_gguf_path(dest_dir: str, main_file: str) -> str:
    """Prefer ``<stem>/<file>``, else the flat ``<file>``."""
    base = os.path.basename(main_file or "")
    if not dest_dir or not base:
        return ""
    nested = os.path.join(sycl_vlm_subdir(dest_dir, main_file), base)
    if os.path.isfile(nested):
        return nested
    return os.path.join(dest_dir, base)


def sycl_vlm_layout_ready(dest_dir: str, main_file: str) -> bool:
    sub = sycl_vlm_subdir(dest_dir, main_file)
    main = os.path.join(sub, os.path.basename(main_file or "")) if sub else ""
    return bool(main and os.path.isfile(main) and list_mmproj_paths(sub))


def ensure_sycl_vlm_subdir(dest_dir: str, main_file: str) -> dict[str, Any]:
    """Move main GGUF + projector into ``dest_dir/<stem>/`` (same-filesystem rename)."""
    base = os.path.basename(main_file or "")
    sub = sycl_vlm_subdir(dest_dir, main_file)
    steps: list[str] = []
    dest_main = os.path.join(sub, base) if sub and base else ""
    if sub:
        os.makedirs(sub, exist_ok=True)
    src_main = os.path.join(dest_dir, base) if dest_dir and base else ""
    if (
        dest_main
        and src_main
        and os.path.isfile(src_main)
        and os.path.abspath(src_main) != os.path.abspath(dest_main)
    ):
        os.rename(src_main, dest_main)
        steps.append(f"moved {base} → {gguf_stem(main_file)}/")
    candidates: list[str] = []
    if dest_dir:
        for name in (paired_mmproj_name(main_file), MMPROJ_STORE_NAME):
            leftover = os.path.join(dest_dir, name)
            if os.path.isfile(leftover) and leftover not in candidates:
                candidates.append(leftover)
    if sub:
        candidates.extend(list_mmproj_paths(sub))
    dest_mmproj = os.path.join(sub, MMPROJ_STORE_NAME) if sub else ""
    placed = ""
    for src in candidates:
        if not os.path.isfile(src):
            continue
        if dest_mmproj and os.path.abspath(src) == os.path.abspath(dest_mmproj):
            placed = dest_mmproj
            continue
        if dest_mmproj and os.path.abspath(os.path.dirname(src)) == os.path.abspath(sub):
            placed = src
            continue
        if dest_mmproj:
            if os.path.isfile(dest_mmproj) and os.path.abspath(src) != os.path.abspath(dest_mmproj):
                os.remove(dest_mmproj)
            os.rename(src, dest_mmproj)
            steps.append(f"moved {os.path.basename(src)} → {gguf_stem(main_file)}/{MMPROJ_STORE_NAME}")
            placed = dest_mmproj
            break
    if not placed and dest_mmproj and os.path.isfile(dest_mmproj):
        placed = dest_mmproj
    return {
        "subdir": sub,
        "main": dest_main if dest_main and os.path.isfile(dest_main) else src_main,
        "mmproj": placed,
        "ready": sycl_vlm_layout_ready(dest_dir, main_file),
        "steps": steps,
    }


def plan_sycl_additionals(
    *,
    main_file: str,
    dest_dir: str,
    inspect_files: Iterable[HfFile] | None = None,
    meta: dict | None = None,
) -> list[dict[str, Any]]:
    """Companion files for a chat GGUF. Today: mmproj only. Does not write."""
    hub = select_mmproj_file(inspect_files)
    source_file = (hub.path if hub else "") or ((meta or {}).get("mmproj_source") or "")
    if not source_file:
        return []
    dest_file = MMPROJ_STORE_NAME
    sub = sycl_vlm_subdir(dest_dir, main_file)
    dest_path = os.path.join(sub, dest_file) if sub else dest_file
    exists = bool(
        dest_path and os.path.isfile(dest_path)
    ) or sycl_mmproj_on_disk(dest_dir, main_file)
    return [
        {
            "role": "mmproj",
            "source": os.path.basename(source_file),
            "file": dest_file,
            "path": dest_path,
            "exists": exists,
            "hub_size_bytes": hub.size if hub else None,
        }
    ]


def install_paired_file(src_path: str, dest_dir: str, dest_name: str) -> str:
    """Atomic move into dest_dir, then rename to dest_name if Hub used another name."""
    moved = atomic_move_into(src_path, dest_dir)
    final = os.path.join(dest_dir, dest_name)
    if os.path.abspath(moved) != os.path.abspath(final):
        if os.path.isfile(final):
            os.remove(final)
        os.replace(moved, final)
    return final


def drop_name_from_csv(values: list[str], name: str) -> list[str]:
    """Return values without name (LA-DEL setup.ini / paths.env CSV)."""
    key = (name or "").strip()
    return [v for v in values if v and v != key]


def sycl_gguf_also_used_by(registry: dict, name: str) -> list[str]:
    """Other [sycl_models] keys that share this key's GGUF filename."""
    key = (name or "").strip()
    row = (registry or {}).get(key) or {}
    filename = (row.get("file") or "").strip()
    if not filename:
        return []
    return sorted(
        other
        for other, meta in (registry or {}).items()
        if other != key and (meta or {}).get("file") == filename
    )


def plan_sycl_remove(name: str, registry: dict, dest_dir: str) -> dict:
    """Describe GGUF + registry teardown. Does not write."""
    key = (name or "").strip()
    row = (registry or {}).get(key) or {}
    filename = (row.get("file") or "").strip()
    path = sycl_main_gguf_path(dest_dir, filename) if filename else ""
    shared = sycl_gguf_also_used_by(registry, key)
    exists = bool(path and os.path.isfile(path))
    sub = sycl_vlm_subdir(dest_dir, filename) if filename else ""
    found = list_mmproj_paths(sub) if sub else []
    if dest_dir and filename:
        found.extend(list_mmproj_paths(dest_dir))
        leftover = os.path.join(dest_dir, paired_mmproj_name(filename))
        if os.path.isfile(leftover) and leftover not in found:
            found.append(leftover)
    mmproj_path = found[0] if found else ""
    mmproj_file = os.path.basename(mmproj_path) if mmproj_path else MMPROJ_STORE_NAME
    return {
        "name": key,
        "file": filename,
        "path": path,
        "gguf_exists": exists,
        "shared_keys": shared,
        "delete_gguf": bool(exists and not shared),
        "mmproj_file": mmproj_file,
        "mmproj_path": mmproj_path,
        "mmproj_exists": bool(mmproj_path),
        "delete_mmproj": bool(mmproj_path and not shared),
        "subdir": sub,
        "delete_subdir": bool(sub and os.path.isdir(sub) and not shared),
        "in_registry": key in (registry or {}),
    }


def sycl_remove_block_reason(
    name: str,
    *,
    active_model: str = "",
    media_keys: list[str] | tuple[str, ...] | None = None,
    assigned_agents: list[str] | None = None,
    confirm_agent_assignments: bool = False,
) -> str | None:
    """Refuse media keys, the loaded GGUF, or assigned agents without confirm."""
    key = (name or "").strip()
    if not key:
        return "Model key is required."
    media = {str(k).strip() for k in (media_keys or []) if k}
    if key in media:
        return (
            f"'{key}' is a media bundle. Use: sudo agictl model media remove {key}"
        )
    if key and key == (active_model or "").strip():
        return (
            f"Cannot remove active model '{key}'. "
            f"Switch first: sudo agictl model activate <other>"
        )
    agents = [a for a in (assigned_agents or []) if a]
    if agents and not confirm_agent_assignments:
        listed = ", ".join(agents[:8])
        extra = f" (+{len(agents) - 8})" if len(agents) > 8 else ""
        return (
            f"Agents still assigned to '{key}': {listed}{extra}. "
            f"Retarget them or pass --confirm-agent-assignments."
        )
    return None


def resolve_activate_parallel(
    ini_parallel: int,
    recommended: int,
    override: int | None = None,
) -> int:
    """Pick llama-server ``--parallel`` for activate.

    An explicit ``--parallel`` wins. Otherwise clamp the ini default down to
    the VRAM recommendation so a dense GGUF (Qwen3.8 ~26 GB on 32 GB) does
    not inherit the 4-slot layout that a smaller MoE GGUF (Qwen3.6) tolerated.
    """
    if override is not None:
        return int(override)
    rec = max(1, int(recommended or 1))
    ini = max(1, int(ini_parallel or 1))
    return min(ini, rec)


def activate_needs_docker_restart(
    *,
    model_changed: bool,
    ctx_override: int | None,
    parallel_override: int | None,
    parallel_changed: bool = False,
    mmproj_reload: bool = False,
) -> bool:
    """Restart llama-server when the loaded GGUF, slot/ctx, or new projector changes."""
    return bool(
        model_changed
        or ctx_override is not None
        or parallel_override is not None
        or parallel_changed
        or mmproj_reload
    )


def sycl_mmproj_on_disk(dest_dir: str, main_file: str) -> bool:
    if not dest_dir or not main_file:
        return False
    if list_mmproj_paths(sycl_vlm_subdir(dest_dir, main_file)):
        return True
    leftover = paired_mmproj_name(main_file)
    if leftover and os.path.isfile(os.path.join(dest_dir, leftover)):
        return True
    return os.path.isfile(os.path.join(dest_dir, MMPROJ_STORE_NAME))


def activate_needs_mmproj_reload(meta: dict | None, dest_dir: str, main_file: str) -> bool:
    """Restart after a projector lands, or when the VLM subdir is not ready yet."""
    if not sycl_mmproj_on_disk(dest_dir, main_file):
        return False
    if not sycl_vlm_layout_ready(dest_dir, main_file):
        return True
    return not bool((meta or {}).get("mmproj_router"))


def catalog_input_modalities_after_probe(current: str | None) -> str:
    tokens = [t.strip() for t in (current or "text").split(",") if t.strip()]
    if "text" not in tokens:
        tokens.insert(0, "text")
    if "image" not in tokens:
        tokens.append("image")
    return ",".join(tokens)


def builtin_probe_png() -> bytes:
    """32×32 opaque red PNG (no extra files)."""
    width = height = 32
    raw = b"".join(b"\x00" + (b"\xff\x00\x00" * width) for _ in range(height))

    def _chunk(tag: bytes, data: bytes) -> bytes:
        crc = zlib.crc32(tag + data) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", crc)

    return (
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + _chunk(b"IDAT", zlib.compress(raw, 9))
        + _chunk(b"IEND", b"")
    )


def load_probe_image(spec: str) -> tuple[bytes, str]:
    """``probe`` → built-in PNG; otherwise read a local JPEG/PNG path."""
    text = (spec or "").strip()
    if not text or text.lower() == "probe":
        return builtin_probe_png(), "image/png"
    if not os.path.isfile(text):
        raise HfIngestError(f"Image not found: {text}", "missing_image")
    with open(text, "rb") as fh:
        data = fh.read(16)
        rest = fh.read()
    blob = data + rest
    if blob[:8] == b"\x89PNG\r\n\x1a\n":
        return blob, "image/png"
    if blob[:2] == b"\xff\xd8":
        return blob, "image/jpeg"
    raise HfIngestError(
        f"{os.path.basename(text)} is not a JPEG or PNG.",
        "not_image",
    )


def chat_image_content_parts(prompt: str, image_bytes: bytes, mime: str) -> list[dict[str, Any]]:
    data_url = f"data:{mime};base64,{base64.b64encode(image_bytes).decode('ascii')}"
    return [
        {"type": "text", "text": prompt or "What is in this image? Reply in one short sentence."},
        {"type": "image_url", "image_url": {"url": data_url}},
    ]


def validate_gguf_file(path: str) -> None:
    if not os.path.isfile(path):
        raise HfIngestError(f"Downloaded file missing: {path}", "missing_file")
    with open(path, "rb") as fh:
        magic = fh.read(4)
    if magic != GGUF_MAGIC:
        raise HfIngestError(
            f"{os.path.basename(path)} is not a GGUF file (bad magic). Refusing SYCL import.",
            "not_gguf",
        )


def atomic_move_into(src: str, dest_dir: str) -> str:
    os.makedirs(dest_dir, exist_ok=True)
    dest = os.path.join(dest_dir, os.path.basename(src))
    if os.path.abspath(src) == os.path.abspath(dest):
        return dest
    fd, tmp = tempfile.mkstemp(prefix=".import-", suffix=".partial", dir=dest_dir)
    os.close(fd)
    try:
        shutil.copy2(src, tmp)
        os.replace(tmp, dest)
    except Exception:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass
        raise
    return dest


def topology_import_block_reason(topology: str, gpu_backend: str) -> str | None:
    topo = (topology or "local").strip().lower()
    backend = (gpu_backend or "").strip().lower()
    if topo == "client":
        return (
            "Hugging Face SYCL import runs on the GPU host (topology=local or server). "
            "Import there, then on this client run: sudo agictl model refresh"
        )
    if topo not in ("local", "server"):
        return f"Unsupported topology '{topo}'. Chat SYCL import requires local or server."
    if backend != "intel":
        return (
            "model sycl import is Intel SYCL only (gpu_backend=intel). "
            "For Ollama: sudo agictl model add <tag>"
        )
    return None


def read_hf_token() -> str:
    """Token for gated Hub repos. Env wins; else setup.ini [local_ai] hf_token.

    Media/SYCL import often runs as root via sudo and would otherwise miss
    a user-level ``huggingface-cli login``. Never log the value.
    """
    for key in ("HF_TOKEN", "HUGGING_FACE_HUB_TOKEN"):
        val = (os.environ.get(key) or "").strip()
        if val:
            return val
    src_setup = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "setup.ini")
    cfg = configparser.ConfigParser()
    for path in ("/etc/versa-agi/setup.ini", src_setup):
        if not os.path.isfile(path):
            continue
        try:
            cfg.read(path)
        except configparser.Error:
            continue
        val = (cfg.get("local_ai", "hf_token", fallback="") or "").strip()
        if val:
            return val
    return ""


def default_hf_download(repo: str, filename: str, dest_dir: str, hf_cmd: str) -> str:
    os.makedirs(dest_dir, exist_ok=True)
    env = os.environ.copy()
    token = read_hf_token()
    if token:
        env["HF_TOKEN"] = token
        env["HUGGING_FACE_HUB_TOKEN"] = token
    result = subprocess.run(
        [hf_cmd, "download", repo, "--include", filename, "--local-dir", dest_dir],
        check=False,
        env=env,
    )
    if result.returncode != 0:
        extra = ""
        if "black-forest-labs/" in (repo or "").lower():
            extra = (
                " That Hub repo is gated. Accept the license on the model card "
                "with the same Hugging Face account as setup.ini [local_ai] hf_token, "
                "then retry."
            )
        if not token:
            extra += " No Hugging Face token was available (set [local_ai] hf_token)."
        raise HfIngestError(
            f"HuggingFace download failed for {repo}/{filename}.{extra}",
            "download_failed",
        )
    path = os.path.join(dest_dir, filename)
    if not os.path.isfile(path):
        # hf CLI may nest files under dest_dir/filename or dest_dir/repo/filename
        matches = []
        for root, _dirs, files in os.walk(dest_dir):
            if filename in files:
                matches.append(os.path.join(root, filename))
        if not matches:
            raise HfIngestError(
                f"Download finished but {filename} was not found under {dest_dir}",
                "download_missing",
            )
        path = matches[0]
    return path


def _files_from_model_info(info: dict[str, Any]) -> list[HfFile]:
    siblings = info.get("siblings") or []
    out: list[HfFile] = []
    if not isinstance(siblings, list):
        return out
    for item in siblings:
        if not isinstance(item, dict):
            continue
        name = item.get("rfilename") or item.get("path")
        if not name:
            continue
        size = item.get("size")
        sha = None
        lfs = item.get("lfs") if isinstance(item.get("lfs"), dict) else {}
        if lfs:
            sha = lfs.get("oid") or lfs.get("sha256")
            size = size if size is not None else lfs.get("size")
        out.append(HfFile(path=str(name), size=int(size) if size is not None else None, sha256=sha))
    return out


def _files_from_tree(tree: Any) -> list[HfFile]:
    if not isinstance(tree, list):
        return []
    out: list[HfFile] = []
    for item in tree:
        if not isinstance(item, dict):
            continue
        if item.get("type") and item.get("type") != "file":
            continue
        name = item.get("path") or item.get("rfilename")
        if not name:
            continue
        size = item.get("size")
        sha = item.get("oid")
        lfs = item.get("lfs") if isinstance(item.get("lfs"), dict) else {}
        if lfs:
            sha = lfs.get("oid") or sha
            size = size if size is not None else lfs.get("size")
        out.append(HfFile(path=str(name), size=int(size) if size is not None else None, sha256=sha))
    return out


def _select_file(files: list[HfFile], filename: str | None) -> HfFile | None:
    if filename:
        for item in files:
            if item.path == filename or item.path.endswith("/" + filename):
                return item
        return HfFile(path=filename)
    ggufs = [f for f in files if GGUF_RE.search(f.path) and not MMPROJ_RE.search(f.path)]
    if len(ggufs) == 1:
        return ggufs[0]
    return None


def _collect_tags(info: dict[str, Any]) -> list[str]:
    tags = []
    raw = info.get("tags") or []
    if isinstance(raw, list):
        tags.extend(str(t) for t in raw if t)
    card = info.get("cardData") if isinstance(info.get("cardData"), dict) else {}
    for key in ("tags", "pipeline_tag"):
        val = card.get(key)
        if isinstance(val, list):
            tags.extend(str(t) for t in val if t)
        elif isinstance(val, str):
            tags.append(val)
    return list(dict.fromkeys(tags))


def _architecture_from_config(cfg: dict[str, Any]) -> str | None:
    arch = cfg.get("architectures") or cfg.get("model_type") or cfg.get("_class_name")
    if isinstance(arch, list) and arch:
        return str(arch[0])
    if isinstance(arch, str):
        return arch
    return None


def _size_gb(size: int | None) -> int | None:
    if size is None or size < 0:
        return None
    return max(1, int((size + (1024 ** 3) - 1) // (1024 ** 3))) if size else 0


def _incomplete_media_bundle(files: list[HfFile], selected: str) -> bool:
    names = " ".join(f.path.lower() for f in files)
    selected_l = selected.lower()
    looks_component = bool(STRONG_MEDIA_FILE_RE.search(selected_l))
    has_vae = "vae" in names
    has_encoder = "encoder" in names or "text_encoder" in names
    return looks_component and not (has_vae and has_encoder)
