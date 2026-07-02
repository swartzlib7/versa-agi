"""Dashboard feature gates — sourced from ``setup.ini [features]`` (D34).

The flags are the **single source of truth** in ``/etc/versa-agi/setup.ini``
under ``[features]`` (operator-chosen at install / ``--update``, persisted, and
carried forward by ``system reconcile-config``). This module reads that section
at import and exposes the same module-level booleans agitop already imports
(``from agitop.feature_flags import ORGANIZATION_UI_VISIBLE``), so no call site
changes. When the file/section/key is absent (pre-D34 systems, tests, dev
checkouts), the hard-coded fallback below applies — so behaviour is unchanged
until the operator's choice exists.

TD-UTIL-001 / TD-SCRIPT-001 / TD-ORG-001 + output routing: each maps to one
``[features]`` key. lifeline.sh reads the same section to tell agents which
``agictl`` command groups are unavailable when a feature is off.
"""

from __future__ import annotations

import os

_SETUP_INI = os.environ.get("VERSA_SETUP_INI", "/etc/versa-agi/setup.ini")

# (module flag, setup.ini key, safe fallback when the key is unset)
_FLAGS = (
    ("UTILITY_MODELS_UI_VISIBLE", "utility_models_ui", False),
    ("SCRIPT_TASKS_UI_VISIBLE", "script_tasks_ui", False),
    ("OUTPUT_ROUTING_UI_VISIBLE", "output_routing_ui", False),
    ("ORGANIZATION_UI_VISIBLE", "organization_ui", False),
)


def _read_features(path: str) -> dict[str, bool]:
    """Parse ``[features]`` from setup.ini → {key: bool}. Empty on any error."""
    out: dict[str, bool] = {}
    try:
        with open(path, encoding="utf-8") as f:
            in_section = False
            for line in f:
                s = line.strip()
                if s.startswith("[") and s.endswith("]"):
                    in_section = s[1:-1] == "features"
                    continue
                if in_section and s and not s.startswith("#") and "=" in s:
                    key, _, val = s.partition("=")
                    out[key.strip()] = val.strip().lower() in ("1", "true", "yes", "on")
    except OSError:
        pass
    return out


_features = _read_features(_SETUP_INI)

# Resolve each flag: setup.ini value when present, else the safe fallback.
UTILITY_MODELS_UI_VISIBLE = _features.get("utility_models_ui", False)
SCRIPT_TASKS_UI_VISIBLE = _features.get("script_tasks_ui", False)
OUTPUT_ROUTING_UI_VISIBLE = _features.get("output_routing_ui", False)
ORGANIZATION_UI_VISIBLE = _features.get("organization_ui", False)
