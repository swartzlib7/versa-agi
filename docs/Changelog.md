# Changelog

> Versa AGi (Agentic General infrastructure)

All notable changes to this project are documented here. This changelog follows release milestones — for detailed engineering notes, see internal documentation.

## [0.11.3] — 2026-05-21

### Backup v2.0 (Manifest-Governed)

#### Added
- **Backup FS Path Manifest** — `design/Versa AGi - Backup Manifest.md` now governs all backup capture and exclusion decisions. The backup script references this document as its authoritative source of truth.
- **Global exclude list** — unified exclusion patterns applied to all home directory captures: `.ollama/`, `.gemini/`, `.cache/`, `.npm/`, `node_modules/`, `__pycache__/`, `.local/`, `venv/`, `.vagrant/`, `.vagrant.d/`, `VirtualBox VMs/`. Previously, each capture group had inconsistent ad-hoc exclusions (COA notably missing `.ollama/` and `.gemini/`).
- **200MB size gate** — each home directory is measured after excludes. If over 200MB, displays top 5 largest subdirectories and prompts for confirmation. Declining aborts the entire backup with instructions to investigate.
- **Sudoers capture** — `/etc/sudoers.d/versa_agi_watchdog` and `/etc/sudoers.d/versa_agi_agictl` now included.
- **CRON tab capture** — `crontab -u watchdog -l` saved to `cron_watchdog.txt` in the archive.
- **SSH tunnel service capture** — `versa-agi-tunnel.service` included for client topology.
- **Post-restore warning** — summary card now displays mandatory `sudo ./setup.sh` requirement.

#### Changed
- **Backup version** — bumped from `1.0` to `2.0`.
- **Sub-agent excludes** — previously had zero exclusions; now uses the same global exclude list as all other homes.
- **`/usr/local/lib/versa-agi/` capture** — now excludes `venv/` (LangGraph harness Python environment, rebuilt by `setup.sh`).
- **Summary card redesigned** — CRON pause warning and post-restore instructions now embedded in the card border instead of separate text.

#### Fixed
- **8GB backup bloat** — root cause: COA and sub-agent homes captured `.ollama/` (model blobs) and `.gemini/` (session caches) without exclusion. Typical backup size now < 500MB.

---

## [0.11.2] — 2026-05-20

### Agent Halt (Manual Cycle Control)

#### Added
- **`agictl agent kill <name>`** — terminates a running sub-agent's harness process and sets `status='halted'` + `inactive=1` to prevent re-spawning. COA-only command (protected agents immune). Recovery via `agictl agent activate` or agitop. Checkpoint-safe — agent resumes from last `SqliteSaver` state on re-activation.
- **Lifeline `halted` gate** — agents with `status='halted'` are blocked from spawning alongside `circuit_breaker`. Log message: `BLOCKED: {name} — status 'halted'`.
- **agitop ✋ Halt Agent button** — Agent Prompt Menu shows a halt button for non-protected active agents. Calls `agictl agent kill` and displays success/failure notification.
- **agitop ▶ Re-activate Agent button** — shown for halted agents (reuses circuit breaker recovery path via `agictl agent activate`).
- **agitop system alert** — system panel shows `✋ HALTED: N agent(s) manually stopped` when any agents are halted.
- **COA poise rule 6** — agent cycle control capability documented as hard constraint. COA can halt sub-agents on Primary User request.

#### Changed
- **`agictl agent activate`** — now clears both `circuit_breaker` and `halted` statuses (shared recovery path).
- **Agent Settings modal** — removed non-functional status picklist (`online`, `working`, `idle`, `paused`, `error`). All statuses are system-managed.

---

## [0.11.1] — 2026-05-17

### Server Setup Fix

#### Fixed
- **`agictl model add` HuggingFace CLI not found on server topology** — `setup.sh` Installation Type 3 (Server) only installed `click` as a Python dependency for `agictl`. The `agictl model add` command on Intel SYCL backend also requires `huggingface-hub[cli]` for GGUF downloads. Now installed alongside `click` during server setup.
- **`agictl model add` HuggingFace CLI PATH resolution under sudo** — `shutil.which()` fails to locate pip-installed binaries under `sudo` due to PATH stripping. Added explicit search in `/usr/local/bin/`, `~/.local/bin/`, and `SUDO_USER`'s `~/.local/bin/` as fallbacks before failing.

