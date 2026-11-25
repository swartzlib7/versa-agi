"""The reply as the model writes it (state_realtime_link.md §2.2, stage 2).

An agent's reply to a person is the text inside one tool call:

    agictl_message(command='message send <uid> "<text>" --mode typed')

A model that streams delivers that call's JSON argument in pieces. This module
reads the pieces, takes the recipient and the text written so far, and keeps
them in a small local file. The link service reads the file and passes it to
VersaVoice, where the app shows it in a bubble until the finished message
arrives. The finished message itself goes out as before, through the tool.

PARKED, 2026-11-18 (Primary User): the reply shown while it is written is switched off
(`[features] reply_drafts=false`). A reply arrives whole, for every model, and the
VersaVoice app may animate it. This code is kept on purpose for a later use where a
long answer should be readable while it is written. Do not remove it.
Design and state: state_realtime_link.md §2.2 and D14.
Lifeline gives the harness no draft file while it is off, so nothing here runs.

A draft is a courtesy. Nothing here may stop or slow a cycle: every entry
point swallows its own errors, and the only I/O is a local file write.
"""

from __future__ import annotations

import atexit
import json
import os
import re
import tempfile
import time
from dataclasses import dataclass
from typing import Any, Callable, Optional

TOOL_NAME = "agictl_message"
MIN_WRITE_GAP_SECONDS = 0.25
MAX_TEXT_CHARS = 8000
PLAIN_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")

_COMMAND_KEY = re.compile(r'"command"\s*:\s*"')
_JSON_ESCAPES = {'"': '"', "\\": "\\", "/": "/", "b": "\b", "f": "\f", "n": "\n", "r": "\r", "t": "\t"}


def partial_json_string(buffer: str, key: re.Pattern = _COMMAND_KEY) -> Optional[tuple[str, bool]]:
    """The string value of ``"command"`` in a JSON object that is still being written.

    Returns (the value so far, whether its closing quote has arrived), or None
    while the value has not started. An escape cut off by the end of the
    buffer is left out until the rest arrives.
    """
    found = key.search(buffer)
    if not found:
        return None
    out: list[str] = []
    i, end = found.end(), len(buffer)
    while i < end:
        ch = buffer[i]
        if ch == '"':
            return "".join(out), True
        if ch != "\\":
            out.append(ch)
            i += 1
            continue
        if i + 1 >= end:
            break
        nxt = buffer[i + 1]
        if nxt == "u":
            digits = buffer[i + 2:i + 6]
            if len(digits) < 4:
                break
            try:
                code = int(digits, 16)
            except ValueError:
                i += 2
                continue
            if 0xD800 <= code <= 0xDBFF:  # first half of a pair: wait for the second
                rest = buffer[i + 6:i + 12]
                if len(rest) < 6:
                    break
                if rest[:2] == "\\u":
                    try:
                        low = int(rest[2:], 16)
                    except ValueError:
                        low = 0
                    if 0xDC00 <= low <= 0xDFFF:
                        out.append(chr(0x10000 + ((code - 0xD800) << 10) + (low - 0xDC00)))
                        i += 12
                        continue
                i += 6  # a lone half is not a character
                continue
            if 0xDC00 <= code <= 0xDFFF:
                i += 6
                continue
            out.append(chr(code))
            i += 6
            continue
        out.append(_JSON_ESCAPES.get(nxt, nxt))
        i += 2
    return "".join(out), False


@dataclass(frozen=True)
class _Token:
    text: str
    complete: bool   # followed by a space, or the command itself has ended
    quoted: bool     # began with a quotation mark


