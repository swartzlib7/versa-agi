"""Operational test suite for the Organization domain database (Wave integration).

This suite is the authoritative, runnable specification of how ``organization.db``
behaves. It exercises the complete design end to end so that, once green, the
database is "fully operational per complete design" — every table, every
relationship, every trigger, the full ``agictl organization`` CLI surface, and a
realistic permutation of business data scenarios.

It is written to read like a manual: each test class is a chapter, each test
method is a documented behaviour. Run verbose to get a manual-style transcript:

    cd core-infra
    python -m unittest harness.tests.test_organization_db            # quiet
    python -m unittest -v harness.tests.test_organization_db         # transcript

Chapters
--------
1.  TestSchemaStructure          — tables, STRICT typing, WAL, indexes, money/qty
2.  TestRelationships            — FK enforcement, dynamic customer/vendor, soft refs
3.  TestUpdatedAtTriggers        — updated_at maintenance + the explicit-value guard
4.  TestMoneyMechanics           — integer cents, REAL quantity rounding, currencies
5.  TestCliSurface               — agictl organization CRUD + upsert + validation
6.  TestBusinessScenario         — a full C3D Studio lifecycle across many tables
7.  TestExchangeLifecycle        — Wave pull / local create / push success / push fail

Design facts under test (see the plan §9):
  * Money is INTEGER minor units (cents). The store never scales or rounds —
    callers pass and receive cents. ``total_cents = round(quantity * unit_price_cents)``.
  * ``quantity`` is REAL (fractional units are real); money never is.
  * Tables are STRICT (typed) and the file is WAL with FK enforcement on every
    connection (the shared ``db_connect`` helper).
  * ``updated_at`` is maintained by AFTER UPDATE triggers on the six mutable
    entities; a caller-supplied ``updated_at`` is preserved (WHEN guard).
  * ``connection_uid`` is a SOFT reference (plain TEXT, no FK) because the native
    ``connections`` table lives in a different database file (tasks.db).
"""

import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest

# core-infra on path (test lives at core-infra/harness/tests/)
_CORE_INFRA = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _CORE_INFRA)

import db_connect                       # noqa: E402
import organization_store as store      # noqa: E402
import organization_demo as demo        # noqa: E402
from agictl import organization_cli     # noqa: E402
from agitop.data.organization_reader import (  # noqa: E402
    OrganizationReader, format_money, invoice_colour, sync_colour,
)

_INIT_SCRIPT = os.path.join(_CORE_INFRA, "scripts", "init_organization_db.sh")

# All 16 tables of the normalized design. Customer/Vendor relationships are NOT
# stored — they are derived from the invoices/estimates two orgs exchange — so the
# role-narrowed bridges (org_customers/org_vendors + their connection/email/
# address variants) are gone. org_staff is the one stored people↔org bridge.
# ``picklists`` is the universal managed-vocabulary lookup (org/product type,
# invoice/estimate status, transaction category, currency).
# ``credentials`` stores agentic access tokens (IMAP/MCP/API).
ALL_TABLES = {
    "organizations", "email_addresses", "physical_addresses",
    "org_staff", "credentials",
    "products", "invoices", "invoice_line_items",
    "estimates", "estimate_line_items", "transactions", "exchange",
    "org_emails", "org_addresses", "org_staff_addresses",
    "picklists",
}
# Tables that carry an updated_at trigger.
TRIGGER_TABLES = {
    "organizations", "products", "invoices",
    "estimates", "transactions", "exchange", "credentials",
}


class OrgDBTestBase(unittest.TestCase):
    """Fresh ``organization.db`` per test, built by the real init script.

    The init script is the production schema source, so the tests run against
    exactly what setup deploys — not a hand-rolled fixture.
    """

    def setUp(self):
        self._dir = tempfile.mkdtemp(prefix="orgdb_")
        self.db = os.path.join(self._dir, "organization.db")
        proc = subprocess.run(
            ["bash", _INIT_SCRIPT, self.db],
            capture_output=True, text=True,
        )
        if proc.returncode != 0:
            self.fail(f"init script failed: {proc.stderr or proc.stdout}")
        self._prev_env = os.environ.get("AGICTL_ORGANIZATION_DB")
        os.environ["AGICTL_ORGANIZATION_DB"] = self.db

    def tearDown(self):
        if self._prev_env is None:
            os.environ.pop("AGICTL_ORGANIZATION_DB", None)
        else:
            os.environ["AGICTL_ORGANIZATION_DB"] = self._prev_env
        shutil.rmtree(self._dir, ignore_errors=True)

    # ── helpers ──
    def conn(self):
        return db_connect.connect(self.db)

    def cli(self, *args):
        """Invoke `agictl organization ...` through a fresh Click root bound to a
        capturing json_response (same envelope contract as the real cli.py)."""
        from click.testing import CliRunner
        import click

        def json_response(success, **kw):
            print(json.dumps({"success": success, **kw}))
            return success

        @click.group()
        def root():
            pass

        organization_cli.register(root, json_response=json_response)
        return CliRunner().invoke(root, list(args), catch_exceptions=False)

    @staticmethod
    def last_json(result):
        return json.loads(result.output.strip().splitlines()[-1])


# ═══════════════════════════════════════════════════════════════════════
# 1. Schema structure
# ═══════════════════════════════════════════════════════════════════════
class TestSchemaStructure(OrgDBTestBase):
    """The physical schema: all tables present, STRICT, WAL, indexed, typed."""

    def test_all_16_tables_present(self):
        """The normalized design materialises as 16 tables."""
        c = self.conn()
        tables = {r[0] for r in c.execute(
            "SELECT name FROM sqlite_schema WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%'")}
        c.close()
        self.assertEqual(tables, ALL_TABLES)

    def test_every_table_is_strict(self):
        """Every table is STRICT, so column types are enforced not advisory."""
        c = self.conn()
        loose = [r[0] for r in c.execute(
            "SELECT name FROM sqlite_schema WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%' AND sql NOT LIKE '%STRICT%'")]
        c.close()
        self.assertEqual(loose, [])

    def test_wal_journal_mode_persisted(self):
        """The file is WAL (one-writer / many-reader), set at init, persistent."""
        c = self.conn()
        self.assertEqual(c.execute("PRAGMA journal_mode").fetchone()[0], "wal")
        c.close()

    def test_money_columns_are_integer(self):
        """All *_cents money columns carry INTEGER affinity (no float money)."""
        c = self.conn()
        for table in ("products", "invoices", "invoice_line_items",
                      "estimates", "estimate_line_items", "transactions"):
            cols = {r[1]: r[2] for r in c.execute(f"PRAGMA table_info({table})")}
            for name, decl in cols.items():
                if name.endswith("_cents"):
                    self.assertEqual(decl, "INTEGER", f"{table}.{name}")
        c.close()

    def test_quantity_columns_are_real(self):
        """Line-item quantity is REAL — fractional units (2.5 hrs) are real."""
        c = self.conn()
        for table in ("invoice_line_items", "estimate_line_items"):
            cols = {r[1]: r[2] for r in c.execute(f"PRAGMA table_info({table})")}
            self.assertEqual(cols["quantity"], "REAL", table)
        c.close()

    def test_exchange_indexes_present(self):
        """The exchange tracker has its three plan-mandated indexes."""
        c = self.conn()
        idx = {r[1] for r in c.execute("PRAGMA index_list(exchange)")}
        c.close()
        for want in ("idx_exchange_lookup", "idx_exchange_pending",
                     "idx_exchange_external"):
            self.assertIn(want, idx)

    def test_external_id_indexes_are_unique(self):
        """The five entity-level external_id indexes are UNIQUE — the DB itself
        blocks duplicate external IDs (NULLs exempt by SQLite semantics)."""
        c = self.conn()
        for table in ("organizations", "products", "invoices", "estimates",
                      "transactions"):
            idx_rows = c.execute("PRAGMA index_list(" + table + ")").fetchall()
            ext_idx = [r for r in idx_rows
                       if r[1] == f"idx_{table}_external"]
            self.assertEqual(len(ext_idx), 1, f"missing idx_{table}_external")
            # PRAGMA index_list column 2 is 'unique' (1=unique, 0=not)
            self.assertEqual(ext_idx[0][2], 1,
                             f"idx_{table}_external must be UNIQUE")
        # Exchange external_id is intentionally NOT unique.
        ex_rows = c.execute("PRAGMA index_list(exchange)").fetchall()
        ex_ext = [r for r in ex_rows if r[1] == "idx_exchange_external"]
        self.assertEqual(len(ex_ext), 1)
        self.assertEqual(ex_ext[0][2], 0,
                         "exchange.external_id must NOT be unique")
        c.close()

    def test_registry_matches_schema_one_to_one(self):
        """The CLI/store registry covers exactly the 16 real tables, no drift."""
        self.assertEqual({v["table"] for v in store.ENTITIES.values()}, ALL_TABLES)


