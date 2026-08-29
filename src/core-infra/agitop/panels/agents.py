"""Agents panel — agent status table with prompt viewer modals."""

import json
import os
import time
from typing import Optional
from textual import on
from textual.app import ComposeResult
from textual.screen import ModalScreen
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import DataTable, Button, Static, Input, TextArea, Select

from agitop.data import AgentReader
from agitop.data.system_reader import SystemReader
from agitop.panels.agent_prompt_menu import AgentPromptMenu
from agitop.panels.model_params_ui import format_json_pretty, parse_json_object

_TZ = time.strftime("%Z")


def _agent_extra_display(raw: str | None) -> str:
    """Pretty-print stored agent extra JSON for TextArea display."""
    if not raw:
        return ""
    return format_json_pretty(raw)

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
        self.prompt_content = content or "(empty)"

    def compose(self) -> ComposeResult:
        with Vertical(id="msg-dialog"):
            yield Static(f"[bold]{self.prompt_title}[/]", id="msg-dialog-header")
            yield TextArea(self.prompt_content, id="prompt-view-body", read_only=True)
            with Horizontal(id="msg-dialog-actions"):
                yield Button("📋 Copy All", variant="default", id="prompt-copy-all")
                yield Button("Close", classes="dismiss-btn", variant="default", id="msg-dialog-close")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        import subprocess
        if event.button.id == "prompt-copy-all":
            try:
                subprocess.run(["xclip", "-selection", "clipboard"], input=self.prompt_content.encode(), check=True)
                self.app.notify("Prompt copied to clipboard", title="Clipboard")
            except FileNotFoundError:
                try:
                    subprocess.run(["xsel", "--clipboard", "--input"], input=self.prompt_content.encode(), check=True)
                    self.app.notify("Prompt copied to clipboard", title="Clipboard")
                except Exception:
                    self.app.notify("Install xclip or xsel for clipboard support", severity="warning")
            except Exception:
                self.app.notify("Clipboard copy failed", severity="warning")
        elif event.button.id == "msg-dialog-close":
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
        self.border_title = "Agents (Registry & Telemetry)"
        self.add_columns(
            "Agent", "Provider", "Model", "Triage", "Role", "Reasoning", "Skills", "Routing",
            "Inactive", "Protected", "Browser", "Comms", "Req. By",
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
            agent_model = (agent.get("model") or "").strip()
            # Empty agents.model falls back at runtime to VERSA_DEFAULT_MODEL —
            # use that for Provider detection. Never treat blank as "Google".
            effective_model = agent_model or (
                self.system_reader.get_default_model() if self.system_reader else ""
            )

            # Provider detection from model name / catalog lists
            local_models = self.system_reader.get_local_models()
            cloud_models = self.system_reader.get_cloud_models()
            third_party_models = self.system_reader.get_third_party_models()
            if agent_status_raw == "invalid_config":
                provider_display = "[yellow]⚠ Unknown[/]"
            elif not effective_model:
                provider_display = "[dim]—[/]"
            elif effective_model in local_models:
                provider_display = "[magenta]🖥 Local[/]"
            elif effective_model.startswith("gemini"):
                provider_display = "☁ [#4285F4]G[/][#EA4335]o[/][#FBBC05]o[/][#4285F4]g[/][#34A853]l[/][#EA4335]e[/]"
            elif effective_model.startswith("grok"):
                provider_display = "[purple]☁ xAI[/]"
            elif effective_model.startswith("gpt"):
                provider_display = "[white]☁ OpenAI[/]"
            elif effective_model.startswith("claude"):
                provider_display = "[yellow]☁ Anthropic[/]"
            elif "/" in effective_model or effective_model in third_party_models:
                provider_display = "[cyan]☁ Open Router[/]" if "/" in effective_model else "[cyan]☁ Cloud[/]"
            elif effective_model in cloud_models:
                provider_display = "☁ [#4285F4]G[/][#EA4335]o[/][#FBBC05]o[/][#4285F4]g[/][#34A853]l[/][#EA4335]e[/]"
            else:
                provider_display = "[dim]? Unknown[/]"

            if agent_status_raw == "removal_requested":
                inactive = "[red]⊘ removal pending[/]"
                name_markup = f"[red dim]{name}[/]"
            elif agent_status_raw == "circuit_breaker":
                inactive = "[bold red]⚡ breaker tripped[/]"
                name_markup = f"[bold red]{name}[/]"
            elif agent_status_raw == "halted":
                inactive = "[bold red]✋ halted[/]"
                name_markup = f"[bold red]{name}[/]"
            elif agent_status_raw == "invalid_config":
                inactive = "[yellow]⚠ config error[/]"
                name_markup = f"[bold yellow]{name}[/]"
            elif is_inactive:
                inactive = "[yellow]○ pending[/]"
                name_markup = f"[dim]{name}[/]"
            else:
                inactive = "[green]● active[/]"
                name_markup = f"[bold white]{name}[/]"
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
            elif agent_status == "active":
                status_msg = agent.get("status_message") or ""
                status_display = "[cyan]running[/]"
                if status_msg:
                    status_display += f" [dim]— {status_msg}[/]"
            elif agent_status:
                status_display = f"[bold]{agent_status}[/]"
            else:
                status_display = "[dim]--[/]"

            # COA soft warning for non-native model (local or proxy)
            coa_warning = ""
            if name == "coa" and (agent_model in local_models or agent_model in third_party_models):
                coa_warning = " [yellow]⚠[/]"

            # Model display — short name only (provider shown in dedicated column)
            if name == "watchdog":
                # Not a runnable agent — no model assignment UI (for now).
                provider_display = "[dim]—[/]"
                model_display = "[dim]n/a[/]"
            else:
                model_display = f"[dim]{agent_model}[/]" if agent_model else "[dim]default[/]"
            triage_model = (agent.get("triage_model") or "").strip()
            triage_display = f"[dim]{triage_model}[/]" if triage_model else "[dim]—[/]"

            browser_val = agent.get("browser_enabled", 0)
            browser_display = "[green]●[/]" if browser_val else "[dim]○[/]"

            reasoning = (agent.get("reasoning_effort") or "").strip()
            if reasoning:
                reasoning_display = f"[cyan]{reasoning}[/]"
            else:
                reasoning_display = "[dim]default[/]"

            skill_mode = (agent.get("skill_injection_mode") or "hybrid").strip().lower()
            skill_labels = {"full": "full", "lazy": "lazy", "hybrid": "hybrid"}
            skill_display = f"[magenta]{skill_labels.get(skill_mode, skill_mode)}[/]"

            routing_on = agent.get("model_routing_enabled", 0)
            routing_display = "[green]● on[/]" if routing_on else "[dim]○ off[/]"

            self.add_row(
                name_markup,
                provider_display,
                model_display,
                triage_display,
                role + coa_warning,
                reasoning_display,
                skill_display,
                routing_display,
                inactive,
                protected,
                browser_display,
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
        if not agent_name:
            return
        # Watchdog is infrastructure, not a runnable agent — no settings/edit.
        if agent_name == "watchdog":
            self.app.notify(
                "Watchdog is a system service (not a runnable agent). Settings are not available.",
                title="Watchdog",
                severity="information",
            )
            return
        self.app.push_screen(AgentPromptMenu(agent_name))


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
                yield Button("Cancel", classes="dismiss-btn", variant="default", id="msg-dialog-close")

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


def _load_catalog_labels(ini) -> dict[str, tuple[str, str, str]]:
    """Merge [catalog] + [catalog_custom] → {key: (class, label, provider)} (custom wins).

    Uses model_catalog.parse_catalog_row for 7- and 11-field catalog rows.
    """
    import os
    import sys

    core_infra = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    if core_infra not in sys.path:
        sys.path.insert(0, core_infra)
    from model_catalog import parse_catalog_row

    out: dict[str, tuple[str, str, str]] = {}
    for section in ("catalog", "catalog_custom"):
        if not ini.has_section(section):
            continue
        for key, raw in ini.items(section):
            row = parse_catalog_row(raw)
            if not row:
                continue
            k = key.strip()
            out[k] = (
                row["class"],
                (row.get("label") or "").strip() or k,
                (row.get("provider") or "").strip(),
            )
    return out


def _model_option_label(label: str, key: str, provider_label: str = "") -> str:
    """Consistent picker display: {Provider}: {model label} ({catalog_key})."""
    import os
    import sys

    core_infra = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    if core_infra not in sys.path:
        sys.path.insert(0, core_infra)
    from model_catalog import format_catalog_picker_label

    return format_catalog_picker_label(provider_label, label, key)


def _load_models_ini(system_reader: Optional[SystemReader] = None) -> list[tuple[str, str]]:
    """Load available models, filtered by execution mode and enabled backends.
    
    Returns list of (label, key) tuples. When system_reader is provided,
    filters models based on which backends are enabled (cloud/local/proxy).

    Display labels come from the unified [catalog]/[catalog_custom] sections
    (Edition 2.x source of truth). Local labels also overlay the pipeline-owned
    [local_models] section so registry-added models keep their names. Backend
    membership/filtering still comes from paths.env via system_reader.
    """
    import configparser, os
    ini = configparser.ConfigParser(delimiters=('=',), strict=False)
    # Canonical: /etc/versa-agi/models.ini, dev fallback: src/models.ini
    for path in ["/etc/versa-agi/models.ini",
                 os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
                     os.path.abspath(__file__)))), "..", "models.ini")]:
        if os.path.exists(path):
            ini.read(path)
            break

    catalog = _load_catalog_labels(ini)
    import os
    import sys

    core_infra = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    if core_infra not in sys.path:
        sys.path.insert(0, core_infra)
    from model_catalog import load_providers, provider_display_label

    providers = load_providers()
    provider_by_key = {key: slug for key, (_cls, _lbl, slug) in catalog.items()}

    def _prov(key: str, fallback: str = "") -> str:
        return provider_display_label(provider_by_key.get(key) or fallback, providers)

    # Cloud + third-party labels from the catalog (by class)
    cloud_entries = [
        (lbl, key)
        for key, (cls, lbl, _slug) in catalog.items()
        if cls in ("cloud", "third_party")
    ]
    proxy_entries = []

    # Local labels: catalog local rows, overlaid with the pipeline-owned
    # [local_models] section (registry-added models that aren't in the catalog).
    local_label_map = {key: lbl for key, (cls, lbl, _slug) in catalog.items() if cls == "local"}
    if ini.has_section("local_models"):
        for key, label in ini.items("local_models"):
            local_label_map.setdefault(key.strip(), label.strip())

    if not cloud_entries and not proxy_entries and not local_label_map:
        # No catalog — show nothing (allow_blank Select stays on System default)
        return []

    # Without system_reader, return all entries unfiltered
    if not system_reader:
        unfiltered: list[tuple[str, str]] = []
        for label, key in cloud_entries:
            unfiltered.append((f"☁ {_model_option_label(label, key, _prov(key))}", key))
        for label, key in proxy_entries:
            unfiltered.append((f"☁ {_model_option_label(label, key, _prov(key))}", key))
        for key, label in local_label_map.items():
            unfiltered.append((f"🖥 {_model_option_label(label, key, _prov(key, 'llamacpp'))}", key))
        return unfiltered

    # Backend-aware filtering: only show models for enabled backends
    # VERSA_CLOUD_MODELS / VERSA_THIRD_PARTY_MODELS are membership of *configured*
    # providers (empty when Gemini/TP skipped) — never invent a hard-coded model.
    cloud_set = set(system_reader.get_cloud_models())
    local_set = set(system_reader.get_local_models())
    proxy_set = set(system_reader.get_third_party_models())
    local_enabled = system_reader.is_local_ai_enabled()
    proxy_enabled = system_reader.is_third_party_enabled()

    filtered = []

    # Cloud models: only when VERSA_CLOUD_MODELS is non-empty (Gemini credentials)
    if cloud_set:
        for label, key in cloud_entries:
            if key in cloud_set:
                filtered.append((f"☁ {_model_option_label(label, key, _prov(key))}", key))

    # Third-party models: only if third_party enabled
    if proxy_enabled and proxy_set:
        for label, key in proxy_entries:
            if key in proxy_set:
                filtered.append((f"☁ {_model_option_label(label, key, _prov(key))}", key))

    # Local models: only if local_ai enabled — use labels from models.ini
    # VERSA_LOCAL_MODELS now contains ALL downloaded models on the server
    # (synced by 'agictl model refresh' via SSH filesystem scan).
    if local_enabled and local_set:
        active_model = system_reader.get_active_local_model()
        gpu_backend = system_reader.get_gpu_backend()
        strategy = system_reader.get_loading_strategy()
        for m in local_set:
            label = local_label_map.get(m, m)
            display = _model_option_label(label, m, _prov(m, "llamacpp"))
            # Star indicator: single mode marks the VRAM-resident model;
            # router mode — all models available, no star needed.
            if strategy == "single" and gpu_backend in ("intel", "remote"):
                star = " ★" if m == active_model else ""
            else:
                star = ""
            filtered.append((f"🖥 {display}{star}", m))

    return filtered


