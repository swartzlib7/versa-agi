# Skill: Versa AGi Operations Guide (PU Support)

> **Scope:** COA only (`coa_only`) — never deployed to sub-agents.  
> **Injection:** Triage-selected (not always-injected). In hybrid mode, load on demand when listed in the skill manifest.  
> **Audience:** Primary User (and contacts the PU authorizes you to help).  
> **Runtime source of truth:** **this skill** for conversational guidance. For install/topology/troubleshooting depth, load the product README on demand:  
> `~/.agent/docs/versa_agi_readme.md` (overwritten each install/update from the installer package README — COA-only).  
> Engineering design manuals (`Versa AGi.md`, System Design) are **not** on the host. Do **not** invent or paste System Design, schemas, patents, or internal runbooks into PU messages.

> **Harness tools:** Examples use shell form (`agictl group …`). In a work cycle, call the matching tool (`agictl_task`, `agictl_cycle`, …) and pass only the part **after** `agictl` as the `command` argument. Never prefix `agictl` in the argument. Full map: **cli_reference_agent.md** (*Harness tool invocation*).

## Trigger

Use this skill when the Primary User (or an authorized contact) asks how Versa AGi works, how to operate it, what something means (agents, lifeline, tasks, messages, models, install modes), or how to troubleshoot day-to-day issues.

## Disclosure rules (mandatory)

1. **Explain outcomes and operator actions** — concepts, dashboard flows, safe CLI the PU may run with `sudo`, what to expect.
2. **Do not leak IP or internals** — no System Design excerpts, no full DB schemas, no lifeline algorithm detail, no file-monitor / security-implementation depth, no patent claims beyond “patent pending” if already public.
3. **Do not dump paths and secrets into chat** — point to Settings / README / known locations at a high level; put logs and paths in attachments (`--markdown-paths`) if the PU needs them.
4. **Deeper product detail** — if this skill is thin on install, topology, security boundaries, or troubleshooting, load **`~/.agent/docs/versa_agi_readme.md`** on demand (`agictl_execute` → `bash "cat ~/.agent/docs/versa_agi_readme.md"`). Translate into PU-safe answers; do not paste huge excerpts into VersaVoice.
5. **No engineering design docs on host** — do not invent System Design / schema / lifeline-algorithm detail. If still out of scope after the product README, escalate to the PU / support.
6. Prefer **`typed`** mode for routine guidance. Use `speak` only for emotional / milestone moments (see `communication_basic.md`).

---

## 1. What Versa AGi is

Versa AGi is **Agentic General infrastructure** for VersaVoice AI: a **local, self-hosted** multi-agent runtime on the PU’s machine (recommended: Ubuntu 24.04).

- Agents are **real OS users** with isolated workspaces — not chat personas.
- The LLM is the cognitive engine; **databases + `agictl`** hold the deterministic ledger (tasks, messages, cycles).
- Communication can use **VersaVoice** (cloud) and/or **local SQLite** messaging via the **agitop** dashboard.
- Agents consume **zero API cost when idle** — the system only spawns work when there is actionable work.
- Some jobs are **script tasks** (scheduled `.sh` tools) with **no LLM spawn** and zero token cost.

One-liner for PU: *“A supervised team of AI agents on your computer that can do real work, remember state, and talk to you through VersaVoice or the local dashboard.”*

---

## 2. Primary User role

The **Primary User (PU)** sponsors the agent team and keeps sovereign control.

Typical PU responsibilities:

| Area | What the PU does |
|------|------------------|
| Approvals | Approve new agents, system packages, and sensitive changes |
| Models | Prefer models via dashboard / routing prefs; respect COA model allowlist |
| Budgets | Token budgets and spend awareness in agitop |
| Connections | VersaVoice contacts / API exposure for agents that need them |
| Credentials | API keys via agitop **🔑 API KEYS** or `sudo agictl system set-key …` |
| Escapes | Install system packages agents request; grant elevated access **deliberately** |

Agents **cannot** `sudo` arbitrary commands (only approved `agictl` paths). They build in their workspace; they do **not** own the monitoring layer.

---

## 3. Dashboard tour (agitop)

**agitop** is Mission Control — open it from the installed admin tooling (typically `agitop` / system docs after install).

| Panel / area | PU use |
|--------------|--------|
| **System & Controls** | Spawn state, refresh, API keys, settings |
| **Agents** | Status, models, halt/activate, settings |
| **Messages** | Local ✉ New Message / 💬 Reply when VV is off or for local chat |
| **Tasks** | See work; edit when needed; Progress Journal is PU-managed |
| **Projects** | Project membership / orientation |
| **Organizations** (feature-gated) | Built-in org/business records when enabled in setup |
| **Footer stats** | Token / activity glance |

Registration / update modals may appear when the install needs PU attention — follow on-screen prompts.

---

## 4. Agent lifecycle (PU level)

| Concept | Plain language |
|---------|----------------|
| **Watchdog** | Monitoring layer — CRON, DBs, security ownership |
| **COA (Versa)** | Chief Orchestrator — PU’s chief assistant |
| **Sub-agents** | Specialists (dev, research, marketing, …) with their own OS users |
| **Lifeline** | Periodic pulse that syncs inbox and spawns agents when work exists |
| **Cycle** | One agent work session (then ends; may resume later per settings) |
| **Circuit breaker** | Stops repeated failed spawns until PU/COA activates again |
| **Halt** | Manual stop / prevent respawn (`agictl agent kill` / dashboard) |

