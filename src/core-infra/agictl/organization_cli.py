"""`agictl organization` command group (Deliverable D9).

Top-level group chosen by Stephen: ``agictl organization <entity> <action>``.
Entities and their columns come from :data:`organization_store.ENTITIES`, so the
add / get / list / update / upsert commands are generated consistently from one
source of truth rather than hand-copied per entity.

Money options are plain integers (cents, D1); the store passes them through
unchanged. Effectful commands emit the standard agictl JSON envelope via
``json_response``; read commands print a JSON ``{"success": true, ...}`` payload
so agents and scripts can parse them.

Times (state_time_display.md §1 rule 10): ``created_at`` and ``updated_at`` are
shown in local time with the zone name. The calendar dates of a record (issue,
due, paid, expiry, transaction date) are never shifted. ``--stored`` prints the
row as stored, for a script that reads those two fields.
"""

from __future__ import annotations

import json
import os
import sys

import click

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import organization_store as store  # noqa: E402
import organization_demo as demo  # noqa: E402
import local_time  # noqa: E402

# The stored UTC times in an Organization row. Not local_time.TIME_FIELDS: that
# set holds `due_date`, which here is an invoice's calendar date.
ROW_TIME_FIELDS = frozenset({"created_at", "updated_at"})
_STORED_HELP = "Print created_at / updated_at as stored (UTC), for a script."


def _shown(rows, stored: bool):
    """Rows as printed: the two time fields local, unless the caller asked for stored values."""
    return rows if stored else local_time.localize(rows, fields=ROW_TIME_FIELDS)


def _field_options(entity: str):
    """Build the list of click.option decorators for an entity's columns."""
    s = store.spec(entity)
    decorators = []
    for col in s["columns"]:
        flag = "--" + col.replace("_", "-")
        if col in s["money"]:
            dec = click.option(flag, type=int, default=None,
                               help="integer cents")
        elif col in s["int"] or col in s.get("bool", []):
            dec = click.option(flag, type=int, default=None)
        elif col in s["real"]:
            dec = click.option(flag, type=float, default=None)
        else:
            dec = click.option(flag, default=None)
        decorators.append(dec)
    return decorators


def _apply(decorators, fn):
    for dec in reversed(decorators):
        fn = dec(fn)
    return fn


def register(cli, *, json_response):
    """Attach the `organization` group to the root agictl `cli`."""

    @cli.group("organization")
    def organization():
        """Organization domain — orgs, products, invoices, estimates,
        transactions, and the integration exchange. Money is integer cents."""
        pass

    for entity in store.entity_names():
        _attach(organization, entity, json_response)

    _attach_demo(organization, json_response)


def _attach_demo(organization, json_response):
    """Demo dataset commands for activation showcase (seed/clear/status)."""

    @organization.group("demo")
    def demo_grp():
        """Sample data for exploring the Organization feature after activation.
        Safe: clear only ever wipes a pure-demo database."""
        pass

    @demo_grp.command("seed")
    def demo_seed():
        """Populate a clean database with a full demo dataset."""
        try:
            result = demo.seed()
        except demo.DemoError as e:
            return json_response(False, error=str(e), code="demo_refused")
        return json_response(True, action="seeded", **result)

    @demo_grp.command("clear")
    def demo_clear():
        """Remove all demo data (refuses if any real data is present)."""
        try:
            result = demo.clear()
        except demo.DemoError as e:
            return json_response(False, error=str(e), code="demo_refused")
        return json_response(True, action="cleared", **result)

    @demo_grp.command("status")
    def demo_status():
        """Report whether demo data is present."""
        import json as _json
        print(_json.dumps({"success": True, **demo.status()}))
        return True


def _attach(organization, entity: str, json_response):
    spec = store.spec(entity)
    grp = organization.group(entity)(lambda: None)
    grp.help = f"Manage {spec['table']}."

    # ── add ──
    def add(**kwargs):
        fields = {k: v for k, v in kwargs.items() if v is not None}
        try:
            row_id = store.insert(entity, fields)
        except store.OrganizationStoreError as e:
            return json_response(False, error=str(e), code=e.code)
        return json_response(True, entity=entity, id=row_id, action="created")
    add.__name__ = f"{entity}_add"
    add = _apply(_field_options(entity), add)
    grp.command("add")(add)

    # ── get <id> ──
    @grp.command("get")
    @click.argument("row_id", type=int)
    @click.option("--stored", is_flag=True, help=_STORED_HELP)
    def get(row_id, stored):
        row = store.get(entity, row_id)
        if row is None:
            return json_response(False, error=f"{entity} {row_id} not found",
                                 code="not_found")
        print(json.dumps({"success": True, "row": _shown(row, stored)}))
        return True

    # ── list ──
    @grp.command("list")
    @click.option("--external-id", default=None)
    @click.option("--org-id", type=int, default=None)
    @click.option("--status", default=None)
    @click.option("--limit", type=int, default=100)
    @click.option("--stored", is_flag=True, help=_STORED_HELP)
    def list_cmd(external_id, org_id, status, limit, stored):
        where = {}
        cols = spec["columns"]
        if external_id is not None and "external_id" in cols:
            where["external_id"] = external_id
        if org_id is not None and "org_id" in cols:
            where["org_id"] = org_id
        if status is not None and "status" in cols:
            where["status"] = status
        try:
            rows = store.list_rows(entity, where=where or None, limit=limit)
        except store.OrganizationStoreError as e:
            return json_response(False, error=str(e), code=e.code)
        print(json.dumps({"success": True, "count": len(rows), "rows": _shown(rows, stored)}))
        return True

    # ── update <id> ──
    def update(row_id, **kwargs):
        fields = {k: v for k, v in kwargs.items() if v is not None}
        if not fields:
            return json_response(False, error="No fields to update",
                                 code="empty_update")
        try:
            changed = store.update(entity, row_id, fields)
        except store.OrganizationStoreError as e:
            return json_response(False, error=str(e), code=e.code)
        if not changed:
            return json_response(False, error=f"{entity} {row_id} not found",
                                 code="not_found")
        return json_response(True, entity=entity, id=row_id, action="updated")
    update.__name__ = f"{entity}_update"
    update = _apply(_field_options(entity), update)
    update = click.argument("row_id", type=int)(update)
    grp.command("update")(update)

    # ── upsert (external_id entities only) ──
    if spec["external_id"]:
        def upsert(**kwargs):
            fields = {k: v for k, v in kwargs.items() if v is not None}
            try:
                row_id, created = store.upsert(entity, fields)
            except store.OrganizationStoreError as e:
                return json_response(False, error=str(e), code=e.code)
            return json_response(True, entity=entity, id=row_id,
                                 action="created" if created else "updated")
        upsert.__name__ = f"{entity}_upsert"
        upsert = _apply(_field_options(entity), upsert)
        grp.command("upsert")(upsert)

    # ── delete <id> ──
    @grp.command("delete")
    @click.argument("row_id", type=int)
    def delete(row_id):
        try:
            removed = store.delete(entity, row_id)
        except store.OrganizationStoreError as e:
            return json_response(False, error=str(e), code=e.code)
        if not removed:
            return json_response(False, error=f"{entity} {row_id} not found",
                                 code="not_found")
        return json_response(True, entity=entity, id=row_id, action="deleted")

