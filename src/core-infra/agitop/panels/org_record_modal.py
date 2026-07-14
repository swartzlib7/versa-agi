"""OrgRecordModal — tabbed per-record editor for organizations.

When a user clicks New or Edit on an Organization in the outer OrganizationModal,
this opens instead of the flat EntityFormModal. It has tabs for:
  General  — existing org fields (name, slug, type, notes, etc.)
  ✉ Emails — bridge via org_emails (link/create & link/unlink)
  📍 Addresses — bridge via org_addresses
  👤 Staff — bridge via org_staff (connection picklist from tasks.db)

Sub-tabs (Emails, Addresses, Staff) require the org to be saved first.
Staff entries get a sub-modal for associating addresses (org_staff_addresses).
"""

from __future__ import annotations

import re
from decimal import Decimal, ROUND_HALF_UP, InvalidOperation

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll, Horizontal
from textual.coordinate import Coordinate
from textual.screen import ModalScreen
from textual.widgets import (    TextArea,
    Button, Checkbox, DataTable, Input, Label, Select, Static, TabbedContent,
    TabPane,
)

from agitop.data import OrganizationReader
from agitop.panels.organization import (
    _FIELD_LABEL, _FORM_ORDER, _FK_REF, _FK_DISABLED, _FIELD_CHOICES,
    _PICKLIST_FIELDS, _BOOL_NEW_DEFAULT, _AUTO_SLUG, _AUTO_NUMBER_FIELDS,
    _FORM_DISABLED, _FORM_AUTO_ON_CREATE, _MONEY_EG, _LINES_MANAGED,
    _fk_options, _slugify, _unique_slug, _cents_to_input, _input_to_cents,
    _date, _yn,
    EntityFormModal,
)


# ═══════════════════════════════════════════════════════════════════════
# CSS
# ═══════════════════════════════════════════════════════════════════════
_ORG_REC_CSS = """
OrgRecordModal {
    align: center middle;
    background: $surface 80%;
}
#org-rec-dialog {
    width: 130;
    height: 72%;
    padding: 1 2;
    border: thick $accent;
    background: $surface;
}
#org-rec-title {
    height: auto;
    border-bottom: solid $accent;
    margin-bottom: 1;
    content-align: center middle;
}

/* ── Tab bar styling (matches outer org-modal-tabs) ── */
#org-rec-tabs {
    height: 1fr;
    min-height: 0;
}
#org-rec-tabs Tabs {
    height: 4;
    overflow: auto hidden;
    background: $surface-darken-1;
    border-bottom: solid $surface-lighten-1;
}
#org-rec-tabs Tab {
    height: 3;
    padding: 0 4;
    margin: 0 1;
    border: solid $surface-lighten-2;
    background: $surface-lighten-2;
    color: $text-muted;
}
#org-rec-tabs Tab:hover {
    background: $surface-lighten-1;
    color: $text;
}
#org-rec-tabs #--content-tab-org-rec-general-tab { border-top: heavy $accent; }
#org-rec-tabs #--content-tab-org-rec-general-tab.-active {
    background: $accent 25%;
    color: $accent;
    border: heavy $accent;
    text-style: bold;
}
#org-rec-tabs #--content-tab-org-rec-emails-tab { border-top: heavy magenta; }
#org-rec-tabs #--content-tab-org-rec-emails-tab.-active {
    background: magenta 25%;
    color: magenta;
    border: heavy magenta;
    text-style: bold;
}
#org-rec-tabs #--content-tab-org-rec-addresses-tab { border-top: heavy darkorange; }
#org-rec-tabs #--content-tab-org-rec-addresses-tab.-active {
    background: darkorange 25%;
    color: darkorange;
    border: heavy darkorange;
    text-style: bold;
}
#org-rec-tabs #--content-tab-org-rec-staff-tab { border-top: heavy $success; }
#org-rec-tabs #--content-tab-org-rec-staff-tab.-active {
    background: $success 25%;
    color: $text-success;
    border: heavy $success;
    text-style: bold;
}
#org-rec-tabs ContentSwitcher {
    height: 1fr;
    min-height: 0;
}
#org-rec-tabs TabPane {
    height: 100%;
    min-height: 0;
    padding: 0;
}

/* ── General tab ── */
.org-rec-general-fields {
    height: auto;
    max-height: 32;
}
.org-rec-grid-row {
    height: auto;
    layout: grid;
    grid-size: 2;
    grid-gutter: 0 3;
    grid-rows: auto;
}
.org-rec-full-width { height: auto; }
.org-rec-notes-area { height: 7; width: 100%; }
.org-rec-form-field { height: auto; padding: 0; }
.org-rec-form-field Input, .org-rec-form-field Select, .org-rec-form-field Checkbox { width: 100%; }
.org-rec-form-label { margin-top: 1; color: $text-muted; }
#org-rec-error { color: $error; height: auto; }

/* ── Footer ── */
.org-rec-footer {
    margin-top: 1;
    height: auto;
    align: center middle;
}
.org-rec-footer Button { width: 1fr; height: 3; margin: 0 1; }

/* ── Bridge panels (emails / addresses / staff sub-tables) ── */
.bridge-table {
    height: 1fr;
    min-height: 8;
}
#org-rec-emails-table    { border: solid magenta; }
#org-rec-addresses-table { border: solid darkorange; }
#org-rec-staff-table     { border: solid $success; }
.bridge-actions {
    height: auto;
    margin-top: 1;
    align: center middle;
}
.bridge-actions Button { width: 1fr; height: 3; margin: 0 1; }
.bridge-placeholder { height: auto; margin: 2; color: $text-muted; text-align: center; }
"""


