"""Tiny on-disk cache for provider model index listings.

The agitop "Import from <Provider>" modal and ``agictl model … list`` both fetch
a provider's full model index over the network on every call. That index changes
rarely, so a short-lived file cache lets the modal open instantly on repeat
visits while staying fresh. Each provider gets one JSON file holding the raw
index plus the fetch timestamp (embedded — no filename housekeeping); entries
older than the TTL are ignored and repulled.

Both ``agictl`` and agitop run as root, so the cache lives under the standard
writable state dir. All operations are best-effort: a missing/unwritable cache
silently falls back to a live fetch, preserving the pre-cache behaviour.

Overrides (env): ``VERSA_MODEL_CACHE_DIR`` (path), ``VERSA_MODEL_CACHE_TTL``
(seconds).
"""

from __future__ import annotations

import json
import os
import time

# runs as root; /var/lib/versa-agi is the standard writable state dir.
_DEFAULT_DIR = "/var/lib/versa-agi/cache/provider_models"
_DEFAULT_TTL = 3600  # 1 hour


def _ttl() -> int:
    raw = os.environ.get("VERSA_MODEL_CACHE_TTL", "").strip()
    return int(raw) if raw.isdigit() else _DEFAULT_TTL


def _dir() -> str:
    return os.environ.get("VERSA_MODEL_CACHE_DIR", _DEFAULT_DIR)


def _path(slug: str) -> str:
    return os.path.join(_dir(), f"{slug}.json")


def load(slug: str, ttl: int | None = None) -> dict[str, dict] | None:
    """Return the cached index for ``slug`` if present and fresh, else ``None``."""
    max_age = _ttl() if ttl is None else ttl
    try:
        with open(_path(slug), "r", encoding="utf-8") as fh:
            payload = json.load(fh)
    except (OSError, ValueError):
        return None
    fetched_at = payload.get("fetched_at")
    if not isinstance(fetched_at, (int, float)):
        return None
    if time.time() - fetched_at > max_age:
        return None
    index = payload.get("index")
    return index if isinstance(index, dict) else None


def store(slug: str, index: dict[str, dict]) -> None:
    """Persist ``index`` for ``slug`` with the current timestamp (best-effort)."""
    payload = {"slug": slug, "fetched_at": time.time(), "index": index}
    try:
        os.makedirs(_dir(), exist_ok=True)
        tmp = _path(slug) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(payload, fh)
        os.replace(tmp, _path(slug))
    except OSError:
        pass


def clear(slug: str | None = None) -> None:
    """Remove the cached index for ``slug`` (or every provider when ``None``)."""
    try:
        if slug is not None:
            os.remove(_path(slug))
            return
        for name in os.listdir(_dir()):
            if name.endswith(".json"):
                os.remove(os.path.join(_dir(), name))
    except OSError:
        pass
