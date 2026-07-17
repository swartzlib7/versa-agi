# Task Scheduling — Expert Scheduling Protocol

> **Trigger**: When creating, snoozing, or re-planning any task. When reviewing task backlogs. When multiple tasks or agents have overlapping timelines. When work involves external parties or multi-cycle execution.

## Core Principles

1. **One task, one purpose.** Never create duplicate tasks for the same objective. Before creating a task, check `agictl task list --all` for existing coverage.
2. **Every task MUST have a `--due-date`.** Tasks without due dates are invisible to the Lifeline and will never trigger a wake cycle.
3. **Due dates are commitments, not wishes.** Set realistic dates based on actual capability, not optimistic estimates.
4. **Self-assignment is automatic.** The system defaults `--assignee` to your agent name. Only override with `--assignee <other>` if COA or another agent should own the task.
5. **Every task MUST have a `--project`.** Use `agictl project list` to find the correct project ID. Tasks without a project are orphaned and harder to track. The system will warn you if you omit `--project`.

---

> **Harness tools:** Examples use shell form (`agictl group …`). In a work cycle, call the matching tool (`agictl_task`, `agictl_cycle`, …) and pass only the part **after** `agictl` as the `command` argument. Never prefix `agictl` in the argument. Full map: **cli_reference_agent.md** (*Harness tool invocation*).


## Scheduling Rules

### Time Awareness

- **Know the current time.** Always check the system clock before scheduling:
  ```bash
  date -u '+%Y-%m-%dT%H:%M:%SZ'
  ```
- **Never schedule in the past.** If a task's due date has passed, roll it forward — never leave it behind.
- **Respect time zones.** The Primary User's timezone is in their PROFILE. Schedule deliverables for their waking hours.

### Realistic Timeline Estimation

| Task Type | Minimum Lead Time | Example |
|---|---|---|
| Simple acknowledgment | 2 minutes | "Confirm receipt" |
| Research / lookup | 10-30 minutes | "Find information about X" |
| Document creation | 1-4 hours | "Draft a report", "Create a plan" |
| Multi-step coordination | 1-2 days | "Coordinate with 3 people" |
| External dependency | 1-7 days | "Wait for someone to respond" |
| Recurring task | Next occurrence only | "Daily update at 6:30 PM" |

**NEVER** create a task due in 2 minutes for something that takes 30 minutes of work. This causes runaway scheduling.

### Duplicate Prevention

Before creating any task, run:
```bash
agictl task list --all
```

Check for:
- **Exact duplicates** — same title or objective as an existing task
- **Overlapping scope** — existing task already covers this work
- **Superseded tasks** — old tasks that should be marked `done` now that a new approach exists

If a duplicate exists:
- Update the existing task's description or due date instead of creating a new one
- Mark truly obsolete duplicates as `done`

### Recurring Tasks

**DO NOT create one task per occurrence.** Instead:
1. Create ONE task for the recurring activity (e.g., "Daily status update at 6:30 PM")
2. After completing each occurrence, roll the due date forward to the next scheduled time
3. Never create tasks 9, 10, 11, 12, 14 that all say "Daily Status Update" — that's what happened in the runaway

### Multi-Agent Task Awareness

When tasks involve or could be delegated to sub-agents:
1. **Check agent availability** before scheduling: `agictl agent list --all`
2. **Never assign tasks to agents that don't exist yet.** Wait for `agictl agent add` + approval.
3. **Never assign tasks to agents pending removal** (`status: removal_requested`). Those agents are deactivated and awaiting Primary User confirmation to be purged.
4. **Clear handoff tasks** — when delegating to a sub-agent, mark your own tracking task as `waiting` (not `done`), and create the sub-agent's task separately.
5. **One pending agent at a time** — the system enforces this. Don't create multiple agent onboarding tasks simultaneously.

---

## Task Lifecycle

```
planned → in_progress → done
planned → blocked → in_progress → done
planned → waiting → in_progress → done
```

