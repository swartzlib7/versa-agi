"""Tasks panel — active tasks list with live data."""

import time
from typing import Optional
from textual import on
from textual.app import ComposeResult
from textual.screen import ModalScreen
from textual.containers import Vertical, VerticalScroll, Horizontal
from textual.widgets import DataTable, Static, Button, Select, Input, TextArea
from rich.markup import escape

from agitop.data import TasksReader
from agitop.widgets import PaginatedDataTable

_TZ = time.strftime("%Z")

def _utc_to_local(utc_str: str) -> str:
    """Convert 'YYYY-MM-DD HH:MM:SS' UTC string to local timezone."""
    if not utc_str or utc_str == "--" or len(utc_str) < 16:
        return utc_str
    from datetime import datetime, timezone
    try:
        dt = datetime.strptime(utc_str[:19], "%Y-%m-%d %H:%M:%S")
        dt = dt.replace(tzinfo=timezone.utc).astimezone()
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except (ValueError, OSError):
        return utc_str


PRIORITY_COLORS = {
    "urgent": "bold red",
    "high": "yellow",
    "normal": "white",
    "low": "dim",
}

TASK_STATUSES = [
    ("planned", "planned"),
    ("in_progress", "in_progress"),
    ("waiting", "waiting"),
    ("blocked", "blocked"),
    ("frozen", "frozen"),
    ("cancelled", "cancelled"),
    ("done", "done"),
]

_PROGRESS_PRUNE_DEFAULT = "Wake cycle review:%"


class ProgressRemoveConfirmModal(ModalScreen[bool]):
    """PU-only confirmation for deleting a single progress journal entry."""

    CSS = """
    ProgressRemoveConfirmModal {
        align: center middle;
        background: $surface 80%;
    }
    #progress-remove-dialog {
        width: 64;
        height: auto;
        padding: 1 2;
        border: heavy $warning;
        background: $surface;
    }
    #progress-remove-actions {
        margin-top: 1;
        height: auto;
        align: center middle;
    }
    #progress-remove-actions Button {
        width: 1fr;
        margin: 0 1;
        min-width: 16;
        height: 3;
    }
    """

    def __init__(self, entry_id: int, note_preview: str, **kwargs):
        super().__init__(**kwargs)
        self.entry_id = entry_id
        self.note_preview = note_preview

    def compose(self) -> ComposeResult:
        preview = escape(self.note_preview)
        if len(preview) > 200:
            preview = preview[:197] + "..."
        with Vertical(id="progress-remove-dialog"):
            yield Static(f"[bold yellow]Remove progress entry #{self.entry_id}[/]\n")
            yield Static(f"[dim]{preview}[/]\n")
            yield Static("[bold]This cannot be undone.[/]")
            with Horizontal(id="progress-remove-actions"):
                yield Button("Remove", variant="error", id="btn-progress-remove-confirm")
                yield Button("Cancel", classes="dismiss-btn", variant="default", id="btn-progress-remove-cancel")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        self.dismiss(event.button.id == "btn-progress-remove-confirm")


