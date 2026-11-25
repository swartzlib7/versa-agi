"""Every `agictl` command the shipped text shows an agent runs as written (SKR-01).

Skills, the prompt files, the update notes and the tool descriptions are read by a
model that will type what it is shown. A command that does not exist, an option the
command does not have, one argument too many, or a `task add` without the two options
it requires is refused at run time, in the middle of a cycle.

This reads every shown command and checks it against the real command tree. It does
not run any command.
"""

from __future__ import annotations

import importlib.util as _importlib_util
import unittest as _unittest

if _importlib_util.find_spec("click") is None:
    raise _unittest.SkipTest("click is not installed in this interpreter; "
                             "the agictl interpreter runs this module")

import glob
import os
import re
import sys
import unittest

import click

CORE_INFRA = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, CORE_INFRA)
sys.path.insert(0, os.path.join(CORE_INFRA, "agictl"))

import agictl.cli as agictl_cli  # noqa: E402

ROOT = agictl_cli.cli

# Text that mentions a command in a sentence and is not an example to run.
PROSE = {
    ("skills/communication.md", "task add to create 3 tasks."): "an example of a message not to send",
    ("config/update_notes/3.8.1.md", "organization … get / list"): "a sentence naming two actions",
    ("harness/agent_harness.py", "model default"): "a log line (\"model default\" reasoning level)",
}

# Files whose commands are known to be refused and wait for a decision. The test fails
# when one of them no longer has a fault, so the entry is removed with the fix.
AWAITING_DECISION: dict[str, str] = {}


def shipped_text_files() -> list[str]:
    files = sorted(glob.glob(os.path.join(CORE_INFRA, "skills", "*.md")))
    files += sorted(glob.glob(os.path.join(CORE_INFRA, "config", "*.md")))
    files += sorted(glob.glob(os.path.join(CORE_INFRA, "config", "roles", "**", "*.md"), recursive=True))
    files += sorted(glob.glob(os.path.join(CORE_INFRA, "config", "update_notes", "*.md")))
    files += [os.path.join(CORE_INFRA, "harness", "agent_harness.py"),
              os.path.join(CORE_INFRA, "harness", "triage.py")]
    return files


COMMAND = re.compile(r"agictl\s+([^\n`|;&)]*)")
QUOTED = re.compile(r"'([a-z][a-z-]* [^'\n]*)'")


def tokens_of(raw):
    """Words of a shown command. Quoted text and <placeholders> become X; [optional parts] go."""
    if raw.count('"') % 2:
        raw += '"'                                  # the match stopped inside a quoted value
    clean = re.sub(r'"[^"\n]*"', "X", raw)
    clean = re.sub(r"(?<![A-Za-z0-9])'[^'\n]*'", "X", clean)
    clean = re.sub(r"<[^>\n]*>|\{[^}\n]*\}", "X", clean)
    clean = re.sub(r"\[[^\]\n]*\]?", " ", clean)
    clean = clean.split(" #")[0]
    toks = [t.lstrip("(") for t in clean.split()]
    return [t for t in toks if t and t not in ("...", "…", "\\")]


def resolve(root, toks):
    cmd, path, i = root, [], 0
    while isinstance(cmd, click.Group) and i < len(toks):
        t = toks[i]
        if t.startswith("-"):
            i += 1
            continue
        parts = t.split("/")
        if len(parts) > 1 and all(p in cmd.commands for p in parts):
            t = parts[0]                            # "connection/project/system set"
        sub = cmd.commands.get(t)
        if sub is None:
            return cmd, path, toks[i:], t
        cmd, path, i = sub, path + [t], i + 1
    return cmd, path, toks[i:], None


def check(raw, root=None):
    """Problems with one shown command (the text after `agictl`)."""
    root = root or ROOT
    toks = tokens_of(raw)
    if not toks or toks[0] not in root.commands:
        return []                                   # prose
    cmd, path, rest, missing = resolve(root, toks)
    name = "agictl " + " ".join(path)
    if missing is not None:
        if missing == "X":
            return []                               # a placeholder stands for the subcommand
        return [f"no command '{missing}' under '{name.strip()}'"]
    if isinstance(cmd, click.Group):
        return []
    out = []
    opts = {}
    for p in cmd.params:
        for o in list(getattr(p, "opts", [])) + list(getattr(p, "secondary_opts", [])):
            opts[o] = p
    positional, i = 0, 0
    while i < len(rest):
        t = rest[i]
        key = t.split("=")[0]
        if key.startswith("-") and not re.fullmatch(r"-?\d+(\.\d+)?", key):
            takes_value = False
            for one in key.split("/"):              # "--freedoms/--barriers"
                p = opts.get(one)
                if p is None and one != "--help":
                    if one.startswith("--"):
                        out.append(f"'{one}' is not an option of '{name}'")
                elif p is not None and not getattr(p, "is_flag", False):
                    takes_value = True
            if takes_value and "=" not in t:
                i += 1                              # its value
        else:
            positional += 1
        i += 1
    args = [p for p in cmd.params if isinstance(p, click.Argument)]
    if not any(a.nargs == -1 for a in args):
        allowed = sum(a.nargs for a in args)
        if positional > allowed:
            out.append(f"'{name}' takes {allowed} argument(s); {positional} shown")
    open_ended = bool(re.search(r"(^|\s)(…|\.\.\.|\[options\]|\[OPTIONS\])(\s|$)", re.sub(r'"[^"\n]*"', "X", raw)))
    if (path == ["task", "add"] and not open_ended
            and "--script-task" not in rest and "--utility-task" not in rest):
        for need in ("--due-date", "--project"):
            if need not in [t.split("=")[0] for t in rest]:
                out.append(f"'{name}' is refused without {need}")
    return out


