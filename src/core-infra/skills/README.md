# Skills Directory

This directory contains skill files and co-located asset directories that define agent behaviors and capabilities.

## Directory Structure

```
skills/
├── README.md                       ← This file
├── communication.md                ← Shipped skill (read-only, 440)
├── solution_architect.md           ← Shipped skill (read-only, 440)
├── solution_architect/             ← Co-located asset directory (755)
│   ├── README.md
│   └── templates/
│       └── install_script_template.sh
├── git_operations.md
└── ...
```

## Skill Types

| Type | Permissions | Description |
|---|---|---|
| **Shipped (system)** | `440` (read-only) | Deployed by `setup.sh`. Cannot be modified by agents. |
| **Agent-Created** | `644` (writable) | Created by COA via `agictl skill new`. COA manages lifecycle. |

## Asset Directories

Each skill can have a **co-located asset directory** with the same name (without `.md`). This directory contains scripts, templates, and reference data that the skill references.

- Asset directories are copied alongside skill `.md` files during deployment.
- Sub-agents receive read-only copies (`755 {agent}:agi_agents`).
- COA can create and modify asset directories for skills she manages.

## Status Lifecycle (DB-driven)

Skills are tracked in the `skills` table in `agents.db`. Lifeline distributes skills based on their status:

```
draft  →  ready  →  synced  →  updated  →  synced
  ↑         ↑         ↑          ↑           ↑
  |         |         |          |           |
  |         |         |          |           Lifeline re-syncs
  |         |         |          COA modifies and confirms
  |         |         Lifeline deployed to all sub-agents
  |         COA completed the template
  agictl skill new <name>
```

## COA as Skills Custodian

COA manages all skills for the team:

1. **Create**: `agictl skill new <name>` → creates `.md` template + asset directory + DB row (`draft`)
2. **Complete**: COA fills in the template and adds assets
3. **Activate**: `agictl skill status <name> ready` → Lifeline distributes on next tick
4. **Update**: Modify the skill, then `agictl skill status <name> updated` → Lifeline re-syncs
5. **Monitor**: `agictl skill list` → system posture at a glance

## Agent-facing skills and harness tools

Skills deployed to sub-agents show `agictl` commands in **shell notation** for readability. Agents invoke them through **harness tools** (`agictl_task`, `agictl_cycle`, …) with the subcommand only — see `cli_reference_agent.md` (*Harness tool invocation*).

When authoring or editing any agent-facing skill, include this blockquote near the top (after the title or trigger line):

```markdown
> **Harness tools:** Examples use shell form (`agictl group …`). In a work cycle, call the matching tool (`agictl_task`, `agictl_cycle`, …) and pass only the part **after** `agictl` as the `command` argument. Never prefix `agictl` in the argument. Full map: **cli_reference_agent.md** (*Harness tool invocation*).
```

COA-only skills (e.g. this file) may omit the blockquote if they are never distributed to sub-agents.

## Differentiation: Requirements Elicitation vs Solution Architect

| Aspect | Requirements Elicitation | Solution Architect |
|---|---|---|
| **Focus** | **What to build** (5W1H) | **How to set up the environment** |
| **Output** | Validated requirements → WBS | Self-contained bash install script |
| **When** | New work with missing dimensions | PU needs stack/environment setup |
