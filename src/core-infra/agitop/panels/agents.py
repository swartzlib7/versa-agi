"""Agents panel — agent status table with prompt viewer modals."""

import os
import time
from typing import Optional
from textual import on
from textual.app import ComposeResult
from textual.screen import ModalScreen
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import DataTable, Button, Static, Input

from agitop.data import AgentReader
from agitop.data.system_reader import SystemReader

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


class PromptViewModal(ModalScreen):
    """Modal dialog to show system or context prompt content."""

    def __init__(self, title: str, content: str, **kwargs):
        super().__init__(**kwargs)
        self.prompt_title = title
        self.prompt_content = content

    def compose(self) -> ComposeResult:
        # Escape Rich markup in prompt content
        safe_content = self.prompt_content.replace("[", "\\[") if self.prompt_content else "[dim](empty)[/]"
        with Vertical(id="msg-dialog"):
            yield Static(f"[bold]{self.prompt_title}[/]", id="msg-dialog-header")
            with VerticalScroll(id="msg-dialog-scroll"):
                yield Static(safe_content)
            yield Button("Close", variant="primary", id="msg-dialog-close")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "msg-dialog-close":
            self.app.pop_screen()


def _read_file(path: str) -> str:
    """Read a file with sudo fallback, return content or error string."""
    import subprocess
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
        except Exception:
            pass
    except FileNotFoundError:
        return f"(file not found: {path})\n\nThis file is generated after the first agent cycle following a patch deployment."
    except Exception as e:
        return f"(error: {e})"
    return "(permission denied)"


class AgentsPanel(DataTable):
    """Displays agent status table with real data and prompt viewer buttons."""

    def __init__(self, agent_reader: Optional[AgentReader],
                 system: SystemReader, **kwargs):
        super().__init__(**kwargs)
        self.agent_reader = agent_reader
        self.system_reader = system

    def on_mount(self) -> None:
        self.cursor_type = "row"
        self.border_title = "Agents (Global Registry & Telemetry)"
        self.add_columns(
            "Agent", "Role", "Inactive", "Protected", "Comms", "Req. By",
            f"Last Cycle ({_TZ})", "Sent", "Recv", "Tasks", "Tokens", "Budget", "Status"
        )
        self.refresh_data()

    def refresh_data(self) -> None:
        """Refresh agent data from SQLite and status files."""
        is_running = self.system_reader.is_agent_process_running()
        
        agents = self.agent_reader.get_all_agents() if self.agent_reader else []

        self.clear()

        # Build dynamic rows
        if not agents:
            agents = [{"name": "coa", "role": "Chief Orchestrator", "inactive": 0, "protected": 1, "requested_by": "System"}]

        for agent in agents:
            name = agent.get("name", "unknown")
            role = agent.get("role") or "--"
            is_inactive = agent.get("inactive", 0)
            agent_status_raw = agent.get("status") or ""
            agent_model = agent.get("model") or ""

            # Backend icon: ☁ cloud, 🖥 local, 🔀 proxy, ⚠ invalid
            local_models = self.system_reader.get_local_models()
            cloud_models = self.system_reader.get_cloud_models()
            third_party_models = self.system_reader.get_third_party_models()
            if agent_status_raw == "invalid_config":
                backend_icon = "⚠ "
            elif agent_model in local_models:
                backend_icon = "🖥 "
            elif agent_model in third_party_models:
                backend_icon = "🔀 "
            elif agent_model in cloud_models or not agent_model:
                backend_icon = "☁ "
            else:
                backend_icon = "? "

            if agent_status_raw == "removal_requested":
                inactive = "[red]⊘ removal pending[/]"
                name_markup = f"[red dim]{backend_icon}{name}[/]"
            elif agent_status_raw == "circuit_breaker":
                inactive = "[bold red]⚡ breaker tripped[/]"
                name_markup = f"[bold red]{backend_icon}{name}[/]"
            elif agent_status_raw == "halted":
                inactive = "[bold red]✋ halted[/]"
                name_markup = f"[bold red]{backend_icon}{name}[/]"
            elif agent_status_raw == "invalid_config":
                inactive = "[yellow]⚠ config error[/]"
                name_markup = f"[bold yellow]{backend_icon}{name}[/]"
            elif is_inactive:
                inactive = "[yellow]○ pending[/]"
                name_markup = f"[dim]{backend_icon}{name}[/]"
            else:
                inactive = "[green]● active[/]"
                name_markup = f"[bold white]{backend_icon}{name}[/]"
            protected = "[green]Yes[/]" if agent.get("protected") == 1 else "No"
            can_comms = agent.get("can_message_connections", 0)
            if agent.get("protected") == 1:
                comms_markup = "[green]● on[/]"
            elif can_comms:
                comms_markup = "[green]● on[/]"
            else:
                comms_markup = "[dim red]○ off[/]"
            req_by = agent.get("requested_by") or "--"

            # Per-agent cycle data
            cycle = self.agent_reader.get_last_cycle(name) if self.agent_reader else None

            last_cycle = "---"
            if cycle and cycle.get("ended_at"):
                last_cycle = _utc_to_local(cycle["ended_at"])[-8:-3]
            elif cycle and cycle.get("started_at"):
                last_cycle = _utc_to_local(cycle["started_at"])[-8:-3]

            stats = self.agent_reader.get_agent_lifetime_stats(name) if self.agent_reader else {"sent": 0, "received": 0, "tasks_done": 0}
            sent = str(stats.get("sent", 0))
            recv = str(stats.get("received", 0))
            tasks_done = str(stats.get("tasks_done", 0))

            cycle_tokens = cycle.get("tokens_total", 0) if cycle else 0
            from agitop.panels.footer_stats import _fmt_tokens
            tokens_str = _fmt_tokens(cycle_tokens) if cycle_tokens > 0 else "---"


            # Token budget display
            budget = agent.get("token_budget", 0)
            budget_str = "∞" if not budget else _fmt_tokens(budget)

            # Status
            agent_status = agent.get("status") or ""
            if agent_status == "circuit_breaker":
                status_msg = agent.get("status_message") or ""
                status_display = f"[bold red]⚡ BREAKER[/]"
            elif agent_status == "halted":
                status_msg = agent.get("status_message") or ""
                status_display = f"[bold red]✋ HALTED[/]"
            elif agent_status == "invalid_config":
                status_msg = agent.get("status_message") or ""
                status_display = f"[bold yellow]⚠ {agent_status}[/]"
            elif agent_status:
                status_display = f"[bold]{agent_status}[/]"
            else:
                status_display = "[dim]--[/]"

            # COA soft warning for non-native model (local or proxy)
            coa_warning = ""
            if name == "coa" and (agent_model in local_models or agent_model in third_party_models):
                coa_warning = " [yellow]⚠[/]"

            self.add_row(
                name_markup,
                role + coa_warning,
                inactive,
                protected,
                comms_markup,
                req_by,
                f"[cyan]{last_cycle}[/cyan]",
                f"[cyan]{sent}[/cyan]",
                f"[cyan]{recv}[/cyan]",
                f"[cyan]{tasks_done}[/cyan]",
                f"[cyan]{tokens_str}[/cyan]",
                f"[orange1]{budget_str}[/orange1]",
                status_display,
                key=name
            )

    @on(DataTable.RowSelected)
    def on_row_selected(self, event: DataTable.RowSelected) -> None:
        """Show prompt viewer options for the selected agent."""
        agent_name = event.row_key.value
        if agent_name:
            self.app.push_screen(AgentPromptMenu(agent_name))


