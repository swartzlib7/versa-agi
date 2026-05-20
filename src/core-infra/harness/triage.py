"""
Versa AGi — Task Triage Node
10-signal confidence-scored decision matrix for message classification,
project routing, and skill injection.
"""

import os
import json
from dataclasses import dataclass, field, asdict
from typing import List, Optional


@dataclass
class TriageResult:
    """Output of the triage node — consumed by the agent node or clarify_exit node."""
    classification: str = "informational"  # work_request, follow_up, informational, clarification_needed
    confidence: float = 0.5
    project_id: Optional[int] = None
    thread_id: str = ""
    task_actions: list = field(default_factory=list)
    skills_to_inject: list = field(default_factory=list)
    strategy_notes: str = ""
    parallel_work_viable: bool = False
    signal_results: dict = field(default_factory=dict)
    has_attachments: bool = False
    attachment_paths: list = field(default_factory=list)


# ═══════════════════════════════════════════════════════
# Triage Prompt — Injected as a structured analysis request
# ═══════════════════════════════════════════════════════

TRIAGE_PROMPT = """You are a message triage system. Analyze the following wake prompt and context, then output a JSON classification.

## WAKE PROMPT (the message/task to analyze):
{wake_prompt}

## ACTIVE TASKS:
{tasks_context}

## CONVERSATION HISTORY (last 5 messages):
{conversation_context}

## INSTRUCTIONS:
Evaluate the wake prompt against these 10 signals and produce a JSON response:

1. **direction_clarity**: Was clear direction given? (true/false)
2. **purpose_clarity**: Is the purpose of the request clear? (true/false)
3. **contradiction_check**: Are there contradictions in the request? (true/false = contradiction found)
4. **historical_context**: Is there relevant history for this? (true/false)
5. **task_correlation**: Are there related existing tasks? (true/false)
6. **project_correlation**: Does this map to a known project? (true/false)
7. **memory_conflict**: Are there conflicting memory entries? (true/false = conflict found)
8. **pending_question**: Was a question asked that needs answering first? (true/false)
9. **parallel_work_viable**: Can work continue while seeking clarification? (true/false)
10. **risk_assessment**: Is there risk in proceeding without full clarity? (true/false = high risk)

Then classify the message:
- **work_request**: New work that needs task creation and execution
- **follow_up**: Continuation of existing work/conversation
- **informational**: Status update, acknowledgment, or FYI — no action needed
- **clarification_needed**: Cannot proceed without more information

Determine which skills should be injected (filenames only, select ALL that apply):
{skills_catalog}

Output ONLY valid JSON in this exact format:
```json
{{
  "classification": "work_request|follow_up|informational|clarification_needed",
  "confidence": 0.0-1.0,
  "project_id": null or integer,
  "task_actions": [],
  "skills_to_inject": [],
  "strategy_notes": "Brief rationale and instructions for the agent",
  "parallel_work_viable": true/false,
  "has_attachments": true/false,
  "signal_results": {{
    "direction_clarity": true/false,
    "purpose_clarity": true/false,
    "contradiction_check": true/false,
    "historical_context": true/false,
    "task_correlation": true/false,
    "project_correlation": true/false,
    "memory_conflict": true/false,
    "pending_question": true/false,
    "parallel_work_viable": true/false,
    "risk_assessment": true/false
  }}
}}
```"""


# ═══════════════════════════════════════════════════════
# Skills Catalog — Dynamic loading from DB-generated file
# ═══════════════════════════════════════════════════════

# Fallback hardcoded catalog used when no catalog file exists
_FALLBACK_SKILLS_CATALOG = """- "communication.md" — Message crafting and response protocols
- "git_operations.md" — Git operations (clone, commit, push, branch management)
- "project_management.md" — Project setup, assignment, workspace management
- "task_scheduling.md" — Task management (create, update, snooze, prioritize)
- "task_routing.md" — Routing tasks between agents
- "requirements_elicitation.md" — 5W1H analysis for new work (what to build)
- "work_initiation.md" — New project setup or starting new work streams
- "memory_management.md" — Managing agent memory
- "connection_lifecycle.md" — Managing VersaVoice connections
- "connection_request_approval.md" — Processing incoming connection requests
- "message_relay.md" — Relaying messages between users/agents
- "agent_management.md" — Managing sub-agents
- "agent_onboarding.md" — Onboarding new agents
- "shared_tooling.md" — Using the shared AGi-Tools workspace
- "security_protocol.md" — Security-sensitive operations
- "reminder_management.md" — Creating and managing reminders
- "self_introduction.md" — Introducing the agent to new contacts
- "founder_story.md" — Sharing the VersaVoice origin story
- "solution_architect.md" — System/environment setup guidance for PU"""

