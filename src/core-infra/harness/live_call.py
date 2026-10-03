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

import datetime
import json
import os
import queue
import re
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
END_CLOSE_ATTEMPTS = 3
LONG_POLL_SECONDS = 25

# GPT-Live append limit is 500 tokens; stay well inside it by characters.
APPEND_MAX_CHARS = 1600
WRAP_UP_LEAD_SECONDS = 60
# Spoken progress lines (narrate_progress): not for quick answers, not back to back.
NARRATE_MIN_DELAY_SECONDS = 3
NARRATE_MIN_GAP_SECONDS = 8
CLOSE_GRACE_SECONDS = 30
CALL_TURN_STEP_CAP = 16
# The phone can end the call (VersaVoice call document) without session.closed reaching
# the sideband: app closed, sign-out, network lost.
PHONE_STATUS_POLL_SECONDS = 10
PHONE_ENDED_STATUSES = ("ended", "declined", "missed", "failed")

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

    def mark(self) -> int:
        """Position for ``speech_after``; the next delta starts a new segment."""
        with self._lock:
            self._split_next = True
            return len(self._segments)

    def speech_after(self, mark: int) -> tuple[str, str]:
        """(voice words, PU words) captured since ``mark``."""
        with self._lock:
            later = self._segments[mark:]
            voice = " ".join(s.text.strip() for s in later if s.speaker == "assistant" and s.text.strip())
            pu = " ".join(s.text.strip() for s in later if s.speaker == "pu" and s.text.strip())
            return voice, pu


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

# Shipped beside coa_poise.md; setup copies it next to the harness library (§3.7 P3-E).
VOICE_CARD_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config", "live_call_voice.md"
)
_TEMPLATE_COMMENT = re.compile(r"<!--.*?-->\s*", re.DOTALL)


def load_voice_card(path: str | None = None) -> str:
    try:
        with open(path or VOICE_CARD_PATH, encoding="utf-8") as f:
            return _TEMPLATE_COMMENT.sub("", f.read()).strip()
    except OSError as exc:
        raise LiveCallError("voice_card", f"voice card template unavailable: {exc}") from exc


# A connection is not the Primary User. No profile, games, or home. Same delegation rules.
CONNECTION_VOICE_CARD = """
You are the voice of {AGENT}, calling {PU}.
You placed this call to {PU}. Reason: {REASON}
Open with hello and their full name, {PU}, then one sentence on why you called, and listen. Speak that name as written. Never say a placeholder such as [last name] in its place.
Style: brief, warm, natural spoken turns. Do not read out IDs or lists unless asked. Do not mention the Primary User's private details, home, or other people.{STYLE_NOTES}

Delegation policy:
Backend tools: the COA agent — projects and tasks, agents and their status, messages, memory, schedules, and system status.
Delegate to the backend when: they ask about or want to change anything that needs facts from the backend; a correction changes work already requested.
Do not delegate to the backend when: greeting, small talk, repeating back what they said, or asking a short clarifying question.
Delegate before giving an answer that depends on backend work. While it works, say briefly that you are checking. Never guess results or say something is done before the backend confirms it. When the backend sends a result to tell them, say all of it, then check briefly that it answers what they needed.
Ending the call: when the reason for the call is handled and they have nothing else, or they say goodbye, say a short goodbye and then delegate "end the call" to the backend. Only the backend can hang up; never say you will hang up without delegating it.

Approvals cannot be given by voice. If they say yes, approve, or grant for a package, sudo access, or an agent, tell them to use that control in the VersaVoice app.
{LANGUAGE_RULE}
""".strip()


