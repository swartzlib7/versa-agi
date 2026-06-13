"""Model Manager Modal — dashboard CRUD for the unified model catalog.

Drives the Edition 2.x isolation model: all edits write the user layer
([catalog_custom]/[providers_custom]) via `agictl`, which auto-runs `model sync`
to regenerate the derived sections + paths.env. The baseline ([catalog]/
[providers]) stays owned by setup.ini and is never touched here.

Reads come from `agictl model catalog list` / `agictl provider list` (the merged
view, with an `origin` tag of baseline|custom|override per row). Writes go through
`agictl model catalog …` / `agictl provider …`. Nothing here edits models.ini
directly — that keeps the dashboard consistent with the CLI and setup.sh.
"""

import json
import subprocess

from textual.app import ComposeResult
from textual.binding import Binding
from textual.screen import ModalScreen
from textual.containers import VerticalScroll, Horizontal, Vertical
from textual.widgets import Static, Button, Input, DataTable, Select, Checkbox


# The stored catalog `class` (cloud|third_party|local) still drives routing, but
# the dashboard presents it in the simpler "shipped / custom / local" language
# the user reasons about. cloud == Google API, third_party == any other API.
_CLASS_CHOICES = [
    ("☁ Cloud · Google", "cloud"),
    ("☁ Cloud · other provider", "third_party"),
    ("🖥 Local · SYCL / Ollama", "local"),
]
_CLASS_ORDER = {"cloud": 0, "third_party": 1, "local": 2}
_COMMON_LC_CLASSES = [
    ("ChatOpenAI", "ChatOpenAI"),
    ("ChatAnthropic", "ChatAnthropic"),
    ("ChatGoogleGenerativeAI", "ChatGoogleGenerativeAI"),
    ("ChatOllama", "ChatOllama"),
]


