"""Registration status modal — install telemetry and release updates."""

from __future__ import annotations

from pathlib import Path

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Static

STATUS_JSON = Path("/var/lib/versa-agi/registration-status.json")


def load_registration_status() -> dict:
    """Load status JSON; prefer refresh_for_display when available."""
    try:
        import sys

        core_infra = Path(__file__).resolve().parents[2]
        if str(core_infra) not in sys.path:
            sys.path.insert(0, str(core_infra))
        from install_acceptance import refresh_for_display

        return refresh_for_display()
    except Exception:
        pass

    if not STATUS_JSON.is_file():
        return {}
    try:
        import json

        return json.loads(STATUS_JSON.read_text(encoding="utf-8"))
    except Exception:
        return {}


class RegistrationModal(ModalScreen):
    """Show install registration status and update availability."""

    def __init__(self, status: dict | None = None):
        super().__init__()
        self.status = status or load_registration_status()

    def compose(self) -> ComposeResult:
        with Vertical(id="registration-dialog"):
            yield Static(self._body_text(), id="registration-body", markup=True)
            yield Button("Dismiss", variant="primary", id="registration-dismiss")

    def _body_text(self) -> str:
        s = self.status
        installed = s.get("installed_version") or "unknown"
        latest = s.get("latest_version") or "—"
        min_supported = s.get("min_supported_version") or "—"
        submitted_raw = s.get("registration_submitted", "false")
        submitted = str(submitted_raw).lower() == "true"
        message = s.get("message", "")
        last_error = s.get("registration_last_error", "")

        lines = [
            "[b]Versa AGi Registration[/b]",
            "",
            f"Installed: [cyan]{installed}[/cyan]",
            f"Latest: {latest}",
            f"Minimum: {min_supported}",
            f"Registered: {'yes' if submitted else 'no'}",
        ]

        if s.get("update_available"):
            lines.extend([
                "",
                "[yellow]A newer version is available.[/yellow]",
                "[b]sudo ./setup.sh --update[/b]",
                "Production: auto-pulls from ~/.versa-agi/repo/src",
                "Dev clones: update from local source only",
            ])

        if s.get("below_min_supported"):
            lines.extend([
                "",
                "[red]Below minimum supported release.[/red]",
                "Install the latest Versa AGi to continue.",
            ])

        if message and message not in lines:
            lines.extend(["", message])

        if last_error and not submitted:
            lines.extend(["", f"Last error: {last_error}"])

        return "\n".join(lines)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "registration-dismiss":
            self.dismiss()
