#!/usr/bin/env python3
"""Versa AGi link service (TD-LINK-001, state_realtime_link.md §3.1).

Runs as watchdog under versa-agi-link.service. It dials out to the VersaVoice
API (the Firebase Cloud Function `api`) and holds one request open (GET /agi/link/wait). The function responds the
moment one of this machine's agents has new mail. The answer is a signal, not
the message:

  agent asleep → `lifeline.sh --agent <name>`: fetches the inbox and spawns it.
  agent awake  → `agictl message sync-inbox` for it, then touch its mail signal
                 file so the listening harness reads the new mail at once.

Nothing listens for inbound connections. With the link down, Lifeline's own
fetch carries on as before. Standard library only.
"""

from __future__ import annotations

import configparser
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Callable

# The one cloud address, the same as agictl/comms.py VV_API_BASE (a test compares them).
API_BASE = "https://us-central1-versavoice-s777.cloudfunctions.net/api/v1"

AGICTL = "/usr/local/bin/agictl"
SETUP_INI = "/etc/versa-agi/setup.ini"
CONFIG_DIR = "/etc/versa-agi"
STATE_DIR = "/var/lib/versa-agi"
STATE_FILE = os.path.join(STATE_DIR, "link_state")
MESSAGES_DB = os.path.join(STATE_DIR, "messages.db")
LOG_FILE = "/var/log/versa-agi-lifeline.log"
MAIL_SIGNAL_NAME = "mail_signal"

WAIT_SECONDS = 45          # how long the VersaVoice API holds one request
WAIT_MARGIN_SECONDS = 20   # extra time before the request is given up
AGENT_REFRESH_SECONDS = 60
OFF_RECHECK_SECONDS = 60
MAX_AGENTS = 20            # the API's limit per request
SYNC_TIMEOUT_SECONDS = 180
LIFELINE_TIMEOUT_SECONDS = 180

# Retry schedule after a failed request, as the tunnel runner does it: once
# after 5 seconds, then every minute for 30 minutes, then every 30 minutes.
RETRY_FIRST_SECONDS = 5
RETRY_MINUTE_SECONDS = 60
RETRY_MINUTE_WINDOW = 30 * 60
RETRY_SLOW_SECONDS = 30 * 60
# The VersaVoice API does not have the route, or refuses the token: nothing a retry in a minute will change.
SLOW_STATUS = (401, 403, 404)


@dataclass(frozen=True)
class Agent:
    name: str
    os_user: str
    path: str
    sub_account: str
    token: str


class LinkError(Exception):
    """A wait request that did not give an answer. ``status`` is the HTTP status, or 0."""

    def __init__(self, message: str, status: int = 0):
        super().__init__(message)
        self.status = status


def retry_delay(failures: int, failing_for: float, status: int = 0) -> float:
    """Seconds to wait before the next request after ``failures`` in a row."""
    if status in SLOW_STATUS:
        return RETRY_SLOW_SECONDS
    if failures <= 1:
        return RETRY_FIRST_SECONDS
    if failing_for < RETRY_MINUTE_WINDOW:
        return RETRY_MINUTE_SECONDS
    return RETRY_SLOW_SECONDS


def link_enabled(setup_ini: str = SETUP_INI) -> bool:
    """Live Link is on: `[features] live_link=true` (off unless set; asked at setup,
    switched in agitop) and VersaVoice messaging itself is enabled."""
    parser = configparser.ConfigParser(interpolation=None, strict=False)
    try:
        parser.read(setup_ini, encoding="utf-8")
    except (configparser.Error, OSError):
        return False
    def flag(section: str, key: str, default: str) -> bool:
        return parser.get(section, key, fallback=default).strip().lower() == "true"
    return flag("versavoice", "enabled", "true") and flag("features", "live_link", "false")


def parse_active_agents(text: str, config_dir: str = CONFIG_DIR, skip: tuple[str, ...] = ("watchdog",)) -> list[Agent]:
    """Agents from `agictl agent get-active` (name|os_user|workspace|…) that have a
    VersaVoice sub-account and token in their config file."""
    agents: list[Agent] = []
    for line in text.splitlines():
        parts = line.strip().split("|")
        if len(parts) < 3 or not parts[0] or parts[0] in skip:
            continue
        name, os_user, path = parts[0], parts[1], parts[2]
        try:
            with open(os.path.join(config_dir, f"{name}_config.json"), encoding="utf-8") as handle:
                versavoice = (json.load(handle) or {}).get("versavoice") or {}
        except (OSError, ValueError):
            continue
        sub_account = str(versavoice.get("sub_account_id") or "").strip()
        token = str(versavoice.get("api_token") or "").strip()
        if sub_account and token:
            agents.append(Agent(name, os_user, path, sub_account, token))
    return agents


