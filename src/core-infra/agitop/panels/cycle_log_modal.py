"""Cycle Log Modal — live tail viewer for agent cycle output."""

import os
import subprocess
from textual.app import ComposeResult
from textual.screen import ModalScreen
from textual.containers import Vertical, Horizontal
from textual.widgets import Button, Static, RichLog


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

    def compose(self) -> ComposeResult:
        with Vertical(id="msg-dialog"):
            yield Static("", id="cycle-log-header")
            yield RichLog(id="cycle-log-body", wrap=False, highlight=True, markup=True)
            with Horizontal(id="msg-dialog-actions"):
                yield Button("📋 Copy All", variant="default", id="cycle-log-copy")
                yield Button("Close", variant="primary", id="msg-dialog-close")

    def on_mount(self) -> None:
        self._resolve_log_file()
        self._load_content()
        # Poll for updates every 2 seconds (live tail)
        self._poll_timer = self.set_interval(2, self._poll_updates)

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
        elif event.button.id == "msg-dialog-close":
            if self._poll_timer:
                self._poll_timer.stop()
            self.app.pop_screen()

    def action_dismiss(self) -> None:
        if self._poll_timer:
            self._poll_timer.stop()
        self.app.pop_screen()
