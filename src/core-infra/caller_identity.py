"""Who is calling agictl.

agictl runs as watchdog (or root). The account that asked for it is recorded
by sudo itself in SUDO_USER, which the caller cannot set: the sudo rule for
agents carries no SETENV, so nothing a caller puts on the command line or in
its environment reaches SUDO_USER.

Every guard in agictl reads AGICTL_AGENT_USER ("which account is asking; unset
means the system or the Primary User"). The wrapper used to be the only thing
that set it, so calling the inner binary directly, or with a different value,
passed as the Primary User. settle() runs before anything reads it and makes
it true: for a caller that came through sudo it is that caller, whatever was
passed.
"""

from __future__ import annotations

import os
import sqlite3

PATHS_ENV_FILE = "/etc/versa-agi/paths.env"

# A caller-chosen database path would point agictl at a database of the
# caller's own making.
DB_PATH_VARS = (
    "AGICTL_DB",
    "AGICTL_AGENTS_DB",
    "AGICTL_MESSAGES_DB",
    "AGICTL_TASKS_DB",
    "AGICTL_CYCLES_DB",
)

# What watchdog may run as root through its sudo rule. Each entry is the
# start of the command line. Everything else is refused: in particular
# `execute`, which would run a script as root.
#   agent deploy-skills / share-skill   Lifeline, when a skill is ready
#   system config apply-coa-sudoers     COA's sudo grant, written or removed
#   system config set-ini coa autonomous   the same switch, from VersaVoice
#   system sync-instance                the instance mirror after that switch
#   system set-key                      COA sets a search key
#   pkg apply                           installing a package the owner approved
#   model                               a client instance working its GPU host
ROOT_FOR_WATCHDOG = (
    ("agent", "deploy-skills"),
    ("agent", "share-skill"),
    ("system", "config", "apply-coa-sudoers"),
    ("system", "config", "set-ini", "coa", "autonomous"),
    ("system", "sync-instance"),
    ("system", "set-key"),
    ("pkg", "apply"),
    ("model",),
)


def _paths_env_value(key: str, path: str = PATHS_ENV_FILE) -> str:
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line.startswith(f"{key}="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    except OSError:
        pass
    return ""


def watchdog_account(path: str = PATHS_ENV_FILE) -> str:
    return _paths_env_value("VERSA_WATCHDOG_USER", path) or "watchdog"


def root_command_allowed(args: list[str]) -> bool:
    """True when watchdog may run this agictl command line as root."""
    words = tuple(args)
    return any(words[: len(prefix)] == prefix for prefix in ROOT_FOR_WATCHDOG)


def lookup_agent(os_user: str, agents_db: str) -> tuple[str, str] | None:
    """(agent name, workspace) for an agent's account, or None when the
    account is not an agent's (the Primary User's own login, for one)."""
    try:
        conn = sqlite3.connect(f"file:{agents_db}?mode=ro", uri=True, timeout=5)
        try:
            row = conn.execute(
                "SELECT name, workspace FROM agents WHERE os_user = ? LIMIT 1",
                (os_user,),
            ).fetchone()
        finally:
            conn.close()
    except sqlite3.Error:
        row = None
    if row and row[0]:
        return str(row[0]).lower(), str(row[1] or "")
    # No registry to ask (first install, a server-only host): the naming rule.
    if os_user == "coa":
        return "coa", ""
    if os_user.startswith("agi-") and len(os_user) > 4:
        return os_user[4:], ""
    return None


def _inside(path: str, folder: str) -> bool:
    if not path or not folder:
        return False
    real, base = os.path.realpath(path), os.path.realpath(folder)
    return real == base or real.startswith(base.rstrip("/") + "/")


def settle(
    environ,
    *,
    euid: int,
    running_as: str,
    args: list[str],
    agents_db: str = "/var/lib/versa-agi/agents.db",
    watchdog: str | None = None,
    lookup=lookup_agent,
) -> str | None:
    """Make the caller's identity in *environ* true. Returns an error text
    when the command must be refused, else None.

    euid, running_as: the account agictl is running as.
    args: the command line after "agictl".
    """
    sudo_user = environ.get("SUDO_USER", "")

    if euid == 0:
        # Root is trusted: the Primary User with sudo, setup, agitop, or COA
        # with the sudo grant. Watchdog reaching root through its rule is held
        # to the list above.
        wd = watchdog or watchdog_account()
        if sudo_user == wd and not root_command_allowed(args):
            return (
                "This command is not one watchdog runs as root. "
                "Run it as the Primary User: sudo agictl " + " ".join(args[:3])
            )
        return None

    if not sudo_user or sudo_user in ("root", running_as):
        # Lifeline, cron, a root shell acting as watchdog: the system itself.
        return None

    # Another account came through sudo. It is who sudo says it is.
    environ["AGICTL_AGENT_USER"] = sudo_user
    for var in DB_PATH_VARS:
        environ.pop(var, None)

    agent = lookup(sudo_user, agents_db)
    if agent:
        name, workspace = agent
        environ["VERSA_AGENT_NAME"] = name
        environ["AGICTL_CONFIG"] = f"/etc/versa-agi/{name}_config.json"
        home = workspace or os.path.expanduser(f"~{sudo_user}")
        if not _inside(environ.get("AGICTL_AGENT_DIR", ""), home):
            environ["AGICTL_AGENT_DIR"] = os.path.join(home, ".agent")
    return None
