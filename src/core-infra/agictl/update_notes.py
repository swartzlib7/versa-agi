"""Update notes for COA (state_update_notes.md). Pure: files and text, no database.

Purpose: after an update, COA is given (1) the maintenance the update needs
on this instance: each item is a check and what to do with its result, never
a problem without a solution; and (2) what we marked as worth telling the
Primary User. Anything COA already gets from its prompt, tools and skills is
not repeated in a note.

A note belongs to one release and is named for it: `3.9.0.md`. After an
update, each release note this instance has not had is put in COA's inbox
once. Delivery is tracked by the release version.
"""

from __future__ import annotations

import os
import re

NOTES_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         "config", "update_notes")
DRAFT_NAME = "next"                   # next.md: a draft for the release being built; never delivered
NOTE_MAX_CHARS = 4000
CONFIG_KEY = "update_notes_delivered"  # cycles.db config: {"<version>": "<delivered at, UTC>"}
MESSAGE_ID_PREFIX = "updnote_"
_SEMVER = re.compile(r"^\d+\.\d+\.\d+$")

# The two parts of a note. Each is optional; a note has at least one.
SECTION_MAINTENANCE = "## Maintenance after this update"
SECTION_TELL_PU = "## Tell the Primary User"
SECTIONS = (SECTION_MAINTENANCE, SECTION_TELL_PU)
_ITEM = re.compile(r"^\d+\. ")


def is_version(name: str) -> bool:
    return bool(_SEMVER.match(name or ""))


def version_key(version: str) -> tuple:
    return tuple(int(part) for part in version.split("."))


def message_id(version: str) -> str:
    """The inbox row of a release's note. One per version, so a second insert can be detected."""
    return f"{MESSAGE_ID_PREFIX}{version}"


def note_problem(text: str) -> str:
    """Empty when a note is well formed, else what is wrong with it."""
    text = (text or "").strip()
    if not text.startswith("# "):
        return "The first line must be a title: `# <title>`."
    if len(text) > NOTE_MAX_CHARS:
        return f"The note is {len(text)} characters; the limit is {NOTE_MAX_CHARS}."
    lines = [line.strip() for line in text.split("\n")]
    if not any(section in lines for section in SECTIONS):
        return "A note needs at least one of these headings: " + "; ".join(SECTIONS) + "."
    for number, item in enumerate(maintenance_items(text), start=1):
        # No problem without a solution: every item says what to look at and what to do.
        if "Check:" not in item or "If " not in item:
            return (f"Maintenance item {number} needs a `Check:` line and at least one "
                    "`If …:` line saying what to do. A problem COA can do nothing about "
                    "does not belong in a note.")
    return ""


def maintenance_items(text: str) -> list[str]:
    """The numbered items under the maintenance heading, each as one block of text."""
    items: list[str] = []
    inside = False
    for line in (text or "").split("\n"):
        if line.strip() == SECTION_MAINTENANCE:
            inside = True
            continue
        if inside and line.startswith("## "):
            break
        if not inside:
            continue
        if _ITEM.match(line):
            items.append(line)
        elif items:
            items[-1] += "\n" + line
    return items


def list_notes(notes_dir: str = NOTES_DIR) -> list[dict]:
    """Notes in the directory, oldest release first, the draft last.

    Each: {name, version ("" for the draft), draft, title, text, problem}.
    Files that are neither `<version>.md` nor `next.md` are ignored.
    """
    notes = []
    if not os.path.isdir(notes_dir):
        return notes
    for filename in os.listdir(notes_dir):
        if not filename.endswith(".md"):
            continue
        name = filename[:-3]
        if not (is_version(name) or name == DRAFT_NAME):
            continue
        try:
            with open(os.path.join(notes_dir, filename), encoding="utf-8") as handle:
                text = handle.read().strip()
        except OSError:
            continue
        if not text:
            continue
        first = text.split("\n", 1)[0].strip()
        notes.append({
            "name": name,
            "version": name if is_version(name) else "",
            "draft": name == DRAFT_NAME,
            "title": first[2:].strip() if first.startswith("# ") else "",
            "text": text,
            "problem": note_problem(text),
        })
    notes.sort(key=lambda n: (1, ()) if n["draft"] else (0, version_key(n["version"])))
    return notes


def due_notes(notes: list[dict], delivered_versions, installed_version: str = "") -> list[dict]:
    """Release notes to deliver now, oldest first.

    A note is due when it is named for a release, that release is not newer
    than the installed version, it is well formed, and its version is not in
    ``delivered_versions``. The draft is never due.
    """
    done = set(delivered_versions or ())
    ceiling = version_key(installed_version) if is_version(installed_version) else None
    due = []
    for note in notes:
        if note["draft"] or note["problem"] or note["version"] in done:
            continue
        if ceiling is not None and version_key(note["version"]) > ceiling:
            continue
        due.append(note)
    return due


def closing_text(version: str) -> str:
    """How COA finishes a note. The same for every note: one job with an end."""
    return (
        "HOW TO FINISH THIS NOTE\n"
        "This is one job with an end. It is not a standing duty.\n"
        "1. Read the note against how this instance actually works.\n"
        "   - Maintenance after this update: do each check once and do what its result calls for. "
        "Where a check finds nothing, there is nothing to do.\n"
        "   - Tell the Primary User: send them those points once, in one short message, with the "
        "result of your checks.\n"
        "2. If it does not fit in this cycle, create ONE task for the rest, with a due date, and "
        "journal progress on it. Do not create a recurring task.\n"
        "3. You may give a sub-agent a specific task to close out a finding, or to confirm that "
        "nothing is needed. Say in the task what done looks like. Closing the job stays with you.\n"
        "4. The job is closed when every check in the note has an answer (changed, or nothing "
        "needed), anything you delegated is done or cancelled, and the Primary User has your short "
        "result.\n"
        f"5. Then record it: `agictl memory system set update_note_{version} \"closed <date>: "
        "<one-line result>\"`. Close the task if you made one, and mark this message processed.\n"
        "6. Do not repeat the checks in later cycles. If something cannot be settled, say so in the "
        "result and close anyway. A question you had to ask someone stays on its own task, not on "
        "this job.\n"
        "This message comes from the system, not from a person: do not reply to it."
    )


def message_text(note: dict) -> str:
    """The inbox message for one release note."""
    return (
        f"VERSA AGi UPDATE NOTE — version {note['version']}\n"
        "This system was just updated. This note is sent once, to you as COA.\n\n"
        f"{note['text']}\n\n"
        f"{closing_text(note['version'])}"
    )
