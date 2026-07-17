"""Tasks panel — active tasks list with live data."""

import json
import os
import subprocess
import time
import configparser
from typing import Optional
from textual import on
from textual.app import ComposeResult
from textual.screen import ModalScreen
from textual.containers import Vertical, VerticalScroll, Horizontal
from textual.widgets import DataTable, Static, Button, Select, Input, TextArea, TabbedContent, TabPane, Checkbox, RadioSet, RadioButton
from textual.widget import Widget
from rich.markup import escape

from agitop.data import TasksReader
from agitop.data.status_reader import StatusReader
from agitop.feature_flags import UTILITY_MODELS_UI_VISIBLE, SCRIPT_TASKS_UI_VISIBLE
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


def _local_to_utc(local_str: str) -> str:
    """Convert 'YYYY-MM-DD HH:MM:SS' local time to UTC for storage."""
    if not local_str or len(local_str) < 16:
        return local_str
    from datetime import datetime, timezone
    try:
        dt = datetime.strptime(local_str[:19], "%Y-%m-%d %H:%M:%S")
        dt = dt.replace(tzinfo=datetime.now().astimezone().tzinfo).astimezone(timezone.utc)
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except (ValueError, OSError):
        return local_str


def _parse_task_due_parts(due: str) -> tuple[str, str, str]:
    """Split stored due_date into date, hour, minute strings."""
    due = (due or "").strip()
    if not due:
        return "", "00", "00"
    try:
        date_part, time_part = due.split(" ", 1) if " " in due else (due, "00:00:00")
        bits = time_part.split(":")
        hour = (bits[0] if bits else "00").zfill(2)[:2]
        minute = (bits[1] if len(bits) > 1 else "00").zfill(2)[:2]
        return date_part[:10], hour, minute
    except Exception:
        return "", "00", "00"


def _combine_task_due(date_part: str, hour: str, minute: str) -> str:
    """Build due_date string from date + time picker parts."""
    date_part = (date_part or "").strip()
    if not date_part:
        return ""
    hour_s = str(hour or "00").zfill(2)[:2]
    minute_s = str(minute or "00").zfill(2)[:2]
    from datetime import datetime
    datetime.strptime(f"{date_part} {hour_s}:{minute_s}:00", "%Y-%m-%d %H:%M:%S")
    return f"{date_part} {hour_s}:{minute_s}:00"


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

STATUS_DISPLAY = {
    "planned": ("📋", "cyan"),
    "in_progress": ("▶", "yellow"),
    "waiting": ("⏳", "orange1"),
    "blocked": ("🚧", "red"),
    "frozen": ("❄", "blue"),
    "done": ("✓", "green"),
    "cancelled": ("✗", "dim"),
}


def _format_task_status(status: str) -> str:
    key = (status or "planned").strip().lower()
    icon, color = STATUS_DISPLAY.get(key, ("•", "white"))
    return f"[{color}]{icon} {key}[/]"


TASK_KIND_DISPLAY = {
    "standard": ("📋", "std", "dim"),
    "utility": ("🤖", "util", "cyan"),
    "script": ("📜", "scrp", "yellow"),
}


def _format_task_kind(kind: str | None) -> str:
    key = (kind or "standard").strip().lower()
    icon, label, color = TASK_KIND_DISPLAY.get(key, ("•", key[:4], "white"))
    return f"[{color}]{icon} {label}[/]"


class UtilityModelsDisabledModal(ModalScreen):
    """Explain that Utility Models must be enabled in System Settings."""

    CSS = """
    UtilityModelsDisabledModal {
        align: center middle;
        background: $surface 80%;
    }
    #utility-disabled-dialog {
        width: 64;
        height: auto;
        padding: 1 2;
        border: heavy $surface-lighten-2;
        background: $surface;
    }
    #utility-disabled-actions {
        margin-top: 1;
        height: auto;
    }
    #utility-disabled-actions Button {
        width: 1fr;
        margin: 0 1;
        height: 3;
    }
    """

    def compose(self) -> ComposeResult:
        with Vertical(id="utility-disabled-dialog"):
            yield Static(
                "[bold]Utility Models Disabled[/]\n\n"
                "The Utility Models feature is turned off in [bold]System Settings[/].\n\n"
                "Open [cyan]⚙ SYSTEM SETTINGS[/] → [cyan]Utility Models[/] tab and enable "
                "[bold]Utility Models Enabled[/], then save."
            )
            with Horizontal(id="utility-disabled-actions"):
                yield Button("Close", classes="dismiss-btn", variant="default", id="btn-utility-disabled-close")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        self.dismiss(None)

    def on_key(self, event) -> None:
        if event.key == "escape":
            event.stop()
            self.dismiss(None)


class TaskSaveFirstModal(ModalScreen):
    """Explain that Journal/Utility tabs require saving the task first."""

    CSS = """
    TaskSaveFirstModal {
        align: center middle;
        background: $surface 80%;
    }
    #task-save-first-dialog {
        width: 64;
        height: auto;
        padding: 1 2;
        border: heavy $surface-lighten-2;
        background: $surface;
    }
    #task-save-first-actions {
        margin-top: 1;
        height: auto;
    }
    #task-save-first-actions Button {
        width: 1fr;
        margin: 0 1;
        height: 3;
    }
    """

    def compose(self) -> ComposeResult:
        with Vertical(id="task-save-first-dialog"):
            yield Static(
                "[bold]Save Task First[/]\n\n"
                "Progress Journal and Utility settings are available after the task is created.\n\n"
                "Complete the [bold]General[/] tab and press [bold]Save[/], then reopen this task to "
                "configure the journal or utility options."
            )
            with Horizontal(id="task-save-first-actions"):
                yield Button("Close", classes="dismiss-btn", variant="default", id="btn-task-save-first-close")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        self.dismiss(None)

    def on_key(self, event) -> None:
        if event.key == "escape":
            event.stop()
            self.dismiss(None)