def load_agents() -> list[Agent]:
    done = subprocess.run([AGICTL, "agent", "get-active"], capture_output=True, text=True, timeout=60)
    if done.returncode != 0:
        raise LinkError("agictl agent get-active failed")
    return parse_active_agents(done.stdout)


def wait_url(sub_accounts: list[str], cursor: str, seconds: int = WAIT_SECONDS, base: str = API_BASE) -> str:
    query = {"subAccountIds": ",".join(sub_accounts), "timeout": str(seconds)}
    if cursor:
        query["since"] = cursor
    return f"{base}/agi/link/wait?{urllib.parse.urlencode(query)}"


def fetch_wait(url: str, token: str, timeout: float) -> dict:
    """One held request. Returns the answer's ``data`` ({events, cursor})."""
    request = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        raise LinkError(f"the VersaVoice API answered {error.code}", error.code) from None
    except (OSError, ValueError) as error:
        raise LinkError(f"no answer from the VersaVoice API ({type(error).__name__})") from None
    if not isinstance(body, dict) or not body.get("success") or not isinstance(body.get("data"), dict):
        raise LinkError("the VersaVoice API's answer could not be read")
    return body["data"]


def harness_running(agent: Agent) -> bool:
    """A harness process of this agent's OS user: the agent is awake."""
    done = subprocess.run(["pgrep", "-u", agent.os_user, "-f", "harness.agent_harness"],
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=20)
    return done.returncode == 0


