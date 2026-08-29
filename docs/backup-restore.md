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
