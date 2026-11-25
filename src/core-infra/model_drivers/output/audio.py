"""Audio output driver — write generated audio bytes to disk."""

from __future__ import annotations

import os


def write_audio_artifact(path: str, data: bytes) -> str:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)
    return path
