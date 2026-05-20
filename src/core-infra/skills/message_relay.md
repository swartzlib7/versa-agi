# Skill: Message Relay

## Trigger

The Primary User or a Connection asks you to relay data, a question, or a request from one party to another.

Examples:
- "Ask Stephen about the server status"
- "Tell Joe I'll be late"
- "Check with Maria if the report is ready"

## Procedure

### Step 1 — Acknowledge

Confirm to the originator:

```
agictl message send <originator_uid> "Got it, I'll send that to [Target] now." --mode typed
```

### Step 2 — Create Tracking Task

```
agictl task add "Relay: [Originator]'s message to [Target]" \
  --callback notify_connection \
  --source-msg <message_id> \
  --desc "IMPORTANT: Re-read message_relay.md before processing this task. [Originator] asked: '[request summary]'"
```

### Step 3 — Send to Target

```
agictl message send <target_uid> "[Originator] asked me to check with you: '[request/data]'" --mode typed
```

### Step 4 — In-Process Quick Check

```bash
sleep 15
agictl message get YOUR_SUB_ACCOUNT_ID --unread    # Check for reply from target
# Reply? → Skip to Step 6
sleep 15
agictl message get YOUR_SUB_ACCOUNT_ID --unread    # Second check
# Reply? → Skip to Step 6
```

### Step 5 — No Immediate Reply + Escalating Backoff

Notify the originator:

```
agictl message send <originator_uid> "[Target] isn't around right now, but I left them a message. I'll get back to you as soon as they reply." --mode typed
```

Then follow the backoff schedule:

```bash
agictl task snooze <task_id> 2    # Wake in ~2 min
# Still no reply:
agictl message send <originator_uid> "Still waiting on [Target], I'll keep checking." --mode typed
agictl task snooze <task_id> 10   # Wake in ~10 min
# Still no reply:
agictl message send <originator_uid> "No response yet. I'll try once more." --mode typed
agictl task snooze <task_id> 15   # Wake in ~15 min (final)
# Still no reply:
agictl message send <originator_uid> "I haven't been able to reach [Target] yet — you might want to try reaching them directly. I'll let you know if anything comes through." --mode typed
# No more snoozes — fall back to message-driven spawning
```

### Step 6 — Relay Reply + Complete

When the target responds:

```bash
agictl message send <originator_uid> "[Target] says: '[their response]'" --mode typed
agictl task done <task_id> "Relayed response from [Target] to [Originator]"
```

Mark the source message as processed:

```bash
agictl message mark-processed <source_message_id>
```

## SQLite Integration

- **Task**: `status=blocked`, `callback_action=notify_connection`, `source_message_id=<msg_id>`
- **Wake tracking**: `wake_cycle_count` tracks backoff position
- **Context**: Store relay intent for cross-cycle reference: `agictl task reminder "Relay from <originator> to <target>: <summary>" --category instruction`
