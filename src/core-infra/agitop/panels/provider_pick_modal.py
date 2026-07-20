"""Pick a model from a provider's Models API and prefill the catalog form.

For ``openrouter`` it drives ``agictl model openrouter list``; for the direct-API
providers (google/xai/openai/anthropic) it drives ``agictl model source list``.
The selected model is returned to the caller for review — nothing is written until
the user saves the Add Model form.
"""

import json
import subprocess
import threading

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Static

from agitop.panels.modality_format import format_modality_labels
from agitop.widgets import PaginatedDataTable
from agitop.widgets.braille_spinner import DOTS2_INTERVAL_S, dots2_markup
from agitop.widgets.provider_brand_icon import provider_brand_class

_PAGE_SIZE = 10


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


def _run_agictl(args, timeout=120):
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


def _model_row_cells(m: dict) -> tuple:
    pr = m.get("pricing") or {}
    pin = pr.get("prompt_per_m", 0)
    pout = pr.get("completion_per_m", 0)
    return (
        (m.get("label") or "")[:40],
        m["id"],
        format_modality_labels(m.get("input_modalities", "text")),
        format_modality_labels(m.get("output_modalities", "text")),
        _format_token_limit(m.get("input_context_limit", m.get("context_length"))),
        _format_token_limit(m.get("output_context_limit")),
        f"{pin:.4g}" if pin else "—",
        f"{pout:.4g}" if pout else "—",
        m.get("work_modality", "balanced"),
    )


