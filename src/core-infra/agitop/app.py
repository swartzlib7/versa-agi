"""
agitop — Versa AGi Mission Control Dashboard v1.0.0
Main Textual application with live data panels.
"""

import json
from pathlib import Path
from typing import Iterable

from textual import on
from textual.app import App, ComposeResult, SystemCommand
from textual.containers import VerticalScroll, Vertical, Horizontal
from textual.screen import Screen
from textual.widgets import Header, Footer, TabbedContent, TabPane, Collapsible, Static
from textual.binding import Binding

from agitop.data import AgentReader, MessageReader, TasksReader, OrganizationReader, OrganizationWriter
from agitop.data.status_reader import StatusReader
from agitop.data.system_reader import SystemReader
from agitop.data.config_reader import ConfigReader
from agitop.panels.system import SystemPanel
from agitop.panels.agents import AgentsPanel
from agitop.panels.tasks import TasksPanel
from agitop.panels.messages import MessagesPanel
from agitop.panels.projects import ProjectsPanel
from agitop.panels.footer_stats import FooterStatsPanel
from agitop.panels.organization import (
    OrganizationEntityPanel, OrganizationTreePanel, ORGANIZATION_TABS,
)
from agitop.panels.organization_modal import OrganizationModal
from agitop.feature_flags import ORGANIZATION_UI_VISIBLE
from agitop.version import read_product_version

VERSION = read_product_version()

# Themes offered in Ctrl+T picker (builtins outside this set are unregistered).
# solarized-light omitted — poor contrast with agitop's cyan/hardcoded styles;
# fixing it would require global markup changes that alter every dark theme.
_ALLOWED_THEMES = frozenset({
    "dracula",
    "flexoki",
    "gruvbox",
    "monokai",
    "nord",
    "rose-pine",
    "solarized-dark",
    "textual-dark",
    "tokyo-night",
})

# Persisted UI state (collapsed regions, etc.) — survives agitop restarts. agitop
# runs as root; /var/lib/versa-agi is the standard writable state dir.
_UI_STATE_PATH = Path("/var/lib/versa-agi/agitop_ui_state.json")


