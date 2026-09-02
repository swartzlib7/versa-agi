"""agictl agent ide — COA IDE mode (hold + seed + Remote-SSH).

on/off are Primary User only (empty AGICTL_AGENT_USER AND euid 0).
status stays COA-readable — the per-turn self-check depends on it.
Do not "harden" status to PU-only later; that kills containment silently.
"""
from __future__ import annotations

import json
import os
import pwd
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import click

from agictl.platform_guard import detect_host_class, write_ssh_config_locally

IDE_STATUS = "ide"
SAFETY_HOLDS = frozenset({"halted", "circuit_breaker"})
# invalid_config is the first-login empty-model hold — IDE generate is LLM-free
# and must still work. Do not treat it as a safety hold here.
IDE_FILE_NAME = "versa-agi_ide.md"
IDE_STATE_PATH = Path("/var/lib/versa-agi/coa/ide_state.json")
CYCLES_DB = Path("/var/lib/versa-agi/coa/cycles.db")
AGENTS_DB = Path("/var/lib/versa-agi/agents.db")
SSHD_DROPIN = Path("/etc/ssh/sshd_config.d/versa-agi-ide.conf")
SSHD_KEYS_DIR = Path("/etc/ssh/versa-agi-ide")
SSHD_AUTH_KEYS = SSHD_KEYS_DIR / "authorized_keys"
KEY_DIR = Path("/etc/versa-agi/ide_ssh")
KEY_PRIV = KEY_DIR / "id_ed25519"
KEY_PUB = KEY_DIR / "id_ed25519.pub"
HOST_ALIAS = "versa-coa"
SSH_USER = "coa"
SSH_HOST = "127.0.0.1"
SSH_PORT = 22
LIFELINE = Path("/home/watchdog/core-infra/lifeline.sh")
COA_ENV = Path("/home/coa/coa-env")
IDE_FILE = COA_ENV / ".agent" / IDE_FILE_NAME

SSH_CONFIG_BLOCK = """Host {alias}
    HostName {host}
    User {user}
    Port {port}
    IdentityFile {identity}
    IdentitiesOnly yes
"""

SSHD_DROPIN_BODY = """# Versa AGi — COA IDE mode (managed by agictl agent ide)
# Loopback + pubkey only. Home authorized_keys is not used for this user.
#
# Order matters: sshd_config keeps the FIRST value obtained for a keyword, so the
# loopback Match must come before the catch-all or `PubkeyAuthentication no`
# wins everywhere and locks coa out of its own IDE session.
# Trailing `Match all` resets context — the Include sits above the global block
# in Debian/Ubuntu sshd_config.
Match User coa Address 127.0.0.1,::1
    PasswordAuthentication no
    PubkeyAuthentication yes
    AuthorizedKeysFile /etc/ssh/versa-agi-ide/authorized_keys
Match User coa
    PasswordAuthentication no
    PubkeyAuthentication no
    AuthorizedKeysFile /etc/ssh/versa-agi-ide/authorized_keys
Match all
"""


