"""Demo data for the Organization domain — activation showcase (Deliverable D31).

Offers a one-command, fully-formed sample dataset so a new operator can explore
the Organization feature (orgs, customers, vendors, products, invoices with line
items, estimates, transactions, and the Wave exchange tracker) immediately after
activation — then wipe it cleanly before real use.

Safety model (protects real books):
  * Every demo top-level record is tagged with a ``DEMO-`` external_id.
  * ``seed`` refuses if any **non-demo** organization already exists.
  * ``clear`` refuses if any **non-demo** organization exists — so it can only
    ever wipe a pure-demo database, never real data.

All inserts go through :mod:`organization_store` (the same validated, FK-checked
path the CLI uses), so the demo set is also a live integration exercise.
"""

from __future__ import annotations

import os
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import db_connect                    # noqa: E402
import organization_store as store   # noqa: E402

DEMO_PREFIX = "DEMO-"

# Reverse foreign-key dependency order for a clean wipe (children first).
# Hardcoded against the known fixed schema; verified by the demo round-trip test.
_DELETE_ORDER = [
    "invoice_line_items", "estimate_line_items",
    "org_staff_addresses", "org_addresses", "org_emails",
    "estimates", "invoices", "transactions", "products",
    "org_staff",
    "exchange", "email_addresses", "physical_addresses", "organizations",
]


class DemoError(Exception):
    """Raised when seed/clear preconditions are not met."""


def _connect():
    return db_connect.connect(db_connect.organization_db_path())


def _non_demo_org_count(conn) -> int:
    return conn.execute(
        "SELECT COUNT(*) FROM organizations "
        "WHERE external_id IS NULL OR external_id NOT LIKE ?",
        (DEMO_PREFIX + "%",),
    ).fetchone()[0]


def _demo_org_count(conn) -> int:
    return conn.execute(
        "SELECT COUNT(*) FROM organizations WHERE external_id LIKE ?",
        (DEMO_PREFIX + "%",),
    ).fetchone()[0]


def status() -> dict:
    """Report whether demo data is present and how much real data exists."""
    conn = _connect()
    try:
        demo = _demo_org_count(conn)
        real = _non_demo_org_count(conn)
    finally:
        conn.close()
    return {"demo_present": demo > 0, "demo_orgs": demo, "real_orgs": real}