---

## [0.11.0] — 2026-05-16

### Edition 2: LangGraph Agent Harness

#### Added
- **LangGraph Agent Harness** — replaced `@google/gemini-cli` with a custom Python-native LangGraph orchestration engine. 11 typed Pydantic tool schemas, `stream()` execution model, cross-cycle checkpointing via `SqliteSaver`, and structured telemetry output.
- **Task Triage Node** — lightweight pre-agent classification with a 10-signal decision matrix. Routes work to the correct project thread and selects relevant skills before the main agent runs.
- **Dynamic Skill Injection** — 18 system skills injected on-demand based on triage classification. Agents only load skills relevant to their current work — reducing prompt size by ~10KB/cycle.
- **Context Window Management** — `pre_model_hook` with `trim_messages` enforces a rolling context window (~32k tokens). Full history preserved in checkpoint; only the LLM input is trimmed.
- **Thread Manager** — visual inspection and management of cross-cycle checkpoint threads per agent via agitop.
- **Anchor Style** — per-agent `anchor_style` setting (`compact`/`full`) controls philosophical preamble injection from `/etc/versa-agi/poise/anchor_full.md`.
- **Budget Warnings** — step budget warnings injected as genuine `HumanMessage` objects at 80%/95% thresholds, with hard termination at 100%.

#### Changed
- **Poise deployment** — flat-copy deployment (no symlinks). Canonical path: `/etc/versa-agi/poise/{agent}.md`. No fallback resolution.
- **Native LangChain integrations** — decommissioned Inference Server proxy layer. All model routing now uses native LangChain classes: `ChatGoogleGenerativeAI` (cloud), `ChatOllama` (local), `ChatOpenAI` (xAI/SYCL).
- **Token telemetry** — extracted natively from LangChain `usage_metadata` and `response_metadata`, supporting all providers. Per-cycle telemetry written to `cycle_telemetry.json` and persisted to `cycles.db`.
- **agitop modal reorganization** — 3×3 button grid: Agent Settings, Technical Setup, View Memory / Poise Template, Last System Prompt, Cycle Log / Request Removal, Close, Manage Threads.
- **System prompt restructuring** — layered neural trajectory with ~45% poise reduction, 5W1H extraction to on-demand skill, INSTRUCTIONS consolidation (~65% reduction).

#### Fixed
- **Lifeline `set -e` in spawn subshell** — removed `set -e` re-entries within the backgrounded spawn subshell. The `kill` against an already-dead runaway monitor caused premature exit under `set -e`, silently skipping post-spawn token extraction. Added `|| true` to `kill`/`wait` for idempotent cleanup.

#### Documentation
- **README.md** — restructured "Implemented ✅" list into 6 categories (Agent Engine, Infrastructure, Safety, Observability, Communication, Operations). Updated stale Gemini CLI references.
- **Versa AGi.md** — updated efficiency section, observability commands, dependencies, agents.db ER diagram with all current columns.
- **System Design** — comprehensive rewrite: removed Inference Server/MCP server references, updated model routing to native LangChain, updated technical debt (TD-001–004 resolved), added Ensure 15–16, updated permissions manifests.
- **Production Plan** — Iteration 14 marked complete, Phase 3b/3c/4 roadmap statuses updated.

---

## [0.10.2] — 2026-05-10

### Distributed Topology Hardening

#### Added
- **SSH tunnel for client topology** — `setup_local.sh` (client remote flow) now provisions `versa-agi-tunnel.service`, an SSH tunnel (`ssh -N -L {port}:localhost:{port}`) that forwards local traffic to the remote inference server. Gemini CLI enforces HTTPS for non-localhost URLs; the tunnel satisfies the localhost exemption while providing encrypted transport. Includes: watchdog SSH key generation, interactive public key display for manual server authorization, SSH connectivity test, and systemd service with auto-restart.
- **Remote backend authentication** — `lifeline.sh` now reads `LITELLM_MASTER_KEY` from `/etc/versa-agi/litellm.env` when `VERSA_GPU_BACKEND=remote`. Previously used dummy key `sk-versa-local` which only works for localhost LiteLLM (no auth). Agents connecting to a remote LiteLLM server would silently fail with 401 Unauthorized.
- **Cloud proxy port separation** — On client topology, the SSH tunnel claims port 4000 for remote local AI. Cloud proxy now auto-overrides to port 4001. Lifeline uses `VERSA_PROXY_LITELLM_URL` for proxy model routing (falls back to `VERSA_LITELLM_URL` on non-client topologies).
- **Intel SYCL support in `--update` mode** — `setup.sh --update` now detects Intel GPU backend and generates correct LiteLLM config with `openai/` model prefix and `api_base: http://localhost:8080/v1`.

