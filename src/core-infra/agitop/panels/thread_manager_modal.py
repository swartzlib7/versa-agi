"""Thread Manager modal — inspect and drain LangGraph checkpoint threads."""

import os
import sqlite3
import subprocess
from typing import Optional

from textual.app import ComposeResult
from textual.containers import Vertical, Horizontal, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Static, Button, DataTable


def _query_checkpoint_db(db_path: str, sql: str, params: tuple = ()) -> list[dict]:
    """Query a checkpoints.db via sudo (owned by agent user, not root)."""
    try:
        # Try direct access first (works if run as root/sudo)
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=2)
        conn.row_factory = sqlite3.Row
        rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
        conn.close()
        return rows
    except Exception:
        return []


def _get_thread_data(agent_name: str) -> list[dict]:
    """Get thread info from an agent's checkpoints.db."""
    db_path = f"/var/lib/versa-agi/{agent_name}/cycles/checkpoints.db"
    if not os.path.exists(db_path):
        return []

    # Get distinct threads with their checkpoint count and total size
    threads = _query_checkpoint_db(
        db_path,
        "SELECT thread_id, "
        "COUNT(*) as checkpoint_count, "
        "SUM(LENGTH(checkpoint)) as total_bytes "
        "FROM checkpoints GROUP BY thread_id ORDER BY thread_id"
    )

    # Get write counts per thread (each write ≈ one message exchange)
    writes = _query_checkpoint_db(
        db_path,
        "SELECT thread_id, COUNT(*) as write_count "
        "FROM writes GROUP BY thread_id"
    )
    write_map = {w["thread_id"]: w["write_count"] for w in writes}

    for t in threads:
        t["write_count"] = write_map.get(t["thread_id"], 0)
        # Human-readable size
        size = t.get("total_bytes") or 0
        if size >= 1_048_576:
            t["size_str"] = f"{size / 1_048_576:.1f} MB"
        elif size >= 1024:
            t["size_str"] = f"{size / 1024:.1f} KB"
        else:
            t["size_str"] = f"{size} B"

    return threads


def _resolve_project_name(thread_id: str) -> str:
    """Map thread_id to project name. Convention: {agent_id}-{project_id}."""
    parts = thread_id.rsplit("-", 1)
    if len(parts) != 2:
        return ""
    project_part = parts[1]
    if project_part == "0":
        return "(catch-all)"
    try:
        pid = int(project_part)
        tasks_db = "/var/lib/versa-agi/coa/tasks.db"
        if os.path.exists(tasks_db):
            conn = sqlite3.connect(f"file:{tasks_db}?mode=ro", uri=True, timeout=2)
            conn.row_factory = sqlite3.Row
            row = conn.execute("SELECT name FROM projects WHERE id = ?", (pid,)).fetchone()
            conn.close()
            if row:
                return row["name"]
    except Exception:
        pass
    return f"project-{project_part}"


def _drain_thread(agent_name: str, thread_id: str) -> bool:
    """Delete all checkpoint data for a specific thread."""
    db_path = f"/var/lib/versa-agi/{agent_name}/cycles/checkpoints.db"
    try:
        conn = sqlite3.connect(db_path, timeout=5)
        conn.execute("DELETE FROM checkpoints WHERE thread_id = ?", (thread_id,))
        conn.execute("DELETE FROM writes WHERE thread_id = ?", (thread_id,))
        conn.commit()
        conn.execute("VACUUM")
        conn.close()
        return True
    except PermissionError:
        # Fallback: use sudo sqlite3
        try:
            cmds = (
                f"DELETE FROM checkpoints WHERE thread_id = '{thread_id}';"
                f"DELETE FROM writes WHERE thread_id = '{thread_id}';"
            )
            result = subprocess.run(
                ["sudo", "sqlite3", db_path, cmds],
                capture_output=True, text=True, timeout=10
            )
            return result.returncode == 0
        except Exception:
            return False
    except Exception:
        return False


def _drain_all_threads(agent_name: str) -> bool:
    """Delete ALL checkpoint data for an agent."""
    db_path = f"/var/lib/versa-agi/{agent_name}/cycles/checkpoints.db"
    try:
        conn = sqlite3.connect(db_path, timeout=5)
        conn.execute("DELETE FROM checkpoints")
        conn.execute("DELETE FROM writes")
        conn.commit()
        conn.execute("VACUUM")
        conn.close()
        return True
    except PermissionError:
        try:
            result = subprocess.run(
                ["sudo", "sqlite3", db_path,
                 "DELETE FROM checkpoints; DELETE FROM writes;"],
                capture_output=True, text=True, timeout=10
            )
            return result.returncode == 0
        except Exception:
            return False
    except Exception:
        return False