class AgentPromptMenu(ModalScreen):
    """Prompt viewer menu — choose System Prompt or Context Prompt."""

    def __init__(self, agent_name: str, **kwargs):
        super().__init__(**kwargs)
        self.agent_name = agent_name

    def compose(self) -> ComposeResult:
        # Fetch agent details for the info section
        agents_panel = self.app.query_one(AgentsPanel)
        agents = agents_panel.agent_reader.get_all_agents() if agents_panel.agent_reader else []
        agent = next((a for a in agents if a.get("name") == self.agent_name), {})

        os_user = agent.get("os_user") or "--"
        workspace = agent.get("workspace") or "--"
        req_name = agent.get("requested_by_name") or agent.get("requested_by") or "--"
        created = _utc_to_local(agent.get("created_at") or "--")
        status = agent.get("status") or "--"
        status_msg = agent.get("status_message") or ""
        role = agent.get("role") or "--"
        model = agent.get("model") or "System default"
        ctx_mode = agent.get("context_injection_mode") or "relevant"
        budget = agent.get("token_budget", 0)
        budget_str = "Unlimited" if not budget else f"{budget:,}"
        num_ctx_val = agent.get("num_ctx", 0)
        if num_ctx_val and num_ctx_val > 0 and num_ctx_val >= 1024:
            num_ctx_str = f"{num_ctx_val // 1024}K"
        elif num_ctx_val and num_ctx_val > 0:
            num_ctx_str = str(num_ctx_val)
        else:
            num_ctx_str = "Auto"

        status_line = status + (f" — {status_msg}" if status_msg else "")
        info_text = (
            f"  [dim]Model:[/]  [cyan]{model}[/]        [dim]Role:[/]  {role:16s}  [dim]Budget:[/]  {budget_str}\n"
            f"  [dim]User:[/]   {os_user:16s}  [dim]Ctx:[/]   {ctx_mode:16s}  [dim]num_ctx:[/] {num_ctx_str}\n"
            f"  [dim]By:[/]     {req_name:16s}  [dim]Since:[/] {created}\n"
            f"  [dim]Status:[/] {status_line}"
        )

        is_pending = agent.get("inactive", 0) == 1
        is_removal_pending = (agent.get("status") or "") == "removal_requested"
        is_circuit_broken = (agent.get("status") or "") == "circuit_breaker"
        is_halted = (agent.get("status") or "") == "halted"
        is_protected = agent.get("protected") == 1

        with Vertical(id="msg-dialog"):
            yield Static(f"[bold]Prompts — {self.agent_name}[/]", id="msg-dialog-header")
            yield Static(info_text)
            if is_circuit_broken:
                yield Button("🔓 Clear Circuit Breaker", id="btn-clear-breaker", variant="error")
            elif is_halted:
                yield Button("▶ Re-activate Agent", id="btn-clear-breaker", variant="error")
            elif is_removal_pending:
                yield Button("🗑 Confirm Removal", id="btn-confirm-remove", variant="error")
                yield Button("↩ Cancel Removal", id="btn-cancel-remove", variant="warning")
            elif is_pending:
                yield Button("✓ Approve & Provision", id="btn-approve-agent", variant="success")
            # Show unfreeze button when agent has frozen tasks
            frozen_count = 0
            if hasattr(self.app, 'tasks_reader') and self.app.tasks_reader:
                frozen_count = self.app.tasks_reader.count_frozen(self.agent_name)
            if frozen_count > 0:
                yield Button(f"❄ Unfreeze Tasks ({frozen_count})", id="btn-unfreeze-tasks", variant="error")
            # Halt button — non-protected, active agents only (not already halted/breaker/removal)
            if not is_protected and not is_halted and not is_circuit_broken and not is_removal_pending and not is_pending:
                yield Button("✋ Halt Agent", id="btn-halt-agent", variant="error")
            yield Static("")
            with Horizontal(classes="btn-grid-row"):
                yield Button("Agent Settings", id="btn-edit-settings", variant="warning", classes="panel-btn")
                yield Button("⚙ Technical Setup", id="btn-tech-setup", variant="warning", classes="panel-btn")
                yield Button("View Memory", id="btn-view-memory", variant="primary", classes="panel-btn")
            with Horizontal(classes="btn-grid-row"):
                yield Button("Poise Template", id="btn-system-prompt", variant="primary", classes="panel-btn")
                yield Button("Last System Prompt", id="btn-context-prompt", variant="primary", classes="panel-btn")
                yield Button("📋 Cycle Log", id="btn-cycle-log", variant="primary", classes="panel-btn")
            with Horizontal(classes="btn-grid-row"):
                if not is_protected and not is_removal_pending:
                    yield Button("🗑 Request Removal", id="btn-request-remove", variant="error", classes="panel-btn")
                else:
                    yield Button("🗑 Request Removal", id="btn-request-remove-disabled", variant="error", classes="panel-btn", disabled=True)
                yield Button("Close", variant="default", id="msg-dialog-close", classes="panel-btn")
                yield Button("🧵 Manage Threads", id="btn-thread-manager", variant="primary", classes="panel-btn")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        name = self.agent_name
        if event.button.id == "btn-system-prompt":
            # Canonical poise location — deployed by setup.sh from repo templates.
            path = f"/etc/versa-agi/poise/{name}.md"
            content = _read_file(path)
            self.app.push_screen(PromptViewModal(f"Poise Template — {name}", content))
        elif event.button.id == "btn-context-prompt":
            path = f"/var/lib/versa-agi/{name}/last_prompt.txt"
            content = _read_file(path)
            self.app.push_screen(PromptViewModal(f"Context Prompt — {name}", content))
        elif event.button.id == "btn-edit-settings":
            self.app.push_screen(AgentEditModal(name))
        elif event.button.id == "btn-tech-setup":
            self.app.push_screen(TechnicalSetupModal(name))
        elif event.button.id == "btn-view-memory":
            self.app.push_screen(MemoryViewModal(name))
        elif event.button.id == "btn-cycle-log":
            from agitop.panels.cycle_log_modal import CycleLogModal
            self.app.push_screen(CycleLogModal(name, system_reader=self.app.query_one(AgentsPanel).system_reader))
        elif event.button.id == "btn-unfreeze-tasks":
            if hasattr(self.app, 'tasks_reader') and self.app.tasks_reader:
                count = self.app.tasks_reader.unfreeze_agent_tasks(name)
                if count > 0:
                    self.app.notify(f"Unfroze {count} task(s) for {name}", title="Tasks")
                    # Refresh the tasks panel
                    try:
                        from agitop.panels.tasks import TasksPanel
                        self.app.query_one("#tasks-panel", TasksPanel).refresh_data()
                    except Exception:
                        pass
                else:
                    self.app.notify(f"No frozen tasks found for {name}", title="Tasks")
            self.app.pop_screen()
        elif event.button.id == "btn-approve-agent":
            import subprocess, json as _json
            result = subprocess.run(
                ["agictl", "agent", "approve", self.agent_name],
                capture_output=True, text=True, timeout=30
            )
            self.app.pop_screen()
            if result.returncode == 0:
                try:
                    data = _json.loads(result.stdout)
                    if data.get("success"):
                        self.app.notify(f"✓ Agent '{self.agent_name}' approved & provisioned", severity="information")
                    else:
                        self.app.notify(f"Approve failed: {data.get('error', 'unknown')}", severity="error")
                except Exception:
                    self.app.notify(f"Approve returned: {result.stdout[:200]}", severity="warning")
            else:
                self.app.notify(f"Approve failed: {result.stderr[:200]}", severity="error")
            # Refresh agents table
            try:
                self.app.query_one(AgentsPanel).refresh_data()
            except Exception:
                pass
        elif event.button.id == "btn-confirm-remove":
            import subprocess, json as _json
            result = subprocess.run(
                ["agictl", "agent", "confirm-remove", self.agent_name],
                capture_output=True, text=True, timeout=60
            )
            self.app.pop_screen()
            if result.returncode == 0:
                try:
                    data = _json.loads(result.stdout)
                    if data.get("success"):
                        archive = data.get("archive", "none")
                        self.app.notify(
                            f"✓ Agent '{self.agent_name}' removed (archive: {archive})",
                            severity="information"
                        )
                    else:
                        self.app.notify(f"Removal failed: {data.get('error', 'unknown')}", severity="error")
                except Exception:
                    self.app.notify(f"Removal returned: {result.stdout[:200]}", severity="warning")
            else:
                self.app.notify(f"Removal failed: {result.stderr[:200]}", severity="error")
            try:
                self.app.query_one(AgentsPanel).refresh_data()
            except Exception:
                pass
        elif event.button.id == "btn-cancel-remove":
            import subprocess, json as _json
            result = subprocess.run(
                ["agictl", "agent", "cancel-remove", self.agent_name],
                capture_output=True, text=True, timeout=10
            )
            self.app.pop_screen()
            if result.returncode == 0:
                self.app.notify(f"↩ Removal cancelled — '{self.agent_name}' reactivated", severity="information")
            else:
                self.app.notify(f"Cancel failed: {result.stderr[:200]}", severity="error")
            try:
                self.app.query_one(AgentsPanel).refresh_data()
            except Exception:
                pass
        elif event.button.id == "btn-request-remove":
            self.app.push_screen(RemovalConfirmModal(self.agent_name))
        elif event.button.id == "btn-clear-breaker":
            import subprocess, json as _json
            result = subprocess.run(
                ["agictl", "agent", "activate", self.agent_name],
                capture_output=True, text=True, timeout=15
            )
            self.app.pop_screen()
            if result.returncode == 0:
                try:
                    data = _json.loads(result.stdout)
                    if data.get("success"):
                        tasks_status = data.get("tasks", "")
                        self.app.notify(
                            f"⚡ Circuit breaker cleared for '{self.agent_name}' — tasks {tasks_status}",
                            severity="information"
                        )
                    else:
                        self.app.notify(f"Reset failed: {data.get('error', 'unknown')}", severity="error")
                except Exception:
                    self.app.notify(f"Reset returned: {result.stdout[:200]}", severity="warning")
            else:
                self.app.notify(f"Reset failed: {result.stderr[:200]}", severity="error")
            try:
                self.app.query_one(AgentsPanel).refresh_data()
            except Exception:
                pass
        elif event.button.id == "btn-halt-agent":
            import subprocess, json as _json
            result = subprocess.run(
                ["agictl", "agent", "kill", self.agent_name],
                capture_output=True, text=True, timeout=15
            )
            self.app.pop_screen()
            if result.returncode == 0:
                try:
                    data = _json.loads(result.stdout)
                    if data.get("success"):
                        was_running = data.get("was_running", False)
                        if was_running:
                            self.app.notify(
                                f"✋ Agent '{self.agent_name}' halted — cycle terminated",
                                severity="warning"
                            )
                        else:
                            self.app.notify(
                                f"✋ Agent '{self.agent_name}' halted — no running cycle",
                                severity="information"
                            )
                    else:
                        self.app.notify(f"Halt failed: {data.get('error', 'unknown')}", severity="error")
                except Exception:
                    self.app.notify(f"Halt returned: {result.stdout[:200]}", severity="warning")
            else:
                self.app.notify(f"Halt failed: {result.stderr[:200]}", severity="error")
            try:
                self.app.query_one(AgentsPanel).refresh_data()
            except Exception:
                pass
        elif event.button.id == "btn-thread-manager":
            from agitop.panels.thread_manager_modal import ThreadManagerModal
            self.app.push_screen(ThreadManagerModal(self.agent_name))
        elif event.button.id == "msg-dialog-close":
            self.app.pop_screen()