#### Fixed
- **Gemini CLI internal models 500 on local AI** — Gemini CLI uses cloud models internally (e.g., `gemini-2.5-flash-lite` for tool output summarization) even when the agent's main model is local. The `gemini-*` wildcard passthrough in `litellm_config.yaml` was gated on `PROXY_ENABLED`, which is `false` on server topology. Now always included when local AI is enabled. Key sourcing falls back from `coa.env` → `setup.ini [gemini] api_key` for server topology (no coa.env).
- **`openssh-server` auto-install for server topology** — `setup_local.sh` now installs and enables `openssh-server` if sshd is not running on server topology (required for client SSH tunnels). Also auto-fixes watchdog shell from `nologin` to `/bin/bash` for SSH compatibility.
- **Tunnel health check false failure** — `setup_local.sh` tunnel verification now passes `Authorization: Bearer` header. LiteLLM rejects unauthenticated `/health` requests when master key is configured, causing a false "server may not be running" warning.
- **`restart_litellm()` was a no-op** — `litellm_helpers.sh` used `systemctl start` which does nothing for already-running services. Changed to `systemctl restart` so config changes take effect.
- **Server mode leaking cloud proxy models** — `setup_local.sh` now forces `[cloud_proxy] enabled=false` in `setup.ini` when running in `server` topology. `setup.sh --update` also purges cloud proxy models from LiteLLM config on server topology.
- **INSTALL_TYPE=2 bypassed hybrid mode** — `setup.sh` now auto-sets `SELECTED_EXEC_MODE=hybrid` when installation type is "Client + Local AI", skipping the redundant execution mode prompt.
- **Session file permissions (setgid)** — `.gemini/tmp/` and `.gemini/history/` subdirectories now use `2770` (setgid) instead of `770`. Without setgid, new session files created by agents inherited the user's primary group instead of `agi_agents`, preventing watchdog from managing them.
- **SYCL model registry mismatches** — `cli.py` `SYCL_MODEL_MAP` corrections: `gemma4:e4b` repo changed from non-existent `unsloth/gemma-4-12B-A2B-it-GGUF` to `unsloth/gemma-4-E4B-it-GGUF`; `gemma4:31b` filename changed from `gemma-4-31B-it-UD-Q4_K_M.gguf` to `gemma-4-31B-it-Q4_K_M.gguf` (no `UD-` prefix in actual repo).
- **`agictl model add` HuggingFace CLI resolution** — Added fallback to LiteLLM venv path (`/opt/versa-agi/litellm/bin/hf`) when running as root, where `huggingface-cli` is not in PATH.

---

## [0.10.1] — 2026-04-28

### Backup & Restore Hardening

#### Added
- **Interactive prerequisite installer** — `restore.sh` Step 2 now offers to install missing system packages (git, sqlite3, jq, inotify-tools, Node.js v22 via NodeSource, Gemini CLI via npm) instead of simply aborting. Mirrors `setup.sh` behavior.
- **`--yes` flag** — `restore.sh` auto-accepts all prerequisite installs without prompting (for unattended restores).
- **Node.js version validation** — restore now checks Node.js major version (≥ v22) and offers NodeSource upgrade when outdated.
- **Gemini CLI version validation** — restore checks minimum version (≥ v0.35.1) and offers upgrade. Checks both `/usr/local/bin/` and `/usr/bin/` paths.

#### Fixed
- **Python venv incomplete packages** — restore venv rebuild now installs the full dependency set (`click`, `rich`, `textual`, `psutil`). Previously only installed `textual`, causing `agictl` to fail with `ModuleNotFoundError: No module named 'click'`.
- **Circular workspace symlink** — `ln -sf` follows existing symlinks to directories, creating `workspace/workspace`. Changed to `ln -sfn` which replaces the symlink itself.
- **CRON prefixes break agitop toggle** — backup paused CRON with `# BACKUP_PAUSED:` prefix, but `toggle_cron()` only strips `# `. Normalized to simple `# ` comments in both `backup.sh` and `restore.sh`.