# ═══════════════════════════════════════════════════════════════════════
# 2. Relationships
# ═══════════════════════════════════════════════════════════════════════
class TestRelationships(OrgDBTestBase):
    """Foreign keys, the dynamic customer/vendor model, and soft cross-file refs."""

    def test_foreign_key_rejected_for_missing_parent(self):
        """An invoice for a non-existent org is rejected (FK enforced)."""
        with self.assertRaises(store.OrganizationStoreError) as ctx:
            store.insert("invoice", {"org_id": 999, "total_cents": 100})
        self.assertEqual(ctx.exception.code, "constraint")

    def test_invoice_customer_is_an_organization(self):
        """An invoice points org_id (issuer) and customer_org_id (billed) at two
        organizations directly — the relationship is the document, not a bridge."""
        seller = store.insert("org", {"name": "C3D Studio"})
        buyer = store.insert("org", {"name": "Acme Corp"})
        inv = store.insert("invoice", {"org_id": seller,
                                       "customer_org_id": buyer,
                                       "total_cents": 45000})
        row = store.get("invoice", inv)
        self.assertEqual(row["org_id"], seller)
        self.assertEqual(row["customer_org_id"], buyer)
        # Both columns are real FKs to organizations.
        c = self.conn()
        refs = {r[2] for r in c.execute("PRAGMA foreign_key_list(invoices)")}
        c.close()
        self.assertEqual(refs, {"organizations"})

    def test_customer_vendor_are_derived_dynamically(self):
        """A->B invoice makes B a customer of A and A a vendor of B — read back
        through the reader's derived views, with no stored customer/vendor row."""
        a = store.insert("org", {"name": "C3D Studio"})
        b = store.insert("org", {"name": "Acme Corp"})
        store.insert("invoice", {"org_id": a, "customer_org_id": b,
                                 "total_cents": 1000})
        reader = OrganizationReader(self.db)
        self.assertEqual([c["name"] for c in reader.customers_of(a)], ["Acme Corp"])
        self.assertEqual([v["name"] for v in reader.vendors_of(b)], ["C3D Studio"])
        # The inverse is empty (A has no vendor, B has no customer).
        self.assertEqual(reader.vendors_of(a), [])
        self.assertEqual(reader.customers_of(b), [])

    def test_transaction_counterparty_is_optional_second_org(self):
        """A transaction carries org_id (whose books) + an optional
        counterparty_org_id (the vendor/customer) for reconciliation."""
        mine = store.insert("org", {"name": "C3D Studio"})
        vendor = store.insert("org", {"name": "Cloud Host Inc"})
        bare = store.insert("transaction", {"org_id": mine, "amount_cents": -500})
        self.assertIsNone(store.get("transaction", bare)["counterparty_org_id"])
        txn = store.insert("transaction", {"org_id": mine,
                                           "counterparty_org_id": vendor,
                                           "amount_cents": -9000})
        self.assertEqual(store.get("transaction", txn)["counterparty_org_id"], vendor)
        c = self.conn()
        refs = {r[2] for r in c.execute("PRAGMA foreign_key_list(transactions)")}
        c.close()
        self.assertEqual(refs, {"organizations"})

    def test_transaction_counterparty_fk_enforced(self):
        """A non-existent counterparty org is rejected (FK enforced)."""
        mine = store.insert("org", {"name": "C3D Studio"})
        with self.assertRaises(store.OrganizationStoreError) as ctx:
            store.insert("transaction", {"org_id": mine,
                                         "counterparty_org_id": 999,
                                         "amount_cents": -500})
        self.assertEqual(ctx.exception.code, "constraint")

    def test_connection_uid_is_soft_reference(self):
        """connection_uid accepts arbitrary text — it points at tasks.db
        connections (a different file), so it carries NO foreign key."""
        org = store.insert("org", {"name": "C3D Studio"})
        # An arbitrary uid with no matching row anywhere is accepted; a staff row
        # simply means "this connection belongs to this org" (no type).
        staff = store.insert("org-staff",
                            {"org_id": org, "connection_uid": "vv-uid-stephen"})
        self.assertEqual(store.get("org-staff", staff)["connection_uid"],
                         "vv-uid-stephen")
        # Prove there is no FK on connection_uid (only org_id references a table).
        c = self.conn()
        refs = {r[2] for r in c.execute("PRAGMA foreign_key_list(org_staff)")}
        c.close()
        self.assertEqual(refs, {"organizations"})

    def test_estimate_converts_to_invoice(self):
        """An estimate can reference the invoice it converted into (FK)."""
        org = store.insert("org", {"name": "C3D Studio"})
        inv = store.insert("invoice", {"org_id": org, "total_cents": 88000})
        est = store.insert("estimate", {"org_id": org, "total_cents": 88000,
                                        "converted_to_invoice_id": inv})
        self.assertEqual(store.get("estimate", est)["converted_to_invoice_id"], inv)

    def test_foreign_key_check_clean_after_build(self):
        """A well-formed graph leaves zero FK violations."""
        org = store.insert("org", {"name": "C3D Studio"})
        cust = store.insert("org", {"name": "Acme Corp"})
        inv = store.insert("invoice", {"org_id": org, "customer_org_id": cust,
                                       "total_cents": 100})
        prod = store.insert("product", {"org_id": org, "name": "Hour",
                                        "unit_price_cents": 12000})
        store.insert("invoice-item", {"invoice_id": inv, "product_id": prod,
                                      "quantity": 2.5, "unit_price_cents": 12000,
                                      "total_cents": 30000})
        c = self.conn()
        violations = c.execute("PRAGMA foreign_key_check").fetchall()
        c.close()
        self.assertEqual(violations, [])