def problems_in(path, text):
    text = re.sub(r"\\\n\s*", " ", text)
    found, seen = [], set()
    pats = [COMMAND]
    if path.endswith(".py"):
        pats = [QUOTED]
    for pat in pats:
        for m in pat.finditer(text):
            raw = m.group(1).strip()
            if not raw or raw in seen:
                continue
            seen.add(raw)
            line = text.count("\n", 0, m.start()) + 1
            for problem in check(raw):
                found.append((line, raw[:100], problem))
    return found


def all_problems() -> dict[str, list[tuple[int, str, str]]]:
    out: dict[str, list[tuple[int, str, str]]] = {}
    for path in shipped_text_files():
        rel = os.path.relpath(path, CORE_INFRA)
        with open(path, encoding="utf-8") as handle:
            found = problems_in(path, handle.read())
        found = [f for f in found
                 if not any(rel == p_rel and f[1].startswith(p_raw) for (p_rel, p_raw) in PROSE)]
        if found:
            out[rel] = found
    return out


class TestTheChecker(unittest.TestCase):
    """The checker catches what it is for, and leaves syntax lines alone."""

    def test_a_command_that_does_not_exist(self):
        self.assertTrue(check('task create "Welcome"'))
        self.assertTrue(check("task list --mine"))
        self.assertTrue(check("agent add researcher --os-user researcher"))

    def test_one_argument_too_many(self):
        self.assertTrue(check('task done 5 "finished"'))
        self.assertTrue(check("agent summary web-dev"))
        self.assertEqual(check("task done 5"), [])
        self.assertEqual(check('task progress 5 "DONE: x"'), [])

    def test_task_add_needs_a_project_and_a_due_date(self):
        self.assertEqual(len(check('task add "Title"')), 2)
        self.assertEqual(len(check('task add "Title" --due-date now')), 1)
        self.assertEqual(check('task add "Title" --project 3 --due-date now --priority high'), [])
        self.assertEqual(check('task add "Nightly" --script-task --script-path x.sh --due-date now'), [])

    def test_syntax_lines_are_not_examples(self):
        self.assertEqual(check('task add "Title" [options]'), [])
        self.assertEqual(check("agent count [--active]"), [])
        self.assertEqual(check("project assign <id> (--agent NAME"), [])
        self.assertEqual(check("memory connection/project/system set ..."), [])
        self.assertEqual(check('game update <id> --freedoms/--barriers "..."'), [])
        self.assertEqual(check("model params set model:<key> --web-search off"), [])

    def test_prose_and_placeholders_are_left_alone(self):
        self.assertEqual(check("is the command-line tool"), [])
        self.assertEqual(check("<group> <command>"), [])
        self.assertEqual(check("task"), [])


class TestShippedCommands(unittest.TestCase):
    def test_files_are_found(self):
        files = shipped_text_files()
        self.assertGreater(len([f for f in files if os.sep + "skills" + os.sep in f]), 30)
        for path in files:
            self.assertTrue(os.path.isfile(path), path)

    def test_every_shown_command_runs_as_written(self):
        problems = {rel: found for rel, found in all_problems().items()
                    if rel not in AWAITING_DECISION}
        lines = [f"{rel}:{line}: agictl {raw}\n      {why}"
                 for rel, found in sorted(problems.items()) for line, raw, why in found]
        self.assertEqual(lines, [], "\n" + "\n".join(lines))

    def test_the_waiting_list_holds_only_files_that_still_have_a_fault(self):
        problems = all_problems()
        for rel, why in AWAITING_DECISION.items():
            self.assertIn(rel, problems, f"{rel} has no fault left: remove it from AWAITING_DECISION ({why})")

    def test_the_prose_list_holds_only_lines_that_are_still_there(self):
        for (rel, raw), _why in PROSE.items():
            with open(os.path.join(CORE_INFRA, rel), encoding="utf-8") as handle:
                self.assertIn(raw, handle.read(), f"{rel}: '{raw}' is gone; remove it from PROSE")


if __name__ == "__main__":
    unittest.main()
