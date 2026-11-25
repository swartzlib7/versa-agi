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
| Web search | When enabled — local SearXNG via `agictl search web`. Queries go to public search engines, and to the Brave or Exa API when you add a key. `agictl search read` downloads a page an agent names (`http` / `https` only) |
| Browser | When enabled — headless Chromium via `agictl browser` |
| REST comms | VersaVoice via `agictl` |
| Read system skills | Read-only shipped skills |

## What agents cannot do

| Area | Why |
|------|-----|
| `sudo` anything except `agictl` | Sudoers scoped to the gateway. **Exception:** when the Primary User enables **COA Autonomous Mode**, COA gets `NOPASSWD: ALL` and the work-cycle sudo block is lifted for that COA only. |
| Install system packages | No package-manager sudo |
| Write a database | Every database is read-only to agent accounts. Only `watchdog` writes, through `agictl`. This holds for the lead agent as for the others |
| Pass as the Primary User to `agictl` | `agictl` takes its caller from sudo's own record, not from anything the caller sets. The sudo rule lets an agent pass no variables of its own |
| Read or change another agent's files | Each agent's home is closed to the other agents. The lead agent can enter, because it governs the team. The shared workspace (AGi-Tools and shared projects) is open to every agent by design |
| Read another agent's prompt | A cycle's prompt, context and environment files are in that agent's own data folder and removed when the cycle ends |
| Modify the monitoring layer | POSIX ownership |
| Modify Poise templates | Read-only `/etc/versa-agi/poise/` — behavior is composed at spawn from DB + role + system vars |
| Read raw cycle stdout/stderr | Archives owned by `watchdog`. Structured memory/awareness via `agictl` is allowed |
| Modify system skills | Deployed read-only |
| Tamper with the Data Gateway | Root-owned |
| Read the Primary User's VersaVoice API token | The files that hold it (the settings file and each agent's config) are readable by the system account only. An agent's process is given a copy of the settings with every key and token blanked. Lifeline and the link service pass the token in the environment, never as a command argument, and never to an agent. `agictl system config get` shows an agent `[hidden]` in place of every key and token, and an agent cannot change its VersaVoice account or who its Primary User is with `config set` |
| Read the provider keys from a file | Same files, same rule. A cycle is still given the provider keys in its own environment, because its own process calls the model |
| Be handed a file outside its attachments folder | A received file keeps only its plain file name and is fetched from an `https` address only |

If the agent needs a system package (`imagemagick`, `ffmpeg`), it **requests** it; the Primary User (or package registry) installs it.

## Escalation you grant

OS boundaries only protect what you have not given.

**COA Autonomous Mode** (`[coa] autonomous=true` plus `/etc/sudoers.d/versa_agi_coa_autonomous`) is root-equivalent for the `coa` OS user. Enable only on hardware you have dedicated to this system. The Primary User enables the grant; COA may disarm it (`set-ini coa autonomous false`) but cannot enable it. Sub-agents never receive the grant.

**Docker is root-equivalent.** Adding an agent to the `docker` group lets that agent mount the host (`docker run -v /:/host`) and reach other workspaces, the monitoring layer, and credentials. Docker documents this as equivalent to root.

**Safer isolated workloads:** Vagrant + Ansible on VirtualBox. Root inside the VM stays inside the VM.

**IDE mode** attaches the Primary User over loopback SSH as `coa`. The harness privilege-escalation guard (`agictl_execute` substring block) is not in that path — sudoers remains the boundary. `on`/`off` require root; `status` is readable by COA so it can stand down when the mode is off. See [IDE mode](ide-mode.md).

## Related

- [Directories](directories.md)
- [Troubleshooting](troubleshooting.md)
