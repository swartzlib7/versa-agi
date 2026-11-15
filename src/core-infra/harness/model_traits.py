# ─────────────────────────────────────────────────────
# Versa AGi — Model request traits
#
# The one place that says what a model's API accepts and needs:
# endpoint, thinking shape, sampling parameters, output limit, streaming,
# reasoning replay, provider web search, prompt caching.
#
# A trait comes from the rules below (provider + model id). A model row in
# models.ini [model_params] can override any of them:
#
#   model:<key> = {..., "traits": {"streaming": true, "max_output_tokens": 32000}}
#
# model_params.to_native_kwargs() and provider_runtime read traits; nothing
# else tests a model name.
# ─────────────────────────────────────────────────────

from __future__ import annotations

import re
from dataclasses import dataclass, fields, replace
from typing import Any

# thinking: how reasoning is asked for.
THINKING_BUDGET = "budget"      # a token budget (Claude before 4.7, Gemini 2.5)
THINKING_ADAPTIVE = "adaptive"  # Claude adaptive thinking + output_config.effort
THINKING_LEVEL = "level"        # Gemini 3 and later: thinking_level

# api: the endpoint the request goes to, when a provider has more than one.
API_RESPONSES = "responses"     # OpenAI Responses (GPT-5.6 and later need it for tools + reasoning)

# replay: what happens to a reasoning record on a later request.
REPLAY_BOUND = "bound"          # valid only while the history before it is unchanged
REPLAY_SIGNATURE = "signature"  # carried back, not tied to earlier history

# web_search: the provider search route this model can use.
SEARCH_OPENROUTER = "openrouter"  # request plugin, the provider's own engine
SEARCH_ANTHROPIC = "anthropic"    # web_search server tool
SEARCH_OPENAI = "openai"          # web_search tool of the Responses endpoint
# Google's own API has no route here: the installed Google client loses the
# thought signature when google_search sits beside function tools.

# cache: how repeated input gets the provider's cached price.
CACHE_MARKS = "marks"            # only where the request marks what to cache (Claude, both routes)
# Every other shipped route caches without being asked (probed 2026-11-15).
# The mark itself: an entry lives 5 minutes and every read renews it. Steps of
# a cycle are closer together than that; a pause in a chat longer than 5
# minutes pays the full price once on the next request.
CACHE_MARK = {"type": "ephemeral"}

ANTHROPIC_ADAPTIVE_MAX_TOKENS = 64_000
ANTHROPIC_WEB_SEARCH_TOOL = "web_search_20260209"
ANTHROPIC_WEB_SEARCH_MAX_USES = 5

# Thinking levels the installed Google client accepts.
GOOGLE_THINKING_LEVELS = ("minimal", "low", "medium", "high")


@dataclass(frozen=True)
class ModelTraits:
    api: str = ""                # "" | responses
    thinking: str = ""           # "" | budget | adaptive | level
    sampling: bool = True        # temperature may be sent
    max_output_tokens: int = 0   # 0 = leave the client's default
    streaming: bool = False      # replies are streamed (long turns, heartbeat)
    replay: str = ""             # "" | bound | signature
    web_search: str = ""         # "" | openrouter | anthropic | openai
    cache: str = ""              # "" (the provider caches by itself, or not at all) | marks

    def as_dict(self) -> dict[str, Any]:
        return {f.name: getattr(self, f.name) for f in fields(self)}


_CLAUDE = re.compile(r"claude-(opus|sonnet|haiku|fable|mythos)-(\d+)(?:[-.](\d{1,2}))?(?!\d)")
_GEMINI = re.compile(r"gemini-(\d+)(?:\.(\d+))?")
_GPT = re.compile(r"gpt-(\d+)(?:\.(\d+))?")


def claude_version(model_id: str) -> tuple[str, tuple[int, int]] | None:
    """('opus', (4, 8)) for claude-opus-4-8 and anthropic/claude-opus-4.8; None otherwise."""
    found = _CLAUDE.search(model_id or "")
    if not found:
        return None
    return found.group(1), (int(found.group(2)), int(found.group(3) or 0))


def gemini_version(model_id: str) -> tuple[int, int] | None:
    """(3, 7) for gemini-3.7-flash and google/gemini-3.7-flash; None otherwise."""
    found = _GEMINI.search(model_id or "")
    if not found:
        return None
    return int(found.group(1)), int(found.group(2) or 0)


def gpt_version(model_id: str) -> tuple[int, int] | None:
    """(5, 6) for gpt-5.6-sol, (6, 0) for gpt-6-sol and openai/gpt-6-sol; None otherwise."""
    found = _GPT.search(model_id or "")
    if not found:
        return None
    return int(found.group(1)), int(found.group(2) or 0)


# From this line on, OpenAI's Chat Completions endpoint rejects function tools
# unless reasoning is off; the Responses endpoint takes both.
GPT_RESPONSES_FROM = (5, 6)


def _claude_adaptive(line: str, version: tuple[int, int]) -> bool:
    """Adaptive thinking only; sampling parameters and thinking budgets are rejected."""
    if line in ("fable", "mythos"):
        return True
    if line == "opus":
        return version >= (4, 7)
    if line == "sonnet":
        return version >= (5, 0)
    return version >= (5, 5)  # haiku


def _claude_bound(line: str, version: tuple[int, int]) -> bool:
    """Thinking blocks are tied to the conversation that produced them."""
    if line in ("fable", "mythos"):
        return version >= (5, 1)
    return version >= (5, 5)


