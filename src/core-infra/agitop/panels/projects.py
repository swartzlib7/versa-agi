"""Projects panel — projects with member management from tasks.db."""

import time
from typing import Optional
from textual import on
from textual.app import ComposeResult
from textual.screen import ModalScreen
from textual.containers import Vertical, VerticalScroll, Horizontal
from textual.widgets import DataTable, Static, Button, Input, Select
from textual.widget import Widget

from agitop.data import TasksReader


_TZ = time.strftime("%Z")

STATUS_COLORS = {
    "active": "green",
    "paused": "yellow",
    "archived": "dim",
}

MEMBER_TYPE_ICONS = {
    "agent": "🤖",
    "connection": "👤",
}


class ProjectMembersModal(ModalScreen):
    """Modal showing project details and member list."""

    def __init__(self, project: dict, tasks_reader: Optional[TasksReader], **kwargs):
        super().__init__(**kwargs)
        self._project = project
        self._tasks_reader = tasks_reader

    def compose(self) -> ComposeResult:
        proj = self._project
        pid = proj.get("id", "?")
        name = proj.get("name", "Unnamed")
        desc = proj.get("description") or ""
        status = proj.get("status", "active")
        workspace = proj.get("workspace_path") or "--"

        color = STATUS_COLORS.get(status, "white")

        type_options = [("git", "git"), ("local", "local")]
        platform_options = [("-- None --", ""), ("github", "github"), ("gitlab", "gitlab")]

        with VerticalScroll(id="project-dialog"):
            yield Static(
                f"[bold]#{pid}[/]  [{color}]{status}[/]",
                id="project-dialog-title"
            )
            # ── Full-width fields ──
            yield Static("[b]Name[/]")
            yield Input(
                value=name,
                placeholder="Project name",
                id="project-edit-name",
            )
            yield Static("[b]Description[/]")
            yield Input(
                value=desc,
                placeholder="Project description",
                id="project-edit-desc",
            )
            # ── 2-column grid: 3 rows ──
            with Horizontal(classes="project-field-row"):
                with Vertical(classes="project-field-col"):
                    yield Static("[b]Type[/]")
                    yield Select(
                        type_options,
                        value=proj.get("type") or "local",
                        id="project-edit-type",
                        allow_blank=False,
                    )
                with Vertical(classes="project-field-col"):
                    yield Static("[b]Platform[/]")
                    yield Select(
                        platform_options,
                        value=proj.get("platform") or "",
                        id="project-edit-platform",
                        allow_blank=False,
                    )
            with Horizontal(classes="project-field-row"):
                with Vertical(classes="project-field-col"):
                    yield Static("[b]Remote URL[/]")
                    yield Input(
                        value=proj.get("remote_url") or "",
                        placeholder="https://github.com/org/repo.git",
                        id="project-edit-remote",
                    )
                with Vertical(classes="project-field-col"):
                    yield Static("[b]Access Token[/] [dim](hidden)[/]")
                    yield Input(
                        value=proj.get("access_token") or "",
                        placeholder="ghp_... or glpat-...",
                        id="project-edit-token",
                        password=True,
                    )
            with Horizontal(classes="project-field-row"):
                with Vertical(classes="project-field-col"):
                    yield Static("[b]Branch[/]")
                    yield Input(
                        value=proj.get("branch") or "",
                        placeholder="main",
                        id="project-edit-branch",
                    )
                with Vertical(classes="project-field-col"):
                    yield Static(f"[b]Workspace[/]")
                    yield Static(f"[dim]{workspace}[/]")

            yield Static("", id="project-edit-error")
            yield Static("[bold]─── Members ───[/]")

            members_table = DataTable(id="members-table")
            yield members_table

            yield Static("")
            with Horizontal(classes="task-dialog-buttons"):
                yield Button("Save", variant="success", id="project-dialog-save")
                yield Button("Close", variant="primary", id="project-dialog-close")

    def on_mount(self) -> None:
        table = self.query_one("#members-table", DataTable)
        table.add_columns("", "Name", "Type", "Roles", "Branch", f"Assigned ({_TZ})")

        if self._tasks_reader:
            members = self._tasks_reader.get_project_members(self._project.get("id", 0))
            for m in members:
                icon = MEMBER_TYPE_ICONS.get(m.get("member_type", ""), "?")
                table.add_row(
                    icon,
                    str(m.get("display_name") or m.get("member_id") or "?"),
                    str(m.get("member_type") or "?"),
                    str(m.get("roles") or "contributor"),
                    str(m.get("branch") or "--"),
                    str(m.get("assigned_at") or "--"),
                )
            if not members:
                table.add_row("", "[dim]No members assigned[/]", "", "", "", "")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "project-dialog-close":
            self.app.pop_screen()
        elif event.button.id == "project-dialog-save":
            self._save()

    def _save(self) -> None:
        pid = self._project.get("id")
        error_label = self.query_one("#project-edit-error", Static)
        new_name = self.query_one("#project-edit-name", Input).value.strip()
        new_remote = self.query_one("#project-edit-remote", Input).value.strip()
        new_branch = self.query_one("#project-edit-branch", Input).value.strip()
        new_desc = self.query_one("#project-edit-desc", Input).value.strip()
        new_type = self.query_one("#project-edit-type", Select).value
        new_platform = self.query_one("#project-edit-platform", Select).value
        new_token = self.query_one("#project-edit-token", Input).value.strip()

        if not new_name:
            error_label.update("[bold red]Project name cannot be empty[/]")
            return

        updates = {}
        old_name = self._project.get("name") or ""
        old_remote = self._project.get("remote_url") or ""
        old_branch = self._project.get("branch") or ""
        old_desc = self._project.get("description") or ""
        old_type = self._project.get("type") or "local"
        old_platform = self._project.get("platform") or ""
        old_token = self._project.get("access_token") or ""

        if new_name != old_name:
            updates["name"] = new_name

        if new_remote != old_remote:
            updates["remote_url"] = new_remote if new_remote else None
        if new_branch != old_branch:
            updates["branch"] = new_branch if new_branch else None
        if new_desc != old_desc:
            updates["description"] = new_desc if new_desc else None
        if new_type != old_type:
            updates["type"] = new_type
        if new_platform != old_platform:
            updates["platform"] = new_platform if new_platform else None
        if new_token != old_token:
            updates["access_token"] = new_token if new_token else None

        if not updates:
            self.app.pop_screen()
            return

        if self._tasks_reader and self._tasks_reader.update_project(pid, updates):
            self.app.notify(f"Project updated", severity="information")
            try:
                self.app.query_one(ProjectsPanel).refresh_data()
            except Exception:
                pass
            self.app.pop_screen()
        else:
            error_label.update("[bold red]Failed to save changes[/]")

    def on_key(self, event) -> None:
        if event.key == "escape":
            self.app.pop_screen()


