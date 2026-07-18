import os
import sys
import json
import time
import uuid
import sqlite3
import shlex
import argparse
import subprocess
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional, Literal

from langchain_core.messages import SystemMessage, HumanMessage, AIMessage, ToolMessage, RemoveMessage, trim_messages
from langchain_core.tools import tool
from langgraph.prebuilt import create_react_agent
from langgraph.graph import StateGraph, MessagesState, START, END
from langgraph.graph.message import REMOVE_ALL_MESSAGES
import warnings
warnings.filterwarnings("ignore", category=DeprecationWarning)


def tlog(msg: str):
    """Timestamped print for result file traceability."""
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def _format_params_audit(model_name: str, agent_overrides: dict | None) -> str:
    """Compact catalog-layer generation params (before provider-native shaping)."""
    from harness.model_params import resolve_model_params

    resolved = resolve_model_params(model_name, agent_overrides=agent_overrides)
    bits = [f"reasoning={resolved.get('reasoning_effort') or 'none'}"]
    if resolved.get("temperature") is not None:
        bits.append(f"temp={resolved['temperature']}")
    if resolved.get("reasoning_max_tokens") is not None:
        bits.append(f"reasoning_tokens={resolved['reasoning_max_tokens']}")
    extra = resolved.get("extra") or {}
    if extra:
        bits.append(f"extra={json.dumps(extra, separators=(',', ':'))}")
    if agent_overrides:
        bits.append("agent_override=yes")
    return " ".join(bits)


def _read_local_paths_env() -> tuple[str, str]:
    """Return (VERSA_GPU_BACKEND, VERSA_INFERENCE_URL) from paths.env."""
    gpu_backend = "standard"
    inference_url = "http://127.0.0.1:11434"
    try:
        with open("/etc/versa-agi/paths.env", "r") as f:
            for line in f:
                if line.startswith("VERSA_GPU_BACKEND="):
                    gpu_backend = line.strip().split("=")[1].strip('"')
                elif line.startswith("VERSA_INFERENCE_URL="):
                    inference_url = line.strip().split("=")[1].strip('"')
    except OSError:
        pass
    return gpu_backend, inference_url


def _resolve_sycl_api_model(catalog_key: str) -> str:
    """Map catalog key to llama-server API model id via [sycl_models]."""
    import configparser

    try:
        ini = configparser.ConfigParser(delimiters=("=",))
        for ini_path in (
            "/etc/versa-agi/models.ini",
            os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                "models.ini",
            ),
        ):
            if os.path.isfile(ini_path):
                ini.read(ini_path)
                break
        if ini.has_section("sycl_models"):
            raw = ini.get("sycl_models", catalog_key, fallback="")
            if raw:
                parts = raw.strip().split(",")
                if len(parts) >= 2:
                    return parts[1].strip().replace(".gguf", "")
    except Exception:
        pass
    return catalog_key


def _native_params_for_audit(native: dict[str, Any], num_ctx: int = 0) -> dict[str, Any]:
    """Provider-native kwargs safe for cycle log (no secrets / bulky blobs)."""
    out: dict[str, Any] = {}
    for key, val in native.items():
        if key in ("extra_body", "model_kwargs"):
            if key == "model_kwargs" and isinstance(val, dict) and val:
                out["model_kwargs"] = val
            continue
        out[key] = val
    if num_ctx and num_ctx > 0:
        out["num_ctx"] = num_ctx
    return out


def resolve_llm_route(
    model_name: str,
    num_ctx: int = 0,
    agent_overrides: dict | None = None,
) -> dict[str, Any]:
    """Resolve provider routing metadata without instantiating the LLM."""
    from harness.model_params import (
        resolve_model_params,
        detect_provider_family,
        to_native_kwargs,
        apply_native_for_local_runtime,
        _load_catalog_provider,
    )
    from model_catalog import resolve_local_provider

    provider_slug = _load_catalog_provider(model_name) or ""
    resolved = resolve_model_params(model_name, agent_overrides=agent_overrides)
    family = detect_provider_family(model_name, provider_slug)
    native = to_native_kwargs(family, model_name, resolved, provider_slug=provider_slug)

    route: dict[str, Any] = {
        "catalog_key": model_name,
        "catalog_provider": provider_slug,
        "provider_family": family,
        "client": "",
        "gpu_backend": "",
        "local_provider": "",
        "inference_url": "",
        "endpoint": "",
        "api_model": model_name,
        "native_params": {},
    }

    if model_name.startswith("gemini"):
        route["client"] = "ChatGoogleGenerativeAI"
        route["endpoint"] = "google-generativeai"
        route["native_params"] = _native_params_for_audit(native, num_ctx)
        return route

    if model_name.startswith("gpt"):
        route["client"] = "ChatOpenAI"
        route["endpoint"] = "https://api.openai.com/v1"
        route["native_params"] = _native_params_for_audit(native, num_ctx)
        return route

    if model_name.startswith("claude"):
        route["client"] = "ChatAnthropic"
        route["endpoint"] = "https://api.anthropic.com"
        route["native_params"] = _native_params_for_audit(native, num_ctx)
        return route

    if model_name.startswith("grok"):
        route["client"] = "ChatOpenAI"
        route["endpoint"] = "https://api.x.ai/v1"
        route["native_params"] = _native_params_for_audit(native, num_ctx)
        return route

    if "/" in model_name:
        route["client"] = "ChatOpenAI"
        route["endpoint"] = "https://openrouter.ai/api/v1"
        route["native_params"] = _native_params_for_audit(native, num_ctx)
        return route

    gpu_backend, inference_url = _read_local_paths_env()
    local_provider = provider_slug or resolve_local_provider(gpu_backend)
    route["gpu_backend"] = gpu_backend
    route["local_provider"] = local_provider
    route["inference_url"] = inference_url

    if local_provider == "llamacpp":
        native = apply_native_for_local_runtime(native, "llamacpp")
        route["client"] = "ChatOpenAI"
        route["endpoint"] = f"{inference_url}/v1"
        route["api_model"] = _resolve_sycl_api_model(model_name)
    else:
        native = apply_native_for_local_runtime(native, "ollama")
        route["client"] = "ChatOllama"
        route["endpoint"] = inference_url
        route["api_model"] = model_name

    route["native_params"] = _native_params_for_audit(native, num_ctx)
    return route


def _format_llm_route_log(role: str, model_name: str, route: dict[str, Any]) -> str:
    """Single-line provider resolution audit for cycle logs."""
    parts = [
        f"LLM ROUTE ({role}/{model_name}):",
        f"catalog_provider={route.get('catalog_provider') or '—'}",
        f"client={route.get('client') or '—'}",
    ]
    if route.get("local_provider"):
        parts.extend([
            f"local_provider={route['local_provider']}",
            f"gpu_backend={route.get('gpu_backend') or '—'}",
            f"inference={route.get('inference_url') or '—'}",
            f"endpoint={route.get('endpoint') or '—'}",
            f"api_model={route.get('api_model') or model_name}",
        ])
    elif route.get("endpoint"):
        parts.append(f"endpoint={route['endpoint']}")
    return " ".join(parts)


def _format_native_params_audit(route: dict[str, Any]) -> str:
    """Compact provider-native params actually passed to LangChain."""
    native = route.get("native_params") or {}
    if not native:
        return "native=(none)"
    return f"native={json.dumps(native, separators=(',', ':'), default=str)}"


def _log_llm_resolution(role: str, model_name: str, num_ctx: int, agent_overrides: dict | None) -> None:
    """Emit catalog + native param audit lines for one model role."""
    route = resolve_llm_route(model_name, num_ctx=num_ctx, agent_overrides=agent_overrides)
    tlog(_format_llm_route_log(role, model_name, route))
    tlog(f"MODEL PARAMS ({role}/{model_name}): catalog-layer {_format_params_audit(model_name, agent_overrides)} | {_format_native_params_audit(route)}")


_PLACEHOLDER_TOOL_CONTENT = "[Result unavailable — the cycle ended before this tool call completed.]"


def _canonicalize_messages(msgs, max_msgs=0):
    """Produce a provider-valid message list from a checkpoint history.

    Every major LLM provider rejects a malformed tool-call transcript
    (``INVALID_CHAT_HISTORY`` / ``400``). This function returns a sequence that
    is valid by construction, guaranteeing:

      * Every ``AIMessage`` tool_call is immediately followed by a ``ToolMessage``
        answering it. If the real result was lost (crash mid-execution, an
        earlier prune, a partial trim) a synthetic placeholder is inserted
        ADJACENT to its parent — so even a dangling call buried in the middle
        of the history is repaired, not just one at the tail.
      * Every ``ToolMessage`` immediately follows the ``AIMessage`` that produced
        its ``tool_call_id``. Orphans (no preceding producer), duplicate answers,
        and foreign results are dropped.
      * An optional depth trim keeps only the last ``max_msgs`` messages, anchored
        so the window begins at a ``HumanMessage`` (the pre_model_hook trims with
        ``start_on="human"``, so a non-human start would be discarded anyway).
        When no ``HumanMessage`` exists the history is dropped entirely.

    Pure function (no I/O). Returns ``(clean, changed, stats)`` where ``changed``
    is ``True`` iff the id-sequence differs from the input — placeholders carry
    fresh ids and drops/trims/reorders change the sequence, so any modification
    flips it and only then must the caller persist.
    """
    original_ids = [getattr(m, "id", None) for m in msgs]
    stats = {"trimmed": 0, "orphans": 0, "placeholders": 0}

    # ── 1. Depth trim, anchored at a HumanMessage ──
    work = list(msgs)
    if max_msgs > 0 and len(work) > max_msgs:
        first_sys = work[0] if work and isinstance(work[0], SystemMessage) else None
        body = work[1:] if first_sys else work
        keep_n = (max_msgs - 1) if first_sys else max_msgs
        start = max(len(body) - keep_n, 0)
        anchor = None
        # Expand backwards toward an earlier HumanMessage (bounded), else
        # shrink forward to the next one.
        for i in range(start, max(start - max_msgs, -1), -1):
            if isinstance(body[i], HumanMessage):
                anchor = i
                break
        if anchor is None:
            for i in range(start + 1, len(body)):
                if isinstance(body[i], HumanMessage):
                    anchor = i
                    break
        kept_body = body[anchor:] if anchor is not None else []
        work = ([first_sys] if first_sys else []) + kept_body
        stats["trimmed"] = len(msgs) - len(work)

    # ── 2. Repair tool_call / ToolMessage pairing and ordering ──
    clean = []
    i, n = 0, len(work)
    while i < n:
        m = work[i]
        if isinstance(m, AIMessage) and getattr(m, "tool_calls", None):
            clean.append(m)
            expected = [tc["id"] for tc in m.tool_calls]
            names = {tc["id"]: tc.get("name", "unknown") for tc in m.tool_calls}
            expected_set = set(expected)
            # Consume the run of ToolMessages immediately following this AIMessage.
            answered = {}
            j = i + 1
            while j < n and isinstance(work[j], ToolMessage):
                tm = work[j]
                tcid = getattr(tm, "tool_call_id", None)
                if tcid in expected_set and tcid not in answered:
                    answered[tcid] = tm
                else:
                    stats["orphans"] += 1  # duplicate / foreign result
                j += 1
            # Emit answers in the tool_call order, synthesizing where missing.
            for cid in expected:
                if cid in answered:
                    clean.append(answered[cid])
                else:
                    clean.append(ToolMessage(
                        content=_PLACEHOLDER_TOOL_CONTENT,
                        tool_call_id=cid,
                        name=names[cid],
                        id=str(uuid.uuid4()),
                    ))
                    stats["placeholders"] += 1
            i = j
        elif isinstance(m, ToolMessage):
            # ToolMessage with no preceding producing AIMessage — orphan.
            stats["orphans"] += 1
            i += 1
        else:
            clean.append(m)
            i += 1

    changed = [getattr(m, "id", None) for m in clean] != original_ids
    return clean, changed, stats