def _claude_search(line: str, version: tuple[int, int]) -> bool:
    if line in ("fable", "mythos"):
        return True
    if line == "haiku":
        return version >= (5, 5)
    return version >= (4, 6)


def _derive(family: str, provider_slug: str, model_id: str) -> ModelTraits:
    claude = claude_version(model_id)
    gemini = gemini_version(model_id)

    if family == "anthropic":
        if not claude:
            return ModelTraits(thinking=THINKING_BUDGET, streaming=True, cache=CACHE_MARKS)
        line, version = claude
        adaptive = _claude_adaptive(line, version)
        return ModelTraits(
            thinking=THINKING_ADAPTIVE if adaptive else THINKING_BUDGET,
            sampling=not adaptive,
            max_output_tokens=ANTHROPIC_ADAPTIVE_MAX_TOKENS if adaptive else 0,
            streaming=True,
            replay=REPLAY_BOUND if _claude_bound(line, version) else "",
            web_search=SEARCH_ANTHROPIC if _claude_search(line, version) else "",
            cache=CACHE_MARKS,
        )

    if family == "google":
        level = bool(gemini) and gemini >= (3, 0)
        return ModelTraits(
            thinking=THINKING_LEVEL if level else THINKING_BUDGET,
            sampling=not level,
            replay=REPLAY_SIGNATURE if level else "",
        )

    gpt = gpt_version(model_id)
    if family == "openai_compat" and provider_slug == "openai":
        if gpt and gpt >= GPT_RESPONSES_FROM:
            return ModelTraits(
                api=API_RESPONSES,
                replay=REPLAY_SIGNATURE,
                web_search=SEARCH_OPENAI,
            )
        return ModelTraits()

    if family == "openai_compat" and provider_slug == "openrouter":
        if claude:
            line, version = claude
            adaptive = _claude_adaptive(line, version)
            return ModelTraits(
                sampling=not adaptive,
                replay=REPLAY_BOUND if _claude_bound(line, version) else "",
                web_search=SEARCH_OPENROUTER if _claude_search(line, version) else "",
                cache=CACHE_MARKS,
            )
        if gemini:
            return ModelTraits(
                replay=REPLAY_SIGNATURE if gemini >= (3, 0) else "",
                web_search=SEARCH_OPENROUTER if gemini >= (2, 0) else "",
            )
        if gpt and gpt >= GPT_RESPONSES_FROM:
            # No search route: with the web plugin GPT-6 and GPT-6.1 repeat a
            # tool call in a loop (probed). OpenAI's own API has the route.
            return ModelTraits(replay=REPLAY_SIGNATURE)
    return ModelTraits()


def _clean_overrides(overrides: Any) -> dict[str, Any]:
    """Keep only known trait names with a value of the right type."""
    if not isinstance(overrides, dict):
        return {}
    out: dict[str, Any] = {}
    defaults = ModelTraits()
    for name, value in overrides.items():
        if not hasattr(defaults, name):
            continue
        kind = type(getattr(defaults, name))
        if kind is bool and isinstance(value, bool):
            out[name] = value
        elif kind is int and isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            out[name] = value
        elif kind is str and isinstance(value, str):
            out[name] = value.strip().lower()
    return out


def normalize_overrides(overrides: Any) -> tuple[dict[str, Any], list[str]]:
    """Validated ``traits`` object for a params layer, with a warning per dropped key."""
    if overrides is None:
        return {}, []
    if not isinstance(overrides, dict):
        return {}, ["traits must be a JSON object; removed"]
    cleaned = _clean_overrides(overrides)
    dropped = sorted(set(overrides) - set(cleaned))
    warnings = [f"unknown or invalid trait '{name}'; removed" for name in dropped]
    return cleaned, warnings


def resolve_traits(
    family: str,
    provider_slug: str | None,
    model_id: str,
    overrides: Any = None,
) -> ModelTraits:
    """Traits for one catalog model: the rules, then the model row's ``traits``."""
    derived = _derive(family or "", provider_slug or "", model_id or "")
    cleaned = _clean_overrides(overrides)
    return replace(derived, **cleaned) if cleaned else derived


def google_thinking_level(effort: str) -> str:
    """Normalized effort → a level the Google client accepts."""
    if effort in GOOGLE_THINKING_LEVELS:
        return effort
    if effort in ("xhigh", "max"):
        return "high"
    return "low"


def builtin_search_tools(
    traits: ModelTraits, enabled: bool, reasoning_on: bool = True,
) -> list[dict[str, Any]]:
    """Provider search entries that sit beside the function tools (direct routes).

    ``reasoning_on``: OpenAI's search works only while the model reasons. With
    reasoning off the model answers from memory and leaves raw citation
    markers in its reply (probed), so the tool is not offered then.
    """
    if not enabled:
        return []
    if traits.web_search == SEARCH_ANTHROPIC:
        return [{
            "type": ANTHROPIC_WEB_SEARCH_TOOL,
            "name": "web_search",
            "max_uses": ANTHROPIC_WEB_SEARCH_MAX_USES,
        }]
    if traits.web_search == SEARCH_OPENAI and traits.api == API_RESPONSES and reasoning_on:
        return [{"type": "web_search"}]
    return []


def openrouter_search_plugins(traits: ModelTraits, enabled: bool) -> list[dict[str, Any]]:
    """OpenRouter web plugin, pinned to the provider's own search.

    Unset, the engine falls back to a search on every request for models
    without a native one; ``native`` keeps the model in charge of when to search.
    """
    if enabled and traits.web_search == SEARCH_OPENROUTER:
        return [{"id": "web", "engine": "native"}]
    return []
