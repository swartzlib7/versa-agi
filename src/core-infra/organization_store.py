"""Data layer for the Organization domain (Deliverable D9).

A small, explicit schema registry (:data:`ENTITIES`) drives generic
insert / get / list / update / upsert helpers so every entity behaves
consistently without copy-pasted CRUD. All access routes through the shared
connection helper (:mod:`db_connect`, D3) — FK enforcement + busy timeout on
every connection — against the single ``organization.db`` file (D23).

Money is handled as integer cents end-to-end (D1): the store neither scales nor
rounds — callers pass and receive integer minor units. ``updated_at`` is
maintained by AFTER UPDATE triggers in the schema (D6), so the store does not
touch it on update.
"""

from __future__ import annotations

import os
import sys
from typing import Any

# core-infra is on sys.path when agictl runs; make db_connect importable both
# as a sibling module and when this file is imported from the agictl package.
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import db_connect  # noqa: E402


class OrganizationStoreError(Exception):
    """Raised for invalid entity/field usage or constraint violations."""

    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


# ─── Schema registry ──────────────────────────────────────────────────
# One entry per CLI-facing entity. ``columns`` lists the writable columns
# (id / created_at / updated_at are managed automatically). ``money`` columns
# are integer cents; ``real`` columns are floats; ``bool`` columns are 0/1
# flags; ``int`` columns are plain integers (FKs, counters); everything else is
# TEXT. ``bool`` columns are coerced to int like ints, but are listed separately
# so surfaces (e.g. the agitop form) can render them as checkboxes.
# ``external_id`` marks entities that support upsert-on-external_id.
# ``has_updated_at`` marks entities whose schema carries an updated_at trigger.
ENTITIES: dict[str, dict[str, Any]] = {
    "org": {
        "table": "organizations",
        "columns": ["name", "slug", "type", "notes", "logo_path",
                    "external_id", "is_active"],
        "required": ["name"],
        "money": [],
        "real": [],
        "int": [],
        "bool": ["is_active"],
        "external_id": True,
        "has_updated_at": True,
    },
    "product": {
        "table": "products",
        "columns": ["org_id", "name", "description", "type", "sku",
                    "unit_price_cents", "currency", "is_active", "external_id"],
        "required": ["org_id", "name"],
        "money": ["unit_price_cents"],
        "real": [],
        "int": ["org_id"],
        "bool": ["is_active"],
        "external_id": True,
        "has_updated_at": True,
    },
    "invoice": {
        "table": "invoices",
        "columns": ["org_id", "customer_org_id", "invoice_number", "status",
                    "subtotal_cents", "tax_total_cents", "total_cents",
                    "currency", "issue_date", "due_date", "paid_date", "notes",
                    "external_id"],
        "required": ["org_id"],
        "money": ["subtotal_cents", "tax_total_cents", "total_cents"],
        "real": [],
        "int": ["org_id", "customer_org_id"],
        "external_id": True,
        "has_updated_at": True,
    },
    "invoice-item": {
        "table": "invoice_line_items",
        "columns": ["invoice_id", "product_id", "description", "quantity",
                    "unit_price_cents", "total_cents"],
        "required": ["invoice_id"],
        "money": ["unit_price_cents", "total_cents"],
        "real": ["quantity"],
        "int": ["invoice_id", "product_id"],
        "external_id": False,
        "has_updated_at": False,
    },
    "estimate": {
        "table": "estimates",
        "columns": ["org_id", "customer_org_id", "estimate_number", "status",
                    "subtotal_cents", "tax_total_cents", "total_cents",
                    "currency", "issue_date", "expiry_date", "notes",
                    "external_id", "converted_to_invoice_id"],
        "required": ["org_id"],
        "money": ["subtotal_cents", "tax_total_cents", "total_cents"],
        "real": [],
        "int": ["org_id", "customer_org_id", "converted_to_invoice_id"],
        "external_id": True,
        "has_updated_at": True,
    },
    "estimate-item": {
        "table": "estimate_line_items",
        "columns": ["estimate_id", "product_id", "description", "quantity",
                    "unit_price_cents", "total_cents"],
        "required": ["estimate_id"],
        "money": ["unit_price_cents", "total_cents"],
        "real": ["quantity"],
        "int": ["estimate_id", "product_id"],
        "external_id": False,
        "has_updated_at": False,
    },
    "transaction": {
        "table": "transactions",
        "columns": ["org_id", "counterparty_org_id", "account_name",
                    "transaction_date", "description", "amount_cents",
                    "currency", "category", "external_id"],
        "required": ["org_id"],
        "money": ["amount_cents"],
        "real": [],
        "int": ["org_id", "counterparty_org_id"],
        "external_id": True,
        "has_updated_at": True,
    },
    "exchange": {
        "table": "exchange",
        "columns": ["name", "source_table", "source_id", "external_id",
                    "source_org_id", "target_org_id",
                    "origin", "status", "replicate", "error_message"],
        "required": ["name", "source_table"],
        "money": [],
        "real": [],
        "int": ["source_id", "source_org_id", "target_org_id"],
        "bool": ["replicate"],
        "external_id": True,
        "has_updated_at": True,
    },

    # ── Contact records ──
    "email": {
        "table": "email_addresses",
        "columns": ["email", "label", "is_primary", "usage_notes", "credential_id"],
        "required": ["email"],
        "money": [], "real": [], "int": ["credential_id"], "bool": ["is_primary"],
        "external_id": False, "has_updated_at": False,
    },
    "address": {
        "table": "physical_addresses",
        "columns": ["line_1", "line_2", "city", "state", "postal_code",
                    "country", "label", "is_primary"],
        "required": [],
        "money": [], "real": [], "int": [], "bool": ["is_primary"],
        "external_id": False, "has_updated_at": False,
    },

    # ── Relationship bridge (people ↔ org) ──
    # Customer/Vendor links are NOT stored — they are derived from the invoices/
    # estimates two orgs exchange (see organization_reader). org_staff is the one
    # stored membership bridge: a row means "this connection belongs to this org".
    "org-staff": {
        "table": "org_staff",
        "columns": ["org_id", "connection_uid"],
        "required": ["org_id"],
        "money": [], "real": [], "int": ["org_id"],
        "external_id": False, "has_updated_at": False,
    },

    # ── Email bridge ──
    # A customer/vendor is just an organization, so its email lives here too.
    "org-email": {
        "table": "org_emails",
        "columns": ["org_id", "email_id"],
        "required": ["org_id", "email_id"],
        "money": [], "real": [], "int": ["org_id", "email_id"],
        "external_id": False, "has_updated_at": False,
    },

    # ── Address bridges ──
    # org_addresses: any org's address. org_staff_addresses: a staff person's.
    "org-address": {
        "table": "org_addresses",
        "columns": ["org_id", "address_id"],
        "required": ["org_id", "address_id"],
        "money": [], "real": [], "int": ["org_id", "address_id"],
        "external_id": False, "has_updated_at": False,
    },
    "org-staff-address": {
        "table": "org_staff_addresses",
        "columns": ["org_staff_id", "address_id"],
        "required": ["org_staff_id", "address_id"],
        "money": [], "real": [], "int": ["org_staff_id", "address_id"],
        "external_id": False, "has_updated_at": False,
    },

    # ── Credentials (agentic access) ──
    "credential": {
        "table": "credentials",
        "columns": ["auth_type", "configuration", "notes"],
        "required": ["auth_type"],
        "money": [], "real": [], "int": [], "bool": [],
        "external_id": False, "has_updated_at": True,
    },

    # ── Universal managed-vocabulary lookup ──
    # One row = one selectable option for a (table_name, field_name) target.
    # Drives the agitop form picklists (org/product type, invoice/estimate status,
    # transaction category, currency). table_name='' applies the option to that
    # field on ANY table (currency).
    "picklist": {
        "table": "picklists",
        "columns": ["name", "value", "table_name", "field_name", "position"],
        "required": ["name", "value", "field_name"],
        "money": [], "real": [], "int": ["position"],
        "external_id": False, "has_updated_at": False,
    },
}


