"""Organization domain writer — validated mutations for agitop (D33).

Counterpart to :mod:`organization_reader`. Per the TS-11 UI Specification agitop
**never executes raw SQL inside a panel**; reads go through a Reader and writes
go through this Writer. The Writer is deliberately thin: it delegates to the same
:mod:`organization_store` path that ``agictl`` uses, so the UI and agents share
**one** validated, FK-checked, money-as-cents code path. Surfacing the store this
way keeps the tooling honest — any registry/validation gap shows up identically
in the CLI and the dashboard.

Unlike the Reader (which swallows errors to stay resilient for display), the
Writer returns a small structured result ``{"success": bool, ...}`` mirroring the
agictl JSON envelope, so panels can show inline success/failure without crashing.
"""
from __future__ import annotations


import os
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import db_connect  # noqa: E402
import organization_store as store  # noqa: E402


class OrganizationWriter:
    """Validated write access for the Organization domain (create/update/delete)."""

    def __init__(self, db_path: str | None = None):
        self.db_path = db_path or db_connect.organization_db_path()
        # Point the shared store (also used by agictl) at the same file the
        # Reader reads, so UI writes and reads never diverge — even when agitop
        # is launched with a non-default --organization-db.
        os.environ["AGICTL_ORGANIZATION_DB"] = self.db_path

    # ── registry passthrough (drives the generic form) ──
    def entity_names(self) -> list[str]:
        return store.entity_names()

    def spec(self, entity: str) -> dict:
        return store.spec(entity)

    # ── mutations ──
    def create(self, entity: str, fields: dict) -> dict:
        """Insert a row. Returns {'success', 'id'|'error', 'code', 'action'}."""
        try:
            row_id = store.insert(entity, fields)
        except store.OrganizationStoreError as e:
            return {"success": False, "error": str(e), "code": e.code}
        return {"success": True, "id": row_id, "action": "created"}

    def update(self, entity: str, row_id: int, fields: dict) -> dict:
        """Update a row by id."""
        try:
            changed = store.update(entity, int(row_id), fields)
        except store.OrganizationStoreError as e:
            return {"success": False, "error": str(e), "code": e.code}
        if not changed:
            return {"success": False, "error": f"{entity} {row_id} not found",
                    "code": "not_found"}
        return {"success": True, "id": int(row_id), "action": "updated"}

    def delete(self, entity: str, row_id: int) -> dict:
        """Delete a row by id. FK-referenced rows surface a 'constraint' error."""
        try:
            removed = store.delete(entity, int(row_id))
        except store.OrganizationStoreError as e:
            return {"success": False, "error": str(e), "code": e.code}
        if not removed:
            return {"success": False, "error": f"{entity} {row_id} not found",
                    "code": "not_found"}
        return {"success": True, "id": int(row_id), "action": "deleted"}

    def get(self, entity: str, row_id: int) -> dict | None:
        """Fetch a single row (used to pre-fill the edit form)."""
        try:
            return store.get(entity, int(row_id))
        except store.OrganizationStoreError:
            return None

    # ── picklist maintenance (Manage Lists "Replace & Delete") ──
    def count_value(self, table_name: str, field_name: str, value: str) -> int:
        """How many data rows currently use ``value`` in ``table_name.field_name``."""
        try:
            return store.count_value(table_name, field_name, value)
        except store.OrganizationStoreError:
            return 0

    def reassign(self, table_name: str, field_name: str, old_value: str,
                 new_value: str) -> dict:
        """Repoint rows from ``old_value`` to ``new_value`` (before deleting an
        in-use picklist option). Returns {'success', 'changed'|'error', 'code'}."""
        try:
            changed = store.reassign(table_name, field_name, old_value, new_value)
        except store.OrganizationStoreError as e:
            return {"success": False, "error": str(e), "code": e.code}
        return {"success": True, "changed": changed, "action": "reassigned"}
