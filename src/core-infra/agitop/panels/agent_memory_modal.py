"""Agent memory viewer — connection memory with PU edit/remove."""
from __future__ import annotations

import os
import sys
_CORE_INFRA = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _CORE_INFRA not in sys.path:
    sys.path.insert(0, _CORE_INFRA)
import db_connect  # noqa: E402


import json
import os
import sqlite3
import time
from typing import Optional

from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, DataTable, Select, Static, TextArea

_TZ = time.strftime("%Z")


def _utc_to_local(utc_str: str) -> str:
    if not utc_str or utc_str == "--" or len(utc_str) < 16:
        return utc_str or "--"
    from datetime import datetime, timezone
    try:
        dt = datetime.strptime(utc_str[:19], "%Y-%m-%d %H:%M:%S")
        dt = dt.replace(tzinfo=timezone.utc).astimezone()
        return dt.strftime("%Y-%m-%d %H:%M")
    except (ValueError, OSError):
        return utc_str


def _tasks_db() -> str:
    return os.getenv("AGICTL_TASKS_DB", "/var/lib/versa-agi/coa/tasks.db")


def _truncate(text: str, limit: int = 60) -> str:
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def _build_name_cache(conn: sqlite3.Connection) -> dict[str, str]:
    cache: dict[str, str] = {}
    try:
        for row in conn.execute("SELECT uid, display_name FROM connections").fetchall():
            uid = row["uid"]
            name = row["display_name"]
            if uid and name and name != "Unknown":
                cache[uid] = name
    except Exception:
        pass
    try:
        config_path = os.getenv("AGICTL_CONFIG", "/etc/versa-agi/coa_config.json")
        with open(config_path, encoding="utf-8") as f:
            cfg = json.load(f)
        pu = cfg.get("primary_user", {})
        if pu.get("uid") and pu.get("display_name"):
            cache[pu["uid"]] = pu["display_name"]
    except Exception:
        pass
    return cache


