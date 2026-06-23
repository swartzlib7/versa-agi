"""Per-catalog-key input/output extension maps for Utility Models and file validation."""

from __future__ import annotations

import json
import os
import sqlite3
from typing import Any

from model_catalog import IO_MODALITIES, OUTPUT_DELIVERY_MODALITIES, load_catalog, parse_input_modalities

# Merged file + document bucket (PDF, office, text, structured data).
FILE_MODALITY = "file"
INPUT_MODALITY_TOKENS = (*IO_MODALITIES, FILE_MODALITY)
OUTPUT_MODALITY_TOKENS = ("text", *OUTPUT_DELIVERY_MODALITIES)

_DEFAULT_INPUT_EXTS: dict[str, list[str]] = {
    "text": ["*"],
    "image": ["jpg", "jpeg", "png", "webp", "gif", "heic", "bmp"],
    "audio": ["mp3", "wav", "m4a", "ogg", "flac", "aac"],
    "video": ["mp4", "mkv", "mov", "webm", "avi"],
    FILE_MODALITY: ["csv", "txt", "md", "pdf", "docx", "xlsx", "json", "yaml", "yml"],
}

_DEFAULT_OUTPUT_EXTS: dict[str, list[str]] = {
    "text": ["*"],
    "image": ["png", "jpg", "jpeg", "webp", "gif"],
    "audio": ["mp3", "wav", "m4a", "ogg"],
    "video": ["mp4", "mkv", "webm", "mov"],
}


def _agents_db() -> str:
    return os.environ.get("AGICTL_AGENTS_DB", "/var/lib/versa-agi/agents.db")


def default_map_for_catalog_entry(m: dict) -> dict[str, dict[str, list[str]]]:
    """Build modality map JSON from catalog input/output_modalities CSV."""
    ins = parse_input_modalities(m)
    outs = {x.strip() for x in (m.get("output_modalities") or "text").split(",") if x.strip()}

    input_map: dict[str, list[str]] = {}
    if "text" in ins or not ins:
        input_map["text"] = list(_DEFAULT_INPUT_EXTS["text"])
    for tok in ("image", "audio", "video"):
        if tok in ins:
            input_map[tok] = list(_DEFAULT_INPUT_EXTS[tok])
    # Always include file bucket for UM --input-files on any catalog key.
    input_map[FILE_MODALITY] = list(_DEFAULT_INPUT_EXTS[FILE_MODALITY])

    output_map: dict[str, list[str]] = {}
    for tok in OUTPUT_MODALITY_TOKENS:
        if tok in outs:
            output_map[tok] = list(_DEFAULT_OUTPUT_EXTS.get(tok, ["*"]))

    if not output_map:
        output_map["text"] = ["*"]

    return {"input": input_map, "output": output_map}


def map_to_json_blob(m: dict[str, Any]) -> str:
    return json.dumps(m, separators=(",", ":"), sort_keys=True)


def parse_map_json(raw: str | None) -> dict[str, Any] | None:
    if not raw or not str(raw).strip():
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    return data


def load_modality_map(catalog_key: str, *, agents_db: str | None = None) -> dict[str, Any] | None:
    db = agents_db or _agents_db()
    if not os.path.isfile(db):
        return None
    try:
        conn = sqlite3.connect(db, timeout=5)
        row = conn.execute(
            "SELECT map_json FROM catalog_modality_maps WHERE catalog_key=?",
            (catalog_key.strip(),),
        ).fetchone()
        conn.close()
        if not row:
            return None
        return parse_map_json(row[0])
    except Exception:
        return None


def resolve_modality_map(catalog_key: str, *, agents_db: str | None = None) -> dict[str, Any] | None:
    """Stored map for ``catalog_key``, falling back to the catalog-derived default.

    Lets newly-added catalog models (e.g. a freshly-added generation model) validate
    immediately, before `agictl model modality-map seed` / the next `setup.sh --update`
    persists an explicit row.
    """
    stored = load_modality_map(catalog_key, agents_db=agents_db)
    if stored:
        return stored
    try:
        entry = load_catalog().get(catalog_key.strip())
    except Exception:
        entry = None
    return default_map_for_catalog_entry(entry) if entry else None


