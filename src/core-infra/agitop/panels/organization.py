"""Organization panel for agitop (Deliverables D25, D26).

A **read-only** dashboard surface over the Organization domain, per TS-11: it
visualises (it does not author — authoring stays in `agictl`/STEWART). All data
comes through :class:`OrganizationReader` (no raw SQL here). The whole surface is
gated by ``feature_flags.ORGANIZATION_UI_VISIBLE`` — when False the panel is not
mounted at all (see app.py).

Layout:
  * Each entity is its own top-level agitop tab (no nested sub-tab UI):
    Organizations · Products · Estimates · Invoices · Transactions · Exchange.
    Counts and sync health live in each table's border title.
  * Use the ❖ Explorer tab to drill into a row's related records.

Digital Silk colours: green = healthy/sent/sync-done, amber = pending/draft,
red = failed/overdue, dim(gray) = closed/inactive.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from decimal import Decimal, ROUND_HALF_UP, InvalidOperation

from textual import on
from textual.app import ComposeResult
from textual.screen import ModalScreen
from textual.containers import Vertical, VerticalScroll, Horizontal
from textual.coordinate import Coordinate
from textual.widget import Widget
from textual.widgets import DataTable, Static, Button, Input, Label, Tree, Checkbox, Select, TextArea

from agitop.data import OrganizationReader
from agitop.data.organization_reader import format_money


# Semantic colour bucket → Textual markup colour (Digital Silk).
_COLOUR = {"green": "green", "amber": "yellow", "red": "red", "gray": "dim"}


def _c(bucket: str | None, text) -> str:
    """Wrap text in the markup colour for a semantic bucket."""
    colour = _COLOUR.get(bucket or "", "white")
    return f"[{colour}]{text}[/]"


def _yn(value) -> str:
    return "yes" if value else "no"


def _date(value) -> str:
    """Trim a datetime/date string to its YYYY-MM-DD prefix for compact tables."""
    if not value:
        return "—"
    return str(value)[:10]


def _cents_to_input(cents) -> str:
    """Render integer cents as an editable decimal string: 50000 → '500.00'."""
    if cents in (None, ""):
        return ""
    try:
        return f"{int(cents) / 100:.2f}"
    except (TypeError, ValueError):
        return str(cents)


def _input_to_cents(text: str) -> int:
    """Parse a money entry ('500', '500.00', '$1,250.5') to integer cents.

    Half-up rounding (accounting convention, per §9.1) on the entered decimal.
    Raises ValueError on anything that isn't a money amount.
    """
    cleaned = text.strip().replace("$", "").replace(",", "")
    if cleaned == "":
        raise ValueError("empty")
    try:
        return int((Decimal(cleaned) * 100).quantize(Decimal("1"),
                                                      rounding=ROUND_HALF_UP))
    except (InvalidOperation, ValueError):
        raise ValueError(f"{text!r} is not a money amount")


def _line_total_cents(quantity, unit_price_cents) -> int:
    """Exact integer cents for a line: round(quantity × unit_price_cents).

    Quantity is REAL (fractional units are real); the product is collapsed to
    whole cents with half-up rounding *before* storage, so no float lands in a
    money column (§9.1)."""
    try:
        qty = Decimal(str(quantity if quantity not in (None, "") else 0))
        unit = Decimal(str(int(unit_price_cents or 0)))
    except (InvalidOperation, ValueError, TypeError):
        return 0
    return int((qty * unit).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _slugify(text: str) -> str:
    """A URL/path-safe handle: lowercase, non-alphanumerics → single hyphens."""
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").strip().lower()).strip("-")
    return s or "org"


def _unique_slug(base: str, existing: set[str]) -> str:
    """``base`` if free, else ``base-2``, ``base-3``, … (dedupe the UNIQUE slug)."""
    if base not in existing:
        return base
    n = 2
    while f"{base}-{n}" in existing:
        n += 1
    return f"{base}-{n}"


# ═══════════════════════════════════════════════════════════════════════
# Write modals (create / edit / delete) — generated from the store registry
# ═══════════════════════════════════════════════════════════════════════
_FORM_CSS = """
EntityFormModal, ConfirmDeleteModal {
    align: center middle;
    background: $surface 80%;
}
#form-dialog {
    width: 135;
    height: auto;
    max-height: 90%;
    padding: 1 2;
    border: thick $accent;
    background: $surface;
}
#form-title {
    border-bottom: solid $accent;
    margin-bottom: 1;
    content-align: center middle;
}
#form-fields {
    height: auto;
    max-height: 38;
}
.form-grid {
    height: auto;
    layout: grid;
    grid-size: 2;
    grid-gutter: 0 3;
    grid-rows: auto;
}
.form-full-width { height: auto; }
.form-textarea { height: 7; width: 100%; }
.form-textarea-tall { height: 14; width: 100%; }
.form-field { height: auto; padding: 0; }
.form-field Input, .form-field Select, .form-field Checkbox { width: 100%; }
.form-label { margin-top: 1; color: $text-muted; }
#form-error { color: $error; height: auto; }
#confirm-dialog {
    width: 64;
    height: auto;
    padding: 1 2;
    border: thick $error;
    background: $surface;
}
#confirm-text { height: auto; margin-bottom: 1; }
.form-actions {
    margin-top: 1;
    height: auto;
    align: center middle;
}
.form-actions Button { width: 1fr; height: 3; margin: 0 1; }
"""


class EntityFormModal(ModalScreen):
    """Generic create/edit form generated from the store registry (D33).

    Every field comes from ``writer.spec(entity)`` — the same registry that
    generates the ``agictl`` options — so all entities get a consistent form
    with no per-entity code. Field widgets are chosen from the registry:
    boolean columns get a :class:`Checkbox`, foreign-key columns get a
    :class:`Select` picklist resolved from the related records (``_FK_REF``),
    and everything else gets an :class:`Input`. Submits through
    :class:`OrganizationWriter` (the shared, validated store path).
    ``dismiss(result)`` returns the write result dict on success, ``None`` on
    cancel.
    """

    CSS = _FORM_CSS

    def __init__(self, writer, reader, entity: str, kind: str,
                 row: dict | None = None, **kwargs):
        super().__init__(**kwargs)
        self.writer = writer
        self.reader = reader
        self.entity = entity
        self.kind = kind
        self.row = row or {}
        self.spec = writer.spec(entity)
        self._widgets: dict[str, object] = {}

    def compose(self) -> ComposeResult:
        verb = "Edit" if self.row else "New"
        noun = _LABELS.get(self.kind, self.entity).rstrip("s")
        order = _FORM_ORDER.get(self.entity, self.spec["columns"])
        full_width = _FORM_FULL_WIDTH.get(self.entity, set())
        textarea_fields = _TEXTAREA_FIELDS.get(self.entity, set())
        # Separate grid fields from full-width fields, preserving order
        grid_cols = [c for c in order if c not in full_width]
        full_cols = [c for c in order if c in full_width]
        h_override = _FORM_HEIGHT_OVERRIDE.get(self.entity)
        with Vertical(id="form-dialog"):
            if h_override:
                self.styles.max_height = h_override
            yield Static(f"[bold]{verb} {noun}[/]", id="form-title")
            with VerticalScroll(id="form-fields"):
                with Vertical(classes="form-grid"):
                    for col in grid_cols:
                        with Vertical(classes="form-field"):
                            yield self._label(col)
                            yield self._widget(col, col in textarea_fields)
                for col in full_cols:
                    with Vertical(classes="form-full-width"):
                        yield self._label(col)
                        yield self._widget(col, col in textarea_fields)
            yield Static("", id="form-error")
            with Horizontal(classes="form-actions"):
                yield Button("Save", variant="success", id="form-save")
                if self.entity == "credential":
                    yield Button("📋 Copy Config", variant="default", id="form-copy-config")
                yield Button("Cancel", classes="dismiss-btn", variant="default", id="form-cancel")

    def _is_disabled(self, col: str) -> bool:
        """A field the operator cannot edit — set by the system/sync/Lines, or an
        auto-generated handle (slug) or document number."""
        auto = _AUTO_SLUG.get(self.entity)
        return ((self.entity, col) in _FK_DISABLED
                or (self.entity, col) in _FORM_DISABLED
                or (self.entity, col) in _AUTO_NUMBER_FIELDS
                or (auto is not None and col == auto[0]))

    def _label(self, col: str) -> Label:
        s = self.spec
        req = "  [red]*[/]" if col in s["required"] else ""
        auto = _AUTO_SLUG.get(self.entity)
        if auto is not None and col == auto[0]:
            hint = "  [dim](auto from name)[/]"
        elif (self.entity, col) in _AUTO_NUMBER_FIELDS:
            hint = "  [dim](auto-numbered)[/]"
        elif (self.entity, col) in _LINES_MANAGED:
            hint = "  [dim](set in Lines)[/]"
        elif self._is_disabled(col):
            hint = "  [dim](system-set)[/]"
        elif col in s.get("bool", []):
            hint = ""
        elif ((self.entity, col) in _FK_REF or (self.entity, col) in _FIELD_CHOICES
              or (self.entity, col) in _PICKLIST_FIELDS):
            hint = "  [dim](pick)[/]"
        elif col in s["money"]:
            eg = _MONEY_EG.get((self.entity, col), "500.00")
            hint = f"  [dim]($ e.g. {eg})[/]"
        elif col in s["int"]:
            hint = "  [dim](integer)[/]"
        elif col in s["real"]:
            hint = "  [dim](number)[/]"
        else:
            hint = ""
        display = _FIELD_LABEL.get(col) or col.replace("_", " ").title()
        return Label(f"{display}{req}{hint}", classes="form-label")

    def _widget(self, col: str, as_textarea: bool = False):
        s = self.spec
        val = self.row.get(col)
        disabled = self._is_disabled(col)
        ref = _FK_REF.get((self.entity, col))
        choices = _FIELD_CHOICES.get((self.entity, col))
        pick = _PICKLIST_FIELDS.get((self.entity, col))
        if as_textarea:
            raw = "" if val is None else str(val)
            # Pretty-print JSON for the configuration field
            if self.entity == "credential" and col == "configuration":
                try:
                    raw = json.dumps(json.loads(raw), indent=2)
                except (ValueError, TypeError):
                    pass
                w = TextArea(raw, id=f"f-{col}", classes="form-textarea-tall")
            else:
                w = TextArea(raw, id=f"f-{col}", classes="form-textarea")
        elif col in s.get("bool", []):
            default = bool(val) if val is not None else _BOOL_NEW_DEFAULT.get(col, False)
            w = Checkbox("yes", value=default, disabled=disabled, id=f"f-{col}")
        elif ref or choices or pick:
            nullable_fk = (self.entity, col) in _NULLABLE_FK
            if choices:
                options = list(choices)
            elif pick:
                options = self.reader.picklist_options(*pick)
            else:
                options = _fk_options(self.reader, ref)
            if nullable_fk:
                options = [("\u2014 none \u2014", "\x00NONE"), *options]
            current = "" if val is None else str(val)
            option_values = [v for _lbl, v in options]
            if current and current not in option_values:
                options = [(f"{current}  (current)", current), *options]
                option_values.append(current)
            # Pass a value only when it's a real option; otherwise let the Select
            # default to its blank state (Select.BLANK explicitly is rejected).
            if current and current in option_values:
                w = Select(options, value=current, prompt="— choose —",
                           disabled=disabled, id=f"f-{col}")
            else:
                w = Select(options, prompt="— choose —", disabled=disabled,
                           id=f"f-{col}")
        elif col in s["money"]:
            eg = _MONEY_EG.get((self.entity, col), "0.00")
            w = Input(value=_cents_to_input(val), placeholder=eg,
                      disabled=disabled, id=f"f-{col}")
        else:
            w = Input(value="" if val is None else str(val),
                      disabled=disabled, id=f"f-{col}")
        self._widgets[col] = w
        return w

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        if event.button.id == "form-cancel":
            self.dismiss(None)
            return
        if event.button.id == "form-copy-config":
            w = self._widgets.get("configuration")
            text = (w.text if isinstance(w, TextArea) else "") or ""
            err_widget = self.query_one("#form-error", Static)
            for cmd in (["xclip", "-selection", "clipboard"],
                        ["xsel", "--clipboard", "--input"],
                        ["pbcopy"]):
                try:
                    proc = subprocess.run(cmd, input=text.encode(),
                                          capture_output=True, timeout=3)
                    if proc.returncode == 0:
                        err_widget.update("[green]✅ Copied to clipboard[/]")
                        return
                except FileNotFoundError:
                    continue
                except Exception:
                    break
            err_widget.update("[red]❌ Clipboard unavailable (install xclip or xsel)[/]")
            return
        money = set(self.spec["money"])
        fields: dict = {}
        for col, w in self._widgets.items():
            if getattr(w, "disabled", False):
                continue                           # system-set field — never sent
            if isinstance(w, Checkbox):
                fields[col] = 1 if w.value else 0
            elif isinstance(w, Select):
                v = w.value
                nullable = (self.entity, col) in _NULLABLE_FK
                # Real option values are always strings; both unset sentinels
                # (Select.BLANK / Select.NULL) are non-str, so this skips them.
                if isinstance(v, str) and v and v != "\x00NONE":
                    fields[col] = v
                elif nullable and (not isinstance(v, str) or v == "\x00NONE"):
                    # Explicitly write NULL so the old FK value is cleared.
                    fields[col] = None
            elif isinstance(w, TextArea):
                v = w.text.strip()
                if v:
                    fields[col] = v
            else:  # Input
                v = w.value.strip()
                if v == "":
                    continue
                if col in money:                   # decimal dollars → integer cents
                    try:
                        fields[col] = _input_to_cents(v)
                    except ValueError:
                        self.query_one("#form-error", Static).update(
                            f"[red]{col}: enter an amount like 500.00[/]")
                        return
                else:
                    fields[col] = v
        if not self.row:                           # auto-stamp system actor on create
            for (ent, col), value in _FORM_AUTO_ON_CREATE.items():
                if ent == self.entity:
                    fields[col] = value
            auto = _AUTO_SLUG.get(self.entity)       # derive the locked slug handle
            if auto is not None:
                slug_col, src_col = auto
                base = _slugify(fields.get(src_col, ""))
                existing = {o.get("slug") for o in self.reader.list_organizations()
                            if o.get("slug")}
                fields[slug_col] = _unique_slug(base, existing)
        if self.row:
            result = self.writer.update(self.entity, self.row["id"], fields)
        else:
            result = self.writer.create(self.entity, fields)
        if result.get("success"):
            self.dismiss(result)
        else:
            self.query_one("#form-error", Static).update(
                f"[red]{result.get('error', 'write failed')}[/]")

    def on_select_changed(self, event: Select.Changed) -> None:
        """Enforce cross-exclude pairs: remove the selected value from the peer."""
        wid = (event.select.id or "")[2:]  # strip the "f-" prefix → column name
        peer_key = _CROSS_EXCLUDE.get((self.entity, wid))
        if not peer_key:
            return
        peer_col = peer_key[1]
        peer_widget = self._widgets.get(peer_col)
        if not isinstance(peer_widget, Select):
            return
        selected = event.value
        ref = _FK_REF.get(peer_key)
        if not ref:
            return
        # Rebuild peer options excluding the value just picked in this Select.
        all_options = _fk_options(self.reader, ref)
        if isinstance(selected, str) and selected:
            all_options = [(lbl, v) for lbl, v in all_options if v != selected]
        # Preserve the peer's current selection if it's still valid.
        peer_current = peer_widget.value
        peer_values = [v for _, v in all_options]
        peer_widget.set_options(all_options)
        if isinstance(peer_current, str) and peer_current in peer_values:
            peer_widget.value = peer_current

    def on_key(self, event) -> None:
        if event.key == "escape":
            event.stop()
            self.dismiss(None)


class ConfirmDeleteModal(ModalScreen):
    """Confirm a destructive delete. ``dismiss(True)`` to proceed."""

    CSS = _FORM_CSS

    def __init__(self, summary: str, **kwargs):
        super().__init__(**kwargs)
        self.summary = summary

    def compose(self) -> ComposeResult:
        with Vertical(id="confirm-dialog"):
            yield Static(
                f"[bold red]Delete[/] {self.summary}?\n\n"
                f"[dim]This is permanent. Rows referenced by others are "
                f"protected by foreign keys and will be refused.[/]",
                id="confirm-text",
            )
            with Horizontal(classes="form-actions"):
                yield Button("Delete", variant="error", id="confirm-yes")
                yield Button("Cancel", classes="dismiss-btn", variant="default", id="confirm-no")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        self.dismiss(event.button.id == "confirm-yes")

    def on_key(self, event) -> None:
        if event.key == "escape":
            event.stop()
            self.dismiss(False)


_MANAGE_CSS = """
ManagePicklistsModal { align: center middle; background: $surface 80%; }
#pick-dialog {
    width: 92;
    height: auto;
    max-height: 90%;
    padding: 1 2;
    border: thick $accent;
    background: $surface;
}
#pick-title { height: auto; margin-bottom: 1; border-bottom: solid $accent; padding-bottom: 1; }
#pick-table { height: 12; margin-bottom: 1; }
.pick-edit { height: auto; margin-bottom: 1; }
.pick-edit Input { width: 1fr; margin: 0 1; }
.pick-replace { height: auto; margin-top: 1; align-vertical: middle; }
.pick-replace Label { width: auto; padding: 1 1 0 1; }
.pick-replace Select { width: 1fr; margin: 0 1; }
#pick-error { height: auto; }
.pick-actions { height: auto; margin-top: 1; border-top: solid $accent; padding-top: 1; align: center middle; }
.pick-actions Button { width: 1fr; height: 3; margin: 0 1; }
"""


class ManagePicklistsModal(ModalScreen):
    """Manage the options of one ``picklists`` list — a (table, field) target.

    Create / Edit (Save) / Replace & Delete the selectable options that back a
    form picklist (e.g. products.type). "In use" shows how many data rows hold
    each option's value; deleting an in-use option requires picking a
    replacement, which repoints those rows (via the validated store reassign)
    before the option is removed. All writes go through :class:`OrganizationWriter`
    (the same validated store path), never raw SQL — TS-11.
    """

    CSS = _MANAGE_CSS

    def __init__(self, writer, reader, table_name: str, field_name: str, **kwargs):
        super().__init__(**kwargs)
        self.writer = writer
        self.reader = reader
        self.table_name = table_name
        self.field_name = field_name
        self.table = DataTable(id="pick-table", cursor_type="row")
        self._rows: dict[str, dict] = {}

    def compose(self) -> ComposeResult:
        with Vertical(id="pick-dialog"):
            yield Static(
                f"[bold]Manage Lists — {self.field_name}[/]  "
                f"[dim]({self.table_name or 'any table'})[/]", id="pick-title")
            yield self.table
            with Horizontal(classes="pick-edit"):
                yield Input(placeholder="name (label)", id="pick-name")
                yield Input(placeholder="value (stored)", id="pick-value")
                yield Input(placeholder="pos", id="pick-pos")
            with Horizontal(classes="pick-replace"):
                yield Label("Replace with:")
                yield Select((), prompt="(replacement)", id="pick-replacement")
            yield Static("", id="pick-error")
            with Horizontal(classes="pick-actions"):
                yield Button("Add", variant="success", id="pick-add")
                yield Button("Save", variant="primary", id="pick-save")
                yield Button("Replace & Delete", variant="error", id="pick-del")
                yield Button("Close", classes="dismiss-btn", variant="default", id="pick-close")

    def on_mount(self) -> None:
        self.table.add_column("ID", width=5)
        self.table.add_column("Name", width=22)
        self.table.add_column("Value", width=22)
        self.table.add_column("Pos", width=5)
        self.table.add_column("In use", width=7)
        self._reload()

    def _reload(self) -> None:
        self.table.clear()
        self._rows.clear()
        replacements: list[tuple[str, str]] = []
        for r in self.reader.list_picklists(self.table_name, self.field_name):
            rid = str(r["id"])
            self._rows[rid] = r
            used = self.writer.count_value(self.table_name, self.field_name, r["value"])
            self.table.add_row(rid, r["name"], r["value"], str(r["position"]),
                               str(used), key=rid)
            replacements.append((f"{r['name']}  ({r['value']})", r["value"]))
        self.query_one("#pick-replacement", Select).set_options(replacements)

    def _selected(self) -> dict | None:
        if self.table.row_count == 0:
            return None
        try:
            key = self.table.coordinate_to_cell_key(
                Coordinate(self.table.cursor_row, 0)).row_key
            return self._rows.get(key.value)
        except Exception:
            return None

    @on(DataTable.RowHighlighted, "#pick-table")
    def _prefill(self, event: DataTable.RowHighlighted) -> None:
        row = self._selected()
        if row:
            self.query_one("#pick-name", Input).value = row["name"]
            self.query_one("#pick-value", Input).value = row["value"]
            self.query_one("#pick-pos", Input).value = str(row["position"])

    def _err(self, msg: str) -> None:
        self.query_one("#pick-error", Static).update(f"[red]{msg}[/]" if msg else "")

    def _inputs(self) -> tuple[str, str, str]:
        return (self.query_one("#pick-name", Input).value.strip(),
                self.query_one("#pick-value", Input).value.strip(),
                self.query_one("#pick-pos", Input).value.strip())

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        bid = event.button.id
        if bid == "pick-close":
            self.dismiss(True)
        elif bid == "pick-add":
            self._add()
        elif bid == "pick-save":
            self._save()
        elif bid == "pick-del":
            self._delete()

    def _add(self) -> None:
        name, value, pos = self._inputs()
        if not name or not value:
            self._err("name and value are required")
            return
        fields = {"name": name, "value": value,
                  "table_name": self.table_name, "field_name": self.field_name}
        if pos:
            fields["position"] = pos
        res = self.writer.create("picklist", fields)
        self._after_write(res, "add failed")

    def _save(self) -> None:
        row = self._selected()
        if not row:
            self._err("select a row to save")
            return
        name, value, pos = self._inputs()
        fields = {"name": name, "value": value}
        if pos:
            fields["position"] = pos
        res = self.writer.update("picklist", row["id"], fields)
        self._after_write(res, "save failed")

    def _delete(self) -> None:
        row = self._selected()
        if not row:
            self._err("select a row to delete")
            return
        used = self.writer.count_value(self.table_name, self.field_name, row["value"])
        if used > 0:
            repl = self.query_one("#pick-replacement", Select).value
            if repl in (None, Select.BLANK) or str(repl) == row["value"]:
                self._err(f"{used} record(s) use '{row['value']}' — "
                          f"choose a different replacement first")
                return
            rr = self.writer.reassign(self.table_name, self.field_name,
                                      row["value"], str(repl))
            if not rr.get("success"):
                self._err(rr.get("error", "reassign failed"))
                return
        res = self.writer.delete("picklist", row["id"])
        self._after_write(res, "delete failed")

    def _after_write(self, res: dict, fail_msg: str) -> None:
        if res.get("success"):
            self._err("")
            self._reload()
        else:
            self._err(res.get("error", fail_msg))

    def on_key(self, event) -> None:
        if event.key == "escape":
            event.stop()
            self.dismiss(True)


_LINES_CSS = """
LineItemsModal { align: center middle; background: $surface 80%; }
#lines-dialog {
    width: 130;
    height: auto;
    max-height: 92%;
    padding: 1 2;
    border: thick $accent;
    background: $surface;
}
#lines-title { height: auto; margin-bottom: 1; border-bottom: solid $accent; padding-bottom: 1; }
#lines-table { height: 13; margin-bottom: 1; border: solid $primary; }
.lines-field-grid {
    height: auto;
    layout: grid;
    grid-size: 2;
    grid-gutter: 0 3;
    grid-rows: auto;
}
.lines-field { height: auto; padding: 0; }
.lines-field Input, .lines-field Select { width: 100%; }
.lines-field-label { margin-top: 1; color: $text-muted; }
.lines-tax-row {
    height: auto;
    layout: grid;
    grid-size: 2;
    grid-gutter: 0 3;
    grid-rows: auto;
    margin-top: 0;
}
.lines-tax-field { height: auto; padding: 0; }
.lines-tax-field Input { width: 100%; }
.lines-totals { height: auto; margin-top: 1; }
#lines-error { height: auto; }
.lines-actions { height: auto; margin-top: 1; align: center middle; }
.lines-actions Button { width: 1fr; height: 3; margin: 0 1; }
"""


class LineItemsModal(ModalScreen):
    """Line-item editor for an invoice or estimate (D33 round 7).

    Add / Save / Delete lines (product · description · quantity · unit price);
    each line total is computed ``round(quantity × unit_price)`` (half-up, §9.1).
    The document totals are **derived**: ``subtotal = Σ line totals`` and
    ``total = subtotal + tax``. Tax is **manual** for now (the operator types the
    amount — it is not forced); an automatic rate-based mode is a planned
    follow-up. Every line change and the tax field write the recomputed
    subtotal/tax/total back to the parent through :class:`OrganizationWriter`
    (the same validated store path — no raw SQL, TS-11). ``dismiss(True)`` so the
    panel refreshes the new totals.
    """

    CSS = _LINES_CSS

    def __init__(self, writer, reader, kind: str, parent_id: int,
                 parent_row: dict | None = None, **kwargs):
        super().__init__(**kwargs)
        self.writer = writer
        self.reader = reader
        self.kind = kind
        self.parent_id = int(parent_id)
        self.parent_row = parent_row or {}
        self.item_entity, self.parent_entity, self.parent_fk = _HAS_LINES[kind]
        self.table = DataTable(id="lines-table", cursor_type="row")
        self._rows: dict[str, dict] = {}
        self._product_opts = _fk_options(self.reader, "products")
        self._product_values = {v for _l, v in self._product_opts}

    def compose(self) -> ComposeResult:
        noun = _LABELS.get(self.kind, self.kind).rstrip("s")
        ref = (self.parent_row.get("invoice_number")
               or self.parent_row.get("estimate_number") or f"#{self.parent_id}")
        with Vertical(id="lines-dialog"):
            yield Static(f"[bold]Lines — {noun} {ref}[/]", id="lines-title")
            yield self.table
            # Row 1: Product | Description
            with Horizontal(classes="lines-field-grid"):
                with Vertical(classes="lines-field"):
                    yield Label("Product", classes="lines-field-label")
                    yield Select(self._product_opts,
                                 prompt="— optional —", id="line-product")
                with Vertical(classes="lines-field"):
                    yield Label("Description", classes="lines-field-label")
                    yield Input(placeholder="line description", id="line-desc")
            # Row 2: Quantity | Unit Price
            with Horizontal(classes="lines-field-grid"):
                with Vertical(classes="lines-field"):
                    yield Label("Quantity", classes="lines-field-label")
                    yield Input(placeholder="e.g. 2.5", id="line-qty")
                with Vertical(classes="lines-field"):
                    yield Label("Unit Price", classes="lines-field-label")
                    yield Input(placeholder="e.g. 120.00", id="line-unit")
            # Row 3: Tax | Apply button
            with Horizontal(classes="lines-tax-row"):
                with Vertical(classes="lines-tax-field"):
                    yield Label("Tax $ (manual)", classes="lines-field-label")
                    yield Input(
                        value=_cents_to_input(self.parent_row.get("tax_total_cents")),
                        placeholder="0.00", id="line-tax")
                with Vertical(classes="lines-tax-field"):
                    yield Label("", classes="lines-field-label")  # spacer
                    yield Button("Apply Tax", variant="primary",
                                 id="line-applytax")
            yield Static("", id="lines-totals")
            yield Static("", id="lines-error")
            with Horizontal(classes="lines-actions"):
                yield Button("Add", variant="success", id="line-add")
                yield Button("Save", variant="primary", id="line-save")
                yield Button("Delete", variant="error", id="line-del")
                yield Button("Close", classes="dismiss-btn", variant="default",
                             id="line-close")

    def on_mount(self) -> None:
        self.table.add_column("ID", width=5)
        self.table.add_column("Product", width=22)
        self.table.add_column("Description", width=26)
        self.table.add_column("Qty", width=8)
        self.table.add_column("Unit", width=12)
        self.table.add_column("Total", width=12)
        self._reload()

    def _reload(self) -> None:
        self.table.clear()
        self._rows.clear()
        for li in self.reader.line_items(self.kind, self.parent_id):
            rid = str(li["id"])
            self._rows[rid] = li
            self.table.add_row(
                rid, li.get("product_name") or "—", li.get("description") or "—",
                str(li.get("quantity") if li.get("quantity") is not None else "—"),
                li.get("unit_price_display"), li.get("total_display"), key=rid)
        self._render_totals()

    def _tax_cents(self) -> int:
        raw = self.query_one("#line-tax", Input).value.strip()
        if not raw:
            return 0
        try:
            return _input_to_cents(raw)
        except ValueError:
            return int(self.parent_row.get("tax_total_cents") or 0)

    def _render_totals(self) -> None:
        subtotal = self.reader.line_subtotal(self.kind, self.parent_id)
        tax = self._tax_cents()
        total = subtotal + tax
        self.query_one("#lines-totals", Static).update(
            f"[dim]Subtotal:[/] {format_money(subtotal)}    "
            f"[dim]Tax:[/] {format_money(tax)}    "
            f"[bold]Total:[/] {format_money(total)}")

    def _save_parent_totals(self) -> None:
        subtotal = self.reader.line_subtotal(self.kind, self.parent_id)
        tax = self._tax_cents()
        self.writer.update(self.parent_entity, self.parent_id,
                           {"subtotal_cents": subtotal, "tax_total_cents": tax,
                            "total_cents": subtotal + tax})

    def _selected(self) -> dict | None:
        if self.table.row_count == 0:
            return None
        try:
            key = self.table.coordinate_to_cell_key(
                Coordinate(self.table.cursor_row, 0)).row_key
            return self._rows.get(key.value)
        except Exception:
            return None

    def _err(self, msg: str) -> None:
        self.query_one("#lines-error", Static).update(f"[red]{msg}[/]" if msg else "")

    @on(DataTable.RowHighlighted, "#lines-table")
    def _prefill(self, event: DataTable.RowHighlighted) -> None:
        row = self._selected()
        if not row:
            return
        pid = row.get("product_id")
        prod = self.query_one("#line-product", Select)
        if pid is not None and str(pid) in self._product_values:
            prod.value = str(pid)
        self.query_one("#line-desc", Input).value = row.get("description") or ""
        self.query_one("#line-qty", Input).value = (
            "" if row.get("quantity") is None else str(row.get("quantity")))
        self.query_one("#line-unit", Input).value = _cents_to_input(
            row.get("unit_price_cents"))

    def _gather_line(self) -> dict | None:
        desc = self.query_one("#line-desc", Input).value.strip()
        qty_raw = self.query_one("#line-qty", Input).value.strip()
        unit_raw = self.query_one("#line-unit", Input).value.strip()
        prod = self.query_one("#line-product", Select).value
        if not unit_raw:
            self._err("unit price is required")
            return None
        try:
            unit_cents = _input_to_cents(unit_raw)
        except ValueError:
            self._err("unit price like 120.00")
            return None
        try:
            qty = float(qty_raw) if qty_raw else 1.0
        except ValueError:
            self._err("quantity must be a number (e.g. 2.5)")
            return None
        fields = {self.parent_fk: self.parent_id, "description": desc,
                  "quantity": qty, "unit_price_cents": unit_cents,
                  "total_cents": _line_total_cents(qty, unit_cents)}
        if isinstance(prod, str) and prod:        # a real product option (not a sentinel)
            fields["product_id"] = prod
        return fields

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        bid = event.button.id
        if bid == "line-close":
            self._save_parent_totals()
            self.dismiss(True)
        elif bid == "line-applytax":
            self._save_parent_totals()
            self._render_totals()
            self._err("")
        elif bid == "line-add":
            fields = self._gather_line()
            if fields:
                self._after(self.writer.create(self.item_entity, fields), "add failed")
        elif bid == "line-save":
            row = self._selected()
            if not row:
                self._err("select a line to save")
                return
            fields = self._gather_line()
            if fields:
                self._after(self.writer.update(self.item_entity, row["id"], fields),
                            "save failed")
        elif bid == "line-del":
            row = self._selected()
            if not row:
                self._err("select a line to delete")
                return
            self._after(self.writer.delete(self.item_entity, row["id"]), "delete failed")

    def _after(self, res: dict, fail_msg: str) -> None:
        if res.get("success"):
            self._err("")
            self._reload()
            self._save_parent_totals()
        else:
            self._err(res.get("error", fail_msg))

    def on_key(self, event) -> None:
        if event.key == "escape":
            event.stop()
            self._save_parent_totals()
            self.dismiss(True)


# ═══════════════════════════════════════════════════════════════════════
# Panel — one per entity, mounted as a top-level agitop tab
# ═══════════════════════════════════════════════════════════════════════

# Tab order + accent icons (Geometric Shapes / Arrows — same blocks as the
# existing ✉ ◎ ◆ tabs so they render in the same terminals). The per-tab accent
# colour is applied in agitop.tcss (border-top on each #--content-tab-org-*-tab),
# matching how Messages/Tasks/Projects are accented. app.py iterates this list to
# build the top-level tabs.
#   (kind, icon, label)
ORGANIZATION_TABS = [
    ("organizations", "◉", "Organizations"),
    ("products", "▣", "Products"),
    ("estimates", "◇", "Estimates"),
    ("invoices", "▤", "Invoices"),
    ("transactions", "⇄", "Transactions"),
    ("exchange", "⇅", "Exchange"),
    ("emails", "✉", "Emails"),
    ("addresses", "📍", "Addresses"),
    ("credentials", "🔑", "Credentials"),
]

# Column layout per entity: (header, width). Kept wide enough to show the data
# the reader already resolves (issuer/customer names, dates, sync provenance).
_COLUMNS = {
    "organizations": [("ID", 5), ("Name", 24), ("Slug", 14), ("Type", 10),
                      ("Logo", 16), ("Active", 7), ("Updated", 12)],
    "products": [("ID", 5), ("Name", 24), ("SKU", 10), ("Type", 10),
                 ("Org", 18), ("Price", 12), ("Active", 7)],
    "estimates": [("ID", 5), ("Number", 14), ("Org", 20), ("Status", 10),
                  ("Issued", 11), ("Expires", 11), ("Total", 12), ("→Inv", 6)],
    "invoices": [("ID", 5), ("Number", 13), ("Org", 18), ("Customer", 20),
                 ("Status", 9), ("Issued", 11), ("Due", 11), ("Total", 12)],
    "transactions": [("ID", 5), ("Date", 11), ("Description", 24),
                     ("Category", 12), ("Account", 14), ("Org", 14),
                     ("Counterparty", 14), ("Amount", 12)],
    "exchange": [("ID", 5), ("System", 9), ("Source", 20), ("External ID", 14),
                 ("Src Org", 14), ("Tgt Org", 14),
                 ("Origin", 9), ("Status", 11), ("Push", 6), ("Updated", 12)],
    "staff": [("ID", 5), ("Org", 20), ("Contact", 24), ("Since", 12)],
    "emails": [("ID", 5), ("Email", 24), ("Label", 10), ("Primary", 8),
               ("Notes", 14), ("Credential", 12), ("Org", 16)],
    "addresses": [("ID", 5), ("Line 1", 18), ("City", 12), ("State", 8),
                  ("Postal", 10), ("Country", 10), ("Label", 10), ("Primary", 8),
                  ("Org", 16)],
    "credentials": [("ID", 5), ("Name", 22), ("Auth Type", 16), ("Notes", 30),
                    ("Updated", 14)],
}

# Tabs that get a "Manage Types" action button → opens ManagePicklistsModal on
# the named (table_name, field_name) managed vocabulary. Each value is a list
# of (table_name, field_name, label) tuples. When a tab has more than one
# managed field, a quick-pick dialog lets the operator choose which list to
# open. Single-field tabs open directly.
_MANAGE_BUTTON: dict[str, list[tuple[str, str, str]]] = {
    "organizations": [("organizations", "type", "Org Type")],
    "products":      [("products", "type", "Product Type"),
                      ("", "currency", "Currency")],
    "invoices":      [("invoices", "status", "Invoice Status"),
                      ("", "currency", "Currency")],
    "estimates":     [("estimates", "status", "Estimate Status"),
                      ("", "currency", "Currency")],
    "transactions":  [("transactions", "category", "Category"),
                      ("", "currency", "Currency")],
    "credentials":   [("credentials", "auth_type", "Auth Type")],
}

# Tabs whose rows own line items → a "Lines" action button opens LineItemsModal.
# Maps panel kind → (item store-entity, parent store-entity, parent FK column).
_HAS_LINES = {
    "estimates": ("estimate-item", "estimate", "estimate_id"),
    "invoices": ("invoice-item", "invoice", "invoice_id"),
}

_LABELS = {kind: label for kind, _icon, label in ORGANIZATION_TABS}

# Panel tab key (plural) → organization_store entity key (the agictl/store name).
_ENTITY = {
    "organizations": "org",
    "products": "product",
    "estimates": "estimate",
    "invoices": "invoice",
    "transactions": "transaction",
    "exchange": "exchange",
    "staff": "org-staff",
    "emails": "email",
    "addresses": "address",
    "credentials": "credential",
}

# Foreign-key columns that resolve to a picklist of related records (D33).
# Keyed by (store entity, column) → a logical reference target. ``exchange``'s
# source_id is intentionally absent — it is a polymorphic reference (its table is
# named by source_table), so it stays a free integer input. A customer is just an
# organization now (no customer bridge), so customer_org_id resolves to the org
# list; the customer relationship itself is implied once the invoice exists.
_FK_REF = {
    ("product", "org_id"): "organizations",
    ("invoice", "org_id"): "organizations",
    ("invoice", "customer_org_id"): "organizations",
    ("estimate", "org_id"): "organizations",
    ("estimate", "customer_org_id"): "organizations",
    ("estimate", "converted_to_invoice_id"): "invoices",
    ("transaction", "org_id"): "organizations",
    ("transaction", "counterparty_org_id"): "organizations",
    ("exchange", "source_org_id"): "organizations",
    ("exchange", "target_org_id"): "organizations",
    ("org-staff", "org_id"): "organizations",
    ("email", "credential_id"): "credentials",
    ("org-email", "org_id"): "organizations",
    ("org-email", "email_id"): "emails_lookup",
    ("org-address", "org_id"): "organizations",
    ("org-address", "address_id"): "addresses_lookup",
}

# Pairs of FK fields on the same entity where the selected value in one must
# be removed from the other's options (can't pick the same org twice).
_CROSS_EXCLUDE = {
    ("exchange", "source_org_id"): ("exchange", "target_org_id"),
    ("exchange", "target_org_id"): ("exchange", "source_org_id"),
}

# FK picklists that are shown but **disabled** — the relationship is set by the
# system, not hand-edited. An estimate's converted-to invoice is created by the
# convert workflow, so re-pointing it in a form is not a practical operation.
_FK_DISABLED = {
    ("estimate", "converted_to_invoice_id"),
}

# FK picklists where the column is nullable — a "— none —" option is prepended
# so the user can explicitly clear the value. On save, a blank/sentinel resolves
# to None (written as SQL NULL) instead of silently leaving the old value.
_NULLABLE_FK = {
    ("email", "credential_id"),
}

# New-record default for boolean checkboxes (matches schema DEFAULTs). Anything
# not listed defaults to unchecked (is_primary, replicate → 0).
_BOOL_NEW_DEFAULT = {"is_active": True}

# Sentinel value for the "All <X>" row in a filter Select. Using an explicit,
# always-present option (allow_blank=False) makes resetting a filter deterministic
# — selecting it posts a normal Select.Changed we handle as "clear" — instead of
# relying on Select.BLANK, which cannot be assigned programmatically and was not
# reliably reloading the table on reset.
_FILTER_ALL = "\x00ALL"

# ── Form field vocabularies & behaviours ──
# Static-vocabulary picklists for NON-foreign-key columns (label == value), used
# only where the vocabulary is fixed by the schema or derived (NOT operator-
# managed): exchange.status is the schema CHECK set; exchange.source_table is the
# syncable-entity set. Operator-managed vocabularies (org/product type,
# invoice/estimate status, transaction category, currency) come from the
# universal ``picklists`` table instead — see _PICKLIST_FIELDS.
_EXCHANGE_STATUSES = ["new", "sync-done", "sync-failed"]
_EXCHANGE_SOURCE_TABLES = ["organizations", "products", "invoices",
                           "estimates", "transactions"]

_FIELD_CHOICES = {
    ("exchange", "status"): [(s, s) for s in _EXCHANGE_STATUSES],
    ("exchange", "source_table"): [(s, s) for s in _EXCHANGE_SOURCE_TABLES],
}

# Form fields whose options come from the universal ``picklists`` table, keyed by
# (store entity, column) → (target table_name, field_name) for the reader lookup.
# Currency is a shared (table_name='') list, so each entity's currency field
# resolves to the same global options.
_PICKLIST_FIELDS = {
    ("org", "type"): ("organizations", "type"),
    ("product", "type"): ("products", "type"),
    ("product", "currency"): ("products", "currency"),
    ("invoice", "status"): ("invoices", "status"),
    ("invoice", "currency"): ("invoices", "currency"),
    ("estimate", "status"): ("estimates", "status"),
    ("estimate", "currency"): ("estimates", "currency"),
    ("transaction", "category"): ("transactions", "category"),
    ("transaction", "currency"): ("transactions", "currency"),
    ("credential", "auth_type"): ("credentials", "auth_type"),
}

# Columns shown DISABLED in the form — system-maintained, never hand-edited.
# Exchange's source_id/error_message are set by the landing/sync mechanics; its
# origin is stamped by the actor (see _FORM_AUTO_ON_CREATE). Invoice/estimate
# money totals are owned by the Lines editor (subtotal = Σ lines, total =
# subtotal + tax), so they are read-only on the header form.
_FORM_DISABLED = {
    ("exchange", "source_id"),
    ("exchange", "error_message"),
    ("exchange", "origin"),
    ("invoice", "subtotal_cents"),
    ("invoice", "tax_total_cents"),
    ("invoice", "total_cents"),
    ("estimate", "subtotal_cents"),
    ("estimate", "tax_total_cents"),
    ("estimate", "total_cents"),
}

# Money totals computed by the Lines editor — a distinct hint from "system-set".
_LINES_MANAGED = {
    ("invoice", "subtotal_cents"), ("invoice", "tax_total_cents"),
    ("invoice", "total_cents"),
    ("estimate", "subtotal_cents"), ("estimate", "tax_total_cents"),
    ("estimate", "total_cents"),
}

# Auto-generated, read-only columns: value is derived on CREATE and locked after.
# organizations.slug is a URL/path-safe unique handle derived from the name (it
# is the table's UNIQUE key); it is never hand-edited, and immutable once set so
# any external reference stays stable. Keyed entity → (column, source column).
_AUTO_SLUG = {"org": ("slug", "name")}

# Auto-numbered document columns — generated by the store (next sequence, 8-digit
# zero-filled: INV-00000001 / EST-00000001) when blank. Shown read-only on the
# form: blank on New (the store fills it), locked on Edit (immutable identifier).
_AUTO_NUMBER_FIELDS = {
    ("invoice", "invoice_number"),
    ("estimate", "estimate_number"),
}

# Columns auto-stamped by the UI on CREATE. The agitop operator is the ``user``
# actor, so a hand-created exchange row lands origin='user' (agent/integration
# rows are written by their respective code paths, per spec §2/§8).
_FORM_AUTO_ON_CREATE = {
    ("exchange", "origin"): "user",
}

# Per-field money placeholder illustrating a TYPICAL amount (decimal dollars),
# so the example reflects realistic use rather than a uniform 500.00.
_MONEY_EG = {
    ("product", "unit_price_cents"): "120.00",
    ("invoice", "subtotal_cents"): "1136.36",
    ("invoice", "tax_total_cents"): "113.64",
    ("invoice", "total_cents"): "1250.00",
    ("estimate", "subtotal_cents"): "1136.36",
    ("estimate", "tax_total_cents"): "113.64",
    ("estimate", "total_cents"): "1250.00",
    ("transaction", "amount_cents"): "250.00",
}

# Human-readable form labels — keyed by raw column name. Unmapped columns get
# auto-titled (underscores → spaces, title-case).
_FIELD_LABEL: dict[str, str] = {
    "org_id": "Organization",
    "customer_org_id": "Customer Org",
    "counterparty_org_id": "Counterparty",
    "source_org_id": "Source Org",
    "target_org_id": "Target Org",
    "external_id": "External ID",
    "source_table": "Source Table",
    "source_id": "Source ID",
    "error_message": "Error Message",
    "is_active": "Active",
    "is_primary": "Primary",
    "logo_path": "Logo Path",
    "name":          "Name",
    "auth_type":     "Auth Type",
    "unit_price_cents": "Unit Price",
    "amount_cents": "Amount",
    "subtotal_cents": "Subtotal",
    "tax_total_cents": "Tax Total",
    "total_cents": "Total",
    "invoice_number": "Invoice #",
    "estimate_number": "Estimate #",
    "issue_date": "Issue Date",
    "due_date": "Due Date",
    "paid_date": "Paid Date",
    "expiry_date": "Expiry Date",
    "transaction_date": "Date",
    "account_name": "Account",
    "converted_to_invoice_id": "Converted to Invoice",
    "usage_notes": "Usage Notes",
    "credential_id": "Credential",
    "line_1": "Address Line 1",
    "line_2": "Address Line 2",
    "postal_code": "Postal Code",
    "connection_uid": "Contact",
}

# Optional per-entity form field ORDER (else registry column order). With the
# 2-column form grid this controls row grouping. Stephen's Products layout is the
# reference pattern: row1 [org_id,type] · row2 [name,description] ·
# row3 [currency,unit_price_cents] · row4 [sku,external_id] · row5 [is_active].
_FORM_ORDER = {
    "product": ["org_id", "type", "name", "description", "currency",
                "unit_price_cents", "sku", "external_id", "is_active"],
    "credential": ["name", "auth_type", "notes", "configuration"],
}

# Fields that should render as a TextArea (multi-line) instead of Input.
_TEXTAREA_FIELDS: dict[str, set[str]] = {
    "credential": {"notes", "configuration"},
}

# Fields that should span full width (outside the 2-col grid).
_FORM_FULL_WIDTH: dict[str, set[str]] = {
    "credential": {"configuration"},
}

# Per-entity height override for the form dialog (e.g. credentials need more room).
_FORM_HEIGHT_OVERRIDE: dict[str, str] = {
    "credential": "80%",
}

# Per-tab picklist filters (mirrors the Messages tab filter affordance, but with
# Select dropdowns since these columns are categorical). Each entry is
# ``(filter_key, label, row_field, kind)`` where:
#   * ``row_field`` is the key on the reader's list-row dict to match against,
#   * ``kind`` is "values" (distinct categorical values become options) or
#     "bool" (Yes/No over a 0/1 column).
# Filtering is client-side over the rows the Reader already returns — no new SQL
# (TS-11): the options reflect the full result set, the table shows the matches.
_FILTERS = {
    "products": [
        ("org", "Org", "org_name", "values"),
        ("active", "Active", "is_active", "bool"),
        ("type", "Type", "type", "values"),
    ],
    "estimates": [
        ("org", "Org", "org_name", "values"),
        ("status", "Status", "status", "values"),
    ],
    "invoices": [
        ("org", "Org", "org_name", "values"),
        ("customer", "Customer", "customer_name", "values"),
        ("status", "Status", "status", "values"),
    ],
    "transactions": [
        ("category", "Category", "category", "values"),
        ("account", "Account", "account_name", "values"),
        ("org", "Org", "org_name", "values"),
        ("counterparty", "Counterparty", "counterparty_name", "values"),
    ],
    "exchange": [
        ("src_org", "Src Org", "source_org_name", "values"),
        ("tgt_org", "Tgt Org", "target_org_name", "values"),
        ("origin", "Origin", "origin", "values"),
        ("status", "Status", "status", "values"),
        ("push", "Push", "replicate", "bool"),
    ],
    "staff": [
        ("org", "Org", "org_name", "values"),
    ],
    "emails": [
        ("org", "Org", "org_name", "values"),
    ],
    "addresses": [
        ("org", "Org", "org_name", "values"),
    ],
    "credentials": [
        ("auth_type", "Auth Type", "auth_type", "values"),
    ],
}


def _fk_options(reader, ref: str) -> list[tuple[str, str]]:
    """Resolve a foreign-key reference to ``[(label, value), …]`` for a Select.

    Reads through the Reader only (no SQL in the panel, per TS-11). Values are
    the integer id as a string so they round-trip straight into the store.
    """
    if ref == "organizations":
        return [(f"{o.get('name') or '—'}  (#{o['id']})", str(o["id"]))
                for o in reader.list_organizations()]
    if ref == "products":
        return [(f"{p.get('name') or '—'}  (#{p['id']})", str(p["id"]))
                for p in reader.list_products()]
    if ref == "invoices":
        return [(f"{i.get('invoice_number') or ('#' + str(i['id']))}  "
                 f"{i.get('total_display', '')}", str(i["id"]))
                for i in reader.list_invoices()]
    if ref == "credentials":
        return [(f"{c.get('name') or c.get('auth_type') or '—'}  (#{c['id']})", str(c["id"]))
                for c in reader.list_credentials()]
    if ref == "emails_lookup":
        return [(f"{e.get('email') or '—'}  (#{e['id']})", str(e["id"]))
                for e in reader.list_emails()]
    if ref == "addresses_lookup":
        return [(f"{a.get('line_1') or '—'}, {a.get('city') or '—'}  (#{a['id']})",
                 str(a["id"]))
                for a in reader.list_addresses()]
    return []


class OrganizationEntityPanel(Widget):
    """A single editable entity table (orgs, products, …) as a top-level tab.

    Read access is via :class:`OrganizationReader`; when a
    :class:`OrganizationWriter` is supplied the panel also shows a New / Edit /
    Delete / Refresh action bar wired to the shared store (so UI edits use the
    exact path agictl uses). Without a writer it is a read-only viewer.
    """

    def __init__(self, reader: OrganizationReader, kind: str, writer=None,
                 tasks_reader=None, **kwargs):
        super().__init__(**kwargs)
        self.reader = reader
        self.writer = writer
        self.tasks_reader = tasks_reader
        self.kind = kind
        self._rows: dict[str, dict] = {}
        self._filters: dict[str, str] = {}
        self._suppress_filter_event = False
        self.table = DataTable(id=f"org-{kind}-table", cursor_type="row")

    def compose(self) -> ComposeResult:
        with Vertical(id=f"org-{self.kind}-panel-body", classes="work-tab-body"):
            if self.kind in _FILTERS:
                with Horizontal(classes="org-filter-bar"):
                    for key, label, _field, _ftype in _FILTERS[self.kind]:
                        yield Select(
                            [(f"All {label}", _FILTER_ALL)], value=_FILTER_ALL,
                            allow_blank=False, id=f"org-{self.kind}-filter-{key}",
                        )
            yield self.table

    def on_mount(self) -> None:
        self.table.cursor_type = "row"
        for header, width in _COLUMNS[self.kind]:
            self.table.add_column(header, width=width)
        self.refresh_data()

    def refresh_data(self) -> None:
        """Reload this entity's table from the reader (read-only)."""
        if self.reader.available():
            self._refresh_filter_options()
        self._refill()


    def _refill(self) -> None:
        """Clear and repopulate the table, applying any active filters."""
        self.table.clear()
        self._rows.clear()
        label = _LABELS[self.kind]
        if not self.reader.available():
            self.table.border_title = f"{label} — database not initialised"
            return
        self.table.border_title = f"{label}"
        n = 0
        fill_method = getattr(self, f"_fill_{self.kind}", None)
        if fill_method:
            n = fill_method()
        else:
            n = self._fill_generic()
        title = f"{label} ({n})" if n > 0 else f"{label}"
        if self.kind == "exchange":
            s = self.reader.summary()
            title += f"  │  pending push {s['pending_push']}"
            if s["sync_failed"]:
                title += f", failed {s['sync_failed']}"
        self.table.border_title = title

    # ─── data refills ───
    def _fill_generic(self) -> int:
        """Fallback fill for entities without a dedicated _fill_* method."""
        n = 0
        rows = self._filtered(self.reader.list_any(self.kind))
        for x in rows:
            rid = str(x["id"])
            self._rows[rid] = x
            n += 1
            row_vals = []
            # Skip the first column (ID) — it's already passed as `rid`.
            for col_name, _ in _COLUMNS[self.kind][1:]:
                val = x.get(col_name)
                # format special columns
                if col_name == "status":
                    val = _c(x.get("colour"), val or "—")
                elif col_name == "replicate":
                    val = _yn(val)
                elif col_name in ("created_at", "updated_at"):
                    val = _date(val)
                else:
                    val = str(val or "—")
                row_vals.append(val)
            self.table.add_row(rid, *row_vals, key=rid)
        return n

    def _fill_organizations(self) -> int:
        n = 0
        for o in self._filtered(self.reader.list_organizations()):
            rid = str(o["id"])
            self._rows[rid] = o
            n += 1
            self.table.add_row(rid, o.get("name") or "—",
                               o.get("slug") or "—",
                               _c(o.get("colour"), o.get("type") or "—"),
                               o.get("logo_path") or "—",
                               _yn(o.get("is_active")),
                               _date(o.get("updated_at")),
                               key=rid)
        return n

    def _fill_products(self) -> int:
        n = 0
        for p in self._filtered(self.reader.list_products()):
            rid = str(p["id"])
            self._rows[rid] = p
            n += 1
            price = p.get("unit_price_cents")
            if price is None:
                price = p.get("price_cents")
            curr = p.get("currency") or "USD"
            price_str = f"${price / 100:.2f} {curr}" if price is not None else "—"
            self.table.add_row(rid, p.get("name") or "—",
                               p.get("sku") or "—",
                               p.get("type") or "—",
                               p.get("org_name") or "—",
                               price_str,
                               _yn(p.get("is_active")),
                               key=rid)
        return n

    def _fill_estimates(self) -> int:
        n = 0
        for e in self._filtered(self.reader.list_estimates()):
            rid = str(e["id"])
            self._rows[rid] = e
            n += 1
            total = e.get("total_cents")
            total_str = f"${total / 100:.2f}" if total is not None else "—"
            self.table.add_row(rid, e.get("estimate_number") or "—",
                               e.get("org_name") or "—",
                               _c(e.get("colour"), e.get("status") or "—"),
                               _date(e.get("issue_date")),
                               _date(e.get("expiry_date")),
                               total_str,
                               str(e.get("converted_to_invoice_id") or "—"),
                               key=rid)
        return n

    def _fill_invoices(self) -> int:
        n = 0
        for i in self._filtered(self.reader.list_invoices()):
            rid = str(i["id"])
            self._rows[rid] = i
            n += 1
            total = i.get("total_cents")
            total_str = f"${total / 100:.2f}" if total is not None else "—"
            self.table.add_row(rid, i.get("invoice_number") or "—",
                               i.get("org_name") or "—",
                               i.get("customer_name") or "—",
                               _c(i.get("colour"), i.get("status") or "—"),
                               _date(i.get("issue_date")),
                               _date(i.get("due_date")),
                               total_str,
                               key=rid)
        return n

    def _fill_transactions(self) -> int:
        n = 0
        for t in self._filtered(self.reader.list_transactions()):
            rid = str(t["id"])
            self._rows[rid] = t
            n += 1
            amount = t.get("amount_cents")
            amount_str = f"${amount / 100:.2f}" if amount is not None else "—"
            self.table.add_row(rid,
                               _date(t.get("transaction_date")),
                               t.get("description") or "—",
                               _c(t.get("colour"), t.get("category") or "—"),
                               t.get("account_name") or "—",
                               t.get("org_name") or "—",
                               t.get("counterparty_name") or "—",
                               amount_str,
                               key=rid)
        return n

    def _fill_exchange(self) -> int:
        n = 0
        for x in self._filtered(self.reader.list_exchange()):
            rid = str(x["id"])
            self._rows[rid] = x
            n += 1
            source = f"{x.get('source_table')} #{x.get('source_id')}"
            self.table.add_row(rid, x.get("name") or "—", source,
                               x.get("external_id") or "—",
                               x.get("source_org_name") or "—",
                               x.get("target_org_name") or "—",
                               x.get("origin") or "—",
                               _c(x.get("colour"), x.get("status") or "—"),
                               _yn(x.get("replicate")), _date(x.get("updated_at")),
                               key=rid)
        return n

    def _fill_staff(self) -> int:
        """Org staff with cross-DB connection name resolution (Option A)."""
        n = 0
        rows = self._filtered(self.reader.list_staff())
        # Option A: resolve connection_uid → display_name from tasks.db
        conn_names = {}
        if self.tasks_reader:
            try:
                for c in self.tasks_reader.get_connections():
                    conn_names[c["uid"]] = c["display_name"]
            except Exception:
                pass
        for s in rows:
            rid = str(s["id"])
            self._rows[rid] = s
            n += 1
            uid = s.get("connection_uid") or ""
            contact = conn_names.get(uid, uid) or "—"
            self.table.add_row(rid, s.get("org_name") or "—", contact,
                               _date(s.get("created_at")), key=rid)
        return n

    def _fill_emails(self) -> int:
        n = 0
        for e in self._filtered(self.reader.list_emails()):
            rid = str(e["id"])
            self._rows[rid] = e
            n += 1
            primary = _c("green" if e.get("is_primary") else "gray",
                         _yn(e.get("is_primary")))
            cred = e.get("credential_name") or e.get("credential_type") or "—"
            self.table.add_row(rid, e.get("email") or "—",
                               e.get("label") or "—", primary,
                               e.get("usage_notes") or "—", cred,
                               e.get("org_name") or "—", key=rid)
        return n

    def _fill_addresses(self) -> int:
        n = 0
        for a in self._filtered(self.reader.list_addresses()):
            rid = str(a["id"])
            self._rows[rid] = a
            n += 1
            primary = _c("green" if a.get("is_primary") else "gray",
                         _yn(a.get("is_primary")))
            self.table.add_row(rid, a.get("line_1") or "—",
                               a.get("city") or "—", a.get("state") or "—",
                               a.get("postal_code") or "—",
                               a.get("country") or "—",
                               a.get("label") or "—", primary,
                               a.get("org_name") or "—", key=rid)
        return n

    def _fill_credentials(self) -> int:
        n = 0
        for c in self._filtered(self.reader.list_credentials()):
            rid = str(c["id"])
            self._rows[rid] = c
            n += 1
            self.table.add_row(rid, c.get("name") or "—",
                               c.get("auth_type") or "—",
                               c.get("notes") or "—",
                               _date(c.get("updated_at")), key=rid)
        return n

    # ── picklist filters ──
    def _filtered(self, rows: list[dict]) -> list[dict]:
        """Keep only rows matching every active filter for this tab."""
        fdefs = _FILTERS.get(self.kind)
        if not fdefs or not self._filters:
            return rows
        out = []
        for r in rows:
            keep = True
            for key, _label, field, ftype in fdefs:
                sel = self._filters.get(key)
                if sel in (None, ""):
                    continue
                val = r.get(field)
                token = str(1 if val else 0) if ftype == "bool" else str(val)
                if token != sel:
                    keep = False
                    break
            if keep:
                out.append(r)
        return out

    @staticmethod
    def _filter_options(fdef, rows: list[dict]) -> list[tuple[str, str]]:
        """Build Select options for one filter from the full result set."""
        _key, _label, field, ftype = fdef
        if ftype == "bool":
            return [("Yes", "1"), ("No", "0")]
        values = sorted({str(r.get(field)) for r in rows
                         if r.get(field) not in (None, "")})
        return [(v, v) for v in values]

    def _refresh_filter_options(self) -> None:
        """Repopulate every filter Select from the full (unfiltered) result set,
        preserving a still-valid current selection and dropping stale ones. The
        first option is always the explicit "All <label>" reset row."""
        if self.kind not in _FILTERS:
            return
        rows = getattr(self.reader, f"list_{self.kind}")()
        self._suppress_filter_event = True
        try:
            for fdef in _FILTERS[self.kind]:
                key, label = fdef[0], fdef[1]
                try:
                    select = self.query_one(f"#org-{self.kind}-filter-{key}", Select)
                except Exception:
                    continue
                value_opts = self._filter_options(fdef, rows)
                options = [(f"All {label}", _FILTER_ALL), *value_opts]
                current = self._filters.get(key)
                valid = current in {value for _label, value in value_opts}
                select.set_options(options)
                select.value = current if valid else _FILTER_ALL
                if not valid:
                    self._filters.pop(key, None)
        finally:
            self._suppress_filter_event = False

    @on(Select.Changed)
    def _on_filter_changed(self, event: Select.Changed) -> None:
        sid = event.select.id or ""
        prefix = f"org-{self.kind}-filter-"
        if not sid.startswith(prefix):
            return
        event.stop()
        if self._suppress_filter_event:
            return
        key = sid[len(prefix):]
        value = event.value
        if value == _FILTER_ALL or value is Select.BLANK:
            self._filters.pop(key, None)
        else:
            self._filters[key] = str(value)
        self._refill()

    def _current_rid(self) -> str | None:
        """Row id under the table cursor, or None if the table is empty."""
        if self.table.row_count == 0:
            return None
        try:
            key = self.table.coordinate_to_cell_key(
                Coordinate(self.table.cursor_row, 0)).row_key
            return key.value
        except Exception:
            return None

    def trigger_action(self, action: str) -> None:
        """Invoked by the wrapper modal's footer buttons."""
        if self.writer is None:
            return
        if action == "new":
            self._open_form(None)
        elif action == "managetypes":
            entries = _MANAGE_BUTTON.get(self.kind)
            if not entries:
                return
            if len(entries) == 1:
                tbl, fld, _lbl = entries[0]
                self.app.push_screen(
                    ManagePicklistsModal(self.writer, self.reader, tbl, fld),
                    lambda _=None: self.refresh_data(),
                )
            else:
                # Multiple managed fields — ask which list to open.
                options = [(lbl, f"{tbl}|{fld}") for tbl, fld, lbl in entries]
                from agitop.panels.org_record_modal import _BridgePickModal
                def _on_picked(result: str | None) -> None:
                    if not result:
                        return
                    tbl, fld = result.split("|", 1)
                    self.app.push_screen(
                        ManagePicklistsModal(self.writer, self.reader, tbl, fld),
                        lambda _=None: self.refresh_data(),
                    )
                self.app.push_screen(
                    _BridgePickModal(options, "List"),
                    _on_picked,
                )
        elif action == "lines":
            rid = self._current_rid()
            if rid and self.kind in _HAS_LINES:
                parent_row = self.writer.get(_ENTITY[self.kind], int(rid))
                self.app.push_screen(
                    LineItemsModal(self.writer, self.reader, self.kind, int(rid),
                                   parent_row),
                    lambda _=None: self.refresh_data(),
                )
            else:
                self.app.bell()
        elif action == "edit":
            rid = self._current_rid()
            if rid:
                self._open_form(rid)
            else:
                self.app.bell()
        elif action == "delete":
            rid = self._current_rid()
            if rid:
                self._confirm_delete(rid)
            else:
                self.app.bell()

    def _open_form(self, rid: str | None) -> None:
        entity = _ENTITY[self.kind]
        row = self.writer.get(entity, int(rid)) if rid else None
        if self.kind == "organizations":
            from agitop.panels.org_record_modal import OrgRecordModal
            self.app.push_screen(
                OrgRecordModal(self.writer, self.reader,
                               tasks_reader=self.tasks_reader, row=row),
                self._after_write,
            )
        else:
            self.app.push_screen(
                EntityFormModal(self.writer, self.reader, entity, self.kind, row),
                self._after_write,
            )

    def _confirm_delete(self, rid: str) -> None:
        row = self._rows.get(str(rid)) or {}
        label = (row.get("name") or row.get("invoice_number")
                 or row.get("estimate_number")
                 or f"{_LABELS[self.kind].rstrip('s')} #{rid}")

        def _done(confirmed: bool) -> None:
            if confirmed:
                result = self.writer.delete(_ENTITY[self.kind], int(rid))
                self._notify(result)
                self.refresh_data()

        self.app.push_screen(ConfirmDeleteModal(str(label)), _done)

    def _after_write(self, result) -> None:
        if result:
            self._notify(result)
            self.refresh_data()

    def _notify(self, result) -> None:
        if not result:
            return
        try:
            if result.get("success"):
                self.app.notify(
                    f"{_LABELS[self.kind].rstrip('s')} {result.get('action')} "
                    f"(id {result.get('id')})")
            else:
                self.app.notify(result.get("error", "write failed"),
                                severity="error")
        except Exception:
            pass


