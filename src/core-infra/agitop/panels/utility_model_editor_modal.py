"""Utility Model editor — create/update profiles via agictl utility model."""

import json
import os
import subprocess

from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Checkbox, Input, Select, Static, TextArea
from agitop.widgets.clear_checkbox import ClearCheckbox

_OUTPUT_MODALITIES = [
    ("Text", "text"),
    ("Image", "image"),
    ("Audio", "audio"),
    ("Video", "video"),
]

_INPUT_MODALITIES = [
    ("Text", "text"),
    ("Image", "image"),
    ("Audio", "audio"),
    ("Video", "video"),
    ("File", "file"),
]

_SELECT_NONE = "__none__"

_VENDOR_NAMES = {
    "google": "Google",
    "openai": "OpenAI",
    "xai": "xAI",
    "anthropic": "Anthropic",
    "openrouter": "OpenRouter",
    "ollama": "Ollama",
    "llamacpp": "llama.cpp",
}


def _vendor_name(provider: str, key: str) -> str:
    """Human vendor label. OpenRouter keys carry a ``vendor/model`` namespace."""
    prov = (provider or "").strip().lower()
    if prov == "openrouter" and "/" in key:
        return key.split("/", 1)[0]
    return _VENDOR_NAMES.get(prov, prov.title() if prov else "?")

# §IX /var/lib/versa-agi/ — staging for agitop → agictl prompt handoff (watchdog-readable).
_UM_PROMPT_STAGING = "/var/lib/versa-agi/utility-models/staging"
_PROMPT_INLINE_MAX = 120_000


def _prompt_cli_args(um_id: str, prompt: str) -> list[str]:
    """Pass system prompt to agictl without /tmp (root vs watchdog permission mismatch)."""
    if len(prompt) <= _PROMPT_INLINE_MAX:
        return ["--system-prompt", prompt]
    os.makedirs(_UM_PROMPT_STAGING, exist_ok=True)
    path = os.path.join(_UM_PROMPT_STAGING, f"{um_id}.prompt")
    with open(path, "w", encoding="utf-8") as f:
        f.write(prompt)
    try:
        import grp
        import pwd

        wd = pwd.getpwnam("watchdog").pw_uid
        coa = grp.getgrnam("coa").gr_gid
        os.chown(path, wd, coa)
        os.chmod(path, 0o660)
    except (ImportError, KeyError, OSError, PermissionError):
        subprocess.run(["chown", "watchdog:coa", path], check=False)
        subprocess.run(["chmod", "660", path], check=False)
    return ["--system-prompt-file", path]


def _run_agictl(args, timeout=30):
    try:
        proc = subprocess.run(
            ["sudo", "-u", "watchdog", "/usr/local/lib/versa-agi/agictl"] + args,
            capture_output=True, text=True, timeout=timeout,
        )
    except Exception as e:
        return False, {}, str(e)
    data = {}
    raw = (proc.stdout or "").strip()
    if raw:
        try:
            data = json.loads(raw)
        except Exception:
            for line in reversed(raw.splitlines()):
                line = line.strip()
                if line.startswith("{"):
                    try:
                        data = json.loads(line)
                        break
                    except Exception:
                        continue
    ok = bool(data.get("success")) if isinstance(data, dict) and "success" in data else (proc.returncode == 0)
    err = "" if ok else ((data.get("error") if isinstance(data, dict) else "") or proc.stderr.strip() or "Unknown error")
    return ok, data, err


def _catalog_rows() -> list[dict]:
    """Enabled catalog models with their input/output modality sets (for filtering)."""
    ok, data, _ = _run_agictl(["model", "catalog", "list"])
    rows: list[dict] = []
    for m in sorted(data.get("models", []) if ok else [], key=lambda r: r.get("key", "")):
        if not m.get("enabled"):
            continue
        key = m["key"]
        in_csv = (m.get("input_modalities") or "").strip()
        out_csv = (m.get("output_modalities") or "").strip()
        rows.append({
            "key": key,
            "label": m.get("label") or key,
            "vendor": _vendor_name(m.get("provider"), key),
            "in_csv": in_csv or "—",
            "out_csv": out_csv or "—",
            "inputs": {s.strip() for s in in_csv.split(",") if s.strip()},
            "outputs": {s.strip() for s in out_csv.split(",") if s.strip()},
        })
    return rows