def _sanitize_agent_params_for_model(reader, agent_name: str, model_name: str) -> list[str]:
    """Reset agent param overrides that are invalid for the assigned model (NULL = inherit)."""
    if not reader or not model_name:
        return []
    agents = reader.get_all_agents()
    agent = next((a for a in agents if a.get("name") == agent_name), {})
    if not agent:
        return []
    try:
        from harness.model_params import sanitize_agent_param_fields
        updates = sanitize_agent_param_fields(
            model_name,
            reasoning_effort=agent.get("reasoning_effort"),
            reasoning_max_tokens=agent.get("reasoning_max_tokens"),
        )
    except Exception:
        return []
    cleared: list[str] = []
    for field, val in updates.items():
        if reader.update_agent_field(agent_name, field, val):
            cleared.append(field)
    return cleared


def _triage_model_kwargs(agents_panel, agent_name) -> tuple[list, dict]:
    """Options/kwargs for the triage model Select on the General tab."""
    agents = agents_panel.agent_reader.get_all_agents() if agents_panel.agent_reader else []
    agent = next((a for a in agents if a.get("name") == agent_name), {})
    current_triage_model = agent.get("triage_model") or ""
    all_model_options = _load_models_ini(agents_panel.system_reader)
    triage_model_options = [
        (label, key) for label, key in all_model_options
        if not label.startswith("🖥")
    ]
    triage_kwargs = {"id": "select-triage-model", "allow_blank": True, "prompt": "Use processing model"}
    if current_triage_model and any(k == current_triage_model for _, k in triage_model_options):
        triage_kwargs["value"] = current_triage_model
    return triage_model_options, triage_kwargs


def _agent_model_gen_context(agents_panel, agent_name) -> dict:
    """Cloud/local context for num_ctx and related technical setup fields."""
    agents = agents_panel.agent_reader.get_all_agents() if agents_panel.agent_reader else []
    agent = next((a for a in agents if a.get("name") == agent_name), {})
    current_model = agent.get("model") or ""
    try:
        from harness.model_context import is_cloud_model, get_server_ctx_ceiling
        is_cloud = is_cloud_model(current_model)
        server_ceiling = get_server_ctx_ceiling()
    except ImportError:
        is_cloud = False
        server_ceiling = None
    return {
        "agent": agent,
        "current_model": current_model,
        "is_cloud": is_cloud,
        "server_ceiling": server_ceiling,
    }