def _run_agictl(args, timeout=25):
    """Run `sudo agictl <args>` and parse the trailing JSON line.

    Returns (ok: bool, data: dict, err: str). Never raises.
    """
    try:
        proc = subprocess.run(
            ["sudo", "agictl"] + args,
            capture_output=True, text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return False, {}, "command timed out"
    except Exception as e:  # noqa: BLE001
        return False, {}, str(e)

    data = {}
    if proc.stdout:
        for line in reversed(proc.stdout.strip().splitlines()):
            line = line.strip()
            if line.startswith("{"):
                try:
                    data = json.loads(line)
                    break
                except Exception:  # noqa: BLE001
                    continue
    ok = bool(data.get("success")) if data else (proc.returncode == 0)
    err = ""
    if not ok:
        err = data.get("error") or (proc.stderr.strip() or "Unknown error")
    return ok, data, err


def _yn(flag):
    return "[green]✓[/]" if flag else "[dim]·[/]"


def _origin_type(origin):
    """Map an isolation-model origin to the user-facing Type label.

    baseline → shipped (came from setup.ini), override → shipped* (a shipped row
    with local edits), custom → custom (added via CLI/dashboard).
    """
    if origin == "custom":
        return "[cyan]custom[/]"
    if origin == "override":
        return "[yellow]shipped*[/]"
    return "[dim]shipped[/]"


def _model_type(m):
    """Type column for a model row: local takes precedence over shipped/custom."""
    if m.get("class") == "local":
        return "[magenta]local[/]"
    return _origin_type(m.get("origin"))


def _local_backend_label(paths_env="/etc/versa-agi/paths.env"):
    """Resolve the *actual* local inference backend from VERSA_GPU_BACKEND.

    The catalog slug for local rows is a static placeholder ('ollama' → ChatOllama),
    but the harness routes local models by VERSA_GPU_BACKEND, not the slug:
      intel / remote → SYCL llama.cpp (OpenAI-compatible)  → show 'sycl'
      standard       → Ollama                              → show 'ollama'
    so we surface the real backend instead of the misleading placeholder.
    """
    backend = ""
    try:
        with open(paths_env) as f:
            for line in f:
                if line.startswith("VERSA_GPU_BACKEND="):
                    backend = line.split("=", 1)[1].strip().strip('"')
                    break
    except Exception:  # noqa: BLE001
        pass
    if backend in ("intel", "remote"):
        return "sycl"
    if backend == "standard":
        return "ollama"
    return "local"


def _model_provider(m, local_label):
    """Provider column display — local rows show the real backend (sycl/ollama)."""
    return local_label if m.get("class") == "local" else m.get("provider", "")


class ModelManagerModal(ModalScreen):
    """View and edit the model catalog and provider registry."""

    BINDINGS = [Binding("escape", "close", "Close")]

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._models_by_key = {}
        self._providers_by_slug = {}
        self._dirty = False  # whether anything changed (drives parent refresh on close)

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="model-manager-dialog"):
            yield Static("[bold]🧩 Model Manager[/]", id="msg-dialog-header")
            yield Static(
                "[dim]Type: [/][dim]shipped[/][dim] = provisioned by setup.ini · [/]"
                "[yellow]shipped*[/][dim] = shipped + local edits · [/]"
                "[cyan]custom[/][dim] = added here/CLI · [/][magenta]local[/][dim] = on-box "
                "SYCL/Ollama. Open a row (Edit / click) to change any field; edits write the "
                "user layer and auto-sync paths.env.[/]",
                id="mm-help",
            )

            yield Static("[bold cyan]Models[/]", classes="mm-subheader")
            yield DataTable(id="mm-models-table")
            with Horizontal(classes="mm-actions"):
                yield Button("✎ Edit", id="mm-model-edit", variant="primary", classes="panel-btn")
                yield Button("✖ Remove", id="mm-model-remove", variant="error", classes="panel-btn")
                yield Button("＋ Add Model", id="mm-model-add", variant="success", classes="panel-btn")

            yield Static("[bold cyan]Providers[/]", classes="mm-subheader")
            yield DataTable(id="mm-providers-table")
            with Horizontal(classes="mm-actions"):
                yield Button("✎ Edit", id="mm-prov-edit", variant="primary", classes="panel-btn")
                yield Button("✖ Remove", id="mm-prov-remove", variant="error", classes="panel-btn")
                yield Button("＋ Add Provider", id="mm-prov-add", variant="success", classes="panel-btn")

            yield Static("", id="mm-feedback")
            with Horizontal(id="msg-dialog-actions"):
                yield Button("🔄 Sync now", id="mm-sync", variant="primary")
                yield Button("Close", classes="dismiss-btn", variant="default", id="mm-close")

    def on_mount(self) -> None:
        mt = self.query_one("#mm-models-table", DataTable)
        mt.cursor_type = "row"
        mt.add_columns("Key", "Type", "Provider", "En", "COA", "Ctx Rec", "Ctx Max", "Label")
        pt = self.query_one("#mm-providers-table", DataTable)
        pt.cursor_type = "row"
        pt.add_columns("Slug", "Type", "En", "LangChain Class", "Label")
        self._reload()

    # ── Data ────────────────────────────────────────────
    def _reload(self) -> None:
        """Re-fetch catalog + providers from agictl and repopulate the tables."""
        ok, data, err = _run_agictl(["model", "catalog", "list"])
        models = data.get("models", []) if ok else []
        ok_p, data_p, err_p = _run_agictl(["provider", "list"])
        providers = data_p.get("providers", []) if ok_p else []

        self._models_by_key = {m["key"]: m for m in models}
        self._providers_by_slug = {p["slug"]: p for p in providers}

        local_label = _local_backend_label()
        mt = self.query_one("#mm-models-table", DataTable)
        mt.clear()
        for m in sorted(models, key=lambda r: (_CLASS_ORDER.get(r["class"], 9), r["key"])):
            mt.add_row(
                m["key"],
                _model_type(m),
                _model_provider(m, local_label),
                _yn(m["enabled"]),
                _yn(m["coa"]),
                str(m.get("ctx_recommended", 0)),
                str(m.get("ctx_max", 0)),
                m.get("label", ""),
                key=m["key"],
            )

        pt = self.query_one("#mm-providers-table", DataTable)
        pt.clear()
        for p in sorted(providers, key=lambda r: r["slug"]):
            pt.add_row(
                p["slug"],
                _origin_type(p.get("origin")),
                _yn(p["enabled"]),
                p.get("cls", ""),
                p.get("label", ""),
                key=p["slug"],
            )

        if not ok or not ok_p:
            self._feedback(f"[red]Failed to load catalog: {err or err_p}[/]")

    def _selected_key(self, table_id) -> str:
        table = self.query_one(table_id, DataTable)
        if table.row_count == 0:
            return ""
        try:
            cell_key = table.coordinate_to_cell_key(table.cursor_coordinate)
            return cell_key.row_key.value or ""
        except Exception:  # noqa: BLE001
            return ""

    def _feedback(self, markup) -> None:
        self.query_one("#mm-feedback", Static).update(markup)

    def _apply(self, args, success_msg) -> None:
        """Run a mutating agictl command, surface feedback, and reload on success."""
        ok, data, err = _run_agictl(args)
        if ok:
            self._dirty = True
            extra = data.get("message", "")
            self._feedback(f"[green]✅ {success_msg}[/]" + (f"  [dim]{extra}[/]" if extra else ""))
            self._reload()
        else:
            self._feedback(f"[red]❌ {err}[/]")

    # ── Events ──────────────────────────────────────────
    def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id
        if bid == "mm-close":
            self._close()
        elif bid == "mm-sync":
            ok, _data, err = _run_agictl(["model", "sync"])
            self._dirty = self._dirty or ok
            self._feedback("[green]✅ Synced derived config + paths.env[/]" if ok
                           else f"[red]❌ {err}[/]")
        elif bid == "mm-model-add":
            self.app.push_screen(
                CatalogFormModal(list(self._providers_by_slug.keys())),
                callback=self._on_model_form,
            )
        elif bid == "mm-model-edit":
            self._edit_selected_model()
        elif bid == "mm-model-remove":
            key = self._selected_key("#mm-models-table")
            if not key:
                self._feedback("[yellow]Select a model row first.[/]")
                return
            self._apply(["model", "catalog", "remove", key], f"Removed '{key}'")
        elif bid == "mm-prov-add":
            self.app.push_screen(ProviderFormModal(), callback=self._on_provider_form)
        elif bid == "mm-prov-edit":
            self._edit_selected_provider()
        elif bid == "mm-prov-remove":
            slug = self._selected_key("#mm-providers-table")
            if not slug:
                self._feedback("[yellow]Select a provider row first.[/]")
                return
            self._apply(["provider", "remove", slug], f"Removed provider '{slug}'")

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        """Clicking / Enter on a row opens its Edit form (codebase convention)."""
        if event.data_table.id == "mm-models-table":
            self._edit_selected_model()
        elif event.data_table.id == "mm-providers-table":
            self._edit_selected_provider()

    def _edit_selected_model(self) -> None:
        key = self._selected_key("#mm-models-table")
        if not key:
            self._feedback("[yellow]Select a model row first.[/]")
            return
        self.app.push_screen(
            CatalogFormModal(list(self._providers_by_slug.keys()),
                             existing=self._models_by_key.get(key)),
            callback=self._on_model_form,
        )

    def _edit_selected_provider(self) -> None:
        slug = self._selected_key("#mm-providers-table")
        if not slug:
            self._feedback("[yellow]Select a provider row first.[/]")
            return
        self.app.push_screen(
            ProviderFormModal(existing=self._providers_by_slug.get(slug)),
            callback=self._on_provider_form,
        )

    def _on_model_form(self, result) -> None:
        if not result:
            return
        if result.get("_mode") == "add":
            args = ["model", "catalog", "add", result["key"],
                    "--class", result["class"], "--provider", result["provider"],
                    "--label", result["label"],
                    "--ctx-recommended", str(result["ctx_recommended"]),
                    "--ctx-max", str(result["ctx_max"]),
                    "--coa-approved" if result["coa"] else "--no-coa-approved",
                    "--enabled" if result["enabled"] else "--disabled"]
            if result.get("gguf_repo") and result.get("gguf_file") and result.get("size_gb"):
                args += ["--gguf-repo", result["gguf_repo"],
                         "--gguf-file", result["gguf_file"],
                         "--size", str(result["size_gb"])]
            self._apply(args, f"Added '{result['key']}'")
        else:
            args = ["model", "catalog", "update", result["key"],
                    "--class", result["class"], "--provider", result["provider"],
                    "--label", result["label"],
                    "--ctx-recommended", str(result["ctx_recommended"]),
                    "--ctx-max", str(result["ctx_max"]),
                    "--coa-approve" if result["coa"] else "--coa-revoke",
                    "--enable" if result["enabled"] else "--disable"]
            self._apply(args, f"Updated '{result['key']}'")

    def _on_provider_form(self, result) -> None:
        if not result:
            return
        verb = "update" if result.get("_mode") == "edit" else "add"
        args = ["provider", verb, result["slug"],
                "--label", result["label"], "--class", result["cls"],
                "--enable" if result["enabled"] else "--disable"]
        msg = (f"Updated provider '{result['slug']}'" if verb == "update"
               else f"Registered provider '{result['slug']}'")
        self._apply(args, msg)

    def action_close(self) -> None:
        self._close()

    def _close(self) -> None:
        if self._dirty:
            try:
                self.app._refresh_all_data()
            except Exception:  # noqa: BLE001
                pass
        self.app.pop_screen()


