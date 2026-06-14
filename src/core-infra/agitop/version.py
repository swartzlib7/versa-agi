"""Read product version from canonical VERSION file."""

from pathlib import Path

_VERSION_FILE = Path(__file__).resolve().parents[1] / "VERSION"


def read_product_version(default: str = "unknown") -> str:
    if _VERSION_FILE.is_file():
        value = _VERSION_FILE.read_text(encoding="utf-8").strip()
        if value:
            return value
    return default