# ═══════════════════════════════════════════════════════════════════════
# OrgRecordModal
# ═══════════════════════════════════════════════════════════════════════

class OrgRecordModal(ModalScreen):
    """Tabbed per-record editor for an organization."""

    CSS = _ORG_REC_CSS
    BINDINGS = [Binding("escape", "close", "Close")]

    def __init__(self, writer, reader: OrganizationReader,
                 tasks_reader=None, agent_reader=None, config=None,
                 row: dict | None = None, **kwargs):
        super().__init__(**kwargs)
        self.writer = writer
        self.reader = reader
        self.tasks_reader = tasks_reader
        self.agent_reader = agent_reader
        self.config = config
        self.row = row or {}
        self.org_id: int | None = self.row.get("id")
        self.spec = writer.spec("org")
        self._widgets: dict[str, object] = {}
        self._result = None  # last write result to return on dismiss

    def compose(self) -> ComposeResult:
        is_new = self.org_id is None
        title = "New Organization" if is_new else f"Edit: {self.row.get('name', '—')}"
        with Vertical(id="org-rec-dialog"):
            yield Static(f"[bold]{title}[/]", id="org-rec-title")
            with TabbedContent(id="org-rec-tabs"):
                with TabPane("General", id="org-rec-general-tab"):
                    yield from self._general_tab()
                with TabPane("✉  Emails", id="org-rec-emails-tab"):
                    if is_new:
                        yield Static("Save the organization first to manage emails.",
                                     classes="bridge-placeholder")
                    else:
                        yield OrgBridgePanel(
                            self.writer, self.reader, self.org_id,
                            bridge_kind="emails", id="org-rec-emails-panel")
                with TabPane("📍 Addresses", id="org-rec-addresses-tab"):
                    if is_new:
                        yield Static("Save the organization first to manage addresses.",
                                     classes="bridge-placeholder")
                    else:
                        yield OrgBridgePanel(
                            self.writer, self.reader, self.org_id,
                            bridge_kind="addresses", id="org-rec-addresses-panel")
                with TabPane("👤 Staff", id="org-rec-staff-tab"):
                    if is_new:
                        yield Static("Save the organization first to manage staff.",
                                     classes="bridge-placeholder")
                    else:
                        yield OrgStaffPanel(
                            self.writer, self.reader, self.org_id,
                            tasks_reader=self.tasks_reader,
                            agent_reader=self.agent_reader,
                            config=self.config,
                            id="org-rec-staff-panel")
            yield Static("", id="org-rec-error")
            with Horizontal(classes="org-rec-footer"):
                yield Button("Save", variant="success", id="org-rec-save")
                yield Button("Close", classes="dismiss-btn", variant="default",
                             id="org-rec-close")

    # ── General tab field generation ──

    def _general_tab(self):
        """Yield the form fields for the General tab.

        Layout:
          name       | type
          slug       | external_id
          logo_path  | is_active
          notes (full width, 5 lines TextArea)
        """
        grid_pairs = [
            ("name", "type"),
            ("slug", "external_id"),
            ("logo_path", "is_active"),
        ]
        with VerticalScroll(classes="org-rec-general-fields"):
            for left, right in grid_pairs:
                with Horizontal(classes="org-rec-grid-row"):
                    with Vertical(classes="org-rec-form-field"):
                        yield self._label(left)
                        yield self._widget(left)
                    with Vertical(classes="org-rec-form-field"):
                        yield self._label(right)
                        yield self._widget(right)
            # Notes — full width TextArea at the bottom
            with Vertical(classes="org-rec-full-width"):
                yield self._label("notes")
                yield self._notes_widget()

    def _is_disabled(self, col: str) -> bool:
        auto = _AUTO_SLUG.get("org")
        return (("org", col) in _FK_DISABLED
                or ("org", col) in _FORM_DISABLED
                or ("org", col) in _AUTO_NUMBER_FIELDS
                or (auto is not None and col == auto[0]))

    def _label(self, col: str) -> Label:
        s = self.spec
        req = "  [red]*[/]" if col in s["required"] else ""
        auto = _AUTO_SLUG.get("org")
        if auto is not None and col == auto[0]:
            hint = "  [dim](auto from name)[/]"
        elif ("org", col) in _AUTO_NUMBER_FIELDS:
            hint = "  [dim](auto-numbered)[/]"
        elif ("org", col) in _LINES_MANAGED:
            hint = "  [dim](set in Lines)[/]"
        elif self._is_disabled(col):
            hint = "  [dim](system-set)[/]"
        elif col in s.get("bool", []):
            hint = ""
        elif (("org", col) in _FK_REF or ("org", col) in _FIELD_CHOICES
              or ("org", col) in _PICKLIST_FIELDS):
            hint = "  [dim](pick)[/]"
        elif col in s["money"]:
            eg = _MONEY_EG.get(("org", col), "500.00")
            hint = f"  [dim]($ e.g. {eg})[/]"
        elif col in s["int"]:
            hint = "  [dim](integer)[/]"
        elif col in s["real"]:
            hint = "  [dim](number)[/]"
        else:
            hint = ""
        display = _FIELD_LABEL.get(col) or col.replace("_", " ").title()
        return Label(f"{display}{req}{hint}", classes="org-rec-form-label")

    def _widget(self, col: str):
        s = self.spec
        val = self.row.get(col)
        disabled = self._is_disabled(col)
        ref = _FK_REF.get(("org", col))
        choices = _FIELD_CHOICES.get(("org", col))
        pick = _PICKLIST_FIELDS.get(("org", col))
        if col in s.get("bool", []):
            default = bool(val) if val is not None else _BOOL_NEW_DEFAULT.get(col, False)
            w = Checkbox("yes", value=default, disabled=disabled, id=f"f-{col}")
        elif ref or choices or pick:
            if choices:
                options = list(choices)
            elif pick:
                options = self.reader.picklist_options(*pick)
            else:
                options = _fk_options(self.reader, ref)
            current = "" if val is None else str(val)
            option_values = [v for _lbl, v in options]
            if current and current not in option_values:
                options = [(f"{current}  (current)", current), *options]
                option_values.append(current)
            if current and current in option_values:
                w = Select(options, value=current, prompt="— choose —",
                           disabled=disabled, id=f"f-{col}")
            else:
                w = Select(options, prompt="— choose —", disabled=disabled,
                           id=f"f-{col}")
        elif col in s["money"]:
            eg = _MONEY_EG.get(("org", col), "0.00")
            w = Input(value=_cents_to_input(val), placeholder=eg,
                      disabled=disabled, id=f"f-{col}")
        else:
            w = Input(value="" if val is None else str(val),
                      disabled=disabled, id=f"f-{col}")
        self._widgets[col] = w
        return w

    def _notes_widget(self):
        """Create a TextArea for the notes field (full width, ~5 lines)."""
        val = self.row.get("notes") or ""
        w = TextArea(val, id="f-notes", classes="org-rec-notes-area")
        self._widgets["notes"] = w
        return w

    # ── Save / Close ──

    def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id or ""
        if bid == "org-rec-close":
            self.dismiss(self._result)
            return
        if bid == "org-rec-save":
            self._save_general()
            return

    def _save_general(self) -> None:
        """Gather fields from the General tab and save."""
        money = set(self.spec["money"])
        fields: dict = {}
        for col, w in self._widgets.items():
            if getattr(w, "disabled", False):
                continue
            if isinstance(w, Checkbox):
                fields[col] = 1 if w.value else 0
            elif isinstance(w, Select):
                v = w.value
                if isinstance(v, str) and v:
                    fields[col] = v
            elif isinstance(w, TextArea):
                v = w.text.strip()
                if v:
                    fields[col] = v
            else:
                v = w.value.strip()
                if v == "":
                    continue
                if col in money:
                    try:
                        fields[col] = _input_to_cents(v)
                    except ValueError:
                        self.query_one("#org-rec-error", Static).update(
                            f"[red]{col}: enter an amount like 500.00[/]")
                        return
                else:
                    fields[col] = v

        if not self.row:  # new org
            for (ent, col), value in _FORM_AUTO_ON_CREATE.items():
                if ent == "org":
                    fields[col] = value
            auto = _AUTO_SLUG.get("org")
            if auto is not None:
                slug_col, src_col = auto
                base = _slugify(fields.get(src_col, ""))
                existing = {o.get("slug") for o in self.reader.list_organizations()
                            if o.get("slug")}
                fields[slug_col] = _unique_slug(base, existing)

        if self.row:
            result = self.writer.update("org", self.row["id"], fields)
        else:
            result = self.writer.create("org", fields)

        if result.get("success"):
            self._result = result
            # Update org_id for sub-tabs if this was a new org
            if not self.org_id and result.get("id"):
                self.org_id = result["id"]
                self.row = self.writer.get("org", self.org_id) or {}
                self._activate_subtabs()
            elif self.org_id:
                self.row = self.writer.get("org", self.org_id) or self.row
            # Update title
            name = self.row.get("name", "—")
            self.query_one("#org-rec-title", Static).update(f"[bold]Edit: {name}[/]")
            self.query_one("#org-rec-error", Static).update(
                f"[green]Saved (id {result.get('id')})[/]")
        else:
            self.query_one("#org-rec-error", Static).update(
                f"[red]{result.get('error', 'write failed')}[/]")

    def _activate_subtabs(self) -> None:
        """Replace placeholder text with live bridge panels after first save."""
        tabs = self.query_one("#org-rec-tabs", TabbedContent)
        # Emails
        try:
            pane = self.query_one("#org-rec-emails-tab", TabPane)
            for child in list(pane.children):
                child.remove()
            panel = OrgBridgePanel(
                self.writer, self.reader, self.org_id,
                bridge_kind="emails", id="org-rec-emails-panel")
            pane.mount(panel)
        except Exception:
            pass
        # Addresses
        try:
            pane = self.query_one("#org-rec-addresses-tab", TabPane)
            for child in list(pane.children):
                child.remove()
            panel = OrgBridgePanel(
                self.writer, self.reader, self.org_id,
                bridge_kind="addresses", id="org-rec-addresses-panel")
            pane.mount(panel)
        except Exception:
            pass
        # Staff
        try:
            pane = self.query_one("#org-rec-staff-tab", TabPane)
            for child in list(pane.children):
                child.remove()
            panel = OrgStaffPanel(
                self.writer, self.reader, self.org_id,
                tasks_reader=self.tasks_reader, id="org-rec-staff-panel")
            pane.mount(panel)
        except Exception:
            pass

    def action_close(self) -> None:
        self.dismiss(self._result)