def _unresolved_tool_call_ids(messages) -> set:
    """Return tool_call IDs from the latest AIMessage not yet answered.

    Parallel tool calls stream as one AIMessage chunk followed by one ToolMessage
    chunk per call. Budget warnings must wait until every ID in that batch is
    answered — checking only the current chunk misses the gap after the first
    result and corrupts the checkpoint (INVALID_CHAT_HISTORY).
    """
    if not messages:
        return set()
    answered = set()
    i = len(messages) - 1
    while i >= 0 and isinstance(messages[i], ToolMessage):
        tcid = getattr(messages[i], "tool_call_id", None)
        if tcid:
            answered.add(tcid)
        i -= 1
    if i < 0:
        return set()
    m = messages[i]
    if not isinstance(m, AIMessage) or not getattr(m, "tool_calls", None):
        return set()
    pending = set()
    for tc in m.tool_calls:
        cid = tc.get("id")
        if cid and cid not in answered:
            pending.add(cid)
    return pending


# Transient HTTP statuses worth retrying — timeouts, conflicts, rate limits,
# the "headers too large" edge case (431), and all 5xx. Excludes 400/401/403/404
# and other deterministic client errors (retrying those is pointless).
_TRANSIENT_HTTP_STATUS = {408, 409, 425, 429, 431, 500, 502, 503, 504}

# Transient exception class names across providers (openai, anthropic, google,
# httpx). Matched by name so we don't have to import every SDK.
_TRANSIENT_EXC_NAMES = {
    "APIConnectionError", "APITimeoutError", "InternalServerError",
    "RateLimitError", "ServiceUnavailable", "ResourceExhausted",
    "DeadlineExceeded", "ServiceUnavailableError", "InternalServerError",
    "Timeout", "ConnectError", "ConnectTimeout", "ReadTimeout",
    "RemoteProtocolError", "PoolTimeout",
}


def _is_transient_transport_error(e) -> bool:
    """True if `e` is a transient transport/edge failure worth retrying.

    Provider-agnostic: checks an HTTP status code (directly or on a nested
    response) against the transient set, then falls back to matching transient
    exception class names anywhere in the MRO. Deterministic client errors
    (400/401/403/404, validation, INVALID_CHAT_HISTORY) are NOT transient.
    """
    err_str = str(e).lower()
    if "image input is not supported" in err_str or "mmproj" in err_str:
        return False
    if "invalid_chat_history" in err_str:
        return False

    status = getattr(e, "status_code", None)
    if status is None:
        resp = getattr(e, "response", None)
        status = getattr(resp, "status_code", None)
    if isinstance(status, int) and status in _TRANSIENT_HTTP_STATUS:
        return True
    for cls in type(e).__mro__:
        if cls.__name__ in _TRANSIENT_EXC_NAMES:
            return True
    return False

from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_openai import ChatOpenAI
from langchain_anthropic import ChatAnthropic
from langchain_ollama import ChatOllama
from harness.model_context import get_trimmer_char_limit

from pydantic import BaseModel, Field

# ═══════════════════════════════════════════════════════
# Tool Output Limit — enforced globally on all tool returns
# ═══════════════════════════════════════════════════════
TOOL_OUTPUT_LIMIT = 6000

def _run_agictl(args_str: str) -> str:
    """Internal helper: execute an agictl command and return output."""
    try:
        cmd = ["agictl"] + shlex.split(args_str)
    except ValueError as ve:
        return f"ERROR parsing command syntax: {ve}. Check quotation marks and escaping."

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        output = result.stdout
        if result.stderr:
            output += "\nSTDERR:\n" + result.stderr
        if TOOL_OUTPUT_LIMIT > 0 and len(output) > TOOL_OUTPUT_LIMIT:
            output = output[:TOOL_OUTPUT_LIMIT] + f"\n\n[OUTPUT TRUNCATED: Exceeded {TOOL_OUTPUT_LIMIT} chars. Refine your query or paginate.]"
        return output if output else "Command executed successfully with no output."
    except subprocess.TimeoutExpired:
        return "ERROR: Command timed out after 120 seconds."
    except Exception as e:
        return f"Error executing command: {e}"


# ═══════════════════════════════════════════════════════
# 1. SYSTEM — System-level queries and config
# ═══════════════════════════════════════════════════════

class SystemInput(BaseModel):
    command: str = Field(description=(
        "The full agictl system subcommand to run. "
        "Examples: 'system whoami', 'system config get', 'system config get primary_user', "
        "'system config set key value', 'system workspace-link /path', 'system sync-profiles'."
    ))

@tool("agictl_system", args_schema=SystemInput)
def agictl_system(command: str) -> str:
    """Query or configure system-level settings.
    Use for: identity (whoami), config management, workspace linking, profile sync.
    Examples:
      - 'system whoami'
      - 'system config get'
      - 'system config set key value'
      - 'system workspace-link /path/to/project'
    """
    return _run_agictl(command)


# ═══════════════════════════════════════════════════════
# 2. MODEL — LLM model management
# ═══════════════════════════════════════════════════════

class ModelInput(BaseModel):
    command: str = Field(description=(
        "The full agictl model subcommand. "
        "Examples: 'model list', 'model list --available', 'model run modelname', "
        "'model activate modelname', 'model refresh'."
    ))

@tool("agictl_model", args_schema=ModelInput)
def agictl_model(command: str) -> str:
    """Manage LLM models — list, run, activate, or refresh available models.
    Examples:
      - 'model list' — show active models
      - 'model list --available' — show all available models
      - 'model activate modelname' — activate a model for this agent
      - 'model refresh' — refresh model registry from providers
    """
    return _run_agictl(command)


# ═══════════════════════════════════════════════════════
# 3. AGENT — Agent registry queries
# ═══════════════════════════════════════════════════════

class AgentInput(BaseModel):
    command: str = Field(description=(
        "The full agictl agent subcommand. Agent-facing commands only. "
        "Examples: 'agent list', 'agent show web-dev', 'agent summary web-dev', "
        "'agent count', 'agent get-active'."
    ))

@tool("agictl_agent", args_schema=AgentInput)
def agictl_agent(command: str) -> str:
    """Query agent registry information (read-only).
    Use for: listing agents, showing agent details, summaries, counts.
    Examples:
      - 'agent list' — all registered agents
      - 'agent show web-dev' — full details for an agent
      - 'agent summary web-dev' — status summary
      - 'agent get-active' — currently active agents
    """
    return _run_agictl(command)


# ═══════════════════════════════════════════════════════
# 3b. UTILITY — One-shot Utility Model runs
# ═══════════════════════════════════════════════════════

class UtilityInput(BaseModel):
    command: str = Field(description=(
        "The agictl utility subcommand only. "
        "Examples: 'utility model list', 'utility run brand-hero-square "
        "--input-files brand/ref.jpg', "
        "'utility run weekly-summary --task-id 42'."
    ))

@tool("agictl_utility", args_schema=UtilityInput)
def agictl_utility(command: str) -> str:
    """Run Utility Model profiles — one-shot generation without a full agent cycle.
    Use for: listing UM profiles, manual runs with --input-files, dry-run validation.
    Examples:
      - 'utility model list' — enabled Utility Model profiles
      - 'utility run <um-id>' — run using UM defaults (run-as-agent context)
      - 'utility run <um-id> --input-files path/a.jpg,path/b.pdf'
      - 'utility run <um-id> --dry-run' — validate maps and paths only
    """
    return _run_agictl(command)


# ═══════════════════════════════════════════════════════
# 4. TASK — Task lifecycle management
# ═══════════════════════════════════════════════════════

class TaskInput(BaseModel):
    command: str = Field(description=(
        "The full agictl task subcommand. "
        "Examples: 'task list', 'task list --all', 'task get 73', "
        "'task add \"Title\" --desc \"Description\" --priority high --due-date \"2026-05-20 12:00:00\"', "
        "'task update 73 --status in_progress', 'task update 73 --status done', "
        "'task progress 73 \"DONE: X. NEXT: Y.\"' (append progress journal entry), "
        "'task progress 73' (list journal), "
        "'task done 73', 'task cancel 73', 'task snooze 73 10', "
        "'task unfreeze 73' (resume a frozen task assigned to you), "
        "'task unfreeze-all <your_agent_name>' (resume all your frozen tasks), "
        "'task reminder \"Check deployment\" --category instruction'."
    ))

@tool("agictl_task", args_schema=TaskInput)
def agictl_task(command: str) -> str:
    """Manage your task queue — list, create, update, complete, or cancel tasks.
    IMPORTANT: Always use exact option syntax with -- prefixes.
    Examples:
      - 'task list' — active tasks
      - 'task list --all' — all tasks including done/cancelled
      - 'task get 73' — full task details
      - 'task add "Setup server" --desc "Docker setup" --priority high --due-date "2026-05-20 12:00:00"'
      - 'task update 73 --status in_progress'
      - 'task progress 73 "DONE: research. NEXT: draft doc."' — append progress entry (your breadcrumbs for next cycle)
      - 'task progress 73' — read the progress journal
      - 'task done 73' — mark complete
      - 'task cancel 73'
      - 'task snooze 73 10' — snooze for 10 minutes
      - 'task unfreeze 73' — resume a frozen task assigned to you (resets retry counter)
      - 'task unfreeze-all <your_agent_name>' — resume all your frozen tasks
    """
    return _run_agictl(command)


# ═══════════════════════════════════════════════════════
# 5. MESSAGE — Communication (VersaVoice + internal)
# ═══════════════════════════════════════════════════════

class MessageInput(BaseModel):
    command: str = Field(description=(
        "The full agictl message subcommand. "
        "Examples: 'message send UID \"Hello!\" --mode typed', "
        "'message get SUB_ACCOUNT_UID --unread', 'message get SUB_ACCOUNT_UID --last-n-count 10', "
        "'message internal agent-name \"message text\"', "
        "'message mark-processed MSG_ID'."
    ))

@tool("agictl_message", args_schema=MessageInput)
def agictl_message(command: str) -> str:
    """Send and receive messages via VersaVoice or internal agent channels.
    Examples:
      - 'message send UID "Hello!" --mode typed' — send to VersaVoice contact
      - 'message get SUB_ACCOUNT_UID --unread' — unprocessed inbound messages
      - 'message get SUB_ACCOUNT_UID --contact UID --last-n-count 10' — conversation history
      - 'message internal agent-name "message"' — send to another agent
      - 'message mark-processed MSG_ID' — mark a message as processed
    """
    return _run_agictl(command)


# ═══════════════════════════════════════════════════════
# 6. CYCLE — Agent lifecycle telemetry
# ═══════════════════════════════════════════════════════

class CycleInput(BaseModel):
    command: str = Field(description=(
        "The full agictl cycle subcommand. "
        "Examples: 'cycle end Summary of work done', 'cycle recent agent-name', "
        "'cycle count agent-name'."
    ))

@tool("agictl_cycle", args_schema=CycleInput)
def agictl_cycle(command: str) -> str:
    """Manage your work cycle — end cycle, view history.
    CRITICAL: You MUST call 'cycle end <summary>' before your budget runs out.
    To respawn on a new model: snooze or create a due task, then 'cycle end'.
    Examples:
      - 'cycle end Completed Docker setup for mysmartyard' — end cycle with summary
      - 'cycle recent coa' — view recent cycle summaries
      - 'cycle count coa' — total cycles executed
    """
    return _run_agictl(command)


