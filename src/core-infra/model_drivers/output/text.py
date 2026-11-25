"""Text output driver — write model text to disk."""

from __future__ import annotations

import os


def write_text_artifact(path: str, content: str) -> str:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    return path