# ═══════════════════════════════════════════════════════════════════════
# OrgBridgePanel — generic bridge list for emails / addresses
# ═══════════════════════════════════════════════════════════════════════

_BRIDGE_CONF = {
    "emails": {
        "label": "Emails",
        "bridge_entity": "org-email",
        "record_entity": "email",
        "record_kind": "emails",
        "bridge_fk": "email_id",
        "columns": [("Email", 28), ("Label", 12), ("Primary", 8),
                    ("Notes", 16), ("Credential", 12)],
        "list_method": "list_org_emails",
        "unlinked_method": "list_unlinked_emails",
        "pick_label": lambda r: f"{r.get('email', '—')}  ({r.get('label') or 'no label'})",
    },
    "addresses": {
        "label": "Addresses",
        "bridge_entity": "org-address",
        "record_entity": "address",
        "record_kind": "addresses",
        "bridge_fk": "address_id",
        "columns": [("Line 1", 20), ("City", 12), ("State", 8),
                    ("Postal", 10), ("Country", 10), ("Label", 10), ("Primary", 8)],
        "list_method": "list_org_addresses",
        "unlinked_method": "list_unlinked_addresses",
        "pick_label": lambda r: (
            f"{r.get('line_1', '—')}, {r.get('city', '—')}"
            f"  ({r.get('state', '')})"),
    },
}