def compose_model_generation_fields(agents_panel, agent_name) -> ComposeResult:
    """Per-agent generation parameter overrides (Overrides tab)."""
    ctx = _agent_model_gen_context(agents_panel, agent_name)
    agent = ctx["agent"]
    current_model = ctx["current_model"]
    is_cloud = ctx["is_cloud"]

    try:
        from harness.model_params import (
            reasoning_effort_select_options,
            effective_agent_reasoning_effort,
            get_model_catalog_hints,
            supports_reasoning_config,
            resolve_local_runtime,
        )
        gpu_backend = (
            agents_panel.system_reader.get_gpu_backend()
            if agents_panel.system_reader else None
        )
        local_runtime = resolve_local_runtime(gpu_backend)
        reasoning_opts = reasoning_effort_select_options(current_model, local_runtime)
        current_reasoning = effective_agent_reasoning_effort(
            current_model, agent.get("reasoning_effort"), local_runtime,
        )
        catalog_hints = get_model_catalog_hints(current_model)
        show_reasoning_max = supports_reasoning_config(current_model, local_runtime)
        think_ollama_only = (
            not is_cloud
            and local_runtime == "llamacpp"
            and supports_reasoning_config(current_model, "ollama")
        )
    except ImportError:
        reasoning_opts = [
            ("Inherit", ""),
            ("none", "none"),
            ("minimal", "minimal"),
            ("low", "low"),
            ("medium", "medium"),
            ("high", "high"),
            ("max", "max"),
        ]
        current_reasoning = agent.get("reasoning_effort") or ""
        catalog_hints = None
        show_reasoning_max = True
        think_ollama_only = False

    temp_val = agent.get("temperature")
    rmt_val = agent.get("reasoning_max_tokens")

    yield Static(
        "[dim]Blank fields inherit from Model Manager / models.ini for the assigned processing model.[/]"
    )
    yield Static("")
    if catalog_hints:
        yield Static(
            f"[dim]Assigned model ({current_model or 'none'}): "
            f"input={catalog_hints['input_modalities']} · "
            f"output={catalog_hints['output_modalities']} · "
            f"drivers={catalog_hints['driver_summary']} · "
            f"tier={catalog_hints['work_modality']}[/]"
        )
    if think_ollama_only:
        yield Static(
            "[dim]Thinking toggle: Ollama provider only — "
            "not configurable on llamacpp/SYCL (model template may still reason).[/]"
        )
    yield Static("")

    with Horizontal(classes="setup-form-row"):
        with Vertical(classes="setup-form-col"):
            yield Static("[cyan]Temperature[/] — blank = inherit")
            yield Input(
                value="" if temp_val is None else str(temp_val),
                placeholder="Inherit",
                id="input-temperature",
            )
        with Vertical(classes="setup-form-col"):
            yield Static("[cyan]Reasoning Effort[/] — blank = inherit")
            yield Select(
                reasoning_opts,
                value=current_reasoning,
                id="select-reasoning-effort",
                allow_blank=True,
                prompt="Inherit",
            )

    with Horizontal(classes="setup-form-row"):
        if show_reasoning_max:
            with Vertical(classes="setup-form-col"):
                yield Static("[cyan]Reasoning Max Tokens[/] — blank = inherit")
                yield Input(
                    value="" if rmt_val is None else str(rmt_val),
                    placeholder="Inherit",
                    id="input-reasoning-max-tokens",
                    type="integer",
                )
            with Vertical(classes="setup-form-col"):
                yield Static("[cyan]Extra Params (JSON)[/] — passthrough; blank = inherit")
                yield Static("", id="model-params-extra-inherit-hint")
                yield TextArea(
                    _agent_extra_display(agent.get("model_params_extra")),
                    id="input-model-params-extra",
                    show_line_numbers=False,
                )
        else:
            with Vertical(classes="setup-form-col"):
                yield Static("[cyan]Extra Params (JSON)[/] — passthrough; blank = inherit")
                yield Static("", id="model-params-extra-inherit-hint")
                yield TextArea(
                    _agent_extra_display(agent.get("model_params_extra")),
                    id="input-model-params-extra",
                    show_line_numbers=False,
                )


def save_model_generation_fields(root, reader, agent_name: str, app) -> bool:
    """Persist per-agent generation overrides from the Overrides tab."""
    from textual.widgets import Select

    def _nullable_float(wid):
        raw = root.query_one(wid, Input).value.strip()
        if not raw:
            return None
        return float(raw)

    def _nullable_int(wid):
        raw = root.query_one(wid, Input).value.strip()
        if not raw:
            return None
        return int(raw)

    temp_override = _nullable_float("#input-temperature")
    reasoning_select = root.query_one("#select-reasoning-effort", Select)
    reasoning_override = (
        reasoning_select.value
        if isinstance(reasoning_select.value, str) and reasoning_select.value
        else None
    )
    try:
        from harness.model_params import allowed_reasoning_efforts
        agent_model = next(
            (a.get("model") for a in (reader.get_all_agents() or [])
             if a.get("name") == agent_name),
            "",
        ) or ""
        if reasoning_override and reasoning_override not in allowed_reasoning_efforts(agent_model):
            reasoning_override = None
    except Exception:
        pass
    try:
        reasoning_max_override = _nullable_int("#input-reasoning-max-tokens")
    except ValueError:
        app.notify("Reasoning max tokens must be a whole number", title="Error", severity="error")
        return False
    extra_raw = root.query_one("#input-model-params-extra", TextArea).text.strip()
    extra_override = None
    if extra_raw:
        parsed, err = parse_json_object(extra_raw, field_label="Extra params")
        if err:
            app.notify(err, title="Error", severity="error")
            return False
        extra_override = json.dumps(parsed, separators=(",", ":"))

    return all([
        reader.update_agent_field(agent_name, "temperature", temp_override),
        reader.update_agent_field(agent_name, "reasoning_effort", reasoning_override),
        reader.update_agent_field(agent_name, "reasoning_max_tokens", reasoning_max_override),
        reader.update_agent_field(agent_name, "model_params_extra", extra_override),
    ])


def compose_technical_setup_fields(agents_panel, agent_name) -> ComposeResult:
    """Yield harness configuration form fields (modal + agent tab)."""
    agents = agents_panel.agent_reader.get_all_agents() if agents_panel.agent_reader else []
    agent = next((a for a in agents if a.get("name") == agent_name), {})

    current_turns = str(agent.get("max_session_turns", 400))
    current_tool_budget = str(agent.get("tool_output_token_budget", 5000))
    current_budget = str(agent.get("token_budget", 0))
    current_timeout = str(agent.get("timeout_minutes", 45))
    current_threshold = str(agent.get("runaway_threshold", 2500))
    current_size_threshold = str(agent.get("runaway_size_threshold", 512))
    current_num_ctx = agent.get("num_ctx", 0)
    current_model = agent.get("model") or ""
    gen_ctx = _agent_model_gen_context(agents_panel, agent_name)
    is_cloud = gen_ctx["is_cloud"]
    server_ceiling = gen_ctx["server_ceiling"]

    try:
        from harness.model_context import get_num_ctx_options
        ctx_options = get_num_ctx_options(current_model, server_ctx_ceiling=server_ceiling)
    except ImportError:
        ctx_options = [("32K", 32768)]

    with Horizontal(classes="setup-form-row"):
        with Vertical(classes="setup-form-col"):
            yield Static("[cyan]Max Graph Steps (Recursion Limit)[/] — max LangGraph tool iterations")
            yield Input(value=current_turns, placeholder="e.g. 50", id="input-max-turns", type="integer")
        with Vertical(classes="setup-form-col"):
            yield Static("[cyan]Tool Output Limit (Characters)[/] — truncate run_shell_command output")
            yield Input(value=current_tool_budget, placeholder="e.g. 6000", id="input-tool-budget", type="integer")

    if not is_cloud and ctx_options:
        ctx_label = "[cyan]Context Window (num_ctx)[/]"
        if server_ceiling:
            ctx_label += f" — capped to server ctx-size: {server_ceiling:,}"
        else:
            ctx_label += " — Ollama context window size in tokens"
        with Horizontal(classes="setup-form-row"):
            with Vertical(classes="setup-form-col"):
                yield Static(ctx_label)
                num_ctx_select_options = [("Auto (model default)", 0)] + [(label, value) for label, value in ctx_options]
                yield Select(
                    num_ctx_select_options,
                    value=current_num_ctx if current_num_ctx in [v for _, v in num_ctx_select_options] else 0,
                    id="select-num-ctx",
                    allow_blank=False,
                )
            with Vertical(classes="setup-form-col"):
                yield Static("[cyan]Token Budget (monthly)[/] — 0 = unlimited")
                yield Input(value=current_budget, placeholder="e.g. 5000000 (0=unlimited)", id="input-budget", type="integer")
    else:
        with Horizontal(classes="setup-form-row"):
            with Vertical(classes="setup-form-col"):
                yield Static("[cyan]Token Budget (monthly)[/] — max tokens per month (0 = unlimited)")
                yield Input(value=current_budget, placeholder="e.g. 5000000 (0=unlimited)", id="input-budget", type="integer")
            with Vertical(classes="setup-form-col"):
                yield Static("[cyan]Timeout (minutes)[/] — max runtime before agent is killed")
                yield Input(value=current_timeout, placeholder="e.g. 30", id="input-timeout", type="integer")

    if not is_cloud and ctx_options:
        with Horizontal(classes="setup-form-row"):
            with Vertical(classes="setup-form-col"):
                yield Static("[cyan]Timeout (minutes)[/] — max runtime before agent is killed")
                yield Input(value=current_timeout, placeholder="e.g. 30", id="input-timeout", type="integer")
            with Vertical(classes="setup-form-col"):
                yield Static("[cyan]Runaway Threshold (lines)[/] — max output lines before freeze")
                yield Input(value=current_threshold, placeholder="e.g. 300", id="input-threshold", type="integer")
        with Horizontal(classes="setup-form-row"):
            with Vertical(classes="setup-form-col"):
                yield Static("[cyan]Runaway Size Threshold (KB)[/] — max result/session file size before freeze")
                yield Input(value=current_size_threshold, placeholder="e.g. 512", id="input-size-threshold", type="integer")
    else:
        with Horizontal(classes="setup-form-row"):
            with Vertical(classes="setup-form-col"):
                yield Static("[cyan]Runaway Threshold (lines)[/] — max output lines before freeze")
                yield Input(value=current_threshold, placeholder="e.g. 300", id="input-threshold", type="integer")
            with Vertical(classes="setup-form-col"):
                yield Static("[cyan]Runaway Size Threshold (KB)[/] — max result/session file size before freeze")
                yield Input(value=current_size_threshold, placeholder="e.g. 512", id="input-size-threshold", type="integer")

    yield Static("")
    yield Static("[bold cyan]─── Rolling Chat History (LangGraph Resume) ───[/]")
    yield Static("")
    yield Static(
        "[cyan]Resume Enabled[/] — carry previous cycles' conversation into new cycles (per project thread).\n"
        "[dim]OFF (default): each cycle starts fresh — durable state lives in tasks, memory, and awareness,\n"
        "not chat history. ON: only for long single-thread collaborations needing verbatim continuity.[/]"
    )
    yield Select(
        [("No (fresh start each cycle)", 0), ("Yes (roll chat history across cycles — default)", 1)],
        value=agent.get("resume_enabled", 1),
        id="select-resume-enabled",
        allow_blank=False,
    )
    yield Static("[cyan]Resume Max Messages[/] — on resume, keep only the last N messages of rolled history (0 = unlimited; ignored when Resume is off)")
    yield Input(value=str(agent.get("resume_max_messages", 25)), placeholder="e.g. 25 (0=unlimited)", id="input-resume-max-msgs", type="integer")
    yield Static("[dim]Thread-level resets: use 🧵 Manage Threads on the Agent Prompt Menu modal.[/]")