_SKILLS_CATALOG_PATH = "/var/lib/versa-agi/skills_catalog.md"


def load_skills_catalog() -> str:
    """Load the dynamic skills catalog from the cached file.

    Falls back to the hardcoded catalog if the file doesn't exist
    (pre-migration or catalog not yet generated by Lifeline).
    """
    if os.path.isfile(_SKILLS_CATALOG_PATH):
        try:
            with open(_SKILLS_CATALOG_PATH, "r") as f:
                content = f.read().strip()
            if content:
                return content
        except Exception:
            pass
    return _FALLBACK_SKILLS_CATALOG


def _extract_json(text: str) -> dict:
    """Extract JSON from LLM response, handling markdown code fences."""
    # Try raw parse first
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # Try extracting from code fence
    if "```" in text:
        parts = text.split("```")
        for part in parts:
            cleaned = part.strip()
            if cleaned.startswith("json"):
                cleaned = cleaned[4:].strip()
            try:
                return json.loads(cleaned)
            except json.JSONDecodeError:
                continue
    # Try finding JSON object boundaries
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            return json.loads(text[start:end + 1])
        except json.JSONDecodeError:
            pass
    return {}


def run_triage(llm, wake_prompt: str, tasks_context: str = "",
               conversation_context: str = "", skills_dir: str = None) -> TriageResult:
    """Execute the triage node: classify the wake prompt and determine routing.

    Args:
        llm: The triage LLM instance (from get_llm)
        wake_prompt: The wake reason prompt content
        tasks_context: Active tasks summary (pre-fetched by lifeline)
        conversation_context: Recent conversation history
        skills_dir: Path to agent's skills directory for injection

    Returns:
        TriageResult with classification, confidence, skills, and routing info
    """
    from langchain_core.messages import HumanMessage as HMsg

    # Build the triage prompt with dynamic skills catalog
    skills_catalog = load_skills_catalog()
    prompt = TRIAGE_PROMPT.format(
        wake_prompt=wake_prompt[:4000],  # Cap to prevent context overflow
        tasks_context=tasks_context[:2000] if tasks_context else "(none)",
        conversation_context=conversation_context[:2000] if conversation_context else "(none)",
        skills_catalog=skills_catalog,
    )

    try:
        response = llm.invoke([HMsg(content=prompt)])
        raw = response.content if hasattr(response, "content") else str(response)
        # Gemini models may return content as a list of parts — normalize to string
        if isinstance(raw, list):
            raw = " ".join(
                p.get("text", str(p)) if isinstance(p, dict) else str(p)
                for p in raw
            )
        data = _extract_json(raw)
    except Exception as e:
        print(f"TRIAGE: LLM call failed — {e}. Defaulting to pass-through.", flush=True)
        return TriageResult(
            classification="follow_up",
            confidence=0.5,
            strategy_notes=f"Triage failed ({e}). Passing through to agent.",
        )

    if not data:
        print("TRIAGE: Could not parse JSON response. Defaulting to pass-through.", flush=True)
        return TriageResult(
            classification="follow_up",
            confidence=0.5,
            strategy_notes="Triage JSON parse failed. Passing through to agent.",
        )

    # Build result from parsed JSON
    result = TriageResult(
        classification=data.get("classification", "follow_up"),
        confidence=float(data.get("confidence", 0.5)),
        project_id=data.get("project_id"),
        task_actions=data.get("task_actions", []),
        skills_to_inject=data.get("skills_to_inject", []),
        strategy_notes=data.get("strategy_notes", ""),
        parallel_work_viable=data.get("parallel_work_viable", False),
        has_attachments=data.get("has_attachments", False),
        signal_results=data.get("signal_results", {}),
    )

    print(f"TRIAGE: {result.classification} (confidence={result.confidence:.2f})", flush=True)
    signals = result.signal_results
    if signals:
        neg = [k for k, v in signals.items() if not v and k not in ("contradiction_check", "memory_conflict", "risk_assessment")]
        neg += [k for k, v in signals.items() if v and k in ("contradiction_check", "memory_conflict", "risk_assessment")]
        if neg:
            print(f"TRIAGE: Negative signals: {', '.join(neg)}", flush=True)
    if result.skills_to_inject:
        print(f"TRIAGE: Skills to inject: {', '.join(result.skills_to_inject)}", flush=True)

    return result


