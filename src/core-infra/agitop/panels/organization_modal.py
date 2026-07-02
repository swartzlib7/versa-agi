"""OrganizationModal — full-screen tabbed modal for all Organization entities.

Replaces the per-entity tabs on the dashboard work-tabs. Launched by the
'Organizations' tab on the dashboard. Contains all entity panels (existing +
new: Staff, Emails, Addresses, Credentials) plus the Explorer.
"""

from __future__ import annotations

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, Horizontal
from textual.screen import ModalScreen
from textual.widgets import Button, Static, TabbedContent, TabPane

from agitop.panels.organization import (
    ORGANIZATION_TABS, OrganizationEntityPanel, OrganizationTreePanel,
    _MANAGE_BUTTON,
)


class OrganizationModal(ModalScreen):
    """Tabbed modal containing all Organization entity panels + Explorer."""

    BINDINGS = [Binding("escape", "close", "Close")]

    def __init__(self, reader, writer, tasks_reader=None, **kwargs):
        super().__init__(**kwargs)
        self.org_reader = reader
        self.org_writer = writer
        self.tasks_reader = tasks_reader

    def compose(self) -> ComposeResult:
        with Vertical(id="org-modal-shell"):
            yield Static("[bold]◉ Organizations[/]", id="org-modal-header")
            with TabbedContent(id="org-modal-tabs"):
                for kind, icon, label in ORGANIZATION_TABS:
                    with TabPane(f"{icon}  {label}", id=f"org-modal-{kind}-tab"):
                        yield OrganizationEntityPanel(
                            self.org_reader, kind,
                            writer=self.org_writer,
                            tasks_reader=self.tasks_reader,
                            id=f"org-{kind}-panel",
                        )
                with TabPane("❖  Explorer", id="org-modal-explorer-tab"):
                    yield OrganizationTreePanel(
                        self.org_reader, id="org-explorer-panel",
                    )

            with Horizontal(id="org-modal-footer"):
                yield Button("＋ New", id="org-modal-new", variant="success")
                yield Button("✎ Edit", id="org-modal-edit", variant="primary")
                yield Button("Lines", id="org-modal-lines", variant="primary")
                yield Button("Manage Types", id="org-modal-managetypes", variant="default")
                yield Button("✖ Delete", id="org-modal-delete", variant="error")
                yield Button("Close", classes="dismiss-btn", variant="default",
                             id="org-modal-close")

    def on_mount(self) -> None:
        # Initialize button visibility for the default active tab
        tabs = self.query_one("#org-modal-tabs", TabbedContent)
        self._update_buttons(tabs.active)

    @on(TabbedContent.TabActivated)
    def _on_tab_activated(self, event: TabbedContent.TabActivated) -> None:
        self._update_buttons(event.tab.id)

    def _update_buttons(self, tab_id: str) -> None:
        is_explorer = "explorer" in tab_id
        kind = None
        for k, _, _ in ORGANIZATION_TABS:
            if f"-{k}-tab" in tab_id:
                kind = k
                break

        btn_new = self.query_one("#org-modal-new", Button)
        btn_edit = self.query_one("#org-modal-edit", Button)
        btn_delete = self.query_one("#org-modal-delete", Button)
        btn_lines = self.query_one("#org-modal-lines", Button)
        btn_types = self.query_one("#org-modal-managetypes", Button)

        if is_explorer or not kind:
            btn_new.display = False
            btn_edit.display = False
            btn_delete.display = False
            btn_lines.display = False
            btn_types.display = False
        else:
            btn_new.display = True
            btn_edit.display = True
            btn_delete.display = True
            btn_lines.display = kind in ("invoices", "estimates")
            btn_types.display = kind in _MANAGE_BUTTON

    def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id
        if bid == "org-modal-close":
            self.dismiss(None)
            return

        tabs = self.query_one("#org-modal-tabs", TabbedContent)
        active_tab_id = tabs.active



        is_explorer = "explorer" in active_tab_id
        if is_explorer:
            return

        kind = None
        for k, _, _ in ORGANIZATION_TABS:
            if f"-{k}-tab" in active_tab_id:
                kind = k
                break

        if kind:
            panel = self.query_one(f"#org-{kind}-panel", OrganizationEntityPanel)
            action = bid.replace("org-modal-", "")
            panel.trigger_action(action)

    def action_close(self) -> None:
        self.dismiss(None)
