"""Harness call mode — COA live voice call to the Primary User over GPT-Live.

GPT-Live (OpenAI) carries the spoken conversation on the phone's WebRTC link.
This module owns the harness side (state_live_voice_call.md §1.3–§1.4):

- the join flow: ring through VersaVoice, relay the phone's SDP offer to
  ``POST /v1/live/sessions``, relay the answer back;
- the sideband WebSocket (transcripts, delegations, close) on the same session;
- the messages the harness injects into the running agent for each delegation
  and at hang-up (Harness Standard §5.1 — re-invoke with a new HumanMessage).

VersaVoice signaling and the call log go through the elevated, hidden
``agictl message call-bridge`` plumbing; the harness process never touches
messages.db or the VersaVoice token.
"""

from __future__ import annotations

import json
import os
import queue
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable

LIVE_API_BASE = os.environ.get("VERSA_LIVE_API_BASE", "https://api.openai.com/v1")
LIVE_WS_BASE = os.environ.get("VERSA_LIVE_WS_BASE", "wss://api.openai.com/v1")

BRIDGE_ENV = "VERSA_CALL_BRIDGE"
BRIDGE_ENV_VALUE = "harness"
BRIDGE_TIMEOUT_SECONDS = 45
LONG_POLL_SECONDS = 25

# GPT-Live append limit is 500 tokens; stay well inside it by characters.
APPEND_MAX_CHARS = 1600
WRAP_UP_LEAD_SECONDS = 60
# Spoken progress lines (narrate_progress): not for quick answers, not back to back.
NARRATE_MIN_DELAY_SECONDS = 3
NARRATE_MIN_GAP_SECONDS = 8
CLOSE_GRACE_SECONDS = 30
CALL_TURN_STEP_CAP = 16

CALL_TOOL_NAME = "agictl_call_pu"


class LiveCallError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


# ── Transcript ────────────────────────────────────────────────────────────

@dataclass
class TranscriptSegment:
    speaker: str  # "pu" | "assistant"
    text: str = ""
    start_ms: int | None = None
    end_ms: int | None = None

    def as_dict(self) -> dict:
        return {"speaker": self.speaker, "text": self.text.strip(),
                "start_ms": self.start_ms, "end_ms": self.end_ms}


class Transcript:
    """Speaker-ordered segments built from GPT-Live transcript deltas."""

    def __init__(self):
        self._segments: list[TranscriptSegment] = []
        self._cursor = 0
        self._split_next = False
        self._lock = threading.Lock()

    def add_delta(self, speaker: str, delta: str, start_ms=None, end_ms=None) -> None:
        if not delta:
            return
        with self._lock:
            last = self._segments[-1] if self._segments else None
            if last is None or last.speaker != speaker or self._split_next:
                last = TranscriptSegment(speaker=speaker, start_ms=start_ms)
                self._segments.append(last)
                self._split_next = False
            last.text += delta
            if end_ms is not None:
                last.end_ms = end_ms

    def has_new_pu_speech(self) -> bool:
        """PU words captured after the cursor (not yet handed to the agent)."""
        with self._lock:
            return any(s.speaker == "pu" and s.text.strip() for s in self._segments[self._cursor:])

    def since_cursor(self) -> list[dict]:
        """Segments not yet handed to the agent; later speech starts a new segment."""
        with self._lock:
            new = [s.as_dict() for s in self._segments[self._cursor:] if s.text.strip()]
            self._cursor = len(self._segments)
            self._split_next = True
            return new

    def all(self) -> list[dict]:
        with self._lock:
            return [s.as_dict() for s in self._segments if s.text.strip()]


def format_segments(segments: list[dict], pu_label: str = "PU", agent_label: str = "You (voice)") -> str:
    lines = []
    for seg in segments:
        text = (seg.get("text") or "").strip()
        if text:
            lines.append(f"{pu_label if seg.get('speaker') == 'pu' else agent_label}: {text}")
    return "\n".join(lines) if lines else "(no new speech captured yet)"


def clip_for_speech(text: str, limit: int = APPEND_MAX_CHARS) -> str:
    """Trim an answer to the GPT-Live append budget at a sentence boundary."""
    text = " ".join((text or "").split())
    if len(text) <= limit:
        return text
    cut = text[:limit]
    for mark in (". ", "! ", "? "):
        idx = cut.rfind(mark)
        if idx > limit * 0.5:
            return cut[: idx + 1]
    return cut.rstrip() + "…"