def entity_names() -> list[str]:
    """All registered entity keys (CLI subcommand names)."""
    return list(ENTITIES)


def spec(entity: str) -> dict[str, Any]:
    """Return the registry entry for ``entity`` or raise ``unknown_entity``."""
    try:
        return ENTITIES[entity]
    except KeyError:
        raise OrganizationStoreError(
            "unknown_entity",
            f"Unknown entity '{entity}'. Known: {', '.join(ENTITIES)}",
        )


def _coerce(entity: str, fields: dict[str, Any]) -> dict[str, Any]:
    """Validate field names and coerce money/int/real values to native types."""
    s = spec(entity)
    allowed = set(s["columns"])
    out: dict[str, Any] = {}
    for key, val in fields.items():
        if key not in allowed:
            raise OrganizationStoreError(
                "unknown_field",
                f"'{key}' is not a column of '{entity}'. "
                f"Allowed: {', '.join(s['columns'])}",
            )
        if val is None:
            out[key] = None
            continue
        if key in s["money"] or key in s["int"] or key in s.get("bool", []):
            try:
                out[key] = int(val)
            except (TypeError, ValueError):
                kind = "money (cents)" if key in s["money"] else "integer"
                raise OrganizationStoreError(
                    "type_error", f"'{key}' must be {kind}, got {val!r}"
                )
        elif key in s["real"]:
            try:
                out[key] = float(val)
            except (TypeError, ValueError):
                raise OrganizationStoreError(
                    "type_error", f"'{key}' must be a number, got {val!r}"
                )
        elif key == "configuration":
            # JSON-text column — validate it's parseable before writing.
            import json as _json
            sval = str(val)
            try:
                _json.loads(sval)
            except (ValueError, TypeError):
                raise OrganizationStoreError(
                    "type_error", f"'configuration' must be valid JSON, got {val!r}"
                )
            out[key] = sval
        else:
            out[key] = str(val)
    return out


