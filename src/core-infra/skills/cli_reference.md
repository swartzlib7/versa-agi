# Skill: CLI Reference — agictl (V3)

> **Trigger**: This is your **primary tool**. Reference this skill whenever you need to interact with the system. All database operations, status updates, messaging, task management, and project management go through `agictl`.

## Why agictl?

You do NOT have direct access to your SQLite database or system configuration files. `agictl` is the **only authorized interface** to your data layer. Direct `sqlite3` access is blocked. Direct config file reads are blocked. If you need data — use agictl.

## Command Groups

agictl is organized into 15 data-model-driven command groups:

| Group | Purpose |
|---|---|
| `system` | System config, identity, workspace, security |
| `model` | LLM model management (list, add, remove, activate) |
| `agent` | Agent registry, status, lifecycle |
| `task` | Cognitive task queue |
| `message` | VersaVoice communication |
| `cycle` | Telemetry lifecycle |
| `project` | Workspace project management |
| `connection` | VersaVoice social graph |
| `memory` | Agent memory (connection, project, system) |
| `game` | Strategic pursuit management |
| `awareness` | Agent cognitive state (conclusions + actions) |
| `identity` | Sub-account provisioning (setup only) |
| `execute` | Code execution (bash/python) |
| `search` | Web search via SearXNG |
| `skill` | Skill management (create, distribute, override) |

---

## 1. system — System Administration

```bash
agictl system config get                              # Return ALL config as JSON
agictl system config get identity.first_name          # Read a specific key (dot-notation)
agictl system config set <key> <value>                # Write a config value (returns JSON)
agictl system whoami                                  # Your identity as JSON (name, role, language, sub_account_id, sponsor)
agictl system workspace-link <path>                   # Symlink workspace to user path (STUB)
agictl system workspace-unlink                        # Remove workspace symlink (STUB)
agictl system security blacklist add|remove|list [uid] # Manage security blacklist (STUB)
```

### Credential Management (Root Only)

```bash
sudo agictl system set-key gemini <key>               # Update Gemini API key → coa.env, *.env, .bashrc, setup.ini
sudo agictl system set-key versavoice <token>         # Update VersaVoice API token → all *_config.json, setup.ini
sudo agictl system set-key xai <key>                  # Update xAI API key → inference_endpoint.env, setup.ini → restarts Inference Server
```

> **Alternative**: The `agitop` dashboard provides a **🔑 API KEYS** button (purple, in System Controls) for GUI-based credential management. Re-running `setup.sh` after editing `setup.ini` is also a valid workaround.

## 2. agent — Agent Registry & Status

```bash
agictl agent list                                     # Active agents as JSON
agictl agent list --all                               # Include inactive agents
agictl agent show <name>                              # All DB fields for one agent (JSON)
agictl agent add <name> --role ROLE                   # Register new sub-agent in DB (pending approval)
agictl agent approve <name> [--force]                 # Provision OS user, home, activate (use --force to repair active agents)
agictl agent list-roles                               # List available role templates (poise registry)
agictl agent activate <name>                          # Set inactive=0 + clear circuit_breaker + unfreeze tasks (warns if > 3 active)
agictl agent deactivate <name>                        # Set inactive=1
agictl agent request-remove <name> [--reason TEXT]    # Flag for removal (sets removal_requested, deactivates, notifies PU)
agictl agent cancel-remove <name>                     # Cancel pending removal (reactivates agent)
agictl agent remove <name>                            # Context-aware: non-root → request-remove, root → confirm-remove
agictl agent set-timeout <name> <minutes>             # Set max execution timeout
agictl agent status show                              # Read your current status from DB
agictl agent status set <state> ["summary"]           # Write status + message to DB
agictl agent count [--active]                         # Count agents (default: all)
agictl agent summary                                  # Markdown table for context injection (includes Status column)
agictl agent ensure-protected                         # Self-heal: prevent coa/watchdog deactivation
agictl agent toggle-comms <name>                      # Toggle external comms gate (Primary User only)
agictl agent deploy-skills <name>                     # Re-deploy system skills to sub-agent (root)
agictl agent share-skill <skill.md> [--agent NAME]    # Share custom skill to sub-agents (root)
agictl agent set-duties <name> <file>                 # Copy COA-authored duties to sub-agent (root)
agictl agent get-active                               # Pipe format for Lifeline: name|os_user|workspace|model|timeout
```