def register(agent_group, json_response):
    @agent_group.group("ide")
    def ide():
        """Hold COA out of Lifeline and generate an IDE session seed."""

    @ide.command("on")
    @click.argument("name", default="coa")
    def ide_on(name):
        _require_ide_operator(json_response)
        name = _require_coa(name, json_response)
        row = _agent_row(name)
        if not row:
            json_response(False, error=f"Agent '{name}' not found")
            sys.exit(1)
        status = (row["status"] or "").strip()
        if status in SAFETY_HOLDS:
            json_response(
                False,
                error=(
                    f"Cannot enable IDE mode while status is '{status}'. "
                    "Resolve the hold first."
                ),
                status=status,
            )
            sys.exit(1)

        host = detect_host_class()
        ssh_err = _provision_ssh()
        if ssh_err:
            json_response(False, error=ssh_err, **host)
            sys.exit(1)

        now = _utc_now()
        already = status == IDE_STATUS
        if not already:
            _set_status(name, IDE_STATUS, f"IDE mode since {now}")
        state = _read_state()
        state["last_on"] = now
        state["resume_pending"] = False
        _write_state(state)

        gap = _autonomous_gap()
        banner = gap.get("banner") or ""
        rc, gen_err = _run_lifeline_generate(name, banner)
        if rc != 0:
            json_response(
                False,
                error=gen_err or f"lifeline --ide-prompt failed (exit {rc})",
                ide_file=str(IDE_FILE),
                **host,
            )
            sys.exit(1)
        if not IDE_FILE.is_file() or IDE_FILE.stat().st_size < 200:
            json_response(False, error="Seed file missing or too small after generate")
            sys.exit(1)

        identity = _pu_identity_path()
        _maybe_install_pu_key(host, identity)
        block = SSH_CONFIG_BLOCK.format(
            alias=HOST_ALIAS,
            host=SSH_HOST,
            user=SSH_USER,
            port=SSH_PORT,
            identity=identity,
        )
        wrote_config = False
        if write_ssh_config_locally(host["host_class"], host["host_virt"]):
            wrote_config = _write_pu_ssh_config(block)

        payload = _status_payload(name, host, gap)
        payload.update(
            {
                "ide_file": str(IDE_FILE),
                "workspace": str(COA_ENV),
                "ssh_user": SSH_USER,
                "ssh_host": SSH_HOST,
                "ssh_port": SSH_PORT,
                "ssh_config_block": block,
                "ssh_config_written": wrote_config,
                "ssh_probe": _ssh_probe(),
                "already_on": already,
                "warn": (
                    "Close the IDE chat before 'ide off' or a Lifeline spawn "
                    "can run beside this session."
                ),
            }
        )
        json_response(True, **payload)

    @ide.command("off")
    @click.argument("name", default="coa")
    def ide_off(name):
        _require_ide_operator(json_response)
        name = _require_coa(name, json_response)
        row = _agent_row(name)
        if not row:
            json_response(False, error=f"Agent '{name}' not found")
            sys.exit(1)
        was_ide = (row["status"] or "").strip() == IDE_STATUS
        if IDE_FILE.is_file():
            try:
                IDE_FILE.unlink()
            except OSError as e:
                json_response(False, error=f"Could not delete seed: {e}")
                sys.exit(1)
        if was_ide:
            _set_status(name, "idle", None)
            state = _read_state()
            now = _utc_now()
            last_on = (state.get("last_on") or "").strip()
            state["last_off"] = now
            state["session_minutes"] = _session_minutes(last_on, now)
            state["resume_pending"] = True
            _write_state(state)
        json_response(
            True,
            status="idle" if was_ide else (row["status"] or ""),
            cleaned=True,
            was_ide=was_ide,
            warn=(
                "COA resumes normal Lifeline spawning on the next pulse. "
                "Close the IDE chat first — an open chat is not a harness "
                "process and will not block Lifeline."
            ),
        )

    @ide.command("status")
    @click.argument("name", default="coa")
    def ide_status(name):
        # Intentionally not PU-gated. COA must call this every turn.
        name = _require_coa(name, json_response)
        row = _agent_row(name)
        if not row:
            json_response(False, error=f"Agent '{name}' not found")
            sys.exit(1)
        host = detect_host_class()
        gap = _autonomous_gap()
        json_response(True, **_status_payload(name, host, gap, row=row))


def _require_ide_operator(json_response):
    """PU-only: wrapper stamp empty AND real root.

    COA can sudo the inner binary as watchdog (AGICTL_AGENT_USER unset).
    euid 0 is the OS boundary. Do not use this helper on `status`.
    """
    caller = os.environ.get("AGICTL_AGENT_USER", "")
    if caller or os.geteuid() != 0:
        json_response(
            False,
            error=(
                "IDE mode on/off is Primary User only "
                f"(caller={caller or 'none'}, euid={os.geteuid()})."
            ),
        )
        sys.exit(1)


def _require_coa(name, json_response):
    name = (name or "coa").strip().lower()
    if name != "coa":
        json_response(False, error="IDE mode is COA-only in this release.")
        sys.exit(1)
    return name


