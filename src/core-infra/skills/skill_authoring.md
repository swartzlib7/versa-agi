# Skill Authoring

> **Scope:** COA-exclusive — this skill is never deployed to sub-agents.

## Purpose

This skill governs the skill lifecycle: creating, overriding, distributing, and withdrawing skills across the agent team. You (COA) are the sole custodian of the skills system.

## Skill Lifecycle

Skills follow a DB-driven lifecycle tracked in `agents.db`:

```
draft → ready → synced → updated → synced (re-sync loop)
```

| Status | Meaning |
|--------|---------|
| `draft` | Skill created, being authored. Not distributed. |
| `ready` | Authored and approved. Lifeline will deploy on next tick. |
| `synced` | Successfully deployed to all sub-agents. |
| `updated` | Content changed. Lifeline will re-deploy on next tick. |

## Creating Skills

COA/PU only. Sub-agents request a skill via a message or task to COA; they do not run `skill new`. Use `agictl skill new` to scaffold. Agent-facing skills must include the **Harness tools** blockquote (see `skills/README.md`). Command examples stay in shell form (`agictl task list`); agents translate to harness tools at runtime.

```bash
agictl skill new my_skill --description "What this skill does" --scope all
agictl skill new my_skill --description "What this skill does" --scope all --created-by web-dev
```

Options:
- `--scope all` (default) — Deployed to all agents (COA + sub-agents) when status is `ready`.
- `--scope coa_only` — COA-exclusive. Never deployed to sub-agents.
- `--created-by NAME` (default `coa`) — Sets `origin` so `skill list` shows who asked. The file still lives on COA.

This creates:
1. `.agent/skills/my_skill.md` — The skill template (draft status).
2. `.agent/skills/my_skill/` — Co-located asset directory for scripts/templates.

After authoring the skill content, mark it ready for distribution:

```bash
agictl skill status my_skill ready
```

Lifeline deploys it to all sub-agents on the next tick and sets status to `synced`.

## Overriding Shipped Skills

To customize a shipped skill without modifying the source:

```bash
sudo agictl skill override communication
```

This creates `communication_override.md` in your skills directory, pre-populated with the shipped content as a template. Edit the override, then mark it ready:

```bash
agictl skill status communication_override ready
```

**How overrides work:**
- The harness checks for `{name}_override.md` before injecting `{name}.md`.
- The override applies only on a disk that has `{name}_override.md`.
- Overrides are not in the watchdog shipped source; they do not rsync from `core-infra/skills/`.

## Withdrawing Skills and Overrides

```bash
agictl skill remove my_skill
agictl skill remove communication_override
```

`skill remove` deletes the registry row. For `agent_created` or `override` on COA, it also deletes the file and asset directory. Next `deploy-skills` retracts fleet copies of retracted shares (`rsync --delete`).

`skill register` and `--update` reconcile also prune non-shipped rows whose COA `.md` is already gone.

## Updating Existing Skills

To redistribute a skill after changes:

```bash
agictl skill status my_skill updated
```

Lifeline re-syncs it on the next tick.

## Viewing Skills

```bash
agictl skill list              # Table view with scope column
agictl skill list --json-output # JSON for programmatic use
agictl skill list -s draft     # Filter by status
```

## Scope Rules

| Scope | Who gets it | Use case |
|-------|-------------|----------|
| `all` | COA + all sub-agents | General skills (communication, git, tasks) |
| `coa_only` | COA only (on disk) | Administrative skills — **`cli_reference.md`** (load on demand), **`skill_authoring.md`** (always injected for COA), **`versa_agi_operations_guide.md`** (triage-selected for COA), **`business_admin.md`** (triage-selected for COA when Versa - Business Admin is ON; install/enable only — fleet API work is **`business_admin_operate`**) |

Sub-agents never see COA-only skills — they are excluded from:
1. The triage catalog (sub-agents can't even request them).
2. The rsync deploy (filtered via `--exclude`).
3. Auto-injection (`cli_reference.md` is never injected; COA loads it on demand).

## Key Commands

| Command | Purpose |
|---------|---------|
| `agictl skill new NAME [--created-by AGENT]` | Create a new skill (COA/PU; file on COA) |
| `agictl skill override NAME` | Create override of shipped skill (COA/PU) |
| `agictl skill remove NAME` | Delete registry row; retract agent_created/override files |
| `agictl skill status NAME ready` | Mark for distribution |
| `agictl skill status NAME updated` | Mark for re-sync |
| `agictl skill list` | View all skills |
| `agictl skill register` | Bootstrap DB from filesystem; prune missing non-shipped rows |
| `sudo agictl agent deploy-skills AGENT` | Force deploy to specific agent |

## Notes

- Skill files must be `.md` format.
- Asset directories share the skill's base name (e.g., `my_skill/` for `my_skill.md`).
- Skills are deployed as `watchdog:agi_agents 440` (read-only for agents).
- Use `agictl skill register` after manual file additions to sync the DB.