# ═══════════════════════════════════════════════════════════════════════
# 3. updated_at triggers
# ═══════════════════════════════════════════════════════════════════════
class TestUpdatedAtTriggers(OrgDBTestBase):
    """updated_at maintenance — the delta-sync backbone (plan D6)."""

    def _force_old(self, table, row_id):
        # Stamp a known-old updated_at directly (bypass the trigger's WHEN guard
        # by writing the same column) so we can prove the trigger refreshes it.
        c = self.conn()
        c.execute(f"UPDATE {table} SET updated_at='2020-01-01 00:00:00' "
                  f"WHERE id=?", (row_id,))
        c.commit()
        c.close()

    def test_update_refreshes_updated_at_on_all_trigger_tables(self):
        """Mutating any of the six entities advances updated_at automatically."""
        org = store.insert("org", {"name": "C3D Studio"})
        seed = {
            "organizations": ("org", org, {"notes": "x"}),
            "products": ("product",
                         store.insert("product", {"org_id": org, "name": "P"}),
                         {"description": "y"}),
            "invoices": ("invoice",
                         store.insert("invoice", {"org_id": org, "total_cents": 1}),
                         {"notes": "z"}),
            "estimates": ("estimate",
                          store.insert("estimate", {"org_id": org, "total_cents": 1}),
                          {"notes": "z"}),
            "transactions": ("transaction",
                             store.insert("transaction", {"org_id": org,
                                                          "amount_cents": 1}),
                             {"description": "z"}),
            "exchange": ("exchange",
                         store.insert("exchange", {"name": "Wave",
                                                   "source_table": "invoices"}),
                         {"status": "sync-done"}),
        }
        for table, (entity, row_id, change) in seed.items():
            self._force_old(table, row_id)
            store.update(entity, row_id, change)
            now = store.get(entity, row_id)["updated_at"]
            self.assertFalse(now.startswith("2020"),
                             f"{table}: updated_at did not refresh")

    def test_explicit_updated_at_is_preserved(self):
        """A caller-supplied updated_at wins — the WHEN guard avoids clobbering
        an explicit value (so the Wave puller can stamp source timestamps)."""
        org = store.insert("org", {"name": "C3D Studio"})
        c = self.conn()
        c.execute("UPDATE organizations SET notes='from wave', "
                  "updated_at='2021-06-01 12:00:00' WHERE id=?", (org,))
        c.commit()
        c.close()
        self.assertEqual(store.get("org", org)["updated_at"],
                         "2021-06-01 12:00:00")

    def test_bridging_tables_have_no_updated_at(self):
        """Pure bridges are immutable links — no updated_at column or trigger."""
        c = self.conn()
        for table in ("org_emails", "org_addresses", "org_staff_addresses"):
            cols = {r[1] for r in c.execute(f"PRAGMA table_info({table})")}
            self.assertNotIn("updated_at", cols, table)
        c.close()


# ═══════════════════════════════════════════════════════════════════════
# 4. Money mechanics
# ═══════════════════════════════════════════════════════════════════════
class TestMoneyMechanics(OrgDBTestBase):
    """Integer cents end to end; REAL quantity with deterministic rounding."""

    def test_money_round_trips_as_cents(self):
        """$450.00 is stored and returned as the integer 45000 — no float."""
        org = store.insert("org", {"name": "C3D Studio"})
        inv = store.insert("invoice", {"org_id": org, "total_cents": 45000,
                                       "currency": "USD"})
        self.assertEqual(store.get("invoice", inv)["total_cents"], 45000)

    def test_line_total_is_exact_for_fractional_quantity(self):
        """2.5 hrs × $120.00 = exactly 30000 cents (quantity REAL, total exact)."""
        org = store.insert("org", {"name": "C3D Studio"})
        inv = store.insert("invoice", {"org_id": org})
        qty, price = 2.5, 12000
        total = round(qty * price)
        item = store.insert("invoice-item",
                           {"invoice_id": inv, "quantity": qty,
                            "unit_price_cents": price, "total_cents": total})
        row = store.get("invoice-item", item)
        self.assertEqual(row["quantity"], 2.5)
        self.assertEqual(row["total_cents"], 30000)

    def test_rounding_collapses_sub_cent_to_whole_cent(self):
        """1.5 × $33.33 = 4999.5 → round() → 5000 cents (no sub-cent stored)."""
        org = store.insert("org", {"name": "C3D Studio"})
        inv = store.insert("invoice", {"org_id": org})
        total = round(1.5 * 3333)   # 4999.5 -> 5000 (banker's rounding to even? -> 5000)
        item = store.insert("invoice-item",
                           {"invoice_id": inv, "quantity": 1.5,
                            "unit_price_cents": 3333, "total_cents": total})
        self.assertEqual(store.get("invoice-item", item)["total_cents"], 5000)

    def test_strict_rejects_text_in_money_column(self):
        """STRICT refuses a non-integer in a *_cents column at the DB boundary."""
        org = store.insert("org", {"name": "C3D Studio"})
        c = self.conn()
        with self.assertRaises(sqlite3.IntegrityError):
            c.execute("INSERT INTO transactions (org_id, amount_cents) "
                      "VALUES (?, 'oops')", (org,))
            c.commit()
        c.close()

    def test_multi_currency_must_group_by_currency(self):
        """Cents carry magnitude, not denomination — totals are per-currency."""
        org = store.insert("org", {"name": "C3D Studio"})
        store.insert("transaction", {"org_id": org, "amount_cents": 45000,
                                     "currency": "USD"})
        store.insert("transaction", {"org_id": org, "amount_cents": 30000,
                                     "currency": "USD"})
        store.insert("transaction", {"org_id": org, "amount_cents": 10000,
                                     "currency": "EUR"})
        c = self.conn()
        by_ccy = dict(c.execute(
            "SELECT currency, SUM(amount_cents) FROM transactions "
            "GROUP BY currency").fetchall())
        c.close()
        self.assertEqual(by_ccy, {"USD": 75000, "EUR": 10000})


