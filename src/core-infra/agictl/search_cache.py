"""Short-lived cache of web search answers (state_web_access.md §1.2 rule 10, WEB-07).

The same query asked again within a few minutes is answered from here and is
not sent to SearXNG, whose engines suspend a client that asks too often. Only
an answer that had results is kept: "nothing came back" is never remembered,
so a blocked search is tried again the next time.

Standard library only. Every failure here is silent: a cache that cannot be
read or written means the search is simply asked again.
"""

from __future__ import annotations

import hashlib
import json
import os
import time

CACHE_DIR = "/var/lib/versa-agi/search-cache"
DEFAULT_MINUTES = 10
MAX_MINUTES = 1440
MAX_ENTRIES = 500
KEEP_RESULTS = 50       # what one answer keeps, so a later --count can be larger than the first


def minutes_setting(raw) -> int:
    """``[search] cache_minutes``: 0 = off, at most a day; the default when not a number."""
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        return DEFAULT_MINUTES
    return max(0, min(MAX_MINUTES, value))


def _key(query: str, categories: str) -> str:
    text = " ".join(str(query or "").split()).lower() + "\n" + str(categories or "general").strip().lower()
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _path(query: str, categories: str, cache_dir: str) -> str:
    return os.path.join(cache_dir, _key(query, categories) + ".json")


def get(query: str, categories: str, minutes: int, *, cache_dir: str = CACHE_DIR, now: float | None = None):
    """(results, age_seconds) for an answer newer than ``minutes``, else (None, 0)."""
    if minutes <= 0:
        return None, 0
    now = time.time() if now is None else now
    try:
        with open(_path(query, categories, cache_dir), encoding="utf-8") as handle:
            entry = json.load(handle)
        age = now - float(entry["at"])
        results = entry["results"]
    except (OSError, ValueError, KeyError, TypeError):
        return None, 0
    if age < 0 or age > minutes * 60 or not isinstance(results, list) or not results:
        return None, 0
    return results, int(age)


def put(query: str, categories: str, results: list, minutes: int, *,
        cache_dir: str = CACHE_DIR, now: float | None = None) -> bool:
    """Keep an answer that has results. False when nothing was written."""
    if minutes <= 0 or not results:
        return False
    now = time.time() if now is None else now
    try:
        os.makedirs(cache_dir, mode=0o700, exist_ok=True)
        _hand_to_owner(cache_dir)
        path = _path(query, categories, cache_dir)
        tmp = f"{path}.{os.getpid()}.new"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump({"at": now, "query": query, "categories": categories,
                       "results": results[:KEEP_RESULTS]}, handle)
        _hand_to_owner(tmp)
        os.replace(tmp, path)
        prune(minutes, cache_dir=cache_dir, now=now)
        return True
    except OSError:
        return False


def prune(minutes: int, *, cache_dir: str = CACHE_DIR, now: float | None = None) -> int:
    """Remove answers older than ``minutes`` and, beyond MAX_ENTRIES, the oldest. Returns how many."""
    now = time.time() if now is None else now
    removed = 0
    try:
        entries = []
        for name in os.listdir(cache_dir):
            path = os.path.join(cache_dir, name)
            if not name.endswith(".json"):
                # A ".new" file left by a write that was cut short.
                if name.endswith(".new") and now - os.path.getmtime(path) > 60:
                    os.remove(path)
                continue
            entries.append((os.path.getmtime(path), path))
        entries.sort()
        cutoff = now - max(1, minutes) * 60
        keep_from = max(0, len(entries) - MAX_ENTRIES)
        for index, (mtime, path) in enumerate(entries):
            if mtime < cutoff or index < keep_from:
                os.remove(path)
                removed += 1
    except OSError:
        pass
    return removed


def _hand_to_owner(path: str) -> None:
    """When root runs the command, the file belongs to the account that owns the
    data folder (watchdog), so the next ordinary call can still read and replace it."""
    if os.geteuid() != 0:
        return
    try:
        parent = os.stat(os.path.dirname(os.path.abspath(path)) if os.path.isfile(path)
                         else os.path.dirname(os.path.abspath(path.rstrip("/"))))
        os.chown(path, parent.st_uid, parent.st_gid)
    except OSError:
        pass
