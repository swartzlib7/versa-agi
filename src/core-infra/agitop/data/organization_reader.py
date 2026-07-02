"""Organization domain reader — read-only data access for agitop (Deliverable D25).

Per the TS-11 UI Specification, agitop **never executes raw SQL inside a panel**;
every panel reads through a dedicated Reader class. This is that reader for the
Organization domain. It is strictly read-only (opens ``organization.db`` via the
shared ``db_connect`` helper in ``readonly=True`` mode) and resilient — any error
yields an empty result rather than crashing the dashboard, matching the existing
``TasksReader`` / ``MessageReader`` behaviour.

Money is stored as integer cents; :func:`format_money` renders it for display.
The reader returns both the raw ``*_cents`` integer (for logic) and, where handy,
a ``*_display`` string (for the panel), so presentation code stays trivial.
"""

from __future__ import annotations

import os
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import db_connect  # noqa: E402


def format_money(cents, currency: str = "USD") -> str:
    """Render integer cents as a human string, e.g. 45000 → '$450.00'.

    Falls back to a currency-prefixed form for non-USD codes. ``None`` → '—'.
    """
    if cents is None:
        return "—"
    symbol = {"USD": "$", "EUR": "€", "GBP": "£", "ZAR": "R"}.get(currency or "", "")
    amount = f"{cents / 100:,.2f}"
    if symbol:
        return f"{symbol}{amount}"
    return f"{amount} {currency}".strip()


# Digital Silk status → semantic colour bucket (TS-11). The panel maps these
# bucket names to its theme; keeping the mapping here keeps panels declarative.
def sync_colour(status: str | None, replicate: int | None = None) -> str:
    """Return a semantic colour bucket for an exchange/sync state."""
    if status == "sync-failed":
        return "red"
    if replicate:
        return "amber"          # pending push
    if status == "sync-done":
        return "green"
    return "gray"


def invoice_colour(status: str | None) -> str:
    """Semantic colour bucket for an invoice status."""
    s = (status or "").lower()
    if s in ("paid",):
        return "gray"           # closed
    if s in ("overdue",):
        return "red"
    if s in ("sent", "viewed"):
        return "green"
    return "amber"              # draft / unknown → needs attention