# ═══════════════════════════════════════════════════════════════════════
# 5. CLI surface
# ═══════════════════════════════════════════════════════════════════════
class TestCliSurface(OrgDBTestBase):
    """The `agictl organization` command group — the agent/operator contract."""

    def test_group_exposes_all_15_entities(self):
        """Every table is reachable as an `organization <entity>` subcommand."""
        out = self.cli("organization", "--help").output
        for entity in store.entity_names():
            self.assertIn(entity, out)

    def test_add_get_round_trip(self):
        """`add` returns a new id; `get` reads the row back."""
        add = self.last_json(self.cli("organization", "org", "add",
                                      "--name", "C3D Studio", "--slug", "c3d"))
        self.assertTrue(add["success"])
        got = self.last_json(self.cli("organization", "org", "get", str(add["id"])))
        self.assertEqual(got["row"]["name"], "C3D Studio")

    def test_money_passes_through_cli_as_cents(self):
        """--total-cents 45000 stores 45000 and reads back as 45000."""
        self.cli("organization", "org", "add", "--name", "C3D Studio")
        add = self.last_json(self.cli("organization", "invoice", "add",
                                      "--org-id", "1", "--total-cents", "45000",
                                      "--currency", "USD"))
        got = self.last_json(self.cli("organization", "invoice", "get",
                                      str(add["id"])))
        self.assertEqual(got["row"]["total_cents"], 45000)

    def test_upsert_is_idempotent_on_external_id(self):
        """Re-upserting the same external_id updates in place — no duplicate."""
        self.cli("organization", "org", "add", "--name", "C3D Studio")
        first = self.last_json(self.cli(
            "organization", "invoice", "upsert", "--org-id", "1",
            "--external-id", "WAVE-INV-1", "--total-cents", "45000"))
        self.assertEqual(first["action"], "created")
        second = self.last_json(self.cli(
            "organization", "invoice", "upsert", "--org-id", "1",
            "--external-id", "WAVE-INV-1", "--total-cents", "50000"))
        self.assertEqual(second["action"], "updated")
        self.assertEqual(second["id"], first["id"])
        listed = self.last_json(self.cli(
            "organization", "invoice", "list", "--external-id", "WAVE-INV-1"))
        self.assertEqual(listed["count"], 1)
        self.assertEqual(listed["rows"][0]["total_cents"], 50000)

    def test_update_changes_fields(self):
        """`update <id>` mutates a row and reports success."""
        self.cli("organization", "org", "add", "--name", "C3D Studio")
        upd = self.last_json(self.cli("organization", "org", "update", "1",
                                      "--notes", "primary studio"))
        self.assertTrue(upd["success"])
        self.assertEqual(self.last_json(
            self.cli("organization", "org", "get", "1"))["row"]["notes"],
            "primary studio")

    def test_delete_removes_row(self):
        """`delete <id>` removes a row; a second get reports not found."""
        self.cli("organization", "org", "add", "--name", "Throwaway")
        out = self.last_json(self.cli("organization", "org", "delete", "1"))
        self.assertTrue(out["success"])
        self.assertEqual(out["action"], "deleted")
        gone = self.last_json(self.cli("organization", "org", "get", "1"))
        self.assertFalse(gone["success"])
        self.assertEqual(gone["code"], "not_found")

    def test_delete_missing_row_is_structured(self):
        """Deleting a non-existent id yields success:false code not_found."""
        j = self.last_json(self.cli("organization", "org", "delete", "404"))
        self.assertFalse(j["success"])
        self.assertEqual(j["code"], "not_found")

    def test_boolean_column_via_cli_is_stored_as_int(self):
        """A bool column (is_active) is still a 0/1 CLI option after the registry
        split booleans into their own category."""
        add = self.last_json(self.cli("organization", "org", "add",
                                      "--name", "Inactive", "--is-active", "0"))
        self.assertTrue(add["success"])
        got = self.last_json(self.cli("organization", "org", "get", str(add["id"])))
        self.assertEqual(got["row"]["is_active"], 0)

    def test_delete_blocked_by_foreign_key(self):
        """An org still referenced by an invoice cannot be deleted (FK on)."""
        self.cli("organization", "org", "add", "--name", "C3D Studio")
        self.cli("organization", "invoice", "add", "--org-id", "1",
                 "--total-cents", "1000")
        j = self.last_json(self.cli("organization", "org", "delete", "1"))
        self.assertFalse(j["success"])
        self.assertEqual(j["code"], "constraint")

    def test_upsert_absent_on_link_tables(self):
        """Bridging entities have no external_id, so they expose no `upsert`."""
        result = self.cli("organization", "org-email", "upsert")
        self.assertNotEqual(result.exit_code, 0)

    def test_bad_foreign_key_returns_structured_error(self):
        """A bad FK surfaces as success:false with code 'constraint', not a crash."""
        j = self.last_json(self.cli("organization", "invoice", "add",
                                    "--org-id", "999", "--total-cents", "100"))
        self.assertFalse(j["success"])
        self.assertEqual(j["code"], "constraint")

    def test_click_rejects_non_integer_money(self):
        """--total-cents oops is rejected by the type system (nonzero exit)."""
        self.cli("organization", "org", "add", "--name", "C3D Studio")
        result = self.cli("organization", "invoice", "add",
                          "--org-id", "1", "--total-cents", "oops")
        self.assertNotEqual(result.exit_code, 0)

    def test_missing_required_field_is_rejected(self):
        """org requires a name; omitting it yields a structured error."""
        j = self.last_json(self.cli("organization", "org", "add",
                                    "--slug", "no-name"))
        self.assertFalse(j["success"])
        self.assertEqual(j["code"], "missing_required")

    def test_invoice_number_auto_generated_and_sequential(self):
        """A locally-created invoice with no number gets INV-00000001, then …002."""
        org = store.insert("org", {"name": "C3D Studio"})
        a = store.insert("invoice", {"org_id": org})
        b = store.insert("invoice", {"org_id": org})
        self.assertEqual(store.get("invoice", a)["invoice_number"], "INV-00000001")
        self.assertEqual(store.get("invoice", b)["invoice_number"], "INV-00000002")

    def test_estimate_number_auto_generated(self):
        """Estimates get their own EST- sequence."""
        org = store.insert("org", {"name": "C3D Studio"})
        e = store.insert("estimate", {"org_id": org})
        self.assertEqual(store.get("estimate", e)["estimate_number"], "EST-00000001")

    def test_supplied_document_number_is_preserved(self):
        """A caller-supplied number (e.g. a Wave invoice) is never overwritten and
        does not perturb the local sequence."""
        org = store.insert("org", {"name": "C3D Studio"})
        store.insert("invoice", {"org_id": org})                     # INV-00000001
        wave = store.insert("invoice", {"org_id": org, "invoice_number": "WAVE-7"})
        nxt = store.insert("invoice", {"org_id": org})
        self.assertEqual(store.get("invoice", wave)["invoice_number"], "WAVE-7")
        self.assertEqual(store.get("invoice", nxt)["invoice_number"], "INV-00000002")

    def test_duplicate_external_id_rejected(self):
        """Inserting two orgs with the same external_id raises a distinct error."""
        store.insert("org", {"name": "First", "external_id": "wave-001"})
        with self.assertRaises(store.OrganizationStoreError) as ctx:
            store.insert("org", {"name": "Second", "external_id": "wave-001"})
        self.assertEqual(ctx.exception.code, "duplicate_external_id")

    def test_null_external_id_allows_duplicates(self):
        """Multiple rows with NULL external_id are fine — only non-NULL is unique."""
        a = store.insert("org", {"name": "Local A"})
        b = store.insert("org", {"name": "Local B"})
        self.assertIsNotNone(a)
        self.assertIsNotNone(b)
        # Both should have None for external_id.
        self.assertIsNone(store.get("org", a)["external_id"])
        self.assertIsNone(store.get("org", b)["external_id"])

    def test_upsert_does_not_create_duplicate(self):
        """Upsert on an existing external_id updates the row, never duplicates."""
        store.insert("org", {"name": "Original", "external_id": "wave-002"})
        row_id, created = store.upsert("org", {"name": "Updated",
                                                "external_id": "wave-002"})
        self.assertFalse(created)
        self.assertEqual(store.get("org", row_id)["name"], "Updated")
        # Only one row with that external_id.
        rows = store.list_rows("org", where={"external_id": "wave-002"})
        self.assertEqual(len(rows), 1)


