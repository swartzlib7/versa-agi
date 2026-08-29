"""API Keys modal — credentials + optional COA first-login model picker."""

from __future__ import annotations

import json
import os
import subprocess

from textual.app import ComposeResult
from textual.containers import Container, Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Select, Static


def _mask_key(value: str) -> str:
    """Return masked key showing last 4 characters."""
    if not value or len(value) < 5:
        return "***"
    return f"***...{value[-4:]}"


def _read_gemini_key() -> str:
    """Read current Gemini API key from coa.env."""
    env_path = "/etc/versa-agi/coa.env"
    try:
        with open(env_path, "r") as f:
            for line in f:
                if line.startswith("GEMINI_API_KEY="):
                    return line.split("=", 1)[1].strip()
    except Exception:
        pass
    return ""


def _read_vv_token() -> str:
    """Read current VersaVoice API token from coa_config.json."""
    config_path = "/etc/versa-agi/coa_config.json"
    try:
        with open(config_path, "r") as f:
            cfg = json.load(f)
        return cfg.get("versavoice", {}).get("api_token", "")
    except Exception:
        return ""


def _read_env_key(env_var: str) -> str:
    """Read an API key from provider_keys.env by variable name."""
    env_path = "/etc/versa-agi/provider_keys.env"
    if not os.path.isfile(env_path):
        env_path = "/etc/versa-agi/inference_endpoint.env"
    try:
        with open(env_path, "r") as f:
            for line in f:
                if line.startswith(f"{env_var}="):
                    return line.split("=", 1)[1].strip()
    except Exception:
        pass
    return ""


def _read_xai_key() -> str:
    return _read_env_key("XAI_API_KEY")


def _read_openai_key() -> str:
    return _read_env_key("OPENAI_API_KEY")


def _read_anthropic_key() -> str:
    return _read_env_key("ANTHROPIC_API_KEY")


def _read_openrouter_key() -> str:
    return _read_env_key("OPENROUTER_API_KEY")


def _is_proxy_enabled() -> bool:
    env_path = "/etc/versa-agi/paths.env"
    try:
        with open(env_path, "r") as f:
            for line in f:
                if "VERSA_THIRD_PARTY_ENABLED" in line:
                    return "true" in line.lower()
    except Exception:
        pass
    return False


