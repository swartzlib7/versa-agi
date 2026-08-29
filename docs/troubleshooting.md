# Troubleshooting

## Deleted VersaVoice sub-account

Local data (tasks, memory, workspace) is preserved. Re-provision:

```bash
sudo agictl identity provision <agent_name> \
  --token "<VV_API_TOKEN>" \
  --first-name "<First>" --last-name "<Last>" \
  --language en --country "United States" --voice female
```

Accept the new connection request in the VersaVoice app afterward.

## System package requests

```bash
agictl pkg list
sudo agictl pkg approve <name>
agictl pkg install <name>
sudo agictl pkg deny <name>
sudo agictl pkg remove <name>
```

Or agitop → Settings → System Packages.

## Agent not spawning

```bash
agictl agent show <name>
agictl task count-frozen <name>
sudo rm -f /tmp/versa_agi_<name>.cooldown /tmp/versa_agi_<name>.lock
agictl task unfreeze-all <name>
```

COA with an empty model after a fresh install is **held** until first-login assign — see [Models](models.md). That is not a circuit breaker.

## Circuit breaker

5+ consecutive failures or 20+ failures in 60 minutes:

```bash
agictl agent show <name>
agictl agent activate <name>
```

Thresholds: `setup.ini` `[agent]` or agitop Settings.

## Agent halted

```bash
agictl agent show <name>
agictl agent activate <name>
```

Or agitop → agent → Re-activate.

## Emergency stop

```bash
sudo agictl agent kill <name>
sudo pkill -u <agent_os_user>
sudo pkill -u coa
```

## `versa-agi-uninstall` not found

Interrupted install before tooling was persisted:

```bash
git clone https://github.com/swartzlib7/versa-agi.git /tmp/versa-agi-fix
sudo bash /tmp/versa-agi-fix/src/uninstall.sh [--purge]
rm -rf /tmp/versa-agi-fix
```

## Related

- [Credentials](credentials.md)
- [Models](models.md)
- [Backup and restore](backup-restore.md)