class RemovalConfirmModal(ModalScreen):
    """Confirmation before requesting agent removal."""

    def __init__(self, agent_name: str, **kwargs):
        super().__init__(**kwargs)
        self.agent_name = agent_name

    def compose(self) -> ComposeResult:
        with Vertical(id="msg-dialog"):
            yield Static("[bold]Confirm Removal Request[/]", id="msg-dialog-header")
            yield Static(
                f"[bold red]Request removal of agent '{self.agent_name}'?[/]\n\n"
                "This will flag the agent for removal.\n"
                "You will need to confirm the removal separately via the dashboard.\n\n"
                "[dim]The agent will be deactivated immediately and will not spawn.[/]"
            )
            with Horizontal(id="msg-dialog-actions"):
                yield Button("🗑 Request Removal", variant="error", id="btn-do-remove")
                yield Button("Cancel", variant="default", id="msg-dialog-close")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-do-remove":
            import subprocess, json as _json
            result = subprocess.run(
                ["agictl", "agent", "request-remove", self.agent_name],
                capture_output=True, text=True, timeout=10
            )
            self.app.pop_screen()  # Remove confirm modal
            if result.returncode == 0:
                self.app.notify(f"🗑 Removal requested for '{self.agent_name}' — confirm when ready", severity="warning")
            else:
                self.app.notify(f"Request failed: {result.stderr[:200]}", severity="error")
            try:
                self.app.query_one(AgentsPanel).refresh_data()
            except Exception:
                pass
        elif event.button.id == "msg-dialog-close":
            self.app.pop_screen()