class CatalogFormModal(ModalScreen):
    """Add or edit a single catalog model (writes [catalog_custom])."""

    def __init__(self, providers, existing=None, **kwargs):
        super().__init__(**kwargs)
        self._providers = providers or []
        self._existing = existing
        self._edit = existing is not None

    def compose(self) -> ComposeResult:
        e = self._existing or {}
        title = "✎ Edit Model" if self._edit else "＋ Add Model"
        prov_opts = [(p, p) for p in self._providers] or [("(none)", "")]
        with VerticalScroll(id="msg-dialog"):
            yield Static(f"[bold]{title}[/]", id="msg-dialog-header")

            yield Static("[b]Model key[/]  [dim](e.g. grok-4, gemma4:e4b)[/]")
            yield Input(value=e.get("key", ""), placeholder="model id", id="f-key",
                        disabled=self._edit)

            yield Static("[b]Class[/]")
            yield Select(_CLASS_CHOICES, value=e.get("class", "cloud"),
                         allow_blank=False, id="f-class")

            yield Static("[b]Provider[/]")
            yield Select(prov_opts,
                         value=e.get("provider", prov_opts[0][1]),
                         allow_blank=False, id="f-provider")

            yield Static("[b]Display label[/]")
            yield Input(value=e.get("label", ""), placeholder="shown in pickers", id="f-label")

            with Horizontal(classes="mm-form-row"):
                with Vertical(classes="mm-form-col"):
                    yield Static("[b]Ctx recommended[/]  [dim](0 for cloud)[/]")
                    yield Input(value=str(e.get("ctx_recommended", 0)), type="integer",
                                id="f-ctx-rec")
                with Vertical(classes="mm-form-col"):
                    yield Static("[b]Ctx max[/]")
                    yield Input(value=str(e.get("ctx_max", 0)), type="integer", id="f-ctx-max")

            with Horizontal(classes="mm-form-row"):
                yield Checkbox("Enabled", value=e.get("enabled", True), id="f-enabled")
                yield Checkbox("COA approved", value=e.get("coa", False), id="f-coa")

            if not self._edit:
                yield Static(
                    "[dim]Local only — optional SYCL GGUF (also registers [sycl_models]):[/]")
                yield Input(placeholder="HuggingFace repo (org/model)", id="f-gguf-repo")
                yield Input(placeholder="GGUF filename", id="f-gguf-file")
                yield Input(placeholder="approx size GB", type="integer", id="f-size")

            yield Static("", id="f-error")
            with Horizontal(id="msg-dialog-actions"):
                yield Button("Save", variant="success", id="f-save")
                yield Button("Cancel", classes="dismiss-btn", variant="default", id="f-cancel")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "f-cancel":
            self.dismiss(None)
        elif event.button.id == "f-save":
            self._submit()

    def _submit(self) -> None:
        key = self.query_one("#f-key", Input).value.strip()
        label = self.query_one("#f-label", Input).value.strip()
        provider = self.query_one("#f-provider", Select).value
        if not key:
            self.query_one("#f-error", Static).update("[red]Model key is required.[/]")
            return
        if not label:
            self.query_one("#f-error", Static).update("[red]Display label is required.[/]")
            return
        if not provider:
            self.query_one("#f-error", Static).update(
                "[red]No provider available — add a provider first.[/]")
            return

        def _int(wid):
            try:
                return int(self.query_one(wid, Input).value.strip() or "0")
            except ValueError:
                return 0

        result = {
            "_mode": "edit" if self._edit else "add",
            "key": key,
            "class": self.query_one("#f-class", Select).value,
            "provider": provider,
            "label": label,
            "ctx_recommended": _int("#f-ctx-rec"),
            "ctx_max": _int("#f-ctx-max"),
            "enabled": self.query_one("#f-enabled", Checkbox).value,
            "coa": self.query_one("#f-coa", Checkbox).value,
        }
        if not self._edit:
            result["gguf_repo"] = self.query_one("#f-gguf-repo", Input).value.strip()
            result["gguf_file"] = self.query_one("#f-gguf-file", Input).value.strip()
            try:
                result["size_gb"] = int(self.query_one("#f-size", Input).value.strip() or "0")
            except ValueError:
                result["size_gb"] = 0
        self.dismiss(result)


