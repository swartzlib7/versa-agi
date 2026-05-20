"""System Settings Modal — configure Circuit Breaker, Web Search, COA Autonomous Mode via agitop."""
"""Includes read-only Skill Viewer modal for inspecting skill file contents."""

import os
import re
import subprocess
import sqlite3
from textual.screen import ModalScreen
from textual.app import ComposeResult
from textual.containers import Vertical, Horizontal, VerticalScroll
from textual.widgets import Static, Button, Input, Checkbox, DataTable, Markdown


_SETUP_INI_PATHS = [
    "/etc/versa-agi/setup.ini",
    os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))), "setup.ini"),
]


def _find_setup_ini() -> str:
    for p in _SETUP_INI_PATHS:
        if os.path.exists(p):
            return os.path.realpath(p)
    return _SETUP_INI_PATHS[0]


def _read_ini_value(section: str, key: str, default: str = "") -> str:
    """Read a value from setup.ini without losing comments (awk-based)."""
    path = _find_setup_ini()
    if not os.path.exists(path):
        return default
    try:
        result = subprocess.run(
            ["awk", "-F", "=",
             "-v", f"section={section}",
             "-v", f"key={key}",
             r'/^\[/ { current = substr($0, 2, index($0,"]")-2) }'
             r' current == section && $1 ~ "^\\s*"key"\\s*$" {'
             r' val = substr($0, index($0,"=")+1);'
             r' gsub(/^[[:space:]]+|[[:space:]]+$/, "", val);'
             r' print val; exit }',
             path],
            capture_output=True, text=True, timeout=5
        )
        val = result.stdout.strip()
        return val if val else default
    except Exception:
        return default


def _write_ini_value(section: str, key: str, value: str) -> bool:
    """Write a value to setup.ini using sed — preserves all comments and formatting."""
    path = _find_setup_ini()
    if not os.path.exists(path):
        return False
    # Use sed to replace the key=value line in-place (handles key = value and key=value)
    # Pattern: within [section], replace key line
    sed_expr = f'/^\\[{re.escape(section)}\\]/,/^\\[/ s/^\\s*{re.escape(key)}\\s*=.*/{key}={value}/'
    try:
        result = subprocess.run(
            ["sed", "-i", sed_expr, path],
            capture_output=True, text=True, timeout=5
        )
        ok = result.returncode == 0
    except PermissionError:
        # Try with sudo
        try:
            result = subprocess.run(
                ["sudo", "sed", "-i", sed_expr, path],
                capture_output=True, text=True, timeout=5
            )
            ok = result.returncode == 0
        except Exception:
            ok = False
    except Exception:
        ok = False

    # Sync written INI to the alternate copy (source ↔ deployed)
    if ok:
        _sync_ini_copies(path)
    return ok


def _sync_ini_copies(written_path: str) -> None:
    """Copy written setup.ini to the alternate location (source ↔ deployed)."""
    import shutil
    targets = [p for p in _SETUP_INI_PATHS if os.path.realpath(p) != os.path.realpath(written_path)]
    for target in targets:
        if os.path.exists(target):
            try:
                shutil.copy2(written_path, target)
            except Exception:
                pass  # Non-fatal


def _get_skills_rows() -> list[dict]:
    """Read all skills from agents.db for table display."""
    db_path = "/var/lib/versa-agi/agents.db"
    if not os.path.exists(db_path):
        return []
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT name, type, origin, has_assets, status, description "
            "FROM skills ORDER BY type DESC, name"
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception:
        return []