def voice_instructions(agent_label: str, pu_name: str, reason: str, *,
                       language: CallLanguage = ENGLISH, style_notes: str = "",
                       template: str | None = None, connection: bool = False) -> str:
    """GPT-Live voice card: COA's purpose, duty, and stance toward the person on the call.

    A Primary User call uses the shipped card. A connection call uses a short card that
    does not carry the Primary User's profile. COA's full poise stays with the harness.
    """
    who = pu_name or ("them" if connection else "the Primary User")
    if connection:
        card = CONNECTION_VOICE_CARD
    else:
        card = template if template is not None else load_voice_card()
    values = {
        "{AGENT}": agent_label or "COA",
        "{PU}": who,
        "{REASON}": reason,
        "{STYLE_NOTES}": f" {who}'s preferences: {style_notes}" if style_notes else "",
        "{LANGUAGE_RULE}": language_rule(language),
    }
    for key, value in values.items():
        card = card.replace(key, value)
    return card


# Game posture as a speaking tone, so the voice never hears the system term (§3.7 P3-F).
POSTURE_TONE = {
    "exploratory": "curious and open",
    "steady": "calm and methodical",
    "aggressive": "brisk and decisive",
    "defensive": "calm, reassuring, and careful about risk",
}
PROFILE_MAX_CHARS = 600
GAMES_MAX = 3
ABILITIES_MAX = 10
# VersaVoice chromosome is the PU's voice setting (X male, Y female).
_VOICE_SETTING = {"x": "male voice", "y": "female voice", "reflective": "your voice"}


def _born_phrase(raw: str) -> str:
    text = raw.strip()[:10]
    try:
        day = datetime.date.fromisoformat(text)
    except ValueError:
        return f"born {raw.strip()}"
    return f"born {day.strftime('%-d %B %Y')}"


def _ability_word(level: int) -> str:
    if level >= 8:
        return "strong"
    if level >= 5:
        return "practiced"
    return "some"


def account_profile_line(pu: dict | None) -> str:
    """My Information, already synced from VersaVoice into ``primary_user`` (§3.8)."""
    pu = pu or {}
    parts = []
    born = " ".join(str(pu.get("dateOfBirth") or "").split())
    origin = " ".join(str(pu.get("countryOfBirth") or "").split())
    if born:
        phrase = _born_phrase(born)
        parts.append(f"{phrase} in {origin}" if origin else phrase)
    elif origin:
        parts.append(f"from {origin}")
    home = [str(pu.get(key) or "").strip()
            for key in ("nearestCity", "stateOrProvince", "countryOfResidence")]
    home = [" ".join(p.split()) for p in home if p]
    if home:
        parts.append("lives in " + ", ".join(home))
    voice = _VOICE_SETTING.get(str(pu.get("chromosome") or "").strip().lower())
    if voice:
        parts.append(f"voice setting: {voice}")
    ranked = []
    raw = pu.get("abilities") or []
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (TypeError, json.JSONDecodeError):
            raw = []
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        name = " ".join(str(item.get("name") or "").split())
        try:
            level = int(item.get("level") or 0)
        except (TypeError, ValueError):
            continue
        if name and 1 <= level <= 10:
            ranked.append((level, name))
    ranked.sort(key=lambda pair: (-pair[0], pair[1].lower()))
    if ranked:
        shown = ", ".join(f"{name} ({_ability_word(level)})" for level, name in ranked[:ABILITIES_MAX])
        parts.append(f"abilities: {shown}")
    return "; ".join(parts)


def call_start_context(*, pu_name: str, brief: str = "", profile: str = "",
                       games: list[dict] | None = None, last_call: str = "") -> list[str]:
    """Quiet context for the start of a call, one ``thinking.append`` per item."""
    who = pu_name or "the Primary User"
    out = []
    if brief.strip():
        out.append(clip_for_speech(f"COA's brief for this call: {brief}"))
    known = []
    if profile:
        known.append(f"What you know about {who}: {clip_for_speech(profile, PROFILE_MAX_CHARS)}")
    lines = []
    for game in (games or [])[:GAMES_MAX]:
        name = " ".join(str(game.get("name") or "").split())
        if not name:
            continue
        intent = clip_for_speech(str(game.get("postulate") or ""), 140)
        tone = POSTURE_TONE.get(str(game.get("posture") or ""), "")
        lines.append(name + (f" ({intent})" if intent else "") + (f" — tone: {tone}" if tone else ""))
    if lines:
        known.append(f"{who}'s active pursuits: " + "; ".join(lines) + ".")
    if last_call:
        known.append(f"Your last call with {who}: {last_call}")
    if known:
        out.append(clip_for_speech(" ".join(known)))
    return out


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