class OrgBridgePanel(Vertical):
    """Bridge-list panel for emails or addresses linked to an org."""

    def __init__(self, writer, reader: OrganizationReader, org_id: int,
                 bridge_kind: str, **kwargs):
        super().__init__(**kwargs)
        self.writer = writer
        self.reader = reader
        self.org_id = org_id
        self.conf = _BRIDGE_CONF[bridge_kind]
        self.bridge_kind = bridge_kind
        self.table = DataTable(id=f"org-rec-{bridge_kind}-table",
                               cursor_type="row", classes="bridge-table")
        self._rows: dict[str, dict] = {}

    def compose(self) -> ComposeResult:
        yield Static(f"[bold]{self.conf['label']} linked to this organization[/]")
        yield self.table
        with Horizontal(classes="bridge-actions"):
            yield Button("🔗 Link Existing", variant="primary",
                         id=f"bridge-{self.bridge_kind}-link")
            yield Button("＋ Create & Link", variant="success",
                         id=f"bridge-{self.bridge_kind}-create")
            yield Button("⊘ Unlink", variant="error",
                         id=f"bridge-{self.bridge_kind}-unlink")

    def on_mount(self) -> None:
        self.table.cursor_type = "row"
        for header, width in self.conf["columns"]:
            self.table.add_column(header, width=width)
        self._refresh()

    def _refresh(self) -> None:
        self.table.clear()
        self._rows.clear()
        rows = getattr(self.reader, self.conf["list_method"])(self.org_id)
        for r in rows:
            rid = str(r["id"])
            self._rows[rid] = r
            vals = self._row_values(r)
            self.table.add_row(*vals, key=rid)
        label = self.conf["label"]
        self.table.border_title = f"{label} ({len(rows)})"

    def _row_values(self, r: dict) -> list[str]:
        if self.bridge_kind == "emails":
            return [
                r.get("email") or "—",
                r.get("label") or "—",
                _yn(r.get("is_primary")),
                r.get("usage_notes") or "—",
                r.get("credential_type") or "—",
            ]
        else:  # addresses
            return [
                r.get("line_1") or "—",
                r.get("city") or "—",
                r.get("state") or "—",
                r.get("postal_code") or "—",
                r.get("country") or "—",
                r.get("label") or "—",
                _yn(r.get("is_primary")),
            ]

    def _current_rid(self) -> str | None:
        if self.table.row_count == 0:
            return None
        try:
            key = self.table.coordinate_to_cell_key(
                Coordinate(self.table.cursor_row, 0)).row_key
            return key.value
        except Exception:
            return None

    def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id or ""
        if bid.endswith("-link"):
            event.stop()
            self._link_existing()
        elif bid.endswith("-create"):
            event.stop()
            self._create_and_link()
        elif bid.endswith("-unlink"):
            event.stop()
            self._unlink()

    def _link_existing(self) -> None:
        """Open a selection picklist of unlinked records."""
        unlinked = getattr(self.reader, self.conf["unlinked_method"])(self.org_id)
        if not unlinked:
            self.app.notify("No unlinked records available", severity="warning")
            return
        pick_label = self.conf["pick_label"]
        options = [(pick_label(r), str(r["id"])) for r in unlinked]
        self.app.push_screen(
            _BridgePickModal(options, self.conf["label"]),
            self._on_link_picked,
        )

    def _on_link_picked(self, record_id: str | None) -> None:
        if not record_id:
            return
        bridge_entity = self.conf["bridge_entity"]
        bridge_fk = self.conf["bridge_fk"]
        result = self.writer.create(bridge_entity, {
            "org_id": self.org_id,
            bridge_fk: int(record_id),
        })
        if result.get("success"):
            self._refresh()
            self.app.notify(f"Linked {self.conf['label'].rstrip('s').lower()}")
        else:
            self.app.notify(result.get("error", "Link failed"), severity="error")

    def _create_and_link(self) -> None:
        """Open EntityFormModal for the record type, then auto-link on success."""
        entity = self.conf["record_entity"]
        kind = self.conf["record_kind"]
        self.app.push_screen(
            EntityFormModal(self.writer, self.reader, entity, kind, row=None),
            self._on_created,
        )

    def _on_created(self, result) -> None:
        if not result or not result.get("success"):
            return
        record_id = result.get("id")
        if not record_id:
            return
        bridge_entity = self.conf["bridge_entity"]
        bridge_fk = self.conf["bridge_fk"]
        self.writer.create(bridge_entity, {
            "org_id": self.org_id,
            bridge_fk: int(record_id),
        })
        self._refresh()
        self.app.notify(f"Created & linked {self.conf['label'].rstrip('s').lower()}")

    def _unlink(self) -> None:
        """Remove the bridge row (not the record itself)."""
        rid = self._current_rid()
        if not rid:
            self.app.bell()
            return
        row = self._rows.get(rid)
        if not row:
            return
        bridge_id = row.get("bridge_id")
        if not bridge_id:
            self.app.notify("No bridge record found", severity="error")
            return
        bridge_entity = self.conf["bridge_entity"]
        result = self.writer.delete(bridge_entity, int(bridge_id))
        if result.get("success"):
            self._refresh()
            self.app.notify(f"Unlinked {self.conf['label'].rstrip('s').lower()}")
        else:
            self.app.notify(result.get("error", "Unlink failed"), severity="error")