def inject_skills(result: TriageResult, skills_dir: str) -> str:
    """Read and concatenate skill file contents for injection into the agent context.

    Args:
        result: The triage result with skills_to_inject list
        skills_dir: Path to the agent's skills directory

    Returns:
        Concatenated skill content as a single string, or empty string
    """
    if not skills_dir or not result.skills_to_inject:
        return ""

    # Map skill filenames to injection reasons based on classification
    skill_reasons = _get_skill_reasons(result)

    injected = []
    for skill_name in result.skills_to_inject:
        skill_path = os.path.join(skills_dir, skill_name)
        if os.path.isfile(skill_path):
            try:
                with open(skill_path, "r") as f:
                    content = f.read()
                reason = skill_reasons.get(skill_name, "Referenced by triage classification.")
                injected.append(f"\n---\n## ── SKILL: {skill_name} ──\n**Why injected:** {reason}\n\n{content}")
                print(f"TRIAGE: Injected skill: {skill_name} ({len(content)} chars)", flush=True)
            except Exception as e:
                print(f"TRIAGE: Failed to read skill {skill_name}: {e}", flush=True)
        else:
            print(f"TRIAGE: Skill not found: {skill_path}", flush=True)

    return "\n".join(injected)


def _get_skill_reasons(result: TriageResult) -> dict:
    """Map skill filenames to human-readable injection reasons based on triage context."""
    reasons = {}
    cls = result.classification
    signals = result.signal_results or {}

    # Communication — always explain why
    if "communication.md" in result.skills_to_inject:
        if cls == "work_request":
            reasons["communication.md"] = "Acknowledge the sender's request before starting work. Follow the communication etiquette and mode selection rules."
        elif cls == "follow_up":
            reasons["communication.md"] = "Continue the conversation thread. Match the sender's tone and formality."
        elif cls == "clarification_needed":
            reasons["communication.md"] = "Craft a clear, empathetic clarification request. Reference what you understood and what needs clarity."
        else:
            reasons["communication.md"] = "A response may be needed. Follow messaging rules — especially the Inter-Agent Acknowledgment Protocol for agent-sourced messages."

    # Task scheduling
    if "task_scheduling.md" in result.skills_to_inject:
        if len(result.task_actions) > 1:
            reasons["task_scheduling.md"] = f"Multiple work items detected ({len(result.task_actions)} actions). Review existing tasks for overlap before creating new ones."
        else:
            reasons["task_scheduling.md"] = "Task management may be needed. Verify whether related tasks already exist before creating or updating."

    # Work initiation
    if "work_initiation.md" in result.skills_to_inject:
        if signals.get("project_correlation"):
            reasons["work_initiation.md"] = "New work detected that maps to an existing project. Target the correct project before starting."
        else:
            reasons["work_initiation.md"] = "New work detected with no clear project match. Determine if this needs a new project or is disposable."

    # Git operations
    if "git_operations.md" in result.skills_to_inject:
        reasons["git_operations.md"] = "The work involves code or file changes. Follow git commit, branch, and push protocols."

    # Project management
    if "project_management.md" in result.skills_to_inject:
        reasons["project_management.md"] = "Project setup, configuration, or membership changes are needed."

    # Requirements elicitation
    if "requirements_elicitation.md" in result.skills_to_inject:
        reasons["requirements_elicitation.md"] = "The request has missing dimensions or ambiguous scope. Elicit requirements before committing to work."

    # Security
    if "security_protocol.md" in result.skills_to_inject:
        reasons["security_protocol.md"] = "Security-sensitive operations detected. Follow security protocol before proceeding."

    # Connection lifecycle
    if "connection_lifecycle.md" in result.skills_to_inject:
        reasons["connection_lifecycle.md"] = "Connection management actions detected (connect, disconnect, or verify)."

    # Self introduction
    if "self_introduction.md" in result.skills_to_inject:
        reasons["self_introduction.md"] = "A new contact requires introduction. Follow the self-introduction protocol."

    # Memory management
    if "memory_management.md" in result.skills_to_inject:
        reasons["memory_management.md"] = "Persistent memory operations are needed (store, retrieve, or update context)."

    # Message relay
    if "message_relay.md" in result.skills_to_inject:
        reasons["message_relay.md"] = "A message needs to be relayed between users or agents."

    # Solution architect
    if "solution_architect.md" in result.skills_to_inject:
        reasons["solution_architect.md"] = "Environment setup or stack installation needed. Guide the PU through safe configuration."

    # Remaining skills get generic reasons
    for skill in result.skills_to_inject:
        if skill not in reasons:
            reasons[skill] = "Referenced by triage classification."

    return reasons


