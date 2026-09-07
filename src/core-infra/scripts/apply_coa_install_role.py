#!/usr/bin/env python3
"""Apply Sentinel overlay to a deployed COA poise file.

Operational rules stay in config/coa_poise.md. This script copies that body
and, when install_role=sentinel, inserts the SENTINEL MODE banner. First-contact
copy is not swapped here — Lifeline fills {FIRST_CONTACT} at spawn.

Re-run on every install and --update so the overlay cannot drift from the
current poise. Fails non-zero if the CONTEXT MAP heading is missing.
"""
from __future__ import annotations

import sys
from pathlib import Path

BANNER = """## SENTINEL MODE

You are the **Remote Sentinel** on this host. Your name is the configured COA first_name (set by the Primary User in setup.ini). You are a remote agent on the Primary User's team and you carry operational duties they define. The full Versa AGi system remains at your disposal — same tools, skills, and COA powers. You do not run the home-install welcome routine; you run the Remote Sentinel routine.

"""

CONTEXT_HEADING = "## CONTEXT MAP — how to read this prompt"


def apply(poise_text: str, role: str) -> str:
    role = (role or "normal").strip().lower()
    if role != "sentinel":
        return poise_text
    if CONTEXT_HEADING not in poise_text:
        raise SystemExit(
            "apply_coa_install_role: CONTEXT MAP heading not found in poise"
        )
    if "{FIRST_CONTACT}" not in poise_text:
        raise SystemExit(
            "apply_coa_install_role: {FIRST_CONTACT} placeholder not found in poise"
        )
    return poise_text.replace(CONTEXT_HEADING, BANNER + CONTEXT_HEADING, 1)


def main() -> int:
    if len(sys.argv) != 3:
        print(
            "usage: apply_coa_install_role.py <poise-dest.md> <normal|sentinel>",
            file=sys.stderr,
        )
        return 2
    dest = Path(sys.argv[1])
    role = sys.argv[2]
    if not dest.is_file():
        print(f"apply_coa_install_role: missing {dest}", file=sys.stderr)
        return 1
    dest.write_text(apply(dest.read_text(encoding="utf-8"), role), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
