"""Pick a model from a provider's Models API and prefill the catalog form.

For ``openrouter`` it drives ``agictl model openrouter list``; for the direct-API
providers (google/xai/openai/anthropic) it drives ``agictl model source list``.
The selected model is returned to the caller for review — nothing is written until
the user saves the Add Model form.
"""

import json
import subprocess

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, DataTable, Static

from agitop.panels.modality_format import format_modality_labels
from agitop.widgets.provider_brand_icon import provider_brand_class


def _format_token_limit(value) -> str:
    """Abbreviate token counts for table cells (1.0M, 128K, —)."""
    if value is None or value == "":
        return "—"
    try:
        n = int(value)
    except (TypeError, ValueError):
        return "—"
    if n <= 0:
        return "—"
    if n >= 1_000_000:
        if n % 1_000_000 == 0:
            return f"{n // 1_000_000}M"
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        if n % 1_000 == 0:
            return f"{n // 1_000}K"
        return f"{n / 1_000:.1f}K"
    return str(n)


def catalog_prefill_from_source(slug: str, model: dict) -> dict:
    """Map a provider list row into CatalogFormModal field values."""
    ctx = model.get("context_length") or model.get("input_context_limit") or 131072
    try:
        ctx_max = int(ctx)
    except (TypeError, ValueError):
        ctx_max = 131072
    model_class = "cloud" if slug == "google" else "third_party"
    return {
        "key": model.get("id") or "",
        "label": model.get("label") or model.get("name") or model.get("id") or "",
        "class": model_class,
        "provider": slug,
        "ctx_recommended": 0,
        "ctx_max": ctx_max,
        "enabled": True,
        "coa": False,
        "work_modality": model.get("work_modality", "balanced") or "balanced",
        "input_modalities": model.get("input_modalities", "text") or "text",
        "output_modalities": model.get("output_modalities", "text") or "text",
        "router_eligible": True,
    }


def _run_agictl(args, timeout=60):
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


class ProviderPickModal(ModalScreen):
    """Browse addable models from one provider; return selection for form prefill."""

    BINDINGS = [Binding("escape", "close", "Close")]

    def __init__(self, slug: str, label: str = "", **kwargs):
        super().__init__(**kwargs)
        self._slug = slug
        self._label = label or slug
        self._models: list[dict] = []

    def _list_args(self) -> list[str]:
        if self._slug == "openrouter":
            return ["model", "openrouter", "list"]
        return ["model", "source", "list", self._slug]

    def compose(self) -> ComposeResult:
        brand = provider_brand_class(self._slug)
        with Vertical(id="provider-pick-dialog", classes=brand):
            with Horizontal(id="provider-pick-header", classes=f"provider-pick-titlebar {brand}"):
                yield Static(
                    f"[bold]Import from {self._label}[/]",
                    id="provider-pick-title",
                    classes="provider-pick-title",
                )
            yield Static(
                "[dim]Chat-capable models not yet in your catalog. "
                "In/Out: 📝 text · 🖼 image · 🔊 audio · 🎬 video (icon + name in table). "
                "Context limits are tokens (provider API; inferred where absent). "
                "Select a row and click Use selected to fill the Add Model form.[/]"
            )
            with VerticalScroll(id="provider-pick-scroll"):
                yield DataTable(id="provider-pick-table", zebra_stripes=True)
            yield Static("", id="provider-pick-feedback")
            with Horizontal(id="provider-pick-actions"):
                yield Button("Use selected", variant="success", id="provider-pick-use")
                yield Button("Close", classes="dismiss-btn", variant="default", id="provider-pick-close")

    def on_mount(self) -> None:
        table = self.query_one("#provider-pick-table", DataTable)
        table.cursor_type = "row"
        table.add_columns(
            "Label", "Model ID", "In", "Out", "In ctx", "Out ctx",
            "$/M in", "$/M out", "Work",
        )
        self._load()

    def _load(self) -> None:
        ok, data, err = _run_agictl(self._list_args())
        table = self.query_one("#provider-pick-table", DataTable)
        table.clear()
        self._models = []
        if not ok:
            self.query_one("#provider-pick-feedback", Static).update(f"[red]{err}[/]")
            return
        self._models = data.get("models", [])
        for m in self._models:
            pr = m.get("pricing") or {}
            pin = pr.get("prompt_per_m", 0)
            pout = pr.get("completion_per_m", 0)
            table.add_row(
                (m.get("label") or "")[:40],
                m["id"],
                format_modality_labels(m.get("input_modalities", "text")),
                format_modality_labels(m.get("output_modalities", "text")),
                _format_token_limit(m.get("input_context_limit", m.get("context_length"))),
                _format_token_limit(m.get("output_context_limit")),
                f"{pin:.4g}" if pin else "—",
                f"{pout:.4g}" if pout else "—",
                m.get("work_modality", "balanced"),
                key=m["id"],
            )
        if not self._models:
            self.query_one("#provider-pick-feedback", Static).update(
                f"[yellow]No addable {self._label} models (all may already be registered).[/]"
            )

    def _selected_model(self) -> dict | None:
        table = self.query_one("#provider-pick-table", DataTable)
        if table.row_count == 0:
            return None
        try:
            mid = table.coordinate_to_cell_key(table.cursor_coordinate).row_key.value or ""
        except Exception:
            return None
        if not mid:
            return None
        return next((m for m in self._models if m.get("id") == mid), None)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "provider-pick-close":
            self.dismiss(None)
            return
        if event.button.id == "provider-pick-use":
            model = self._selected_model()
            if not model:
                self.query_one("#provider-pick-feedback", Static).update(
                    "[yellow]Select a model row first.[/]"
                )
                return
            self.dismiss(catalog_prefill_from_source(self._slug, model))

    def action_close(self) -> None:
        self.dismiss(None)