def reply_lines_help(follow_ups_left: int = MAX_FOLLOW_UPS) -> str:
    """Optional reply lines. With no follow-ups left, FOLLOW UP is not offered."""
    if follow_ups_left > 0:
        follow = (
            f"`{FOLLOW_UP_MARKER} <work you will do right after this answer and report back on>` — "
            "for slow work you can do yourself, answer briefly now (\"one moment, I'll check\") and "
            "use FOLLOW UP instead of making them wait; the report is told to them on this call, "
            "before any hang-up; "
        )
        limit = ""
    else:
        follow = ""
        limit = (
            " No follow-ups are left on this call: do the work now and put the result in this "
            "reply. Do not promise to get back to them on the call; anything still open goes in "
            "your chat summary after the call."
        )
    return (
        " Optional lines at the end of your reply, each on its own line and not spoken as written: "
        f"`{NOTE_MARKER} <fact the voice should keep in mind>`; "
        f"`{STEER_MARKER} <what the voice should do next, e.g. ask whether Friday works>`; "
        f"{follow}"
        f"`{END_CALL_MARKER}` — when they are done or the voice asked to end the call: the call "
        "closes after the voice finishes speaking (if the voice already said goodbye, reply with "
        f"only `{END_CALL_MARKER}`)."
        f"{limit}"
    )


REPLY_LINES_HELP = reply_lines_help()

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


def follow_up_message(task: str, segments: list[dict], reason: str,
                      follow_ups_left: int = MAX_FOLLOW_UPS, ending: bool = False,
                      delivery: str = "") -> str:
    check = f"{delivery}\n" if delivery else ""
    closing = (
        "They are ending the call. This is your last report before the hang-up: give the result "
        "now. The call closes after it is spoken. "
        if ending else ""
    )
    return (
        "📞 LIVE CALL — follow-up you promised (no new request from the Primary User).\n"
        f"Follow-up: {task}\nCall reason: {reason}\n\n"
        f"Transcript since your last answer:\n{format_segments(segments)}\n\n"
        f"{check}"
        f"{closing}"
        "Do the work with your tools, then reply. Your final reply is told to them right away, "
        "unprompted: open naturally (\"I've checked the build logs…\"), short, facts and status. "
        "This reply is the report, so give the result, not another promise. Say something is done "
        "only after the tool result confirms it. No verbal approvals."
        + SUB_AGENT_RULE + reply_lines_help(0 if ending else follow_ups_left)
    )


def report_instruction(pu_name: str, answer: str) -> str:
    """A finished follow-up report: GPT-Live says it now (instructions.append)."""
    who = pu_name or "them"
    return (
        f"COA (the backend) has the result {who} is waiting for. Tell {who} now, in your own words: "
        f"{answer} Then check briefly that it answers what they needed."
    )


DELIVERY_SPOKEN = "spoken"
DELIVERY_CUT_OFF = "cut_off"
DELIVERY_NOT_SPOKEN = "not_spoken"


def delivery_check_line(report: str, status: str, voice_said: str) -> str:
    """What happened to a report handed to the voice; COA compares the words (G25)."""
    label = {
        DELIVERY_SPOKEN: "the voice spoke after it was sent and was not interrupted",
        DELIVERY_CUT_OFF: "the voice started, and they spoke over it",
    }.get(status, "the voice said nothing after it was sent")
    said = f' The voice said: "{clip_for_speech(voice_said, 400)}".' if voice_said else ""
    return (
        f"Delivery check — your report \"{clip_for_speech(report, 400)}\": {label}.{said} "
        "Compare the voice's words with your report. If the result got through, do not repeat it. "
        "If it did not and it still answers them, restate it first in your reply.\n"
    )


