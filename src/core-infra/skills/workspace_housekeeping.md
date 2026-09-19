# Workspace Housekeeping

> **Purpose:** Scan this agent’s home and workspace for leftover files and folders that sit outside registered project directories. Refile what still matters, remove what does not, and keep a living manifest of those decisions.
> **Scope:** All agents (`all`).
> **Trigger:** A housekeeping **task** assigned by COA (**monthly**, first Friday 03:00 host time, or ad-hoc tidy / inspect / report). Load this skill before acting.
> **Assignment:** COA assigns *who runs the scan* with `agictl task add …` (`--agent` that person). Any agent may be assigned. Sub-agents do not run `skill new` and do not scan other homes.

> **Harness tools:** Examples use shell form (`agictl group …`). In a work cycle, call the matching tool (`agictl_task`, `agictl_cycle`, …) and pass only the part **after** `agictl` as the `command` argument. Never prefix `agictl` in the argument. Full map: **cli_reference_agent.md** (*Harness tool invocation*).

## What this is for

Registered work lives in `workspace/<project-slug>/` for a project from `agictl project list`. Scratch, old review trees, deleted-project shells, screenshot dumps, and files in `$HOME` outside `workspace/` are leftovers. They waste disk, confuse later cycles, and can hold stale secrets or build artifacts.

This skill does **not** invent new project homes. It classifies what is already on disk and either files it correctly or reports it for a decision.

## The manifest (primary)

The living file is `workspace/AGi-Tools/reports/HOUSEKEEPING_MANIFEST.md` (create `reports/` if needed). Copy `workspace_housekeeping/MANIFEST_TEMPLATE.md` once if the file does not exist. `reports/` is a System Design §IX path (`coa:agi_agents` `2770`) — `--update` restabilizes that directory so the fleet can keep writing. Do not `chown` other agents’ report files.

**Execute every cleanup against this manifest.** Dated run reports are snapshots of one pass. They are not the decision log.

The manifest records, at a high level:

- **Cleaned** — what was removed or refiled (path pattern + action + date). Not a file-by-file dump.
- **Keep** — what was left on purpose, and why.
- **Agreements** — PU (or COA, for sub-agent reports) decisions that bind later runs.

Do not re-flag a **Keep** or **Agreement** path unless the tree changed (gone, moved, or a new leftover appeared beside it). Honor those rows. When the PU answers a decision, write it into the manifest in the same cycle — do not leave it only in chat.

## Allowed vs leftover

| Keep without reporting | Treat as leftover |
|---|---|
| `workspace/<registered-project>/` | Anything else under `workspace/` |
| `$HOME/.ssh/`, `$HOME/.gitconfig` | Files/folders in `$HOME` that are not `workspace/` or a standard OS/dotfile |
| AGi-Tools / AGi-Knowledgebase (reserved), including the housekeeping manifest and dated reports | Git worktrees that are not the project's current checkout |
| In-project `__tmp/` you created this cycle | Empty shells of deleted projects; parked copies under `screenshots/`; detached review trees |
| Paths listed **Keep** or **Agreement** on the manifest | The same path if it is no longer covered by that row |

Do **not** scan other agents' homes. Do **not** touch `/etc`, system packages, or another agent's workspace.

## Procedure (every run)

### 1. Inventory registered projects

```bash
agictl project list
```

Build the allow-list of `workspace_path` values. Your workspace root is under your home (`~/workspace/` or COA: `/home/coa/coa-env/workspace/`).

### 2. Read the manifest

Open `HOUSEKEEPING_MANIFEST.md` before scanning. Note **Keep**, **Agreement**, and **Open** rows. Those are the filesystem-state record for this home.

### 3. Scan

From your home, list:

1. Direct children of `workspace/` that are **not** on the allow-list.
2. Git worktrees (`git worktree list` inside each git project) whose path is not the registered `workspace_path`.
3. Top-level items in `$HOME` other than `workspace/`, `.ssh`, `.gitconfig`, and normal OS/dotfiles (`.bashrc`, `.cursor`, `.local`, `.cache`, …). Flag unexpected named folders (handover dumps, extra repo copies).
4. Size and age: `du -sh` and `ls -ld --time-style=long-iso`.

Compare each finding to the manifest. Skip decided keeps. Carry **Open** rows forward if they are still on disk.

Write this run’s snapshot to `workspace/AGi-Tools/reports/housekeeping-$(date +%Y-%m-%d)-$VERSA_AGENT_NAME.md`. Copy `workspace_housekeeping/REPORT_TEMPLATE.md`. Point the report at the manifest path.

### 4. Act (safe by default)

**Do now, then list in the manifest (Cleaned) and the dated report:**

- Empty directory that is only a deleted-project shell (README saying "empty shell" / no unique data).
- Obvious scratch you created: `__tmp*`, failed review worktrees that are **not** the running app checkout. Remove git worktrees with `git worktree remove <path>` from the main checkout — do not `rm -rf` a worktree.
- Duplicate state files after the canonical copy is confirmed elsewhere.

**Do not delete without naming it on the manifest (Open) and waiting:**

- Anything that might still be source of truth (JSON state, credentials, unpublished copy, a worktree the live process is using).
- Another agent's files.
- `node_modules` / `.next` inside a registered project (those belong to that project).

**Refile** leftover that still matters into the matching registered project. Then remove the old path. Record the move on the manifest.

### 5. Update the manifest

In the same run, before messaging:

1. Append **Cleaned** rows for this pass.
2. Add **Keep** rows for leftovers you left on purpose (with reason).
3. Add or close **Agreement** rows when the PU has decided.
4. Refresh **Open** (still waiting) and **Last run** / **Next due**.
5. Link this run’s dated report at the top of the manifest.

### 6. Report

- **Sub-agents:** send COA the dated report **and** say the manifest was updated (`agictl message send` COA's UID, `--mode typed`, `--markdown-paths` the report file). Do **not** message the Primary User. One message. If the scan found zero leftovers and the manifest has no new Open rows, send a one-line "clean" to COA so the digest can close.
- **COA:** wait until the other agents' reports are in (or 90 minutes after the scheduled start), merge into one digest, send **one** message to the Primary User. Translate from the manifests: what was removed, what was kept, what the PU already agreed, what still needs a decision. No internal task IDs unless useful. When the PU replies, COA writes the agreement onto the relevant agent’s manifest (or tells that agent to) before the next monthly run.

### 7. Close the task for next month (recurring only)

This is a **normal recurring task**, not a Script Task. After the report, if the task is the monthly run:

```bash
agictl task progress <id> "DONE: [what you scanned/removed/refiled; manifest updated]. NEXT: first Friday 03:00 host time."
agictl task update <id> --due-date "YYYY-MM-DD 03:00:00"
```

Set `--due-date` to the **first Friday of next month at 03:00:00** in the host timezone. Leave status `planned`. Do not leave `in_progress`. Ad-hoc tidy tasks: mark done after the report and manifest update.

## Related commands

```bash
agictl project list
agictl task progress <id> "..."
agictl task update <id> --due-date "YYYY-MM-DD 03:00:00"
agictl message send <uid> "..." --mode typed --markdown-paths <report.md>
```