# ═══════════════════════════════════════════════════════════════════════
# OrgStaffPanel — staff bridge with connection picklist
# ═══════════════════════════════════════════════════════════════════════

class OrgStaffPanel(Vertical):
    """Staff linked to an org via org_staff, with connections from tasks.db."""

    def __init__(self, writer, reader: OrganizationReader, org_id: int,
                 tasks_reader=None, agent_reader=None, config=None, **kwargs):
        super().__init__(**kwargs)
        self.writer = writer
        self.reader = reader
        self.org_id = org_id
        self.tasks_reader = tasks_reader
        self.agent_reader = agent_reader
        self.config = config
        self.table = DataTable(id="org-rec-staff-table", cursor_type="row",
                               classes="bridge-table")
        self._rows: dict[str, dict] = {}
        self._conn_map: dict[str, dict] = {}  # uid → full connection dict

    def compose(self) -> ComposeResult:
        yield Static("[bold]Staff linked to this organization[/]")
        yield self.table
        with Horizontal(classes="bridge-actions"):
            yield Button("🔗 Link Contact", variant="primary",
                         id="staff-link")
            yield Button("⊘ Unlink", variant="error",
                         id="staff-unlink")

    def on_mount(self) -> None:
        self.table.cursor_type = "row"
        for header, width in [("Contact", 24), ("Relationship", 14),
                               ("Language", 10), ("DOB", 12), ("Since", 12)]:
            self.table.add_column(header, width=width)
        self._load_connections()
        self._refresh()

    def _load_connections(self) -> None:
        """Cache connections from tasks.db (excluding agents) + Primary User if absent."""
        self._conn_map.clear()
        agent_uids: set[str] = set()
        if self.agent_reader:
            try:
                agent_uids = self.agent_reader.get_agent_sub_account_uids()
            except Exception:
                pass
        if self.tasks_reader:
            try:
                for c in self.tasks_reader.get_connections():
                    uid = c.get("uid") or ""
                    if uid in agent_uids:
                        continue
                    self._conn_map[c["uid"]] = c
            except Exception:
                pass
        self._inject_primary_user(agent_uids)

    def _inject_primary_user(self, agent_uids: set[str]) -> None:
        """Include Primary User from config when not already in connections (ORG-UI-STAFF-4)."""
        if not self.config:
            return
        try:
            pu = self.config.get_config().get("primary_user") or {}
        except Exception:
            return
        uid = pu.get("uid") or ""
        if not uid or uid in self._conn_map or uid in agent_uids:
            return
        self._conn_map[uid] = {
            "uid": uid,
            "display_name": pu.get("display_name") or "Primary User",
            "spoken_lang": pu.get("spokenLanguage") or "",
            "relationship": "Primary User",
            "date_of_birth": None,
            "first_seen": None,
        }

    @staticmethod
    def _pick_label(conn: dict, name_counts: dict[str, int]) -> str:
        """Picklist label; when display_name collides, append uid suffix + first_seen."""
        name = conn.get("display_name") or "—"
        rel = conn.get("relationship") or "—"
        label = f"{name}  ({rel})"
        if name_counts.get(name, 0) <= 1:
            return label
        uid = conn.get("uid") or ""
        suffix = uid[-6:] if len(uid) >= 6 else uid
        first = str(conn.get("first_seen") or "")[:10]
        if first:
            return f"{label} · …{suffix} · {first}"
        return f"{label} · …{suffix}"

    def _refresh(self) -> None:
        self.table.clear()
        self._rows.clear()
        rows = self.reader.list_org_staff(self.org_id)
        for s in rows:
            rid = str(s["id"])
            self._rows[rid] = s
            uid = s.get("connection_uid") or ""
            conn = self._conn_map.get(uid, {})
            self.table.add_row(
                conn.get("display_name", uid) or "—",
                conn.get("relationship") or "—",
                conn.get("spoken_lang") or "—",
                conn.get("date_of_birth") or "—",
                _date(s.get("created_at")),
                key=rid,
            )
        self.table.border_title = f"Staff ({len(rows)})"

    def _current_rid(self) -> str | None:
        if self.table.row_count == 0:
            return None
        try:
            key = self.table.coordinate_to_cell_key(
                Coordinate(self.table.cursor_row, 0)).row_key
            return key.value
        except Exception:
            return None

    def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id or ""
        if bid == "staff-link":
            event.stop()
            self._link_contact()
        elif bid == "staff-unlink":
            event.stop()
            self._unlink()

    def _link_contact(self) -> None:
        """Open a picklist of connections from tasks.db (+ Primary User if injected)."""
        linked_uids = {s.get("connection_uid") for s in self._rows.values()}
        available = [
            c for c in self._conn_map.values()
            if c["uid"] not in linked_uids
        ]
        if not available:
            self.app.notify("No unlinked contacts available", severity="warning")
            return
        name_counts: dict[str, int] = {}
        for c in available:
            name = c.get("display_name") or "—"
            name_counts[name] = name_counts.get(name, 0) + 1
        options = [
            (self._pick_label(c, name_counts), c["uid"])
            for c in sorted(available, key=lambda x: x.get("display_name") or "")
        ]
        self.app.push_screen(
            _BridgePickModal(options, "Contact"),
            self._on_contact_picked,
        )

    def _on_contact_picked(self, uid: str | None) -> None:
        if not uid:
            return
        result = self.writer.create("org-staff", {
            "org_id": self.org_id,
            "connection_uid": uid,
        })
        if result.get("success"):
            self._refresh()
            name = self._conn_map.get(uid, {}).get("display_name", uid)
            self.app.notify(f"Linked {name}")
        else:
            self.app.notify(result.get("error", "Link failed"), severity="error")

    def _open_staff_addresses(self) -> None:
        """Open the staff-address sub-modal for the selected staff record."""
        rid = self._current_rid()
        if not rid:
            self.app.bell()
            return
        row = self._rows.get(rid)
        if not row:
            return
        uid = row.get("connection_uid") or ""
        conn = self._conn_map.get(uid, {})
        name = conn.get("display_name", uid)
        self.app.push_screen(
            StaffAddressModal(
                self.writer, self.reader, int(rid), name),
        )

    def _unlink(self) -> None:
        rid = self._current_rid()
        if not rid:
            self.app.bell()
            return
        result = self.writer.delete("org-staff", int(rid))
        if result.get("success"):
            self._refresh()
            self.app.notify("Staff unlinked")
        else:
            self.app.notify(result.get("error", "Unlink failed"), severity="error")


