---
name: project_management
description: Guide the COA through onboarding new projects — Git-backed or local
---

> **Harness tools:** Examples use shell form (`agictl group …`). In a work cycle, call the matching tool (`agictl_task`, `agictl_cycle`, …) and pass only the part **after** `agictl` as the `command` argument. Never prefix `agictl` in the argument. Full map: **cli_reference_agent.md** (*Harness tool invocation*).


# Project Management Skill

## Purpose

Guide the Primary User through onboarding a new project into the Versa AGi workspace. Determine whether the project uses Git (GitHub/GitLab) or is a local directory, configure access, clone if needed, and register it in the system.

Always present to the user what is needed to perform the work at their level of experience and highlight if the user needs to perform any actions to enable the work.

After onboarding (or when collaboration is unset), run **Step 4: Collaboration pattern** and write the plan. When a collaboration plan exists, use it as the primary operational model and adapt it when the Primary User changes how they want to work.

## Operational Mindset

Before making architectural or structural decisions on any project, understand which phase applies:

### Building (Product Development)

This is the mindset for a **new product under active development** — not yet released, no live users.

- **No backward compatibility required.** Code, schemas, and configs can be refactored freely.
- **Setup/purge cycles are normal.** The environment may be torn down and rebuilt repeatedly during development. All scripts (`setup.sh`, `purge.sh`) must work cleanly from zero state every time.
- **No manual intervention should be required.** If a script needs you to manually create resources, fix permissions, or call APIs by hand — that's a bug in the script, not a workflow step.
- **The goal is a packaged product** that will eventually be released on the internet. Every decision should move toward a self-contained, installable system.

### Maintaining (Post-Release)

This mindset applies **after** the product is released and has live users.

- **Backward compatibility matters.** Data migrations must be safe. Breaking changes require planning, versioning, and rollback strategies.
- **Users have data.** Destructive operations need safeguards, confirmations, and audit trails.
- **Changes are cautious.** Test thoroughly, deploy incrementally, monitor for regressions.

### How to Apply

- **Always confirm which phase** the project is in before suggesting structural changes.
- During **Building**: bias toward simplicity and clean re-implementation over workarounds.
- During **Maintaining**: bias toward safety and incremental changes over wholesale rewrites.
- If unsure, **ask the Primary User** which phase applies.
- Once a project is registered and work is **product code / a named feature**, load **`software_engineering`** and **`feature_statefold`** (one living `state_*.md` per feature — no parallel specs).

## Trigger

Use this skill when:
- The Primary User mentions a **new project** or asks you to work on something new
- The user asks to **add a repository** or **set up a project workspace**
- A task requires working in a codebase that is not yet registered
- The Primary User asks to **update project metadata** (description, remote URL, branch, platform, or access token)

## Pre-Check

Before starting, check existing projects:
```bash
agictl project list
```

If the project is already registered, inform the user and skip onboarding.

## Flow

### Step 1: Ask About Git

Ask the Primary User:
> "Is this project backed by a Git repository (GitHub or GitLab), or should I create a local workspace for it?"

### Step 2A: Git-Backed Project

If the user confirms Git:

1. **Check Git readiness:**
   ```bash
   agictl system config get git_enabled
   ```

2. **If NOT enabled** — guide the user through SSH setup:
   - Inform: "Git support needs to be configured first. Please run this on your terminal:"
   - Provide: `sudo -u coa agictl project git-setup`
   - Explain: "This generates an SSH keypair. The public key will appear in your workspace folder."
   - Ask: "Which platform will you use — **GitHub** or **GitLab**?"
   - Guide: "Please add the public key (found at `~/agi-workspace/versa_agi_ed25519.pub`) as a **deploy key** on your [GitHub/GitLab] repository, then let me know when it's done."
   - Wait for user confirmation

3. **If enabled** — proceed to clone:
   - Ask: "Please share the **SSH clone URL** for the repository."
   - Example: `git@github.com:username/project-name.git`
   - Once received, clone:
     ```bash
     agictl project add <project-name> --remote <url>
     ```
   - The platform (GitHub/GitLab) is auto-detected from the URL
   - Confirm success: "Project cloned successfully. Branch: main. You can find it at `~/agi-workspace/<project-name>/`"

