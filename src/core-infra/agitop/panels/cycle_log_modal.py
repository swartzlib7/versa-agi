"""Cycle Log Modal — live tail viewer for agent cycle output."""

from __future__ import annotations

import os
import subprocess
from typing import Any, Callable, Optional

from textual.app import ComposeResult
from textual.screen import ModalScreen
from textual.containers import Vertical, Horizontal
from textual.widgets import Button, Static, RichLog, Select


class _ScreenHost:
    """Delegates widget/timer access to a Textual screen."""

    def __init__(self, screen) -> None:
        self._screen = screen

    @property
    def app(self):
        return self._screen.app

    def query_one(self, selector: str, expect_type=None):
        return self._screen.query_one(selector, expect_type)

    def set_interval(self, interval: float, callback: Callable[[], None]):
        return self._screen.set_interval(interval, callback)

    def call_after_refresh(self, callback: Callable[[], None], animate: bool = False):
        return self._screen.call_after_refresh(callback, animate=animate)


class _MappedScreenHost(_ScreenHost):
    """Maps canonical cycle-log selectors to embedded widget ids."""

    def __init__(self, screen, id_map: dict[str, str]) -> None:
        super().__init__(screen)
        self._id_map = id_map

    def query_one(self, selector: str, expect_type=None):
        for old, new in self._id_map.items():
            selector = selector.replace(old, new)
        return self._screen.query_one(selector, expect_type)


EMBEDDED_CYCLE_LOG_ID_MAP = {
    "#cycle-log-header": "#agent-cycle-log-header",
    "#cycle-log-select": "#agent-cycle-log-select",
    "#step-nav-bar": "#agent-step-nav-bar",
    "#step-prev": "#agent-step-prev",
    "#step-indicator": "#agent-step-indicator",
    "#step-next": "#agent-step-next",
    "#cycle-log-body": "#agent-cycle-log-body",
    "#cycle-log-copy": "#agent-cycle-log-copy",
}


