"""Model Feedback Modal — PU/COA CRUD for model_feedback via agictl."""

import json
import subprocess

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, DataTable, Input, Select, Static

_WORK_MODALITIES = [
    ("(any)", ""),
    ("fast", "fast"),
    ("balanced", "balanced"),
    ("reasoning", "reasoning"),
    ("code", "code"),
    ("local", "local"),
]
_PREFERENCES = [("prefer", "prefer"), ("avoid", "avoid")]


def _run_agictl(args, timeout=25):
    try:
        proc = subprocess.run(
            ["sudo", "agictl"] + args,
            capture_output=True, text=True, timeout=timeout,
        )
    except Exception as e:
        return False, {}, str(e)
    data = {}
    if proc.stdout:
        for line in reversed(proc.stdout.strip().splitlines()):
            line = line.strip()
            if line.startswith("{"):
                try:
                    data = json.loads(line)
                    break
                except Exception:
                    continue
    ok = bool(data.get("success")) if data else (proc.returncode == 0)
    err = "" if ok else (data.get("error") or proc.stderr.strip() or "Unknown error")
    return ok, data, err


def _catalog_list() -> list[dict]:
    ok, data, _ = _run_agictl(["model", "catalog", "list"])
    return data.get("models", []) if ok else []


def _catalog_key_choices(preselect: str = "") -> list[tuple[str, str]]:
    """Enabled catalog keys for the feedback picklist."""
    choices: list[tuple[str, str]] = []
    seen: set[str] = set()
    for m in sorted(_catalog_list(), key=lambda r: r.get("key", "")):
        if not m.get("enabled"):
            continue
        key = m["key"]
        from model_catalog import format_catalog_picker_label, provider_display_label

        choices.append((
            format_catalog_picker_label(
                provider_display_label(m.get("provider") or ""),
                m.get("label") or key,
                key,
            ),
            key,
        ))
        seen.add(key)
    preselect = (preselect or "").strip()
    if preselect and preselect not in seen:
        choices.append((f"{preselect} — (current model)", preselect))
    if not choices:
        choices.append(("(no enabled models)", ""))
    return choices


def _norm_modality(mod) -> str:
    return (mod or "").strip()


def _is_duplicate_entry(
    entries: list[dict],
    catalog_key: str,
    preference: str,
    work_modality: str,
) -> bool:
    """True when an active row matches key + preference + work modality."""
    wm = _norm_modality(work_modality)
    for row in entries:
        if row.get("catalog_key") != catalog_key:
            continue
        if row.get("preference") != preference:
            continue
        if _norm_modality(row.get("work_modality")) == wm:
            return True
    return False


