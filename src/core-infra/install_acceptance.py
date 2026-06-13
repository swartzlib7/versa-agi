"""
Install acceptance registration — tripwire retry for agitop.

See design/Versa AGi - Production Plan.md §6.8.
"""

from __future__ import annotations

import configparser
import json
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

SETUP_INI = Path("/etc/versa-agi/setup.ini")
DEFAULT_ACCEPTANCE_FILE = Path("/etc/versa-agi/install-acceptance.json")
MAX_ATTEMPTS = 10


def _read_registration(setup_ini: Path = SETUP_INI) -> dict[str, str]:
    cfg = configparser.ConfigParser(delimiters=("=",))
    if not setup_ini.is_file():
        return {}
    cfg.read(setup_ini)
    if not cfg.has_section("registration"):
        return {}
    return {k: v for k, v in cfg.items("registration")}


def _write_registration_key(key: str, value: str, setup_ini: Path = SETUP_INI) -> None:
    if not setup_ini.is_file():
        return
    lines = setup_ini.read_text().splitlines()
    out: list[str] = []
    in_section = False
    replaced = False
    for line in lines:
        stripped = line.strip()
        if stripped == "[registration]":
            in_section = True
            out.append(line)
            continue
        if in_section and stripped.startswith("[") and stripped.endswith("]"):
            if not replaced:
                out.append(f"{key}={value}")
                replaced = True
            in_section = False
        if in_section and stripped.startswith(f"{key}="):
            out.append(f"{key}={value}")
            replaced = True
            continue
        out.append(line)
    if in_section and not replaced:
        out.append(f"{key}={value}")
    setup_ini.write_text("\n".join(out) + "\n")


def try_submit_registration(setup_ini: Path = SETUP_INI) -> bool:
    """Best-effort background submission. Returns True if submitted or skipped cleanly."""
    reg = _read_registration(setup_ini)
    if not reg:
        return False

    if reg.get("registration_submitted", "false").lower() == "true":
        return True

    endpoint = reg.get("registration_endpoint", "").strip()
    if not endpoint:
        return False

    try:
        attempt_count = int(reg.get("registration_attempt_count", "0") or "0")
    except ValueError:
        attempt_count = 0
    if attempt_count >= MAX_ATTEMPTS:
        return False

    acceptance_path = Path(reg.get("acceptance_file", str(DEFAULT_ACCEPTANCE_FILE)))
    if not acceptance_path.is_file():
        return False

    _write_registration_key("registration_attempt_count", str(attempt_count + 1), setup_ini)

    try:
        payload = acceptance_path.read_bytes()
        req = urllib.request.Request(
            endpoint,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            if 200 <= resp.status < 300:
                now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                _write_registration_key("registration_submitted", "true", setup_ini)
                _write_registration_key("registration_submitted_at", now, setup_ini)
                _write_registration_key("registration_last_error", "", setup_ini)
                return True
            _write_registration_key(
                "registration_last_error",
                f"HTTP {resp.status}"[:200],
                setup_ini,
            )
    except urllib.error.HTTPError as exc:
        _write_registration_key(
            "registration_last_error",
            f"HTTP {exc.code}"[:200],
            setup_ini,
        )
    except Exception as exc:
        _write_registration_key(
            "registration_last_error",
            str(exc)[:200],
            setup_ini,
        )
    return False


def tripwire_submit(log: bool = True) -> None:
    """Silent tripwire entry point for agitop on_mount."""
    try:
        try_submit_registration()
    except Exception as exc:
        if log:
            print(f"[agitop] install registration tripwire: {exc}", file=sys.stderr)


if __name__ == "__main__":
    ok = try_submit_registration()
    sys.exit(0 if ok else 1)