def _load_models_ini(system_reader: Optional[SystemReader] = None) -> list[tuple[str, str]]:
    """Load available models, filtered by execution mode and enabled backends.
    
    Returns list of (label, key) tuples. When system_reader is provided,
    filters models based on which backends are enabled (cloud/local/proxy).
    
    Sections read from models.ini:
      [models]              — Cloud Native (Gemini) display labels
      [third_party_models]  — Third-Party Cloud Providers (xAI, etc.)
      [local_models]        — Local AI models (Ollama / SYCL) display labels
      [context_windows]     — Context window sizes (read by model_context.py)
    """
    import configparser, os
    ini = configparser.ConfigParser()
    # Try deployed path first, then source path
    for path in ["/var/lib/versa-agi/config/models.ini",
                 os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                              "..", "config", "models.ini")]:
        if os.path.exists(path):
            ini.read(path)
            break

    # Load cloud model labels from [models] section
    cloud_entries = []
    if ini.has_section("models"):
        for key, label in ini.items("models"):
            cloud_entries.append((label.strip(), key.strip()))

    # Load third-party model labels from [third_party_models] section
    proxy_entries = []
    if ini.has_section("third_party_models"):
        for key, label in ini.items("third_party_models"):
            proxy_entries.append((label.strip(), key.strip()))

    # Load local model labels from [local_models] section
    local_label_map = {}
    if ini.has_section("local_models"):
        for key, label in ini.items("local_models"):
            local_label_map[key.strip()] = label.strip()

    if not cloud_entries and not proxy_entries and not local_label_map:
        # Fallback if ini not found
        return [("gemini-3-flash-preview", "gemini-3-flash-preview")]

    # Without system_reader, return all entries unfiltered
    if not system_reader:
        local_entries = [(label, key) for key, label in local_label_map.items()]
        return cloud_entries + proxy_entries + local_entries

    # Backend-aware filtering: only show models for enabled backends
    cloud_set = set(system_reader.get_cloud_models())
    local_set = set(system_reader.get_local_models())
    proxy_set = set(system_reader.get_third_party_models())
    local_enabled = system_reader.is_local_ai_enabled()
    proxy_enabled = system_reader.is_third_party_enabled()

    filtered = []

    # Cloud models: only if cloud_models list is non-empty (API key configured)
    if cloud_set:
        for label, key in cloud_entries:
            if key in cloud_set:
                filtered.append((f"☁ {label}", key))

    # Third-party models: only if third_party enabled
    if proxy_enabled and proxy_set:
        for label, key in proxy_entries:
            if key in proxy_set:
                filtered.append((f"🔀 {label}", key))

    # Local models: only if local_ai enabled — use labels from models.ini
    if local_enabled and local_set:
        for m in local_set:
            label = local_label_map.get(m, m)  # Fall back to raw name if no label
            filtered.append((f"🖥 {label}", m))

    return filtered if filtered else [("gemini-3-flash-preview", "gemini-3-flash-preview")]