#### Changed
- **Documentation** — clarified `system_config.json` (template with null runtime fields) vs `coa_config.json` (authoritative runtime config with API tokens) distinction in System Design, Product Specification, and README.

---

## [0.10.0] — 2026-04-26

### System Backup & Restore

#### Added
- **`versa-agi-backup`** — complete system-level backup utility. Captures all databases, configuration, agent workspaces, system binaries, CRON, sudoers, and systemd units into a single compressed archive at `~/.versa-agi/backups/`.
- **`restore.sh`** — self-contained restore script embedded in every backup archive. Recreates OS users from manifest, restores all data to original paths, rebuilds Python venvs, fixes permissions, runs health checks.
- **`manifest.json`** — embedded metadata including hostname, OS, kernel, agent registry, OS user UIDs/GIDs/groups, and Primary User symlinks for faithful system reproduction.
- **`capture()` exclude support** — optional 3rd argument for rsync exclusions (used to prevent recursive backup nesting).
- **Persisted admin tooling** — `backup.sh`, `uninstall.sh`, `patch_wrapper.sh`, `rekey.sh` now deployed to `/usr/local/lib/versa-agi/` with convenience symlinks at `/usr/local/bin/versa-agi-*`.

#### Changed
- **`setup.ini` canonical path** — migrated from source-tree-relative to `/etc/versa-agi/setup.ini` as the single source of truth. All scripts updated with fallback search order: `/etc/` → `${SCRIPT_DIR}` → parent dir.
- **`install.sh` ownership scoping** — `chown -R` now targets only `~/.versa-agi/repo/` (was over-scoped to entire `~/.versa-agi/`).

#### Fixed
- **`set -e` compatibility** — replaced `[ -n ] && action` patterns with explicit `if` blocks and `return 0` to prevent silent script termination when paths don't exist.

---

## [0.9.1] — 2026-04-06

### Local AI Hardening & Context Injection Fixes

#### Fixed
- **Uninstall mode reset** — `uninstall_local.sh` now resets execution mode from both `hybrid` and `local` back to `cloud` (previously only reset `local`, leaving `hybrid` dangling).
- **Conversation history blank for sub-agents** — Internal messages use `to_user_id=agent_name` (not VV UID). Added `--agent-name` to `conversation-context` command; all queries now match both VV UID and agent name.
- **Project memory leaking across agents** — Sub-agents were seeing ALL projects with active tasks. Now COA sees all (orchestrator visibility), sub-agents see only projects where they are explicitly assigned.
- **`setup_local.sh` source-of-truth sync** — Now updates `setup.ini [local_ai] enabled=true` in addition to `paths.env`. Previously, running `patch.sh` after install would revert the enabled flag to false.
- **`/opt/versa-agi/litellm/` ownership** — Aligned to `root:root 755` (consistent with the `venv/` directory). The systemd service runs as `watchdog` but only needs read+execute access.

#### Changed
- **Pip install spinner** — `setup_local.sh` now shows an animated Braille spinner during the slow `litellm[proxy]` installation step.
- **CLI guidance** — Removed non-existent `agictl agent set` commands from setup/uninstall output. Model assignment is via the Dashboard only.

---

## [0.9.0] — 2026-04-06

### Local AI Backend (TD-001)

