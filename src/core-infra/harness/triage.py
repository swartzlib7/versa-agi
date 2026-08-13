"""
Versa AGi — Task Triage Node
10-signal confidence-scored decision matrix for message classification,
project routing, and skill injection.

Altitude: flagship triage produces checks + strategic brief + skill picks.
Low-altitude protocol (CLI, mark-processed, snooze) lives in poise/skills.
"""

import db_connect

import os
import json
from dataclasses import dataclass, field
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
    required_work_modality: Optional[str] = None
    recommended_model: Optional[str] = None
    # Provenance: which triage inputs were non-empty this cycle
    inputs_used: list = field(default_factory=list)


# ═══════════════════════════════════════════════════════
# Triage Prompt — Injected as a structured analysis request
# ═══════════════════════════════════════════════════════

TRIAGE_PROMPT = """You are a message triage system. Analyze the wake prompt and context, then output a JSON classification.

The execution agent that receives your output may be a **weaker / cheaper model**. Use this pass for high-altitude judgment: classification, risks, skill selection, and a clear strategic brief. Do **not** teach low-altitude protocol (CLI, mark-processed order, snooze recipes, messaging etiquette, attachment paths) — poise and skills already own those with fuller context.

## WAKE PROMPT (the message/task to analyze):
{wake_prompt}

## ACTIVE TASKS:
{tasks_context}

## CONVERSATION HISTORY (last 5 messages):
{conversation_context}

## ACTIVE GAMES (digest — strategic frame; may be empty):
{games_context}

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
- **follow_up**: Continuation of existing work/conversation (new substance — not a pure ack)
- **informational**: Status update, acknowledgment, standing-by, social/FYI, or other low-urgency notice — may or may not need a light touch; no heavy new work implied
- **clarification_needed**: Cannot proceed without more information

## Reply posture (advisory — required in strategy_notes)
Suggest a reply posture; the execution agent and poise/skills own the final call. Use advisory wording only ("consider", "likely", "suggest") — never imperatives ("Do NOT", "You MUST", "Never reply").
- **Likely silent**: inbound looks like a **peer-agent** terminal acknowledgment ("Got it", "Acknowledged", "Ack", "Standing by") or pure inter-agent status with no question — ack-of-ack loops waste cycles.
- **Likely reply**: inbound is from a **human** (Primary User or connection), assigns work, asks a question, needs a Gate verdict / next slice, or is a social check-in / intro / warmth that would feel cold if ignored.
- Human social/FYI (e.g. a short voice intro) is **not** an inter-agent terminal ack — prefer a brief warm acknowledgment over silence.
- Classify peer-agent terminal acks as `informational` with likely-silent posture; do not force silence on human engagement.

Determine which skills should be injected (filenames only, select ALL the weaker agent will need):
{skills_catalog}

## OUTPUT RULES (altitude)
- **strategy_notes**: Short **advisory** strategic brief (goal, suggested reply posture, risks, clarify-vs-proceed, why these skills, game posture if relevant). Structured bullets OK. Assume a weaker model will read this — advise, do not command.
- **Forbid** in strategy_notes and task_actions: CLI commands, `agictl` invocations, mark-processed ordering, snooze recipes, attachment filesystem paths, and imperative protocol ("Do NOT reply", "You MUST end").
- **Allow** suggested reply posture (likely reply vs likely silent) as high-altitude advice.
- **task_actions**: High-level work labels only (e.g. "reply-to-sender-with-analysis", "update-game-barriers", "brief-warm-ack"). Prefer labels that describe outcomes, not orders.
- Set `has_attachments: true` when wake/conversation indicates media/files attached — do not instruct how to view them.

Output ONLY valid JSON in this exact format:
```json
{{
  "classification": "work_request|follow_up|informational|clarification_needed",
  "confidence": 0.0-1.0,
  "project_id": null or integer,
  "task_actions": [],
  "skills_to_inject": [],
  "strategy_notes": "Short strategic brief (not protocol)",
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


ROUTING_APPENDIX = """
## MODEL ROUTING (optional — only when routing context is provided)
Assigned model: {assigned_model} (work_modality: {assigned_work_modality})
Required input modalities: {required_input_modalities}
Mode: {mode}
{candidates_block}
{feedback_block}
{coa_note}