# ═══════════════════════════════════════════════════════
# 7. PROJECT — Workspace and project management
# ═══════════════════════════════════════════════════════

class ProjectInput(BaseModel):
    command: str = Field(description=(
        "The full agictl project subcommand. "
        "Examples: 'project list', 'project add name --desc \"Description\" --remote URL', "
        "'project update 3 --desc \"New description\"', "
        "'project assign 3 --agent charlie', "
        "'project pause 3', 'project resume 3', "
        "'project archive 3', 'project git-setup'."
    ))

@tool("agictl_project", args_schema=ProjectInput)
def agictl_project(command: str) -> str:
    """Manage workspace projects — list, register, update, pause, resume, archive.
    Examples:
      - 'project list' — all registered projects (includes id)
      - 'project add myapp --desc "Web app" --remote git@github.com:org/repo.git'
      - 'project update 3 --desc "Updated summary"' — update metadata by project id
      - 'project assign 3 --agent charlie' — assign agent to project by id
      - 'project pause 3' — pause project (skipped by Lifeline)
      - 'project resume 3' — resume a paused project
      - 'project members 3' — list members for project id
      - 'project git-setup' — configure git identity and SSH keys
    """
    return _run_agictl(command)


# ═══════════════════════════════════════════════════════
# 8. CONNECTION — Social graph (VersaVoice contacts)
# ═══════════════════════════════════════════════════════

class ConnectionInput(BaseModel):
    command: str = Field(description=(
        "The full agictl connection subcommand. "
        "Examples: 'connection list primary-user', 'connection list agent', "
        "'connection request UID'."
    ))

@tool("agictl_connection", args_schema=ConnectionInput)
def agictl_connection(command: str) -> str:
    """Manage VersaVoice connections — list contacts, send connection requests.
    Examples:
      - 'connection list primary-user' — list Primary User's contacts
      - 'connection list agent' — list your established connections
      - 'connection request UID' — send connection invitation
    """
    return _run_agictl(command)


# ═══════════════════════════════════════════════════════
# 9. MEMORY — Agent persistent memory
# ═══════════════════════════════════════════════════════

class MemoryInput(BaseModel):
    command: str = Field(description=(
        "The full agictl memory subcommand. "
        "Examples: 'memory connection get UID', "
        "'memory connection set UID --preferences \"prefers text\" --rapport building', "
        "'memory connection list', 'memory project get 7', "
        "'memory project set 7 --phase \"development\" --blockers \"none\"', "
        "'memory system get key', 'memory system set key value', 'memory system list'."
    ))

@tool("agictl_memory", args_schema=MemoryInput)
def agictl_memory(command: str) -> str:
    """Manage your persistent memory — per-contact, per-project, and system knowledge.
    Memory persists across cycles and is YOUR responsibility to maintain.
    Examples:
      - 'memory connection set UID --preferences "prefers voice" --rapport building'
      - 'memory project set 7 --phase "testing" --next-steps "deploy to staging"'
      - 'memory system set docker_available "true"'
      - 'memory system list' — view all system knowledge
    """
    return _run_agictl(command)


# ═══════════════════════════════════════════════════════
# 9b. GAME — Strategic pursuit management
# ═══════════════════════════════════════════════════════

class GameInput(BaseModel):
    command: str = Field(description=(
        "The full agictl game subcommand. "
        "Examples: 'game add \"VersaVoice Launch\" --postulate \"Build the voice platform\"', "
        "'game update 1 --posture aggressive --barriers \"Competitor launched\"', "
        "'game show 1', 'game list', 'game list --status active', "
        "'game assign-project 1 3', "
        "'game opponent add 3 \"CompetitorCo\" --type business --desc \"Main rival\"', "
        "'game opponent list --project 3', 'game opponent update 1 --assessment \"Losing ground\"', "
        "'game opponent delete 1'."
    ))

@tool("agictl_game", args_schema=GameInput)
def agictl_game(command: str) -> str:
    """Manage strategic pursuits — games, posture, and competitive intelligence.
    Examples:
      - 'game add "Career" --postulate "Launch acting career" --posture exploratory'
      - 'game update 1 --posture aggressive --barriers "Audition rejection rate high"'
      - 'game show 1' — full game state with projects + awareness
      - 'game list' — all games
      - 'game assign-project 1 3' — assign project #3 to game #1
      - 'game opponent add 3 "RivalCo" --type business'
      - 'game opponent list --project 3'
      - 'game opponent update 1 --assessment "They raised Series B"'
      - 'game opponent delete 1'
    """
    return _run_agictl(command)


# ═══════════════════════════════════════════════════════
# 9c. AWARENESS — Agent cognitive state (Conclusions + Actions)
# ═══════════════════════════════════════════════════════

class AwarenessInput(BaseModel):
    command: str = Field(description=(
        "The full agictl awareness subcommand. "
        "Examples: 'awareness add conclusion --subject project --subject-id 3 --content \"Blocked by design input\"', "
        "'awareness add action --subject connection --subject-id abc123 --content \"Switch to voice\" --action-conclusion-id 7', "
        "'awareness revise 7 --content \"Updated understanding\"', "
        "'awareness complete 12', "
        "'awareness list --type conclusion --status active', "
        "'awareness get 7'."
    ))

@tool("agictl_awareness", args_schema=AwarenessInput)
def agictl_awareness(command: str) -> str:
    """Manage your cognitive awareness — conclusions about the world and actions derived from them.
    This is the core of the Awareness-First discipline. You MUST write conclusions and actions every cycle.
    Examples:
      - 'awareness add conclusion --subject self --content "I over-explain in messages"'
      - 'awareness add action --subject connection --subject-id abc --content "Use bullet points" --action-conclusion-id 7'
      - 'awareness revise 7 --content "Updated: they now prefer detailed reports"'
      - 'awareness complete 12' — mark an action as done
      - 'awareness list --status active' — all active awareness
      - 'awareness get 7' — single entry details
    """
    return _run_agictl(command)


# ═══════════════════════════════════════════════════════
# 10. IDENTITY — VersaVoice sub-account provisioning
# ═══════════════════════════════════════════════════════

class IdentityInput(BaseModel):
    command: str = Field(description=(
        "The full agictl identity subcommand. COA-only. "
        "Example: 'identity provision agent-user --token TOKEN --first-name Name --last-name Last'."
    ))

@tool("agictl_identity", args_schema=IdentityInput)
def agictl_identity(command: str) -> str:
    """Provision or manage VersaVoice identity for agents (COA-only).
    Example:
      - 'identity provision web-dev --token TOKEN --first-name Web --last-name Dev'
    """
    return _run_agictl(command)


# ═══════════════════════════════════════════════════════
# 11. EXECUTE — Code execution (bash/python)
# ═══════════════════════════════════════════════════════

class ExecuteInput(BaseModel):
    command: str = Field(description=(
        "The agictl execute subcommand (or shorthand 'bash \"...\"' / 'python \"...\"'). "
        "Examples: 'bash \"ls -la\"', 'execute bash \"ls -la /home\"', "
        "'execute python \"print(1+1)\"'. "
        "You CANNOT use sudo, su, or any privilege escalation commands."
    ))

@tool("agictl_execute", args_schema=ExecuteInput)
def agictl_execute(command: str) -> str:
    """Execute bash or python scripts in your workspace.
    You do NOT have sudo/su access. Privilege escalation commands are blocked.
    Examples:
      - 'bash "ls -la"'
      - 'execute bash "docker compose up -d"'
      - 'execute python "import os; print(os.getcwd())"'
    """
    if not command:
        return "ERROR: You must provide a command string!"

    stripped = command.strip()
    if stripped.startswith("bash ") and not stripped.startswith("execute "):
        command = f"execute {stripped}"
    elif stripped.startswith("python ") and not stripped.startswith("execute "):
        command = f"execute {stripped}"

    # ── Privilege Escalation Guard ──
    # Enforced at infrastructure level — the model cannot bypass this.
    BLOCKED_PATTERNS = ["sudo ", "sudo\t", " sudo ", "su ", "su\t", " su ", "newgrp ", "pkexec ", "gpasswd ", "usermod "]
    cmd_lower = command.lower()
    for pattern in BLOCKED_PATTERNS:
        if pattern in cmd_lower or cmd_lower.startswith(pattern.strip()):
            return (
                f"BLOCKED: Privilege escalation command detected ('{pattern.strip()}'). "
                "You do NOT have sudo/su access. This command will NEVER succeed. "
                "Set the task to blocked: agictl task update <id> --status blocked. "
                "Report this blocker to the COA or Primary User and move to your next task."
            )

    return _run_agictl(command)


class SearchInput(BaseModel):
    query: str = Field(description=(
        "The search query to look up on the web. "
        "Example: 'how to install Node.js 22 on Ubuntu 24.04'"
    ))
    count: int = Field(default=5, description="Number of results to return (default: 5, max: 10)")

@tool("agictl_search", args_schema=SearchInput)
def agictl_search(query: str, count: int = 5) -> str:
    """Search the web using the local SearXNG instance.
    Returns top results with title, URL, and snippet.
    Use this for technical research, version compatibility checks, or documentation lookups.
    """
    if not query:
        return "ERROR: You must provide a search query!"
    count = min(count, 10)
    return _run_agictl(f'search web "{query}" --count {count}')


# ═══════════════════════════════════════════════════════
# All tools registry — passed to create_react_agent
# ═══════════════════════════════════════════════════════

ALL_TOOLS = [
    agictl_system,
    agictl_model,
    agictl_agent,
    agictl_utility,
    agictl_task,
    agictl_message,
    agictl_cycle,
    agictl_project,
    agictl_connection,
    agictl_memory,
    agictl_game,
    agictl_awareness,
    agictl_identity,
    agictl_execute,
]

# Conditionally register search tool based on setup.ini config
def _is_search_enabled():
    import configparser
    config = configparser.ConfigParser()
    config.read("/etc/versa-agi/setup.ini")
    return config.get("search", "enabled", fallback="false").lower() == "true"

if _is_search_enabled():
    ALL_TOOLS.append(agictl_search)

# ═══════════════════════════════════════════════════════
# BROWSER — Headless browser automation (Playwright)
# ═══════════════════════════════════════════════════════

class BrowserInput(BaseModel):
    command: str = Field(description=(
        "The full agictl browser subcommand to run. "
        "Examples: 'browser goto \"https://example.com\"', "
        "'browser goto \"https://example.com\" --screenshot', "
        "'browser click \"https://example.com\" \"button.submit\"', "
        "'browser fill \"https://example.com\" \"#email\" \"user@test.com\"', "
        "'browser screenshot \"https://example.com\" --full-page', "
        "'browser extract \"https://example.com\" --selector \"h1\"', "
        "'browser extract \"https://example.com\" --selector \"a\" --attribute \"href\"'."
    ))

@tool("agictl_browser", args_schema=BrowserInput)
def agictl_browser(command: str) -> str:
    """Browse web pages using a headless Chromium browser.
    Use for: navigating pages, clicking elements, filling forms, taking screenshots, extracting content.
    IMPORTANT: Only http:// and https:// URLs are allowed. file:// is blocked.
    Examples:
      - 'browser goto "https://example.com"' — load page and get text content
      - 'browser goto "https://example.com" --screenshot' — load + screenshot
      - 'browser click "https://example.com" "button.submit"' — click an element
      - 'browser fill "https://example.com" "#email" "user@test.com"' — fill a form field
      - 'browser screenshot "https://example.com" --full-page' — full-page screenshot
      - 'browser extract "https://example.com" --selector "h1"' — extract text from selector
      - 'browser extract "https://example.com" --selector "a" --attribute "href"' — extract attribute
    """
    return _run_agictl(command)