class CycleLogController:
    """Embeddable cycle log tail + checkpoint viewer logic."""

    def __init__(
        self,
        host,
        agent_name: str,
        system_reader=None,
        os_user: str | None = None,
    ) -> None:
        self._host = host
        self.agent_name = agent_name
        self.system_reader = system_reader
        self._os_user = os_user or agent_name
        self._log_path = None
        self._last_pos = 0
        self._poll_timer = None
        self._is_live = False
        self._full_content = ""
        self._thread_steps: list[tuple[Any, Any]] = []
        self._current_step_idx = -1
        self._active_thread_id = None

    @property
    def app(self):
        return self._host.app

    def query_one(self, selector: str, expect_type=None):
        return self._host.query_one(selector, expect_type)

    def set_interval(self, interval: float, callback: Callable[[], None]):
        return self._host.set_interval(interval, callback)

    def call_after_refresh(self, callback: Callable[[], None], animate: bool = False):
        return self._host.call_after_refresh(callback, animate=animate)

    def start(self) -> None:
        self._resolve_log_file()
        self._populate_select_options()
        self._load_content()
        self._poll_timer = self.set_interval(2, self._poll_updates)

    def stop(self) -> None:
        if self._poll_timer:
            self._poll_timer.stop()
            self._poll_timer = None

    def _populate_select_options(self) -> None:
        options = [("Active / Latest Cycle Log", "active")]
        cycles_dir = f"/var/lib/versa-agi/{self.agent_name}/cycles"
        for fname in _list_cycle_result_json_files(cycles_dir):
            ts_str = fname.replace("result_", "").replace(".json", "")
            try:
                from datetime import datetime
                epoch = int(ts_str)
                dt = datetime.fromtimestamp(epoch)
                formatted_ts = dt.strftime("%Y-%m-%d %H:%M:%S")
                label = f"Cycle Log: {formatted_ts}"
            except Exception:
                label = f"Cycle Log: {fname}"
            options.append((label, f"log:{fname}"))

        db_path = f"/var/lib/versa-agi/{self.agent_name}/cycles/checkpoints.db"
        try:
            if os.path.exists(db_path):
                import sqlite3
                conn = sqlite3.connect(f"file:{db_path}?mode=ro&immutable=1", uri=True, timeout=2)
                conn.row_factory = sqlite3.Row
                rows = conn.execute(
                    "SELECT thread_id, COUNT(*) as count FROM checkpoints GROUP BY thread_id ORDER BY thread_id"
                ).fetchall()
                conn.close()

                project_names = {}
                tasks_db = "/var/lib/versa-agi/coa/tasks.db"
                try:
                    if os.path.exists(tasks_db):
                        tconn = sqlite3.connect(tasks_db, timeout=2)
                        for prow in tconn.execute("SELECT id, name FROM projects"):
                            project_names[str(prow[0])] = prow[1]
                        tconn.close()
                except Exception:
                    pass

                for r in rows:
                    thread_id = r["thread_id"]
                    count = r["count"]
                    parts = thread_id.split("-", 1)
                    project_id = parts[1] if len(parts) > 1 else "0"
                    if project_id == "0":
                        project_label = "general"
                    else:
                        project_label = project_names.get(project_id, f"project-{project_id}")
                    options.append((f"Thread: {project_label} ({count} steps)", f"checkpoint:{thread_id}"))
        except Exception:
            pass

        select = self.query_one("#cycle-log-select", Select)
        select.set_options(options)

    def on_select_changed(self, event: Select.Changed) -> None:
        value = event.value
        if not value:
            return

        log_widget = self.query_one("#cycle-log-body", RichLog)
        log_widget.clear()
        self._full_content = ""
        nav_bar = self.query_one("#step-nav-bar")

        if value == "active":
            self._show_active_cycle_log()
        elif value.startswith("log:"):
            nav_bar.display = False
            self._thread_steps = []
            self._current_step_idx = -1
            self._active_thread_id = None
            self._is_live = False
            fname = value.split(":", 1)[1]
            path = f"/var/lib/versa-agi/{self.agent_name}/cycles/{fname}"
            self._log_path = path
            content = self._read_file(path)
            self._full_content = content
            safe_content = content.replace("[", "\\[")
            for line in safe_content.splitlines():
                log_widget.write(self._format_line(line))
            header = self.query_one("#cycle-log-header", Static)
            header.update(f"[bold]Cycle Log — {self.agent_name}[/]  [dim]○ historical ({fname})[/]")
        elif value.startswith("checkpoint:"):
            self._is_live = False
            thread_id = value.split(":", 1)[1]
            self._active_thread_id = thread_id
            self._load_thread_steps(thread_id)

    def _load_thread_steps(self, thread_id: str) -> None:
        db_path = f"/var/lib/versa-agi/{self.agent_name}/cycles/checkpoints.db"
        log_widget = self.query_one("#cycle-log-body", RichLog)
        if not os.path.exists(db_path):
            log_widget.write("[red]Checkpoints database not found.[/]")
            return
        try:
            import sqlite3
            conn = sqlite3.connect(f"file:{db_path}?mode=ro&immutable=1", uri=True, timeout=2)
            rows = conn.execute(
                "SELECT DISTINCT json_extract(c.metadata, '$.step') as step_num, c.checkpoint_id "
                "FROM checkpoints c "
                "JOIN writes w ON c.thread_id = w.thread_id AND c.checkpoint_id = w.checkpoint_id "
                "WHERE c.thread_id = ? AND w.channel = 'messages' "
                "ORDER BY c.rowid ASC",
                (thread_id,),
            ).fetchall()
            conn.close()
            if not rows:
                log_widget.write(f"[yellow]No checkpoints found for thread {thread_id}.[/]")
                return
            self._thread_steps = [(row[0], row[1]) for row in rows]
            self._current_step_idx = len(self._thread_steps) - 1
            nav_bar = self.query_one("#step-nav-bar")
            nav_bar.display = True
            self._load_checkpoint_step(self._current_step_idx)
        except Exception as e:
            log_widget.write(f"[red]Error loading thread steps: {e}[/]")

    def _load_checkpoint_step(self, step_idx: int) -> None:
        if step_idx < 0 or step_idx >= len(self._thread_steps):
            return
        step_num, checkpoint_id = self._thread_steps[step_idx]
        total_steps = len(self._thread_steps)
        max_step = self._thread_steps[-1][0]
        indicator = self.query_one("#step-indicator", Static)
        indicator.update(f"Step {step_num} / {max_step}  ({step_idx + 1} of {total_steps})")
        prev_btn = self.query_one("#step-prev", Button)
        next_btn = self.query_one("#step-next", Button)
        prev_btn.disabled = step_idx <= 0
        next_btn.disabled = step_idx >= total_steps - 1
        db_path = f"/var/lib/versa-agi/{self.agent_name}/cycles/checkpoints.db"
        log_widget = self.query_one("#cycle-log-body", RichLog)
        log_widget.clear()
        log_widget.auto_scroll = False
        header = self.query_one("#cycle-log-header", Static)
        try:
            import sqlite3
            import sys
            import glob
            import json
            harness_sites = glob.glob("/usr/local/lib/versa-agi/venv/lib/python3.*/site-packages")
            if harness_sites and harness_sites[0] not in sys.path:
                sys.path.insert(0, harness_sites[0])
            from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
            serializer = JsonPlusSerializer()
            conn = sqlite3.connect(f"file:{db_path}?mode=ro&immutable=1", uri=True, timeout=2)
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT checkpoint_id, parent_checkpoint_id, checkpoint, metadata "
                "FROM checkpoints WHERE thread_id = ? AND checkpoint_id = ?",
                (self._active_thread_id, checkpoint_id),
            ).fetchone()
            conn.close()
            if not row:
                log_widget.write(f"[red]Checkpoint {checkpoint_id[:12]}... not found.[/]")
                return
            meta_data = {}
            if row["metadata"]:
                try:
                    meta_data = json.loads(row["metadata"].decode("utf-8", errors="ignore"))
                except Exception:
                    pass
            source = meta_data.get("source", "unknown")
            header.update(f"[bold]Thread {self._active_thread_id}[/]  [dim]Step {step_num} ({source})[/]")
            cp_blob = row["checkpoint"]
            checkpoint_data = None
            for type_str in ["msgpack", "json"]:
                try:
                    checkpoint_data = serializer.loads_typed((type_str, cp_blob))
                    break
                except Exception:
                    pass
            if not checkpoint_data:
                log_widget.write("[red]Failed to deserialize checkpoint state.[/]")
                return
            log_widget.write(
                f"[bold magenta]=== Thread {self._active_thread_id} — Step {step_num} ({source}) ===[/]"
            )
            log_widget.write(f"Checkpoint: {checkpoint_id[:12]}...")
            log_widget.write("")
            channel_values = checkpoint_data.get("channel_values", {})
            messages = channel_values.get("messages", [])
            if not messages:
                log_widget.write("[yellow]No messages in this checkpoint state.[/]")
                other_channels = [c for c in channel_values.keys() if c != "messages"]
                if other_channels:
                    log_widget.write(f"\n[bold]Other Active Channels:[/] {', '.join(other_channels)}")
                return
            copy_lines = [f"=== Thread {self._active_thread_id} — Step {step_num} ==="]
            messages = list(reversed(messages))
            for msg in messages:
                msg_type = getattr(msg, "type", "unknown")
                content = getattr(msg, "content", "")
                if isinstance(content, list):
                    content = str(content)
                safe_content = content.replace("[", "\\[")
                tool_calls = getattr(msg, "tool_calls", [])
                if msg_type == "human":
                    log_widget.write("[bold green]Human Message:[/]")
                    log_widget.write(safe_content)
                    copy_lines.append(f"Human: {content}")
                elif msg_type == "ai":
                    log_widget.write("[bold cyan]AI Message:[/]")
                    if safe_content.strip():
                        log_widget.write(safe_content)
                        copy_lines.append(f"AI: {content}")
                    if tool_calls:
                        log_widget.write("[bold yellow]AI requested tool execution:[/]")
                        for tc in tool_calls:
                            tc_name = tc.get("name", "unknown")
                            tc_args = tc.get("args", {})
                            log_widget.write(f"  🔧 [bold]{tc_name}[/]({json.dumps(tc_args)})")
                            copy_lines.append(f"AI Tool Call: {tc_name}({json.dumps(tc_args)})")
                elif msg_type == "tool":
                    name = getattr(msg, "name", "unknown")
                    log_widget.write(f"[bold yellow]Tool Output ({name}):[/]")
                    log_widget.write(safe_content)
                    copy_lines.append(f"Tool ({name}) Output: {content}")
                elif msg_type == "system":
                    log_widget.write("[bold purple]System Message:[/]")
                    log_widget.write(safe_content)
                    copy_lines.append(f"System: {content}")
                else:
                    log_widget.write(f"[bold gray]{msg_type.capitalize()} Message:[/]")
                    log_widget.write(safe_content)
                    copy_lines.append(f"{msg_type.capitalize()}: {content}")
                log_widget.write("[dim]─" * 40 + "[/]")
            log_widget.write(f"\n[dim]{len(messages)} messages in checkpoint state[/]")
            self._full_content = "\n".join(copy_lines)
            self.call_after_refresh(log_widget.scroll_home, animate=False)
        except Exception as e:
            log_widget.write(f"[red]Error loading checkpoint step: {e}[/]")

    def _check_harness_running(self) -> bool:
        for user in [self._os_user, self.agent_name]:
            try:
                result = subprocess.run(
                    ["pgrep", "-u", user, "-f", "harness.agent_harness"],
                    capture_output=True, text=True, timeout=3,
                )
                if result.returncode == 0:
                    return True
            except Exception:
                continue
        return False

    def _resolve_log_file(self) -> None:
        cycles_dir = f"/var/lib/versa-agi/{self.agent_name}/cycles"
        self._is_live = self._check_harness_running()
        self._log_path = None
        result_files = _list_cycle_result_json_files(cycles_dir)
        if result_files:
            self._log_path = os.path.join(cycles_dir, result_files[0])
        header = self.query_one("#cycle-log-header", Static)
        if self._log_path:
            status = "[bold green]● LIVE[/]" if self._is_live else "[dim]○ completed[/]"
            header.update(f"[bold]Cycle Log — {self.agent_name}[/]  {status}")
        else:
            header.update(f"[bold]Cycle Log — {self.agent_name}[/]  [dim](no cycle data found)[/]")

    def _read_file(self, path: str) -> str:
        try:
            with open(path, "r") as f:
                return f.read()
        except PermissionError:
            try:
                result = subprocess.run(
                    ["sudo", "cat", path], capture_output=True, text=True, timeout=5,
                )
                if result.returncode == 0:
                    return result.stdout
            except Exception:
                pass
        except Exception:
            pass
        return ""

    def _format_line(self, line: str) -> str:
        if "AGENT →" in line or "AGENT ->" in line:
            return f"[bold cyan]{line}[/]"
        if "TOOL  ←" in line or "TOOL  <-" in line:
            return f"[green]{line}[/]"
        if "STDERR" in line or "Error:" in line:
            return f"[red]{line}[/]"
        if "CHECKPOINT" in line:
            return f"[yellow]{line}[/]"
        if "BUDGET" in line:
            return f"[bold yellow]{line}[/]"
        if "CYCLE COMPLETE" in line or "Cycle ended" in line:
            return f"[bold green]{line}[/]"
        if line.startswith("="):
            return f"[dim]{line}[/]"
        return line

    def _show_active_cycle_log(self) -> None:
        """Load the active/latest cycle log (same as picklist 'active')."""
        log_widget = self.query_one("#cycle-log-body", RichLog)
        log_widget.clear()
        self._full_content = ""
        self._last_pos = 0
        nav_bar = self.query_one("#step-nav-bar")
        nav_bar.display = False
        self._thread_steps = []
        self._current_step_idx = -1
        self._active_thread_id = None
        self._is_live = self._check_harness_running()
        self._resolve_log_file()
        if self._log_path:
            self._load_content()
        else:
            log_widget.write("[dim]No cycle log data — historical result files were purged.[/]")

    def _load_content(self) -> None:
        if not self._log_path:
            return
        content = self._read_file(self._log_path)
        if not content:
            return
        self._full_content = content
        log_widget = self.query_one("#cycle-log-body", RichLog)
        safe_content = content.replace("[", "\\[")
        for line in safe_content.splitlines():
            log_widget.write(self._format_line(line))
        self._last_pos = len(content)

    def _poll_updates(self) -> None:
        if not self._log_path:
            return
        if not self._is_live:
            if self._check_harness_running():
                self._is_live = True
                self._resolve_log_file()
                header = self.query_one("#cycle-log-header", Static)
                header.update(f"[bold]Cycle Log — {self.agent_name}[/]  [bold green]● LIVE[/]")
            else:
                return
        content = self._read_file(self._log_path)
        if not content or len(content) <= self._last_pos:
            if not self._check_harness_running():
                self._is_live = False
                header = self.query_one("#cycle-log-header", Static)
                header.update(f"[bold]Cycle Log — {self.agent_name}[/]  [dim]○ completed[/]")
            return
        new_content = content[self._last_pos:]
        self._full_content = content
        self._last_pos = len(content)
        log_widget = self.query_one("#cycle-log-body", RichLog)
        safe_content = new_content.replace("[", "\\[")
        for line in safe_content.splitlines():
            log_widget.write(self._format_line(line))

    def on_button_pressed(self, button_id: str) -> None:
        if button_id == "cycle-log-copy":
            if self._full_content:
                try:
                    subprocess.run(
                        ["xclip", "-selection", "clipboard"],
                        input=self._full_content.encode(), check=True,
                    )
                    self.app.notify("Cycle log copied to clipboard", title="Clipboard")
                except FileNotFoundError:
                    try:
                        subprocess.run(
                            ["xsel", "--clipboard", "--input"],
                            input=self._full_content.encode(), check=True,
                        )
                        self.app.notify("Cycle log copied to clipboard", title="Clipboard")
                    except Exception:
                        self.app.notify("Install xclip or xsel for clipboard support", severity="warning")
                except Exception:
                    self.app.notify("Clipboard copy failed", severity="warning")
            else:
                self.app.notify("No content to copy", severity="warning")
        elif button_id == "step-prev":
            if self._current_step_idx > 0:
                self._current_step_idx -= 1
                self._load_checkpoint_step(self._current_step_idx)
        elif button_id == "step-next":
            if self._current_step_idx < len(self._thread_steps) - 1:
                self._current_step_idx += 1
                self._load_checkpoint_step(self._current_step_idx)

    def refresh_after_purge(self) -> None:
        """Reload picklist, header, and log body after result files were deleted."""
        self._log_path = None
        self._last_pos = 0
        self._full_content = ""
        self._populate_select_options()
        select = self.query_one("#cycle-log-select", Select)
        select.value = "active"
        self._show_active_cycle_log()
        select.refresh()
        log_widget = self.query_one("#cycle-log-body", RichLog)
        log_widget.refresh()
        self.call_after_refresh(log_widget.scroll_home, animate=False)

    def reload_from_disk(self) -> None:
        """Full cycle-log tab refresh — repopulate picklist and reload current view."""
        select = self.query_one("#cycle-log-select", Select)
        current = select.value or "active"
        self._populate_select_options()
        if current == "active" or not current:
            select.value = "active"
            self._show_active_cycle_log()
        else:
            select.value = current
            self.on_select_changed(Select.Changed(select, select.value))
        select.refresh()
        self.query_one("#cycle-log-body", RichLog).refresh()