# ═══════════════════════════════════════════════════════════════════════
# Tree Explorer — hierarchical read-only view of an org and its records
# ═══════════════════════════════════════════════════════════════════════

# Max child rows rendered per group in the Explorer. The tree is an orientation
# aid, not a ledger — capping keeps a high-volume org browsable; the full list
# always lives on that entity's own tab.
_TREE_CHILD_CAP = 10


class OrganizationTreePanel(Widget):
    """Hierarchy view: Organization → customers, vendors, products, invoices,
    estimates, transactions.

    Read-only, built from the same Reader, and intended as an **orientation
    aid**: each group renders at most ``_TREE_CHILD_CAP`` rows (with a "… N more"
    note) so even an org with hundreds of invoices stays browsable. Mounted as an
    extra top-level tab so the flat tables and the tree complement each other.
    """

    def __init__(self, reader: OrganizationReader, **kwargs):
        super().__init__(**kwargs)
        self.reader = reader
        self._tree: Tree = Tree("Organizations", id="org-tree")

    def compose(self) -> ComposeResult:
        with Vertical(id="org-explorer-panel-body", classes="work-tab-body"):
            yield self._tree

    def on_mount(self) -> None:
        self._tree.root.expand()
        self.refresh_data()

    def refresh_data(self) -> None:
        self._tree.clear()
        root = self._tree.root
        if not self.reader.available():
            root.set_label("Organizations — database not initialised")
            return
        orgs = self.reader.list_organizations()
        root.set_label(f"Organizations ({len(orgs)})")
        for o in orgs:
            self._add_org(root, o)
        root.expand()

    def _add_org(self, root, o: dict) -> None:
        tag = "" if o.get("is_active") else "  [inactive]"
        node = root.add(f"{o.get('name') or '—'}  ({o.get('type') or '—'}){tag}")
        detail = self.reader.organization_detail(o["id"]) or {}
        self._add_group(node, "Customers", detail.get("customers", []),
                        lambda x: f"{x.get('name')}")
        self._add_group(node, "Vendors", detail.get("vendors", []),
                        lambda x: f"{x.get('name')}")
        self._add_group(node, "Products", self.reader.list_products_for_org(o["id"]),
                        lambda x: f"{x.get('name')}  {x.get('price_display')}")
        self._add_group(node, "Invoices", detail.get("invoices", []),
                        lambda x: f"{x.get('invoice_number') or '—'}  "
                                  f"{x.get('status') or ''}  {x.get('total_display')}")
        self._add_group(node, "Estimates", detail.get("estimates", []),
                        lambda x: f"{x.get('estimate_number') or '—'}  "
                                  f"{x.get('status') or ''}  {x.get('total_display')}")
        self._add_group(node, "Transactions",
                        self.reader.list_transactions_for_org(o["id"]),
                        lambda x: f"{x.get('transaction_date') or '—'}  "
                                  f"{(x.get('description') or '')[:28]}  "
                                  f"{x.get('amount_display')}")

    @staticmethod
    def _add_group(parent, title: str, items: list, fmt) -> None:
        if not items:
            return
        # Orientation aid, not a full ledger — cap how many rows render per group
        # so a high-volume org (hundreds of invoices) stays browsable. The group
        # label always shows the true total; a trailing leaf notes the remainder.
        total = len(items)
        group = parent.add(f"{title} ({total})")
        for it in items[:_TREE_CHILD_CAP]:
            group.add_leaf(fmt(it))
        if total > _TREE_CHILD_CAP:
            group.add_leaf(f"[dim]… {total - _TREE_CHILD_CAP} more "
                           f"(open the {title} tab for all)[/]")