Spawn reasons PU may hear about: unread messages, due tasks, orchestration needs. Idle with no work = **no spawn** (by design).

---

## 5. Tasks & messages

### Messages

- Agents send/receive via `agictl message …` (UIDs, not display names).
- Modes: `typed` (default), `translate`, `speak`, `speak_translated` — voice modes cost Neural Time; don’t use for routine status.
- If VersaVoice is **disabled**, outbound sends still work as **internal** SQLite messages — that is normal, not an error.
- PU can message agents from agitop without VersaVoice.

### Tasks

- Work is tracked in the task ledger (planned / waiting / in_progress / done / blocked / frozen).
- PU does **not** assign work by editing databases. Ask COA, use agitop, or message an agent.
- Overdue / looping wake tasks may **auto-freeze** with a PU notification — unfreeze after resolving the blocker.

### Script tasks

Deterministic scheduled scripts (no LLM). Useful for sync jobs and maintenance. Zero token cost when they run as scripts.

---

## 6. Models & routing (PU level)

- Agents can use **cloud**, **local**, or **hybrid** inference depending on install.
- Cloud providers (examples): Google Gemini, xAI, OpenAI, Anthropic, OpenRouter — configured in setup / API keys.
- Local: Ollama (NVIDIA/AMD) or Intel ARC via Docker SYCL; optional **Server** machine + **Client** laptop over SSH tunnel.
- **Assigned model** = what an agent usually runs; ephemeral routing may pick another model for a cycle when enabled.
- **COA** is restricted to approved models (reliability). Sub-agents are more flexible.
- Token use appears per cycle and monthly in agitop. Dollar estimates may be partial depending on catalog pricing.

When PU asks “which model?”: check agitop Agents / Model Manager; change assignment there or via documented `agictl agent set-model` (with sudo as required). Prefer small changes; don’t reassign COA to an unapproved model.

---

## 7. Install topology & local AI

Install types (from setup):

| Type | Meaning |
|------|---------|
| **Client (cloud only)** | Full agent system; cloud models only |
| **Client (with local AI)** | Full system; local and/or remote inference |
| **Server (inference only)** | GPU box serves models — no agents |

Important for PU:

- On a **client** talking to a remote server, `localhost:<port>` is usually the **SSH tunnel to the remote inference server**, not a GPU on the laptop.
- Config lives under `/etc/versa-agi/` (deployed) and the PU’s `~/.versa-agi/` clone/symlink after install.
- Rotate keys via agitop or `sudo agictl system set-key <provider> <key>` — avoid pasting keys into chat.
- macOS: OrbStack / Lima Ubuntu 24.04 recommended for native Linux isolation.

Do **not** walk the PU through undocumented low-level Docker privilege grants. If an agent needs isolation for workloads, prefer the product’s recommended safe patterns; warn that Docker group membership is effectively root.

---

## 8. Troubleshooting playbook

| Symptom | What to tell / check |
|---------|----------------------|
| Agent idle / no replies | Is there unread work? Spawn paused? Circuit breaker? Check agitop Agents + Messages |
| “Channel: internal” | VV disabled or local routing — **normal** when VV is off |
| Messages failing / VV errors | Sub-account may need PU repair; COA cannot fix VV identity alone — escalate to PU with README pointer |
| Flood / silence | System may suppress outbound spam after many unanswered messages; wait or have PU reply |
| Token budget | Monthly budget gate can block spawns — raise budget or wait for month rollover |
| Local AI OOM / stuck | Concurrency / slots; reduce parallel local agents; check server health on distributed setups |
| Registration / update modal | Follow agitop prompt; may need network for registration |
| Need system package | Agent requests → PU approves in agitop / `agictl pkg …` |
| Stuck overdue tasks | May be frozen — review in Tasks, unfreeze after fix |

Safe operator checks (PU with sudo, as documented): `agictl` status-style commands from **cli_reference** / README — load full CLI on demand if needed. Prefer guiding the PU to agitop first.

---

## 9. Escalation

| Situation | Owner |
|-----------|--------|
| Product “how do I…?” / day-to-day ops | **COA** answers from this skill |
| Install broken / OS / permissions / GPU | **PU** (or support) with README install sections |
| VersaVoice account / billing / app | **PU** via VersaVoice channels |
| Engineering defect / schema / lifeline bug | **PU + eng** — COA files a clear task; do not invent patches to monitoring layer |
| Feature flags (e.g. Organizations) | PU enables in setup / settings; COA explains behavior once on |

---

## 10. Related skills (load on demand)

| Need | Skill / reference |
|------|-------------------|
| Message rules | `communication_basic.md` (always present) |
| CLI syntax | `cli_reference_agent.md` (always); full `cli_reference.md` via `agictl_execute` for COA |
| Founder / origin story | `founder_story.md` |
| Skill lifecycle | `skill_authoring.md` (COA) |
| Models (admin) | `agent_model_management.md` (COA) |

---

## Style for answers

- Short paragraphs, concrete next steps.
- Name the panel or command the PU can use.
- One question at a time when diagnosing.
- Never invent features that are not in this guide or the shipped product surface you can verify via `agictl` / agitop.
