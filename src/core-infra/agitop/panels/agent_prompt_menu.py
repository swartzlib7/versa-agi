"""Tabbed agent detail modal — settings, memory, prompts, cycle logs, threads."""

from __future__ import annotations

import os
import sqlite3
from typing import Optional

from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import (
    Button, DataTable, Input, RichLog, Select, Static, TabbedContent, TabPane, TextArea,
)

from agitop.panels.agent_memory_modal import (
    EditConnectionMemoryModal,
    RemoveConnectionMemoryModal,
    _build_name_cache,
    _tasks_db,
    _truncate,
    _utc_to_local as _mem_utc_to_local,
)
from agitop.panels.cycle_log_modal import (
    CycleLogController,
    EMBEDDED_CYCLE_LOG_ID_MAP,
    PurgeLogsConfirmModal,
    _MappedScreenHost,
)
from agitop.panels.thread_manager_modal import (
    DrainConfirmModal,
    _get_thread_data,
    _resolve_project_name,
)


class AgentPromptMenu(ModalScreen):
    """Tabbed agent viewer — settings, memory, prompts, logs, threads."""

    def __init__(self, agent_name: str, **kwargs):
        super().__init__(**kwargs)
        self.agent_name = agent_name
        self._conn_rows: dict[str, dict] = {}
        self.selected_contact_uid: Optional[str] = None
        self._original_model = ""
        self._original_num_ctx = 0
        self._cycle_embed: Optional[CycleLogController] = None
        self._cycle_initialized = False
        self.selected_thread_id: Optional[str] = None

    def _agents_panel(self):
        from agitop.panels.agents import AgentsPanel
        return self.app.query_one(AgentsPanel)

    def _agent_record(self) -> dict:
        panel = self._agents_panel()
        agents = panel.agent_reader.get_all_agents() if panel.agent_reader else []
        return next((a for a in agents if a.get("name") == self.agent_name), {})

    def compose(self) -> ComposeResult:
        from agitop.panels.agents import (
            _load_models_ini, _read_file, _utc_to_local, _triage_model_kwargs,
            compose_technical_setup_fields,
        )

        agent = self._agent_record()
        os_user = agent.get("os_user") or "--"
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
        if num_ctx_val and num_ctx_val >= 1024:
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

        is_pending_provision = (agent.get("status") or "") == "pending_approval"
        is_removal_pending = status == "removal_requested"
        is_circuit_broken = status == "circuit_breaker"
        is_halted = status == "halted"
        is_protected = agent.get("protected") == 1

        current_model = agent.get("model") or ""
        self._original_model = current_model
        self._original_num_ctx = agent.get("num_ctx", 0)
        panel = self._agents_panel()
        model_options = _load_models_ini(panel.system_reader)
        if is_protected and self.agent_name == "coa":
            coa_allowed = set(panel.system_reader.get_coa_approved_models())
            if coa_allowed:
                model_options = [(label, key) for label, key in model_options if key in coa_allowed]
        model_kwargs = {"id": "select-model", "allow_blank": True, "prompt": "System default"}
        if current_model and any(k == current_model for _, k in model_options):
            model_kwargs["value"] = current_model
        triage_model_options, triage_kwargs = _triage_model_kwargs(panel, self.agent_name)

        poise_path = f"/etc/versa-agi/poise/{self.agent_name}.md"
        prompt_path = f"/var/lib/versa-agi/{self.agent_name}/last_prompt.txt"
        poise_content = _read_file(poise_path)
        prompt_content = _read_file(prompt_path)

        frozen_count = 0
        if hasattr(self.app, "tasks_reader") and self.app.tasks_reader:
            frozen_count = self.app.tasks_reader.count_frozen(self.agent_name)
        halt_disabled = (
            is_protected or is_halted or is_circuit_broken
            or is_removal_pending or is_pending_provision
        )
        remove_disabled = is_protected or is_removal_pending

        with Vertical(id="agent-dialog"):
            yield Static(f"[bold]Agent — {self.agent_name}[/]", id="agent-dialog-title")
            with Horizontal(id="agent-dialog-header-row", classes="agent-dialog-header-row"):
                yield Static(info_text, id="agent-dialog-info")
                with Vertical(classes="agent-header-action-col"):
                    if is_circuit_broken:
                        yield Button(
                            "🔓 Clear Circuit Breaker", id="btn-clear-breaker",
                            variant="error", classes="panel-btn",
                        )
                    elif is_halted:
                        yield Button(
                            "▶ Re-activate Agent", id="btn-clear-breaker",
                            variant="error", classes="panel-btn",
                        )
                with Vertical(classes="agent-header-action-col"):
                    if frozen_count > 0:
                        yield Button(
                            f"❄ Unfreeze Tasks ({frozen_count})",
                            id="btn-unfreeze-tasks", variant="error", classes="panel-btn",
                        )

            if is_removal_pending or is_pending_provision:
                with Horizontal(id="agent-status-actions"):
                    if is_removal_pending:
                        yield Button("🗑 Confirm Removal", id="btn-confirm-remove", variant="error", classes="panel-btn")
                        yield Button("↩ Cancel Removal", id="btn-cancel-remove", variant="warning", classes="panel-btn")
                    elif is_pending_provision:
                        yield Button("✓ Approve & Provision", id="btn-approve-agent", variant="success", classes="panel-btn")

            with TabbedContent(initial="agent-general-tab", id="agent-tabs"):
                with TabPane("General", id="agent-general-tab"):
                    with Vertical(id="agent-general-pane"):
                        with VerticalScroll(id="agent-general-scroll"):
                            yield Static("", classes="modal-tab-spacer")
                            with Horizontal(classes="setup-form-row"):
                                with Vertical(classes="setup-form-col"):
                                    yield Static("[cyan]Processing Model[/] — AI model for this agent")
                                    yield Select(model_options, **model_kwargs)
                                with Vertical(classes="setup-form-col"):
                                    yield Static(
                                        "[cyan]Triage Model[/] — lightweight model for message classification "
                                    )
                                    yield Select(triage_model_options, **triage_kwargs)
                            with Horizontal(classes="setup-form-row"):
                                with Vertical(classes="setup-form-col"):
                                    yield Static("[cyan]Context Injection Mode[/]")
                                    yield Select(
                                        [("All contacts (COA default)", "all"), ("Relevant contacts only", "relevant")],
                                        value=agent.get("context_injection_mode") or "relevant",
                                        id="select-ctx-mode", allow_blank=False,
                                    )
                                with Vertical(classes="setup-form-col"):
                                    yield Static(
                                        "[cyan]Auto Model Routing[/] — triage may select a different execution model per spawn"
                                    )
                                    yield Select(
                                        [
                                            ("Disabled (use assigned model only)", 0),
                                            ("Enabled (pool or preferred-map routing)", 1),
                                        ],
                                        value=1 if int(agent.get("model_routing_enabled") or 0) else 0,
                                        id="select-model-routing",
                                        allow_blank=False,
                                    )
                                    yield Static(
                                        "[dim]Triage may select a different execution model per spawn.[/]",
                                        id="model-routing-hint",
                                    )
                            with Horizontal(classes="setup-form-row"):
                                with Vertical(classes="setup-form-col"):
                                    yield Static("[cyan]Anchor Style[/]")
                                    yield Select(
                                        [("Full (philosophical block)", "full"), ("Compact (identity line only)", "compact")],
                                        value=agent.get("anchor_style") or "compact",
                                        id="select-anchor-style", allow_blank=False,
                                    )
                                with Vertical(classes="setup-form-col"):
                                    yield Static("[cyan]Conversation Depth[/]")
                                    yield Input(
                                        value=str(agent.get("conversation_depth", 10)),
                                        placeholder="e.g. 10", id="input-convo-depth", type="integer",
                                    )
                            with Horizontal(classes="setup-form-row"):
                                if not is_protected:
                                    with Vertical(classes="setup-form-col"):
                                        yield Static("[cyan]External Comms[/]")
                                        yield Select(
                                            [("Enabled", 1), ("Disabled", 0)],
                                            value=agent.get("can_message_connections", 0),
                                            id="select-comms", allow_blank=False,
                                        )
                                    with Vertical(classes="setup-form-col"):
                                        yield Static("[cyan]State[/]")
                                        yield Select(
                                            [("Active (spawnable)", 0), ("Inactive (pending approval)", 1)],
                                            value=agent.get("inactive", 0),
                                            id="select-inactive", allow_blank=False,
                                        )
                            _ba_enabled = agent.get("browser_enabled", 0) == 1
                            _ba_label = "Disable" if _ba_enabled else "Enable"
                            _ba_variant = "error" if _ba_enabled else "success"
                            _ba_status = "[green]● Enabled[/]" if _ba_enabled else "[red]● Disabled[/]"
                            _browser_heading = f"[cyan]Browser Automation[/] — Status: {_ba_status}"
                            with Horizontal(classes="setup-form-row"):
                                with Vertical(classes="setup-form-col"):
                                    yield Static("[cyan]Skill Injection Mode[/]")
                                    yield Select(
                                        [
                                            ("Hybrid (core injected + lazy manifest)", "hybrid"),
                                            ("Full (inject all skills)", "full"),
                                            ("Lazy (manifest only)", "lazy"),
                                        ],
                                        value=agent.get("skill_injection_mode", "hybrid") or "hybrid",
                                        id="select-skill-mode",
                                        allow_blank=False,
                                    )
                                with Vertical(classes="setup-form-col agent-browser-toggle-col"):
                                    yield Static(_browser_heading, id="agent-browser-status-label")
                                    yield Button(
                                        f"{_ba_label} Browser Automation",
                                        variant=_ba_variant, id="btn-agent-browser-toggle",
                                    )
                        with Horizontal(classes="agent-tab-actions"):
                            yield Button(
                                "Save", variant="success", id="btn-agent-general-save",
                                classes="panel-btn",
                            )
                            yield Button(
                                "✋ Halt Agent", id="btn-halt-agent", variant="error",
                                classes="panel-btn", disabled=halt_disabled,
                            )
                            yield Button(
                                "🗑 Request Removal", id="btn-request-remove", variant="error",
                                classes="panel-btn", disabled=remove_disabled,
                            )
                            yield Button(
                                "Close", variant="default", id="btn-agent-general-close",
                                classes="panel-btn dismiss-btn agent-modal-close",
                            )

                with TabPane("Technical", id="agent-tech-tab"):
                    with Vertical(id="agent-tech-pane"):
                        with VerticalScroll(id="agent-tech-scroll"):
                            yield Static("", classes="modal-tab-spacer")
                            yield from compose_technical_setup_fields(panel, self.agent_name)
                        with Horizontal(classes="agent-tab-actions"):
                            yield Button(
                                "Save", variant="success", id="btn-agent-tech-save",
                                classes="panel-btn",
                            )
                            yield Button(
                                "Close", variant="default", id="btn-agent-tech-close",
                                classes="panel-btn dismiss-btn agent-modal-close",
                            )

                with TabPane("Overrides", id="agent-overrides-tab"):
                    with Vertical(id="agent-overrides-pane"):
                        with VerticalScroll(id="agent-overrides-scroll"):
                            yield Static("", classes="modal-tab-spacer")
                            from agitop.panels.agents import compose_model_generation_fields
                            yield from compose_model_generation_fields(panel, self.agent_name)
                        with Horizontal(classes="agent-tab-actions"):
                            yield Button(
                                "Save", variant="success", id="btn-agent-overrides-save",
                                classes="panel-btn",
                            )
                            yield Button(
                                "Close", variant="default", id="btn-agent-overrides-close",
                                classes="panel-btn dismiss-btn agent-modal-close",
                            )

                with TabPane("Connection Memory", id="agent-memory-tab"):
                    with Vertical(id="agent-memory-pane"):
                        yield Static("", classes="modal-tab-spacer")
                        yield Static("[bold cyan]Connection Memory[/]", id="agent-tab-mem-header")
                        with VerticalScroll(id="agent-memory-scroll"):
                            yield DataTable(id="agent-tab-mem-table", cursor_type="row")
                        yield Static("", id="agent-tab-mem-hint")
                        with Horizontal(classes="agent-tab-actions"):
                            yield Button(
                                "Edit Selected", variant="primary",
                                id="btn-agent-mem-edit-conn", disabled=True, classes="panel-btn",
                            )
                            yield Button(
                                "Remove Selected", variant="error",
                                id="btn-agent-mem-remove-conn", disabled=True, classes="panel-btn",
                            )
                            yield Button(
                                "Close", variant="default",
                                id="btn-agent-memory-close",
                                classes="panel-btn dismiss-btn agent-modal-close",
                            )

                with TabPane("Poise Template", id="agent-poise-tab"):
                    with Vertical(id="agent-poise-pane"):
                        yield Static("", classes="modal-tab-spacer")
                        yield Static("[bold cyan]Poise Template[/] [dim](select text + Ctrl+C to copy)[/]")
                        with VerticalScroll(id="agent-poise-scroll"):
                            yield TextArea(poise_content, id="agent-poise-body", read_only=True)
                        with Horizontal(classes="agent-tab-actions"):
                            yield Button(
                                "Close", variant="default",
                                id="btn-agent-poise-close",
                                classes="panel-btn dismiss-btn agent-modal-close",
                            )

                with TabPane("System Prompt", id="agent-last-prompt-tab"):
                    with Vertical(id="agent-prompt-pane"):
                        yield Static("", classes="modal-tab-spacer")
                        yield Static("[bold cyan]System Prompt[/] [dim](select text + Ctrl+C to copy)[/]")
                        with VerticalScroll(id="agent-prompt-scroll"):
                            yield TextArea(prompt_content, id="agent-last-prompt-body", read_only=True)
                        with Horizontal(classes="agent-tab-actions"):
                            yield Button(
                                "Close", variant="default",
                                id="btn-agent-prompt-close",
                                classes="panel-btn dismiss-btn agent-modal-close",
                            )

                with TabPane("Cycle Logs", id="agent-cycle-tab"):
                    with Vertical(id="agent-cycle-pane"):
                        yield Static("", classes="modal-tab-spacer")
                        yield Static("", id="agent-cycle-log-header")
                        yield Select(
                            options=[("Active / Latest Cycle Log", "active")],
                            id="agent-cycle-log-select", allow_blank=False, value="active",
                        )
                        with Horizontal(id="agent-step-nav-bar"):
                            yield Button("◀ Prev", id="agent-step-prev")
                            yield Static("", id="agent-step-indicator")
                            yield Button("Next ▶", id="agent-step-next")
                        yield RichLog(id="agent-cycle-log-body", wrap=False, highlight=True, markup=True)
                        with Horizontal(classes="agent-cycle-actions"):
                            yield Button(
                                "📋 Copy All", variant="default",
                                id="agent-cycle-log-copy", classes="panel-btn",
                            )
                            yield Button(
                                "🗑 Purge All Logs", variant="error",
                                id="btn-agent-cycle-log-purge", classes="panel-btn",
                            )
                            yield Button(
                                "Close", variant="default",
                                id="btn-agent-cycle-close",
                                classes="panel-btn dismiss-btn agent-modal-close",
                            )

                with TabPane("Threads", id="agent-threads-tab"):
                    with Vertical(id="agent-threads-pane"):
                        yield Static("", classes="modal-tab-spacer")
                        db_path = f"/var/lib/versa-agi/{self.agent_name}/cycles/checkpoints.db"
                        yield Static(f"[bold cyan]Threads[/]  [dim]{db_path}[/]")
                        with VerticalScroll(id="agent-threads-scroll"):
                            yield DataTable(id="agent-thread-table", cursor_type="row")
                        yield Static("", id="agent-thread-summary")
                        with Horizontal(classes="agent-thread-actions"):
                            if os.path.exists(db_path):
                                yield Button(
                                    "🗑 Drain Selected", variant="error",
                                    id="btn-agent-drain-selected", disabled=True, classes="panel-btn",
                                )
                                yield Button(
                                    "🗑 Drain All", variant="error",
                                    id="btn-agent-drain-all", classes="panel-btn",
                                )
                            yield Button(
                                "Close", variant="default",
                                id="btn-agent-threads-close",
                                classes="panel-btn dismiss-btn agent-modal-close",
                            )

    def _close_agent_modal(self) -> None:
        if self._cycle_embed:
            self._cycle_embed.stop()
        self.app.pop_screen()

    def on_mount(self) -> None:
        _TZ = __import__("time").strftime("%Z")
        table = self.query_one("#agent-tab-mem-table", DataTable)
        table.add_columns("Contact", "Rapport", "Comm Style", "Summary", f"Updated ({_TZ})")
        self.refresh_connection_table()

        thread_table = self.query_one("#agent-thread-table", DataTable)
        thread_table.cursor_type = "row"
        thread_table.add_columns("Thread ID", "Project", "Checkpoints", "Writes", "Size")
        self.refresh_thread_table()

        self._init_cycle_log_embed()
        from agitop.panels.agents import apply_technical_setup_hints
        apply_technical_setup_hints(self.app, self.agent_name)
        self._sync_model_routing_ui()

    def _sync_model_routing_ui(self) -> None:
        from model_catalog import model_supports_auto_routing

        try:
            model_select = self.query_one("#select-model", Select)
            model_val = model_select.value if isinstance(model_select.value, str) else ""
            routing_select = self.query_one("#select-model-routing", Select)
            supported = model_supports_auto_routing(model_val, self.agent_name)
            hint = self.query_one("#model-routing-hint", Static)
            if supported:
                routing_select.disabled = False
                hint.update("[dim]Triage may select a different execution model per spawn.[/]")
            else:
                if routing_select.value in (1, "1", True):
                    routing_select.value = 0
                routing_select.disabled = True
                if model_val:
                    hint.update(
                        "[dim yellow]Not available for this model — requires a router-eligible catalog key.[/]"
                    )
                else:
                    hint.update("[dim yellow]Assign a processing model before enabling auto routing.[/]")
        except Exception:
            pass

    def _perform_general_save(self) -> None:
        from agitop.panels.agents import AgentEditModal

        self._sync_model_routing_ui()
        editor = AgentEditModal(self.agent_name, host=self)
        editor._original_model = self._original_model
        editor._original_num_ctx = self._original_num_ctx
        editor.on_button_pressed(self._fake_button_event("btn-save-settings"))
        self._agents_panel().refresh_data()
        try:
            model_select = self.query_one("#select-model", Select)
            val = model_select.value
            self._original_model = val if isinstance(val, str) else ""
        except Exception:
            pass
        self._sync_model_routing_ui()

    @on(Select.Changed, "#select-model")
    def on_agent_model_changed(self, _event: Select.Changed) -> None:
        self._sync_model_routing_ui()
        from agitop.panels.agents import apply_model_generation_hints
        apply_model_generation_hints(self.app, self.agent_name)

    def _init_cycle_log_embed(self) -> None:
        agent = self._agent_record()
        os_user = agent.get("os_user") or self.agent_name
        panel = self._agents_panel()
        host = _MappedScreenHost(self, EMBEDDED_CYCLE_LOG_ID_MAP)
        self._cycle_embed = CycleLogController(
            host,
            self.agent_name,
            system_reader=panel.system_reader,
            os_user=os_user,
        )
        try:
            self._cycle_embed.start()
            self.query_one("#agent-step-nav-bar").display = False
        except Exception:
            pass
        self._cycle_initialized = True

    @staticmethod
    def _fake_button_event(button_id: str):
        class _E:
            button = type("B", (), {"id": button_id})()

            @staticmethod
            def stop():
                pass

        return _E()

    def refresh_connection_table(self) -> None:
        table = self.query_one("#agent-tab-mem-table", DataTable)
        table.clear()
        self._conn_rows = {}
        self.selected_contact_uid = None
        self.query_one("#btn-agent-mem-edit-conn", Button).disabled = True
        self.query_one("#btn-agent-mem-remove-conn", Button).disabled = True
        self.query_one("#agent-tab-mem-hint", Static).update("[dim]Select a row to edit or remove.[/]")
        try:
            conn = sqlite3.connect(_tasks_db(), timeout=5)
            conn.row_factory = sqlite3.Row
            name_cache = _build_name_cache(conn)
            rows = conn.execute(
                "SELECT * FROM agent_memory_connection WHERE agent_name=? ORDER BY updated_at DESC",
                (self.agent_name,),
            ).fetchall()
            conn.close()
        except Exception as e:
            self.query_one("#agent-tab-mem-header", Static).update(
                f"[bold cyan]Connection Memory[/] [red](error: {e})[/]"
            )
            return
        for r in rows:
            row = dict(r)
            uid = row.get("contact_uid") or ""
            if not uid:
                continue
            display = name_cache.get(uid, uid[:12] + "...")
            summary = _truncate(
                row.get("personal_notes") or row.get("preferences") or row.get("emotional_notes") or ""
            )
            table.add_row(
                display,
                row.get("rapport_level") or "--",
                _truncate(row.get("communication_style") or "", 24) or "--",
                summary or "--",
                _mem_utc_to_local(row.get("updated_at") or ""),
                key=uid,
            )
            self._conn_rows[uid] = row
        count = len(self._conn_rows)
        self.query_one("#agent-tab-mem-header", Static).update(
            f"[bold cyan]Connection Memory ({count})[/]"
            if count else "[bold cyan]Connection Memory[/] [dim](none)[/]"
        )

    def refresh_cycle_log_after_purge(self) -> None:
        """Repaint cycle-log tab after filesystem purge (picklist + log body)."""
        try:
            tabs = self.query_one("#agent-tabs", TabbedContent)
            tabs.active = "agent-cycle-tab"
        except Exception:
            pass
        if self._cycle_embed:
            self._cycle_embed.refresh_after_purge()

    def refresh_thread_table(self) -> None:
        try:
            table = self.query_one("#agent-thread-table", DataTable)
        except Exception:
            return
        table.clear()
        self.selected_thread_id = None
        try:
            self.query_one("#btn-agent-drain-selected", Button).disabled = True
        except Exception:
            pass
        threads = _get_thread_data(self.agent_name)
        summary = self.query_one("#agent-thread-summary", Static)
        if not threads:
            summary.update("[dim]No checkpoint threads — agent will start fresh on next cycle.[/]")
            return
        total_size = 0
        for t in threads:
            thread_id = t["thread_id"]
            project = _resolve_project_name(thread_id)
            table.add_row(
                thread_id, project,
                str(t["checkpoint_count"]), str(t["write_count"]), t["size_str"],
                key=thread_id,
            )
            total_size += t.get("total_bytes") or 0
        if total_size >= 1_048_576:
            total_str = f"{total_size / 1_048_576:.1f} MB"
        elif total_size >= 1024:
            total_str = f"{total_size / 1024:.1f} KB"
        else:
            total_str = f"{total_size} B"
        summary.update(
            f"[dim]{len(threads)} thread(s) — {total_str} total  │  "
            f"Select a row, then Drain Selected or Drain All[/]"
        )

    @on(DataTable.RowHighlighted, "#agent-thread-table")
    def on_thread_highlighted(self, event: DataTable.RowHighlighted) -> None:
        if event.row_key is None:
            return
        self.selected_thread_id = str(event.row_key.value)
        self.query_one("#btn-agent-drain-selected", Button).disabled = False
        self.query_one("#agent-thread-summary", Static).update(
            f"[bold cyan]Selected:[/] {self.selected_thread_id}"
        )

    @on(DataTable.RowHighlighted, "#agent-tab-mem-table")
    def on_connection_highlighted(self, event: DataTable.RowHighlighted) -> None:
        if event.row_key is None:
            return
        self.selected_contact_uid = event.row_key.value
        self.query_one("#btn-agent-mem-edit-conn", Button).disabled = False
        self.query_one("#btn-agent-mem-remove-conn", Button).disabled = False
        self.query_one("#agent-tab-mem-hint", Static).update(
            f"[bold cyan]Selected:[/] {self.selected_contact_uid}"
        )

    @on(DataTable.RowSelected, "#agent-tab-mem-table")
    def on_connection_selected(self, event: DataTable.RowSelected) -> None:
        row = self._conn_rows.get(event.row_key.value)
        if row:
            self.selected_contact_uid = event.row_key.value
            self.app.push_screen(EditConnectionMemoryModal(self.agent_name, row, self))

    @on(Select.Changed, "#agent-cycle-log-select")
    def on_cycle_select(self, event: Select.Changed) -> None:
        if self._cycle_embed:
            self._cycle_embed.on_select_changed(event)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id
        name = self.agent_name

        if bid in ("agent-step-prev", "agent-step-next", "agent-cycle-log-copy") and self._cycle_embed:
            mapped = {
                "agent-step-prev": "step-prev",
                "agent-step-next": "step-next",
                "agent-cycle-log-copy": "cycle-log-copy",
            }
            self._cycle_embed.on_button_pressed(mapped[bid])
            return

        if bid == "btn-agent-cycle-log-purge":
            self.app.push_screen(PurgeLogsConfirmModal(name, parent=self))
            return

        if bid == "btn-agent-mem-edit-conn" and self.selected_contact_uid:
            row = self._conn_rows.get(self.selected_contact_uid)
            if row:
                self.app.push_screen(EditConnectionMemoryModal(name, row, self))
        elif bid == "btn-agent-mem-remove-conn" and self.selected_contact_uid:
            self.app.push_screen(RemoveConnectionMemoryModal(name, self.selected_contact_uid, self))
        elif bid == "btn-agent-general-save":
            self._perform_general_save()
        elif bid == "btn-agent-tech-save":
            from agitop.panels.agents import TechnicalSetupModal
            setup = TechnicalSetupModal(name, host=self)
            setup.on_button_pressed(self._fake_button_event("btn-save-setup"))
            self._agents_panel().refresh_data()
        elif bid == "btn-agent-overrides-save":
            from agitop.panels.agents import save_model_generation_fields
            reader = self._agents_panel().agent_reader
            if reader:
                try:
                    if save_model_generation_fields(self, reader, name, self.app):
                        self.app.notify(f"Overrides saved for {name}", title="Overrides")
                        self._agents_panel().refresh_data()
                    else:
                        self.app.notify("Save failed — check DB permissions", title="Error", severity="error")
                except ValueError:
                    self.app.notify("Invalid input — check numeric fields", title="Error", severity="error")
        elif bid == "btn-agent-browser-toggle":
            from agitop.panels.agents import AgentEditModal
            editor = AgentEditModal(name, host=self)
            editor.on_button_pressed(self._fake_button_event("btn-agent-browser-toggle"))
        elif bid == "btn-agent-drain-selected" and self.selected_thread_id:
            project = _resolve_project_name(self.selected_thread_id)
            self.app.push_screen(
                DrainConfirmModal(
                    name, self.selected_thread_id, project, parent=self,
                )
            )
        elif bid == "btn-agent-drain-all":
            self.app.push_screen(
                DrainConfirmModal(name, "", "", drain_all=True, parent=self)
            )
        elif bid == "btn-request-remove":
            from agitop.panels.agents import RemovalConfirmModal
            self.app.push_screen(RemovalConfirmModal(name))
        elif bid in (
            "msg-dialog-close",
            "btn-agent-general-close",
            "btn-agent-tech-close",
            "btn-agent-overrides-close",
            "btn-agent-memory-close",
            "btn-agent-poise-close",
            "btn-agent-prompt-close",
            "btn-agent-cycle-close",
            "btn-agent-threads-close",
        ):
            self._close_agent_modal()
        else:
            self._handle_legacy_agent_actions(bid)

    def _handle_legacy_agent_actions(self, bid: str) -> None:
        import json as _json
        import subprocess
        name = self.agent_name
        if bid == "btn-unfreeze-tasks":
            if hasattr(self.app, "tasks_reader") and self.app.tasks_reader:
                count = self.app.tasks_reader.unfreeze_agent_tasks(name)
                if count > 0:
                    self.app.notify(f"Unfroze {count} task(s) for {name}", title="Tasks")
                else:
                    self.app.notify(f"No frozen tasks found for {name}", title="Tasks")
            self.app.pop_screen()
        elif bid == "btn-approve-agent":
            result = subprocess.run(["agictl", "agent", "approve", name], capture_output=True, text=True, timeout=30)
            self.app.pop_screen()
            self._notify_agictl_result(result, f"Agent '{name}' approved")
        elif bid == "btn-confirm-remove":
            result = subprocess.run(["agictl", "agent", "confirm-remove", name], capture_output=True, text=True, timeout=60)
            self.app.pop_screen()
            self._notify_agictl_result(result, f"Agent '{name}' removed")
        elif bid == "btn-cancel-remove":
            result = subprocess.run(["agictl", "agent", "cancel-remove", name], capture_output=True, text=True, timeout=10)
            self.app.pop_screen()
            self._notify_agictl_result(result, f"Removal cancelled for '{name}'")
        elif bid == "btn-clear-breaker":
            result = subprocess.run(["agictl", "agent", "activate", name], capture_output=True, text=True, timeout=15)
            self.app.pop_screen()
            self._notify_agictl_result(result, f"Agent '{name}' activated")
        elif bid == "btn-halt-agent":
            result = subprocess.run(["agictl", "agent", "kill", name], capture_output=True, text=True, timeout=15)
            self.app.pop_screen()
            self._notify_agictl_result(result, f"Agent '{name}' halted")

    def _notify_agictl_result(self, result, ok_msg: str) -> None:
        import json as _json
        try:
            self._agents_panel().refresh_data()
        except Exception:
            pass
        if result.returncode == 0:
            try:
                data = _json.loads(result.stdout)
                if data.get("success"):
                    self.app.notify(ok_msg, severity="information")
                else:
                    self.app.notify(data.get("error", "unknown"), severity="error")
            except Exception:
                self.app.notify(ok_msg, severity="information")
        else:
            self.app.notify(result.stderr[:200] or "Command failed", severity="error")
