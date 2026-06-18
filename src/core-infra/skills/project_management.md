---
name: project_management
description: Guide the COA through onboarding new projects — Git-backed or local
---

> **Harness tools:** Examples use shell form (`agictl group …`). In a work cycle, call the matching tool (`agictl_task`, `agictl_cycle`, …) and pass only the part **after** `agictl` as the `command` argument. Never prefix `agictl` in the argument. Full map: **cli_reference_agent.md** (*Harness tool invocation*).


# Project Management Skill

## Purpose

Guide the Primary User through onboarding a new project into the Versa AGi workspace. Determine whether the project uses Git (GitHub/GitLab) or is a local directory, configure access, clone if needed, and register it in the system.

Always present to the user what is needed to perform the work at their level of experience and highlight if the user needs to perform any actions to enable the work.

Then setup a basic plan to collaborate on the project. When such a plan exists, use it as the primary operational model for collaboration and coordination. Continually adapt the plan it based on what is workable and what is needed and wanted by the user.

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

### Step 3: Confirmation

After either path, summarize:
- Project name and type (git/local)
- Platform (GitHub/GitLab, if applicable)
- Workspace path
- Current branch (if git)

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
| `agictl project unassign <id> --agent <name>` | Remove agent from project |
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

**Phase 1 — Conceptualization:** Create `workspace/{slug}/`, register with `agictl project add`, produce a Product Specification capturing the essence, components, technologies, and process flow.
**Phase 2 — Specification:** Create a Production Plan with high-level component list. Each component spec gets its own Technical Specification file.
**Phase 3 — Iterative Build:** Work through the Product Specification systematically, using SQLite tasks to track progress across cycles. Each iteration gathers and confirms a Software Configuration Object for the product.
**Phase 4 — Prototype:** Build a basic prototype to test the main concept. Confirm all assumptions and cross-reference with official technical documentation.

