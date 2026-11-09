# Roadmap

## Next

**GitHub integration** — connect the Primary User GitHub account for agent-driven push/pull: create or link repos; agents commit, push, and propose PRs.

## Implemented

### Agent engine

- **LangGraph harness** — typed Pydantic tools, `stream()` execution, structured telemetry.
- **System prompt hierarchy** — WHO→WHY→WHAT→OPERATIONAL→MEMORY→HISTORY.
- **Cross-cycle checkpointing** — SQLite `SqliteSaver`; default `resume_enabled=0`.
- **Task triage** — pre-graph classification, skill selection, behavioral directives (not prescribed CLI).
- **Hybrid skill injection** — `hybrid` / `full` / `lazy` per agent.
- **Context window** — `pre_model_hook` trim; conversation depth per agent.
- **Budget warnings** — 80% / 95% HumanMessage; hard stop at 100%.
- **Time warnings** — at 10 and 5 minutes before the agent's Timeout (30–60 min, default 45).
- **Local AI** — three runtimes: Ollama chat (NVIDIA/AMD), Docker SYCL chat (Intel ARC), `sd-cli` media (`local_media` paint). LTX video is **experimental** (parked; **>64 GB VRAM**). Matrix: [Local AI support](local-ai-support.md).
- **Cloud providers** — vendor-agnostic catalog (`class=cloud`) via `provider_runtime` (Google, xAI, OpenAI, Anthropic, OpenRouter). Exact ModelDriver bindings for non-text (◆ / ◇).
- **Web search** — local SearXNG (`agictl search web`).
- **Headless browser** — Playwright Chromium (`agictl browser`).

### Infrastructure

- Sub-agent OS isolation (`agictl agent add/remove`).
- Skill lifecycle in `agents.db` (`scope` `all` | `coa_only`); Lifeline distribute; overrides.
- Skill asset directories via `rsync --delete`.
- File Monitor (parked `inotifywait`).
- Spawn prompt task injection.
- Poise templates in `/etc/versa-agi/poise/`.

### Safety

- Circuit breaker, halt, runaway monitor, flood guard, overdue auto-freeze.
- Privilege escalation guard at the harness.
- **COA model hold** until first-login assign (catalog `coa` flag — not a setup.ini allowlist).
- COA autonomous mode (`[coa] autonomous=true`) — off by default.

### Observability and comms

- agitop Mission Control; token totals; thread manager; live cycle log.
- VV rate limiter; VV-gated routing; local messaging; message delete; two-step removal.
- 403 / quota Provider alerts to the Primary User (see `state_provider_alerts.md`).
- **Live voice calls** — COA calls the Primary User in the VersaVoice app (OpenAI GPT-Live voice; the harness answers every request with COA's tools). Ringing in foreground and background, missed calls, transcript + summary in `messages.db calls`, agitop Settings → Live Call. Setup: [README](../README.md#optional-live-voice-calls). Call length 5 / 10 / 15 / 25 min with a spoken 5-minute notice.
- **Live chat** — after a person writes, the agent's cycle stays open and answers new messages within seconds until the chat is quiet (agitop Settings → Live Chat: Off / 5 / 10 / 15 / 30 min, default 15). Primary User first; several people per agent; at most 5 listening agents per Primary User; due tasks wait; graceful **End chat** (agitop or `agictl agent chat-close`). Setup: [README](../README.md#live-chat).
- **Agent status and activity in VersaVoice** — every cycle reports start and end; chat status line (resting / waking / working / here); status icon and ACTIVITY on agent cards (all-time cycles, 24-hour busy graph, outcomes, last 20 cycles), with past cycles filled in once from the machine's history.

### Operations

- `versa-agi-backup` / restore; `agictl system set-key` + API Keys modal.
- Skills hardening; local concurrency gate; database vacuum; system package registry.

Catalog layers (shipped / presets / live / site overlays) shipped 2026-08. Operator page: [Models](models.md).
