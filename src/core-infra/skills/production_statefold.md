# Skill: Production Statefold — One living state doc per feature

> **Trigger**: Starting, continuing, or closing feature work that needs a plan ↔ results tracker; cleaning up stale `*_spec.md` / `context_*.md` / notes / parallel plans for the same feature.
> **Scope**: All agents (`all`)

> **Harness tools:** Examples use shell form (`agictl group …`). In a work cycle, call the matching tool (`agictl_task`, `agictl_cycle`, …) and pass only the part **after** `agictl` as the `command` argument. Never prefix `agictl` in the argument. Full map: **cli_reference_agent.md** (*Harness tool invocation*).

## Purpose

Stop documentation sprawl. Each feature has **one** living `state_<short_name>.md` that owns why, **behavior/contract**, current state, backlog, results, and change log. Fold redundant docs into it, then archive or delete the sources.

This is the **agent-facing** variant of eng-admin Cursor Statefold. It does **not** replace the Primary User’s Cursor Statefold / Orientation loop on the engineering workstation. Cursor homes (`versa-agi/design/spec/state/`, `docs/_admin/`) are for that workstation only — never treat them as this skill’s default.

## Doc home resolution

Resolve the home **per project**, relative to that project’s root (`workspace/{slug}/`). Never resolve against the agent env root or the PU monorepo.

1. If `docs/production/state/_project.yml` exists, use `doc_home` / `archive_home` / `shape_file` from it.
2. Else if the repo already has `state_*.md` files, adopt that directory and write `_project.yml` recording it.
3. Else default to `docs/production/state/` and write `_project.yml`.

Then:

- If `shape_file` is set, open that map first. **Extracted** row → that `state_*.md`. **Not extracted** and in session now → Extract a unit. Do not pre-create empty states for deferred rows.
- If `shape_file` is empty, open or create `state_<feature>.md` under `doc_home`.

Official product overviews (Orientation, System Design, Production Plan, Change Logs) stay where they are — sync them only when a backlog `DOC-*` item says so.

## Project config

File: `docs/production/state/_project.yml` (inside the default doc home). All paths are relative to the **project root**.

```yaml
# docs/production/state/_project.yml — Versa AGi statefold config for this project
doc_home: docs/production/state/
archive_home: docs/production/state/__archive/
shape_file:          # optional; a shape_<solution>.md map, leave empty if none
```

| Key | Required | Meaning |
|-----|----------|---------|
| `doc_home` | yes | Directory for `state_*.md`. Default `docs/production/state/`. |
| `archive_home` | yes | Folded leftovers. Default `docs/production/state/__archive/`. |
| `shape_file` | no | Optional map. Empty until a harvest starts. Do not invent a map. |

- If a required key is present but empty and the work needs it, **ask the PU/COA**. Do not invent a third home.
- **Git-backed projects:** each agent has its own clone — commit `_project.yml` so it propagates.
- **Local projects:** agents symlink to COA’s directory — the file is shared immediately.
- Local and `--git-init` projects may already have this file from `agictl project add`. Cloned remotes do not — write it on first feature after confirming with the PU.
- Copy the stub from `.agent/skills/production_statefold/templates/_project.yml`.

## Hard rules

1. **Do not** create new `context_*.md` or `*_spec.md`.
2. **Do not** leave a live spec/plan beside a state doc for the same feature.
3. Behavior/contract lives **inside** the state doc (§ Behavior) — not a companion file.
4. Default shape: **one** `state_*.md` per feature. Hub + spoke only if the PU/COA explicitly agrees (framework epics).
5. Close every work session that touched the feature: status, Change Log, Results Feedback — and Behavior if rules changed.
6. Do not use `versa-agi/design/spec/state/` unless this project’s `_project.yml` (or an existing `state_*.md` tree) actually points there.

## Hub + spoke (optional — needs agreement)

Default remains **one** `state_<feature>.md` per feature. Propose hub + spoke only when a single file would become an unreadable mega-doc (shared rules + multiple independently shippable units).

| Role | File | Owns |
|------|------|------|
| **Hub** | `state_<epic>.md` | Shared behavior/contract, architecture, ship gate, cross-cutting backlog, links to spokes |
| **Spoke** | `state_<unit>.md` | Unit-specific contract, backlog, code anchors for that unit only |

1. **Ask before splitting.** Do not invent spokes unilaterally.
2. Each file is still the sole live tracker for that named unit.
3. Hub holds shared rules; spokes do not restate the epic.
4. Create a spoke only when that unit is in session now. Deferred units stay as hub backlog rows — no empty spoke files.
5. If `shape_file` is set, that map is the session entry; otherwise the hub is.