Classify the cognitive work tier as required_work_modality: fast|balanced|reasoning|code|local
Pool mode only: set recommended_model to a candidate key or null to keep assigned model.
Deprioritize PU 'avoid' feedback; favor 'prefer' when task/modality matches.
Never override COA approval rules.

Add to your JSON output:
  "required_work_modality": "fast|balanced|reasoning|code|local",
  "recommended_model": null or "catalog_key"
"""

COA_ROUTING_NOTE = """
COA (Chief Orchestrator) — classify **this agent's** cognitive work, not work delegated to sub-agents.
Assigning tasks, routing to another agent, or discussing their implementation → balanced or reasoning, not code.
Use code only when COA will directly write, edit, or patch code in this cycle (not when merely mentioning coding work for others).
"""


def _format_routing_appendix(routing: dict, agent_name: str = "coa") -> str:
    if not routing:
        return ""
    candidates = routing.get("candidates") or []
    cand_lines = "\n".join(
        f"  - {c['key']}: work={c.get('work_modality')}, in={c.get('input')}, out={c.get('output')}"
        for c in candidates
    ) or "  (none — preferred-map mode)"
    feedback = routing.get("pu_feedback") or []
    fb_lines = "\n".join(
        f"  - {f['preference']} {f['catalog_key']} "
        f"(modality={f.get('work_modality') or 'any'}, hint={f.get('task_hint') or ''})"
        for f in feedback
    ) or "  (none)"
    coa_note = COA_ROUTING_NOTE if (agent_name or "").lower() == "coa" else ""
    return ROUTING_APPENDIX.format(
        assigned_model=routing.get("assigned_model", ""),
        assigned_work_modality=routing.get("assigned_work_modality", "balanced"),
        required_input_modalities=", ".join(routing.get("required_input_modalities") or ["text"]),
        mode=routing.get("mode", "pool"),
        candidates_block=f"Candidates:\n{cand_lines}",
        feedback_block=f"PU feedback:\n{fb_lines}",
        coa_note=coa_note,
    )


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
- "script_tasks.md" — Scheduling deterministic .sh scripts from AGi-Tools (no LLM)
- "security_protocol.md" — Security-sensitive operations
- "reminder_management.md" — Creating and managing reminders
- "self_introduction.md" — Introducing the agent to new contacts
- "founder_story.md" — Sharing the VersaVoice origin story
- "solution_architect.md" — System/environment setup guidance for PU
- "system_packages.md" — Requesting and installing system packages (apt)
- "versa_agi_operations_guide.md" — PU-facing Versa AGi product/ops guidance (COA only; how the system works, agitop, troubleshooting)"""

_SKILLS_CATALOG_PATH = "/var/lib/versa-agi/skills_catalog.md"

_NOT_USED_BY_TRIAGE = (
    "full poise, full Games/awareness board in system prompt, workspace files, "
    "WBS/collaboration docs, operational memory dumps"
)

# Signals where True means a problem (others: True = healthy / present).
_ADVERSE_WHEN_TRUE = frozenset({
    "contradiction_check",
    "memory_conflict",
    "risk_assessment",
    "pending_question",
})


def adverse_signals(signals: Optional[dict]) -> List[str]:
    """Return signal names that indicate a problem for this cycle.

    - Most signals: False is adverse (e.g. direction_clarity missing).
    - ``_ADVERSE_WHEN_TRUE``: True is adverse (e.g. pending_question present).
    """
    if not signals:
        return []
    out: List[str] = []
    for key, value in signals.items():
        if key in _ADVERSE_WHEN_TRUE:
            if value:
                out.append(key)
        elif not value:
            out.append(key)
    return out


