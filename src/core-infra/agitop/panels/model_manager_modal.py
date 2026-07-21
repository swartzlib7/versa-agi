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
import os
import re
import subprocess

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.screen import ModalScreen
from textual.containers import VerticalScroll, Horizontal, Vertical
from textual.widgets import Static, Button, Input, Label, DataTable, Select, Checkbox, TabbedContent, TabPane
from agitop.widgets.clear_checkbox import ClearCheckbox

from agitop.panels.modality_format import format_io_modalities
from agitop.widgets.provider_brand_icon import provider_brand_class, provider_import_button_label


# The stored catalog `class` (cloud|third_party|local) still drives routing, but
# the dashboard presents it in the simpler "shipped / custom / local" language
# the user reasons about. cloud == Google API, third_party == any other API.
_CLASS_CHOICES = [
    ("☁ Cloud · Google", "cloud"),
    ("☁ Cloud · other provider", "third_party"),
    ("🖥 Local · Ollama / llama.cpp", "local"),
]
_CLASS_ORDER = {"cloud": 0, "third_party": 1, "local": 2}
_COMMON_LC_CLASSES = [
    ("ChatOpenAI", "ChatOpenAI"),
    ("ChatAnthropic", "ChatAnthropic"),
    ("ChatGoogleGenerativeAI", "ChatGoogleGenerativeAI"),
    ("ChatOllama", "ChatOllama"),
]
_WORK_MODALITY_CHOICES = [
    ("fast", "fast"),
    ("balanced", "balanced"),
    ("reasoning", "reasoning"),
    ("code", "code"),
    ("local", "local"),
]

