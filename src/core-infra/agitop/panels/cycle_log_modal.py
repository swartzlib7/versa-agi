"""Cycle Log Modal — live tail viewer for agent cycle output."""

import os
import subprocess
from textual.app import ComposeResult
from textual.screen import ModalScreen
from textual.containers import Vertical, Horizontal
from textual.widgets import Button, Static, RichLog, Select


class CycleLogModal(ModalScreen):
    """Modal that tails the active or last cycle result file for an agent."""

    BINDINGS = [("escape", "dismiss", "Close")]

    def __init__(self, agent_name: str, system_reader=None, os_user: str = None, **kwargs):
        super().__init__(**kwargs)
        self.agent_name = agent_name
        self.system_reader = system_reader
        # os_user is the actual Linux username (e.g. agi-sylvie) for pgrep -u
        self._os_user = os_user or agent_name
        self._log_path = None
        self._last_pos = 0
        self._poll_timer = None
        self._is_live = False
        self._full_content = ""  # Raw content for clipboard copy
        # Checkpoint step navigation state
        self._thread_steps = []       # Ordered list of (step_num, checkpoint_id)
        self._current_step_idx = -1   # Index into _thread_steps
        self._active_thread_id = None # Currently selected thread_id

    def compose(self) -> ComposeResult:
        with Vertical(id="msg-dialog"):
            yield Static("", id="cycle-log-header")
            yield Select(
                options=[("Active / Latest Cycle Log", "active")],
                id="cycle-log-select",
                allow_blank=False,
                value="active"
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
        self._resolve_log_file()
        self._populate_select_options()
        self._load_content()
        # Poll for updates every 2 seconds (live tail)
        self._poll_timer = self.set_interval(2, self._poll_updates)

    def _populate_select_options(self) -> None:
        """Find past result logs and checkpoint threads and add them to Select."""
        from datetime import datetime
        options = []
        
        # 1. Active / Latest log
        options.append(("Active / Latest Cycle Log", "active"))
        
        # 2. Find past cycle logs (agitop runs as root — direct filesystem access)
        cycles_dir = f"/var/lib/versa-agi/{self.agent_name}/cycles"
        try:
            if os.path.isdir(cycles_dir):
                result_files = [
                    f for f in os.listdir(cycles_dir)
                    if f.startswith("result_") and f.endswith(".json")
                ]
                # Sort by filename descending (Unix epoch = chronological)
                result_files.sort(reverse=True)
                for fname in result_files:
                    # Parse Unix epoch timestamp (result_1779872403.json)
                    ts_str = fname.replace("result_", "").replace(".json", "")
                    try:
                        epoch = int(ts_str)
                        dt = datetime.fromtimestamp(epoch)
                        formatted_ts = dt.strftime("%Y-%m-%d %H:%M:%S")
                        label = f"Cycle Log: {formatted_ts}"
                    except Exception:
                        label = f"Cycle Log: {fname}"
                    
                    options.append((label, f"log:{fname}"))
        except Exception:
            pass
            
        # 3. Find checkpoint threads from checkpoints.db
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
                
                # Resolve project names from tasks.db for readable labels
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
                    # Parse project_id from thread_id (format: agent_id-project_id)
                    parts = thread_id.split("-", 1)
                    project_id = parts[1] if len(parts) > 1 else "0"
                    if project_id == "0":
                        project_label = "general"
                    else:
                        project_label = project_names.get(project_id, f"project-{project_id}")
                    options.append((f"Thread: {project_label} ({count} steps)", f"checkpoint:{thread_id}"))
        except Exception:
            pass
            
        # Update Select widget with discovered options
        select = self.query_one("#cycle-log-select", Select)
        select.set_options(options)

    def on_select_changed(self, event: Select.Changed) -> None:
        """Handle log/checkpoint selection change."""
        value = event.value
        if not value:
            return
            
        log_widget = self.query_one("#cycle-log-body", RichLog)
        log_widget.clear()
        self._full_content = ""
        nav_bar = self.query_one("#step-nav-bar")
        
        if value == "active":
            # Hide step nav, clear thread state, enable live tailing
            nav_bar.display = False
            self._thread_steps = []
            self._current_step_idx = -1
            self._active_thread_id = None
            self._is_live = self._check_harness_running()
            self._resolve_log_file()
            self._load_content()
            header = self.query_one("#cycle-log-header", Static)
            status = "[bold green]● LIVE[/]" if self._is_live else "[dim]○ completed[/]"
            header.update(f"[bold]Cycle Log — {self.agent_name}[/]  {status}")
        elif value.startswith("log:"):
            # Hide step nav, clear thread state, disable live tailing
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
            # Disable live tailing, load thread steps with nav
            self._is_live = False
            thread_id = value.split(":", 1)[1]
            self._active_thread_id = thread_id
            self._load_thread_steps(thread_id)

    def _load_thread_steps(self, thread_id: str) -> None:
        """Load step metadata for a thread and display the latest step."""
        db_path = f"/var/lib/versa-agi/{self.agent_name}/cycles/checkpoints.db"
        log_widget = self.query_one("#cycle-log-body", RichLog)
        
        if not os.path.exists(db_path):
            log_widget.write("[red]Checkpoints database not found.[/]")
            return
        
        try:
            import sqlite3
            conn = sqlite3.connect(f"file:{db_path}?mode=ro&immutable=1", uri=True, timeout=2)
            # Only index steps that wrote to the 'messages' channel — routing-only
            # steps (branch:to:agent, llm_input_messages) show identical content
            rows = conn.execute(
                "SELECT DISTINCT json_extract(c.metadata, '$.step') as step_num, c.checkpoint_id "
                "FROM checkpoints c "
                "JOIN writes w ON c.thread_id = w.thread_id AND c.checkpoint_id = w.checkpoint_id "
                "WHERE c.thread_id = ? AND w.channel = 'messages' "
                "ORDER BY c.rowid ASC",
                (thread_id,)
            ).fetchall()
            conn.close()
            
            if not rows:
                log_widget.write(f"[yellow]No checkpoints found for thread {thread_id}.[/]")
                return
            
            self._thread_steps = [(row[0], row[1]) for row in rows]
            self._current_step_idx = len(self._thread_steps) - 1  # Start at latest
            
            # Show step nav bar
            nav_bar = self.query_one("#step-nav-bar")
            nav_bar.display = True
            
            self._load_checkpoint_step(self._current_step_idx)
            
        except Exception as e:
            log_widget.write(f"[red]Error loading thread steps: {e}[/]")

    def _load_checkpoint_step(self, step_idx: int) -> None:
        """Load and display a specific checkpoint step."""
        if step_idx < 0 or step_idx >= len(self._thread_steps):
            return
        
        step_num, checkpoint_id = self._thread_steps[step_idx]
        total_steps = len(self._thread_steps)
        max_step = self._thread_steps[-1][0]
        
        # Update step indicator and button states
        indicator = self.query_one("#step-indicator", Static)
        indicator.update(f"Step {step_num} / {max_step}  ({step_idx + 1} of {total_steps})")
        prev_btn = self.query_one("#step-prev", Button)
        next_btn = self.query_one("#step-next", Button)
        prev_btn.disabled = (step_idx <= 0)
        next_btn.disabled = (step_idx >= total_steps - 1)
        
        # Load checkpoint data
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
            
            # Ensure langgraph serializer is available from harness virtualenv
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
                (self._active_thread_id, checkpoint_id)
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
            
            # Deserialize checkpoint blob
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
                
            log_widget.write(f"[bold magenta]=== Thread {self._active_thread_id} — Step {step_num} ({source}) ===[/]")
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
            
            # Show most recent messages first (reverse chronological for troubleshooting)
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
        """Check if the agent harness process is running for this agent's OS user."""
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
        """Find the most recent result file for this agent."""
        cycles_dir = f"/var/lib/versa-agi/{self.agent_name}/cycles"

        # Check if agent harness is currently running
        self._is_live = self._check_harness_running()

        # Find the latest result file (agitop runs as root — direct filesystem access)
        try:
            if os.path.isdir(cycles_dir):
                result_files = [
                    f for f in os.listdir(cycles_dir)
                    if f.startswith("result_") and f.endswith(".json")
                ]
                if result_files:
                    result_files.sort(reverse=True)
                    self._log_path = os.path.join(cycles_dir, result_files[0])
        except Exception:
            pass

        # Update header
        header = self.query_one("#cycle-log-header", Static)
        if self._log_path:
            status = "[bold green]● LIVE[/]" if self._is_live else "[dim]○ completed[/]"
            header.update(f"[bold]Cycle Log — {self.agent_name}[/]  {status}")
        else:
            header.update(f"[bold]Cycle Log — {self.agent_name}[/]  [dim](no cycle data found)[/]")

    def _read_file(self, path: str) -> str:
        """Read file with sudo fallback."""
        try:
            with open(path, "r") as f:
                return f.read()
        except PermissionError:
            try:
                result = subprocess.run(
                    ["sudo", "cat", path],
                    capture_output=True, text=True, timeout=5,
                )
                if result.returncode == 0:
                    return result.stdout
            except Exception:
                pass
        except Exception:
            pass
        return ""

    def _format_line(self, line: str) -> str:
        """Apply syntax highlighting to a log line."""
        if "AGENT →" in line or "AGENT ->" in line:
            return f"[bold cyan]{line}[/]"
        elif "TOOL  ←" in line or "TOOL  <-" in line:
            return f"[green]{line}[/]"
        elif "STDERR" in line or "Error:" in line:
            return f"[red]{line}[/]"
        elif "CHECKPOINT" in line:
            return f"[yellow]{line}[/]"
        elif "BUDGET" in line:
            return f"[bold yellow]{line}[/]"
        elif "CYCLE COMPLETE" in line or "Cycle ended" in line:
            return f"[bold green]{line}[/]"
        elif line.startswith("="):
            return f"[dim]{line}[/]"
        return line

    def _load_content(self) -> None:
        """Initial load of the log file content."""
        if not self._log_path:
            return

        content = self._read_file(self._log_path)
        if not content:
            return

        self._full_content = content
        log_widget = self.query_one("#cycle-log-body", RichLog)
        # Escape Rich markup in raw output
        safe_content = content.replace("[", "\\[")
        for line in safe_content.splitlines():
            log_widget.write(self._format_line(line))

        self._last_pos = len(content)

    def _poll_updates(self) -> None:
        """Check for new content appended to the log file."""
        if not self._log_path:
            return

        # If not live, check if the agent has started since we opened
        if not self._is_live:
            if self._check_harness_running():
                self._is_live = True
                # Re-resolve log file — a new result_*.json may have been created
                self._resolve_log_file()
                header = self.query_one("#cycle-log-header", Static)
                header.update(f"[bold]Cycle Log — {self.agent_name}[/]  [bold green]● LIVE[/]")
            else:
                return  # Still not running, nothing to tail

        content = self._read_file(self._log_path)
        if not content or len(content) <= self._last_pos:
            # Check if the process has finished
            if not self._check_harness_running():
                self._is_live = False
                header = self.query_one("#cycle-log-header", Static)
                header.update(f"[bold]Cycle Log — {self.agent_name}[/]  [dim]○ completed[/]")
            return

        # New content available
        new_content = content[self._last_pos:]
        self._full_content = content
        self._last_pos = len(content)

        log_widget = self.query_one("#cycle-log-body", RichLog)
        safe_content = new_content.replace("[", "\\[")
        for line in safe_content.splitlines():
            log_widget.write(self._format_line(line))

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "cycle-log-copy":
            if self._full_content:
                try:
                    subprocess.run(["xclip", "-selection", "clipboard"], input=self._full_content.encode(), check=True)
                    self.app.notify("Cycle log copied to clipboard", title="Clipboard")
                except FileNotFoundError:
                    try:
                        subprocess.run(["xsel", "--clipboard", "--input"], input=self._full_content.encode(), check=True)
                        self.app.notify("Cycle log copied to clipboard", title="Clipboard")
                    except Exception:
                        self.app.notify("Install xclip or xsel for clipboard support", severity="warning")
                except Exception:
                    self.app.notify("Clipboard copy failed", severity="warning")
            else:
                self.app.notify("No content to copy", severity="warning")
        elif event.button.id == "step-prev":
            if self._current_step_idx > 0:
                self._current_step_idx -= 1
                self._load_checkpoint_step(self._current_step_idx)
        elif event.button.id == "step-next":
            if self._current_step_idx < len(self._thread_steps) - 1:
                self._current_step_idx += 1
                self._load_checkpoint_step(self._current_step_idx)
        elif event.button.id == "msg-dialog-close":
            if self._poll_timer:
                self._poll_timer.stop()
            self.app.pop_screen()

    def action_dismiss(self) -> None:
        if self._poll_timer:
            self._poll_timer.stop()
        self.app.pop_screen()
