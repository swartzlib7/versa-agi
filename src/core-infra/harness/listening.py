"""Listening window: keep a cycle open while people chat (state_live_chat.md §1.4).

Lifeline passes ``--listen-minutes`` (what VersaVoice granted; 0 = one-shot
cycle). The window opens the first time the cycle handles a message from a
person: the Primary User, or a VersaVoice sender who is not one of this
instance's agents (``VERSA_AGENT_IDS``). Agent-to-agent mail never opens it.

While open, the harness waits for new mail between turns instead of ending
the cycle. Idle time resets on every person message injected and on every
message the agent sends. The window closes when nobody has written for the
granted minutes, or when Lifeline's hard stop is near; the agent then gets one
closing message and ends the cycle.

``VERSA_LISTENING`` (``open`` / ``closing``) and ``VERSA_LISTEN_MINUTES`` are
set in this process's environment so ``agictl cycle end`` (a child process)
answers positively while the window is open.
"""

from __future__ import annotations

import os
import time
from typing import Callable, Iterable

WAIT_POLL_SECONDS = 3.0
# How often the listening wait looks at the mail signal file.
SIGNAL_CHECK_SECONDS = 0.25
# Close the window this long before the time limit: the last time warning
# (state_long_cycles.md §1.7), so `cycle end` is accepted from then on. The
# harness passes the warning's real lead, which is longer for slow steps.
CLOSE_LEAD_SECONDS = 480
ENV_STATE = "VERSA_LISTENING"
ENV_MINUTES = "VERSA_LISTEN_MINUTES"


def agent_ids_from_env(env=None) -> set[str]:
    """This instance's agent sub-account ids and names (Lifeline export)."""
    raw = (env if env is not None else os.environ).get("VERSA_AGENT_IDS", "")
    return {part.strip() for part in raw.split(",") if part.strip()}


def is_person(row: dict, pu_ids: set[str], agent_ids: set[str]) -> bool:
    """A message from the Primary User, or from a VersaVoice user who is not our agent."""
    sender = str(row.get("from_user_id") or "").strip()
    if not sender:
        return False
    if sender in pu_ids:
        return True
    if (row.get("channel") or "vv") != "vv":
        return False  # internal mail from anyone but the PU is agent-to-agent
    return sender not in agent_ids


def conversations_block(pu_first_name: str, coa: bool = False, minutes: int = 0) -> str:
    """Fixed CONVERSATIONS rules for the system prompt (§1.6). Byte-stable per install.

    ``coa`` adds rule 7 (D23): when a chat closes and where the instructions
    for changing the time limit are. ``minutes`` is the listening time granted.
    """
    pu = (pu_first_name or "").strip() or "the Primary User"
    rule_7 = ""
    if coa and minutes > 0:
        rule_7 = (
            f"7. This chat closes when nobody has written for {int(minutes)} minutes, or a few minutes "
            f"before your time limit. When {pu} asks how long a chat can last, or asks you to change "
            "it, load the skill `versa_agi_operations_guide.md` (Live chat) and follow it.\n"
        )
    return (
        "\n\n## ── CONVERSATIONS ──\n"
        "1. When a person writes to you, your cycle stays open for a while after you reply, "
        "so they can keep chatting. Reply promptly and keep replies short enough for a chat.\n"
        "2. More than one person may write while you are working. Keep each conversation "
        "separate and always be clear who you are replying to.\n"
        f"3. The Primary User comes first. When a message from {pu} arrives while you are "
        "talking with someone else, first send that person a short, friendly hold note "
        f"(\"Just a second — {pu} sent me a message. I'll be right back.\"), answer {pu}, "
        "then return.\n"
        "4. Coordinate work for the Primary User with other people where it helps; share one "
        "person's matters with another only when that serves the Primary User's purpose.\n"
        "5. Tasks that fall due while you are chatting wait until the chat ends; Lifeline "
        "will wake you for them.\n"
        "6. Anything unfinished — work or conversation — can continue in your next spawn.\n"
        + rule_7 +
        "── END CONVERSATIONS ──\n"
    )


def read_close_request(path: str | None, cycle_id: str | None) -> str | None:
    """Reason from `agictl agent chat-close` for this cycle ("requested" / "shutdown"), else None.

    The file holds "<cycleId> <reason>"; a request for an earlier cycle is ignored.
    """
    if not path or not cycle_id:
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            parts = fh.read().split()
    except OSError:
        return None
    if len(parts) >= 2 and parts[0] == cycle_id and parts[1] in ("requested", "shutdown"):
        return parts[1]
    return None


def read_close_deadline(path: str | None, cycle_id: str | None) -> float | None:
    """Epoch seconds by which this cycle must be closed, when the close request names one.

    The shutdown guard writes "<cycleId> shutdown <epoch>" (state_host_services.md §1.4.1).
    """
    if not path or not cycle_id:
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            parts = fh.read().split()
    except OSError:
        return None
    if len(parts) >= 3 and parts[0] == cycle_id:
        try:
            return float(parts[2])
        except ValueError:
            return None
    return None


def shutdown_message(seconds_left: float | None = None) -> str:
    """Told once, at a safe point, when the computer is shutting down mid-cycle (§1.4.1 rule 5)."""
    left = ""
    if seconds_left is not None:
        left = f" You have about {max(10, int(round(seconds_left / 10.0)) * 10)} seconds."
    return (
        "⏻ SHUTDOWN — This computer is shutting down now. Stop the work you are doing; do not "
        "start anything new. If a person is waiting for your reply, send them one short message "
        "saying the computer is shutting down and you will carry on when it is back. Then close "
        "your cycle at once: run `agictl cycle end \"summary\" --handoff \"Open: … | Next: …\"`."
        + left +
        " Anything unfinished continues in your next spawn."
    )


