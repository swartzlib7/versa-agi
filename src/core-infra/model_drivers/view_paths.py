"""Validate local image paths for agictl view / harness modality tools."""
from __future__ import annotations

import db_connect


import os
import sqlite3

from model_drivers.message_adapters import IMAGE_EXTENSIONS, _guess_image_mime, read_image_base64

AGENTS_DB = os.getenv("AGICTL_AGENTS_DB", "/var/lib/versa-agi/agents.db")


class ViewPathError(Exception):
    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(message)


def _agent_workspace(agent_name: str) -> str:
    agent_dir = os.environ.get("AGICTL_AGENT_DIR", "").strip()
    if agent_dir:
        return os.path.dirname(agent_dir)

    conn = db_connect.connect_compat(AGENTS_DB, timeout=5)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT workspace FROM agents WHERE name=?", (agent_name,)).fetchone()
    conn.close()
    if not row or not row["workspace"]:
        raise ViewPathError("agent_not_found", f"Agent '{agent_name}' has no workspace")
    return row["workspace"]


def resolve_view_image_path(path: str, agent_name: str = "") -> str:
    """Resolve a local image path. Relative paths are resolved from the agent workspace."""
    if not path or not str(path).strip():
        raise ViewPathError("path_required", "Image path is required")

    raw = os.path.expanduser(str(path).strip())
    # Local files only — remote fetch would introduce SSRF risk on the agent host.
    # Use browser screenshot or download to a local path first, then view.
    if "://" in raw and not raw.lower().startswith("file://"):
        raise ViewPathError("not_local", "Remote URLs are not supported — provide a local file path")
    if raw.lower().startswith("file://"):
        raw = raw[7:]

    if not os.path.isabs(raw):
        if agent_name:
            try:
                raw = os.path.join(_agent_workspace(agent_name), raw)
            except ViewPathError:
                raw = os.path.abspath(raw)
        else:
            raw = os.path.abspath(raw)

    real = os.path.realpath(raw)
    if not os.path.isfile(real):
        raise ViewPathError("not_found", f"File not found: {real}")
    if not os.access(real, os.R_OK):
        raise ViewPathError("not_readable", f"Cannot read file: {real}")

    ext = os.path.splitext(real)[1].lower()
    mime = _guess_image_mime(real)
    if ext not in IMAGE_EXTENSIONS and not mime.startswith("image/"):
        raise ViewPathError("not_image", f"Not a supported image file: {real}")

    return real


def inspect_image_for_view(path: str, agent_name: str) -> dict:
    """Validate path and return JSON-serializable metadata for view tools."""
    from model_drivers.image_processing import prepare_image_for_view

    real = resolve_view_image_path(path, agent_name)
    inject_path, proc_meta = prepare_image_for_view(real, agent_name)
    mime, _b64 = read_image_base64(inject_path)
    size = os.path.getsize(inject_path)
    result = {
        "success": True,
        "path": inject_path,
        "source_path": real,
        "mime": mime,
        "bytes": size,
        "modality": "image",
        "agent": agent_name,
    }
    if proc_meta.get("processed"):
        result["processed"] = True
        if proc_meta.get("resized_from"):
            result["resized_from"] = proc_meta["resized_from"]
            result["resized_to"] = proc_meta["resized_to"]
    elif proc_meta.get("processing_skipped"):
        result["processing_note"] = proc_meta["processing_skipped"]
    elif proc_meta.get("processing_error"):
        result["processing_note"] = proc_meta["processing_error"]
    return result