def wake_agent(agent: Agent, lifeline: str) -> None:
    """A Lifeline pass for this one agent. Returns when the pass has ended; a
    spawned cycle carries on in the background, as with the cron pass."""
    subprocess.run([lifeline, "--agent", agent.name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                   stdin=subprocess.DEVNULL, timeout=LIFELINE_TIMEOUT_SECONDS, start_new_session=True)


def sync_agent(agent: Agent) -> None:
    """Fetch this agent's inbox. The token goes in the environment, not on the command line."""
    env = dict(os.environ, AGICTL_VV_TOKEN=agent.token, AGICTL_MESSAGES_DB=MESSAGES_DB)
    subprocess.run([AGICTL, "message", "sync-inbox", agent.os_user, "--agent-path", agent.path,
                    "--sub-account", agent.sub_account],
                   env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=SYNC_TIMEOUT_SECONDS)


def touch_mail_signal(agent: Agent, state_dir: str = STATE_DIR) -> None:
    """Rewrite the agent's mail signal file; the listening harness watches its time."""
    path = os.path.join(state_dir, agent.name, MAIL_SIGNAL_NAME)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(f"{time.time_ns()}\n")
    os.chmod(path, 0o644)


def write_state(state: str, detail: str = "", path: str = STATE_FILE) -> None:
    """`up` / `down` / `off`, the time, and a short reason. Lifeline reads the
    first word and the file's time to know whether the link is carrying mail."""
    folder = os.path.dirname(path)
    handle, temp = tempfile.mkstemp(prefix=".link_state.", dir=folder)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as out:
            out.write(f"{state} {int(time.time())} {detail}".rstrip() + "\n")
        os.chmod(temp, 0o644)
        os.replace(temp, path)
    except OSError:
        try:
            os.unlink(temp)
        except OSError:
            pass
        raise


def safe_state(state: Callable[[str, str], None], name: str, detail: str) -> None:
    """A state file that cannot be written must not stop the link."""
    try:
        state(name, detail)
    except OSError:
        pass


def log_line(message: str, path: str = LOG_FILE) -> None:
    stamp = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    line = f"[{stamp}] [LINK] {message}"
    print(line, flush=True)
    try:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(line + "\n")
    except OSError:
        pass


class Deliveries:
    """One delivery at a time per agent, off the wait loop. A signal that arrives
    while an agent's delivery is running makes it run once more afterwards."""

    def __init__(self, deliver: Callable[[Agent], None]):
        self._deliver = deliver
        self._lock = threading.Lock()
        self._running: set[str] = set()
        self._again: dict[str, Agent] = {}

    def signal(self, agent: Agent) -> None:
        with self._lock:
            if agent.name in self._running:
                self._again[agent.name] = agent
                return
            self._running.add(agent.name)
        threading.Thread(target=self._run, args=(agent,), daemon=True, name=f"deliver-{agent.name}").start()

    def _run(self, agent: Agent) -> None:
        while True:
            try:
                self._deliver(agent)
            except Exception as error:  # a failed delivery must not stop the service
                log_line(f"{agent.name} — delivery failed ({type(error).__name__}); Lifeline's own fetch will bring the mail")
            with self._lock:
                again = self._again.pop(agent.name, None)
                if again is None:
                    self._running.discard(agent.name)
                    return
                agent = again

    def idle(self) -> bool:
        with self._lock:
            return not self._running


class LinkService:
    """The wait loop for the agents that share one token (one Primary User)."""

    def __init__(self, lifeline: str, *,
                 fetch: Callable[[str, str, float], dict] = fetch_wait,
                 running: Callable[[Agent], bool] = harness_running,
                 wake: Callable[[Agent, str], None] = wake_agent,
                 sync: Callable[[Agent], None] = sync_agent,
                 touch: Callable[[Agent], None] = touch_mail_signal,
                 state: Callable[[str, str], None] = write_state,
                 log: Callable[[str], None] = log_line,
                 clock: Callable[[], float] = time.monotonic):
        self.lifeline = lifeline
        self._fetch = fetch
        self._running = running
        self._wake = wake
        self._sync = sync
        self._touch = touch
        self._state = state
        self._log = log
        self._clock = clock
        self.cursor = ""
        self.failures = 0
        self.failing_since: float | None = None
        self.is_up: bool | None = None
        self.deliveries = Deliveries(self.deliver)

    def deliver(self, agent: Agent) -> None:
        """Bring new mail to one agent."""
        if not self._running(agent):
            self._log(f"{agent.name} — mail signal, waking")
            self._wake(agent, self.lifeline)
            # The pass may have found the agent held, in a cooldown, or already
            # spawned by the cron pass. Only an awake agent needs the rest.
            if not self._running(agent):
                return
        else:
            self._log(f"{agent.name} — mail signal, agent is awake")
        self._sync(agent)
        self._touch(agent)

    def wait_once(self, agents: list[Agent]) -> float:
        """One held request for these agents. Returns the seconds to pause before
        the next one: 0 after an answer, the retry delay after a failure."""
        by_sub = {agent.sub_account: agent for agent in agents}
        url = wait_url(sorted(by_sub), self.cursor)
        try:
            data = self._fetch(url, agents[0].token, WAIT_SECONDS + WAIT_MARGIN_SECONDS)
        except LinkError as error:
            now = self._clock()
            self.failures += 1
            if self.failing_since is None:
                self.failing_since = now
            delay = retry_delay(self.failures, now - self.failing_since, error.status)
            if self.is_up is not False:
                self._log(f"down — {error}. Lifeline's own fetch carries the mail. Next try in {int(delay)} s")
            self.is_up = False
            safe_state(self._state, "down", str(error))
            return delay
        if self.is_up is not True:
            self._log(f"up — {len(agents)} agent(s) on the link")
        self.is_up = True
        self.failures = 0
        self.failing_since = None
        safe_state(self._state, "up", f"{len(agents)} agents")
        cursor = data.get("cursor")
        if isinstance(cursor, str) and cursor:
            self.cursor = cursor
        signalled: dict[str, Agent] = {}
        for event in data.get("events") or []:
            if isinstance(event, dict) and event.get("kind") == "message":
                agent = by_sub.get(str(event.get("subAccountId") or ""))
                if agent is not None:
                    signalled[agent.name] = agent
        for agent in signalled.values():
            self.deliveries.signal(agent)
        return 0.0


# ── Reply drafts (state_realtime_link.md §2.2) ─────────────────────────────
# PARKED, 2026-11-18 (Primary User): the reply shown while it is written is switched off
# (`[features] reply_drafts=false`). A reply arrives whole, for every model, and the
# VersaVoice app may animate it. This code is kept on purpose for a later use where a
# long answer should be readable while it is written. Do not remove it.
# Design and state: state_realtime_link.md §2.2 and D14.
#
# The harness keeps the reply it is writing in <agent>/cycles/reply_draft. This
# thread passes each change to the VersaVoice API, which stores it in one
# Firestore document the app listens to. A draft is a courtesy: every failure
# is dropped, and nothing here touches the message itself.
#
# The file belongs to the agent's account, so it is read as untrusted input.

DRAFT_POLL_SECONDS = 0.3
DRAFT_POST_GAP_SECONDS = 1.0        # at most one request a second per agent
DRAFT_KEEPALIVE_SECONDS = 8.0       # an open draft is confirmed while its message is being sent
DRAFT_MAX_OPEN_SECONDS = 120.0      # an open draft nobody has touched for this long is closed
DRAFT_MAX_FILE_BYTES = 65536
DRAFT_MAX_TEXT_CHARS = 8000
DRAFT_HOLD_SECONDS = 30 * 60        # the API has no draft route, or refuses the token
_PLAIN_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


def drafts_enabled(setup_ini: str = SETUP_INI) -> bool:
    """`[features] reply_drafts=true`. Off unless set: the feature is parked."""
    parser = configparser.ConfigParser(interpolation=None, strict=False)
    try:
        parser.read(setup_ini, encoding="utf-8")
    except (configparser.Error, OSError):
        return False
    return parser.get("features", "reply_drafts", fallback="false").strip().lower() == "true"


def draft_path(agent: Agent, state_dir: str = STATE_DIR) -> str:
    return os.path.join(state_dir, agent.name, "cycles", "reply_draft")


def read_draft(path: str) -> dict | None:
    """The draft record in ``path``, checked, or None when it cannot be used."""
    try:
        if os.path.getsize(path) > DRAFT_MAX_FILE_BYTES:
            return None
        with open(path, encoding="utf-8") as handle:
            record = json.load(handle)
    except (OSError, ValueError):
        return None
    if not isinstance(record, dict) or record.get("v") != 1:
        return None
    done = record.get("done") is True
    to, text, cycle = record.get("to"), record.get("text"), record.get("cycle")
    draft, seq = record.get("draft"), record.get("seq")
    if not isinstance(text, str) or not isinstance(to, str):
        return None
    if type(draft) is not int or type(seq) is not int or draft < 0 or seq < 0:
        return None
    if not done and (not _PLAIN_ID.match(to) or not text.strip()):
        return None
    if not isinstance(cycle, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{0,80}", cycle):
        cycle = ""
    return {"to": to if _PLAIN_ID.match(to) else "", "text": text[:DRAFT_MAX_TEXT_CHARS],
            "draft": draft, "seq": seq, "done": done, "cycle": cycle}


def post_draft(agent: Agent, to: str, record: dict, timeout: float = 10) -> int:
    """Send one draft state to the VersaVoice API. Returns the HTTP status, 0 for no answer."""
    body = json.dumps({"subAccountId": agent.sub_account, "recipientId": to, "text": record["text"],
                       "draftId": record["draft"], "seq": record["seq"], "done": record["done"],
                       "cycleId": record["cycle"]}).encode("utf-8")
    request = urllib.request.Request(
        f"{API_BASE}/agi/draft", data=body, method="POST",
        headers={"Authorization": f"Bearer {agent.token}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status
    except urllib.error.HTTPError as error:
        return error.code
    except (OSError, ValueError):
        return 0


class _DraftState:
    def __init__(self) -> None:
        self.stamp: int | None = None      # the file's time when last read
        self.latest: dict | None = None
        self.dirty = False                 # latest has not been sent
        self.posted_at = float("-inf")
        self.open_to = ""                  # recipient of the draft the API holds open


class DraftSender:
    """Passes each agent's draft file to the API. ``tick`` is one look at every agent."""

    def __init__(self, post: Callable[[Agent, str, dict], int] = post_draft,
                 read: Callable[[str], dict | None] = read_draft,
                 path_of: Callable[[Agent], str] = draft_path,
                 clock: Callable[[], float] = time.monotonic,
                 wall: Callable[[], float] = time.time):
        self._post = post
        self._read = read
        self._path_of = path_of
        self._clock = clock
        self._wall = wall
        self._states: dict[str, _DraftState] = {}
        self.hold_until = float("-inf")

    def tick(self, agents: list[Agent]) -> None:
        now = self._clock()
        if now < self.hold_until:
            return
        for agent in agents:
            try:
                self._look(agent, now)
            except Exception:
                pass  # one agent's draft must not stop the others

    def serve(self, stop: threading.Event, agents_now: Callable[[], list[Agent]]) -> None:
        while not stop.is_set():
            self.tick(agents_now())
            stop.wait(DRAFT_POLL_SECONDS)

    def _send(self, agent: Agent, state: _DraftState, to: str, record: dict, now: float) -> bool:
        status = self._post(agent, to, record)
        state.posted_at = now
        if status in SLOW_STATUS:
            self.hold_until = now + DRAFT_HOLD_SECONDS
        return 200 <= status < 300

    def _close(self, agent: Agent, state: _DraftState, now: float) -> None:
        if state.open_to:
            last = state.latest or {}
            self._send(agent, state, state.open_to, {
                "text": "", "draft": last.get("draft", 0), "seq": last.get("seq", 0) + 1,
                "done": True, "cycle": last.get("cycle", "")}, now)
            state.open_to = ""

    def _look(self, agent: Agent, now: float) -> None:
        state = self._states.setdefault(agent.name, _DraftState())
        path = self._path_of(agent)
        try:
            info = os.stat(path)
        except OSError:
            self._close(agent, state, now)
            state.stamp, state.latest, state.dirty = None, None, False
            return
        if info.st_mtime_ns != state.stamp:
            state.stamp = info.st_mtime_ns
            record = self._read(path)
            if record is None:
                return
            state.latest, state.dirty = record, True
        record = state.latest
        if record is None:
            return
        if record["done"] or self._wall() - info.st_mtime > DRAFT_MAX_OPEN_SECONDS:
            self._close(agent, state, now)
            state.dirty = False
            return
        if state.dirty:
            if now - state.posted_at < DRAFT_POST_GAP_SECONDS:
                return
            if state.open_to and state.open_to != record["to"]:
                self._close(agent, state, now)   # the agent moved on to another person
            if self._send(agent, state, record["to"], record, now):
                state.open_to = record["to"]
                state.dirty = False
        elif state.open_to and now - state.posted_at >= DRAFT_KEEPALIVE_SECONDS:
            self._send(agent, state, state.open_to, record, now)


def run(lifeline: str, *, stop: threading.Event | None = None,
        agents_of: Callable[[], list[Agent]] = load_agents,
        enabled: Callable[[], bool] = link_enabled,
        service_for: Callable[[str], LinkService] | None = None,
        state: Callable[[str, str], None] = write_state,
        log: Callable[[str], None] = log_line,
        clock: Callable[[], float] = time.monotonic,
        drafts: DraftSender | None = None,
        drafts_on: Callable[[], bool] = drafts_enabled) -> None:
    """Serve until ``stop`` is set. The agents of the first token found are served;
    every agent of an install shares its Primary User's token. ``drafts``, when
    given, passes reply drafts on in a thread of its own."""
    stop = stop or threading.Event()
    served: list[Agent] = []
    if drafts is not None:
        threading.Thread(target=drafts.serve, args=(stop, lambda: list(served)),
                         name="reply-drafts", daemon=True).start()
    make = service_for or (lambda path: LinkService(path))
    service: LinkService | None = None
    token = ""
    agents: list[Agent] = []
    loaded_at: float | None = None
    was_off: str | None = None
    while not stop.is_set():
        now = clock()
        if loaded_at is None or now - loaded_at >= AGENT_REFRESH_SECONDS:
            loaded_at = now
            try:
                found = agents_of() if enabled() else []
            except Exception as error:
                found = agents
                log(f"could not read the agent list ({type(error).__name__}); keeping the last one")
            if found:
                first = found[0].token
                agents = [agent for agent in found if agent.token == first][:MAX_AGENTS]
                if first != token or service is None:
                    token = first
                    service = make(lifeline)
            else:
                agents = []
            served[:] = agents if drafts is not None and drafts_on() else []   # parked: off by default
        if not agents or service is None:
            reason = "Live Link is off" if not enabled() else "no agent with a VersaVoice account"
            if was_off != reason:
                log(f"off — {reason}")
                was_off = reason
            safe_state(state, "off", reason)
            stop.wait(OFF_RECHECK_SECONDS)
            loaded_at = None
            continue
        was_off = None
        pause = service.wait_once(agents)
        if pause > 0:
            stop.wait(pause)


def main() -> int:
    core_infra = os.environ.get("VERSA_CORE_INFRA") or "/home/watchdog/core-infra"
    lifeline = os.path.join(core_infra, "lifeline.sh")
    stop = threading.Event()

    def on_stop(*_):
        # Raising here ends a held request at once instead of at its timeout.
        stop.set()
        raise SystemExit(0)

    for number in (signal.SIGTERM, signal.SIGINT):
        signal.signal(number, on_stop)
    log_line("service started")
    try:
        run(lifeline, stop=stop, drafts=DraftSender())
    except SystemExit:
        pass
    finally:
        try:
            write_state("off", "service stopped")
        except OSError:
            pass
        log_line("service stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
