"""Client → GPU-host media SSH (import/generate) and PNG return.

No Textual import. Weights stay on the GPU host. The client only copies the PNG.
"""

from __future__ import annotations

import json
import os
import re
import select
import shlex
import shutil
import subprocess
import time
from typing import Any, Callable

_SETUP_INI = "/etc/versa-agi/setup.ini"
_CLIENT_CONFIG = "/etc/versa-agi/client_config.json"
_WATCHDOG_USER = "watchdog"
_REMOTE_OUT_DIR = "/tmp/versa-agi-media-out"
_SSH_OPTS = (
    "-o", "StrictHostKeyChecking=accept-new",
    "-o", "UserKnownHostsFile=/dev/null",
    "-o", "LogLevel=ERROR",
    "-o", "ConnectTimeout=15",
    "-o", "BatchMode=yes",
)

ProgressFn = Callable[[str], None]
RunFn = Callable[..., Any]


class MediaRemoteError(Exception):
    def __init__(self, message: str, code: str = "remote_failed"):
        super().__init__(message)
        self.message = message
        self.code = code


def read_local_ai_topology(setup_ini: str = _SETUP_INI) -> str:
    if not os.path.isfile(setup_ini):
        return "local"
    try:
        import configparser

        cfg = configparser.ConfigParser()
        cfg.read(setup_ini)
        return (cfg.get("local_ai", "topology", fallback="local") or "local").strip().lower()
    except Exception:
        return "local"


def is_client_topology(topology: str | None = None, setup_ini: str = _SETUP_INI) -> bool:
    topo = (topology if topology is not None else read_local_ai_topology(setup_ini))
    return (topo or "local").strip().lower() == "client"


def read_tunnel_host(client_config: str = _CLIENT_CONFIG) -> str:
    if not os.path.isfile(client_config):
        return ""
    try:
        with open(client_config, encoding="utf-8") as fh:
            cfg = json.load(fh)
        return str(cfg.get("tunnel_host") or "").strip()
    except Exception:
        return ""


def watchdog_ssh_key(watchdog_user: str = _WATCHDOG_USER) -> str:
    return f"/home/{watchdog_user}/.ssh/versa_agi_ed25519"


def local_bundle_ready(bundle_dir: str) -> bool:
    """True when this host already has media weights (GPU host or a test stub)."""
    if not bundle_dir or not os.path.isdir(bundle_dir):
        return False
    if os.path.isfile(os.path.join(bundle_dir, "bundle.json")):
        return True
    try:
        names = os.listdir(bundle_dir)
    except OSError:
        return False
    return any(name.endswith(".gguf") for name in names)


def format_elapsed(seconds: float) -> str:
    total = max(0, int(seconds))
    mins, secs = divmod(total, 60)
    if mins:
        return f"{mins}m {secs:02d}s"
    return f"{secs}s"


def split_progress_lines(text: str) -> tuple[list[str], str]:
    """Split tqdm-style CR/LF chunks. Returns (complete lines, remainder)."""
    parts = re.split(r"[\r\n]", text)
    return [part.strip() for part in parts[:-1] if part.strip()], parts[-1]


def is_ssh_noise(line: str) -> bool:
    """True for OpenSSH known_hosts chatter we do not want as progress."""
    low = (line or "").strip().lower()
    return "permanently added" in low or "known hosts" in low or "known_hosts" in low


