"""
Versa AGi — Task Triage (pre-graph)

Purpose: analyse new inbound messages and VV project/task tags against the
project/task registry and loadable skills; advise ack/ack-loop posture and
inbound-media presence; attach a certainty to each finding.

Does not re-assess Games, awareness, or other poise-already-given data.
Low-altitude protocol (CLI, mark-processed) lives in poise/skills.
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
    correlations: list = field(default_factory=list)
    ack_advice: dict = field(default_factory=dict)
    media_certainty: Optional[float] = None
    # Provenance: which triage inputs were non-empty this cycle
    inputs_used: list = field(default_factory=list)


# ═══════════════════════════════════════════════════════
# Triage Prompt — Injected as a structured analysis request
# ═══════════════════════════════════════════════════════

TRIAGE_PROMPT = """You are a spawn triage system. Analyse **new inbound only**, then output JSON.

The execution agent already has poise, Games, awareness, and the full conversation on its system prompt. Do **not** re-assess those. Do **not** invent work from old history. Do **not** teach CLI / mark-processed / snooze protocol.

## NEW INBOUND (unread messages + VV App tags + media flags — this is what you analyse):
{inbox_context}

## PROJECT / TASK REGISTRY (ID, name, description — match tags and inbound text against these):
{registry_context}

## SKILLS AVAILABLE TO LOAD (already on the agent's system prompt; recommend from this list only):
{skills_catalog}

## WAKE (clock / reason only — not the analysis object):
{wake_prompt}

## What to produce
1. **Correlations** — each new message and each VV `agiProjects` / `agiTasks` tag vs the registry. Include project_id and/or task_id when matched. **certainty** 0–1 on every row.
2. **Ack advice** — natural acknowledgement vs detected ack-loop (peer-agent terminal ack / standing-by with no new question). Human social/FYI/intros prefer a brief warm ack. Advisory wording only ("consider", "likely"). **certainty** 0–1.
3. **Recommended skills** — filenames from the loadable list the agent should `cat` this cycle (not skills already in the system prompt). **certainty** 0–1 each.
4. **Media** — `has_attachments` from inbound flags only. Do not tell the agent to view. **media_certainty** 0–1.
5. **Classification** of the **new inbound** (or of the wake if inbound is empty):
   - work_request: new work in the inbound
   - follow_up: continuation with new substance
   - informational: ack / FYI / standing-by / social — may still need a light human ack
   - clarification_needed: cannot proceed without more information
6. Signals (only from inbound + registry — do not score memory or Games):
   - direction_clarity, purpose_clarity (true = clear)
   - contradiction_check (true = contradiction found)
   - task_correlation, project_correlation (true = matched a registry row)
   - pending_question (true = inbound asks something that should be answered)
   - parallel_work_viable, risk_assessment (true = high risk)

If NEW INBOUND is empty or "(none)", say so. Do **not** claim "no new human substance" when inbound messages from a human are present.

## OUTPUT RULES
- strategy_notes: short advisory (correlations, ack posture, why these skills). No CLI, no agictl, no mark-processed, no Games posture essays, no "continue task X" unless the **inbound** assigned that work.
- task_actions: outcome labels only.
- Never instruct viewing attachments.

