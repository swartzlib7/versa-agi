# Agent Management

> **Purpose**: Register, list, approve, and remove agents in the Versa AGi system.
> This skill is for COA primarily, but any agent with agictl access can query the registry.

## Constraints

- **Free tier limit**: Max 3 active agents (COA + 2)
- **Protected agents**: `coa` and `watchdog` can NEVER be removed or deactivated. The system will auto-reactivate them if they are found inactive.
- **Approval flow**: New agents register as `inactive` (pending). Only the Primary User can approve via the dashboard.
- **Removal flow**: Agent removal follows the same approval pattern — request removal, then the Primary User confirms via the dashboard.

## CLI Reference

```bash
# List all registered agents
agictl agent list

# List active agents only (used by Lifeline)
agictl agent list --all

# Register a new agent (starts as inactive/pending)
agictl agent add <name> --os-user <user> --role "Role Label" [--model MODEL]

# Deactivate an agent (cannot deactivate coa or watchdog)
agictl agent deactivate <name>

# Request removal of an agent (COA-level — no root needed)
agictl agent request-remove <name> --reason "Reason for removal"

# Cancel a pending removal request (reactivates the agent)
agictl agent cancel-remove <name>

# Remove an agent directly (delegates based on privilege)
# Non-root: same as request-remove
# Root: same as confirm-remove (full cleanup)
agictl agent remove <name>
```

### Root-Level Commands (Dashboard Only)
These are hidden commands invoked by the agitop dashboard — NOT by agents directly:
```bash
# Approve & provision a pending agent (creates OS user, scaffolds workspace)
agictl agent approve <name>

# Confirm removal of an agent (archive + VV cleanup + purge + userdel)
agictl agent confirm-remove <name>
```

## Procedures

### Registering a New Agent

1. **Announce intent** to the Primary User: explain what the agent will do.
2. **Register**: `agictl agent add researcher --os-user researcher --role "Subject Researcher"`
3. The agent starts with `inactive=1` (pending approval).
4. **Notify the Primary User** — they will approve via the agitop dashboard.
5. Once approved, the agent appears in the registry as active and the Lifeline will begin spawning it.

### Requesting Agent Removal

1. **Assess the need**: confirm the agent is no longer required.
2. **Request removal**: `agictl agent request-remove <name> --reason "No longer needed"`
3. The agent is immediately deactivated (`inactive=1`, `status=removal_requested`).
4. The Primary User is notified via VersaVoice and will confirm via the agitop dashboard.
5. On confirmation, the system archives the workspace, deletes the VV sub-account, purges data, and removes the OS user.

### Handling Inactive Agent Tasks

When the Lifeline detects pending tasks for an inactive agent, it injects a context alert. When you see this:

1. **Inform the Primary User** about the situation.
2. **Present options**:
   - If agent is pending approval: wait for dashboard approval
   - If agent is pending removal: reassign or cancel the tasks
   - Reassign the task(s) to an active agent
   - Cancel the task(s): `agictl task done <task_id> "Cancelled — agent inactive"`
3. **Wait** for the Primary User's direction before acting.

### Follow-up Pattern

> **IMPORTANT**: When creating follow-up tasks related to agent management, include: `IMPORTANT: Re-read agent_management.md before processing this task.`