def _db_path(env_name, default):
    """Env override, treating set-but-empty as absent.

    `os.getenv(name, default)` returns "" when the variable exists but is
    empty, which silently points sqlite at a private temp database.
    """
    return (os.environ.get(env_name) or "").strip() or str(default)


def _agent_row(name):
    import db_connect

    path = _db_path("AGICTL_AGENTS_DB", AGENTS_DB)
    if not os.path.isfile(path):
        return None
    conn = db_connect.connect_compat(path, timeout=5)
    conn.row_factory = __import__("sqlite3").Row
    try:
        return conn.execute(
            "SELECT name, status, status_message, workspace FROM agents WHERE name=?",
            (name,),
        ).fetchone()
    finally:
        conn.close()


def _set_status(name, status, message):
    import db_connect

    path = _db_path("AGICTL_AGENTS_DB", AGENTS_DB)
    conn = db_connect.connect_compat(path, timeout=5)
    try:
        conn.execute(
            "UPDATE agents SET status=?, status_message=?, updated_at=datetime('now') "
            "WHERE name=?",
            (status, message, name),
        )
        conn.commit()
    finally:
        conn.close()


def _read_state():
    try:
        return json.loads(IDE_STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _write_state(state):
    IDE_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = IDE_STATE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, IDE_STATE_PATH)
    try:
        os.chown(IDE_STATE_PATH, _uid("watchdog"), _gid("coa"))
        os.chmod(IDE_STATE_PATH, 0o640)
    except OSError:
        pass