Output ONLY valid JSON:
```json
{{
  "classification": "work_request|follow_up|informational|clarification_needed",
  "confidence": 0.0,
  "project_id": null,
  "task_actions": [],
  "skills_recommended": [{{"name": "example.md", "certainty": 0.0}}],
  "skills_to_inject": [],
  "strategy_notes": "Short advisory",
  "parallel_work_viable": true,
  "has_attachments": false,
  "media_certainty": 1.0,
  "correlations": [{{"project_id": null, "task_id": null, "note": "", "certainty": 0.0}}],
  "ack_advice": {{"posture": "reply|silent|ack-loop", "note": "", "certainty": 0.0}},
  "signal_results": {{
    "direction_clarity": true,
    "purpose_clarity": true,
    "contradiction_check": false,
    "task_correlation": false,
    "project_correlation": false,
    "pending_question": false,
    "parallel_work_viable": true,
    "risk_assessment": false
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
- "remote_sentinel.md" — Sentinel first contact (formal duty/readiness; COA only)
- "founder_story.md" — Sharing the VersaVoice origin story
- "solution_architect.md" — System/environment setup guidance for PU
- "system_packages.md" — Requesting and installing system packages (apt)
- "versa_agi_operations_guide.md" — PU-facing Versa AGi product/ops guidance (COA only; how the system works, agitop, troubleshooting)"""

_SKILLS_CATALOG_PATH = "/var/lib/versa-agi/skills_catalog.md"

_NOT_USED_BY_TRIAGE = (
    "full poise, Games, awareness board, conversation history except unread inbox, "
    "workspace files, WBS/collaboration docs, operational memory dumps"
)

# Skills the harness already pastes into the system prompt (hybrid/full). lazy
# still pastes these today — catalog builder must match harness, not System Design.
_ALWAYS_IN_PROMPT = frozenset({
    "cli_reference_agent.md",
    "cli_reference.md",
    "skill_authoring.md",
    "memory_management.md",
    "communication_basic.md",
    "communication.md",
})
_FEATURE_GATED_SKILLS = {
    "business_admin": (
        "business_admin.md",
        "business_admin_override.md",
        "business_admin_operate.md",
    ),
}

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


def _feature_enabled(slug: str) -> bool:
    """Read [features] <slug> from setup.ini. Missing key = on (shipped default)."""
    ini = os.environ.get("AGICTL_SETUP_INI", "/etc/versa-agi/setup.ini")
    if not os.path.isfile(ini):
        return True
    try:
        in_section = False
        with open(ini, encoding="utf-8") as f:
            for raw in f:
                line = raw.strip()
                if line.startswith("[") and line.endswith("]"):
                    in_section = line[1:-1].strip().lower() == "features"
                    continue
                if in_section and "=" in line and not line.startswith("#"):
                    key, _, val = line.partition("=")
                    if key.strip().lower() == slug.lower():
                        return val.strip().lower() in ("1", "true", "on", "yes")
    except Exception:
        return True
    return True


def already_loaded_skill_names(agent_name: str = "coa") -> set:
    """Filenames already in this spawn's system prompt (do not re-offer)."""
    names = set(_ALWAYS_IN_PROMPT)
    if (os.environ.get("VERSA_FIRST_CONTACT") or "").strip().lower() == "sentinel":
        names.add("remote_sentinel.md")
    if agent_name != "coa":
        names.discard("skill_authoring.md")
        names.discard("cli_reference.md")
    return names


def _gated_skill_filenames() -> set:
    blocked = set()
    for slug, files in _FEATURE_GATED_SKILLS.items():
        if not _feature_enabled(slug):
            blocked.update(files)
    return blocked


def loadable_skills_catalog(agent_name: str = "coa") -> str:
    """Extras the agent can still load: DB rows minus already-loaded / gated / scope."""
    catalog_lines = []
    if os.path.isfile(_SKILLS_CATALOG_PATH):
        try:
            with open(_SKILLS_CATALOG_PATH, "r") as f:
                catalog_lines = [l for l in f.read().strip().splitlines() if l.strip()]
        except Exception:
            pass
    if not catalog_lines:
        catalog_lines = _FALLBACK_SKILLS_CATALOG.strip().splitlines()

    exclude_names = already_loaded_skill_names(agent_name) | _gated_skill_filenames()
    coa_only = set()
    try:
        agents_db = os.environ.get("AGICTL_AGENTS_DB", "/var/lib/versa-agi/agents.db")
        if os.path.isfile(agents_db):
            conn = db_connect.connect_compat(f"file:{agents_db}?mode=ro", uri=True, timeout=3)
            if agent_name and agent_name != "coa":
                coa_only = {row[0] for row in conn.execute(
                    "SELECT name FROM skills WHERE scope='coa_only'"
                ).fetchall()}
            overrides = {
                row[0] for row in conn.execute(
                    "SELECT name FROM skills WHERE type='override' AND status != 'draft'"
                ).fetchall()
            }
            conn.close()
            for ov in overrides:
                base = ov.replace("_override", "")
                exclude_names.add(f"{base}.md")
    except Exception:
        overrides = set()

    filtered = []
    for line in catalog_lines:
        skip = False
        for skill_name in list(exclude_names) + [f"{n}.md" for n in coa_only]:
            key = skill_name if skill_name.endswith(".md") else f"{skill_name}.md"
            if f'"{key}"' in line or f'"{key[:-3]}"' in line:
                skip = True
                break
            bare = key[:-3] if key.endswith(".md") else key
            if f'"{bare}.md"' in line:
                skip = True
                break
        if not skip:
            filtered.append(line)
    return "\n".join(filtered) if filtered else "(none)"


def load_skills_catalog(agent_name: str = "coa") -> str:
    """Loadable-extras catalog (alias used by tests / triage prompt)."""
    return loadable_skills_catalog(agent_name)


def format_loadable_skills_block(agent_name: str = "coa") -> str:
    """System-prompt block: full loadable extras menu (Lifeline/harness inject)."""
    body = loadable_skills_catalog(agent_name)
    return (
        "\n\n---\n## ── SKILLS AVAILABLE TO LOAD ──\n\n"
        "These skills are **not** already in this prompt. Load one before related work:\n"
        "`agictl execute bash \"cat .agent/skills/<name>\"` "
        "(or `cat \"$AGICTL_AGENT_DIR/skills/<name>\"`).\n\n"
        f"{body}\n"
    )


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
    inbox_context: str,
    registry_context: str,
    routing_context: Optional[dict],
) -> List[str]:
    used = ["wake", "skills-catalog"]
    if inbox_context and inbox_context.strip() and inbox_context.strip() != "(none)":
        used.append("inbox")
    if registry_context and registry_context.strip() and registry_context.strip() != "(none)":
        used.append("registry")
    if routing_context:
        used.append("routing")
    return used


