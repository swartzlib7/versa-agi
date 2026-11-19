# Directory layout

Three layers: **monitoring** (Watchdog), **agent workspaces** (per-agent OS users), **centralized data** (`agictl` / SQLite).

| Layer | Path | Purpose |
|-------|------|---------|
| Primary User home | `~/.versa-agi/` | Persistent repo clone (`repo/`) + `setup.ini` symlink |
| Monitoring | `/home/watchdog/core-infra/` | Lifeline, File Monitor, agictl, agitop |
| Agent workspace | `/home/coa/coa-env/` | COA environment (sub-agents have their own homes) |
| Data | `/var/lib/versa-agi/` | SQLite databases, model config, `link_state` (whether the link to VersaVoice is up), `net_down` (present while the machine cannot reach VersaVoice), `shutdown_pending` (present while the machine is shutting down) |
| Services | `/usr/local/lib/versa-agi/versa_agi_link.py`, `versa-agi-link.service` | Link service: holds one outbound request to VersaVoice and starts a Lifeline pass for an agent the moment it has new mail |
| System settings | `/etc/polkit-1/rules.d/50-versa-agi-sleep.rules`, `/etc/systemd/logind.conf.d/50-versa-agi-lid.conf` | Sleep hold: the permission to keep the machine awake while an agent works, and (laptops, only if you answered yes) the setting that makes a shut lid wait too |
| Services | `/usr/local/lib/versa-agi/shutdown_guard.sh`, `versa-agi-shutdown-guard.service` | Shutdown guard: when the machine shuts down or restarts, agents in a cycle hand off and close before it goes down |
| Security config | `/etc/versa-agi/` | setup.ini (deployed), poise, vault, credentials |

On each install/update, the product README hub and the operator pages in this folder are copied to the COA workspace for on-demand consult:

`/home/coa/coa-env/.agent/docs/` — `versa_agi_readme.md` plus these section files (read-only; overwritten every update). Sub-agents do not receive them.

## Related

- [Security](security.md)
- [README hub](../README.md)
