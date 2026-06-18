"""
Install acceptance registration — submit, tripwire, and status for agitop.

See design/Versa AGi - Production Plan.md §6.8, Iteration 24.
"""

from __future__ import annotations

import argparse
import configparser
import hashlib
import json
import shutil
import socket
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

SETUP_INI = Path("/etc/versa-agi/setup.ini")
SOURCE_SETUP_INI = Path(__file__).resolve().parent.parent / "setup.ini"
REG_CONF = Path("/etc/versa-agi/registration.conf")
REG_CONF_SOURCE = Path(__file__).resolve().parent / "registration.conf"
DEFAULT_ACCEPTANCE_FILE = Path("/etc/versa-agi/install-acceptance.json")
STATUS_JSON = Path("/var/lib/versa-agi/registration-status.json")
MAX_ATTEMPTS = 10
VERSION_FILE = Path(__file__).resolve().parent / "VERSION"


def read_product_version() -> str:
    if VERSION_FILE.is_file():
        return VERSION_FILE.read_text(encoding="utf-8").strip() or "unknown"
    return "unknown"


def _hostname_hash() -> str:
    host = socket.gethostname() or "unknown"
    digest = hashlib.sha256(host.encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def _platform_id() -> str:
    release = Path("/etc/os-release")
    if not release.is_file():
        return "unknown"
    values: dict[str, str] = {}
    for line in release.read_text(encoding="utf-8").splitlines():
        if "=" not in line or line.startswith("#"):
            continue
        key, raw = line.split("=", 1)
        values[key] = raw.strip().strip('"')
    distro = values.get("ID", "unknown")
    version = values.get("VERSION_ID", "unknown")
    return f"{distro}-{version}"


def build_gate_payload(
    *,
    installed_version: str,
    accepted_at_utc: str,
) -> dict:
    """Minimal probe payload for pre-install version gate (no Firestore write)."""
    return {
        "event": "install_acceptance",
        "product": "versa-agi",
        "company": "VersaVoice AI LLC",
        "version": installed_version,
        "install_mode": "full",
        "accepted_at_utc": accepted_at_utc,
        "license": "BSL-1.1",
        "platform": _platform_id(),
        "hostname_hash": _hostname_hash(),
    }


def _read_ini_section(path: Path, section: str) -> dict[str, str]:
    cfg = configparser.ConfigParser(delimiters=("=",))
    if not path.is_file():
        return {}
    cfg.read(path)
    if not cfg.has_section(section):
        return {}
    return {k: v for k, v in cfg.items(section)}


def read_registration_conf(path: Path = REG_CONF) -> dict[str, str]:
    if path.is_file():
        return _read_ini_section(path, "registration")
    if REG_CONF_SOURCE.is_file():
        return _read_ini_section(REG_CONF_SOURCE, "registration")
    return {}


def read_runtime_state(setup_ini: Path = SETUP_INI) -> dict[str, str]:
    return _read_ini_section(setup_ini, "registration")


def _sync_setup_ini_to_source(deployed: Path = SETUP_INI) -> None:
    """Copy deployed setup.ini back to src/setup.ini (non-fatal)."""
    try:
        if not deployed.is_file() or not SOURCE_SETUP_INI.is_file():
            return
        if deployed.resolve() == SOURCE_SETUP_INI.resolve():
            return
        shutil.copy2(deployed, SOURCE_SETUP_INI)
        SOURCE_SETUP_INI.chmod(0o600)
    except OSError:
        pass


def _write_runtime_key(key: str, value: str, setup_ini: Path = SETUP_INI) -> None:
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
    _sync_setup_ini_to_source(setup_ini)


def load_status() -> dict:
    if STATUS_JSON.is_file():
        try:
            return json.loads(STATUS_JSON.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def save_status(data: dict) -> None:
    STATUS_JSON.parent.mkdir(parents=True, exist_ok=True)
    STATUS_JSON.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    try:
        STATUS_JSON.chmod(0o640)
        import subprocess
        subprocess.run(
            ["chown", "watchdog:watchdog", str(STATUS_JSON)],
            check=False,
            capture_output=True,
        )
    except OSError:
        pass


def _merge_status(
    response: dict,
    runtime: dict,
    *,
    installed_version: str = "",
) -> dict:
    merged = {
        "registration_status": response.get("registration_status", "deferred"),
        "success": response.get("success", False),
        "installed_version": response.get(
            "installed_version", installed_version or read_product_version()
        ),
        "latest_version": response.get("latest_version", ""),
        "min_supported_version": response.get("min_supported_version", ""),
        "update_available": bool(response.get("update_available", False)),
        "below_min_supported": bool(response.get("below_min_supported", False)),
        "message": response.get("message", ""),
        "registration_submitted": runtime.get("registration_submitted", "false"),
        "registration_submitted_at": runtime.get("registration_submitted_at", ""),
        "registration_last_error": runtime.get("registration_last_error", ""),
        "checked_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    return merged


def _parse_response_body(raw: bytes, http_code: int) -> dict:
    if not raw:
        return {
            "success": False,
            "registration_status": "error",
            "message": f"HTTP {http_code}",
        }
    try:
        data = json.loads(raw.decode("utf-8"))
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    return {
        "success": False,
        "registration_status": "error",
        "message": f"HTTP {http_code}",
    }


def submit_registration(
    setup_ini: Path = SETUP_INI,
    reg_conf: Path = REG_CONF,
    *,
    strict_block: bool = False,
) -> dict:
    """POST install acceptance JSON. Returns merged status dict."""
    runtime = read_runtime_state(setup_ini)
    infra = read_registration_conf(reg_conf)
    installed_version = read_product_version()

    if runtime.get("registration_submitted", "false").lower() == "true":
        cached = load_status()
        if cached.get("latest_version"):
            return _merge_status(
                {
                    "success": True,
                    "registration_status": cached.get(
                        "registration_status", "registered"
                    ),
                    "installed_version": installed_version,
                    "latest_version": cached.get("latest_version", ""),
                    "min_supported_version": cached.get(
                        "min_supported_version", ""
                    ),
                    "update_available": cached.get("update_available", False),
                    "below_min_supported": cached.get(
                        "below_min_supported", False
                    ),
                    "message": cached.get("message", "Already registered."),
                },
                runtime,
                installed_version=installed_version,
            )
        return send_heartbeat(setup_ini, reg_conf)

    endpoint = infra.get("registration_endpoint", "").strip()
    install_key = infra.get("registration_install_key", "").strip()
    if not endpoint:
        status = _merge_status(
            {
                "success": True,
                "registration_status": "deferred",
                "installed_version": installed_version,
                "message": "Registration endpoint not configured.",
            },
            runtime,
            installed_version=installed_version,
        )
        save_status(status)
        return status

    try:
        attempt_count = int(runtime.get("registration_attempt_count", "0") or "0")
    except ValueError:
        attempt_count = 0
    if attempt_count >= MAX_ATTEMPTS:
        status = _merge_status(
            {
                "success": False,
                "registration_status": "deferred",
                "installed_version": installed_version,
                "message": "Maximum registration attempts reached.",
            },
            runtime,
            installed_version=installed_version,
        )
        save_status(status)
        return status

    acceptance_path = Path(
        runtime.get("acceptance_file", str(DEFAULT_ACCEPTANCE_FILE))
    )
    if not acceptance_path.is_file():
        status = _merge_status(
            {
                "success": False,
                "registration_status": "deferred",
                "installed_version": installed_version,
                "message": "Acceptance file missing.",
            },
            runtime,
            installed_version=installed_version,
        )
        save_status(status)
        return status

    _write_runtime_key("registration_attempt_count", str(attempt_count + 1), setup_ini)
    runtime = read_runtime_state(setup_ini)

    headers = {"Content-Type": "application/json"}
    if install_key:
        headers["X-Versa-Install-Key"] = install_key

    try:
        payload = acceptance_path.read_bytes()
        req = urllib.request.Request(
            endpoint,
            data=payload,
            headers=headers,
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = resp.read()
            parsed = _parse_response_body(body, resp.status)
            if 200 <= resp.status < 300:
                now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                prior = read_runtime_state(setup_ini)
                _write_runtime_key("registration_submitted", "true", setup_ini)
                _write_runtime_key("registration_submitted_at", now, setup_ini)
                _write_runtime_key("registration_last_error", "", setup_ini)
                if not prior.get("registration_last_heartbeat_at", "").strip():
                    _write_runtime_key(
                        "registration_last_heartbeat_at", now, setup_ini
                    )
                runtime = read_runtime_state(setup_ini)
            else:
                _write_runtime_key(
                    "registration_last_error",
                    parsed.get("message", f"HTTP {resp.status}")[:200],
                    setup_ini,
                )
                runtime = read_runtime_state(setup_ini)
    except urllib.error.HTTPError as exc:
        body = exc.read() if exc.fp else b""
        parsed = _parse_response_body(body, exc.code)
        _write_runtime_key(
            "registration_last_error",
            parsed.get("message", f"HTTP {exc.code}")[:200],
            setup_ini,
        )
        runtime = read_runtime_state(setup_ini)
        status = _merge_status(parsed, runtime, installed_version=installed_version)
        save_status(status)
        if strict_block and (
            exc.code == 403
            or status.get("registration_status") == "rejected_below_min"
            or status.get("below_min_supported")
        ):
            status["blocked"] = True
        return status
    except Exception as exc:
        _write_runtime_key("registration_last_error", str(exc)[:200], setup_ini)
        runtime = read_runtime_state(setup_ini)
        status = _merge_status(
            {
                "success": False,
                "registration_status": "warn_offline",
                "installed_version": installed_version,
                "message": str(exc)[:200],
            },
            runtime,
            installed_version=installed_version,
        )
        save_status(status)
        return status

    status = _merge_status(parsed, runtime, installed_version=installed_version)
    save_status(status)
    if strict_block and status.get("below_min_supported"):
        status["blocked"] = True
    return status


def build_heartbeat_payload() -> dict:
    """Minimal weekly activity payload for lifeline heartbeat."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {
        "event": "heartbeat",
        "product": "versa-agi",
        "version": read_product_version(),
        "hostname_hash": _hostname_hash(),
        "license": "BSL-1.1",
        "accepted_at_utc": now,
        "platform": _platform_id(),
    }


def send_heartbeat(
    setup_ini: Path = SETUP_INI,
    reg_conf: Path = REG_CONF,
) -> dict:
    """POST weekly heartbeat. Updates registration_last_heartbeat_at on success."""
    runtime = read_runtime_state(setup_ini)
    infra = read_registration_conf(reg_conf)
    installed_version = read_product_version()

    if runtime.get("registration_submitted", "false").lower() != "true":
        return {
            "success": False,
            "registration_status": "deferred",
            "installed_version": installed_version,
            "message": "Install not registered yet.",
        }

    endpoint = infra.get("registration_endpoint", "").strip()
    install_key = infra.get("registration_install_key", "").strip()
    if not endpoint:
        return {
            "success": False,
            "registration_status": "deferred",
            "installed_version": installed_version,
            "message": "Registration endpoint not configured.",
        }

    payload = build_heartbeat_payload()
    headers = {"Content-Type": "application/json"}
    if install_key:
        headers["X-Versa-Install-Key"] = install_key

    try:
        req = urllib.request.Request(
            endpoint,
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            parsed = _parse_response_body(resp.read(), resp.status)
            if 200 <= resp.status < 300:
                now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                _write_runtime_key(
                    "registration_last_heartbeat_at", now, setup_ini
                )
            status = _merge_status(
                parsed,
                read_runtime_state(setup_ini),
                installed_version=installed_version,
            )
            save_status(status)
            return status
    except urllib.error.HTTPError as exc:
        body = exc.read() if exc.fp else b""
        parsed = _parse_response_body(body, exc.code)
        status = _merge_status(
            parsed,
            read_runtime_state(setup_ini),
            installed_version=installed_version,
        )
        save_status(status)
        return status
    except Exception as exc:
        status = _merge_status(
            {
                "success": False,
                "registration_status": "warn_offline",
                "installed_version": installed_version,
                "message": str(exc)[:200],
            },
            read_runtime_state(setup_ini),
            installed_version=installed_version,
        )
        save_status(status)
        return status


def format_version_block_message(status: dict) -> str:
    """Human-readable multi-line message for below-min version blocks."""
    installed = status.get("installed_version", "").strip()
    min_v = status.get("min_supported_version", "").strip()
    latest = status.get("latest_version", "").strip()
    lines: list[str] = []
    if installed:
        lines.append(f"Version {installed} is no longer supported.")
    else:
        lines.append("This version is no longer supported.")
    if min_v:
        lines.append(f"Minimum supported version: {min_v}")
    if latest:
        lines.append(f"Latest release: {latest}")
    lines.append(
        "Download and install the latest Versa AGi release before continuing."
    )
    return "\n".join(lines)


def check_version_gate(
    reg_conf: Path = REG_CONF,
    *,
    accepted_at_utc: str = "",
) -> dict:
    """Probe release policy before full install proceeds."""
    infra = read_registration_conf(reg_conf)
    installed_version = read_product_version()
    endpoint = infra.get("registration_endpoint", "").strip()
    install_key = infra.get("registration_install_key", "").strip()
    accepted = accepted_at_utc or datetime.now(timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )

    if not endpoint:
        return {
            "success": True,
            "registration_status": "deferred",
            "installed_version": installed_version,
            "message": "Registration endpoint not configured.",
        }

    payload = build_gate_payload(
        installed_version=installed_version,
        accepted_at_utc=accepted,
    )
    headers = {"Content-Type": "application/json"}
    if install_key:
        headers["X-Versa-Install-Key"] = install_key

    try:
        req = urllib.request.Request(
            endpoint,
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            parsed = _parse_response_body(resp.read(), resp.status)
            status = _merge_status(
                parsed,
                {},
                installed_version=installed_version,
            )
            save_status(status)
            return status
    except urllib.error.HTTPError as exc:
        body = exc.read() if exc.fp else b""
        parsed = _parse_response_body(body, exc.code)
        status = _merge_status(
            parsed,
            {},
            installed_version=installed_version,
        )
        save_status(status)
        if exc.code == 403 or status.get("below_min_supported"):
            status["blocked"] = True
        return status
    except Exception as exc:
        status = _merge_status(
            {
                "success": False,
                "registration_status": "warn_offline",
                "installed_version": installed_version,
                "message": str(exc)[:200],
            },
            {},
            installed_version=installed_version,
        )
        save_status(status)
        return status


def enrich_status(data: dict | None = None) -> dict:
    """Merge cached JSON with live setup.ini + VERSION file."""
    merged = dict(data or load_status())
    runtime = read_runtime_state()
    installed = read_product_version()
    merged["installed_version"] = (
        merged.get("installed_version") or installed
    )
    merged["registration_submitted"] = runtime.get(
        "registration_submitted",
        merged.get("registration_submitted", "false"),
    )
    merged["registration_submitted_at"] = runtime.get(
        "registration_submitted_at",
        merged.get("registration_submitted_at", ""),
    )
    merged["registration_last_error"] = runtime.get(
        "registration_last_error",
        merged.get("registration_last_error", ""),
    )
    merged["registration_attempt_count"] = runtime.get(
        "registration_attempt_count",
        merged.get("registration_attempt_count", "0"),
    )
    return merged


def refresh_for_display() -> dict:
    """Load registration status for agitop modal; refresh release policy if missing."""
    data = enrich_status()
    if data.get("latest_version"):
        save_status(data)
        return data

    runtime = read_runtime_state()
    if runtime.get("registration_submitted", "false").lower() == "true":
        fresh = send_heartbeat()
        if fresh.get("latest_version"):
            return fresh

    gate = check_version_gate()
    if gate.get("latest_version"):
        return gate
    save_status(data)
    return data


def tripwire_submit(log: bool = True) -> dict:
    """Retry registration on agitop launch when not yet submitted."""
    try:
        return submit_registration(strict_block=False)
    except Exception as exc:
        if log:
            print(f"[agitop] install registration tripwire: {exc}", file=sys.stderr)
        return {}


def should_show_registration_modal(status: dict | None = None) -> bool:
    data = status or load_status()
    if not data:
        return False
    if data.get("registration_submitted", "false").lower() != "true":
        return True
    if data.get("update_available"):
        return True
    if data.get("below_min_supported"):
        return True
    return False


def main() -> int:
    parser = argparse.ArgumentParser(description="Versa AGi install registration")
    parser.add_argument(
        "command",
        nargs="?",
        choices=["submit", "status", "tripwire", "gate", "format-block", "heartbeat"],
        default="submit",
    )
    parser.add_argument(
        "--strict-block",
        action="store_true",
        help="Flag blocked below-min responses for full install",
    )
    parser.add_argument("--json", action="store_true", help="Emit JSON on stdout")
    parser.add_argument(
        "payload_json",
        nargs="?",
        help="JSON payload for format-block command",
    )
    args = parser.parse_args()

    if args.command == "format-block":
        raw = args.payload_json or sys.stdin.read().strip()
        try:
            data = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            data = {}
        print(format_version_block_message(data))
        return 0

    if args.command == "status":
        data = refresh_for_display()
        if args.json:
            print(json.dumps(data))
        else:
            print(json.dumps(data, indent=2))
        return 0

    if args.command == "tripwire":
        result = tripwire_submit()
        if args.json:
            print(json.dumps(result))
        return 0

    if args.command == "gate":
        result = check_version_gate()
        if args.json:
            print(json.dumps(result))
        if result.get("blocked"):
            return 2
        return 0

    if args.command == "heartbeat":
        result = send_heartbeat()
        if args.json:
            print(json.dumps(result))
        if result.get("registration_status") == "heartbeat_ok":
            return 0
        if result.get("registration_status") in ("deferred", "heartbeat_deferred"):
            return 0
        if result.get("success"):
            return 0
        return 1

    result = submit_registration(strict_block=args.strict_block)
    if args.json:
        print(json.dumps(result))
    if result.get("blocked"):
        return 2
    if result.get("registration_submitted", "false").lower() == "true":
        return 0
    if result.get("registration_status") == "deferred":
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
