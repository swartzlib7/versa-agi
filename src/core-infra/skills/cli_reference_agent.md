# agictl — Command Reference (agent subset)

> **Spawn injection:** This file is **always injected** every cycle for all agents.
>
> **COA — full operator manual:** `cli_reference.md` covers model catalog, provider CRUD, and admin-only commands. It is **not** auto-injected (token budget). Load when needed:
> - tool **`agictl_execute`**, argument **`bash "cat ~/.agent/skills/cli_reference.md"`**

agictl is the **only authorized interface** to your data layer. Never use `sqlite3` directly or read config files. If you need data — use agictl.

## Harness tool invocation

During a work cycle you do **not** run shell commands. The LangGraph harness exposes **typed tools** — one per `agictl` command group. Each tool executes the real CLI for you.

| CLI group | Harness tool | `command` argument (omit leading `agictl`) |
|---|---|---|
| `system` | `agictl_system` | `system whoami` |
| `model` | `agictl_model` | `model list` |
| `utility` | `agictl_utility` | `utility run <um-id> --input-files a.jpg` |
| `agent` | `agictl_agent` | `agent list` |
| `task` | `agictl_task` | `task snooze 43 1440` |
| `message` | `agictl_message` | `message send UID "Hello" --mode typed` |
| `cycle` | `agictl_cycle` | `cycle end "Summary of work"` |
| `project` | `agictl_project` | `project list` |
| `connection` | `agictl_connection` | `connection list-primary-user` |
| `memory` | `agictl_memory` | `memory system list` |
| `game` | `agictl_game` | `game list` |
| `awareness` | `agictl_awareness` | `awareness add conclusion --subject self --content "..."` |
| `identity` | `agictl_identity` | `identity provision …` |
| `execute` / `bash` | `agictl_execute` | `bash "ls -la"` or `execute python "print(1)"` |
| `browser` | `agictl_browser` | `browser goto "https://…"` |
| `organization` | `agictl_organization` | `organization org list` (only when Organization feature is ON) |
| `search` | `agictl_search` | typed args: `query`, `count` (not a command string) |
| `view` | `agictl_view_image` | typed arg: `path` (not a command string) |

**Convention:** Examples in this reference use **shell notation** (`agictl group subcommand …`) so they match `--help` and operator docs. Translate when calling a tool:

| Shell (documentation) | Harness call |
|---|---|
| `agictl task list` | tool **`agictl_task`**, argument **`task list`** |
| `agictl cycle end "Done"` | tool **`agictl_cycle`**, argument **`cycle end "Done"`** |
| `agictl message get UID --unread` | tool **`agictl_message`**, argument **`message get UID --unread`** |

**Do not** prefix the `command` argument with `agictl` — the harness adds it. Passing `agictl cycle end …` runs `agictl agictl cycle end …` and fails.