def load_skills_catalog(agent_name: str = "coa") -> str:
    """Load the dynamic skills catalog from the cached file.

    Falls back to the hardcoded catalog if the file doesn't exist
    (pre-migration or catalog not yet generated by Lifeline).

    For sub-agents (agent_name != 'coa'), skills with scope='coa_only'
    are filtered out so triage never considers COA-exclusive skills.
    """
    catalog_lines = []
    if os.path.isfile(_SKILLS_CATALOG_PATH):
        try:
            with open(_SKILLS_CATALOG_PATH, "r") as f:
                catalog_lines = [l for l in f.read().strip().splitlines() if l.strip()]
        except Exception:
            pass

    if not catalog_lines:
        catalog_lines = _FALLBACK_SKILLS_CATALOG.strip().splitlines()

    # Filter out coa_only skills for sub-agents
    if agent_name and agent_name != "coa":
        try:
            import sqlite3
            agents_db = os.environ.get("AGICTL_AGENTS_DB", "/var/lib/versa-agi/agents.db")
            if os.path.isfile(agents_db):
                conn = db_connect.connect_compat(f"file:{agents_db}?mode=ro", uri=True, timeout=3)
                coa_only = {row[0] for row in conn.execute(
                    "SELECT name FROM skills WHERE scope='coa_only'"
                ).fetchall()}
                conn.close()
                if coa_only:
                    # Exclude lines containing coa_only skill filenames
                    filtered = []
                    for line in catalog_lines:
                        skip = False
                        for skill_name in coa_only:
                            if f'"{skill_name}.md"' in line:
                                skip = True
                                break
                        if not skip:
                            filtered.append(line)
                    catalog_lines = filtered
        except Exception:
            pass  # Non-fatal — include all skills if DB unavailable

    return "\n".join(catalog_lines)


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


def _record_inputs_used(
    wake_prompt: str,
    tasks_context: str,
    conversation_context: str,
    games_context: str,
    routing_context: Optional[dict],
) -> List[str]:
    used = ["wake", "skills-catalog"]
    if tasks_context and tasks_context.strip() and tasks_context.strip() != "(none)":
        used.append("active-tasks")
    if conversation_context and conversation_context.strip() and conversation_context.strip() != "(none)":
        used.append("conversation(last-N)")
    if games_context and games_context.strip() and games_context.strip() != "(none)":
        used.append("games-digest")
    if routing_context:
        used.append("routing")
    return used


def run_triage(llm, wake_prompt: str, tasks_context: str = "",
               conversation_context: str = "", skills_dir: str = None,
               agent_name: str = "coa", routing_context: dict = None,
               games_context: str = "") -> TriageResult:
    """Execute the triage node: classify the wake prompt and determine routing.

    Args:
        llm: The triage LLM instance (from get_llm)
        wake_prompt: The wake reason prompt content
        tasks_context: Active tasks summary (pre-fetched by lifeline)
        conversation_context: Recent conversation history
        skills_dir: Path to agent's skills directory for injection
        agent_name: Agent name for scope filtering (default: coa)
        routing_context: Optional ephemeral model routing JSON
        games_context: Compact active-games digest for strategic frame

    Returns:
        TriageResult with classification, confidence, skills, and routing info
    """
    from langchain_core.messages import HumanMessage as HMsg

    games_ctx = (games_context or "").strip() or "(none)"
    tasks_ctx = (tasks_context or "").strip() or "(none)"
    convo_ctx = (conversation_context or "").strip() or "(none)"
    inputs_used = _record_inputs_used(
        wake_prompt, tasks_ctx, convo_ctx, games_ctx, routing_context,
    )

    # Build the triage prompt with dynamic skills catalog
    skills_catalog = load_skills_catalog(agent_name=agent_name)
    prompt = TRIAGE_PROMPT.format(
        wake_prompt=wake_prompt[:4000],  # Cap to prevent context overflow
        tasks_context=tasks_ctx[:2000],
        conversation_context=convo_ctx[:2000],
        games_context=games_ctx[:1500],
        skills_catalog=skills_catalog,
    )
    if routing_context:
        prompt += _format_routing_appendix(routing_context, agent_name=agent_name)

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
            inputs_used=inputs_used,
        )

    if not data:
        print("TRIAGE: Could not parse JSON response. Defaulting to pass-through.", flush=True)
        return TriageResult(
            classification="follow_up",
            confidence=0.5,
            strategy_notes="Triage JSON parse failed. Passing through to agent.",
            inputs_used=inputs_used,
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
        required_work_modality=data.get("required_work_modality"),
        recommended_model=data.get("recommended_model"),
        inputs_used=inputs_used,
    )

    print(f"TRIAGE: {result.classification} (confidence={result.confidence:.2f})", flush=True)
    adverse = adverse_signals(result.signal_results)
    if adverse:
        print(f"TRIAGE: Adverse signals: {', '.join(adverse)}", flush=True)
    if result.skills_to_inject:
        print(f"TRIAGE: Skills to inject: {', '.join(result.skills_to_inject)}", flush=True)
    if result.required_work_modality:
        print(f"TRIAGE: Work modality: {result.required_work_modality}", flush=True)
    if result.recommended_model:
        print(f"TRIAGE: Recommended model: {result.recommended_model}", flush=True)

    return result