class ModelFeedbackModal(ModalScreen):
    BINDINGS = [Binding("escape", "close", "Close")]

    def __init__(self, catalog_key: str = "", **kwargs):
        super().__init__(**kwargs)
        self._catalog_key = (catalog_key or "").strip()
        self._entries: list[dict] = []

    def compose(self) -> ComposeResult:
        key_choices = _catalog_key_choices(self._catalog_key)
        choice_values = {v for _, v in key_choices if v}
        initial_key = (
            self._catalog_key
            if self._catalog_key in choice_values
            else (key_choices[0][1] if key_choices else "")
        )

        with Vertical(id="model-feedback-dialog"):
            yield Static("[bold]Model Feedback[/]", id="model-feedback-header")
            yield Static(
                "[dim]PU/COA preferences fed into triage pool resolution. "
                "Duplicate key + preference + modality combinations are rejected.[/]"
            )

            with Container(id="mf-columns"):
                with Vertical(classes="mf-col"):
                    yield Static("[cyan]Model[/]")
                    yield Select(
                        key_choices,
                        value=initial_key,
                        id="mf-key",
                        allow_blank=False,
                    )
                    yield Static("[cyan]Preference[/]")
                    yield Select(_PREFERENCES, id="mf-preference", allow_blank=False)
                with Vertical(classes="mf-col"):
                    yield Static("[cyan]Work modality (optional)[/]")
                    yield Select(_WORK_MODALITIES, id="mf-modality", allow_blank=False)
                    yield Static("[cyan]Task hint (optional)[/]")
                    yield Input(placeholder="e.g. debugging", id="mf-hint")
                    yield Static("[cyan]Note (optional)[/]")
                    yield Input(placeholder="PU feedback note", id="mf-note")

            with Vertical(id="mf-records-panel"):
                yield Static("[bold]Records[/]")
                yield DataTable(id="mf-table", zebra_stripes=True)
                yield Static("", id="mf-feedback")

            with Horizontal(id="model-feedback-actions"):
                yield Button("Add", variant="success", id="mf-add")
                yield Button("Delete", variant="error", id="mf-delete")
                yield Button("Close", classes="dismiss-btn", variant="default", id="mf-close")

    def on_mount(self) -> None:
        table = self.query_one("#mf-table", DataTable)
        table.cursor_type = "row"
        table.add_columns("ID", "Key", "Pref", "Modality", "By", "Note")
        self._reload()

    def _reload(self) -> None:
        ok, data, err = _run_agictl(["model", "feedback", "list"])
        table = self.query_one("#mf-table", DataTable)
        table.clear()
        self._entries = []
        if not ok:
            self.query_one("#mf-feedback", Static).update(f"[red]{err}[/]")
            return
        self._entries = data.get("feedback", [])
        rows = self._entries
        if self._catalog_key:
            rows = [r for r in rows if r.get("catalog_key") == self._catalog_key]
        for row in rows:
            table.add_row(
                str(row["id"]),
                row["catalog_key"],
                row["preference"],
                row.get("work_modality") or "any",
                row.get("created_by", ""),
                (row.get("note") or "")[:40],
                key=str(row["id"]),
            )

    def _selected_id(self) -> str:
        table = self.query_one("#mf-table", DataTable)
        if table.row_count == 0:
            return ""
        try:
            return table.coordinate_to_cell_key(table.cursor_coordinate).row_key.value or ""
        except Exception:
            return ""

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "mf-close":
            self.app.pop_screen()
            return
        if event.button.id == "mf-add":
            key = str(self.query_one("#mf-key", Select).value or "").strip()
            pref = self.query_one("#mf-preference", Select).value
            mod = self.query_one("#mf-modality", Select).value
            hint = self.query_one("#mf-hint", Input).value.strip()
            note = self.query_one("#mf-note", Input).value.strip()
            if not key or not pref:
                self.query_one("#mf-feedback", Static).update(
                    "[yellow]Model and preference are required[/]"
                )
                return
            if _is_duplicate_entry(self._entries, key, str(pref), str(mod or "")):
                self.query_one("#mf-feedback", Static).update(
                    "[yellow]Duplicate entry — this key + preference + modality already exists[/]"
                )
                return
            args = ["model", "feedback", "add", "--key", key, "--preference", str(pref)]
            if mod:
                args += ["--work-modality", str(mod)]
            if hint:
                args += ["--task-hint", hint]
            if note:
                args += ["--note", note]
            ok, _, err = _run_agictl(args)
            self.query_one("#mf-feedback", Static).update(
                "[green]Added[/]" if ok else f"[red]{err}[/]"
            )
            if ok:
                self._reload()
            return
        if event.button.id == "mf-delete":
            fid = self._selected_id()
            if not fid:
                self.query_one("#mf-feedback", Static).update(
                    "[yellow]Select a record to delete[/]"
                )
                return
            ok, _, err = _run_agictl(["model", "feedback", "remove", fid])
            self.query_one("#mf-feedback", Static).update(
                "[green]Deleted[/]" if ok else f"[red]{err}[/]"
            )
            if ok:
                self._reload()

    def action_close(self) -> None:
        self.app.pop_screen()
