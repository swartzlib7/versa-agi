"""System Memory Editor Modal."""

import sqlite3
import os
from textual import on
from textual.app import ComposeResult
from textual.screen import ModalScreen
from textual.containers import Vertical, VerticalScroll, Horizontal
from textual.widgets import DataTable, Button, Static, Input

class SystemMemoryEditorModal(ModalScreen):
    """Editable grid for global system memories."""

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="msg-dialog"):
            yield Static("[bold]🧠 Edit System Memories (Global)[/]", id="msg-dialog-header")
            self.table = DataTable(id="sys-memory-table", cursor_type="row")
            yield self.table
            
            yield Static("", id="sys-memory-hint")
            with Horizontal(id="msg-dialog-actions"):
                yield Button("Delete Selected", variant="error", id="btn-delete-mem", disabled=True)
                yield Button("Edit Selected", variant="primary", id="btn-edit-mem", disabled=True)
                yield Button("Close", variant="default", id="msg-dialog-close")

    def on_mount(self) -> None:
        self.table.add_columns("Key", "Value", "Stored By", "Updated")
        self.refresh_table()

    def refresh_table(self) -> None:
        self.table.clear()
        self.selected_row_key = None
        self.query_one("#btn-delete-mem", Button).disabled = True
        self.query_one("#btn-edit-mem", Button).disabled = True
        self.query_one("#sys-memory-hint", Static).update("[dim]Select a row above to edit or delete.[/dim]")

        tasks_db = os.getenv("AGICTL_TASKS_DB", "/var/lib/versa-agi/coa/tasks.db")
        try:
            conn = sqlite3.connect(tasks_db, timeout=5)
            conn.row_factory = sqlite3.Row
            rows = conn.execute("SELECT * FROM agent_memory_system ORDER BY key ASC").fetchall()
            for r in rows:
                val = r["value"]
                if val and len(val) > 60:
                    val = val[:57] + "..."
                self.table.add_row(
                    r["key"], 
                    val, 
                    str(r["agent_name"] or "?"), 
                    str(r["updated_at"] or "--"),
                    key=r["key"]
                )
            conn.close()
        except Exception as e:
            self.app.notify(f"Error loading system memory: {e}", severity="error")
            
    @on(DataTable.RowSelected)
    def on_row_selected(self, event: DataTable.RowSelected) -> None:
        self.selected_row_key = event.row_key.value
        self.query_one("#btn-delete-mem", Button).disabled = False
        self.query_one("#btn-edit-mem", Button).disabled = False
        self.query_one("#sys-memory-hint", Static).update(f"[bold cyan]Selected:[/] {self.selected_row_key}")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "msg-dialog-close":
            self.app.pop_screen()
        elif event.button.id == "btn-delete-mem":
            if hasattr(self, 'selected_row_key') and self.selected_row_key:
                self.delete_memory(self.selected_row_key)
        elif event.button.id == "btn-edit-mem":
            if hasattr(self, 'selected_row_key') and self.selected_row_key:
                self.app.push_screen(EditMemoryRowModal(self.selected_row_key, self))

    def delete_memory(self, key_value: str) -> None:
        tasks_db = os.getenv("AGICTL_TASKS_DB", "/var/lib/versa-agi/coa/tasks.db")
        try:
            conn = sqlite3.connect(tasks_db, timeout=5)
            conn.execute("DELETE FROM agent_memory_system WHERE key=?", (key_value,))
            conn.commit()
            conn.close()
            self.app.notify(f"Deleted memory: {key_value}", severity="information")
            self.refresh_table()
        except Exception as e:
            self.app.notify(f"Error deleting memory: {e}", severity="error")


class EditMemoryRowModal(ModalScreen):
    """Sub-modal mapping an Input box to mutate an existing memory value."""
    
    def __init__(self, key_value: str, parent_modal: SystemMemoryEditorModal, **kwargs):
        super().__init__(**kwargs)
        self.key_value = key_value
        self.parent_modal = parent_modal
        self.current_val = ""

    def on_mount(self) -> None:
        tasks_db = os.getenv("AGICTL_TASKS_DB", "/var/lib/versa-agi/coa/tasks.db")
        try:
            conn = sqlite3.connect(tasks_db, timeout=5)
            conn.row_factory = sqlite3.Row
            row = conn.execute("SELECT value FROM agent_memory_system WHERE key=?", (self.key_value,)).fetchone()
            if row:
                self.current_val = row["value"]
            conn.close()
        except Exception:
            pass
        self.query_one("#input-mem-val", Input).value = self.current_val

    def compose(self) -> ComposeResult:
        with Vertical(id="msg-dialog"):
            yield Static(f"[bold]Edit Memory:[/] {self.key_value}", id="msg-dialog-header")
            yield Input(placeholder="Memory Value", id="input-mem-val")
            with Horizontal(id="msg-dialog-actions"):
                yield Button("Save Changes", variant="success", id="btn-save-mem")
                yield Button("Cancel", variant="default", id="msg-dialog-close")
                
    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "msg-dialog-close":
            self.app.pop_screen()
        elif event.button.id == "btn-save-mem":
            new_val = self.query_one("#input-mem-val", Input).value
            tasks_db = os.getenv("AGICTL_TASKS_DB", "/var/lib/versa-agi/coa/tasks.db")
            try:
                conn = sqlite3.connect(tasks_db, timeout=5)
                # Keep original agent_name but update timestamp natively via CURRENT_TIMESTAMP
                conn.execute(
                    "UPDATE agent_memory_system SET value=?, updated_at=CURRENT_TIMESTAMP WHERE key=?", 
                    (new_val, self.key_value)
                )
                conn.commit()
                conn.close()
                self.app.notify(f"Saved memory: {self.key_value}", severity="information")
                self.parent_modal.refresh_table()
                self.app.pop_screen()
            except Exception as e:
                self.app.notify(f"Error saving memory: {e}", severity="error")