def _agi_tag_ids(raw_payload):
    """Project and task IDs from VV App inbox particle fields (same as agictl)."""
    if not raw_payload:
        return [], []
    try:
        payload = json.loads(raw_payload) if isinstance(raw_payload, str) else raw_payload
    except (json.JSONDecodeError, TypeError):
        return [], []
    if not isinstance(payload, dict):
        return [], []
    project_ids = []
    for t in payload.get("agiProjects") or []:
        if isinstance(t, dict):
            pid = str(t.get("id") or "").strip()
            if pid and pid not in project_ids:
                project_ids.append(pid)
    task_ids = []
    for t in payload.get("agiTasks") or []:
        if isinstance(t, dict):
            tid = str(t.get("id") or "").strip()
            if tid and tid not in task_ids:
                task_ids.append(tid)
            pp = str(t.get("projectId") or "").strip()
            if pp and pp not in project_ids:
                project_ids.append(pp)
    return project_ids, task_ids


def _reply_to_message_id(raw_payload):
    """Inbound reply target (same id as inbox messageId / particle id)."""
    if not raw_payload:
        return ""
    try:
        payload = json.loads(raw_payload) if isinstance(raw_payload, str) else raw_payload
    except (json.JSONDecodeError, TypeError):
        return ""
    if not isinstance(payload, dict):
        return ""
    return str(payload.get("replyToMessageId") or "").strip()


def _message_identity_ids(agent_name: str) -> list:
    ids = []
    config_path = os.environ.get("AGICTL_CONFIG", "")
    if config_path and os.path.isfile(config_path):
        try:
            with open(config_path, encoding="utf-8") as f:
                sub = (json.load(f).get("versavoice") or {}).get("sub_account_id") or ""
            if sub:
                ids.append(sub)
        except Exception:
            pass
    if agent_name and agent_name not in ids:
        ids.append(agent_name)
    return ids