def closing_message(reason: str, minutes: int) -> str:
    if reason == "deadline":
        why = "This session is close to its time limit"
    elif reason == "requested":
        why = "The Primary User closed this chat"
    elif reason == "shutdown":
        why = "This computer is shutting down"
    else:
        why = f"The chat has been quiet for {minutes} minutes"
    return (
        f"💤 LIVE CHAT CLOSED — {why}, so the listening window is now closed. Close your cycle "
        "now: run `agictl cycle end \"summary\" --handoff \"Open: … | Next: …\"`. You do not need "
        "to message anyone about it. Anything unfinished continues in your next spawn; a new "
        "message wakes you again."
    )


class ListeningWindow:
    def __init__(self, minutes: int, deadline_epoch: float | None = None,
                 pu_ids: Iterable[str] = (), agent_ids: Iterable[str] = (),
                 clock: Callable[[], float] = time.time, env=None,
                 close_file: str | None = None, cycle_id: str | None = None,
                 close_lead: Callable[[], float] | None = None):
        try:
            self.minutes = max(0, int(minutes or 0))
        except (TypeError, ValueError):
            self.minutes = 0
        self.deadline = float(deadline_epoch) if deadline_epoch else None
        self.pu_ids = {p for p in pu_ids if p}
        self.agent_ids = {a for a in agent_ids if a}
        self._clock = clock
        self._env = env if env is not None else os.environ
        self.close_file = close_file
        self.cycle_id = cycle_id
        self._close_lead = close_lead
        self.opened = False
        self.closing = False
        self.last_activity: float | None = None
        self.turns = 0

    @property
    def enabled(self) -> bool:
        return self.minutes > 0

    def _set_env(self, state: str) -> None:
        self._env[ENV_STATE] = state
        self._env[ENV_MINUTES] = str(self.minutes)

    def note_rows(self, rows: Iterable[dict]) -> bool:
        """Person rows open the window or reset idle. True when a person row was seen."""
        if not self.enabled or self.closing:
            return False
        if not any(is_person(r, self.pu_ids, self.agent_ids) for r in rows or []):
            return False
        if not self.opened:
            self.opened = True
            self._set_env("open")
        self.last_activity = self._clock()
        self.turns += 1
        return True

    def note_activity(self) -> None:
        """The agent sent a message (or a call ran): reset idle."""
        if self.opened and not self.closing:
            self.last_activity = self._clock()

    def should_wait(self) -> bool:
        return self.opened and not self.closing

    def close_reason(self) -> str | None:
        """"requested" / "shutdown" / "deadline" / "idle" when the window must close now, else None."""
        requested = read_close_request(self.close_file, self.cycle_id)
        if requested:
            return requested
        now = self._clock()
        if self.deadline is not None and self.deadline - now <= self.close_lead():
            return "deadline"
        if self.last_activity is not None and now - self.last_activity >= self.minutes * 60:
            return "idle"
        return None

    def close_lead(self) -> float:
        """Seconds before the time limit at which the window closes."""
        if self._close_lead is not None:
            try:
                return max(float(CLOSE_LEAD_SECONDS), float(self._close_lead()))
            except (TypeError, ValueError):
                pass
        return float(CLOSE_LEAD_SECONDS)

    def begin_closing(self, reason: str) -> str:
        self.closing = True
        self._set_env("closing")
        return closing_message(reason, self.minutes)


class MailSignal:
    """The file the link service rewrites when it has stored new mail for this
    agent (state_realtime_link.md RL-07). ``raised()`` is true once per rewrite."""

    def __init__(self, path: str | None):
        self.path = path or ""
        self._seen = self._stamp()

    def _stamp(self) -> int:
        if not self.path:
            return 0
        try:
            return os.stat(self.path).st_mtime_ns
        except OSError:
            return 0

    def raised(self) -> bool:
        stamp = self._stamp()
        if stamp == self._seen:
            return False
        self._seen = stamp
        return True


def wait_for_mail(window: ListeningWindow, inject: Callable[[], bool],
                  sleep: Callable[[float], None] = time.sleep,
                  poll_seconds: float = WAIT_POLL_SECONDS,
                  tick: Callable[[], object] | None = None,
                  signal: Callable[[], bool] | None = None,
                  signal_seconds: float = SIGNAL_CHECK_SECONDS) -> str:
    """Block between turns until mail is injected or the window must close.

    Returns "mail" (``inject`` fed a new turn), or the close reason
    ("idle" / "deadline"). ``inject`` is the harness's mid-cycle mail hook.
    ``tick`` runs on every poll that brought no mail (the cache keep-alive).
    ``signal`` is looked at every ``signal_seconds``; when it is true the
    poll runs at once instead of at the end of ``poll_seconds``.
    """
    while True:
        reason = window.close_reason()
        if reason:
            return reason
        if signal is None:
            sleep(poll_seconds)
        else:
            waited = 0.0
            while waited < poll_seconds:
                step = min(signal_seconds, poll_seconds - waited)
                sleep(step)
                waited += step
                if signal():
                    break
        if inject():
            return "mail"
        if tick is not None:
            tick()
