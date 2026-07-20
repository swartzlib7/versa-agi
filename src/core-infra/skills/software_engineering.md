# Skill: Software Engineering — Product code craft

> **Trigger**: Implementing, fixing, refactoring, or reviewing **product/application code** in a registered project workspace (not host env install, not pure requirements gathering).
> **Scope**: All agents (`all`)

> **Harness tools:** Examples use shell form (`agictl group …`). In a work cycle, call the matching tool (`agictl_task`, `agictl_cycle`, …) and pass only the part **after** `agictl` as the `command` argument. Never prefix `agictl` in the argument. Full map: **cli_reference_agent.md** (*Harness tool invocation*).

## Differentiation

| Skill | Use when |
|-------|----------|
| **software_engineering** (this) | **How to change product code** well |
| **requirements_elicitation** | **What to build** is still ambiguous (5W1H) |
| **solution_architect** | **Host environment / stack install** (apt, services) — not app logic |
| **git_operations** | Clone, branch, push, credentials |
| **feature_statefold** | Feature documentation discipline (`state_*.md`) — load alongside this for feature work |

## Building vs Maintaining

Reuse the phase definitions in **project_management** (Building = pre-release freedom; Maintaining = compatibility + careful change). **Confirm which phase** before structural refactors. If unsure, ask the Primary User / COA.

## Procedure

1. **Orient** — Identify the target project (`work_initiation`). Open existing patterns in the same module before inventing new ones.
2. **Contract** — For feature work, open or create the living `state_*.md` (**feature_statefold**). Do not start a parallel `*_spec.md` / `context_*.md`.
3. **Plan small** — Prefer the smallest diff that meets the acceptance criteria. No drive-by refactors unrelated to the task.
4. **Implement** — Match project conventions (naming, layout, error handling, tests). Follow Building/Maintaining bias from step 0.
5. **Verify** — Run or add tests for claimed behavior. Do not mark work done on untested assertions when the project has a test path.
6. **Commit** — Clear messages; work on the agent’s assigned branch per **git_operations**. Summarize for COA review when required by your poise.
7. **Escalate** — After repeated failures on the same blocker (roughly 2–3 serious attempts), stop thrashing: record what failed, open a task/awareness note, ask COA/PU.

## Rules

- Read before write — search neighboring files and existing helpers first.
- One job per change set — split unrelated fixes.
- Documentation of **feature status/plan/results** lives in **feature_statefold**, not duplicated here.
- Host package installs and systemd stacks → **solution_architect**, not this skill.