def build_inbox_context(agent_name: str) -> str:
    """Unread inbound bodies + VV tags + media flag. No char cap."""
    db_path = os.environ.get("AGICTL_MESSAGES_DB", "")
    if not db_path or not os.path.isfile(db_path):
        return "(none)"
    ids = _message_identity_ids(agent_name)
    if not ids:
        return "(none)"
    placeholders = ",".join("?" * len(ids))
    try:
        conn = db_connect.connect_compat(db_path, timeout=5)
        rows = conn.execute(
            f"SELECT message_id, created_at, from_user_id, display_name, "
            f"COALESCE(original_text, text, '') AS body, "
            f"has_attachments, attachment_path, raw_payload "
            f"FROM messages WHERE status='unprocessed' AND direction='received' "
            f"AND to_user_id IN ({placeholders}) ORDER BY created_at ASC",
            tuple(ids),
        ).fetchall()
        conn.close()
    except Exception:
        return "(none)"
    if not rows:
        return "(none)"

    lines = [f"{len(rows)} unread inbound message(s):", ""]
    for row in rows:
        mid, created, from_uid, dname, body, has_att, att_path, raw = row
        who = dname or from_uid or "?"
        lines.append(f"[{created}] FROM {who} ({from_uid}): {body}")
        try:
            project_ids, task_ids = _agi_tag_ids(raw)
        except Exception:
            project_ids, task_ids = [], []
        if project_ids:
            lines.append(f"  TAGGED PROJECT IDS: {', '.join(project_ids)}")
        if task_ids:
            lines.append(f"  TAGGED TASK IDS: {', '.join(task_ids)}")
        media = bool(has_att) or bool((att_path or "").strip() and not str(att_path).startswith("http"))
        if raw:
            try:
                payload = json.loads(raw) if isinstance(raw, str) else raw
                atts = payload.get("attachments") if isinstance(payload, dict) else None
                if isinstance(atts, list) and atts:
                    media = True
            except (json.JSONDecodeError, TypeError):
                pass
        lines.append(f"  media: {'yes' if media else 'no'}")
        lines.append(f"  message_id: {mid}")
        reply_to = _reply_to_message_id(raw)
        if reply_to:
            lines.append(f"  reply_to_message_id: {reply_to}")
        lines.append("")
    return "\n".join(lines).rstrip()


def build_registry_context(agent_name: str) -> str:
    """Projects and tasks: id, name/title, description. COA = all; else assigned."""
    db_path = os.environ.get("AGICTL_TASKS_DB", "/var/lib/versa-agi/coa/tasks.db")
    if not os.path.isfile(db_path):
        return "(none)"
    is_coa = (agent_name or "").lower() == "coa"
    try:
        conn = db_connect.connect_compat(f"file:{db_path}?mode=ro", uri=True, timeout=5)
        if is_coa:
            projects = conn.execute(
                "SELECT id, name, COALESCE(description, '') FROM projects "
                "WHERE status NOT IN ('archived') ORDER BY id"
            ).fetchall()
            tasks = conn.execute(
                "SELECT id, title, COALESCE(description, ''), project_id, status, assigned_to "
                "FROM tasks WHERE status NOT IN ('done', 'cancelled', 'frozen') ORDER BY id"
            ).fetchall()
        else:
            projects = conn.execute(
                "SELECT DISTINCT p.id, p.name, COALESCE(p.description, '') "
                "FROM projects p "
                "LEFT JOIN project_members pm ON pm.project_id = p.id "
                "  AND pm.member_type='agent' AND pm.member_id=? "
                "LEFT JOIN tasks t ON t.project_id = p.id AND t.assigned_to=? "
                "  AND t.status NOT IN ('done', 'cancelled', 'frozen') "
                "WHERE p.status NOT IN ('archived') AND (pm.member_id IS NOT NULL OR t.id IS NOT NULL) "
                "ORDER BY p.id",
                (agent_name, agent_name),
            ).fetchall()
            tasks = conn.execute(
                "SELECT id, title, COALESCE(description, ''), project_id, status, assigned_to "
                "FROM tasks WHERE assigned_to=? AND status NOT IN ('done', 'cancelled', 'frozen') "
                "ORDER BY id",
                (agent_name,),
            ).fetchall()
        conn.close()
    except Exception:
        return "(none)"

    lines = ["PROJECTS (id | name | description):"]
    if projects:
        for pid, name, desc in projects:
            lines.append(f"  #{pid} | {name} | {desc}")
    else:
        lines.append("  (none)")
    lines.append("")
    lines.append("TASKS (id | title | project_id | status | assignee | description):")
    if tasks:
        for tid, title, desc, proj, status, assignee in tasks:
            lines.append(f"  #{tid} | {title} | project={proj} | {status} | {assignee} | {desc}")
    else:
        lines.append("  (none)")
    return "\n".join(lines)


