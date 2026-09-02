# Operations

## File exchange

After setup, two home-directory symlinks:

| Symlink | Purpose |
|---------|---------|
| `~/agi-workspace` | Drop in project files, repos, or documents — the agent sees them |
| `~/agi-attachments` | Files sent to the agent via VersaVoice |

## Efficiency

**Compute-Zero:** Lifeline verifies actionable work before spawning. Idle cycles abort in under a second. You only pay for real work.

## Observability

**agitop** (`sudo agitop`) is the Mission Control dashboard: agents, messages, tasks, token usage, system health. `agictl` is the CLI for the same data.

<div align="center">
  <img src="brand/versa-agi-01.png" alt="agitop Mission Control Dashboard" width="100%">
  <br>
  <sub>agitop — Cloud mode</sub>
  <br><br>
  <img src="brand/versa-agi-02.png" alt="agitop Hybrid Mode" width="100%">
  <br>
  <sub>agitop — Hybrid with local AI</sub>
</div>

Token usage is tracked per cycle and totaled monthly in agitop.

## IDE mode

`sudo versa-agi-ide` holds COA out of Lifeline spawning and writes `.agent/versa-agi_ide.md` for a Remote-SSH session. Full runbook: [IDE mode](ide-mode.md).

## Related

- [Models](models.md)
- [Credentials](credentials.md)
- [IDE mode](ide-mode.md)
- [Troubleshooting](troubleshooting.md)
