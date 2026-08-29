# Agent security model

Strict separation between the **agent**, the **monitoring layer** (Watchdog), and the **Primary User**. Agents build freely in their workspace; they cannot modify the infrastructure that monitors them.

## What agents can do

| Area | Freedom |
|------|---------|
| Write code | Own workspace and cloned repos |
| Create files | Anywhere in their workspace except locked dirs |
| Create skills | Extend their own capabilities |
| Git | Commit, branch, merge in workspace repos |
| npm / pip / cargo | User-level, workspace-local |
| Run scripts | Anything in their workspace |
| Network | curl, wget, API calls — no extra firewall |
| Web search | When enabled — local SearXNG via `agictl search web` |
| Browser | When enabled — headless Chromium via `agictl browser` |
| REST comms | VersaVoice via `agictl` |
| Read system skills | Read-only shipped skills |

## What agents cannot do

| Area | Why |
|------|-----|
| `sudo` anything except `agictl` | Sudoers scoped to the gateway |
| Install system packages | No package-manager sudo |
| Modify the monitoring layer | POSIX ownership |
| Modify Poise templates | Read-only `/etc/versa-agi/poise/` — behavior is composed at spawn from DB + role + system vars |
| Read raw cycle stdout/stderr | Archives owned by `watchdog`. Structured memory/awareness via `agictl` is allowed |
| Modify system skills | Deployed read-only |
| Tamper with the Data Gateway | Root-owned |

If the agent needs a system package (`imagemagick`, `ffmpeg`), it **requests** it; the Primary User (or package registry) installs it.

## Escalation you grant

OS boundaries only protect what you have not given.

**Docker is root-equivalent.** Adding an agent to the `docker` group lets that agent mount the host (`docker run -v /:/host`) and reach other workspaces, the monitoring layer, and credentials. Docker documents this as equivalent to root.

**Safer isolated workloads:** Vagrant + Ansible on VirtualBox. Root inside the VM stays inside the VM.

## Related

- [Directories](directories.md)
- [Troubleshooting](troubleshooting.md)