def stale_answer_instruction(answer: str) -> str:
    """An answer that finished while they were still talking: say it only if it still fits."""
    return (
        "COA finished the earlier request while they were still talking. If this still answers "
        f"what they want, tell them now: {answer} If they changed or corrected the request, do not "
        "say it; COA is working on their latest words."
    )


def delegation_message(delegation_ids: list[str], segments: list[dict], reason: str,
                       narrate: bool = False, unspoken_answer: str = "",
                       follow_ups_left: int = MAX_FOLLOW_UPS, delivery: str = "") -> str:
    ids = ", ".join(delegation_ids)
    carried = (
        f"\nYour previous answer finished after they spoke again, so it was NOT spoken as your "
        f"reply. The voice was told to say it only if it still fits: \"{unspoken_answer}\". "
        "Follow their latest words.\n"
        if unspoken_answer else ""
    )
    if delivery:
        carried += f"\n{delivery}"
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
        f"during the call.{SUB_AGENT_RULE}{narration}{reply_lines_help(follow_ups_left)}"
    )


def step_cap_message() -> str:
    return (
        "⏱️ LIVE CALL: this request has used its step allowance. Reply now in one or two spoken "
        "sentences with what you have and what you will do after the call."
    )


SUMMARY_MARKER = "CALL SUMMARY:"
SUMMARY_MAX_CHARS = 600