def _connect():
    return db_connect.connect(db_connect.organization_db_path())


# Entities whose document number auto-generates when not supplied: entity →
# (column, prefix). A locally-authored invoice/estimate (no number given) gets
# the next sequence value, zero-filled to 8 digits — INV-00000001, EST-00000001.
# A number supplied by the caller (e.g. a Wave-pulled invoice carrying its own
# number) is always preserved.
_AUTO_NUMBER = {
    "invoice": ("invoice_number", "INV-"),
    "estimate": ("estimate_number", "EST-"),
}
_AUTO_NUMBER_WIDTH = 8


def _next_document_number(conn, table: str, column: str, prefix: str) -> str:
    """Next ``<prefix><n:08d>`` for ``table.column``, one past the current max.

    Scans only values matching the prefix + an all-digit suffix, so hand-entered
    or Wave numbers in other formats never perturb the sequence. Single-writer
    system, so a plain max+1 is sufficient (no number-allocation table)."""
    rows = conn.execute(
        f"SELECT {column} FROM {table} WHERE {column} LIKE ?", (prefix + "%",)
    ).fetchall()
    highest = 0
    plen = len(prefix)
    for r in rows:
        suffix = (r[0] or "")[plen:]
        if suffix.isdigit():
            highest = max(highest, int(suffix))
    return f"{prefix}{highest + 1:0{_AUTO_NUMBER_WIDTH}d}"


def insert(entity: str, fields: dict[str, Any]) -> int:
    """Insert a row; return its new id. Raises on missing required fields.

    For invoices/estimates, the document number auto-generates (next sequence,
    8-digit zero-filled) when the caller did not supply one."""
    s = spec(entity)
    data = _coerce(entity, fields)
    missing = [c for c in s["required"] if data.get(c) in (None, "")]
    if missing:
        raise OrganizationStoreError(
            "missing_required",
            f"{entity} requires: {', '.join(missing)}",
        )
    cols = list(data)
    placeholders = ", ".join("?" for _ in cols)
    sql = f"INSERT INTO {s['table']} ({', '.join(cols)}) VALUES ({placeholders})"
    conn = _connect()
    try:
        auto = _AUTO_NUMBER.get(entity)
        if auto is not None:
            col, prefix = auto
            if data.get(col) in (None, ""):
                value = _next_document_number(conn, s["table"], col, prefix)
                data[col] = value
                if col not in cols:
                    cols.append(col)
                    placeholders = ", ".join("?" for _ in cols)
                    sql = (f"INSERT INTO {s['table']} ({', '.join(cols)}) "
                           f"VALUES ({placeholders})")
        cur = conn.execute(sql, [data[c] for c in cols])
        conn.commit()
        return int(cur.lastrowid)
    except OrganizationStoreError:
        raise
    except Exception as e:  # FK / STRICT / constraint failures surface here
        msg = str(e)
        if "UNIQUE constraint failed" in msg and "external_id" in msg:
            eid = data.get("external_id", "?")
            raise OrganizationStoreError(
                "duplicate_external_id",
                f"{entity} with external_id '{eid}' already exists",
            )
        raise OrganizationStoreError("constraint", msg)
    finally:
        conn.close()