def _catalog_option_label(row: dict) -> str:
    """'{Vendor} ({model id}) [{input} --> {output}]'."""
    return f"{row['vendor']} ({row['key']}) [{row['in_csv']} --> {row['out_csv']}]"


def _filter_catalog_choices(rows: list[dict], in_mod, out_mod) -> list[tuple[str, str]]:
    """Catalog Select options matching the chosen input + output modalities (both applied)."""
    choices: list[tuple[str, str]] = []
    for row in rows:
        if in_mod and in_mod not in row["inputs"]:
            continue
        if out_mod and out_mod not in row["outputs"]:
            continue
        choices.append((_catalog_option_label(row), row["key"]))
    if not choices:
        choices.append(("(no models match these modalities)", _SELECT_NONE))
    return choices


def _agent_choices() -> list[tuple[str, str]]:
    ok, data, _ = _run_agictl(["agent", "list"])
    choices = [("coa", "coa")]
    agents = data if isinstance(data, list) else data.get("agents", [])
    if ok:
        for a in agents:
            name = a.get("name", "") if isinstance(a, dict) else ""
            if name and name.lower() != "watchdog" and name not in {c[1] for c in choices}:
                choices.append((name, name))
    return choices


class UtilityModelEditorModal(ModalScreen):
    """New or edit Utility Model profile."""

    def __init__(self, parent, record: dict | None = None, **kwargs):
        super().__init__(**kwargs)
        self.parent_screen = parent
        self.record = record or {}
        self.is_edit = bool(record and record.get("id"))

    def compose(self) -> ComposeResult:
        title = "Edit Utility Model" if self.is_edit else "New Utility Model"
        r = self.record
        self._catalog_rows = _catalog_rows()
        saved_catalog = (r.get("catalog_model") or "").strip()
        out_mod = r.get("output_modality") or "text"
        in_mod = "text"
        if self.is_edit and saved_catalog:
            for row in self._catalog_rows:
                if row["key"] == saved_catalog:
                    in_mod = "text" if "text" in row["inputs"] else next(iter(row["inputs"]), "text")
                    break
        catalog_choices = _filter_catalog_choices(self._catalog_rows, in_mod, out_mod)
        choice_values = {v for _, v in catalog_choices}
        initial_catalog = (
            saved_catalog
            if saved_catalog in choice_values
            else (catalog_choices[0][1] if catalog_choices else _SELECT_NONE)
        )
        with Vertical(id="utility-model-editor-dialog"):
            yield Static(f"[bold cyan]{title}[/]", id="utility-model-editor-header")
            with VerticalScroll(id="utility-model-editor-scroll"):
                yield Static("", classes="modal-tab-spacer")
                with Horizontal(classes="task-field-row"):
                    with Vertical(classes="task-field-col"):
                        yield Static("[b]ID (slug)[/]", classes="modal-form-label")
                        yield Input(
                            value=r.get("id", "") or "",
                            placeholder="brand-hero-square",
                            id="um-id",
                            disabled=self.is_edit,
                        )
                    with Vertical(classes="task-field-col"):
                        yield Static("[b]Label[/]", classes="modal-form-label")
                        yield Input(
                            value=r.get("label", "") or "",
                            placeholder="Weekly hero image",
                            id="um-label",
                        )
                yield Static("[b]Catalog model[/]", classes="modal-form-label")
                yield Select(
                    catalog_choices,
                    value=initial_catalog,
                    id="um-catalog-model",
                    allow_blank=False,
                )
                with Horizontal(classes="task-field-row"):
                    with Vertical(classes="task-field-col"):
                        yield Static("[b]Input modality[/]", classes="modal-form-label")
                        yield Select(
                            _INPUT_MODALITIES,
                            value=in_mod,
                            id="um-input-modality",
                            allow_blank=False,
                        )
                    with Vertical(classes="task-field-col"):
                        yield Static("[b]Output modality[/]", classes="modal-form-label")
                        yield Select(
                            _OUTPUT_MODALITIES,
                            value=out_mod,
                            id="um-output-modality",
                            allow_blank=False,
                        )
                with Horizontal(classes="task-field-row"):
                    with Vertical(classes="task-field-col"):
                        yield Static("[b]Run as agent[/]", classes="modal-form-label")
                        yield Select(
                            _agent_choices(),
                            value=r.get("run_as_agent") or "coa",
                            id="um-run-as-agent",
                            allow_blank=False,
                        )
                    with Vertical(classes="task-field-col"):
                        yield Static("[b]Output path[/]", classes="modal-form-label")
                        yield Input(
                            value=r.get("output_path") or ".agent/utility",
                            id="um-output-path",
                        )
                yield Static("[b]System prompt[/]", classes="modal-form-label")
                yield TextArea(r.get("system_prompt") or "", id="um-system-prompt", show_line_numbers=True)
                with Horizontal(id="um-enabled-row"):
                    yield ClearCheckbox("Enabled", id="um-enabled", value=bool(r.get("enabled", True)))
                    yield Static(
                        "[dim]Disabled profiles remain saved but cannot run or be selected for new Utility Tasks.[/]",
                        id="um-enabled-note",
                    )
            with Horizontal(id="utility-model-editor-actions"):
                yield Button("Save", variant="success", id="btn-um-save")
                yield Button("Close", variant="default", id="btn-um-close", classes="dismiss-btn")

    @on(Select.Changed, "#um-input-modality")
    @on(Select.Changed, "#um-output-modality")
    def _refilter_catalog(self) -> None:
        """Re-filter the Catalog model picklist by the chosen input/output modalities."""
        try:
            in_sel = self.query_one("#um-input-modality", Select)
            out_sel = self.query_one("#um-output-modality", Select)
            catalog = self.query_one("#um-catalog-model", Select)
        except Exception:
            return
        in_mod, out_mod = in_sel.value, out_sel.value
        if in_mod is Select.BLANK or out_mod is Select.BLANK:
            return
        current = catalog.value
        choices = _filter_catalog_choices(self._catalog_rows, in_mod, out_mod)
        catalog.set_options(choices)
        values = {v for _, v in choices}
        catalog.value = current if current in values else choices[0][1]

    @on(Button.Pressed, "#btn-um-close")
    def on_close(self) -> None:
        self.app.pop_screen()

    @on(Button.Pressed, "#btn-um-save")
    def on_save(self) -> None:
        um_id = self.query_one("#um-id", Input).value.strip()
        label = self.query_one("#um-label", Input).value.strip()
        catalog = self.query_one("#um-catalog-model", Select).value
        out_mod = self.query_one("#um-output-modality", Select).value
        out_path = self.query_one("#um-output-path", Input).value.strip()
        run_as = self.query_one("#um-run-as-agent", Select).value
        prompt = self.query_one("#um-system-prompt", TextArea).text
        enabled = self.query_one("#um-enabled", Checkbox).value

        if not um_id or not label or not catalog or catalog == _SELECT_NONE:
            self.app.notify("ID, label, and catalog model are required", severity="error")
            return
        if not (prompt or "").strip():
            self.app.notify("System prompt is required", severity="error")
            return

        common = [
            "--label", label,
            "--catalog-model", str(catalog),
            "--output-modality", str(out_mod),
            "--output-path", out_path or ".agent/utility",
            "--run-as-agent", str(run_as),
        ]
        if self.is_edit:
            args = ["utility", "model", "update", um_id, *common]
            args.append("--enabled" if enabled else "--disabled")
        else:
            args = ["utility", "model", "add", "--id", um_id, *common]
            if not enabled:
                args.append("--disabled")
        args.extend(_prompt_cli_args(um_id, prompt))

        ok, _, err = _run_agictl(args)
        if ok:
            self.app.notify(f"Utility Model '{um_id}' saved", title="Utility Models")
            if hasattr(self.parent_screen, "_refresh_utility_models_table"):
                self.parent_screen._refresh_utility_models_table()
            self.app.pop_screen()
        else:
            self.app.notify(err or "Save failed", severity="error", title="Utility Models")