# Conditionally register browser tool based on setup.ini config
def _is_browser_enabled():
    import configparser
    config = configparser.ConfigParser()
    config.read("/etc/versa-agi/setup.ini")
    return config.get("browser", "enabled", fallback="false").lower() == "true"

if _is_browser_enabled():
    ALL_TOOLS.append(agictl_browser)


# ═══════════════════════════════════════════════════════
# VIEW — Agent-initiated image perception
# ═══════════════════════════════════════════════════════

MODALITY_VIEW_MIN_STEPS = 8

_HARNESS_VIEW_CTX: dict[str, Any] = {}


class ViewImageInput(BaseModel):
    path: str = Field(description=(
        "Absolute or workspace-relative path to a local image file on disk."
    ))


@tool("agictl_view_image", args_schema=ViewImageInput)
def agictl_view_image(path: str) -> str:
    """View a local image file — injects it into your context when the execution model supports vision.

    Use when you need to perceive a screenshot, attachment, diagram, or any image on disk.
    The execution model must declare image in catalog input_modalities.
    Examples:
      - agictl_view_image(path="/tmp/screenshot.png")
      - agictl_view_image(path="workspace/project/diagram.png")
    """
    from model_catalog import execution_model_supports_input
    from model_drivers.view_paths import ViewPathError, inspect_image_for_view

    ctx = _HARNESS_VIEW_CTX
    agent_name = ctx.get("agent_name") or os.environ.get("VERSA_AGENT_NAME", "")
    execution_model = ctx.get("execution_model") or ""
    remaining = int(ctx.get("steps_remaining") or 0)

    if remaining < MODALITY_VIEW_MIN_STEPS:
        return json.dumps({
            "success": False,
            "code": "late_cycle_cutoff",
            "error": (
                f"Modality tools refused — fewer than {MODALITY_VIEW_MIN_STEPS} steps remain. "
                "Wrap up: journal progress (agictl task progress), leave a handoff, and agictl cycle end."
            ),
            "steps_remaining": remaining,
        })

    if not execution_model_supports_input(execution_model, "image"):
        return json.dumps({
            "success": False,
            "code": "modality_unsupported",
            "error": (
                f"Execution model '{execution_model}' cannot perceive images "
                "(catalog input_modalities lacks 'image'). "
                "Use agictl agent set-model to assign a vision-capable catalog key, "
                "journal next actions, and agictl cycle end to respawn on the new model."
            ),
            "execution_model": execution_model,
        })

    try:
        result = inspect_image_for_view(path, agent_name)
    except ViewPathError as e:
        return json.dumps({"success": False, "code": e.code, "error": e.message})
    except OSError as e:
        return json.dumps({"success": False, "code": "io_error", "error": str(e)})

    result["execution_model"] = execution_model
    result["inject"] = True
    return json.dumps(result)


ALL_TOOLS.append(agictl_view_image)


def _build_view_image_message(
    payload: dict,
    provider_family: str,
) -> HumanMessage | None:
    """Build a multimodal HumanMessage for a successful view.

    The caller feeds this message to the model by *breaking and re-invoking*
    the stream (see the VIEW RE-INVOKE block in the main loop) — NOT via
    `agent.update_state`. A live `agent.stream(...)` Pregel loop holds its
    channels in memory and never re-reads an externally written checkpoint
    mid-run, so an `update_state` injection is invisible to the agent node's
    next turn — the model would answer without ever seeing the image. See
    "VIEW INJECT / multimodal re-invoke" in System Design § Development
    Standards.
    """
    from model_drivers.message_adapters import build_image_content_parts

    path = payload.get("path") or ""
    if not path:
        return None
    try:
        parts = build_image_content_parts(
            path,
            provider_family,
            caption=f"Agent requested view of image at {path}",
        )
        inject_id = f"view-inject-{uuid.uuid4()}"
        inject_msg = HumanMessage(content=parts, id=inject_id)
        fp = ""
        try:
            import hashlib
            digest = hashlib.sha256()
            with open(path, "rb") as img_f:
                digest.update(img_f.read(65536))
            fp = digest.hexdigest()[:12]
        except OSError:
            pass
        size_note = f" bytes={payload.get('bytes')}" if payload.get("bytes") else ""
        fp_note = f" sha256_12={fp}" if fp else ""
        src = payload.get("source_path") or path
        src_note = f" source={src}" if src and src != path else ""
        resize_note = ""
        if payload.get("resized_from") and payload.get("resized_to"):
            resize_note = f" resized={payload['resized_from']}→{payload['resized_to']}"
        tlog(
            f"VIEW INJECT: path={path}{size_note}{fp_note}{src_note}{resize_note} "
            f"model={payload.get('execution_model')} "
            f"provider_family={provider_family} id={inject_id}"
        )
        return inject_msg
    except Exception as e:
        tlog(f"VIEW INJECT: Failed — {e}")
        return None


def _trim_view_inject_payloads(agent, config, pending: list[dict] | None = None) -> int:
    """Strip image payloads from view-inject HumanMessages in the checkpoint.

    When *pending* is set, trims those message ids after the model turn that
    consumed the inject. Always scans for any remaining ``view-inject-*`` ids
    with image blocks (stale payloads from cycles where trim never ran).
    """
    from model_drivers.message_adapters import content_has_image_parts, trim_image_parts_from_message

    pending_ids: set[str] = set()
    paths_by_id: dict[str, str] = {}
    if pending:
        pending_ids = {p["message_id"] for p in pending if p.get("message_id")}
        paths_by_id = {p["message_id"]: p.get("path", "") for p in pending if p.get("message_id")}

    try:
        snapshot = agent.get_state(config)
        current = snapshot.values.get("messages", []) if snapshot else []
    except Exception as e:
        tlog(f"VIEW TRIM: Could not read checkpoint — {e}")
        return 0

    def _path_for_message(mid: str, content: object) -> str:
        if mid in paths_by_id and paths_by_id[mid]:
            return paths_by_id[mid]
        if isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and part.get("type") == "text":
                    text = part.get("text") or ""
                    if " at " in text:
                        return text.rsplit(" at ", 1)[-1].strip()
        return ""

    changed = False
    updated: list = []
    trimmed = 0
    for m in current:
        if not isinstance(m, HumanMessage):
            updated.append(m)
            continue
        mid = getattr(m, "id", None) or ""
        should_trim = (
            mid in pending_ids
            or (isinstance(mid, str) and mid.startswith("view-inject-"))
        )
        if should_trim and content_has_image_parts(m.content):
            path = _path_for_message(mid, m.content)
            new_content = trim_image_parts_from_message(m.content, path)
            updated.append(HumanMessage(content=new_content, id=mid))
            trimmed += 1
            changed = True
        else:
            updated.append(m)

    if changed:
        try:
            agent.update_state(
                config,
                {"messages": [RemoveMessage(id=REMOVE_ALL_MESSAGES)] + updated},
                as_node="__start__",
            )
            tlog(f"VIEW TRIM: removed image blocks from {trimmed} injected message(s)")
        except Exception as e:
            tlog(f"VIEW TRIM: Failed — {e}")
            return 0
    return trimmed


def _apply_view_surgical_trim(agent, config, pending: list[dict]) -> int:
    """Strip image payloads from injected view messages after the next agent turn."""
    return _trim_view_inject_payloads(agent, config, pending)



def write_telemetry(agent_name: str, total_tokens: int, prompt_tokens: int, completion_tokens: int, messages: list):
    if agent_name == "coa":
        telemetry_file = "/var/lib/versa-agi/coa/cycles/cycle_telemetry.json"
    else:
        telemetry_file = f"/var/lib/versa-agi/{agent_name}/cycles/cycle_telemetry.json"

    os.makedirs(os.path.dirname(telemetry_file), exist_ok=True)

    serializable_messages = []
    for m in messages:
        msg_dict = {"type": m.type, "content": m.content if isinstance(m.content, str) else str(m.content)}
        if hasattr(m, "tool_calls") and m.tool_calls:
            msg_dict["toolCalls"] = [{"name": tc.get("name", "unknown"), "status": "called"} for tc in m.tool_calls]
        serializable_messages.append(msg_dict)

    with open(telemetry_file, "w") as f:
        json.dump({
            "total_tokens": total_tokens,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "messages": serializable_messages
        }, f)


def _finalize_cycle_step_budget(agent_name: str, step_count: int, max_steps: int) -> None:
    """Close the open cycle row when the harness hits the hard step limit.

    Mirrors what `agictl cycle end` does so RECENT ACTIVITY summaries and wake
    context stay coherent when the agent ignores budget warnings.
    """
    summary = (
        f"[Harness] Step budget reached ({step_count}/{max_steps}). "
        "Work continues on next spawn — review task progress and recent cycle summaries."
    )
    try:
        proc = subprocess.run(
            ["agictl", "cycle", "end", summary, "--agent", agent_name],
            capture_output=True, text=True, timeout=15,
        )
        if proc.returncode != 0:
            err = (proc.stderr or proc.stdout or "").strip()
            tlog(f"STEP BUDGET: cycle finalize returned {proc.returncode}: {err}")
        else:
            tlog(f"STEP BUDGET: Cycle closed for resume ({step_count}/{max_steps})")
    except Exception as e:  # noqa: BLE001
        tlog(f"STEP BUDGET: Failed to finalize cycle — {e}")


# ═══════════════════════════════════════════════════════
# LLM Provider Resolution
# ═══════════════════════════════════════════════════════