def parse_agictl_json(stdout: str) -> dict:
    if not stdout:
        return {}
    for line in reversed(stdout.strip().splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                return json.loads(line)
            except Exception:  # noqa: BLE001
                continue
    return {}


def build_watchdog_ssh_cmd(
    remote: str,
    *,
    tunnel_host: str,
    ssh_key: str = "",
    watchdog_user: str = _WATCHDOG_USER,
) -> list[str]:
    host = (tunnel_host or "").strip()
    if not host:
        raise ValueError("No tunnel_host configured. Run setup_local.sh in client mode first.")
    key = (ssh_key or watchdog_ssh_key(watchdog_user)).strip()
    return [
        "sudo", "-u", watchdog_user,
        "ssh", "-i", key,
        *_SSH_OPTS,
        f"{watchdog_user}@{host}",
        remote,
    ]


def build_gpu_host_agictl_cmd(
    args: list[str],
    *,
    topology: str,
    tunnel_host: str = "",
    ssh_key: str = "",
    watchdog_user: str = _WATCHDOG_USER,
    sudo: bool = True,
) -> list[str]:
    """Local ``sudo agictl`` or client SSH, same pattern as SYCL activate."""
    local = (["sudo", "agictl"] if sudo else ["agictl"]) + list(args)
    if (topology or "local").strip().lower() != "client":
        return local
    remote = "sudo -n agictl " + " ".join(shlex.quote(part) for part in args)
    return build_watchdog_ssh_cmd(
        remote,
        tunnel_host=tunnel_host,
        ssh_key=ssh_key,
        watchdog_user=watchdog_user,
    )


def build_gpu_host_scp_cmd(
    remote_path: str,
    local_path: str,
    *,
    tunnel_host: str,
    ssh_key: str = "",
    watchdog_user: str = _WATCHDOG_USER,
) -> list[str]:
    host = (tunnel_host or "").strip()
    if not host:
        raise ValueError("No tunnel_host configured. Run setup_local.sh in client mode first.")
    key = (ssh_key or watchdog_ssh_key(watchdog_user)).strip()
    return [
        "sudo", "-u", watchdog_user,
        "scp", "-i", key,
        *_SSH_OPTS,
        f"{watchdog_user}@{host}:{remote_path}",
        local_path,
    ]


def build_media_generate_args(
    name: str,
    prompt: str,
    out_path: str,
    *,
    width: int | None = None,
    height: int | None = None,
    steps: int | None = None,
    cfg_scale: float | None = None,
    seed: int | None = None,
    offload: bool = False,
) -> list[str]:
    args = [
        "model", "media", "generate",
        "--name", name,
        "--prompt", prompt,
        "--out", out_path,
    ]
    if width is not None:
        args += ["--width", str(int(width))]
    if height is not None:
        args += ["--height", str(int(height))]
    if steps is not None:
        args += ["--steps", str(int(steps))]
    if cfg_scale is not None:
        args += ["--cfg-scale", str(cfg_scale)]
    if seed is not None:
        args += ["--seed", str(int(seed))]
    if offload:
        args.append("--offload")
    return args


def run_cmd_streaming(
    cmd: list[str],
    *,
    timeout: int = 3600,
    on_progress: ProgressFn | None = None,
    popen: Callable[..., Any] | None = None,
) -> tuple[bool, dict, str]:
    """Run ``cmd``, emit CR/LF progress, parse a trailing agictl JSON line."""
    factory = popen or subprocess.Popen
    started = time.monotonic()
    try:
        proc = factory(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            bufsize=0,
        )
    except Exception as exc:  # noqa: BLE001
        return False, {}, str(exc)

    stdout_fh = proc.stdout
    if stdout_fh is None:
        return False, {}, "no process output"
    chunks: list[str] = []
    remainder = ""
    last_line = "still working…"
    last_beat = 0.0
    timed_out = False
    try:
        while True:
            remaining = timeout - (time.monotonic() - started)
            if remaining <= 0:
                timed_out = True
                proc.kill()
                break
            ready, _, _ = select.select([stdout_fh], [], [], min(1.0, remaining))
            if ready:
                raw = stdout_fh.read(4096)
                if raw:
                    text = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else raw
                    chunks.append(text)
                    lines, remainder = split_progress_lines(remainder + text)
                    for line in lines:
                        if is_ssh_noise(line):
                            continue
                        last_line = line
                        if on_progress:
                            on_progress(line)
                elif proc.poll() is not None:
                    break
            elif on_progress and (time.monotonic() - last_beat) >= 2:
                last_beat = time.monotonic()
                on_progress(last_line if not is_ssh_noise(last_line) else "still working…")
            if proc.poll() is not None and not ready:
                rest = stdout_fh.read()
                if rest:
                    text = rest.decode("utf-8", errors="replace") if isinstance(rest, bytes) else rest
                    chunks.append(text)
                    lines, remainder = split_progress_lines(remainder + text)
                    for line in lines:
                        if is_ssh_noise(line):
                            continue
                        last_line = line
                        if on_progress:
                            on_progress(line)
                break
        try:
            proc.wait(timeout=5)
        except Exception:  # noqa: BLE001
            proc.kill()
    except Exception as exc:  # noqa: BLE001
        try:
            proc.kill()
        except Exception:  # noqa: BLE001
            pass
        return False, {}, str(exc)

    if remainder.strip():
        chunks.append(remainder)
        if on_progress and not is_ssh_noise(remainder.strip()):
            on_progress(remainder.strip())
    full = "".join(chunks)
    if timed_out:
        return False, {}, "command timed out"
    data = parse_agictl_json(full)
    ok = bool(data.get("success")) if data else (proc.returncode == 0)
    err = ""
    if not ok:
        err = data.get("error") or (full.strip() or "Unknown error")
    return ok, data, err


def remote_media_generate(
    name: str,
    prompt: str,
    dest_path: str,
    *,
    width: int | None = None,
    height: int | None = None,
    steps: int | None = None,
    cfg_scale: float | None = None,
    seed: int | None = None,
    offload: bool = False,
    topology: str | None = None,
    tunnel_host: str = "",
    ssh_key: str = "",
    timeout: int = 1800,
    on_progress: ProgressFn | None = None,
    run_fn: RunFn | None = None,
) -> dict[str, Any]:
    """Paint on the GPU host and copy the PNG to ``dest_path`` on this machine."""
    topo = (topology if topology is not None else read_local_ai_topology()).strip().lower()
    if topo != "client":
        raise MediaRemoteError(
            "remote_media_generate is for topology=client only",
            "not_client",
        )
    host = (tunnel_host or read_tunnel_host()).strip()
    if not host:
        raise MediaRemoteError(
            "No tunnel_host configured. Run setup_local.sh in client mode first.",
            "no_tunnel",
        )
    dest = os.path.abspath(dest_path)
    os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
    stamp = int(time.time())
    remote_out = f"{_REMOTE_OUT_DIR}/{name}-{stamp}.png"
    local_tmp = f"/tmp/versa-agi-media-in-{name}-{stamp}.png"

    args = build_media_generate_args(
        name,
        prompt,
        remote_out,
        width=width,
        height=height,
        steps=steps,
        cfg_scale=cfg_scale,
        seed=seed,
        offload=offload,
    )
    ssh_cmd = build_gpu_host_agictl_cmd(
        args,
        topology="client",
        tunnel_host=host,
        ssh_key=ssh_key,
    )
    scp_cmd = build_gpu_host_scp_cmd(
        remote_out,
        local_tmp,
        tunnel_host=host,
        ssh_key=ssh_key,
    )

    def _run(cmd: list[str], *, cmd_timeout: int) -> Any:
        if run_fn is not None:
            return run_fn(cmd, timeout=cmd_timeout)
        if on_progress is not None:
            ok, data, err = run_cmd_streaming(
                cmd, timeout=cmd_timeout, on_progress=on_progress,
            )
            return type("Proc", (), {
                "returncode": 0 if ok else 1,
                "stdout": json.dumps(data) if data else "",
                "stderr": err,
                "_parsed": data,
                "_err": err,
            })()
        return subprocess.run(
            cmd, capture_output=True, text=True, timeout=cmd_timeout,
        )

    if on_progress:
        on_progress(f"Painting on GPU host ({host})…")
    try:
        painted = _run(ssh_cmd, cmd_timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise MediaRemoteError(
            f"GPU host paint timed out after {timeout}s",
            "timeout",
        ) from exc
    parsed = getattr(painted, "_parsed", None)
    if not isinstance(parsed, dict):
        parsed = parse_agictl_json(getattr(painted, "stdout", "") or "")
    if painted.returncode != 0 or (parsed and not parsed.get("success", True)):
        err = (
            getattr(painted, "_err", "")
            or (parsed.get("error") if parsed else "")
            or (getattr(painted, "stderr", "") or getattr(painted, "stdout", "") or "paint failed")
        )
        raise MediaRemoteError(str(err).strip(), "paint_failed")

    if on_progress and parsed.get("seed") is not None:
        on_progress(f"seed {parsed['seed']}")
    if on_progress:
        on_progress("Copying PNG back to this machine…")
    try:
        copied = _run(scp_cmd, cmd_timeout=120)
    except subprocess.TimeoutExpired as exc:
        raise MediaRemoteError("PNG copy from GPU host timed out", "scp_timeout") from exc
    if copied.returncode != 0 or not os.path.isfile(local_tmp):
        err = (getattr(copied, "stderr", "") or getattr(copied, "stdout", "") or "scp failed")
        raise MediaRemoteError(str(err).strip(), "scp_failed")

    if os.path.abspath(local_tmp) != dest:
        shutil.copy2(local_tmp, dest)
        try:
            os.remove(local_tmp)
        except OSError:
            pass
    if not os.path.isfile(dest):
        raise MediaRemoteError(f"PNG did not land at {dest}", "copy_missing")

    try:
        cleanup = build_watchdog_ssh_cmd(
            f"rm -f {shlex.quote(remote_out)}",
            tunnel_host=host,
            ssh_key=ssh_key,
        )
        _run(cleanup, cmd_timeout=30)
    except Exception:  # noqa: BLE001
        pass

    size = os.path.getsize(dest)
    return {
        "success": True,
        "action": "generated",
        "name": name,
        "path": dest,
        "bytes": size,
        "remote_host": host,
        "returned": True,
        **{k: parsed[k] for k in ("width", "height", "steps", "cfg_scale", "seed", "offload", "warning") if k in parsed},
    }