def apply_model_generation_hints(app, agent_name) -> None:
    """Show inherited generation-param hints on the Overrides tab."""
    from textual.widgets import Select
    try:
        from harness.model_params import resolve_model_params, SYSTEM_DEFAULTS
        agents_panel = app.query_one(AgentsPanel)
        agents = agents_panel.agent_reader.get_all_agents() if agents_panel.agent_reader else []
        agent = next((a for a in agents if a.get("name") == agent_name), {})
        model = agent.get("model") or ""
        if model and agents_panel.agent_reader:
            cleared = _sanitize_agent_params_for_model(
                agents_panel.agent_reader, agent_name, model,
            )
            if cleared:
                agent = next(
                    (a for a in agents_panel.agent_reader.get_all_agents()
                     if a.get("name") == agent_name),
                    agent,
                )
                if "reasoning_effort" in cleared:
                    app.query_one("#select-reasoning-effort", Select).value = ""
                if "reasoning_max_tokens" in cleared:
                    try:
                        app.query_one("#input-reasoning-max-tokens", Input).placeholder = "Inherit"
                    except Exception:
                        pass
        resolved = resolve_model_params(model) if model else dict(SYSTEM_DEFAULTS)

        if agent.get("temperature") is None and resolved.get("temperature") is not None:
            app.query_one("#input-temperature", Input).placeholder = (
                f"Inherit ({resolved['temperature']})"
            )
        if not agent.get("reasoning_effort") and resolved.get("reasoning_effort"):
            app.query_one("#select-reasoning-effort", Select).prompt = (
                f"Inherit ({resolved['reasoning_effort']})"
            )
        if agent.get("reasoning_max_tokens") is None and resolved.get("reasoning_max_tokens") is not None:
            app.query_one("#input-reasoning-max-tokens", Input).placeholder = (
                f"Inherit ({resolved['reasoning_max_tokens']})"
            )
        if not agent.get("model_params_extra") and resolved.get("extra"):
            extra = resolved["extra"]
            if extra:
                try:
                    hint = app.query_one("#model-params-extra-inherit-hint", Static)
                    hint.update(
                        "[dim]Inherited from model layer:[/]\n"
                        + format_json_pretty(extra)
                    )
                except Exception:
                    pass
    except Exception:
        pass


def apply_technical_setup_hints(app, agent_name) -> None:
    """Sanitize stored model params when the assigned model changes."""
    try:
        agents_panel = app.query_one(AgentsPanel)
        agents = agents_panel.agent_reader.get_all_agents() if agents_panel.agent_reader else []
        agent = next((a for a in agents if a.get("name") == agent_name), {})
        model = agent.get("model") or ""
        if model and agents_panel.agent_reader:
            _sanitize_agent_params_for_model(
                agents_panel.agent_reader, agent_name, model,
            )
        apply_model_generation_hints(app, agent_name)
    except Exception:
        pass


class TechnicalSetupModal(ModalScreen):
    """Modal to view/edit agent harness configuration (max steps, tool budget, triage model)."""

    def __init__(self, agent_name: str, host=None, **kwargs):
        super().__init__(**kwargs)
        self.agent_name = agent_name
        self._host = host
        self._embedded = host is not None

    @property
    def _form_root(self):
        return self._host if self._embedded else self

    @property
    def _form_app(self):
        return self._host.app if self._embedded else self.app

    def compose(self) -> ComposeResult:
        from textual.containers import Horizontal, VerticalScroll
        agents_panel = self.app.query_one(AgentsPanel)
        with VerticalScroll(id="msg-dialog"):
            yield Static(f"[bold]⚙ Technical — {self.agent_name}[/]", id="msg-dialog-header")
            yield Static("")
            with VerticalScroll(id="msg-dialog-scroll"):
                yield from compose_technical_setup_fields(agents_panel, self.agent_name)
            with Horizontal(id="msg-dialog-actions"):
                yield Button("Save", variant="success", id="btn-save-setup")
                yield Button("Cancel", classes="dismiss-btn", variant="default", id="msg-dialog-close")

    def on_mount(self) -> None:
        apply_technical_setup_hints(self.app, self.agent_name)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        from textual.widgets import Select
        if event.button.id == "btn-save-setup":
            root = self._form_root
            app = self._form_app
            agents_panel = app.query_one(AgentsPanel)
            reader = agents_panel.agent_reader
            if reader:
                try:
                    turns = int(root.query_one("#input-max-turns", Input).value)
                    tool_budget = int(root.query_one("#input-tool-budget", Input).value)
                    budget_val = int(root.query_one("#input-budget", Input).value)
                    timeout_val = int(root.query_one("#input-timeout", Input).value)
                    threshold_val = int(root.query_one("#input-threshold", Input).value)
                    size_threshold_val = int(root.query_one("#input-size-threshold", Input).value)

                    ok = all([
                        reader.update_agent_field(self.agent_name, "max_session_turns", turns),
                        reader.update_agent_field(self.agent_name, "tool_output_token_budget", tool_budget),
                        reader.update_agent_field(self.agent_name, "token_budget", budget_val),
                        reader.update_agent_field(self.agent_name, "timeout_minutes", timeout_val),
                        reader.update_agent_field(self.agent_name, "runaway_threshold", threshold_val),
                        reader.update_agent_field(self.agent_name, "runaway_size_threshold", size_threshold_val),
                    ])
                    # Update num_ctx if the Select exists (non-cloud models)
                    try:
                        num_ctx_select = root.query_one("#select-num-ctx", Select)
                        num_ctx_val = num_ctx_select.value
                        if isinstance(num_ctx_val, int):
                            ok = ok and reader.update_agent_field(self.agent_name, "num_ctx", num_ctx_val)
                    except Exception:
                        pass  # Cloud models don't have the select
                    # Save resume controls
                    resume_select = root.query_one("#select-resume-enabled", Select)
                    # Fallback must match the system default (0 = fresh start)
                    resume_val = resume_select.value if isinstance(resume_select.value, int) else 0
                    ok = ok and reader.update_agent_field(self.agent_name, "resume_enabled", resume_val)
                    resume_max = int(root.query_one("#input-resume-max-msgs", Input).value)
                    ok = ok and reader.update_agent_field(self.agent_name, "resume_max_messages", resume_max)
                    if ok:
                        app.notify(f"Settings saved for {self.agent_name}", title="Technical")
                    else:
                        app.notify("Save failed — check DB permissions", title="Error", severity="error")
                except ValueError:
                    app.notify("Invalid input — must be valid numbers", title="Error", severity="error")
                except Exception as e:
                    app.notify(f"Error: {e}", title="Error", severity="error")
            if not self._embedded:
                app.pop_screen()
        elif event.button.id == "msg-dialog-close":
            if not self._embedded:
                self._form_app.pop_screen()