#### Added
- **Hybrid execution modes** — `cloud`, `local`, or `hybrid` via `setup.ini [gemini] mode=`. Local-only mode allows air-gapped operation without a Gemini API key.
- **`setup_local.sh`** — Standalone installer for Ollama + LiteLLM proxy. Creates a Python venv at `/opt/versa-agi/litellm/`, generates systemd service, pulls default model, and updates `paths.env`.
- **`uninstall_local.sh`** — Clean removal: stops LiteLLM service, deactivates agents assigned to local models, resets `setup.ini [local_ai] enabled=false`.
- **Backend resolution in `lifeline.sh`** — Model-driven dispatch: agents with local models get `GOOGLE_GEMINI_BASE_URL` pointed to the LiteLLM proxy. Invalid configs (local model + disabled backend, unknown model, cloud model in local-only mode) set `status=invalid_config` and skip the agent.
- **`setup.ini [local_ai]` section** — `enabled`, `ollama_host`, `proxy_port`, `default_model`, `local_models`, `auto_pull_model`.
- **`setup.ini [gemini] cloud_models`** — Tracked cloud model registry, auto-updated by `patch.sh`.
- **Dashboard: LITELLM + LOCAL AI indicators** — `SystemPanel` shows real-time LiteLLM daemon status and current execution mode.
- **Dashboard: Config error alerts** — Persistent `⚠ AGENT CONFIG ERROR` banner when any agents have `invalid_config` status.
- **Dashboard: Backend icons** — ☁ (cloud), 🖥 (local), ⚠ (invalid) shown per agent in the agents table.
- **Dashboard: Mode-aware model picker** — `AgentEditModal` filters available models by execution mode. Cloud models prefixed ☁, local models prefixed 🖥.
- **Dashboard: COA soft warning** — Yellow ⚠ indicator when COA is assigned a local model.

#### Changed
- **`setup.sh` Step 9** — Execution mode selection prompt with conditional Gemini API key validation (skipped in local-only mode).
- **`patch.sh` Step 5c** — Auto-syncs cloud model registry + local AI settings from `setup.ini` into `paths.env` on every patch.
- **`patch.sh` INI backfill** — Now includes `[local_ai]` in expected sections.
- **`paths.env`** — New environment variables: `VERSA_EXECUTION_MODE`, `VERSA_CLOUD_MODELS`, `VERSA_LOCAL_AI_ENABLED`, `VERSA_LOCAL_MODELS`, `VERSA_LITELLM_URL`.

---

## [0.8.0] — 2026-04-06

### WSL Hardening, Fast-Start Poise, Quota Resilience

#### Added
- **First-boot exception** — Lifeline forces a wake cycle when the Initial Welcome Sequence task is still `planned`, bypassing the 60-second staleness guard and daily overdue marker that could strand the agent after its first quota-exhausted cycle.
- **Two-tier API quota cooldown** — Daily quota exhaustion (`TerminalQuotaError`, `free_tier_requests`) triggers a 1-hour cooldown (was 3 min). Temporary 429s retain the existing 5-minute cooldown. Prevents ~20 futile retries/hour on exhausted free-tier limits.
- **`toggle_cron()` method** — `SystemReader` now provides a Python-native CRON toggle using `subprocess.run()`, replacing the `os.system()` + sed pipeline that failed silently on WSL.
- **`kill_agents()` method** — `SystemReader` now targets all registered agent OS users (COA + sub-agents), not just the hardcoded COA user.
- **Poise message command reference** — Essential `agictl message` commands (`get --unread`, `send`, `mark-processed`, conversation history) embedded directly in the COA poise to eliminate discovery overhead.
- **Skills deployment in `setup.sh`** — System skills from `core-infra/skills/` are now copied into `.agent/skills/` during initial setup (previously only deployed by `patch.sh`).

#### Changed
- **Poise fast-start** — Removed redundant `agictl system whoami` and `agictl task list` from the work cycle (both are pre-loaded in spawn prompt). Eliminated ~10 wasted tool calls per cycle.
- **agitop WSL compatibility** — Replaced all `os.system()` calls with `subprocess.run(capture_output=True)` to prevent terminal conflicts with Textual's TUI on WSL.

#### Fixed
- **Python venv validation** — `setup.sh` now validates that required packages (`click`, `rich`, `textual`, `psutil`) are importable inside the venv on every run, rather than just checking directory existence. Prevents phantom venv from a failed first-run on minimal Ubuntu/WSL images.
- **`python3-pip` auto-install** — Added to the Step 2 prerequisite block alongside `python3-venv` for minimal images that strip pip.

---

## [0.7.0] — 2026-03-29

### Hardening: Runaway Monitor, Role Registry, Poise Refactor

#### Added
- **3-trigger runaway detection** — monitors result file line count, result file size, and session file size. Each configurable per-agent via the `agitop` dashboard.
- **Directory-based role registry** — roles at `config/roles/{role_id}/` with `poise.md` (behavioral template) and `role.ini` (metadata + model override).
- **Per-role model selection** — each role can specify a Gemini model in `role.ini`. Lightweight roles (e.g., `sysmon`) can use cheaper models.
- **`agent_onboarding.md` skill** — dedicated sub-agent provisioning workflow with role registry awareness.
- **`gemini-3.1-flash-lite-preview`** — added to supported model reference.

