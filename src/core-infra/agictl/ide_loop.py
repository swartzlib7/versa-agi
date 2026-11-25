"""The IDE-mode loop: its terms, what a tick answers, when it has gone silent.

state_coa_ide_mode.md rules 24 to 29. Pure: no database and no files, so the
rules can be tested with a clock that is handed in.

The system holds the terms and does the counting. A chat's own memory is
compacted over a long session, so a count kept by the model would be lost.
"""
from __future__ import annotations

import hashlib
import re
from datetime import datetime, timedelta, timezone

import local_time

EVERY_MIN_MINUTES = 5
EVERY_MAX_MINUTES = 120
HOURS_MAX = 24
IDLE_TICKS_MAX = 500
# A running loop is silent when its last tick is older than this many intervals (plus a minute).
SILENT_AFTER_INTERVALS = 2
SILENT_GRACE_SECONDS = 60
SENDERS_SHOWN = 8
TASKS_SHOWN = 10

# A loop that ended with at_end "off" should have turned the mode off by this long after its end.
END_GRACE_SECONDS = 45 * 60

DO_WORK = "work"    # something is waiting: handle it
DO_IDLE = "idle"    # nothing is waiting: end the turn
DO_STOP = "stop"    # the mode is off, or the loop has ended and the session stays: disarm
DO_CLOSE = "close"  # the loop has ended and the session ends with it: close out, tell the Primary User, mode off
DO_USER = "user"    # the Primary User wrote: follow the message

AT_END_OFF = "off"    # when the loop runs out: close out and turn IDE mode off (the default)
AT_END_STAY = "stay"  # when the loop runs out: only the loop ends
AT_END_CHOICES = (AT_END_OFF, AT_END_STAY)

CLOSE_SAY = (
    "Close the session now, without asking: run the close-out in AGENTS.md (awareness, memory, task "
    "journals, projects), send the Primary User one message (the loop has ended, what you did, IDE mode "
    "is off) and no closing notes to anyone else, run `agictl agent ide off coa`, and disarm the loop "
    "in this chat."
)


# The door (AGENTS.md) names its own version, so a copy a tool loaded earlier can be told
# from the one installed now (rule 30).
_DOOR_STAMP = re.compile(r"(Rules version: `)([0-9a-f]{8}|-{8})(`)")


def door_stamp(text: str) -> str:
    """The version a door says it is ("" when it has no stamp line)."""
    found = _DOOR_STAMP.search(text or "")
    return found.group(2) if found else ""


def door_version(text: str) -> str:
    """The version a door should carry: 8 hex of its SHA-256 with the stamp blanked."""
    blank = _DOOR_STAMP.sub(lambda m: m.group(1) + "-" * 8 + m.group(3), text or "", count=1)
    return hashlib.sha256(blank.encode("utf-8")).hexdigest()[:8]


def stamp_door(text: str) -> str:
    """The door with its stamp set to its own version."""
    version = door_version(text)
    return _DOOR_STAMP.sub(lambda m: m.group(1) + version + m.group(3), text, count=1)


def door_notice(version: str, path: str) -> str:
    """The sentence `ide status` adds to its message. Every door tells COA to read all of it."""
    if not version:
        return ""
    return (f" Rules version {version}: if the AGENTS.md you are following shows another version, or "
            f"none, it is an old copy. Read {path} again now and follow that file, not the copy.")


def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse(value) -> datetime | None:
    return local_time.parse_stored(value) if value else None


def terms_error(every, hours, idle_ticks) -> str:
    """Empty when the terms of a loop are valid, else a message for whoever set them."""
    if every is None or not EVERY_MIN_MINUTES <= every <= EVERY_MAX_MINUTES:
        return f"--every must be between {EVERY_MIN_MINUTES} and {EVERY_MAX_MINUTES} minutes."
    if hours is None and idle_ticks is None:
        return ("Say how the loop ends: --hours (a fixed length), --idle-ticks "
                "(that many quiet ticks in a row), or both.")
    if hours is not None and not 0 < hours <= HOURS_MAX:
        return f"--hours must be above 0 and at most {HOURS_MAX}."
    if idle_ticks is not None and not 1 <= idle_ticks <= IDLE_TICKS_MAX:
        return f"--idle-ticks must be between 1 and {IDLE_TICKS_MAX}."
    return ""


def new_loop(every: int, hours, idle_ticks, now: datetime, at_end: str = AT_END_OFF) -> dict:
    """The stored terms of a loop that starts now. The first tick is expected one interval on."""
    return {
        "at_end": at_end if at_end in AT_END_CHOICES else AT_END_OFF,
        "ended_at": "",
        "end_warned_at": "",
        "every_minutes": int(every),
        "started_at": _iso(now),
        "ends_at": _iso(now + timedelta(hours=float(hours))) if hours is not None else "",
        "max_idle": int(idle_ticks) if idle_ticks is not None else 0,
        "ticks": 0,
        "idle_in_a_row": 0,
        "last_tick_at": _iso(now),
        "warned_at": "",
    }