def get(entity: str, row_id: int) -> dict[str, Any] | None:
    """Fetch one row by id as a dict, or None."""
    s = spec(entity)
    conn = _connect()
    try:
        row = conn.execute(
            f"SELECT * FROM {s['table']} WHERE id = ?", (int(row_id),)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def list_rows(
    entity: str,
    *,
    where: dict[str, Any] | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """List rows, optionally filtered by exact-match ``where``, newest id first."""
    s = spec(entity)
    sql = f"SELECT * FROM {s['table']}"
    params: list[Any] = []
    if where:
        clauses = []
        for key, val in where.items():
            if key not in s["columns"] and key != "id":
                raise OrganizationStoreError(
                    "unknown_field", f"'{key}' is not filterable on '{entity}'"
                )
            clauses.append(f"{key} = ?")
            params.append(val)
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY id DESC LIMIT ?"
    params.append(int(limit))
    conn = _connect()
    try:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()


def update(entity: str, row_id: int, fields: dict[str, Any]) -> bool:
    """Update a row by id. Returns True if a row changed. updated_at is
    refreshed by the schema's AFTER UPDATE trigger (D6)."""
    data = _coerce(entity, fields)
    if not data:
        raise OrganizationStoreError("empty_update", "No fields to update")
    s = spec(entity)
    assignments = ", ".join(f"{c} = ?" for c in data)
    sql = f"UPDATE {s['table']} SET {assignments} WHERE id = ?"
    conn = _connect()
    try:
        cur = conn.execute(sql, [*data.values(), int(row_id)])
        conn.commit()
        return cur.rowcount > 0
    except Exception as e:
        raise OrganizationStoreError("constraint", str(e))
    finally:
        conn.close()


def upsert(
    entity: str,
    fields: dict[str, Any],
    *,
    match_key: str = "external_id",
) -> tuple[int, bool]:
    """Insert or update keyed on ``match_key`` (default external_id).

    Returns ``(row_id, created)`` where ``created`` is True for an insert.
    This is the idempotent path the weekly Wave sync uses to land records
    without creating duplicates.
    """
    s = spec(entity)
    if match_key not in s["columns"]:
        raise OrganizationStoreError(
            "bad_match_key",
            f"'{match_key}' is not a column of '{entity}'",
        )
    match_val = fields.get(match_key)
    if match_val in (None, ""):
        raise OrganizationStoreError(
            "missing_match_value",
            f"upsert needs a non-empty '{match_key}'",
        )
    conn = _connect()
    try:
        existing = conn.execute(
            f"SELECT id FROM {s['table']} WHERE {match_key} = ?", (match_val,)
        ).fetchone()
    finally:
        conn.close()
    if existing:
        row_id = int(existing["id"])
        # Update everything except the match key itself.
        rest = {k: v for k, v in fields.items() if k != match_key}
        if rest:
            update(entity, row_id, rest)
        return row_id, False
    return insert(entity, fields), True


def delete(entity: str, row_id: int) -> bool:
    """Delete a row by id. Returns True if a row was removed.

    FK enforcement is ON for every connection (D3), so deleting a row that
    other rows still reference (e.g. an org with invoices) raises a
    ``constraint`` error rather than silently orphaning data — the caller
    surfaces it. Children must be removed first (or via ON DELETE rules).
    """
    s = spec(entity)
    sql = f"DELETE FROM {s['table']} WHERE id = ?"
    conn = _connect()
    try:
        cur = conn.execute(sql, (int(row_id),))
        conn.commit()
        return cur.rowcount > 0
    except Exception as e:  # FK / constraint failures surface here
        raise OrganizationStoreError("constraint", str(e))
    finally:
        conn.close()


# ── Picklist data-maintenance helpers (Manage Lists "Replace & Delete") ──

def _resolve_table_field(table_name: str, field_name: str) -> tuple[str, str]:
    """Validate a (table, column) pair against the registry and return it.

    Picklists reference target columns by NAME; these helpers must never splice
    an unvalidated identifier into SQL. We only accept a table that a registered
    entity owns and a column that entity declares — anything else raises
    ``unknown_field`` (no arbitrary identifiers reach SQL)."""
    for s in ENTITIES.values():
        if s["table"] == table_name:
            if field_name in s["columns"]:
                return table_name, field_name
            break
    raise OrganizationStoreError(
        "unknown_field", f"'{field_name}' is not a column of table '{table_name}'")


def count_value(table_name: str, field_name: str, value: str) -> int:
    """How many rows in ``table_name`` currently hold ``value`` in ``field_name``.

    Used to tell the operator how many records a picklist option is in use by
    before they Replace & Delete it."""
    table, field = _resolve_table_field(table_name, field_name)
    conn = _connect()
    try:
        row = conn.execute(
            f"SELECT COUNT(*) AS n FROM {table} WHERE {field} = ?", (value,)
        ).fetchone()
        return int(row["n"]) if row else 0
    finally:
        conn.close()


def reassign(table_name: str, field_name: str, old_value: str,
             new_value: str) -> int:
    """Repoint every row using ``old_value`` to ``new_value`` (Replace & Delete).

    Returns the number of rows changed. Identifiers are registry-validated; the
    values are bound parameters."""
    table, field = _resolve_table_field(table_name, field_name)
    conn = _connect()
    try:
        cur = conn.execute(
            f"UPDATE {table} SET {field} = ? WHERE {field} = ?",
            (new_value, old_value))
        conn.commit()
        return cur.rowcount
    except Exception as e:
        raise OrganizationStoreError("constraint", str(e))
    finally:
        conn.close()

