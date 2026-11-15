"""Keeps a cache entry alive while a chat waits (state_model_drivers.md §2.7 B17).

A Claude cache entry lives 5 minutes from the start of the last request that
used it. Between two messages of a chat the cycle makes no request, so after
a longer wait the next request would write the whole prompt and conversation
again. While the listening window waits, this sends the cycle model's last
request again with no reply asked for: the provider bills a cached read and
the 5 minutes start again.

The ping is the last request as it was sent (system prompt, tools,
conversation, reasoning settings). It is not added to the conversation.
"""

from __future__ import annotations

import time
from typing import Any, Callable

from langchain_core.callbacks import BaseCallbackHandler

from harness import model_traits
from harness.prompt_cache import cached_tokens

# A ping goes this long after the last request started; the entry would be gone at LIFETIME_SECONDS.
INTERVAL_SECONDS = 240
LIFETIME_SECONDS = 300
PING_TIMEOUT_SECONDS = 60


class LastRequest(BaseCallbackHandler):
    """Remembers what the cycle model was last sent, and when that request started."""

    def __init__(self, clock: Callable[[], float] = time.time):
        self._clock = clock
        self.messages: list | None = None
        self.started: float | None = None

    def on_chat_model_start(self, serialized: Any, messages: Any, **kwargs: Any) -> None:
        if messages and messages[0]:
            self.messages = list(messages[0])
            self.started = self._clock()


def output_limit(family: str) -> int:
    """The smallest reply the route can be asked for: none on Anthropic's API.

    OpenRouter ignores a limit of 0 and lets the model write a whole reply
    (probed), so the ping asks for one token there.
    """
    return 0 if family == "anthropic" else 1


class KeepAlive:
    def __init__(self, traits: model_traits.ModelTraits, llm: Any, tools: list, last: LastRequest,
                 family: str = "anthropic", log: Callable[[str], None] | None = None,
                 clock: Callable[[], float] = time.time, interval: float = INTERVAL_SECONDS):
        self.enabled = traits.cache == model_traits.CACHE_MARKS and llm is not None
        self._llm, self._tools, self._last = llm, list(tools or []), last
        self._limit = output_limit(family)
        self._log = log or (lambda line: None)
        self._clock = clock
        self._interval = interval
        self._bound: Any = None
        self.off_reason = ""
        self.pings = 0
        self.input_tokens = self.output_tokens = self.read = self.written = 0

    def due(self) -> bool:
        if not self.enabled or self.off_reason:
            return False
        if self._last.messages is None or self._last.started is None:
            return False
        return self._clock() - self._last.started >= self._interval

    def tick(self) -> bool:
        """Called on every poll of the chat wait. True when a ping was sent."""
        if not self.due():
            return False
        self.ping()
        return True

    def _model(self):
        if self._bound is None:
            # Streaming is a transport choice, not part of the cached request; a reply of
            # no tokens cannot be streamed.
            quiet = self._llm.model_copy(update={"streaming": False})
            self._bound = quiet.bind_tools(self._tools) if self._tools else quiet
        return self._bound

    def ping(self) -> None:
        """Send the last request again, asking for no reply. Never raises."""
        waited = self._clock() - (self._last.started or self._clock())
        self._last.started = self._clock()  # the entry's 5 minutes run from the start of this request
        try:
            reply = self._model().invoke(
                list(self._last.messages or []), max_tokens=self._limit, timeout=PING_TIMEOUT_SECONDS,
            )
        except Exception as error:  # noqa: BLE001 — a failed ping must not end a chat
            self._off(f"a ping failed ({type(error).__name__}: {str(error)[:160]})")
            return
        usage = getattr(reply, "usage_metadata", None) or {}
        read, written = cached_tokens(reply)
        self.pings += 1
        self.input_tokens += int(usage.get("input_tokens") or 0)
        self.output_tokens += int(usage.get("output_tokens") or 0)
        self.read += read
        self.written += written
        self._log(f"CACHE KEEP-ALIVE: ping {self.pings} — {read:,} input tokens read from the cache"
                  + (f", {written:,} written" if written else ""))
        if not read and waited < LIFETIME_SECONDS:
            # The entry should have been there. A ping that does not find it does not protect
            # the next request either, so it is not worth sending more.
            self._off("a ping read nothing from the cache")

    def _off(self, reason: str) -> None:
        self.off_reason = reason
        self._log(f"CACHE KEEP-ALIVE: off for this cycle — {reason}")

    def usage(self) -> tuple[int, int, int, int]:
        """(input, output, read from the cache, written to it) tokens of the pings so far."""
        return self.input_tokens, self.output_tokens, self.read, self.written
