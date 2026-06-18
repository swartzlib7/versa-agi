"""Shared model generation param fields for agitop (Model Manager + agent overrides)."""

from __future__ import annotations

import json
from typing import Any, Callable

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Input, Select, Static, TextArea

_THINK_MODE_OPTIONS = [
    ("inherit", ""),
    ("boolean (on/off)", "boolean"),
    ("levels (low/medium/high)", "levels"),
]


def format_json_pretty(value: Any) -> str:
    """Pretty-print a JSON object for TextArea display."""
    if not value:
        return ""
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return ""
        try:
            value = json.loads(raw)
        except json.JSONDecodeError:
            return raw
    if not isinstance(value, dict):
        return str(value)
    return json.dumps(value, indent=2, sort_keys=True)


def parse_json_object(raw: str, *, field_label: str = "JSON") -> tuple[dict[str, Any] | None, str | None]:
    """Parse a JSON object; return (dict, error_message)."""
    text = (raw or "").strip()
    if not text:
        return None, None
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        return None, f"{field_label} must be valid JSON ({exc.msg})"
    if not isinstance(parsed, dict):
        return None, f"{field_label} must be a JSON object"
    return parsed, None


def compose_catalog_generation_params(
    reasoning_opts: list[tuple[str, str]],
    *,
    show_think_mode: bool = False,
) -> ComposeResult:
    """Default Generation Params section for Model Manager catalog form."""
    with Horizontal(classes="mm-form-row"):
        with Vertical(classes="mm-form-col"):
            yield Static("[b]Temperature[/]")
            yield Input(placeholder="inherit", id="f-temperature")
        with Vertical(classes="mm-form-col"):
            yield Static("[b]Reasoning effort[/]")
            yield Select(
                reasoning_opts,
                value="",
                allow_blank=True,
                id="f-reasoning-effort",
                prompt="inherit",
            )
            yield Static(
                "Default thinking level at spawn. Blank = inherit.",
                classes="mm-form-hint",
            )
    with Horizontal(classes="mm-form-row"):
        with Vertical(classes="mm-form-col"):
            yield Static("[b]Reasoning max tokens[/]")
            yield Input(placeholder="inherit", type="integer", id="f-reasoning-max")
        with Vertical(classes="mm-form-col"):
            yield Static("[b]Allowed reasoning[/]")
            yield Input(placeholder="inherit", id="f-allowed-efforts")
            yield Static(
                "Legal effort values for this model (pickers + validation) — not the default. CSV: none,low,high",
                classes="mm-form-hint",
            )
    if show_think_mode:
        with Horizontal(classes="mm-form-row"):
            with Vertical(classes="mm-form-col"):
                yield Static("[b]Think mode[/]  [dim](Ollama only)[/]")
                yield Select(
                    _THINK_MODE_OPTIONS,
                    value="",
                    allow_blank=True,
                    id="f-think-mode",
                    prompt="inherit",
                )
    with Horizontal(classes="mm-form-row"):
        with Vertical(classes="mm-form-col"):
            yield Static("[b]Extra passthrough[/]")
            yield TextArea("", id="f-extra", show_line_numbers=False, classes="mm-form-textarea")
            yield Static(
                "JSON object of provider sampling knobs (top_p, top_k, penalties, num_predict). "
                "Merged into the extra bag and applied at inference.",
                classes="mm-form-hint",
            )


