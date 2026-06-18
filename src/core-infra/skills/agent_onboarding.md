# Sub-Agent Onboarding

> **Harness tools:** Examples use shell form (`agictl group …`). In a work cycle, call the matching tool (`agictl_task`, `agictl_cycle`, …) and pass only the part **after** `agictl` as the `command` argument. Never prefix `agictl` in the argument. Full map: **cli_reference_agent.md** (*Harness tool invocation*).

**Trigger:** Primary User requests a new agent, COA proposes an agent, or a `hire_agent` task is due.

## Pre-Flight Check

1. `agictl agent list --all` — verify no agent already exists for this purpose
2. **One pending at a time:** If any inactive (unapproved) sub-agent exists, `agictl agent add` will **block**. Wait for the Primary User to approve or remove it before creating another.

## Available Roles

Run `agictl agent list-roles` to see the deployed role registry. Each role includes:
- **Role ID** — used with `--role` flag
- **Name** — human-readable label
- **Description** — role purpose
- **Model** — per-role Gemini model override (empty = system default)

## Provisioning Flow

1. **Register the agent:** Run `agictl agent add <name> --role <role_id>`. This creates a **database record only** (inactive, pending approval). No OS user or directories are created yet. **You (COA) can run this command.**

2. **Notify Primary User:** Send a message explaining:
   - Agent name and role
   - Purpose / justification
   - Ask them to approve via the **agitop dashboard** (Approve & Provision button)

3. **Primary User approves via dashboard:** The agitop dashboard provisions the OS user, home directory, poise, and activates the agent for Lifeline spawning. **This step requires the Primary User — you cannot approve agents.**

4. **Post-provisioning (your responsibility):**
   - **Define duties:** Author a duties markdown file, then run `sudo agictl agent set-duties <name> <file>` to provision it
   - **Skills:** Skills are automatically deployed to sub-agents by Lifeline via the DB-driven sync pipeline (`rsync --delete`). Use `agictl skill new` to create custom skills and `agictl skill status` to distribute them. Use `sudo agictl agent deploy-skills <name>` to force an immediate sync.
   - **SSH key:** The agent's SSH keypair was auto-generated at provisioning. For the first git project, deliver the public key to the Primary User (see `git_operations.md`)
   - **Assign to project:** Use `agictl project assign <id> --agent <name>` if applicable (resolve `<id>` via `project list`)
   - **Send welcome message:** Brief the new agent with orientation context

5. **Verify:** `agictl agent list --all` — the agent should show as active. Lifeline begins spawning on its next tick.

> **CRITICAL:** Sub-agents get their own OS user at `/home/agi-{name}/` — **NEVER** under `workspace/` (which is for project repos).

## What You Can Manage

- **`duties.md`** — mutable assignment brief. Defines what the agent works on.
- **Skills** — Use `agictl skill new`, `agictl skill override`, and `agictl skill status` to manage skills. See the `skill_authoring` skill for full details.

## What You Cannot Do

- **Create OS users or directories** — requires root privileges (handled by dashboard approval).
- **Approve agents** — Primary User only, via agitop dashboard.
- **Modify `poise.md`** — immutable behavioral framework (watchdog-owned, read-only).

## Multiple Agents Per Role

The agent `name` is unique, but any number of agents can share the same role (e.g., `dev-alpha`, `dev-bravo` both with `--role dev`).

> **Do NOT** manually create directories, write poise files, or insert DB records for this work. Use `agictl agent add` for registration and wait for dashboard approval for provisioning.