# ═══════════════════════════════════════════════════════════════════════
# 6. Full business scenario
# ═══════════════════════════════════════════════════════════════════════
class TestBusinessScenario(OrgDBTestBase):
    """A realistic permutation: C3D Studio's books, end to end across tables.

    Mirrors the plan's delta-report businesses (C3D Studio, Duende Lunar) so the
    scenario doubles as a worked example for the eventual user manual.
    """

    def test_full_lifecycle(self):
        # 1. Two of our own businesses.
        c3d = store.insert("org", {"name": "C3D Studio", "slug": "c3d",
                                   "type": "business", "external_id": "WAVE-BIZ-c3d"})
        duende = store.insert("org", {"name": "Duende Lunar LLC", "slug": "duende",
                                      "type": "business",
                                      "external_id": "WAVE-BIZ-duende"})

        # 2. A customer and a vendor — each an organization in its own right. The
        #    customer/vendor ROLE is not stored; it emerges from the documents
        #    below (C3D invoices Acme → Acme is a customer; Cloud Host invoices
        #    C3D → Cloud Host is a vendor).
        acme = store.insert("org", {"name": "Acme Corp", "type": "business"})
        cloud = store.insert("org", {"name": "Cloud Host Inc", "type": "business"})

        # 3. Contact info: a customer org is just an organization, so its email +
        #    address live on the org itself (org_emails / org_addresses).
        email = store.insert("email", {"email": "ap@acme.example",
                                       "label": "accounts payable",
                                       "is_primary": 1})
        addr = store.insert("address", {"line_1": "1 Market St", "city": "Austin",
                                        "state": "TX", "postal_code": "78701",
                                        "country": "US", "is_primary": 1})
        store.insert("org-email", {"org_id": acme, "email_id": email})
        store.insert("org-address", {"org_id": acme, "address_id": addr})

        # 4. Staff member (soft connection_uid to tasks.db connections).
        store.insert("org-staff", {"org_id": c3d, "connection_uid": "vv-stephen"})

        # 5. Products with prices in cents.
        hour = store.insert("product", {"org_id": c3d, "name": "Design Hour",
                                        "sku": "DH", "unit_price_cents": 12000,
                                        "currency": "USD"})
        logo = store.insert("product", {"org_id": c3d, "name": "Logo Package",
                                        "sku": "LOGO", "unit_price_cents": 50000,
                                        "currency": "USD"})

        # 6. An invoice to Acme with two line items + 10% tax.
        line1 = round(2.5 * 12000)        # 30000
        line2 = round(1.0 * 50000)        # 50000
        subtotal = line1 + line2          # 80000
        tax = round(subtotal * 0.10)      # 8000
        total = subtotal + tax            # 88000
        inv = store.insert("invoice",
                          {"org_id": c3d, "customer_org_id": acme,
                           "invoice_number": "INV-0042", "status": "sent",
                           "subtotal_cents": subtotal, "tax_total_cents": tax,
                           "total_cents": total, "currency": "USD",
                           "issue_date": "2026-06-21", "due_date": "2026-07-21",
                           "external_id": "WAVE-INV-0042"})
        store.insert("invoice-item",
                    {"invoice_id": inv, "product_id": hour,
                     "description": "Design work", "quantity": 2.5,
                     "unit_price_cents": 12000, "total_cents": line1})
        store.insert("invoice-item",
                    {"invoice_id": inv, "product_id": logo,
                     "description": "Brand logo", "quantity": 1.0,
                     "unit_price_cents": 50000, "total_cents": line2})

        # 7. An estimate that converts into a (second) invoice.
        est = store.insert("estimate",
                          {"org_id": c3d, "customer_org_id": acme,
                           "estimate_number": "EST-0009", "status": "accepted",
                           "subtotal_cents": 20000, "tax_total_cents": 2000,
                           "total_cents": 22000, "currency": "USD",
                           "issue_date": "2026-06-10", "expiry_date": "2026-07-10"})
        conv_inv = store.insert("invoice",
                               {"org_id": c3d, "customer_org_id": acme,
                                "invoice_number": "INV-0043", "status": "draft",
                                "total_cents": 22000, "currency": "USD"})
        store.update("estimate", est, {"converted_to_invoice_id": conv_inv})

        # 7b. Cloud Host invoices C3D for hosting — this single document is what
        #     makes Cloud Host a vendor of C3D (and C3D a customer of Cloud Host).
        store.insert("invoice", {"org_id": cloud, "customer_org_id": c3d,
                                 "invoice_number": "CH-2026-06", "status": "paid",
                                 "total_cents": 9000, "currency": "USD"})

        # 8. Transactions: one historical (2021 rule) + one recent.
        store.insert("transaction",
                    {"org_id": c3d, "account_name": "Checking",
                     "transaction_date": "2021-03-15", "description": "Hosting",
                     "amount_cents": -1500, "currency": "USD", "category": "Software"})
        store.insert("transaction",
                    {"org_id": duende, "account_name": "Checking",
                     "transaction_date": "2026-06-20", "description": "Royalty",
                     "amount_cents": 25000, "currency": "USD", "category": "Income"})

        # ── Assertions: the books reconcile and the graph is intact ──
        # Invoice line items sum to the stored subtotal.
        c = self.conn()
        line_sum = c.execute(
            "SELECT SUM(total_cents) FROM invoice_line_items WHERE invoice_id=?",
            (inv,)).fetchone()[0]
        self.assertEqual(line_sum, subtotal)
        self.assertEqual(store.get("invoice", inv)["total_cents"], 88000)

        # Estimate is linked to its converted invoice.
        self.assertEqual(store.get("estimate", est)["converted_to_invoice_id"],
                         conv_inv)

        # The customer org's email is reachable through org_emails.
        cust_emails = c.execute(
            "SELECT e.email FROM org_emails oe "
            "JOIN email_addresses e ON e.id = oe.email_id "
            "WHERE oe.org_id=?", (acme,)).fetchall()
        self.assertEqual([r[0] for r in cust_emails], ["ap@acme.example"])

        # Per-currency income/expense is well defined; integrity is clean.
        self.assertEqual(c.execute("PRAGMA foreign_key_check").fetchall(), [])
        c.close()

        # Customer/vendor roles are DERIVED from the documents (no stored bridge):
        # C3D invoiced Acme (Acme is a customer); Cloud Host invoiced C3D (a vendor).
        reader = OrganizationReader(self.db)
        self.assertIn("Acme Corp", [x["name"] for x in reader.customers_of(c3d)])
        self.assertIn("Cloud Host Inc", [x["name"] for x in reader.vendors_of(c3d)])

        # Two of our businesses, one customer, one vendor.
        self.assertEqual(len(store.list_rows("org")), 4)