def enrich_triage_from_inbox(result: TriageResult, agent_name: str) -> TriageResult:
    """Set has_attachments from unprocessed inbox rows (mechanics stay in poise/skills)."""
    if result.has_attachments:
        if "attachment-enrich" not in result.inputs_used:
            result.inputs_used = list(result.inputs_used) + ["attachment-enrich"]
        return result

    db_path = os.environ.get("AGICTL_MESSAGES_DB", "")
    if not db_path or not os.path.isfile(db_path):
        return result

    sub_account = ""
    config_path = os.environ.get("AGICTL_CONFIG", "")
    if config_path and os.path.isfile(config_path):
        try:
            with open(config_path, encoding="utf-8") as f:
                sub_account = (json.load(f).get("versavoice") or {}).get("sub_account_id") or ""
        except Exception:
            pass

    ids = list(dict.fromkeys(x for x in (sub_account, agent_name) if x))
    if not ids:
        return result

    placeholders = ",".join("?" * len(ids))
    try:
        import sqlite3
        conn = db_connect.connect_compat(db_path, timeout=5)
        rows = conn.execute(
            f"SELECT has_attachments, attachment_path, raw_payload FROM messages "
            f"WHERE status='unprocessed' AND direction='received' AND to_user_id IN ({placeholders})",
            tuple(ids),
        ).fetchall()
        conn.close()
    except Exception:
        return result

    paths: list[str] = []
    for has_flag, attach_path, raw_payload in rows:
        path = (attach_path or "").strip()
        if path and not path.startswith("http"):
            paths.append(path)
        if has_flag and not paths:
            result.has_attachments = True
        if raw_payload:
            try:
                payload = json.loads(raw_payload) if isinstance(raw_payload, str) else raw_payload
                attachments = payload.get("attachments") if isinstance(payload, dict) else None
                if isinstance(attachments, list) and attachments:
                    result.has_attachments = True
            except (json.JSONDecodeError, TypeError):
                pass

    if paths:
        result.has_attachments = True
        result.attachment_paths = paths
    elif result.has_attachments:
        result.attachment_paths = []

    if result.has_attachments and "attachment-enrich" not in result.inputs_used:
        result.inputs_used = list(result.inputs_used) + ["attachment-enrich"]

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
                reason = skill_reasons.get(skill_name, "Selected by triage for this cycle.")
                injected.append(
                    f"\n---\n## ── SKILL: {skill_name} (triage-selected) ──\n"
                    f"**Why injected:** {reason}\n\n{content}"
                )
                print(f"TRIAGE: Injected skill: {skill_name} ({len(content)} chars)", flush=True)
            except Exception as e:
                print(f"TRIAGE: Failed to read skill {skill_name}: {e}", flush=True)
        else:
            print(f"TRIAGE: Skill not found: {skill_path}", flush=True)

    return "\n".join(injected)