def run_triage(llm, wake_prompt: str, tasks_context: str = "",
               conversation_context: str = "", skills_dir: str = None,
               agent_name: str = "coa", routing_context: dict = None,
               games_context: str = "", inbox_context: str = "",
               registry_context: str = "") -> TriageResult:
    """Analyse new inbound + VV tags against the registry; recommend loadable skills."""
    from langchain_core.messages import HumanMessage as HMsg

    inbox_ctx = (inbox_context or "").strip() or "(none)"
    registry_ctx = (registry_context or "").strip() or "(none)"
    if inbox_ctx == "(none)":
        inbox_ctx = build_inbox_context(agent_name)
    if registry_ctx == "(none)":
        registry_ctx = build_registry_context(agent_name)

    inputs_used = _record_inputs_used(
        wake_prompt, inbox_ctx, registry_ctx, routing_context,
    )

    skills_catalog = loadable_skills_catalog(agent_name=agent_name)
    prompt = TRIAGE_PROMPT.format(
        wake_prompt=wake_prompt or "(none)",
        inbox_context=inbox_ctx,
        registry_context=registry_ctx,
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

    skills = data.get("skills_to_inject") or []
    for item in data.get("skills_recommended") or []:
        if isinstance(item, dict) and item.get("name"):
            name = item["name"]
            if name not in skills:
                skills.append(name)
        elif isinstance(item, str) and item not in skills:
            skills.append(item)
    already = already_loaded_skill_names(agent_name) | _gated_skill_filenames()
    skills = [s if s.endswith(".md") else f"{s}.md" for s in skills if s]
    skills = [s for s in skills if s not in already]

    media_c = data.get("media_certainty")
    try:
        media_c = float(media_c) if media_c is not None else None
    except (TypeError, ValueError):
        media_c = None

    result = TriageResult(
        classification=data.get("classification", "follow_up"),
        confidence=float(data.get("confidence", 0.5)),
        project_id=data.get("project_id"),
        task_actions=data.get("task_actions", []),
        skills_to_inject=skills,
        strategy_notes=data.get("strategy_notes", ""),
        parallel_work_viable=data.get("parallel_work_viable", False),
        has_attachments=data.get("has_attachments", False),
        signal_results=data.get("signal_results", {}),
        required_work_modality=data.get("required_work_modality"),
        recommended_model=data.get("recommended_model"),
        correlations=data.get("correlations") or [],
        ack_advice=data.get("ack_advice") or {},
        media_certainty=media_c,
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
    if "remote_sentinel.md" in result.skills_to_inject:
        reasons["remote_sentinel.md"] = "Sentinel first-contact routine (duty and readiness)."
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
        "Treat findings as **advisory** — not orders. "
        "The loadable skill catalog is already on your system prompt; load recommended skills from there. "
        "For protocol (messaging, tasks CLI, git), follow poise and skills — they have fuller context.",
        "",
        f"Classification: **{result.classification}** (confidence: {result.confidence:.2f})",
    ]
    if result.strategy_notes:
        lines.append(f"Advisory: {result.strategy_notes}")
    if result.correlations:
        lines.append(f"Correlations: {json.dumps(result.correlations)}")
    ack = result.ack_advice or {}
    if ack:
        posture = ack.get("posture") or ""
        note = ack.get("note") or ""
        cert = ack.get("certainty")
        cert_s = f" (certainty {cert})" if cert is not None else ""
        lines.append(f"Ack advice: {posture}{cert_s}" + (f" — {note}" if note else ""))
    if result.required_work_modality:
        lines.append(f"Work modality: **{result.required_work_modality}**")
    if result.recommended_model:
        lines.append(f"Routed model (ephemeral): **{result.recommended_model}**")
    if result.skills_to_inject:
        lines.append(f"Skills recommended (load from catalog): {', '.join(result.skills_to_inject)}")
    if result.has_attachments:
        lines.append(
            "⚠ Inbound attachment(s) are on disk under `.agent/attachments/` (see poise). "
            "Do not view or load them unless the PU or a Connection explicitly asked in this wake. "
            "If they asked, use `agictl_view_image` / `agictl_view_video` before describing the media. "
            "Do not invent paths or content."
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
