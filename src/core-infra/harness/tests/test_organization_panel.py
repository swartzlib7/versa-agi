"""Headless UI tests for the Organization agitop panel + modals (D25/D26).

Uses Textual's built-in ``run_test`` pilot harness to mount the real
``OrganizationPanel`` (and its detail modals) against a seeded temporary
``organization.db`` — proving the read-only UI layer renders live data without
needing a terminal. No agent, no full dashboard: a minimal host App embeds just
the panel, so these tests stay fast and isolated.

Run:  cd core-infra
      /opt/versa-agi/venv/bin/python3 -m unittest harness.tests.test_organization_panel
"""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest

_CORE_INFRA = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _CORE_INFRA)

import organization_demo as demo                       # noqa: E402
from agitop.data.organization_reader import OrganizationReader  # noqa: E402
from agitop.data.organization_writer import OrganizationWriter  # noqa: E402

from textual.app import App, ComposeResult             # noqa: E402
from textual.widgets import DataTable, Button, Input, Tree, Checkbox, Select  # noqa: E402
from agitop.panels.organization import (                # noqa: E402
    OrganizationEntityPanel, OrganizationTreePanel, ORGANIZATION_TABS,
    EntityFormModal, ConfirmDeleteModal,
)

_INIT_SCRIPT = os.path.join(_CORE_INFRA, "scripts", "init_organization_db.sh")


class _HostApp(App):
    """Minimal host that embeds every flattened Organization entity panel,
    the tree Explorer, and (optionally) the write action bar."""

    def __init__(self, reader: OrganizationReader, writer: OrganizationWriter | None = None):
        super().__init__()
        self._reader = reader
        self._writer = writer

    def compose(self) -> ComposeResult:
        for kind, _icon, _label in ORGANIZATION_TABS:
            yield OrganizationEntityPanel(
                self._reader, kind, writer=self._writer, id=f"org-{kind}-panel",
            )
        yield OrganizationTreePanel(self._reader, id="org-explorer-panel")


class OrganizationPanelTest(unittest.IsolatedAsyncioTestCase):
    """Mount the panel headlessly against a seeded demo database."""

    def setUp(self):
        self._dir = tempfile.mkdtemp(prefix="orgui_")
        self.db = os.path.join(self._dir, "organization.db")
        proc = subprocess.run(["bash", _INIT_SCRIPT, self.db],
                              capture_output=True, text=True)
        if proc.returncode != 0:
            self.fail(f"init failed: {proc.stderr or proc.stdout}")
        self._prev = os.environ.get("AGICTL_ORGANIZATION_DB")
        os.environ["AGICTL_ORGANIZATION_DB"] = self.db
        demo.seed()
        self.reader = OrganizationReader(self.db)

    def tearDown(self):
        if self._prev is None:
            os.environ.pop("AGICTL_ORGANIZATION_DB", None)
        else:
            os.environ["AGICTL_ORGANIZATION_DB"] = self._prev
        shutil.rmtree(self._dir, ignore_errors=True)

    async def test_panel_mounts_and_tables_populate(self):
        """Every entity panel mounts and its table is filled from the DB."""
        app = _HostApp(self.reader)
        async with app.run_test() as pilot:
            await pilot.pause()
            orgs_panel = app.query_one("#org-organizations-panel", OrganizationEntityPanel)
            inv_panel = app.query_one("#org-invoices-panel", OrganizationEntityPanel)
            exc_panel = app.query_one("#org-exchange-panel", OrganizationEntityPanel)
            orgs = app.query_one("#org-organizations-table", DataTable)
            invs = app.query_one("#org-invoices-table", DataTable)
            exch = app.query_one("#org-exchange-table", DataTable)
            self.assertGreaterEqual(orgs.row_count, 5)
            self.assertGreaterEqual(invs.row_count, 3)
            self.assertGreaterEqual(exch.row_count, 2)
            # Each panel cached the row dicts it used to populate (for modal lookup).
            self.assertGreaterEqual(len(orgs_panel._rows), 5)
            self.assertGreaterEqual(len(inv_panel._rows), 3)
            self.assertGreaterEqual(len(exc_panel._rows), 2)