def _list_cycle_result_json_files(cycles_dir: str) -> list[str]:
    """List result_*.json cycle logs, with sudo fallback when needed."""
    try:
        if not os.path.isdir(cycles_dir):
            return []
        names = os.listdir(cycles_dir)
    except PermissionError:
        try:
            result = subprocess.run(
                ["sudo", "ls", "-1", cycles_dir],
                capture_output=True, text=True, timeout=10,
            )
            if result.returncode != 0:
                return []
            names = [n.strip() for n in result.stdout.splitlines() if n.strip()]
        except Exception:
            return []
    except OSError:
        return []
    result_files = [
        n for n in names
        if n.startswith("result_") and n.endswith(".json")
    ]
    result_files.sort(reverse=True)
    return result_files


def _agent_cycles_dir(agent_name: str) -> str:
    return f"/var/lib/versa-agi/{agent_name}/cycles"


def _is_purgeable_cycle_log(name: str) -> bool:
    return name.startswith("result_") and (name.endswith(".log") or name.endswith(".json"))


def _list_purgeable_cycle_logs(cycles_dir: str) -> list[str]:
    try:
        names = os.listdir(cycles_dir)
    except PermissionError:
        try:
            result = subprocess.run(
                ["sudo", "ls", "-1", cycles_dir],
                capture_output=True, text=True, timeout=10,
            )
            if result.returncode != 0:
                return []
            names = [n.strip() for n in result.stdout.splitlines() if n.strip()]
        except Exception:
            return []
    except OSError:
        return []
    return sorted(n for n in names if _is_purgeable_cycle_log(n))