def _load_ui_state() -> dict:
    """Best-effort read of the persisted UI state; never raises."""
    try:
        data = json.loads(_UI_STATE_PATH.read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_ui_state(state: dict) -> None:
    """Best-effort write of the persisted UI state; never raises."""
    try:
        _UI_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        _UI_STATE_PATH.write_text(json.dumps(state))
    except OSError:
        pass


class AgitopApp(App):
    """Versa AGi Mission Control Dashboard."""

    TITLE = f"agitop — Versa AGi Mission Control v{VERSION}"
    CSS_PATH = "agitop.tcss"
    # Do NOT set `theme = "…"` as a class attribute — that shadows App's
    # Reactive descriptor and theme picker selections never refresh CSS.
    # Default / persisted theme is applied in __init__ via self.theme = …

    BINDINGS = [
        Binding("q", "quit", "Quit", show=True),
        Binding("r", "refresh_all", "Refresh", show=True),
        Binding("g", "show_registration", "Registration", show=True),
        Binding("b", "show_coa_bootstrap", "API Keys / COA", show=True),
        Binding("question_mark", "show_help", "Help", show=True),
    ]

    # Textual built-ins we keep out of the Ctrl+P palette.
    _PALETTE_HIDDEN = frozenset({"Maximize", "Minimize", "Screenshot"})

    def get_system_commands(self, screen: Screen) -> Iterable[SystemCommand]:
        """Command palette without Maximize / Minimize / Screenshot."""
        for command in super().get_system_commands(screen):
            if command.title not in self._PALETTE_HIDDEN:
                yield command

    def __init__(self, agents_db_path: str = "", messages_db_path: str = "",
                 tasks_db_path: str = "", cycles_db_path: str = "",
                 config_path: str = "", cycle_id_path: str = "",
                 organization_db_path: str = ""):
        super().__init__()
        self._configure_themes()
        self.config_path = config_path
        # Initialize data readers
        self.agent_reader = AgentReader(agents_db_path, cycles_db_path, messages_db_path, tasks_db_path) if agents_db_path and cycles_db_path else None
        self.message_reader = MessageReader(messages_db_path) if messages_db_path else None
        self.tasks_reader = TasksReader(tasks_db_path) if tasks_db_path else None
        # Organization domain (Wave integration) — gated by feature flag. Reads
        # via OrganizationReader; writes via OrganizationWriter (shared store
        # path, same as agictl) so the UI can author without an agent.
        self.organization_reader = (
            OrganizationReader(organization_db_path) if organization_db_path
            else OrganizationReader()
        )
        self.organization_writer = OrganizationWriter(
            self.organization_reader.db_path
        )

        self.status = StatusReader(cycle_id_path)
        self.system = SystemReader()
        self.config = ConfigReader(config_path) if config_path else None
        self._prev_tab = "messages-tab"
        self._prev_sys_tab = "sys-system-tab"
        # Guard: the Collapsible reactive fires during initial mount. We only
        # persist toggle events once the app is fully mounted, otherwise the
        # init event overwrites the loaded state with the default value.
        self._ui_ready = False

    # Dummy system-tabs panes that open modals / run ops (org-tab pattern).
    _SYSTEM_LAUNCHERS = frozenset({
        "sys-launch-settings",
        "sys-launch-game",
        "sys-launch-api-keys",
        "sys-launch-models",
        "sys-launch-routing",
    })

    def _configure_themes(self) -> None:
        """Keep a curated dark-theme set for the Ctrl+T picker."""
        for name in list(self.available_themes):
            if name not in _ALLOWED_THEMES:
                self.unregister_theme(name)
        saved = _load_ui_state().get("theme", "gruvbox")
        self.theme = saved if saved in _ALLOWED_THEMES else "gruvbox"

    def watch_theme(self, theme_name: str) -> None:
        """Persist theme choice across agitop restarts."""
        if not getattr(self, "_ui_ready", False):
            return
        state = _load_ui_state()
        if state.get("theme") == theme_name:
            return
        state["theme"] = theme_name
        _save_ui_state(state)

    def compose(self) -> ComposeResult:
        """Create the dashboard layout."""
        ui_state = _load_ui_state()
        _sys_tab = ui_state.get("system_tabs_active", "sys-system-tab")
        if _sys_tab not in ("sys-system-tab", "sys-agents-tab"):
            _sys_tab = "sys-system-tab"
        self._prev_sys_tab = _sys_tab
        yield Header()

        with VerticalScroll(id="dashboard-scroll", can_focus=False):
            with Vertical(id="dashboard"):
                with Collapsible(
                    title="System & Controls",
                    id="system-controls-collapse",
                    collapsed=ui_state.get("system_controls_collapsed", False),
                ):
                    with TabbedContent(
                        initial=_sys_tab,
                        id="system-tabs",
                    ):
                        with TabPane("System", id="sys-system-tab"):
                            yield SystemPanel(
                                self.system, self.config, self.status,
                                self.agent_reader, id="system-panel",
                            )
                        with TabPane("Agents", id="sys-agents-tab"):
                            yield AgentsPanel(
                                self.agent_reader, self.system, id="agents-panel",
                            )
                        # Launcher tabs (always visible) — open modals / run ops
                        with TabPane("⚙ Settings", id="sys-launch-settings"):
                            yield Static("Opening Settings…")
                        with TabPane("🎯 Game of Life", id="sys-launch-game"):
                            yield Static("Opening Game of Life…")
                        with TabPane("🔑 API Keys", id="sys-launch-api-keys"):
                            yield Static("Opening API Keys…")
                        with TabPane("🧩 Models", id="sys-launch-models"):
                            yield Static("Opening Model Manager…")
                        with TabPane("🔀 Routing", id="sys-launch-routing"):
                            yield Static("Opening Model Routing…")
                yield FooterStatsPanel(
                    self.agent_reader, tasks_reader=self.tasks_reader, id="footer-stats-panel",
                )
                with TabbedContent(initial="messages-tab", id="work-tabs"):
                    with TabPane("✉  Messages", id="messages-tab"):
                        yield MessagesPanel(
                            self.message_reader,
                            config=self.config,
                            agent_reader=self.agent_reader,
                            id="messages-panel",
                        )
                    with TabPane("◎  Tasks", id="tasks-tab"):
                        yield TasksPanel(
                            self.tasks_reader,
                            message_reader=self.message_reader,
                            status_reader=self.status,
                            id="tasks-panel",
                        )
                    with TabPane("◆  Projects", id="projects-tab"):
                        yield ProjectsPanel(self.tasks_reader, id="projects-panel")
                    if ORGANIZATION_UI_VISIBLE:
                        with TabPane("◉ Organizations", id="org-tab"):
                            yield Static("Launching Organizations...", id="org-launch-status")

        yield Footer()

    def on_collapsible_toggled(self, event: Collapsible.Toggled) -> None:
        """Remember the collapsed state of System & Controls across restarts."""
        if not self._ui_ready:
            return   # ignore events fired during initial mount/compose
        if event.collapsible.id != "system-controls-collapse":
            return
        state = _load_ui_state()
        state["system_controls_collapsed"] = event.collapsible.collapsed
        _save_ui_state(state)

    # Also handle the concrete subclasses explicitly — Textual posts Collapsed/
    # Expanded (not the base Toggled) so we catch both names to be safe across
    # Textual versions.
    def on_collapsible_collapsed(self, event: Collapsible.Toggled) -> None:
        self.on_collapsible_toggled(event)

    def on_collapsible_expanded(self, event: Collapsible.Toggled) -> None:
        self.on_collapsible_toggled(event)


    @on(TabbedContent.TabActivated)
    def _on_tab_activated(self, event: TabbedContent.TabActivated) -> None:
        """Persist content tabs; fire launcher tabs (Settings/Game/… / Organizations)."""
        try:
            system_tabs = self.query_one("#system-tabs", TabbedContent)
        except Exception:
            system_tabs = None
        if system_tabs is not None and event.tabbed_content is system_tabs:
            pane_id = event.pane.id if event.pane else None
            if pane_id in self._SYSTEM_LAUNCHERS:
                system_tabs.active = self._prev_sys_tab
                self._run_system_launcher(pane_id)
                return
            if pane_id in ("sys-system-tab", "sys-agents-tab"):
                self._prev_sys_tab = pane_id
                if self._ui_ready:
                    state = _load_ui_state()
                    state["system_tabs_active"] = pane_id
                    _save_ui_state(state)
            return

        tabbed_content = self.query_one("#work-tabs", TabbedContent)
        # Only handle events originating from the dashboard's work-tabs,
        # not from nested TabbedContent inside modals (OrgRecordModal, etc.)
        if event.tabbed_content is not tabbed_content:
            return
        if event.pane.id == "org-tab":
            # Revert to the previously active tab so the dummy pane isn't shown
            tabbed_content.active = self._prev_tab
            self.push_screen(
                OrganizationModal(
                    self.organization_reader,
                    self.organization_writer,
                    tasks_reader=self.tasks_reader,
                    agent_reader=self.agent_reader,
                    config=self.config,
                ),
            )
        else:
            self._prev_tab = event.pane.id

    def _run_system_launcher(self, pane_id: str) -> None:
        """Handle System & Controls launcher tabs (modals / one-shot ops)."""
        if pane_id == "sys-launch-settings":
            from agitop.panels.system_settings_modal import SystemSettingsModal
            self.push_screen(SystemSettingsModal())
        elif pane_id == "sys-launch-game":
            from agitop.panels.strategy_modal import StrategyModal
            self.push_screen(StrategyModal(self.tasks_reader))
        elif pane_id == "sys-launch-api-keys":
            from agitop.panels.api_keys_modal import ApiKeysModal
            self.push_screen(ApiKeysModal())
        elif pane_id == "sys-launch-models":
            from agitop.panels.model_manager_modal import ModelManagerModal
            self.push_screen(ModelManagerModal())
        elif pane_id == "sys-launch-routing":
            from agitop.panels.model_routing_modal import ModelRoutingModal
            self.push_screen(ModelRoutingModal())

    def on_mount(self) -> None:  # type: ignore[override]
        """Start periodic refresh timer and background install registration tripwire."""
        self._ui_ready = True  # allow toggle-state persistence from here on
        status = self._run_registration_tripwire()
        if self._should_prompt_registration(status):
            display_status = self._fetch_registration_status() or status
            self.call_after_refresh(
                lambda: self._open_registration_then_bootstrap(display_status)
            )
        else:
            self.call_after_refresh(self._maybe_open_coa_bootstrap)
        self._start_refresh_timer()

    def _open_registration_then_bootstrap(self, status: dict) -> None:
        from agitop.panels.registration_modal import RegistrationModal

        def _after_reg(_result=None) -> None:
            self._maybe_open_coa_bootstrap()

        self.push_screen(RegistrationModal(status), _after_reg)

    def _maybe_open_coa_bootstrap(self) -> None:
        try:
            from agitop.coa_bootstrap import should_auto_prompt_bootstrap
            from agitop.panels.api_keys_modal import ApiKeysModal

            if should_auto_prompt_bootstrap():
                self.push_screen(ApiKeysModal(bootstrap=True), self._after_coa_bootstrap)
        except Exception as exc:
            import sys
            print(f"[agitop] COA bootstrap tripwire: {exc}", file=sys.stderr)

    def action_show_coa_bootstrap(self) -> None:
        """Open API Keys modal in COA bootstrap mode (banner / b binding)."""
        from agitop.coa_bootstrap import needs_coa_bootstrap
        from agitop.panels.api_keys_modal import ApiKeysModal

        bootstrap = needs_coa_bootstrap()
        callback = self._after_coa_bootstrap if bootstrap else None
        self.push_screen(ApiKeysModal(bootstrap=bootstrap), callback)

    def _after_coa_bootstrap(self, result=None) -> None:
        """Refresh panels after first-login assign so COA hold/model update immediately."""
        if result == "done":
            self._refresh_all_data()

    def _should_prompt_registration(self, status: dict) -> bool:
        """Auto-prompt only for actionable version gates — not every failed registration retry."""
        if not status:
            return False
        if status.get("below_min_supported"):
            return True
        if status.get("update_available"):
            return True
        return False

    def _fetch_registration_status(self) -> dict:
        """Refresh registration display data for the modal."""
        try:
            import sys

            core_infra = Path(__file__).resolve().parents[2]
            if str(core_infra) not in sys.path:
                sys.path.insert(0, str(core_infra))
            from install_acceptance import refresh_for_display

            return refresh_for_display() or {}
        except Exception:
            return {}

    def _run_registration_tripwire(self) -> dict:
        """Retry deferred install acceptance submission once per launch."""
        try:
            import sys
            core_infra = Path(__file__).resolve().parents[2]
            if str(core_infra) not in sys.path:
                sys.path.insert(0, str(core_infra))
            from install_acceptance import tripwire_submit
            return tripwire_submit() or {}
        except Exception as exc:
            import sys
            print(f"[agitop] install registration tripwire: {exc}", file=sys.stderr)
            return {}

    def action_show_registration(self) -> None:
        """Open registration status modal from footer binding."""
        from agitop.panels.registration_modal import RegistrationModal

        self.push_screen(RegistrationModal(self._fetch_registration_status()))

    def _start_refresh_timer(self) -> None:
        """Create data refresh timer based on SystemPanel's interval setting."""
        if hasattr(self, '_data_timer') and self._data_timer:
            self._data_timer.stop()
        interval = self.query_one("#system-panel", SystemPanel).get_refresh_seconds()
        self._data_timer = self.set_interval(interval, self._refresh_all_data)

    def _refresh_all_data(self) -> None:
        """Unified refresh — all panels on one timer."""
        if len(self.screen_stack) > 1:
            return
        self.query_one("#system-panel", SystemPanel).refresh_data()
        self.query_one("#agents-panel", AgentsPanel).refresh_data()
        self.query_one("#projects-panel", ProjectsPanel).refresh_data()
        self.query_one("#messages-panel", MessagesPanel).refresh_data()
        self.query_one("#tasks-panel", TasksPanel).refresh_data()
        self.query_one("#footer-stats-panel", FooterStatsPanel).refresh_data()

    def action_update_refresh_interval(self) -> None:
        """Called by SystemPanel when user cycles the refresh rate."""
        self._start_refresh_timer()
        panel = self.query_one("#system-panel", SystemPanel)
        interval_label = SystemPanel.REFRESH_INTERVALS[panel._refresh_idx][0]
        self.notify(f"Refresh interval: {interval_label}", title="agitop")

    def action_refresh_all(self) -> None:
        """Force refresh all panels."""
        self._refresh_all_data()
        self.notify("Dashboard refreshed", title="agitop")



    def action_show_help(self) -> None:
        """Show help overlay with keybindings and links."""
        self.notify(
            f"agitop v{VERSION} — Versa AGi Mission Control\n\n"
            "\\[q] Quit  \\[r] Refresh  \\[g] Registration  \\[?] Help\n\n"
            "VersaVoice AI: https://versavoice.ai\n"
            "VersaVoice App: https://versavoice.app",
            title="agitop — Help",
        )


def main():
    """Entry point for agitop."""
    import argparse

    parser = argparse.ArgumentParser(
        description=f"agitop v{VERSION} — Versa AGi Mission Control Dashboard"
    )
    parser.add_argument(
        "--agents-db", default="/var/lib/versa-agi/agents.db",
        help="Path to agents.db"
    )
    parser.add_argument(
        "--messages-db", default="/var/lib/versa-agi/messages.db",
        help="Path to messages.db"
    )
    parser.add_argument(
        "--tasks-db", default="/var/lib/versa-agi/coa/tasks.db",
        help="Path to tasks.db"
    )
    parser.add_argument(
        "--cycles-db", default="/var/lib/versa-agi/coa/cycles.db",
        help="Path to cycles.db"
    )
    parser.add_argument(
        "--config", default="",
        help="Path to system_config.json"
    )
    parser.add_argument(
        "--cycle-id", default="/var/lib/versa-agi/coa/.current_cycle_id",
        help="Path to .current_cycle_id file"
    )
    parser.add_argument(
        "--organization-db", default="/var/lib/versa-agi/organization.db",
        help="Path to organization.db (Organization domain / Wave integration)"
    )

    args = parser.parse_args()

    app = AgitopApp(
        agents_db_path=args.agents_db,
        messages_db_path=args.messages_db,
        tasks_db_path=args.tasks_db,
        cycles_db_path=args.cycles_db,
        config_path=args.config,
        cycle_id_path=args.cycle_id,
        organization_db_path=args.organization_db,
    )
    app.run()


if __name__ == "__main__":
    main()
