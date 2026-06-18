"""Model Routing Modal — preferred-map and routing mode (setup.ini [model_routing])."""

import json
import subprocess

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Checkbox, Select, Static, TabbedContent, TabPane

from agitop.panels.system_settings_modal import (
    _read_ini_value,
    _write_ini_value_err,
)

_WORK_MODALITIES = ("fast", "balanced", "reasoning", "code", "local")
_WORK_MODALITY_LABELS = {
    "fast": "Fast — acknowledgments, simple replies",
    "balanced": "Balanced — general mixed work",
    "reasoning": "Reasoning — planning, architecture, analysis",
    "code": "Code — implementation, debugging",
    "local": "Local — on-prem / privacy-preferring",
}
_OUTPUT_MODALITIES = ("image", "audio", "video")
_OUTPUT_LABELS = {
    "image": "Image — generation / rendering",
    "audio": "Audio — TTS, speech, sound",
    "video": "Video — generation / rendering",
}


def _catalog_list() -> list[dict]:
    try:
        proc = subprocess.run(
            ["sudo", "-u", "watchdog", "/usr/local/lib/versa-agi/agictl", "model", "catalog", "list"],
            capture_output=True, text=True, timeout=15,
        )
        if not proc.stdout:
            return []
        for line in reversed(proc.stdout.strip().splitlines()):
            if line.strip().startswith("{"):
                data = json.loads(line)
                return data.get("models", []) if data.get("success", True) else []
    except Exception:
        pass
    return []


def _load_router_model_choices(current_key: str = "") -> list[tuple[str, str]]:
    """Enabled, router-eligible catalog keys for preferred-map pickers."""
    choices: list[tuple[str, str]] = [("(none — pool fallback)", "")]
    seen = {""}
    for m in sorted(_catalog_list(), key=lambda r: r.get("key", "")):
        if m.get("enabled") and m.get("router_eligible"):
            key = m["key"]
            label = m.get("label") or key
            note = " (not COA-approved)" if not m.get("coa") else ""
            choices.append((f"{key} — {label}{note}", key))
            seen.add(key)
    current_key = (current_key or "").strip()
    if current_key and current_key not in seen:
        choices.append((f"{current_key} — (not eligible)", current_key))
    return choices


def _load_output_model_choices(output_modality: str, current_key: str = "") -> list[tuple[str, str]]:
    """Enabled COA-approved models that declare the given output_modality."""
    choices: list[tuple[str, str]] = [("(none — no system default)", "")]
    seen = {""}
    for m in sorted(_catalog_list(), key=lambda r: r.get("key", "")):
        if not m.get("enabled") or not m.get("coa"):
            continue
        outs = {x.strip() for x in (m.get("output_modalities") or "text").split(",") if x.strip()}
        if output_modality not in outs:
            continue
        key = m["key"]
        label = m.get("label") or key
        choices.append((f"{key} — {label}", key))
        seen.add(key)
    current_key = (current_key or "").strip()
    if current_key and current_key not in seen:
        choices.append((f"{current_key} — (output mismatch)", current_key))
    return choices