class TechnicalSetupModal(ModalScreen):
    """Modal to view/edit agent harness configuration (max steps, tool budget, triage model)."""

    def __init__(self, agent_name: str, **kwargs):
        super().__init__(**kwargs)
        self.agent_name = agent_name

    def compose(self) -> ComposeResult:
        from textual.containers import Horizontal
        from textual.widgets import Select
        agents_panel = self.app.query_one(AgentsPanel)
        agents = agents_panel.agent_reader.get_all_agents() if agents_panel.agent_reader else []
        agent = next((a for a in agents if a.get("name") == self.agent_name), {})

        current_turns = str(agent.get("max_session_turns", 50))
        current_tool_budget = str(agent.get("tool_output_token_budget", 1500))
        current_triage_model = agent.get("triage_model") or ""
        current_budget = str(agent.get("token_budget", 0))
        current_timeout = str(agent.get("timeout_minutes", 60))
        current_threshold = str(agent.get("runaway_threshold", 300))
        current_size_threshold = str(agent.get("runaway_size_threshold", 512))
        current_num_ctx = agent.get("num_ctx", 0)
        current_model = agent.get("model") or ""

        # Load model options for triage model selector
        model_options = _load_models_ini(agents_panel.system_reader)
        triage_kwargs = {"id": "select-triage-model", "allow_blank": True, "prompt": "Use agent model"}
        if current_triage_model and any(k == current_triage_model for _, k in model_options):
            triage_kwargs["value"] = current_triage_model

        # Load num_ctx picklist options filtered by model's max context
        try:
            from harness.model_context import get_num_ctx_options, get_model_context, is_cloud_model
            is_cloud = is_cloud_model(current_model)
            ctx_options = get_num_ctx_options(current_model)
        except ImportError:
            is_cloud = False
            ctx_options = [("32K", 32768)]

        with VerticalScroll(id="msg-dialog"):
            yield Static(f"[bold]⚙ Technical Setup — {self.agent_name}[/]", id="msg-dialog-header")
            with VerticalScroll(id="msg-dialog-scroll"):
                yield Static("[cyan]Max Graph Steps (Recursion Limit)[/] — max LangGraph tool iterations")
                yield Input(value=current_turns, placeholder="e.g. 50", id="input-max-turns", type="integer")
                yield Static("[cyan]Tool Output Limit (Characters)[/] — truncate run_shell_command output")
                yield Input(value=current_tool_budget, placeholder="e.g. 6000", id="input-tool-budget", type="integer")
                yield Static("[cyan]Triage Model[/] — lightweight model for message classification (blank = use agent model)")
                yield Select(
                    model_options,
                    **triage_kwargs,
                )
                if not is_cloud and ctx_options:
                    yield Static("[cyan]Context Window (num_ctx)[/] — Ollama context window size in tokens")
                    num_ctx_select_options = [("Auto (model default)", 0)] + [(label, value) for label, value in ctx_options]
                    yield Select(
                        num_ctx_select_options,
                        value=current_num_ctx if current_num_ctx in [v for _, v in num_ctx_select_options] else 0,
                        id="select-num-ctx",
                        allow_blank=False,
                    )
                yield Static("[cyan]Token Budget (monthly)[/] — max tokens per month (0 = unlimited)")
                yield Input(value=current_budget, placeholder="e.g. 5000000 (0=unlimited)", id="input-budget", type="integer")
                yield Static("[cyan]Timeout (minutes)[/] — max runtime before agent is killed")
                yield Input(value=current_timeout, placeholder="e.g. 30", id="input-timeout", type="integer")
                yield Static("[cyan]Runaway Threshold (lines)[/] — max output lines before freeze")
                yield Input(value=current_threshold, placeholder="e.g. 300", id="input-threshold", type="integer")
                yield Static("[cyan]Runaway Size Threshold (KB)[/] — max result/session file size before freeze")
                yield Input(value=current_size_threshold, placeholder="e.g. 512", id="input-size-threshold", type="integer")
                yield Static("")
                yield Static("[bold cyan]─── LangGraph Resume Control ───[/]")
                yield Static("[cyan]Resume Enabled[/] — whether the agent resumes from checkpoint state")
                yield Select(
                    [("Yes (resume from checkpoint)", 1), ("No (fresh start each cycle)", 0)],
                    value=agent.get("resume_enabled", 1),
                    id="select-resume-enabled",
                    allow_blank=False,
                )
                yield Static("[cyan]Resume Max Messages[/] — trim checkpoint state to last N messages (0 = unlimited)")
                yield Input(value=str(agent.get("resume_max_messages", 0)), placeholder="0 = unlimited", id="input-resume-max-msgs", type="integer")
                yield Static("[dim]Thread-level resets: use 🧵 Manage Threads on the Agent Prompt Menu modal.[/]")
            with Horizontal(id="msg-dialog-actions"):
                yield Button("Save", variant="success", id="btn-save-setup")
                yield Button("Cancel", variant="default", id="msg-dialog-close")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        from textual.widgets import Select
        if event.button.id == "btn-save-setup":
            agents_panel = self.app.query_one(AgentsPanel)
            reader = agents_panel.agent_reader
            if reader:
                try:
                    turns = int(self.query_one("#input-max-turns", Input).value)
                    tool_budget = int(self.query_one("#input-tool-budget", Input).value)
                    budget_val = int(self.query_one("#input-budget", Input).value)
                    timeout_val = int(self.query_one("#input-timeout", Input).value)
                    threshold_val = int(self.query_one("#input-threshold", Input).value)
                    size_threshold_val = int(self.query_one("#input-size-threshold", Input).value)
                    triage_select = self.query_one("#select-triage-model", Select)
                    triage_model = triage_select.value if isinstance(triage_select.value, str) and triage_select.value else None

                    ok = all([
                        reader.update_agent_field(self.agent_name, "max_session_turns", turns),
                        reader.update_agent_field(self.agent_name, "tool_output_token_budget", tool_budget),
                        reader.update_agent_field(self.agent_name, "triage_model", triage_model),
                        reader.update_agent_field(self.agent_name, "token_budget", budget_val),
                        reader.update_agent_field(self.agent_name, "timeout_minutes", timeout_val),
                        reader.update_agent_field(self.agent_name, "runaway_threshold", threshold_val),
                        reader.update_agent_field(self.agent_name, "runaway_size_threshold", size_threshold_val),
                    ])
                    # Update num_ctx if the Select exists (non-cloud models)
                    try:
                        num_ctx_select = self.query_one("#select-num-ctx", Select)
                        num_ctx_val = num_ctx_select.value
                        if isinstance(num_ctx_val, int):
                            ok = ok and reader.update_agent_field(self.agent_name, "num_ctx", num_ctx_val)
                    except Exception:
                        pass  # Cloud models don't have the select
                    # Save resume controls
                    resume_select = self.query_one("#select-resume-enabled", Select)
                    resume_val = resume_select.value if isinstance(resume_select.value, int) else 1
                    ok = ok and reader.update_agent_field(self.agent_name, "resume_enabled", resume_val)
                    resume_max = int(self.query_one("#input-resume-max-msgs", Input).value)
                    ok = ok and reader.update_agent_field(self.agent_name, "resume_max_messages", resume_max)
                    if ok:
                        self.app.notify(f"Settings saved for {self.agent_name}", title="Technical Setup")
                    else:
                        self.app.notify("Save failed — check DB permissions", title="Error", severity="error")
                except ValueError:
                    self.app.notify("Invalid input — must be valid numbers", title="Error", severity="error")
                except Exception as e:
                    self.app.notify(f"Error: {e}", title="Error", severity="error")
            self.app.pop_screen()
        elif event.button.id == "msg-dialog-close":
            self.app.pop_screen()