class AgentMemoryViewModal(ModalScreen):
    """Viewer for an agent's connection memory (per contact)."""

    def __init__(self, agent_name: str, **kwargs):
        super().__init__(**kwargs)
        self.agent_name = agent_name
        self._conn_rows: dict[str, dict] = {}
        self.selected_contact_uid: Optional[str] = None

    def compose(self) -> ComposeResult:
        with Vertical(id="agent-memory-dialog"):
            yield Static(
                f"[bold]🧠 Connection Memory — {self.agent_name}[/]",
                id="agent-memory-title",
            )
            with Vertical(id="agent-memory-connection-pane"):
                yield Static(
                    "[bold cyan]Connection Memory[/]",
                    id="agent-memory-connection-header",
                )
                yield Static(
                    "[dim]Per-contact preferences, rapport, and notes for this agent's connections.[/]"
                )
                with VerticalScroll(id="agent-memory-connection-scroll"):
                    yield DataTable(id="agent-memory-connection-table", cursor_type="row")
                yield Static("", id="agent-memory-connection-hint")
                with Horizontal(classes="btn-grid-row"):
                    yield Button(
                        "Edit Selected", variant="primary",
                        id="btn-agent-mem-edit-conn", disabled=True, classes="panel-btn",
                    )
                    yield Button(
                        "Remove Selected", variant="error",
                        id="btn-agent-mem-remove-conn", disabled=True, classes="panel-btn",
                    )
            with Horizontal(id="agent-memory-dialog-footer"):
                yield Button(
                    "Close", variant="default", id="btn-agent-memory-close",
                    classes="modal-close-btn dismiss-btn",
                )

    def on_mount(self) -> None:
        conn_table = self.query_one("#agent-memory-connection-table", DataTable)
        conn_table.add_columns("Contact", "Rapport", "Comm Style", "Summary", f"Updated ({_TZ})")
        self.refresh_connection_table()

    def refresh_connection_table(self) -> None:
        table = self.query_one("#agent-memory-connection-table", DataTable)
        table.clear()
        self._conn_rows = {}
        self.selected_contact_uid = None
        self.query_one("#btn-agent-mem-edit-conn", Button).disabled = True
        self.query_one("#btn-agent-mem-remove-conn", Button).disabled = True
        self.query_one("#agent-memory-connection-hint", Static).update(
            "[dim]Select a row to edit or remove.[/]"
        )
        try:
            conn = db_connect.connect_compat(_tasks_db(), timeout=5)
            conn.row_factory = sqlite3.Row
            name_cache = _build_name_cache(conn)
            rows = conn.execute(
                "SELECT * FROM agent_memory_connection WHERE agent_name=? ORDER BY updated_at DESC",
                (self.agent_name,),
            ).fetchall()
            conn.close()
        except Exception as e:
            self.query_one("#agent-memory-connection-header", Static).update(
                f"[bold cyan]Connection Memory[/] [red](error: {e})[/]"
            )
            return

        for r in rows:
            row = dict(r)
            uid = row.get("contact_uid") or ""
            if not uid:
                continue
            display = name_cache.get(uid, uid[:12] + "...")
            summary = _truncate(
                row.get("personal_notes") or row.get("preferences") or row.get("emotional_notes") or ""
            )
            table.add_row(
                display,
                row.get("rapport_level") or "--",
                _truncate(row.get("communication_style") or "", 24) or "--",
                summary or "--",
                _utc_to_local(row.get("updated_at") or ""),
                key=uid,
            )
            self._conn_rows[uid] = row

        count = len(self._conn_rows)
        self.query_one("#agent-memory-connection-header", Static).update(
            f"[bold cyan]Connection Memory ({count})[/]"
            if count else "[bold cyan]Connection Memory[/] [dim](none)[/]"
        )

    @on(DataTable.RowHighlighted, "#agent-memory-connection-table")
    def on_connection_highlighted(self, event: DataTable.RowHighlighted) -> None:
        if event.row_key is None:
            return
        self.selected_contact_uid = event.row_key.value
        self.query_one("#btn-agent-mem-edit-conn", Button).disabled = False
        self.query_one("#btn-agent-mem-remove-conn", Button).disabled = False
        self.query_one("#agent-memory-connection-hint", Static).update(
            f"[bold cyan]Selected:[/] {self.selected_contact_uid}"
        )

    @on(DataTable.RowSelected, "#agent-memory-connection-table")
    def on_connection_selected(self, event: DataTable.RowSelected) -> None:
        uid = event.row_key.value
        row = self._conn_rows.get(uid)
        if row:
            self.selected_contact_uid = uid
            self.app.push_screen(EditConnectionMemoryModal(self.agent_name, row, self))

    def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id
        if bid == "btn-agent-memory-close":
            self.app.pop_screen()
        elif bid == "btn-agent-mem-edit-conn" and self.selected_contact_uid:
            row = self._conn_rows.get(self.selected_contact_uid)
            if row:
                self.app.push_screen(EditConnectionMemoryModal(self.agent_name, row, self))
        elif bid == "btn-agent-mem-remove-conn" and self.selected_contact_uid:
            self.app.push_screen(
                RemoveConnectionMemoryModal(self.agent_name, self.selected_contact_uid, self)
            )