class ApiKeysModal(ModalScreen):
    """Credentials manager; optional COA bootstrap picker when ``bootstrap=True``."""

    def __init__(self, bootstrap: bool = False) -> None:
        super().__init__()
        self._bootstrap = bootstrap
        self._selected_provider = ""
        self._selected_model = ""
        self._original: dict[str, str] = {}

    def compose(self) -> ComposeResult:
        title = (
            "[bold]🔑 COA Setup — API Keys & Model[/]"
            if self._bootstrap
            else "[bold]🔑 API Keys & Credentials[/]"
        )
        with Vertical(id="api-keys-dialog"):
            yield Static(title, id="msg-dialog-header")
            yield Static("", id="api-keys-status")

            with VerticalScroll(id="api-keys-scroll"):
                with Container(id="api-keys-columns"):
                    with Vertical(classes="api-keys-col"):
                        yield Static("[b]Gemini API Key[/]  [dim](Google AI Studio / GCP)[/]")
                        yield Input(
                            placeholder="Enter Gemini API Key...",
                            password=True,
                            id="input-gemini-key",
                        )
                        yield Static("", id="status-gemini")
                        yield Static("[b]xAI API Key[/]  [dim](Grok)[/]")
                        yield Input(
                            placeholder="Enter xAI API Key...",
                            password=True,
                            id="input-xai-key",
                        )
                        yield Static("", id="status-xai")
                        yield Static("[b]Anthropic API Key[/]  [dim](Claude Models)[/]")
                        yield Input(
                            placeholder="Enter Anthropic API Key...",
                            password=True,
                            id="input-anthropic-key",
                        )
                        yield Static("", id="status-anthropic")

                    with Vertical(classes="api-keys-col"):
                        yield Static("[b]VersaVoice API Token[/]  [dim](Sponsor Token)[/]")
                        yield Input(
                            placeholder="Enter VersaVoice API Token...",
                            password=True,
                            id="input-vv-token",
                        )
                        yield Static("", id="status-vv")
                        yield Static("[b]OpenAI API Key[/]  [dim](GPT Models)[/]")
                        yield Input(
                            placeholder="Enter OpenAI API Key...",
                            password=True,
                            id="input-openai-key",
                        )
                        yield Static("", id="status-openai")
                        yield Static("[b]OpenRouter API Key[/]  [dim](Multi-vendor aggregator)[/]")
                        yield Input(
                            placeholder="Enter OpenRouter API Key...",
                            password=True,
                            id="input-openrouter-key",
                        )
                        yield Static("", id="status-openrouter")

                yield Static("", id="api-keys-feedback")
                # Bootstrap COA picker mounts here after keys are usable
                yield Vertical(id="coa-bootstrap-section")

            with Horizontal(id="api-keys-footer"):
                if self._bootstrap:
                    yield Button("Remind later", id="btn-coa-remind", variant="default")
                yield Button("💾 Save Changes", variant="success", id="btn-api-save")
                if self._bootstrap:
                    yield Button(
                        "Set as COA model",
                        variant="primary",
                        id="btn-coa-set-model",
                    )
                else:
                    yield Button(
                        "Close",
                        classes="dismiss-btn",
                        variant="default",
                        id="msg-dialog-close",
                    )

    async def on_mount(self) -> None:
        self._refresh_key_status()
        if self._bootstrap:
            await self._rebuild_coa_section()

    def _refresh_key_status(self) -> None:
        gemini_key = _read_gemini_key()
        vv_token = _read_vv_token()
        xai_key = _read_xai_key()
        openai_key = _read_openai_key()
        anthropic_key = _read_anthropic_key()
        openrouter_key = _read_openrouter_key()
        proxy_enabled = _is_proxy_enabled()

        g_status = (
            f"[green]✅ Set[/] — {_mask_key(gemini_key)}"
            if gemini_key
            else "[red]❌ Not configured[/]"
        )
        v_status = (
            f"[green]✅ Set[/] — {_mask_key(vv_token)}"
            if vv_token
            else "[red]❌ Not configured[/]"
        )
        x_status_prefix = "[cyan]Enabled[/]" if proxy_enabled else "[dim]Disabled[/]"

        def _provider_status(key: str) -> str:
            if key:
                return f"[green]✅ Set[/] — {_mask_key(key)}  ({x_status_prefix})"
            return f"[red]❌ Not configured[/]  ({x_status_prefix})"

        self.query_one("#status-gemini", Static).update(f"   {g_status}")
        self.query_one("#status-vv", Static).update(f"   {v_status}")
        self.query_one("#status-xai", Static).update(f"   {_provider_status(xai_key)}")
        self.query_one("#status-openai", Static).update(f"   {_provider_status(openai_key)}")
        self.query_one("#status-anthropic", Static).update(
            f"   {_provider_status(anthropic_key)}"
        )
        self.query_one("#status-openrouter", Static).update(
            f"   {_provider_status(openrouter_key)}"
        )

        if self._bootstrap:
            self.query_one("#api-keys-status", Static).update(
                "[dim]Connect at least one cloud provider, then pick a Recommended COA model below.[/]"
            )
        else:
            self.query_one("#api-keys-status", Static).update(
                "[dim]Enter a new value to update. Leave blank to keep current.[/]"
            )

        self._original = {
            "gemini": gemini_key,
            "versavoice": vv_token,
            "xai": xai_key,
            "openai": openai_key,
            "anthropic": anthropic_key,
            "openrouter": openrouter_key,
        }

    async def _rebuild_coa_section(self) -> None:
        from agitop.coa_bootstrap import (
            PROVIDER_LABELS,
            recommended_options,
            usable_providers,
        )

        section = self.query_one("#coa-bootstrap-section", Vertical)
        await section.remove_children()

        providers = usable_providers()
        if not providers:
            await section.mount(
                Static(
                    "[yellow]Save a provider API key above to unlock Recommended COA models.[/]",
                    id="coa-bootstrap-hint",
                )
            )
            return

        await section.mount(
            Static(
                "[b]COA model[/]  [dim]Recommended for your connected provider(s)[/]",
                id="coa-bootstrap-heading",
            )
        )

        if not self._selected_provider or self._selected_provider not in providers:
            self._selected_provider = providers[0]

        if len(providers) > 1:
            opts = [(PROVIDER_LABELS.get(p, p), p) for p in providers]
            await section.mount(
                Select(
                    opts,
                    value=self._selected_provider,
                    id="coa-provider-select",
                    allow_blank=False,
                )
            )
        else:
            await section.mount(
                Static(
                    f"Provider: [cyan]{PROVIDER_LABELS.get(self._selected_provider, self._selected_provider)}[/]",
                    id="coa-provider-label",
                )
            )

        rec = recommended_options(self._selected_provider)
        if not rec:
            await section.mount(
                Static("[red]No Recommended models for this provider.[/]")
            )
            return
        if not self._selected_model or self._selected_model not in [k for _, k in rec]:
            self._selected_model = rec[0][1]
        await section.mount(
            Select(
                rec,
                value=self._selected_model,
                id="coa-model-select",
                allow_blank=False,
                prompt="Recommended COA models",
            )
        )

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id or ""
        if bid == "msg-dialog-close":
            self.app.pop_screen()
        elif bid == "btn-api-save":
            await self._save_keys()
        elif bid == "btn-coa-remind":
            from agitop.coa_bootstrap import mark_bootstrap_remind_later

            mark_bootstrap_remind_later()
            self.app.notify(
                "COA setup deferred — reopen with b or API Keys when ready.",
                severity="warning",
            )
            self.dismiss("remind")
        elif bid == "btn-coa-set-model":
            self._set_coa_model()

    async def on_select_changed(self, event: Select.Changed) -> None:
        if not self._bootstrap:
            return
        sid = event.select.id or ""
        if sid == "coa-provider-select" and event.value != Select.BLANK:
            self._selected_provider = str(event.value)
            self._selected_model = ""
            await self._rebuild_coa_section()
        elif sid == "coa-model-select" and event.value != Select.BLANK:
            self._selected_model = str(event.value)

    async def _save_keys(self) -> None:
        """Save changed keys via agictl system set-key."""
        new_gemini = self.query_one("#input-gemini-key", Input).value.strip()
        new_vv = self.query_one("#input-vv-token", Input).value.strip()
        new_xai = self.query_one("#input-xai-key", Input).value.strip()
        new_openai = self.query_one("#input-openai-key", Input).value.strip()
        new_anthropic = self.query_one("#input-anthropic-key", Input).value.strip()
        new_openrouter = self.query_one("#input-openrouter-key", Input).value.strip()

        changes = []
        if new_gemini and new_gemini != self._original.get("gemini", ""):
            changes.append(("gemini", new_gemini))
        if new_vv and new_vv != self._original.get("versavoice", ""):
            changes.append(("versavoice", new_vv))
        if new_xai and new_xai != self._original.get("xai", ""):
            changes.append(("xai", new_xai))
        if new_openai and new_openai != self._original.get("openai", ""):
            changes.append(("openai", new_openai))
        if new_anthropic and new_anthropic != self._original.get("anthropic", ""):
            changes.append(("anthropic", new_anthropic))
        if new_openrouter and new_openrouter != self._original.get("openrouter", ""):
            changes.append(("openrouter", new_openrouter))

        if not changes:
            self.query_one("#api-keys-feedback", Static).update(
                "[yellow]No changes detected. Enter new values in the fields above.[/]"
            )
            return

        results = []
        errors = []
        # VV / provider keys can take longer (live VV validation).
        # set-key may migrate + live-import the shipped COA set (8 OpenRouter rows).
        timeout = 90 if any(t == "versavoice" for t, _ in changes) else 75
        for key_type, value in changes:
            try:
                proc = subprocess.run(
                    ["sudo", "agictl", "system", "set-key", key_type, value],
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                )
                result = json.loads(proc.stdout) if proc.stdout else {}
                if result.get("success"):
                    count = result.get("files_updated", 0)
                    results.append(f"[green]✅ {key_type}[/] — {count} file(s) updated")
                else:
                    err = result.get("error", proc.stderr.strip() or "Unknown error")
                    errors.append(f"[red]❌ {key_type}[/] — {err}")
            except subprocess.TimeoutExpired:
                errors.append(f"[red]❌ {key_type}[/] — Command timed out")
            except Exception as e:
                errors.append(f"[red]❌ {key_type}[/] — {e}")

        self.query_one("#api-keys-feedback", Static).update("\n".join(results + errors))

        if results and not errors:
            self.app.notify(
                f"✅ {len(results)} key(s) updated successfully",
                severity="information",
            )
            self._refresh_key_status()
            # Clear password fields after successful save
            for fid in (
                "input-gemini-key",
                "input-vv-token",
                "input-xai-key",
                "input-openai-key",
                "input-anthropic-key",
                "input-openrouter-key",
            ):
                self.query_one(f"#{fid}", Input).value = ""
            if self._bootstrap:
                await self._rebuild_coa_section()

    def _set_coa_model(self) -> None:
        from agitop.coa_bootstrap import assign_coa_model

        feedback = self.query_one("#api-keys-feedback", Static)
        model = self._selected_model
        try:
            sel = self.query_one("#coa-model-select", Select)
            if sel.value != Select.BLANK:
                model = str(sel.value)
        except Exception:
            pass
        if not model:
            feedback.update(
                "[yellow]Save a provider key first, then select a Recommended COA model.[/]"
            )
            return
        ok, msg = assign_coa_model(model)
        if not ok:
            feedback.update(f"[red]{msg}[/]")
            return
        self.app.notify(msg, severity="information")
        self.dismiss("done")
