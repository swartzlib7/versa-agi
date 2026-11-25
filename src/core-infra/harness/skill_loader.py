"""Read one skill file whole (state_shipped_skills_review.md, SKR-03).

An agent that is told to load a skill must receive all of it. Read through a shell
command, a skill is cut at the agent's tool output limit (5,000 characters by default),
and most skills are longer than that. This reads the file in the harness process, which
already runs as the agent, and returns every character.

Only files inside the agent's own skills folder are read, and the shipped reference
documents beside it (`.agent/docs/`, asked for as `docs/<file>`).
"""

from __future__ import annotations

import os

SKILL_SUFFIX = ".md"
# A skill read is text for the model. A file far past any shipped skill (the largest is
# about 60,000 characters) is not a skill; refuse it and say so instead of cutting it.
MAX_SKILL_CHARS = 200_000


def skills_folder(explicit: str | None = None, environ=None) -> str:
    """The agent's skills folder: the one the harness was started with, else from its env."""
    environ = os.environ if environ is None else environ
    if explicit:
        return explicit
    agent_dir = environ.get("AGICTL_AGENT_DIR") or ""
    return os.path.join(agent_dir, "skills") if agent_dir else ""


def _names(folder: str) -> list[str]:
    try:
        return sorted(n for n in os.listdir(folder) if n.endswith(SKILL_SUFFIX))
    except OSError:
        return []


def resolve(name: str, folder: str) -> tuple[str, str]:
    """(path, error). A bare name, with or without `.md`, or `skill_folder/file` for an asset."""
    wanted = (name or "").strip().strip("`'\"")
    if wanted.startswith(".agent/"):
        wanted = wanted[len(".agent/"):]
    if wanted.startswith("docs/") and folder:
        # The shipped reference documents live beside the skills folder.
        folder = os.path.join(os.path.dirname(os.path.normpath(folder)), "docs")
        wanted = wanted[len("docs/"):]
    elif wanted.startswith("skills/"):
        wanted = wanted[len("skills/"):]
    if not wanted:
        return "", "Give the skill's name, for example: task_scheduling"
    if not folder or not os.path.isdir(folder):
        return "", "That folder does not exist for this agent."
    if wanted.startswith(("/", "~")) or ".." in wanted.split("/") or "\\" in wanted:
        return "", "Give a skill name from your skills list, not a path outside your skills folder."
    candidates = [wanted] if os.path.splitext(wanted)[1] else [wanted + SKILL_SUFFIX, wanted]
    root = os.path.realpath(folder)
    for candidate in candidates:
        path = os.path.realpath(os.path.join(root, candidate))
        if path != root and not path.startswith(root + os.sep):
            return "", "Give a skill name from your skills list, not a path outside your skills folder."
        if os.path.isfile(path):
            return path, ""
    stem = os.path.splitext(os.path.basename(wanted))[0].lower()
    near = [n for n in _names(folder) if stem and (stem in n.lower() or n.lower()[:-3] in stem)]
    hint = f" Did you mean: {', '.join(near[:6])}?" if near else ""
    return "", f"No skill named '{wanted}' in your skills folder.{hint}"


def read_skill(name: str, folder: str) -> str:
    """The whole file, or a line starting `ERROR:`."""
    path, error = resolve(name, folder)
    if error:
        return f"ERROR: {error}"
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            text = handle.read(MAX_SKILL_CHARS + 1)
    except OSError as exc:
        return f"ERROR: could not read {os.path.basename(path)}: {exc.strerror or exc}"
    if len(text) > MAX_SKILL_CHARS:
        return (f"ERROR: {os.path.basename(path)} is longer than {MAX_SKILL_CHARS:,} characters, "
                "which no skill is. Read the part you need with agictl_execute.")
    if not text.strip():
        return f"ERROR: {os.path.basename(path)} is empty."
    return text
