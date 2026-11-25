"""Host-class detection mirroring ui_lib.sh detect_host_runtime().

Used to pick IDE connect instructions. Does not refuse WSL or VMs.
"""
from __future__ import annotations

import os
import platform
import subprocess


def detect_host_class() -> dict:
    """Return host_class, host_virt, windows_interop, os_pretty.

    host_class is native_linux | wsl1 | wsl2. Non-Linux uname is reported
    as host_class=unsupported (Remote-SSH still works from a Linux guest).
    """
    system = platform.system()
    os_pretty = _os_pretty()
    if system != "Linux":
        return {
            "host_class": "unsupported",
            "host_virt": system.lower() or "unknown",
            "windows_interop": False,
            "os_pretty": os_pretty or system,
        }

    is_wsl = bool(os.environ.get("WSL_DISTRO_NAME")) or _proc_version_is_wsl()
    if is_wsl:
        wsl2 = (
            os.path.exists("/proc/sys/fs/binfmt_misc/WSLInterop")
            or bool(os.environ.get("WSL_INTEROP"))
        )
        return {
            "host_class": "wsl2" if wsl2 else "wsl1",
            "host_virt": "wsl2" if wsl2 else "wsl1",
            "windows_interop": os.path.isdir("/mnt/c/Windows"),
            "os_pretty": os_pretty,
        }

    virt = _detect_virt()
    return {
        "host_class": "native_linux",
        "host_virt": virt,
        "windows_interop": False,
        "os_pretty": os_pretty,
    }


def write_ssh_config_locally(host_class: str, host_virt: str) -> bool:
    """True when it is useful to write ~/.ssh/config on this Linux home."""
    if host_class in ("wsl1", "wsl2"):
        return False
    if host_class != "native_linux":
        return False
    if host_virt and host_virt not in ("none", ""):
        return False
    return True


def _proc_version_is_wsl() -> bool:
    try:
        with open("/proc/version", encoding="utf-8") as f:
            text = f.read().lower()
    except OSError:
        return False
    return "microsoft" in text or "wsl" in text


def _os_pretty() -> str:
    try:
        with open("/etc/os-release", encoding="utf-8") as f:
            data = {}
            for line in f:
                if "=" in line:
                    k, v = line.rstrip().split("=", 1)
                    data[k] = v.strip().strip('"')
            return data.get("PRETTY_NAME") or data.get("NAME") or "Linux"
    except OSError:
        return "Linux"


def _detect_virt() -> str:
    try:
        proc = subprocess.run(
            ["systemd-detect-virt"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return "none"
    value = (proc.stdout or "").strip()
    if proc.returncode != 0 or value in ("", "none"):
        return "none"
    return value
