"""
Status reader — reads .current_cycle_id for runtime timer.
"""

from pathlib import Path
from typing import Optional


class StatusReader:
    """Reads the current cycle ID file for the runtime timer."""

    def __init__(self, cycle_id_path: str):
        self.cycle_id_path = Path(cycle_id_path)

    def get_current_cycle_id(self) -> Optional[str]:
        """Read the current cycle ID file."""
        try:
            if self.cycle_id_path.exists():
                return self.cycle_id_path.read_text().strip() or None
        except Exception:
            pass
        return None
