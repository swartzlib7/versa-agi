import os
import sys
import json
import sqlite3
import shlex
import argparse
import subprocess
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional, Literal

from langchain_core.messages import SystemMessage, HumanMessage, AIMessage, ToolMessage, trim_messages
from langchain_core.tools import tool
from langgraph.prebuilt import create_react_agent
from langgraph.graph import StateGraph, MessagesState, START, END
import warnings
warnings.filterwarnings("ignore", category=DeprecationWarning)


def tlog(msg: str):
    """Timestamped print for result file traceability."""
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)

from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_openai import ChatOpenAI
from langchain_ollama import ChatOllama

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
    
    Args:
        model_name: The model identifier (e.g. 'gemini-2.5-flash', 'gemma4:26b')
        num_ctx: Context window size in tokens for Ollama models. 0 = Ollama default.
    """
    # TODO: Implement xAI Provider
    # TODO: Implement Anthropic Provider

    if model_name.startswith("gemini"):
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            raise ValueError("GEMINI_API_KEY is required for Gemini models.")
        return ChatGoogleGenerativeAI(model=model_name, temperature=0.2, google_api_key=api_key)

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
        return ChatOpenAI(base_url=base_url, api_key="sk-local", model=model_name, temperature=0.2)
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
    )

    # Inject skills based on triage classification
    # Filter out cli_reference.md — it's always-injected above, not triage-driven.
    skill_content = ""
    if skills_dir and triage_result.skills_to_inject:
        triage_result.skills_to_inject = [s for s in triage_result.skills_to_inject if s != "cli_reference.md"]
        if triage_result.skills_to_inject:
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
    # Uses char count as a proxy for tokens (~4 chars/token).
    # Dynamically aligned with num_ctx when provided.
    if args.num_ctx and args.num_ctx > 0:
        CONTEXT_WINDOW_CHARS = args.num_ctx * 4  # ~4 chars/token
    else:
        CONTEXT_WINDOW_CHARS = 128000  # default fallback (~32K tokens)

    def pre_model_hook(state):
        """Trim messages to fit context window before LLM call.
        Returns llm_input_messages (not messages) to preserve full checkpoint history."""
        trimmed = trim_messages(
            state["messages"],
            max_tokens=CONTEXT_WINDOW_CHARS,
            strategy="last",
            token_counter=len,       # char-based proxy; ~4 chars/token
            include_system=True,     # always preserve system prompt
            start_on="human",        # ensure valid message ordering
            allow_partial=False,
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

        # Pass 2: Always validate checkpoint integrity on RESUME
        # Previously gated by a sentinel file written by lifeline — but that was fragile
        # (circuit breaker, permission issues, or early exits could prevent the sentinel).
        # The tail scan is cheap enough to run unconditionally.
        if session_type == "RESUME":
            try:
                snapshot = agent.get_state(config)
                if snapshot and snapshot.values.get("messages"):
                    saved_msgs = snapshot.values["messages"]
                    # Only scan the tail — dangling tool calls can only be at the very end.
                    # 10 messages is generous; in practice, it's the last 2-3 (AI + pending Tools).
                    REPAIR_SCAN_DEPTH = 10
                    tail_start = max(0, len(saved_msgs) - REPAIR_SCAN_DEPTH)
                    tail = saved_msgs[tail_start:]

                    # Find the last AIMessage with tool_calls in the tail
                    last_ai_idx = None
                    for i in range(len(tail) - 1, -1, -1):
                        if isinstance(tail[i], AIMessage) and getattr(tail[i], "tool_calls", None):
                            last_ai_idx = tail_start + i  # Absolute index
                            break

                    if last_ai_idx is not None:
                        ai_msg = saved_msgs[last_ai_idx]
                        tool_call_ids = {tc["id"] for tc in ai_msg.tool_calls}
                        answered_ids = set()
                        for msg in saved_msgs[last_ai_idx + 1:]:
                            if isinstance(msg, ToolMessage) and hasattr(msg, "tool_call_id"):
                                answered_ids.add(msg.tool_call_id)
                        dangling = tool_call_ids - answered_ids
                        if dangling:
                            tlog(f"CHECKPOINT REPAIR: Patching {len(dangling)} dangling tool call(s) from previous crashed cycle")
                            repair_msgs = []
                            for tc in ai_msg.tool_calls:
                                if tc["id"] in dangling:
                                    repair_msgs.append(ToolMessage(
                                        content="[Cycle terminated before this tool call completed. Result unavailable.]",
                                        tool_call_id=tc["id"],
                                        name=tc.get("name", "unknown"),
                                    ))
                            agent.update_state(config, {"messages": repair_msgs})
                            tlog(f"CHECKPOINT REPAIR: State repaired — {len(repair_msgs)} placeholder ToolMessage(s) injected")
                        else:
                            tlog(f"CHECKPOINT: Clean resume — no dangling calls (thread: {args.thread_id})")
                    else:
                        tlog(f"CHECKPOINT: Clean resume — no pending tool calls in tail (thread: {args.thread_id})")
                else:
                    tlog(f"CHECKPOINT: Resume with empty state (thread: {args.thread_id})")
            except Exception as e:
                tlog(f"CHECKPOINT REPAIR: Failed — {e}. Deleting corrupted thread.")
                session_type = "NEW"
                try:
                    checkpointer.conn.execute("DELETE FROM checkpoints WHERE thread_id = ?", (args.thread_id,))
                    checkpointer.conn.execute("DELETE FROM writes WHERE thread_id = ?", (args.thread_id,))
                    checkpointer.conn.commit()
                    tlog(f"CHECKPOINT: Corrupted thread {args.thread_id} deleted — starting fresh")
                except Exception as de:
                    tlog(f"CHECKPOINT: Could not delete corrupted thread — {de}")

            # ── Resume Depth Trimming ──
            # When resume_max_messages > 0, trim the checkpoint state to keep
            # only the last N messages. Older messages remain in the DB file
            # (reclaimed by vacuum pruning), but the active state is trimmed.
            if session_type == "RESUME" and args.resume_max_messages > 0:
                try:
                    snapshot = agent.get_state(config)
                    if snapshot and snapshot.values.get("messages"):
                        saved_msgs = snapshot.values["messages"]
                        msg_count = len(saved_msgs)
                        max_msgs = args.resume_max_messages
                        if msg_count > max_msgs:
                            # Preserve the first SystemMessage if present
                            from langchain_core.messages import SystemMessage
                            first_sys = None
                            if isinstance(saved_msgs[0], SystemMessage):
                                first_sys = saved_msgs[0]
                                # Trim from the rest, keeping last (max_msgs - 1)
                                trimmed = [first_sys] + saved_msgs[-(max_msgs - 1):]
                            else:
                                trimmed = saved_msgs[-max_msgs:]
                            # Replace the full state with the trimmed version
                            agent.update_state(config, {"messages": trimmed}, as_node="__start__")
                            tlog(f"RESUME TRIM: {msg_count} → {len(trimmed)} messages (max: {max_msgs})")
                        else:
                            tlog(f"RESUME TRIM: {msg_count} messages within limit ({max_msgs}) — no trim needed")
                except Exception as e:
                    tlog(f"RESUME TRIM: Failed — {e} (continuing with full state)")

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
    messages = [HumanMessage(content=enhanced_wake)]

    step_count = 0
    max_steps = args.max_steps
    budget_80 = int(max_steps * 0.80)
    budget_95 = int(max_steps * 0.95)
    warned_80 = False
    warned_95 = False
    cycle_ended = False
    input_messages = messages  # Initial input for first stream invocation
    _harness_crashed = False

    try:
        while step_count < max_steps and not cycle_ended:
            # Each stream invocation processes until a budget threshold or completion.
            # With checkpointing, the graph state persists between invocations —
            # we only need to pass NEW messages (e.g., the budget warning).
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
                remaining = max_steps - step_count
                warning = None

                if step_count >= budget_95 and not warned_95:
                    warned_95 = True
                    warning = (
                        f"⚠️ CRITICAL: You have used {step_count} of {max_steps} steps ({remaining} remaining). "
                        "STOP all work immediately. You MUST: "
                        "1) Update your current task with progress notes: agictl task update <id> --status in_progress --desc 'progress so far...'. "
                        "2) End your cycle with a summary: agictl cycle end 'Summary of what was done and what remains'. "
                        "You will be respawned on the next tick to continue."
                    )
                elif step_count >= budget_80 and not warned_80:
                    warned_80 = True
                    warning = (
                        f"⚠️ BUDGET WARNING: You have used {step_count} of {max_steps} steps ({remaining} remaining). "
                        "Begin wrapping up your current work. "
                        "Update your task with progress: agictl task update <id> --desc 'progress notes...'. "
                        "If you cannot complete the task in the remaining steps, "
                        "save your progress and end the cycle — you will be respawned to continue."
                    )

                if warning:
                    tlog(f"[BUDGET] Injecting warning into agent conversation (step {step_count})")
                    tlog(f"[BUDGET] {warning}")
                    # Break this stream — re-invoke with warning as the only new input.
                    # The checkpointer holds all prior state; the agent sees this as a new human message.
                    input_messages = [HumanMessage(content=warning)]
                    messages.append(input_messages[0])
                    break

                # ── Hard Budget Enforcement ──
                if step_count >= max_steps:
                    tlog(f"\n[BUDGET EXCEEDED] Hard limit reached ({step_count}/{max_steps}). Terminating cycle.")
                    cycle_ended = True
                    break
            else:
                # Stream completed naturally (agent produced final response with no tool calls)
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
