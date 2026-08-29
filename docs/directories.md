# Directory layout

Three layers: **monitoring** (Watchdog), **agent workspaces** (per-agent OS users), **centralized data** (`agictl` / SQLite).

| Layer | Path | Purpose |
|-------|------|---------|
| Primary User home | `~/.versa-agi/` | Persistent repo clone (`repo/`) + `setup.ini` symlink |
| Monitoring | `/home/watchdog/core-infra/` | Lifeline, File Monitor, agictl, agitop |
| Agent workspace | `/home/coa/coa-env/` | COA environment (sub-agents have their own homes) |
| Data | `/var/lib/versa-agi/` | SQLite databases, model config |
| Security config | `/etc/versa-agi/` | setup.ini (deployed), poise, vault, credentials |

On each install/update, the product README hub and the operator pages in this folder are copied to the COA workspace for on-demand consult:

`/home/coa/coa-env/.agent/docs/` — `versa_agi_readme.md` plus these section files (read-only; overwritten every update). Sub-agents do not receive them.

## Related

- [Security](security.md)
- [README hub](../README.md)