class DeleteProjectModal(ModalScreen):
    """Confirmation modal for deleting an archived project."""

    def __init__(self, project_id: str, project_name: str, tasks_reader: TasksReader, **kwargs):
        super().__init__(**kwargs)
        self.project_id = int(project_id)
        self.project_name = project_name
        self.tasks_reader = tasks_reader

    def compose(self) -> ComposeResult:
        with Vertical(id="msg-dialog"):
            yield Static(
                f"[bold red]Delete Project[/]\n\n"
                f"Permanently delete archived project [bold]{self.project_name}[/] (ID: {self.project_id})?\n\n"
                f"[dim]Any tasks linked to this project will be unlinked (not deleted).[/]",
                id="msg-dialog-header"
            )
            yield Button("Delete", variant="error", id="confirm-delete")
            yield Button("Cancel", variant="primary", id="cancel-delete")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "confirm-delete":
            success, msg = self.tasks_reader.delete_project(self.project_id)
            self.app.pop_screen()
            if success:
                self.app.notify(msg, severity="information")
            else:
                self.app.notify(msg, severity="error")
            # Refresh the projects panel
            try:
                self.app.query_one(ProjectsPanel).refresh_data()
            except Exception:
                pass
        else:
            self.app.pop_screen()

    def on_key(self, event) -> None:
        if event.key == "escape":
            self.app.pop_screen()


class ProjectsPanel(DataTable):
    """Displays projects from tasks.db with member info."""

    def __init__(self, tasks_reader: Optional[TasksReader], **kwargs):
        super().__init__(**kwargs)
        self.tasks_reader = tasks_reader
        self._project_data: dict[str, dict] = {}

    def on_mount(self) -> None:
        self.cursor_type = "row"
        self.border_title = "Projects  │  ENTER for details  │  DEL to delete archived"
        self.add_columns("ID", "Name", "Type", "Platform", "Branch", "Status", "Members")
        self.refresh_data()

    def refresh_data(self) -> None:
        """Refresh project data from SQLite."""
        projects = self.tasks_reader.get_all_projects() if self.tasks_reader else []
        self.clear()
        self._project_data.clear()

        for proj in projects:
            status = proj.get("status", "active")
            color = STATUS_COLORS.get(status, "white")
            s_formatted = f"[{color}]{status}[/]"
            pid = str(proj.get("id") or "")

            self._project_data[pid] = proj

            # Members summary
            members_summary = "--"
            if self.tasks_reader and pid:
                members_summary = self.tasks_reader.get_project_member_summary(int(pid))

            self.add_row(
                pid,
                str(proj.get("name") or "Unnamed"),
                str(proj.get("type") or "local"),
                str(proj.get("platform") or "--"),
                str(proj.get("branch") or "--"),
                s_formatted,
                members_summary,
                key=pid
            )

    def on_key(self, event) -> None:
        if event.key in ("delete", "backspace"):
            self._try_delete_selected()

    def _try_delete_selected(self) -> None:
        """Attempt to delete the currently selected project row."""
        if not self.tasks_reader:
            return
        row_key, _ = self.coordinate_to_cell_key(self.cursor_coordinate)
        pid = str(row_key.value) if row_key else None
        if not pid or pid not in self._project_data:
            return

        proj = self._project_data[pid]
        status = proj.get("status", "")
        name = proj.get("name", "?")

        if status != "archived":
            self.app.notify(f"Only archived projects can be deleted ('{name}' is {status}) - Ask COA to archive or use agictl in the CLI.", severity="warning")
            return

        self.app.push_screen(DeleteProjectModal(pid, name, self.tasks_reader))

    @on(DataTable.RowSelected)
    def on_row_selected(self, event: DataTable.RowSelected) -> None:
        """Launch project members modal on row select."""
        row_key = event.row_key.value
        proj = self._project_data.get(row_key, {})
        if proj:
            self.app.push_screen(ProjectMembersModal(proj, self.tasks_reader))