def _read_skill_file(skill_name: str) -> str:
    """Read the full content of a skill .md file from the canonical source."""
    skill_path = f"/home/watchdog/core-infra/skills/{skill_name}.md"
    if not os.path.exists(skill_path):
        return f"(Skill file not found: {skill_path})"
    try:
        with open(skill_path, "r") as f:
            return f.read()
    except PermissionError:
        # Try with sudo cat
        try:
            result = subprocess.run(
                ["sudo", "cat", skill_path],
                capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0:
                return result.stdout
            return f"(Permission denied: {skill_path})"
        except Exception as e:
            return f"(Error reading skill: {e})"
    except Exception as e:
        return f"(Error: {e})"


class SkillViewModal(ModalScreen):
    """Read-only modal for viewing skill file contents."""

    def __init__(self, skill_data: dict, **kwargs):
        super().__init__(**kwargs)
        self.skill_data = skill_data

    def compose(self) -> ComposeResult:
        name = self.skill_data.get("name", "")
        skill_type = self.skill_data.get("type", "")
        origin = self.skill_data.get("origin", "")
        status = self.skill_data.get("status", "")
        has_assets = "Yes" if self.skill_data.get("has_assets") else "No"
        desc = self.skill_data.get("description", "")

        header = (
            f"[bold]📖 {name}[/]\n"
            f"[cyan]Type:[/] {skill_type}  [cyan]Origin:[/] {origin}  "
            f"[cyan]Status:[/] {status}  [cyan]Assets:[/] {has_assets}\n"
            f"[cyan]Description:[/] {desc}"
        )

        content = _read_skill_file(name)

        with Vertical(id="skill-dialog"):
            yield Static(header, id="skill-dialog-header")
            with VerticalScroll(id="skill-content-scroll"):
                yield Markdown(content, id="skill-content-body")
            with Horizontal(id="skill-dialog-actions"):
                yield Button("Close", variant="primary", id="skill-view-close")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "skill-view-close":
            self.app.pop_screen()


class SystemSettingsModal(ModalScreen):
    """Modal for configuring system-level settings."""

    def compose(self) -> ComposeResult:
        # Circuit Breaker
        cb_consecutive = _read_ini_value("agent", "circuit_breaker_consecutive", "5")
        cb_hourly = _read_ini_value("agent", "circuit_breaker_hourly", "20")

        # Web Search
        search_enabled = _read_ini_value("search", "enabled", "false").lower() == "true"
        searxng_url = _read_ini_value("search", "searxng_url", "http://localhost:8888")

        # COA Autonomous Mode
        coa_autonomous = _read_ini_value("coa", "autonomous", "false").lower() == "true"

        # Skills data
        self._skills_rows = _get_skills_rows()

        with Vertical(id="msg-dialog"):
            with VerticalScroll(id="settings-scroll"):
                yield Static("[bold]⚙ System Settings[/]", id="msg-dialog-header")

                # ── Circuit Breaker ──
                yield Static("[bold cyan]Circuit Breaker[/] — prevents runaway API cost from repeated spawn failures")
                yield Static("")
                yield Static("[cyan]Consecutive Failure Threshold[/] — freeze agent after N consecutive failures")
                yield Input(value=cb_consecutive, placeholder="e.g. 5", id="input-cb-consecutive", type="integer")
                yield Static("[cyan]Hourly Failure Threshold[/] — freeze agent after N failures in one hour")
                yield Input(value=cb_hourly, placeholder="e.g. 20", id="input-cb-hourly", type="integer")
                yield Static("[dim]Only exit codes 1 (error), 42 (input error), 99 (runaway) trigger the breaker.[/]")
                yield Static("[dim]Exit codes 0 (success), 53 (turn limit), 124 (timeout) are excluded.[/]")

                # ── Web Search ──
                yield Static("")
                yield Static("[bold cyan]Web Search[/] — local SearXNG integration for agent research")
                yield Checkbox("Enabled", id="chk-search-enabled", value=search_enabled)
                yield Static("[cyan]SearXNG URL[/]")
                yield Input(value=searxng_url, placeholder="http://localhost:8888", id="input-searxng-url")
                yield Static("[dim]Requires a running SearXNG instance. Agents use 'agictl search web' to query.[/]")

                # ── COA Autonomous Mode ──
                yield Static("")
                yield Static("[bold cyan]COA Autonomous Mode[/] — full sudo access for dedicated hardware")
                yield Checkbox("Autonomous (NOPASSWD: ALL)", id="chk-coa-autonomous", value=coa_autonomous)
                yield Static("[bold yellow]⚠ Only enable on hardware gifted exclusively to the system[/]")
                yield Static("[dim]Grants COA full root access. Disabled by default. Applied immediately on Save.[/]")

                # ── Skills Registry ──
                yield Static("")
                yield Static(f"[bold cyan]Skills Registry[/] — {len(self._skills_rows)} skill(s) registered")
                skills_table = DataTable(id="skills-registry-table")
                yield skills_table
                yield Static("[dim]Double-click or press Enter to view a skill · Manage via: agictl skill list[/]")

            with Horizontal(id="msg-dialog-actions"):
                yield Button("Save", variant="success", id="btn-save-settings")
                yield Button("Cancel", variant="default", id="msg-dialog-close")

    def on_mount(self) -> None:
        """Populate the skills DataTable after mount."""
        try:
            table = self.query_one("#skills-registry-table", DataTable)
            table.cursor_type = "row"
            table.add_columns("Name", "Type", "Origin", "Assets", "Status", "Description")
            for idx, r in enumerate(self._skills_rows):
                assets = "✓" if r.get("has_assets") else "—"
                desc = (r.get("description") or "")[:45]
                table.add_row(
                    r.get("name", ""),
                    r.get("type", ""),
                    r.get("origin", ""),
                    assets,
                    r.get("status", ""),
                    desc,
                    key=str(idx),
                )
        except Exception:
            pass


    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        """Also open viewer on double-click/Enter."""
        if event.data_table.id != "skills-registry-table":
            return
        try:
            idx = int(event.row_key.value)
            skill_data = self._skills_rows[idx]
            self.app.push_screen(SkillViewModal(skill_data))
        except (ValueError, IndexError):
            pass

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-save-settings":
            try:
                # ── Circuit Breaker ──
                cb_consecutive = int(self.query_one("#input-cb-consecutive", Input).value)
                cb_hourly = int(self.query_one("#input-cb-hourly", Input).value)

                if cb_consecutive < 1 or cb_hourly < 1:
                    self.app.notify("Thresholds must be ≥ 1", severity="error")
                    return

                ok1 = _write_ini_value("agent", "circuit_breaker_consecutive", str(cb_consecutive))
                ok2 = _write_ini_value("agent", "circuit_breaker_hourly", str(cb_hourly))

                # ── Web Search ──
                search_enabled = self.query_one("#chk-search-enabled", Checkbox).value
                searxng_url = self.query_one("#input-searxng-url", Input).value.strip()
                ok3 = _write_ini_value("search", "enabled", "true" if search_enabled else "false")
                ok4 = _write_ini_value("search", "searxng_url", searxng_url) if searxng_url else True

                # ── COA Autonomous ──
                coa_autonomous = self.query_one("#chk-coa-autonomous", Checkbox).value
                ok5 = _write_ini_value("coa", "autonomous", "true" if coa_autonomous else "false")

                # Apply sudoers immediately (no setup.sh re-run needed)
                sudoers_file = "/etc/sudoers.d/versa_agi_coa_autonomous"
                try:
                    if coa_autonomous:
                        # Grant full sudo to COA user
                        subprocess.run(
                            ["sudo", "bash", "-c",
                             f'echo "coa ALL=(ALL) NOPASSWD: ALL" > {sudoers_file} && chmod 440 {sudoers_file}'],
                            capture_output=True, timeout=10
                        )
                    else:
                        # Remove autonomous sudoers if it exists
                        subprocess.run(
                            ["sudo", "rm", "-f", sudoers_file],
                            capture_output=True, timeout=10
                        )
                except Exception:
                    pass  # Non-fatal — sudoers change is best-effort from dashboard

                if all([ok1, ok2, ok3, ok4, ok5]):
                    summary_parts = [
                        f"Circuit breaker: {cb_consecutive}/{cb_hourly}",
                        f"Search: {'on' if search_enabled else 'off'}",
                        f"Autonomous: {'on' if coa_autonomous else 'off'}",
                    ]
                    self.app.notify(
                        " · ".join(summary_parts) + " — active next CRON tick",
                        title="Settings Saved"
                    )
                else:
                    self.app.notify("Some settings failed to save — check permissions", severity="warning")
            except ValueError:
                self.app.notify("Invalid input — thresholds must be whole numbers", severity="error")
            self.app.pop_screen()
        elif event.button.id == "msg-dialog-close":
            self.app.pop_screen()