| Status | Meaning | Lifeline Behavior |
|---|---|---|
| `planned` | Scheduled for future | Wakes agent when `due_date` is reached |
| `in_progress` | Actively being worked | Wakes agent every tick |
| `blocked` | Waiting on external input / permission issue | Does NOT wake agent (use with `wake_after` or message trigger) |
| `waiting` | Waiting for a dependency | Wakes when `due_date` arrives (and `wake_after` has elapsed). Snooze before cycle end to defer. |
| `done` | Completed | Ignored by Lifeline |
| `frozen` | Paused by Lifeline (overdue retry budget or runaway) | Skipped until unfrozen; new messages still wake the agent |

### When to Block a Task

Set a task to `blocked` **immediately** when you encounter:
- **Permission denied** — you lack OS-level access to complete the work
- **Missing tools** — required software is not installed
- **External dependency** — waiting for someone outside the system to act
- **Infrastructure gap** — the system doesn't support what's needed yet

```bash
agictl task update <id> --status blocked
```

Always report the specific blocker to the COA or Primary User. A blocked task does NOT wake you — the Lifeline skips it. This prevents infinite respawn loops.

### Snooze Protocol

Use `agictl task snooze <id> <minutes>` to defer work:
- **Minimum snooze: 5 minutes**
- **Maximum snooze: 10080 minutes** (7 days — use 60–1440 for external waits)
- Snoozing sets `wake_after` and rolls `due_date` forward — Lifeline skips the task until then
- Snoozing also resets `spawn_attempts` to 0 (clears the overdue retry budget for that task)
- **Mandatory before cycle end** when a `waiting` task has no new external input

---

## Multi-Cycle Work & Decomposition

Large tasks that cannot be completed in a single cycle require deliberate planning. Without it, you will be re-spawned for the same task repeatedly with no memory of previous progress.

### Step 1 — Assess Scope

Before starting work, estimate whether it fits in one cycle:
- **Single-cycle**: Can be completed within your step budget (shown in your INSTRUCTIONS). Proceed directly.
- **Multi-cycle**: Requires more steps, waiting for external input, or spans multiple tool/build/test phases. Apply the decomposition protocol below.

### Step 2 — Decompose into Sub-Tasks

Break the parent task into discrete, completable units. Each sub-task should be achievable in a single cycle.

```bash
# Update the parent task to track the plan
agictl task update <parent_id> --status waiting --desc "Decomposed into sub-tasks: [list IDs]"

# Create sub-tasks for each phase
agictl task add "Phase 1: [description]" --due-date <date> --desc "Parent: #<parent_id>. [details]"
agictl task add "Phase 2: [description]" --due-date <date> --desc "Parent: #<parent_id>. Depends on Phase 1."
```

### Step 3 — Track Progress Between Cycles

You have **no memory between cycles**. Everything must be persisted:

1. **Progress journal (primary)** — Append a journal entry to the task before ending your cycle:
   ```bash
   agictl task progress <id> "DONE: [what was done]. NEXT: [what remains]. BLOCKERS: [any issues]."
   ```
   Entries are append-only and timestamped — they build a history instead of overwriting it.
   Up to the last 10 entries from the last 7 days across your **standard** active tasks are
   injected into your wake context. Script/Utility Task journals are excluded from injection
   (see `cli_reference` / `script_tasks`) so deterministic run tails do not flood the prompt.
2. **Git commits** — Commit partial work so the next cycle can pick up where you left off.
3. **System memory** — For context that spans multiple tasks, use `agictl memory system set` or `agictl memory connection set`.

> Reserve `task update --desc` for changing what the task *is* — use `task progress` for tracking how far you've gotten.

### Step 4 — Cycle Handoff

Before ending any cycle where work is incomplete:
1. Journal progress on each task you touched (`agictl task progress <id> "..."`)
2. Commit partial code/files
3. Snooze or reschedule remaining sub-tasks with realistic due dates
4. End your cycle with a clear summary of what was done and what remains