def load_catalog_generation_params(
    root,
    *,
    custom_params: dict[str, Any],
    resolved: dict[str, Any],
    model_key: str,
    reasoning_opts_fn: Callable[[], list[tuple[str, str]]],
) -> list[str]:
    """Populate catalog generation fields; return validation warnings."""
    from harness.model_params import (
        effective_agent_reasoning_effort,
        efforts_to_csv,
        normalize_custom_params,
    )

    structured, warnings = normalize_custom_params(custom_params, model_key)

    if structured.get("temperature") is not None:
        root.query_one("#f-temperature", Input).value = str(structured["temperature"])
    elif resolved.get("temperature") is not None:
        root.query_one("#f-temperature", Input).placeholder = f"inherit ({resolved['temperature']})"

    sel = root.query_one("#f-reasoning-effort", Select)
    custom_effort = structured.get("reasoning_effort")
    resolved_effort = resolved.get("reasoning_effort")
    try:
        if custom_effort is not None:
            sel.value = effective_agent_reasoning_effort(model_key, custom_effort)
        else:
            sel.value = ""
            if resolved_effort:
                sel.prompt = f"inherit ({resolved_effort})"
    except Exception:
        opts = reasoning_opts_fn()
        if custom_effort is not None and custom_effort in {v for _, v in opts}:
            sel.value = custom_effort
        else:
            sel.value = ""
            if resolved_effort:
                sel.prompt = f"inherit ({resolved_effort})"

    if structured.get("reasoning_max_tokens") is not None:
        root.query_one("#f-reasoning-max", Input).value = str(structured["reasoning_max_tokens"])
    elif resolved.get("reasoning_max_tokens") is not None:
        root.query_one("#f-reasoning-max", Input).placeholder = (
            f"inherit ({resolved['reasoning_max_tokens']})"
        )

    allowed = structured.get("allowed_reasoning_efforts")
    if allowed is not None:
        root.query_one("#f-allowed-efforts", Input).value = efforts_to_csv(allowed)
    elif resolved.get("allowed_reasoning_efforts"):
        root.query_one("#f-allowed-efforts", Input).placeholder = (
            f"inherit ({efforts_to_csv(resolved['allowed_reasoning_efforts'])})"
        )

    try:
        think_sel = root.query_one("#f-think-mode", Select)
        custom_think = structured.get("think_mode")
        resolved_think = resolved.get("think_mode")
        if custom_think:
            think_sel.value = custom_think
        else:
            think_sel.value = ""
            if resolved_think:
                think_sel.prompt = f"inherit ({resolved_think})"
    except Exception:
        pass

    extra = structured.get("extra")
    if extra:
        root.query_one("#f-extra", TextArea).text = format_json_pretty(extra)

    return warnings


def collect_catalog_generation_params(root, model_key: str) -> tuple[dict[str, Any] | None, str | None]:
    """Read catalog generation fields; return (result dict for agictl, error message)."""
    from harness.model_params import allowed_reasoning_efforts

    result: dict[str, Any] = {}
    flags = {
        "has_temp": False,
        "has_reasoning": False,
        "has_rmax": False,
        "has_allowed": False,
        "has_think": False,
        "has_extra": False,
    }

    temp_raw = root.query_one("#f-temperature", Input).value.strip()
    if temp_raw:
        try:
            result["temperature"] = float(temp_raw)
            flags["has_temp"] = True
        except ValueError:
            return None, "Temperature must be a number."

    reasoning_val = root.query_one("#f-reasoning-effort", Select).value
    if isinstance(reasoning_val, str) and reasoning_val:
        if reasoning_val not in allowed_reasoning_efforts(model_key):
            return None, (
                f"Reasoning effort '{reasoning_val}' is not valid for model '{model_key}'."
            )
        result["reasoning_effort"] = reasoning_val
        flags["has_reasoning"] = True

    rmax_raw = root.query_one("#f-reasoning-max", Input).value.strip()
    if rmax_raw:
        try:
            result["reasoning_max_tokens"] = int(rmax_raw)
            flags["has_rmax"] = True
        except ValueError:
            return None, "Reasoning max tokens must be an integer."

    allowed_raw = root.query_one("#f-allowed-efforts", Input).value.strip()
    if allowed_raw:
        from harness.model_params import REASONING_EFFORTS, efforts_from_csv

        parts = efforts_from_csv(allowed_raw) or []
        invalid = [x for x in parts if x.lower() not in REASONING_EFFORTS]
        if invalid:
            return None, f"Invalid allowed reasoning value(s): {', '.join(invalid)}"
        result["allowed_reasoning_efforts_csv"] = allowed_raw
        flags["has_allowed"] = True

    try:
        think_val = root.query_one("#f-think-mode", Select).value
        if isinstance(think_val, str) and think_val:
            result["think_mode"] = think_val
            flags["has_think"] = True
    except Exception:
        pass

    extra_raw = root.query_one("#f-extra", TextArea).text.strip()
    if extra_raw:
        parsed, err = parse_json_object(extra_raw, field_label="Extra passthrough")
        if err:
            return None, err
        result["extra_json"] = json.dumps(parsed, separators=(",", ":"))
        flags["has_extra"] = True

    result["_has_params"] = any(flags.values())
    return result, None
