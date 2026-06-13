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
        "'cycle count agent-name', 'cycle trigger'."
    ))

@tool("agictl_cycle", args_schema=CycleInput)
def agictl_cycle(command: str) -> str:
    """Manage your work cycle — end cycle, view history, trigger respawn.
    CRITICAL: You MUST call 'cycle end <summary>' before your budget runs out.
    Examples:
      - 'cycle end Completed Docker setup for mysmartyard' — end cycle with summary
      - 'cycle recent coa' — view recent cycle summaries
      - 'cycle count coa' — total cycles executed
      - 'cycle trigger' — request immediate respawn on next tick
    """
    return _run_agictl(command)


# ═══════════════════════════════════════════════════════
# 7. PROJECT — Workspace and project management
# ═══════════════════════════════════════════════════════

class ProjectInput(BaseModel):
    command: str = Field(description=(
        "The full agictl project subcommand. "
        "Examples: 'project list', 'project add name --desc \"Description\" --remote URL', "
        "'project pause name', 'project resume name', "
        "'project archive name', 'project git-setup'."
    ))

@tool("agictl_project", args_schema=ProjectInput)
def agictl_project(command: str) -> str:
    """Manage workspace projects — list, register, pause, resume, archive.
    Examples:
      - 'project list' — all registered projects
      - 'project add myapp --desc "Web app" --remote git@github.com:org/repo.git'
      - 'project pause myapp' — pause project (skipped by Lifeline)
      - 'project resume myapp' — resume a paused project
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
        "The full agictl execute subcommand. "
        "Examples: 'execute bash \"ls -la /home\"', 'execute python \"print(1+1)\"'. "
        "You CANNOT use sudo, su, or any privilege escalation commands."
    ))

@tool("agictl_execute", args_schema=ExecuteInput)
def agictl_execute(command: str) -> str:
    """Execute bash or python scripts in your workspace.
    You do NOT have sudo/su access. Privilege escalation commands are blocked.
    Examples:
      - 'execute bash "ls -la"'
      - 'execute bash "docker compose up -d"'
      - 'execute python "import os; print(os.getcwd())"'
    """
    if not command:
        return "ERROR: You must provide a command string!"

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
# Telemetry
# ═══════════════════════════════════════════════════════

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


# ═══════════════════════════════════════════════════════
# LLM Provider Resolution
# ═══════════════════════════════════════════════════════

def get_llm(model_name: str, num_ctx: int = 0):
    """Instantiate the correct LLM provider based on the model name.
    
    Provider routing (by model prefix):
      gemini-*  → ChatGoogleGenerativeAI (direct API)
      gpt-*     → ChatOpenAI (direct API — api.openai.com)
      claude-*  → ChatAnthropic (direct API — api.anthropic.com)
      grok-*    → ChatOpenAI (direct API — api.x.ai/v1, OpenAI-compatible)
      vendor/model (contains /) → ChatOpenAI (direct API — openrouter.ai/api/v1)
      *         → Local AI (ChatOllama or ChatOpenAI with local endpoint)
    
    Args:
        model_name: The model identifier (e.g. 'gemini-2.5-flash', 'gpt-5.5-2026-04-23')
        num_ctx: Context window size in tokens for Ollama models. 0 = Ollama default.
    """

    # ── Gemini (Google) — direct API ──
    if model_name.startswith("gemini"):
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            raise ValueError("GEMINI_API_KEY is required for Gemini models.")
        return ChatGoogleGenerativeAI(model=model_name, temperature=0.2, google_api_key=api_key)

    # ── OpenAI (GPT) — direct API ──
    if model_name.startswith("gpt"):
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise ValueError("OPENAI_API_KEY is required for OpenAI models. Set via: sudo agictl system set-key openai <key>")
        return ChatOpenAI(model=model_name, temperature=0.2, api_key=api_key)

    # ── Anthropic (Claude) — direct API ──
    if model_name.startswith("claude"):
        api_key = os.getenv("ANTHROPIC_API_KEY")
        if not api_key:
            raise ValueError("ANTHROPIC_API_KEY is required for Anthropic models. Set via: sudo agictl system set-key anthropic <key>")
        # NOTE: temperature/top_p/top_k are deprecated for Claude Opus 4.7+ — omit to avoid 400 errors.
        return ChatAnthropic(model=model_name, api_key=api_key)

    # ── xAI (Grok) — direct API via OpenAI-compatible endpoint ──
    if model_name.startswith("grok"):
        api_key = os.getenv("XAI_API_KEY")
        if not api_key:
            raise ValueError("XAI_API_KEY is required for xAI models. Set via: sudo agictl system set-key xai <key>")
        return ChatOpenAI(base_url="https://api.x.ai/v1", model=model_name, temperature=0.2, api_key=api_key)

    # ── OpenRouter (namespaced vendor/model IDs) — direct API ──
    if "/" in model_name:
        api_key = os.getenv("OPENROUTER_API_KEY")
        if not api_key:
            raise ValueError(
                "OPENROUTER_API_KEY is required for OpenRouter models. "
                "Set via: sudo agictl system set-key openrouter <key>"
            )
        return ChatOpenAI(
            base_url="https://openrouter.ai/api/v1",
            model=model_name,
            temperature=0.2,
            api_key=api_key,
            default_headers={
                "HTTP-Referer": "https://versavoice.ai",
                "X-Title": "Versa AGi",
            },
        )

    # ── Local AI (Ollama / Intel SYCL) ──
    gpu_backend = "standard"
    inference_url = "http://127.0.0.1:11434"

    # Read from paths.env first (truth source for topology)
    try:
        with open("/etc/versa-agi/paths.env", "r") as f:
            for line in f:
                if line.startswith("VERSA_GPU_BACKEND="):
                    gpu_backend = line.strip().split("=")[1].strip('"')
                elif line.startswith("VERSA_INFERENCE_URL="):
                    inference_url = line.strip().split("=")[1].strip('"')
    except Exception:
        pass

    if gpu_backend in ["intel", "remote"]:
        base_url = f"{inference_url}/v1"
        # Intel SYCL / llama.cpp uses GGUF basenames as model IDs
        # (e.g. "gemma-4-26B-A4B-it-UD-Q4_K_M"), not Ollama-style short
        # names (e.g. "gemma4:26b"). Translate via models.ini [sycl_models].
        resolved_name = model_name
        try:
            import configparser
            ini = configparser.ConfigParser(delimiters=('=',))
            for ini_path in ["/etc/versa-agi/models.ini",
                             os.path.join(os.path.dirname(os.path.dirname(
                                 os.path.abspath(__file__))), "models.ini")]:
                if os.path.isfile(ini_path):
                    ini.read(ini_path)
                    break
            if ini.has_section("sycl_models"):
                raw = ini.get("sycl_models", model_name, fallback="")
                if raw:
                    parts = raw.strip().split(",")
                    if len(parts) >= 2:
                        gguf_file = parts[1].strip()
                        # Strip .gguf extension — server uses basename without it
                        resolved_name = gguf_file.replace(".gguf", "")
        except Exception:
            pass  # Fall through with original name
        return ChatOpenAI(base_url=base_url, api_key="sk-local", model=resolved_name, temperature=0.2)
    else:
        kwargs = {"base_url": inference_url, "model": model_name, "temperature": 0.2}
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
    parser.add_argument("--tasks-file", default=None, help="Path to pre-computed active tasks context for triage")
    parser.add_argument("--convo-file", default=None, help="Path to pre-computed conversation history for triage")
    parser.add_argument("--resume-max-messages", type=int, default=0, help="Trim checkpoint to last N messages on resume (0 = unlimited)")
    parser.add_argument("--skill-mode", default="hybrid", choices=["full", "lazy", "hybrid"], help="Skill injection mode: full (inject all), lazy (manifest only), hybrid (core injected + lazy manifest)")
    args = parser.parse_args()

    TOOL_OUTPUT_LIMIT = args.tool_budget

    with open(args.system_file, "r") as f:
        system_prompt = f.read()

    # Resolve skills directory early — used by both CLI reference injection and triage
    skills_dir = getattr(args, 'skills_dir', None)

    # ── Always-Inject: CLI Reference ──
    # cli_reference.md is the authoritative tool manual — always present.
    # Unlike triage-driven skills, this is foundational to correct tool usage.
    cli_ref_injected = False
    if skills_dir:
        cli_ref_path = os.path.join(skills_dir, "cli_reference_agent.md")
        if os.path.isfile(cli_ref_path):
            try:
                with open(cli_ref_path, "r") as f:
                    cli_ref_content = f.read()
                system_prompt += f"\n\n---\n## ── TOOL REFERENCE: cli_reference.md ──\n\n{cli_ref_content}"
                cli_ref_injected = True
                tlog(f"CLI REFERENCE: Injected ({len(cli_ref_content)} chars)")
            except Exception as e:
                tlog(f"CLI REFERENCE: Failed to read — {e}")

    # ── Always-Inject: Skill Authoring (COA-exclusive) ──
    # skill_authoring.md is injected only for COA — sub-agents never see it.
    if skills_dir and args.agent == "coa":
        skill_auth_path = os.path.join(skills_dir, "skill_authoring.md")
        if os.path.isfile(skill_auth_path):
            try:
                with open(skill_auth_path, "r") as f:
                    skill_auth_content = f.read()
                system_prompt += f"\n\n---\n## ── SKILL AUTHORING REFERENCE ──\n\n{skill_auth_content}"
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
                system_prompt += f"\n\n---\n## ── MANDATORY: MEMORY & AWARENESS PROCEDURE ──\n**This procedure MUST be executed before ending every cycle.**\n\n{mem_skill_content}"
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
                system_prompt += f"\n\n---\n## ── COMMUNICATION RULES ──\n\n{comm_basic_content}"
                tlog(f"COMMUNICATION BASIC: Injected ({len(comm_basic_content)} chars)")
            except Exception as e:
                tlog(f"COMMUNICATION BASIC: Failed to read — {e}")

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

    llm = get_llm(args.model, num_ctx=args.num_ctx)

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
    from harness.triage import run_triage, inject_skills, build_triage_context

    triage_model_name = getattr(args, 'triage_model', None) or args.model
    triage_llm = get_llm(triage_model_name) if triage_model_name != args.model else llm

    tlog(f"TRIAGE MODEL: {triage_model_name}")
    if skills_dir:
        tlog(f"SKILLS DIR: {skills_dir}")

    # Run triage classification
    triage_result = run_triage(
        llm=triage_llm,
        wake_prompt=wake_prompt,
        tasks_context=tasks_context,
        conversation_context=convo_context,
        skills_dir=skills_dir,
        agent_name=args.agent,
    )

    # Inject skills based on triage classification
    # Filter out always-injected skills — they're not triage-driven.
    skill_content = ""
    always_injected = {"cli_reference.md", "skill_authoring.md", "memory_management.md", "communication_basic.md", "communication.md"}
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

    def _count_dict_part(part: dict) -> int:
        """Count all string payloads in a content part — not just 'text'.

        Providers return list-content parts keyed by type: 'text', 'thinking'/
        'reasoning' (extended thinking blocks, re-sent on every turn), 'data'
        (base64 media), 'executable_code', etc. Counting only 'text' undercounts
        — sometimes massively (base64 images, long thinking traces).
        """
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
                    if changed:
                        agent.update_state(
                            config,
                            {"messages": [RemoveMessage(id=REMOVE_ALL_MESSAGES)] + clean},
                            as_node="__start__",
                        )
                        post = agent.get_state(config)
                        post_count = len(post.values.get("messages", [])) if post else -1
                        depth = args.resume_max_messages if args.resume_max_messages > 0 else "unlimited"
                        tlog(
                            f"CHECKPOINT REPAIR: {len(current)} → {post_count} messages "
                            f"(trimmed {stats['trimmed']}, orphans dropped {stats['orphans']}, "
                            f"placeholders {stats['placeholders']}, max: {depth})"
                        )
                        if not clean:
                            session_type = "NEW"
                    else:
                        tlog(
                            f"CHECKPOINT: Clean resume — {len(current)} messages, "
                            f"no repair needed (thread: {args.thread_id})"
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
    tlog(f"MODEL: {args.model} | TRIAGE: {triage_model_name}")
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
    input_messages = messages  # Initial input for first stream invocation
    _harness_crashed = False

    # Bounded retry for transient transport errors (e.g. a 431/5xx/connection
    # blip from the model edge). The graph is checkpointed, so on a transient
    # failure we simply re-invoke and resume from the last checkpoint; only an
    # exhausted retry budget (or a non-transient error) crashes the cycle.
    MAX_TRANSIENT_RETRIES = 4

    try:
        while step_count < max_steps and not cycle_ended:
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
                        elif "tools" in chunk:
                            msg = chunk["tools"]["messages"][0]
                            messages.append(msg)
                            content = msg.content if isinstance(msg.content, str) else str(msg.content)
                            preview = content[:200].replace("\n", " ")
                            tlog(f"[STEP {step_count}/{max_steps}] TOOL  ← {preview}")

                            # ── Cycle End Detection ──
                            # When the agent calls `agictl cycle end`, break immediately so
                            # telemetry writes before the process terminates.
                            if "\U0001f6d1 Cycle ended:" in content:
                                tlog(f"\n--- CYCLE END DETECTED (step {step_count}) ---")
                                cycle_ended = True
                                break

                        # ── Budget Warnings ──
                        # Break the stream and re-invoke with the warning as a genuine HumanMessage.
                        # The agent sees it as new input and can wrap up gracefully.
                        #
                        # SAFETY GATE: never interject while the just-streamed chunk is an
                        # AIMessage with unresolved tool_calls. Breaking there checkpoints a
                        # dangling tool call (its ToolMessage never runs), and the re-invoke
                        # raises INVALID_CHAT_HISTORY — crashing the cycle. Hold the warning
                        # until the next safe chunk (the tool result lands one chunk later).
                        pending_tool_calls = (
                            "agent" in chunk
                            and hasattr(msg, "tool_calls")
                            and bool(msg.tool_calls)
                        )
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
                        # Same safety gate: allow one extra chunk so a pending tool call
                        # resolves — terminating between AIMessage and ToolMessage leaves
                        # a dangling tool call in the checkpoint.
                        if step_count >= max_steps and not pending_tool_calls:
                            tlog(f"\n[BUDGET EXCEEDED] Hard limit reached ({step_count}/{max_steps}). Terminating cycle.")
                            cycle_ended = True
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

if __name__ == "__main__":
    main()