def message_text(content: Any) -> str:
    """Plain text of a LangChain message content (str or content-part list)."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for part in content:
            if isinstance(part, str):
                parts.append(part)
            elif isinstance(part, dict) and part.get("type") == "text":
                parts.append(str(part.get("text") or ""))
        return "\n".join(p for p in parts if p)
    return str(content or "")


# ── Language (state_live_voice_call.md §3.4 P2-B) ──────────────────────────

LANGUAGES_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "live_call_languages.json")


@dataclass(frozen=True)
class CallLanguage:
    code: str       # language GPT-Live is told to speak
    name: str
    coverage: str   # voice | supported | english | unknown
    requested: str  # the PU's own VersaVoice language name


ENGLISH = CallLanguage("en", "English", "voice", "English")


def load_language_map(path: str = LANGUAGES_PATH) -> dict[str, dict]:
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle).get("languages", {})
    except (OSError, json.JSONDecodeError):
        return {}


def resolve_call_language(spoken_language: str, table: dict[str, dict] | None = None) -> CallLanguage:
    """The PU's VersaVoice ``spokenLanguage`` (``code|Name``) → what the call speaks.

    Languages the map marks ``english`` (or does not know) fall back to English.
    """
    table = load_language_map() if table is None else table
    raw = (spoken_language or "").strip()
    code = raw.split("|")[0].strip().lower()
    row = table.get(code)
    if not row:
        name = raw.split("|")[1].strip() if "|" in raw else (code or "English")
        return CallLanguage("en", "English", "unknown", name if code not in ("", "en", "auto") else "English")
    if row.get("coverage") in ("voice", "supported"):
        return CallLanguage(code, row.get("name") or code, row["coverage"], row.get("name") or code)
    return CallLanguage("en", "English", "english", row.get("name") or code)


def language_rule(lang: CallLanguage) -> str:
    if lang.code != "en":
        return f"Speak {lang.name} unless the Primary User asks to switch."
    if lang.requested and lang.requested != "English":
        return (f"Speak English. Calls are not available in {lang.requested} yet; if they ask, "
                "say so briefly and continue in English.")
    return "Speak English unless the Primary User asks to switch."


# ── Prompts ───────────────────────────────────────────────────────────────

def voice_instructions(agent_label: str, pu_name: str, reason: str, *,
                       language: CallLanguage = ENGLISH, style_notes: str = "") -> str:
    """GPT-Live voice card (§3.4 P2-D): identity, style, delegation policy, guardrails, language.

    COA's full poise stays with the harness, which answers every delegation.
    """
    who = pu_name or "the Primary User"
    style = "Style: brief, warm, natural spoken turns. Do not read out IDs or lists unless asked."
    if style_notes:
        style += f" {who}'s preferences: {style_notes}"
    return "\n".join([
        f"You are the voice of {agent_label}, the Chief Orchestrator Agent (COA) of {who}'s "
        "Versa AGi system — a precise, capable colleague, not a simulated personality.",
        f"You placed this call to {who}. Reason: {reason}",
        f"Open by greeting {who} by name and saying in one sentence why you called, then listen.",
        style,
        "",
        "Delegation policy:",
        "Backend tools: the COA agent — projects and tasks, agents and their status, messages, "
        "memory, schedules, and system status.",
        "Delegate to the backend when: they ask about or want to change anything in their "
        "projects, tasks, agents, messages, or system; a correction changes work already "
        "requested; the answer needs facts or careful reasoning.",
        "Do not delegate to the backend when: greeting, small talk, repeating back what they "
        "said, or asking a short clarifying question.",
        "Delegate before giving an answer that depends on backend work. While it works, say "
        "briefly that you are checking. Never guess results or say something is done before "
        "the backend confirms it.",
        "Ending the call: when the reason for the call is handled and they have nothing else, "
        "or they say goodbye, say a short goodbye and then delegate \"end the call\" to the "
        "backend. Only the backend can hang up; never say you will hang up without delegating it.",
        "",
        "Approvals cannot be given by voice. If they say yes, approve, or grant for a package, "
        "sudo access, or an agent, tell them to use that control in the VersaVoice app.",
        language_rule(language),
    ])


# Optional reply lines (D21) — never spoken as written.
STEER_MARKER = "STEER:"
NOTE_MARKER = "NOTE:"
FOLLOW_UP_MARKER = "FOLLOW UP:"
END_CALL_MARKER = "END CALL"
MAX_FOLLOW_UPS = 2
# §3.6 hang-up: close once the voice has been quiet this long (bounded waits).
HANGUP_QUIET_SECONDS = 2.0
HANGUP_SPEECH_START_SECONDS = 4.0
HANGUP_MAX_WAIT_SECONDS = 12.0
# Transcript deltas can trail the delegation event; wait this long before calling it a repeat.
REPEAT_GRACE_SECONDS = 1.5
STEER_MAX_CHARS = 300

REPLY_LINES_HELP = (
    " Optional lines at the end of your reply, each on its own line and not spoken as written: "
    f"`{NOTE_MARKER} <fact the voice should keep in mind>`; "
    f"`{STEER_MARKER} <what the voice should do next, e.g. ask whether Friday works>`; "
    f"`{FOLLOW_UP_MARKER} <work you will do right after this answer and report back on>` — "
    "for slow work you can do yourself, answer briefly now (\"one moment, I'll check\") and use "
    "FOLLOW UP instead of making them wait; "
    f"`{END_CALL_MARKER}` — when they are done or the voice asked to end the call: the call "
    "closes after the voice finishes speaking (if the voice already said goodbye, reply with "
    f"only `{END_CALL_MARKER}`)."
)

SUB_AGENT_RULE = (
    " Sub-agents are not live during a call (they run on the next Lifeline tick and reply by "
    "message): do not hand work to them and wait. If one is needed, assign the task and tell the "
    "Primary User you will report back by chat after the call."
)


@dataclass
class ParsedReply:
    spoken: str
    steer: str = ""
    note: str = ""
    follow_up: str = ""
    end_call: bool = False


def parse_reply(text: Any) -> ParsedReply:
    """Split COA's call reply into the spoken part and the optional NOTE / STEER / FOLLOW UP /
    END CALL lines."""
    spoken_lines: list[str] = []
    found = {STEER_MARKER: "", NOTE_MARKER: "", FOLLOW_UP_MARKER: ""}
    end_call = False
    for raw in message_text(text).splitlines():
        line = raw.strip().strip("`").strip()
        upper = line.upper()
        if upper.rstrip(".:!").strip() == END_CALL_MARKER:
            end_call = True
            continue
        marker = next((m for m in found if upper.startswith(m)), None)
        if marker:
            found[marker] = " ".join(line[len(marker):].split())
        else:
            spoken_lines.append(raw)
    return ParsedReply(
        spoken="\n".join(spoken_lines).strip(),
        steer=found[STEER_MARKER][:STEER_MAX_CHARS],
        note=found[NOTE_MARKER],
        follow_up=found[FOLLOW_UP_MARKER],
        end_call=end_call,
    )


def follow_up_message(task: str, segments: list[dict], reason: str) -> str:
    return (
        "📞 LIVE CALL — follow-up you promised (no new request from the Primary User).\n"
        f"Follow-up: {task}\nCall reason: {reason}\n\n"
        f"Transcript since your last answer:\n{format_segments(segments)}\n\n"
        "Do the work with your tools, then reply. Your final reply is spoken to them unprompted: "
        "open naturally (\"I've checked the build logs…\"), short, facts and status. Say something "
        "is done only after the tool result confirms it. No verbal approvals."
        + SUB_AGENT_RULE + REPLY_LINES_HELP
    )


def delegation_message(delegation_ids: list[str], segments: list[dict], reason: str,
                       narrate: bool = False, unspoken_answer: str = "") -> str:
    ids = ", ".join(delegation_ids)
    carried = (
        f"\nYour previous answer was NOT spoken, because they spoke again before it was ready: "
        f"\"{unspoken_answer}\". Use what still applies; follow their latest words.\n"
        if unspoken_answer else ""
    )
    narration = (
        " When you call a tool you may add one short, natural sentence in the same message "
        "about what you are doing (e.g. \"Let me pull up the QA schedule.\") — it is spoken "
        "while they wait. Skip it for quick lookups; never state results in it."
        if narrate else ""
    )
    return (
        f"📞 LIVE CALL — the Primary User needs something (delegation {ids}).\n"
        f"Call reason: {reason}\n\n"
        f"Transcript since your last answer:\n{format_segments(segments)}\n{carried}\n"
        "Work it with your tools, then reply. Your final reply to this message is spoken to "
        "the Primary User: short, facts and status, no Markdown or lists. Say something is done "
        "only after the tool result confirms it. A spoken \"yes\" or \"approve\" is never an "
        "approval — ask them to use the control in the VersaVoice app. Do not end the cycle "
        f"during the call.{SUB_AGENT_RULE}{narration}{REPLY_LINES_HELP}"
    )


def step_cap_message() -> str:
    return (
        "⏱️ LIVE CALL: this request has used its step allowance. Reply now in one or two spoken "
        "sentences with what you have and what you will do after the call."
    )


SUMMARY_MARKER = "CALL SUMMARY:"
SUMMARY_MAX_CHARS = 600


def call_ended_message(close_reason: str, voice_seconds: int | None, segments: list[dict],
                       language: CallLanguage = ENGLISH, pending_follow_up: str = "") -> str:
    minutes = f"{(voice_seconds or 0) / 60:.1f} min" if voice_seconds else "unknown length"
    promised = (
        f"You promised this follow-up on the call and it did not run: {pending_follow_up}. "
        "Do it now and include the result in your chat summary.\n\n"
        if pending_follow_up else ""
    )
    return (
        f"📞 LIVE CALL ENDED ({close_reason}, {minutes}, spoken in {language.name}).\n\n"
        f"Full transcript:\n{format_segments(segments, agent_label='You')}\n\n"
        f"{promised}"
        "Follow through now:\n"
        "1. Send the Primary User a short chat summary with agictl_message: what was discussed, "
        "what you did, and what comes next. If the details are long, write them to a markdown "
        "file in your workspace and attach it (`--markdown <path>`); the message itself still "
        "carries the short summary and points them to the attachment.\n"
        "2. Create a task (due date + owner) for anything agreed on the call that is not done yet.\n"
        "3. Record decisions and preferences in memory — e.g. when they want a call instead of a "
        "message goes in system memory `live_call.when_to_call`.\n"
        "4. Update your agent status.\n"
        f"End this turn with a final reply that starts with `{SUMMARY_MARKER}` followed by 2–4 "
        "sentences: what was discussed, what was decided, what happens next. That line is saved "
        "as your last-call note and shown to you in later cycles. Then continue the cycle normally."
    )


def extract_summary(text: Any) -> str:
    """Text after the last ``CALL SUMMARY:`` marker (or the whole reply), trimmed."""
    body = message_text(text)
    idx = body.upper().rfind(SUMMARY_MARKER)
    if idx >= 0:
        body = body[idx + len(SUMMARY_MARKER):]
    return clip_for_speech(body.strip().strip("`").strip(), SUMMARY_MAX_CHARS)


def last_call_line(row: dict | None) -> str:
    """One line for the latest call-log row (§3.4 P2-C), or '' when there is none."""
    if not row:
        return ""
    when = str(row.get("created_at") or "")[:16].replace("T", " ")
    secs = row.get("voice_seconds")
    length = f"{secs / 60:.1f} min" if secs else "no audio"
    summary = " ".join(str(row.get("summary") or "").split())
    tail = summary or f"reason: {row.get('reason') or '—'}"
    return f"{when} UTC ({length}, {row.get('status') or 'unknown'}) — {tail}"


def prompt_block(*, ready: bool, reasons: str, call_model: str, language: CallLanguage,
                 last_call: str, calls_per_cycle: int = 1) -> str:
    """Dynamic COA prompt section (after the LIVE SITUATION boundary, so the cached prefix holds)."""
    if ready:
        limit = f"up to {calls_per_cycle} call{'s' if calls_per_cycle != 1 else ''} per cycle"
        status = (
            "Status: ready — you can call the Primary User with the `agictl_call_pu` tool "
            f"({limit}). Load `live_call.md` before placing or planning a call, and check system "
            f"memory `live_call.when_to_call`. Call model: {call_model}. Call language: {language.name}."
        )
    else:
        status = f"Status: not ready — {reasons}. Do not offer calls."
    return (
        "\n\n---\n## ── LIVE CALL\n\n"
        f"{status}\n"
        f"Last live call: {last_call or 'none yet'}\n"
    )


CONNECTED_TOOL_RESULT = (
    "The Primary User joined — you are on a live call. End your turn now with one short line "
    "(it is not spoken). Each request from the Primary User will arrive as a new message, and "
    "your final reply to each one is spoken to them."
)


# ── Bridge (agictl call-bridge) ───────────────────────────────────────────

BridgeRunner = Callable[[list[str], str | None, int], dict]


def run_bridge(args: list[str], stdin: str | None = None, timeout: int = BRIDGE_TIMEOUT_SECONDS) -> dict:
    """Run ``agictl message call-bridge <args>``; return its JSON response."""
    env = dict(os.environ)
    env[BRIDGE_ENV] = BRIDGE_ENV_VALUE
    try:
        proc = subprocess.run(
            ["agictl", "message", "call-bridge", *args],
            input=stdin,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
        )
    except subprocess.TimeoutExpired:
        return {"success": False, "error": "call bridge timed out", "code": "bridge_timeout"}
    except OSError as exc:
        return {"success": False, "error": str(exc), "code": "bridge_missing"}
    for line in reversed((proc.stdout or "").splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                continue
    return {"success": False, "error": (proc.stderr or proc.stdout or "no response").strip()[:300],
            "code": "bridge_output"}


# ── OpenAI GPT-Live transport ─────────────────────────────────────────────

def create_live_session(api_key: str, session_config: dict, sdp_offer: str,
                        http_post: Callable | None = None) -> tuple[str, str]:
    """``POST /v1/live/sessions`` with the phone's SDP offer → (session_id, sdp_answer)."""
    body = {"session": session_config, "transport": {"type": "webrtc", "sdp": sdp_offer}}
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    url = f"{LIVE_API_BASE}/live/sessions"
    if http_post is None:
        import httpx

        resp = httpx.post(url, json=body, headers=headers, timeout=30)
        status, text = resp.status_code, resp.text
    else:
        status, text = http_post(url, body, headers)
    if status not in (200, 201):
        raise LiveCallError("session_create", f"GPT-Live session create failed ({status}): {text[:300]}")
    try:
        data = json.loads(text)
        return data["session"]["id"], data["transport"]["sdp"]
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        raise LiveCallError("session_create", f"GPT-Live session response unreadable: {exc}") from exc


