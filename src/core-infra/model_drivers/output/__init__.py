"""Output modality drivers (TD-UTIL-001).

Authoritative driver registry for the Utility Model runner. ``registry.yaml`` is
the human-readable manifest; this module is the runtime loader (a plain Python
map, so there is no YAML dependency in the agictl/harness path). Keep the two in
sync when adding a driver.

Each entry maps an ``output_modality`` to ``(module_path, callable_name)``. The
callable is imported lazily on first use to avoid import-time coupling (the stub
driver imports :class:`UtilityRunError` from the runner).
"""

from __future__ import annotations

import importlib
from typing import Callable

# modality -> (module, callable). `.stub` entries are placeholders that raise
# `driver_pending` until a real generation model/driver is wired (e.g. video, which
# has no output model and uses a separate async API).
_DRIVERS: dict[str, tuple[str, str]] = {
    "text": ("model_drivers.output.text", "write_text_artifact"),
    "image": ("model_drivers.output.image", "write_image_artifact"),
    "audio": ("model_drivers.output.audio", "write_audio_artifact"),
    "video": ("model_drivers.output.stub", "not_implemented"),
}


def get_output_driver(modality: str) -> Callable:
    """Return the driver callable registered for ``modality``.

    Raises ``ValueError`` for an unregistered modality (the runner validates the
    modality before calling, so this is a safety net).
    """
    spec = _DRIVERS.get((modality or "").strip().lower())
    if not spec:
        raise ValueError(f"No output driver registered for modality '{modality}'")
    module_name, callable_name = spec
    return getattr(importlib.import_module(module_name), callable_name)


def has_real_driver(modality: str) -> bool:
    """True when ``modality`` has a working (non-stub) driver wired."""
    spec = _DRIVERS.get((modality or "").strip().lower())
    return bool(spec) and not spec[0].endswith(".stub")
