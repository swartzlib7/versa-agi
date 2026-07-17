#!/usr/bin/env python3
"""
weave_poise.py

Weave a sub-agent poise from the universal skeleton + a role fragment.

The skeleton (config/roles/agent_poise.md) carries the shared system context
with {ROLE_IDENTITY} and {CORE_DUTIES} placeholders. Each role fragment
(config/roles/<role_id>/poise.md) supplies only the role-specific sections,
delimited by HTML-comment markers:

    <!-- ROLE_IDENTITY -->
    You are a **Developer Agent** in Versa AGi — ...

    <!-- CORE_DUTIES -->
    1. **Implementation** — ...

Used by sync_poise.py (roles registry deployment, invoked by setup.sh in
both install flows). Also runnable standalone:

    weave_poise.py <base> <fragment> [-o OUTPUT]     # weave to file or stdout
"""

import re
import sys

MARKER_RE = re.compile(r"^<!--\s*([A-Z_]+)\s*-->\s*$", re.MULTILINE)
REQUIRED_SECTIONS = ("ROLE_IDENTITY", "CORE_DUTIES")


def parse_fragment(fragment_text: str) -> dict:
    """Split a fragment into {SECTION_NAME: content} by marker lines."""
    sections = {}
    matches = list(MARKER_RE.finditer(fragment_text))
    if not matches:
        raise ValueError("fragment has no <!-- SECTION --> markers")
    for i, m in enumerate(matches):
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(fragment_text)
        sections[m.group(1)] = fragment_text[start:end].strip("\n")
    return sections


def weave(base_text: str, fragment_text: str) -> str:
    """Return the full poise: skeleton with fragment sections substituted."""
    sections = parse_fragment(fragment_text)
    missing = [s for s in REQUIRED_SECTIONS if s not in sections]
    if missing:
        raise ValueError(f"fragment missing section(s): {', '.join(missing)}")

    result = base_text
    for name, content in sections.items():
        placeholder = "{" + name + "}"
        if placeholder not in result:
            raise ValueError(f"skeleton has no {placeholder} placeholder")
        result = result.replace(placeholder, content)

    leftover = re.findall(r"\{[A-Z_]+\}", result)
    if leftover:
        raise ValueError(f"unresolved placeholder(s) after weave: {', '.join(sorted(set(leftover)))}")
    return result


def is_fragment(text: str) -> bool:
    """True when the file is a role fragment (vs an already-full poise)."""
    return bool(MARKER_RE.search(text))


def main() -> int:
    args = [a for a in sys.argv[1:] if a != "-o"]
    out_path = None
    if "-o" in sys.argv[1:]:
        out_idx = sys.argv.index("-o")
        out_path = sys.argv[out_idx + 1]
        args = sys.argv[1:out_idx] + sys.argv[out_idx + 2:]
    if len(args) != 2:
        print(__doc__.strip(), file=sys.stderr)
        return 2

    base_path, fragment_path = args
    with open(base_path, "r", encoding="utf-8") as f:
        base_text = f.read()
    with open(fragment_path, "r", encoding="utf-8") as f:
        fragment_text = f.read()

    try:
        woven = weave(base_text, fragment_text)
    except ValueError as e:
        print(f"weave_poise: {fragment_path}: {e}", file=sys.stderr)
        return 1

    if out_path:
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(woven)
    else:
        sys.stdout.write(woven)
    return 0


if __name__ == "__main__":
    sys.exit(main())