class SyclActivationModal(ModalScreen):
    """Confirmation modal for Intel SYCL model activation.

    Defers model+num_ctx DB write until the user confirms server-side activation.
    On confirm, updates ALL agents using local models (SYCL = single active model).
    Disables CRON on mount to prevent agents spawning with a mismatched model.
    Re-enables CRON on confirm or cancel.
    """

    def __init__(self, model_name: str, topology: str, agent_name: str, pending_num_ctx: int, **kwargs):
        super().__init__(**kwargs)
        self.model_name = model_name
        self.topology = topology
        self.agent_name = agent_name
        self.pending_num_ctx = pending_num_ctx
        self._cron_was_enabled = False

    def compose(self) -> ComposeResult:
        from textual.containers import Horizontal
        yield Static("[bold yellow]⚠ SYCL Model Activation Required[/]\n", id="sycl-title")
        yield Static(
            f"The active model on the inference server must be changed to [bold cyan]{self.model_name}[/].\n\n"
            f"[bold]All agents using local models will be updated.[/]\n\n"
            f"CRON has been [bold red]paused[/] to prevent agents from spawning with a mismatched model.\n"
        )
        if self.topology == "client":
            yield Static(
                f"[bold]Run on the server:[/]\n\n"
                f"  [green]sudo agictl model activate {self.model_name}[/]\n"
            )
        else:
            yield Static(
                f"[bold]Run:[/]\n\n"
                f"  [green]sudo agictl model activate {self.model_name}[/]\n"
            )
        yield Static("\nPress [bold]Confirm[/] once the model has been activated.\n")
        with Horizontal(id="msg-dialog-actions"):
            yield Button("Confirm — Model Activated", variant="success", id="btn-sycl-confirm")
            yield Button("Cancel", variant="default", id="btn-sycl-cancel")

    def on_mount(self) -> None:
        """Disable CRON when the modal opens."""
        try:
            agents_panel = self.app.query_one(AgentsPanel)
            if agents_panel.system_reader:
                self._cron_was_enabled = agents_panel.system_reader.is_cron_enabled()
                if self._cron_was_enabled:
                    agents_panel.system_reader.toggle_cron()
                    self.app.notify("CRON paused during model activation", title="Lifeline")
        except Exception:
            pass

    def _resume_cron(self) -> None:
        """Re-enable CRON if it was enabled before the modal opened."""
        if not self._cron_was_enabled:
            return
        try:
            agents_panel = self.app.query_one(AgentsPanel)
            if agents_panel.system_reader:
                new_state = agents_panel.system_reader.toggle_cron()
                if new_state:
                    self.app.notify("CRON resumed — agents will spawn on next tick", title="Lifeline")
                else:
                    self.app.notify("CRON toggle returned unexpected state", title="Lifeline", severity="warning")
        except Exception as e:
            self.app.notify(f"Failed to resume CRON: {e}", title="Error", severity="error")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-sycl-confirm":
            # Update ALL agents using local models (SYCL = single active model constraint)
            updated_count = 0
            try:
                agents_panel = self.app.query_one(AgentsPanel)
                if agents_panel.agent_reader:
                    from harness.model_context import is_cloud_model
                    all_agents = agents_panel.agent_reader.get_all_agents()
                    for agent in all_agents:
                        agent_model = agent.get("model") or ""
                        if agent_model and not is_cloud_model(agent_model):
                            agents_panel.agent_reader.update_agent_field(
                                agent["name"], "model", self.model_name
                            )
                            agents_panel.agent_reader.update_agent_field(
                                agent["name"], "num_ctx", self.pending_num_ctx
                            )
                            updated_count += 1
                    agents_panel.refresh_data()
                    self.app.notify(
                        f"Model set to {self.model_name} for {updated_count} agent(s)",
                        title="Agent Settings",
                    )
            except Exception as e:
                self.app.notify(f"DB update error: {e}", title="Error", severity="error")
            # Re-enable CRON
            self._resume_cron()
            self.app.pop_screen()
        elif event.button.id == "btn-sycl-cancel":
            # No DB write happened — just re-enable CRON and refresh UI
            try:
                agents_panel = self.app.query_one(AgentsPanel)
                agents_panel.refresh_data()
            except Exception:
                pass
            self._resume_cron()
            self.app.notify("Model change cancelled", title="Agent Settings")
            self.app.pop_screen()


