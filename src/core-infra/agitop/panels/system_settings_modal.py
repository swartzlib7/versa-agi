"""System Settings Modal — configure Task Management, Circuit Breaker, Web Search, COA Autonomous Mode via agitop."""

import os
import sys
_CORE_INFRA = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _CORE_INFRA not in sys.path:
    sys.path.insert(0, _CORE_INFRA)
import db_connect  # noqa: E402
"""Includes read-only Skill Viewer modal for inspecting skill file contents."""

import json
import os
import re
import subprocess
import sqlite3
import threading
from textual import on
from textual.screen import ModalScreen
from textual.app import ComposeResult
from textual.containers import Vertical, Horizontal, VerticalScroll
from textual.widgets import Static, Button, Input, Checkbox, DataTable, Markdown, TextArea, TabbedContent, TabPane, Select, RichLog
from agitop.widgets.clear_checkbox import ClearCheckbox

from agitop.widgets import PaginatedDataTable
from agitop.feature_flags import UTILITY_MODELS_UI_VISIBLE


_SETUP_INI_PATHS = [
    "/etc/versa-agi/setup.ini",
    os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))), "setup.ini"),
]

PATHS_ENV_FILE = "/etc/versa-agi/paths.env"

_SYS_MEMORY_PAGE_SIZE = 52


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


def _write_ini_value_err(section: str, key: str, value: str) -> tuple[bool, str]:
    """Write a value to setup.ini via agictl set-ini.

    Returns (ok, error). The error carries the agictl validation/permission
    message so callers can surface the actual reason (e.g. a routing model that
    is not COA-approved) instead of a misleading generic failure.
    """
    import json as _json
    try:
        result = subprocess.run(
            ["sudo", "-u", "watchdog", "/usr/local/lib/versa-agi/agictl", "system", "config", "set-ini", section, key, str(value)],
            capture_output=True, text=True, timeout=10
        )
        out = (result.stdout or "").strip()
        data = {}
        if out:
            try:
                data = _json.loads(out)
            except Exception:
                data = {}
        if result.returncode == 0 and data.get("success", False):
            _sync_ini_copies(_find_setup_ini())
            return True, ""
        err = (
            data.get("error")
            or (result.stderr or "").strip()
            or f"set-ini failed (exit {result.returncode})"
        )
        return False, err
    except Exception as e:
        return False, str(e)


def _write_ini_value(section: str, key: str, value: str) -> bool:
    """Write a value to setup.ini using agictl system config set-ini."""
    ok, _ = _write_ini_value_err(section, key, value)
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
        conn = db_connect.connect_compat(agents_db)
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
        conn = db_connect.connect_compat(db_path)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT name, type, origin, has_assets, status, description "
            "FROM skills ORDER BY type DESC, name"
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception:
        return []


def _get_packages_rows() -> list[dict]:
    """Read all system packages from agents.db for table display."""
    db_path = "/var/lib/versa-agi/agents.db"
    if not os.path.exists(db_path):
        return []
    try:
        conn = db_connect.connect_compat(db_path)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT name, status, reason, requested_by, requested_at, resolved_at "
            "FROM system_packages ORDER BY requested_at DESC"
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception:
        return []

def _get_coa_skills_dir() -> str:
    """Resolve COA's .agent/skills directory from the agents registry."""
    try:
        conn = db_connect.connect_compat("file:/var/lib/versa-agi/agents.db?mode=ro", uri=True)
        row = conn.execute("SELECT workspace FROM agents WHERE name='coa'").fetchone()
        conn.close()
        if row and row[0]:
            return os.path.join(row[0], ".agent", "skills")
    except Exception:
        pass
    return "/home/coa/coa-env/.agent/skills"


def _resolve_skill_path(skill_data: dict) -> str:
    """Resolve a skill's canonical source file.

    Shipped/system skills live in the watchdog core-infra source. Agent-created
    and override skills are authored in COA's .agent/skills/.
    """
    name = skill_data.get("name", "")
    coa_path = os.path.join(_get_coa_skills_dir(), f"{name}.md")
    shipped_path = f"/home/watchdog/core-infra/skills/{name}.md"
    skill_type = skill_data.get("type", "")

    if skill_type in ("agent_created", "override"):
        return coa_path
    if skill_type == "system":
        if os.path.exists(shipped_path):
            return shipped_path
        # Registry row may predate a COA-only copy or be stale after rsync cleanup
        if os.path.exists(coa_path):
            return coa_path
        return shipped_path
    return coa_path if os.path.exists(coa_path) else shipped_path


def _read_skill_file(skill_path: str) -> str:
    """Read the full content of a skill .md file from its canonical source."""
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


def _write_skill_file(skill_path: str, content: str) -> tuple:
    """Write skill content back to its source file. Returns (ok, error)."""
    try:
        with open(skill_path, "w") as f:
            f.write(content)
        return True, ""
    except PermissionError:
        # sudo tee preserves the existing inode (owner/permissions unchanged)
        try:
            result = subprocess.run(
                ["sudo", "tee", skill_path],
                input=content, text=True, capture_output=True, timeout=10
            )
            if result.returncode == 0:
                return True, ""
            return False, (result.stderr or "sudo tee failed").strip()
        except Exception as e:
            return False, str(e)
    except Exception as e:
        return False, str(e)


def _mark_skill_updated(skill_name: str) -> None:
    """Flag an edited skill for Lifeline re-distribution (synced → updated)."""
    try:
        conn = db_connect.connect_compat("/var/lib/versa-agi/agents.db", timeout=5)
        conn.execute(
            "UPDATE skills SET status = CASE WHEN status = 'synced' THEN 'updated' ELSE status END, "
            "updated_at = datetime('now') WHERE name = ?",
            (skill_name,)
        )
        conn.commit()
        conn.close()
    except Exception:
        pass


def _delete_skill(skill_data: dict) -> tuple:
    """Remove a skill: source file, assets, distributed copies, registry row.

    Returns (ok, error). Agent-created and override skills may be removed.
    Shipped system skills are managed by setup --update and cannot be deleted.
    """
    name = skill_data.get("name", "")
    skill_type = skill_data.get("type", "")
    if skill_type == "system":
        return False, "Shipped system skills cannot be removed (managed by setup)"
    if skill_type not in ("agent_created", "override"):
        return False, f"Skill type '{skill_type}' cannot be removed from the dashboard"

    try:
        # Source file + co-located asset directory (rm -f tolerates stale
        # registry rows whose file no longer exists)
        src = _resolve_skill_path(skill_data)
        asset_dir = os.path.join(os.path.dirname(src), name)
        subprocess.run(["sudo", "rm", "-f", src], capture_output=True, timeout=10)
        subprocess.run(["sudo", "rm", "-rf", asset_dir], capture_output=True, timeout=10)

        # Distributed copies in sub-agent skill directories
        try:
            conn = db_connect.connect_compat("file:/var/lib/versa-agi/agents.db?mode=ro", uri=True)
            rows = conn.execute(
                "SELECT os_user FROM agents WHERE name NOT IN ('coa', 'watchdog') AND os_user IS NOT NULL"
            ).fetchall()
            conn.close()
        except Exception:
            rows = []
        for (os_user,) in rows:
            agent_skills = f"/home/{os_user}/.agent/skills"
            subprocess.run(["sudo", "rm", "-f", os.path.join(agent_skills, f"{name}.md")],
                           capture_output=True, timeout=10)
            subprocess.run(["sudo", "rm", "-rf", os.path.join(agent_skills, name)],
                           capture_output=True, timeout=10)

        # Registry row
        conn = db_connect.connect_compat("/var/lib/versa-agi/agents.db", timeout=5)
        conn.execute("DELETE FROM skills WHERE name = ?", (name,))
        conn.commit()
        conn.close()
        return True, ""
    except Exception as e:
        return False, str(e)


class SkillRemoveConfirmModal(ModalScreen):
    """Confirmation dialog for removing an agent-created skill."""

    CSS = """
    SkillRemoveConfirmModal {
        align: center middle;
        background: $surface 80%;
    }
    #skill-remove-dialog {
        width: 64;
        height: auto;
        padding: 1 2;
        border: heavy $error;
        background: $surface;
    }
    #skill-remove-actions {
        margin-top: 1;
        height: auto;
        align: center middle;
    }
    #skill-remove-actions Button {
        width: 1fr;
        margin: 0 1;
        min-width: 16;
        height: 3;
    }
    """

    def __init__(self, skill_name: str, **kwargs):
        super().__init__(**kwargs)
        self.skill_name = skill_name

    def compose(self) -> ComposeResult:
        with Vertical(id="skill-remove-dialog"):
            yield Static(f"[bold red]⚠ Remove Skill: {self.skill_name}[/]\n")
            yield Static(
                "This deletes the skill source file, its asset directory,\n"
                "all copies distributed to sub-agents, and the registry entry.\n\n"
                "[bold]This cannot be undone.[/]"
            )
            with Horizontal(id="skill-remove-actions"):
                yield Button("Remove Permanently", variant="error", id="btn-skill-remove-confirm")
                yield Button("Close", classes="dismiss-btn", variant="default", id="btn-skill-remove-cancel")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        self.dismiss(event.button.id == "btn-skill-remove-confirm")


class PackageRemoveConfirmModal(ModalScreen):
    """Confirmation dialog for removing a package from the registry."""

    CSS = """
    PackageRemoveConfirmModal {
        align: center middle;
        background: $surface 80%;
    }
    #pkg-remove-dialog {
        width: 64;
        height: auto;
        padding: 1 2;
        border: heavy $error;
        background: $surface;
    }
    #pkg-remove-actions {
        margin-top: 1;
        height: auto;
        align: center middle;
    }
    #pkg-remove-actions Button {
        width: 1fr;
        margin: 0 1;
        min-width: 16;
        height: 3;
    }
    """

    def __init__(self, pkg_name: str, **kwargs):
        super().__init__(**kwargs)
        self.pkg_name = pkg_name

    def compose(self) -> ComposeResult:
        with Vertical(id="pkg-remove-dialog"):
            yield Static(f"[bold red]⚠ Remove Package: {self.pkg_name}[/]\n")
            yield Static(
                "This removes the package from the system packages registry.\n\n"
                "It does not uninstall the package via apt.\n\n"
                "[bold]This cannot be undone.[/]"
            )
            with Horizontal(id="pkg-remove-actions"):
                yield Button("Remove Permanently", variant="error", id="btn-pkg-remove-confirm")
                yield Button("Close", classes="dismiss-btn", variant="default", id="btn-pkg-remove-cancel")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        self.dismiss(event.button.id == "btn-pkg-remove-confirm")


