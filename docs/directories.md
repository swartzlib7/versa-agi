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
| Update backup | `/var/backups/versa-agi/update-<date and time>/` | The one backup an update makes before it deploys: code, databases, settings and every agent's folder. Root only. Replaced by the next update. See [Backup and restore](backup-restore.md) |
| Versions | `src/core-infra/config/shipped_tools.ini` (in the source tree) | Every program and package an install or update puts on the machine, with the version shipped: rclone and the Python packages pinned, operating-system packages recorded, and what is taken at its newest version on purpose |
| Tools | `/usr/local/bin/rclone`, `fuse3` | Cloud storage for agents. Setup installs rclone at the version Versa AGi ships (checked against rclone's published checksum first) and the operating system's FUSE package. A machine with no network at update time skips this with a warning. Uninstall leaves both in place |
| Security config | `/etc/versa-agi/` | setup.ini (deployed), poise, vault, credentials |

On each install/update, the product README hub and the operator pages in this folder are copied to the COA workspace for on-demand consult:

`/home/coa/coa-env/.agent/docs/` — `versa_agi_readme.md` plus these section files (read-only; overwritten every update). Sub-agents do not receive them.

## Related

- [Security](security.md)
- [README hub](../README.md)
