# Script Tasks — Deterministic Scheduled Scripts

> **Purpose**: Run an agent-authored `.sh` from the shared **AGi-Tools** repo on a schedule (or once-off) via lifeline — **no agent spawn, no LLM, zero token cost**. Distinct from a Normal Agent work cycle and from a Utility Model run.
> **Scope**: All agents (`all`)

> **Harness tools:** Examples use shell form (`agictl task …`). In a work cycle, call tool **`agictl_task`** and pass only the part **after** `agictl` as the `command` argument.

## Mental model

| Concept | What it is |
|---------|------------|
| **Normal Agent** | LangGraph cycle — tools, memory, tasks, conversation (consumes judgment) |
| **Utility Model** | `utility_models` row — catalog key + system prompt → artifact (one-shot LLM) |
| **Script Task** | `task_kind=script` + `script_path` → lifeline runs a `.sh` from AGi-Tools as **`assigned_to`** on the due date — **no harness, no model** |

Use a **Script Task** for deterministic, repeatable work where a reasoning cycle is wasteful: integration syncs, exports, backups, housekeeping, health probes. Use a **Normal Agent** when the work needs judgment, tools, or multi-step coordination. Use a **Utility Model** when you need a one-shot *generated* artifact.

## Authoring the script (AGi-Tools)

Script Tasks run **only** top-level `*.sh` files in the shared **AGi-Tools** repository. Author the tool there per `shared_tooling.md`, then attach it to a task.

Authoring rules (see `shared_tooling.md` for the full list):

- Single `.sh` at the **top level** of AGi-Tools, executable (`chmod +x`).
- **Idempotent** — a recurring task re-runs the same script; running twice must not corrupt state.
- **Exit non-zero on failure** — the return code drives task routing (`done` / `blocked`). Never `exit 0` on an error path.
- **Own your logs** — only the last few output lines are surfaced; write durable logs yourself and print a concise final status.
- **Stay within the runtime budget** — long runs are killed at `[script_tasks] max_runtime_seconds` (rc `124`).

## Creating a Script Task

Create via CLI or agitop Task modal → **Utility / Script** tab → **Script** mode:

```bash
agictl task add "Nightly export sync" --assignee coa --due-date "2026-06-22 02:00:00" \
  --script-task --script-path nightly_export.sh \
  --script-parameters "--region us --full" \
  --script-interval 86400 \
  --utility-start-alert --utility-stop-alert
```

| Flag | Meaning |
|------|---------|
| `--script-task` | Sets `task_kind=script` |
| `--script-path` | Top-level `.sh` filename in AGi-Tools (required) |
| `--script-parameters` | Args passed verbatim to the script (argv, no shell) |
| `--script-interval` | Recurrence seconds — **blank/0 = once-off**; positive = reschedule each run |
| `--utility-start-alert` | VersaVoice short message to PU when the run starts |
| `--utility-stop-alert` | VV message to PU on finish (carries rc + output tail) |

> Script Tasks reuse the Start/Stop **alert** columns shared with Utility Tasks. A task is **either** a Script Task **or** a Utility Task — never both.

## Scheduling behavior

Lifeline runs due Script Tasks each tick (`agictl task run-due-scripts`), **before** normal agent spawn — no agent is woken.

| Interval | Outcome |
|----------|---------|
| Blank / `0` (once-off) | Task → `done` (rc `0`) or `blocked` (rc ≠ 0) |
| Positive seconds (recurring) | Stays `planned`; `due_date` advances by the interval for the next run |

Every run records `script_last_rc` / `script_last_run_at` and appends a `task progress` journal entry with the return code and output tail. A timeout is reported as rc `124`.

## Reviewing results

```bash
agictl task get <id>          # script_last_rc, script_last_run_at, recent_progress journal
agictl task progress <id>     # full run history (rc + output tail per run)
```

A **`blocked`** Script Task means the last run exited non-zero (or timed out). Read the journal tail, fix the script in AGi-Tools, then re-arm the task (`agictl task update <id> --status planned --due-date "..."`).

## Containment & safety

- Scripts must resolve **inside** the AGi-Tools root (realpath-checked — path traversal and symlink escape are rejected); only `.sh` is allowed.
- A per-host run-lock prevents overlapping runs of the same task/script.
- The script executes as the task's **`assigned_to`** OS user with AGi-Tools as the working directory — normal UNIX permissions apply.
- The shared **AGi-Tools** and **AGi-Knowledgebase** projects are **reserved** — they cannot be archived or deleted.

## When to escalate to COA / PU

- Script missing from AGi-Tools or not executable → author/fix it per `shared_tooling.md`
- Repeated `blocked` runs → review the script's logs and exit codes; do not loop a broken job
- Need a brand-new shared tool → build and document it in AGi-Tools first, then attach the task
- Feature appears disabled (Task modal mode hidden / lifeline skips) → PU checks `setup.ini [script_tasks] enabled`

## Cross-links

- Authoring shared tools: `shared_tooling.md`
- One-shot generation instead: `utility_models.md`
- Task lifecycle: `task_scheduling.md`
- Full operator CLI: `cli_reference.md` (PU / COA admin)
