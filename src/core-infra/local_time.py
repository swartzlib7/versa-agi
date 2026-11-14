"""Stored UTC ↔ local time, for every surface a person or an agent reads or types
(state_time_display.md §1).

Storage is UTC text, `YYYY-MM-DD HH:MM:SS`. Shown and typed times are in the
machine's zone. One source for agictl, agitop and the harness. Standard
library only.

The offset is always the one for the date being converted, so a date on the
other side of a daylight-saving change is right (a fixed "today's offset" is
one hour off for half the year).
"""

from __future__ import annotations

import re
import time
from datetime import datetime, timedelta, timezone

STORE_FORMAT = "%Y-%m-%d %H:%M:%S"
SHOW_FORMAT = "%Y-%m-%d %H:%M"

# Columns that hold a stored UTC time. Used to convert rows for display.
TIME_FIELDS = frozenset({
    "created_at", "updated_at", "completed_at", "due_date", "wake_after", "started_at",
    "ended_at", "session_start_ts", "last_awareness_ts", "script_last_run_at", "assigned_at",
    "first_seen", "last_contact", "profile_synced_at", "last_interaction", "last_assessed_at",
    "environment_assessed_at", "requested_at", "resolved_at", "notified_at", "joined_at",
    "frame_start", "frame_end", "delivered_at",
})

_PLAIN = re.compile(
    r"^(\d{4})-(\d{2})-(\d{2})"
    r"(?:[ T](\d{2}):(\d{2})(?::(\d{2})(?:\.\d+)?)?)?"
    r"\s*(Z|[+-]\d{2}:?\d{2}|[A-Za-z]{2,5})?$"
)

TYPED_FORMATS = (
    '"YYYY-MM-DD HH:MM" (local time), "YYYY-MM-DD HH:MM:SS", "YYYY-MM-DD" (start of that day), '
    'or with a zone: "YYYY-MM-DDTHH:MM:SSZ", "YYYY-MM-DD HH:MM -05:00"'
)


def _naive(match) -> datetime:
    year, month, day, hour, minute, second = (int(g) if g else 0 for g in match.groups()[:6])
    return datetime(year, month, day, hour, minute, second)


def _offset(text: str) -> timezone:
    sign = -1 if text[0] == "-" else 1
    digits = text[1:].replace(":", "")
    return timezone(sign * timedelta(hours=int(digits[:2]), minutes=int(digits[2:4])))


def local_zone_names() -> set[str]:
    """Abbreviations of the machine's zone, e.g. {"EST", "EDT"}."""
    return {name.upper() for name in time.tzname if name}


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def parse_stored(value) -> datetime | None:
    """A stored time → aware UTC datetime. A value with no zone is UTC. None when not a time."""
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    match = _PLAIN.match(str(value or "").strip())
    if not match:
        return None
    try:
        naive = _naive(match)
    except ValueError:
        return None
    zone = match.group(7)
    if not zone or zone.upper() in ("Z", "UTC", "GMT"):
        return naive.replace(tzinfo=timezone.utc)
    if zone[0] in "+-":
        return naive.replace(tzinfo=_offset(zone)).astimezone(timezone.utc)
    if zone.upper() in local_zone_names():
        return naive.astimezone(timezone.utc)     # already shown as local: read it back as local
    return None


_DATE_ONLY = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def is_calendar_date(value) -> bool:
    """`2026-11-13` with no time: a calendar date (an invoice date), not an instant."""
    return bool(_DATE_ONLY.match(str(value or "").strip()))


def to_local(value) -> datetime | None:
    """A stored time → aware datetime in the machine's zone (that date's offset)."""
    parsed = parse_stored(value)
    return parsed.astimezone() if parsed else None


def show(value, seconds: bool = False, zone: bool = True, missing: str = "") -> str:
    """A stored time as local text: `2026-11-13 11:30 EST`.

    Text that is not a time comes back unchanged, so a hand-written value is
    never hidden. Empty → ``missing``.
    """
    if value is None or str(value).strip() == "":
        return missing
    if is_calendar_date(value):
        return str(value).strip()                 # a calendar date has no zone to convert
    local = to_local(value)
    if local is None:
        return str(value)
    text = local.strftime(STORE_FORMAT if seconds else SHOW_FORMAT)
    label = local.strftime("%Z")
    return f"{text} {label}" if zone and label else text


def show_date(value, missing: str = "") -> str:
    """The local calendar date of a stored time: `2026-11-13`."""
    if value is None or str(value).strip() == "":
        return missing
    if is_calendar_date(value):
        return str(value).strip()
    local = to_local(value)
    return local.strftime("%Y-%m-%d") if local else str(value)[:10]


def zone_label(value=None) -> str:
    """Zone abbreviation for a stored time (or now): `EST` in winter, `EDT` in summer."""
    local = to_local(value) if value is not None else datetime.now().astimezone()
    return (local or datetime.now().astimezone()).strftime("%Z")


def to_store(moment: datetime) -> str:
    """An aware datetime → the UTC text the databases hold."""
    if moment.tzinfo is None:
        moment = moment.astimezone()              # naive = machine local, that date's offset
    return moment.astimezone(timezone.utc).strftime(STORE_FORMAT)


def parse_typed(text) -> datetime:
    """A time a person or agent typed → aware UTC datetime.

    No zone → local time. `Z`, an offset, or the machine's own zone
    abbreviation → that instant. Anything else raises ValueError with the
    accepted formats.
    """
    raw = str(text or "").strip()
    match = _PLAIN.match(raw)
    if not match:
        raise ValueError(f"Not a date and time: {raw!r}. Use {TYPED_FORMATS}.")
    try:
        naive = _naive(match)
    except ValueError as exc:
        raise ValueError(f"Not a real date or time: {raw!r} ({exc}).") from None
    zone = match.group(7)
    if not zone:
        return naive.astimezone(timezone.utc)     # local, with that date's offset
    upper = zone.upper()
    if upper in ("Z", "UTC", "GMT"):
        return naive.replace(tzinfo=timezone.utc)
    if zone[0] in "+-":
        return naive.replace(tzinfo=_offset(zone)).astimezone(timezone.utc)
    if upper in local_zone_names():
        return naive.astimezone(timezone.utc)
    raise ValueError(
        f"Unknown time zone {zone!r} in {raw!r}. Leave the zone off for local time "
        f"({', '.join(sorted(local_zone_names()))}), or give an offset such as -05:00."
    )


def typed_to_store(text) -> str:
    """What a person typed → UTC text for storage. Raises ValueError."""
    return to_store(parse_typed(text))


def local_to_store(local_text) -> str:
    """Local `YYYY-MM-DD HH:MM[:SS]` from a form field → UTC text. Unparseable → unchanged."""
    try:
        return typed_to_store(local_text)
    except ValueError:
        return str(local_text or "")


def month_start_store(now: datetime | None = None) -> str:
    """UTC text of the first instant of the current local month."""
    local = (now or utc_now()).astimezone()
    return to_store(datetime(local.year, local.month, 1))


def localize(obj, fields=TIME_FIELDS, seconds: bool = False):
    """A copy of rows (dict / list of dicts, nested) with every stored time shown as local."""
    if isinstance(obj, dict):
        out = {}
        for key, value in obj.items():
            if key in fields and isinstance(value, str) and value:
                out[key] = show(value, seconds=seconds)
            else:
                out[key] = localize(value, fields, seconds)
        return out
    if isinstance(obj, (list, tuple)):
        return [localize(item, fields, seconds) for item in obj]
    return obj
