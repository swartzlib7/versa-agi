"""Placeholder drivers for image/audio/video output modalities."""

from __future__ import annotations


def not_implemented(*_args, **_kwargs):
    # Imported lazily so this module has no import-time dependency on the runner.
    from harness.utility_runner import UtilityRunError

    raise UtilityRunError(
        "driver_pending",
        "Output driver for this modality is not wired yet — use a text UM or chat model",
    )
