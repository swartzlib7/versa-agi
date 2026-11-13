"""Activity stats from cycles.db, for the VersaVoice agent card (state_live_chat.md LCH-24).

Builds the same shape the cloud keeps in `users/{agentUid}/agi_activity/stats`
(§1.9.5) from this machine's full cycle history, so the charts show history
and not only cycles that ended after Live Chat P4 shipped.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

RECENT = 20
HOURS_KEPT = 48
OUTCOMES = ("ok", "timeout", "runaway", "step_limit", "crash")


def outcome_for(exit_code, ended_at) -> str | None:
    """Lifeline exit code → outcome. None when the cycle is still running / unknown."""
    if exit_code is None or exit_code == "":
        return "ok" if ended_at else None
    try:
        code = int(exit_code)
    except (TypeError, ValueError):
        return "crash"
    # 54 = time limit, closed by the harness. 55 = repeated call, 97 = stall
    # (state_long_cycles.md §1.8).
    return {0: "ok", 124: "timeout", 54: "timeout", 99: "runaway", 53: "step_limit",
            97: "stall", 55: "loop"}.get(code, "crash")


def wake_for(wake_reason) -> str:
    text = str(wake_reason or "").lower()
    if "message" in text:
        return "messages"
    if "force" in text or "run now" in text:
        return "forced"
    return "tasks"


def parse_utc(value) -> datetime | None:
    """cycles.db stores sqlite datetime('now') strings (UTC)."""
    if not value:
        return None
    text = str(value).strip().replace("T", " ").rstrip("Z")
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M:%S.%f"):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _hour_key(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H")


def build_stats(rows: list[dict], now: datetime | None = None) -> dict:
    """rows: dicts with id, started_at, ended_at, exit_code, wake_reason (any order)."""
    now = now or datetime.now(timezone.utc)
    cycles = []
    for row in rows:
        start = parse_utc(row.get("started_at"))
        if start is None:
            continue
        end = parse_utc(row.get("ended_at"))
        cycles.append({
            "id": str(row.get("id") or ""),
            "start": start,
            "end": end if end and end >= start else None,
            "outcome": outcome_for(row.get("exit_code"), row.get("ended_at")),
            "wake": wake_for(row.get("wake_reason")),
        })
    cycles.sort(key=lambda c: c["start"])

    outcomes = {key: 0 for key in OUTCOMES}
    for c in cycles:
        if c["outcome"]:
            outcomes[c["outcome"]] += 1

    timed = [c for c in cycles if c["outcome"] and c["end"]]
    recent = [
        {
            "cycleId": c["id"],
            "wake": c["wake"],
            "outcome": c["outcome"],
            "startedAt": _iso(c["start"]),
            "endedAt": _iso(c["end"]),
            "durationSeconds": int((c["end"] - c["start"]).total_seconds()),
        }
        for c in reversed(timed[-RECENT:])
    ]

    cutoff = now - timedelta(hours=HOURS_KEPT)
    hourly: dict[str, dict] = {}
    for c in timed:
        if c["end"] < cutoff:
            continue
        t = max(c["start"], cutoff)
        while t < c["end"]:
            hour_end = t.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
            slice_end = min(hour_end, c["end"])
            bucket = hourly.setdefault(_hour_key(t), {"cycles": 0, "busySeconds": 0})
            bucket["busySeconds"] += int(round((slice_end - t).total_seconds()))
            t = slice_end
        end_bucket = hourly.setdefault(_hour_key(c["end"]), {"cycles": 0, "busySeconds": 0})
        end_bucket["cycles"] += 1

    return {
        "recent": recent,
        "outcomes": outcomes,
        "hourly": hourly,
        "cyclesTotal": len(cycles),
        "trackedSince": _iso(cycles[0]["start"]) if cycles else _iso(now),
    }