class ModelRoutingModal(ModalScreen):
    """Configure ephemeral per-spawn model routing (pool vs preferred-map)."""

    BINDINGS = [Binding("escape", "close", "Close")]

    def compose(self) -> ComposeResult:
        routing_mode = _read_ini_value("agent", "model_routing_mode", "pool")
        routing_default = _read_ini_value("agent", "model_routing_enabled", "false").lower() == "true"

        with Vertical(id="model-routing-dialog"):
            yield Static("[bold]🔀 Model Routing[/]", id="model-routing-header")
            yield Static(
                "[dim]Ephemeral per-spawn routing — triage classifies work modality. "
                "[bold]Pool[/]: triage may pick among router-eligible candidates (+ Model Feedback biases). "
                "[bold]Preferred[/]: triage classifies tier → one preferred key per tier on the Work tab. "
                "Mode and new-agent default: General tab. "
                "Per-agent toggle: Agents → General (Auto Model Routing).[/]",
                id="routing-help",
            )

            with TabbedContent(initial="routing-general-tab", id="model-routing-tabs"):
                with TabPane("General", id="routing-general-tab"):
                    with VerticalScroll(id="routing-general-scroll"):
                        yield Static("[bold]Routing policy[/]")
                        with Container(id="routing-general-columns"):
                            with Vertical(classes="routing-col"):
                                yield Static("[cyan]Routing mode[/]")
                                yield Select(
                                    [
                                        ("Pool — triage picks from router-eligible candidates", "pool"),
                                        ("Preferred — triage classifies; Work tab map selects model", "preferred"),
                                    ],
                                    value=routing_mode if routing_mode in ("pool", "preferred") else "pool",
                                    id="select-routing-mode",
                                    allow_blank=False,
                                )
                            with Vertical(classes="routing-col"):
                                yield Static(
                                    "[cyan]Enable auto model routing for newly registered agents[/]"
                                )
                                with Container(classes="routing-checkbox-box"):
                                    yield Checkbox(
                                        "Default for new agents",
                                        id="chk-routing-default",
                                        value=routing_default,
                                    )

                with TabPane("Work Routing", id="routing-work-tab"):
                    with VerticalScroll(id="routing-work-scroll"):
                        yield Static("[bold]Preferred model per work modality[/]")
                        yield Static(
                            "[dim]Router-eligible catalog keys. Non-COA models work for sub-agents; "
                            "COA spawns ignore them at runtime. "
                            "Empty = pool fallback when mode is preferred (General tab). "
                            "Edit catalog work_modality via 🧩 MODELS → Edit Model.[/]"
                        )
                        work_mid = (len(_WORK_MODALITIES) + 1) // 2
                        with Container(id="routing-work-columns"):
                            for col_mods in (
                                _WORK_MODALITIES[:work_mid],
                                _WORK_MODALITIES[work_mid:],
                            ):
                                with Vertical(classes="routing-col"):
                                    for wm in col_mods:
                                        current = _read_ini_value("model_routing", wm, "")
                                        yield Static(f"[cyan]{_WORK_MODALITY_LABELS[wm]}[/]")
                                        yield Select(
                                            _load_router_model_choices(current),
                                            value=current if current else "",
                                            id=f"select-routing-pref-{wm}",
                                            allow_blank=False,
                                        )

                with TabPane("Output Routing", id="routing-output-tab"):
                    with VerticalScroll(id="routing-output-scroll"):
                        yield Static("[bold]Output routing[/]")
                        yield Static(
                            "[dim]Generation defaults for Utility Models (Phase F) — one key per "
                            "image/audio/video. Catalog must declare matching output_modality. "
                            "Not used for chat spawn routing yet.[/]"
                        )
                        out_mid = (len(_OUTPUT_MODALITIES) + 1) // 2
                        with Container(id="routing-output-columns"):
                            for col_mods in (
                                _OUTPUT_MODALITIES[:out_mid],
                                _OUTPUT_MODALITIES[out_mid:],
                            ):
                                with Vertical(classes="routing-col"):
                                    for om in col_mods:
                                        current = _read_ini_value("output_routing", om, "")
                                        yield Static(f"[cyan]{_OUTPUT_LABELS[om]}[/]")
                                        yield Select(
                                            _load_output_model_choices(om, current),
                                            value=current if current else "",
                                            id=f"select-output-pref-{om}",
                                            allow_blank=False,
                                        )

            with Horizontal(id="model-routing-actions"):
                yield Button("Save", variant="success", id="btn-routing-save")
                yield Button("Cancel", classes="dismiss-btn", variant="default", id="btn-routing-close")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-routing-close":
            self.app.pop_screen()
            return
        if event.button.id != "btn-routing-save":
            return

        routing_mode = str(self.query_one("#select-routing-mode", Select).value or "pool")
        routing_default = self.query_one("#chk-routing-default", Checkbox).value

        failures: list[str] = []

        def _save(section: str, key: str, value: str, label: str) -> None:
            ok, err = _write_ini_value_err(section, key, value)
            if not ok:
                failures.append(f"{label}: {err}")

        def _select_value(widget_id: str) -> str:
            raw = self.query_one(widget_id, Select).value
            if raw is None or raw is Select.BLANK:
                return ""
            return str(raw)

        _save("agent", "model_routing_mode", routing_mode, "Routing mode")
        _save(
            "agent", "model_routing_enabled",
            "true" if routing_default else "false", "New-agent default",
        )
        for wm in _WORK_MODALITIES:
            _save("model_routing", wm, _select_value(f"#select-routing-pref-{wm}"),
                  f"{wm} preferred model")
        for om in _OUTPUT_MODALITIES:
            _save("output_routing", om, _select_value(f"#select-output-pref-{om}"),
                  f"{om} output model")

        if not failures:
            self.app.notify(
                f"Routing saved — mode: {routing_mode}, "
                f"new-agent default: {'on' if routing_default else 'off'}",
                title="Model Routing",
            )
            self.app.pop_screen()
        else:
            detail = "\n".join(f"• {f}" for f in failures[:6])
            self.app.notify(
                f"Could not save {len(failures)} routing setting(s):\n{detail}\n"
                f"Pick an eligible model or set '(none — pool fallback)'.",
                title="Model Routing",
                severity="warning",
                timeout=12,
            )

    def action_close(self) -> None:
        self.app.pop_screen()