def _delete_cycle_log_file(path: str) -> bool:
    try:
        os.remove(path)
        return True
    except FileNotFoundError:
        return True
    except (PermissionError, OSError):
        pass
    try:
        result = subprocess.run(
            ["sudo", "rm", "-f", path],
            capture_output=True, text=True, timeout=10,
        )
        return result.returncode == 0
    except Exception:
        return False


def purge_agent_cycle_log_files(agent_name: str) -> tuple[int, Optional[str]]:
    """Delete all result_*.json and result_*.log cycle logs for an agent."""
    cycles_dir = _agent_cycles_dir(agent_name)
    if not os.path.isdir(cycles_dir):
        return 0, f"Cycles directory not found: {cycles_dir}"
    deleted = 0
    failed: list[str] = []
    for fname in _list_purgeable_cycle_logs(cycles_dir):
        path = os.path.join(cycles_dir, fname)
        if _delete_cycle_log_file(path):
            deleted += 1
        else:
            failed.append(fname)
    if failed:
        return deleted, f"Could not delete: {', '.join(failed[:5])}"
    return deleted, None


class PurgeLogsConfirmModal(ModalScreen):
    """Confirmation before deleting agent cycle text log files."""

    CSS = """
    PurgeLogsConfirmModal {
        align: center middle;
        background: $surface 80%;
    }
    #cycle-log-purge-dialog {
        width: 64;
        height: auto;
        padding: 1 2;
        border: heavy $error;
        background: $surface;
    }
    #cycle-log-purge-actions {
        margin-top: 1;
        height: auto;
        align: center middle;
    }
    #cycle-log-purge-actions Button {
        width: 1fr;
        margin: 0 1;
        min-width: 16;
        height: 3;
    }
    """

    def __init__(self, agent_name: str, parent=None, **kwargs):
        super().__init__(**kwargs)
        self.agent_name = agent_name
        self._parent = parent

    def compose(self) -> ComposeResult:
        cycles_dir = _agent_cycles_dir(self.agent_name)
        with Vertical(id="cycle-log-purge-dialog"):
            yield Static(
                f"[bold red]Purge all cycle logs for {self.agent_name}?[/]\n\n"
                f"Deletes [bold]result_*.json[/] and [bold]result_*.log[/] under:\n"
                f"[dim]{cycles_dir}[/]\n\n"
                "Removes all historical cycle log files shown in the picklist. "
                "Checkpoint threads ([bold]checkpoints.db[/]) are not affected.\n"
            )
            yield Static("[bold]This cannot be undone.[/]")
            with Horizontal(id="cycle-log-purge-actions"):
                yield Button("Purge", variant="error", id="btn-confirm-purge-logs")
                yield Button(
                    "Cancel", classes="dismiss-btn", variant="default", id="btn-cancel-purge-logs",
                )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        if event.button.id == "btn-cancel-purge-logs":
            self.app.pop_screen()
            return
        if event.button.id != "btn-confirm-purge-logs":
            return
        deleted, err = purge_agent_cycle_log_files(self.agent_name)
        parent = self._parent
        self.app.pop_screen()
        if err and deleted == 0:
            self.app.notify(err, title="Purge Failed", severity="error")
            return
        if deleted:
            msg = f"Purged {deleted} log file(s) for {self.agent_name}"
            if err:
                msg += f" ({err})"
            self.app.notify(msg, title="Cycle Logs", severity="information")
        else:
            self.app.notify(f"No cycle log files found for {self.agent_name}", title="Cycle Logs")
        if parent is not None and hasattr(parent, "refresh_cycle_log_after_purge"):
            parent.refresh_cycle_log_after_purge()
        elif parent is not None and getattr(parent, "_cycle_embed", None):
            parent._cycle_embed.refresh_after_purge()
        else:
            active = self.app.screen
            if hasattr(active, "refresh_cycle_log_after_purge"):
                active.refresh_cycle_log_after_purge()
            elif getattr(active, "_cycle_embed", None):
                active._cycle_embed.refresh_after_purge()
            elif getattr(active, "_controller", None):
                active._controller.refresh_after_purge()


