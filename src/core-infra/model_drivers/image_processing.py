"""Normalize local images before multimodal VIEW INJECT (harness-wide policy)."""

from __future__ import annotations

import configparser
import hashlib
import os
from dataclasses import dataclass

_SETUP_INI_PATHS = (
    "/etc/versa-agi/setup.ini",
    os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "setup.ini",
    ),
)


@dataclass(frozen=True)
class ImageProcessingConfig:
    enabled: bool = True
    output_format: str = "jpeg"
    jpeg_quality: int = 80
    jpeg_dpi: int = 72
    max_width: int = 2048
    max_height: int = 2048


def _setup_ini_path() -> str:
    for path in _SETUP_INI_PATHS:
        if os.path.isfile(path):
            return path
    return _SETUP_INI_PATHS[0]


def load_image_processing_config() -> ImageProcessingConfig:
    """Read [image_processing] from setup.ini (defaults when missing)."""
    defaults = ImageProcessingConfig()
    path = _setup_ini_path()
    if not os.path.isfile(path):
        return defaults
    parser = configparser.ConfigParser()
    try:
        parser.read(path, encoding="utf-8")
    except Exception:
        return defaults
    if not parser.has_section("image_processing"):
        return defaults
    sec = parser["image_processing"]

    def _bool(key: str, default: bool) -> bool:
        raw = sec.get(key, str(default)).strip().lower()
        return raw in ("1", "true", "yes", "on")

    def _int(key: str, default: int) -> int:
        try:
            return int(sec.get(key, str(default)).strip())
        except (TypeError, ValueError):
            return default

    fmt = sec.get("format", defaults.output_format).strip().lower() or "jpeg"
    if fmt in ("jpg", "jpeg"):
        fmt = "jpeg"
    return ImageProcessingConfig(
        enabled=_bool("enabled", defaults.enabled),
        output_format=fmt,
        jpeg_quality=max(1, min(100, _int("jpeg_quality", defaults.jpeg_quality))),
        jpeg_dpi=max(1, min(600, _int("jpeg_dpi", defaults.jpeg_dpi))),
        max_width=max(64, _int("max_width", defaults.max_width)),
        max_height=max(64, _int("max_height", defaults.max_height)),
    )


def _view_cache_dir(agent_name: str) -> str:
    base = f"/var/lib/versa-agi/{agent_name or 'coa'}/view-cache"
    os.makedirs(base, exist_ok=True)
    try:
        os.chmod(base, 0o770)
    except OSError:
        pass
    return base


def _cache_path(source: str, cfg: ImageProcessingConfig, agent_name: str) -> str:
    st = os.stat(source)
    fingerprint = (
        f"{source}:{st.st_mtime_ns}:{st.st_size}:"
        f"{cfg.output_format}:{cfg.jpeg_quality}:{cfg.jpeg_dpi}:"
        f"{cfg.max_width}x{cfg.max_height}"
    )
    digest = hashlib.sha256(fingerprint.encode()).hexdigest()[:16]
    return os.path.join(_view_cache_dir(agent_name), f"view_{digest}.jpg")


def prepare_image_for_view(
    source_path: str,
    agent_name: str = "",
    *,
    config: ImageProcessingConfig | None = None,
) -> tuple[str, dict]:
    """Return (path_for_inject, metadata). Uses processed JPEG when enabled."""
    cfg = config or load_image_processing_config()
    meta: dict = {
        "source_path": source_path,
        "processed": False,
        "processing_enabled": cfg.enabled,
    }
    if not cfg.enabled:
        return source_path, meta
    if cfg.output_format != "jpeg":
        meta["processing_skipped"] = f"unsupported format policy: {cfg.output_format}"
        return source_path, meta

    try:
        from PIL import Image
    except ImportError:
        meta["processing_skipped"] = "Pillow not installed"
        return source_path, meta

    dest = _cache_path(source_path, cfg, agent_name)
    try:
        if os.path.isfile(dest) and os.path.getmtime(dest) >= os.path.getmtime(source_path):
            meta.update({
                "processed": True,
                "processed_path": dest,
                "bytes": os.path.getsize(dest),
                "cache_hit": True,
            })
            return dest, meta
    except OSError:
        pass

    try:
        with Image.open(source_path) as img:
            img.load()
            if img.mode not in ("RGB", "L"):
                img = img.convert("RGB")
            elif img.mode == "L":
                img = img.convert("RGB")

            width, height = img.size
            max_w, max_h = cfg.max_width, cfg.max_height
            scale = min(1.0, max_w / width if width else 1.0, max_h / height if height else 1.0)
            if scale < 1.0:
                new_size = (max(1, int(width * scale)), max(1, int(height * scale)))
                img = img.resize(new_size, Image.Resampling.LANCZOS)
                meta["resized_from"] = f"{width}x{height}"
                meta["resized_to"] = f"{new_size[0]}x{new_size[1]}"

            dpi = (cfg.jpeg_dpi, cfg.jpeg_dpi)
            img.save(
                dest,
                format="JPEG",
                quality=cfg.jpeg_quality,
                optimize=True,
                dpi=dpi,
            )
        try:
            os.chmod(dest, 0o644)
        except OSError:
            pass
        meta.update({
            "processed": True,
            "processed_path": dest,
            "bytes": os.path.getsize(dest),
            "jpeg_quality": cfg.jpeg_quality,
            "jpeg_dpi": cfg.jpeg_dpi,
            "cache_hit": False,
        })
        return dest, meta
    except Exception as exc:
        meta["processing_error"] = str(exc)[:200]
        return source_path, meta