### Step 2B: Local Project (No Git)

If no Git:
1. Ask for the project name
2. Create and register:
   ```bash
   agictl project add <project-name>
   ```
3. Confirm: "Local project created. You can find it at `~/agi-workspace/<project-name>/`"

### Step 3: Confirmation + owner seed task

After either path, summarize:
- Project name and type (git/local)
- Platform (GitHub/GitLab, if applicable)
- Workspace path
- Current branch (if git)
- Project `id` from `agictl project list` (needed for tasks and member assign)

**Seed task (required on new projects):** Create a tracking task assigned to the **project owner agent** (the creating agent — `project_members.roles` includes `owner`; usually you). This wakes the owner to finish collaboration setup if the cycle ends early.

```bash
# due-date: soon but realistic (see task_scheduling) — example: ~30 minutes from now
agictl task add "Project setup: collaboration plan + WBS" \
  --project <project_id> \
  --assignee <owner_agent_name> \
  --due-date "YYYY-MM-DD HH:MM:SS" \
  --desc "Run project_management Step 4 (collaboration interview + QA reviewer). Write the collaboration plan (state_*.md § Collaboration or interim COLLABORATION.md). On the first feature, establish a WBS backlog table (feature_statefold). Keep WBS rows mirrored to project tasks (task_scheduling / software_engineering bridge)."
```

Before creating, check `agictl task list --all` so you do not duplicate an existing setup task for this project.

Then continue to **Step 4** (do not skip on new projects). When Step 4 and the first-feature WBS are done, journal progress and mark the seed task done (`agictl task done <id>` or update status).

### Step 4: Collaboration pattern

Establish *how* you and the Primary User will stage work and who does quality verification. This is **not** requirements elicitation (`requirements_elicitation` = *what* to build via 5W1H). This step = *how we work / who QAs*.

If a collaboration plan already exists for this project and the PU confirms it is still valid, summarize it and skip the interview. Otherwise interview and write the artifact.

#### 4a — Building vs Maintaining

If not already confirmed for this project, ask which Operational Mindset applies (Building vs Maintaining). Record the answer in the collaboration plan.

#### 4b — Delivery pattern

Ask the Primary User to choose one:

| Pattern | Meaning |
|---------|---------|
| **Staged QT units** | WBS table; one work unit at a time; QA after each unit before the next |
| **Milestone batches** | Several units built, then QA at agreed milestones |
| **Continuous** | Agent runs until a feature/milestone is done; QA at the end (still keep a WBS table for non-trivial work) |

**Defaults if the PU does not choose:** **staged** for multi-step features; **continuous** for tiny one-shot fixes.

#### 4c — QA reviewer

Ask who will quality-test / sign off:

- **Primary User** (default), or
- An elected **Connection** (must be a known contact uid)

If a Connection is elected:

1. Resolve uid via `agictl connection list` / `agictl connection list agent` as appropriate.
2. Assign them to the project if not already a member:
   ```bash
   agictl project assign <project_id> --connection <uid>
   ```
3. Record `qa_reviewer=connection:<uid>` (and display name) in the collaboration plan.
4. On staged/milestone QA pauses (see `software_engineering`), **notify that Connection** that a unit is ready for QA; do not advance to the next staged unit until they sign off (or the PU overrides).

If PU: record `qa_reviewer=pu`.

#### 4d — Write the collaboration plan

Write a short plan (pattern, Building/Maintaining, `qa_reviewer`, any notes) to:

1. **Preferred once a feature exists:** that feature’s living `state_*.md` under a **§ Collaboration** section (`feature_statefold`).
2. **Interim (no feature yet):** `workspace/{slug}/COLLABORATION.md` only.

**Fold rule:** When the first feature `state_*.md` is created, copy/merge `COLLABORATION.md` into § Collaboration and remove or archive the interim file so it is not a second live tracker. Do **not** create `*_spec.md` or `context_*.md` for this.