class OrganizationFilterTest(unittest.IsolatedAsyncioTestCase):
    """Per-tab picklist filters narrow the visible rows (D33 round 4)."""

    def setUp(self):
        self._dir = tempfile.mkdtemp(prefix="orgfilt_")
        self.db = os.path.join(self._dir, "organization.db")
        proc = subprocess.run(["bash", _INIT_SCRIPT, self.db],
                              capture_output=True, text=True)
        if proc.returncode != 0:
            self.fail(f"init failed: {proc.stderr or proc.stdout}")
        self._prev = os.environ.get("AGICTL_ORGANIZATION_DB")
        os.environ["AGICTL_ORGANIZATION_DB"] = self.db
        demo.seed()
        self.reader = OrganizationReader(self.db)

    def tearDown(self):
        if self._prev is None:
            os.environ.pop("AGICTL_ORGANIZATION_DB", None)
        else:
            os.environ["AGICTL_ORGANIZATION_DB"] = self._prev
        shutil.rmtree(self._dir, ignore_errors=True)

    async def test_value_filter_narrows_and_clears(self):
        """Filtering products by org shows only that org's rows; clearing restores."""
        app = _HostApp(self.reader)
        async with app.run_test() as pilot:
            await pilot.pause()
            panel = app.query_one("#org-products-panel", OrganizationEntityPanel)
            table = app.query_one("#org-products-table", DataTable)
            full = table.row_count
            products = self.reader.list_products()
            target_org = products[0]["org_name"]
            expected = sum(1 for p in products if p["org_name"] == target_org)
            panel._filters["org"] = target_org
            panel._refill()
            await pilot.pause()
            self.assertEqual(table.row_count, expected)
            self.assertLessEqual(table.row_count, full)
            panel._filters.clear()
            panel._refill()
            await pilot.pause()
            self.assertEqual(table.row_count, full)

    async def test_bool_filter_via_select_change(self):
        """Selecting the Exchange 'Push' picklist filters via the Select.Changed path."""
        app = _HostApp(self.reader)
        async with app.run_test() as pilot:
            await pilot.pause()
            table = app.query_one("#org-exchange-table", DataTable)
            rows = self.reader.list_exchange()
            expected_push = sum(1 for r in rows if r.get("replicate"))
            select = app.query_one("#org-exchange-filter-push", Select)
            select.value = "1"
            await pilot.pause()
            self.assertEqual(table.row_count, expected_push)
            # Clearing the filter state and refilling restores every row.
            panel = app.query_one("#org-exchange-panel", OrganizationEntityPanel)
            panel._filters.clear()
            panel._refill()
            await pilot.pause()
            self.assertEqual(table.row_count, len(rows))

    async def test_filter_reset_via_all_option_reloads_table(self):
        """Selecting the explicit 'All' option (the reset row) reloads every row —
        the deterministic fix for the filter-reset-doesn't-reload bug."""
        from agitop.panels.organization import _FILTER_ALL
        app = _HostApp(self.reader)
        async with app.run_test() as pilot:
            await pilot.pause()
            table = app.query_one("#org-products-table", DataTable)
            full = table.row_count
            select = app.query_one("#org-products-filter-org", Select)
            products = self.reader.list_products()
            target_org = products[0]["org_name"]
            expected = sum(1 for p in products if p["org_name"] == target_org)
            # Apply via the real Select.Changed path.
            select.value = target_org
            await pilot.pause()
            self.assertEqual(table.row_count, expected)
            # Reset via the explicit All option — table must reload to full.
            select.value = _FILTER_ALL
            await pilot.pause()
            self.assertEqual(table.row_count, full)
            panel = app.query_one("#org-products-panel", OrganizationEntityPanel)
            self.assertNotIn("org", panel._filters)