def _format_spawn_attempts(count: int, max_attempts: int = 3) -> str:
    """Lifeline retry counter — highlights as budget is consumed."""
    n = max(0, int(count or 0))
    if n == 0:
        return "[dim]0[/]"
    if n >= max_attempts:
        return f"[bold red]{n}[/]"
    if n >= max(1, max_attempts - 1):
        return f"[yellow]{n}[/]"
    return f"[orange1]{n}[/]"

_PROGRESS_PRUNE_DEFAULT = "Wake cycle review:%"

_SETUP_INI_PATHS = [
    "/etc/versa-agi/setup.ini",
    os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))),
        "setup.ini",
    ),
]


def _read_task_max_spawn_attempts() -> int:
    for path in _SETUP_INI_PATHS:
        if not os.path.isfile(path):
            continue
        cfg = configparser.ConfigParser()
        try:
            cfg.read(path)
            return max(1, int(cfg.get("agent", "task_max_spawn_attempts", fallback="3")))
        except (ValueError, configparser.Error):
            return 3
    return 3


_SELECT_NONE = "__none__"


def _utility_feature_enabled() -> bool:
    for path in _SETUP_INI_PATHS:
        if not os.path.isfile(path):
            continue
        cfg = configparser.ConfigParser()
        try:
            cfg.read(path)
            return cfg.get("utility_models", "enabled", fallback="true").lower() == "true"
        except configparser.Error:
            return True
    return True


def _fetch_utility_model_options() -> list[tuple[str, str]]:
    """Enabled Utility Models for Select widgets."""
    options: list[tuple[str, str]] = [("-- Select --", _SELECT_NONE)]
    try:
        proc = subprocess.run(
            ["sudo", "-u", "watchdog", "/usr/local/lib/versa-agi/agictl",
             "utility", "model", "list", "--enabled-only"],
            capture_output=True, text=True, timeout=20,
        )
        for line in reversed((proc.stdout or "").strip().splitlines()):
            if line.strip().startswith("{"):
                data = json.loads(line)
                for row in data.get("utility_models", []):
                    um_id = row.get("id", "")
                    label = row.get("label") or um_id
                    if um_id:
                        options.append((f"{um_id} — {label}", um_id))
                break
    except Exception:
        pass
    return options


def _script_feature_enabled() -> bool:
    """TD-SCRIPT-001 — read [script_tasks] enabled (mirrors _utility_feature_enabled)."""
    for path in _SETUP_INI_PATHS:
        if not os.path.isfile(path):
            continue
        cfg = configparser.ConfigParser()
        try:
            cfg.read(path)
            return cfg.get("script_tasks", "enabled", fallback="true").lower() == "true"
        except configparser.Error:
            return True
    return True


def _resolve_agitools_path(tasks_reader) -> Optional[str]:
    """Resolve the AGi-Tools project workspace path (dynamic — never hardcoded)."""
    if not tasks_reader:
        return None
    try:
        rows = tasks_reader._query(
            "SELECT workspace_path FROM projects WHERE name='AGi-Tools'"
        )
        if rows and rows[0].get("workspace_path"):
            return str(rows[0]["workspace_path"])
    except Exception:
        pass
    return None


def _fetch_script_options(tasks_reader) -> list[tuple[str, str]]:
    """Flat scan of AGi-Tools for *.sh scripts (TD-SCRIPT-001, decision #4: .sh only)."""
    options: list[tuple[str, str]] = [("-- Select --", _SELECT_NONE)]
    try:
        root = _resolve_agitools_path(tasks_reader)
        if root and os.path.isdir(root):
            for fn in sorted(os.listdir(root)):
                if fn.endswith(".sh") and os.path.isfile(os.path.join(root, fn)):
                    options.append((fn, fn))
    except Exception:
        pass
    return options