def _tokens(command: str, closed: bool) -> list[_Token]:
    """The words of a command that may stop anywhere, read as `shlex.split` reads a whole one."""
    words: list[_Token] = []
    current: list[str] = []
    quote = ""
    began_quoted = False
    in_word = False
    i, end = 0, len(command)
    while i < end:
        ch = command[i]
        if quote == "'":
            if ch == "'":
                quote = ""
            else:
                current.append(ch)
        elif quote == '"':
            if ch == '"':
                quote = ""
            elif ch == "\\":
                if i + 1 >= end:
                    break
                nxt = command[i + 1]
                # As shlex reads it (the tool runs the command through shlex.split, not a shell):
                # inside double quotes a backslash only escapes a quote or a backslash.
                current.append(nxt if nxt in '"\\' else "\\" + nxt)
                i += 1
            else:
                current.append(ch)
        elif ch.isspace():
            if in_word:
                words.append(_Token("".join(current), True, began_quoted))
                current, in_word, began_quoted = [], False, False
        elif ch in "'\"":
            if not in_word:
                began_quoted = True
            in_word = True
            quote = ch
        elif ch == "\\":
            if i + 1 >= end:
                break
            current.append(command[i + 1])
            in_word = True
            i += 1
        else:
            current.append(ch)
            in_word = True
        i += 1
    if in_word:
        words.append(_Token("".join(current), closed and not quote, began_quoted))
    return words


@dataclass(frozen=True)
class Draft:
    to: str          # recipient uid
    text: str        # the reply so far
    complete: bool   # the text's closing quote has arrived


def partial_send(command: str, closed: bool = False) -> Optional[Draft]:
    """The recipient and the text so far of a `message send` command still being written.

    None for any other command, and until the recipient is complete and the
    text has started. Every option of `message send` takes a value, so an
    option and the word after it are skipped wherever they stand.
    """
    words = _tokens(command, closed)
    if words and words[0].complete and words[0].text == "message":
        words = words[1:]
    if not words or not words[0].complete or words[0].text != "send":
        return None
    positional: list[_Token] = []
    skip_value = False
    for word in words[1:]:
        if skip_value:
            skip_value = False
            continue
        if not word.quoted and word.text.startswith("--"):
            if not word.complete:
                break  # an option name still being written
            skip_value = "=" not in word.text
            continue
        positional.append(word)
        if len(positional) == 2:
            break
    if len(positional) < 2:
        return None
    recipient, text = positional
    if not recipient.complete or not PLAIN_ID.match(recipient.text):
        return None
    if not text.text.strip():
        return None
    return Draft(recipient.text, text.text[:MAX_TEXT_CHARS], text.complete)


def write_draft_file(path: str, record: dict) -> None:
    """Replace the file in one step, so a reader never sees half a record."""
    folder = os.path.dirname(path)
    handle, temp = tempfile.mkstemp(prefix=".reply_draft.", dir=folder)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as out:
            json.dump(record, out, ensure_ascii=False)
        os.chmod(temp, 0o644)
        os.replace(temp, path)
    except Exception:
        try:
            os.unlink(temp)
        except OSError:
            pass
        raise


