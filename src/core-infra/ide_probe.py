"""Which IDE is this chat in, and which tool is running it (state_coa_ide_mode.md §1 rules 21–22).

Run by COA from the IDE door: `python3 /usr/local/lib/versa-agi/ide_probe.py`.

Two facts, in this order:
1. The IDE: Cursor, Antigravity, VS Code. Forks are checked before VS Code,
   because every fork also sets the `VSCODE_*` variables.
2. The tool running the chat: the IDE's own agent, or an agent extension such
   as Claude Code. Whether a same-chat loop may be offered follows the tool.

The IDE is read from every `CURSOR_*` / `ANTIGRAVITY_*` / `VSCODE_*` variable
and from the parent processes. The variables of an IDE's own agent
(`CURSOR_AGENT`, `CURSOR_TRACE_ID`) are not set when an extension runs the
chat, so they name the tool, never the IDE.

Standard library only. Prints variable names as evidence, never their values.
"""

from __future__ import annotations

import os
import re

IDE_CURSOR = "Cursor"
IDE_ANTIGRAVITY = "Antigravity"
IDE_VSCODE = "VS Code"
TOOL_CLAUDE_CODE = "Claude Code"
TOOL_CURSOR_AGENT = "Cursor agent"
TOOL_ANTIGRAVITY_AGENT = "Antigravity agent"
TOOL_COPILOT = "GitHub Copilot"
UNKNOWN = "unknown"

# Tool → its own command that wakes this same chat on a timer. A tool that is
# not here has none that we have checked (rule 22).
SAME_CHAT_WAKE = {
    TOOL_CURSOR_AGENT: "/loop",
    TOOL_CLAUDE_CODE: "/loop",
}

# First match wins. Claude Code comes first: it is the tool when it runs
# inside Cursor or Antigravity, whatever else is set.
_TOOL_MARKERS = (
    (TOOL_CLAUDE_CODE, ("CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT")),
    (TOOL_CURSOR_AGENT, ("CURSOR_AGENT", "CURSOR_TRACE_ID", "CURSOR_CONVERSATION_ID")),
    (TOOL_ANTIGRAVITY_AGENT, ("ANTIGRAVITY_AGENT",)),
    (TOOL_COPILOT, ("COPILOT_MODEL", "COPILOT_GITHUB_TOKEN")),
)

# Variables whose values may hold a path that names the IDE.
_PATH_PREFIXES = ("CURSOR", "ANTIGRAVITY", "VSCODE", "CLAUDE_CODE_EXECPATH", "GIT_ASKPASS", "TERM_PROGRAM")
_CURSOR_WORD = re.compile(r"(?<![a-z])cursor(?![a-z])")
_ANTIGRAVITY_WORD = re.compile(r"(?<![a-z])antigravity(?![a-z])")
_VSCODE_WORD = re.compile(r"vscode|(?<![a-z])code-server(?![a-z])")


def process_chain(limit: int = 14) -> list[str]:
    """Command lines of this process and its parents, nearest first. Empty where /proc is not readable."""
    chain: list[str] = []
    pid = os.getpid()
    while pid > 1 and len(chain) < limit:
        try:
            with open(f"/proc/{pid}/cmdline", "rb") as handle:
                chain.append(handle.read().replace(b"\0", b" ").decode("utf-8", "replace").strip())
            with open(f"/proc/{pid}/stat", encoding="utf-8", errors="replace") as handle:
                pid = int(handle.read().rsplit(")", 1)[1].split()[1])
        except (OSError, ValueError, IndexError):
            break
    return chain


def _first_key(env, prefix: str) -> str:
    return next((key for key in sorted(env) if key.startswith(prefix)), "")


def identify_ide(env, chain) -> tuple[str, str]:
    """(IDE, evidence). Cursor and Antigravity before VS Code."""
    paths = " ".join(str(value) for key, value in env.items() if key.startswith(_PATH_PREFIXES)).lower()
    processes = " ".join(chain).lower()
    for ide, prefix, word in ((IDE_CURSOR, "CURSOR_", _CURSOR_WORD),
                              (IDE_ANTIGRAVITY, "ANTIGRAVITY_", _ANTIGRAVITY_WORD)):
        key = _first_key(env, prefix)
        if key:
            return ide, f"variable {key}"
        if word.search(paths):
            return ide, "a path in the environment"
        if word.search(processes):
            return ide, "a parent process"
    key = _first_key(env, "VSCODE_")
    if key:
        return IDE_VSCODE, f"variable {key}, and no Cursor or Antigravity marker"
    if _VSCODE_WORD.search(paths) or _VSCODE_WORD.search(processes):
        return IDE_VSCODE, "a path or parent process, and no Cursor or Antigravity marker"
    return UNKNOWN, "no marker found"


def identify_tool(env) -> tuple[str, str]:
    """(tool running this chat, evidence)."""
    for tool, keys in _TOOL_MARKERS:
        key = next((k for k in keys if env.get(k)), "")
        if key:
            return tool, f"variable {key}"
    return UNKNOWN, "no marker found"


def identify(env=None, chain=None) -> dict:
    env = os.environ if env is None else env
    chain = process_chain() if chain is None else chain
    ide, ide_evidence = identify_ide(env, chain)
    tool, tool_evidence = identify_tool(env)
    return {
        "ide": ide,
        "tool": tool,
        "same_chat_wake": SAME_CHAT_WAKE.get(tool, ""),
        "ide_evidence": ide_evidence,
        "tool_evidence": tool_evidence,
    }


def report(found: dict) -> str:
    """What COA reads. The IDE first, then the tool, then what the tool allows."""
    if found["same_chat_wake"]:
        wake = f"{found['same_chat_wake']} (a loop may be offered)"
    elif found["tool"] == UNKNOWN:
        wake = ("not known. Offer a loop only if the tool you are running in has its own "
                "command that wakes this same chat on a timer")
    else:
        wake = "none (one cycle per message)"
    return "\n".join((
        f"IDE: {found['ide']}  [{found['ide_evidence']}]",
        f"Tool: {found['tool']}  [{found['tool_evidence']}]",
        f"Same-chat wake: {wake}",
    ))


if __name__ == "__main__":
    print(report(identify()))