def get_llm(model_name: str, num_ctx: int = 0, agent_overrides: dict | None = None):
    """Instantiate the correct LLM provider based on the model name.
    
    Provider routing (by model prefix):
      gemini-*  → ChatGoogleGenerativeAI (direct API)
      gpt-*     → ChatOpenAI (direct API — api.openai.com)
      claude-*  → ChatAnthropic (direct API — api.anthropic.com)
      grok-*    → ChatOpenAI (direct API — api.x.ai/v1, OpenAI-compatible)
      vendor/model (contains /) → ChatOpenAI (direct API — openrouter.ai/api/v1)
      *         → Local AI (catalog provider: ollama → ChatOllama, llamacpp → ChatOpenAI)
    
    Args:
        model_name: The model identifier (e.g. 'gemini-2.5-flash', 'gpt-5.5-2026-04-23')
        num_ctx: Context window size in tokens for Ollama models. 0 = Ollama default.
        agent_overrides: Optional per-agent param overrides (temperature, reasoning, extra).
    """
    from harness.model_params import (
        resolve_model_params,
        detect_provider_family,
        to_native_kwargs,
        apply_native_for_local_runtime,
        _load_catalog_provider,
    )
    from model_catalog import resolve_local_provider

    route = resolve_llm_route(model_name, num_ctx=num_ctx, agent_overrides=agent_overrides)
    provider_slug = route["catalog_provider"]
    family = route["provider_family"]
    native = to_native_kwargs(
        family, model_name,
        resolve_model_params(model_name, agent_overrides=agent_overrides),
        provider_slug=provider_slug or None,
    )
    local_provider = route.get("local_provider") or provider_slug or resolve_local_provider(route.get("gpu_backend", ""))
    if local_provider == "llamacpp":
        native = apply_native_for_local_runtime(native, "llamacpp")
    elif local_provider == "ollama" or family == "local":
        native = apply_native_for_local_runtime(native, "ollama")

    def _openai_compat(**base):
        merged = {**base, **{k: v for k, v in native.items() if k not in base}}
        return ChatOpenAI(**merged)

    # ── Gemini (Google) — direct API ──
    if model_name.startswith("gemini"):
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            raise ValueError("GEMINI_API_KEY is required for Gemini models.")
        gkwargs = {"model": model_name, "google_api_key": api_key}
        if "temperature" in native:
            gkwargs["temperature"] = native["temperature"]
        if native.get("model_kwargs"):
            gkwargs.update(native["model_kwargs"])
        return ChatGoogleGenerativeAI(**gkwargs)

    # ── OpenAI (GPT) — direct API ──
    if model_name.startswith("gpt"):
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise ValueError("OPENAI_API_KEY is required for OpenAI models. Set via: sudo agictl system set-key openai <key>")
        return _openai_compat(model=model_name, api_key=api_key)

    # ── Anthropic (Claude) — direct API ──
    if model_name.startswith("claude"):
        api_key = os.getenv("ANTHROPIC_API_KEY")
        if not api_key:
            raise ValueError("ANTHROPIC_API_KEY is required for Anthropic models. Set via: sudo agictl system set-key anthropic <key>")
        akwargs = {"model": model_name, "api_key": api_key}
        if native.get("model_kwargs"):
            akwargs.update(native["model_kwargs"])
        elif "temperature" in native:
            akwargs["temperature"] = native["temperature"]
        return ChatAnthropic(**akwargs)

    # ── xAI (Grok) — direct API via OpenAI-compatible endpoint ──
    if model_name.startswith("grok"):
        api_key = os.getenv("XAI_API_KEY")
        if not api_key:
            raise ValueError("XAI_API_KEY is required for xAI models. Set via: sudo agictl system set-key xai <key>")
        return _openai_compat(base_url="https://api.x.ai/v1", model=model_name, api_key=api_key)

    # ── OpenRouter (namespaced vendor/model IDs) — direct API ──
    if "/" in model_name:
        api_key = os.getenv("OPENROUTER_API_KEY")
        if not api_key:
            raise ValueError(
                "OPENROUTER_API_KEY is required for OpenRouter models. "
                "Set via: sudo agictl system set-key openrouter <key>"
            )
        return _openai_compat(
            base_url="https://openrouter.ai/api/v1",
            model=model_name,
            api_key=api_key,
            default_headers={
                "HTTP-Referer": "https://versavoice.ai",
                "X-Title": "Versa AGi",
            },
        )

    # ── Local AI (Ollama / llama.cpp SYCL) ──
    if route["local_provider"] == "llamacpp":
        return _openai_compat(
            base_url=route["endpoint"],
            api_key="sk-local",
            model=route["api_model"],
        )

    kwargs = {"base_url": route["endpoint"], "model": model_name}
    if "temperature" in native:
        kwargs["temperature"] = native["temperature"]
    for k, v in native.items():
        if k not in ("temperature", "extra_body", "model_kwargs", "reasoning_effort"):
            kwargs[k] = v
    if num_ctx and num_ctx > 0:
        kwargs["num_ctx"] = num_ctx
    return ChatOllama(**kwargs)


# ═══════════════════════════════════════════════════════
# Main Entry Point
# ═══════════════════════════════════════════════════════