class EditConnectionMemoryModal(ModalScreen):
    """PU editor for a connection memory row."""

    CSS = """
    EditConnectionMemoryModal {
        align: center middle;
        background: $surface 80%;
    }
    #conn-memory-edit-dialog {
        width: 86;
        height: auto;
        max-height: 85%;
        padding: 1 2;
        border: heavy $primary;
        background: $surface;
    }
    #conn-mem-preferences,
    #conn-mem-personal-notes,
    #conn-mem-emotional-notes {
        height: 4;
        margin: 1 0;
    }
    #conn-mem-comm-style {
        height: 3;
        margin-bottom: 1;
    }
    #conn-memory-edit-actions {
        margin-top: 1;
        height: auto;
        align: center middle;
    }
    #conn-memory-edit-actions Button {
        width: 1fr;
        margin: 0 1;
        min-width: 16;
        height: 3;
    }
    """

    def __init__(self, agent_name: str, row: dict, parent, **kwargs):
        super().__init__(**kwargs)
        self.agent_name = agent_name
        self.row = row
        self.parent_modal = parent

    def compose(self) -> ComposeResult:
        uid = self.row.get("contact_uid", "?")
        rapport = self.row.get("rapport_level") or "new"
        with Vertical(id="conn-memory-edit-dialog"):
            yield Static(
                f"[bold]Edit Connection Memory[/]\n"
                f"[dim]{self.agent_name} · {uid}[/]"
            )
            yield Static("[cyan]Rapport[/]")
            yield Select(
                [
                    ("new", "new"),
                    ("building", "building"),
                    ("established", "established"),
                    ("strong", "strong"),
                ],
                value=rapport,
                id="conn-mem-rapport",
                allow_blank=False,
            )
            yield Static("[cyan]Communication style[/]")
            yield TextArea(self.row.get("communication_style") or "", id="conn-mem-comm-style")
            yield Static("[cyan]Preferences[/]")
            yield TextArea(self.row.get("preferences") or "", id="conn-mem-preferences")
            yield Static("[cyan]Personal notes[/]")
            yield TextArea(self.row.get("personal_notes") or "", id="conn-mem-personal-notes")
            yield Static("[cyan]Emotional notes[/]")
            yield TextArea(self.row.get("emotional_notes") or "", id="conn-mem-emotional-notes")
            with Horizontal(id="conn-memory-edit-actions"):
                yield Button("Save", variant="success", id="btn-conn-mem-save")
                yield Button("Cancel", classes="dismiss-btn", variant="default", id="btn-conn-mem-cancel")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        if event.button.id == "btn-conn-mem-cancel":
            self.app.pop_screen()
            return
        uid = self.row.get("contact_uid")
        rapport = self.query_one("#conn-mem-rapport", Select).value
        try:
            conn = db_connect.connect_compat(_tasks_db(), timeout=5)
            conn.execute(
                """UPDATE agent_memory_connection SET
                   rapport_level=?, communication_style=?, preferences=?,
                   personal_notes=?, emotional_notes=?, updated_at=CURRENT_TIMESTAMP
                   WHERE agent_name=? AND contact_uid=?""",
                (
                    rapport,
                    self.query_one("#conn-mem-comm-style", TextArea).text.strip() or None,
                    self.query_one("#conn-mem-preferences", TextArea).text.strip() or None,
                    self.query_one("#conn-mem-personal-notes", TextArea).text.strip() or None,
                    self.query_one("#conn-mem-emotional-notes", TextArea).text.strip() or None,
                    self.agent_name,
                    uid,
                ),
            )
            conn.commit()
            conn.close()
            self.app.notify(f"Updated connection memory for {uid[:12]}...", severity="information")
            self.parent_modal.refresh_connection_table()
            self.app.pop_screen()
        except Exception as e:
            self.app.notify(f"Error saving: {e}", severity="error")


class RemoveConnectionMemoryModal(ModalScreen):
    """Confirm removal of a connection memory row."""

    CSS = """
    RemoveConnectionMemoryModal {
        align: center middle;
        background: $surface 80%;
    }
    #conn-memory-remove-dialog {
        width: 64;
        height: auto;
        padding: 1 2;
        border: heavy $error;
        background: $surface;
    }
    #conn-memory-remove-actions {
        margin-top: 1;
        height: auto;
        align: center middle;
    }
    #conn-memory-remove-actions Button {
        width: 1fr;
        margin: 0 1;
        min-width: 16;
        height: 3;
    }
    """

    def __init__(self, agent_name: str, contact_uid: str, parent, **kwargs):
        super().__init__(**kwargs)
        self.agent_name = agent_name
        self.contact_uid = contact_uid
        self.parent_modal = parent

    def compose(self) -> ComposeResult:
        with Vertical(id="conn-memory-remove-dialog"):
            yield Static("[bold red]Remove connection memory?[/]\n")
            yield Static(
                f"[dim]{self.agent_name} · {self.contact_uid}[/]\n\n"
                "[bold]This cannot be undone.[/]"
            )
            with Horizontal(id="conn-memory-remove-actions"):
                yield Button("Remove", variant="error", id="btn-conn-mem-remove")
                yield Button("Cancel", classes="dismiss-btn", variant="default", id="btn-conn-mem-remove-cancel")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        if event.button.id == "btn-conn-mem-remove-cancel":
            self.app.pop_screen()
            return
        try:
            conn = db_connect.connect_compat(_tasks_db(), timeout=5)
            conn.execute(
                "DELETE FROM agent_memory_connection WHERE agent_name=? AND contact_uid=?",
                (self.agent_name, self.contact_uid),
            )
            conn.commit()
            conn.close()
            self.app.notify("Connection memory removed", severity="information")
            self.parent_modal.refresh_connection_table()
        except Exception as e:
            self.app.notify(f"Error removing: {e}", severity="error")
        self.app.pop_screen()