def _get_skill_reasons(result: TriageResult) -> dict:
    """Map skill filenames to short injection reasons (not protocol essays)."""
    reasons = {}
    cls = result.classification
    signals = result.signal_results or {}

    if "communication.md" in result.skills_to_inject:
        reasons["communication.md"] = f"Classification={cls}; follow messaging rules in this skill."
    if "task_scheduling.md" in result.skills_to_inject:
        reasons["task_scheduling.md"] = "Task create/update/progress may be needed; check duplicates first."
    if "work_initiation.md" in result.skills_to_inject:
        if signals.get("project_correlation"):
            reasons["work_initiation.md"] = "New work may map to an existing project — target correctly."
        else:
            reasons["work_initiation.md"] = "New work with unclear project — register or treat as disposable."
    if "git_operations.md" in result.skills_to_inject:
        reasons["git_operations.md"] = "Code/file changes likely; follow git protocols in this skill."
    if "project_management.md" in result.skills_to_inject:
        reasons["project_management.md"] = "Project setup, collaboration, or membership may be needed."
    if "requirements_elicitation.md" in result.skills_to_inject:
        reasons["requirements_elicitation.md"] = "Ambiguous scope — elicit 5W1H before committing."
    if "security_protocol.md" in result.skills_to_inject:
        reasons["security_protocol.md"] = "Security-sensitive operations may be involved."
    if "connection_lifecycle.md" in result.skills_to_inject:
        reasons["connection_lifecycle.md"] = "Connection management actions may be needed."
    if "self_introduction.md" in result.skills_to_inject:
        reasons["self_introduction.md"] = "New contact may need introduction."
    if "memory_management.md" in result.skills_to_inject:
        reasons["memory_management.md"] = "Persistent memory store/retrieve may be needed."
    if "message_relay.md" in result.skills_to_inject:
        reasons["message_relay.md"] = "Relay between users/agents may be needed."
    if "solution_architect.md" in result.skills_to_inject:
        reasons["solution_architect.md"] = "Environment/stack setup guidance may be needed."
    if "system_packages.md" in result.skills_to_inject:
        reasons["system_packages.md"] = "System package request/install may be needed."
    if "versa_agi_operations_guide.md" in result.skills_to_inject:
        reasons["versa_agi_operations_guide.md"] = (
            "PU ops/how-Versa-AGi-works guidance; follow this skill, not System Design dumps."
        )

    for skill in result.skills_to_inject:
        if skill not in reasons:
            reasons[skill] = "Selected by triage for this cycle."

    return reasons


def build_triage_context(result: TriageResult) -> str:
    """Build a provenance-labeled advisory preamble from triage results.

    High-altitude facts + strategic brief only — no static execution-order scripts.
    """
    inputs = result.inputs_used or ["wake", "skills-catalog"]
    inputs_line = " | ".join(inputs)

    lines = [
        "## ── TRIAGE RESULT (advisory) ──",
        "Source: **Triage node** (separate model from this cycle’s execution agent).",
        f"Inputs used: {inputs_line}",
        f"Not used by triage: {_NOT_USED_BY_TRIAGE}.",
        "Treat the strategic brief as **advisory** high-altitude guidance — not orders. "
        "For protocol (messaging, tasks CLI, git), follow poise and injected skills — they have fuller context.",
        "",
        f"Classification: **{result.classification}** (confidence: {result.confidence:.2f})",
    ]
    if result.strategy_notes:
        lines.append(f"Strategic brief: {result.strategy_notes}")
    if result.required_work_modality:
        lines.append(f"Work modality: **{result.required_work_modality}**")
    if result.recommended_model:
        lines.append(f"Routed model (ephemeral): **{result.recommended_model}**")
    if result.skills_to_inject:
        lines.append(f"Skills selected: {', '.join(result.skills_to_inject)}")
    if result.has_attachments:
        lines.append(
            "⚠ Inbound attachment(s) flagged — locate under `.agent/attachments/` "
            "(see poise) and use `agictl_view_image` per cli_reference / communication skill before replying."
        )
    if result.task_actions:
        lines.append(f"Task actions (labels): {json.dumps(result.task_actions)}")

    # ── Signal Summary (only truly adverse — see adverse_signals) ──
    adverse = adverse_signals(result.signal_results)
    if adverse:
        lines.append(f"Adverse signals: {', '.join(adverse)}")

    if result.classification == "informational":
        lines.append("")
        lines.append(
            "Suggested posture: if this is a **peer-agent** terminal ack / standing-by, "
            "silence is usually fine (avoid ack-of-ack). If the sender is a **human** "
            "(PU or connection) — including social check-ins or intros — prefer a brief "
            "warm acknowledgment unless poise/skills clearly say otherwise."
        )

    if result.classification == "clarification_needed":
        lines.append("")
        lines.append(
            "Note: classification is clarification_needed — consider "
            "`requirements_elicitation` if injected before irreversible work."
        )

    return "\n".join(lines)