**Cycle end:** call tool **`agictl_cycle`** alone in the final step (no parallel tool calls). Argument: `cycle end "your summary"`.

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
agictl task progress <id> "<text>"                    # Append a progress entry (append-only journal)
agictl task progress <id> [--last N]                  # No text: list journal (oldest first; default --last 20, no date filter)
agictl task done <id>                                 # Shortcut: mark as done
agictl task cancel <id>                               # Shortcut: mark as cancelled
agictl task snooze <id> <minutes>                     # Set wake_after (min 5 minutes)
agictl task unfreeze <id>                             # Resume a frozen task assigned to you
agictl task unfreeze-all <your_agent_name>              # Resume all your frozen tasks
agictl task reminder "<text>" [--category CAT]        # Create a reminder task
```

> **Frozen tasks**: Lifeline auto-freezes overdue tasks after the configured retry budget. If the Primary User asks you to resume, use `agictl task unfreeze <id>` (or `unfreeze-all` with your agent name). You can only unfreeze tasks assigned to you — COA and the Primary User can unfreeze any agent's tasks.

**task add options**: `--desc`, `--priority low|normal|high|urgent`, `--assignee`, `--project <id>`, `--callback notify_sponsor|notify_connection|await_reply|check_connection|none`, `--source-msg <id>`, `--requested-by <uid>`, `--due-date "YYYY-MM-DD HH:MM:SS"`

**task update options**: `--status planned|in_progress|waiting|blocked|cancelled|done`, `--desc`, `--priority`, `--assignee`, `--due-date "YYYY-MM-DD HH:MM:SS"`, `--requested-by`

> `--due-date` is **mandatory** when creating tasks and when setting status back to `planned`. If a task cannot be completed by its due date, **roll the due date forward** — do not leave it in the past.

> **SCRIPT TASKS**: For deterministic, scheduled `.sh` jobs (no reasoning cycle), add `--script-task --script-path <name.sh> [--script-parameters "<args>"] [--script-interval <seconds>]`. The script must be a top-level `.sh` in **AGi-Tools** (idempotent, exits non-zero on failure). Blank/0 interval = once-off; positive = recurring. Optional `--utility-start-alert` / `--utility-stop-alert` send PU alerts. See skill `script_tasks.md`.

> **TASK PROGRESS**: You have no memory between cycles. Before ending any cycle with unfinished work, leave a breadcrumb: `agictl task progress <id> "DONE: ... NEXT: ... BLOCKERS: ..."`. Entries are append-only and timestamped. Up to the last 10 entries from the last 7 days across your **standard** active tasks are injected into your wake context. Script/Utility Task journals are **not** injected (and Script/Utility rows older than 7 days are pruned on each deterministic run). Listing via `task progress <id>` returns whatever remains in the DB (`--last N`, no date filter). Use `task progress` for journaling; reserve `task update --desc` for changing the task's actual description.

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
agictl project pause <id>                             # Pause project
agictl project resume <id>                            # Resume project
agictl project archive <id> [--zip]                 # Archive project
agictl project git-setup                              # Configure git identity + SSH
```

> **Project IDs**: Resolve numeric IDs via `project list`. Only `project add` takes a name (for new registration).

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

> **MANDATORY**: Use the **`memory_management.md`** skill (always-injected) at the end of every cycle to execute the 5-step Awareness-First procedure: Reflect → Conclude → Act → Profile → Verify.

## 9. game

```bash
agictl game add "<name>" [--postulate TEXT] [--posture exploratory|steady|aggressive|defensive] [--autonomy advisory|collaborative|autonomous]
agictl game update <id> [--name TEXT] [--postulate TEXT] [--posture ...] [--autonomy ...] [--freedoms TEXT] [--barriers TEXT] [--milestones JSON] [--status active|paused|archived]
agictl game show <id>                                  # Full details + related projects + active awareness
agictl game list [--status active|paused|archived]     # All games
agictl game assign-project <game_id> <project_id>      # Link project to game
```

**Posture**: `exploratory`, `steady`, `aggressive`, `defensive`

### Opponents

```bash
agictl game opponent add <project_id> "<name>" [--type person|agent|business|association] [--desc TEXT] [--sources JSON]
agictl game opponent list [--project <id>]
agictl game opponent update <id> [--name TEXT] [--desc TEXT] [--sources JSON] [--assessment TEXT]
agictl game opponent delete <id>
```

## 10. awareness

```bash
agictl awareness add conclusion --subject <type> [--subject-id ID] --content "<text>" [--context "<why>"]
agictl awareness add action --subject <type> [--subject-id ID] --content "<text>" --action-conclusion-id <id> [--context "<why>"]
agictl awareness revise <entry_id> --content "<updated text>"
agictl awareness supersede <entry_id>
agictl awareness complete <entry_id>
agictl awareness list [--type conclusion|action] [--subject <type>] [--subject-id ID] [--status active|revised|superseded|completed]
agictl awareness get <entry_id>
```

**Subject types**: `connection`, `project`, `game`, `system`, `self`

> **Enforcement**: `cycle end` warns if no awareness was logged this session.

---

## 11. search — Web Search

```bash
agictl search web "<query>"                           # Search the web via local SearXNG
agictl search web "<query>" --count 10                # Return up to 10 results (default: 5)
agictl search web "<query>" --categories science      # Filter by search category (default: general)
```

> **Availability**: Search requires SearXNG to be installed and `setup.ini [search] enabled=true`. If search is disabled, the harness tool `agictl_search` will not be available.

**Returns JSON**: `{success: true, query: "...", results: [{title, url, snippet, engine}], count: N}`

Use for: technical research, version compatibility checks, documentation lookups, competitive intelligence.

## 12. view — Image perception

**Harness tool:** `agictl_view_image(path="...")` — preferred in-cycle (validates, injects image into context).

**CLI (standalone validation):**

