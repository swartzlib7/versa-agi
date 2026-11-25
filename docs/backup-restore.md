# Backup and restore

Uninstall one-liners stay on the [README](../README.md#uninstall). This page is the full backup / restore runbook.

## Backup

```bash
sudo versa-agi-backup
sudo versa-agi-backup --dry-run
sudo versa-agi-backup --output /path/to/backup.tar.gz
```

Archives land under `~/.versa-agi-backups/` unless `--output` is set. The archive captures databases, agent workspaces, and configuration. System binaries, CRON, sudoers, and Python venvs are excluded — restore re-provisions those.

Each archive embeds `restore.sh` and `manifest.json`.

## The backup an update makes

Separate from the archive above. Every `versa-agi-update` (or `setup.sh --update`) makes one before it deploys:

- **Where:** `/var/backups/versa-agi/update-<date and time>/`, readable by root only. It is not inside any account's folder, so it is not part of the archive above and agents cannot reach it.
- **What:** `core-infra/` (the code), `data/` (the databases and state from `/var/lib/versa-agi/`), `etc/` (settings and keys from `/etc/versa-agi/`), and `homes/<user>/` for the Chief Orchestrator Agent and every sub-agent. Caches, downloaded models and installed dependencies are left out, as in the archive.
- **How many:** one. Each update replaces the previous backup after the new one is complete.
- **Disk:** the room needed is measured first. If the disk is too full, the update goes ahead without a new backup, the earlier one stays, and you get a message from Lifeline with the numbers. Free some space and update again to get a current backup.

To put the code back after an update (the update prints these lines with the real folder name):

```bash
sudo rsync -a /var/backups/versa-agi/update-<date and time>/core-infra/ /home/watchdog/core-infra/
sudo rsync -a /var/backups/versa-agi/update-<date and time>/homes/coa/coa-env/ /home/coa/coa-env/
```

Putting `data/` back is your decision: an update can change how the databases are laid out, so data from before the update belongs with the code from before it.

## Restore

```bash
sudo mkdir -p /tmp/restore
sudo tar -xzpf versa-agi-backup-*.tar.gz -C /tmp/restore && cd /tmp/restore
sudo ./restore.sh
sudo ./restore.sh --dry-run
sudo ./restore.sh --yes
```

Two phases:

1. **Data overlay** — databases, workspaces, and configuration back to original paths.
2. **Clean provisioning** — `setup.sh` (automatic with `--yes`, or run as instructed) re-creates OS users, groups, CRON, sudoers, venvs, and applies the permission model.

```bash
sudo bash /path/to/versa-agi/src/audit_permissions.sh
```

## Uninstall reminder

```bash
sudo versa-agi-uninstall
sudo versa-agi-uninstall --purge
sudo versa-agi-uninstall --dry-run
```

`--purge` is irreversible. Run `sudo versa-agi-backup` first.
