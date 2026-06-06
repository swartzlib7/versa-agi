"""System Settings Modal — configure Circuit Breaker, Web Search, COA Autonomous Mode via agitop."""
"""Includes read-only Skill Viewer modal for inspecting skill file contents."""

import os
import re
import subprocess
import sqlite3
import threading
from textual.screen import ModalScreen
from textual.app import ComposeResult
from textual.containers import Vertical, Horizontal, VerticalScroll, Container
from textual.widgets import Static, Button, Input, Checkbox, DataTable, Markdown


_SETUP_INI_PATHS = [
    "/etc/versa-agi/setup.ini",
    os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))), "setup.ini"),
]

PATHS_ENV_FILE = "/etc/versa-agi/paths.env"


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


def _update_paths_env_key(key: str, value: str) -> bool:
    """Update a single key in paths.env (in-place). Creates if missing."""
    if not os.path.isfile(PATHS_ENV_FILE):
        return False
    try:
        with open(PATHS_ENV_FILE, "r") as f:
            lines = f.readlines()
        entry = f'{key}="{value}"\n'
        found = False
        for i, line in enumerate(lines):
            if line.strip().startswith(f"{key}="):
                lines[i] = entry
                found = True
                break
        if not found:
            lines.append(entry)
        with open(PATHS_ENV_FILE, "w") as f:
            f.writelines(lines)
        return True
    except PermissionError:
        try:
            result = subprocess.run(
                ["sudo", "bash", "-c",
                 f"sed -i 's|^{key}=.*|{key}=\"{value}\"|' {PATHS_ENV_FILE}"],
                capture_output=True, text=True, timeout=5
            )
            return result.returncode == 0
        except Exception:
            return False
    except Exception:
        return False


def _sweep_local_agents_to_model(target_model: str) -> list[tuple[str, str]]:
    """Sweep all locally-assigned agents to a single model.

    Identifies agents whose current model matches any entry in the
    local_models registry (from setup.ini) and updates them to target_model.
    Returns list of (agent_name, old_model) tuples for affected agents.
    """
    agents_db = "/var/lib/versa-agi/agents.db"
    if not os.path.isfile(agents_db):
        return []

    # Build the set of all known local model names
    local_csv = _read_ini_value("local_ai", "local_models", "")
    local_names = set()
    if local_csv:
        local_names = {m.strip() for m in local_csv.split(",") if m.strip()}
    # Also include the previously active model (may not be in local_models)
    prev_active = _read_ini_value("local_ai", "sycl_active_model", "")
    if prev_active:
        local_names.add(prev_active)
    # Include the target itself
    local_names.add(target_model)

    if not local_names:
        return []

    affected = []
    try:
        conn = sqlite3.connect(agents_db)
        cursor = conn.cursor()
        placeholders = ",".join("?" * len(local_names))
        cursor.execute(
            f"SELECT name, model FROM agents WHERE model IN ({placeholders})",
            list(local_names),
        )
        rows = cursor.fetchall()
        for agent_name, old_model in rows:
            if old_model != target_model:
                affected.append((agent_name, old_model))
        if affected:
            cursor.execute(
                f"UPDATE agents SET model = ? WHERE model IN ({placeholders})",
                [target_model] + list(local_names),
            )
            conn.commit()
        conn.close()
    except Exception:
        pass
    return affected


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


