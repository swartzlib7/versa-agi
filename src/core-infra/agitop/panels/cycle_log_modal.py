"""Cycle Log Modal — live tail viewer for agent cycle output."""

import os
import subprocess
from textual.app import ComposeResult
from textual.screen import ModalScreen
from textual.containers import Vertical, VerticalScroll, Horizontal
from textual.widgets import Button, Static, RichLog


class CycleLogModal(ModalScreen):
    """Modal that tails the active or last cycle result file for an agent."""

    BINDINGS = [("escape", "dismiss", "Close")]

    def __init__(self, agent_name: str, system_reader=None, **kwargs):
        super().__init__(**kwargs)
        self.agent_name = agent_name
        self.system_reader = system_reader
        self._log_path = None
        self._last_pos = 0
        self._poll_timer = None
        self._is_live = False

    def compose(self) -> ComposeResult:
        with Vertical(id="msg-dialog"):
            yield Static("", id="cycle-log-header")
            yield RichLog(id="cycle-log-body", wrap=False, highlight=True, markup=True)
            with Horizontal(id="msg-dialog-actions"):
                yield Button("Close", variant="primary", id="msg-dialog-close")

    def on_mount(self) -> None:
        self._resolve_log_file()
        self._load_content()
        # Poll for updates every 2 seconds (live tail)
        self._poll_timer = self.set_interval(2, self._poll_updates)

    def _resolve_log_file(self) -> None:
        """Find the most recent result file for this agent."""
        cycles_dir = f"/var/lib/versa-agi/{self.agent_name}/cycles"

        # Check if agent harness is currently running
        self._is_live = False
        if self.system_reader:
            try:
                result = subprocess.run(
                    ["pgrep", "-u", self.agent_name, "-f", "harness.agent_harness"],
                    capture_output=True, text=True, timeout=3,
                )
                if result.returncode == 0:
                    self._is_live = True
            except Exception:
                # Try with os_user pattern (agi-{name} or just {name})
                for user in [self.agent_name, f"agi-{self.agent_name}"]:
                    try:
                        result = subprocess.run(
                            ["pgrep", "-u", user, "-f", "harness.agent_harness"],
                            capture_output=True, text=True, timeout=3,
                        )
                        if result.returncode == 0:
                            self._is_live = True
                            break
                    except Exception:
                        continue

        # Find the latest result file (most recent by filename timestamp)
        try:
            result = subprocess.run(
                ["sudo", "ls", "-t", cycles_dir],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode == 0:
                for fname in result.stdout.strip().splitlines():
                    if fname.startswith("result_") and fname.endswith(".json"):
                        self._log_path = os.path.join(cycles_dir, fname)
                        break
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

    def _load_content(self) -> None:
        """Initial load of the log file content."""
        if not self._log_path:
            return

        content = self._read_file(self._log_path)
        if not content:
            return

        log_widget = self.query_one("#cycle-log-body", RichLog)
        # Escape Rich markup in raw output
        safe_content = content.replace("[", "\\[")
        for line in safe_content.splitlines():
            # Re-apply our own formatting for key patterns
            display_line = line
            if "AGENT →" in line or "AGENT ->" in line:
                display_line = f"[bold cyan]{line}[/]"
            elif "TOOL  ←" in line or "TOOL  <-" in line:
                display_line = f"[green]{line}[/]"
            elif "STDERR" in line or "Error:" in line:
                display_line = f"[red]{line}[/]"
            elif "CHECKPOINT" in line:
                display_line = f"[yellow]{line}[/]"
            elif "BUDGET" in line:
                display_line = f"[bold yellow]{line}[/]"
            elif "CYCLE COMPLETE" in line or "Cycle ended" in line:
                display_line = f"[bold green]{line}[/]"
            elif line.startswith("="):
                display_line = f"[dim]{line}[/]"
            else:
                display_line = line
            log_widget.write(display_line)

        self._last_pos = len(content)

    def _poll_updates(self) -> None:
        """Check for new content appended to the log file."""
        if not self._log_path or not self._is_live:
            return

        content = self._read_file(self._log_path)
        if not content or len(content) <= self._last_pos:
            # Check if the process is still running
            try:
                result = subprocess.run(
                    ["pgrep", "-f", "harness.agent_harness"],
                    capture_output=True, text=True, timeout=3,
                )
                if result.returncode != 0:
                    self._is_live = False
                    header = self.query_one("#cycle-log-header", Static)
                    header.update(f"[bold]Cycle Log — {self.agent_name}[/]  [dim]○ completed[/]")
            except Exception:
                pass
            return

        # New content available
        new_content = content[self._last_pos:]
        self._last_pos = len(content)

        log_widget = self.query_one("#cycle-log-body", RichLog)
        safe_content = new_content.replace("[", "\\[")
        for line in safe_content.splitlines():
            display_line = line
            if "AGENT →" in line or "AGENT ->" in line:
                display_line = f"[bold cyan]{line}[/]"
            elif "TOOL  ←" in line or "TOOL  <-" in line:
                display_line = f"[green]{line}[/]"
            elif "STDERR" in line or "Error:" in line:
                display_line = f"[red]{line}[/]"
            elif "CHECKPOINT" in line:
                display_line = f"[yellow]{line}[/]"
            elif "BUDGET" in line:
                display_line = f"[bold yellow]{line}[/]"
            elif "CYCLE COMPLETE" in line or "Cycle ended" in line:
                display_line = f"[bold green]{line}[/]"
            elif line.startswith("="):
                display_line = f"[dim]{line}[/]"
            else:
                display_line = line
            log_widget.write(display_line)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "msg-dialog-close":
            if self._poll_timer:
                self._poll_timer.stop()
            self.app.pop_screen()

    def action_dismiss(self) -> None:
        if self._poll_timer:
            self._poll_timer.stop()
        self.app.pop_screen()
