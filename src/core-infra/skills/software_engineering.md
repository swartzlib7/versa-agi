# Skill: Software Engineering — Product code craft

> **Trigger**: Implementing, fixing, refactoring, or reviewing **product/application code** in a registered project workspace (not host env install, not pure requirements gathering).
> **Scope**: All agents (`all`)

> **Harness tools:** Examples use shell form (`agictl group …`). In a work cycle, call the matching tool (`agictl_task`, `agictl_cycle`, …) and pass only the part **after** `agictl` as the `command` argument. Never prefix `agictl` in the argument. Full map: **cli_reference_agent.md** (*Harness tool invocation*).

## Differentiation

| Skill | Use when |
|-------|----------|
| **software_engineering** (this) | **How to change product code** well; optional staged delivery units |
| **requirements_elicitation** | **What to build** is still ambiguous (5W1H) |
| **project_management** | Project onboard; **collaboration pattern + QA reviewer** interview |
| **task_scheduling** | Creating/updating `agictl task` (due dates, decompose, progress journal) |
| **solution_architect** | **Host environment / stack install** (apt, services) — not app logic |
| **git_operations** | Clone, branch, push, credentials |
| **feature_statefold** | Feature documentation discipline (`state_*.md`) — load alongside this for feature work |

## Building vs Maintaining

Reuse the phase definitions in **project_management** (Building = pre-release freedom; Maintaining = compatibility + careful change). **Confirm which phase** before structural refactors. If unsure, ask the Primary User / COA.

## Procedure

1. **Orient** — Identify the target project (`work_initiation`). Open existing patterns in the same module before inventing new ones.
2. **Contract** — For feature work, open or create the living `state_*.md` (**feature_statefold**). Do not start a parallel `*_spec.md` / `context_*.md`. Fold interim `COLLABORATION.md` into § Collaboration if present.
3. **Delivery mode** — After Orient (+ Contract for features):
   - Honor the project/feature **collaboration plan** (pattern + `qa_reviewer`) if set.
   - If unset and work is **multi-step** or the PU will quality-test: offer **staged / milestone / continuous** (same meanings as `project_management` Step 4), or ask COA/PU. Defaults: **staged** for multi-step; **continuous** for tiny one-shot fixes.
   - **Staged / milestone:** materialize or update the WBS table in state §4; **one unit at a time** on staged (unless PU says otherwise). Mirror each active WBS row to an `agictl task` with `--project` (see **WBS ↔ Task bridge** below; load **task_scheduling**).
   - **Continuous / tiny fix:** Plan small — smallest diff that meets acceptance; no WBS ceremony beyond a single row if useful.
4. **Implement** — Match project conventions (naming, layout, error handling, tests). Follow Building/Maintaining bias.
5. **Verify** — Run or add tests for claimed behavior. Do not mark work done on untested assertions when the project has a test path. Update WBS **Agent verify**; `agictl task progress <id> "…"`.
6. **QA pause (staged / milestone)** — Do **not** start the next staged unit until the QA reviewer signs off (or PU overrides):
   - `qa_reviewer=pu` → ask the Primary User to quality-test.
   - `qa_reviewer=connection:<uid>` → notify that Connection that the unit is ready for QA; wait for sign-off.
   - Update WBS **QA** column and task progress when passed.
7. **Commit** — Clear messages; work on the agent’s assigned branch per **git_operations**. Summarize for COA review when required by your poise. Close tasks when WBS rows are Done.
8. **Escalate** — After repeated failures on the same blocker (roughly 2–3 serious attempts), stop thrashing: record what failed, open a task/awareness note, ask COA/PU.

## WBS ↔ Task bridge

| Rule | Detail |
|------|--------|
| Authority | State §4 WBS = plan of record for humans; tasks = Lifeline wake + progress journal |
| Mirror | Each active WBS row → one `agictl task add … --project <id>` (title ≈ Deliverable; desc refs WBS ID). Put the task id in the **Task ID** column |
| Progress | Agent verify → `task progress` + WBS; QA pass → progress note + WBS QA; row Done → `task done` / status done |
| Decompose | Multi-cycle unit → `task_scheduling` sub-task protocol; parent Task ID stays on the WBS row |
| Dedup | Before add: `agictl task list --all` for same project/objective |
| Not a replace | Never drop the WBS table and track only in tasks for multi-step feature work |

## Rules

- Read before write — search neighboring files and existing helpers first.
- One job per change set — split unrelated fixes.
- Documentation of **feature status/plan/results** lives in **feature_statefold**, not duplicated here.
- Host package installs and systemd stacks → **solution_architect**, not this skill.
