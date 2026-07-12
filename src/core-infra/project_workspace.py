"""Project display name vs directory slug helpers.

Display ``name`` is unique and mutable (except reserved system projects).
Directory slug is the basename of ``workspace_path`` and is immutable after create.
"""

from __future__ import annotations

import os
import re
import sqlite3
from typing import Iterable, Optional, Set

COA_WORKSPACE_BASE = "/home/coa/coa-env/workspace"

# Shared system projects — name and on-disk directory must stay fixed.
RESERVED_SYSTEM_PROJECTS = frozenset({"AGi-Tools", "AGi-Knowledgebase"})

_DIR_SLUG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_INVALID_DIR_NAMES = frozenset({".", "..", "!_archive"})


def is_reserved_system_project(name: str) -> bool:
    return name in RESERVED_SYSTEM_PROJECTS


def project_dir_from_workspace_path(workspace_path: Optional[str]) -> str:
    """Return the immutable directory slug from a stored workspace_path."""
    if not workspace_path:
        return ""
    return os.path.basename(workspace_path.rstrip(os.sep))


def slugify_project_dir(name: str) -> str:
    """Derive a filesystem-safe directory slug from a display name."""
    s = (name or "").strip().lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    s = re.sub(r"-{2,}", "-", s).strip("-")
    return s or "project"


def validate_project_dir(dir_name: str) -> Optional[str]:
    """Return an error message if dir_name is not a valid override slug."""
    if not dir_name or not dir_name.strip():
        return "Directory name cannot be empty"
    d = dir_name.strip()
    if d in _INVALID_DIR_NAMES or d.startswith("!"):
        return f"Directory name '{d}' is reserved"
    if os.sep in d or (os.altsep and os.altsep in d) or "/" in d or "\\" in d:
        return "Directory name cannot contain path separators"
    if not _DIR_SLUG_RE.fullmatch(d):
        return (
            "Directory name must start with an alphanumeric character and contain "
            "only letters, digits, '.', '_' or '-'"
        )
    return None


def collect_taken_project_dirs(
    conn: sqlite3.Connection,
    workspace_base: str = COA_WORKSPACE_BASE,
) -> Set[str]:
    """Directory slugs already used on disk or in the projects table."""
    taken: Set[str] = set()
    try:
        if os.path.isdir(workspace_base):
            taken.update(os.listdir(workspace_base))
    except OSError:
        pass
    try:
        rows = conn.execute("SELECT workspace_path FROM projects").fetchall()
    except sqlite3.Error:
        return taken
    for row in rows:
        path = row["workspace_path"] if isinstance(row, sqlite3.Row) else row[0]
        slug = project_dir_from_workspace_path(path)
        if slug:
            taken.add(slug)
    return taken


def allocate_project_dir(
    preferred: str,
    taken: Iterable[str],
    *,
    allow_increment: bool = True,
) -> str:
    """Pick a unique directory slug.

    When ``allow_increment`` is True (auto-slug path), appends -2, -3, …
    When False (explicit ``--dir``), raises ValueError if preferred is taken.
    """
    err = validate_project_dir(preferred)
    if err:
        raise ValueError(err)
    taken_set = set(taken)
    if preferred not in taken_set:
        return preferred
    if not allow_increment:
        raise ValueError(f"Directory '{preferred}' is already in use")
    n = 2
    while True:
        candidate = f"{preferred}-{n}"
        if candidate not in taken_set:
            return candidate
        n += 1


def resolve_project_dir(
    display_name: str,
    dir_override: Optional[str],
    taken: Iterable[str],
) -> str:
    """Resolve directory slug for project create: override or auto-slug + increment."""
    if dir_override is not None and str(dir_override).strip() != "":
        return allocate_project_dir(str(dir_override).strip(), taken, allow_increment=False)
    base = slugify_project_dir(display_name)
    return allocate_project_dir(base, taken, allow_increment=True)