Example interim / § Collaboration body:

```markdown
## Collaboration

| Field | Value |
|-------|-------|
| **Mindset** | Building \| Maintaining |
| **Pattern** | staged \| milestone \| continuous |
| **qa_reviewer** | pu \| connection:<uid> |
| **QA display name** | (if Connection) |

Notes: (optional)
```

Continually adapt this plan when the PU changes pattern or QA reviewer.

## Updating Existing Projects

When the Primary User asks to change a project description or other registered metadata:

1. Resolve the project ID:
   ```bash
   agictl project list
   ```
2. Update by ID (not name):
   ```bash
   agictl project update <id> --desc "Updated summary"
   ```
   Other flags: `--remote`, `--branch`, `--platform`, `--access-token`, `--type`.
3. Confirm the JSON response includes the updated `description` (and other fields changed).

> **Name changes** are not available via CLI — use the agitop Projects panel (General tab).

## Project Lifecycle Commands

Reference for managing projects after onboarding:

| Command | Effect |
|---|---|
| `agictl project list` | Show all projects with status (includes `id`) |
| `agictl project update <id> [--desc TEXT] [--remote URL] [--branch B] [--platform github\|gitlab] [--access-token T] [--type git\|local]` | Update project metadata by ID (description, git remote/branch, platform, credentials) |
| `agictl project assign <id> --agent <name>` | Assign agent to project (provisions workspace) |
| `agictl project assign <id> --connection <uid>` | Assign a Connection as project member (e.g. elected QA reviewer) |
| `agictl project unassign <id> --agent <name>` | Remove agent from project |
| `agictl project unassign <id> --connection <uid>` | Remove Connection from project |
| `agictl project members <id>` | List project members |
| `agictl project pause <id>` | Pause — sentinel/lifeline skip this project |
| `agictl project resume <id>` | Resume a paused project |
| `agictl project archive <id>` | Archive — soft-delete, excluded everywhere |

## Important Notes

- Each project lives in `workspace/<project-name>/`
- Git projects are independent repo clones — each has its own `.git`
- The workspace is always symlinked to the Primary User's accessible path
- Platform auto-detection works for `github.com` and `gitlab.com` URLs
- The SSH key is **dedicated** (`versa_agi_ed25519`) — do not use the system default key

## Workspace Rules

1. NEVER create project files in `coa-env/` root — always inside `workspace/{slug}/`
2. Use kebab-case for directory names
3. Register every new project with `agictl project add` before starting work
4. Each project directory is self-contained
5. Commit project work with clear, project-scoped messages
6. Use `agictl project list` to check active projects each cycle

## New Project Workflow

Aligned with **feature_statefold**: one living `state_*.md` per feature. Do **not** create `*_spec.md`, `context_*.md`, or parallel “Technical Specification” files.

**Phase 1 — Register & orient:** Create `workspace/{slug}/`, register with `agictl project add`, seed the owner setup task (Step 3). Capture the project essence in the first feature’s `state_*.md` (Behavior / Current / Target). Product-level Orientation / Production Plan / Change Logs stay as official overviews only when a backlog `DOC-*` item says to sync them — they are not a substitute for the feature state doc.

**Phase 2 — Collaborate & plan:** Run Step 4 (collaboration pattern + QA reviewer). Write § Collaboration. Build a WBS backlog table in that state doc (§4 Backlog — see `feature_statefold`). Human-readable progress lives in the WBS table.

**Phase 3 — Iterative build:** Work the WBS one unit (or milestone batch) at a time per the collaboration plan. Mirror active WBS rows to `agictl task … --project <id>` for Lifeline wake and progress journals (`task_scheduling`; bridge detail in `software_engineering`). SQLite/tasks are the runtime tracker — **not** a replacement for the WBS table.

**Phase 4 — Validate:** Prototype or exercise the main path; confirm Behavior § in the state doc against real runs; update Results Feedback and Change Log. Close done tasks when WBS rows complete.