def call_ended_message(close_reason: str, voice_seconds: int | None, segments: list[dict],
                       language: CallLanguage = ENGLISH, pending_follow_up: str = "",
                       unheard: str = "") -> str:
    minutes = f"{(voice_seconds or 0) / 60:.1f} min" if voice_seconds else "unknown length"
    promised = (
        f"You promised this follow-up on the call and it did not run: {pending_follow_up}. "
        "Do it now and include the result in your chat summary.\n\n"
        if pending_follow_up else ""
    )
    if unheard:
        promised += (
            f"This result was ready after the call closed, so they did not hear it: {unheard} "
            "Include it in your chat summary.\n\n"
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
            "Status: ready — you can call the Primary User, or a VersaVoice connection, with "
            "the `agictl_call_pu` tool "
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

    def __init__(self, session_id: str, ws, *, max_minutes: int, clock: Callable[[], float] = time.monotonic,
                 phone_status: Callable[[], str] | None = None):
        self.session_id = session_id
        self._ws = ws
        self._send_lock = threading.Lock()
        self._events: queue.Queue = queue.Queue()
        self._clock = clock
        self._phone_status = phone_status
        self.started_at = clock()
        self._phone_checked_at = self.started_at
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
        # Times the PU spoke while the voice was mid-sentence (delivery check, G25).
        self.barge_ins: list[float] = []
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

    def wait_for_speech(self, sleep: Callable[[float], None], expect_speech: bool) -> float | None:
        """Wait until the voice has finished speaking; return when it started, if it did.

        With ``expect_speech`` (something was just sent to say), wait for it to start, then for
        HANGUP_QUIET_SECONDS of silence; otherwise only for the current speech to trail off.
        """
        asked = self._clock()
        started: float | None = None
        while not self.closed.is_set():
            now = self._clock()
            last = self.last_output_at
            spoke_since = last is not None and last >= asked
            if spoke_since and started is None:
                started = last
            if now - asked >= HANGUP_MAX_WAIT_SECONDS:
                break
            if spoke_since and now - last >= HANGUP_QUIET_SECONDS:
                break
            if not spoke_since and (not expect_speech or now - asked >= HANGUP_SPEECH_START_SECONDS):
                if last is None or now - last >= HANGUP_QUIET_SECONDS:
                    break
            sleep(0.2)
        return started

    def hang_up_after_speech(self, sleep: Callable[[float], None], expect_speech: bool) -> None:
        """COA ends the call (§3.6): close once the voice has finished speaking."""
        self.wait_for_speech(sleep, expect_speech)
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
            try:
                self.handle_event(event)
            except Exception as exc:
                self.errors.append(f"event {event.get('type') if isinstance(event, dict) else '?'}: {exc}")

    def handle_event(self, event: dict) -> None:
        etype = event.get("type") or ""
        if etype == "session.input_transcript.delta":
            now = self._clock()
            if self.last_output_at is not None and now - self.last_output_at < HANGUP_QUIET_SECONDS:
                self.barge_ins.append(now)
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

    def phone_ended(self) -> bool:
        """Every PHONE_STATUS_POLL_SECONDS: if the call document is final, close the session."""
        if self._phone_status is None or self.closed.is_set():
            return False
        now = self._clock()
        if now - self._phone_checked_at < PHONE_STATUS_POLL_SECONDS:
            return False
        self._phone_checked_at = now
        try:
            status = self._phone_status()
        except Exception as exc:
            self.errors.append(f"phone status: {exc}")
            return False
        if status not in PHONE_ENDED_STATUSES:
            return False
        self._close_reason_override = self._close_reason_override or "phone_ended"
        self.close()
        return True

    def has_pending_events(self) -> bool:
        return not self._events.empty()

    def wait_for_work(self, poll_seconds: float = 2.0) -> tuple[str, list[str]]:
        """Block until a delegation or the close. Returns ("delegation", ids) or ("closed", [])."""
        while True:
            self.enforce_duration()
            if self.phone_ended():
                return "closed", []
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
    delivery: str = ""  # DELIVERY_* for a report handed to the voice

    def as_dict(self) -> dict:
        out = {"delegation_id": self.delegation_id, "offset_ms": self.offset_ms,
               "tools": self.tools, "result_text": self.result_text, "spoken": self.spoken}
        for key in ("steer", "note", "follow_up", "duplicate", "superseded", "end_call", "delivery"):
            if getattr(self, key):
                out[key] = getattr(self, key)
        return out


@dataclass
class HandedReport:
    """A result sent with ``instructions.append`` for the voice to say (G25)."""
    record_id: str
    text: str
    kind: str  # "report" (follow-up) | "stale" (superseded answer)
    mark: int
    status: str = ""
    voice_said: str = ""


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
        self.calling_connection = False
        self.callee_name = ""
        self.callee_language = ""
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
        # END CALL with a follow-up still queued: run it and speak the report first.
        self.end_after_follow_up = False
        # Promised work that cannot run on this call; the call-ended message hands it to chat.
        self.after_call: list[str] = []
        # Answers that finished after the call closed, or reports the voice never got across.
        self.unheard: list[str] = []
        # Report sent to the voice and not yet checked; then its result for COA's next message.
        self.handed: HandedReport | None = None
        self.delivery: HandedReport | None = None

    @property
    def live(self) -> bool:
        return self.session is not None and not self.ended_injected

    # ── placing the call (inside the tool) ──
    def place(self, reason: str, settings, last_call_summary: str = "", brief: str = "",
              recipient_id: str = "") -> dict:
        limit = max(1, int(getattr(settings, "calls_per_cycle", 1) or 1))
        if self.live:
            return {"success": False, "status": "refused", "error": "You are already on this call."}
        try:
            card_template = load_voice_card()
        except LiveCallError as exc:
            return {"success": False, "status": "failed", "error": str(exc)}
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
        self.calling_connection = bool(recipient_id.strip())
        self.callee_name = ""
        open_args = ["open", "--reason", self.reason]
        if self.calling_connection:
            open_args += ["--recipient", recipient_id.strip()]
        opened = self._bridge(open_args, None, BRIDGE_TIMEOUT_SECONDS)
        if not opened.get("success"):
            return {"success": False, "status": "failed",
                    "error": opened.get("error") or "call could not be placed"}
        self.call_id = opened.get("call_id") or ""
        self.callee_name = str(opened.get("callee_name") or "").strip()
        self.callee_language = str(opened.get("callee_language") or "").strip()
        if opened.get("status") == "offline":
            if self.calling_connection:
                note = "They have no reachable device. Send them a chat message instead."
            else:
                note = "The Primary User has no reachable device. Send a chat message instead."
            return {"success": True, "status": "offline", "note": note}

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
                if self.calling_connection:
                    note = "They did not join. Send them a chat message instead."
                else:
                    note = "The Primary User did not join. Send a chat message instead."
                return {"success": True, "status": status if status != "ended" else "missed",
                        "note": note}
        if not sdp_offer:
            self._end("missed", "join_timeout")
            if self.calling_connection:
                note = "They did not join in time. Send them a chat message instead."
            else:
                note = "The Primary User did not join in time. Send a chat message instead."
            return {"success": True, "status": "missed", "note": note}

        api_key = self._api_key_resolver()
        if not api_key:
            self._end("failed", "no_openai_key")
            return {"success": False, "status": "failed", "error": "OpenAI API key unavailable"}
        context = self._call_context()
        if self.calling_connection and self.callee_language:
            self.language = resolve_call_language(self.callee_language)
        else:
            self.language = context.get("language") or ENGLISH
        spoken_name = self.callee_name if self.calling_connection else self.pu_name
        session_config = {
            "model": settings.voice_model,
            "instructions": voice_instructions(
                self.agent_label, spoken_name, self.reason,
                language=self.language,
                style_notes="" if self.calling_connection else (context.get("style_notes") or ""),
                template=card_template,
                connection=self.calling_connection,
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
        self.session = LiveSession(session_id, ws, max_minutes=settings.max_minutes, clock=self._clock,
                                   phone_status=self._phone_status)
        if self.calling_connection:
            start = call_start_context(pu_name=spoken_name, brief=brief)
        else:
            start = call_start_context(
                pu_name=self.pu_name, brief=brief, profile=context.get("pu_profile") or "",
                games=context.get("games") or [], last_call=context.get("last_call") or "",
            )
        for item in start:
            self.session.thinking(None, item)
        self._bridge(["log", self.call_id, "--status", "live"], json.dumps(self._snapshot()), BRIDGE_TIMEOUT_SECONDS)
        self._log(f"LIVE CALL: connected call={self.call_id} session={session_id} language={self.language.code}")
        if self.calling_connection:
            note = ("They joined — you are on a live call. End your turn now with one short line "
                    "(it is not spoken). Each thing they ask arrives as a new message; your final "
                    "reply to it is spoken.")
        else:
            note = CONNECTED_TOOL_RESULT
        return {"success": True, "status": "connected", "call_id": self.call_id, "note": note}

    # ── driving turns (harness outer loop) ──
    def next_injection(self, last_ai_text: str) -> str | None:
        """Called when the agent's turn ends. Speaks the answer, then waits for the next input.

        Returns the text of the next HumanMessage, or None when call mode is over.
        """
        if self.session is None or self.ended_injected:
            return None
        if self.current:
            self._deliver_reply(last_ai_text)
        if self.handed is not None:
            self._check_delivery()
        if self.hangup_requested and not self.session.closed.is_set():
            self._log("LIVE CALL: COA is ending the call")
            self.session.hang_up_after_speech(self._sleep, expect_speech=self.hangup_after_speech)
            return self._ended_injection()

        while True:
            # A queued PU request runs first, unless they are ending the call.
            if (self.pending_follow_up and not self.session.closed.is_set()
                    and (self.end_after_follow_up or not self.session.has_pending_events())):
                self.follow_ups_used += 1
                fid = f"follow_up_{self.follow_ups_used}"
                self.delegations[fid] = DelegationRecord(delegation_id=fid)
                task, self.pending_follow_up = self.pending_follow_up, ""
                self._start_turn([fid])
                self._log(f"LIVE CALL: {fid} — {task[:80]}"
                          f"{' (report before hang-up)' if self.end_after_follow_up else ''}")
                return follow_up_message(
                    task, self.session.transcript.since_cursor(), self.reason,
                    follow_ups_left=MAX_FOLLOW_UPS - self.follow_ups_used,
                    ending=self.end_after_follow_up, delivery=self._take_delivery(),
                )

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
                                      unspoken_answer=carried,
                                      follow_ups_left=MAX_FOLLOW_UPS - self.follow_ups_used,
                                      delivery=self._take_delivery())
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
        """Speak the reply; apply NOTE / STEER; queue FOLLOW UP (D21, G24).

        A follow-up report goes out as ``instructions.append`` so the voice says it now.
        If the PU spoke again and a new delegation is waiting, the finished answer is handed
        to the voice to say only if it still fits, and carried into the next turn.
        ``END CALL`` with a follow-up queued waits for that report before the hang-up.
        """
        reply = parse_reply(last_ai_text)
        answer = clip_for_speech(reply.spoken)
        target = self.current[-1]
        wire = self._wire_id(target)
        is_follow_up = wire is None
        open_call = not self.session.closed.is_set()
        superseded = (open_call and self.session.has_pending_events()
                      and self.session.transcript.has_new_pu_speech())
        if superseded:
            if answer:
                mark = self.session.transcript.mark()
                if self.session.instructions(stale_answer_instruction(answer)):
                    self.handed = HandedReport(target, answer, "stale", mark)
            self.unspoken_answer = answer
            reply = ParsedReply(spoken=answer, note=reply.note)
            self.end_after_follow_up = False
            spoken = False
        elif not open_call:
            spoken = False
            if answer:
                self.unheard.append(answer)
        elif is_follow_up:
            spoken = False
            if answer:
                mark = self.session.transcript.mark()
                spoken = self.session.instructions(report_instruction(self.pu_name, answer))
                if spoken:
                    self.handed = HandedReport(target, answer, "report", mark)
                else:
                    self.unheard.append(answer)
        else:
            spoken = bool(answer) and self.session.commentary(wire, answer)
        if spoken:
            self.last_answer = answer

        if reply.follow_up:
            if self.end_after_follow_up:
                self.after_call.append(reply.follow_up)
                self._log("LIVE CALL: follow-up during the closing report — left for chat")
            elif self.follow_ups_used < MAX_FOLLOW_UPS:
                self.pending_follow_up = reply.follow_up
            else:
                self.after_call.append(reply.follow_up)
                self._log("LIVE CALL: follow-up limit reached — left for chat")

        closing_report = is_follow_up and self.end_after_follow_up
        if open_call and not superseded and (reply.end_call or closing_report):
            if self.pending_follow_up:
                self.end_after_follow_up = True
                self._log("LIVE CALL: end call after the follow-up report")
            else:
                self.end_after_follow_up = False
                self.hangup_requested = True
                self.hangup_after_speech = spoken
                if not spoken:
                    self.session.thinking(wire, "COA is ending the call now.")
        if open_call and reply.note:
            self.session.thinking(wire, reply.note)
        if open_call and reply.steer:
            self.session.instructions(f"Guidance from COA (the backend): {reply.steer}")
        tools: list[str] = []
        for did in self.current:
            rec = self.delegations.get(did)
            if rec:
                rec.result_text = answer
                rec.spoken = spoken and did == target
                rec.superseded = superseded
                rec.steer, rec.note, rec.follow_up = reply.steer, reply.note, reply.follow_up
                rec.end_call = (self.hangup_requested or self.end_after_follow_up) and did == target
                tools.extend(rec.tools)
        self._log(
            f"LIVE CALL: answered {target} ({self.turn_steps} steps; tools: "
            f"{', '.join(sorted(set(tools))) or 'none'}; spoken={spoken}"
            f"{'; superseded' if superseded else ''}"
            f"{'; steer' if reply.steer else ''}{'; follow-up queued' if self.pending_follow_up else ''}"
            f"{'; end call after report' if self.end_after_follow_up else ''}"
            f"{'; end call' if self.hangup_requested else ''})"
        )
        self.current = []
        self._snapshot_async()

    def _check_delivery(self) -> None:
        """Wait for the voice to say a handed report; mark it for COA's next message (G25).

        A follow-up report is sent once more when the voice said nothing and the PU is quiet,
        or when the call is ending. Unconfirmed reports at hang-up go to the chat summary.
        """
        h, self.handed = self.handed, None
        s = self.session
        ending = self.hangup_requested or self.end_after_follow_up
        started = s.wait_for_speech(self._sleep, expect_speech=True)
        status, voice, pu = self._delivery_status(h.mark, started)
        if (h.kind == "report" and status != DELIVERY_SPOKEN and not s.closed.is_set()
                and (ending or (status == DELIVERY_NOT_SPOKEN and not pu))):
            self._log(f"LIVE CALL: report {h.record_id} {status} — sent again")
            mark = s.transcript.mark()
            if s.instructions(report_instruction(self.pu_name, h.text)):
                started = s.wait_for_speech(self._sleep, expect_speech=True)
                status, again, _ = self._delivery_status(mark, started)
                voice = " ".join(v for v in (voice, again) if v)
        h.status, h.voice_said = status, voice
        rec = self.delegations.get(h.record_id)
        if rec:
            rec.delivery = status
        self._log(f"LIVE CALL: delivery check {h.record_id} ({h.kind}) — {status}")
        if self.hangup_requested:
            self.hangup_after_speech = False
            if status != DELIVERY_SPOKEN and h.kind == "report":
                self.unheard.append(h.text)
            return
        self.delivery = h

    def _delivery_status(self, mark: int, started: float | None) -> tuple[str, str, str]:
        voice, pu = self.session.transcript.speech_after(mark)
        if not voice:
            return DELIVERY_NOT_SPOKEN, voice, pu
        if started is not None and any(t >= started for t in self.session.barge_ins):
            return DELIVERY_CUT_OFF, voice, pu
        return DELIVERY_SPOKEN, voice, pu

    def _take_delivery(self) -> str:
        h, self.delivery = self.delivery, None
        return delivery_check_line(h.text, h.status, h.voice_said) if h else ""

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
        work = [w for w in [self.pending_follow_up, *self.after_call] if w]
        last = self.delivery
        if last and last.kind == "report" and last.status != DELIVERY_SPOKEN:
            self.unheard.append(last.text)
        unheard = " ".join(self.unheard)
        self.pending_follow_up, self.after_call, self.unheard = "", [], []
        self.handed = self.delivery = None
        self.end_after_follow_up = False
        return call_ended_message(reason, seconds, segments, self.language,
                                  pending_follow_up="; ".join(work), unheard=unheard)

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
        self._close_on_versavoice(args, json.dumps(self._snapshot()))

    def _phone_status(self) -> str:
        polled = self._bridge(["status", self.call_id], None, BRIDGE_TIMEOUT_SECONDS)
        return str(polled.get("status") or "") if polled.get("success") else ""

    def _end(self, status: str, reason: str) -> None:
        self.finalized = True
        if self.call_id:
            self._close_on_versavoice(
                ["end", self.call_id, "--status", status, "--close-reason", reason], None)

    def _close_on_versavoice(self, args: list[str], stdin: str | None) -> None:
        """PUT the final status. A failed update is unfinished (LVC-27)."""
        for _ in range(END_CLOSE_ATTEMPTS):
            result = self._bridge(args, stdin, BRIDGE_TIMEOUT_SECONDS)
            if result.get("success"):
                return
        self._log("LIVE CALL: VersaVoice close unfinished")


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