class SyclActivationModal(ModalScreen):
    """Automated Intel SYCL model activation modal.

    Executes 'agictl model activate' on the inference server automatically:
      - Local topology:  runs sudo agictl model activate locally
      - Client topology: SSH to server as watchdog, runs agictl model activate,
                         then runs local 'sudo agictl model refresh' to sync state.

    On success, updates ALL agents using local models (SYCL = single active model).
    Disables CRON on mount to prevent agents spawning with a mismatched model.
    Re-enables CRON on completion (success or failure) or cancel.
    """

    def __init__(self, model_name: str, topology: str, agent_name: str, pending_num_ctx: int, **kwargs):
        super().__init__(**kwargs)
        self.model_name = model_name
        self.topology = topology
        self.agent_name = agent_name
        self.pending_num_ctx = pending_num_ctx
        self._cron_was_enabled = False
        self._activating = False
        self._preflight_passed = False  # Set True after successful pre-flight
        self._last_status_text = ""  # Plain-text copy of status for clipboard

    def compose(self) -> ComposeResult:
        from textual.containers import Horizontal
        location = "remote server" if self.topology == "client" else "this machine"
        with Vertical(id="sycl-dialog"):
            yield Static("[bold yellow]⚠ SYCL Model Activation[/]\n", id="sycl-title")
            yield Static(
                f"Switch the active inference model to [bold cyan]{self.model_name}[/].\n\n"
                f"Target: [bold]{location}[/]\n"
                f"[bold]All agents using local models will be updated.[/]\n\n"
                f"CRON has been [bold red]paused[/] during activation.\n",
                id="sycl-info",
            )
            yield Static("[dim]Checking model availability...[/]", id="sycl-status")
            with Horizontal(id="sycl-actions"):
                yield Button("Activate Model", variant="success", id="btn-sycl-confirm", disabled=True)
                yield Button("Copy", variant="default", id="btn-sycl-copy")
                yield Button("Cancel", classes="dismiss-btn", variant="default", id="btn-sycl-cancel")

    def on_mount(self) -> None:
        """Disable CRON when the modal opens, then run pre-flight check."""
        try:
            agents_panel = self.app.query_one(AgentsPanel)
            if agents_panel.system_reader:
                self._cron_was_enabled = agents_panel.system_reader.is_cron_enabled()
                if self._cron_was_enabled:
                    agents_panel.system_reader.toggle_cron()
                    self.app.notify("CRON paused during model activation", title="Lifeline")
        except Exception:
            pass
        # Launch pre-flight check in background thread
        self._run_preflight()

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

    def _set_status(self, text: str) -> None:
        """Update the status label in the modal."""
        import re
        self._last_status_text = re.sub(r'\[/?[^\]]*\]', '', text)  # Strip Rich markup
        try:
            self.query_one("#sycl-status", Static).update(text)
        except Exception:
            pass

    def _set_buttons_disabled(self, disabled: bool) -> None:
        """Enable/disable action buttons during activation."""
        try:
            self.query_one("#btn-sycl-confirm", Button).disabled = disabled
            self.query_one("#btn-sycl-cancel", Button).disabled = disabled
        except Exception:
            pass

    def _resolve_gguf_filename(self) -> str:
        """Look up the GGUF filename for self.model_name from models.ini."""
        import configparser, os
        ini = configparser.ConfigParser(delimiters=('=',), strict=False)
        for path in ["/etc/versa-agi/models.ini",
                     os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
                         os.path.abspath(__file__)))), "..", "models.ini")]:
            if os.path.exists(path):
                ini.read(path)
                break
        if not ini.has_section("sycl_models"):
            return ""
        raw = ini.get("sycl_models", self.model_name, fallback="")
        if not raw:
            return ""
        parts = raw.strip().split(",")
        return parts[1].strip() if len(parts) >= 2 else ""

    def _run_preflight(self) -> None:
        """Check if the model is downloaded on the target via agictl model list."""
        import threading

        def _check():
            import subprocess, json as _json

            if self.topology == "client":
                # Query server's model inventory via SSH
                try:
                    agents_panel = self.app.query_one(AgentsPanel)
                    sr = agents_panel.system_reader
                    tunnel_host = sr.get_tunnel_host() if sr else ""
                    ssh_key = sr.get_watchdog_ssh_key() if sr else ""
                    wd_user = sr.watchdog_user if sr else "watchdog"

                    if not tunnel_host:
                        self.app.call_from_thread(
                            self._on_preflight_failed,
                            "No tunnel_host configured.\n\n"
                            "Run: [bold]sudo ./setup_local.sh[/] (option 2) to configure client mode.",
                        )
                        return

                    result = subprocess.run(
                        ["sudo", "-u", wd_user,
                         "ssh", "-i", ssh_key,
                         "-o", "StrictHostKeyChecking=accept-new",
                         "-o", "ConnectTimeout=10",
                         "-o", "BatchMode=yes",
                         f"{wd_user}@{tunnel_host}",
                         "agictl model list"],
                        capture_output=True, text=True, timeout=20,
                    )
                    if result.returncode != 0:
                        self.app.call_from_thread(
                            self._on_preflight_failed,
                            f"[bold red]Failed to query server models.[/]\n\n"
                            f"[dim]{result.stderr.strip()[:200]}[/]",
                        )
                        return

                    models = _json.loads(result.stdout)
                    target = next((m for m in models if m["name"] == self.model_name), None)

                    if target and target.get("downloaded"):
                        self.app.call_from_thread(self._on_preflight_ready)
                    else:
                        self.app.call_from_thread(
                            self._on_preflight_failed,
                            f"[bold red]Model not downloaded on server.[/]\n\n"
                            f"[bold]{self.model_name}[/] was not found on [dim]{tunnel_host}[/]\n\n"
                            f"[bold cyan]On the server, run:[/]\n"
                            f"  [bold]sudo agictl model add {self.model_name}[/]\n\n"
                            f"Then return here and retry.",
                        )
                except subprocess.TimeoutExpired:
                    self.app.call_from_thread(
                        self._on_preflight_failed,
                        "SSH connection to server timed out.\n\n"
                        "Check that the SSH tunnel is running and the server is reachable.",
                    )
                except Exception as e:
                    self.app.call_from_thread(
                        self._on_preflight_failed,
                        f"[bold red]Pre-flight SSH error[/]\n\n"
                        f"[dim]topology={self.topology}[/]\n"
                        f"{e}\n\n"
                        f"[dim]You may cancel and retry, or check SSH connectivity.[/]",
                    )
            else:
                # Local/server topology — query agictl model list directly
                try:
                    result = subprocess.run(
                        ["sudo", "agictl", "model", "list"],
                        capture_output=True, text=True, timeout=15,
                    )
                    if result.returncode == 0 and result.stdout.strip():
                        models = _json.loads(result.stdout)
                        target = next((m for m in models if m["name"] == self.model_name), None)
                        if target and target.get("downloaded"):
                            self.app.call_from_thread(self._on_preflight_ready)
                        else:
                            self.app.call_from_thread(
                                self._on_preflight_failed,
                                f"[bold red]Model not downloaded.[/]\n\n"
                                f"[bold]{self.model_name}[/] is not available locally.\n\n"
                                f"[bold cyan]Run:[/]\n"
                                f"  [bold]sudo agictl model add {self.model_name}[/]\n\n"
                                f"Then return here and retry.",
                            )
                    else:
                        # agictl failed — fall back to allowing the attempt
                        self.app.call_from_thread(self._on_preflight_ready)
                except Exception:
                    # Can't verify — allow the attempt
                    self.app.call_from_thread(self._on_preflight_ready)

        thread = threading.Thread(target=_check, daemon=True)
        thread.start()

    def _on_preflight_ready(self) -> None:
        """Pre-flight passed — enable the Activate button."""
        self._preflight_passed = True
        self._set_status("[bold green]\u2713 Model available[/] — ready to activate.")
        try:
            self.query_one("#btn-sycl-confirm", Button).disabled = False
        except Exception:
            pass

    def _on_preflight_failed(self, message: str) -> None:
        """Pre-flight failed — show instructions, keep Activate disabled."""
        self._preflight_passed = False
        self._set_status(message)
        try:
            self.query_one("#btn-sycl-confirm", Button).disabled = True
            self.query_one("#btn-sycl-cancel", Button).disabled = False
        except Exception:
            pass

    def _run_activation(self) -> None:
        """Run model activation in a background thread."""
        import subprocess
        import threading

        def _activate():
            try:
                agents_panel = self.app.query_one(AgentsPanel)
                system_reader = agents_panel.system_reader

                if self.topology == "client":
                    # ── Client topology: SSH to server, activate remotely ──
                    tunnel_host = system_reader.get_tunnel_host() if system_reader else ""
                    ssh_key = system_reader.get_watchdog_ssh_key() if system_reader else ""
                    wd_user = system_reader.watchdog_user if system_reader else "watchdog"

                    if not tunnel_host:
                        self.app.call_from_thread(self._on_activation_failed,
                            "No tunnel_host configured. Run setup_local.sh in client mode first.")
                        return

                    self.app.call_from_thread(self._set_status,
                        "[bold yellow]◐ Activating model on remote server...[/]")

                    # Step 1: SSH to server and run activation
                    ssh_cmd = [
                        "sudo", "-u", wd_user,
                        "ssh", "-i", ssh_key,
                        "-o", "StrictHostKeyChecking=accept-new",
                        "-o", "ConnectTimeout=15",
                        "-o", "BatchMode=yes",
                        f"{wd_user}@{tunnel_host}",
                        f"sudo agictl model activate {self.model_name}",
                    ]
                    result = subprocess.run(
                        ssh_cmd, capture_output=True, text=True, timeout=120,
                    )
                    if result.returncode != 0:
                        # Extract error from agictl JSON response (stdout), with stderr as fallback
                        error_msg = ""
                        try:
                            import json as _json
                            data = _json.loads(result.stdout)
                            error_msg = data.get("error", "")
                        except Exception:
                            pass
                        if not error_msg:
                            error_msg = (result.stdout or result.stderr or "Unknown error").strip()
                        self.app.call_from_thread(self._on_activation_failed,
                            f"Server activation failed:\n{error_msg[:500]}")
                        return

                    # Step 2: Local model refresh to sync paths.env
                    self.app.call_from_thread(self._set_status,
                        "[bold yellow]◑ Syncing local model state...[/]")
                    refresh_result = subprocess.run(
                        ["sudo", "agictl", "model", "refresh"],
                        capture_output=True, text=True, timeout=30,
                    )
                    if refresh_result.returncode != 0:
                        # Non-fatal — activation succeeded, just warn about sync
                        self.app.call_from_thread(
                            lambda: self.app.notify(
                                "Model activated on server but local refresh failed. Run: sudo agictl model refresh",
                                title="Partial Success", severity="warning",
                            )
                        )

                else:
                    # ── Local topology: run activation directly ──
                    self.app.call_from_thread(self._set_status,
                        "[bold yellow]◐ Activating model...[/]")

                    result = subprocess.run(
                        ["sudo", "agictl", "model", "activate", self.model_name],
                        capture_output=True, text=True, timeout=120,
                    )
                    if result.returncode != 0:
                        # Extract error from agictl JSON response (stdout), with stderr as fallback
                        error_msg = ""
                        try:
                            import json as _json
                            data = _json.loads(result.stdout)
                            error_msg = data.get("error", "")
                        except Exception:
                            pass
                        if not error_msg:
                            error_msg = (result.stdout or result.stderr or "Unknown error").strip()
                        self.app.call_from_thread(self._on_activation_failed,
                            f"Activation failed:\n{error_msg[:500]}")
                        return

                # ── Success: update all agent DB records ──
                self.app.call_from_thread(self._on_activation_success)

            except subprocess.TimeoutExpired:
                self.app.call_from_thread(self._on_activation_failed,
                    "Activation timed out after 120 seconds.")
            except Exception as e:
                self.app.call_from_thread(self._on_activation_failed, str(e))

        thread = threading.Thread(target=_activate, daemon=True)
        thread.start()

    def _on_activation_success(self) -> None:
        """Called on the main thread after successful activation."""
        updated_agents = set()
        try:
            agents_panel = self.app.query_one(AgentsPanel)
            if agents_panel.agent_reader:
                # Target agent — always update
                agents_panel.agent_reader.update_agent_field(
                    self.agent_name, "model", self.model_name
                )
                agents_panel.agent_reader.update_agent_field(
                    self.agent_name, "num_ctx", self.pending_num_ctx
                )
                _sanitize_agent_params_for_model(
                    agents_panel.agent_reader, self.agent_name, self.model_name,
                )
                updated_agents.add(self.agent_name)

                # Sweep other agents on local models — only in single mode.
                # Router mode: agents keep individual model assignments.
                strategy = "single"
                if agents_panel.system_reader:
                    strategy = agents_panel.system_reader.get_loading_strategy()
                if strategy == "single":
                    from harness.model_context import is_cloud_model
                    all_agents = agents_panel.agent_reader.get_all_agents()
                    for agent in all_agents:
                        name = agent.get("name", "")
                        if name in updated_agents:
                            continue
                        agent_model = agent.get("model") or ""
                        if agent_model and not is_cloud_model(agent_model):
                            agents_panel.agent_reader.update_agent_field(
                                name, "model", self.model_name
                            )
                            agents_panel.agent_reader.update_agent_field(
                                name, "num_ctx", self.pending_num_ctx
                            )
                            _sanitize_agent_params_for_model(
                                agents_panel.agent_reader, name, self.model_name,
                            )
                            updated_agents.add(name)
                agents_panel.refresh_data()
        except Exception as e:
            self.app.notify(f"DB update error: {e}", title="Error", severity="error")

        self._set_status(
            f"[bold green]✓ Model activated: {self.model_name}[/]\n"
            f"  Updated {len(updated_agents)} agent(s)"
        )
        self.app.notify(
            f"Model set to {self.model_name} for {len(updated_agents)} agent(s)",
            title="Agent Settings",
        )
        self._resume_cron()
        self._activating = False
        # Replace buttons with a single Close
        self._set_buttons_disabled(False)
        try:
            self.query_one("#btn-sycl-confirm", Button).remove()
            cancel_btn = self.query_one("#btn-sycl-cancel", Button)
            cancel_btn.label = "Close"
            cancel_btn.variant = "primary"
        except Exception:
            pass

    def _on_activation_failed(self, error_msg: str) -> None:
        """Called on the main thread after failed activation."""
        # Write error to log file for easy copy/paste (TUI doesn't support text selection)
        log_path = "/tmp/versa_agi_activation.log"
        try:
            import re
            clean_msg = re.sub(r'\[/?[^\]]*\]', '', error_msg)  # Strip Rich markup
            with open(log_path, "w") as f:
                f.write(f"SYCL Activation Error — {self.model_name}\n")
                f.write(f"Topology: {self.topology}\n")
                f.write(f"{'=' * 50}\n")
                f.write(clean_msg + "\n")
        except Exception:
            log_path = ""
        log_hint = f"\n\n[dim]Full error: cat {log_path}[/]" if log_path else ""
        self._set_status(
            f"[bold red]✗ Activation Failed[/]\n\n{error_msg}\n\n"
            f"[dim]You can retry or cancel to restore CRON.[/]{log_hint}"
        )
        self._activating = False
        self._set_buttons_disabled(False)
        try:
            self.query_one("#btn-sycl-confirm", Button).label = "Retry"
        except Exception:
            pass

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-sycl-confirm":
            if self._activating:
                return  # Ignore double-clicks
            self._activating = True
            self._set_buttons_disabled(True)
            self._run_activation()
        elif event.button.id == "btn-sycl-copy":
            if self._last_status_text:
                import subprocess as _sp
                copied = False
                for clip_cmd in [["xclip", "-selection", "clipboard"], ["xsel", "--clipboard", "--input"]]:
                    try:
                        _sp.run(clip_cmd, input=self._last_status_text, text=True, timeout=3)
                        copied = True
                        break
                    except Exception:
                        continue
                if copied:
                    self.app.notify("Copied to clipboard", title="Copy")
                else:
                    self.app.notify("Install xclip: sudo apt install xclip", title="Copy Failed", severity="warning")
        elif event.button.id == "btn-sycl-cancel":
            if self._activating:
                return  # Don't allow cancel during activation
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

    def __init__(self, agent_name: str, host=None, **kwargs):
        super().__init__(**kwargs)
        self.agent_name = agent_name
        self._host = host
        self._embedded = host is not None
        self._original_model = ""
        self._original_num_ctx = 0
        self._watchdog_locked = agent_name == "watchdog"

    @property
    def _form_root(self):
        return self._host if self._embedded else self

    @property
    def _form_app(self):
        return self._host.app if self._embedded else self.app

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
        current_browser = agent.get("browser_enabled", 0)
        current_ctx_mode = agent.get("context_injection_mode") or "relevant"
        current_status = agent.get("status") or ""
        current_anchor = agent.get("anchor_style") or "compact"

        if self._watchdog_locked:
            with VerticalScroll(id="msg-dialog"):
                yield Static("[bold]⚙  Watchdog[/]", id="msg-dialog-header")
                with VerticalScroll(id="msg-dialog-scroll"):
                    yield Static(
                        "[dim]Watchdog is a system service, not a runnable agent.\n"
                        "Model and harness settings are not editable here.[/]"
                    )
                with Horizontal(id="msg-dialog-actions"):
                    yield Button("Close", classes="dismiss-btn", variant="default", id="msg-dialog-close")
            return

        # Load model options — mode-aware filtering
        agents_panel = self.app.query_one(AgentsPanel)
        model_options = _load_models_ini(agents_panel.system_reader)

        # COA model restriction: only approved models for the orchestrator
        if is_protected and self.agent_name == "coa":
            coa_allowed = set(agents_panel.system_reader.get_coa_approved_models())
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
                # Browser Automation — available for all agents (including COA)
                yield Static("[cyan]Browser Automation[/] — allow agent to use headless browser")
                _ba_enabled = current_browser == 1
                _ba_label = "Disable" if _ba_enabled else "Enable"
                _ba_variant = "error" if _ba_enabled else "success"
                _ba_status = "[green]● Enabled[/]" if _ba_enabled else "[red]● Disabled[/]"
                yield Static(f"Status: {_ba_status}", id="agent-browser-status-label")
                yield Button(f"{_ba_label} Browser Automation", variant=_ba_variant, id="btn-agent-browser-toggle")
            with Horizontal(id="msg-dialog-actions"):
                yield Button("Save", variant="success", id="btn-save-settings")
                yield Button("Cancel", classes="dismiss-btn", variant="default", id="msg-dialog-close")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        from textual.widgets import Select
        if event.button.id == "msg-dialog-close":
            if self._embedded:
                return
            self.app.pop_screen()
            return
        if event.button.id == "btn-save-settings":
            if self._watchdog_locked:
                self._form_app.notify(
                    "Watchdog settings cannot be edited.",
                    title="Watchdog",
                    severity="warning",
                )
                return
            root = self._form_root
            app = self._form_app
            agents_panel = app.query_one(AgentsPanel)
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
                        model_select = root.query_one("#select-model", Select)
                        model_val = model_select.value
                        if not isinstance(model_val, str) or not model_val:
                            ok_model = reader.update_agent_field(self.agent_name, "model", None)
                        else:
                            # Check if this is a SYCL model change — defer DB write if so.
                            # On "remote" (client) topology, SYCL activation is only needed
                            # when the selected model is NOT already active on the server.
                            # The server's active model(s) are in VERSA_LOCAL_MODELS.
                            needs_sycl = False
                            if model_val != self._original_model:
                                try:
                                    from harness.model_context import is_cloud_model, get_model_context
                                    if not is_cloud_model(model_val):
                                        system_reader = agents_panel.system_reader
                                        if system_reader and system_reader.get_gpu_backend() in ("intel", "remote"):
                                            strategy = system_reader.get_loading_strategy()
                                            if strategy == "router":
                                                # Router: server loads on demand — no activation needed.
                                                # Direct save below handles the DB write.
                                                pass
                                            else:
                                                # Single: check against the VRAM-resident model
                                                active_model = system_reader.get_active_local_model()
                                                if model_val != active_model:
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
                                if model_val != self._original_model:
                                    cleared = _sanitize_agent_params_for_model(
                                        reader, self.agent_name, model_val,
                                    )
                                    if cleared:
                                        app.notify(
                                            f"Reset invalid param overrides: {', '.join(cleared)}",
                                            title="Model Params",
                                        )
                    except Exception:
                        pass
                    # Update context injection mode
                    ok_ctx = True
                    try:
                        ctx_select = root.query_one("#select-ctx-mode", Select)
                        ctx_val = ctx_select.value
                        if isinstance(ctx_val, str) and ctx_val:
                            ok_ctx = reader.update_agent_field(self.agent_name, "context_injection_mode", ctx_val)
                    except Exception:
                        pass
                    # Update conversation depth
                    ok_depth = True
                    try:
                        depth_val = int(root.query_one("#input-convo-depth", Input).value)
                        ok_depth = reader.update_agent_field(self.agent_name, "conversation_depth", depth_val)
                    except Exception:
                        pass
                    # Status picklist removed — statuses are system-managed
                    ok_status = True
                    # Update inactive flag if the Select exists (not protected)
                    ok_inactive = True
                    try:
                        inactive_select = root.query_one("#select-inactive", Select)
                        inactive_val = inactive_select.value
                        ok_inactive = reader.update_agent_field(self.agent_name, "inactive", inactive_val)
                    except Exception:
                        pass  # Protected agents don't have the select
                    # Update comms flag if the Select exists (not protected)
                    ok_comms = True
                    try:
                        comms_select = root.query_one("#select-comms", Select)
                        comms_val = comms_select.value
                        ok_comms = reader.update_agent_field(self.agent_name, "can_message_connections", comms_val)
                    except Exception:
                        pass  # Protected agents don't have the select
                    # Browser toggle is handled immediately by btn-agent-browser-toggle (not deferred to Save)
                    ok_browser = True
                    # Update anchor style
                    ok_anchor = True
                    try:
                        anchor_select = root.query_one("#select-anchor-style", Select)
                        anchor_val = anchor_select.value
                        if isinstance(anchor_val, str) and anchor_val:
                            ok_anchor = reader.update_agent_field(self.agent_name, "anchor_style", anchor_val)
                    except Exception:
                        pass
                    ok_triage = True
                    try:
                        triage_select = root.query_one("#select-triage-model", Select)
                        triage_model = (
                            triage_select.value
                            if isinstance(triage_select.value, str) and triage_select.value
                            else None
                        )
                        ok_triage = reader.update_agent_field(
                            self.agent_name, "triage_model", triage_model,
                        )
                    except Exception:
                        pass
                    ok_routing = True
                    try:
                        routing_select = root.query_one("#select-model-routing", Select)
                        routing_raw = routing_select.value
                        routing_val = 1 if routing_raw in (1, "1", True) else 0
                        ok_routing = reader.update_agent_field(
                            self.agent_name, "model_routing_enabled", routing_val,
                        )
                    except Exception:
                        pass
                    ok_skill = True
                    try:
                        skill_mode_select = root.query_one("#select-skill-mode", Select)
                        skill_mode_val = (
                            skill_mode_select.value
                            if isinstance(skill_mode_select.value, str)
                            else "hybrid"
                        )
                        ok_skill = reader.update_agent_field(
                            self.agent_name, "skill_injection_mode", skill_mode_val,
                        )
                    except Exception:
                        pass
                    if all([
                        ok_model, ok_ctx, ok_depth, ok_status, ok_inactive, ok_comms,
                        ok_anchor, ok_browser, ok_triage, ok_routing, ok_skill,
                    ]):
                        app.notify(f"Saved settings for {self.agent_name}", title="Agent Settings")
                        agents_panel.refresh_data()
                    else:
                        app.notify("Save failed — check DB permissions", title="Error", severity="error")
                except ValueError:
                    app.notify("Invalid input — must be whole numbers", title="Error", severity="error")
            if not self._embedded:
                app.pop_screen()
            # Push SYCL activation modal after closing the edit modal
            if sycl_activation_needed:
                app.push_screen(SyclActivationModal(
                    new_model, topology, self.agent_name, pending_num_ctx,
                ))
        elif event.button.id == "msg-dialog-close":
            if not self._embedded:
                self._form_app.pop_screen()
        elif event.button.id == "btn-agent-browser-toggle":
            self._toggle_agent_browser()

    def _update_browser_toggle_ui(self, val: int) -> None:
        """Update browser toggle button and status label in UI."""
        root = self._form_root
        try:
            btn = root.query_one("#btn-agent-browser-toggle", Button)
            status_label = root.query_one("#agent-browser-status-label", Static)
            if val == 1:
                btn.label = "Disable Browser Automation"
                btn.variant = "error"
                status_label.update("[cyan]Browser Automation[/] — Status: [green]● Enabled[/]")
            else:
                btn.label = "Enable Browser Automation"
                btn.variant = "success"
                status_label.update("[cyan]Browser Automation[/] — Status: [red]● Disabled[/]")
        except Exception:
            pass

    def _toggle_agent_browser(self) -> None:
        """Fetch current agent browser state and push provisioning/cleanup modal."""
        import sqlite3 as _sql3
        app = self._form_app
        try:
            _conn = _sql3.connect("/var/lib/versa-agi/agents.db", timeout=5)
            _row = _conn.execute(
                "SELECT browser_enabled, os_user FROM agents WHERE name=?",
                (self.agent_name,)
            ).fetchone()
            if not _row:
                _conn.close()
                app.notify("Agent not found in database", severity="error")
                return
            current_val = _row[0] or 0
            os_user = _row[1] or ""
            new_val = 0 if current_val == 1 else 1
            _conn.close()
        except Exception as e:
            app.notify(f"DB error: {e}", severity="error")
            return

        if os_user:
            app.push_screen(
                AgentBrowserToggleModal(
                    agent_name=self.agent_name,
                    new_val=new_val,
                    os_user=os_user,
                    parent_modal=self
                )
            )