class OrganizationFormFieldsTest(unittest.IsolatedAsyncioTestCase):
    """Form picklists, system-set fields, layout order, and the View button
    (D33 UI feedback — Phase A)."""

    def setUp(self):
        self._dir = tempfile.mkdtemp(prefix="orgform_")
        self.db = os.path.join(self._dir, "organization.db")
        proc = subprocess.run(["bash", _INIT_SCRIPT, self.db],
                              capture_output=True, text=True)
        if proc.returncode != 0:
            self.fail(f"init failed: {proc.stderr or proc.stdout}")
        self._prev = os.environ.get("AGICTL_ORGANIZATION_DB")
        os.environ["AGICTL_ORGANIZATION_DB"] = self.db
        demo.seed()
        self.reader = OrganizationReader(self.db)
        self.writer = OrganizationWriter(self.db)

    def tearDown(self):
        if self._prev is None:
            os.environ.pop("AGICTL_ORGANIZATION_DB", None)
        else:
            os.environ["AGICTL_ORGANIZATION_DB"] = self._prev
        shutil.rmtree(self._dir, ignore_errors=True)

    async def test_exchange_system_fields_disabled_and_picklists(self):
        """Exchange form: source_id/error_message/origin disabled; status and
        source_table are Select picklists."""
        app = _HostApp(self.reader, self.writer)
        async with app.run_test() as pilot:
            await pilot.pause()
            panel = app.query_one("#org-exchange-panel", OrganizationEntityPanel)
            panel._open_form(None)
            await pilot.pause()
            modal = app.screen
            for col in ("source_id", "error_message", "origin"):
                self.assertTrue(modal.query_one(f"#f-{col}").disabled, col)
            self.assertIsInstance(modal.query_one("#f-status"), Select)
            self.assertIsInstance(modal.query_one("#f-source_table"), Select)

    async def test_exchange_create_auto_stamps_origin_user(self):
        """A hand-created exchange row lands origin='user' (operator is the user)."""
        app = _HostApp(self.reader, self.writer)
        async with app.run_test() as pilot:
            await pilot.pause()
            panel = app.query_one("#org-exchange-panel", OrganizationEntityPanel)
            panel._open_form(None)
            await pilot.pause()
            modal = app.screen
            modal.query_one("#f-name", Input).value = "Wave"
            modal.query_one("#f-source_table", Select).value = "invoices"
            modal.on_button_pressed(Button.Pressed(modal.query_one("#form-save", Button)))
            await pilot.pause()
            row = next(r for r in self.reader.list_exchange() if r["name"] == "Wave")
            self.assertEqual(row["origin"], "user")

    async def test_estimate_and_invoice_status_are_picklists(self):
        """status renders as a Select on estimates and invoices."""
        app = _HostApp(self.reader, self.writer)
        async with app.run_test() as pilot:
            await pilot.pause()
            for kind in ("estimates", "invoices"):
                panel = app.query_one(f"#org-{kind}-panel", OrganizationEntityPanel)
                panel._open_form(None)
                await pilot.pause()
                self.assertIsInstance(app.screen.query_one("#f-status"), Select)
                app.pop_screen()
                await pilot.pause()

    async def test_product_form_field_order(self):
        """The product form lays out fields in Stephen's requested order."""
        from agitop.panels.organization import _FORM_ORDER
        app = _HostApp(self.reader, self.writer)
        async with app.run_test() as pilot:
            await pilot.pause()
            panel = app.query_one("#org-products-panel", OrganizationEntityPanel)
            panel._open_form(None)
            await pilot.pause()
            self.assertEqual(list(app.screen._widgets.keys()), _FORM_ORDER["product"])

    async def test_money_placeholder_is_typical(self):
        """Money fields show a typical example, not a uniform 500.00."""
        app = _HostApp(self.reader, self.writer)
        async with app.run_test() as pilot:
            await pilot.pause()
            panel = app.query_one("#org-products-panel", OrganizationEntityPanel)
            panel._open_form(None)
            await pilot.pause()
            self.assertEqual(
                app.screen.query_one("#f-unit_price_cents", Input).placeholder, "120.00")

    async def test_type_currency_status_are_picklists_from_table(self):
        """Managed-vocabulary fields render as Selects sourced from the picklist
        table (seeded by init): product type/currency, org type, txn category."""
        app = _HostApp(self.reader, self.writer)
        async with app.run_test() as pilot:
            await pilot.pause()
            checks = [("products", ["type", "currency"]),
                      ("organizations", ["type"]),
                      ("transactions", ["category", "currency"])]
            for kind, cols in checks:
                panel = app.query_one(f"#org-{kind}-panel", OrganizationEntityPanel)
                panel._open_form(None)
                await pilot.pause()
                for col in cols:
                    self.assertIsInstance(app.screen.query_one(f"#f-{col}"), Select, col)
                app.pop_screen()
                await pilot.pause()

    async def test_manage_types_button_only_on_org_and_product(self):
        """The Manage Types button is on Organizations and Products only."""
        app = _HostApp(self.reader, self.writer)
        async with app.run_test() as pilot:
            await pilot.pause()
            for kind in ("organizations", "products"):
                self.assertEqual(len(app.query(f"#org-{kind}-managetypes")), 1)
            for kind in ("invoices", "estimates", "transactions", "exchange"):
                self.assertEqual(len(app.query(f"#org-{kind}-managetypes")), 0)

    async def test_manage_modal_adds_an_option(self):
        """Adding a value through the Manage Lists modal persists it."""
        from agitop.panels.organization import ManagePicklistsModal
        app = _HostApp(self.reader, self.writer)
        async with app.run_test() as pilot:
            await pilot.pause()
            app.push_screen(ManagePicklistsModal(self.writer, self.reader,
                                                 "products", "type"))
            await pilot.pause()
            modal = app.screen
            modal.query_one("#pick-name", Input).value = "Wholesale"
            modal.query_one("#pick-value", Input).value = "wholesale"
            modal.on_button_pressed(Button.Pressed(modal.query_one("#pick-add", Button)))
            await pilot.pause()
            self.assertIn(("Wholesale", "wholesale"),
                          self.reader.picklist_options("products", "type"))

    async def test_manage_modal_replace_and_delete_repoints_rows(self):
        """Replace & Delete on an in-use option repoints data rows then removes it."""
        from agitop.panels.organization import ManagePicklistsModal
        app = _HostApp(self.reader, self.writer)
        async with app.run_test() as pilot:
            await pilot.pause()
            # Demo seeds two 'service' products — deleting 'service' must repoint them.
            self.assertTrue(any(p["type"] == "service" for p in self.reader.list_products()))
            app.push_screen(ManagePicklistsModal(self.writer, self.reader,
                                                 "products", "type"))
            await pilot.pause()
            modal = app.screen
            modal.table.move_cursor(row=0)          # 'service' (position 1)
            await pilot.pause()
            self.assertEqual(modal._selected()["value"], "service")
            modal.query_one("#pick-replacement", Select).value = "product"
            modal.on_button_pressed(Button.Pressed(modal.query_one("#pick-del", Button)))
            await pilot.pause()
            values = [v for _l, v in self.reader.picklist_options("products", "type")]
            self.assertNotIn("service", values)
            self.assertNotIn("service", {p["type"] for p in self.reader.list_products()})

    async def test_org_slug_auto_generated_and_locked(self):
        """Creating an org auto-derives a slug from the name; the field is locked."""
        app = _HostApp(self.reader, self.writer)
        async with app.run_test() as pilot:
            await pilot.pause()
            panel = app.query_one("#org-organizations-panel", OrganizationEntityPanel)
            panel._open_form(None)
            await pilot.pause()
            modal = app.screen
            self.assertTrue(modal.query_one("#f-slug", Input).disabled)
            modal.query_one("#f-name", Input).value = "Bright & Bold Co"
            modal.on_button_pressed(Button.Pressed(modal.query_one("#form-save", Button)))
            await pilot.pause()
            org = next(o for o in self.reader.list_organizations()
                       if o["name"] == "Bright & Bold Co")
            self.assertEqual(org["slug"], "bright-bold-co")

    async def test_org_slug_dedupes(self):
        """A second org with a colliding name gets a -2 suffixed slug."""
        app = _HostApp(self.reader, self.writer)
        async with app.run_test() as pilot:
            await pilot.pause()
            panel = app.query_one("#org-organizations-panel", OrganizationEntityPanel)
            for _ in range(2):
                panel._open_form(None)
                await pilot.pause()
                modal = app.screen
                modal.query_one("#f-name", Input).value = "Dup Name"
                modal.on_button_pressed(Button.Pressed(modal.query_one("#form-save", Button)))
                await pilot.pause()
            slugs = {o["slug"] for o in self.reader.list_organizations()
                     if o["name"] == "Dup Name"}
            self.assertEqual(slugs, {"dup-name", "dup-name-2"})

    async def test_invoice_estimate_totals_disabled_in_header_form(self):
        """subtotal/tax/total are read-only on the invoice & estimate forms
        (owned by the Lines editor)."""
        app = _HostApp(self.reader, self.writer)
        async with app.run_test() as pilot:
            await pilot.pause()
            for kind in ("invoices", "estimates"):
                panel = app.query_one(f"#org-{kind}-panel", OrganizationEntityPanel)
                panel._open_form(None)
                await pilot.pause()
                modal = app.screen
                for col in ("subtotal_cents", "tax_total_cents", "total_cents"):
                    self.assertTrue(modal.query_one(f"#f-{col}").disabled, f"{kind}.{col}")
                app.pop_screen()
                await pilot.pause()

    async def test_lines_button_only_on_invoice_and_estimate(self):
        """The Lines action button is on Invoices and Estimates only."""
        app = _HostApp(self.reader, self.writer)
        async with app.run_test() as pilot:
            await pilot.pause()
            for kind in ("invoices", "estimates"):
                self.assertEqual(len(app.query(f"#org-{kind}-lines")), 1)
            for kind in ("organizations", "products", "transactions", "exchange"):
                self.assertEqual(len(app.query(f"#org-{kind}-lines")), 0)

    async def test_line_editor_adds_line_and_recomputes_totals(self):
        """Adding a line through the editor recomputes the parent subtotal/total."""
        from agitop.panels.organization import LineItemsModal
        org = self.reader.list_organizations()[0]
        inv = self.writer.create("invoice", {"org_id": org["id"],
                                             "invoice_number": "LINE-TEST"})["id"]
        app = _HostApp(self.reader, self.writer)
        async with app.run_test() as pilot:
            await pilot.pause()
            parent = self.writer.get("invoice", inv)
            app.push_screen(LineItemsModal(self.writer, self.reader, "invoices",
                                           inv, parent))
            await pilot.pause()
            modal = app.screen
            modal.query_one("#line-desc", Input).value = "Consulting"
            modal.query_one("#line-qty", Input).value = "2.5"
            modal.query_one("#line-unit", Input).value = "120.00"
            modal.query_one("#line-tax", Input).value = "30.00"
            modal.on_button_pressed(Button.Pressed(modal.query_one("#line-add", Button)))
            await pilot.pause()
            row = self.writer.get("invoice", inv)
            self.assertEqual(row["subtotal_cents"], 30000)   # 2.5 × 120.00
            self.assertEqual(row["tax_total_cents"], 3000)   # manual tax
            self.assertEqual(row["total_cents"], 33000)      # subtotal + tax
            self.assertEqual(len(self.reader.line_items("invoices", inv)), 1)


