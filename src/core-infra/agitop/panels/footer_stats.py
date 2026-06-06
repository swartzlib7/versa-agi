"""Footer stats panel — projects, contacts, cycles with live data."""

import json
import os
import subprocess
from typing import Optional
from textual.app import ComposeResult
from textual.containers import Vertical, Horizontal, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Static, Button

from agitop.data import AgentReader


def _read_file(path: str) -> str:
    """Read a file with sudo fallback, return content or error string."""
    try:
        with open(path, "r") as f:
            return f.read()
    except PermissionError:
        try:
            result = subprocess.run(
                ["sudo", "cat", path],
                capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0:
                return result.stdout
            return f"(permission denied: {path})"
        except Exception:
            return f"(permission denied: {path})"
    except FileNotFoundError:
        return f"(file not found: {path})"


def _fmt_tokens(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    elif n >= 1_000:
        return f"{n / 1_000:.1f}K"
    return f"{n:,}"


def _parse_session_file(raw: str):
    """Parse a session file (JSON or JSONL).
    
    Returns a list of message objects.
    - JSON: single document with .messages[] array
    - JSONL: one JSON object per line
    """
    # Try standard JSON first (legacy format)
    try:
        data = json.loads(raw)
        if isinstance(data, dict) and "messages" in data:
            return data["messages"]
        if isinstance(data, list):
            return data
        return [data]
    except json.JSONDecodeError:
        pass

    # JSONL: parse each line as a separate JSON object
    messages = []
    for line in raw.strip().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            messages.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return messages


def _format_session_summary(messages: list) -> str:
    """Extract a brief summary from a session.
    
    Shows: truncated user prompt + first 50 agent responses.
    Accepts a list of message objects (from JSON .messages[] or JSONL lines).
    """
    lines = []
    gemini_count = 0
    max_gemini = 50

    for msg in messages:
        msg_type = msg.get("type", "unknown")
        content = msg.get("content", "")

        if msg_type in ["user", "human"]:
            text = content if isinstance(content, str) else str(content)
            text = text.replace("\\n", "\n")
            if text:
                display = text[:250]
                if len(text) > 250:
                    display += f"\n... ({len(text):,} chars total)"
                lines.append(f"[bold green]PROMPT:[/] {display}")
                lines.append("")

        elif msg_type in ["gemini", "ai"]:
            gemini_count += 1
            if gemini_count > max_gemini:
                break

            text = content if isinstance(content, str) else str(content)
            if text:
                display = text[:400]
                if len(text) > 400:
                    display += f"\n... ({len(text):,} chars total)"
                lines.append(f"[bold cyan]TURN {gemini_count}:[/] {display}")

            # Tool calls for this turn
            tool_calls = msg.get("toolCalls", [])
            for tc in tool_calls:
                lines.append(f"[dim yellow]  TOOL: {tc.get('name', '?')} ({tc.get('status', '')})[/]")

            lines.append("")

        elif msg_type == "tool":
            text = content if isinstance(content, str) else str(content)
            if text:
                display = text[:250]
                if len(text) > 250:
                    display += f"\n... ({len(text):,} chars total)"
                lines.append(f"[dim]  ↳ OUTPUT: {display}[/]")
                lines.append("")

    # Count remaining turns
    total_gemini = sum(1 for m in messages if m.get("type") in ["gemini", "ai"])
    remaining = total_gemini - min(gemini_count, max_gemini)
    if remaining > 0:
        lines.append(f"[dim]... {remaining} more agent turn(s) in session file[/]")

    return "\n".join(lines) if lines else "(no messages found)"


class SessionViewModal(ModalScreen):
    """Modal showing the last session summary and token usage."""

    def __init__(self, cycle: dict, **kwargs):
        super().__init__(**kwargs)
        self.cycle = cycle

    def compose(self) -> ComposeResult:
        cycle = self.cycle
        session_path = cycle.get("json_output_path", "")
        cycle_id = cycle.get("id", "unknown")

        # Load session file
        data = None
        content = ""
        fname = "none"

        if session_path:
            raw = _read_file(session_path)
            if not raw.startswith("("):
                try:
                    messages = _parse_session_file(raw)
                    content = _format_session_summary(messages)
                    fname = os.path.basename(session_path)
                except Exception:
                    content = f"(could not parse: {session_path})"
                    fname = "?"
            else:
                content = raw
                fname = "?"
        else:
            content = "(no session file linked to this cycle)"

        # Token stats — use cycle DB values (correctly delta'd by Lifeline)
        # Do NOT re-parse session file — with --resume it contains cumulative totals across cycles
        t_in = cycle.get("tokens_input", 0)
        t_out = cycle.get("tokens_output", 0)
        t_think = cycle.get("tokens_thinking", 0)
        t_cached = cycle.get("tokens_cached", 0)
        t_total = cycle.get("tokens_total", 0)

        token_bar = (
            f"[bold]Session Tokens:[/]  "
            f"In: [cyan]{_fmt_tokens(t_in)}[/]  "
            f"Out: [cyan]{_fmt_tokens(t_out)}[/]  "
            f"Think: [cyan]{_fmt_tokens(t_think)}[/]  "
            f"Cached: [dim cyan]{_fmt_tokens(t_cached)}[/]  "
            f"Total: [bold cyan]{_fmt_tokens(t_total)}[/bold cyan]"
        )

        # Escape Rich markup in content but preserve our tags
        safe_content = content.replace("[", "\\[") if "[" in content else content
        for tag in ["bold green", "bold cyan", "dim yellow", "dim", "/bold green", "/bold cyan", "/dim yellow", "/dim", "/"]:
            safe_content = safe_content.replace(f"\\[{tag}]", f"[{tag}]")

        with Vertical(id="msg-dialog"):
            yield Static(
                f"[bold]Last Session[/]  │  {cycle_id}  │  [dim]{fname}[/]",
                id="msg-dialog-header"
            )
            yield Static(token_bar)
            with VerticalScroll(id="msg-dialog-scroll"):
                yield Static(safe_content)
            yield Static(f"[dim]File: {session_path}[/]")
            yield Button("Close", variant="primary", id="msg-dialog-close")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "msg-dialog-close":
            self.app.pop_screen()


class FooterStatsLabel(Static):
    """The centered label inside the footer."""
    pass


class ResetConfirmModal(ModalScreen):
    """Confirmation modal for resetting monthly tokens."""
    def __init__(self, agent_reader, **kwargs):
        super().__init__(**kwargs)
        self._agent_reader = agent_reader

    def compose(self) -> ComposeResult:
        with Vertical(id="msg-dialog"):
            yield Static("[bold red]Reset Cycle Data[/]", id="msg-dialog-header")
            yield Static(
                "[bold]Reset Month[/] — delete cycle records for the current month.\n"
                "[bold]Drain All[/] — delete ALL cycle history (clears stale data).\n\n"
                "[dim]These actions cannot be undone.[/]"
            )
            with Horizontal(id="msg-dialog-actions"):
                yield Button("Reset Month", variant="error", id="btn-confirm-reset")
                yield Button("Drain All", variant="warning", id="btn-drain-all")
                yield Button("Cancel", variant="default", id="msg-dialog-close")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-confirm-reset":
            if self._agent_reader and self._agent_reader.reset_monthly_cycles():
                self.app.notify("Monthly tokens reset successfully", title="Reset")
                try:
                    footer = self.app.query_one(FooterStatsPanel)
                    footer.refresh_data()
                except Exception:
                    pass
            else:
                self.app.notify("Reset failed — check DB permissions", title="Error", severity="error")
            self.app.pop_screen()
        elif event.button.id == "btn-drain-all":
            if self._agent_reader and self._agent_reader.drain_all_cycles():
                self.app.notify("All cycle history drained", title="Drain")
                try:
                    footer = self.app.query_one(FooterStatsPanel)
                    footer.refresh_data()
                except Exception:
                    pass
            else:
                self.app.notify("Drain failed — check DB permissions", title="Error", severity="error")
            self.app.pop_screen()
        elif event.button.id == "msg-dialog-close":
            self.app.pop_screen()


class ResetLink(Static):
    """Clickable 'Reset' link in the footer."""
    def __init__(self, agent_reader: Optional[AgentReader], **kwargs):
        super().__init__(**kwargs)
        self.agent_reader = agent_reader

    def on_click(self) -> None:
        if self.agent_reader:
            self.app.push_screen(ResetConfirmModal(self.agent_reader))


class SessionLink(Static):
    """Clickable 'See Last Session' link."""
    def __init__(self, agent_reader: Optional[AgentReader], **kwargs):
        super().__init__(**kwargs)
        self.agent_reader = agent_reader

    def on_click(self) -> None:
        if not self.agent_reader:
            return
        cycle = self.agent_reader.get_last_cycle()
        if cycle:
            self.app.push_screen(SessionViewModal(cycle))


class FooterStatsPanel(Static):
    """Displays aggregate stats footer."""

    def __init__(self, agent_reader: Optional[AgentReader],
                 tasks_reader=None, **kwargs):
        super().__init__(**kwargs)
        self.agent_reader = agent_reader
        self.tasks_reader = tasks_reader

    def compose(self) -> ComposeResult:
        with Horizontal(id="footer-row"):
            yield FooterStatsLabel(id="footer-label")
            yield ResetLink(self.agent_reader, id="footer-reset-link")
            yield SessionLink(self.agent_reader, id="footer-session-link")

    def on_mount(self) -> None:
        self.refresh_data()

    def refresh_data(self) -> None:
        """Refresh footer stats from SQLite."""
        cycle = self.agent_reader.get_last_cycle() if self.agent_reader else None
        tokens = self.agent_reader.get_monthly_token_totals() if self.agent_reader else {}
        total_cycles = self.agent_reader.get_total_cycles_count() if self.agent_reader else 0

        last_exit = cycle.get("exit_code") if cycle else None
        if last_exit is None:
            last_exit = "--"
        month_tokens = tokens.get("total", 0) if tokens else 0
        month_input = tokens.get("input", 0) if tokens else 0
        month_output = tokens.get("output", 0) if tokens else 0
        month_think = tokens.get("thinking", 0) if tokens else 0
        month_cached = tokens.get("cached", 0) if tokens else 0

        exit_color = "green" if last_exit == 0 else "red" if isinstance(last_exit, int) else "dim"

        # Games and awareness counts
        games_count = 0
        awareness_count = 0
        if self.tasks_reader:
            games_count = self.tasks_reader.count_active_games()
            awareness_count = self.tasks_reader.count_active_awareness()

        self.query_one("#footer-label").update(
            f"CYCLES: [bold]{total_cycles}[/]    │    "
            f"LAST EXIT: [{exit_color}]{last_exit}[/{exit_color}]    │    "
            f"TOKEN  In: [cyan]{_fmt_tokens(month_input)}[/]  Out: [cyan]{_fmt_tokens(month_output)}[/]  "
            f"Think: [cyan]{_fmt_tokens(month_think)}[/]  Cached: [dim cyan]{_fmt_tokens(month_cached)}[/]  "
            f"Total: [bold cyan]{_fmt_tokens(month_tokens)}[/bold cyan]    │    "
            f"GAMES: [bold]{games_count}[/]    │    "
            f"AWARE: [bold]{awareness_count}[/]"
        )

        # Reset link text
        self.query_one("#footer-reset-link").update("  │  [bold u]Reset[/]")

        # Session link text
        has_session = cycle and cycle.get("json_output_path")
        link_text = "  │  [bold u]See Last Session[/]" if has_session else ""
        self.query_one("#footer-session-link").update(link_text)

