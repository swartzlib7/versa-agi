# Skill: Feature Statefold — One living state doc per feature

> **Trigger**: Starting, continuing, or closing feature work that needs a plan ↔ results tracker; cleaning up stale `*_spec.md` / `context_*.md` / notes / parallel plans for the same feature.
> **Scope**: All agents (`all`)

> **Harness tools:** Examples use shell form (`agictl group …`). In a work cycle, call the matching tool (`agictl_task`, `agictl_cycle`, …) and pass only the part **after** `agictl` as the `command` argument. Never prefix `agictl` in the argument. Full map: **cli_reference_agent.md** (*Harness tool invocation*).

## Purpose

Stop documentation sprawl. Each feature has **one** living `state_<short_name>.md` that owns why, **behavior/contract**, current state, backlog, results, and change log. Fold redundant docs into it, then archive or delete the sources.

This is the **agent-facing** variant of eng-admin Cursor Statefold. It does **not** replace the Primary User’s Cursor Statefold / Orientation loop on the engineering workstation.

## Where state docs live

| Product / context | Default home |
|-------------------|--------------|
| **Versa AGi** (this runtime’s own features) | `versa-agi/design/spec/state/` |
| **Other projects** in your workspace | That repo’s existing state/docs convention; if none, create `design/spec/state/` under the project root |
| Archive of folded sources | `__archive/` **inside** that same state-doc home |

Official product overviews (Orientation, System Design, Production Plan, Change Logs) stay where they are — sync them only when a backlog `DOC-*` item says so.

## Hard rules

1. **Do not** create new `context_*.md` or `*_spec.md`.
2. **Do not** leave a live spec/plan beside a state doc for the same feature.
3. Behavior/contract lives **inside** the state doc (§ Behavior) — not a companion file.
4. Default shape: **one** `state_*.md` per feature. Hub + spoke only if the PU/COA explicitly agrees (framework epics).
5. Close every work session that touched the feature: status, Change Log, Results Feedback — and Behavior if rules changed.

## Procedure (Statefold)

1. **Name the feature** → `state_<short_name>.md` only.
2. **Inventory** related notes/specs/plans in the project; confirm what to fold (or use judgment when the task already named the sources).
3. **Merge** unique facts and behavior rules into the state doc; verify Current State against code.
4. **Archive** folded sources under `__archive/` (or delete if instructed).
5. **Work from the backlog** in that state doc; update results when you ship or learn.

## Minimal state skeleton

Copy from `.agent/skills/feature_statefold/templates/state_feature.md` (shipped asset) or create:

```markdown
# State: <Feature Title>

| Field | Value |
|-------|-------|
| **Feature** | |
| **Status** | 🟡 Planned / 🔧 In progress / ✅ Done |
| **Last verified against code** | YYYY-MM-DD |
| **Primary code** | |

## 1. Behavior / contract
## 2. Current State
## 3. Target State
## 4. Backlog / Plan
## 5. Results Feedback
## 6. Change Log
```

## Pairing

- **Code changes** → also load **software_engineering**.
- **New project onboarding** → **project_management** / **work_initiation** first, then this skill for the first feature inside that project.