#### Changed
- **COA poise refactored** — 256 → 169 lines. Monolithic 14-step work cycle replaced with a skill-routing decision tree.
- **Enriched sentinel format** — runaway sentinel now contains `trigger:diagnostics` for clear system notifications.
- **`agictl list-roles`** — now parses `role.ini` for description and model display.
- **`agictl agent add`** — auto-populates agent model from role configuration.
- **Skills split** — mid-work inbox check and conversational context recovery moved from poise into `communication.md` skill.

---

## [0.6.0] — 2026-03-27

### Stabilization: CLI Alignment, Dashboard, Profile Sync

#### Added
- **Profile enrichment pipeline** — schema migration (`city`, `chromosome`, `profile_synced_at`), `agictl system sync-profiles`, 7-day lifeline staleness check.
- **`agitop` deployed via `patch.sh`** — prevents launcher staleness.
- **TD-SEC-012** — Trusted-User Simplified Security Protocol (planned).

#### Changed
- **Skills deployed read-only (440)** — agents can create their own writable skills, but system skills are immutable.
- **Effort calibration protocol** — prevents agents from burning cycles on exaggerated requests.
- **One recurring task, rolled forward** — prevents scheduling pollution.

---

## [0.5.0] — 2026-03-25

### V3 Gateway Hardening

#### Added
- **Watchdog Sentinel kill-switch** — `sentinel_enabled` flag in `setup.ini`.
- **Configurable agent timeouts** — database-backed, editable via `agitop`.
- **`agictl task` full data payloads** — eliminated hallucination loops from incomplete CLI responses.

#### Changed
- **SQLite-centric configuration** — eliminated legacy JSON runtime files.
- **Permission regressions** — fixed persistent deployment pipeline permission issues.
- **Agent workspace documentation** — updated to reflect V3 architecture.

---

## [0.4.0] — 2026-03-22

### V3 Architectural Baseline

#### Added
- **4-database schema** — `agents.db`, `messages.db`, `tasks.db`, `cycles.db` with strict separation of concerns.
- **Python-native `agictl` CLI** — Click-based, replaces bash prototype.
- **`agitop` Mission Control Dashboard** — Textual-based TUI with agent monitoring, task overview, and cycle telemetry.
- **REST-first communication** — native VersaVoice AI REST API integration (replacing MCP stdio for system operations).
- **Token usage tracking** — per-session extraction from Gemini CLI JSON session files.

#### Changed
- **Agent execution model** — per-agent OS user isolation with `agi_agents` group.
- **Poise framework** — precision instrument model, not personality simulation.

---

## [0.3.0] — 2026-03-14

### Identity & Automation

#### Added
- **Persistent agent identity** — survives reinstallation via database storage.
- **`init_vv_identity.sh`** — automated VersaVoice sub-account provisioning.
- **`agictl system whoami`** — identity verification command.

#### Changed
- **Sensitive tokens removed from Git** — API keys now exclusively in `setup.ini` (gitignored).

---

## [0.2.0] — 2026-03-12

### Communication Foundation

#### Added
- **VersaVoice MCP integration** — agent messaging through VersaVoice AI platform.
- **Inbox pre-fetch policy** — Lifeline syncs messages before spawning agents.
- **Skills library** — `security_protocol.md`, `communication.md`, `memory_management.md`.

---

## [0.1.0] — 2026-03-08

### Prototype (POC)

#### Added
- Core infrastructure skeleton: `lifeline.sh`, `watchdog.sh`, agent registry.
- COA environment with poise framework, work cycle, and VersaVoice identity.
- CRON-based 1-minute Lifeline pulse.
- Interactive `setup.sh` installer with branded CLI output.
- `uninstall.sh` clean teardown.

#### Confirmed
- Gemini CLI auto-terminates in headless mode (`-p`).
- Exit codes are meaningful (0, 1, 42, 53).
- `--yolo` mode exits after task completion.

---

*For installation, see [README.md](../README.md). For architecture details, see [architecture.md](architecture.md).*