def view(loop: dict | None, now: datetime) -> dict:
    """The loop as a tick or a status shows it."""
    if not loop:
        return {"armed": False}
    ends = _parse(loop.get("ends_at"))
    if loop.get("ended_at"):
        # Run out, with the session to be closed: kept until `ide off` so Lifeline can tell
        # when that did not happen (rule 29).
        return {"armed": False, "ended_at": local_time.show(loop.get("ended_at")),
                "at_end": loop.get("at_end") or AT_END_OFF, "tick": loop.get("ticks", 0)}
    return {
        "armed": True,
        "at_end": loop.get("at_end") or AT_END_OFF,
        "every_minutes": loop.get("every_minutes"),
        "tick": loop.get("ticks", 0),
        "idle_in_a_row": loop.get("idle_in_a_row", 0),
        "idle_limit": loop.get("max_idle") or None,
        "ends_at": local_time.show(loop.get("ends_at")) if ends else None,
        "minutes_left": max(0, int((ends - now).total_seconds() // 60)) if ends else None,
    }


def summarize_messages(rows: list[dict]) -> dict:
    """Unread mail as a count per sender, newest sender first. No message text."""
    senders: dict[str, dict] = {}
    for row in rows or []:
        name = str(row.get("display_name") or row.get("from_user_id") or "unknown").strip() or "unknown"
        entry = senders.setdefault(name, {"name": name, "count": 0, "latest": ""})
        entry["count"] += 1
        stamp = str(row.get("created_at") or "")
        if stamp > entry["latest"]:
            entry["latest"] = stamp
    ordered = sorted(senders.values(), key=lambda entry: entry["latest"], reverse=True)
    for entry in ordered:
        entry["latest"] = local_time.show(entry["latest"])
    return {"unread": len(rows or []), "from": ordered[:SENDERS_SHOWN]}


def summarize_tasks(tasks: list[dict]) -> list[dict]:
    """Due tasks as id, title, status, priority and when they came due."""
    return [
        {"id": task.get("id"), "title": task.get("title"), "status": task.get("status"),
         "priority": task.get("priority"), "due": local_time.show(task.get("due_date"))}
        for task in (tasks or [])[:TASKS_SHOWN]
    ]


def _waiting_text(unread: int, due: int) -> str:
    parts = []
    if unread:
        parts.append(f"{unread} unread message{'s' if unread != 1 else ''}")
    if due:
        parts.append(f"{due} task{'s' if due != 1 else ''} due")
    return " and ".join(parts)


def decide(loop: dict | None, mode_on: bool, unread: int, due: int, now: datetime,
           on_message: bool = False) -> tuple[dict, dict | None]:
    """What a tick answers, and the loop as it stands afterwards (None: no loop any more).

    The answer holds ``do``, ``say`` and, on a loop's last tick, ``last``.
    """
    waiting = _waiting_text(unread, due)
    if not mode_on:
        return ({"do": DO_STOP, "say": "IDE mode is off. If a loop is armed in this chat, disarm it. "
                                        "Take no further action: do not touch tasks, memory or messages."},
                None)

    after = dict(loop) if loop else None
    if after is not None:
        after["last_tick_at"] = _iso(now)   # any call shows the chat is alive
        after["warned_at"] = ""

    if on_message:
        if after is not None:
            after["idle_in_a_row"] = 0
        note = f" Also waiting: {waiting}." if waiting else " Nothing else is waiting."
        if after is not None and after.get("ended_at"):
            note += (" The loop has run out and the session was to be closed: unless they say otherwise, "
                     "finish the close-out and turn the mode off.")
        return ({"do": DO_USER, "say": "Follow the Primary User's message." + note}, after)

    if after is not None and after.get("ended_at"):
        # The tool's loop fired again after the loop ran out: the close-out is still owed.
        return ({"do": DO_CLOSE, "say": "The loop has already ended. " + CLOSE_SAY}, after)

    def ended(reason: str) -> tuple[dict, dict | None]:
        """The loop has run out with nothing waiting: close the session, or only stop."""
        if (after.get("at_end") or AT_END_OFF) == AT_END_STAY:
            return ({"do": DO_STOP, "say": f"Nothing is waiting and {reason}. Disarm the loop in this chat "
                                            "and tell the Primary User it has ended. IDE mode stays on."}, None)
        after["ended_at"] = _iso(now)
        return ({"do": DO_CLOSE, "say": f"Nothing is waiting and {reason}. " + CLOSE_SAY}, after)

    if after is None:
        if waiting:
            return ({"do": DO_WORK, "say": f"{waiting.capitalize()}: handle them, messages first. "
                                           "No loop is armed; this was a single check."}, None)
        return ({"do": DO_IDLE, "say": "Nothing is waiting. No loop is armed; this was a single check."}, None)

    after["ticks"] = int(after.get("ticks") or 0) + 1
    ends = _parse(after.get("ends_at"))
    time_up = bool(ends and now >= ends)

    if waiting:
        after["idle_in_a_row"] = 0
        say = f"{waiting.capitalize()}: handle them, messages first, as you would in a work cycle."
        if time_up:
            if (after.get("at_end") or AT_END_OFF) == AT_END_STAY:
                return ({"do": DO_WORK, "last": True,
                         "say": say + " This was the loop's last tick: its time is up. When you are done, "
                                      "disarm the loop in this chat and tell the Primary User it has ended. "
                                      "IDE mode stays on."}, None)
            after["ended_at"] = _iso(now)
            return ({"do": DO_WORK, "last": True,
                     "say": say + " This was the loop's last tick: its time is up. When the work is done: "
                            + CLOSE_SAY}, after)
        return ({"do": DO_WORK, "say": say}, after)

    after["idle_in_a_row"] = int(after.get("idle_in_a_row") or 0) + 1
    if time_up:
        return ended("the loop's time is up")
    limit = int(after.get("max_idle") or 0)
    if limit and after["idle_in_a_row"] >= limit:
        return ended(f"this was quiet tick {after['idle_in_a_row']} in a row, the agreed limit")
    return ({"do": DO_IDLE, "say": "Nothing is waiting. End this turn now with one short line and do nothing "
                                    f"else. The next tick is in {after.get('every_minutes')} minutes."}, after)


def end_overdue_minutes(loop: dict | None, now: datetime) -> int:
    """Minutes past the end of a loop that was to close the session and has not; 0 otherwise.

    The end is the last tick of a loop that ran out, or the agreed end time when
    no last tick came. Counted only when nothing was heard from the chat for the
    grace time, so a close-out that is still being written is left alone.
    """
    if not loop or (loop.get("at_end") or AT_END_OFF) != AT_END_OFF:
        return 0
    end = _parse(loop.get("ended_at")) or _parse(loop.get("ends_at"))
    last = _parse(loop.get("last_tick_at"))
    if not end or now < end:
        return 0
    heard = max(end, last) if last else end
    if (now - heard).total_seconds() <= END_GRACE_SECONDS:
        return 0
    return int((now - end).total_seconds() // 60)


def end_overdue_text(loop: dict, minutes: int, unread: int, due: int) -> str:
    """What the Primary User is told, once, when an ended loop left the mode on."""
    waiting = _waiting_text(unread, due)
    held = f"Waiting for her now: {waiting}." if waiting else "Nothing is waiting for her right now."
    when = local_time.show(loop.get("ended_at") or loop.get("ends_at"))
    return (
        f"[Lifeline] COA's IDE loop ended at {when} and was to turn IDE mode off, but the mode is still on "
        f"{minutes} minutes later. The chat did not finish closing the session. While the mode is on I do "
        f"not start COA, so her messages and due tasks wait. {held} Turn IDE mode off (agitop → COA → IDE "
        "Integration) and I take over. What she did in the session may not be written to her memory."
    )


def silent_minutes(loop: dict | None, now: datetime) -> int:
    """Minutes since the last tick of a loop that has gone silent; 0 when it has not."""
    if not loop or loop.get("ended_at"):
        return 0
    last = _parse(loop.get("last_tick_at"))
    every = int(loop.get("every_minutes") or 0)
    if not last or every <= 0:
        return 0
    gap = (now - last).total_seconds()
    if gap <= SILENT_AFTER_INTERVALS * every * 60 + SILENT_GRACE_SECONDS:
        return 0
    return int(gap // 60)


def silence_text(loop: dict, minutes: int, unread: int, due: int) -> str:
    """What the Primary User is told, once, when a running loop has gone silent."""
    waiting = _waiting_text(unread, due)
    held = f"Waiting for her now: {waiting}." if waiting else "Nothing is waiting for her right now."
    return (
        f"[Lifeline] COA's IDE loop has been silent for {minutes} minutes (a check was due every "
        f"{loop.get('every_minutes')}). The IDE chat was closed, its loop ended, or the computer slept. "
        f"While IDE mode is on I do not start COA, so her messages and due tasks wait. {held} "
        "Open the IDE chat again, or turn IDE mode off (agitop → COA → IDE Integration) and I take over. "
        "Sub-agents are not affected."
    )