class OrganizationWriterTest(unittest.TestCase):
    """The write seam (OrganizationWriter) — same store path agictl uses."""

    def setUp(self):
        self._dir = tempfile.mkdtemp(prefix="orgwr_")
        self.db = os.path.join(self._dir, "organization.db")
        proc = subprocess.run(["bash", _INIT_SCRIPT, self.db],
                              capture_output=True, text=True)
        if proc.returncode != 0:
            self.fail(f"init failed: {proc.stderr or proc.stdout}")
        self._prev = os.environ.get("AGICTL_ORGANIZATION_DB")
        os.environ["AGICTL_ORGANIZATION_DB"] = self.db
        self.reader = OrganizationReader(self.db)
        self.writer = OrganizationWriter(self.db)

    def tearDown(self):
        if self._prev is None:
            os.environ.pop("AGICTL_ORGANIZATION_DB", None)
        else:
            os.environ["AGICTL_ORGANIZATION_DB"] = self._prev
        shutil.rmtree(self._dir, ignore_errors=True)

    def test_create_update_delete_round_trip(self):
        """create → reader sees it; update mutates; delete removes it."""
        res = self.writer.create("org", {"name": "Acme Co", "slug": "acme"})
        self.assertTrue(res["success"])
        oid = res["id"]
        self.assertEqual(self.reader.organization_detail(oid)["org"]["name"], "Acme Co")

        upd = self.writer.update("org", oid, {"notes": "vip client"})
        self.assertTrue(upd["success"])
        self.assertEqual(self.reader.organization_detail(oid)["org"]["notes"], "vip client")

        rm = self.writer.delete("org", oid)
        self.assertTrue(rm["success"])
        self.assertIsNone(self.reader.organization_detail(oid))

    def test_create_validation_error_is_structured(self):
        """A missing required field returns success:false (not an exception)."""
        res = self.writer.create("org", {"slug": "no-name"})
        self.assertFalse(res["success"])
        self.assertEqual(res["code"], "missing_required")

    def test_delete_blocked_by_foreign_key(self):
        """An org referenced by an invoice cannot be deleted (FK on)."""
        oid = self.writer.create("org", {"name": "Has Invoices"})["id"]
        self.writer.create("invoice", {"org_id": oid, "total_cents": 1000})
        rm = self.writer.delete("org", oid)
        self.assertFalse(rm["success"])
        self.assertEqual(rm["code"], "constraint")


