# Skill: System Package Management

> **Trigger**: Use this skill when you need a system-level package (apt) installed on the server — e.g., a build tool, library, or runtime dependency. The package registry enforces an approval workflow: you request, the Primary User approves, and you install.

## How It Works

1. **You request** a package via `agictl pkg request <name> --reason "..."`.
2. **Lifeline notifies you** — on your next spawn after approval, a one-shot `PKG_NOTICE` block is injected into your system prompt listing the approved packages.
3. **You install** the approved package via `agictl pkg install <name>`.

You will never be notified twice — the notification is one-shot per approval.

## Commands (Agent-Accessible)

```bash
agictl pkg list                                       # View all registered packages and their statuses
agictl pkg request <name> --reason "..."              # Request a package for installation
agictl pkg install <name>                             # Install an approved package (approved-gate enforced)
```

## Privilege Boundaries

| Command | Who Can Run |
|:---|:---|
| `pkg list` | Any user |
| `pkg request` | Any user |
| `pkg install` | Any user (approved-gate) |
| `pkg add` | Primary User only |
| `pkg approve` | Primary User only |
| `pkg deny` | Primary User only |
| `pkg remove` | Primary User only |

> **You cannot approve, deny, add, or remove packages.** These are PU-only operations. If your request is denied, you'll receive a notification with the denial reason on your next spawn.

## Package Naming

Package names must match apt naming conventions: lowercase letters, digits, dots, hyphens, and plus signs. Invalid names are rejected at the CLI layer.

## Return Format

All commands return JSON:

```json
{
  "success": true,
  "package": "jq",
  "status": "requested"
}
```

On failure:
```json
{
  "success": false,
  "error": "Package 'jq' is not approved — current status: requested"
}
```

## Usage Patterns

### Requesting a Build Dependency
```bash
# You need jq for JSON processing in a script
agictl pkg request jq --reason "Required for JSON parsing in data pipeline scripts"
# → Status: requested. Wait for PU approval + Lifeline notification.
```

### Installing After Approval
```bash
# After receiving PKG_NOTICE in your system prompt:
agictl pkg install jq
# → Installs via apt-get (watchdog → root escalation)
```

### Checking Package Status
```bash
agictl pkg list
# → Shows all packages: requested, approved, denied
```

## Workflow Diagram

```
Agent: agictl pkg request <name>
  → DB: status = 'requested'
  → PU sees request in agitop ⚙ Settings → System Packages

PU: agictl pkg approve <name>  (or via agitop)
  → DB: status = 'approved', notified_at = NULL

Lifeline (next agent spawn):
  → Detects approved + notified_at IS NULL
  → Injects PKG_NOTICE into system prompt
  → Sets notified_at = now (one-shot)

Agent: agictl pkg install <name>
  → Validates status = 'approved'
  → Executes: sudo apt-get install -y <name>
  → Returns JSON result
```

## Important Notes

- **Do NOT use `agictl execute bash` to install packages** — `apt-get` requires root and is infrastructure-blocked. Always use the package registry.
- **One request per package** — duplicate requests for the same package name will fail.
- **Denied packages** — if denied, check the denial reason in `agictl pkg list` and discuss with the PU before re-requesting.
