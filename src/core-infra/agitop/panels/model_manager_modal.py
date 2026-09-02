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
import time

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.screen import ModalScreen
from textual.containers import VerticalScroll, Horizontal, Vertical
from textual.widgets import Static, Button, Input, Label, DataTable, Select, Checkbox, TabbedContent, TabPane
from agitop.widgets.clear_checkbox import ClearCheckbox

from agitop.panels.media_wizard import (
    _LOCAL_CATALOG_PROVIDERS,
    build_gpu_host_agictl_cmd,
    import_action_enabled,
    media_form_prefill,
    media_import_failure_hint,
    media_wizard_summary,
    read_local_ai_topology,
    read_tunnel_host,
    watchdog_ssh_key,
)
from model_media_remote import format_elapsed, run_cmd_streaming
from agitop.panels.modality_format import format_modality_labels
from agitop.widgets.provider_brand_icon import provider_brand_class, provider_import_button_label


# Live catalog class is vendor-neutral: cloud (any remote API) or local.
_CLASS_CHOICES = [
    ("☁ Cloud", "cloud"),
    ("🖥 Local · Ollama / llama.cpp", "local"),
]
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


def gguf_registry_blocked(inspect: dict | None, confirm_unknown: bool = False) -> str | None:
    """Return an error if SYCL GGUF fields must not be saved/imported."""
    from model_hf_ingest import gguf_registry_blocked as _blocked

    return _blocked(inspect, confirm_unknown=confirm_unknown)