def build_triage_context(result: TriageResult) -> str:
    """Build a context block from triage results to prepend to the wake prompt.

    This gives the agent immediate awareness of the triage decision AND
    actionable behavioral directives based on the classification and signals.
    """
    lines = [
        "## ── TRIAGE RESULT ──",
        f"Classification: **{result.classification}** (confidence: {result.confidence:.2f})",
    ]
    if result.strategy_notes:
        lines.append(f"Strategy: {result.strategy_notes}")
    if result.has_attachments:
        lines.append("⚠ This message includes attachments — read them before acting.")
        if result.attachment_paths:
            lines.append(f"  Paths: {', '.join(result.attachment_paths)}")
    if result.task_actions:
        lines.append(f"Task actions: {json.dumps(result.task_actions)}")

    # ── Signal Summary ──
    signals = result.signal_results or {}
    neg_signals = []
    for k, v in signals.items():
        if k in ("contradiction_check", "memory_conflict", "risk_assessment"):
            if v:
                neg_signals.append(k)
        else:
            if not v:
                neg_signals.append(k)
    if neg_signals:
        lines.append(f"Negative signals: {', '.join(neg_signals)}")

    # ── Behavioral Directives ──
    lines.append("")
    lines.append("## ── TRIAGE DIRECTIVES ──")
    lines.append("Follow this execution order for this cycle:")
    lines.append("")

    cls = result.classification

    if cls == "work_request":
        lines.append("### Execution Order")
        lines.append("1. **COMMUNICATE FIRST** — Acknowledge the sender's message before doing any work. Let them know you received their request and what you plan to do.")
        if len(result.task_actions) > 1:
            lines.append(f"2. **PLAN WORK** — This request contains {len(result.task_actions)} distinct work items. Review existing tasks for overlap before planning new ones.")
        elif result.task_actions:
            lines.append("2. **PLAN WORK** — Verify whether a related task already exists. Create a new task only if none covers this work.")
        else:
            lines.append("2. **PLAN WORK** — Determine the scope. Create tasks if the work is non-trivial and no existing tasks cover it.")
        lines.append("3. **EXECUTE** — Begin the work, keeping task states accurate as you progress.")
        lines.append("4. **REPORT** — When work is complete, update tracking and notify the sender with results.")

    elif cls == "follow_up":
        lines.append("### Execution Order")
        lines.append("1. **REVIEW CONTEXT** — This continues an existing thread. Review your current state (tasks, conversation history) before responding.")
        lines.append("2. **COMMUNICATE** — Respond to the sender with an update or continuation.")
        lines.append("3. **CONTINUE WORK** — Resume any in-progress tasks related to this thread.")

    elif cls == "informational":
        lines.append("### Execution Order")
        lines.append("1. **ASSESS SENDER** — Determine if the message is from the Primary User/contact or from another agent.")
        lines.append("2. **IF FROM AGENT** — This is likely a terminal acknowledgment or status update. Mark as processed, update relevant memory if needed, and **end cycle without replying**. Do NOT send an acknowledgment to an acknowledgment.")
        lines.append("3. **IF FROM PU/CONTACT** — Acknowledge receipt appropriately if the content warrants it.")
        lines.append("4. **UPDATE MEMORY** — If the information is relevant for future work, store it in agent memory.")
        lines.append("5. **NO TASK CREATION** — Informational messages do not require task creation unless they reveal new work.")

    elif cls == "clarification_needed":
        lines.append("### Execution Order")
        lines.append("1. **COMMUNICATE** — Send a clear, specific clarification request to the sender. Reference what you understood and what needs clarity.")
        if result.parallel_work_viable:
            lines.append("2. **PARALLEL WORK** — While waiting for clarification, proceed with any unambiguous aspects of the request.")
        else:
            lines.append("2. **WAIT** — Do not start work until clarification is received. End your cycle after sending the clarification request.")

    # ── Signal-Specific Guidance ──
    signal_guidance = []
    if signals.get("pending_question"):
        signal_guidance.append("— **Pending question detected** — address the unanswered question in your response before proceeding with new work.")
    if signals.get("contradiction_check"):
        signal_guidance.append("— **Contradiction detected** — flag the contradicting elements in your response and ask for clarification before proceeding.")
    if signals.get("memory_conflict"):
        signal_guidance.append("— **Memory conflict** — your stored context conflicts with the current request. Mention this discrepancy and confirm the correct approach.")
    if signals.get("risk_assessment"):
        signal_guidance.append("— **High risk detected** — proceed cautiously. Confirm destructive or irreversible actions with the sender before executing.")
    if not signals.get("project_correlation") and cls == "work_request":
        signal_guidance.append("— **No project match** — identify the correct project before starting. If none exists, consult the work_initiation skill for project registration.")

    if signal_guidance:
        lines.append("")
        lines.append("### Signal-Specific Guidance")
        lines.extend(signal_guidance)

    return "\n".join(lines)