class AgentEditModal(ModalScreen):
    """Modal to edit agent timeout, runaway threshold, model, and inactive flag."""

    def __init__(self, agent_name: str, **kwargs):
        super().__init__(**kwargs)
        self.agent_name = agent_name

    def compose(self) -> ComposeResult:
        from textual.widgets import Select
        from textual.containers import Horizontal
        # Read current values from the app's agent reader
        agents_panel = self.app.query_one(AgentsPanel)
        agents = agents_panel.agent_reader.get_all_agents() if agents_panel.agent_reader else []
        agent = next((a for a in agents if a.get("name") == self.agent_name), {})
        current_model = agent.get("model") or ""
        self._original_model = current_model  # Track for change detection
        self._original_num_ctx = agent.get("num_ctx", 0)  # Track for revert
        is_protected = agent.get("protected") == 1
        current_inactive = agent.get("inactive", 0)
        current_comms = agent.get("can_message_connections", 0)
        current_ctx_mode = agent.get("context_injection_mode") or "relevant"
        current_status = agent.get("status") or ""
        current_anchor = agent.get("anchor_style") or "compact"

        # Load model options — mode-aware filtering
        agents_panel = self.app.query_one(AgentsPanel)
        model_options = _load_models_ini(agents_panel.system_reader)

        # COA model restriction: only approved models for the orchestrator
        if is_protected and self.agent_name == "coa":
            coa_allowed = set(agents_panel.system_reader.get_coa_approved_models())
            if coa_allowed:
                model_options = [(label, key) for label, key in model_options if key in coa_allowed]

        # Build Select kwargs — only set value when model matches an option
        model_kwargs = {"id": "select-model", "allow_blank": True, "prompt": "System default"}
        if current_model and any(k == current_model for _, k in model_options):
            model_kwargs["value"] = current_model

        # Status options removed — all statuses are system-managed
        # (idle, active, circuit_breaker, halted, invalid_config)
        # Use "✋ Halt Agent" button on the Agent Prompt Menu for manual control.

        with VerticalScroll(id="msg-dialog"):
            yield Static(f"[bold]⚙  Settings — {self.agent_name}[/]", id="msg-dialog-header")
            with VerticalScroll(id="msg-dialog-scroll"):
                yield Static("[cyan]Model[/] — AI model for this agent")
                yield Select(
                    model_options,
                    **model_kwargs,
                )
                yield Static("[cyan]Context Injection Mode[/] — how connection memories are injected")
                yield Select(
                    [("All contacts (COA default)", "all"), ("Relevant contacts only", "relevant")],
                    value=current_ctx_mode,
                    id="select-ctx-mode",
                    allow_blank=False,
                )
                yield Static("[cyan]Conversation Depth[/] — historical messages per contact in prompt injection")
                yield Input(value=str(agent.get("conversation_depth", 10)), placeholder="e.g. 10", id="input-convo-depth", type="integer")
                yield Static("[cyan]Anchor Style[/] — philosophical anchor prepended to poise")
                yield Select(
                    [("Full (philosophical block)", "full"), ("Compact (identity line only)", "compact")],
                    value=current_anchor,
                    id="select-anchor-style",
                    allow_blank=False,
                )
                if not is_protected:
                    yield Static("[cyan]Inactive[/] — toggle agent activation")
                    yield Select(
                        [("Active (spawnable)", 0), ("Inactive (pending approval)", 1)],
                        value=current_inactive,
                        id="select-inactive",
                        allow_blank=False,
                    )
                    yield Static("[cyan]External Comms[/] — allow agent to message contacts")
                    yield Select(
                        [("Enabled", 1), ("Disabled", 0)],
                        value=current_comms,
                        id="select-comms",
                        allow_blank=False,
                    )
            with Horizontal(id="msg-dialog-actions"):
                yield Button("Save", variant="success", id="btn-save-settings")
                yield Button("Cancel", variant="default", id="msg-dialog-close")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        from textual.widgets import Select
        if event.button.id == "btn-save-settings":
            agents_panel = self.app.query_one(AgentsPanel)
            reader = agents_panel.agent_reader
            sycl_activation_needed = False
            new_model = ""
            topology = "local"
            pending_num_ctx = 0
            if reader:
                try:
                    # Determine model selection and check if SYCL activation is needed
                    ok_model = True
                    try:
                        model_select = self.query_one("#select-model", Select)
                        model_val = model_select.value
                        if not isinstance(model_val, str) or not model_val:
                            ok_model = reader.update_agent_field(self.agent_name, "model", None)
                        else:
                            # Check if this is a SYCL model change — defer DB write if so
                            needs_sycl = False
                            if model_val != self._original_model:
                                try:
                                    from harness.model_context import is_cloud_model, get_model_context
                                    if not is_cloud_model(model_val):
                                        system_reader = agents_panel.system_reader
                                        if system_reader and system_reader.get_gpu_backend() in ("intel", "remote"):
                                            needs_sycl = True
                                            sycl_activation_needed = True
                                            new_model = model_val
                                            topology = system_reader.get_topology()
                                            recommended, _ = get_model_context(model_val)
                                            pending_num_ctx = recommended
                                except Exception:
                                    pass

                            if needs_sycl:
                                pass  # Defer model+num_ctx write to SyclActivationModal
                            else:
                                ok_model = reader.update_agent_field(self.agent_name, "model", model_val)
                                # Auto-reset num_ctx to the new model's recommended default
                                try:
                                    from harness.model_context import get_model_context
                                    recommended, _ = get_model_context(model_val)
                                    reader.update_agent_field(self.agent_name, "num_ctx", recommended)
                                except ImportError:
                                    pass
                    except Exception:
                        pass
                    # Update context injection mode
                    ok_ctx = True
                    try:
                        ctx_select = self.query_one("#select-ctx-mode", Select)
                        ctx_val = ctx_select.value
                        if isinstance(ctx_val, str) and ctx_val:
                            ok_ctx = reader.update_agent_field(self.agent_name, "context_injection_mode", ctx_val)
                    except Exception:
                        pass
                    # Update conversation depth
                    ok_depth = True
                    try:
                        depth_val = int(self.query_one("#input-convo-depth", Input).value)
                        ok_depth = reader.update_agent_field(self.agent_name, "conversation_depth", depth_val)
                    except Exception:
                        pass
                    # Status picklist removed — statuses are system-managed
                    ok_status = True
                    # Update inactive flag if the Select exists (not protected)
                    ok_inactive = True
                    try:
                        inactive_select = self.query_one("#select-inactive", Select)
                        inactive_val = inactive_select.value
                        ok_inactive = reader.update_agent_field(self.agent_name, "inactive", inactive_val)
                    except Exception:
                        pass  # Protected agents don't have the select
                    # Update comms flag if the Select exists (not protected)
                    ok_comms = True
                    try:
                        comms_select = self.query_one("#select-comms", Select)
                        comms_val = comms_select.value
                        ok_comms = reader.update_agent_field(self.agent_name, "can_message_connections", comms_val)
                    except Exception:
                        pass  # Protected agents don't have the select
                    # Update anchor style
                    ok_anchor = True
                    try:
                        anchor_select = self.query_one("#select-anchor-style", Select)
                        anchor_val = anchor_select.value
                        if isinstance(anchor_val, str) and anchor_val:
                            ok_anchor = reader.update_agent_field(self.agent_name, "anchor_style", anchor_val)
                    except Exception:
                        pass
                    if all([ok_model, ok_ctx, ok_depth, ok_status, ok_inactive, ok_comms, ok_anchor]):
                        self.app.notify(f"Saved settings for {self.agent_name}", title="Agent Settings")
                        agents_panel.refresh_data()
                    else:
                        self.app.notify("Save failed — check DB permissions", title="Error", severity="error")
                except ValueError:
                    self.app.notify("Invalid input — must be whole numbers", title="Error", severity="error")
            self.app.pop_screen()
            # Push SYCL activation modal after closing the edit modal
            if sycl_activation_needed:
                self.app.push_screen(SyclActivationModal(
                    new_model, topology, self.agent_name, pending_num_ctx,
                ))
        elif event.button.id == "msg-dialog-close":
            self.app.pop_screen()


