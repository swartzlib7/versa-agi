# IDE session seed

Standing rules (handshake, how you operate, close-out, Cursor loop) live in **`AGENTS.md`** at the workspace root. This file is the **seed**: point-in-time poise and live state. The LIVE SITUATION block below is superseded by the refresh in this file.

## Refresh live state

Do not trust the seed snapshot. After `agictl agent ide status coa` (see `AGENTS.md`), run these exactly as written:

```bash
agictl message count-unprocessed {MESSAGE_ACCOUNT} 0 --agent-name {AGENT_SHORT_NAME}
agictl task list
agictl task list --all
agictl awareness table --status active --limit 10
```

If the count is above zero, read them with `agictl message get` — `cat .agent/skills/cli_reference.md` for its arguments.

### Reading the two task lists

`agictl task list` shows only what is **actionable right now**: `in_progress`, plus
`planned` / `waiting` / `blocked` whose `due_date` has already passed. A task that
is open but scheduled for later does not appear, and neither does a `frozen` one.

**An empty `agictl task list` does not mean there is nothing to do.** Under
normal Lifeline spawning that emptiness is the schedule working. In this mode the Primary User is
present and may want to pull work forward, so always run `--all` too and tell them
what is actually on the board — how many are open, and when the next one comes due.
Never report "no tasks" on the strength of the first command alone.

Do not start a future-dated task on your own initiative. Surface it and let the
Primary User choose.

Do not invent flags. `agictl task list` takes `--all` and nothing else; there is
no `--status`. If a command is rejected, run `agictl <group> <verb> --help` or
`cat .agent/skills/cli_reference.md` rather than guessing another flag.
