"""API Keys Management Modal — View and update system credentials."""

import os
import json
import subprocess
from textual.app import ComposeResult
from textual.screen import ModalScreen
from textual.containers import Vertical, Horizontal, Container
from textual.widgets import Static, Button, Input


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
    """Read current xAI API key from provider_keys.env."""
    return _read_env_key("XAI_API_KEY")


def _read_openai_key() -> str:
    """Read current OpenAI API key from provider_keys.env."""
    return _read_env_key("OPENAI_API_KEY")


def _read_anthropic_key() -> str:
    """Read current Anthropic API key from provider_keys.env."""
    return _read_env_key("ANTHROPIC_API_KEY")


def _read_openrouter_key() -> str:
    """Read current OpenRouter API key from provider_keys.env."""
    return _read_env_key("OPENROUTER_API_KEY")


def _is_proxy_enabled() -> bool:
    """Check if cloud proxy is enabled in paths.env."""
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
    """Modal for viewing and updating API keys."""

    def compose(self) -> ComposeResult:
        with Vertical(id="api-keys-dialog"):
            yield Static("[bold]🔑 API Keys & Credentials[/]", id="msg-dialog-header")
            yield Static("", id="api-keys-status")

            with Container(id="api-keys-columns"):
                with Vertical(classes="api-keys-col"):
                    yield Static("[b]Gemini API Key[/]  [dim](Google AI Studio / GCP)[/]")
                    yield Input(placeholder="Enter Gemini API Key...", password=True, id="input-gemini-key")
                    yield Static("", id="status-gemini")
                    yield Static("[b]xAI API Key[/]  [dim](Grok)[/]")
                    yield Input(placeholder="Enter xAI API Key...", password=True, id="input-xai-key")
                    yield Static("", id="status-xai")
                    yield Static("[b]Anthropic API Key[/]  [dim](Claude Models)[/]")
                    yield Input(placeholder="Enter Anthropic API Key...", password=True, id="input-anthropic-key")
                    yield Static("", id="status-anthropic")

                with Vertical(classes="api-keys-col"):
                    yield Static("[b]VersaVoice API Token[/]  [dim](Sponsor Token)[/]")
                    yield Input(placeholder="Enter VersaVoice API Token...", password=True, id="input-vv-token")
                    yield Static("", id="status-vv")
                    yield Static("[b]OpenAI API Key[/]  [dim](GPT Models)[/]")
                    yield Input(placeholder="Enter OpenAI API Key...", password=True, id="input-openai-key")
                    yield Static("", id="status-openai")
                    yield Static("[b]OpenRouter API Key[/]  [dim](Multi-vendor aggregator)[/]")
                    yield Input(placeholder="Enter OpenRouter API Key...", password=True, id="input-openrouter-key")
                    yield Static("", id="status-openrouter")

            yield Static("", id="api-keys-feedback")

            with Horizontal(id="msg-dialog-actions"):
                yield Button("💾 Save Changes", variant="success", id="btn-api-save")
                yield Button("Close", classes="dismiss-btn", variant="default", id="msg-dialog-close")

    def on_mount(self) -> None:
        """Load current key states and populate status indicators."""
        gemini_key = _read_gemini_key()
        vv_token = _read_vv_token()
        xai_key = _read_xai_key()
        openai_key = _read_openai_key()
        anthropic_key = _read_anthropic_key()
        openrouter_key = _read_openrouter_key()
        proxy_enabled = _is_proxy_enabled()

        g_status = f"[green]✅ Set[/] — {_mask_key(gemini_key)}" if gemini_key else "[red]❌ Not configured[/]"
        v_status = f"[green]✅ Set[/] — {_mask_key(vv_token)}" if vv_token else "[red]❌ Not configured[/]"
        x_status_prefix = f"[cyan]Enabled[/]" if proxy_enabled else "[dim]Disabled[/]"

        def _provider_status(key: str) -> str:
            if key:
                return f"[green]✅ Set[/] — {_mask_key(key)}  ({x_status_prefix})"
            return f"[red]❌ Not configured[/]  ({x_status_prefix})"

        self.query_one("#status-gemini", Static).update(f"   {g_status}")
        self.query_one("#status-vv", Static).update(f"   {v_status}")
        self.query_one("#status-xai", Static).update(f"   {_provider_status(xai_key)}")
        self.query_one("#status-openai", Static).update(f"   {_provider_status(openai_key)}")
        self.query_one("#status-anthropic", Static).update(f"   {_provider_status(anthropic_key)}")
        self.query_one("#status-openrouter", Static).update(f"   {_provider_status(openrouter_key)}")
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

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "msg-dialog-close":
            self.app.pop_screen()
        elif event.button.id == "btn-api-save":
            self._save_keys()

    def _save_keys(self) -> None:
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
        for key_type, value in changes:
            try:
                proc = subprocess.run(
                    ["sudo", "agictl", "system", "set-key", key_type, value],
                    capture_output=True, text=True, timeout=15
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

        feedback_parts = results + errors
        feedback = "\n".join(feedback_parts)
        self.query_one("#api-keys-feedback", Static).update(feedback)

        if results and not errors:
            self.app.notify(f"✅ {len(results)} key(s) updated successfully", severity="information")
            self.on_mount()
