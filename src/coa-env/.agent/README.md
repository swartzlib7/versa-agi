# Agent Workspace

> This directory is your managed workspace. It is governed by the system infrastructure.

## What You Own

| Directory | Purpose |
|---|---|
| `skills/` | Behavioral skill files — you may create and update these |
| `workspace/` | Managed project workspace — mapping symlink to `~/coa-env/workspace/` |
| `attachments/` | **READ-ONLY** — inbound message attachments auto-downloaded by Lifeline. Symlinked externally outside this payload zone into `~/coa-env/attachments/`. |
| `archive/` | **READ-ONLY** — cyclical state databases. Symlinked externally out to `~/coa-env/archive/`. |

---

## Workspace Directory (`workspace/`)

Projects are organized as top-level directories within `workspace/`. Each directory is a self-contained project workspace that you manage via `agictl project add`.

### Structure

```
workspace/
├── Project Alpha/       ← Example: A Git-backed project
└── Project Bravo/       ← Example: A local project
```

### Rules

1. **One folder per project** — never mix project files across directories
2. **Use `agictl`** to register projects: `agictl project add "Project Name"`
3. **Git-backed projects** are cloned here: `agictl project add "Project Name" --remote <ssh-url>`
4. **Local projects** are created here: `mkdir -p .agent/workspace/"Project Name"`
5. **Never create project files** in `coa-env/` root — always inside `workspace/{your-project-name}/`
6. **Commit work** via Git with clear, descriptive messages after each phase

### Examples

#### Project Alpha
A Git-backed project cloned from a remote repository. The agent manages commits, pushes, and pull requests.

#### Project Bravo
A local project created directly in the workspace. Suitable for documentation, planning, or work that doesn't need a remote repository.

### Access

The Primary User can access this workspace via the symlink at `~/agi-workspace/` (configured during setup).

Read **`project_management.md`** in skills for the full onboarding flow and 4-phase project workflow.

---

## Externalized (Not Here)

The following are managed by the system infrastructure and are **not in your workspace**:

| Item | Location | Access |
|---|---|---|
| `agent_memory.db` | `/var/lib/versa-agi/<agent>/` | Via `agictl` commands only |
| `poise.md` | `/etc/versa-agi/poise/` | Read-only, injected into `system.md` by Lifeline |
| `.env` / credentials | `/etc/versa-agi/` | Injected as env vars by Lifeline |

## Restrictions

This directory is owned by the system infrastructure (`watchdog`). You **cannot create new files or directories** at this level. All your writable spaces are the subdirectories listed above.

- `agictl` is installed globally at `/usr/local/bin/` — use it directly
- Configuration is externalized and injected by the Lifeline — do not attempt to create config files
- **Direct `sqlite3` access to the database is blocked** — all DB operations go through `agictl`

## How to Access What You Need

- **Your identity**: `agictl system whoami`
- **System config values**: `agictl system config get <key>`
- **Conversation history**: `agictl message get YOUR_SUB_ACCOUNT_ID --unread`
- **Task management**: `agictl task list`
- **Reminders**: `agictl task list` (reminders are tasks with category)

All operational data flows through `agictl`. You never need to read config files directly.
