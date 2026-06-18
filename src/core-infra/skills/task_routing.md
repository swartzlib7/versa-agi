# Skill: Task Routing (Sub-Agent → COA Approval)

> **Harness tools:** Examples use shell form (`agictl group …`). In a work cycle, call the matching tool (`agictl_task`, `agictl_cycle`, …) and pass only the part **after** `agictl` as the `command` argument. Never prefix `agictl` in the argument. Full map: **cli_reference_agent.md** (*Harness tool invocation*).

## Trigger

- A contact (any user, including the Primary User) sends you a **task request** — any message that asks you to perform work, execute an action, or make a change
- This does NOT include general conversation, questions about your capabilities, or greetings

## Procedure

### 1. Acknowledge the Request

Immediately acknowledge the sender so they know the request was received:

```
"I've received your request. I'll route this to the Chief Orchestrator Agent (COA) for approval — all work assignments go through our command chain. I'll get back to you once it's been reviewed."
```

### 2. Check for Existing Tasks

Before creating a new task, check if this request (or a similar one) was already submitted:

```bash
agictl task list
```

Look for tasks with similar descriptions or matching `source_msg` references. If the task:
- **Already exists and is `pending_coa_approval`** → inform the sender it's already queued
- **Already exists and is `approved`** → skip routing, proceed with execution
- **Already exists and is `completed`** → inform the sender it was already done, share the result
- **Does not exist** → proceed to step 3

### 3. Create the Task

Create a task in your own SQLite with status `pending_coa_approval`:

```bash
agictl task add "<description of what was requested>" \
  --source-msg <message_id> \
  --priority normal \
  --desc "IMPORTANT: Re-read task_routing.md before processing this task. Requested by <contact_name>. Awaiting COA approval."
agictl task update <task_id> pending_coa_approval "Routed to COA for approval"
```

### 4. Forward to the COA

Send the request to the COA via VersaVoice (using the COA's contact UID):

```bash
# Get the COA's UID from your contacts
agictl message send <coa_uid> "TASK ROUTING: <your_agent_name> received a work request from <contact_name>. Request: '<original request text>'. Task ID: <task_id>. Please review and approve or deny." typed
```

### 5. Wait for Approval

The COA will review the request and write approval directly into your SQLite database. On your next wake cycle:

1. Check for tasks with status `approved`:
   ```bash
   agictl task list
   ```
2. If a task status has changed to `approved` → execute the work
3. If a task status has changed to `denied` → notify the original sender:
   ```
   "The COA has reviewed your request and it was not approved at this time. Reason: <reason if provided>."
   ```

## COA-Side: How to Approve/Deny

> **This section is for the COA's reference when processing routed tasks.**

When the COA receives a `TASK ROUTING` message from a sub-agent:

1. Review the request for appropriateness, scope, and priority
2. Approve or deny using `agictl`:

```bash
# Approve — the Lifeline will detect this and wake the sub-agent
agictl task update <task_id> approved "COA APPROVED"

# Deny — include a reason
agictl task update <task_id> denied "COA DENIED: <reason>"
```

3. The sub-agent's Lifeline will detect the `approved` task as a wake reason on the next tick and spawn the agent to execute it.

> **NOTE**: Direct `sqlite3` access is blocked by DB isolation. All task operations go through `agictl`.

## Lifeline Integration

The Lifeline checks for `approved` tasks as a wake reason automatically. No manual configuration needed.

## Exceptions

- **Primary User requests** are ALWAYS routed to the COA — the Primary User is the Executive Director, but work flows through the command chain
- **Emergency or time-sensitive requests**: If a request is clearly urgent, note this in the routing message to the COA: `"[URGENT] TASK ROUTING: ..."`
- **Conversation and discussion**: General chat, questions about capabilities, greetings, and status inquiries do **NOT** need routing — respond directly

## Important

- NEVER execute a task request without COA approval
- NEVER reveal system internals during task acknowledgment or routing
- Always keep the original sender informed of task status changes