def save_modality_map(catalog_key: str, data: dict[str, Any], *, agents_db: str | None = None) -> None:
    db = agents_db or _agents_db()
    conn = sqlite3.connect(db, timeout=5)
    conn.execute(
        """INSERT INTO catalog_modality_maps (catalog_key, map_json, updated_at)
           VALUES (?, ?, datetime('now'))
           ON CONFLICT(catalog_key) DO UPDATE SET
             map_json=excluded.map_json,
             updated_at=datetime('now')""",
        (catalog_key.strip(), map_to_json_blob(data)),
    )
    conn.commit()
    conn.close()


def seed_all_modality_maps(*, agents_db: str | None = None, cat: dict | None = None) -> int:
    """Insert default maps for every merged catalog key (idempotent). Returns count seeded."""
    db = agents_db or _agents_db()
    cat = cat or load_catalog()
    if not os.path.isfile(db):
        return 0
    seeded = 0
    conn = sqlite3.connect(db, timeout=5)
    for key, m in cat.items():
        existing = conn.execute(
            "SELECT 1 FROM catalog_modality_maps WHERE catalog_key=?",
            (key,),
        ).fetchone()
        if existing:
            continue
        blob = map_to_json_blob(default_map_for_catalog_entry(m))
        conn.execute(
            "INSERT INTO catalog_modality_maps (catalog_key, map_json) VALUES (?, ?)",
            (key, blob),
        )
        seeded += 1
    conn.commit()
    conn.close()
    return seeded


def _ext_of(path: str) -> str:
    return os.path.splitext(path)[1].lower().lstrip(".")


def _modality_for_ext(ext: str) -> str | None:
    for modality, exts in _DEFAULT_INPUT_EXTS.items():
        if ext in exts:
            return modality
    return None


def _allowed_exts(modality: str, allowed: list[str]) -> bool:
    if "*" in allowed:
        return True
    return modality in allowed or _ext_of(f"x.{modality}") in allowed


def extension_allowed(modality: str, ext: str, allowed: list[str]) -> bool:
    ext = (ext or "").lower().lstrip(".")
    if "*" in allowed:
        return True
    return ext in {a.lower().lstrip(".") for a in allowed}


def validate_input_files(
    catalog_key: str,
    paths: list[str],
    *,
    agents_db: str | None = None,
) -> tuple[bool, str, list[dict]]:
    """Validate local input paths against catalog modality map."""
    if not paths:
        return True, "", []

    mmap = resolve_modality_map(catalog_key, agents_db=agents_db)
    if not mmap:
        return False, f"No modality map for catalog key '{catalog_key}'", []

    input_map: dict[str, list[str]] = mmap.get("input") or {}
    checked: list[dict] = []

    for raw in paths:
        p = (raw or "").strip()
        if not p:
            continue
        if "://" in p and not p.lower().startswith("file://"):
            return False, f"Remote URLs not supported: {p}", checked
        real = os.path.expanduser(p)
        if not os.path.isabs(real):
            real = os.path.abspath(real)
        if not os.path.isfile(real):
            return False, f"Input file not found: {real}", checked

        ext = _ext_of(real)
        modality = _modality_for_ext(ext) or FILE_MODALITY
        allowed = input_map.get(modality)
        if allowed is None:
            return (
                False,
                f"Catalog '{catalog_key}' does not accept input modality '{modality}' (.{ext})",
                checked,
            )
        if not extension_allowed(modality, ext, allowed):
            return (
                False,
                f"Extension '.{ext}' not allowed for {modality} on '{catalog_key}' (allowed: {allowed})",
                checked,
            )
        checked.append({"path": real, "modality": modality, "ext": ext})

    return True, "", checked


def validate_output_artifact(
    catalog_key: str,
    path: str,
    output_modality: str,
    *,
    agents_db: str | None = None,
) -> tuple[bool, str]:
    mmap = resolve_modality_map(catalog_key, agents_db=agents_db)
    if not mmap:
        return False, f"No modality map for '{catalog_key}'"
    allowed = (mmap.get("output") or {}).get(output_modality)
    if allowed is None:
        return False, f"Output modality '{output_modality}' not in map for '{catalog_key}'"
    ext = _ext_of(path)
    if not extension_allowed(output_modality, ext, allowed):
        return False, f"Output .{ext} not allowed for {output_modality} (allowed: {allowed})"
    return True, ""
