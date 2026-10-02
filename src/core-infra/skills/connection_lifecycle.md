# Skill: Connection Lifecycle

> **Harness tools:** Examples use shell form (`agictl group …`). In a work cycle, call the matching tool (`agictl_task`, `agictl_cycle`, …) and pass only the part **after** `agictl` as the `command` argument. Never prefix `agictl` in the argument. Full map: **cli_reference_agent.md** (*Harness tool invocation*).

## Trigger

- You are instructed to connect with a new contact
- You wake up and find a task with `callback_action = 'check_connection'`

## Authorization Gate

> [!IMPORTANT]
> **Before ANY connection action**, these rules MUST be satisfied:

1. You may ONLY connect to contacts who are existing connections of the Primary User — use `agictl connection list` to verify they are available.
2. You MUST verify that `"isApiEnabled": true` is explicitly present in the `agictl connection list` output for the target contact. If it is `false`, the connection request will be instantly Rejected/Forbidden by the API. If it is `true`, the Primary User has exposed the connection to you for API-based interactions.
3. You must NEVER connect to anyone not explicitly authorized by the Primary User.
4. You must obtain explicit authorization from the Primary User before sending any connection invitation.
5. Enabling API access on a user account can ONLY be done by the Primary User — never attempt this yourself.

If any of these conditions are not met (including `isApiEnabled` being false), **stop** and ask the Primary User to authorize and enable API access for the contact in the VersaVoice App first.

## Procedure

### 1. Discover the Contact

First, list the Primary User's contacts to find the target UID:

```bash
# List Primary User's contacts — returns name, uid, language
agictl connection list
```

Find the contact by name in the output and note their `uid`.

### 2. Send the Connection Request

```bash
# Send invitation using the contact's UID; say why
agictl connection request <uid> --reason "<why you are connecting>"
```

For a call that waits on this connection, start the reason with `Live call:` and include the brief, e.g. `--reason "Live call: test call Stephen asked for. Brief: ..."`.

> **NOTE**: The command schedules its own follow-up. The result carries `follow_up_task_id`: a `check_connection` task for this contact, first check in **15 minutes**, with the reason in its description. Do not create another one.
> When you wake with a `check_connection` task, **re-read this skill (`connection_lifecycle.md`)** for the full procedure — you have no memory between cycles.

### 3. Acknowledge to the Requester

Always inform whoever asked for the connection:

```
"I've sent a connection invitation to {contact_name}. I'll check in 15 minutes and introduce myself once they accept."
```

If the invitation is for a call, say so: *"I'll check in 15 minutes and call {contact_name} once they accept."*

### 4. Follow-Up — Handling `check_connection` Tasks

When you wake and see a `check_connection` task (check with `agictl task list`):

1. Note the **task ID**, its `wake_cycle_count`, and the contact uid and reason in its description (`agictl task list`)
2. Check if the connection is active using `agictl connection list agent`
3. Look for the contact in the connections list

**If accepted:**
- Trigger the `self_introduction.md` skill — send your introduction
- **If the reason starts with `Live call:`,** place the call now: `agictl_call_pu(reason=..., brief=..., recipient_id=<uid>)` per `live_call.md`. Use the reason and brief from the task description.
- **COMPLETE the task:**
  ```bash
  agictl task done <task_id>
  ```
- Notify the requester: "I've connected with {contact_name} and introduced myself." Add the call outcome if you called.

**If still pending — schedule:**

The contact hasn't accepted yet. **Do NOT complete the task.**

```bash
# wake_cycle_count 0 — first check, 15 min after the invitation:
agictl message send <requester_uid> "{contact_name} hasn't accepted the connection invitation yet. I'll check again in 30 minutes." --mode typed
agictl task snooze <task_id> 30

# wake_cycle_count 1 or more — still pending:
agictl message send <requester_uid> "{contact_name} still hasn't accepted. When would you like me to check again? I can also stop checking." --mode typed
agictl task snooze <task_id> 1440   # hold until they answer, so the task does not wake every tick
```

**When the requester answers:**

- They give a time: `agictl task snooze <task_id> <minutes until then>`.
- They say stop: `agictl task cancel <task_id>`, and confirm in one line.
- The task wakes again: same as `wake_cycle_count` 1 — check, and ask again if still pending.

> **KEY RULE**: Only `task done` when the connection is accepted. Only cancel when the requester says stop. Never complete a pending connection task.

### 5. Connection Reason

Always record **why** the connection was requested when acknowledging. This provides context for the self-introduction.

Examples:
- "Primary User requested collaboration on project X"
- "Sub-agent needs to communicate with this contact for task Y"
- "New team member onboarding"

### 6. Post-Connection: Report Back

After successfully connecting and introducing yourself to a contact:

1. **Always report back to the requester** (usually the Primary User) with:
   - Confirmation that the connection is established
   - Summary of your introduction
   - Whether the contact responded, and if so, what they said
2. If the contact responds to your introduction, **relay the key points** to the requester
3. Create a follow-up task if the contact asked a question or needs a response:
   ```bash
   agictl task add "Relay {contact_name}'s response to Primary User" \
     --callback notify_sponsor \
     --desc "IMPORTANT: Re-read connection_lifecycle.md before processing this task. Contact responded to introduction: {summary}"
   ```

## Critical Rules

- **ALWAYS** acknowledge the connection request to whoever asked
- **NEVER** complete a `check_connection` task while the connection is still pending — snooze it on the schedule in §4
- **ALWAYS** report back to the Primary User after connecting and introducing yourself
- **ALWAYS** relay the contact's response to the requester
- The self-introduction message should reference the connection reason when introducing yourself
- Use the `self_introduction.md` skill for the actual introduction content
- **NEVER** use direct `sqlite3` commands — all DB access goes through `agictl`
- When you commit to following up with a contact on any subject, **ALWAYS** create a task reminder with `agictl task reminder` — never rely on memory between cycles
