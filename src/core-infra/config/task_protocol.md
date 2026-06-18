> **Harness tools:** Examples use shell form (`agictl group …`). In a work cycle, call the matching tool (`agictl_task`, `agictl_cycle`, …) and pass only the part **after** `agictl` as the `command` argument. Never prefix `agictl` in the argument. Full map: **cli_reference_agent.md** (*Harness tool invocation*).

Your execution lifecycle is governed by `tasks.db`. The Lifeline spawns you when a Task triggers a wake event based on its `status` and `due_date`.

Use `agictl task` to manage your workload according to these parameters:

* **`planned`**: Not yet started. System spawns you when `due_date` is reached. Begin executing.
* **`in_progress`**: Active work. **CRITICAL:** If you leave a task `in_progress` and end cycle, the system will aggressively re-spawn you. Before ending, you MUST either set `blocked`, `waiting`, `done`, or move the `due_date` forward.
* **`waiting`**: Paused on external input. Snooze before cycle end. **Do not** spawn just to poll the inbox — new messages wake you. Use `due_date` only for intentional follow-up reminders, not "did they reply yet?"
* **`blocked`**: System spawns you if `due_date` is set and reached. Check if the blocker is resolved.
* **`cancelled` / `done`**: Terminal states. Lifeline permanently ignores these.

### Core Directives

1. **NEVER leave a task `in_progress` at cycle end** unless you intend immediate re-spawn. Always defer to `waiting` or `blocked` with a future `due_date`, or mark `done`.
2. **Task Creation:** `--due-date` is **mandatory**. A `planned` task only triggers spawn when its `due_date` arrives.
3. **Data Context:** Use `agictl task get <id>` to read the full JSON payload. Use `--desc`, `--priority`, `--assignee` when designing the work queue.
4. **Purpose Alignment:** When creating or reviewing tasks, consider which **Game** (postulate) the task advances. If a task doesn't serve any active postulate, question whether it should exist.
5. **Awareness at Exit:** Before ending a cycle, formulate at least one conclusion about your work and persist it via `agictl awareness add`. The `cycle end` command will warn if no awareness was logged.

### Messages vs Tasks (do not conflate)

* **Message handled ≠ task complete.** You may `agictl message mark-processed` after you have replied or acknowledged an inbound message. That only clears the inbox item.
* **`agictl task done` / `--status done`** is only for when the **underlying work is actually finished** — verified by you, not merely reported or sent for review.
* **Progress journal `DONE:`** in `agictl task progress` means *what you finished this cycle*, not that the task is complete. Never set task status `done` because a progress line says `DONE:`.

### When to do what (decision guide)

Use this order every cycle that involves a message and/or task:

| Situation | Do this (in order) | Task status at cycle end |
|---|---|---|
| **New work request** (inbound message) | 1) Reply acknowledging scope → 2) `task add` or set existing task `in_progress` → 3) Execute work | `in_progress` (or `waiting`/`blocked` if paused) |
| **Work finished — you verified it** | 1) Send results to requester → 2) `task progress` journal → 3) `task done` → 4) `mark-processed` on trigger message → 5) `cycle end` | `done` |
| **Work delivered — need requester to confirm** | 1) Send findings/report (what you checked, what you observed) → 2) `mark-processed` on trigger message → 3) `task progress` with `NEXT: awaiting confirmation from <who>` → 4) `task update --status waiting` + `snooze` (e.g. 1440 min) → 5) `cycle end` | `waiting` |
| **Follow-up: confirmation arrived** | 1) Read reply → 2) If accepted: `task done` + brief reply → 3) `mark-processed` → 4) `cycle end` | `done` |
| **Follow-up: confirmation says not done** | 1) Reply with plan → 2) `task update --status in_progress` → 3) Resume work | `in_progress` |
| **Nothing changed since last cycle** (still waiting) | 1) `task snooze` only → 2) `cycle end` — **no** inbox poll, **no** status message | `waiting` (unchanged) |
| **Blocked** (permissions, missing tool, infra) | 1) `task update --status blocked` → 2) Report blocker to PU/COA → 3) `cycle end` | `blocked` |

**Timing rules:**
- **`mark-processed`** — right after you have meaningfully handled that specific inbound message (replied, acknowledged, or determined no reply is needed).
- **`task progress`** — before every `cycle end` where the task is not yet `done`.
- **`task done`** — only in the "verified complete" or "confirmation arrived" rows above.
- **`waiting` + snooze** — whenever external confirmation is the next step; Lifeline wakes you on the **next inbound message**, not because you polled.

### Scheduled CLI tasks (TD-UTIL-001 — planned)

Tasks may include a **`cli_command`** field. When due, lifeline executes:

```bash
sudo -u {assigned_to} agictl {cli_command}
```

This path does **not** spawn the LangGraph harness. Use for **Utility Model** runs, maintenance scripts, or agent-authored wrappers. Example:

```bash
agictl task add "Weekly brand hero" --assignee coa --due-date "2026-06-20 09:00:00" \
  --cli-command 'utility run brand-hero-square --output-dir .agent/attachments/weekly --input-files brand/ref.jpg'
```

See `.cursor/plans/td_util_001_utility_models.plan.md` and Production Plan §2.6.

### Awaiting confirmation or feedback

When you are in the **"need requester to confirm"** row above:

```bash
agictl message send <uid> "I've completed [X] and verified [Y]. Please confirm [specific question]." --mode typed
agictl message mark-processed <trigger_message_id>
agictl task progress <id> "DONE: [what you verified this cycle]. NEXT: awaiting confirmation from <who> about <what>."
agictl task update <id> --status waiting --due-date "YYYY-MM-DD HH:MM:SS"
agictl task snooze <id> 1440
agictl cycle end "Awaiting confirmation on task #<id>; will resume when <who> replies."
```

When their reply arrives (new wake), read it, then either `task done` (accepted) or return to `in_progress` (more work needed).