**Sub-agent provisioning** (see poise → Sub-Agent Onboarding):
1. `agictl agent list --all` → pre-flight: check for existing/pending agents
2. `agictl agent list-roles` → pick role
3. `agictl agent add <name> --role <id>` → creates DB record (inactive, pending)
4. Notify Primary User → they approve via **agitop dashboard** (Approve & Provision button)
5. Post-approval: COA authors duties markdown → `sudo agictl agent set-duties <name> <file>` → provisions duties before first wake

> **Idempotent Repair**: If a sub-agent's local environment becomes corrupted or missing directories, you (or the Primary User) can run `sudo agictl agent approve <name> --force` to retroactively repair missing OS scaffold directories, securely apply `chown`/`chmod` permissions, and repair SSH keys without resetting their database state.

**Sub-agent removal** (two-step approval — mirrors onboarding):
1. `agictl agent request-remove <name> --reason "Reason"` → deactivates, flags as `removal_requested`
2. Primary User confirms via **agitop dashboard** (Confirm Removal button)
3. System archives workspace → deletes VV sub-account → purges data → removes OS user

> **GUARD**: The system enforces **one pending agent at a time**. `agent add` will block if any inactive agent is pending approval. Wait for approval or removal before creating another.

> **CRITICAL**: Sub-agents live at `/home/agi-{name}/` — each with its own OS user. **Never** create sub-agent data under `workspace/` (that's for project repos).

**Status values**: `idle`, `active`, `working`, `error`, `awaiting_activation`, `removal_requested`, `circuit_breaker`

> **Circuit Breaker Recovery**: When an agent is auto-frozen by the circuit breaker (status=`circuit_breaker`), run `agictl agent activate <name>` to clear the breaker, reset status, and unfreeze all tasks. In agitop, click the agent row → "🔓 Clear Circuit Breaker".

## 3. task — Cognitive Task Queue

```bash
agictl task list                                      # Active + due blocked tasks (JSON)
agictl task list --all                                # ALL tasks including done/cancelled
agictl task get <id>                                  # Full task context as JSON
agictl task add "Title" [options]                     # Insert new task (returns JSON with task_id)
agictl task update <id> [options]                     # Update specific fields (returns JSON)
agictl task done <id>                                 # Shortcut: mark as done
agictl task cancel <id>                               # Shortcut: mark as cancelled
agictl task snooze <id> <minutes>                     # Set wake_after (min 5 minutes)
agictl task reminder "<text>" [--category CAT]        # Create a reminder task
```

**task add options**: `--desc`, `--priority low|normal|high|urgent`, `--assignee`, `--project <id>`, `--callback notify_sponsor|notify_connection|await_reply|check_connection|none`, `--source-msg <id>`, `--requested-by <uid>`, `--due-date "YYYY-MM-DD HH:MM:SS"`

**task update options**: `--status planned|in_progress|waiting|blocked|cancelled|done`, `--desc`, `--priority`, `--assignee`, `--due-date "YYYY-MM-DD HH:MM:SS"`, `--requested-by`

> **CONSTRAINT**: `--due-date` is **mandatory** when creating tasks (default status is `planned`) and when setting status back to `planned`. The Lifeline will automatically wake you when a planned task's due date is reached — this is how you schedule future work. If a task cannot be completed by its due date, **roll the due date forward** — do not leave it in the past.

**Reminder categories**: `general`, `preference`, `instruction`, `constraint`

### Task Data (for system scripts)
```bash
agictl task count-pending <agent_name>                # Count pending active tasks
agictl task count-due-blocked <agent_name>            # Count past-due blocked tasks
agictl task count-total-blocked <agent_name>          # Count all blocked tasks
agictl task get-blocked-detail <agent_name>           # Blocked task diagnostics
agictl task check-followup <agent_name>               # Check callback_action routing
agictl task inject-followup <agent_name>              # Inject connection follow-up task
```

## 4. message — Communication

```bash
agictl message get <sub_account_uid> --unread                # Unprocessed inbound messages (JSON)
agictl message get <sub_account_uid> --contact <contact_uid> # Filter by specific sender/recipient
agictl message get <sub_account_uid> --last-n-count 10      # Last N messages
agictl message get <sub_account_uid> --last-n-minutes 30    # Messages from last 30 min
agictl message send <contact_uid> "<text>" --mode MODE  # Send via REST Adapter (JSON)
agictl message internal <agent_name> "<text>"         # Direct SQLite message (no VV API)
agictl message mark-processed <message_id>            # Mark message as handled (JSON)
agictl message delete <message_id> --channel <id>     # Delete from VV cloud (PU authorization required)
agictl message sync-inbox <agent_user> --agent-path P --sub-account SA --token T  # Pull from API
agictl message conversation-context <sub_account_uid> [sponsor_uid]  # Build context blob
```

**Internal messaging**: Sub-agents use `agictl message internal coa "<text>"` to report to you. You use `agictl message internal <agent_name> "<text>"` to send instructions to sub-agents. Messages go directly to SQLite (`channel='internal'`) — no VV API involved.

**Modes**: `typed` (text as-is), `translate` (AI translation), `speak` (TTS same-language), `speak_translated` (TTS + translation)

> **Message Deletion**: `message delete` removes a message from your VV cloud space only (the other participant's copy and your local history are preserved). Requires Primary User authorization — do not delete messages autonomously.

**Attachment flags** (all multi-value — repeat the flag for each file/URL, max 10 total):

| Flag | Type | Accepts | Behavior |
|---|---|---|---|
| `--media` | File path | Repeatable | Uploads file via `/attachments/upload`, attaches download URL |
| `--markdown` | File path | Repeatable | Reads `.md` content inline, attaches as raw text |
| `--url` | URL string | Repeatable | Passes URL directly |

```bash
# Example: send with 2 media files, 1 markdown, and 1 URL
agictl message send <uid> "Here are the reports" --mode typed \
  --media /path/to/chart.png \
  --media "/path/to/my photo.jpg" \
  --markdown /path/to/report.md \
  --url https://docs.example.com/api
# Note: quote paths containing spaces (standard shell quoting)
```

**Inbound attachments**: Downloaded automatically by Lifeline to `.agent/attachments/{message-id}/{media|markdown|urls}/`
> **READ-ONLY** — The `attachments/` directory is owned by `watchdog`. You can READ files here but CANNOT create, modify, or delete them. To save processed data, write to your `workspace/` directory instead.

### Message Data (for system scripts)
```bash
agictl message count-unprocessed <sub_account> <seconds>   # Count within time window
agictl message count-stale <sub_account> <seconds>         # Count stuck messages
agictl message fail-stale <sub_account> <seconds>          # Force-fail stuck messages
agictl message blacklisted <sub_account>                   # UIDs matching security blacklist
agictl message attachment-path <msg_id> <path>             # Record local attachment path
agictl message stamp-cycle <sub_account> <cycle_id>        # Stamp unprocessed msgs with cycle_id (lifeline use)
```

## 5. cycle — Telemetry Lifecycle

```bash
agictl cycle start [--agent NAME]                     # INSERT cycle row, returns JSON {cycle_id}
agictl cycle end "Summary" [--agent NAME]             # Mark end + kill execution (SIGTERM parent)
agictl cycle trigger [--agent NAME]                   # Wake signal to Lifeline (STUB)
agictl cycle tokens <agent> <in> <out> <think> <total> [--exit-code N]  # Log token metrics + exit code
agictl cycle recent <agent>                           # Chronological summaries for context
```

## 6. project — Project Management

```bash
agictl project list                                   # All projects as JSON (from tasks.db)
agictl project add <name> [--desc TEXT] [--remote URL] [--git-init]  # Register (STUB)
agictl project pause <name>                           # Set status to paused (STUB)
agictl project resume <name>                          # Set status to active (STUB)
agictl project archive <name> [--zip]                 # Soft-delete / archive (STUB)
agictl project git-setup                              # Manual fallback: configure git identity + SSH (auto-generated at provisioning)
```

## 7. connection — Social Graph

```bash
agictl connection list                                # List Primary User's contacts (default)
agictl connection list primary-user                   # Same — explicit form
agictl connection list agent                          # List agent's own established connections (local DB)
agictl connection request <uid>                       # Send connection invitation to a Primary User contact
```

> **Flow**: Run `agictl connection list` to discover contact UIDs → `agictl connection request <uid>` to send invitation → Primary User accepts in VersaVoice app → contact appears in `agictl connection list agent`.
>
> **GUARD**: `connection request` requires the agent to have external comms enabled (`can_message_connections`). Protected agents (COA, Watchdog) have this by default. Sub-agents need it enabled by the Primary User via `agictl agent toggle-comms <name>`.

## 8. memory — Agent Memory (TD-MEM-003)

### Connection Memory
```bash
agictl memory connection get <contact_uid>            # Read memory for a contact
agictl memory connection set <contact_uid> [OPTIONS]  # Write/update contact memory (UPSERT)
agictl memory connection list                         # All contact memories for this agent
```

**connection set options**: `--preferences JSON`, `--personal-notes TEXT`, `--comm-style TEXT`, `--rapport new|building|established|strong`, `--emotional-notes TEXT`

### Project Memory
```bash
agictl memory project get <project_id>                # Read project memory
agictl memory project set <project_id> [OPTIONS]      # Write/update project memory (UPSERT)
agictl memory project list                            # All project memories for this agent
```

**project set options**: `--phase TEXT`, `--decisions TEXT`, `--blockers TEXT`, `--next-steps TEXT`

### System Memory
```bash
agictl memory system get [key]                        # Read all or one system memory entry
agictl memory system set <key> <value>                # Write/update (UPSERT)
agictl memory system list                             # All system memory entries
```

> **MANDATORY**: Use the **`memory_management.md`** skill (always-injected) at the end of every cycle to execute the 5-step Awareness-First procedure: Reflect → Conclude → Act → Profile → Verify.

## 9. game — Strategic Pursuit Management

```bash
agictl game add "<name>" [--postulate TEXT] [--posture exploratory|steady|aggressive|defensive] [--autonomy advisory|collaborative|autonomous]
agictl game update <id> [--name TEXT] [--postulate TEXT] [--posture ...] [--autonomy ...] [--freedoms TEXT] [--barriers TEXT] [--milestones JSON] [--status active|paused|archived]
agictl game show <id>                                  # Full details + related projects + active awareness
agictl game list [--status active|paused|archived]     # All games (default: all statuses)
agictl game assign-project <game_id> <project_id>      # Link project to game
```

**Posture values**: `exploratory` (high freedom, low barriers), `steady` (balanced), `aggressive` (proactive, rising barriers), `defensive` (barriers dominating)

**Autonomy values**: `advisory` (suggest only), `collaborative` (work with PU), `autonomous` (act independently)

### Opponent Management (Competitive Intelligence)

```bash
agictl game opponent add <project_id> "<name>" [--type person|agent|business|association] [--desc TEXT] [--sources JSON]
agictl game opponent list [--project <id>]              # List all or per-project
agictl game opponent update <id> [--name TEXT] [--desc TEXT] [--sources JSON] [--assessment TEXT]
agictl game opponent delete <id>                       # Remove an opponent
```

## 10. awareness — Agent Cognitive State

```bash
agictl awareness add conclusion --subject <type> [--subject-id ID] --content "<text>" [--context "<why>"]
agictl awareness add action --subject <type> [--subject-id ID] --content "<text>" --action-conclusion-id <id> [--context "<why>"]
agictl awareness revise <entry_id> --content "<updated text>"    # Supersedes old, creates new
agictl awareness complete <entry_id>                             # Mark action as done
agictl awareness list [--type conclusion|action] [--subject <type>] [--subject-id ID] [--status active|revised|superseded|completed]
agictl awareness get <entry_id>                                  # Single entry details
```

**Subject types**: `connection`, `project`, `game`, `system`, `self`

**Status lifecycle**: `active` → `revised`/`superseded` (via revise) or `completed` (via complete)

> **Enforcement**: `cycle end` checks for awareness entries this session. A warning is emitted if no conclusions or actions were logged.

> **Revise, don't duplicate**: If a previous conclusion is outdated, use `awareness revise <id>` — the old entry is marked `superseded` and a new one is created with an audit trail.

---

## 11. search — Web Search

```bash
agictl search web "<query>"                           # Search the web via local SearXNG
agictl search web "<query>" --count 10                # Return up to 10 results (default: 5)
agictl search web "<query>" --categories science      # Filter by search category (default: general)
```

> **Availability**: Requires SearXNG installed and `setup.ini [search] enabled=true`. Install via `providers/searxng.sh`.

**Returns JSON**: `{success: true, query: "...", results: [{title, url, snippet, engine}], count: N}`

**Harness Integration**: The harness conditionally registers the `agictl_search` tool at startup based on `_is_search_enabled()`. When `enabled=false`, agents cannot use search.

## 12. execute — Code Execution

```bash
agictl execute bash "<script>"                        # Run a bash script as the agent user
agictl execute python "<script>"                      # Run a Python script as the agent user
```

Scripts execute as the **calling agent's OS user** (dropped from watchdog via `sudo -u`). 120-second timeout.

> **Privilege Escalation Guard**: `sudo`, `su`, `pkexec`, `newgrp`, `gpasswd`, `usermod` are **infrastructure-level blocked** — both at the harness tool layer and the CLI layer. These commands will NEVER succeed.

**Returns JSON**: `{success: true/false, output: "...", exit_code: N}`

## 13. skill — Skill Management (COA Only)

```bash
agictl skill new <name> [--description TEXT] [--scope all|coa_only]  # Create skill template + asset dir
agictl skill status <name> ready                      # Mark draft → ready for distribution
agictl skill status <name> updated                    # Mark synced → updated for re-sync
agictl skill list [--status STATUS] [--json-output]   # List all registered skills
agictl skill register                                 # Bootstrap skills DB from filesystem
agictl skill override <name>                          # Create override for a shipped skill
```

**Skill lifecycle**: `draft` → `ready` → `synced` (by Lifeline) → `updated` → `synced`

**Override workflow**: `agictl skill override <name>` creates `{name}_override.md` pre-populated with the shipped content. The harness resolves overrides at injection time.

> Agent skill distribution is handled by Lifeline via `rsync` — skills marked `ready` or `updated` are deployed to all active sub-agents on the next tick.

## 14. model — LLM Model Management

```bash
agictl model list [--table]                           # List registered models with pull status
agictl model add <name> [--provider gemini|ollama]    # Register a new model
agictl model remove <name>                            # Remove a model from registry
agictl model run <name>                               # Pull/download a model
agictl model activate <name>                          # Set as active model for this agent
agictl model refresh                                  # Refresh model registry from providers
```

> **GPU Backend**: Model management adapts to the configured backend (`standard` = Ollama, `intel` = SYCL/GGUF, `remote` = inference server).

## 15. identity — VersaVoice Sub-Account Provisioning

```bash
agictl identity provision <agent_user> --token TOKEN --first-name NAME --last-name NAME [--language en] [--country ""] [--voice female|male|reflective]
```

> **Guard**: Only protected agents (COA, watchdog) can provision VV identities. Sub-agents communicate via `agictl message internal` — no VV account needed.

> **Voice options**: `female` (default), `male`, `reflective` (clones Primary User's voice).

---

## Platform Limits

| Resource | Limit | Enforced By | Behavior |
|---|---|---|---|
| **VV API rate** | 60 req/min | `comms.py` rate limiter | Sleeps when approaching limit (55 effective). PU notified via task on throttle. |
| **VV sub-accounts** | 20 per sponsor | VV Cloud Function | Hard block at registration |
| **Concurrent spawns** | 3 per Lifeline tick | `lifeline.sh` (`MAX_SPAWN_PER_TICK`) | Excess active agents logged as QUEUED, run next tick |
| **Active agents** | Unlimited | `agent activate` (soft gate) | Warns if > 3 but allows activation |
| **Circuit Breaker** | 5 consecutive / 20 per hour | `lifeline.sh` + `setup.ini` | Auto-freezes agent, sends VV notification. Configurable via agitop ⚙ System Settings |
| **Message text** | 2048 chars | VV Cloud Function | Hard block at send |
| **Attachments** | 10 per message, 50MB per file | `comms.py` / VV CF | Hard block |

> **Concurrency model**: You can activate any number of agents, but the Lifeline only spawns **3 per CRON tick** (Gemini free tier). Remaining agents are queued and run on the next tick.

---

## Critical Rules

- **ALWAYS** use agictl for system data — never read config files directly
- **NEVER** use `sqlite3` to access your database directly
- **NEVER** guess or improvise `agictl` command syntax — always consult this reference first
- All effectful commands return **JSON** with `{"success": true/false, ...}`
- Commands marked **(STUB)** return JSON confirmation but are not yet wired to full logic