# ═══════════════════════════════════════════════════════════════════════
# StaffAddressModal — sub-modal for staff ↔ address bridge
# ═══════════════════════════════════════════════════════════════════════

_STAFF_ADDR_CSS = """
StaffAddressModal {
    align: center middle;
    background: $surface 80%;
}
#staff-addr-dialog {
    width: 110;
    height: auto;
    max-height: 70%;
    padding: 1 2;
    border: thick $accent;
    background: $surface;
}
#staff-addr-title { height: auto; border-bottom: solid $accent; margin-bottom: 1; }
"""


class StaffAddressModal(ModalScreen):
    """Sub-modal for linking addresses to a staff member via org_staff_addresses."""

    CSS = _STAFF_ADDR_CSS
    BINDINGS = [Binding("escape", "close", "Close")]

    def __init__(self, writer, reader: OrganizationReader,
                 org_staff_id: int, staff_name: str, **kwargs):
        super().__init__(**kwargs)
        self.writer = writer
        self.reader = reader
        self.org_staff_id = org_staff_id
        self.staff_name = staff_name
        self.table = DataTable(id="staff-addr-table", cursor_type="row",
                               classes="bridge-table")
        self._rows: dict[str, dict] = {}

    def compose(self) -> ComposeResult:
        with Vertical(id="staff-addr-dialog"):
            yield Static(f"[bold]Addresses — {self.staff_name}[/]",
                         id="staff-addr-title")
            yield self.table
            with Horizontal(classes="bridge-actions"):
                yield Button("🔗 Link Existing", variant="primary",
                             id="staff-addr-link")
                yield Button("＋ Create & Link", variant="success",
                             id="staff-addr-create")
                yield Button("⊘ Unlink", variant="error",
                             id="staff-addr-unlink")
                yield Button("Close", classes="dismiss-btn", variant="default",
                             id="staff-addr-close")

    def on_mount(self) -> None:
        self.table.cursor_type = "row"
        for header, width in [("Line 1", 20), ("City", 12), ("State", 8),
                               ("Postal", 10), ("Country", 10), ("Label", 10),
                               ("Primary", 8)]:
            self.table.add_column(header, width=width)
        self._refresh()

    def _refresh(self) -> None:
        self.table.clear()
        self._rows.clear()
        rows = self.reader.list_staff_addresses(self.org_staff_id)
        for a in rows:
            rid = str(a["id"])
            self._rows[rid] = a
            self.table.add_row(
                rid,
                a.get("line_1") or "—", a.get("city") or "—",
                a.get("state") or "—", a.get("postal_code") or "—",
                a.get("country") or "—", a.get("label") or "—",
                _yn(a.get("is_primary")),
                key=rid,
            )
        self.table.border_title = f"Addresses ({len(rows)})"

    def _current_rid(self) -> str | None:
        if self.table.row_count == 0:
            return None
        try:
            key = self.table.coordinate_to_cell_key(
                Coordinate(self.table.cursor_row, 0)).row_key
            return key.value
        except Exception:
            return None

    def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id or ""
        if bid == "staff-addr-close":
            self.dismiss(None)
        elif bid == "staff-addr-link":
            event.stop()
            self._link_existing()
        elif bid == "staff-addr-create":
            event.stop()
            self._create_and_link()
        elif bid == "staff-addr-unlink":
            event.stop()
            self._unlink()

    def _link_existing(self) -> None:
        unlinked = self.reader.list_unlinked_addresses_for_staff(self.org_staff_id)
        if not unlinked:
            self.app.notify("No unlinked addresses available", severity="warning")
            return
        options = [
            (f"{a.get('line_1', '—')}, {a.get('city', '—')}  ({a.get('state', '')})",
             str(a["id"]))
            for a in unlinked
        ]
        self.app.push_screen(
            _BridgePickModal(options, "Address"),
            self._on_link_picked,
        )

    def _on_link_picked(self, address_id: str | None) -> None:
        if not address_id:
            return
        result = self.writer.create("org-staff-address", {
            "org_staff_id": self.org_staff_id,
            "address_id": int(address_id),
        })
        if result.get("success"):
            self._refresh()
            self.app.notify("Address linked")
        else:
            self.app.notify(result.get("error", "Link failed"), severity="error")

    def _create_and_link(self) -> None:
        self.app.push_screen(
            EntityFormModal(self.writer, self.reader, "address", "addresses",
                            row=None),
            self._on_created,
        )

    def _on_created(self, result) -> None:
        if not result or not result.get("success"):
            return
        address_id = result.get("id")
        if not address_id:
            return
        self.writer.create("org-staff-address", {
            "org_staff_id": self.org_staff_id,
            "address_id": int(address_id),
        })
        self._refresh()
        self.app.notify("Address created & linked")

    def _unlink(self) -> None:
        rid = self._current_rid()
        if not rid:
            self.app.bell()
            return
        row = self._rows.get(rid)
        if not row:
            return
        bridge_id = row.get("bridge_id")
        if not bridge_id:
            self.app.notify("No bridge record found", severity="error")
            return
        result = self.writer.delete("org-staff-address", int(bridge_id))
        if result.get("success"):
            self._refresh()
            self.app.notify("Address unlinked")
        else:
            self.app.notify(result.get("error", "Unlink failed"), severity="error")

    def action_close(self) -> None:
        self.dismiss(None)


