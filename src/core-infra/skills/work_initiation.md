# Work Initiation Skill

> **Trigger**: The moment you are asked to do something by the Primary User or another connection.

> **Harness tools:** Examples use shell form (`agictl group …`). In a work cycle, call the matching tool (`agictl_task`, `agictl_cycle`, …) and pass only the part **after** `agictl` as the `command` argument. Never prefix `agictl` in the argument. Full map: **cli_reference_agent.md** (*Harness tool invocation*).

## Purpose

Ensure that all work is correctly targeted at a project, a new project is registered if needed, or it is explicitly identified as temporary/disposable work.

## Procedure

### Step 1: Analyze the Request

When you receive a new instruction or task, immediately determine the scope of the work:

1.  **Does this work belong to an existing project?**
    *   Check `agictl project list` for active projects.
    *   If a project matches the scope (e.g., "Fix a bug in the website" matches a "website" project), target that project.
2.  **Is this a significant new piece of work?**
    *   If it is a new development effort, a new study, or a multi-step task that doesn't fit existing projects, **register a new project**.
    *   Follow the **`project_management.md`** skill for onboarding.
3.  **Is this temporary or disposable work?**
    *   If the request is a simple query, a quick test, or a one-off task that doesn't require persistence or long-term tracking (e.g., "Tell me the time", "Check if a file exists"), treat it as disposable.
    *   Perform the work in a temporary location or in your root workspace if appropriate, and clean up afterwards if necessary.

### Step 2: Set the Context

Before beginning execution:

*   If using an existing project: `agictl agent status set active "Working on project: [project_name]"`
*   If registering a new project: Follow onboarding and then update status.
*   If disposable: `agictl agent status set active "Processing temporary request"`

## Core Directive

**Generally, all work will end up being in a project somewhere**, unless it is temporary stuff that you are doing just for the moment that you can clean up afterwards—that's disposable. **Always determine which project to target the work at, or creating a new project.**
