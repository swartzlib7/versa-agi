"""
Config reader — reads system_config.json and setup.ini registration state.
"""

import configparser
import json
from pathlib import Path


class ConfigReader:
    """Reads system_config.json for mode, spawn state, identity."""

    SETUP_INI_PATH = Path("/etc/versa-agi/setup.ini")

    def __init__(self, config_path: str, setup_ini_path: str = ""):
        self.config_path = Path(config_path)
        self.setup_ini_path = Path(setup_ini_path) if setup_ini_path else self.SETUP_INI_PATH

    def get_config(self) -> dict:
        """Read the full system config."""
        try:
            if self.config_path.exists():
                return json.loads(self.config_path.read_text())
        except Exception:
            pass
        return {}

    def get_spawn_state(self) -> str:
        """Get the spawn state (active/paused)."""
        return self.get_config().get("spawn_state", "unknown")

    def get_identity(self) -> dict:
        """Get agent identity info."""
        return self.get_config().get("identity", {})

    def get_agent_name(self) -> str:
        """Get the agent name from config."""
        return self.get_config().get("agent", "coa")

    def get_registration_state(self) -> dict[str, str]:
        """Read [registration] runtime keys from setup.ini."""
        cfg = configparser.ConfigParser(delimiters=("=",))
        if not self.setup_ini_path.is_file():
            return {}
        try:
            cfg.read(self.setup_ini_path)
            if not cfg.has_section("registration"):
                return {}
            return {k: v for k, v in cfg.items("registration")}
        except Exception:
            return {}

    def get_registration_status(self) -> dict:
        """Read cached registration status written by install_acceptance.py."""
        status_path = Path("/var/lib/versa-agi/registration-status.json")
        if not status_path.is_file():
            return {}
        try:
            return json.loads(status_path.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def is_registration_submitted(self) -> bool:
        """True when install acceptance telemetry was successfully submitted."""
        return self.get_registration_state().get(
            "registration_submitted", "false"
        ).lower() == "true"
