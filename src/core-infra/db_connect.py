"""Shared SQLite connection helper for Versa AGi (Deliverable D3).

Every connection opened through :func:`connect` enables foreign-key
enforcement and a deterministic busy timeout, so the ad-hoc
``sqlite3.connect(...)`` sites scattered across the codebase converge on one
safe, consistent setup (the D24 retrofit adopts this helper).

Scope of this helper (per-connection only):
  * ``PRAGMA foreign_keys = ON``   — FK constraints are OFF by default in
    SQLite and must be enabled on *every* connection.
  * ``PRAGMA busy_timeout = 5000`` — wait up to 5 s for a competing writer
    instead of raising ``SQLITE_BUSY`` immediately.
  * ``row_factory = sqlite3.Row``  — dict-style row access for callers.

NOT handled here: ``journal_mode = WAL`` and ``synchronous = NORMAL`` are
persistent properties of the database *file*, set once at init time
(``scripts/init_*.sh``), not per connection. See the Organization plan §9.
"""

from __future__ import annotations

import os
import sqlite3

# 5 s. Python's ``sqlite3.connect(timeout=...)`` is expressed in seconds and
# drives the same busy-timeout mechanism; the explicit PRAGMA below pins the
# value (in ms) so ``PRAGMA busy_timeout`` is verifiably 5000 regardless of how
# the connection was opened.
DEFAULT_TIMEOUT_S = 5
BUSY_TIMEOUT_MS = DEFAULT_TIMEOUT_S * 1000


def connect(
    db_path: str,
    *,
    readonly: bool = False,
    timeout: float = DEFAULT_TIMEOUT_S,
    row_factory: bool = True,
    check_same_thread: bool = True,
    immutable: bool = False,
) -> sqlite3.Connection:
    """Open a SQLite connection with FK enforcement and a busy timeout.

    Args:
        db_path: Filesystem path to the database.
        readonly: Open via a ``file:...?mode=ro`` URI (for readers such as
            agitop panels that must never write).
        timeout: Seconds to wait on a locked database before erroring. Also
            pinned as ``PRAGMA busy_timeout`` in milliseconds.
        row_factory: When True, set ``sqlite3.Row`` for dict-style access.
        check_same_thread: Passed through to ``sqlite3.connect`` (LangGraph
            checkpoint DBs need ``False``).
        immutable: When readonly, append ``immutable=1`` (agitop cycle-log
            readers that must not touch WAL).

    Returns:
        A configured :class:`sqlite3.Connection`.
    """
    if readonly:
        q = "mode=ro"
        if immutable:
            q += "&immutable=1"
        uri = f"file:{db_path}?{q}"
        conn = sqlite3.connect(
            uri, uri=True, timeout=timeout, check_same_thread=check_same_thread
        )
    else:
        conn = sqlite3.connect(
            db_path, timeout=timeout, check_same_thread=check_same_thread
        )

    # Per-connection pragmas. foreign_keys cannot be toggled inside a
    # transaction; a freshly opened connection is not in one, so this is safe.
    conn.execute(f"PRAGMA busy_timeout = {int(timeout * 1000)}")
    conn.execute("PRAGMA foreign_keys = ON")

    if row_factory:
        conn.row_factory = sqlite3.Row
    return conn


def connect_compat(
    database: str,
    timeout: float = DEFAULT_TIMEOUT_S,
    *,
    uri: bool = False,
    check_same_thread: bool = True,
    row_factory: bool = False,
    **_ignored,
) -> sqlite3.Connection:
    """Drop-in replacement for ``sqlite3.connect`` used by the D24 retrofit.

    Understands plain paths and ``file:…?mode=ro[&immutable=1]`` URIs.
    Defaults ``row_factory=False`` so tuple-style callers keep working;
    callers that set ``conn.row_factory = sqlite3.Row`` themselves are unchanged.
    """
    path = database
    readonly = False
    immutable = False
    if uri or (isinstance(database, str) and database.startswith("file:")):
        rest = database[5:] if database.startswith("file:") else database
        if "?" in rest:
            path, query = rest.split("?", 1)
            qs = dict(
                part.split("=", 1) for part in query.split("&") if "=" in part
            )
            readonly = qs.get("mode") == "ro"
            immutable = qs.get("immutable") in ("1", "true")
        else:
            path = rest
    return connect(
        path,
        readonly=readonly,
        timeout=timeout,
        row_factory=row_factory,
        check_same_thread=check_same_thread,
        immutable=immutable,
    )


def organization_db_path() -> str:
    """Resolve the single organization database file (Deliverable D23).

    All organization-domain tables (organizations, customers, vendors,
    products, invoices, estimates, transactions, exchange) live in one file
    because SQLite foreign keys cannot span database files and the schema is
    FK-dense. Overridable via ``AGICTL_ORGANIZATION_DB`` for tests and
    alternate deployments.
    """
    return os.environ.get(
        "AGICTL_ORGANIZATION_DB", "/var/lib/versa-agi/organization.db"
    )