def _validate_utility_input_paths(raw: str) -> tuple[bool, str]:
    if not raw.strip():
        return True, ""
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return False, "Input files must be a JSON array of paths"
    if not isinstance(parsed, list):
        return False, "Input files must be a JSON array of paths"
    for item in parsed:
        if not isinstance(item, str) or not item.strip():
            return False, "Input files must contain non-empty path strings"
        path = item.strip()
        if "://" in path and not path.lower().startswith("file://"):
            return False, f"Remote URLs are not supported: {path}"
        if path.lower().startswith("file://"):
            path = path[7:]
        real = os.path.expanduser(path)
        if not os.path.isabs(real):
            real = os.path.abspath(real)
        if not os.path.isfile(real):
            return False, f"Input file not found: {real}"
    return True, ""


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
    """Modal dialog to view task details and edit status, due_date, wake_after, project, assignee."""

    def __init__(self, task: dict, tasks_reader: Optional[TasksReader], **kwargs):
        super().__init__(**kwargs)
        self._task_record = task
        self._tasks_reader = tasks_reader
        self._is_new = not task.get("id")
        self._progress_page = 0
        self._progress_page_size = 16
        self._progress_total = 0
        self._progress_entry_notes: dict[str, str] = {}
        self._um_feature_enabled = _utility_feature_enabled()
        self._script_feature_enabled = _script_feature_enabled()

    def compose(self) -> ComposeResult:
        task = self._task_record
        task_id = task.get("id", "?")
        title = task.get("title", "Untitled")
        desc = task.get("description") or "No description."
        current_status = task.get("status", "planned")
        current_due = task.get("due_date") or ""
        current_wake = task.get("wake_after") or ""
        current_project = task.get("project_id")
        current_assignee = task.get("assigned_to") or ""
        task_kind = (task.get("task_kind") or "standard")
        is_utility = task_kind == "utility"
        is_script = task_kind == "script"
        current_um = task.get("utility_model_id") or ""
        current_um_inputs = task.get("utility_input_files") or ""
        current_um_out = task.get("utility_output_override") or ""
        current_um_start = bool(task.get("utility_start_alert"))
        current_um_stop = bool(task.get("utility_stop_alert"))
        current_um_spawn = task.get("utility_spawn_agent") or ""
        um_feature_enabled = self._um_feature_enabled
        # TD-SCRIPT-001 — Script Task fields (reuse start/stop alert columns).
        is_script = is_script and SCRIPT_TASKS_UI_VISIBLE
        current_script = task.get("script_path") or ""
        current_script_params = task.get("script_parameters") or ""
        current_script_interval = task.get("script_interval_seconds")
        script_feature_enabled = self._script_feature_enabled
        script_options = _fetch_script_options(self._tasks_reader)
        if current_script and current_script not in {v for _, v in script_options}:
            script_options.append((current_script, current_script))

        um_options = _fetch_utility_model_options()
        if current_um and current_um not in {v for _, v in um_options}:
            um_options.append((current_um, current_um))

        spawn_options = [("-- None --", _SELECT_NONE)]
        if self._tasks_reader:
            for name in self._tasks_reader.get_agent_names():
                if name.lower() != "watchdog":
                    spawn_options.append((name, name))
        if current_um_spawn and current_um_spawn not in {v for _, v in spawn_options}:
            spawn_options.append((current_um_spawn, current_um_spawn))

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
                if name.lower() == "watchdog":
                    continue
                assignee_options.append((name, name))
        assignee_values = [v for _, v in assignee_options]
        if current_assignee and current_assignee not in assignee_values:
            assignee_options.append((current_assignee, current_assignee))

        due_date_part, due_hour, due_minute = _parse_task_due_parts(
            _utc_to_local(current_due) if current_due else ""
        )
        wake_date_part, wake_hour, wake_minute = _parse_task_due_parts(
            _utc_to_local(current_wake) if current_wake else ""
        )

        with Vertical(id="task-dialog"):
            title_label = "New Task" if self._is_new else f"#{task_id}"
            yield Static(f"[bold]{title_label}[/]", id="task-dialog-title")
            yield Static("", id="task-dialog-title-spacer")

            um_status_msg = (
                "Choose how this task runs when it comes due.\n"
                "Standard wakes the agent; Utility Model and Script run via lifeline "
                "with no agent spawn (as the Assigned To user)."
                if (um_feature_enabled or script_feature_enabled)
                else "Utility Models and Script Tasks are disabled in System Settings."
            )

            with TabbedContent(initial="task-general-tab", id="task-tabs"):
                with TabPane("General", id="task-general-tab"):
                    with Vertical(id="task-general-pane"):
                        with VerticalScroll(id="task-general-scroll"):
                            yield Static("", classes="modal-tab-spacer")
                            yield Static("[b]Title[/b]", classes="modal-form-label")
                            yield Input(
                                value=title,
                                placeholder="Task title",
                                id="task-edit-title",
                            )
                            yield Static("[b]Description[/b]", classes="modal-form-label")
                            yield TextArea(
                                desc,
                                id="task-edit-desc",
                            )
                            with Horizontal(classes="task-schedule-grid"):
                                with Vertical(classes="task-field-col task-schedule-meta-col"):
                                    yield Static("[b]Project[/b]", classes="modal-form-label")
                                    yield Select(
                                        project_options,
                                        value=project_value,
                                        id="task-edit-project",
                                        allow_blank=False,
                                    )
                                    yield Static("[b]Assigned To[/b]", classes="modal-form-label")
                                    yield Select(
                                        assignee_options,
                                        value=current_assignee,
                                        id="task-edit-assignee",
                                        allow_blank=False,
                                    )
                                    yield Static(
                                        "[dim]Who runs it & owns the artifacts — "
                                        "Utility/Script tasks execute as this user.[/]",
                                        classes="modal-form-label task-field-hint",
                                    )
                                    yield Static("[b]Status[/b]", classes="modal-form-label")
                                    yield Select(
                                        TASK_STATUSES,
                                        value=current_status,
                                        id="task-edit-status",
                                        allow_blank=False,
                                    )
                                with Vertical(classes="task-field-col task-schedule-datetime-col"):
                                    yield Static(
                                        f"[b]Due Date[/b] [dim](local — {_TZ}, stored UTC)[/]",
                                        classes="modal-form-label",
                                    )
                                    with Vertical(classes="task-schedule-datetime-box"):
                                        with Vertical(classes="task-schedule-datetime-inner"):
                                            yield Input(
                                                value=due_date_part,
                                                placeholder="YYYY-MM-DD",
                                                id="task-edit-due-date",
                                            )
                                            with Horizontal(classes="task-due-time-row"):
                                                with Vertical(classes="task-time-field"):
                                                    yield Static("[dim]Hour[/]", classes="task-time-label")
                                                    yield Input(
                                                        value=due_hour,
                                                        placeholder="00",
                                                        id="task-edit-due-hour",
                                                        classes="task-time-input",
                                                        max_length=2,
                                                    )
                                                yield Static(":", classes="task-due-sep")
                                                with Vertical(classes="task-time-field"):
                                                    yield Static("[dim]Min[/]", classes="task-time-label")
                                                    yield Input(
                                                        value=due_minute,
                                                        placeholder="00",
                                                        id="task-edit-due-minute",
                                                        classes="task-time-input",
                                                        max_length=2,
                                                    )
                                            yield Button(
                                                "Now", variant="default",
                                                id="btn-task-due-today", classes="task-due-today-btn",
                                            )
                                with Vertical(classes="task-field-col task-schedule-datetime-col"):
                                    yield Static(
                                        f"[b]Wake After[/b] [dim](local — {_TZ})[/]",
                                        classes="modal-form-label",
                                    )
                                    with Vertical(classes="task-schedule-datetime-box"):
                                        with Vertical(classes="task-schedule-datetime-inner"):
                                            yield Input(
                                                value=wake_date_part,
                                                placeholder="YYYY-MM-DD (optional)",
                                                id="task-edit-wake-date",
                                            )
                                            with Horizontal(classes="task-due-time-row"):
                                                with Vertical(classes="task-time-field"):
                                                    yield Static("[dim]Hour[/]", classes="task-time-label")
                                                    yield Input(
                                                        value=wake_hour,
                                                        placeholder="00",
                                                        id="task-edit-wake-hour",
                                                        classes="task-time-input",
                                                        max_length=2,
                                                    )
                                                yield Static(":", classes="task-due-sep")
                                                with Vertical(classes="task-time-field"):
                                                    yield Static("[dim]Min[/]", classes="task-time-label")
                                                    yield Input(
                                                        value=wake_minute,
                                                        placeholder="00",
                                                        id="task-edit-wake-minute",
                                                        classes="task-time-input",
                                                        max_length=2,
                                                    )
                                            yield Button(
                                                "Clear", variant="default",
                                                id="btn-task-wake-clear", classes="task-due-today-btn",
                                            )
                            yield Static("", id="task-edit-error")
                        with Horizontal(classes="task-dialog-buttons"):
                            yield Button("Save", variant="success", id="task-dialog-save")
                            yield Button("Close", classes="dismiss-btn", variant="default", id="task-dialog-close")

                with TabPane("Progress Journal", id="task-journal-tab"):
                    with Vertical(id="task-journal-pane"):
                        yield Static("", classes="modal-tab-spacer")
                        # Fixed 1fr viewport so page size tracks layout height
                        # (DataTable alone collapses to content → only ~4 rows).
                        with Vertical(id="task-progress-scroll"):
                            yield PaginatedDataTable(
                                self._handle_progress_key, id="task-progress-table"
                            )
                        yield Static(
                            "[dim]Double-click or Enter to edit · PgUp/PgDn to navigate · "
                            "agents journal via agictl task progress · "
                            "PU: edit/remove/prune entries here[/]",
                            id="task-progress-hint",
                        )
                        with Horizontal(classes="task-progress-actions"):
                            yield Button(
                                "Edit selected", variant="primary",
                                id="task-progress-edit", disabled=self._is_new,
                            )
                            yield Button(
                                "Remove selected", variant="warning",
                                id="task-progress-remove", disabled=self._is_new,
                            )
                            yield Button(
                                "Prune matching…", variant="error",
                                id="task-progress-prune", disabled=self._is_new,
                            )
                            yield Button(
                                "Close", variant="default",
                                id="task-journal-close", classes="dismiss-btn",
                            )

                if UTILITY_MODELS_UI_VISIBLE or SCRIPT_TASKS_UI_VISIBLE:
                    with TabPane("Utility / Script", id="task-utility-tab"):
                        with Vertical(id="task-utility-pane"):
                            yield Static("", classes="modal-tab-spacer")
                            yield Static(um_status_msg, id="task-utility-status-msg")
                            with VerticalScroll(id="task-utility-scroll"):
                                with Vertical(id="task-utility-header"):
                                    yield Static("[bold cyan]Execution mode[/]")
                                    mode_buttons = [
                                        RadioButton(
                                            "Standard",
                                            id="mode-standard",
                                            value=not is_utility and not is_script,
                                        )
                                    ]
                                    if UTILITY_MODELS_UI_VISIBLE:
                                        mode_buttons.append(RadioButton(
                                            "Utility Model", id="mode-utility", value=is_utility,
                                        ))
                                    if SCRIPT_TASKS_UI_VISIBLE:
                                        mode_buttons.append(RadioButton(
                                            "Script", id="mode-script", value=is_script,
                                        ))
                                    yield RadioSet(*mode_buttons, id="task-edit-mode")
                                # Row 1: Utility Model (left) | Script (right)
                                with Horizontal(classes="task-field-row"):
                                    with Vertical(classes="task-field-col"):
                                        if UTILITY_MODELS_UI_VISIBLE:
                                            yield Static("[b]Utility Model[/b]", classes="modal-form-label")
                                            yield Select(
                                                um_options,
                                                value=current_um if current_um else _SELECT_NONE,
                                                id="task-edit-utility-model",
                                                allow_blank=False,
                                            )
                                    with Vertical(classes="task-field-col"):
                                        if SCRIPT_TASKS_UI_VISIBLE:
                                            yield Static("[b]Script[/b] [dim](.sh in AGi-Tools)[/]", classes="modal-form-label")
                                            yield Select(
                                                script_options,
                                                value=current_script if current_script else _SELECT_NONE,
                                                id="task-edit-script-path",
                                                allow_blank=False,
                                            )
                                # Row 2: Input files (left) | Recurrence interval (right)
                                with Horizontal(classes="task-field-row"):
                                    with Vertical(classes="task-field-col"):
                                        if UTILITY_MODELS_UI_VISIBLE:
                                            yield Static("[b]Input files[/b] [dim](JSON array of paths)[/]", classes="modal-form-label")
                                            yield Input(
                                                value=current_um_inputs,
                                                placeholder='["brand/ref.jpg"]',
                                                id="task-edit-utility-input-files",
                                            )
                                    with Vertical(classes="task-field-col"):
                                        if SCRIPT_TASKS_UI_VISIBLE:
                                            yield Static(
                                                "[b]Recurrence interval[/b] [dim](seconds — blank/0 = once-off)[/]",
                                                classes="modal-form-label",
                                            )
                                            yield Input(
                                                value=(str(current_script_interval) if current_script_interval else ""),
                                                placeholder="e.g. 3600",
                                                id="task-edit-script-interval",
                                            )
                                # Row 3: Output path override (left) | Parameters (right)
                                with Horizontal(classes="task-field-row"):
                                    with Vertical(classes="task-field-col"):
                                        if UTILITY_MODELS_UI_VISIBLE:
                                            yield Static("[b]Output path override[/b] [dim](optional)[/]", classes="modal-form-label")
                                            yield Input(
                                                value=current_um_out,
                                                placeholder=".agent/utility/hero",
                                                id="task-edit-utility-output-override",
                                            )
                                    with Vertical(classes="task-field-col"):
                                        if SCRIPT_TASKS_UI_VISIBLE:
                                            yield Static("[b]Parameters[/b] [dim](passed to the script)[/]", classes="modal-form-label")
                                            yield Input(
                                                value=current_script_params,
                                                placeholder="--flag value",
                                                id="task-edit-script-parameters",
                                            )
                                # Row 4: Spawn agent on completion (left) | VV alerts (right, spaced)
                                # Shared by Utility and Script — both support spawn + alerts.
                                with Horizontal(classes="task-field-row"):
                                    with Vertical(classes="task-field-col"):
                                        if UTILITY_MODELS_UI_VISIBLE or SCRIPT_TASKS_UI_VISIBLE:
                                            yield Static("[b]Spawn agent on completion[/b]", classes="modal-form-label")
                                            yield Select(
                                                spawn_options,
                                                value=current_um_spawn if current_um_spawn else _SELECT_NONE,
                                                id="task-edit-utility-spawn-agent",
                                                allow_blank=False,
                                            )
                                    with Vertical(classes="task-field-col"):
                                        if UTILITY_MODELS_UI_VISIBLE or SCRIPT_TASKS_UI_VISIBLE:
                                            yield Static("[b]VV alerts[/b]", classes="modal-form-label")
                                            with Horizontal(id="task-utility-alert-row"):
                                                with Vertical(classes="task-field-col task-vv-alert-col"):
                                                    yield Checkbox("Start Alert", id="task-edit-utility-start-alert", value=current_um_start)
                                                with Vertical(classes="task-field-col task-vv-alert-col"):
                                                    yield Checkbox("Stop Alert", id="task-edit-utility-stop-alert", value=current_um_stop)
                            with Horizontal(classes="task-dialog-buttons"):
                                yield Button("Save", variant="success", id="task-utility-save")
                                yield Button(
                                    "Close", variant="default",
                                    id="task-utility-close", classes="dismiss-btn",
                                )

    def on_mount(self) -> None:
        tabs = self.query_one("#task-tabs")
        if self._is_new:
            tabs.add_class("task-new-mode")
        # Tab is dimmed only when BOTH deterministic features are disabled.
        if (UTILITY_MODELS_UI_VISIBLE or SCRIPT_TASKS_UI_VISIBLE) \
                and not self._um_feature_enabled and not self._script_feature_enabled:
            tabs.add_class("utility-feature-disabled")
        if UTILITY_MODELS_UI_VISIBLE or SCRIPT_TASKS_UI_VISIBLE:
            self._apply_mode(self._current_mode())
        try:
            table = self.query_one("#task-progress-table", PaginatedDataTable)
            table.cursor_type = "row"
            table.add_columns("When", "Agent", "Note")
            if self._tasks_reader and isinstance(self._task_record.get("id"), int):
                self._progress_total = self._tasks_reader.count_task_progress(self._task_record["id"])
            self.call_after_refresh(self._sync_progress_page_size)
        except Exception:
            pass

    def _current_mode(self) -> str:
        """Selected execution mode from the radio (robust to which buttons exist)."""
        try:
            btn = self.query_one("#task-edit-mode", RadioSet).pressed_button
        except Exception:
            return "standard"
        return {
            "mode-standard": "standard",
            "mode-utility": "utility",
            "mode-script": "script",
        }.get(getattr(btn, "id", None), "standard")

    def _apply_mode(self, mode: str) -> None:
        """Enable only the fields relevant to the selected mode (and enabled feature)."""
        um_on = mode == "utility" and self._um_feature_enabled
        sc_on = mode == "script" and self._script_feature_enabled
        special = um_on or sc_on

        def _set(wid: str, on: bool) -> None:
            try:
                self.query_one(wid).disabled = not on
            except Exception:
                pass

        _set("#task-edit-utility-model", um_on)
        _set("#task-edit-utility-input-files", um_on)
        _set("#task-edit-utility-output-override", um_on)
        _set("#task-edit-utility-spawn-agent", special)
        _set("#task-edit-script-path", sc_on)
        _set("#task-edit-script-parameters", sc_on)
        _set("#task-edit-script-interval", sc_on)
        _set("#task-edit-utility-start-alert", special)
        _set("#task-edit-utility-stop-alert", special)

    @on(RadioSet.Changed, "#task-edit-mode")
    def _on_mode_changed(self, event: RadioSet.Changed) -> None:
        event.stop()
        self._apply_mode(self._current_mode())

    def on_resize(self, event) -> None:
        self._sync_progress_page_size()

    @on(TabbedContent.TabActivated, "#task-tabs")
    def _on_task_tab_activated(self, event: TabbedContent.TabActivated) -> None:
        if event.tab.id == "task-journal-tab":
            if self._is_new:
                self.query_one("#task-tabs", TabbedContent).active = "task-general-tab"
                self.app.push_screen(TaskSaveFirstModal())
                return
            self.call_after_refresh(self._sync_progress_page_size)
        elif (UTILITY_MODELS_UI_VISIBLE or SCRIPT_TASKS_UI_VISIBLE) and event.tab.id == "task-utility-tab":
            if self._is_new:
                self.query_one("#task-tabs", TabbedContent).active = "task-general-tab"
                self.app.push_screen(TaskSaveFirstModal())
            elif not self._um_feature_enabled and not self._script_feature_enabled:
                self.query_one("#task-tabs", TabbedContent).active = "task-general-tab"
                self.app.push_screen(UtilityModelsDisabledModal())

    def _progress_rows_per_page(self) -> int:
        """Fit one page of journal rows to the visible table height."""
        try:
            table = self.query_one("#task-progress-table", PaginatedDataTable)
        except Exception:
            return self._progress_page_size
        height = table.size.height
        if height <= 0:
            return self._progress_page_size
        # Header row + border/chrome; each data row is one terminal line.
        return max(1, height - 3)

    def _sync_progress_page_size(self) -> None:
        """Recalculate page size from layout and reload if it changed."""
        new_size = self._progress_rows_per_page()
        if new_size == self._progress_page_size:
            self._update_progress_table()
            return
        self._progress_page_size = new_size
        if self._progress_total:
            max_page = max(0, (self._progress_total - 1) // self._progress_page_size)
            self._progress_page = min(self._progress_page, max_page)
        self._update_progress_table()

    def _ensure_progress_columns(self, table: PaginatedDataTable) -> None:
        if not table.columns:
            table.add_columns("When", "Agent", "Note")

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
            table = self.query_one("#task-progress-table", PaginatedDataTable)
            try:
                table.clear(columns=False)
            except TypeError:
                table.clear()
            self._ensure_progress_columns(table)
            task_id = self._task_record.get("id")
            if not self._tasks_reader or not isinstance(task_id, int):
                table.border_title = "Progress Journal"
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
        if event.button.id in ("task-dialog-close", "task-journal-close", "task-utility-close"):
            self.app.pop_screen()
        elif event.button.id in ("task-dialog-save", "task-utility-save"):
            self._save()
        elif event.button.id == "btn-task-due-today":
            from datetime import datetime
            now = datetime.now()
            self.query_one("#task-edit-due-date", Input).value = now.strftime("%Y-%m-%d")
            self.query_one("#task-edit-due-hour", Input).value = now.strftime("%H")
            self.query_one("#task-edit-due-minute", Input).value = now.strftime("%M")
        elif event.button.id == "btn-task-wake-clear":
            self.query_one("#task-edit-wake-date", Input).value = ""
            self.query_one("#task-edit-wake-hour", Input).value = "00"
            self.query_one("#task-edit-wake-minute", Input).value = "00"
        elif event.button.id == "task-progress-edit":
            self._edit_selected_progress()
        elif event.button.id == "task-progress-remove":
            self._remove_selected_progress()
        elif event.button.id == "task-progress-prune":
            self._prune_progress()

    @on(DataTable.RowSelected, "#task-progress-table")
    def _on_progress_row_selected(self, event: DataTable.RowSelected) -> None:
        """Double-click or Enter on a row opens the edit modal."""
        self._edit_selected_progress()

    def _get_selected_progress_entry_id(self) -> Optional[str]:
        table = self.query_one("#task-progress-table", PaginatedDataTable)
        if table.row_count == 0:
            return None
        try:
            row_key, _ = table.coordinate_to_cell_key(table.cursor_coordinate)
        except Exception:
            return None
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
        error_label = self.query_one("#task-edit-error", Static)
        try:
            new_due_local = _combine_task_due(
                self.query_one("#task-edit-due-date", Input).value,
                self.query_one("#task-edit-due-hour", Input).value,
                self.query_one("#task-edit-due-minute", Input).value,
            )
            new_due = _local_to_utc(new_due_local) if new_due_local else ""
            wake_date_val = self.query_one("#task-edit-wake-date", Input).value.strip()
            if wake_date_val:
                new_wake_local = _combine_task_due(
                    wake_date_val,
                    self.query_one("#task-edit-wake-hour", Input).value,
                    self.query_one("#task-edit-wake-minute", Input).value,
                )
                new_wake = _local_to_utc(new_wake_local) if new_wake_local else None
            else:
                new_wake = None
        except ValueError:
            error_label.update("[bold red]Due/wake dates must be YYYY-MM-DD with valid time[/]")
            return
        new_project = self.query_one("#task-edit-project", Select).value
        new_assignee = self.query_one("#task-edit-assignee", Select).value

        if isinstance(new_assignee, str) and new_assignee.lower() == "watchdog":
            error_label.update("[bold red]Tasks cannot be assigned to watchdog[/]")
            return

        # Validate: title is required
        if not new_title:
            error_label.update("[bold red]Title cannot be empty[/]")
            return

        # Validate: planned requires due_date
        if new_status == "planned" and not new_due:
            error_label.update("[bold red]Due date is required for planned tasks[/]")
            return

        if not task_id:
            new_id = self._tasks_reader.add_task(
                new_title,
                assigned_to=new_assignee or "coa",
                project_id=new_project if new_project else None,
                description=new_desc or None,
            ) if self._tasks_reader else None
            if not new_id:
                error_label.update("[bold red]Failed to create task[/]")
                return
            post: dict = {}
            if new_status and new_status != "planned":
                post["status"] = new_status
            if new_due:
                post["due_date"] = new_due
            if new_wake:
                post["wake_after"] = new_wake
            if post:
                self._tasks_reader.update_task(new_id, post)
            self.app.notify(f"Task #{new_id} created", title="agitop")
            try:
                self.app.query_one("#tasks-panel", TasksPanel).refresh_data()
            except Exception:
                pass
            self.app.pop_screen()
            return

        updates = {}
        old_title = self._task_record.get("title", "")
        old_desc = self._task_record.get("description") or ""
        old_status = self._task_record.get("status", "planned")
        old_due = self._task_record.get("due_date") or ""
        old_wake = self._task_record.get("wake_after") or ""
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
        if new_wake != (old_wake or None):
            updates["wake_after"] = new_wake
        if new_project != old_project:
            updates["project_id"] = new_project if new_project else None
        if new_assignee != old_assignee:
            updates["assigned_to"] = new_assignee if new_assignee else None

        if UTILITY_MODELS_UI_VISIBLE or SCRIPT_TASKS_UI_VISIBLE:
            mode = self._current_mode()
            prev_kind = self._task_record.get("task_kind") or "standard"
            um_start = self.query_one("#task-edit-utility-start-alert", Checkbox).value
            um_stop = self.query_one("#task-edit-utility-stop-alert", Checkbox).value

            # ── Qualifying-setting gate for headless (non-Standard) tasks ──
            # lifeline only auto-runs a Utility/Script task when it is selectable
            # by the due-query: status='planned', a due_date in the past/now, and
            # an assigned executor (the OS user the job runs as). Enforce this only
            # when the task is actually being set to run (status='planned') — a
            # done/blocked/cancelled task is not eligible to fire, so its General
            # tab must stay freely editable without tripping the gate.
            _is_nonstandard = (
                (mode == "utility" and self._um_feature_enabled)
                or (mode == "script" and self._script_feature_enabled)
            )
            if _is_nonstandard and new_status == "planned":
                _mode_label = "Utility" if mode == "utility" else "Script"
                if not new_assignee:
                    error_label.update(
                        f"[bold red]{_mode_label} tasks need an assignee — it's the user "
                        f"that runs the job and owns the output.[/]"
                    )
                    return
                if not new_due:
                    error_label.update(
                        f"[bold red]{_mode_label} tasks need a due date — lifeline only runs "
                        f"them once the due date is now or in the past.[/]"
                    )
                    return

            def _clear_utility() -> None:
                updates["utility_model_id"] = None
                updates["utility_input_files"] = None
                updates["utility_output_override"] = None

            def _clear_script() -> None:
                updates["script_path"] = None
                updates["script_parameters"] = None
                updates["script_interval_seconds"] = None

            if mode == "utility" and self._um_feature_enabled:
                um_id = self.query_one("#task-edit-utility-model", Select).value
                um_inputs = self.query_one("#task-edit-utility-input-files", Input).value.strip()
                um_out = self.query_one("#task-edit-utility-output-override", Input).value.strip()
                um_spawn = self.query_one("#task-edit-utility-spawn-agent", Select).value
                if not um_id or um_id == _SELECT_NONE:
                    error_label.update("[bold red]Utility Model is required for Utility Tasks[/]")
                    return
                ok_inputs, input_err = _validate_utility_input_paths(um_inputs)
                if not ok_inputs:
                    error_label.update(f"[bold red]{escape(input_err)}[/]")
                    return
                updates["task_kind"] = "utility"
                updates["utility_model_id"] = str(um_id)
                updates["utility_input_files"] = um_inputs or None
                updates["utility_output_override"] = um_out or None
                updates["utility_start_alert"] = 1 if um_start else 0
                updates["utility_stop_alert"] = 1 if um_stop else 0
                updates["utility_spawn_agent"] = (
                    um_spawn if um_spawn and um_spawn != _SELECT_NONE else None
                )
                _clear_script()
            elif mode == "script" and self._script_feature_enabled:
                sc_path = self.query_one("#task-edit-script-path", Select).value
                sc_params = self.query_one("#task-edit-script-parameters", Input).value.strip()
                sc_interval_raw = self.query_one("#task-edit-script-interval", Input).value.strip()
                if not sc_path or sc_path == _SELECT_NONE:
                    error_label.update("[bold red]A script is required for Script Tasks[/]")
                    return
                if sc_interval_raw and not sc_interval_raw.isdigit():
                    error_label.update("[bold red]Recurrence interval must be a whole number of seconds[/]")
                    return
                updates["task_kind"] = "script"
                updates["script_path"] = str(sc_path)
                updates["script_parameters"] = sc_params or None
                updates["script_interval_seconds"] = int(sc_interval_raw) if sc_interval_raw else None
                updates["utility_start_alert"] = 1 if um_start else 0
                updates["utility_stop_alert"] = 1 if um_stop else 0
                sc_spawn = self.query_one("#task-edit-utility-spawn-agent", Select).value
                updates["utility_spawn_agent"] = (
                    sc_spawn if sc_spawn and sc_spawn != _SELECT_NONE else None
                )
                _clear_utility()
            elif mode == "standard":
                # Only convert away from a deterministic kind whose UI is actually
                # visible — never silently wipe a mode the UI cannot represent.
                if prev_kind == "utility" and UTILITY_MODELS_UI_VISIBLE and self._um_feature_enabled:
                    updates["task_kind"] = "standard"
                    _clear_utility()
                    updates["utility_start_alert"] = 0
                    updates["utility_stop_alert"] = 0
                    updates["utility_spawn_agent"] = None
                elif prev_kind == "script" and SCRIPT_TASKS_UI_VISIBLE and self._script_feature_enabled:
                    updates["task_kind"] = "standard"
                    _clear_script()
                    updates["utility_start_alert"] = 0
                    updates["utility_stop_alert"] = 0
                    updates["utility_spawn_agent"] = None

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

    CSS = """
    DeleteTaskModal {
        align: center middle;
        background: $surface 80%;
    }
    #task-delete-dialog {
        width: 64;
        height: auto;
        padding: 1 2;
        border: heavy $error;
        background: $surface;
    }
    #task-delete-actions {
        margin-top: 1;
        height: auto;
        align: center middle;
    }
    #task-delete-actions Button {
        width: 1fr;
        margin: 0 1;
        min-width: 16;
        height: 3;
    }
    """

    def __init__(self, task_id: str, task_title: str, tasks_reader: TasksReader, **kwargs):
        super().__init__(**kwargs)
        self.task_id = int(task_id)
        self.task_title = task_title
        self.tasks_reader = tasks_reader

    def compose(self) -> ComposeResult:
        title = escape(self.task_title)
        if len(title) > 120:
            title = title[:117] + "..."
        with Vertical(id="task-delete-dialog"):
            yield Static(f"[bold red]⚠ Delete Task #{self.task_id}[/]\n")
            yield Static(f"[dim]{title}[/]\n")
            yield Static(
                "Permanently deletes this task and its progress journal.\n\n"
                "[bold]This cannot be undone.[/]"
            )
            with Horizontal(id="task-delete-actions"):
                yield Button("Delete", variant="error", id="btn-task-delete-confirm")
                yield Button("Close", classes="dismiss-btn", variant="default", id="btn-task-delete-cancel")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        if event.button.id == "btn-task-delete-confirm":
            success, msg = self.tasks_reader.delete_task(self.task_id)
            self.dismiss(None)
            if success:
                self.app.notify(msg, severity="information")
            else:
                self.app.notify(msg, severity="error")
            try:
                self.app.query_one("#tasks-panel", TasksPanel).refresh_data()
            except Exception:
                pass
        else:
            self.dismiss(None)

    def on_key(self, event) -> None:
        if event.key == "escape":
            event.stop()
            self.dismiss(None)


class TasksPanel(Widget):
    """Tasks list with extending table and New/Edit/Delete actions."""

    PAGE_SIZE = 50

    def __init__(
        self,
        tasks_reader: Optional[TasksReader],
        message_reader=None,
        status_reader: Optional[StatusReader] = None,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self.tasks_reader = tasks_reader
        self.message_reader = message_reader
        self.status_reader = status_reader
        self._page = 0
        self._total = 0
        self._max_spawn_attempts = _read_task_max_spawn_attempts()
        self._task_data: dict[str, dict] = {}
        self._project_cache: dict[int, str] = {}
        self._name_cache: dict[str, str] = {}
        self.table = DataTable(id="tasks-table")

    def compose(self) -> ComposeResult:
        with Vertical(id="tasks-panel-body", classes="work-tab-body"):
            yield self.table
            with Horizontal(classes="work-tab-actions"):
                yield Button("New", variant="success", id="btn-tasks-new")
                yield Button("Edit", variant="primary", id="btn-tasks-edit", disabled=True)
                yield Button("Delete", variant="error", id="btn-tasks-delete", disabled=True)
                yield Button("Quit", variant="default", id="btn-tasks-quit", classes="btn-quit")

    def on_mount(self) -> None:
        self._update_title()
        self.table.cursor_type = "row"
        self.table.add_column("ID", width=5)
        self.table.add_column("Type", width=8)
        self.table.add_column("Title", width=48)
        self.table.add_column("Desc", width=40)
        self.table.add_column("Status", width=14)
        self.table.add_column("Spawns", width=7)
        self.table.add_column("Priority", width=9)
        self.table.add_column("Requested By", width=14)
        self.table.add_column("Project", width=16)
        self.table.add_column("Assign To", width=12)
        self.table.add_column("Assign By", width=12)
        self.table.add_column("Tags", width=12)
        self.table.add_column(f"Due ({_TZ})", width=19)
        self.refresh_data()

    def _update_action_buttons(self) -> None:
        has_row = bool(self._selected_task_id())
        try:
            self.query_one("#btn-tasks-edit", Button).disabled = not has_row
            self.query_one("#btn-tasks-delete", Button).disabled = not has_row
        except Exception:
            pass

    def _selected_task_id(self) -> str | None:
        try:
            row_key, _ = self.table.coordinate_to_cell_key(self.table.cursor_coordinate)
            if row_key and row_key.value:
                return str(row_key.value)
        except Exception:
            pass
        return None

    @on(DataTable.RowHighlighted, "#tasks-table")
    def _on_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        self._update_action_buttons()

    @on(Button.Pressed)
    def _on_action_button(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-tasks-new":
            self.app.push_screen(TaskEditModal({}, self.tasks_reader))
        elif event.button.id == "btn-tasks-edit":
            tid = self._selected_task_id()
            task = self._task_data.get(tid or "", {})
            if task:
                self.app.push_screen(TaskEditModal(task, self.tasks_reader))
        elif event.button.id == "btn-tasks-delete":
            self._try_delete_selected()
        elif event.button.id == "btn-tasks-quit":
            self.app.exit()

    def _update_title(self) -> None:
        total_pages = max(1, (self._total + self.PAGE_SIZE - 1) // self.PAGE_SIZE)
        current_page = self._page + 1
        self.table.border_title = (
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
        self.table.clear()
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

            self.table.add_row(
                task_id,
                _format_task_kind(task.get("task_kind")),
                str(task.get("title") or "Untitled"),
                desc_truncated,
                _format_task_status(str(task.get("status") or "planned")),
                _format_spawn_attempts(task.get("spawn_attempts", 0), self._max_spawn_attempts),
                p_formatted,
                str(self._resolve_name(task.get("requested_by")) or "--"),
                self._resolve_project(task.get("project_id")),
                str(task.get("assigned_to") or "--"),
                str(task.get("assigned_by") or "--"),
                str(task.get("tags") or "--"),
                str(task.get("due_date") or "--") if not task.get("due_date") else _utc_to_local(str(task.get("due_date"))),
                key=task_id
            )
        self._update_action_buttons()

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
        row_key, _ = self.table.coordinate_to_cell_key(self.table.cursor_coordinate)
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

    @on(DataTable.RowSelected, "#tasks-table")
    def on_row_selected(self, event: DataTable.RowSelected) -> None:
        """Launch task edit modal on row select."""
        row_key = event.row_key.value
        task = self._task_data.get(row_key, {})
        if task:
            self.app.push_screen(TaskEditModal(task, self.tasks_reader))
