"""Classify harness result logs for Provider denials and format PU alerts.

Used by Lifeline after a cycle. Stdlib only — safe to run with system python3.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

KIND_NONE = "none"
KIND_FORBIDDEN = "forbidden"
KIND_QUOTA = "quota"
KIND_RATE_LIMIT = "rate_limit"
KIND_OVERLOAD = "overload"

_HTTP_403 = re.compile(
    r"(Error code:\s*403|HTTP/?1\.1?\s*403|HTTP\s+403|\"status_code\":\s*403)",
    re.IGNORECASE,
)
_QUOTA = re.compile(
    r"(TerminalQuotaError|exhausted.{0,40}daily.{0,20}quota|"
    r"daily.{0,20}quota.{0,20}exhausted|free_tier_requests)",
    re.IGNORECASE,
)
_RATE = re.compile(
    r"(Error code:\s*429|HTTP/?1\.1?\s*429|RESOURCE_EXHAUSTED|"
    r"rate[\s._-]?limit|Too Many Requests)",
    re.IGNORECASE,
)
_OVERLOAD = re.compile(
    r"(Error code:\s*503|HTTP/?1\.1?\s*503|UNAVAILABLE|"
    r"high demand|model.{0,20}overloaded)",
    re.IGNORECASE,
)
_CREDITS = re.compile(
    r"(used all available credits|spending limit|insufficient[_ ]quota|"
    r"credit balance|permission-denied)",
    re.IGNORECASE,
)
_FATAL = re.compile(r"FATAL EXCEPTION:\s*(.+)")
_ERR403 = re.compile(r"Error code:\s*403\s*-\s*(.+)")
_ERROR_FIELD = re.compile(r"['\"]error['\"]\s*[:=]\s*['\"]([^'\"]+)['\"]")
_EXEC_MODEL = re.compile(r"EXECUTION MODEL:\s*(\S+)")
_ROUTE = re.compile(
    r"LLM ROUTE \(execution/[^)]+\):\s*catalog_provider=(\S+)",
    re.IGNORECASE,
)


def classify_text(text: str) -> dict[str, Any]:
    """Return kind / detail / model / provider for a harness result log."""
    out: dict[str, Any] = {
        "kind": KIND_NONE,
        "detail": "",
        "model": "",
        "provider": "",
        "fatal": bool(_FATAL.search(text)),
    }
    m_model = _EXEC_MODEL.search(text)
    if m_model:
        out["model"] = m_model.group(1).split("(", 1)[0].strip()
    m_prov = _ROUTE.search(text)
    if m_prov:
        out["provider"] = m_prov.group(1).strip()

    if _HTTP_403.search(text) or _CREDITS.search(text):
        out["kind"] = KIND_FORBIDDEN
        out["detail"] = _detail_403(text)
        return out
    if _QUOTA.search(text):
        out["kind"] = KIND_QUOTA
        out["detail"] = _first_match_line(text, _QUOTA)
        return out
    if _RATE.search(text):
        out["kind"] = KIND_RATE_LIMIT
        out["detail"] = _first_match_line(text, _RATE)
        return out
    if _OVERLOAD.search(text):
        out["kind"] = KIND_OVERLOAD
        out["detail"] = _first_match_line(text, _OVERLOAD)
        return out
    return out


def classify_file(path: str | Path) -> dict[str, Any]:
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {
            "kind": KIND_NONE,
            "detail": "",
            "model": "",
            "provider": "",
            "fatal": False,
        }
    return classify_text(text)


def format_pu_message(agent: str, info: dict[str, Any]) -> str:
    kind = info.get("kind") or KIND_NONE
    if kind == KIND_FORBIDDEN:
        title = "⚠️ Provider request denied (403)"
        hint = (
            "Lifeline will back off for 1 hour. After you add credits or raise "
            "the spending limit, the next cycle will retry."
        )
    elif kind == KIND_QUOTA:
        title = "⚠️ Provider daily quota exhausted"
        hint = "Lifeline will back off for 1 hour and retry after that."
    else:
        return ""
    lines = [title, "", f"Agent: {agent}"]
    if info.get("model"):
        lines.append(f"Model: {info['model']}")
    if info.get("provider"):
        lines.append(f"Provider: {info['provider']}")
    detail = (info.get("detail") or "").strip()
    if detail:
        lines.extend(["", detail])
    lines.extend(["", hint])
    return "\n".join(lines)


def _detail_403(text: str) -> str:
    raw = ""
    m = _FATAL.search(text)
    if m:
        raw = m.group(1).splitlines()[0].strip()
    if not raw:
        m = _ERR403.search(text)
        if m:
            raw = m.group(1).splitlines()[0].strip()
    em = _ERROR_FIELD.search(raw or text)
    if em:
        return em.group(1).strip()[:500]
    return raw[:500]


def _first_match_line(text: str, pattern: re.Pattern[str]) -> str:
    for line in text.splitlines():
        if pattern.search(line):
            return line.strip()[:500]
    return ""


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) < 2:
        print("usage: provider_alerts.py classify|message <result_file> [agent]", file=sys.stderr)
        return 2
    cmd, path = args[0], args[1]
    info = classify_file(path)
    if cmd == "classify":
        print(json.dumps(info, ensure_ascii=False))
        return 0
    if cmd == "message":
        agent = args[2] if len(args) > 2 else "agent"
        print(format_pu_message(agent, info), end="")
        return 0
    print(f"unknown command: {cmd}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
