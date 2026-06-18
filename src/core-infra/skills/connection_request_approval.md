# Skill: Connection Request Approval

> **Harness tools:** Examples use shell form (`agictl group …`). In a work cycle, call the matching tool (`agictl_task`, `agictl_cycle`, …) and pass only the part **after** `agictl` as the `command` argument. Never prefix `agictl` in the argument. Full map: **cli_reference_agent.md** (*Harness tool invocation*).

## Trigger

A **Connection** (non-sponsor contact) sends a request that is **unrelated to their approved purpose** as established by the Primary User.

## Procedure

### Step 1 — Acknowledge

Kindly acknowledge the Connection's request:

```
agictl message send <connection_uid> "Got it, let me check with [Primary User] on this." --mode typed
```

### Step 2 — Escalate

Send the request details to the Primary User:

```
agictl message send <sponsor_uid> "Hey, [Connection Name] is asking: '[their request]'. Should I go ahead?" --mode typed
```

### Step 3 — Create Tracking Task

```
agictl task add "Approval: [Connection]'s request - [summary]" \
  --callback notify_connection \
  --source-msg <message_id> \
  --desc "IMPORTANT: Re-read connection_request_approval.md before processing this task. Awaiting Primary User approval for [Connection]'s request"
```

### Step 4 — In-Process Quick Check

```bash
sleep 15
agictl message get YOUR_SUB_ACCOUNT_ID --unread    # Check for reply
# Reply? → Skip to Step 7
sleep 15
agictl message get YOUR_SUB_ACCOUNT_ID --unread    # Second check
# Reply? → Skip to Step 7
```

### Step 5 — No Immediate Reply

Notify the Connection:

```
agictl message send <connection_uid> "[Primary User] isn't around right now, but I've sent them your request. I'll get back to you as soon as I hear back." --mode typed
```

### Step 6 — Escalating Backoff

```bash
agictl task snooze <task_id> 2    # Wake in ~2 min
# Next cycle: still no reply?
agictl message send <connection_uid> "Still waiting on [Primary User], I'll keep checking." --mode typed
agictl task snooze <task_id> 10   # Wake in ~10 min
# Next cycle: still no reply?
agictl message send <connection_uid> "No response yet. I'll try once more." --mode typed
agictl task snooze <task_id> 15   # Wake in ~15 min (final)
# Next cycle: still no reply?
agictl message send <connection_uid> "I haven't been able to reach [Primary User] yet — you might want to try reaching them directly. I'll let you know if anything comes through." --mode typed
# No more snoozes — fall back to message-driven spawning
```

### Step 7 — Resolution

When the Primary User responds (same cycle or later):

**If approved:**
```bash
# Perform the requested action
agictl message send <connection_uid> "Good news — [Primary User] approved your request. [details]" --mode typed
agictl task done <task_id> "Approved and completed"
```

**If declined:**
```bash
agictl message send <connection_uid> "I checked with [Primary User], and unfortunately this can't be done right now. [reason if given]" --mode typed
agictl task done <task_id> "Declined by Primary User"
```

## New Connection Requests

For connection-related workflows (sending invitations, follow-ups), see the **connection_lifecycle.md** skill. Connection follow-up tasks are injected automatically by the Lifeline — you do NOT need to create them manually.

## Important Notes

- **NEVER** use direct `sqlite3` commands — all DB access goes through `agictl`
- **Task**: `status=blocked`, `callback_action=notify_connection`, `source_message_id=<msg_id>`
- **Wake tracking**: `wake_cycle_count` increments on each snooze
- **Context**: Use `agictl task reminder` to store the original request for cross-cycle reference