class ProviderFormModal(ModalScreen):
    """Add or edit a provider (writes [providers_custom]). Keys set via 🔑 API Keys.

    For a baseline provider this writes a full-row override; for a custom one it
    edits the existing row. The slug is immutable once set.
    """

    def __init__(self, existing=None, **kwargs):
        super().__init__(**kwargs)
        self._existing = existing
        self._edit = existing is not None

    def compose(self) -> ComposeResult:
        e = self._existing or {}
        title = "✎ Edit Provider" if self._edit else "＋ Add Provider"
        cls_val = e.get("cls") or "ChatOpenAI"
        cls_opts = list(_COMMON_LC_CLASSES)
        if cls_val not in [v for _, v in cls_opts]:
            cls_opts.append((cls_val, cls_val))
        with VerticalScroll(id="msg-dialog"):
            yield Static(f"[bold]{title}[/]", id="msg-dialog-header")
            yield Static("[b]Slug[/]  [dim](e.g. mistral, deepseek)[/]")
            yield Input(value=e.get("slug", ""), placeholder="provider slug", id="p-slug",
                        disabled=self._edit)
            yield Static("[b]Display name[/]")
            yield Input(value=e.get("label", ""), placeholder="e.g. Mistral", id="p-label")
            yield Static("[b]LangChain class[/]")
            yield Select(cls_opts, value=cls_val, allow_blank=False, id="p-class")
            yield Checkbox("Enabled", value=e.get("enabled", False), id="p-enabled")
            yield Static(
                "[dim]Set the API key afterwards via the 🔑 API Keys modal "
                "(agictl system set-key).[/]")
            yield Static("", id="p-error")
            with Horizontal(id="msg-dialog-actions"):
                yield Button("Save", variant="success", id="p-save")
                yield Button("Cancel", classes="dismiss-btn", variant="default", id="p-cancel")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "p-cancel":
            self.dismiss(None)
        elif event.button.id == "p-save":
            slug = self.query_one("#p-slug", Input).value.strip()
            label = self.query_one("#p-label", Input).value.strip()
            if not slug or not label:
                self.query_one("#p-error", Static).update(
                    "[red]Slug and display name are required.[/]")
                return
            self.dismiss({
                "_mode": "edit" if self._edit else "add",
                "slug": slug,
                "label": label,
                "cls": self.query_one("#p-class", Select).value,
                "enabled": self.query_one("#p-enabled", Checkbox).value,
            })