class UtilityModelRemoveConfirmModal(ModalScreen):
    """Confirmation dialog for removing a Utility Model profile."""

    def __init__(self, um_id: str, um_label: str = "", **kwargs):
        super().__init__(**kwargs)
        self.um_id = um_id
        self.um_label = um_label or um_id

    def compose(self) -> ComposeResult:
        with Vertical(id="um-remove-dialog"):
            yield Static(f"[bold red]⚠ Remove Utility Model: {self.um_id}[/]\n")
            yield Static(
                f"[dim]{self.um_label}[/]\n\n"
                "This permanently deletes the profile from utility_models.\n"
                "Utility Tasks referencing this ID will fail until reassigned.\n\n"
                "[bold]This cannot be undone.[/]"
            )
            with Horizontal(id="um-remove-actions"):
                yield Button("Remove Permanently", variant="error", id="btn-um-remove-confirm")
                yield Button("Close", classes="dismiss-btn", variant="default", id="btn-um-remove-cancel")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        self.dismiss(event.button.id == "btn-um-remove-confirm")


class SkillViewModal(ModalScreen):
    """Skill modal — view, edit (with source write-back), and remove."""

    CSS = """
    #skill-content-editor {
        height: 1fr;
        border: solid $surface-lighten-1;
    }
    """

    def __init__(self, skill_data: dict, **kwargs):
        super().__init__(**kwargs)
        self.skill_data = skill_data
        self.skill_path = _resolve_skill_path(skill_data)
        self._changed = False

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
            f"[cyan]Description:[/] {desc}\n"
            f"[dim]Source: {self.skill_path}[/]"
        )

        content = _read_skill_file(self.skill_path)
        self._file_exists = os.path.exists(self.skill_path)

        with Vertical(id="skill-dialog"):
            yield Static(header, id="skill-dialog-header")
            with VerticalScroll(id="skill-content-scroll"):
                yield Markdown(content, id="skill-content-body")
            yield TextArea(content if self._file_exists else "", id="skill-content-editor")
            with Horizontal(id="skill-dialog-actions"):
                yield Button("Edit", variant="warning", id="skill-edit")
                yield Button("Save", variant="success", id="skill-save")
                yield Button("Remove", variant="error", id="skill-remove")
                yield Button("Close", classes="dismiss-btn", variant="default", id="skill-view-close")

    def on_mount(self) -> None:
        self.query_one("#skill-content-editor", TextArea).display = False
        self.query_one("#skill-save", Button).display = False
        if not self._file_exists:
            # Stale registry entry — nothing to edit, but Remove stays available
            self.query_one("#skill-edit", Button).disabled = True
        if self.skill_data.get("type") == "system":
            # Shipped skills are system-managed — restored by setup --update
            self.query_one("#skill-remove", Button).disabled = True

    def _enter_edit_mode(self) -> None:
        self.query_one("#skill-content-scroll", VerticalScroll).display = False
        self.query_one("#skill-content-editor", TextArea).display = True
        self.query_one("#skill-edit", Button).display = False
        self.query_one("#skill-save", Button).display = True

    def _exit_edit_mode(self, new_content: str) -> None:
        self.query_one("#skill-content-body", Markdown).update(new_content)
        self.query_one("#skill-content-scroll", VerticalScroll).display = True
        self.query_one("#skill-content-editor", TextArea).display = False
        self.query_one("#skill-edit", Button).display = True
        self.query_one("#skill-save", Button).display = False

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "skill-view-close":
            self.dismiss(self._changed)
        elif event.button.id == "skill-edit":
            self._enter_edit_mode()
        elif event.button.id == "skill-save":
            content = self.query_one("#skill-content-editor", TextArea).text
            ok, err = _write_skill_file(self.skill_path, content)
            if ok:
                name = self.skill_data.get("name", "")
                _mark_skill_updated(name)
                self._changed = True
                self._exit_edit_mode(content)
                if self.skill_data.get("type") in ("agent_created", "override"):
                    self.app.notify(
                        f"Skill '{name}' saved — re-syncs to sub-agents on next Lifeline tick",
                        title="Skills"
                    )
                else:
                    self.app.notify(
                        f"Skill '{name}' saved — note: setup --update overwrites shipped skills from the repo",
                        title="Skills", severity="warning"
                    )
            else:
                self.app.notify(f"Save failed: {err}", title="Skills", severity="error")
        elif event.button.id == "skill-remove":
            self.app.push_screen(
                SkillRemoveConfirmModal(self.skill_data.get("name", "")),
                callback=self._on_remove_confirmed,
            )

    def _on_remove_confirmed(self, confirmed: bool) -> None:
        if not confirmed:
            return
        ok, err = _delete_skill(self.skill_data)
        if ok:
            self.app.notify(f"Skill '{self.skill_data.get('name', '')}' removed", title="Skills")
            self.dismiss(True)
        else:
            self.app.notify(f"Remove failed: {err}", title="Skills", severity="error")


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
                yield Button("Close", classes="dismiss-btn", variant="default", id="btn-router-cancel")

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


class RunsLogModal(ModalScreen):
    """Read-only viewer for the Utility runs log.

    Mirrors the cycle-log viewer pattern (``#msg-dialog`` + ``RichLog``) so the
    log-viewing UX is consistent across the dashboard. Each line in
    ``/var/lib/versa-agi/utility-runs/runs.log`` is one JSON record (one per run,
    success or failure); the tail is shown newest-first with a status glyph.
    """

    CSS = """
    RunsLogModal {
        align: center middle;
        background: $surface 80%;
    }
    """

    BINDINGS = [("escape", "dismiss", "Close")]
    _RUNS_LOG = "/var/lib/versa-agi/utility-runs/runs.log"
    _TAIL_LINES = 500

    def compose(self) -> ComposeResult:
        with Vertical(id="msg-dialog"):
            yield Static("[bold cyan]\U0001f4c4 Utility Runs Log[/]", id="cycle-log-header")
            yield RichLog(id="cycle-log-body", wrap=False, highlight=True, markup=True)
            with Horizontal(id="msg-dialog-actions"):
                yield Button("\U0001f9f9 Drain", variant="error", id="runs-log-drain")
                yield Button("\U0001f4cb Copy All", variant="default", id="runs-log-copy")
                yield Button("Close", classes="dismiss-btn", variant="default", id="msg-dialog-close")

    def on_mount(self) -> None:
        self._load()

    def _read_lines(self) -> list[str]:
        try:
            with open(self._RUNS_LOG, encoding="utf-8", errors="replace") as f:
                return f.read().splitlines()
        except OSError:
            return []

    def _load(self) -> None:
        body = self.query_one("#cycle-log-body", RichLog)
        body.clear()
        lines = [ln.strip() for ln in self._read_lines() if ln.strip()]
        if not lines:
            body.write("[dim]No utility runs recorded yet (runs.log is empty or missing).[/]")
            return
        tail = lines[-self._TAIL_LINES:]
        for raw in reversed(tail):  # newest first
            try:
                rec = json.loads(raw)
            except ValueError:
                body.write(raw)
                continue
            ok = rec.get("ok")
            mark = "[green]\u2713[/]" if ok else "[red]\u2717[/]"
            ts = rec.get("ts", "")
            um = rec.get("um_id", "")
            model = rec.get("catalog_model", "")
            agent = rec.get("agent", "")
            tid = rec.get("task_id")
            if ok:
                arts = rec.get("artifacts") or []
                tail_txt = f"[green]{len(arts)} artifact(s)[/]"
            else:
                tail_txt = f"[red]{rec.get('code', '')}: {rec.get('error', '')}[/]"
            body.write(
                f"{mark} [dim]{ts}[/] {um} [dim]({model})[/] \u00b7 {agent} \u00b7 task {tid} \u2014 {tail_txt}"
            )

    def _drain(self) -> None:
        """Truncate runs.log via the watchdog-owned agictl (the file's owner)."""
        try:
            proc = subprocess.run(
                ["sudo", "-u", "watchdog", "/usr/local/lib/versa-agi/agictl",
                 "utility", "drain-runs-log"],
                capture_output=True, text=True, timeout=20,
            )
            ok = False
            for line in reversed((proc.stdout or "").strip().splitlines()):
                if line.strip().startswith("{"):
                    ok = bool(json.loads(line).get("success"))
                    break
            if ok:
                self.app.notify("runs.log drained", title="Utility Runs Log")
            else:
                self.app.notify("Failed to drain runs.log", severity="error")
        except Exception as e:
            self.app.notify(str(e), severity="error")
        self._load()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "msg-dialog-close":
            self.app.pop_screen()
        elif event.button.id == "runs-log-drain":
            self._drain()
        elif event.button.id == "runs-log-copy":
            content = "\n".join(self._read_lines())
            try:
                subprocess.run(
                    ["xclip", "-selection", "clipboard"],
                    input=content.encode(), check=True,
                )
                self.app.notify("runs.log copied", title="Clipboard")
            except Exception:
                try:
                    subprocess.run(
                        ["xsel", "--clipboard", "--input"],
                        input=content.encode(), check=True,
                    )
                    self.app.notify("runs.log copied", title="Clipboard")
                except Exception:
                    self.app.notify("Install xclip or xsel for clipboard support", severity="warning")

    def action_dismiss(self) -> None:
        self.app.pop_screen()