_FALLBACK_REASONING_OPTS = [
    ("inherit", ""),
    ("none", "none"),
    ("minimal", "minimal"),
    ("low", "low"),
    ("medium", "medium"),
    ("high", "high"),
    ("max", "max"),
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


def _configured_source_providers():
    """Providers with an API key — drives Add Model Import buttons.

    Prefer ``agictl model source providers``; fall back to reading keys from
    /etc when sudo/agictl is unavailable so a keyed OpenRouter still appears.
    """
    ok, data, _err = _run_agictl(["model", "source", "providers"])
    if ok:
        rows = [
            {"slug": p["slug"], "label": p.get("label", p["slug"])}
            for p in data.get("providers", [])
            if p.get("configured")
        ]
        if rows:
            return rows

    # Local fallback (key present → show Import)
    labels = {
        "google": "Google",
        "xai": "xAI",
        "openai": "OpenAI",
        "anthropic": "Anthropic",
        "openrouter": "OpenRouter",
    }
    key_env = {
        "google": "GEMINI_API_KEY",
        "xai": "XAI_API_KEY",
        "openai": "OPENAI_API_KEY",
        "anthropic": "ANTHROPIC_API_KEY",
        "openrouter": "OPENROUTER_API_KEY",
    }
    found = []
    for slug, env_var in key_env.items():
        if _provider_key_present(env_var, slug):
            found.append({"slug": slug, "label": labels[slug]})
    return found


def _provider_key_present(env_var: str, slug: str) -> bool:
    """True if a non-empty key exists in env files or setup.ini for this provider."""
    if (os.environ.get(env_var) or "").strip():
        return True
    for path in (
        "/etc/versa-agi/provider_keys.env",
        "/etc/versa-agi/coa.env",
        "/etc/versa-agi/inference_endpoint.env",
    ):
        try:
            with open(path) as f:
                for line in f:
                    if line.startswith(f"{env_var}="):
                        val = line.split("=", 1)[1].strip().strip('"').strip("'")
                        if val:
                            return True
        except OSError:
            continue
    setup_keys = {
        "google": ("gemini", "api_key"),
        "xai": ("third_party", "xai_api_key"),
        "openai": ("third_party", "openai_api_key"),
        "anthropic": ("third_party", "anthropic_api_key"),
        "openrouter": ("third_party", "openrouter_api_key"),
    }
    sec_opt = setup_keys.get(slug)
    if not sec_opt:
        return False
    section, option = sec_opt
    try:
        in_sec = False
        with open("/etc/versa-agi/setup.ini") as f:
            for line in f:
                s = line.strip()
                if s.startswith("[") and s.endswith("]"):
                    in_sec = s == f"[{section}]"
                elif in_sec and s.startswith(f"{option}="):
                    return bool(s.split("=", 1)[1].strip())
    except OSError:
        pass
    return False



def _yn(flag):
    return "[green]✓[/]" if flag else "[dim]·[/]"


def _enabled_val(flag):
    return "[green]yes[/]" if flag else "[dim]no[/]"


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
    """Resolve active local provider slug (ollama | llamacpp) from VERSA_GPU_BACKEND."""
    backend = ""
    try:
        with open(paths_env) as f:
            for line in f:
                if line.startswith("VERSA_GPU_BACKEND="):
                    backend = line.split("=", 1)[1].strip().strip('"')
                    break
    except OSError:
        pass
    try:
        from harness.model_params import read_gpu_backend, resolve_local_runtime
        if not backend:
            backend = read_gpu_backend()
        return resolve_local_runtime(backend)
    except Exception:  # noqa: BLE001
        if backend in ("intel", "remote"):
            return "llamacpp"
        if backend == "standard":
            return "ollama"
        return "local"


def _catalog_reasoning_options(
    model_key: str = "",
    local_runtime: str | None = None,
) -> list[tuple[str, str]]:
    """Reasoning effort Select options for a catalog model (from model:<key> params)."""
    try:
        from harness.model_params import reasoning_effort_select_options
        opts = reasoning_effort_select_options((model_key or "").strip(), local_runtime)
        return [("inherit" if label == "Inherit" else label, val) for label, val in opts]
    except Exception:  # noqa: BLE001
        return list(_FALLBACK_REASONING_OPTS)


def _model_provider(m):
    """Provider column display — catalog slug (ollama / llamacpp for local rows)."""
    return m.get("provider", "")


class ModelRemoveConfirmModal(ModalScreen):
    """Confirmation dialog for removing a catalog model."""

    CSS = """
    ModelRemoveConfirmModal {
        align: center middle;
        background: $surface 80%;
    }
    #model-remove-dialog {
        width: 64;
        height: auto;
        padding: 1 2;
        border: heavy $error;
        background: $surface;
    }
    #model-remove-actions {
        margin-top: 1;
        height: auto;
        align: center middle;
    }
    #model-remove-actions Button {
        width: 1fr;
        margin: 0 1;
        min-width: 16;
        height: 3;
    }
    """

    def __init__(self, key: str, label: str = "", origin: str = "", **kwargs):
        super().__init__(**kwargs)
        self.key = key
        self.label = label
        self.origin = origin

    def compose(self) -> ComposeResult:
        title = f"[bold red]⚠ Remove Model: {self.key}[/]"
        if self.label and self.label != self.key:
            title += f"\n[dim]{self.label}[/]"
        if self.origin in ("baseline", "override"):
            body = (
                "This model is provisioned by setup.ini.\n"
                "Remove will disable it via a custom override.\n"
                "To drop it entirely, remove it from setup.ini.\n\n"
                "[bold]This cannot be undone from Reset alone.[/]"
            )
        else:
            body = (
                "This deletes the custom catalog entry from [catalog_custom]\n"
                "and clears any per-model default params.\n\n"
                "[bold]This cannot be undone.[/]"
            )
        with Vertical(id="model-remove-dialog"):
            yield Static(f"{title}\n")
            yield Static(body)
            with Horizontal(id="model-remove-actions"):
                yield Button("Remove", variant="error", id="btn-model-remove-confirm")
                yield Button("Close", classes="dismiss-btn", variant="default", id="btn-model-remove-cancel")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        self.dismiss(event.button.id == "btn-model-remove-confirm")


class ProviderRemoveConfirmModal(ModalScreen):
    """Confirmation dialog for removing a provider registry entry."""

    CSS = """
    ProviderRemoveConfirmModal {
        align: center middle;
        background: $surface 80%;
    }
    #provider-remove-dialog {
        width: 64;
        height: auto;
        padding: 1 2;
        border: heavy $error;
        background: $surface;
    }
    #provider-remove-actions {
        margin-top: 1;
        height: auto;
        align: center middle;
    }
    #provider-remove-actions Button {
        width: 1fr;
        margin: 0 1;
        min-width: 16;
        height: 3;
    }
    """

    def __init__(self, slug: str, label: str = "", origin: str = "", **kwargs):
        super().__init__(**kwargs)
        self.slug = slug
        self.label = label
        self.origin = origin

    def compose(self) -> ComposeResult:
        title = f"[bold red]⚠ Remove Provider: {self.slug}[/]"
        if self.label and self.label != self.slug:
            title += f"\n[dim]{self.label}[/]"
        if self.origin in ("baseline", "override"):
            body = (
                "This provider is provisioned by setup.ini.\n"
                "Remove will disable it via a custom override.\n"
                "Catalog models referencing it remain until edited.\n"
                "To drop it entirely, remove it from setup.ini.\n\n"
                "[bold]This cannot be undone from Reset alone.[/]"
            )
        else:
            body = (
                "This deletes the custom provider from [providers_custom].\n"
                "Catalog models referencing it remain until edited.\n\n"
                "[bold]This cannot be undone.[/]"
            )
        with Vertical(id="provider-remove-dialog"):
            yield Static(f"{title}\n")
            yield Static(body)
            with Horizontal(id="provider-remove-actions"):
                yield Button("Remove", variant="error", id="btn-provider-remove-confirm")
                yield Button("Close", classes="dismiss-btn", variant="default", id="btn-provider-remove-cancel")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        self.dismiss(event.button.id == "btn-provider-remove-confirm")


class ModelManagerModal(ModalScreen):
    """View and edit the model catalog and provider registry."""

    BINDINGS = [Binding("escape", "close", "Close")]

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._models_by_key = {}
        self._providers_by_slug = {}
        self._dirty = False  # whether anything changed (drives parent refresh on close)

    def compose(self) -> ComposeResult:
        with Vertical(id="model-manager-shell"):
            yield Static("[bold]🧩 Model Manager[/]", id="msg-dialog-header")
            yield Static(
                "[dim]Type: [/][dim]shipped[/][dim] = provisioned by setup.ini · [/]"
                "[yellow]shipped*[/][dim] = shipped + local edits · [/]"
                "[cyan]custom[/][dim] = added here/CLI · [/][magenta]local[/][dim] = on-box "
                "Ollama/llama.cpp. [/][yellow]↩ Reset[/][dim] drops custom overrides back to "
                "shipped baseline (custom rows: use Remove).[/]",
                id="mm-help",
            )

            with TabbedContent(initial="mm-models-tab", id="model-manager-tabs"):
                with TabPane("Models", id="mm-models-tab"):
                    with Vertical(id="mm-models-pane"):
                        yield DataTable(id="mm-models-table")

                with TabPane("Providers", id="mm-providers-tab"):
                    with Vertical(id="mm-providers-pane"):
                        yield DataTable(id="mm-providers-table")

            yield Static("", id="mm-feedback")

            with Horizontal(id="model-manager-footer"):
                yield Button("✎ Edit", id="mm-edit", variant="primary")
                yield Button("↩ Reset", id="mm-reset", variant="warning")
                yield Button("✖ Remove", id="mm-remove", variant="error")
                yield Button("＋ Add", id="mm-add", variant="success")
                yield Button("Close", classes="dismiss-btn", variant="default", id="mm-close")

    def on_mount(self) -> None:
        mt = self.query_one("#mm-models-table", DataTable)
        mt.cursor_type = "row"
        mt.add_columns("Label", "Key", "Type", "Work", "Rtr", "En", "COA", "Rsn", "I/O", "$/M")
        pt = self.query_one("#mm-providers-table", DataTable)
        pt.cursor_type = "row"
        pt.add_columns("Label", "Slug", "Type", "En", "LangChain Class")
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
            pin = m.get("prompt_per_m")
            pout = m.get("completion_per_m")
            if pin or pout:
                price = f"{pin:.3g}/{pout:.3g}" if pin and pout else (f"{pin:.3g}/—" if pin else f"—/{pout:.3g}")
            else:
                price = "—"
            mt.add_row(
                m.get("label", ""),
                m["key"],
                _model_type(m),
                m.get("work_modality", "balanced"),
                _yn(m.get("router_eligible")),
                _enabled_val(m.get("enabled", False)),
                _yn(m["coa"]),
                m.get("reasoning_effort", "none"),
                format_io_modalities(
                    m.get("input_modalities", "text"),
                    m.get("output_modalities", "text"),
                ),
                price,
                key=m["key"],
            )

        pt = self.query_one("#mm-providers-table", DataTable)
        pt.clear()
        for p in sorted(providers, key=lambda r: r["slug"]):
            pt.add_row(
                p.get("label", ""),
                p["slug"],
                _origin_type(p.get("origin")),
                _yn(p["enabled"]),
                p.get("cls", ""),
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
        # ── Context-sensitive actions (based on active tab) ──
        elif bid in ("mm-add", "mm-edit", "mm-reset", "mm-remove"):
            tabs = self.query_one("#model-manager-tabs", TabbedContent)
            on_models = tabs.active == "mm-models-tab"
            if bid == "mm-add":
                if on_models:
                    self.app.push_screen(
                        CatalogFormModal(
                            list(self._providers_by_slug.keys()),
                            source_providers=_configured_source_providers(),
                        ),
                        callback=self._on_model_form,
                    )
                else:
                    self.app.push_screen(ProviderFormModal(), callback=self._on_provider_form)
            elif bid == "mm-edit":
                if on_models:
                    self._edit_selected_model()
                else:
                    self._edit_selected_provider()
            elif bid == "mm-reset":
                if on_models:
                    key = self._selected_key("#mm-models-table")
                    if not key:
                        self._feedback("[yellow]Select a model row first.[/]")
                        return
                    m = self._models_by_key.get(key) or {}
                    if m.get("origin") == "custom":
                        self._feedback("[yellow]Custom models have no baseline — use Remove.[/]")
                        return
                    self._apply(["model", "catalog", "reset", key], f"Reset '{key}' to baseline")
                else:
                    slug = self._selected_key("#mm-providers-table")
                    if not slug:
                        self._feedback("[yellow]Select a provider row first.[/]")
                        return
                    p = self._providers_by_slug.get(slug) or {}
                    if p.get("origin") == "custom":
                        self._feedback("[yellow]Custom providers have no baseline — use Remove.[/]")
                        return
                    self._apply(["provider", "reset", slug], f"Reset provider '{slug}' to baseline")
            elif bid == "mm-remove":
                if on_models:
                    key = self._selected_key("#mm-models-table")
                    if not key:
                        self._feedback("[yellow]Select a model row first.[/]")
                        return
                    m = self._models_by_key.get(key) or {}
                    self.app.push_screen(
                        ModelRemoveConfirmModal(key, m.get("label", ""), m.get("origin", "")),
                        callback=lambda confirmed: self._on_model_remove_confirmed(confirmed, key),
                    )
                else:
                    slug = self._selected_key("#mm-providers-table")
                    if not slug:
                        self._feedback("[yellow]Select a provider row first.[/]")
                        return
                    p = self._providers_by_slug.get(slug) or {}
                    self.app.push_screen(
                        ProviderRemoveConfirmModal(slug, p.get("label", ""), p.get("origin", "")),
                        callback=lambda confirmed: self._on_provider_remove_confirmed(confirmed, slug),
                    )

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

    def _on_model_remove_confirmed(self, confirmed: bool, key: str) -> None:
        if confirmed:
            self._apply(["model", "catalog", "remove", key], f"Removed '{key}'")

    def _on_provider_remove_confirmed(self, confirmed: bool, slug: str) -> None:
        if confirmed:
            self._apply(["provider", "remove", slug], f"Removed provider '{slug}'")

    def _on_model_form(self, result) -> None:
        if not result:
            return
        param_args = []
        if "temperature" in result:
            param_args += ["--temperature", str(result["temperature"])]
        if result.get("reasoning_effort") is not None:
            param_args += ["--reasoning-effort", result["reasoning_effort"]]
        if "reasoning_max_tokens" in result:
            param_args += ["--reasoning-max-tokens", str(result["reasoning_max_tokens"])]
        if result.get("extra_json"):
            param_args += ["--extra", result["extra_json"]]
        if result.get("allowed_reasoning_efforts_csv"):
            param_args += ["--allowed-reasoning-efforts", result["allowed_reasoning_efforts_csv"]]
        if result.get("think_mode"):
            param_args += ["--think-mode", result["think_mode"]]

        if result.get("_mode") == "add":
            args = ["model", "catalog", "add", result["key"],
                    "--class", result["class"], "--provider", result["provider"],
                    "--label", result["label"],
                    "--ctx-recommended", str(result["ctx_recommended"]),
                    "--ctx-max", str(result["ctx_max"]),
                    "--work-modality", result["work_modality"],
                    "--input-modalities", result["input_modalities"],
                    "--output-modalities", result["output_modalities"],
                    "--coa-approved" if result["coa"] else "--no-coa-approved",
                    "--enabled" if result["enabled"] else "--disabled",
                    "--router-eligible" if result["router_eligible"] else "--no-router-eligible"]
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
                    "--work-modality", result["work_modality"],
                    "--input-modalities", result["input_modalities"],
                    "--output-modalities", result["output_modalities"],
                    "--coa-approve" if result["coa"] else "--coa-revoke",
                    "--enable" if result["enabled"] else "--disable",
                    "--router-eligible" if result["router_eligible"] else "--no-router-eligible"]
            self._apply(args, f"Updated '{result['key']}'")
        if param_args:
            self._apply(["model", "params", "set", f"model:{result['key']}"] + param_args,
                        f"Set default params for '{result['key']}'")
        elif result.get("_clear_params"):
            self._apply(["model", "params", "clear", f"model:{result['key']}"],
                        f"Cleared default params for '{result['key']}'")

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

    def __init__(self, providers, existing=None, source_providers=None, **kwargs):
        super().__init__(**kwargs)
        self._providers = providers or []
        self._existing = existing
        self._edit = existing is not None
        self._had_custom_params = False
        self._source_providers = source_providers

    def compose(self) -> ComposeResult:
        e = self._existing or {}
        prov_opts = [(p, p) for p in self._providers] or [("(none)", "")]
        local_runtime = _local_backend_label()
        prov_values = {v for _, v in prov_opts}
        default_provider = e.get("provider")
        if not default_provider:
            if e.get("class") == "local" and local_runtime in prov_values:
                default_provider = local_runtime
            else:
                default_provider = prov_opts[0][1]
        if default_provider not in prov_values:
            default_provider = prov_opts[0][1]
        reasoning_ctx = (e.get("provider") or local_runtime) if e.get("class") == "local" else local_runtime
        reasoning_opts = _catalog_reasoning_options(e.get("key", ""), reasoning_ctx)
        with Vertical(id="catalog-form-shell"):
            if self._edit:
                yield Static("[bold]✎ Edit Model[/]", id="msg-dialog-header")
            else:
                yield Static("[bold]Add Model[/]", id="catalog-form-title")
                yield Static("[dim]Import from provider API:[/]", id="catalog-form-import-label")
                with Horizontal(id="catalog-form-header"):
                    for prov in (self._source_providers or []):
                        slug = prov["slug"]
                        brand = provider_brand_class(slug)
                        yield Button(
                            provider_import_button_label(slug, prov["label"]),
                            id=f"f-import-{slug}",
                            classes=f"provider-import-btn {brand}",
                        )

            with VerticalScroll(id="catalog-form-scroll"):
                with Horizontal(classes="mm-form-row"):
                    with Vertical(classes="mm-form-col"):
                        yield Static("[b]Model key[/]  [dim](e.g. grok-4, gemma4:e4b)[/]")
                        yield Input(value=e.get("key", ""), placeholder="model id", id="f-key",
                                    disabled=self._edit)
                    with Vertical(classes="mm-form-col"):
                        yield Static("[b]Display label[/]")
                        yield Input(value=e.get("label", ""), placeholder="shown in pickers", id="f-label")

                with Horizontal(classes="mm-form-row"):
                    with Vertical(classes="mm-form-col"):
                        yield Static("[b]Class[/]")
                        yield Select(_CLASS_CHOICES, value=e.get("class", "cloud"),
                                     allow_blank=False, id="f-class")
                    with Vertical(classes="mm-form-col"):
                        yield Static("[b]Provider[/]")
                        yield Select(prov_opts,
                                     value=default_provider,
                                     allow_blank=False, id="f-provider")

                with Horizontal(classes="mm-form-row"):
                    with Vertical(classes="mm-form-col"):
                        yield Static("[b]Ctx recommended[/]  [dim](0 for cloud)[/]")
                        yield Input(value=str(e.get("ctx_recommended", 0)), type="integer",
                                    id="f-ctx-rec")
                    with Vertical(classes="mm-form-col"):
                        yield Static("[b]Ctx max[/]")
                        yield Input(value=str(e.get("ctx_max", 0)), type="integer", id="f-ctx-max")

                with Horizontal(classes="mm-form-row mm-check-row"):
                    yield ClearCheckbox("Enabled", value=e.get("enabled", True), id="f-enabled")
                    yield ClearCheckbox("COA approved", value=e.get("coa", False), id="f-coa")

                yield Static("[bold cyan]Routing Modalities[/]", classes="mm-section-heading")
                with Horizontal(classes="mm-form-row"):
                    with Vertical(classes="mm-form-col"):
                        yield ClearCheckbox(
                            "Router eligible",
                            value=bool(e.get("router_eligible")),
                            id="f-router-eligible",
                        )
                    with Vertical(classes="mm-form-col"):
                        yield Static("[b]Work modality[/]  [dim](routing tier)[/]")
                        yield Select(
                            _WORK_MODALITY_CHOICES,
                            value=e.get("work_modality", "balanced") or "balanced",
                            allow_blank=False,
                            id="f-work-modality",
                        )
                with Horizontal(classes="mm-form-row"):
                    with Vertical(classes="mm-form-col"):
                        yield Static("[b]Input modalities[/]  [dim](CSV: text,image,audio,video)[/]")
                        yield Input(value=e.get("input_modalities", "text") or "text", id="f-input-modalities")
                    with Vertical(classes="mm-form-col"):
                        yield Static("[b]Output modalities[/]  [dim](CSV: text,image,audio,video)[/]")
                        yield Input(value=e.get("output_modalities", "text") or "text", id="f-output-modalities")

                yield Static("[bold cyan]Default Generation Params[/]  [dim](optional — writes model:<key> layer)[/]")
                if e.get("class") == "local" and reasoning_ctx == "llamacpp":
                    yield Static(
                        "[dim]llamacpp provider: think_mode applies on Ollama only. "
                        "temperature / top_p / num_predict (→ max_tokens) apply here.[/]"
                    )
                from agitop.panels.model_params_ui import compose_catalog_generation_params
                show_think = e.get("class") == "local" or default_provider == "ollama"
                yield from compose_catalog_generation_params(reasoning_opts, show_think_mode=show_think)

                if not self._edit:
                    yield Static(
                        "[dim]Local only — optional SYCL GGUF (also registers [sycl_models]):[/]")
                    yield Input(placeholder="HuggingFace repo (org/model)", id="f-gguf-repo")
                    yield Input(placeholder="GGUF filename", id="f-gguf-file")
                    yield Input(placeholder="approx size GB", type="integer", id="f-size")

                yield Static("", id="f-error")

            with Horizontal(id="catalog-form-footer"):
                yield Button("Save", variant="success", id="f-save")
                yield Button("Model Feedback", variant="warning", id="f-feedback")
                yield Button("Cancel", classes="dismiss-btn", variant="default", id="f-cancel")

    def on_mount(self) -> None:
        if self._edit and self._existing:
            key = self._existing.get("key", "")
            ok, data, _err = _run_agictl(["model", "params", "get", f"model:{key}"])
            if not ok:
                return
            try:
                from agitop.panels.model_params_ui import load_catalog_generation_params

                custom_params = data.get("params") or {}
                resolved = data.get("resolved") or data.get("effective") or {}
                self._had_custom_params = bool(custom_params)
                warnings = load_catalog_generation_params(
                    self,
                    custom_params=custom_params,
                    resolved=resolved,
                    model_key=key,
                    reasoning_opts_fn=lambda: _catalog_reasoning_options(key),
                )
                if warnings:
                    self.query_one("#f-error", Static).update(
                        "[yellow]" + "; ".join(warnings) + "[/]"
                    )
            except Exception:
                pass

    def _prefill_from_source(self, prefill: dict, provider_label: str) -> None:
        """Fill Add Model fields from a provider pick; user saves when ready."""
        key = (prefill.get("key") or "").strip()
        if not key:
            return

        self.query_one("#f-key", Input).value = key
        self.query_one("#f-label", Input).value = prefill.get("label") or key
        self.query_one("#f-ctx-rec", Input).value = str(prefill.get("ctx_recommended", 0))
        self.query_one("#f-ctx-max", Input).value = str(prefill.get("ctx_max", 0))
        self.query_one("#f-input-modalities", Input).value = (
            prefill.get("input_modalities") or "text"
        )
        self.query_one("#f-output-modalities", Input).value = (
            prefill.get("output_modalities") or "text"
        )

        model_class = prefill.get("class") or "third_party"
        provider = prefill.get("provider") or ""
        work = prefill.get("work_modality") or "balanced"

        class_sel = self.query_one("#f-class", Select)
        if model_class in {v for _, v in _CLASS_CHOICES}:
            class_sel.value = model_class

        prov_sel = self.query_one("#f-provider", Select)
        if provider in self._providers:
            prov_sel.value = provider

        work_sel = self.query_one("#f-work-modality", Select)
        if work in {v for _, v in _WORK_MODALITY_CHOICES}:
            work_sel.value = work

        self.query_one("#f-enabled", Checkbox).value = bool(prefill.get("enabled", True))
        self.query_one("#f-coa", Checkbox).value = bool(prefill.get("coa", False))
        self.query_one("#f-router-eligible", Checkbox).value = bool(
            prefill.get("router_eligible", True)
        )

        self.query_one("#f-error", Static).update(
            f"[green]Prefilled from {provider_label}[/] — review fields and click Save."
        )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "f-cancel":
            self.dismiss(None)
        elif event.button.id and event.button.id.startswith("f-import-"):
            from agitop.panels.provider_pick_modal import ProviderPickModal

            slug = event.button.id[len("f-import-"):]
            label = next(
                (p["label"] for p in (self._source_providers or []) if p["slug"] == slug),
                slug,
            )

            def _on_pick(prefill: dict | None) -> None:
                if prefill:
                    self._prefill_from_source(prefill, label)

            self.app.push_screen(ProviderPickModal(slug, label), callback=_on_pick)
        elif event.button.id == "f-feedback":
            from agitop.panels.model_feedback_modal import ModelFeedbackModal
            model_key = self.query_one("#f-key", Input).value.strip()
            if not model_key and self._existing:
                model_key = (self._existing.get("key") or "").strip()
            self.app.push_screen(ModelFeedbackModal(catalog_key=model_key))
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

        model_class = self.query_one("#f-class", Select).value
        if model_class == "local" and provider not in ("ollama", "llamacpp"):
            self.query_one("#f-error", Static).update(
                "[red]Local models must use provider 'ollama' or 'llamacpp'.[/]")
            return

        def _int(wid):
            try:
                return int(self.query_one(wid, Input).value.strip() or "0")
            except ValueError:
                return 0

        result = {
            "_mode": "edit" if self._edit else "add",
            "key": key,
            "class": model_class,
            "provider": provider,
            "label": label,
            "ctx_recommended": _int("#f-ctx-rec"),
            "ctx_max": _int("#f-ctx-max"),
            "enabled": self.query_one("#f-enabled", Checkbox).value,
            "coa": self.query_one("#f-coa", Checkbox).value,
            "work_modality": self.query_one("#f-work-modality", Select).value or "balanced",
            "input_modalities": self.query_one("#f-input-modalities", Input).value.strip() or "text",
            "output_modalities": self.query_one("#f-output-modalities", Input).value.strip() or "text",
            "router_eligible": self.query_one("#f-router-eligible", Checkbox).value,
        }
        from agitop.panels.model_params_ui import collect_catalog_generation_params

        param_result, param_err = collect_catalog_generation_params(self, key)
        if param_err:
            self.query_one("#f-error", Static).update(f"[red]{param_err}[/]")
            return
        if param_result:
            result.update({k: v for k, v in param_result.items() if not k.startswith("_")})
            if param_result.get("_param_warnings"):
                self.query_one("#f-error", Static).update(
                    "[yellow]" + "; ".join(param_result["_param_warnings"]) + "[/]"
                )
        if self._edit and self._had_custom_params and not param_result.get("_has_params"):
            result["_clear_params"] = True
        if not self._edit:
            result["gguf_repo"] = self.query_one("#f-gguf-repo", Input).value.strip()
            result["gguf_file"] = self.query_one("#f-gguf-file", Input).value.strip()
            try:
                result["size_gb"] = int(self.query_one("#f-size", Input).value.strip() or "0")
            except ValueError:
                result["size_gb"] = 0
        self.dismiss(result)


def _provider_slugify(text: str) -> str:
    """URL-safe provider slug: lowercase, non-alphanumerics → single hyphens."""
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").strip().lower()).strip("-")
    return s


class ProviderFormModal(ModalScreen):
    """Add or edit a provider (writes [providers_custom]). Keys set via 🔑 API Keys.

    For a baseline provider this writes a full-row override; for a custom one it
    edits the existing row. The slug is immutable once set.
    """

    CSS = """
    ProviderFormModal { align: center middle; background: $surface 80%; }
    #pf-dialog {
        width: 105;
        height: 40%;
        padding: 1 2;
        border: thick $accent;
        background: $surface;
    }
    #pf-title { height: auto; margin-bottom: 1; border-bottom: solid $accent; padding-bottom: 1; }
    .pf-grid-row {
        height: auto;
        layout: grid;
        grid-size: 2;
        grid-gutter: 0 3;
        grid-rows: auto;
    }
    .pf-field { height: auto; padding: 0; }
    .pf-field Input, .pf-field Select, .pf-field Checkbox, .pf-field ClearCheckbox { width: 100%; }
    .pf-label { margin-top: 1; color: $text-muted; }
    .pf-full { height: auto; margin-top: 1; }
    #pf-error { height: auto; }
    .pf-actions {
        dock: bottom;
        height: auto;
        margin-top: 1;
        align: center middle;
    }
    .pf-actions Button { width: 1fr; height: 3; margin: 0 1; }
    """

    def __init__(self, existing=None, **kwargs):
        super().__init__(**kwargs)
        self._existing = existing
        self._edit = existing is not None
        # Add mode: keep slug in sync with name until the operator edits slug.
        self._slug_manual = False
        self._slug_programmatic = False

    def compose(self) -> ComposeResult:
        e = self._existing or {}
        title = "✎ Edit Provider" if self._edit else "＋ Add Provider"
        cls_val = e.get("cls") or "ChatOpenAI"
        cls_opts = list(_COMMON_LC_CLASSES)
        if cls_val not in [v for _, v in cls_opts]:
            cls_opts.append((cls_val, cls_val))
        with Vertical(id="pf-dialog"):
            yield Static(f"[bold]{title}[/]", id="pf-title")
            # Row 1: Display Name (left) | Slug auto from name (right)
            with Horizontal(classes="pf-grid-row"):
                with Vertical(classes="pf-field"):
                    yield Label("[b]Name[/]", classes="pf-label")
                    yield Input(value=e.get("label", ""), placeholder="e.g. Mistral",
                                id="p-label")
                with Vertical(classes="pf-field"):
                    yield Label(
                        "[b]Slug[/]  [dim](auto from name — editable)[/]"
                        if not self._edit else "[b]Slug[/]",
                        classes="pf-label",
                    )
                    yield Input(value=e.get("slug", ""), placeholder="e.g. mistral",
                                id="p-slug", disabled=self._edit)
            # Row 2: LangChain Class | Enabled
            with Horizontal(classes="pf-grid-row"):
                with Vertical(classes="pf-field"):
                    yield Label("[b]LangChain Class[/]", classes="pf-label")
                    yield Select(cls_opts, value=cls_val, allow_blank=False, id="p-class")
                with Vertical(classes="pf-field"):
                    yield Label("", classes="pf-label")  # spacer to align
                    yield ClearCheckbox("Enabled", value=e.get("enabled", False), id="p-enabled")
            with Vertical(classes="pf-full"):
                yield Static(
                    "[dim]Set the API key afterwards via the 🔑 API Keys modal "
                    "(agictl system set-key).[/]")
            yield Static("", id="pf-error")
            with Horizontal(classes="pf-actions"):
                yield Button("Save", variant="success", id="p-save")
                yield Button("Cancel", classes="dismiss-btn", variant="default", id="p-cancel")

    def on_mount(self) -> None:
        if not self._edit:
            try:
                self.query_one("#p-label", Input).focus()
            except Exception:  # noqa: BLE001
                pass

    @on(Input.Changed, "#p-label")
    def _on_label_changed(self, event: Input.Changed) -> None:
        if self._edit or self._slug_manual:
            return
        slug = _provider_slugify(event.value)
        self._slug_programmatic = True
        try:
            self.query_one("#p-slug", Input).value = slug
        finally:
            self._slug_programmatic = False

    @on(Input.Changed, "#p-slug")
    def _on_slug_changed(self, event: Input.Changed) -> None:
        if self._edit or self._slug_programmatic:
            return
        self._slug_manual = True

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "p-cancel":
            self.dismiss(None)
        elif event.button.id == "p-save":
            slug = _provider_slugify(self.query_one("#p-slug", Input).value)
            label = self.query_one("#p-label", Input).value.strip()
            if not label:
                self.query_one("#pf-error", Static).update(
                    "[red]Name is required.[/]")
                return
            if not slug:
                slug = _provider_slugify(label)
            if not slug:
                self.query_one("#pf-error", Static).update(
                    "[red]Could not derive a slug from the name.[/]")
                return
            self.dismiss({
                "_mode": "edit" if self._edit else "add",
                "slug": slug,
                "label": label,
                "cls": self.query_one("#p-class", Select).value,
                "enabled": self.query_one("#p-enabled", Checkbox).value,
            })