class CycleLogModal(ModalScreen):
    """Modal that tails the active or last cycle result file for an agent."""

    BINDINGS = [("escape", "dismiss", "Close")]

    def __init__(self, agent_name: str, system_reader=None, os_user: str = None, **kwargs):
        super().__init__(**kwargs)
        self._controller = CycleLogController(
            _ScreenHost(self), agent_name, system_reader, os_user,
        )

    def compose(self) -> ComposeResult:
        with Vertical(id="msg-dialog"):
            yield Static("", id="cycle-log-header")
            yield Select(
                options=[("Active / Latest Cycle Log", "active")],
                id="cycle-log-select",
                allow_blank=False,
                value="active",
            )
            with Horizontal(id="step-nav-bar"):
                yield Button("◀ Prev", id="step-prev")
                yield Static("", id="step-indicator")
                yield Button("Next ▶", id="step-next")
            yield RichLog(id="cycle-log-body", wrap=False, highlight=True, markup=True)
            with Horizontal(id="msg-dialog-actions"):
                yield Button("📋 Copy All", variant="default", id="cycle-log-copy")
                yield Button("Close", classes="dismiss-btn", variant="default", id="msg-dialog-close")

    def on_mount(self) -> None:
        self._controller.start()

    def on_select_changed(self, event: Select.Changed) -> None:
        self._controller.on_select_changed(event)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "msg-dialog-close":
            self._controller.stop()
            self.app.pop_screen()
        else:
            self._controller.on_button_pressed(event.button.id)

    def action_dismiss(self) -> None:
        self._controller.stop()
        self.app.pop_screen()
