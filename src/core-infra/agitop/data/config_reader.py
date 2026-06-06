"""
Config reader — reads system_config.json.
"""

import json
from pathlib import Path


class ConfigReader:
    """Reads system_config.json for mode, spawn state, identity."""

    def __init__(self, config_path: str):
        self.config_path = Path(config_path)

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