def _parse_agictl_json(stdout: str) -> dict:
    if not stdout:
        return {}
    for line in reversed(stdout.strip().splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                return json.loads(line)
            except Exception:  # noqa: BLE001
                continue
    return {}


def _run_cmd(cmd, timeout=25):
    """Run ``cmd`` and parse a trailing agictl JSON line. Never raises."""
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True, text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return False, {}, "command timed out"
    except Exception as e:  # noqa: BLE001
        return False, {}, str(e)

    data = _parse_agictl_json(proc.stdout)
    ok = bool(data.get("success")) if data else (proc.returncode == 0)
    err = ""
    if not ok:
        err = data.get("error") or (proc.stderr.strip() or proc.stdout.strip() or "Unknown error")
    return ok, data, err


def _run_agictl(args, timeout=25, sudo=True):
    """Run `agictl <args>` (optionally via sudo) and parse the trailing JSON line."""
    cmd = (["sudo", "agictl"] if sudo else ["agictl"]) + args
    return _run_cmd(cmd, timeout=timeout)


def _fmt_per_m(value) -> str:
    """Format $/M token price for display (catalog provider rates)."""
    try:
        n = float(value)
    except (TypeError, ValueError):
        return "—"
    if n <= 0:
        return "—"
    return f"{n:.3g}"


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
        "google": ("third_party", "google_api_key"),
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

    def __init__(
        self,
        key: str,
        label: str = "",
        origin: str = "",
        local_sycl: bool = False,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self.key = key
        self.label = label
        self.origin = origin
        self.local_sycl = local_sycl

    def compose(self) -> ComposeResult:
        title = f"[bold red]⚠ Remove Model: {self.key}[/]"
        if self.label and self.label != self.key:
            title += f"\n[dim]{self.label}[/]"
        if self.local_sycl:
            body = (
                "This deletes the GGUF on the GPU host, the SYCL registry,\n"
                "the catalog row, and local_models.\n"
                "Activate another model first if this one is loaded.\n"
                "Agents still assigned keep this key until you retarget them.\n\n"
                "[bold]This cannot be undone.[/]"
            )
        else:
            body = (
                "This deletes the catalog entry and removes the key from\n"
                "setup.ini activation lists so migrate will not bring it back.\n"
                "Per-model default params are cleared.\n\n"
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
        mt.add_columns(
            "Label", "Key", "Provider", "Type", "Work", "Rtr", "En", "COA", "Rsn",
            "Input", "Input Price", "Output", "Output Price", "Drivers",
        )
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
        def _row_sort(r):
            prov = (self._providers_by_slug.get(r.get("provider") or "") or {}).get(
                "label"
            ) or r.get("provider") or ""
            label = r.get("label") or r.get("key") or ""
            return (prov.casefold(), label.casefold(), r.get("key") or "")

        for m in sorted(models, key=_row_sort):
            pin = m.get("prompt_per_m")
            pout = m.get("completion_per_m")
            price_in = f"{pin:.3g}" if pin else "—"
            price_out = f"{pout:.3g}" if pout else "—"
            mt.add_row(
                m.get("label", ""),
                m["key"],
                (self._providers_by_slug.get(m.get("provider") or "") or {}).get(
                    "label"
                ) or m.get("provider") or "—",
                _model_type(m),
                m.get("work_modality", "balanced"),
                _yn(m.get("router_eligible")),
                _enabled_val(m.get("enabled", False)),
                _yn(m["coa"]),
                m.get("reasoning_effort", "none"),
                format_modality_labels(m.get("input_modalities", "text")),
                price_in,
                format_modality_labels(m.get("output_modalities", "text")),
                price_out,
                m.get("driver_summary", "text-native"),
                key=m["key"],
            )

        pt = self.query_one("#mm-providers-table", DataTable)
        pt.clear()
        for p in sorted(providers, key=lambda r: r["slug"]):
            # Stock providers are always listed (En=· until keyed / opted-in).
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

    def _apply(self, args, success_msg, timeout=25) -> None:
        """Run a mutating agictl command, surface feedback, and reload on success."""
        ok, data, err = _run_agictl(args, timeout=timeout)
        if ok:
            self._dirty = True
            extra = data.get("message", "")
            hints = data.get("driver_hints") or []
            hint_text = f"\n[yellow]{' '.join(hints)}[/]" if hints else ""
            self._feedback(
                f"[green]✅ {success_msg}[/]"
                + (f"  [dim]{extra}[/]" if extra else "")
                + hint_text
            )
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
                    local_sycl = (
                        m.get("class") == "local" and m.get("provider") == "llamacpp"
                    )
                    self.app.push_screen(
                        ModelRemoveConfirmModal(
                            key,
                            m.get("label", ""),
                            m.get("origin", ""),
                            local_sycl=local_sycl,
                        ),
                        callback=lambda confirmed, k=key: self._on_model_remove_confirmed(confirmed, k),
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
        if not confirmed:
            return
        m = self._models_by_key.get(key) or {}
        if m.get("class") == "local" and m.get("provider") == "llamacpp":
            self._apply(
                ["model", "sycl", "remove", key, "--confirm-agent-assignments"],
                f"Removed local '{key}'",
                timeout=600,
            )
        else:
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
        self._hf_inspect = None
        self._import_busy = False

    def compose(self) -> ComposeResult:
        e = self._existing or {}
        prov_opts = [(p, p) for p in self._providers] or [("(none)", "")]
        if "local_media" not in {v for _, v in prov_opts}:
            prov_opts.append(("local_media", "local_media"))
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
                yield Static("[dim]Import from a provider:[/]", id="catalog-form-import-label")
                with Horizontal(id="catalog-form-header"):
                    import_providers = list(self._source_providers or [])
                    if not any(p.get("slug") == "huggingface" for p in import_providers):
                        import_providers.append(
                            {"slug": "huggingface", "label": "Hugging Face"}
                        )
                    for prov in import_providers:
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
                        yield Static("[b]Display label[/]  [dim]product name only; pickers add Provider + key[/]")
                        yield Input(value=e.get("label", ""), placeholder="e.g. GPT-5.6 Terra", id="f-label")

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
                        yield Static("[b]Input[/]  [dim](CSV: text,image,audio,video)[/]")
                        yield Input(
                            value=e.get("input_modalities", "text") or "text",
                            id="f-input-modalities",
                        )
                    with Vertical(classes="mm-form-col-price"):
                        yield Static("[b]Input Price[/]  [dim]($/M)[/]")
                        yield Static(_fmt_per_m(e.get("prompt_per_m")), id="f-price-in")
                    with Vertical(classes="mm-form-col"):
                        yield Static("[b]Output[/]  [dim](CSV: text,image,audio,video)[/]")
                        yield Input(
                            value=e.get("output_modalities", "text") or "text",
                            id="f-output-modalities",
                        )
                    with Vertical(classes="mm-form-col-price"):
                        yield Static("[b]Output Price[/]  [dim]($/M)[/]")
                        yield Static(_fmt_per_m(e.get("completion_per_m")), id="f-price-out")

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
                        "[bold cyan]Hugging Face inspect[/]  [dim](paste URL or hf://org/repo/file.gguf)[/]")
                    yield Input(
                        placeholder="hf://unsloth/gemma-4-E4B-it-GGUF/gemma-4-E4B-it-Q4_K_M.gguf",
                        id="f-hf-source",
                    )
                    yield Static("", id="f-hf-inspect-result")
                    yield Static(
                        "[dim]Inspect first. The matching import button turns on from "
                        "that class (chat → SYCL, media → Media). Media never enters "
                        "llama-server.[/]")
                    yield Input(placeholder="HuggingFace repo (org/model)", id="f-gguf-repo")
                    yield Input(placeholder="GGUF filename", id="f-gguf-file")
                    yield Input(placeholder="approx size GB", type="integer", id="f-size")

                yield Static("", id="f-error")

            with Horizontal(id="catalog-form-footer"):
                yield Button("Save", variant="success", id="f-save")
                if not self._edit:
                    yield Button("Inspect HF", variant="primary", id="f-hf-inspect")
                    yield Button("⬇ SYCL Import", variant="warning", id="f-sycl-import", disabled=True)
                    yield Button("▣ Media Import", variant="warning", id="f-media-import", disabled=True)
                yield Button("★ Model Feedback", variant="warning", id="f-feedback")
                yield Button("Cancel", classes="dismiss-btn", variant="default", id="f-cancel")

    def on_mount(self) -> None:
        self._sync_import_buttons()
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
        try:
            self.query_one("#f-price-in", Static).update(
                _fmt_per_m(prefill.get("prompt_per_m"))
            )
            self.query_one("#f-price-out", Static).update(
                _fmt_per_m(prefill.get("completion_per_m"))
            )
        except Exception:
            pass

        model_class = prefill.get("class") or "cloud"
        if model_class == "third_party":
            model_class = "cloud"
        provider = prefill.get("provider") or ""
        work = prefill.get("work_modality") or "balanced"

        class_sel = self.query_one("#f-class", Select)
        if model_class in {v for _, v in _CLASS_CHOICES}:
            class_sel.value = model_class

        prov_sel = self.query_one("#f-provider", Select)
        try:
            if provider:
                prov_sel.value = provider
        except Exception:
            pass

        work_sel = self.query_one("#f-work-modality", Select)
        if work in {v for _, v in _WORK_MODALITY_CHOICES}:
            work_sel.value = work

        self.query_one("#f-enabled", Checkbox).value = bool(prefill.get("enabled", True))
        self.query_one("#f-coa", Checkbox).value = bool(prefill.get("coa", False))
        self.query_one("#f-router-eligible", Checkbox).value = bool(
            prefill.get("router_eligible", True)
        )

        hf_source = (prefill.get("hf_source") or "").strip()
        if hf_source:
            try:
                self.query_one("#f-hf-source", Input).value = hf_source
            except Exception:
                pass
            self._inspect_hf()
            next_step = (
                "Media Import"
                if (prefill.get("kind") or "") == "media"
                else "Save"
            )
            self.query_one("#f-error", Static).update(
                f"[green]Prefilled from {provider_label}[/] — review, then {next_step}."
            )
            return

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
                "Hugging Face" if slug == "huggingface" else slug,
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
        elif event.button.id == "f-hf-inspect":
            self._inspect_hf()
        elif event.button.id == "f-sycl-import":
            self._sycl_import()
        elif event.button.id == "f-media-import":
            self._media_import()
        elif event.button.id == "f-save":
            self._submit()

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id != "f-hf-source":
            return
        if self._hf_inspect is None:
            return
        self._hf_inspect = None
        try:
            self.query_one("#f-hf-inspect-result", Static).update(
                "[dim]Source changed — Inspect HF again.[/]"
            )
        except Exception:  # noqa: BLE001
            pass
        self._sync_import_buttons()

    def _inspect_hf(self) -> None:
        source = ""
        try:
            source = self.query_one("#f-hf-source", Input).value.strip()
        except Exception:
            source = ""
        if not source:
            repo = self.query_one("#f-gguf-repo", Input).value.strip()
            gguf = self.query_one("#f-gguf-file", Input).value.strip()
            if repo and gguf:
                source = f"hf://{repo}/{gguf}"
        if not source:
            self.query_one("#f-hf-inspect-result", Static).update(
                "[red]Paste a Hugging Face URL or hf://org/repo/file.gguf first.[/]"
            )
            self._sync_import_buttons()
            return
        ok, data, err = _run_agictl(["model", "hf", "inspect", source], timeout=45, sudo=False)
        if not ok:
            self._hf_inspect = None
            self.query_one("#f-hf-inspect-result", Static).update(f"[red]Inspect failed: {err}[/]")
            self._sync_import_buttons()
            return
        self._hf_inspect = data
        kind = data.get("classification") or "unknown"
        selected = (data.get("selected_file") or {}).get("path") or ""
        size_gb = data.get("size_gb")
        src = data.get("source") or {}
        color = {
            "chat_gguf": "green",
            "chat_vlm_mmproj": "yellow",
            "media_pipeline": "red",
            "unknown": "yellow",
        }.get(kind, "yellow")
        lines = [
            f"[{color}]class={kind}[/{color}]  repo={src.get('repo_id') or ''}  file={selected}",
        ]
        if size_gb:
            lines.append(f"size≈{size_gb}GB")
        if data.get("next_step"):
            lines.append(str(data["next_step"]))
        self.query_one("#f-hf-inspect-result", Static).update("\n".join(lines))
        if kind == "media_pipeline":
            self.query_one("#f-gguf-repo", Input).value = ""
            self.query_one("#f-gguf-file", Input).value = ""
            self._show_media_plan(source)
            return
        if src.get("repo_id"):
            self.query_one("#f-gguf-repo", Input).value = src["repo_id"]
        if selected:
            self.query_one("#f-gguf-file", Input).value = os.path.basename(selected)
        if size_gb:
            self.query_one("#f-size", Input).value = str(size_gb)
        if kind == "chat_vlm_mmproj":
            self.query_one("#f-input-modalities", Input).value = "text"
            self.query_one("#f-error", Static).update(
                "[yellow]VLM: import stores the projector. Catalog stays text-only until vision probe.[/]"
            )
        self._sync_import_buttons()

    def _show_media_plan(self, source: str) -> None:
        dest = self.query_one("#f-key", Input).value.strip()
        args = ["model", "media", "inspect", source]
        if dest:
            args += ["--name", dest]
        ok, data, err = _run_agictl(args, timeout=45, sudo=False)
        if ok:
            self._hf_inspect = data
        else:
            data = self._hf_inspect or {}
        self.query_one("#f-hf-inspect-result", Static).update(
            f"[red]{media_wizard_summary(data)}[/]" if not ok
            else f"[cyan]{media_wizard_summary(data)}[/]"
        )
        if not ok:
            self.query_one("#f-error", Static).update(
                f"[red]Media inspect failed: {err}. SYCL Import stays blocked.[/]"
            )
            return
        prefill = media_form_prefill(data)
        key_in = self.query_one("#f-key", Input)
        if not key_in.value.strip():
            key_in.value = prefill["key"]
        self.query_one("#f-label", Input).value = prefill["label"]
        try:
            self.query_one("#f-class", Select).value = prefill["class"]
        except Exception:
            pass
        try:
            self.query_one("#f-provider", Select).value = prefill["provider"]
        except Exception:
            pass
        try:
            self.query_one("#f-work-modality", Select).value = prefill["work_modality"]
        except Exception:
            pass
        self.query_one("#f-input-modalities", Input).value = prefill["input_modalities"]
        self.query_one("#f-output-modalities", Input).value = prefill["output_modalities"]
        self.query_one("#f-router-eligible", Checkbox).value = False
        self.query_one("#f-coa", Checkbox).value = False
        self.query_one("#f-error", Static).update(
            "[cyan]Media pipeline — use Media Import. Not a SYCL chat model.[/]"
        )
        self._sync_import_buttons()

    def _media_source(self) -> str:
        source = ""
        try:
            source = self.query_one("#f-hf-source", Input).value.strip()
        except Exception:
            source = ""
        if source:
            return source
        repo = self.query_one("#f-gguf-repo", Input).value.strip()
        gguf = self.query_one("#f-gguf-file", Input).value.strip()
        return f"hf://{repo}/{gguf}" if repo and gguf else ""

    def _set_import_status(self, text: str) -> None:
        try:
            self.query_one("#f-error", Static).update(text)
        except Exception:  # noqa: BLE001
            pass

    def _set_import_busy(self, busy: bool) -> None:
        self._import_busy = busy
        self._sync_import_buttons()

    def _sync_import_buttons(self) -> None:
        """Enable SYCL vs Media from the last inspect class, not from a guess."""
        if self._edit:
            return
        flags = import_action_enabled(
            (self._hf_inspect or {}).get("classification"),
            busy=self._import_busy,
        )
        for wid, enabled in (
            ("#f-hf-inspect", flags["inspect"]),
            ("#f-sycl-import", flags["sycl"]),
            ("#f-media-import", flags["media"]),
        ):
            try:
                self.query_one(wid, Button).disabled = not enabled
            except Exception:  # noqa: BLE001
                pass

    def _start_import_worker(
        self,
        cmd: list,
        *,
        kind: str,
        topology: str,
        key: str,
    ) -> None:
        if self._import_busy:
            return
        where = "GPU host" if topology == "client" else "this host"
        label = "Media Import" if kind == "media" else "SYCL Import"
        self._set_import_busy(True)
        self._set_import_status(f"[yellow]{label} on the {where}… 0s[/]")
        self.run_worker(
            lambda: self._import_worker(
                cmd, kind=kind, topology=topology, key=key, where=where, label=label,
            ),
            exclusive=True,
            thread=True,
            name=f"{kind}-import",
        )

    def _import_worker(self, cmd, *, kind, topology, key, where, label) -> None:
        started = time.monotonic()

        def on_progress(line: str) -> None:
            elapsed = format_elapsed(time.monotonic() - started)
            snippet = (line or "").strip()
            if len(snippet) > 160:
                snippet = snippet[-160:]
            try:
                self.app.call_from_thread(
                    self._set_import_status,
                    f"[yellow]{label} on the {where} · {elapsed}[/]\n{snippet}",
                )
            except Exception:  # noqa: BLE001
                pass

        ok, data, err = run_cmd_streaming(cmd, timeout=3600, on_progress=on_progress)
        try:
            self.app.call_from_thread(
                self._import_done, ok, data, err, kind, topology, key, where,
            )
        except Exception:  # noqa: BLE001
            pass

    def _import_done(self, ok, data, err, kind, topology, key, where) -> None:
        self._set_import_busy(False)
        if not ok:
            hint = media_import_failure_hint(err) if kind == "media" else err
            title = "Media" if kind == "media" else "SYCL"
            self._set_import_status(f"[red]{title} import failed: {hint}[/]")
            return
        if kind == "media":
            if topology == "client":
                _run_agictl(["model", "refresh"], timeout=30, sudo=True)
            store = data.get("store_dir") or f"/opt/versa-agi/media-models/{key}"
            self._set_import_status(
                f"[green]Bundle stored at {store} on the {where}. Not a chat model. "
                "Paint from this laptop with agictl model media generate or "
                "agictl utility run — the PNG comes back here. "
                "Utility Profile is not created here.[/]"
            )
            return
        self._set_import_status(
            f"[green]Imported '{key}' as text-only chat. Activate separately — never auto-activated.[/]"
        )
        if data.get("file"):
            self.query_one("#f-gguf-file", Input).value = os.path.basename(str(data["file"]))
        if data.get("repo"):
            self.query_one("#f-gguf-repo", Input).value = str(data["repo"])
        if data.get("size_gb"):
            self.query_one("#f-size", Input).value = str(data["size_gb"])

    def _sycl_import(self) -> None:
        if self._import_busy:
            return
        if not self._hf_inspect:
            self._inspect_hf()
        blocked = gguf_registry_blocked(self._hf_inspect)
        if blocked:
            self.query_one("#f-error", Static).update(f"[red]{blocked}[/]")
            return
        key = self.query_one("#f-key", Input).value.strip()
        if not key:
            self.query_one("#f-error", Static).update("[red]Model key is required for SYCL import.[/]")
            return
        source = self.query_one("#f-hf-source", Input).value.strip()
        if not source:
            repo = self.query_one("#f-gguf-repo", Input).value.strip()
            gguf = self.query_one("#f-gguf-file", Input).value.strip()
            source = f"hf://{repo}/{gguf}" if repo and gguf else ""
        if not source:
            self.query_one("#f-error", Static).update("[red]Inspect a Hugging Face source first.[/]")
            return
        label = self.query_one("#f-label", Input).value.strip() or key
        args = ["model", "sycl", "import", source, "--name", key, "--runtime", "chat", "--label", label]
        kind = (self._hf_inspect or {}).get("classification")
        if kind == "unknown":
            args.append("--confirm-unknown")
        self._start_import_worker(
            (["sudo", "agictl"] + args),
            kind="sycl",
            topology=read_local_ai_topology(),
            key=key,
        )

    def _media_import(self) -> None:
        if self._import_busy:
            return
        if not self._hf_inspect:
            self._inspect_hf()
        kind = (self._hf_inspect or {}).get("classification")
        if kind != "media_pipeline":
            self.query_one("#f-error", Static).update(
                "[red]Media Import is for media pipelines. Inspect a Qwen-Image source first.[/]"
            )
            return
        key = self.query_one("#f-key", Input).value.strip() or media_form_prefill(
            self._hf_inspect
        )["key"]
        if not key:
            self.query_one("#f-error", Static).update("[red]Model key is required for Media Import.[/]")
            return
        source = self._media_source()
        if not source:
            self.query_one("#f-error", Static).update("[red]Inspect a Hugging Face source first.[/]")
            return
        args = [
            "model", "media", "import", source,
            "--name", key, "--runtime", "media",
        ]
        if kind == "unknown":
            args.append("--confirm-unknown")
        topology = read_local_ai_topology()
        try:
            cmd = build_gpu_host_agictl_cmd(
                args,
                topology=topology,
                tunnel_host=read_tunnel_host(),
                ssh_key=watchdog_ssh_key(),
            )
        except ValueError as exc:
            self.query_one("#f-error", Static).update(f"[red]{exc}[/]")
            return
        self._start_import_worker(cmd, kind="media", topology=topology, key=key)

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
        if model_class == "local" and provider not in _LOCAL_CATALOG_PROVIDERS:
            self.query_one("#f-error", Static).update(
                "[red]Local models must use provider 'ollama', 'llamacpp', or 'local_media'.[/]")
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
            if result["gguf_repo"] and result["gguf_file"]:
                inspect = self._hf_inspect
                if not inspect:
                    src = f"hf://{result['gguf_repo']}/{result['gguf_file']}"
                    _ok, inspect, _err = _run_agictl(
                        ["model", "hf", "inspect", src], timeout=45, sudo=False,
                    )
                    self._hf_inspect = inspect or None
                blocked = gguf_registry_blocked(inspect)
                if blocked:
                    self.query_one("#f-error", Static).update(f"[red]{blocked}[/]")
                    return
                if (inspect or {}).get("classification") == "chat_vlm_mmproj":
                    result["input_modalities"] = "text"
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
