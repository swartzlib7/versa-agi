# agictl — Command Reference

agictl is the **only authorized interface** to your data layer. Never use `sqlite3` directly or read config files. If you need data — use agictl.

---

## 1. system

```bash
agictl system config get                              # All config as JSON
agictl system config get identity.first_name          # Read specific key (dot-notation)
agictl system config set <key> <value>                # Write a config value
agictl system whoami                                  # Your identity (name, role, language, sub_account_id, sponsor)
```

## 2. agent

```bash
agictl agent list                                     # Active agents as JSON
agictl agent list --all                               # Include inactive agents
agictl agent show <name>                              # Full details for one agent
agictl agent add <name> --role ROLE                   # Register new sub-agent (pending approval)
agictl agent list-roles                               # Available role templates
agictl agent activate <name>                          # Activate + clear circuit_breaker + unfreeze tasks
agictl agent deactivate <name>                        # Set inactive
agictl agent request-remove <name> [--reason TEXT]    # Flag for removal (deactivates, notifies PU)
agictl agent cancel-remove <name>                     # Cancel pending removal
agictl agent status show                              # Read your current status
agictl agent status set <state> ["summary"]           # Write status + message
agictl agent count [--active]                         # Count agents
agictl agent summary                                  # Markdown table of all agents
agictl agent toggle-comms <name>                      # Toggle external comms (PU only)
```

**Status values**: `idle`, `active`, `working`, `error`, `awaiting_activation`, `removal_requested`, `circuit_breaker`

**Sub-agent provisioning**:
1. `agictl agent list --all` → check for existing/pending
2. `agictl agent list-roles` → pick role
3. `agictl agent add <name> --role <id>` → creates DB record (inactive, pending)
4. Notify Primary User → they approve via **agitop** dashboard

> **GUARD**: One pending agent at a time. `agent add` blocks if any inactive agent is pending approval.

> **CRITICAL**: Sub-agents live at `/home/agi-{name}/` — never create sub-agent data under `workspace/`.

## 3. task

```bash
agictl task list                                      # Active + due blocked tasks
agictl task list --all                                # ALL tasks including done/cancelled
agictl task list --mine                               # Tasks assigned to you
agictl task get <id>                                  # Full task details
agictl task add "Title" [options]                     # Create task (returns JSON with task_id)
agictl task update <id> [options]                     # Update specific fields
agictl task done <id>                                 # Shortcut: mark as done
agictl task cancel <id>                               # Shortcut: mark as cancelled
agictl task snooze <id> <minutes>                     # Set wake_after (min 5 minutes)
agictl task reminder "<text>" [--category CAT]        # Create a reminder task
```

**task add options**: `--desc`, `--priority low|normal|high|urgent`, `--assignee`, `--project <id>`, `--callback notify_sponsor|notify_connection|await_reply|check_connection|none`, `--source-msg <id>`, `--requested-by <uid>`, `--due-date "YYYY-MM-DD HH:MM:SS"`

**task update options**: `--status planned|in_progress|waiting|blocked|cancelled|done`, `--desc`, `--priority`, `--assignee`, `--due-date "YYYY-MM-DD HH:MM:SS"`, `--requested-by`

> `--due-date` is **mandatory** when creating tasks and when setting status back to `planned`. If a task cannot be completed by its due date, **roll the due date forward** — do not leave it in the past.

**Reminder categories**: `general`, `preference`, `instruction`, `constraint`

## 4. message

```bash
agictl message get <sub_account_uid> --unread                # Unprocessed inbound messages
agictl message get <sub_account_uid> --contact <contact_uid> # Filter by sender/recipient
agictl message get <sub_account_uid> --last-n-count 10       # Last N messages
agictl message get <sub_account_uid> --last-n-minutes 30     # Messages from last 30 min
agictl message send <contact_uid> "<text>" --mode MODE       # Send via VersaVoice
agictl message internal <agent_name> "<text>"                # Direct message to another agent (SQLite, no VV API)
agictl message mark-processed <message_id>                   # Mark message as handled
agictl message conversation-context <sub_account_uid> [sponsor_uid]  # Build context blob
```

**Modes**: `typed` (text as-is), `translate` (AI translation), `speak` (TTS same-language), `speak_translated` (TTS + translation)

**Attachment flags** (repeatable, max 10 total):

| Flag | Accepts | Behavior |
|---|---|---|
| `--media` | File path | Uploads file, attaches download URL |
| `--markdown` | File path | Reads `.md` content inline as text |
| `--url` | URL string | Passes URL directly |

```bash
# Example: send with media, markdown, and URL attachments
agictl message send <uid> "Here are the reports" --mode typed \
  --media /path/to/chart.png \
  --markdown /path/to/report.md \
  --url https://docs.example.com/api
```

**Inbound attachments**: Auto-downloaded to `.agent/attachments/{message-id}/{media|markdown|urls}/`
> This directory is READ-ONLY. To save processed data, write to `workspace/` instead.

## 5. cycle

```bash
agictl cycle end "Summary" [--agent NAME]             # End cycle with summary
agictl cycle recent <agent>                           # Recent cycle summaries for context
```

## 6. project

```bash
agictl project list                                   # All projects as JSON
agictl project add <name> [--desc TEXT] [--remote URL] [--git-init]  # Register project
agictl project pause <name>                           # Pause project
agictl project resume <name>                          # Resume project
agictl project archive <name> [--zip]                 # Archive project
agictl project git-setup                              # Configure git identity + SSH
```

## 7. connection

```bash
agictl connection list                                # Primary User's contacts
agictl connection list agent                          # Your established connections
agictl connection request <uid>                       # Send connection invitation
```

> **Flow**: `connection list` → discover UIDs → `connection request <uid>` → PU accepts in VersaVoice → contact appears in `connection list agent`.

> **GUARD**: Requires external comms enabled (`can_message_connections`). Sub-agents need PU to enable via `agictl agent toggle-comms <name>`.

## 8. memory

### Connection Memory
```bash
agictl memory connection get <contact_uid>
agictl memory connection set <contact_uid> [OPTIONS]  # UPSERT
agictl memory connection list
```
**Options**: `--preferences JSON`, `--personal-notes TEXT`, `--comm-style TEXT`, `--rapport new|building|established|strong`, `--emotional-notes TEXT`

### Project Memory
```bash
agictl memory project get <project_id>
agictl memory project set <project_id> [OPTIONS]      # UPSERT
agictl memory project list
```
**Options**: `--phase TEXT`, `--decisions TEXT`, `--blockers TEXT`, `--next-steps TEXT`

### System Memory
```bash
agictl memory system get [key]
agictl memory system set <key> <value>                # UPSERT
agictl memory system list
```

---

## Platform Limits

| Resource | Limit | Behavior |
|---|---|---|
| VV API rate | 60 req/min | Sleeps when approaching limit |
| Message text | 2048 chars | Hard block at send |
| Attachments | 10 per message, 50MB per file | Hard block |
| Concurrent spawns | 3 per tick | Excess agents queued for next tick |
| Circuit Breaker | 5 consecutive / 20 per hour | Auto-freezes agent, sends notification |

## Rules

- **ALWAYS** use agictl — never read config files or databases directly
- **NEVER** guess command syntax — consult this reference
- All effectful commands return JSON: `{"success": true/false, ...}`
- Commands marked **(STUB)** return confirmation but are not fully wired yet