class SystemSettingsModal(ModalScreen):
    """Modal for configuring system-level settings."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._skills_rows = _get_skills_rows()
        self._pkg_rows = _get_packages_rows()
        self._skills_page = 0
        self._pkg_page = 0
        self._memory_page = 0
        self.selected_memory_key = None
        self.selected_utility_model_id = None
        self._skills_page_size = 52
        self._pkg_page_size = 39

    def compose(self) -> ComposeResult:
        # Circuit Breaker
        cb_consecutive = _read_ini_value("agent", "circuit_breaker_consecutive", "5")
        cb_hourly = _read_ini_value("agent", "circuit_breaker_hourly", "20")
        flood_guard_hours = _read_ini_value("agent", "flood_guard_timeout_hours", "3")
        task_max_spawn = _read_ini_value("agent", "task_max_spawn_attempts", "3")

        # Web Search
        search_enabled = _read_ini_value("search", "enabled", "false").lower() == "true"
        searxng_url = _read_ini_value("search", "searxng_url", "http://localhost:8888")

        # AI Mode
        ai_mode = _read_ini_value("gemini", "mode", "cloud")

        # COA Autonomous Mode
        coa_autonomous = _read_ini_value("coa", "autonomous", "false").lower() == "true"

        # Browser Automation
        browser_enabled = _read_ini_value("browser", "enabled", "false").lower() == "true"
        browser_timeout = _read_ini_value("browser", "timeout", "30")

        # Model Loading Strategy
        loading_strategy = _read_ini_value("local_ai", "model_loading_strategy", "single")
        gpu_backend = _read_ini_value("local_ai", "gpu_backend", "standard")
        local_ai_enabled = _read_ini_value("local_ai", "enabled", "false").lower() == "true"
        self._show_strategy = local_ai_enabled and gpu_backend in ("intel", "remote")

        img_enabled = _read_ini_value("image_processing", "enabled", "true").lower() == "true"
        img_format = _read_ini_value("image_processing", "format", "jpeg").lower()
        if img_format == "jpg":
            img_format = "jpeg"
        img_quality = _read_ini_value("image_processing", "jpeg_quality", "80")
        img_dpi = _read_ini_value("image_processing", "jpeg_dpi", "72")
        img_max_w = _read_ini_value("image_processing", "max_width", "2048")
        img_max_h = _read_ini_value("image_processing", "max_height", "2048")

        aud_enabled = _read_ini_value("audio_processing", "enabled", "true").lower() == "true"
        aud_format = _read_ini_value("audio_processing", "format", "wav").lower()
        aud_voice = _read_ini_value("audio_processing", "voice", "alloy").lower()

        um_enabled = _read_ini_value("utility_models", "enabled", "true").lower() == "true"
        um_write_manifest = _read_ini_value("utility_models", "write_manifest", "true").lower() == "true"
        # Parked (VV required): vv_enabled = _read_ini_value("versavoice", "enabled", "true").lower() == "true"
        vv_enabled = True  # checkbox locked on; local-only path parked

        _browser_label = "Disable" if browser_enabled else "Enable"
        _browser_variant = "error" if browser_enabled else "success"
        _browser_status = "[bold green]● Enabled[/]" if browser_enabled else "[bold red]● Disabled[/]"

        with Vertical(id="settings-dialog"):
            yield Static("[bold]⚙ System Settings[/]", id="settings-dialog-header")

            with TabbedContent(initial="settings-general-tab", id="settings-tabs"):
                with TabPane("General", id="settings-general-tab"):
                    with Vertical(id="settings-general-pane"):
                        with VerticalScroll(id="settings-general-scroll"):
                            yield Static("", classes="modal-tab-spacer")
                            with Horizontal(classes="settings-general-cols"):
                                with Vertical(classes="settings-general-col"):
                                    _mode_labels = {"cloud": "Cloud", "local": "Local", "hybrid": "Hybrid"}
                                    _mode_label = _mode_labels.get(ai_mode, ai_mode)
                                    yield Static(f"[bold cyan]AI Mode[/]  [bold]{_mode_label}[/]")
                                    yield Static("[dim]Edit setup.ini [gemini] mode + run: sudo ./setup.sh --update[/]")

                                    yield Static("")
                                    yield Static("[bold cyan]Circuit Breaker[/]")
                                    yield Static("[dim]Prevents runaway API cost from repeated spawn failures[/]")
                                    yield Static("[dim]Only exit codes 1 (error), 42 (input error), 99 (runaway) trigger the breaker.[/]")
                                    yield Static("")
                                    with Horizontal(classes="task-field-row"):
                                        with Vertical(classes="task-field-col"):
                                            yield Static("[cyan]Consecutive Failure Threshold[/]")
                                            yield Input(value=cb_consecutive, placeholder="e.g. 5", id="input-cb-consecutive", type="integer")
                                        with Vertical(classes="task-field-col"):
                                            yield Static("[cyan]Hourly Failure Threshold[/]")
                                            yield Input(value=cb_hourly, placeholder="e.g. 20", id="input-cb-hourly", type="integer")

                                    yield Static("")
                                    with Horizontal(classes="settings-aligned-field-row", id="settings-flood-task-row"):
                                        with Vertical(classes="settings-aligned-field-col"):
                                            yield Static("[cyan]Flood Guard Timeout (hours)[/]")
                                            yield Input(
                                                value=flood_guard_hours,
                                                placeholder="e.g. 3",
                                                id="input-flood-guard-hours",
                                                type="integer",
                                            )
                                            yield Static(
                                                "[dim]PU messaging suppression auto-lifts after this many hours "
                                                "without a new outbound message.[/]"
                                            )
                                        with Vertical(classes="settings-aligned-field-col"):
                                            yield Static("[cyan]Max Spawn Attempts (per overdue task)[/]")
                                            yield Input(
                                                value=task_max_spawn,
                                                placeholder="e.g. 3",
                                                id="input-task-max-spawn",
                                                type="integer",
                                            )
                                            yield Static(
                                                "[dim]Overdue planned and repeatedly-waking waiting tasks retry "
                                                "this many lifeline wake cycles before auto-freeze. Primary User "
                                                "is notified; spawn_attempts resets on snooze, done, or cancelled.[/]"
                                            )

                                    yield Static("")
                                    with Vertical(classes="settings-section-box"):
                                        yield Static("[bold cyan]VersaVoice API[/]")
                                        yield Static(
                                            "[dim]VersaVoice is required. Local-only (disable) path is parked "
                                            "until downstream gaps are fixed.[/]"
                                        )
                                        # Parked: allow turning VV off from dashboard
                                        # yield ClearCheckbox(
                                        #     "Use VersaVoice API",
                                        #     id="chk-vv-enabled",
                                        #     value=vv_enabled,
                                        # )
                                        yield ClearCheckbox(
                                            "Use VersaVoice API (required)",
                                            id="chk-vv-enabled",
                                            value=True,
                                            disabled=True,
                                        )

                                with Vertical(classes="settings-general-col"):
                                    if self._show_strategy:
                                        _strat_label = "Router" if loading_strategy == "router" else "Single"
                                        _strat_color = "green" if loading_strategy == "router" else "yellow"
                                        with Vertical(classes="settings-section-box"):
                                            yield Static(
                                                f"[bold cyan]Model Loading[/]  "
                                                f"[bold {_strat_color}]{_strat_label}[/]"
                                            )
                                            if loading_strategy == "router":
                                                yield Static(
                                                    "[dim]All models available on demand — "
                                                    "no Docker restart to switch[/]"
                                                )
                                            else:
                                                yield Static(
                                                    "[dim]One model in VRAM — Docker restart required to switch[/]"
                                                )
                                            yield Button(
                                                f"Switch to {'Single' if loading_strategy == 'router' else 'Router'} Mode",
                                                variant="warning",
                                                id="btn-toggle-strategy",
                                            )

                                    with Vertical(classes="settings-section-box"):
                                        yield Static(
                                            "[bold cyan]COA Autonomous Mode[/]  "
                                            "[bold yellow]⚠ Only enable on dedicated hardware[/]"
                                        )
                                        yield Static(
                                            "[dim]Grants COA unrestricted sudo access (NOPASSWD: ALL).[/]"
                                        )
                                        yield ClearCheckbox(
                                            "Enable sudo access",
                                            id="chk-coa-autonomous",
                                            value=coa_autonomous,
                                        )

                                    with Vertical(classes="settings-section-box"):
                                        yield Static("[bold cyan]Web Search[/]")
                                        yield Static("[dim]Local SearXNG integration for agent research[/]")
                                        with Horizontal(classes="settings-web-search-row"):
                                            with Vertical(classes="settings-web-search-col"):
                                                yield ClearCheckbox("Enabled", id="chk-search-enabled", value=search_enabled)
                                            with Vertical(classes="settings-web-search-col"):
                                                yield Static("[cyan]SearXNG URL[/]")
                                                yield Input(
                                                    value=searxng_url,
                                                    placeholder="http://localhost:8888",
                                                    id="input-searxng-url",
                                                )

                                    with Vertical(classes="settings-section-box"):
                                        with Vertical(classes="settings-browser-grid"):
                                            with Vertical(classes="settings-browser-info"):
                                                yield Static(
                                                    f"[bold cyan]Browser Automation[/]  {_browser_status}",
                                                    id="browser-status-label",
                                                )
                                                yield Static(
                                                    "[dim]Headless Chromium for page navigation and extraction[/]"
                                                )
                                            with Vertical(classes="settings-browser-toggle-cell"):
                                                yield Button(
                                                    f"{_browser_label} Browser Automation",
                                                    variant=_browser_variant,
                                                    id="btn-browser-toggle",
                                                )
                                            with Vertical(
                                                id="settings-browser-timeout-cell",
                                                classes="settings-browser-timeout-cell",
                                            ):
                                                yield Static("[cyan]Page Load Timeout (seconds)[/]")
                                                yield Input(
                                                    value=browser_timeout,
                                                    placeholder="e.g. 30",
                                                    id="input-browser-timeout",
                                                    type="integer",
                                                )
                        with Horizontal(classes="settings-tab-actions"):
                            yield Button("Save", variant="success", id="btn-save-settings-general")
                            yield Button(
                                "Close", variant="default", id="btn-settings-close-general",
                                classes="dismiss-btn",
                            )

                with TabPane("Skills Registry", id="settings-skills-tab"):
                    with Vertical(id="settings-skills-pane"):
                        yield Static("", classes="modal-tab-spacer")
                        skills_table = PaginatedDataTable(self._handle_skills_key, id="skills-registry-table")
                        yield skills_table
                        yield Static(
                            "[dim]Double-click or press Enter to view/edit/remove a skill · "
                            "CLI: agictl skill list[/]",
                            id="settings-skills-hint",
                        )
                        with Horizontal(classes="settings-tab-actions"):
                            yield Button(
                                "Close", variant="default", id="btn-settings-close-skills",
                                classes="dismiss-btn",
                            )

                with TabPane("Packages & Requests", id="settings-packages-tab"):
                    with Vertical(id="settings-packages-pane"):
                        yield Static("", classes="modal-tab-spacer")
                        pkg_table = PaginatedDataTable(self._handle_packages_key, id="packages-table")
                        yield pkg_table
                        with Horizontal(id="packages-actions", classes="settings-tab-actions"):
                            yield Button("Approve", variant="success", id="btn-pkg-approve")
                            yield Button("Deny", variant="error", id="btn-pkg-deny")
                            yield Button("Install", variant="warning", id="btn-pkg-install")
                            yield Button("Add/Request", variant="primary", id="btn-pkg-add")
                            yield Button("Remove", variant="default", id="btn-pkg-remove")
                            yield Button(
                                "Close", variant="default", id="btn-settings-close-packages",
                                classes="dismiss-btn",
                            )

                with TabPane("System Memory", id="settings-system-memory-tab"):
                    with Vertical(id="settings-system-memory-pane"):
                        yield Static("", classes="modal-tab-spacer")
                        yield DataTable(id="settings-sys-memory-table", cursor_type="row")
                        yield Static("", id="settings-sys-memory-hint")
                        with Horizontal(classes="settings-tab-actions"):
                            yield Button(
                                "Edit Selected", variant="primary", id="btn-settings-edit-mem",
                                disabled=True,
                            )
                            yield Button(
                                "Delete Selected", variant="error", id="btn-settings-delete-mem",
                                disabled=True,
                            )
                            yield Button(
                                "Close", variant="default", id="btn-settings-close-memory",
                                classes="dismiss-btn",
                            )

                if UTILITY_MODELS_UI_VISIBLE:
                    with TabPane("Image Processing", id="settings-image-processing-tab"):
                        with Vertical(id="settings-image-processing-pane"):
                            with VerticalScroll(id="settings-image-processing-scroll"):
                                yield Static("", classes="modal-tab-spacer")
                                yield Static("[bold cyan]Harness Image Processing[/]")
                                yield Static(
                                    "[dim]Applied before VIEW INJECT for all vision-capable models. "
                                    "Normalizes attachments and screenshots to a shared JPEG pipeline "
                                    "so context size stays predictable.[/]"
                                )
                                yield Static("")
                                yield ClearCheckbox("Enabled", id="chk-image-processing-enabled", value=img_enabled)
                                yield Static("")
                                yield Static("[cyan]Output Format[/]")
                                yield Select(
                                    [("JPEG", "jpeg")],
                                    value=img_format if img_format == "jpeg" else "jpeg",
                                    id="select-image-format",
                                    allow_blank=False,
                                )
                                yield Static("[dim]Additional formats will be added here when supported.[/]")
                                yield Static("")
                                with Horizontal(classes="task-field-row"):
                                    with Vertical(classes="task-field-col"):
                                        yield Static("[cyan]JPEG Quality (1–100)[/]")
                                        yield Input(
                                            value=img_quality, placeholder="80",
                                            id="input-jpeg-quality", type="integer",
                                        )
                                    with Vertical(classes="task-field-col"):
                                        yield Static("[cyan]JPEG DPI (metadata)[/]")
                                        yield Input(
                                            value=img_dpi, placeholder="72",
                                            id="input-jpeg-dpi", type="integer",
                                        )
                                yield Static(
                                    "[dim]Quality controls compression. DPI is written into JPEG metadata only "
                                    "— it does not change pixel dimensions.[/]"
                                )
                                yield Static("")
                                yield Static("[bold cyan]Resolution (all formats)[/]")
                                yield Static("[dim]Downscale larger images before inject, preserving aspect ratio.[/]")
                                with Horizontal(classes="task-field-row"):
                                    with Vertical(classes="task-field-col"):
                                        yield Static("[cyan]Max Width (px)[/]")
                                        yield Input(
                                            value=img_max_w, placeholder="2048",
                                            id="input-image-max-width", type="integer",
                                        )
                                    with Vertical(classes="task-field-col"):
                                        yield Static("[cyan]Max Height (px)[/]")
                                        yield Input(
                                            value=img_max_h, placeholder="2048",
                                            id="input-image-max-height", type="integer",
                                        )
                            with Horizontal(classes="settings-tab-actions"):
                                yield Button("Save", variant="success", id="btn-save-settings-image")
                                yield Button(
                                    "Close", variant="default", id="btn-settings-close-image",
                                    classes="dismiss-btn",
                                )

                    with TabPane("Audio Processing", id="settings-audio-processing-tab"):
                        with Vertical(id="settings-audio-processing-pane"):
                            with VerticalScroll(id="settings-audio-processing-scroll"):
                                yield Static("", classes="modal-tab-spacer")
                                yield Static("[bold #a78bfa]Harness Audio Processing[/]")
                                yield Static(
                                    "[dim]Defaults for Utility Model audio generation. Streaming audio is "
                                    "always received as PCM16 and packaged locally — WAV is native; OGG/MP3/"
                                    "FLAC require ffmpeg (falls back to WAV). A Utility Model's config_json "
                                    "may override these per-profile.[/]"
                                )
                                yield Static("")
                                yield ClearCheckbox("Enabled", id="chk-audio-processing-enabled", value=aud_enabled)
                                yield Static("")
                                with Horizontal(classes="task-field-row"):
                                    with Vertical(classes="task-field-col"):
                                        yield Static("[#a78bfa]Output Container[/]")
                                        yield Select(
                                            [
                                                ("WAV (native, no ffmpeg)", "wav"),
                                                ("OGG (Opus — ffmpeg)", "ogg"),
                                                ("MP3 (ffmpeg)", "mp3"),
                                                ("FLAC (ffmpeg)", "flac"),
                                            ],
                                            value=aud_format if aud_format in ("wav", "ogg", "mp3", "flac") else "wav",
                                            id="select-audio-format",
                                            allow_blank=False,
                                        )
                                    with Vertical(classes="task-field-col"):
                                        yield Static("[#a78bfa]Voice[/] [dim](OpenAI-specific)[/]")
                                        yield Select(
                                            [
                                                (v.title(), v) for v in (
                                                    "alloy", "ash", "ballad", "coral", "echo",
                                                    "fable", "nova", "onyx", "sage", "shimmer", "verse",
                                                )
                                            ],
                                            value=aud_voice if aud_voice in (
                                                "alloy", "ash", "ballad", "coral", "echo",
                                                "fable", "nova", "onyx", "sage", "shimmer", "verse",
                                            ) else "alloy",
                                            id="select-audio-voice",
                                            allow_blank=False,
                                        )
                                yield Static(
                                    "[dim]Container sets the saved file extension. [b]Voice names above are "
                                    "OpenAI-specific[/b] (alloy, verse, …) and apply to OpenAI TTS models "
                                    "like openai/gpt-audio; other providers use different voice IDs. This "
                                    "global default is why a single shared voice list is a stopgap — "
                                    "per-model voice config is tracked as TD-MODALITY-CONFIG-001.[/]"
                                )
                            with Horizontal(classes="settings-tab-actions"):
                                yield Button("Save", variant="success", id="btn-save-settings-audio")
                                yield Button(
                                    "Close", variant="default", id="btn-settings-close-audio",
                                    classes="dismiss-btn",
                                )

                if UTILITY_MODELS_UI_VISIBLE:
                    with TabPane("Utility Models", id="settings-utility-models-tab"):
                        with Vertical(id="settings-utility-models-pane"):
                            yield Static("", classes="modal-tab-spacer")
                            with Vertical(id="settings-utility-models-header"):
                                yield Static("[bold cyan]Utility Models[/]")
                                yield Static(
                                    "[dim]One-shot generation profiles — catalog model + system prompt per row. "
                                    "Invoke via agictl utility run or Utility Tasks.[/]"
                                )
                                with Horizontal(id="utility-models-enabled-row"):
                                    with Vertical(classes="utility-models-row-col"):
                                        yield ClearCheckbox("Enabled", id="chk-utility-models-enabled", value=um_enabled)
                                    with Vertical(classes="utility-models-row-col"):
                                        yield ClearCheckbox(
                                            "Write Manifest", id="chk-utility-models-write-manifest",
                                            value=um_write_manifest,
                                        )
                                    with Vertical(classes="utility-models-row-col utility-models-row-col-action"):
                                        yield Button(
                                            "View Log", variant="warning",
                                            id="btn-utility-runs-log",
                                        )
                            um_table = PaginatedDataTable(
                                self._handle_utility_models_key,
                                id="utility-models-table",
                                cursor_type="row",
                            )
                            yield um_table
                            yield Static(
                                "[dim]Double-click or Enter to edit · select row for Edit/Delete · "
                                "CLI: agictl utility model list[/]",
                                id="utility-models-hint",
                            )
                            with Horizontal(id="utility-models-actions", classes="settings-tab-actions"):
                                yield Button("New", variant="success", id="btn-utility-model-new")
                                yield Button("Edit", variant="primary", id="btn-utility-model-edit", disabled=True)
                                yield Button("Delete", variant="error", id="btn-utility-model-delete", disabled=True)
                                yield Button(
                                    "Close", variant="default", id="btn-settings-close-utility-models",
                                    classes="dismiss-btn",
                                )

    def _fetch_utility_models(self) -> list[dict]:
        try:
            proc = subprocess.run(
                ["sudo", "-u", "watchdog", "/usr/local/lib/versa-agi/agictl", "utility", "model", "list"],
                capture_output=True, text=True, timeout=20,
            )
            for line in reversed((proc.stdout or "").strip().splitlines()):
                if line.strip().startswith("{"):
                    data = json.loads(line)
                    return data.get("utility_models", []) if data.get("success") else []
        except Exception:
            pass
        return []

    def _set_utility_model_action_state(self, um_id: str | None) -> None:
        self.selected_utility_model_id = um_id
        enabled = bool(um_id)
        self.query_one("#btn-utility-model-edit", Button).disabled = not enabled
        self.query_one("#btn-utility-model-delete", Button).disabled = not enabled

    def _sync_utility_model_selection(self) -> None:
        try:
            table = self.query_one("#utility-models-table", PaginatedDataTable)
        except Exception:
            self._set_utility_model_action_state(None)
            return
        if table.row_count == 0:
            self._set_utility_model_action_state(None)
            return
        try:
            row_key, _ = table.coordinate_to_cell_key(table.cursor_coordinate)
            um_id = row_key.value if row_key else None
        except Exception:
            um_id = None
        self._set_utility_model_action_state(um_id)

    def _refresh_utility_models_table(self) -> None:
        try:
            table = self.query_one("#utility-models-table", PaginatedDataTable)
        except Exception:
            return
        table.clear(columns=True)
        table.add_column("ID", width=20)
        table.add_column("Label", width=18)
        table.add_column("Model", width=26)
        table.add_column("Out", width=6)
        table.add_column("Output Path")
        table.add_column("Run As", width=10)
        table.add_column("On", width=4)
        self._set_utility_model_action_state(None)
        rows = self._fetch_utility_models()
        for row in rows:
            table.add_row(
                row.get("id", ""),
                row.get("label", ""),
                row.get("catalog_model", ""),
                row.get("output_modality", ""),
                (row.get("output_path") or "")[:48],
                row.get("run_as_agent", ""),
                "Y" if row.get("enabled") else "N",
                key=row.get("id"),
            )
        count = len(rows)
        if count:
            table.border_title = f"Utility Models ({count})"
            table.move_cursor(row=0)
            self._sync_utility_model_selection()
        else:
            table.border_title = "Utility Models (0) — select New to add a profile"

    def on_mount(self) -> None:
        """Populate the skills and packages DataTables after mount."""
        try:
            table = self.query_one("#skills-registry-table", DataTable)
            table.cursor_type = "row"
            table.add_columns("Name", "Type", "Origin", "Assets", "Status", "Description")
            self._update_skills_table()
        except Exception:
            pass

        try:
            pkg_table = self.query_one("#packages-table", DataTable)
            pkg_table.cursor_type = "row"
            pkg_table.add_columns("Package", "Status", "Reason", "Requested By", "Requested At")
            self._update_packages_table()
        except Exception:
            pass

        try:
            mem_table = self.query_one("#settings-sys-memory-table", DataTable)
            mem_table.add_column("Updated", width=20)
            mem_table.add_column("Stored By", width=12)
            mem_table.add_column("Key", width=30)
            mem_table.add_column("Value")
            self._refresh_system_memory()
        except Exception:
            pass

        try:
            um_table = self.query_one("#utility-models-table", PaginatedDataTable)
            um_table.cursor_type = "row"
            self._refresh_utility_models_table()
        except Exception:
            pass

    @on(TabbedContent.TabActivated)
    def _on_settings_tab_activated(self, event: TabbedContent.TabActivated) -> None:
        if event.pane.id == "settings-utility-models-tab":
            self.call_after_refresh(self._sync_utility_model_selection)

    @on(DataTable.RowHighlighted, "#utility-models-table")
    def on_utility_model_highlighted(self, event: DataTable.RowHighlighted) -> None:
        if event.row_key is None:
            return
        self._set_utility_model_action_state(event.row_key.value)

    @on(DataTable.RowSelected, "#utility-models-table")
    def on_utility_model_selected(self, event: DataTable.RowSelected) -> None:
        if event.row_key is None:
            return
        self._set_utility_model_action_state(event.row_key.value)

    def _open_utility_model_editor(self, um_id: str | None = None) -> None:
        from agitop.panels.utility_model_editor_modal import UtilityModelEditorModal

        record = None
        if um_id:
            for row in self._fetch_utility_models():
                if row.get("id") == um_id:
                    record = row
                    break
            try:
                proc = subprocess.run(
                    ["sudo", "-u", "watchdog", "/usr/local/lib/versa-agi/agictl",
                     "utility", "model", "show", um_id],
                    capture_output=True, text=True, timeout=15,
                )
                for line in reversed((proc.stdout or "").strip().splitlines()):
                    if line.strip().startswith("{"):
                        parsed = json.loads(line)
                        if parsed.get("utility_model"):
                            record = parsed["utility_model"]
                        break
            except Exception:
                pass
        self.app.push_screen(UtilityModelEditorModal(self, record=record))

    def _delete_utility_model(self) -> None:
        um_id = self.selected_utility_model_id
        if not um_id:
            self.app.notify("Select a Utility Model first", severity="warning")
            return
        um_label = um_id
        for row in self._fetch_utility_models():
            if row.get("id") == um_id:
                um_label = row.get("label") or um_id
                break
        self.app.push_screen(
            UtilityModelRemoveConfirmModal(um_id, um_label),
            self._on_utility_remove_confirmed,
        )

    def _on_utility_remove_confirmed(self, confirmed: bool) -> None:
        if not confirmed:
            return
        um_id = self.selected_utility_model_id
        if not um_id:
            return
        try:
            proc = subprocess.run(
                ["sudo", "-u", "watchdog", "/usr/local/lib/versa-agi/agictl",
                 "utility", "model", "remove", um_id],
                capture_output=True, text=True, timeout=15,
            )
            ok = proc.returncode == 0
            for line in reversed((proc.stdout or "").strip().splitlines()):
                if line.strip().startswith("{"):
                    ok = json.loads(line).get("success", ok)
                    break
            if ok:
                self.app.notify(f"Removed Utility Model '{um_id}'", title="Utility Models")
                self._refresh_utility_models_table()
            else:
                self.app.notify("Failed to remove Utility Model", severity="error")
        except Exception as e:
            self.app.notify(str(e), severity="error")

    def _save_audio_processing(self) -> None:
        """Persist the [audio_processing] defaults (own Save button)."""
        aud_enabled = self.query_one("#chk-audio-processing-enabled", Checkbox).value
        aud_format = self.query_one("#select-audio-format", Select).value or "wav"
        aud_voice = self.query_one("#select-audio-voice", Select).value or "alloy"
        ok_a = _write_ini_value("audio_processing", "enabled", "true" if aud_enabled else "false")
        ok_b = _write_ini_value("audio_processing", "format", str(aud_format))
        ok_c = _write_ini_value("audio_processing", "voice", str(aud_voice))
        if ok_a and ok_b and ok_c:
            self.app.notify(
                f"Audio: {'on' if aud_enabled else 'off'} · {aud_format} · {aud_voice}",
                title="Settings Saved",
            )
        else:
            self.app.notify("Failed to save audio_processing settings", severity="error")

    def _save_utility_models_enabled(self) -> None:
        was_enabled = _read_ini_value("utility_models", "enabled", "true").lower() == "true"
        enabled = self.query_one("#chk-utility-models-enabled", Checkbox).value
        write_manifest = self.query_one("#chk-utility-models-write-manifest", Checkbox).value
        ok_enabled = _write_ini_value("utility_models", "enabled", "true" if enabled else "false")
        ok_manifest = _write_ini_value(
            "utility_models", "write_manifest", "true" if write_manifest else "false"
        )
        if ok_enabled and ok_manifest:
            msg = f"Utility Models {'enabled' if enabled else 'disabled'}"
            msg += f" · manifest {'on' if write_manifest else 'off'}"
            if was_enabled and not enabled:
                frozen = self._freeze_utility_tasks()
                if frozen:
                    msg += f" — froze {frozen} utility task(s)"
            self.app.notify(msg, title="Utility Models")
        else:
            self.app.notify("Failed to save utility_models settings", severity="error")

    def _freeze_utility_tasks(self) -> int:
        try:
            proc = subprocess.run(
                ["sudo", "-u", "watchdog", "/usr/local/lib/versa-agi/agictl",
                 "utility", "freeze-tasks"],
                capture_output=True, text=True, timeout=20,
            )
            for line in reversed((proc.stdout or "").strip().splitlines()):
                if line.strip().startswith("{"):
                    data = json.loads(line)
                    if data.get("success"):
                        return int(data.get("frozen_count") or 0)
        except Exception:
            pass
        return 0

    def _refresh_system_memory(self) -> None:
        table = self.query_one("#settings-sys-memory-table", DataTable)
        table.clear()
        self.selected_memory_key = None
        self.query_one("#btn-settings-edit-mem", Button).disabled = True
        self.query_one("#btn-settings-delete-mem", Button).disabled = True
        self.query_one("#settings-sys-memory-hint", Static).update(
            "[dim]Select a row to edit or delete.[/]"
        )

        tasks_db = os.getenv("AGICTL_TASKS_DB", "/var/lib/versa-agi/coa/tasks.db")
        offset = self._memory_page * _SYS_MEMORY_PAGE_SIZE
        try:
            conn = db_connect.connect_compat(tasks_db, timeout=5)
            conn.row_factory = sqlite3.Row
            total = conn.execute("SELECT COUNT(*) FROM agent_memory_system").fetchone()[0]
            total_pages = max(1, (total + _SYS_MEMORY_PAGE_SIZE - 1) // _SYS_MEMORY_PAGE_SIZE)
            current_page = self._memory_page + 1

            rows = conn.execute(
                "SELECT * FROM agent_memory_system ORDER BY updated_at ASC LIMIT ? OFFSET ?",
                (_SYS_MEMORY_PAGE_SIZE, offset),
            ).fetchall()
            for r in rows:
                val = r["value"]
                if val and len(val) > 80:
                    val = val[:77] + "..."
                table.add_row(
                    str(r["updated_at"] or "--"),
                    str(r["agent_name"] or "?"),
                    r["key"],
                    val,
                    key=r["key"],
                )
            conn.close()

            if total_pages > 1:
                table.border_title = (
                    f"System Memory ({total} entries)  │  "
                    f"Page {current_page}/{total_pages}  │  PgUp/PgDn to navigate"
                )
            else:
                table.border_title = f"System Memory ({total} entries)"
        except Exception:
            table.border_title = "System Memory"

    def refresh_table(self) -> None:
        """Called by system memory edit/delete modals after changes."""
        self._refresh_system_memory()

    @on(DataTable.RowHighlighted, "#settings-sys-memory-table")
    def on_memory_highlighted(self, event: DataTable.RowHighlighted) -> None:
        if event.row_key is None:
            return
        self.selected_memory_key = event.row_key.value
        self.query_one("#btn-settings-edit-mem", Button).disabled = False
        self.query_one("#btn-settings-delete-mem", Button).disabled = False
        self.query_one("#settings-sys-memory-hint", Static).update(
            f"[bold cyan]Selected:[/] {self.selected_memory_key}"
        )

    @on(DataTable.RowSelected, "#settings-sys-memory-table")
    def on_memory_selected(self, event: DataTable.RowSelected) -> None:
        self.selected_memory_key = event.row_key.value
        if self.selected_memory_key:
            from agitop.panels.system_memory_editor import EditMemoryRowModal
            self.app.push_screen(EditMemoryRowModal(self.selected_memory_key, self))

    def on_key(self, event) -> None:
        focused = self.app.focused
        if event.key == "pagedown":
            if isinstance(focused, DataTable) and focused.id == "settings-sys-memory-table":
                try:
                    db = os.getenv("AGICTL_TASKS_DB", "/var/lib/versa-agi/coa/tasks.db")
                    conn = db_connect.connect_compat(db, timeout=5)
                    total = conn.execute("SELECT COUNT(*) FROM agent_memory_system").fetchone()[0]
                    conn.close()
                    max_page = max(0, (total - 1) // _SYS_MEMORY_PAGE_SIZE)
                    if self._memory_page < max_page:
                        self._memory_page += 1
                        self._refresh_system_memory()
                except Exception:
                    pass
        elif event.key == "pageup":
            if isinstance(focused, DataTable) and focused.id == "settings-sys-memory-table":
                if self._memory_page > 0:
                    self._memory_page -= 1
                    self._refresh_system_memory()

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        """Also open viewer on double-click/Enter."""
        if event.data_table.id == "utility-models-table":
            if event.row_key and event.row_key.value:
                self._open_utility_model_editor(event.row_key.value)
            return
        if event.data_table.id != "skills-registry-table":
            return
        try:
            idx = int(event.row_key.value)
            skill_data = self._skills_rows[idx]
            self.app.push_screen(SkillViewModal(skill_data), callback=self._on_skill_modal_close)
        except (ValueError, IndexError):
            pass

    def _on_skill_modal_close(self, changed: bool) -> None:
        """Refresh the skills table after an edit or removal."""
        if changed:
            self._skills_rows = _get_skills_rows()
            max_page = max(0, (len(self._skills_rows) - 1) // self._skills_page_size)
            self._skills_page = min(self._skills_page, max_page)
            self._update_skills_table()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-toggle-strategy":
            current = _read_ini_value("local_ai", "model_loading_strategy", "single")
            self.app.push_screen(RouterModeConfirmModal(current))
        elif event.button.id == "btn-save-settings-audio":
            self._save_audio_processing()
            self.app.pop_screen()
        elif event.button.id in (
            "btn-save-settings-general",
            "btn-save-settings-image",
        ):
            try:
                # ── Task Management + Circuit Breaker + Flood Guard ──
                task_max_spawn = int(self.query_one("#input-task-max-spawn", Input).value)
                cb_consecutive = int(self.query_one("#input-cb-consecutive", Input).value)
                cb_hourly = int(self.query_one("#input-cb-hourly", Input).value)
                flood_guard_hours = int(self.query_one("#input-flood-guard-hours", Input).value)

                if task_max_spawn < 1 or cb_consecutive < 1 or cb_hourly < 1 or flood_guard_hours < 1:
                    self.app.notify("Thresholds must be ≥ 1", severity="error")
                    return

                ok0 = _write_ini_value("agent", "task_max_spawn_attempts", str(task_max_spawn))
                ok1 = _write_ini_value("agent", "circuit_breaker_consecutive", str(cb_consecutive))
                ok2 = _write_ini_value("agent", "circuit_breaker_hourly", str(cb_hourly))
                ok3 = _write_ini_value("agent", "flood_guard_timeout_hours", str(flood_guard_hours))

                # ── Web Search ──
                search_enabled = self.query_one("#chk-search-enabled", Checkbox).value
                searxng_url = self.query_one("#input-searxng-url", Input).value.strip()
                ok4 = _write_ini_value("search", "enabled", "true" if search_enabled else "false")
                ok5 = _write_ini_value("search", "searxng_url", searxng_url) if searxng_url else True

                # VV required — always persist enabled=true (checkbox is locked on).
                # Parked: vv_enabled = self.query_one("#chk-vv-enabled", Checkbox).value
                # Parked: ok_vv = _write_ini_value("versavoice", "enabled", "true" if vv_enabled else "false")
                vv_enabled = True
                ok_vv = _write_ini_value("versavoice", "enabled", "true")

                # ── COA Autonomous ──
                coa_autonomous = self.query_one("#chk-coa-autonomous", Checkbox).value
                ok6 = _write_ini_value("coa", "autonomous", "true" if coa_autonomous else "false")

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

                # ── Browser Timeout (browser toggle handled by modal, not Save) ──
                browser_timeout = int(self.query_one("#input-browser-timeout", Input).value)
                if browser_timeout < 5:
                    self.app.notify("Browser timeout must be ≥ 5 seconds", severity="error")
                    return
                ok7 = _write_ini_value("browser", "timeout", str(browser_timeout))

                # Image Processing widgets only exist when the Utility Models UI is
                # visible (the tab is gated behind the same flag) — skip otherwise.
                img_enabled = False
                jpeg_quality = jpeg_dpi = max_width = max_height = 0
                ok8 = ok9 = ok10 = ok11 = ok12 = ok13 = True
                if UTILITY_MODELS_UI_VISIBLE:
                    img_enabled = self.query_one("#chk-image-processing-enabled", Checkbox).value
                    img_format = self.query_one("#select-image-format", Select).value or "jpeg"
                    jpeg_quality = int(self.query_one("#input-jpeg-quality", Input).value)
                    jpeg_dpi = int(self.query_one("#input-jpeg-dpi", Input).value)
                    max_width = int(self.query_one("#input-image-max-width", Input).value)
                    max_height = int(self.query_one("#input-image-max-height", Input).value)
                    if not (1 <= jpeg_quality <= 100):
                        self.app.notify("JPEG quality must be 1–100", severity="error")
                        return
                    if not (1 <= jpeg_dpi <= 600):
                        self.app.notify("JPEG DPI must be 1–600", severity="error")
                        return
                    if max_width < 64 or max_height < 64:
                        self.app.notify("Max width/height must be ≥ 64 px", severity="error")
                        return
                    ok8 = _write_ini_value("image_processing", "enabled", "true" if img_enabled else "false")
                    ok9 = _write_ini_value("image_processing", "format", str(img_format))
                    ok10 = _write_ini_value("image_processing", "jpeg_quality", str(jpeg_quality))
                    ok11 = _write_ini_value("image_processing", "jpeg_dpi", str(jpeg_dpi))
                    ok12 = _write_ini_value("image_processing", "max_width", str(max_width))
                    ok13 = _write_ini_value("image_processing", "max_height", str(max_height))

                if all([ok0, ok1, ok2, ok3, ok4, ok5, ok_vv, ok6, ok7, ok8, ok9, ok10, ok11, ok12, ok13]):
                    summary_parts = [
                        f"Task max spawn: {task_max_spawn}",
                        f"Circuit breaker: {cb_consecutive}/{cb_hourly}",
                        f"Flood guard: {flood_guard_hours}h",
                        f"Search: {'on' if search_enabled else 'off'}",
                        f"VersaVoice: {'on' if vv_enabled else 'off'}",
                        f"Browser timeout: {browser_timeout}s",
                        f"Autonomous: {'on' if coa_autonomous else 'off'}",
                    ]
                    if UTILITY_MODELS_UI_VISIBLE:
                        summary_parts.append(
                            f"Image: {'on' if img_enabled else 'off'} JPEG q={jpeg_quality} {max_width}x{max_height}"
                        )
                    self.app.notify(
                        " · ".join(summary_parts) + " — active next CRON tick",
                        title="Settings Saved"
                    )
                    try:
                        from agitop.panels.messages import MessagesPanel
                        self.app.query_one(MessagesPanel).refresh_data()
                    except Exception:
                        pass
                else:
                    self.app.notify("Some settings failed to save — check permissions", severity="warning")
            except ValueError:
                self.app.notify("Invalid input — thresholds must be whole numbers", severity="error")
            self.app.pop_screen()
        elif event.button.id in (
            "btn-settings-close-general",
            "btn-settings-close-image",
            "btn-settings-close-audio",
            "btn-settings-close-skills",
            "btn-settings-close-packages",
            "btn-settings-close-memory",
            "btn-settings-close-utility-models",
        ):
            if event.button.id == "btn-settings-close-utility-models":
                self._save_utility_models_enabled()
            elif event.button.id == "btn-settings-close-audio":
                self._save_audio_processing()
            self.app.pop_screen()
        elif event.button.id == "btn-utility-model-new":
            self._open_utility_model_editor()
        elif event.button.id == "btn-utility-model-edit":
            if self.selected_utility_model_id:
                self._open_utility_model_editor(self.selected_utility_model_id)
        elif event.button.id == "btn-utility-model-delete":
            self._delete_utility_model()
        elif event.button.id == "btn-utility-runs-log":
            self.app.push_screen(RunsLogModal())
        elif event.button.id == "btn-browser-toggle":
            browser_enabled = _read_ini_value("browser", "enabled", "false").lower() == "true"
            new_val = 0 if browser_enabled else 1
            ok = _write_ini_value("browser", "enabled", "false" if browser_enabled else "true")
            if ok:
                # Resolve COA os_user from agents DB (authoritative after setup)
                try:
                    _db = db_connect.connect_compat("file:/var/lib/versa-agi/agents.db?mode=ro", uri=True)
                    _row = _db.execute("SELECT os_user FROM agents WHERE name='coa'").fetchone()
                    _db.close()
                    coa_user = _row[0] if _row else "coa"
                except Exception:
                    coa_user = "coa"
                from agitop.panels.agents import AgentBrowserToggleModal
                self.app.push_screen(
                    AgentBrowserToggleModal(agent_name="coa", new_val=new_val, os_user=coa_user),
                    callback=lambda _: self._refresh_browser_status()
                )
            else:
                self.app.notify("Failed to write setup.ini — check permissions", severity="error")
        elif event.button.id in ("btn-pkg-approve", "btn-pkg-deny", "btn-pkg-install", "btn-pkg-remove"):
            self._handle_pkg_action(event.button.id)
        elif event.button.id == "btn-pkg-add":
            self._handle_pkg_add()
        elif event.button.id == "btn-settings-edit-mem":
            if self.selected_memory_key:
                from agitop.panels.system_memory_editor import EditMemoryRowModal
                self.app.push_screen(EditMemoryRowModal(self.selected_memory_key, self))
        elif event.button.id == "btn-settings-delete-mem":
            if self.selected_memory_key:
                from agitop.panels.system_memory_editor import DeleteMemoryConfirmModal
                self.app.push_screen(DeleteMemoryConfirmModal(self.selected_memory_key, self))

    def _handle_skills_key(self, key: str) -> None:
        if key == "pageup":
            if self._skills_page > 0:
                self._skills_page -= 1
                self._update_skills_table()
        elif key == "pagedown":
            max_page = max(0, (len(self._skills_rows) - 1) // self._skills_page_size)
            if self._skills_page < max_page:
                self._skills_page += 1
                self._update_skills_table()

    def _handle_packages_key(self, key: str) -> None:
        if key == "pageup":
            if self._pkg_page > 0:
                self._pkg_page -= 1
                self._update_packages_table()
        elif key == "pagedown":
            max_page = max(0, (len(self._pkg_rows) - 1) // self._pkg_page_size)
            if self._pkg_page < max_page:
                self._pkg_page += 1
                self._update_packages_table()

    def _handle_utility_models_key(self, _key: str) -> None:
        """PageUp/PageDown on utility models table (single-page list for now)."""
        pass

    def _on_browser_modal_close(self, success: bool) -> None:
        if success:
            self._refresh_browser_status()

    def _refresh_browser_status(self) -> None:
        try:
            browser_enabled = _read_ini_value("browser", "enabled", "false").lower() == "true"
            status_lbl = self.query_one("#browser-status-label", Static)
            _browser_status = "[bold green]● Enabled[/]" if browser_enabled else "[bold red]● Disabled[/]"
            status_lbl.update(f"[bold cyan]Browser Automation[/]  {_browser_status}")

            btn = self.query_one("#btn-browser-toggle", Button)
            btn.label = "Disable Browser Automation" if browser_enabled else "Enable Browser Automation"
            btn.variant = "error" if browser_enabled else "success"
        except Exception:
            pass

    def _install_playwright_background(self) -> None:
        """Background thread: fix driver permissions, install deps + chromium, notify."""
        import glob as _glob
        try:
            # Fix driver permissions (pip may not preserve +x on node binary)
            for driver_dir in _glob.glob("/usr/local/lib/versa-agi/venv/lib/python3.*/site-packages/playwright/driver"):
                node_bin = os.path.join(driver_dir, "node")
                if os.path.isfile(node_bin):
                    os.chmod(node_bin, 0o755)
                pkg_bin = os.path.join(driver_dir, "package", "bin")
                if os.path.isdir(pkg_bin):
                    for f in os.listdir(pkg_bin):
                        fp = os.path.join(pkg_bin, f)
                        if os.path.isfile(fp):
                            os.chmod(fp, 0o755)

            pw_bin = "/usr/local/lib/versa-agi/venv/bin/playwright"
            if not os.path.isfile(pw_bin):
                self.app.call_from_thread(
                    self.app.notify, "Playwright binary not found — run setup.sh", title="Error", severity="error"
                )
                return

            # Install system dependencies (as root — agitop runs as root)
            r1 = subprocess.run(
                [pw_bin, "install-deps", "chromium"],
                capture_output=True, text=True, timeout=120
            )
            if r1.returncode != 0:
                self.app.call_from_thread(
                    self.app.notify, "Failed to install system dependencies", title="Error", severity="error"
                )
                return

            # Install Chromium for COA user
            coa_user = _read_ini_value("users", "coa", "coa")
            r2 = subprocess.run(
                ["sudo", "-u", coa_user, "-H", pw_bin, "install", "chromium"],
                capture_output=True, text=True, timeout=120
            )
            if r2.returncode != 0:
                self.app.call_from_thread(
                    self.app.notify, f"Failed to install Chromium for {coa_user}", title="Error", severity="error"
                )
                return

            self.app.call_from_thread(
                self.app.notify, "Playwright Chromium installed successfully", title="Browser Automation"
            )
        except Exception as e:
            self.app.call_from_thread(
                self.app.notify, f"Installation error: {e}", title="Error", severity="error"
            )

    def _update_skills_table(self) -> None:
        try:
            table = self.query_one("#skills-registry-table", DataTable)
            table.clear()

            start = self._skills_page * self._skills_page_size
            end = start + self._skills_page_size
            page_rows = self._skills_rows[start:end]

            for idx, r in enumerate(page_rows):
                assets = "✓" if r.get("has_assets") else "—"
                desc = r.get("description") or ""
                abs_idx = start + idx
                table.add_row(
                    r.get("name", ""),
                    r.get("type", ""),
                    r.get("origin", ""),
                    assets,
                    r.get("status", ""),
                    desc,
                    key=str(abs_idx),
                )

            total_pages = max(1, (len(self._skills_rows) + self._skills_page_size - 1) // self._skills_page_size)
            current_page = self._skills_page + 1
            table.border_title = f"Skills Registry ({len(self._skills_rows)})  │  Page {current_page}/{total_pages}  │  PgUp/PgDn to navigate"
        except Exception:
            pass

    def _update_packages_table(self) -> None:
        try:
            pkg_table = self.query_one("#packages-table", DataTable)
            pkg_table.clear()

            start = self._pkg_page * self._pkg_page_size
            end = start + self._pkg_page_size
            page_rows = self._pkg_rows[start:end]

            for idx, r in enumerate(page_rows):
                status_styles = {"approved": "green", "requested": "yellow", "denied": "red"}
                s = r.get("status", "")
                style = status_styles.get(s, "")
                display_status = f"[{style}]{s}[/{style}]" if style else s
                abs_idx = start + idx
                pkg_table.add_row(
                    r.get("name", ""),
                    display_status,
                    r.get("reason", "") or "—",
                    r.get("requested_by", "") or "—",
                    r.get("requested_at", "") or "—",
                    key=str(abs_idx),
                )

            total_pages = max(1, (len(self._pkg_rows) + self._pkg_page_size - 1) // self._pkg_page_size)
            current_page = self._pkg_page + 1
            pkg_table.border_title = f"Packages & Requests ({len(self._pkg_rows)})  │  Page {current_page}/{total_pages}  │  PgUp/PgDn to navigate"
        except Exception:
            pass

    def _get_selected_pkg_name(self) -> str | None:
        """Return the selected package name, or None after notifying the user."""
        try:
            pkg_table = self.query_one("#packages-table", DataTable)
            cursor_row = pkg_table.cursor_row
            if cursor_row is None or cursor_row < 0:
                self.app.notify("Select a package first", severity="warning")
                return None
            row_key, _ = pkg_table.coordinate_to_cell_key((cursor_row, 0))
            if not row_key or not row_key.value:
                self.app.notify("Select a package first", severity="warning")
                return None
            idx = int(row_key.value)
            if idx < 0 or idx >= len(self._pkg_rows):
                self.app.notify("Select a package first", severity="warning")
                return None
            return self._pkg_rows[idx].get("name", "")
        except Exception:
            self.app.notify("Select a package first", severity="warning")
            return None

    def _handle_pkg_action(self, button_id: str) -> None:
        """Handle package approve/deny/install/remove for selected row."""
        pkg_name = self._get_selected_pkg_name()
        if not pkg_name:
            return

        action_map = {
            "btn-pkg-approve": "approve",
            "btn-pkg-deny": "deny",
            "btn-pkg-install": "install",
            "btn-pkg-remove": "remove",
        }
        action = action_map.get(button_id)
        if not action:
            return

        if action == "install":
            self.app.push_screen(PackageInstallModal(pkg_name=pkg_name), callback=self._on_install_modal_close)
            return

        if action == "remove":
            self.app.push_screen(
                PackageRemoveConfirmModal(pkg_name),
                callback=lambda confirmed: self._on_pkg_remove_confirmed(confirmed, pkg_name),
            )
            return

        self._execute_pkg_action(action, pkg_name)

    def _execute_pkg_action(self, action: str, pkg_name: str, *, success_msg: str | None = None) -> None:
        def _run():
            try:
                cmd = ["sudo", "-u", "watchdog", "/usr/local/lib/versa-agi/agictl", "pkg", action, pkg_name]
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
                import json
                try:
                    resp = json.loads(result.stdout)
                    if resp.get("success"):
                        msg = success_msg or f"Package '{pkg_name}': {action} succeeded"
                        self.app.call_from_thread(self.app.notify, msg, title="Packages")
                    else:
                        self.app.call_from_thread(
                            self.app.notify, resp.get("error", "Unknown error"), title="Error", severity="error"
                        )
                except json.JSONDecodeError:
                    self.app.call_from_thread(
                        self.app.notify, result.stdout or result.stderr, title="Result"
                    )
            except Exception as e:
                self.app.call_from_thread(
                    self.app.notify, str(e), title="Error", severity="error"
                )
            finally:
                self.app.call_from_thread(self._refresh_packages)

        threading.Thread(target=_run, daemon=True).start()

    def _on_pkg_remove_confirmed(self, confirmed: bool, pkg_name: str) -> None:
        if not confirmed:
            return
        self._execute_pkg_action(
            "remove",
            pkg_name,
            success_msg=f"Package '{pkg_name}' removed from registry",
        )

    def _refresh_packages(self) -> None:
        """Reload package data, clear and repopulate the DataTable."""
        try:
            self._pkg_rows = _get_packages_rows()
            self._update_packages_table()
        except Exception:
            pass

    def _on_install_modal_close(self, success: bool) -> None:
        self._refresh_packages()

    def _handle_pkg_add(self) -> None:
        """Prompt for package name and add it via agictl request modal."""
        self.app.push_screen(PackageRequestModal(), callback=self._on_pkg_request_modal_close)

    def _on_pkg_request_modal_close(self, success: bool) -> None:
        if success:
            self._refresh_packages()


class PackageRequestModal(ModalScreen):
    """Modal dialog for a PU or agent to request a system package."""

    CSS = """
    PackageRequestModal {
        align: center middle;
        background: $surface 80%;
    }
    #request-pkg-dialog {
        width: 60;
        height: auto;
        padding: 1 2;
        border: heavy $primary;
        background: $surface;
    }
    #request-pkg-actions {
        margin-top: 1;
        height: auto;
    }
    #request-pkg-actions Button {
        margin-right: 1;
    }
    #lbl-request-pkg-error {
        margin-top: 1;
        height: auto;
    }
    """

    def compose(self) -> ComposeResult:
        with Vertical(id="request-pkg-dialog"):
            yield Static("[bold yellow]Add/Request System Package[/]\n")
            yield Static("Enter the apt package name to request or add:")
            yield Input(placeholder="e.g. valgrind", id="input-request-pkg-name")
            yield Static("\nProvide a brief reason/justification:")
            yield Input(placeholder="e.g. Debug memory leaks", id="input-request-pkg-reason")
            yield Static("", id="lbl-request-pkg-error")
            with Horizontal(id="request-pkg-actions"):
                yield Button("Submit Request", variant="success", id="btn-request-confirm")
                yield Button("Close", classes="dismiss-btn", variant="default", id="btn-request-cancel")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-request-cancel":
            self.dismiss(False)
        elif event.button.id == "btn-request-confirm":
            pkg_name = self.query_one("#input-request-pkg-name", Input).value.strip()
            reason = self.query_one("#input-request-pkg-reason", Input).value.strip()
            error_lbl = self.query_one("#lbl-request-pkg-error", Static)

            if not pkg_name:
                error_lbl.update("[red]Package name is required[/]")
                return
            if not re.match(r"^[a-z0-9][a-z0-9.+\-]+$", pkg_name):
                error_lbl.update("[red]Invalid package name format[/]")
                return
            if not reason:
                error_lbl.update("[red]Reason is required[/]")
                return

            # Submit via agictl pkg request
            def _run():
                try:
                    cmd = ["sudo", "-u", "watchdog", "/usr/local/lib/versa-agi/agictl", "pkg", "request", pkg_name, "--reason", reason]
                    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
                    import json
                    try:
                        resp = json.loads(result.stdout)
                        if resp.get("success"):
                            self.app.call_from_thread(self.app.notify, f"Package '{pkg_name}' requested", title="Success")
                            self.app.call_from_thread(self.dismiss, True)
                        else:
                            error_msg = resp.get('error', 'Request failed')
                            self.app.call_from_thread(error_lbl.update, f"[red]{error_msg}[/]")
                    except json.JSONDecodeError:
                        self.app.call_from_thread(self.app.notify, result.stdout or result.stderr, title="Result")
                        self.app.call_from_thread(self.dismiss, False)
                except Exception as e:
                    self.app.call_from_thread(error_lbl.update, f"[red]Error: {e}[/]")

            threading.Thread(target=_run, daemon=True).start()


class BrowserAutomationModal(ModalScreen):
    """Modal for enabling/disabling Browser Automation with real-time feedback.

    Follows the SyclActivationModal pattern: shows status, runs background
    provisioning (playwright install/cleanup), and closes with notification.
    """

    CSS = """
    BrowserAutomationModal {
        align: center middle;
        background: $surface 80%;
    }
    #browser-dialog {
        width: 75;
        height: 20;
        padding: 1 2;
        border: heavy $primary;
        background: $surface;
    }
    #browser-terminal {
        height: 1fr;
        background: $boost;
        border: solid $surface-lighten-1;
        padding: 0 1;
        scrollbar-gutter: stable;
        color: $text-muted;
    }
    #browser-actions {
        margin-top: 1;
        height: auto;
        align: right middle;
    }
    #browser-actions Button {
        margin-left: 1;
    }
    """

    def __init__(self, current_enabled: bool, **kwargs):
        super().__init__(**kwargs)
        self.current_enabled = current_enabled
        self.target_enabled = not current_enabled
        self._running = False
        self._terminal_text = ""

    def compose(self) -> ComposeResult:
        action = "Disable" if self.current_enabled else "Enable"
        with Vertical(id="browser-dialog"):
            yield Static(f"[bold yellow]🌐 Browser Automation — {action}[/]\n", id="browser-title")
            yield Static("...", id="browser-info")
            yield VerticalScroll(Static(id="browser-terminal-text"), id="browser-terminal")
            with Horizontal(id="browser-actions"):
                confirm_variant = "success" if self.target_enabled else "error"
                yield Button(f"Confirm {action}", variant=confirm_variant, id="btn-browser-confirm")
                yield Button("Close", classes="dismiss-btn", variant="default", id="btn-browser-close")

    def on_mount(self) -> None:
        self.query_one("#browser-terminal").display = False
        if self.target_enabled:
            info = (
                f"This will [bold]enable[/] headless Chromium browser automation system-wide.\n\n"
                f"Playwright Chromium binaries will be installed for the watchdog user.\n"
                f"Agents with browser_enabled=1 will gain access on their next spawn.\n"
            )
        else:
            info = (
                f"This will [bold]disable[/] headless Chromium browser automation system-wide.\n\n"
                f"Agents with browser_enabled=1 will lose access until re-enabled.\n"
                f"Chromium binaries will remain installed.\n"
            )
        self.query_one("#browser-info", Static).update(info)

    @staticmethod
    def _strip_ansi(text: str) -> str:
        import re as _re
        return _re.sub(r'\x1b\[[0-9;]*[a-zA-Z]', '', text)

    def _append_text(self, text: str) -> None:
        self._terminal_text += text
        try:
            term = self.query_one("#browser-terminal-text", Static)
            term.update(self._terminal_text)
            self.query_one("#browser-terminal", VerticalScroll).scroll_end(animate=False)
        except Exception:
            pass

    def _enable_close(self) -> None:
        btn = self.query_one("#btn-browser-close", Button)
        btn.disabled = False
        btn.loading = False
        btn.label = "Close"

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        if event.button.id == "btn-browser-close":
            self.dismiss(len(self._terminal_text) > 0)
        elif event.button.id == "btn-browser-confirm" and not self._running:
            self._running = True
            
            # Switch to terminal view
            self.query_one("#browser-info").display = False
            self.query_one("#btn-browser-confirm").display = False
            self.query_one("#browser-terminal").display = True
            
            # Disable close button, show loading state, and change label
            close_btn = self.query_one("#btn-browser-close", Button)
            close_btn.disabled = True
            close_btn.loading = True
            close_btn.label = "Working..."
            
            self._terminal_text = ""
            threading.Thread(target=self._run_toggle, daemon=True).start()

    def _run_toggle(self) -> None:
        """Background thread: toggle browser, optionally install Playwright system-wide."""
        try:
            if self.target_enabled:
                # Fix Playwright driver permissions (pip install may not preserve +x on node binary)
                import glob as _glob
                for driver_dir in _glob.glob("/usr/local/lib/versa-agi/venv/lib/python3.*/site-packages/playwright/driver"):
                    node_bin = os.path.join(driver_dir, "node")
                    if os.path.isfile(node_bin):
                        os.chmod(node_bin, 0o755)
                    pkg_bin = os.path.join(driver_dir, "package", "bin")
                    if os.path.isdir(pkg_bin):
                        for f in os.listdir(pkg_bin):
                            fp = os.path.join(pkg_bin, f)
                            if os.path.isfile(fp):
                                os.chmod(fp, 0o755)
                self.app.call_from_thread(self._append_text, "✓ Playwright driver permissions verified\n\n")

                self.app.call_from_thread(self._append_text, "Installing Playwright system dependencies (this may take a moment)...\n")
                self.app.call_from_thread(self._append_text, "$ /usr/local/lib/versa-agi/venv/bin/playwright install-deps chromium\n")
                
                cmd_deps = ["/usr/local/lib/versa-agi/venv/bin/playwright", "install-deps", "chromium"]
                process_deps = subprocess.Popen(
                    cmd_deps,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1,
                )
                while True:
                    line = process_deps.stdout.readline()
                    if not line:
                        break
                    self.app.call_from_thread(self._append_text, self._strip_ansi(line))
                process_deps.wait()
                
                if process_deps.returncode != 0:
                    self.app.call_from_thread(
                        self._append_text,
                        f"\n[red]✗ System dependencies installation failed with exit code {process_deps.returncode}[/]\n"
                    )
                    self.app.call_from_thread(
                        self.app.notify, "Failed to install Playwright system dependencies", title="Error", severity="error"
                    )
                    return
                
                self.app.call_from_thread(self._append_text, "\n[green]✓ System dependencies installed successfully.[/]\n\n")
                
                coa_user = _read_ini_value("users", "coa", "coa")
                self.app.call_from_thread(self._append_text, f"Installing Chromium browser for user '{coa_user}'...\n")
                self.app.call_from_thread(self._append_text, f"$ sudo -u {coa_user} -H /usr/local/lib/versa-agi/venv/bin/playwright install chromium\n")
                
                cmd_browser = ["sudo", "-u", coa_user, "-H", "/usr/local/lib/versa-agi/venv/bin/playwright", "install", "chromium"]
                process_browser = subprocess.Popen(
                    cmd_browser,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1,
                )
                while True:
                    line = process_browser.stdout.readline()
                    if not line:
                        break
                    self.app.call_from_thread(self._append_text, self._strip_ansi(line))
                process_browser.wait()
                
                if process_browser.returncode != 0:
                    self.app.call_from_thread(
                        self._append_text,
                        f"\n[red]✗ Playwright browser installation failed with exit code {process_browser.returncode}[/]\n"
                    )
                    self.app.call_from_thread(
                        self.app.notify, "Failed to install Playwright Chromium", title="Error", severity="error"
                    )
                    return
                
                self.app.call_from_thread(self._append_text, "\n[green]✓ Playwright Chromium browser installed successfully.[/]\n\n")
                
                self.app.call_from_thread(self._append_text, "Writing setup.ini: browser.enabled = true...\n")
                ok = _write_ini_value("browser", "enabled", "true")
                if not ok:
                    self.app.call_from_thread(self._append_text, "[red]Failed to write setup.ini — check permissions[/]\n")
                    return
                
                self.app.call_from_thread(self._append_text, "[green]✓ setup.ini updated successfully.[/]\n")
                self.app.call_from_thread(
                    self.app.notify, "Browser automation enabled system-wide", title="Browser Automation"
                )
            else:
                self.app.call_from_thread(self._append_text, "Writing setup.ini: browser.enabled = false...\n")
                ok = _write_ini_value("browser", "enabled", "false")
                if not ok:
                    self.app.call_from_thread(self._append_text, "[red]Failed to write setup.ini — check permissions[/]\n")
                    return
                self.app.call_from_thread(self._append_text, "[green]✓ setup.ini updated successfully.[/]\n\n")
                
                coa_user = _read_ini_value("users", "coa", "coa")
                self.app.call_from_thread(self._append_text, f"Removing Chromium binaries for user '{coa_user}'...\n")
                cache_dir = f"/home/{coa_user}/.cache/ms-playwright/"
                if os.path.isdir(cache_dir):
                    cmd = ["sudo", "-u", coa_user, "rm", "-rf", cache_dir]
                    self.app.call_from_thread(self._append_text, f"$ sudo -u {coa_user} rm -rf {cache_dir}\n")
                    res = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
                    if res.returncode == 0:
                        self.app.call_from_thread(self._append_text, f"[green]✓ Cleaned up cache directory successfully.[/]\n")
                    else:
                        self.app.call_from_thread(self._append_text, f"[red]✗ Cleanup failed: {res.stderr}[/]\n")
                else:
                    self.app.call_from_thread(self._append_text, "Cache directory does not exist or is already removed.\n")
                
                self.app.call_from_thread(self._append_text, "[green]✓ Browser automation disabled system-wide[/]\n")
                self.app.call_from_thread(
                    self.app.notify, "Browser automation disabled", title="Browser Automation"
                )
        except Exception as e:
            self.app.call_from_thread(self._append_text, f"\n[red]Error: {e}[/]\n")
        finally:
            self._running = False
            self.app.call_from_thread(self._enable_close)


class PackageInstallModal(ModalScreen):
    """Modal that runs 'sudo agictl pkg install <package>' and streams real-time stdout/stderr feedback."""

    CSS = """
    PackageInstallModal {
        align: center middle;
        background: $surface 80%;
    }
    #install-dialog {
        width: 75;
        height: 20;
        padding: 1 2;
        border: heavy $primary;
        background: $surface;
    }
    #install-terminal {
        height: 1fr;
        background: $boost;
        border: solid $surface-lighten-1;
        padding: 0 1;
        scrollbar-gutter: stable;
        color: $text-muted;
    }
    #install-actions {
        margin-top: 1;
        height: auto;
        align: right middle;
    }
    """

    def __init__(self, pkg_name: str, **kwargs):
        super().__init__(**kwargs)
        self.pkg_name = pkg_name
        self._terminal_text = ""
        self._running = False

    def compose(self) -> ComposeResult:
        with Vertical(id="install-dialog"):
            yield Static(f"[bold yellow]📦 Installing Package: {self.pkg_name}[/]\n", id="install-title")
            yield VerticalScroll(Static(id="install-terminal-text"), id="install-terminal")
            with Horizontal(id="install-actions"):
                yield Button("Close", classes="dismiss-btn", variant="default", id="btn-install-close")

    def on_mount(self) -> None:
        self.query_one("#btn-install-close", Button).disabled = True
        self._running = True
        threading.Thread(target=self._run_install, daemon=True).start()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-install-close":
            self.dismiss(True)

    def _append_text(self, text: str) -> None:
        self._terminal_text += text
        try:
            term = self.query_one("#install-terminal-text", Static)
            term.update(self._terminal_text)
            self.query_one("#install-terminal", VerticalScroll).scroll_end(animate=False)
        except Exception:
            pass

    def _enable_close(self) -> None:
        btn = self.query_one("#btn-install-close", Button)
        btn.disabled = False
        btn.loading = False

    def _run_install(self) -> None:
        try:
            self.app.call_from_thread(self._append_text, f"$ sudo -u watchdog /usr/local/lib/versa-agi/agictl pkg install {self.pkg_name}\n")
            cmd = ["sudo", "-u", "watchdog", "/usr/local/lib/versa-agi/agictl", "pkg", "install", self.pkg_name]
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )

            # Stream output line by line
            while True:
                line = process.stdout.readline()
                if not line:
                    break
                self.app.call_from_thread(self._append_text, line)

            process.wait()

            if process.returncode == 0:
                self.app.call_from_thread(self._append_text, "\n[green]✓ Installation completed successfully[/]\n")
                self.app.call_from_thread(
                    self.app.notify, f"Package '{self.pkg_name}' installed", title="Packages"
                )
            else:
                self.app.call_from_thread(self._append_text, f"\n[red]✗ Installation failed with exit code {process.returncode}[/]\n")
                self.app.call_from_thread(
                    self.app.notify, f"Failed to install package '{self.pkg_name}'", title="Error", severity="error"
                )
        except Exception as e:
            self.app.call_from_thread(self._append_text, f"\n[red]Error executing install: {e}[/]\n")
        finally:
            self._running = False
            self.app.call_from_thread(self._enable_close)