class ReplyDraft:
    """Follows the reply being written in one cycle and keeps the draft file current.

    ``start_call`` at each model call, ``tool_chunk`` for each piece of a tool
    call, ``end_call`` when the model has finished, ``finish`` when the tool
    has run (or the cycle ends). All four are safe to call at any time.
    """

    def __init__(self, path: str, cycle_id: str = "",
                 write: Callable[[str, dict], None] = write_draft_file,
                 clock: Callable[[], float] = time.monotonic,
                 wall: Callable[[], float] = time.time,
                 min_gap: float = MIN_WRITE_GAP_SECONDS):
        self.path = path
        self.cycle_id = cycle_id
        self._write = write
        self._clock = clock
        self._wall = wall
        self._min_gap = min_gap
        self._names: dict[Any, str] = {}
        self._buffers: dict[Any, str] = {}
        self._active: Any = None          # index of the tool call whose text is shown
        self._draft_id = int(wall() * 1000)
        self._seq = 0
        self._pending: Optional[Draft] = None
        self._written: Optional[tuple[str, str]] = None
        self._open = False
        self._last_write = float("-inf")
        self.failed = 0

    # ── model call ──────────────────────────────────────────────
    def start_call(self) -> None:
        """A new model call: pieces of an earlier call (a retry, a new step) no longer count."""
        try:
            self._names.clear()
            self._buffers.clear()
            self._active = None
            self._pending = None
        except Exception:
            self.failed += 1

    def tool_chunk(self, index: Any, name: Optional[str], args_piece: Optional[str]) -> None:
        try:
            if name:
                self._names[index] = name
            if self._names.get(index) != TOOL_NAME:
                return
            if args_piece:
                self._buffers[index] = self._buffers.get(index, "") + args_piece
            value = partial_json_string(self._buffers.get(index, ""))
            if value is None:
                return
            draft = partial_send(*value)
            if draft is None:
                return
            if index != self._active:
                if self._active is not None or self._open:
                    self._draft_id += 1   # a second message in the same step is a new draft
                    self._written = None
                self._active = index
            self._pending = draft
            if self._clock() - self._last_write >= self._min_gap:
                self._flush()
        except Exception:
            self.failed += 1

    def end_call(self) -> None:
        """The model has finished its call: write the last of the text."""
        try:
            self._flush()
        except Exception:
            self.failed += 1

    # ── after the tool ran, or at the end of the cycle ──────────
    def finish(self) -> None:
        try:
            self._pending = None
            self._active = None
            if not self._open:
                return
            self._open = False
            self._written = None
            self._seq += 1
            self._write(self.path, self._record("", "", True))
            self._draft_id += 1
        except Exception:
            self.failed += 1

    # ── internals ───────────────────────────────────────────────
    def _flush(self) -> None:
        draft = self._pending
        if draft is None or (draft.to, draft.text) == self._written:
            return
        self._seq += 1
        self._write(self.path, self._record(draft.to, draft.text, False))
        self._written = (draft.to, draft.text)
        self._open = True
        self._last_write = self._clock()

    def _record(self, to: str, text: str, done: bool) -> dict:
        return {"v": 1, "to": to, "text": text, "draft": self._draft_id, "seq": self._seq,
                "done": done, "cycle": self.cycle_id, "at": round(self._wall(), 3)}


def from_environment(cycle_id: str = "") -> Optional[ReplyDraft]:
    """The draft follower for this cycle, or None when Lifeline gave no file (drafts are off)."""
    path = (os.environ.get("VERSA_REPLY_DRAFT_FILE") or "").strip()
    if not path or not os.path.isdir(os.path.dirname(path)):
        return None
    draft = ReplyDraft(path, cycle_id or os.environ.get("VERSA_CYCLE_ID", ""))
    draft._open = os.path.exists(path)   # a file left by a cycle that died: clear it
    draft.finish()
    atexit.register(draft.finish)        # a cycle that ends mid-reply leaves no open draft
    return draft


def callback_for(draft: ReplyDraft):
    """A LangChain callback that feeds ``draft`` from a streamed model call."""
    from langchain_core.callbacks import BaseCallbackHandler

    class ReplyDraftCallback(BaseCallbackHandler):
        def on_chat_model_start(self, *args: Any, **kwargs: Any) -> None:
            draft.start_call()

        def on_llm_new_token(self, token: str, *, chunk: Any = None, **kwargs: Any) -> None:
            try:
                message = getattr(chunk, "message", None)
                for piece in (getattr(message, "tool_call_chunks", None) or []):
                    draft.tool_chunk(piece.get("index"), piece.get("name"), piece.get("args"))
            except Exception:
                draft.failed += 1

        def on_llm_end(self, *args: Any, **kwargs: Any) -> None:
            draft.end_call()

        def on_llm_error(self, *args: Any, **kwargs: Any) -> None:
            draft.finish()

    return ReplyDraftCallback()