class OrganizationReader:
    """Read-only accessor for the Organization domain database."""

    def __init__(self, db_path: str | None = None):
        self.db_path = db_path or db_connect.organization_db_path()

    # ── low-level ──
    def available(self) -> bool:
        """True if the organization database file exists (feature activated)."""
        return os.path.isfile(self.db_path)

    def _query(self, sql: str, params: tuple = ()) -> list[dict]:
        if not self.available():
            return []
        try:
            conn = db_connect.connect(self.db_path, readonly=True, timeout=2)
            try:
                return [dict(r) for r in conn.execute(sql, params).fetchall()]
            finally:
                conn.close()
        except Exception:
            return []

    def _one(self, sql: str, params: tuple = ()) -> dict | None:
        rows = self._query(sql, params)
        return rows[0] if rows else None

    # ── summary (panel header / footer) ──
    def summary(self) -> dict:
        """Counts across the domain plus sync health, for the panel header."""
        if not self.available():
            return {"available": False}
        out = {"available": True}
        for label, table in (
            ("organizations", "organizations"), ("products", "products"),
            ("invoices", "invoices"), ("estimates", "estimates"),
            ("transactions", "transactions"), ("exchange", "exchange"),
        ):
            row = self._one(f"SELECT COUNT(*) AS n FROM {table}")
            out[label] = row["n"] if row else 0
        pend = self._one("SELECT COUNT(*) AS n FROM exchange WHERE replicate=1")
        fail = self._one("SELECT COUNT(*) AS n FROM exchange WHERE status='sync-failed'")
        out["pending_push"] = pend["n"] if pend else 0
        out["sync_failed"] = fail["n"] if fail else 0
        return out

    # ── list views ──
    def list_organizations(self, limit: int = 200) -> list[dict]:
        return self._query(
            "SELECT id, name, slug, type, is_active, logo_path, external_id, "
            "updated_at FROM organizations ORDER BY name LIMIT ?", (limit,))

    def picklist_options(self, table_name: str, field_name: str) -> list[tuple[str, str]]:
        """Managed-vocabulary options for a (table, field) form picklist —
        ``[(name, value), …]`` ordered by position. A row whose ``table_name`` is
        empty applies to that field on any table (used for the shared currency
        list), so the lookup matches both the specific table and the global rows."""
        rows = self._query(
            "SELECT name, value FROM picklists "
            "WHERE field_name = ? AND (table_name = ? OR table_name = '') "
            "ORDER BY position, name", (field_name, table_name))
        return [(r["name"], r["value"]) for r in rows]

    def list_picklists(self, table_name: str | None = None,
                       field_name: str | None = None, limit: int = 500) -> list[dict]:
        """Raw picklist rows for the Manage Lists modal, optionally scoped to a
        (table, field). Newest-first within the position order."""
        sql = "SELECT id, name, value, table_name, field_name, position FROM picklists"
        clauses, params = [], []
        if table_name is not None:
            clauses.append("table_name = ?")
            params.append(table_name)
        if field_name is not None:
            clauses.append("field_name = ?")
            params.append(field_name)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY field_name, position, name LIMIT ?"
        params.append(limit)
        return self._query(sql, tuple(params))

    def list_invoices(self, limit: int = 200) -> list[dict]:
        """Invoices with issuing-org and customer-org names resolved."""
        rows = self._query(
            "SELECT i.id, i.invoice_number, i.status, i.total_cents, i.currency, "
            "       i.issue_date, i.due_date, i.paid_date, i.external_id, "
            "       o.name AS org_name, co.name AS customer_name "
            "FROM invoices i "
            "JOIN organizations o ON o.id = i.org_id "
            "LEFT JOIN organizations co ON co.id = i.customer_org_id "
            "ORDER BY i.id DESC LIMIT ?", (limit,))
        for r in rows:
            r["total_display"] = format_money(r.get("total_cents"), r.get("currency"))
            r["colour"] = invoice_colour(r.get("status"))
        return rows

    def list_estimates(self, limit: int = 200) -> list[dict]:
        rows = self._query(
            "SELECT e.id, e.estimate_number, e.status, e.total_cents, e.currency, "
            "       e.issue_date, e.expiry_date, e.converted_to_invoice_id, "
            "       o.name AS org_name "
            "FROM estimates e JOIN organizations o ON o.id = e.org_id "
            "ORDER BY e.id DESC LIMIT ?", (limit,))
        for r in rows:
            r["total_display"] = format_money(r.get("total_cents"), r.get("currency"))
        return rows

    def list_transactions(self, limit: int = 200) -> list[dict]:
        rows = self._query(
            "SELECT t.id, t.transaction_date, t.account_name, t.description, "
            "       t.amount_cents, t.currency, t.category, o.name AS org_name, "
            "       cp.name AS counterparty_name "
            "FROM transactions t JOIN organizations o ON o.id = t.org_id "
            "LEFT JOIN organizations cp ON cp.id = t.counterparty_org_id "
            "ORDER BY t.transaction_date DESC, t.id DESC LIMIT ?", (limit,))
        for r in rows:
            r["amount_display"] = format_money(r.get("amount_cents"), r.get("currency"))
        return rows

    def list_products(self, limit: int = 200) -> list[dict]:
        rows = self._query(
            "SELECT p.id, p.name, p.sku, p.type, p.unit_price_cents, p.currency, "
            "       p.is_active, o.name AS org_name "
            "FROM products p JOIN organizations o ON o.id = p.org_id "
            "ORDER BY p.name LIMIT ?", (limit,))
        for r in rows:
            r["price_display"] = format_money(r.get("unit_price_cents"), r.get("currency"))
        return rows

    def list_exchange(self, limit: int = 200) -> list[dict]:
        """Recent exchange/sync rows with a semantic colour bucket each."""
        rows = self._query(
            "SELECT e.id, e.name, e.source_table, e.source_id, e.external_id, "
            "       e.source_org_id, e.target_org_id, e.origin, "
            "       e.status, e.replicate, e.error_message, e.updated_at, "
            "       so.name AS source_org_name, "
            "       tg.name AS target_org_name "
            "FROM exchange e "
            "LEFT JOIN organizations so ON so.id = e.source_org_id "
            "LEFT JOIN organizations tg ON tg.id = e.target_org_id "
            "ORDER BY e.id DESC LIMIT ?", (limit,))
        for r in rows:
            r["colour"] = sync_colour(r.get("status"), r.get("replicate"))
        return rows

    def list_org_customers(self, limit: int = 200) -> list[dict]:
        """Every organization, for the invoice/estimate customer picklist.

        Customer is no longer a stored bridge — any organization can be billed,
        and the customer relationship is *implied* once the invoice exists, so
        the picklist is simply the org list. Kept as the stable name the form
        layer asks for (returns ``customer_name`` per row)."""
        return self._query(
            "SELECT id, name AS customer_name FROM organizations "
            "ORDER BY name LIMIT ?", (limit,))

    def customers_of(self, org_id: int) -> list[dict]:
        """Orgs this org has billed (issued an invoice/estimate to) — DYNAMIC.

        A->B invoice/estimate means B is a customer of A. Deduplicated across
        both document types."""
        return self._query(
            "SELECT co.id AS org_id, co.name FROM organizations co WHERE co.id IN ("
            "  SELECT customer_org_id FROM invoices "
            "    WHERE org_id=? AND customer_org_id IS NOT NULL "
            "  UNION "
            "  SELECT customer_org_id FROM estimates "
            "    WHERE org_id=? AND customer_org_id IS NOT NULL"
            ") ORDER BY co.name", (org_id, org_id))

    def vendors_of(self, org_id: int) -> list[dict]:
        """Orgs that have billed this org (issued it an invoice/estimate) —
        DYNAMIC. A->B invoice/estimate means A is a vendor of B."""
        return self._query(
            "SELECT so.id AS org_id, so.name FROM organizations so WHERE so.id IN ("
            "  SELECT org_id FROM invoices WHERE customer_org_id=? "
            "  UNION "
            "  SELECT org_id FROM estimates WHERE customer_org_id=?"
            ") ORDER BY so.name", (org_id, org_id))

    # ── detail views (modals) ──
    def organization_detail(self, org_id: int) -> dict | None:
        """An org plus the customers, vendors, invoices, estimates, emails and
        addresses attached to it — the Explorer's org detail payload.

        Customers and vendors are DERIVED from the invoices/estimates the org
        exchanges (no stored customer/vendor bridge)."""
        org = self._one("SELECT * FROM organizations WHERE id=?", (org_id,))
        if not org:
            return None
        customers = self.customers_of(org_id)
        vendors = self.vendors_of(org_id)
        invoices = self.list_invoices_for_org(org_id)
        estimates = self._query(
            "SELECT id, estimate_number, status, total_cents, currency "
            "FROM estimates WHERE org_id=? ORDER BY id DESC", (org_id,))
        for r in estimates:
            r["total_display"] = format_money(r.get("total_cents"), r.get("currency"))
        emails = self._query(
            "SELECT e.email, e.label, e.is_primary FROM org_emails oe "
            "JOIN email_addresses e ON e.id = oe.email_id WHERE oe.org_id=?", (org_id,))
        addresses = self._query(
            "SELECT a.line_1, a.city, a.state, a.postal_code, a.country "
            "FROM org_addresses oa JOIN physical_addresses a ON a.id = oa.address_id "
            "WHERE oa.org_id=?", (org_id,))
        return {"org": org, "customers": customers, "vendors": vendors,
                "invoices": invoices, "estimates": estimates,
                "emails": emails, "addresses": addresses}

    def list_invoices_for_org(self, org_id: int) -> list[dict]:
        rows = self._query(
            "SELECT id, invoice_number, status, total_cents, currency, issue_date "
            "FROM invoices WHERE org_id=? ORDER BY id DESC", (org_id,))
        for r in rows:
            r["total_display"] = format_money(r.get("total_cents"), r.get("currency"))
            r["colour"] = invoice_colour(r.get("status"))
        return rows

    def line_items(self, kind: str, parent_id: int) -> list[dict]:
        """Line items for an invoice or estimate (with product names + money
        displays) — drives the Lines editor. ``kind`` is the panel kind
        ('invoices' | 'estimates')."""
        table, fk = ("invoice_line_items", "invoice_id") if kind == "invoices" \
            else ("estimate_line_items", "estimate_id")
        rows = self._query(
            f"SELECT li.id, li.product_id, li.description, li.quantity, "
            f"       li.unit_price_cents, li.total_cents, p.name AS product_name "
            f"FROM {table} li LEFT JOIN products p ON p.id = li.product_id "
            f"WHERE li.{fk}=? ORDER BY li.id", (parent_id,))
        for r in rows:
            r["unit_price_display"] = format_money(r.get("unit_price_cents"))
            r["total_display"] = format_money(r.get("total_cents"))
        return rows

    def line_subtotal(self, kind: str, parent_id: int) -> int:
        """Sum of line totals (cents) for an invoice/estimate — the subtotal."""
        return sum(int(r.get("total_cents") or 0)
                   for r in self.line_items(kind, parent_id))

    def list_products_for_org(self, org_id: int) -> list[dict]:
        rows = self._query(
            "SELECT id, name, sku, unit_price_cents, currency, is_active "
            "FROM products WHERE org_id=? ORDER BY name", (org_id,))
        for r in rows:
            r["price_display"] = format_money(r.get("unit_price_cents"), r.get("currency"))
        return rows

    def list_transactions_for_org(self, org_id: int) -> list[dict]:
        rows = self._query(
            "SELECT id, transaction_date, description, amount_cents, currency, category "
            "FROM transactions WHERE org_id=? ORDER BY transaction_date DESC, id DESC",
            (org_id,))
        for r in rows:
            r["amount_display"] = format_money(r.get("amount_cents"), r.get("currency"))
        return rows

    # ── New entity list views (Staff / Emails / Addresses / Credentials) ──

    def list_credentials(self, limit: int = 200) -> list[dict]:
        """All credential records with auth_type."""
        return self._query(
            "SELECT id, auth_type, configuration, notes, updated_at "
            "FROM credentials ORDER BY id DESC LIMIT ?", (limit,))

    def list_emails(self, limit: int = 200) -> list[dict]:
        """Email addresses with org context (via org_emails) and credential info."""
        return self._query(
            "SELECT e.id, e.email, e.label, e.is_primary, "
            "       e.usage_notes, e.credential_id, "
            "       c.auth_type AS credential_type, "
            "       o.name AS org_name, oe.org_id "
            "FROM email_addresses e "
            "LEFT JOIN org_emails oe ON oe.email_id = e.id "
            "LEFT JOIN organizations o ON o.id = oe.org_id "
            "LEFT JOIN credentials c ON c.id = e.credential_id "
            "ORDER BY e.id DESC LIMIT ?", (limit,))

    def list_staff(self, limit: int = 200) -> list[dict]:
        """Org staff rows with org name. connection_uid returned for cross-DB
        resolution via TasksReader (Option A — two-query pattern)."""
        return self._query(
            "SELECT s.id, s.org_id, s.connection_uid, s.created_at, "
            "       o.name AS org_name "
            "FROM org_staff s "
            "LEFT JOIN organizations o ON o.id = s.org_id "
            "ORDER BY s.id DESC LIMIT ?", (limit,))

    def list_addresses(self, limit: int = 200) -> list[dict]:
        """Physical addresses with org context (via org_addresses)."""
        return self._query(
            "SELECT a.id, a.line_1, a.line_2, a.city, a.state, "
            "       a.postal_code, a.country, a.label, a.is_primary, "
            "       o.name AS org_name, oa.org_id "
            "FROM physical_addresses a "
            "LEFT JOIN org_addresses oa ON oa.address_id = a.id "
            "LEFT JOIN organizations o ON o.id = oa.org_id "
            "ORDER BY a.id DESC LIMIT ?", (limit,))

    # ── Per-org bridge lookups (Organization Record Modal) ──

    def list_org_emails(self, org_id: int) -> list[dict]:
        """Emails linked to a specific org via org_emails."""
        return self._query(
            "SELECT e.id, e.email, e.label, e.is_primary, "
            "       e.usage_notes, e.credential_id, "
            "       c.auth_type AS credential_type, "
            "       oe.id AS bridge_id "
            "FROM org_emails oe "
            "JOIN email_addresses e ON e.id = oe.email_id "
            "LEFT JOIN credentials c ON c.id = e.credential_id "
            "WHERE oe.org_id = ? "
            "ORDER BY e.email", (org_id,))

    def list_org_addresses(self, org_id: int) -> list[dict]:
        """Addresses linked to a specific org via org_addresses."""
        return self._query(
            "SELECT a.id, a.line_1, a.line_2, a.city, a.state, "
            "       a.postal_code, a.country, a.label, a.is_primary, "
            "       oa.id AS bridge_id "
            "FROM org_addresses oa "
            "JOIN physical_addresses a ON a.id = oa.address_id "
            "WHERE oa.org_id = ? "
            "ORDER BY a.line_1", (org_id,))

    def list_org_staff(self, org_id: int) -> list[dict]:
        """Staff linked to a specific org via org_staff."""
        return self._query(
            "SELECT s.id, s.org_id, s.connection_uid, s.created_at "
            "FROM org_staff s "
            "WHERE s.org_id = ? "
            "ORDER BY s.created_at DESC", (org_id,))

    def list_staff_addresses(self, org_staff_id: int) -> list[dict]:
        """Addresses linked to a staff member via org_staff_addresses."""
        return self._query(
            "SELECT a.id, a.line_1, a.line_2, a.city, a.state, "
            "       a.postal_code, a.country, a.label, a.is_primary, "
            "       sa.id AS bridge_id "
            "FROM org_staff_addresses sa "
            "JOIN physical_addresses a ON a.id = sa.address_id "
            "WHERE sa.org_staff_id = ? "
            "ORDER BY a.line_1", (org_staff_id,))

    def list_unlinked_emails(self, org_id: int) -> list[dict]:
        """Emails NOT yet linked to this org (for the 'Link Existing' picklist)."""
        return self._query(
            "SELECT e.id, e.email, e.label "
            "FROM email_addresses e "
            "WHERE e.id NOT IN ("
            "  SELECT oe.email_id FROM org_emails oe WHERE oe.org_id = ?"
            ") ORDER BY e.email", (org_id,))

    def list_unlinked_addresses(self, org_id: int) -> list[dict]:
        """Addresses NOT yet linked to this org (for the 'Link Existing' picklist)."""
        return self._query(
            "SELECT a.id, a.line_1, a.city, a.state, a.country "
            "FROM physical_addresses a "
            "WHERE a.id NOT IN ("
            "  SELECT oa.address_id FROM org_addresses oa WHERE oa.org_id = ?"
            ") ORDER BY a.line_1", (org_id,))

    def list_unlinked_addresses_for_staff(self, org_staff_id: int) -> list[dict]:
        """Addresses NOT yet linked to this staff member."""
        return self._query(
            "SELECT a.id, a.line_1, a.city, a.state, a.country "
            "FROM physical_addresses a "
            "WHERE a.id NOT IN ("
            "  SELECT sa.address_id FROM org_staff_addresses sa "
            "  WHERE sa.org_staff_id = ?"
            ") ORDER BY a.line_1", (org_staff_id,))