# ═══════════════════════════════════════════════════════════════════════
# _BridgePickModal — reusable selection picklist for bridge linking
# ═══════════════════════════════════════════════════════════════════════

_PICK_CSS = """
_BridgePickModal {
    align: center middle;
    background: $surface 80%;
}
#bridge-pick-dialog {
    width: 80;
    height: auto;
    max-height: 60%;
    padding: 1 2;
    border: thick $accent;
    background: $surface;
}
#bridge-pick-title { height: auto; border-bottom: solid $accent; margin-bottom: 1; }
.bridge-pick-actions { height: auto; margin-top: 1; border-top: solid $accent;
    padding-top: 1; align: center middle; }
.bridge-pick-actions Button { width: 1fr; height: 3; margin: 0 1; }
"""


class _BridgePickModal(ModalScreen):
    """Simple modal with a Select picklist. Returns the selected value on Link."""

    CSS = _PICK_CSS
    BINDINGS = [Binding("escape", "close", "Close")]

    def __init__(self, options: list[tuple[str, str]], label: str, **kwargs):
        super().__init__(**kwargs)
        self.options = options
        self.label = label

    def compose(self) -> ComposeResult:
        with Vertical(id="bridge-pick-dialog"):
            yield Static(f"[bold]Link {self.label}[/]", id="bridge-pick-title")
            yield Select(self.options, prompt=f"— select {self.label.lower()} —",
                         id="bridge-pick-select")
            with Horizontal(classes="bridge-pick-actions"):
                yield Button("Link", variant="success", id="bridge-pick-ok")
                yield Button("Cancel", classes="dismiss-btn", variant="default",
                             id="bridge-pick-cancel")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "bridge-pick-cancel":
            self.dismiss(None)
        elif event.button.id == "bridge-pick-ok":
            sel = self.query_one("#bridge-pick-select", Select)
            v = sel.value
            if isinstance(v, str) and v:
                self.dismiss(v)
            else:
                self.app.bell()

    def action_close(self) -> None:
        self.dismiss(None)
