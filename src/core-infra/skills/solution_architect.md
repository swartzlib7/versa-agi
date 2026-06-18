# Solution Architect

> **Trigger:** Primary User (PU) needs a development environment or software stack configured on the host system.

> **Harness tools:** Examples use shell form (`agictl group …`). In a work cycle, call the matching tool (`agictl_task`, `agictl_cycle`, …) and pass only the part **after** `agictl` as the `command` argument. Never prefix `agictl` in the argument. Full map: **cli_reference_agent.md** (*Harness tool invocation*).

## Purpose

Guide the PU through safe, non-containerized environment configuration on Ubuntu 24.04. Generate self-contained bash installation scripts that the PU (or COA in autonomous mode) can execute with `sudo` to set up development stacks, runtime environments, and supporting services.

## Differentiation from Requirements Elicitation (5W1H)

| Aspect | Requirements Elicitation | Solution Architect |
|---|---|---|
| **Focus** | **What to build** — scope, motivation, actors | **How to set up the environment** — stack, packages, configs |
| **Trigger** | New work request with missing dimensions | PU needs a development environment prepared |
| **Output** | Validated requirements → WBS | Self-contained bash install script |
| **5W1H role** | Uses 5W1H to clarify ambiguity | Assumes requirements are clear, focuses on compatibility paths |

## Workflow

### 1. Confirm Host Assumptions
Before generating any script, verify:
- **OS**: Ubuntu 24.04 LTS (unless PU specifies otherwise)
- **Architecture**: x86_64 or aarch64
- **Existing packages**: Check for conflicts with installed software
- **Available resources**: Disk space, memory, ports in use

### 2. Gather PU Requirements
Collect specific details about the desired stack:
- **Runtime**: Language, version (e.g., Node.js 22, Python 3.12, Go 1.22)
- **Database**: Type, version (e.g., PostgreSQL 16, Redis 7, MongoDB 7)
- **Services**: Web servers, message queues, caches
- **Ports**: Required port allocations
- **User/Group**: Which OS user the stack should run under

### 3. Research Compatibility Paths
Use `agictl search web` (when available) to:
- Verify version compatibility between stack components
- Find official installation methods (apt repos, PPAs, official scripts)
- Check known issues with Ubuntu 24.04 and selected versions

### 4. Generate Installation Script
Produce a **self-contained bash script** following these standards:
- Start with `#!/bin/bash` and `set -euo pipefail`
- Include a header comment block documenting: purpose, date, stack versions, PU name
- Check prerequisites before installing
- Use **established methods** in this priority order:
  1. Official apt repositories / PPAs
  2. Official vendor install scripts (e.g., `nvm`, `rustup`)
  3. `snap` for sandboxed applications
  4. `pip` in virtual environments (never system-wide)
  5. Manual download + verification (checksum validation)
- Create `systemd` service files where applicable
- Set appropriate file permissions and ownership
- Include health checks after each major install step
- Include a **rollback/uninstall section** at the bottom (commented out)
- Store the script in the skill's asset directory: `.agent/skills/solution_architect/generated/`

### 5. Present for Review
Before execution:
- Show the complete script to the PU via message
- Explain each section and any assumptions made
- Wait for PU approval before execution

### 6. Execution
- **Standard mode**: PU runs the script manually with `sudo`
- **Autonomous mode** (`coa_autonomous=true`): COA executes directly via `sudo bash <script>`
- Store generated scripts for future reference and reuse

## Script Template

Reference the template at `.agent/skills/solution_architect/templates/install_script_template.sh` for the standard script structure.

## Anti-Patterns

- **NEVER** add users to the `docker` group — this grants root-equivalent access
- **NEVER** install packages system-wide with `pip` — always use virtual environments
- **NEVER** use `curl | bash` without first downloading and inspecting the script
- **NEVER** disable SELinux/AppArmor without explicit PU approval
- **NEVER** open firewall ports without documenting which service uses them

## Notes

- Generated scripts should be idempotent — safe to run multiple times
- Version pinning is preferred over "latest" to ensure reproducibility
- When multiple valid installation methods exist, document the trade-offs