class RouterModeConfirmModal(ModalScreen):
    """Confirmation modal for toggling model loading strategy (single ↔ router).

    Writes setup.ini and paths.env, then restarts the Docker container
    with the appropriate launch arguments. Shows live progress feedback.
    """

    def __init__(self, current_strategy: str, **kwargs):
        super().__init__(**kwargs)
        self.current_strategy = current_strategy
        self.target_strategy = "router" if current_strategy == "single" else "single"
        self._switching = False
        self._last_status_text = ""

    def compose(self) -> ComposeResult:
        target_label = "Router" if self.target_strategy == "router" else "Single"
        current_label = "Router" if self.current_strategy == "router" else "Single"

        if self.target_strategy == "router":
            desc = (
                "All downloaded models become available simultaneously.\n"
                "The server loads and unloads models on demand from VRAM.\n"
                "No Docker restart needed when switching between models.\n"
                "Agents can each use a different local model."
            )
        else:
            desc = (
                "Only one model is loaded in VRAM at a time.\n"
                "Switching models requires a Docker restart.\n"
                "All agents using local models share the active model."
            )

        with Vertical(id="router-dialog"):
            yield Static(f"[bold yellow]⚠ Model Loading Strategy Change[/]\n", id="router-title")
            yield Static(
                f"Switch from [bold]{current_label}[/] → [bold cyan]{target_label}[/] mode.\n\n"
                f"{desc}\n\n"
                f"[bold]This will restart the inference Docker container.[/]\n",
                id="router-info",
            )
            yield Static("[dim]Ready to switch.[/]", id="router-status")
            with Horizontal(id="router-actions"):
                yield Button(f"Switch to {target_label}", variant="warning", id="btn-router-confirm")
                yield Button("Copy", variant="default", id="btn-router-copy")
                yield Button("Cancel", variant="default", id="btn-router-cancel")

    def _set_status(self, text: str) -> None:
        self._last_status_text = re.sub(r'\[/?[^\]]*\]', '', text)
        try:
            self.query_one("#router-status", Static).update(text)
        except Exception:
            pass

    def _set_buttons_disabled(self, disabled: bool) -> None:
        try:
            self.query_one("#btn-router-confirm", Button).disabled = disabled
            self.query_one("#btn-router-cancel", Button).disabled = disabled
        except Exception:
            pass

    def _run_switch(self) -> None:
        """Execute the strategy switch in a background thread."""

        def _execute():
            try:
                # ── Step 1: Update setup.ini ──
                self.app.call_from_thread(
                    self._set_status,
                    "[bold yellow]◐ Updating setup.ini...[/]"
                )
                ok1 = _write_ini_value("local_ai", "model_loading_strategy", self.target_strategy)
                if not ok1:
                    self.app.call_from_thread(
                        self._on_switch_failed,
                        "Failed to update setup.ini — check file permissions."
                    )
                    return

                # ── Step 2: Update paths.env ──
                self.app.call_from_thread(
                    self._set_status,
                    "[bold yellow]◑ Updating paths.env...[/]"
                )
                ok2 = _update_paths_env_key("VERSA_MODEL_LOADING_STRATEGY", self.target_strategy)
                if not ok2:
                    self.app.call_from_thread(
                        self._on_switch_failed,
                        "Failed to update paths.env — check file permissions."
                    )
                    return

                # If switching to single mode, set the active model and sweep agents
                if self.target_strategy == "single":
                    active = _read_ini_value("local_ai", "sycl_active_model", "")
                    if active:
                        _update_paths_env_key("VERSA_ACTIVE_LOCAL_MODEL", active)

                    # ── Step 2b: Sweep all local agents to the active model ──
                    if active:
                        self.app.call_from_thread(
                            self._set_status,
                            "[bold yellow]◒ Syncing agents to single model...[/]"
                        )
                        sweep_result = _sweep_local_agents_to_model(active)
                        if sweep_result:
                            agent_list = ", ".join(
                                f"{name} ({old})" for name, old in sweep_result
                            )
                            self.app.call_from_thread(
                                self._set_status,
                                f"[bold yellow]◓ Synced {len(sweep_result)} agent(s): {agent_list}[/]"
                            )
                        self._sweep_count = len(sweep_result)
                    else:
                        self._sweep_count = 0
                else:
                    _update_paths_env_key("VERSA_ACTIVE_LOCAL_MODEL", "")
                    self._sweep_count = 0

                # ── Step 3: Restart Docker container (only if running) ──
                # Strategy toggle is a config-level change. Docker restart is
                # only needed on server/local topology where the SYCL container
                # is actually running. On client/dev machines, config save is
                # the complete action.
                container_running = False
                try:
                    check = subprocess.run(
                        ["docker", "ps", "--format", "{{.Names}}"],
                        capture_output=True, text=True, timeout=5,
                    )
                    container_running = "versa-agi-sycl" in check.stdout
                except Exception:
                    pass  # Docker not installed or not accessible

                if container_running:
                    self.app.call_from_thread(
                        self._set_status,
                        "[bold yellow]◐ Restarting Docker container...[/]\n"
                        "[dim]This may take a few seconds.[/]"
                    )
                    # Use agictl system-level restart (no model validation)
                    result = subprocess.run(
                        ["sudo", "docker", "restart", "versa-agi-sycl"],
                        capture_output=True, text=True, timeout=120,
                    )
                    if result.returncode != 0:
                        error_detail = (result.stderr or "Unknown error").strip()[:300]
                        self.app.call_from_thread(
                            self._on_switch_partial,
                            f"Strategy saved but Docker restart failed:\n{error_detail}"
                        )
                        return
                else:
                    # No container running — config-only change is complete
                    self.app.call_from_thread(
                        self._set_status,
                        "[bold yellow]◑ No SYCL container detected — config saved.[/]"
                    )

                self.app.call_from_thread(self._on_switch_success)

            except subprocess.TimeoutExpired:
                self.app.call_from_thread(
                    self._on_switch_failed,
                    "Operation timed out after 120 seconds."
                )
            except Exception as e:
                self.app.call_from_thread(self._on_switch_failed, str(e))

        thread = threading.Thread(target=_execute, daemon=True)
        thread.start()

    def _on_switch_success(self) -> None:
        target_label = "Router" if self.target_strategy == "router" else "Single"
        sweep_info = ""
        sweep_count = getattr(self, "_sweep_count", 0)
        if self.target_strategy == "single" and sweep_count > 0:
            sweep_info = f"\n  {sweep_count} agent(s) synced to active model."
        self._set_status(
            f"[bold green]✓ Strategy switched to {target_label} mode[/]\n"
            f"  Configuration and Docker container updated.{sweep_info}"
        )
        self.app.notify(
            f"Model loading: {target_label} mode — active immediately",
            title="Strategy Changed",
        )
        self._switching = False
        self._set_buttons_disabled(False)
        try:
            self.query_one("#btn-router-confirm", Button).remove()
            cancel_btn = self.query_one("#btn-router-cancel", Button)
            cancel_btn.label = "Close"
            cancel_btn.variant = "primary"
        except Exception:
            pass

    def _on_switch_partial(self, message: str) -> None:
        """Config saved but Docker had issues — non-fatal."""
        target_label = "Router" if self.target_strategy == "router" else "Single"
        self._set_status(
            f"[bold yellow]⚠ Strategy set to {target_label} — Docker needs attention[/]\n\n"
            f"{message}\n\n"
            f"[dim]Config is saved. Restart Docker manually or run:[/]\n"
            f"[bold]sudo agictl model activate <model>[/]"
        )
        self.app.notify(
            f"Strategy updated to {target_label} — Docker may need manual restart",
            title="Partial Success", severity="warning",
        )
        self._switching = False
        self._set_buttons_disabled(False)
        try:
            self.query_one("#btn-router-confirm", Button).remove()
            cancel_btn = self.query_one("#btn-router-cancel", Button)
            cancel_btn.label = "Close"
            cancel_btn.variant = "primary"
        except Exception:
            pass

    def _on_switch_failed(self, error_msg: str) -> None:
        self._set_status(
            f"[bold red]✗ Strategy Switch Failed[/]\n\n{error_msg}\n\n"
            f"[dim]You can retry or cancel.[/]"
        )
        self._switching = False
        self._set_buttons_disabled(False)
        try:
            self.query_one("#btn-router-confirm", Button).label = "Retry"
        except Exception:
            pass

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-router-confirm":
            if self._switching:
                return
            self._switching = True
            self._set_buttons_disabled(True)
            self._run_switch()
        elif event.button.id == "btn-router-copy":
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
        elif event.button.id == "btn-router-cancel":
            if self._switching:
                return
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

        # AI Mode
        ai_mode = _read_ini_value("gemini", "mode", "cloud")

        # COA Autonomous Mode
        coa_autonomous = _read_ini_value("coa", "autonomous", "false").lower() == "true"

        # Model Loading Strategy
        loading_strategy = _read_ini_value("local_ai", "model_loading_strategy", "single")
        gpu_backend = _read_ini_value("local_ai", "gpu_backend", "standard")
        local_ai_enabled = _read_ini_value("local_ai", "enabled", "false").lower() == "true"
        self._show_strategy = local_ai_enabled and gpu_backend in ("intel", "remote")

        # Skills data
        self._skills_rows = _get_skills_rows()

        with Vertical(id="settings-dialog"):
            with VerticalScroll(id="settings-scroll"):
                yield Static("[bold]⚙ System Settings[/]", id="settings-dialog-header")

                # ── Two-Column Grid ──
                with Container(id="settings-columns"):
                    # ── Left Column ──
                    with Vertical(classes="settings-col"):
                        # AI Mode (read-only)
                        _mode_labels = {"cloud": "Cloud", "local": "Local", "hybrid": "Hybrid"}
                        _mode_label = _mode_labels.get(ai_mode, ai_mode)
                        yield Static(f"[bold cyan]AI Mode[/]  [bold]{_mode_label}[/]")
                        yield Static("[dim]Edit setup.ini [gemini] mode + run: sudo ./setup.sh --update[/]")

                        # Circuit Breaker
                        yield Static("")
                        yield Static("[bold cyan]Circuit Breaker[/]")
                        yield Static("[dim]Prevents runaway API cost from repeated spawn failures[/]")
                        yield Static("")
                        yield Static("[cyan]Consecutive Failure Threshold[/]")
                        yield Input(value=cb_consecutive, placeholder="e.g. 5", id="input-cb-consecutive", type="integer")
                        yield Static("[cyan]Hourly Failure Threshold[/]")
                        yield Input(value=cb_hourly, placeholder="e.g. 20", id="input-cb-hourly", type="integer")
                        yield Static("[dim]Only exit codes 1 (error), 42 (input error), 99 (runaway) trigger the breaker.[/]")

                    # ── Right Column ──
                    with Vertical(classes="settings-col"):
                        # Model Loading Strategy (Intel SYCL only)
                        if self._show_strategy:
                            _strat_label = "Router" if loading_strategy == "router" else "Single"
                            _strat_color = "green" if loading_strategy == "router" else "yellow"
                            yield Static(f"[bold cyan]Model Loading[/]  [bold {_strat_color}]{_strat_label}[/]")
                            if loading_strategy == "router":
                                yield Static("[dim]All models available on demand — no Docker restart to switch[/]")
                            else:
                                yield Static("[dim]One model in VRAM — Docker restart required to switch[/]")
                            yield Button(
                                f"Switch to {'Single' if loading_strategy == 'router' else 'Router'} Mode",
                                variant="warning", id="btn-toggle-strategy",
                            )
                            yield Static("")

                        # Web Search
                        yield Static("[bold cyan]Web Search[/]")
                        yield Static("[dim]Local SearXNG integration for agent research[/]")
                        yield Checkbox("Enabled", id="chk-search-enabled", value=search_enabled)
                        yield Static("[cyan]SearXNG URL[/]")
                        yield Input(value=searxng_url, placeholder="http://localhost:8888", id="input-searxng-url")

                        # COA Autonomous Mode
                        yield Static("")
                        yield Static("[bold cyan]COA Autonomous Mode[/]")
                        yield Checkbox("Autonomous (NOPASSWD: ALL)", id="chk-coa-autonomous", value=coa_autonomous)
                        yield Static("[bold yellow]⚠ Only enable on dedicated hardware[/]")

                # ── Skills Registry (full width below grid) ──
                yield Static("")
                yield Static(f"[bold cyan]Skills Registry[/] — {len(self._skills_rows)} skill(s) registered")
                skills_table = DataTable(id="skills-registry-table")
                yield skills_table
                yield Static("[dim]Double-click or press Enter to view a skill · Manage via: agictl skill list[/]")

            with Horizontal(id="settings-dialog-actions"):
                yield Button("Save", variant="success", id="btn-save-settings")
                yield Button("Cancel", variant="default", id="btn-settings-close")

    def on_mount(self) -> None:
        """Populate the skills DataTable after mount."""
        try:
            table = self.query_one("#skills-registry-table", DataTable)
            table.cursor_type = "row"
            table.add_columns("Name", "Type", "Origin", "Assets", "Status", "Description")
            for idx, r in enumerate(self._skills_rows):
                assets = "✓" if r.get("has_assets") else "—"
                desc = r.get("description") or ""
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
        if event.button.id == "btn-toggle-strategy":
            current = _read_ini_value("local_ai", "model_loading_strategy", "single")
            self.app.push_screen(RouterModeConfirmModal(current))
        elif event.button.id == "btn-save-settings":
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
        elif event.button.id == "btn-settings-close":
            self.app.pop_screen()
