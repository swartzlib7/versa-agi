# Skill: Reminder Management

> **Harness tools:** Examples use shell form (`agictl group …`). In a work cycle, call the matching tool (`agictl_task`, `agictl_cycle`, …) and pass only the part **after** `agictl` as the `command` argument. Never prefix `agictl` in the argument. Full map: **cli_reference_agent.md** (*Harness tool invocation*).

## Trigger

- A contact (including the Primary User) asks you to remind them of something
- A contact asks to be notified at a specific time or after a delay
- Keywords: "remind me", "in X minutes", "later", "don't let me forget", "set a reminder"

## Procedure

### 1. Parse the Request

Extract two things from the message:
- **What** to remember (the content)
- **When** to trigger (the timing)

Common time patterns and their snooze duration:

| Natural language | Snooze minutes |
|---|---|
| "in 5 minutes" | `5` |
| "in 10 minutes" | `10` |
| "in half an hour" | `30` |
| "in 1 hour" | `60` |
| "in 2 hours" | `120` |
| "tomorrow" | `1440` |
| "in a few minutes" | `5` |
| No time given | omit snooze (persistent reminder) |

### 2. Create the Reminder

```bash
# Timed reminder (has a due time) — create task + snooze
agictl task reminder "Remind Stephen to go to bed" --category general
agictl task snooze <task_id> 10

# Persistent reminder (no time — stays active until marked done)
agictl task reminder "Stephen prefers typed messages for casual chat" --category preference
```

### 3. Acknowledge Immediately

**Always** respond to the sender confirming the reminder was set:

```
"Done — I've set a reminder for you in 10 minutes to go to bed. I'll message you when it's time."
```

Or for persistent reminders:
```
"Noted — I'll keep that in mind."
```

### 4. When a Timed Reminder Comes Due

When you wake up and see a reminder task that is past its `wake_after` time:

1. Send a message to the contact who requested it:
   ```
   "Hey Stephen — this is your reminder to go to bed. 🛏️"
   ```
2. Mark the task as done:
   ```bash
   agictl task done <task_id>
   ```

### Categories

Use appropriate categories to organize reminders:

| Category | When to use |
|---|---|
| `general` | Default — one-time reminders |
| `preference` | User preferences that should persist ("prefers typed mode") |
| `instruction` | Standing instructions ("always check inbox first") |
| `constraint` | Limitations or restrictions to remember |

### Important

- **Always acknowledge** reminder requests — silence is never acceptable
- For timed reminders, be specific in your confirmation about when it will fire
- If you cannot determine the timing, ask: "When would you like to be reminded?"
- Use `agictl task list` to review all active tasks (including reminders) at the start of each cycle