def _utc_now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _session_minutes(last_on, last_off):
    """Whole minutes between last_on and last_off. 0 if either stamp is bad."""
    try:
        start = datetime.fromisoformat(last_on.replace("Z", "+00:00"))
        end = datetime.fromisoformat(last_off.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return 0
    return max(0, int((end - start).total_seconds() // 60))


def _autonomous_gap():
    """Harness cycles after last_off. Generate writes no cycle."""
    last_off = (_read_state().get("last_off") or "").strip()
    empty = {"count": 0, "last_at": "", "summary": "", "banner": ""}
    if not last_off or not CYCLES_DB.is_file():
        return empty
    import db_connect

    conn = db_connect.connect_compat(str(CYCLES_DB), timeout=5)
    try:
        since = _last_off_sql(last_off)
        count = int(
            conn.execute(
                "SELECT COUNT(*) FROM cycles WHERE id LIKE 'coa-%' AND started_at > ?",
                (since,),
            ).fetchone()[0]
            or 0
        )
        last = conn.execute(
            "SELECT started_at, summary FROM cycles "
            "WHERE id LIKE 'coa-%' AND started_at > ? "
            "ORDER BY started_at DESC LIMIT 1",
            (since,),
        ).fetchone()
    except Exception:
        return empty
    finally:
        conn.close()
    if count <= 0 or not last:
        return empty
    last_at = last[0] or ""
    summary = (last[1] or "").replace("\n", " ").strip()[:120]
    wake = f", wake: {summary}" if summary else ""
    banner = (
        f"COA ran {count} Lifeline cycle(s) since the last IDE session "
        f"(last {last_at}{wake}). Refresh tasks and messages before acting."
    )
    return {"count": count, "last_at": last_at, "summary": summary, "banner": banner}


def _last_off_sql(iso_z):
    """SQLite datetime() wants 'YYYY-MM-DD HH:MM:SS'."""
    return iso_z.replace("T", " ").replace("Z", "")


def _status_payload(name, host, gap, row=None):
    row = row or _agent_row(name)
    status = (row["status"] if row else "") or ""
    on = status == IDE_STATUS
    msg = "IDE mode on." if on else "IDE mode off."
    if on and gap.get("banner"):
        msg = f"IDE mode on. {gap['banner']}"
    return {
        "agent": name,
        "status": status,
        "mode": "on" if on else "off",
        "message": msg,
        "status_message": (row["status_message"] if row else None),
        "ide_file": str(IDE_FILE) if IDE_FILE.is_file() else "",
        "workspace": str(COA_ENV),
        "host_class": host.get("host_class"),
        "host_virt": host.get("host_virt"),
        "autonomous_cycles": gap.get("count") or 0,
        "ssh_user": SSH_USER,
        "ssh_host": SSH_HOST,
        "ssh_port": SSH_PORT,
    }


def _provision_ssh():
    if not shutil.which("sshd") and not Path("/usr/sbin/sshd").exists():
        return (
            "openssh-server is not installed. "
            "Install it with: sudo apt-get install -y openssh-server"
        )
    KEY_DIR.mkdir(parents=True, exist_ok=True)
    os.chmod(KEY_DIR, 0o750)
    if not KEY_PRIV.is_file():
        subprocess.run(
            [
                "ssh-keygen",
                "-t",
                "ed25519",
                "-f",
                str(KEY_PRIV),
                "-N",
                "",
                "-C",
                "versa-agi-ide",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        os.chmod(KEY_PRIV, 0o600)
        if KEY_PUB.is_file():
            os.chmod(KEY_PUB, 0o644)
    SSHD_KEYS_DIR.mkdir(parents=True, exist_ok=True)
    os.chmod(SSHD_KEYS_DIR, 0o755)
    pub = KEY_PUB.read_text(encoding="utf-8").strip() + "\n"
    if SSHD_AUTH_KEYS.is_file():
        existing = SSHD_AUTH_KEYS.read_text(encoding="utf-8")
        if pub.strip() not in existing:
            with SSHD_AUTH_KEYS.open("a", encoding="utf-8") as f:
                f.write(pub)
    else:
        SSHD_AUTH_KEYS.write_text(pub, encoding="utf-8")
    os.chmod(SSHD_AUTH_KEYS, 0o644)
    if not SSHD_DROPIN.is_file() or SSHD_DROPIN.read_text(encoding="utf-8") != SSHD_DROPIN_BODY:
        prior = SSHD_DROPIN.read_text(encoding="utf-8") if SSHD_DROPIN.is_file() else None
        SSHD_DROPIN.write_text(SSHD_DROPIN_BODY, encoding="utf-8")
        os.chmod(SSHD_DROPIN, 0o644)
        # Never reload an sshd config we have not validated — a bad drop-in can
        # lock the operator out of the host.
        bad = _sshd_config_invalid()
        if bad:
            if prior is None:
                SSHD_DROPIN.unlink(missing_ok=True)
            else:
                SSHD_DROPIN.write_text(prior, encoding="utf-8")
            return f"Refusing to reload sshd — drop-in failed validation: {bad}"
        reload_err = _reload_sshd()
        if reload_err:
            return reload_err
    if not _sshd_running():
        return (
            "sshd is not running. Start it with: sudo service ssh start "
            "(or enable systemd: sudo systemctl enable --now ssh)"
        )
    return None


def _ssh_probe():
    """Non-fatal: does the IDE key actually authenticate as coa over loopback?

    Runs as root with the root-side private key so it works regardless of
    whether the PU copy landed. Surfaces auth/sshd breakage at the CLI instead
    of inside the IDE, where the error is opaque.
    """
    if not KEY_PRIV.is_file() or not shutil.which("ssh"):
        return "skipped"
    try:
        proc = subprocess.run(
            [
                "ssh",
                "-i", str(KEY_PRIV),
                "-o", "BatchMode=yes",
                "-o", "IdentitiesOnly=yes",
                "-o", "StrictHostKeyChecking=no",
                "-o", "UserKnownHostsFile=/dev/null",
                "-o", "ConnectTimeout=5",
                "-p", str(SSH_PORT),
                f"{SSH_USER}@{SSH_HOST}",
                "true",
            ],
            capture_output=True,
            text=True,
            timeout=20,
        )
    except (OSError, subprocess.TimeoutExpired):
        return "skipped"
    if proc.returncode == 0:
        return "ok"
    return "failed: " + ((proc.stderr or "").strip().splitlines() or ["unknown"])[-1][:200]


def _sshd_config_invalid():
    """Return an error string if `sshd -t` rejects the current config."""
    sshd = shutil.which("sshd") or "/usr/sbin/sshd"
    try:
        proc = subprocess.run(
            [sshd, "-t"], capture_output=True, text=True, timeout=15
        )
    except (OSError, subprocess.TimeoutExpired):
        return None  # cannot validate; do not block on it
    if proc.returncode == 0:
        return None
    return (proc.stderr or proc.stdout or "").strip()[-400:] or "sshd -t failed"


def _reload_sshd():
    for cmd in (
        ["systemctl", "reload", "ssh"],
        ["systemctl", "reload", "sshd"],
        ["service", "ssh", "reload"],
    ):
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        except (OSError, subprocess.TimeoutExpired):
            continue
        if proc.returncode == 0:
            return None
    return "Wrote sshd drop-in but could not reload sshd. Reload it and retry."


def _sshd_running():
    try:
        proc = subprocess.run(
            ["pgrep", "-x", "sshd"],
            capture_output=True,
            timeout=5,
        )
        return proc.returncode == 0
    except OSError:
        return False


def _run_lifeline_generate(name, banner):
    if not LIFELINE.is_file():
        return 1, f"Lifeline not found: {LIFELINE}"
    env = os.environ.copy()
    env["VERSA_IDE_GAP_BANNER"] = banner
    env["PATH"] = "/usr/local/bin:/usr/bin:/bin:" + env.get("PATH", "")
    try:
        proc = subprocess.run(
            [
                "sudo", "-u", "watchdog",
                "env",
                f"VERSA_IDE_GAP_BANNER={banner}",
                f"PATH={env.get('PATH', '/usr/local/bin:/usr/bin:/bin')}",
                str(LIFELINE),
                "--ide-prompt",
                name,
            ],
            capture_output=True,
            text=True,
            timeout=180,
        )
    except subprocess.TimeoutExpired:
        return 1, "lifeline --ide-prompt timed out"
    except OSError as e:
        return 1, str(e)
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip()[-800:]
        return proc.returncode, err
    return 0, ""


def _pu_home():
    user = os.environ.get("SUDO_USER") or os.environ.get("USER")
    if not user or user in ("root", "watchdog", "coa"):
        return None
    try:
        return Path(pwd.getpwnam(user).pw_dir)
    except KeyError:
        return None


def _pu_identity_path():
    home = _pu_home()
    if home:
        return str(home / ".ssh" / "versa-coa")
    return str(KEY_PRIV)


def _maybe_install_pu_key(host, identity):
    if not write_ssh_config_locally(host["host_class"], host["host_virt"]):
        return
    dest = Path(identity)
    if dest.exists() or not KEY_PRIV.is_file():
        return
    home = _pu_home()
    if not home:
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(KEY_PRIV, dest)
    os.chmod(dest, 0o600)
    try:
        pw = pwd.getpwnam(os.environ.get("SUDO_USER", ""))
        os.chown(dest, pw.pw_uid, pw.pw_gid)
        os.chown(dest.parent, pw.pw_uid, pw.pw_gid)
    except (KeyError, OSError):
        pass


def _write_pu_ssh_config(block):
    home = _pu_home()
    if not home:
        return False
    cfg = home / ".ssh" / "config"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    existing = cfg.read_text(encoding="utf-8") if cfg.is_file() else ""
    if f"Host {HOST_ALIAS}" in existing:
        return False
    with cfg.open("a", encoding="utf-8") as f:
        if existing and not existing.endswith("\n"):
            f.write("\n")
        f.write("\n" + block)
    try:
        os.chmod(cfg, 0o600)
        pw = pwd.getpwnam(os.environ.get("SUDO_USER", ""))
        os.chown(cfg, pw.pw_uid, pw.pw_gid)
    except (KeyError, OSError):
        pass
    return True


def _uid(name):
    return pwd.getpwnam(name).pw_uid


def _gid(name):
    return pwd.getpwnam(name).pw_gid