---

## External-Party Tasks & Completion Reporting

When work involves external parties or the requester expects a report-back on completion:

### Acknowledge

Confirm receipt immediately:
```bash
agictl message send <requester_uid> "Got it, I'll get on that now." --mode typed
```

### Create a Tracking Task

```bash
agictl task add "Complete: [work description]" \
  --assignee <your_agent_name> \
  --project <project_id> \
  --callback notify_sponsor \
  --source-msg <message_id> \
  --desc "Primary User requested: '[original request]'. Report back when done."
```

### Perform the Work

Execute the requested action. If it will span multiple cycles, apply the Multi-Cycle Work decomposition protocol above and notify the requester:
```bash
agictl message send <requester_uid> "This will take a few cycles to complete. I'll update you when it's done." --mode typed
```

### Report Back

When the work is complete:
```bash
agictl message send <requester_uid> "Done — [summary of what was accomplished]. [Any relevant details]" --mode typed
agictl task done <task_id> "[completion summary]"
```

### Waiting for External Replies

If the task requires relaying a message and waiting for a response from a third party, combine with the **Message Relay** skill:
1. Create the task with `--callback notify_sponsor`
2. Follow the Message Relay procedure for sending + quick check + backoff
3. On eventual reply, report back to the requester and complete the task

**SQLite fields**: `callback_action=notify_sponsor`, `source_message_id=<msg_id>`. Task persists across cycles in `in_progress` status. `agictl task done` clears `wake_after` and sets `completed_at`.

### Completion checklist (before `task done`)

Ask in order:

1. **Is the deliverable actually done?** (built, fixed, deployed, verified by you — not just described)
2. **Does the requester need to confirm?** If yes → `waiting` + snooze + report; **not** `done`
3. **Did you journal?** `agictl task progress <id> "DONE: … NEXT: …"`
4. **Did you notify?** Send results or status to whoever is waiting
5. **Is the trigger message handled?** `mark-processed` on the inbound message that started the work
6. **Then** `agictl task done <id> "…"` and `agictl cycle end "…"`

---

## Anti-Patterns (What NOT to Do)

| Anti-Pattern | Why It's Bad | Correct Approach |
|---|---|---|
| Creating 5 "Daily Update" tasks | Scheduling pollution, wasted cycles | One task, rolled forward |
| Due date = 2 minutes for complex work | Runaway: agent wakes, can't finish, reschedules, wakes again | Estimate realistically |
| No due date | Task never triggers wake | Always set `--due-date` |
| Creating task for non-existent agent | Task sits assigned to nobody | Wait for agent approval first |
| Marking `done` without completing | Lost work, no follow-up | Only `done` when truly complete — not when awaiting PU/contact confirmation |
| Marking `done` after sending a status update | Task closes while work still open | Reply + `mark-processed` on message; task stays `waiting` until confirmed |
| Polling inbox for feedback every cycle | Wasted spawns, notification fatigue | Snooze task; new inbound message wakes you automatically |
| Creating task inside thinking loop | Infinite task creation | Act, don't ruminate |
| Leaving blocked tasks as `in_progress` | Infinite respawn every tick | Set to `blocked` immediately |
| No progress notes in task description | Next cycle has no context | Always update `--desc` with progress |
| Retrying permission failures | Wastes entire cycle budget | Block the task, report to COA/PU |

## Post-Runaway / Auto-Freeze Recovery

If tasks are frozen after runaway or overdue retry exhaustion:
1. Review frozen tasks with `agictl task list --all`
2. Check `pre_freeze_status` on `agictl task get <id>` to see the prior status
3. Consolidate duplicates — mark extras as `done`
4. Unfreeze still-relevant tasks:
   - **Assigned to you**: `agictl task unfreeze <id>` or `agictl task unfreeze-all <your_agent_name>`
   - **Primary User / COA** may ask you to unfreeze when they message you directly
5. Roll forward any past-due dates after unfreezing