**When to propose:** shared registry/transport/pipeline + several units; shared protocol + adapters. **When not to:** one delivery surface; two loosely related features (those are separate state docs).

## Procedure (Statefold)

1. **Resolve the doc home** (section above). Name the feature → `state_<short_name>.md` only.
2. **Inventory** related notes/specs/plans in the project. Present a candidate include set. Confirm (or use judgment when the task already named the sources).
3. **Latent features** — if other distinct features appear in those files, present them as candidate state docs only:

```
Latent features found — add to Statefold cycle?
- [ ] <feature_short_name> → state_<feature_short_name>.md
      Evidence: <file:section or symbol>
      Suggested: Statefold now / defer / ignore
```

   Wait for **Statefold now / defer / ignore**. Never create extra state docs without confirmation.
4. **Merge** unique facts and behavior rules into the state doc; verify Current State against code.
5. **Archive** folded sources under `archive_home` (or delete if instructed).
6. **Work from the backlog** in that state doc; update results when you ship or learn.
7. If `workspace/{slug}/COLLABORATION.md` exists, **fold** it into § Collaboration on the first feature state doc and remove/archive the interim file.

### Extract a unit (only when `shape_file` is set and that unit is in session)

1. Create `state_<unit>.md` (or the agreed spoke) under `doc_home` from `.agent/skills/production_statefold/templates/state_feature.md`.
2. Harvest that source slice into the state (copy allowed). Verify behavior against code.
3. Mark the shape-file row extracted and link the state. Do not pre-create empty states for deferred rows.
4. Map skeleton: `.agent/skills/production_statefold/templates/shape_solution.md`.

Harvest later: fill `shape_file` in `_project.yml` and follow this subsection. Do not invent a map now.

## Collaboration §

Copy pattern / `qa_reviewer` from **project_management** Step 4. Prefer this section over a long-lived `COLLABORATION.md`.

## Backlog / Plan (WBS)

When work is **multi-step** or the collaboration pattern is **staged** or **milestone**, §4 **must** use a WBS table:

| ID | Deliverable | Depends | Agent verify | QA | Status | Task ID |
|----|-------------|---------|--------------|-----|--------|---------|

- **Tiny one-shot fixes** may use a single row or a short checklist instead.
- **Task ID** — mirror each active row to an `agictl task` with `--project` (`task_scheduling`; bridge in `software_engineering`). State WBS remains the human-readable plan of record; tasks are the wake/progress system.
- **software_engineering** staged units write/update this table and pause for the QA reviewer in § Collaboration.

## Minimal state skeleton

Copy from `.agent/skills/production_statefold/templates/state_feature.md` (shipped asset) or create:

```markdown
# State: <Feature Title>

> **Doc home:** <resolved path, e.g. docs/production/state/>

| Field | Value |
|-------|-------|
| **Feature** | |
| **Status** | 🟡 Planned / 🔧 In progress / ✅ Done |
| **Last verified against code** | YYYY-MM-DD |
| **Primary code** | |

## Collaboration
| Field | Value |
|-------|-------|
| **Mindset** | Building \| Maintaining |
| **Pattern** | staged \| milestone \| continuous |
| **qa_reviewer** | pu \| connection:<uid> |

## 1. Behavior / contract
## 2. Current State
## 3. Target State
## 4. Backlog / Plan (WBS)
## 5. Results Feedback
## 6. Change Log
```

## Pairing

- **Code changes** → also load **software_engineering**.
- **New project onboarding** → **project_management** / **work_initiation** first, then this skill for the first feature inside that project.
- **Tasks for WBS rows** → load **task_scheduling**.

## Anti-patterns

- Creating `*_spec.md` or `context_*.md` “for clarity”
- Two live docs for one feature (state + spec, or competing plans)
- Splitting hub + spoke without user agreement; pre-creating empty deferred spokes
- Restating the whole epic in every spoke
- Treating chat or overview docs as source of truth
- Closing a session without updating the feature state doc
- Expanding scope without backlog items in the state doc
- Using `versa-agi/design/spec/state/` (or any monorepo path) as a default for a workspace project
- Inventing a third doc home when `_project.yml` is missing or a key is empty
- Inventing a `shape_file` / harvest map when none exists
- Pre-creating empty states for deferred shape-file rows
- Leaving `_project.yml` uncommitted on a git-backed project