```bash
agictl view image <path>                              # Validate path; return JSON metadata
agictl view image workspace/screenshots/page.png      # Relative to agent workspace
agictl view image /any/local/path.png                 # Absolute path anywhere on disk
```

**Requirement:** execution model catalog `input_modalities` must include `image`. Refused when fewer than 8 steps remain.

**Cross-spawn fallback:** if the execution model lacks vision: `agictl agent set-model` (COA/admin), journal progress, snooze or create a self-assigned task due now, then `agictl cycle end`. Next Lifeline tick respawns on the vision model.

## 13. browser — Headless Browser

```bash
agictl browser goto "<url>"                           # Load page, return text content
agictl browser goto "<url>" --screenshot              # Load page + save screenshot
agictl browser click "<url>" "<selector>"             # Navigate then click an element
agictl browser fill "<url>" "<selector>" "<value>"    # Navigate then fill a form field
agictl browser screenshot "<url>"                     # Capture visible viewport
agictl browser screenshot "<url>" --full-page         # Capture full scrollable page
agictl browser extract "<url>"                        # Extract all text content
agictl browser extract "<url>" --selector "<css>"     # Extract text from specific elements
agictl browser extract "<url>" --selector "<css>" --attribute "<attr>"  # Extract attribute values
```

> **Availability**: Browser requires Playwright to be installed and both `setup.ini [browser] enabled=true` AND your agent's `browser_enabled=1`. If browser is disabled, the harness tool `agictl_browser` will not be available.

**Security**: Only `http://` and `https://` URLs allowed. `file://`, `javascript:`, and `data:` URLs are blocked. All operations run as your OS user.

**Screenshots**: Saved to `workspace/screenshots/browser_<timestamp>.png`.

**Returns JSON**: `{success: true, url: "...", title: "...", content: "...", screenshot: "/path/..."}`

Use for: web page verification, content extraction, form testing, dashboard screenshots, API documentation scraping.

## 14. execute — Code Execution

```bash
agictl bash "<script>"                                # Shorthand for execute bash
agictl execute bash "<script>"                        # Run a bash script
agictl execute python "<script>"                      # Run a Python script
```

Scripts execute as **your agent user** (not root/watchdog) with a 120-second timeout.

> **BLOCKED**: `sudo`, `su`, `pkexec`, `newgrp`, `gpasswd`, `usermod` — these are infrastructure-level blocked. If you need elevated access, set the task to `blocked` and report to COA.

**Returns JSON**: `{success: true/false, output: "...", exit_code: N}`

## 15. model — LLM Model Management

```bash
agictl model list                                     # List registered models with status
agictl model list --table                             # Display as formatted table
sudo agictl model catalog list --table                # Catalog keys (use key column as model ID)
sudo agictl agent set-model <name> <catalog-key>      # Assign model (COA or PU only)
sudo agictl agent set-model <name> --clear            # Clear → inherit default
```

> **Access**: Sub-agents see their assigned model via `system whoami` only. **COA** can assign models with `agent set-model` (must be `coa_approved` when assigning to COA). Full catalog/params/provider CRUD is in **`cli_reference.md`** — load on demand via tool **`agictl_execute`**, argument **`bash "cat ~/.agent/skills/cli_reference.md"`** (see top of this file).

## 16. pkg — System Package Registry

```bash
agictl pkg list                                       # View all registered packages and statuses
agictl pkg request <name> --reason "..."              # Request a system package for installation
agictl pkg install <name>                             # Install an approved package
```

> **Approval workflow**: You request → PU approves → Lifeline notifies you (one-shot prompt injection) → you install. You **cannot** approve, deny, or remove packages — those are PU-only.

> **Security**: Package names must be valid apt names (lowercase, digits, dots, hyphens, plus). Installation uses watchdog→root escalation via sudoers. Only packages with `status='approved'` can be installed.

**Returns JSON**: `{success: true, package: "jq", status: "requested"}`

Use for: requesting build tools, libraries, or runtime dependencies that your work requires.

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
- **Harness first** — call typed tools (`agictl_task`, …); pass subcommands without the `agictl` prefix (see *Harness tool invocation* above)
- **NEVER** guess command syntax — consult this reference
- All effectful commands return JSON: `{"success": true/false, ...}`
- Commands marked **(STUB)** return confirmation but are not fully wired yet