# ═══════════════════════════════════════════════════════════════════════
# 7. Exchange (integration) lifecycle
# ═══════════════════════════════════════════════════════════════════════
class TestExchangeLifecycle(OrgDBTestBase):
    """The exchange tracker's pull / local-create / push-success / push-fail
    states — the contract the weekly Wave sync drives."""

    def test_pull_landing_is_not_a_push_candidate(self):
        """A record pulled from Wave lands origin=integration, replicate=0 —
        it must never be selected for push-back."""
        org = store.insert("org", {"name": "C3D Studio"})
        inv = store.insert("invoice", {"org_id": org, "total_cents": 100,
                                       "external_id": "WAVE-INV-1"})
        ex = store.insert("exchange",
                         {"name": "Wave", "source_table": "invoices",
                          "source_id": inv, "external_id": "WAVE-INV-1",
                          "origin": "integration", "status": "sync-done",
                          "replicate": 0})
        row = store.get("exchange", ex)
        self.assertEqual(row["replicate"], 0)
        self.assertEqual(row["origin"], "integration")

    def test_local_create_is_pending_push(self):
        """A locally-created record is flagged replicate=1, external_id NULL,
        status=new — the exact push-candidate signature."""
        org = store.insert("org", {"name": "C3D Studio"})
        inv = store.insert("invoice", {"org_id": org, "total_cents": 45000})
        ex = store.insert("exchange",
                         {"name": "Wave", "source_table": "invoices",
                          "source_id": inv, "origin": "user",
                          "status": "new", "replicate": 1})
        candidates = self._push_candidates()
        self.assertIn(ex, [c["id"] for c in candidates])

    def test_push_success_clears_replicate_and_sets_external_id(self):
        """On a successful push: external_id populated, status sync-done,
        replicate cleared to 0 (no longer a candidate)."""
        org = store.insert("org", {"name": "C3D Studio"})
        inv = store.insert("invoice", {"org_id": org, "total_cents": 45000})
        ex = store.insert("exchange",
                         {"name": "Wave", "source_table": "invoices",
                          "source_id": inv, "origin": "user",
                          "status": "new", "replicate": 1})
        # Simulate the sync writing back Wave's id.
        store.update("invoice", inv, {"external_id": "WAVE-NEW-1"})
        store.update("exchange", ex, {"external_id": "WAVE-NEW-1",
                                      "status": "sync-done", "replicate": 0})
        self.assertEqual(store.get("invoice", inv)["external_id"], "WAVE-NEW-1")
        self.assertNotIn(ex, [c["id"] for c in self._push_candidates()])

    def test_push_failure_records_error_and_clears_replicate(self):
        """On failure: status sync-failed, error_message set, replicate cleared
        (so a broken record does not wedge the queue)."""
        org = store.insert("org", {"name": "C3D Studio"})
        inv = store.insert("invoice", {"org_id": org, "total_cents": 45000})
        ex = store.insert("exchange",
                         {"name": "Wave", "source_table": "invoices",
                          "source_id": inv, "origin": "user",
                          "status": "new", "replicate": 1})
        store.update("exchange", ex, {"status": "sync-failed", "replicate": 0,
                                      "error_message": "Wave 422: missing customer"})
        row = store.get("exchange", ex)
        self.assertEqual(row["status"], "sync-failed")
        self.assertIn("422", row["error_message"])
        self.assertNotIn(ex, [c["id"] for c in self._push_candidates()])

    def test_exchange_status_check_constraint(self):
        """status is constrained to the three known states."""
        org = store.insert("org", {"name": "C3D Studio"})
        c = self.conn()
        with self.assertRaises(sqlite3.IntegrityError):
            c.execute("INSERT INTO exchange (name, source_table, status) "
                      "VALUES ('Wave', 'invoices', 'bogus')")
            c.commit()
        c.close()

    def test_exchange_source_target_org_roundtrip(self):
        """source_org_id and target_org_id round-trip through insert/get."""
        org_a = store.insert("org", {"name": "Alpha Co"})
        org_b = store.insert("org", {"name": "Beta Co"})
        ex = store.insert("exchange", {
            "name": "Wave", "source_table": "invoices",
            "source_org_id": org_a, "target_org_id": org_b,
        })
        row = store.get("exchange", ex)
        self.assertEqual(row["source_org_id"], org_a)
        self.assertEqual(row["target_org_id"], org_b)

    def test_exchange_org_fk_constraint(self):
        """source_org_id must reference an existing organization."""
        with self.assertRaises(sqlite3.IntegrityError):
            c = self.conn()
            c.execute("PRAGMA foreign_keys=ON")
            c.execute("INSERT INTO exchange (name, source_table, source_org_id) "
                      "VALUES ('Wave', 'invoices', 99999)")
            c.commit()
            c.close()

    def test_exchange_same_org_check_constraint(self):
        """source_org_id and target_org_id cannot be the same value."""
        org = store.insert("org", {"name": "Same Co"})
        c = self.conn()
        with self.assertRaises(sqlite3.IntegrityError):
            c.execute("INSERT INTO exchange (name, source_table, source_org_id, target_org_id) "
                      "VALUES ('Wave', 'invoices', ?, ?)", (org, org))
            c.commit()
        c.close()

    def test_exchange_nullable_org_columns(self):
        """Both org columns default to NULL — existing pattern preserved."""
        ex = store.insert("exchange", {
            "name": "Wave", "source_table": "invoices",
        })
        row = store.get("exchange", ex)
        self.assertIsNone(row["source_org_id"])
        self.assertIsNone(row["target_org_id"])

    def _push_candidates(self):
        """The canonical push-candidate query from the plan."""
        c = self.conn()
        rows = [dict(r) for r in c.execute(
            "SELECT * FROM exchange WHERE name='Wave' AND replicate=1 "
            "AND external_id IS NULL AND status='new'")]
        c.close()
        return rows