def open_sideband(api_key: str, session_id: str):
    from websockets.sync.client import connect

    return connect(
        f"{LIVE_WS_BASE}/live/sessions/{session_id}/attach",
        additional_headers={"Authorization": f"Bearer {api_key}"},
        open_timeout=15,
        max_size=None,
    )


class LiveSession:
    """Sideband control of one running GPT-Live session."""

    def __init__(self, session_id: str, ws, *, max_minutes: int, clock: Callable[[], float] = time.monotonic):
        self.session_id = session_id
        self._ws = ws
        self._send_lock = threading.Lock()
        self._events: queue.Queue = queue.Queue()
        self._clock = clock
        self.started_at = clock()
        self.deadline = self.started_at + max_minutes * 60
        self.transcript = Transcript()
        self.closed = threading.Event()
        self.close_reason = ""
        self.voice_seconds: int | None = None
        self.errors: list[str] = []
        self._close_sent = False
        self._wrap_sent = False
        self._close_reason_override = ""
        self.last_output_at: float | None = None
        self._reader = threading.Thread(target=self._read_loop, name="live-call-sideband", daemon=True)
        self._reader.start()

    # outgoing
    def _send(self, event: dict) -> bool:
        event.setdefault("event_id", f"evt_{uuid.uuid4().hex[:16]}")
        with self._send_lock:
            try:
                self._ws.send(json.dumps(event))
                return True
            except Exception as exc:
                self.errors.append(f"send {event.get('type')}: {exc}")
                return False

    def instructions(self, text: str) -> bool:
        return self._send({"type": "session.instructions.append", "delegation_id": None,
                           "content": clip_for_speech(text)})

    def thinking(self, delegation_id: str | None, text: str) -> bool:
        return self._send({"type": "session.thinking.append", "delegation_id": delegation_id,
                           "content": clip_for_speech(text)})

    def commentary(self, delegation_id: str | None, text: str) -> bool:
        return self._send({"type": "session.commentary.append", "delegation_id": delegation_id,
                           "content": clip_for_speech(text)})

    def close(self) -> None:
        if self._close_sent or self.closed.is_set():
            return
        self._close_sent = True
        self._send({"type": "session.close"})

    def hang_up_after_speech(self, sleep: Callable[[float], None], expect_speech: bool) -> None:
        """COA ends the call (§3.6): close once the voice has finished speaking.

        With ``expect_speech`` (a goodbye was just sent), wait for it to start, then for
        HANGUP_QUIET_SECONDS of silence; otherwise only for the current speech to trail off.
        """
        asked = self._clock()
        while not self.closed.is_set():
            now = self._clock()
            last = self.last_output_at
            spoke_since = last is not None and last >= asked
            if now - asked >= HANGUP_MAX_WAIT_SECONDS:
                break
            if spoke_since and now - last >= HANGUP_QUIET_SECONDS:
                break
            if not spoke_since and (not expect_speech or now - asked >= HANGUP_SPEECH_START_SECONDS):
                if last is None or now - last >= HANGUP_QUIET_SECONDS:
                    break
            sleep(0.2)
        self._close_reason_override = "agent_hangup"
        self.close()

    # incoming
    def _read_loop(self) -> None:
        while not self.closed.is_set():
            try:
                raw = self._ws.recv(timeout=1)
            except TimeoutError:
                continue
            except Exception as exc:
                self._mark_closed(self.close_reason or "connection_lost", error=str(exc))
                return
            try:
                event = json.loads(raw)
            except (TypeError, json.JSONDecodeError):
                continue
            self.handle_event(event)

    def handle_event(self, event: dict) -> None:
        etype = event.get("type") or ""
        if etype == "session.input_transcript.delta":
            self.transcript.add_delta("pu", event.get("delta") or "", event.get("start_ms"), event.get("end_ms"))
        elif etype == "session.output_transcript.delta":
            self.last_output_at = self._clock()
            self.transcript.add_delta("assistant", event.get("delta") or "", event.get("start_ms"), event.get("end_ms"))
        elif etype == "session.delegation.created":
            deleg = event.get("delegation") or {}
            if deleg.get("id") and (deleg.get("target") in (None, "client")):
                self._events.put(("delegation", deleg["id"], event.get("offset_ms")))
        elif etype == "session.usage.updated":
            secs = (event.get("usage") or {}).get("seconds")
            if isinstance(secs, (int, float)):
                self.voice_seconds = int(secs)
        elif etype == "session.closed":
            secs = (event.get("usage") or {}).get("seconds")
            if isinstance(secs, (int, float)):
                self.voice_seconds = int(secs)
            self._mark_closed(event.get("reason") or "close_requested")
        elif etype == "error":
            err = event.get("error") or {}
            self.errors.append(f"{err.get('code') or err.get('type')}: {err.get('message')}")

    def _mark_closed(self, reason: str, error: str | None = None) -> None:
        if error:
            self.errors.append(error)
        if not self.closed.is_set():
            self.close_reason = self._close_reason_override or reason
            self.closed.set()
            self._events.put(("closed", reason, None))
            try:
                self._ws.close()
            except Exception:
                pass

    def enforce_duration(self) -> None:
        """Spoken wrap-up a minute before the cap, then close at the cap (rule 9)."""
        now = self._clock()
        if not self._wrap_sent and now >= self.deadline - WRAP_UP_LEAD_SECONDS:
            self._wrap_sent = True
            self.instructions(
                "About a minute is left on this call. Wrap up now: summarize what was agreed and "
                "what happens next, then say goodbye."
            )
        if now >= self.deadline:
            self.close()
        if now >= self.deadline + CLOSE_GRACE_SECONDS:
            self._mark_closed("expired")

    def has_pending_events(self) -> bool:
        return not self._events.empty()

    def wait_for_work(self, poll_seconds: float = 2.0) -> tuple[str, list[str]]:
        """Block until a delegation or the close. Returns ("delegation", ids) or ("closed", [])."""
        while True:
            self.enforce_duration()
            try:
                kind, value, _ = self._events.get(timeout=poll_seconds)
            except queue.Empty:
                continue
            if kind == "closed":
                return "closed", []
            ids = [value]
            while True:
                try:
                    k2, v2, _ = self._events.get_nowait()
                except queue.Empty:
                    break
                if k2 == "closed":
                    self._events.put((k2, v2, None))
                    break
                ids.append(v2)
            return "delegation", ids

    def finalize_close(self, wait_seconds: float = 15.0) -> None:
        """Send session.close and wait for session.closed (usage) before cleanup."""
        self.close()
        self.closed.wait(timeout=wait_seconds)
        if not self.closed.is_set():
            self._mark_closed("unconfirmed")