def seed() -> dict:
    """Populate the demo dataset. Refuses if real or existing demo data is found.

    Returns a summary of what was created.
    """
    conn = _connect()
    try:
        if _non_demo_org_count(conn) > 0:
            raise DemoError(
                "Refusing to seed: real (non-demo) organizations exist. "
                "Demo data is only for a clean activation database."
            )
        if _demo_org_count(conn) > 0:
            raise DemoError(
                "Demo data already present. Run `organization demo clear` first."
            )
    finally:
        conn.close()

    created: dict[str, int] = {}

    def add(entity, fields):
        created[entity] = created.get(entity, 0) + 1
        return store.insert(entity, fields)

    # ── Our two businesses ──
    c3d = add("org", {"name": "C3D Studio", "slug": "demo-c3d", "type": "business",
                      "notes": "Demo business — 3D & brand design",
                      "logo_path": "/var/lib/versa-agi/assets/logos/c3d-studio.png",
                      "external_id": DEMO_PREFIX + "BIZ-c3d"})
    duende = add("org", {"name": "Duende Lunar LLC", "slug": "demo-duende",
                         "type": "business", "notes": "Demo business — music label",
                         "logo_path": "/var/lib/versa-agi/assets/logos/duende-lunar.svg",
                         "external_id": DEMO_PREFIX + "BIZ-duende"})

    # ── Other organizations we trade with. There is no stored customer/vendor
    #    link — the relationship is IMPLIED by the invoices/estimates below
    #    (C3D invoices Acme & Globex → they are customers of C3D; Cloud Host
    #    invoices C3D → Cloud Host is a vendor of C3D). ──
    acme = add("org", {"name": "Acme Corp", "type": "business",
                       "external_id": DEMO_PREFIX + "ORG-acme"})
    globex = add("org", {"name": "Globex Inc", "type": "business",
                         "external_id": DEMO_PREFIX + "ORG-globex"})
    cloud = add("org", {"name": "Cloud Host Inc", "type": "business",
                        "external_id": DEMO_PREFIX + "ORG-cloud"})

    # ── Contact info: a customer org is just an organization, so its email and
    #    address live on the org itself (org_emails / org_addresses). ──
    email = add("email", {"email": "ap@acme.example", "label": "accounts payable",
                          "is_primary": 1})
    addr = add("address", {"line_1": "1 Market St", "city": "Austin", "state": "TX",
                           "postal_code": "78701", "country": "US", "is_primary": 1})
    add("org-email", {"org_id": acme, "email_id": email})
    add("org-address", {"org_id": acme, "address_id": addr})
    # A staff member of C3D (soft connection_uid → tasks.db connections).
    add("org-staff", {"org_id": c3d, "connection_uid": "demo-vv-owner"})

    # ── Products (prices in cents; type drawn from the seeded picklist) ──
    hour = add("product", {"org_id": c3d, "name": "Design Hour", "sku": "DH",
                           "type": "service",
                           "unit_price_cents": 12000, "currency": "USD",
                           "external_id": DEMO_PREFIX + "PROD-hour"})
    logo = add("product", {"org_id": c3d, "name": "Logo Package", "sku": "LOGO",
                           "type": "service",
                           "unit_price_cents": 50000, "currency": "USD",
                           "external_id": DEMO_PREFIX + "PROD-logo"})

    # ── Invoice INV-0042 to Acme: 2.5 hrs + 1 logo, +10% tax ──
    line1 = round(2.5 * 12000)   # 30000
    line2 = round(1.0 * 50000)   # 50000
    subtotal = line1 + line2     # 80000
    tax = round(subtotal * 0.10) # 8000
    inv = add("invoice", {"org_id": c3d, "customer_org_id": acme,
                          "invoice_number": "INV-0042", "status": "sent",
                          "subtotal_cents": subtotal, "tax_total_cents": tax,
                          "total_cents": subtotal + tax, "currency": "USD",
                          "issue_date": "2026-06-21", "due_date": "2026-07-21",
                          "external_id": DEMO_PREFIX + "INV-0042"})
    add("invoice-item", {"invoice_id": inv, "product_id": hour,
                         "description": "Design work", "quantity": 2.5,
                         "unit_price_cents": 12000, "total_cents": line1})
    add("invoice-item", {"invoice_id": inv, "product_id": logo,
                         "description": "Brand logo", "quantity": 1.0,
                         "unit_price_cents": 50000, "total_cents": line2})

    # ── A paid invoice to Globex (closed) ──
    add("invoice", {"org_id": c3d, "customer_org_id": globex,
                    "invoice_number": "INV-0041", "status": "paid",
                    "subtotal_cents": 60000, "tax_total_cents": 6000,
                    "total_cents": 66000, "currency": "USD",
                    "issue_date": "2026-05-15", "due_date": "2026-06-15",
                    "paid_date": "2026-06-02",
                    "external_id": DEMO_PREFIX + "INV-0041"})

    # ── Estimate that converts into a draft invoice ──
    est = add("estimate", {"org_id": c3d, "customer_org_id": acme,
                           "estimate_number": "EST-0009", "status": "accepted",
                           "subtotal_cents": 20000, "tax_total_cents": 2000,
                           "total_cents": 22000, "currency": "USD",
                           "issue_date": "2026-06-10", "expiry_date": "2026-07-10",
                           "external_id": DEMO_PREFIX + "EST-0009"})
    add("estimate-item", {"estimate_id": est, "product_id": hour,
                         "description": "Design work", "quantity": 1.0,
                         "unit_price_cents": 12000, "total_cents": 12000})
    add("estimate-item", {"estimate_id": est, "description": "Consulting",
                         "quantity": 1.0, "unit_price_cents": 8000,
                         "total_cents": 8000})
    conv = add("invoice", {"org_id": c3d, "customer_org_id": acme,
                           "invoice_number": "INV-0043", "status": "draft",
                           "total_cents": 22000, "currency": "USD"})
    store.update("estimate", est, {"converted_to_invoice_id": conv})

    # ── Cloud Host invoices C3D for hosting. This single invoice is what makes
    #    Cloud Host a vendor of C3D (and C3D a customer of Cloud Host) — purely
    #    dynamic, no vendor bridge row. ──
    add("invoice", {"org_id": cloud, "customer_org_id": c3d,
                    "invoice_number": "CH-2026-06", "status": "paid",
                    "subtotal_cents": 9000, "tax_total_cents": 0,
                    "total_cents": 9000, "currency": "USD",
                    "issue_date": "2026-06-01", "due_date": "2026-06-15",
                    "paid_date": "2026-06-03",
                    "external_id": DEMO_PREFIX + "INV-CH-2026-06"})

    # ── Transactions: historical (2021) + recent, income + expense.
    #    counterparty_org_id ties each to the other party for reconciliation:
    #    C3D's hosting expense → paid to Cloud Host (a vendor); Duende's royalty
    #    income → received from Acme (a customer). ──
    add("transaction", {"org_id": c3d, "counterparty_org_id": cloud,
                        "account_name": "Checking",
                        "transaction_date": "2021-03-15", "description": "Hosting",
                        "amount_cents": -1500, "currency": "USD",
                        "category": "software",
                        "external_id": DEMO_PREFIX + "TXN-2021-1"})
    add("transaction", {"org_id": duende, "counterparty_org_id": acme,
                        "account_name": "Checking",
                        "transaction_date": "2026-06-20", "description": "Royalty",
                        "amount_cents": 25000, "currency": "USD",
                        "category": "income",
                        "external_id": DEMO_PREFIX + "TXN-2026-1"})

    # ── Exchange rows: one pulled from Wave, one pending push ──
    add("exchange", {"name": "Wave", "source_table": "invoices", "source_id": inv,
                     "external_id": DEMO_PREFIX + "INV-0042", "origin": "integration",
                     "status": "sync-done", "replicate": 0})
    add("exchange", {"name": "Wave", "source_table": "invoices", "source_id": conv,
                     "origin": "user", "status": "new", "replicate": 1})

    return {"created": created, "total_rows": sum(created.values())}


def clear() -> dict:
    """Wipe all rows, but only when the database is pure demo (no real orgs).

    Returns a summary of rows removed.
    """
    conn = _connect()
    try:
        real = _non_demo_org_count(conn)
        if real > 0:
            raise DemoError(
                f"Refusing to clear: {real} real (non-demo) organization(s) "
                "present. Clear only operates on a pure-demo database."
            )
        removed = {}
        for table in _DELETE_ORDER:
            cur = conn.execute(f"DELETE FROM {table}")
            if cur.rowcount > 0:
                removed[table] = cur.rowcount
        conn.commit()
        return {"removed": removed, "total_rows": sum(removed.values())}
    finally:
        conn.close()