class AgentBrowserToggleModal(ModalScreen):
    """Modal that toggles browser automation for an agent and streams real-time installation/cleanup feedback."""

    CSS = """
    AgentBrowserToggleModal {
        align: center middle;
        background: $surface 80%;
    }
    #browser-toggle-dialog {
        width: 75;
        height: 20;
        padding: 1 2;
        border: heavy $primary;
        background: $surface;
    }
    #browser-toggle-terminal {
        height: 1fr;
        background: $boost;
        border: solid $surface-lighten-1;
        padding: 0 1;
        scrollbar-gutter: stable;
        color: $text-muted;
    }
    #browser-toggle-actions {
        margin-top: 1;
        height: auto;
        align: right middle;
    }
    """

    def __init__(self, agent_name: str, new_val: int, os_user: str, parent_modal=None, **kwargs):
        super().__init__(**kwargs)
        self.agent_name = agent_name
        self.new_val = new_val
        self.os_user = os_user
        self.parent_modal = parent_modal
        self._terminal_text = ""
        self._running = False

    def compose(self) -> ComposeResult:
        action = "Enabling" if self.new_val == 1 else "Disabling"
        with Vertical(id="browser-toggle-dialog"):
            yield Static(f"[bold yellow]🌐 Browser Automation — {action} for {self.agent_name}[/]\n", id="browser-toggle-title")
            yield VerticalScroll(Static(id="browser-toggle-terminal-text"), id="browser-toggle-terminal")
            with Horizontal(id="browser-toggle-actions"):
                yield Button("Cancel/Close", classes="dismiss-btn", variant="default", id="btn-browser-toggle-close")

    def on_mount(self) -> None:
        self.query_one("#btn-browser-toggle-close", Button).disabled = True
        self._running = True
        import threading
        threading.Thread(target=self._run_toggle, daemon=True).start()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-browser-toggle-close":
            self.dismiss(True)

    @staticmethod
    def _strip_ansi(text: str) -> str:
        import re as _re
        return _re.sub(r'\x1b\[[0-9;]*[a-zA-Z]', '', text)

    def _append_text(self, text: str) -> None:
        self._terminal_text += text
        try:
            term = self.query_one("#browser-toggle-terminal-text", Static)
            term.update(self._terminal_text)
            self.query_one("#browser-toggle-terminal", VerticalScroll).scroll_end(animate=False)
        except Exception:
            pass

    def _enable_close(self) -> None:
        btn = self.query_one("#btn-browser-toggle-close", Button)
        btn.disabled = False
        btn.loading = False

    def _write_db_val(self, val: int) -> bool:
        import sqlite3 as _sql3
        try:
            _conn = _sql3.connect("/var/lib/versa-agi/agents.db", timeout=5)
            _conn.execute(
                "UPDATE agents SET browser_enabled=?, updated_at=datetime('now') WHERE name=?",
                (val, self.agent_name)
            )
            _conn.commit()
            _conn.close()
            return True
        except Exception:
            return False

    def _run_toggle(self) -> None:
        import subprocess as _sp
        import os as _os
        try:
            if self.new_val == 1:
                pw_bin = "/usr/local/lib/versa-agi/venv/bin/playwright"
                if _os.path.isfile(pw_bin):
                    # Fix Playwright driver permissions (pip install may not preserve +x on node binary)
                    import glob as _glob
                    for driver_dir in _glob.glob("/usr/local/lib/versa-agi/venv/lib/python3.*/site-packages/playwright/driver"):
                        node_bin = _os.path.join(driver_dir, "node")
                        if _os.path.isfile(node_bin):
                            _os.chmod(node_bin, 0o755)
                        pkg_bin_dir = _os.path.join(driver_dir, "package", "bin")
                        if _os.path.isdir(pkg_bin_dir):
                            for f in _os.listdir(pkg_bin_dir):
                                fp = _os.path.join(pkg_bin_dir, f)
                                if _os.path.isfile(fp):
                                    _os.chmod(fp, 0o755)
                    self.app.call_from_thread(self._append_text, f"$ sudo -u {self.os_user} -H {pw_bin} install chromium\n")
                    cmd = ["sudo", "-u", self.os_user, "-H", pw_bin, "install", "chromium"]
                    process = _sp.Popen(
                        cmd,
                        stdout=_sp.PIPE,
                        stderr=_sp.STDOUT,
                        text=True,
                        bufsize=1,
                    )
                    while True:
                        line = process.stdout.readline()
                        if not line:
                            break
                        self.app.call_from_thread(self._append_text, self._strip_ansi(line))
                    process.wait()
                    if process.returncode == 0:
                        self._write_db_val(1)
                        if self.parent_modal:
                            self.app.call_from_thread(self.parent_modal._update_browser_toggle_ui, 1)
                        self.app.call_from_thread(self._append_text, f"\n[green]✓ Playwright Chromium installed successfully for {self.agent_name}[/]\n")
                        self.app.call_from_thread(
                            self.app.notify, f"Playwright Chromium installed for {self.agent_name}", title="Browser Provisioning"
                        )
                    else:
                        self.app.call_from_thread(self._append_text, f"\n[red]✗ Installation failed with exit code {process.returncode}[/]\n")
                        self.app.call_from_thread(
                            self.app.notify, f"Failed to install Chromium for {self.agent_name}", title="Error", severity="error"
                        )
                else:
                    self.app.call_from_thread(self._append_text, f"[red]Playwright binary not found at {pw_bin}[/]\n")
            else:
                self.app.call_from_thread(self._append_text, f"Removing browser binaries from /home/{self.os_user}/.cache/ms-playwright/...\n")
                cache_dir = f"/home/{self.os_user}/.cache/ms-playwright/"
                if _os.path.isdir(cache_dir):
                    cmd = ["sudo", "-u", self.os_user, "rm", "-rf", cache_dir]
                    self.app.call_from_thread(self._append_text, f"$ sudo -u {self.os_user} rm -rf {cache_dir}\n")
                    res = _sp.run(cmd, capture_output=True, text=True, timeout=30)
                    if res.returncode == 0:
                        self._write_db_val(0)
                        if self.parent_modal:
                            self.app.call_from_thread(self.parent_modal._update_browser_toggle_ui, 0)
                        self.app.call_from_thread(self._append_text, f"[green]✓ Cleaned up cache directory successfully.[/]\n")
                    else:
                        self.app.call_from_thread(self._append_text, f"[red]✗ Cleanup failed: {res.stderr}[/]\n")
                else:
                    self._write_db_val(0)
                    if self.parent_modal:
                        self.app.call_from_thread(self.parent_modal._update_browser_toggle_ui, 0)
                    self.app.call_from_thread(self._append_text, "Cache directory does not exist or is already removed.\n")
                
                self.app.call_from_thread(self._append_text, f"\n[green]✓ Browser automation disabled for {self.agent_name}[/]\n")
                self.app.call_from_thread(
                    self.app.notify, f"Browser binaries removed for {self.agent_name}", title="Browser Cleanup"
                )
        except Exception as e:
            self.app.call_from_thread(self._append_text, f"\n[red]Error during browser toggle: {e}[/]\n")
        finally:
            self._running = False
            self.app.call_from_thread(self._enable_close)