# ── Call runtime (shared by the call tool and the harness loop) ───────────

@dataclass
class DelegationRecord:
    delegation_id: str
    offset_ms: int | None = None
    tools: list[str] = field(default_factory=list)
    result_text: str = ""
    spoken: bool = False
    steer: str = ""
    note: str = ""
    follow_up: str = ""
    duplicate: bool = False
    superseded: bool = False
    end_call: bool = False

    def as_dict(self) -> dict:
        out = {"delegation_id": self.delegation_id, "offset_ms": self.offset_ms,
               "tools": self.tools, "result_text": self.result_text, "spoken": self.spoken}
        for key in ("steer", "note", "follow_up", "duplicate", "superseded", "end_call"):
            if getattr(self, key):
                out[key] = getattr(self, key)
        return out


class LiveCallRuntime:
    """Calls for one cycle (up to ``[live_call] calls_per_cycle``), one at a time: placed by
    the tool, driven by the harness outer loop."""

    def __init__(self, *, agent_label: str, pu_name: str, api_key_resolver: Callable[[], str],
                 bridge: BridgeRunner = run_bridge, sideband_factory: Callable = open_sideband,
                 http_post: Callable | None = None, log: Callable[[str], None] = print,
                 sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.monotonic,
                 context_provider: Callable[[], dict] | None = None):
        self.agent_label = agent_label
        self.pu_name = pu_name
        self._api_key_resolver = api_key_resolver
        self._context_provider = context_provider
        self.language = ENGLISH
        self.awaiting_summary = False
        self._bridge = bridge
        self._sideband_factory = sideband_factory
        self._http_post = http_post
        self._log = log
        self._sleep = sleep
        self._clock = clock
        self.settings = None
        self.calls_placed = 0
        self._reset_call()

    def _reset_call(self) -> None:
        """Per-call state; cleared before each call of the cycle."""
        self.call_id = ""
        self.reason = ""
        self.session: LiveSession | None = None
        self.ended_injected = False
        self.finalized = False
        self.current: list[str] = []
        self.delegations: dict[str, DelegationRecord] = {}
        self.turn_steps = 0
        self.cap_nudged = False
        self.turn_started_at = 0.0
        self.last_remark_at: float | None = None
        self.pending_follow_up = ""
        self.follow_ups_used = 0
        self.last_answer = ""
        self.unspoken_answer = ""
        self.hangup_requested = False
        self.hangup_after_speech = False

    @property
    def live(self) -> bool:
        return self.session is not None and not self.ended_injected

    # ── placing the call (inside the tool) ──
    def place(self, reason: str, settings, last_call_summary: str = "") -> dict:
        limit = max(1, int(getattr(settings, "calls_per_cycle", 1) or 1))
        if self.live:
            return {"success": False, "status": "refused", "error": "You are already on this call."}
        if self.calls_placed >= limit:
            plural = "call" if limit == 1 else "calls"
            return {"success": False, "status": "refused",
                    "error": f"{limit} {plural} per cycle already placed. Continue by chat or in a "
                             "later cycle."}
        if self.awaiting_summary:
            if not last_call_summary.strip():
                return {"success": False, "status": "refused",
                        "error": "You already called this cycle. Pass that call's summary as "
                                 "last_call_summary, then call again."}
            self.capture_summary(f"{SUMMARY_MARKER} {last_call_summary}")
        if self.calls_placed:
            self._reset_call()
        self.calls_placed += 1
        self.settings = settings
        self.reason = reason.strip()
        opened = self._bridge(["open", "--reason", self.reason], None, BRIDGE_TIMEOUT_SECONDS)
        if not opened.get("success"):
            return {"success": False, "status": "failed", "error": opened.get("error") or "call could not be placed"}
        self.call_id = opened.get("call_id") or ""
        if opened.get("status") == "offline":
            return {"success": True, "status": "offline",
                    "note": "The Primary User has no reachable device. Send a chat message instead."}

        deadline = self._clock() + settings.join_timeout_seconds
        sdp_offer = ""
        while self._clock() < deadline:
            wait = int(max(1, min(LONG_POLL_SECONDS, deadline - self._clock())))
            polled = self._bridge(["wait-offer", self.call_id, "--wait", str(wait)], None, wait + 20)
            if not polled.get("success"):
                self._end("failed", "signaling_error")
                return {"success": False, "status": "failed", "error": polled.get("error") or "call signaling failed"}
            status = polled.get("status")
            if status == "offered" and polled.get("sdp_offer"):
                sdp_offer = polled["sdp_offer"]
                break
            if status in ("declined", "missed", "ended"):
                return {"success": True, "status": status if status != "ended" else "missed",
                        "note": "The Primary User did not join. Send a chat message instead."}
        if not sdp_offer:
            self._end("missed", "join_timeout")
            return {"success": True, "status": "missed",
                    "note": "The Primary User did not join in time. Send a chat message instead."}

        api_key = self._api_key_resolver()
        if not api_key:
            self._end("failed", "no_openai_key")
            return {"success": False, "status": "failed", "error": "OpenAI API key unavailable"}
        context = self._call_context()
        self.language = context.get("language") or ENGLISH
        session_config = {
            "model": settings.voice_model,
            "instructions": voice_instructions(
                self.agent_label, self.pu_name, self.reason,
                language=self.language, style_notes=context.get("style_notes") or "",
            ),
            "delegation": {"type": "client"},
        }
        try:
            session_id, sdp_answer = create_live_session(api_key, session_config, sdp_offer, self._http_post)
        except LiveCallError as exc:
            self._end("failed", exc.code)
            return {"success": False, "status": "failed", "error": str(exc)}

        answered = self._bridge(["answer", self.call_id, "--session-id", session_id], sdp_answer, BRIDGE_TIMEOUT_SECONDS)
        if not answered.get("success"):
            self._end("failed", "answer_relay")
            return {"success": False, "status": "failed", "error": answered.get("error") or "answer relay failed"}

        try:
            ws = self._sideband_factory(api_key, session_id)
        except Exception as exc:
            self._end("failed", "sideband")
            return {"success": False, "status": "failed", "error": f"sideband attach failed: {exc}"}
        self.session = LiveSession(session_id, ws, max_minutes=settings.max_minutes, clock=self._clock)
        if context.get("last_call"):
            self.session.thinking(None, f"Your last call with {self.pu_name or 'the Primary User'}: "
                                        f"{context['last_call']}")
        self._bridge(["log", self.call_id, "--status", "live"], json.dumps(self._snapshot()), BRIDGE_TIMEOUT_SECONDS)
        self._log(f"LIVE CALL: connected call={self.call_id} session={session_id} language={self.language.code}")
        return {"success": True, "status": "connected", "call_id": self.call_id, "note": CONNECTED_TOOL_RESULT}

    # ── driving turns (harness outer loop) ──
    def next_injection(self, last_ai_text: str) -> str | None:
        """Called when the agent's turn ends. Speaks the answer, then waits for the next input.

        Returns the text of the next HumanMessage, or None when call mode is over.
        """
        if self.session is None or self.ended_injected:
            return None
        if self.current:
            self._deliver_reply(last_ai_text)
        if self.hangup_requested and not self.session.closed.is_set():
            self._log("LIVE CALL: COA is ending the call")
            self.session.hang_up_after_speech(self._sleep, expect_speech=self.hangup_after_speech)
            return self._ended_injection()

        while True:
            if (self.pending_follow_up and not self.session.closed.is_set()
                    and not self.session.has_pending_events()):
                self.follow_ups_used += 1
                fid = f"follow_up_{self.follow_ups_used}"
                self.delegations[fid] = DelegationRecord(delegation_id=fid)
                task, self.pending_follow_up = self.pending_follow_up, ""
                self._start_turn([fid])
                self._log(f"LIVE CALL: {fid} — {task[:80]}")
                return follow_up_message(task, self.session.transcript.since_cursor(), self.reason)

            kind, ids = ("closed", []) if self.session.closed.is_set() else self.session.wait_for_work()
            if kind != "delegation":
                break
            for did in ids:
                self.delegations.setdefault(did, DelegationRecord(delegation_id=did))
            if self._is_repeat(ids):
                continue
            self._start_turn(ids)
            segments = self.session.transcript.since_cursor()
            carried, self.unspoken_answer = self.unspoken_answer, ""
            self._log(f"LIVE CALL: delegation {', '.join(ids)}")
            return delegation_message(ids, segments, self.reason, narrate=self.narrate,
                                      unspoken_answer=carried)
        if self.pending_follow_up:
            self._log("LIVE CALL: call ended before the follow-up ran — reported by chat")
        return self._ended_injection()

    def _is_repeat(self, ids: list[str]) -> bool:
        """Delegation(s) with no new PU words since the last answer — answer quietly, don't re-run."""
        if not self.last_answer or self.unspoken_answer:
            return False
        for _ in range(int(REPEAT_GRACE_SECONDS / 0.1)):
            if self.session.transcript.has_new_pu_speech():
                break
            self._sleep(0.1)
        if self.session.transcript.has_new_pu_speech():
            return False
        for did in ids:
            self.session.thinking(did, f"Same request as before — already answered: {self.last_answer}")
            rec = self.delegations.get(did)
            if rec:
                rec.duplicate = True
                rec.result_text = self.last_answer
        self._log(f"LIVE CALL: repeat delegation {', '.join(ids)} — answered quietly")
        return True

    @staticmethod
    def _wire_id(record_id: str) -> str | None:
        """GPT-Live delegation id for an append; follow-ups are unprompted (null)."""
        return None if record_id.startswith("follow_up_") else record_id

    def _start_turn(self, ids: list[str]) -> None:
        self.current = ids
        self.turn_steps = 0
        self.cap_nudged = False
        self.turn_started_at = self._clock()
        self.last_remark_at = None

    def _deliver_reply(self, last_ai_text: Any) -> None:
        """Speak the reply; apply NOTE / STEER; queue FOLLOW UP (D21).

        If the PU spoke again and a new delegation is waiting, the finished answer is stale:
        it is sent quietly and carried into the next turn instead of being spoken.
        """
        reply = parse_reply(last_ai_text)
        answer = clip_for_speech(reply.spoken)
        target = self.current[-1]
        wire = self._wire_id(target)
        open_call = not self.session.closed.is_set()
        superseded = (open_call and self.session.has_pending_events()
                      and self.session.transcript.has_new_pu_speech())
        if superseded:
            if answer:
                self.session.thinking(wire, f"Earlier answer, superseded by what they just said: {answer}")
            self.unspoken_answer = answer
            reply = ParsedReply(spoken=answer, note=reply.note)
            spoken = False
        else:
            spoken = bool(answer) and open_call and self.session.commentary(wire, answer)
            if spoken:
                self.last_answer = answer
            if reply.end_call and open_call:
                self.hangup_requested = True
                self.hangup_after_speech = spoken
                if not spoken:
                    self.session.thinking(wire, "COA is ending the call now.")
        if open_call and reply.note:
            self.session.thinking(wire, reply.note)
        if open_call and reply.steer:
            self.session.instructions(f"Guidance from COA (the backend): {reply.steer}")
        if reply.follow_up:
            if self.follow_ups_used < MAX_FOLLOW_UPS:
                self.pending_follow_up = reply.follow_up
            else:
                self._log("LIVE CALL: follow-up limit reached — not queued")
        tools: list[str] = []
        for did in self.current:
            rec = self.delegations.get(did)
            if rec:
                rec.result_text = answer
                rec.spoken = spoken and did == target
                rec.superseded = superseded
                rec.steer, rec.note, rec.follow_up = reply.steer, reply.note, reply.follow_up
                rec.end_call = self.hangup_requested and did == target
                tools.extend(rec.tools)
        self._log(
            f"LIVE CALL: answered {target} ({self.turn_steps} steps; tools: "
            f"{', '.join(sorted(set(tools))) or 'none'}; spoken={spoken}"
            f"{'; superseded' if superseded else ''}"
            f"{'; steer' if reply.steer else ''}{'; follow-up queued' if self.pending_follow_up else ''}"
            f"{'; end call' if self.hangup_requested else ''})"
        )
        self.current = []
        self._snapshot_async()

    @property
    def narrate(self) -> bool:
        return bool(getattr(self.settings, "narrate_progress", False))

    def note_tool_calls(self, tool_names: list[str], remark: Any = "") -> None:
        """Record a tool step; speak COA's progress line when narration and pacing allow."""
        if not self.current or self.session is None:
            return
        for did in self.current:
            rec = self.delegations.get(did)
            if rec:
                rec.tools.extend(tool_names)
        target = self._wire_id(self.current[-1])
        line = clip_for_speech(message_text(remark), 240)
        now = self._clock()
        if (
            self.narrate and line
            and now - self.turn_started_at >= NARRATE_MIN_DELAY_SECONDS
            and (self.last_remark_at is None or now - self.last_remark_at >= NARRATE_MIN_GAP_SECONDS)
        ):
            self.last_remark_at = now
            self.session.commentary(target, line)
            return
        self.session.thinking(target, line or f"Working on it: {', '.join(tool_names)}.")

    def in_turn(self) -> bool:
        return bool(self.current) and self.session is not None

    def _ended_injection(self) -> str:
        session = self.session
        self.ended_injected = True
        if session is not None:
            session.finalize_close()
        self._finalize("ended")
        self.awaiting_summary = bool(self.call_id)
        reason = session.close_reason if session else "unknown"
        seconds = session.voice_seconds if session else None
        segments = session.transcript.all() if session else []
        self._log(f"LIVE CALL: ended ({reason}, {seconds}s)")
        pending, self.pending_follow_up = self.pending_follow_up, ""
        return call_ended_message(reason, seconds, segments, self.language, pending_follow_up=pending)

    def capture_summary(self, last_ai_text: Any) -> None:
        """Save COA's reply to the call-ended message as the call's last-call note (G16)."""
        if not self.awaiting_summary:
            return
        self.awaiting_summary = False
        summary = extract_summary(last_ai_text)
        if summary:
            self._bridge(["summary", self.call_id], summary, BRIDGE_TIMEOUT_SECONDS)
            self._log(f"LIVE CALL: summary saved ({len(summary)} chars)")

    def _call_context(self) -> dict:
        if self._context_provider is None:
            return {}
        try:
            return self._context_provider() or {}
        except Exception as exc:
            self._log(f"LIVE CALL: call context unavailable — {exc}")
            return {}

    def shutdown(self, status: str = "ended") -> None:
        """Harness exit path: close any open call and write the final record."""
        if self.session is not None and not self.session.closed.is_set():
            self.session.instructions("The call has to end now. Say a brief goodbye.")
            self.session.finalize_close()
        if self.session is not None:
            self._finalize(status)

    # ── records ──
    def _snapshot(self) -> dict:
        return {
            "transcript": self.session.transcript.all() if self.session else [],
            "delegations": [r.as_dict() for r in self.delegations.values()],
            "voice_seconds": self.session.voice_seconds if self.session else None,
        }

    def _snapshot_async(self) -> None:
        payload = json.dumps(self._snapshot())
        threading.Thread(
            target=self._bridge, args=(["log", self.call_id], payload, BRIDGE_TIMEOUT_SECONDS),
            daemon=True,
        ).start()

    def _finalize(self, status: str) -> None:
        if self.finalized or not self.call_id:
            return
        self.finalized = True
        session = self.session
        close_reason = (session.close_reason if session else "") or "unconfirmed"
        final_status = status if status in ("ended", "failed") else "ended"
        args = ["end", self.call_id, "--status", final_status, "--close-reason", close_reason]
        if session and session.voice_seconds is not None:
            args += ["--voice-seconds", str(session.voice_seconds)]
        self._bridge(args, json.dumps(self._snapshot()), BRIDGE_TIMEOUT_SECONDS)

    def _end(self, status: str, reason: str) -> None:
        self.finalized = True
        if self.call_id:
            self._bridge(["end", self.call_id, "--status", status, "--close-reason", reason], None, BRIDGE_TIMEOUT_SECONDS)


def sanitize_for_call_model(messages: list) -> list:
    """Text-only copies for the call model's LLM input (checkpoint untouched).

    The cycle and call models may be different providers; image parts and
    provider reasoning blocks from the cycle model do not carry across (§1.7).
    """
    out = []
    for msg in messages:
        content = getattr(msg, "content", None)
        if isinstance(content, list):
            parts = []
            for part in content:
                if isinstance(part, str):
                    parts.append({"type": "text", "text": part})
                elif isinstance(part, dict):
                    ptype = part.get("type")
                    if ptype == "text":
                        parts.append({"type": "text", "text": str(part.get("text") or "")})
                    elif ptype in ("image_url", "image", "media", "video", "audio", "file"):
                        parts.append({"type": "text", "text": "[media omitted during call]"})
            text = "\n".join(p["text"] for p in parts if p.get("text"))
            try:
                msg = msg.model_copy(update={"content": text})
            except AttributeError:
                pass
        out.append(msg)
    return out