def main():
    global TOOL_OUTPUT_LIMIT
    parser = argparse.ArgumentParser(description="Versa AGi LangGraph Harness")
    parser.add_argument("--agent", required=True, help="Name of the agent")
    parser.add_argument("--system-file", required=True, help="Path to the system prompt file")
    parser.add_argument("--wake-file", required=True, help="Path to the wake reason prompt file")
    parser.add_argument("--model", required=True, help="Model to execute")
    parser.add_argument("--max-steps", type=int, default=50, help="Maximum number of agent graph steps")
    parser.add_argument("--tool-budget", type=int, default=6000, help="Maximum character output limit for tools")
    parser.add_argument("--thread-id", default=None, help="Thread ID for checkpoint persistence (agent_id-project_id)")
    parser.add_argument("--resume", action="store_true", help="Resume from last checkpoint for this thread")
    parser.add_argument("--skills-dir", default=None, help="Path to agent skills directory for dynamic injection")
    parser.add_argument("--triage-model", default=None, help="Model for triage node (falls back to --model if not set)")
    parser.add_argument("--num-ctx", type=int, default=0, help="Context window size in tokens for Ollama (0 = model default)")
    parser.add_argument("--temperature", type=float, default=None, help="Per-agent temperature override (omit = inherit)")
    parser.add_argument("--reasoning-effort", default=None, help="Per-agent reasoning effort override (omit = inherit)")
    parser.add_argument("--reasoning-max-tokens", type=int, default=None, help="Per-agent reasoning token budget (omit = inherit)")
    parser.add_argument("--model-params-extra", default=None, help="Per-agent extra params JSON passthrough (omit = inherit)")
    parser.add_argument("--tasks-file", default=None, help="Path to pre-computed active tasks context for triage")
    parser.add_argument("--convo-file", default=None, help="Path to pre-computed conversation history for triage")
    parser.add_argument("--routing-file", default=None, help="Path to ephemeral model routing JSON from lifeline")
    parser.add_argument("--resume-max-messages", type=int, default=0, help="Trim checkpoint to last N messages on resume (0 = unlimited)")
    parser.add_argument("--skill-mode", default="hybrid", choices=["full", "lazy", "hybrid"], help="Skill injection mode: full (inject all), lazy (manifest only), hybrid (core injected + lazy manifest)")
    args = parser.parse_args()

    TOOL_OUTPUT_LIMIT = args.tool_budget

    with open(args.system_file, "r") as f:
        system_prompt = f.read()

    # Resolve skills directory early — used by both CLI reference injection and triage
    skills_dir = getattr(args, 'skills_dir', None)

    # ── Always-Inject skill blocks (static content) ──
    # Collected first, then inserted BEFORE the LIVE SITUATION sentinel so the
    # static prompt prefix (cache-eligible) includes them — appending after the
    # per-cycle data would make this content uncacheable (System Design §3.1,
    # Poise Layout Design Pattern).
    DYNAMIC_BOUNDARY_SENTINEL = "## ── LIVE SITUATION"
    static_skill_blocks = []

    # ── Always-Inject: CLI Reference (agent subset) ──
    # cli_reference_agent.md — always injected for every agent (token-efficient spawn default).
    # COA loads full cli_reference.md on demand via agictl_execute (see COA block below).
    cli_ref_injected = False
    if skills_dir:
        cli_ref_path = os.path.join(skills_dir, "cli_reference_agent.md")
        if os.path.isfile(cli_ref_path):
            try:
                with open(cli_ref_path, "r") as f:
                    cli_ref_content = f.read()
                static_skill_blocks.append(f"\n\n---\n## ── TOOL REFERENCE: cli_reference_agent.md ──\n\n{cli_ref_content}")
                cli_ref_injected = True
                tlog(f"CLI REFERENCE (agent): Injected ({len(cli_ref_content)} chars)")
            except Exception as e:
                tlog(f"CLI REFERENCE (agent): Failed to read — {e}")

    # ── COA: full operator CLI reference (on demand, not auto-injected) ──
    if skills_dir and args.agent == "coa":
        full_ref_path = os.path.join(skills_dir, "cli_reference.md")
        if os.path.isfile(full_ref_path):
            static_skill_blocks.append(
                "\n\n---\n## ── COA: FULL CLI REFERENCE (load on demand) ──\n"
                "This spawn includes **cli_reference_agent.md** only. For model catalog, provider CRUD, "
                "admin commands, and operator-only groups, load the full manual **before** that work:\n"
                "- tool **`agictl_execute`**, argument **`bash \"cat ~/.agent/skills/cli_reference.md\"`**\n"
                "Sub-agents do not have this file — never reference it to them.\n"
            )
            tlog("CLI REFERENCE (full): on-demand via ~/.agent/skills/cli_reference.md")

    # ── Always-Inject: Skill Authoring (COA-exclusive) ──
    # skill_authoring.md is injected only for COA — sub-agents never see it.
    if skills_dir and args.agent == "coa":
        skill_auth_path = os.path.join(skills_dir, "skill_authoring.md")
        if os.path.isfile(skill_auth_path):
            try:
                with open(skill_auth_path, "r") as f:
                    skill_auth_content = f.read()
                static_skill_blocks.append(f"\n\n---\n## ── SKILL AUTHORING REFERENCE ──\n\n{skill_auth_content}")
                tlog(f"SKILL AUTHORING: Injected ({len(skill_auth_content)} chars)")
            except Exception as e:
                tlog(f"SKILL AUTHORING: Failed to read — {e}")

    # ── Always-Inject: Memory Management (Awareness-First Procedure) ──
    # memory_management.md is mandatory — agents MUST execute the 5-step
    # awareness procedure every cycle. Never gated behind triage.
    if skills_dir:
        mem_skill_path = os.path.join(skills_dir, "memory_management.md")
        if os.path.isfile(mem_skill_path):
            try:
                with open(mem_skill_path, "r") as f:
                    mem_skill_content = f.read()
                static_skill_blocks.append(f"\n\n---\n## ── MANDATORY: MEMORY & AWARENESS PROCEDURE ──\n**This procedure MUST be executed before ending every cycle.**\n\n{mem_skill_content}")
                tlog(f"MEMORY SKILL: Injected ({len(mem_skill_content)} chars)")
            except Exception as e:
                tlog(f"MEMORY SKILL: Failed to read — {e}")

    # ── Always-Inject: Communication Basic (essential messaging rules) ──
    # communication_basic.md is a ~2 KB condensed version of the full communication skill.
    # Always present — agents must know how to send/receive messages correctly.
    if skills_dir:
        comm_basic_path = os.path.join(skills_dir, "communication_basic.md")
        if os.path.isfile(comm_basic_path):
            try:
                with open(comm_basic_path, "r") as f:
                    comm_basic_content = f.read()
                static_skill_blocks.append(f"\n\n---\n## ── COMMUNICATION RULES ──\n\n{comm_basic_content}")
                tlog(f"COMMUNICATION BASIC: Injected ({len(comm_basic_content)} chars)")
            except Exception as e:
                tlog(f"COMMUNICATION BASIC: Failed to read — {e}")

    # ── Insert static skill blocks at the cache boundary ──
    if static_skill_blocks:
        skills_payload = "".join(static_skill_blocks)
        boundary_idx = system_prompt.find(DYNAMIC_BOUNDARY_SENTINEL)
        if boundary_idx != -1:
            system_prompt = (
                system_prompt[:boundary_idx].rstrip()
                + skills_payload
                + "\n\n---\n\n"
                + system_prompt[boundary_idx:]
            )
            tlog(f"STATIC SKILLS: Inserted at LIVE SITUATION boundary ({len(skills_payload)} chars)")
        else:
            # Legacy poise without sentinel — fall back to appending
            system_prompt += skills_payload
            tlog(f"STATIC SKILLS: No boundary sentinel found — appended ({len(skills_payload)} chars)")

    with open(args.wake_file, "r") as f:
        wake_prompt = f.read()

    # Read pre-computed triage context files (written by lifeline.sh)
    tasks_context = ""
    if args.tasks_file and os.path.isfile(args.tasks_file):
        with open(args.tasks_file, "r") as f:
            tasks_context = f.read().strip()

    convo_context = ""
    if args.convo_file and os.path.isfile(args.convo_file):
        with open(args.convo_file, "r") as f:
            convo_context = f.read().strip()

    # ── Session Type ──
    # Determined later by actual checkpoint state inspection, not just --resume flag.
    session_type = "NEW"
    thread_label = args.thread_id or "none"

    from harness.model_params import agent_overrides_from_values
    agent_param_overrides = agent_overrides_from_values(
        temperature=args.temperature,
        reasoning_effort=args.reasoning_effort,
        reasoning_max_tokens=args.reasoning_max_tokens,
        model_params_extra=args.model_params_extra,
    )

    routing_context = None
    if args.routing_file and os.path.isfile(args.routing_file):
        try:
            with open(args.routing_file, "r") as rf:
                routing_context = json.load(rf)
            cand_n = len((routing_context or {}).get("candidates") or [])
            tlog(f"ROUTING: loaded context from {args.routing_file} (mode={(routing_context or {}).get('mode')}, candidates={cand_n})")
        except Exception as e:
            tlog(f"ROUTING: failed to load {args.routing_file} — {e}")
    else:
        if args.routing_file:
            tlog(f"ROUTING: file missing — {args.routing_file}")
        else:
            tlog("ROUTING: no routing file passed (model_routing_enabled off or empty pool)")

    # ── Checkpointer Setup ──
    checkpointer = None
    config = {"recursion_limit": args.max_steps * 3}

    if args.thread_id:
        try:
            from langgraph.checkpoint.sqlite import SqliteSaver
            checkpoint_path = f"/var/lib/versa-agi/{args.agent}/cycles/checkpoints.db"
            os.makedirs(os.path.dirname(checkpoint_path), exist_ok=True)
            db_conn = sqlite3.connect(checkpoint_path, check_same_thread=False)
            checkpointer = SqliteSaver(db_conn)
            config["configurable"] = {"thread_id": args.thread_id}
            tlog(f"CHECKPOINT: {checkpoint_path} (thread: {args.thread_id})")
        except ImportError:
            tlog("CHECKPOINT: langgraph-checkpoint-sqlite not installed — running without persistence")
        except Exception as e:
            tlog(f"CHECKPOINT: Failed to initialize — {e}")

    # ── Triage Node ──
    # Runs a lightweight classification before the main agent loop.
    # Uses a separate model if --triage-model is set, otherwise falls back to --model.
    from harness.triage import run_triage, inject_skills, build_triage_context, enrich_triage_from_inbox

    triage_model_name = getattr(args, 'triage_model', None) or args.model
    triage_llm = get_llm(triage_model_name, agent_overrides=agent_param_overrides)

    tlog(f"TRIAGE MODEL: {triage_model_name}")
    if skills_dir:
        tlog(f"SKILLS DIR: {skills_dir}")

    # Run triage classification (before main LLM — ephemeral routing decision)
    triage_result = run_triage(
        llm=triage_llm,
        wake_prompt=wake_prompt,
        tasks_context=tasks_context,
        conversation_context=convo_context,
        skills_dir=skills_dir,
        agent_name=args.agent,
        routing_context=routing_context,
    )
    triage_result = enrich_triage_from_inbox(triage_result, args.agent)

    from harness.model_routing import resolve_execution_model
    execution_model, routing_mode, routing_work_modality = resolve_execution_model(
        routing_context, triage_result, args.model, agent_name=args.agent,
        wake_prompt=wake_prompt,
    )
    tlog(f"EXECUTION MODEL: {execution_model} (assigned={args.model}, mode={routing_mode})")
    if routing_work_modality:
        tlog(f"ROUTING: work_modality={routing_work_modality}")
    if routing_context and routing_mode == "none":
        rec = getattr(triage_result, "recommended_model", None)
        if not rec and not routing_work_modality:
            tlog("ROUTING: triage omitted work_modality and recommended_model — kept assigned model")

    cycle_id = os.environ.get("VERSA_CYCLE_ID", "")
    if cycle_id:
        try:
            import subprocess as _sp
            cmd = [
                "agictl", "cycle", "set-routing", cycle_id,
                "--assigned-model", args.model,
                "--execution-model", execution_model,
                "--routing-mode", routing_mode,
            ]
            if routing_work_modality:
                cmd.extend(["--work-modality", routing_work_modality])
            _sp.run(cmd, capture_output=True, text=True, timeout=15)
            tlog(f"ROUTING: recorded on cycle {cycle_id}")
        except Exception as e:
            tlog(f"ROUTING: cycle set-routing failed — {e}")

    llm = get_llm(execution_model, num_ctx=args.num_ctx, agent_overrides=agent_param_overrides)

    from harness.model_params import detect_provider_family
    from model_catalog import catalog_entry_for_model

    _cat_entry = catalog_entry_for_model(execution_model)
    _provider_slug = (_cat_entry or {}).get("provider")
    _HARNESS_VIEW_CTX.update({
        "agent_name": args.agent,
        "execution_model": execution_model,
        "provider_family": detect_provider_family(execution_model, _provider_slug),
    })

    # Inject skills based on triage classification
    # Filter out always-injected skills — they're not triage-driven.
    skill_content = ""
    always_injected = {
        "cli_reference_agent.md",
        "skill_authoring.md",
        "memory_management.md",
        "communication_basic.md",
        "communication.md",
    }
    skill_mode = getattr(args, 'skill_mode', 'hybrid')
    tlog(f"SKILL MODE: {skill_mode}")
    if skills_dir and triage_result.skills_to_inject:
        triage_result.skills_to_inject = [s for s in triage_result.skills_to_inject if s not in always_injected]
        # ── Override Resolution ──
        # Before injecting a skill, check if {name}_override.md exists.
        # If an override exists, inject it instead of the shipped version.
        resolved_skills = []
        for skill_name in triage_result.skills_to_inject:
            base = skill_name.replace(".md", "")
            override_path = os.path.join(skills_dir, f"{base}_override.md")
            if os.path.isfile(override_path):
                resolved_skills.append(f"{base}_override.md")
                tlog(f"SKILL OVERRIDE: {skill_name} → {base}_override.md")
            else:
                resolved_skills.append(skill_name)
        triage_result.skills_to_inject = resolved_skills
        if triage_result.skills_to_inject:
            if skill_mode == "full":
                # Full mode: inject entire skill content (legacy behavior)
                skill_content = inject_skills(triage_result, skills_dir)
            elif skill_mode in ("hybrid", "lazy"):
                # Hybrid/Lazy mode: generate a compact manifest instead of full content.
                # Agents load full skill files on-demand via agictl execute bash "cat <path>".
                manifest_lines = []
                for skill_name in triage_result.skills_to_inject:
                    skill_path = os.path.join(skills_dir, skill_name)
                    # Extract first non-empty, non-heading line as description
                    desc = skill_name
                    if os.path.isfile(skill_path):
                        try:
                            with open(skill_path, "r") as sf:
                                for line in sf:
                                    line = line.strip()
                                    if line and not line.startswith("#") and not line.startswith(">"):
                                        desc = line[:120]
                                        break
                        except Exception:
                            pass
                    manifest_lines.append(f"- **{skill_name}** → `~/.agent/skills/{skill_name}` — {desc}")
                    tlog(f"SKILL MANIFEST: {skill_name} (lazy)")

                if manifest_lines:
                    skill_content = (
                        "\n\n---\n## ── SKILLS AVAILABLE ──\n\n"
                        "**Load these skills BEFORE performing related work.** "
                        "Command: `agictl execute bash \"cat <path>\"`\n\n"
                        + "\n".join(manifest_lines)
                    )
            else:
                skill_content = inject_skills(triage_result, skills_dir)

    # Build enhanced system prompt with injected skills
    enhanced_prompt = system_prompt
    if skill_content:
        enhanced_prompt = system_prompt + "\n" + skill_content

    # Build triage-enhanced wake prompt
    triage_context = build_triage_context(triage_result)
    enhanced_wake = f"{triage_context}\n\n---\n\n{wake_prompt}"

    # ── Route Decision ──
    # confidence >= 0.7: proceed to agent
    # confidence < 0.7 + parallel viable: clarify AND continue
    # confidence < 0.7 + no parallel work: clarify and exit
    if triage_result.classification == "clarification_needed" and triage_result.confidence < 0.7 and not triage_result.parallel_work_viable:
        tlog(f"TRIAGE: Low confidence ({triage_result.confidence:.2f}), no parallel work — clarify & exit path")
        # The agent will still run but with explicit instructions to clarify and exit
        enhanced_wake = f"{triage_context}\n\n⚠ TRIAGE DIRECTIVE: Confidence is low and no parallel work is viable. Send a clarification message to the sender addressing the negative signals listed above, then end your cycle.\n\n---\n\n{wake_prompt}"

    # Persist the effective prompt (post always-inject + triage skill payload + wake)
    # so agitop's System Prompt tab matches what the model receives. Lifeline wrote
    # a pre-harness snapshot to the same path before spawn; overwrite here.
    last_prompt_path = f"/var/lib/versa-agi/{args.agent}/last_prompt.txt"
    try:
        with open(last_prompt_path, "w", encoding="utf-8") as f:
            f.write(enhanced_prompt)
            if not enhanced_prompt.endswith("\n"):
                f.write("\n")
            f.write(enhanced_wake)
            if not enhanced_wake.endswith("\n"):
                f.write("\n")
        tlog(f"LAST_PROMPT: Wrote effective prompt ({len(enhanced_prompt) + len(enhanced_wake)} chars) → {last_prompt_path}")
    except Exception as e:
        tlog(f"LAST_PROMPT: Failed to write {last_prompt_path} — {e}")

    # ── Agent Construction ──
    # The system prompt is always loaded fresh from the poise file — never part of checkpoint state.

    # Context Trimming: pre_model_hook trims messages before sending to LLM.
    # Full history is preserved in the checkpoint — only the LLM input is trimmed.
    # Uses actual character count as a conservative proxy for tokens (~3 chars/token).
    # Model-aware: cloud providers have known context limits; local models use num_ctx.

    # ── Model-Aware Context Window ──
    # get_trimmer_char_limit returns the raw model budget (token window × 80%
    # headroom × 3 chars/token). The system prompt and tool schemas are sent
    # with EVERY request but are not part of state["messages"] — the trimmer
    # never sees them. Subtract them explicitly so the headroom is real, with
    # a floor so a pathologically large prompt cannot zero out the budget.
    MODEL_CHAR_BUDGET = get_trimmer_char_limit(args.model, args.num_ctx)
    TOOL_SCHEMA_CHARS = sum(
        len(t.name) + len(t.description or "") + len(str(t.args))
        for t in ALL_TOOLS
    )
    TRIM_BUDGET_FLOOR = 32_000  # ~10K tokens — minimum working context
    CONTEXT_WINDOW_CHARS = max(
        MODEL_CHAR_BUDGET - len(enhanced_prompt) - TOOL_SCHEMA_CHARS,
        TRIM_BUDGET_FLOOR,
    )
    tlog(f"CONTEXT BUDGET: model={MODEL_CHAR_BUDGET:,} chars − system prompt "
         f"{len(enhanced_prompt):,} − tool schemas {TOOL_SCHEMA_CHARS:,} "
         f"→ trim limit {CONTEXT_WINDOW_CHARS:,} chars")

    # Per-message serialization overhead (role, type, name, structural JSON).
    # Conservative flat estimate — keeps the proxy honest for many-message histories.
    MESSAGE_OVERHEAD_CHARS = 40

    # Fixed char budget for vision blocks in trim accounting (not wire size).
    # Full base64 would blow the trim window and drop the entire history (23→0).
    VISION_PART_CHAR_BUDGET = 16_000

    def _count_dict_part(part: dict) -> int:
        """Count all string payloads in a content part — not just 'text'.

        Providers return list-content parts keyed by type: 'text', 'thinking'/
        'reasoning' (extended thinking blocks, re-sent on every turn), 'data'
        (base64 media), 'executable_code', etc. Counting only 'text' undercounts
        — sometimes massively (base64 images, long thinking traces).
        """
        ptype = part.get("type", "")
        if ptype in ("image_url", "image", "media"):
            return VISION_PART_CHAR_BUDGET

        total = 0
        for value in part.values():
            if isinstance(value, str):
                total += len(value)
            elif isinstance(value, dict):
                total += _count_dict_part(value)
            elif isinstance(value, list):
                for v in value:
                    if isinstance(v, str):
                        total += len(v)
                    elif isinstance(v, dict):
                        total += _count_dict_part(v)
        return total

    def _count_message_chars(messages):
        """Count total characters across all messages for context window management.

        Sums: message .content (str or multimodal list — ALL part payloads),
        tool_call name/arguments, tool_call_id fields, and a flat per-message
        serialization overhead — all of which contribute to the API token count.
        Used with a conservative ~3 chars ≈ 1 token budget conversion.
        """
        total = 0
        for m in messages:
            total += MESSAGE_OVERHEAD_CHARS
            # Content: string or multimodal list
            if isinstance(m.content, str):
                total += len(m.content)
            elif isinstance(m.content, list):
                for part in m.content:
                    if isinstance(part, str):
                        total += len(part)
                    elif isinstance(part, dict):
                        total += _count_dict_part(part)
            # Tool calls: name + arguments are sent to the LLM as structured input
            if hasattr(m, "tool_calls") and m.tool_calls:
                for tc in m.tool_calls:
                    total += len(tc.get("name", "") or "")
                    args_val = tc.get("args", {})
                    if isinstance(args_val, str):
                        total += len(args_val)
                    else:
                        total += len(str(args_val))
            # Tool call ID (present on ToolMessage responses)
            if hasattr(m, "tool_call_id") and m.tool_call_id:
                total += len(m.tool_call_id)
        return total

    def pre_model_hook(state):
        """Trim messages to fit context window before LLM call.
        Returns llm_input_messages (not messages) to preserve full checkpoint history."""
        all_msgs = state["messages"]
        trimmed = trim_messages(
            all_msgs,
            max_tokens=CONTEXT_WINDOW_CHARS,
            strategy="last",
            token_counter=_count_message_chars,
            include_system=True,     # always preserve system prompt
            start_on="human",        # ensure valid message ordering
            allow_partial=False,
        )
        if len(trimmed) < len(all_msgs):
            tlog(f"CONTEXT TRIM: {len(all_msgs)} → {len(trimmed)} messages "
                 f"({_count_message_chars(trimmed):,} chars, limit: {CONTEXT_WINDOW_CHARS:,})")
        if not trimmed and all_msgs:
            # Never send an empty window — keep the tail even if over budget.
            trimmed = all_msgs[-min(4, len(all_msgs)):]
            tlog(
                f"CONTEXT TRIM: trim_messages returned empty — kept last "
                f"{len(trimmed)} message(s) as fallback"
            )
        return {"llm_input_messages": trimmed}

    agent_kwargs = {
        "model": llm,
        "tools": ALL_TOOLS,
        "prompt": enhanced_prompt,
        "pre_model_hook": pre_model_hook,
    }
    if checkpointer:
        agent_kwargs["checkpointer"] = checkpointer

    agent = create_react_agent(**agent_kwargs)

    # ── Checkpoint State Inspection & Repair ──
    # Two-pass approach:
    # Pass 1 (always): Lightweight SQL query to determine if a checkpoint exists (NEW vs RESUME).
    # Pass 2 (always on RESUME): Scan the tail of the checkpoint for dangling tool calls.
    #   - Dangling calls occur when a previous cycle was hard-killed (timeout, runaway, crash)
    #     mid-execution, leaving AIMessages with tool_calls but no corresponding ToolMessages.
    #   - Only scans the last N messages — dangling tool_calls can only exist at the tail.
    #   - Cost is negligible (~50ms for a get_state + 10-message scan).
    if checkpointer and "configurable" in config:
        # Pass 1: Lightweight session type check (no message deserialization)
        try:
            cursor = db_conn.execute(
                "SELECT COUNT(*) FROM checkpoints WHERE thread_id = ?",
                (args.thread_id,),
            )
            has_checkpoint = cursor.fetchone()[0] > 0
            session_type = "RESUME" if has_checkpoint else "NEW"
        except Exception:
            session_type = "NEW"  # Table may not exist yet

        # ── Checkpoint integrity & depth trim (single atomic pass) ──
        # On RESUME the persisted history can be malformed in ways every provider
        # rejects (INVALID_CHAT_HISTORY): dangling tool_calls (an AIMessage whose
        # ToolMessage never landed), orphan ToolMessages (no preceding producer),
        # or a window not starting at a HumanMessage. Instead of mutating in place
        # with RemoveMessage batches — which can PARTIAL-FAIL and leave a corrupted
        # state (the thread 93-0 incident) — we compute a canonical, valid sequence
        # and clear-and-reseed it ATOMICALLY via the REMOVE_ALL_MESSAGES sentinel.
        # That single write cannot partial-fail and repairs even a dangling call
        # buried mid-history (the placeholder is inserted adjacent to its parent).
        if session_type == "RESUME":
            try:
                snapshot = agent.get_state(config)
                current = snapshot.values.get("messages", []) if snapshot else []
                if not current:
                    tlog(f"CHECKPOINT: Resume with empty state (thread: {args.thread_id})")
                else:
                    clean, changed, stats = _canonicalize_messages(current, args.resume_max_messages)
                    # A non-empty `next` means the prior cycle was interrupted
                    # mid-superstep (e.g. `agictl cycle end` SIGTERM during a
                    # parallel tool batch, timeout, or runaway kill). The
                    # interrupted step's PENDING WRITES are not folded into the
                    # committed `values` that canonicalize inspects — so a
                    # dangling AIMessage(tool_calls) can be invisible here yet
                    # replayed on the next invoke, crashing with
                    # INVALID_CHAT_HISTORY (the thread 93-0 incident). Reseeding
                    # at `__start__` supersedes that checkpoint and discards the
                    # pending writes, so the resume starts from the committed,
                    # canonical transcript instead of replaying a dead step.
                    pending_next = tuple(getattr(snapshot, "next", ()) or ())
                    if changed or pending_next:
                        agent.update_state(
                            config,
                            {"messages": [RemoveMessage(id=REMOVE_ALL_MESSAGES)] + clean},
                            as_node="__start__",
                        )
                        post = agent.get_state(config)
                        post_count = len(post.values.get("messages", [])) if post else -1
                        depth = args.resume_max_messages if args.resume_max_messages > 0 else "unlimited"
                        pending_note = f", cleared pending step next={pending_next}" if pending_next else ""
                        tlog(
                            f"CHECKPOINT REPAIR: {len(current)} → {post_count} messages "
                            f"(trimmed {stats['trimmed']}, orphans dropped {stats['orphans']}, "
                            f"placeholders {stats['placeholders']}, max: {depth}{pending_note})"
                        )
                        if not clean:
                            session_type = "NEW"
                    else:
                        tlog(
                            f"CHECKPOINT: Clean resume — {len(current)} messages, "
                            f"no repair needed (thread: {args.thread_id})"
                        )
                    stale_views = _trim_view_inject_payloads(agent, config)
                    if stale_views:
                        tlog(
                            f"CHECKPOINT: Trimmed stale view-inject image payload(s) "
                            f"from prior cycle(s) ({stale_views})"
                        )
            except Exception as e:
                # Any failure means we cannot guarantee a valid history. Wipe the
                # thread so the next invoke starts from a known-good empty state
                # rather than risk an INVALID_CHAT_HISTORY crash.
                tlog(f"CHECKPOINT REPAIR: Failed — {e}. Wiping thread for a clean start.")
                session_type = "NEW"
                try:
                    checkpointer.conn.execute("DELETE FROM checkpoints WHERE thread_id = ?", (args.thread_id,))
                    checkpointer.conn.execute("DELETE FROM writes WHERE thread_id = ?", (args.thread_id,))
                    checkpointer.conn.commit()
                    tlog(f"CHECKPOINT: Corrupted thread {args.thread_id} wiped — starting fresh")
                except Exception as de:
                    tlog(f"CHECKPOINT: Could not wipe thread — {de}")

    # ── Startup Context Log ──
    # Printed AFTER checkpoint inspection so session_type is accurate.
    tlog("=" * 60)
    tlog(f"[{session_type}] AGENT: {args.agent} | THREAD: {thread_label}")
    tlog(f"ASSIGNED: {args.model} | TRIAGE: {triage_model_name} | EXECUTION: {execution_model}")
    _log_llm_resolution("triage", triage_model_name, 0, agent_param_overrides)
    _log_llm_resolution("execution", execution_model, args.num_ctx, agent_param_overrides)
    if routing_mode != "none" or routing_work_modality:
        rwm = f", work_modality={routing_work_modality}" if routing_work_modality else ""
        tlog(f"ROUTING: mode={routing_mode}{rwm}")
    if getattr(triage_result, "required_work_modality", None):
        tlog(f"  TRIAGE WORK MODALITY: {triage_result.required_work_modality}")
    if getattr(triage_result, "recommended_model", None):
        tlog(f"  TRIAGE RECOMMENDED: {triage_result.recommended_model}")
    num_ctx_display = f"{args.num_ctx:,}" if args.num_ctx > 0 else "default"
    resume_display = f"{args.resume_max_messages}" if args.resume_max_messages > 0 else "unlimited"
    tlog(f"BUDGET: {args.max_steps} steps | TOOL LIMIT: {args.tool_budget} chars | NUM_CTX: {num_ctx_display} | RESUME DEPTH: {resume_display}")
    tlog(f"SYSTEM PROMPT: {len(enhanced_prompt)} chars ({len(enhanced_prompt.splitlines())} lines)")
    sections = [s.strip().lstrip("#").strip() for s in enhanced_prompt.splitlines() if s.strip().startswith("## ")]
    if sections:
        tlog(f"  SECTIONS: {' | '.join(sections[:10])}")
    if skill_content:
        tlog(f"  INJECTED SKILLS: {', '.join(triage_result.skills_to_inject)} ({len(skill_content)} chars)")
    wake_lines = [l for l in wake_prompt.splitlines() if l.strip()]
    tlog(f"WAKE PROMPT: {len(wake_prompt)} chars ({len(wake_lines)} lines)")
    for line in wake_lines[:3]:
        tlog(f"  {line[:120]}")
    if len(wake_lines) > 3:
        tlog(f"  ... ({len(wake_lines) - 3} more lines)")
    tlog(f"TRIAGE: {triage_result.classification} (confidence={triage_result.confidence:.2f})")
    if triage_result.strategy_notes:
        tlog(f"  STRATEGY: {triage_result.strategy_notes}")
    if triage_result.project_id is not None:
        tlog(f"  PROJECT: {triage_result.project_id} → thread: {triage_result.thread_id}")
    if triage_result.task_actions:
        tlog(f"  TASK ACTIONS: {triage_result.task_actions}")
    if triage_result.signal_results:
        signals = " | ".join(f"{k}={'✓' if v else '✗'}" for k, v in triage_result.signal_results.items())
        tlog(f"  SIGNALS: {signals}")
    if triage_result.has_attachments:
        tlog(f"  ATTACHMENTS: {triage_result.attachment_paths}")
    tlog(f"TOOLS: {', '.join(t.name for t in ALL_TOOLS)} ({len(ALL_TOOLS)} total)")
    tlog("=" * 60)

    # ── Message Initialization ──
    # On resume: triage-enhanced wake prompt adds new context to existing checkpoint state
    # On fresh: triage-enhanced wake prompt is the initial human message
    # Stable ids so a transient-retry can re-pass the same input idempotently
    # (add_messages upserts by id — without a fixed id it would assign a new
    # UUID on retry and duplicate the wake/warning message).
    messages = [HumanMessage(content=enhanced_wake, id=str(uuid.uuid4()))]

    step_count = 0
    max_steps = args.max_steps
    budget_80 = int(max_steps * 0.80)
    budget_95 = int(max_steps * 0.95)
    warned_80 = False
    warned_95 = False
    cycle_ended = False
    budget_hard_stop = False
    input_messages = messages  # Initial input for first stream invocation
    _harness_crashed = False
    pending_view_trims: list[dict] = []
    pending_view_injects: list[HumanMessage] = []

    # Bounded retry for transient transport errors
    # blip from the model edge). The graph is checkpointed, so on a transient
    # failure we simply re-invoke and resume from the last checkpoint; only an
    # exhausted retry budget (or a non-transient error) crashes the cycle.
    MAX_TRANSIENT_RETRIES = 4

    try:
        while step_count < max_steps and not cycle_ended:
            _HARNESS_VIEW_CTX["steps_remaining"] = max_steps - step_count
            # Each stream invocation processes until a budget threshold or completion.
            # With checkpointing, the graph state persists between invocations —
            # we only need to pass NEW messages (e.g., the budget warning).
            #
            # The inner `while True` retries this invocation on a transient
            # transport error: the checkpointer holds all completed steps, so we
            # re-invoke with the SAME input (idempotent by id) to resume.
            transient_attempt = 0
            stream_natural_end = False
            while True:
                try:
                    for chunk in agent.stream({"messages": input_messages}, config=config):
                        step_count += 1
                        if "agent" in chunk:
                            msg = chunk["agent"]["messages"][0]
                            messages.append(msg)
                            if hasattr(msg, "tool_calls") and msg.tool_calls:
                                tool_names = ", ".join(tc.get("name", "?") for tc in msg.tool_calls)
                                tlog(f"[STEP {step_count}/{max_steps}] AGENT → tool call: {tool_names}")
                            else:
                                content = msg.content if isinstance(msg.content, str) else str(msg.content)
                                preview = content[:200].replace("\n", " ")
                                tlog(f"[STEP {step_count}/{max_steps}] AGENT → {preview}")
                            # Trim after any agent turn once the model has consumed the inject
                            # (previously only ran on non-tool replies, so tool-heavy cycles
                            # left full base64 images in the checkpoint).
                            if pending_view_trims:
                                _trim_view_inject_payloads(agent, config, pending_view_trims)
                                pending_view_trims.clear()
                        elif "tools" in chunk:
                            msg = chunk["tools"]["messages"][0]
                            messages.append(msg)
                            content = msg.content if isinstance(msg.content, str) else str(msg.content)
                            preview = content[:200].replace("\n", " ")
                            tlog(f"[STEP {step_count}/{max_steps}] TOOL  ← {preview}")

                            tool_name = getattr(msg, "name", "") or ""
                            if tool_name == "agictl_view_image" and isinstance(content, str):
                                try:
                                    view_payload = json.loads(content)
                                except json.JSONDecodeError:
                                    view_payload = {}
                                if view_payload.get("success") and view_payload.get("inject"):
                                    inject_msg = _build_view_image_message(
                                        view_payload,
                                        _HARNESS_VIEW_CTX.get("provider_family", "openai_compat"),
                                    )
                                    if inject_msg and inject_msg.id:
                                        # Queue for break-and-reinvoke (see VIEW RE-INVOKE
                                        # below). Mid-stream update_state does NOT reach the
                                        # running agent node, so we must re-invoke the stream.
                                        pending_view_injects.append(inject_msg)
                                        pending_view_trims.append({
                                            "message_id": inject_msg.id,
                                            "path": view_payload.get("path", ""),
                                        })

                            # ── Cycle End Detection ──
                            # When the agent calls `agictl cycle end`, break immediately so
                            # telemetry writes before the process terminates.
                            if "\U0001f6d1 Cycle ended:" in content:
                                tlog(f"\n--- CYCLE END DETECTED (step {step_count}) ---")
                                cycle_ended = True
                                break

                        # ── VIEW RE-INVOKE (multimodal image injection) ──
                        # A live agent.stream() Pregel loop holds its channels in
                        # memory and never re-reads an externally written checkpoint
                        # mid-run, so update_state cannot inject an image into the
                        # current turn. Mirror the budget-warning pattern: once the
                        # current tool batch is fully resolved, break and re-invoke
                        # with the queued image HumanMessage(s) as input — only then
                        # does the agent node actually receive the image.
                        # SAFETY GATE: same as budget — wait until no tool_call from
                        # the latest AIMessage is unanswered, or the re-invoke raises
                        # INVALID_CHAT_HISTORY on dangling parallel tool_calls.
                        if pending_view_injects and not _unresolved_tool_call_ids(messages):
                            input_messages = pending_view_injects
                            messages.extend(pending_view_injects)
                            tlog(
                                f"VIEW RE-INVOKE: feeding {len(pending_view_injects)} "
                                f"image message(s) to the model (step {step_count})"
                            )
                            pending_view_injects = []
                            break

                        # ── Budget Warnings ──
                        # Break the stream and re-invoke with the warning as a genuine HumanMessage.
                        # The agent sees it as new input and can wrap up gracefully.
                        #
                        # SAFETY GATE: never interject while any tool_call from the latest
                        # AIMessage is still awaiting its ToolMessage. With parallel tool
                        # calls that means waiting for the whole batch — not just the
                        # agent chunk. Breaking mid-batch checkpoints dangling tool_calls
                        # and the re-invoke raises INVALID_CHAT_HISTORY.
                        pending_tool_calls = bool(_unresolved_tool_call_ids(messages))
                        remaining = max_steps - step_count
                        warning = None

                        if pending_tool_calls:
                            pass  # defer warning to the next chunk
                        elif step_count >= budget_95 and not warned_95:
                            warned_95 = True
                            warned_80 = True  # suppress a stale 80% warning after this one
                            warning = (
                                f"⚠️ CRITICAL: You have used {step_count} of {max_steps} steps ({remaining} remaining). "
                                "STOP all work immediately. You MUST: "
                                "1) Journal your progress on the current task: agictl task progress <id> 'DONE: ... NEXT: ... BLOCKERS: ...'. "
                                "2) End your cycle with a summary: agictl cycle end 'Summary of what was done and what remains'. "
                                "Your progress entry is injected into your next wake context — it is how your future self resumes. "
                                "You will be respawned on the next tick to continue."
                            )
                        elif step_count >= budget_80 and not warned_80:
                            warned_80 = True
                            warning = (
                                f"⚠️ BUDGET WARNING: You have used {step_count} of {max_steps} steps ({remaining} remaining). "
                                "Begin wrapping up your current work. "
                                "Journal your progress: agictl task progress <id> 'DONE: ... NEXT: ...'. "
                                "If you cannot complete the task in the remaining steps, "
                                "save your progress and end the cycle — you will be respawned to continue."
                            )

                        if warning:
                            tlog(f"[BUDGET] Injecting warning into agent conversation (step {step_count})")
                            tlog(f"[BUDGET] {warning}")
                            # Break this stream — re-invoke with warning as the only new input.
                            # The checkpointer holds all prior state; the agent sees this as a new human message.
                            input_messages = [HumanMessage(content=warning, id=str(uuid.uuid4()))]
                            messages.append(input_messages[0])
                            break

                        # ── Hard Budget Enforcement ──
                        # Same safety gate: wait until every parallel tool_call in the
                        # current batch has its ToolMessage before terminating.
                        if step_count >= max_steps and not pending_tool_calls:
                            tlog(f"\n[BUDGET EXCEEDED] Hard limit reached ({step_count}/{max_steps}). Terminating cycle.")
                            cycle_ended = True
                            budget_hard_stop = True
                            break
                    else:
                        # Stream completed naturally (agent produced final response with no tool calls)
                        stream_natural_end = True
                except Exception as e:
                    # Transient transport blip (431/5xx/connection): back off and
                    # resume from the checkpoint with the SAME input (idempotent).
                    if _is_transient_transport_error(e) and transient_attempt < MAX_TRANSIENT_RETRIES:
                        transient_attempt += 1
                        delay = min(2 ** transient_attempt, 8)
                        tlog(
                            f"TRANSIENT API ERROR (attempt {transient_attempt}/{MAX_TRANSIENT_RETRIES}): "
                            f"{type(e).__name__}: {e}. Resuming from checkpoint in {delay}s."
                        )
                        time.sleep(delay)
                        continue
                    raise  # non-transient, or retry budget exhausted → crash
                break  # stream invocation finished without a transient error

            if stream_natural_end:
                break

        final_message = messages[-1].content
        tlog(f"\n--- CYCLE COMPLETE ({step_count} steps) ---")
        tlog(final_message)
    except Exception as e:
        messages.append(AIMessage(content=f"FATAL EXCEPTION: {e}\nThe cycle crashed or hit recursion limit ({step_count} steps)."))
        tlog(messages[-1].content)
        _harness_crashed = True

    result_messages = messages

    total_tokens, prompt_tokens, completion_tokens = 0, 0, 0
    for m in result_messages:
        if not isinstance(m, AIMessage):
            continue
        # Standard LangChain usage_metadata (Gemini, OpenAI)
        if hasattr(m, "usage_metadata") and m.usage_metadata:
            prompt_tokens += m.usage_metadata.get("input_tokens", 0)
            completion_tokens += m.usage_metadata.get("output_tokens", 0)
            total_tokens += m.usage_metadata.get("total_tokens", 0)
            continue
        # Ollama: token data in response_metadata
        rm = getattr(m, "response_metadata", None) or {}
        if rm.get("eval_count") or rm.get("prompt_eval_count"):
            p = rm.get("prompt_eval_count", 0) or 0
            c = rm.get("eval_count", 0) or 0
            prompt_tokens += p
            completion_tokens += c
            total_tokens += p + c

    # Fallback: char-based estimation if no provider reported tokens
    if total_tokens == 0 and result_messages:
        total_chars = sum(len(m.content) for m in result_messages if hasattr(m, "content") and isinstance(m.content, str))
        prompt_chars = sum(len(m.content) for m in result_messages if isinstance(m, (HumanMessage, SystemMessage)) and isinstance(m.content, str))
        completion_chars = sum(len(m.content) for m in result_messages if isinstance(m, AIMessage) and isinstance(m.content, str))
        # ~4 chars per token is a rough estimate
        prompt_tokens = prompt_chars // 4
        completion_tokens = completion_chars // 4
        total_tokens = total_chars // 4

    write_telemetry(args.agent, total_tokens, prompt_tokens, completion_tokens, result_messages)

    if pending_view_trims:
        _trim_view_inject_payloads(agent, config, pending_view_trims)
        pending_view_trims.clear()
    elif checkpointer:
        _trim_view_inject_payloads(agent, config)

    # ── Cleanup checkpointer connection ──
    if checkpointer and hasattr(checkpointer, 'conn'):
        try:
            checkpointer.conn.close()
        except:
            pass

    # ── Exit with error code if crash occurred ──
    # This is critical for the circuit breaker — exit code 0 hides failures.
    if _harness_crashed:
        sys.exit(1)

    # Hard step budget: finalize cycle + signal lifeline to respawn (exit 53).
    if budget_hard_stop:
        _finalize_cycle_step_budget(args.agent, step_count, max_steps)
        sys.exit(53)

if __name__ == "__main__":
    main()
