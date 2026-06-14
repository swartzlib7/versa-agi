"""
agitop — Versa AGi Mission Control Dashboard v1.0.0
Main Textual application with live data panels.
"""

import json
from pathlib import Path

from textual.app import App, ComposeResult
from textual.containers import VerticalScroll
from textual.widgets import Header, Footer, TabbedContent, TabPane
from textual.binding import Binding

from agitop.data import AgentReader, MessageReader, TasksReader
from agitop.data.status_reader import StatusReader
from agitop.data.system_reader import SystemReader
from agitop.data.config_reader import ConfigReader
from agitop.panels.system import SystemPanel
from agitop.panels.agents import AgentsPanel
from agitop.panels.tasks import TasksPanel
from agitop.panels.messages import MessagesPanel
from agitop.panels.projects import ProjectsPanel
from agitop.panels.footer_stats import FooterStatsPanel
from agitop.version import read_product_version

VERSION = read_product_version()

class AgitopApp(App):
    """Versa AGi Mission Control Dashboard."""

    TITLE = f"agitop — Versa AGi Mission Control v{VERSION}"
    CSS_PATH = "agitop.tcss"
    # theme = "textual-light"

    BINDINGS = [
        Binding("q", "quit", "Quit", show=True),
        Binding("r", "refresh_all", "Refresh", show=True),
        Binding("g", "show_registration", "Registration", show=True),
        Binding("question_mark", "show_help", "Help", show=True),
    ]

    def __init__(self, agents_db_path: str = "", messages_db_path: str = "",
                 tasks_db_path: str = "", cycles_db_path: str = "",
                 config_path: str = "", cycle_id_path: str = ""):
        super().__init__()
        self.config_path = config_path
        # Initialize data readers
        self.agent_reader = AgentReader(agents_db_path, cycles_db_path, messages_db_path, tasks_db_path) if agents_db_path and cycles_db_path else None
        self.message_reader = MessageReader(messages_db_path) if messages_db_path else None
        self.tasks_reader = TasksReader(tasks_db_path) if tasks_db_path else None
        
        self.status = StatusReader(cycle_id_path)
        self.system = SystemReader()
        self.config = ConfigReader(config_path) if config_path else None

    def compose(self) -> ComposeResult:
        """Create the dashboard layout."""
        yield Header()

        with VerticalScroll(id="dashboard"):
            yield SystemPanel(self.system, self.config, self.status, self.agent_reader, id="system-panel")
            yield AgentsPanel(self.agent_reader, self.system, id="agents-panel")
            yield FooterStatsPanel(self.agent_reader, tasks_reader=self.tasks_reader, id="footer-stats-panel")
            with TabbedContent(initial="projects-tab", id="work-tabs"):
                with TabPane("◆  Projects", id="projects-tab"):
                    yield ProjectsPanel(self.tasks_reader, id="projects-panel")
                with TabPane("◎  Tasks", id="tasks-tab"):
                    yield TasksPanel(self.tasks_reader, message_reader=self.message_reader, id="tasks-panel")
                with TabPane("✉  Messages", id="messages-tab"):
                    yield MessagesPanel(self.message_reader, config=self.config, agent_reader=self.agent_reader, id="messages-panel")

        yield Footer()

    def on_mount(self) -> None:
        """Start periodic refresh timer and background install registration tripwire."""
        status = self._run_registration_tripwire()
        if self._should_prompt_registration(status):
            display_status = self._fetch_registration_status() or status
            self.call_after_refresh(
                lambda: self._open_registration_modal(display_status)
            )
        self._start_refresh_timer()

    def _should_prompt_registration(self, status: dict) -> bool:
        if not status:
            return False
        if status.get("registration_submitted", "false").lower() != "true":
            return True
        if status.get("update_available"):
            return True
        if status.get("below_min_supported"):
            return True
        return False

    def _open_registration_modal(self, status: dict) -> None:
        from agitop.panels.registration_modal import RegistrationModal

        self.push_screen(RegistrationModal(status))

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
        self.query_one("#system-panel", SystemPanel).refresh_data()
        self.query_one("#agents-panel", AgentsPanel).refresh_data()
        self.query_one("#projects-panel", ProjectsPanel).refresh_data()
        self.query_one("#messages-panel", MessagesPanel).refresh_data()
        self.query_one("#tasks-panel", TasksPanel).refresh_data()
        self.query_one("#footer-stats-panel", FooterStatsPanel).refresh_data()

    def action_update_refresh_interval(self) -> None:
        """Called by SystemPanel when user cycles the refresh rate."""
        self._start_refresh_timer()
        interval_label = SystemPanel.REFRESH_INTERVALS[
            self.query_one("#system-panel", SystemPanel)._refresh_idx
        ][0]
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

    args = parser.parse_args()

    app = AgitopApp(
        agents_db_path=args.agents_db,
        messages_db_path=args.messages_db,
        tasks_db_path=args.tasks_db,
        cycles_db_path=args.cycles_db,
        config_path=args.config,
        cycle_id_path=args.cycle_id,
    )
    app.run()


if __name__ == "__main__":
    main()