class MemoryViewModal(ModalScreen):
    """Modal to view agent memory (connection, project, system)."""

    def __init__(self, agent_name: str, **kwargs):
        super().__init__(**kwargs)
        self.agent_name = agent_name

    def compose(self) -> ComposeResult:
        import sqlite3
        import os
        from textual.containers import Horizontal

        tasks_db = os.getenv("AGICTL_TASKS_DB", "/var/lib/versa-agi/coa/tasks.db")
        content = ""

        try:
            conn = sqlite3.connect(tasks_db, timeout=5)
            conn.row_factory = sqlite3.Row

            # Build UID→name cache from connections table
            name_cache = {}
            try:
                name_rows = conn.execute("SELECT uid, display_name FROM connections").fetchall()
                for nr in name_rows:
                    if nr["uid"] and nr["display_name"] and nr["display_name"] != "Unknown":
                        name_cache[nr["uid"]] = nr["display_name"]
            except Exception:
                pass

            # Add Primary User from config (not in connections table)
            try:
                import json
                config_path = os.getenv("AGICTL_CONFIG", "/etc/versa-agi/coa_config.json")
                with open(config_path) as f:
                    cfg = json.load(f)
                pu = cfg.get("primary_user", {})
                pu_uid = pu.get("uid")
                pu_name = pu.get("display_name")
                if pu_uid and pu_name:
                    name_cache[pu_uid] = pu_name
            except Exception:
                pass

            # Connection memories
            conn_rows = conn.execute(
                "SELECT * FROM agent_memory_connection WHERE agent_name=? ORDER BY updated_at DESC",
                (self.agent_name,)
            ).fetchall()
            if conn_rows:
                content += "[bold cyan]━━━ Connection Memory ━━━[/]\n\n"
                for r in conn_rows:
                    r = dict(r)
                    uid = r.get('contact_uid') or ''
                    display_name = name_cache.get(uid, uid[:12] + "...")
                    content += f"  [bold]{display_name}[/] [dim]({uid[:12]}...)[/]\n"
                    if r.get('preferences'):
                        content += f"    Preferences: {r['preferences']}\n"
                    if r.get('communication_style'):
                        content += f"    Comm style: {r['communication_style']}\n"
                    if r.get('rapport_level'):
                        content += f"    Rapport: [green]{r['rapport_level']}[/]\n"
                    if r.get('personal_notes'):
                        content += f"    Notes: {r['personal_notes']}\n"
                    if r.get('emotional_notes'):
                        content += f"    Emotional: {r['emotional_notes']}\n"
                    if r.get('last_interaction'):
                        content += f"    Last interaction: {r['last_interaction']}\n"
                    content += f"    [dim]Updated: {_utc_to_local(r.get('updated_at', '--'))}[/]\n\n"
            else:
                content += "[dim]No connection memories stored yet.[/]\n\n"

            # Project memories
            proj_rows = conn.execute(
                "SELECT * FROM agent_memory_project WHERE agent_name=? ORDER BY updated_at DESC",
                (self.agent_name,)
            ).fetchall()
            if proj_rows:
                content += "[bold cyan]━━━ Project Memory ━━━[/]\n\n"
                for r in proj_rows:
                    r = dict(r)
                    content += f"  [bold]Project #{r.get('project_id', '?')}[/]\n"
                    if r.get('current_phase'):
                        content += f"    Phase: {r['current_phase']}\n"
                    if r.get('key_decisions'):
                        content += f"    Decisions: {r['key_decisions']}\n"
                    if r.get('blockers'):
                        content += f"    Blockers: [red]{r['blockers']}[/]\n"
                    if r.get('next_steps'):
                        content += f"    Next: {r['next_steps']}\n"
                    content += f"    [dim]Updated: {_utc_to_local(r.get('updated_at', '--'))}[/]\n\n"
            else:
                content += "[dim]No project memories stored yet.[/]\n\n"

            # System memories (global — shared across all agents)
            sys_rows = conn.execute(
                "SELECT * FROM agent_memory_system ORDER BY key ASC"
            ).fetchall()
            if sys_rows:
                content += "[bold cyan]━━━ System Memory (Global) ━━━[/]\n\n"
                for r in sys_rows:
                    r = dict(r)
                    stored_by = r.get('agent_name', '?')
                    content += f"  [bold]{r.get('key', '?')}[/]: {r.get('value', '')}\n"
                    content += f"    [dim]By: {stored_by} │ Updated: {_utc_to_local(r.get('updated_at', '--'))}[/]\n"
            else:
                content += "[dim]No system memories stored yet.[/]\n"

            conn.close()
        except Exception as e:
            content = f"[red]Error reading memory: {e}[/]"

        with VerticalScroll(id="msg-dialog"):
            yield Static(f"[bold]🧠 Memory — {self.agent_name}[/]", id="msg-dialog-header")
            with VerticalScroll(id="msg-dialog-scroll"):
                yield Static(content)
            yield Button("Close", variant="primary", id="msg-dialog-close")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "msg-dialog-close":
            self.app.pop_screen()
