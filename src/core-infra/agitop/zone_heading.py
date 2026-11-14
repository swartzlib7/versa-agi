"""Headings that name the machine's zone: `Due (EST)` (state_time_display.md §1 rule 9).

The zone name is read when a heading is drawn, never once at start-up, so a
table opened after a daylight-saving change is right. A table that stays on
screen calls ``refresh`` each time it reloads its rows.
"""

from __future__ import annotations

import os
import sys

_CORE_INFRA = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _CORE_INFRA not in sys.path:
    sys.path.insert(0, _CORE_INFRA)
import local_time  # noqa: E402

from rich.text import Text  # noqa: E402


def heading(name: str) -> str:
    """`Due` → `Due (EST)`, with the zone name as it is now."""
    return f"{name} ({local_time.zone_label()})"


def refresh(table, name: str) -> bool:
    """Rename the `name (ZONE)` column of a DataTable when the zone name has changed.

    True when a heading was renamed. A table with no such column is left alone.
    """
    wanted = heading(name)
    prefix = f"{name} ("
    for column in table.columns.values():
        label = getattr(column.label, "plain", str(column.label))
        if not label.startswith(prefix) or label == wanted:
            continue
        column.label = Text(wanted)
        # DataTable keeps its drawn header cells. This is the call its own
        # add_column / remove_column use to drop them.
        clear = getattr(table, "_clear_caches", None)
        if callable(clear):
            clear()
        table.refresh()
        return True
    return False