class DrainConfirmModal(ModalScreen):
    """Confirmation before draining a thread."""

    def __init__(self, agent_name: str, thread_id: str, project_name: str,
                 drain_all: bool = False, **kwargs):
        super().__init__(**kwargs)
        self.agent_name = agent_name
        self.thread_id = thread_id
        self.project_name = project_name
        self.drain_all = drain_all

    def compose(self) -> ComposeResult:
        if self.drain_all:
            msg = (
                f"[bold red]Drain ALL threads for {self.agent_name}?[/]\n\n"
                "This will delete all checkpoint state.\n"
                "The agent will start with a fresh context on its next cycle.\n\n"
                "[dim]This action cannot be undone.[/]"
            )
        else:
            label = f"{self.thread_id}"
            if self.project_name:
                label += f" ({self.project_name})"
            msg = (
                f"[bold red]Drain thread {label}?[/]\n\n"
                "This will delete all checkpoint state for this thread.\n"
                "The agent will start fresh on this thread's next cycle.\n\n"
                "[dim]This action cannot be undone.[/]"
            )

        with Vertical(id="msg-dialog"):
            yield Static("[bold]Confirm Drain[/]", id="msg-dialog-header")
            yield Static(msg)
            with Horizontal(id="msg-dialog-actions"):
                yield Button("Drain", variant="error", id="btn-confirm-drain")
                yield Button("Cancel", classes="dismiss-btn", variant="default", id="msg-dialog-close")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-confirm-drain":
            if self.drain_all:
                ok = _drain_all_threads(self.agent_name)
            else:
                ok = _drain_thread(self.agent_name, self.thread_id)

            self.app.pop_screen()  # Remove this confirm dialog → ThreadManagerModal is now active
            if ok:
                label = "all threads" if self.drain_all else self.thread_id
                self.app.notify(f"✓ Drained {label} for {self.agent_name}", title="Thread Manager")
                # Refresh the parent ThreadManagerModal (now the active screen)
                active = self.app.screen
                if isinstance(active, ThreadManagerModal):
                    active.refresh_table()
            else:
                self.app.notify("Drain failed — check DB permissions", title="Error", severity="error")
        elif event.button.id == "msg-dialog-close":
            self.app.pop_screen()


class ThreadManagerModal(ModalScreen):
    """Modal for viewing and managing LangGraph checkpoint threads."""

    def __init__(self, agent_name: str, **kwargs):
        super().__init__(**kwargs)
        self.agent_name = agent_name
        self.db_path = f"/var/lib/versa-agi/{agent_name}/cycles/checkpoints.db"

    def compose(self) -> ComposeResult:
        exists = os.path.exists(self.db_path)

        with Vertical(id="msg-dialog"):
            yield Static(
                f"[bold]🧵 Thread Manager — {self.agent_name}[/]",
                id="msg-dialog-header"
            )
            yield Static(f"[dim]DB: {self.db_path}[/]", id="thread-db-path")
            if not exists:
                yield Static("[dim]No checkpoints.db found — agent has not run with persistence yet.[/]")
            else:
                table = DataTable(id="thread-table")
                table.add_columns(
                    "Thread ID", "Project", "Checkpoints", "Writes", "Size"
                )
                yield table
                yield Static("", id="thread-summary")

            with Horizontal(id="msg-dialog-actions"):
                if exists:
                    yield Button("🗑 Drain All", variant="error", id="btn-drain-all")
                yield Button("Close", classes="dismiss-btn", variant="default", id="msg-dialog-close")

    def on_mount(self) -> None:
        self.refresh_table()

    def refresh_table(self) -> None:
        """Reload thread data into the table."""
        try:
            table = self.query_one("#thread-table", DataTable)
        except Exception:
            return

        table.clear()
        threads = _get_thread_data(self.agent_name)

        summary = self.query_one("#thread-summary")

        if not threads:
            summary.update(
                "[dim]No checkpoint threads — agent will start fresh on next cycle.[/]"
            )
            return

        total_size = 0
        for t in threads:
            thread_id = t["thread_id"]
            project = _resolve_project_name(thread_id)
            table.add_row(
                thread_id,
                project,
                str(t["checkpoint_count"]),
                str(t["write_count"]),
                t["size_str"],
                key=thread_id,
            )
            total_size += t.get("total_bytes") or 0

        # Summary
        if total_size >= 1_048_576:
            total_str = f"{total_size / 1_048_576:.1f} MB"
        elif total_size >= 1024:
            total_str = f"{total_size / 1024:.1f} KB"
        else:
            total_str = f"{total_size} B"

        summary.update(
            f"[dim]{len(threads)} thread(s) — {total_str} total  │  "
            f"Select a row to drain one thread, or use Drain All[/]"
        )

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        """Drain a specific thread when clicked."""
        thread_id = str(event.row_key.value)
        project = _resolve_project_name(thread_id)
        self.app.push_screen(
            DrainConfirmModal(self.agent_name, thread_id, project)
        )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-drain-all":
            self.app.push_screen(
                DrainConfirmModal(self.agent_name, "", "", drain_all=True)
            )
        elif event.button.id == "msg-dialog-close":
            self.app.pop_screen()