# ═══════════════════════════════════════════════════════════════════════
# 8. Demo data feature (activation showcase)
# ═══════════════════════════════════════════════════════════════════════
class TestDemoData(OrgDBTestBase):
    """The activation demo dataset — seed / status / clear, with safety guards."""

    def test_seed_populates_full_dataset(self):
        """`demo seed` fills a clean DB with a many-table sample set."""
        result = demo.seed()
        self.assertGreater(result["total_rows"], 20)
        # Spot-check representative tables are populated.
        c = self.conn()
        for table, atleast in (("organizations", 5), ("invoices", 3),
                               ("invoice_line_items", 2), ("exchange", 2)):
            n = c.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            self.assertGreaterEqual(n, atleast, table)
        c.close()

    def test_seeded_data_is_referentially_clean(self):
        """The demo graph has zero FK violations — a valid worked example."""
        demo.seed()
        c = self.conn()
        self.assertEqual(c.execute("PRAGMA foreign_key_check").fetchall(), [])
        c.close()

    def test_status_reflects_presence(self):
        """`demo status` reports demo presence accurately before and after seed."""
        self.assertFalse(demo.status()["demo_present"])
        demo.seed()
        st = demo.status()
        self.assertTrue(st["demo_present"])
        self.assertGreater(st["demo_orgs"], 0)
        self.assertEqual(st["real_orgs"], 0)

    def test_seed_is_idempotent_guarded(self):
        """Re-seeding without clearing is refused (no duplicate demo data)."""
        demo.seed()
        with self.assertRaises(demo.DemoError):
            demo.seed()

    def test_clear_removes_all_demo_data(self):
        """`demo clear` empties a pure-demo DB completely."""
        demo.seed()
        demo.clear()
        c = self.conn()
        # picklists are shipped system vocabulary (seeded by init, not demo), so
        # demo clear intentionally leaves them — exclude from the empty check.
        for table in ALL_TABLES - {"picklists"}:
            n = c.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            self.assertEqual(n, 0, f"{table} not cleared")
        c.close()

    def test_seed_refuses_when_real_data_present(self):
        """Demo must never touch a DB that holds real (non-demo) records."""
        store.insert("org", {"name": "Real Customer Co"})  # no DEMO- external_id
        with self.assertRaises(demo.DemoError):
            demo.seed()

    def test_clear_refuses_when_real_data_present(self):
        """`clear` protects real data — refuses unless the DB is pure demo."""
        demo.seed()
        store.insert("org", {"name": "Real Customer Co"})  # a real org appears
        with self.assertRaises(demo.DemoError):
            demo.clear()
        # And the demo data is untouched by the refused clear.
        self.assertTrue(demo.status()["demo_present"])

    def test_seed_clear_round_trip_via_cli(self):
        """The full seed → status → clear cycle works through the CLI surface."""
        seeded = self.last_json(self.cli("organization", "demo", "seed"))
        self.assertTrue(seeded["success"])
        st = self.last_json(self.cli("organization", "demo", "status"))
        self.assertTrue(st["demo_present"])
        cleared = self.last_json(self.cli("organization", "demo", "clear"))
        self.assertTrue(cleared["success"])
        self.assertFalse(
            self.last_json(self.cli("organization", "demo", "status"))["demo_present"])


# ═══════════════════════════════════════════════════════════════════════
# 9. agitop reader (read-only UI data layer, TS-11)
# ═══════════════════════════════════════════════════════════════════════
class TestOrganizationReader(OrgDBTestBase):
    """The agitop OrganizationReader — read-only, resilient, money-formatting.

    Per TS-11 the UI reads through this class only (no raw SQL in panels). These
    tests pin the data the Organization panel and its modals will render.
    """

    def reader(self):
        return OrganizationReader(self.db)

    def test_money_formatting(self):
        """Cents render as currency strings; None renders as an em dash."""
        self.assertEqual(format_money(45000, "USD"), "$450.00")
        self.assertEqual(format_money(10000, "EUR"), "€100.00")
        self.assertEqual(format_money(None), "—")

    def test_colour_buckets(self):
        """Semantic colour buckets follow Digital Silk intent."""
        self.assertEqual(sync_colour("sync-failed", 0), "red")
        self.assertEqual(sync_colour("new", 1), "amber")     # pending push
        self.assertEqual(sync_colour("sync-done", 0), "green")
        self.assertEqual(invoice_colour("paid"), "gray")     # closed
        self.assertEqual(invoice_colour("overdue"), "red")

    def test_available_reflects_file(self):
        """`available()` is True for a built DB, False for a missing path."""
        self.assertTrue(self.reader().available())
        self.assertFalse(OrganizationReader("/no/such/organization.db").available())

    def test_missing_db_is_resilient(self):
        """A missing DB yields empty results, never an exception (dashboard-safe)."""
        r = OrganizationReader("/no/such/organization.db")
        self.assertEqual(r.summary(), {"available": False})
        self.assertEqual(r.list_invoices(), [])

    def test_summary_counts(self):
        """`summary()` reports per-entity counts plus sync health."""
        demo.seed()
        s = self.reader().summary()
        self.assertTrue(s["available"])
        self.assertGreaterEqual(s["organizations"], 5)
        self.assertGreaterEqual(s["invoices"], 3)
        self.assertGreaterEqual(s["pending_push"], 1)   # demo seeds a pending row
        self.assertEqual(s["sync_failed"], 0)

    def test_list_invoices_resolves_names_and_money(self):
        """Invoice rows resolve issuing-org + customer names and format money."""
        demo.seed()
        rows = self.reader().list_invoices()
        self.assertTrue(rows)
        inv = next(r for r in rows if r["invoice_number"] == "INV-0042")
        self.assertEqual(inv["org_name"], "C3D Studio")
        self.assertEqual(inv["customer_name"], "Acme Corp")
        self.assertEqual(inv["total_display"], format_money(inv["total_cents"], inv["currency"]))
        self.assertEqual(inv["colour"], "green")   # status 'sent'

    def test_organization_detail_assembles_related(self):
        """Org detail returns customers, vendors, invoices, estimates, contacts."""
        demo.seed()
        c3d = next(o for o in self.reader().list_organizations()
                   if o["name"] == "C3D Studio")
        detail = self.reader().organization_detail(c3d["id"])
        self.assertEqual(detail["org"]["name"], "C3D Studio")
        self.assertGreaterEqual(len(detail["customers"]), 2)
        self.assertGreaterEqual(len(detail["vendors"]), 1)
        self.assertGreaterEqual(len(detail["invoices"]), 2)
        self.assertTrue(any(c["name"] == "Acme Corp" for c in detail["customers"]))

    def test_exchange_views_carry_colour(self):
        """Exchange list rows expose a colour bucket for the panel."""
        demo.seed()
        rows = self.reader().list_exchange()
        self.assertTrue(rows)
        self.assertIn(rows[0]["colour"], {"green", "amber", "red", "gray"})

    def test_exchange_reader_resolves_org_names(self):
        """list_exchange() carries source_org_name and target_org_name."""
        org_a = store.insert("org", {"name": "Source Corp"})
        org_b = store.insert("org", {"name": "Target Corp"})
        store.insert("exchange", {
            "name": "Wave", "source_table": "invoices",
            "source_org_id": org_a, "target_org_id": org_b,
        })
        rows = self.reader().list_exchange()
        self.assertTrue(rows)
        row = rows[0]
        self.assertEqual(row["source_org_name"], "Source Corp")
        self.assertEqual(row["target_org_name"], "Target Corp")