class ProgressEditModal(ModalScreen[Optional[str]]):
    """PU-only editor for a single progress journal entry."""

    CSS = """
    ProgressEditModal {
        align: center middle;
        background: $surface 80%;
    }
    #progress-edit-dialog {
        width: 70;
        height: auto;
        max-height: 80%;
        padding: 1 2;
        border: heavy $primary;
        background: $surface;
    }
    #progress-edit-note {
        height: 12;
        margin: 1 0;
    }
    #progress-edit-error {
        height: auto;
        margin-bottom: 1;
    }
    #progress-edit-actions {
        margin-top: 1;
        height: auto;
        align: center middle;
    }
    #progress-edit-actions Button {
        width: 1fr;
        margin: 0 1;
        min-width: 16;
        height: 3;
    }
    """

    def __init__(
        self,
        entry_id: int,
        agent_name: str,
        created_at: str,
        note: str,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self.entry_id = entry_id
        self.agent_name = agent_name
        self.created_at = created_at
        self.note = note

    def compose(self) -> ComposeResult:
        ts = _utc_to_local(self.created_at)[:16] if self.created_at else "?"
        with Vertical(id="progress-edit-dialog"):
            yield Static(
                f"[bold]Edit progress entry #{self.entry_id}[/]\n"
                f"[dim]{ts} · {escape(self.agent_name or '?')}[/]"
            )
            yield TextArea(self.note, id="progress-edit-note")
            yield Static("", id="progress-edit-error")
            with Horizontal(id="progress-edit-actions"):
                yield Button("Save", variant="success", id="btn-progress-edit-save")
                yield Button("Cancel", classes="dismiss-btn", variant="default", id="btn-progress-edit-cancel")

    def _save(self) -> None:
        note = self.query_one("#progress-edit-note", TextArea).text.strip()
        error_label = self.query_one("#progress-edit-error", Static)
        if not note:
            error_label.update("[bold red]Note cannot be empty[/]")
            return
        self.dismiss(note)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        if event.button.id == "btn-progress-edit-save":
            self._save()
        else:
            self.dismiss(None)


class ProgressPruneModal(ModalScreen[Optional[str]]):
    """PU-only confirmation for pruning progress entries by SQL LIKE pattern."""

    CSS = """
    ProgressPruneModal {
        align: center middle;
        background: $surface 80%;
    }
    #progress-prune-dialog {
        width: 64;
        height: auto;
        padding: 1 2;
        border: heavy $error;
        background: $surface;
    }
    #progress-prune-pattern {
        margin: 1 0;
    }
    #progress-prune-actions {
        margin-top: 1;
        height: auto;
        align: center middle;
    }
    #progress-prune-actions Button {
        width: 1fr;
        margin: 0 1;
        min-width: 16;
        height: 3;
    }
    """

    def __init__(
        self,
        task_id: int,
        tasks_reader: TasksReader,
        default_pattern: str = _PROGRESS_PRUNE_DEFAULT,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self.task_id = task_id
        self.tasks_reader = tasks_reader
        self.default_pattern = default_pattern

    def compose(self) -> ComposeResult:
        match_count = self.tasks_reader.count_task_progress_matching(
            self.task_id, self.default_pattern
        )
        prune_count = max(0, match_count - 1)
        with Vertical(id="progress-prune-dialog"):
            yield Static(
                f"[bold red]Prune progress journal — task #{self.task_id}[/]\n\n"
                "Remove older entries matching a SQL LIKE pattern.\n"
                "The [bold]most recent[/] match is always kept.\n"
                f"Default: [bold]{match_count}[/] match(es), [bold]{prune_count}[/] to remove.\n\n"
                "[bold]This cannot be undone.[/]"
            )
            yield Static("[b]LIKE pattern[/b]  [dim](Enter to confirm)[/]")
            yield Input(value=self.default_pattern, id="progress-prune-pattern")
            with Horizontal(id="progress-prune-actions"):
                yield Button("Prune older matches", variant="error", id="btn-progress-prune-confirm")
                yield Button("Cancel", classes="dismiss-btn", variant="default", id="btn-progress-prune-cancel")

    def _confirm_prune(self) -> None:
        pattern = self.query_one("#progress-prune-pattern", Input).value.strip()
        self.dismiss(pattern or None)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "progress-prune-pattern":
            event.stop()
            self._confirm_prune()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        if event.button.id == "btn-progress-prune-confirm":
            self._confirm_prune()
        else:
            self.dismiss(None)


class TaskEditModal(ModalScreen):
    """Modal dialog to view task details and edit status/due_date/project/assignee."""

    def __init__(self, task: dict, tasks_reader: Optional[TasksReader], **kwargs):
        super().__init__(**kwargs)
        self._task_record = task
        self._tasks_reader = tasks_reader
        self._progress_page = 0
        self._progress_page_size = 8
        self._progress_total = 0
        self._progress_entry_notes: dict[str, str] = {}

    def compose(self) -> ComposeResult:
        task = self._task_record
        task_id = task.get("id", "?")
        title = task.get("title", "Untitled")
        desc = task.get("description") or "No description."
        current_status = task.get("status", "planned")
        current_due = task.get("due_date") or ""
        current_project = task.get("project_id")
        current_assignee = task.get("assigned_to") or ""

        # Build project options — ensure current value is always present
        project_options = [("-- None --", 0)]
        if self._tasks_reader:
            project_options += self._tasks_reader.get_project_options()
        project_value = current_project if current_project else 0
        project_ids = [v for _, v in project_options]
        if project_value and project_value not in project_ids:
            project_options.append((f"#{project_value} (archived)", project_value))

        # Build assignee options — ensure current value is always present
        assignee_options = [("-- Unassigned --", "")]
        if self._tasks_reader:
            for name in self._tasks_reader.get_agent_names():
                assignee_options.append((name, name))
        assignee_values = [v for _, v in assignee_options]
        if current_assignee and current_assignee not in assignee_values:
            assignee_options.append((current_assignee, current_assignee))

        with Vertical(id="task-dialog"):
            yield Static(f"[bold]#{task_id}[/]", id="task-dialog-title")
            yield Static("[b]Title[/b]")
            yield Input(
                value=title,
                placeholder="Task title",
                id="task-edit-title",
            )
            yield Static("[b]Description[/b]")
            yield TextArea(
                desc,
                id="task-edit-desc",
            )
            with Horizontal(classes="task-field-row"):
                with Vertical(classes="task-field-col"):
                    yield Static("[b]Project[/b]")
                    yield Select(
                        project_options,
                        value=project_value,
                        id="task-edit-project",
                        allow_blank=False,
                    )
                with Vertical(classes="task-field-col"):
                    yield Static("[b]Assigned To[/b]")
                    yield Select(
                        assignee_options,
                        value=current_assignee,
                        id="task-edit-assignee",
                        allow_blank=False,
                    )
                with Vertical(classes="task-field-col"):
                    yield Static("[b]Status[/b]")
                    yield Select(
                        TASK_STATUSES,
                        value=current_status,
                        id="task-edit-status",
                        allow_blank=False,
                    )
                with Vertical(classes="task-field-col"):
                    yield Static("[b]Due Date[/b] [dim](YYYY-MM-DD HH:MM:SS)[/]")
                    yield Input(
                        value=current_due,
                        placeholder="YYYY-MM-DD HH:MM:SS",
                        id="task-edit-due",
                    )

            # ── Progress Journal (read-only, paginated) ──
            yield Static("")
            yield Static("[b]Progress Journal[/b]", id="task-progress-header")
            yield PaginatedDataTable(self._handle_progress_key, id="task-progress-table")
            with Horizontal(classes="task-progress-actions"):
                yield Button("Edit selected", variant="primary", id="task-progress-edit")
                yield Button("Remove selected", variant="warning", id="task-progress-remove")
                yield Button("Prune matching…", variant="error", id="task-progress-prune")
            yield Static(
                "[dim]PgUp/PgDn to navigate · agents journal via agictl task progress · "
                "PU: edit/remove/prune entries here[/]",
                id="task-progress-hint",
            )

            yield Static("", id="task-edit-error")
            with Horizontal(classes="task-dialog-buttons"):
                yield Button("Save", variant="success", id="task-dialog-save")
                yield Button("Cancel", classes="dismiss-btn", variant="default", id="task-dialog-close")

    def on_mount(self) -> None:
        try:
            table = self.query_one("#task-progress-table", DataTable)
            table.cursor_type = "row"
            table.add_columns("When", "Agent", "Note")
            if self._tasks_reader and isinstance(self._task_record.get("id"), int):
                self._progress_total = self._tasks_reader.count_task_progress(self._task_record["id"])
            self._update_progress_table()
        except Exception:
            pass

    def _handle_progress_key(self, key: str) -> None:
        if key == "pageup":
            if self._progress_page > 0:
                self._progress_page -= 1
                self._update_progress_table()
        elif key == "pagedown":
            max_page = max(0, (self._progress_total - 1) // self._progress_page_size)
            if self._progress_page < max_page:
                self._progress_page += 1
                self._update_progress_table()

    def _update_progress_table(self) -> None:
        try:
            table = self.query_one("#task-progress-table", DataTable)
            table.clear()
            task_id = self._task_record.get("id")
            if not self._tasks_reader or not isinstance(task_id, int):
                return

            self._progress_total = self._tasks_reader.count_task_progress(task_id)
            self._progress_entry_notes = {}
            if self._progress_total == 0:
                table.border_title = "Progress Journal (0 entries)"
                return

            start = self._progress_page * self._progress_page_size
            rows = self._tasks_reader.get_task_progress_page(
                task_id, offset=start, limit=self._progress_page_size
            )
            for entry in rows:
                entry_id = str(entry.get("id") or "")
                raw_note = str(entry.get("note") or "")
                if entry_id:
                    self._progress_entry_notes[entry_id] = raw_note
                ts = _utc_to_local(str(entry.get("created_at") or ""))[:16]
                author = entry.get("agent_name") or "?"
                note = escape(raw_note)
                if len(note) > 120:
                    note = note[:117] + "..."
                table.add_row(ts, author, note, key=entry_id or None)

            total_pages = max(1, (self._progress_total + self._progress_page_size - 1) // self._progress_page_size)
            current_page = self._progress_page + 1
            table.border_title = (
                f"Progress Journal ({self._progress_total})  │  "
                f"Page {current_page}/{total_pages}  │  PgUp/PgDn · newest first"
            )
        except Exception:
            pass

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "task-dialog-close":
            self.app.pop_screen()
        elif event.button.id == "task-dialog-save":
            self._save()
        elif event.button.id == "task-progress-edit":
            self._edit_selected_progress()
        elif event.button.id == "task-progress-remove":
            self._remove_selected_progress()
        elif event.button.id == "task-progress-prune":
            self._prune_progress()

    def _get_selected_progress_entry_id(self) -> Optional[str]:
        table = self.query_one("#task-progress-table", DataTable)
        row_key, _ = table.coordinate_to_cell_key(table.cursor_coordinate)
        entry_id_str = str(row_key.value) if row_key and row_key.value else ""
        if not entry_id_str:
            return None
        return entry_id_str

    def _edit_selected_progress(self) -> None:
        task_id = self._task_record.get("id")
        if not self._tasks_reader or not isinstance(task_id, int):
            return
        entry_id_str = self._get_selected_progress_entry_id()
        if not entry_id_str:
            self.app.notify("Select a journal entry first", severity="warning")
            return

        entry = self._tasks_reader.get_task_progress_entry(int(entry_id_str), task_id)
        if not entry:
            self.app.notify("Progress entry not found", severity="error")
            return

        def _on_save(new_note: Optional[str]) -> None:
            if not new_note:
                return
            if self._tasks_reader.update_task_progress_entry(int(entry_id_str), task_id, new_note):
                self._update_progress_table()
                self.app.notify(f"Updated progress entry #{entry_id_str}", title="agitop")
            else:
                self.app.notify("Failed to update entry", severity="error")

        self.app.push_screen(
            ProgressEditModal(
                int(entry_id_str),
                str(entry.get("agent_name") or "?"),
                str(entry.get("created_at") or ""),
                str(entry.get("note") or ""),
            ),
            _on_save,
        )

    def _remove_selected_progress(self) -> None:
        task_id = self._task_record.get("id")
        if not self._tasks_reader or not isinstance(task_id, int):
            return
        entry_id_str = self._get_selected_progress_entry_id()
        if not entry_id_str or entry_id_str not in self._progress_entry_notes:
            self.app.notify("Select a journal entry first", severity="warning")
            return
        note_preview = self._progress_entry_notes[entry_id_str]

        def _on_confirm(confirmed: bool) -> None:
            if not confirmed:
                return
            if self._tasks_reader.delete_task_progress_entry(int(entry_id_str), task_id):
                self._progress_total = max(0, self._progress_total - 1)
                max_page = max(0, (self._progress_total - 1) // self._progress_page_size)
                if self._progress_page > max_page:
                    self._progress_page = max_page
                self._update_progress_table()
                self.app.notify(f"Removed progress entry #{entry_id_str}", title="agitop")
            else:
                self.app.notify("Failed to remove entry", severity="error")

        self.app.push_screen(
            ProgressRemoveConfirmModal(int(entry_id_str), note_preview),
            _on_confirm,
        )

    def _prune_progress(self) -> None:
        task_id = self._task_record.get("id")
        if not self._tasks_reader or not isinstance(task_id, int):
            return

        def _on_prune(pattern: Optional[str]) -> None:
            if not pattern:
                return
            deleted = self._tasks_reader.prune_task_progress(task_id, pattern)
            if deleted:
                self._progress_page = 0
                self._update_progress_table()
                self.app.notify(
                    f"Pruned {deleted} older entries (kept most recent match)",
                    title="agitop",
                )
            else:
                remaining = self._tasks_reader.count_task_progress_matching(task_id, pattern)
                if remaining:
                    self.app.notify("Only one matching entry — kept as most recent", severity="information")
                else:
                    self.app.notify("No matching entries to prune", severity="warning")

        self.app.push_screen(ProgressPruneModal(task_id, self._tasks_reader), _on_prune)

    def _save(self) -> None:
        task_id = self._task_record.get("id")
        new_title = self.query_one("#task-edit-title", Input).value.strip()
        new_desc = self.query_one("#task-edit-desc", TextArea).text.strip()
        new_status = self.query_one("#task-edit-status", Select).value
        new_due = self.query_one("#task-edit-due", Input).value.strip()
        new_project = self.query_one("#task-edit-project", Select).value
        new_assignee = self.query_one("#task-edit-assignee", Select).value
        error_label = self.query_one("#task-edit-error", Static)

        # Validate: title is required
        if not new_title:
            error_label.update("[bold red]Title cannot be empty[/]")
            return

        # Validate: planned requires due_date
        if new_status == "planned" and not new_due:
            error_label.update("[bold red]Due date is required for planned tasks[/]")
            return

        updates = {}
        old_title = self._task_record.get("title", "")
        old_desc = self._task_record.get("description") or ""
        old_status = self._task_record.get("status", "planned")
        old_due = self._task_record.get("due_date") or ""
        old_project = self._task_record.get("project_id")
        old_assignee = self._task_record.get("assigned_to") or ""

        if new_title != old_title:
            updates["title"] = new_title
        if new_desc != old_desc:
            updates["description"] = new_desc if new_desc else None
        if new_status != old_status:
            updates["status"] = new_status
        if new_due != old_due:
            updates["due_date"] = new_due if new_due else None
        if new_project != old_project:
            updates["project_id"] = new_project if new_project else None
        if new_assignee != old_assignee:
            updates["assigned_to"] = new_assignee if new_assignee else None

        if not updates:
            self.app.pop_screen()
            return

        if self._tasks_reader and self._tasks_reader.update_task(task_id, updates):
            self.app.notify(f"Task #{task_id} updated", title="agitop")
            # Refresh the tasks panel
            try:
                from agitop.panels.tasks import TasksPanel
                self.app.query_one("#tasks-panel", TasksPanel).refresh_data()
            except Exception:
                pass
            self.app.pop_screen()
        else:
            error_label.update("[bold red]Failed to save changes[/]")


class DeleteTaskModal(ModalScreen):
    """Confirmation modal for deleting a done/cancelled task."""

    def __init__(self, task_id: str, task_title: str, tasks_reader: TasksReader, **kwargs):
        super().__init__(**kwargs)
        self.task_id = int(task_id)
        self.task_title = task_title
        self.tasks_reader = tasks_reader

    def compose(self) -> ComposeResult:
        with Vertical(id="msg-dialog"):
            yield Static(
                f"[bold red]Delete Task[/]\n\n"
                f"Permanently delete task [bold]#{self.task_id}[/]: {self.task_title}?",
                id="msg-dialog-header"
            )
            yield Button("Delete", variant="error", id="confirm-delete")
            yield Button("Cancel", classes="dismiss-btn", variant="default", id="cancel-delete")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "confirm-delete":
            success, msg = self.tasks_reader.delete_task(self.task_id)
            self.app.pop_screen()
            if success:
                self.app.notify(msg, severity="information")
            else:
                self.app.notify(msg, severity="error")
            try:
                self.app.query_one("#tasks-panel", TasksPanel).refresh_data()
            except Exception:
                pass
        else:
            self.app.pop_screen()

    def on_key(self, event) -> None:
        if event.key == "escape":
            self.app.pop_screen()


class TasksPanel(DataTable):
    """Displays active tasks using a DataTable for structured columns."""

    PAGE_SIZE = 50

    def __init__(self, tasks_reader: Optional[TasksReader], message_reader=None, **kwargs):
        super().__init__(**kwargs)
        self.tasks_reader = tasks_reader
        self.message_reader = message_reader
        self._page = 0
        self._total = 0

    def on_mount(self) -> None:
        self.cursor_type = "row"
        # Explicit widths — Title and Desc widest; compact cols for ids/status/dates.
        self.add_column("ID", width=5)
        self.add_column("Title", width=50)
        self.add_column("Desc", width=40)
        self.add_column("Status", width=11)
        self.add_column("Priority", width=9)
        self.add_column("Requested By", width=14)
        self.add_column("Project", width=16)
        self.add_column("Assign To", width=12)
        self.add_column("Assign By", width=12)
        self.add_column("Tags", width=12)
        self.add_column(f"Due ({_TZ})", width=19)
        self._task_data = {}
        self._project_cache: dict[int, str] = {}
        self._name_cache: dict[str, str] = {}
        self.refresh_data()

    def _update_title(self) -> None:
        total_pages = max(1, (self._total + self.PAGE_SIZE - 1) // self.PAGE_SIZE)
        current_page = self._page + 1
        self.border_title = (
            f"Tasks ({self._total})  │  Page {current_page}/{total_pages}  │  "
            f"PgUp/PgDn to navigate  │  DEL to delete done/cancelled"
        )

    def _resolve_project(self, project_id) -> str:
        """Resolve project_id to name with caching."""
        if not project_id:
            return "--"
        pid = int(project_id)
        if pid not in self._project_cache:
            self._project_cache[pid] = self.tasks_reader.get_project_name(pid) if self.tasks_reader else f"#{pid}"
        return self._project_cache[pid]

    def _resolve_name(self, uid) -> str:
        """Resolve a VV UID to display name. Checks connections table (tasks.db) first, messages.db fallback."""
        if not uid:
            return "--"
        if uid in self._name_cache:
            return self._name_cache[uid]
        name = uid[:8]
        # 1. Try connections table in tasks.db (canonical)
        if self.tasks_reader:
            try:
                import sqlite3
                conn = sqlite3.connect(self.tasks_reader.db_path, timeout=2)
                conn.row_factory = sqlite3.Row
                row = conn.execute("SELECT display_name FROM connections WHERE uid=?", (uid,)).fetchone()
                conn.close()
                if row and row["display_name"]:
                    name = row["display_name"]
                    self._name_cache[uid] = name
                    return name
            except Exception:
                pass
        # 2. Fallback to messages.db display_name
        if self.message_reader:
            try:
                import sqlite3
                conn = sqlite3.connect(self.message_reader.db_path, timeout=2)
                conn.row_factory = sqlite3.Row
                row = conn.execute(
                    "SELECT display_name FROM messages WHERE from_user_id=? AND display_name IS NOT NULL LIMIT 1",
                    (uid,)
                ).fetchone()
                conn.close()
                if row:
                    name = row["display_name"]
            except Exception:
                pass
        self._name_cache[uid] = name
        return name

    def refresh_data(self) -> None:
        """Refresh task data from SQLite with pagination."""
        self._total = self.tasks_reader.count_all_tasks() if self.tasks_reader else 0
        offset = self._page * self.PAGE_SIZE
        tasks = self.tasks_reader.get_all_tasks(limit=self.PAGE_SIZE, offset=offset) if self.tasks_reader else []
        self.clear()
        self._task_data = {}
        self._update_title()

        for task in tasks:
            priority = task.get("priority", "normal")
            color = PRIORITY_COLORS.get(priority, "white")
            p_formatted = f"[{color}]{priority}[/]"

            desc = str(task.get("description") or "--")
            desc_truncated = desc[:300] + "..." if len(desc) > 303 else desc

            task_id = str(task.get("id") or "")
            if task_id:
                self._task_data[task_id] = task

            self.add_row(
                task_id,
                str(task.get("title") or "Untitled"),
                desc_truncated,
                str(task.get("status") or "planned"),
                p_formatted,
                str(self._resolve_name(task.get("requested_by")) or "--"),
                self._resolve_project(task.get("project_id")),
                str(task.get("assigned_to") or "--"),
                str(task.get("assigned_by") or "--"),
                str(task.get("tags") or "--"),
                str(task.get("due_date") or "--") if not task.get("due_date") else _utc_to_local(str(task.get("due_date"))),
                key=task_id
            )

    def on_key(self, event) -> None:
        if event.key in ("delete", "backspace"):
            self._try_delete_selected()
        elif event.key == "pagedown":
            max_page = max(0, (self._total - 1) // self.PAGE_SIZE)
            if self._page < max_page:
                self._page += 1
                self.refresh_data()
        elif event.key == "pageup":
            if self._page > 0:
                self._page -= 1
                self.refresh_data()

    def _try_delete_selected(self) -> None:
        """Attempt to delete the currently selected task row."""
        if not self.tasks_reader:
            return
        row_key, _ = self.coordinate_to_cell_key(self.cursor_coordinate)
        tid = str(row_key.value) if row_key else None
        if not tid or tid not in self._task_data:
            return

        task = self._task_data[tid]
        status = task.get("status", "")
        title = task.get("title", "?")

        if status not in ("done", "cancelled"):
            self.app.notify(f"Only done/cancelled tasks can be deleted ('{title}' is {status})", severity="warning")
            return

        self.app.push_screen(DeleteTaskModal(tid, title, self.tasks_reader))

    @on(DataTable.RowSelected)
    def on_row_selected(self, event: DataTable.RowSelected) -> None:
        """Launch task edit modal on row select."""
        row_key = event.row_key.value
        task = self._task_data.get(row_key, {})
        if task:
            self.app.push_screen(TaskEditModal(task, self.tasks_reader))
