"""Projects panel — projects with member management from tasks.db."""

import json
import subprocess
import time
from typing import Optional
from textual import on
from textual.app import ComposeResult
from textual.screen import ModalScreen
from textual.containers import Vertical, VerticalScroll, Horizontal
from textual.widgets import DataTable, Static, Button, Input, Select

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


def _run_agictl(args: list[str], timeout: int = 30) -> tuple[bool, dict, str]:
    """Run `sudo agictl <args>` and parse the trailing JSON line."""
    try:
        proc = subprocess.run(
            ["sudo", "agictl"] + args,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return False, {}, "command timed out"
    except Exception as e:
        return False, {}, str(e)

    data = {}
    if proc.stdout:
        for line in reversed(proc.stdout.strip().splitlines()):
            line = line.strip()
            if line.startswith("{"):
                try:
                    data = json.loads(line)
                    break
                except Exception:
                    continue
    ok = bool(data.get("success")) if data else (proc.returncode == 0)
    err = ""
    if not ok:
        err = data.get("error") or (proc.stderr.strip() or "Unknown error")
    return ok, data, err


class ProjectAssignAgentModal(ModalScreen[Optional[str]]):
    """Pick an agent to assign — provisions workspace via agictl project assign."""

    CSS = """
    ProjectAssignAgentModal {
        align: center middle;
        background: $surface 80%;
    }
    #project-assign-dialog {
        width: 56;
        height: auto;
        padding: 1 2;
        border: heavy $primary;
        background: $surface;
    }
    #project-assign-actions {
        margin-top: 1;
        height: auto;
        align: center middle;
    }
    #project-assign-actions Button {
        width: 1fr;
        margin: 0 1;
        min-width: 16;
        height: 3;
    }
    """

    def __init__(self, project_name: str, agent_options: list[tuple[str, str]], **kwargs):
        super().__init__(**kwargs)
        self.project_name = project_name
        self.agent_options = agent_options

    def compose(self) -> ComposeResult:
        with Vertical(id="project-assign-dialog"):
            yield Static(
                f"[bold]Assign agent to {self.project_name}[/]\n\n"
                "[dim]Git projects: clone into workspace/<project> and create an agent branch.\n"
                "Local projects: symlink the COA workspace into the agent workspace.[/]"
            )
            if not self.agent_options:
                yield Static("[yellow]No unassigned agents available[/]")
            else:
                yield Static("[b]Agent[/b]")
                yield Select(self.agent_options, id="project-assign-agent", allow_blank=False)
            with Horizontal(id="project-assign-actions"):
                yield Button("Assign", variant="success", id="btn-project-assign-confirm")
                yield Button("Cancel", classes="dismiss-btn", variant="default", id="btn-project-assign-cancel")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        if event.button.id == "btn-project-assign-confirm":
            if not self.agent_options:
                self.dismiss(None)
                return
            agent = self.query_one("#project-assign-agent", Select).value
            self.dismiss(str(agent) if agent else None)
        else:
            self.dismiss(None)


class ProjectUnassignAgentModal(ModalScreen[bool]):
    """Confirm agent removal — freezes tasks and cleans workspace via agictl."""

    CSS = """
    ProjectUnassignAgentModal {
        align: center middle;
        background: $surface 80%;
    }
    #project-unassign-dialog {
        width: 64;
        height: auto;
        padding: 1 2;
        border: heavy $warning;
        background: $surface;
    }
    #project-unassign-actions {
        margin-top: 1;
        height: auto;
        align: center middle;
    }
    #project-unassign-actions Button {
        width: 1fr;
        margin: 0 1;
        min-width: 16;
        height: 3;
    }
    """

    def __init__(self, project_name: str, agent_name: str, **kwargs):
        super().__init__(**kwargs)
        self.project_name = project_name
        self.agent_name = agent_name

    def compose(self) -> ComposeResult:
        with Vertical(id="project-unassign-dialog"):
            yield Static(
                f"[bold yellow]Remove {self.agent_name} from {self.project_name}?[/]\n\n"
                "This will:\n"
                "· Freeze and unassign their active tasks on this project\n"
                "· Remove the project workspace directory or symlink\n"
                "· Delete their project membership and project memory\n\n"
                "[bold]This cannot be undone.[/]"
            )
            with Horizontal(id="project-unassign-actions"):
                yield Button("Unassign agent", variant="error", id="btn-project-unassign-confirm")
                yield Button("Cancel", classes="dismiss-btn", variant="default", id="btn-project-unassign-cancel")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        self.dismiss(event.button.id == "btn-project-unassign-confirm")


class ProjectMembersModal(ModalScreen):
    """Modal showing project details and member list."""

    def __init__(self, project: dict, tasks_reader: Optional[TasksReader], **kwargs):
        super().__init__(**kwargs)
        self._project = project
        self._tasks_reader = tasks_reader
        self._member_rows: dict[str, dict] = {}

    def compose(self) -> ComposeResult:
        proj = self._project
        pid = proj.get("id", "?")
        name = proj.get("name", "Unnamed")
        desc = proj.get("description") or ""
        status = proj.get("status", "active")
        workspace = proj.get("workspace_path") or "--"
        proj_type = proj.get("type") or "local"

        color = STATUS_COLORS.get(status, "white")

        type_options = [("git", "git"), ("local", "local")]
        platform_options = [("-- None --", ""), ("github", "github"), ("gitlab", "gitlab")]

        with VerticalScroll(id="project-dialog"):
            # Resolve game name if linked
            game_id = proj.get("game_id")
            game_label = ""
            if game_id and self._tasks_reader:
                game_name = self._tasks_reader.get_game_name(game_id)
                game_label = f"  │  🎯 {game_name}"

            yield Static(
                f"[bold]#{pid}[/]  [{color}]{status}[/]{game_label}",
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
                        value=proj_type,
                        id="project-edit-type",
                        allow_blank=False,
                    )
                with Vertical(classes="project-field-col", id="project-platform-col"):
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
                    yield Static("[b]COA Workspace[/]")
                    yield Static(f"[dim]{workspace}[/]", id="project-coa-workspace")

            yield Static("", id="project-edit-error")
            yield Static("[bold]─── Members ───[/]")

            members_table = DataTable(id="members-table")
            yield members_table

            with Horizontal(classes="project-members-actions"):
                yield Button("Assign agent", variant="primary", id="project-member-assign")
                yield Button("Unassign agent", variant="warning", id="project-member-remove")

            yield Static("")
            with Horizontal(classes="task-dialog-buttons"):
                yield Button("Save", variant="success", id="project-dialog-save")
                yield Button("Close", variant="default", id="project-dialog-close", classes="dismiss-btn")

    def on_mount(self) -> None:
        self._sync_platform_visibility()
        self._refresh_members_table()

    def _sync_platform_visibility(self) -> None:
        try:
            proj_type = self.query_one("#project-edit-type", Select).value
            platform_col = self.query_one("#project-platform-col")
            platform_col.display = proj_type == "git"
        except Exception:
            pass

    @on(Select.Changed, "#project-edit-type")
    def on_type_changed(self, event: Select.Changed) -> None:
        self._sync_platform_visibility()

    def _refresh_members_table(self) -> None:
        table = self.query_one("#members-table", DataTable)
        table.cursor_type = "row"
        if not table.columns:
            table.add_columns("", "Name", "Type", "Roles", "Branch", f"Assigned ({_TZ})")
        table.clear()
        self._member_rows = {}

        if not self._tasks_reader:
            return

        members = self._tasks_reader.get_project_members(self._project.get("id", 0))
        for m in members:
            member_type = str(m.get("member_type") or "?")
            member_id = str(m.get("member_id") or "")
            row_key = f"{member_type}:{member_id}"
            self._member_rows[row_key] = m
            icon = MEMBER_TYPE_ICONS.get(member_type, "?")
            table.add_row(
                icon,
                str(m.get("display_name") or member_id or "?"),
                member_type,
                str(m.get("roles") or "contributor"),
                str(m.get("branch") or "--"),
                str(m.get("assigned_at") or "--"),
                key=row_key,
            )
        if not members:
            table.add_row("", "[dim]No members assigned[/]", "", "", "", "")

    def _get_selected_member(self) -> Optional[dict]:
        table = self.query_one("#members-table", DataTable)
        row_key, _ = table.coordinate_to_cell_key(table.cursor_coordinate)
        key = str(row_key.value) if row_key and row_key.value else ""
        return self._member_rows.get(key)

    def _assign_agent(self) -> None:
        if not self._tasks_reader:
            return
        project_name = self._project.get("name") or ""
        if not project_name:
            return

        assigned = {
            m.get("member_id")
            for m in self._tasks_reader.get_project_members(self._project.get("id", 0))
            if m.get("member_type") == "agent"
        }
        options = [
            (name, name)
            for name in self._tasks_reader.get_agent_names()
            if name not in assigned
        ]

        def _on_pick(agent_name: Optional[str]) -> None:
            if not agent_name:
                return
            ok, data, err = _run_agictl(
                ["project", "assign", project_name, "--agent", agent_name],
                timeout=150,
            )
            if ok:
                ws = data.get("workspace", "")
                branch = data.get("branch", "")
                detail = f" · {branch}" if branch else ""
                if ws:
                    detail += f" · {ws}"
                self.app.notify(f"Assigned {agent_name}{detail}", title="agitop")
                self._refresh_members_table()
                try:
                    self.app.query_one(ProjectsPanel).refresh_data()
                except Exception:
                    pass
            else:
                self.app.notify(err or "Assign failed", severity="error")

        self.app.push_screen(ProjectAssignAgentModal(project_name, options), _on_pick)

    def _remove_agent(self) -> None:
        member = self._get_selected_member()
        if not member:
            self.app.notify("Select a member row first", severity="warning")
            return
        if member.get("member_type") != "agent":
            self.app.notify("Only agent members can be removed here", severity="warning")
            return

        agent_name = str(member.get("member_id") or "")
        roles = str(member.get("roles") or "")
        if "owner" in roles:
            self.app.notify("Cannot remove project owner — transfer ownership first", severity="warning")
            return

        project_name = self._project.get("name") or ""

        def _on_confirm(confirmed: bool) -> None:
            if not confirmed:
                return
            ok, data, err = _run_agictl(
                ["project", "unassign", project_name, "--agent", agent_name],
                timeout=60,
            )
            if ok:
                frozen = data.get("tasks_frozen", 0)
                msg = f"Removed {agent_name}"
                if frozen:
                    msg += f" · {frozen} task(s) frozen and unassigned"
                self.app.notify(msg, title="agitop")
                self._refresh_members_table()
                try:
                    self.app.query_one(ProjectsPanel).refresh_data()
                except Exception:
                    pass
            else:
                self.app.notify(err or "Remove failed", severity="error")

        self.app.push_screen(
            ProjectUnassignAgentModal(project_name, agent_name),
            _on_confirm,
        )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "project-dialog-close":
            self.app.pop_screen()
        elif event.button.id == "project-dialog-save":
            self._save()
        elif event.button.id == "project-member-assign":
            self._assign_agent()
        elif event.button.id == "project-member-remove":
            self._remove_agent()

    def _save(self) -> None:
        pid = self._project.get("id")
        error_label = self.query_one("#project-edit-error", Static)
        new_name = self.query_one("#project-edit-name", Input).value.strip()
        new_remote = self.query_one("#project-edit-remote", Input).value.strip()
        new_branch = self.query_one("#project-edit-branch", Input).value.strip()
        new_desc = self.query_one("#project-edit-desc", Input).value.strip()
        new_type = self.query_one("#project-edit-type", Select).value
        new_platform = (
            self.query_one("#project-edit-platform", Select).value
            if new_type == "git"
            else ""
        )
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
            if new_type != "git":
                updates["platform"] = None
        if new_type == "git" and new_platform != old_platform:
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
            yield Button("Cancel", classes="dismiss-btn", variant="default", id="cancel-delete")

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
        self.add_column("ID", width=5)
        self.add_column("Name", width=24)
        self.add_column("Desc", width=80)
        self.add_column("Status", width=10)
        self.add_column("Type", width=8)
        self.add_column("Platform", width=10)
        self.add_column("Branch", width=12)
        self.add_column("Game", width=16)
        self.add_column("Members", width=12)
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

            # Game name resolution
            game_name = "--"
            game_id = proj.get("game_id")
            if game_id and self.tasks_reader:
                game_name = self.tasks_reader.get_game_name(game_id)

            desc = str(proj.get("description") or "--")
            desc_truncated = desc[:300] + "..." if len(desc) > 303 else desc

            self.add_row(
                pid,
                str(proj.get("name") or "Unnamed"),
                desc_truncated,
                s_formatted,
                str(proj.get("type") or "local"),
                str(proj.get("platform") or "--"),
                str(proj.get("branch") or "--"),
                game_name,
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