class ProviderPickModal(ModalScreen):
    """Browse addable models from one provider; return selection for form prefill."""

    BINDINGS = [Binding("escape", "close", "Close")]

    def __init__(self, slug: str, label: str = "", **kwargs):
        super().__init__(**kwargs)
        self._slug = slug
        self._label = label or slug
        self._models: list[dict] = []
        self._page = 0
        self._page_size = _PAGE_SIZE
        self._fetching = False
        self._spinner_tick = 0
        self._spinner_timer = None

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
                "PgUp/PgDn (Mac: Fn+↑/↓ or Ctrl+B/F) to page · select a row · "
                "Use selected to fill the Add Model form.[/]"
            )
            yield Static("", id="provider-pick-loading")
            with Vertical(id="provider-pick-scroll", classes="provider-pick-hidden"):
                yield PaginatedDataTable(
                    self._handle_pick_key,
                    id="provider-pick-table",
                    zebra_stripes=True,
                )
            yield Static("", id="provider-pick-feedback")
            with Horizontal(id="provider-pick-actions"):
                yield Button(
                    "Use selected", variant="success",
                    id="provider-pick-use", disabled=True,
                )
                yield Button("Close", classes="dismiss-btn", variant="default", id="provider-pick-close")

    def on_mount(self) -> None:
        table = self.query_one("#provider-pick-table", PaginatedDataTable)
        table.cursor_type = "row"
        table.add_columns(
            "Label", "Model ID", "In", "Out", "In ctx", "Out ctx",
            "$/M in", "$/M out", "Work",
        )
        self._begin_fetch()

    def on_unmount(self) -> None:
        self._stop_spinner()

    def _stop_spinner(self) -> None:
        if self._spinner_timer is not None:
            self._spinner_timer.stop()
            self._spinner_timer = None

    def _tick_spinner(self) -> None:
        if not self._fetching:
            return
        self.query_one("#provider-pick-loading", Static).update(
            dots2_markup(self._spinner_tick, f"Loading models from {self._label}", "cyan")
        )
        self._spinner_tick += 1

    def _set_fetching(self, fetching: bool) -> None:
        self._fetching = fetching
        if fetching:
            self.query_one("#provider-pick-scroll").add_class("provider-pick-hidden")
            self.query_one("#provider-pick-loading").remove_class("provider-pick-hidden")
            self.query_one("#provider-pick-use", Button).disabled = True
        else:
            self.query_one("#provider-pick-loading").add_class("provider-pick-hidden")
            self.query_one("#provider-pick-scroll").remove_class("provider-pick-hidden")
            self.query_one("#provider-pick-use", Button).disabled = False

    def _begin_fetch(self) -> None:
        if self._fetching:
            return
        self._spinner_tick = 0
        self._set_fetching(True)
        self.query_one("#provider-pick-loading", Static).update(
            dots2_markup(0, f"Loading models from {self._label}", "cyan")
        )
        self._spinner_timer = self.set_interval(DOTS2_INTERVAL_S, self._tick_spinner)
        threading.Thread(target=self._fetch_models, daemon=True).start()

    def _fetch_models(self) -> None:
        ok, data, err = _run_agictl(self._list_args())
        models = data.get("models", []) if ok else []
        self.app.call_from_thread(self._on_models_fetched, ok, models, err)

    def _on_models_fetched(self, ok: bool, models: list[dict], err: str) -> None:
        self._stop_spinner()
        self._fetching = False
        feedback = self.query_one("#provider-pick-feedback", Static)
        if not ok:
            self._set_fetching(False)
            feedback.update(f"[red]{err}[/]")
            self.query_one("#provider-pick-loading", Static).update(f"[red]{err}[/]")
            return
        self._models = models
        if not self._models:
            self._set_fetching(False)
            feedback.update(
                f"[yellow]No addable {self._label} models (all may already be registered).[/]"
            )
            self.query_one("#provider-pick-loading", Static).update(
                f"[yellow]No addable {self._label} models.[/]"
            )
            return
        self._page = 0
        self._set_fetching(False)
        # Size the page to the table viewport once layout has settled, then render.
        self.call_after_refresh(self._initial_render)

    def _initial_render(self) -> None:
        self._sync_page_size()
        self._update_table()
        self.query_one("#provider-pick-table", PaginatedDataTable).focus()

    def _rows_per_page(self) -> int:
        """Visible row capacity from the table height (minus border + header)."""
        try:
            table = self.query_one("#provider-pick-table", PaginatedDataTable)
        except Exception:
            return self._page_size
        height = table.size.height
        if height <= 0:
            return self._page_size
        return max(1, height - 3)

    def _sync_page_size(self) -> None:
        self._page_size = self._rows_per_page()

    def _max_page(self) -> int:
        return max(0, (len(self._models) - 1) // self._page_size)

    def on_resize(self, event) -> None:
        if self._fetching or not self._models:
            return
        previous = self._page_size
        self._sync_page_size()
        if self._page_size != previous:
            self._update_table()

    def _handle_pick_key(self, key: str) -> None:
        if self._fetching or not self._models:
            return
        if key == "pageup" and self._page > 0:
            self._page -= 1
            self._update_table()
        elif key == "pagedown" and self._page < self._max_page():
            self._page += 1
            self._update_table()

    def _update_table(self) -> None:
        table = self.query_one("#provider-pick-table", PaginatedDataTable)
        table.clear()
        total = len(self._models)
        self._page = min(self._page, self._max_page())
        start = self._page * self._page_size
        for m in self._models[start:start + self._page_size]:
            table.add_row(*_model_row_cells(m), key=m["id"])

        total_pages = self._max_page() + 1
        current_page = self._page + 1
        table.border_title = (
            f"{total} addable model(s)  │  Page {current_page}/{total_pages}  │  "
            f"PgUp/PgDn · Fn+↑/↓ · Ctrl+B/F"
        )
        if table.row_count:
            table.move_cursor(row=0)

    def _selected_model(self) -> dict | None:
        table = self.query_one("#provider-pick-table", PaginatedDataTable)
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
            if self._fetching:
                return
            model = self._selected_model()
            if not model:
                self.query_one("#provider-pick-feedback", Static).update(
                    "[yellow]Select a model row first.[/]"
                )
                return
            self.dismiss(catalog_prefill_from_source(self._slug, model))

    def action_close(self) -> None:
        self.dismiss(None)