# ═══════════════════════════════════════════════════════════════════════
# 10. Picklists (universal managed vocabulary)
# ═══════════════════════════════════════════════════════════════════════
class TestPicklists(OrgDBTestBase):
    """The universal ``picklists`` lookup — seeded defaults, the (table,field)
    resolver, the shared currency list, and the Replace & Delete reassign."""

    def reader(self):
        return OrganizationReader(self.db)

    def test_seeded_default_vocabularies(self):
        """Init ships starter vocabularies for the managed fields."""
        r = self.reader()
        self.assertIn(("Service", "service"), r.picklist_options("products", "type"))
        self.assertIn(("Business", "business"),
                      r.picklist_options("organizations", "type"))
        self.assertIn(("Paid", "paid"), r.picklist_options("invoices", "status"))

    def test_currency_list_is_shared_across_tables(self):
        """Currency is a global list (table_name='') resolved for every table."""
        r = self.reader()
        prod = r.picklist_options("products", "currency")
        txn = r.picklist_options("transactions", "currency")
        self.assertEqual(prod, txn)
        self.assertIn(("USD", "USD"), prod)

    def test_seed_is_idempotent(self):
        """Re-running init does not duplicate the seeded vocabulary."""
        before = len(self.reader().list_picklists())
        proc = subprocess.run(["bash", _INIT_SCRIPT, self.db],
                              capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(len(self.reader().list_picklists()), before)

    def test_reassign_repoints_rows(self):
        """Replace & Delete: reassign repoints every row from old→new value."""
        a = store.insert("org", {"name": "A", "type": "business"})
        b = store.insert("org", {"name": "B", "type": "business"})
        changed = store.reassign("organizations", "type", "business", "individual")
        self.assertEqual(changed, 2)
        self.assertEqual(store.get("org", a)["type"], "individual")
        self.assertEqual(store.get("org", b)["type"], "individual")

    def test_count_value_reports_usage(self):
        """count_value reports how many rows hold a given option value."""
        store.insert("org", {"name": "A", "type": "business"})
        store.insert("org", {"name": "B", "type": "business"})
        store.insert("org", {"name": "C", "type": "individual"})
        self.assertEqual(store.count_value("organizations", "type", "business"), 2)

    def test_reassign_rejects_unknown_field(self):
        """A non-existent target column is refused (no raw identifier reaches SQL)."""
        with self.assertRaises(store.OrganizationStoreError) as ctx:
            store.reassign("organizations", "bogus_col", "x", "y")
        self.assertEqual(ctx.exception.code, "unknown_field")

    def test_picklist_crud_via_cli(self):
        """A picklist option is fully manageable through the registry-driven CLI."""
        add = self.last_json(self.cli(
            "organization", "picklist", "add", "--name", "Wholesale",
            "--value", "wholesale", "--table-name", "products",
            "--field-name", "type", "--position", "9"))
        self.assertTrue(add["success"])
        self.assertIn(("Wholesale", "wholesale"),
                      self.reader().picklist_options("products", "type"))


# ═══════════════════════════════════════════════════════════════════════
# 11. Credentials & email extension (D35)
# ═══════════════════════════════════════════════════════════════════════
class TestCredentials(OrgDBTestBase):
    """The ``credentials`` table and email_addresses extension for agentic access."""

    def reader(self):
        return OrganizationReader(self.db)

    def test_credential_crud_roundtrip(self):
        """Insert + get for a credential with JSON config."""
        cid = store.insert("credential", {
            "auth_type": "imap",
            "configuration": '{"host":"imap.gmail.com","port":993}',
            "notes": "Gmail",
        })
        row = store.get("credential", cid)
        self.assertEqual(row["auth_type"], "imap")
        self.assertIn("imap.gmail.com", row["configuration"])
        self.assertEqual(row["notes"], "Gmail")

    def test_credential_json_validation_rejects_invalid(self):
        """Invalid JSON in configuration raises a type_error."""
        with self.assertRaises(store.OrganizationStoreError) as ctx:
            store.insert("credential", {
                "auth_type": "api",
                "configuration": "not-json{",
            })
        self.assertEqual(ctx.exception.code, "type_error")

    def test_credential_json_validation_accepts_dict(self):
        """A valid JSON dict is accepted."""
        cid = store.insert("credential", {
            "auth_type": "mcp",
            "configuration": '{"server":"stdio"}',
        })
        self.assertIsNotNone(store.get("credential", cid))

    def test_email_usage_notes_and_credential_id(self):
        """email_addresses supports usage_notes and credential_id."""
        cid = store.insert("credential", {
            "auth_type": "imap",
            "configuration": '{}',
        })
        eid = store.insert("email", {
            "email": "test@example.com",
            "usage_notes": "Support inbox",
            "credential_id": cid,
        })
        row = store.get("email", eid)
        self.assertEqual(row["usage_notes"], "Support inbox")
        self.assertEqual(row["credential_id"], cid)

    def test_email_credential_fk_enforced(self):
        """credential_id must reference an existing credential."""
        c = self.conn()
        c.execute("PRAGMA foreign_keys=ON")
        with self.assertRaises(sqlite3.IntegrityError):
            c.execute("INSERT INTO email_addresses (email, credential_id) "
                      "VALUES ('bad@example.com', 99999)")
            c.commit()
        c.close()

    def test_email_credential_id_nullable(self):
        """credential_id defaults to NULL."""
        eid = store.insert("email", {"email": "plain@example.com"})
        row = store.get("email", eid)
        self.assertIsNone(row["credential_id"])

    def test_reader_list_credentials(self):
        """list_credentials() returns credentials with auth_type."""
        store.insert("credential", {
            "auth_type": "api",
            "configuration": '{"key":"abc"}',
        })
        rows = self.reader().list_credentials()
        self.assertTrue(rows)
        self.assertEqual(rows[0]["auth_type"], "api")

    def test_reader_list_emails_with_credential_and_org(self):
        """list_emails() resolves credential type and org name."""
        org = store.insert("org", {"name": "Acme"})
        cid = store.insert("credential", {
            "auth_type": "imap",
            "configuration": '{}',
        })
        eid = store.insert("email", {
            "email": "acme@example.com",
            "credential_id": cid,
        })
        store.insert("org-email", {"org_id": org, "email_id": eid})
        rows = self.reader().list_emails()
        self.assertTrue(rows)
        row = rows[0]
        self.assertEqual(row["email"], "acme@example.com")
        self.assertEqual(row["credential_type"], "imap")
        self.assertEqual(row["org_name"], "Acme")

    def test_reader_list_staff(self):
        """list_staff() returns staff rows with org name."""
        org = store.insert("org", {"name": "Beta Inc"})
        store.insert("org-staff", {"org_id": org, "connection_uid": "uid-123"})
        rows = self.reader().list_staff()
        self.assertTrue(rows)
        self.assertEqual(rows[0]["org_name"], "Beta Inc")
        self.assertEqual(rows[0]["connection_uid"], "uid-123")

    def test_reader_list_addresses(self):
        """list_addresses() returns addresses with org context."""
        org = store.insert("org", {"name": "Gamma LLC"})
        aid = store.insert("address", {"line_1": "123 Main St", "city": "Springfield"})
        store.insert("org-address", {"org_id": org, "address_id": aid})
        rows = self.reader().list_addresses()
        self.assertTrue(rows)
        self.assertEqual(rows[0]["org_name"], "Gamma LLC")
        self.assertEqual(rows[0]["line_1"], "123 Main St")

    def test_auth_type_picklist_seeded(self):
        """The auth_type picklist is seeded with IMAP, MCP, API."""
        r = self.reader()
        options = r.picklist_options("credentials", "auth_type")
        values = [v for _, v in options]
        self.assertIn("imap", values)
        self.assertIn("mcp", values)
        self.assertIn("api", values)


if __name__ == "__main__":
    unittest.main(verbosity=2)