class OrganizationWriteUITest(unittest.IsolatedAsyncioTestCase):
    """The editable panel: action bar, generic form modal, and persistence."""

    def setUp(self):
        self._dir = tempfile.mkdtemp(prefix="orgwrui_")
        self.db = os.path.join(self._dir, "organization.db")
        proc = subprocess.run(["bash", _INIT_SCRIPT, self.db],
                              capture_output=True, text=True)
        if proc.returncode != 0:
            self.fail(f"init failed: {proc.stderr or proc.stdout}")
        self._prev = os.environ.get("AGICTL_ORGANIZATION_DB")
        os.environ["AGICTL_ORGANIZATION_DB"] = self.db
        demo.seed()
        self.reader = OrganizationReader(self.db)
        self.writer = OrganizationWriter(self.db)

    def tearDown(self):
        if self._prev is None:
            os.environ.pop("AGICTL_ORGANIZATION_DB", None)
        else:
            os.environ["AGICTL_ORGANIZATION_DB"] = self._prev
        shutil.rmtree(self._dir, ignore_errors=True)

    async def test_action_bar_present_with_writer(self):
        """A writer-backed panel shows the New/Edit/Delete action buttons."""
        app = _HostApp(self.reader, self.writer)
        async with app.run_test() as pilot:
            await pilot.pause()
            for action in ("new", "edit", "delete"):
                app.query_one(f"#org-organizations-{action}", Button)

    async def test_form_modal_builds_one_widget_per_column(self):
        """The generic form renders a widget for every writable column, with a
        Checkbox for booleans."""
        app = _HostApp(self.reader, self.writer)
        async with app.run_test() as pilot:
            await pilot.pause()
            panel = app.query_one("#org-organizations-panel", OrganizationEntityPanel)
            panel._open_form(None)
            await pilot.pause()
            self.assertIsInstance(app.screen, EntityFormModal)
            for col in self.writer.spec("org")["columns"]:
                app.screen.query_one(f"#f-{col}")           # exists (any widget)
            self.assertIsInstance(app.screen.query_one("#f-is_active"), Checkbox)

    async def test_fk_column_renders_select(self):
        """A foreign-key column (product.org_id) renders a Select picklist."""
        app = _HostApp(self.reader, self.writer)
        async with app.run_test() as pilot:
            await pilot.pause()
            panel = app.query_one("#org-products-panel", OrganizationEntityPanel)
            panel._open_form(None)
            await pilot.pause()
            self.assertIsInstance(app.screen.query_one("#f-org_id"), Select)

    async def test_form_create_persists_to_db(self):
        """Filling the form and pressing Save inserts a row via the writer."""
        app = _HostApp(self.reader, self.writer)
        async with app.run_test() as pilot:
            await pilot.pause()
            before = self.reader.summary()["organizations"]
            panel = app.query_one("#org-organizations-panel", OrganizationEntityPanel)
            panel._open_form(None)
            await pilot.pause()
            modal = app.screen
            modal.query_one("#f-name", Input).value = "Brand New Co"
            modal.on_button_pressed(Button.Pressed(modal.query_one("#form-save", Button)))
            await pilot.pause()
            self.assertEqual(self.reader.summary()["organizations"], before + 1)

    async def test_checkbox_controls_boolean_value(self):
        """Unchecking is_active stores 0 (the checkbox drives the bool field)."""
        app = _HostApp(self.reader, self.writer)
        async with app.run_test() as pilot:
            await pilot.pause()
            panel = app.query_one("#org-organizations-panel", OrganizationEntityPanel)
            panel._open_form(None)
            await pilot.pause()
            modal = app.screen
            modal.query_one("#f-name", Input).value = "Inactive Co"
            modal.query_one("#f-is_active", Checkbox).value = False
            modal.on_button_pressed(Button.Pressed(modal.query_one("#form-save", Button)))
            await pilot.pause()
            row = next(o for o in self.reader.list_organizations()
                       if o["name"] == "Inactive Co")
            self.assertEqual(row["is_active"], 0)

    async def test_form_create_with_fk_select_persists(self):
        """Choosing an org in the org_id picklist creates a product under it."""
        app = _HostApp(self.reader, self.writer)
        async with app.run_test() as pilot:
            await pilot.pause()
            before = self.reader.summary()["products"]
            org = self.reader.list_organizations()[0]
            panel = app.query_one("#org-products-panel", OrganizationEntityPanel)
            panel._open_form(None)
            await pilot.pause()
            modal = app.screen
            modal.query_one("#f-org_id", Select).value = str(org["id"])
            modal.query_one("#f-name", Input).value = "Form Widget"
            modal.on_button_pressed(Button.Pressed(modal.query_one("#form-save", Button)))
            await pilot.pause()
            self.assertEqual(self.reader.summary()["products"], before + 1)

    async def test_tree_explorer_mounts_with_orgs(self):
        """The POC tree builds an org node per organization."""
        app = _HostApp(self.reader, self.writer)
        async with app.run_test() as pilot:
            await pilot.pause()
            tree = app.query_one("#org-tree", Tree)
            self.assertGreaterEqual(len(tree.root.children), 5)

    async def test_money_field_prefills_as_decimal(self):
        """Editing a product shows its cents price as an editable decimal."""
        prod = self.reader.list_products()[0]
        raw = self.writer.get("product", prod["id"])
        app = _HostApp(self.reader, self.writer)
        async with app.run_test() as pilot:
            await pilot.pause()
            panel = app.query_one("#org-products-panel", OrganizationEntityPanel)
            panel._open_form(str(prod["id"]))
            await pilot.pause()
            shown = app.screen.query_one("#f-unit_price_cents", Input).value
            self.assertEqual(shown, f"{raw['unit_price_cents'] / 100:.2f}")

    async def test_money_entry_saved_as_cents(self):
        """Typing 500.00 into a money field stores 50000 cents."""
        org = self.reader.list_organizations()[0]
        app = _HostApp(self.reader, self.writer)
        async with app.run_test() as pilot:
            await pilot.pause()
            panel = app.query_one("#org-products-panel", OrganizationEntityPanel)
            panel._open_form(None)
            await pilot.pause()
            modal = app.screen
            modal.query_one("#f-org_id", Select).value = str(org["id"])
            modal.query_one("#f-name", Input).value = "Priced Widget"
            modal.query_one("#f-unit_price_cents", Input).value = "500.00"
            modal.on_button_pressed(Button.Pressed(modal.query_one("#form-save", Button)))
            await pilot.pause()
            row = next(p for p in self.reader.list_products()
                       if p["name"] == "Priced Widget")
            self.assertEqual(self.writer.get("product", row["id"])["unit_price_cents"],
                             50000)

    async def test_converted_invoice_picklist_is_disabled(self):
        """The estimate→converted-invoice picklist is shown but not editable."""
        app = _HostApp(self.reader, self.writer)
        async with app.run_test() as pilot:
            await pilot.pause()
            panel = app.query_one("#org-estimates-panel", OrganizationEntityPanel)
            panel._open_form(None)
            await pilot.pause()
            sel = app.screen.query_one("#f-converted_to_invoice_id", Select)
            self.assertTrue(sel.disabled)

    async def test_organizations_table_has_logo_column(self):
        """The organizations table exposes a Logo column and shows the basename."""
        app = _HostApp(self.reader, self.writer)
        async with app.run_test() as pilot:
            await pilot.pause()
            table = app.query_one("#org-organizations-table", DataTable)
            headers = [str(c.label) for c in table.columns.values()]
            self.assertIn("Logo", headers)

    async def test_tree_caps_child_rows(self):
        """A group with more than the cap renders cap rows plus a '… more' leaf."""
        from agitop.panels.organization import _TREE_CHILD_CAP
        # Give one org many invoices to exceed the cap.
        org = self.reader.list_organizations()[0]
        for i in range(_TREE_CHILD_CAP + 5):
            self.writer.create("invoice", {"org_id": org["id"],
                                           "invoice_number": f"BULK-{i}",
                                           "total_cents": 100})
        app = _HostApp(self.reader, self.writer)
        async with app.run_test() as pilot:
            await pilot.pause()
            panel = app.query_one("#org-explorer-panel", OrganizationTreePanel)
            panel.refresh_data()
            await pilot.pause()
            org_node = next(n for n in panel._tree.root.children
                            if str(org["name"]) in str(n.label))
            inv_group = next(g for g in org_node.children
                             if "Invoices" in str(g.label))
            # cap rendered rows + 1 overflow note
            self.assertEqual(len(inv_group.children), _TREE_CHILD_CAP + 1)
            self.assertIn("more", str(inv_group.children[-1].label))


if __name__ == "__main__":
    unittest.main(verbosity=2)

