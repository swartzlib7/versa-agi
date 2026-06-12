"""Tasks panel — active tasks list with live data."""

import time
from typing import Optional
from textual import on
from textual.app import ComposeResult
from textual.screen import ModalScreen
from textual.containers import Vertical, VerticalScroll, Horizontal
from textual.widgets import DataTable, Static, Button, Select, Input, TextArea

from agitop.data import TasksReader

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


class TaskEditModal(ModalScreen):
    """Modal dialog to view task details and edit status/due_date/project/assignee."""

    def __init__(self, task: dict, tasks_reader: Optional[TasksReader], **kwargs):
        super().__init__(**kwargs)
        self._task_record = task
        self._tasks_reader = tasks_reader

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

            # ── Progress Journal (read-only) ──
            # Agent-authored breadcrumbs via 'agictl task progress <id> "..."' —
            # the cross-cycle continuity carrier, surfaced here for the PU.
            progress = []
            if self._tasks_reader and isinstance(task.get("id"), int):
                progress = self._tasks_reader.get_task_progress(task["id"])
            yield Static("")
            yield Static(
                f"[b]Progress Journal[/b] [dim]({len(progress)} "
                f"entr{'y' if len(progress) == 1 else 'ies'} — newest first)[/]"
            )
            with VerticalScroll(id="task-progress-scroll"):
                if progress:
                    from rich.markup import escape
                    for entry in reversed(progress):
                        ts = _utc_to_local(str(entry.get("created_at") or ""))[:16]
                        author = entry.get("agent_name") or "?"
                        note = escape(str(entry.get("note") or ""))
                        yield Static(
                            f"[cyan]{ts}[/] [yellow]{author}[/] — {note}",
                            classes="task-progress-entry",
                        )
                else:
                    yield Static(
                        "[dim]No entries yet — agents journal progress with: "
                        "agictl task progress <id> \"DONE: ... NEXT: ...\"[/]"
                    )

            yield Static("", id="task-edit-error")
            with Horizontal(classes="task-dialog-buttons"):
                yield Button("Save", variant="success", id="task-dialog-save")
                yield Button("Cancel", variant="primary", id="task-dialog-close")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "task-dialog-close":
            self.app.pop_screen()
        elif event.button.id == "task-dialog-save":
            self._save()

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
            yield Button("Cancel", variant="primary", id="cancel-delete")

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
