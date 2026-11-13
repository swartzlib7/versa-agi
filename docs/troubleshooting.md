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

Or agitop → Settings → System Packages. Or VersaVoice → Versa AGi → Agents → the agent → Packages (Requested / Approved / Denied). Decisions land on the next inbox pulse or **Retrieve Messages & Sync**; **Sync to VV** is the PU schedule when inbox does not run. If a just-made VV approval has not landed, COA may check `agictl system sync-instance --status` and run the full command **once** — not on a timer.

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

Which stopped cycles count as failures:

| How the cycle ended | Counts toward the circuit breaker |
|---|---|
| Error or crash | Yes |
| Stall: no model reply | Yes |
| The same tool call repeated | Yes |
| Runaway output | Yes |
| Closed by the agent | No |
| Step limit | No |
| Time limit | No |

`agictl agent activate <name>` also clears the failed cycles that tripped the breaker, so it does not trip again at once.

## A cycle was stopped

The reason is in the Lifeline log (`/var/log/versa-agi-lifeline.log`) and in agitop → agent → **Cycle Logs**.

| Log line | What happened | What the system does next | What to do |
|---|---|---|---|
| `LIMIT: … time limit reached` | The cycle reached the agent's time limit. It was closed with a summary and a handoff. | The agent wakes on the next pulse and carries on in the same thread. | Nothing. If long work hits the limit every cycle, raise it (below). |
| `TIMEOUT: … stopped by the backstop` | One model call was still running 5 minutes after the time limit. | The cycle is stopped and its record closed with a note. The agent wakes again when work is due. | If it repeats, check that the model server or provider is answering. |
| `STALL:` | The model gave no reply for 10 minutes (20 minutes on a local model). | The cycle is stopped. Tasks are not frozen. The agent wakes again when work is due. | Check that the model server or provider is answering. Five stalls in a row trip the circuit breaker. |
| `LOOP:` | The agent made the same tool call and got the same result 6 times. It was told at the third. | The cycle is closed with a summary and a handoff. Tasks are not frozen. | Open the cycle in **Cycle Logs** and read what was blocked. |
| `RUNAWAY:` then `HALTED:` | The cycle wrote more output in 15 minutes than the agent's runaway thresholds allow (2500 lines or 2048 KB by default). | The cycle is stopped, all of the agent's tasks are frozen, and the Primary User is told. | Read the cycle log, then `agictl task unfreeze-all <name>`. |

Time limit: 30 to 240 minutes per agent, 45 by default.

- agitop → agent → **Technical** → **Time limit**, then **Save**.
- Or `sudo agictl agent set-timeout <name> <minutes>`.

The agent is warned at 15 minutes and at 8 minutes before the limit, earlier when its steps are slow. Every 30 minutes of a cycle a `[CHECK-IN]` line is written to the cycle log, and the agent is asked to record its task progress.

## Reset one agent's history

Use this when an agent's old conversation or cycle history is getting in its way and you want it to start clean without losing its lined-up work. Do it while that agent is not in a cycle.

1. agitop → agent → **Threads** → **Drain All**.
2. agitop → agent → **Cycle Logs** → **Purge All Logs**.
3. agitop → **System** → **VACUUM**.

| Step | Deletes | Keeps |
|---|---|---|
| **Drain Selected** | That thread's saved conversation and its summary frames. | The agent's other threads, its cycle history, tasks, memory. |
| **Drain All** | Every thread of that agent and all of its summary frames. | Its cycle history, tasks, memory. Other agents. |
| **Purge All Logs** | That agent's cycle log files and its cycle history: summaries, handoffs, token counts, exit codes. A cycle that is running keeps its record. | Threads, summary frames, tasks, memory. Other agents' history. |
| **VACUUM** | Nothing. It gives the freed disk space back. | Everything. |

Tasks, memory, awareness, messages, and the agent's instructions stay through all three steps.

Draining deletes summary frames for good. The agent can no longer search them. Purging deletes that agent's cycle history for good: its token totals restart, and its next wake starts without a note about its last cycle. Neither can be undone. Run `sudo versa-agi-backup` first if you may want them back.

## Web search returns nothing

`agictl search web "<query>"` lists the engines that did not answer and why (`unresponsive_engines`). Public search pages block automated readers from time to time; a block on one engine clears on its own.

- Run `agictl search status`. It shows which engines answer a test search and why the others did not.
- Run `sudo versa-agi-update`. It refreshes SearXNG and prints a `Test search:` line with the engines that answered.
- Check the container: `docker ps -f name=searxng`, `docker logs searxng`.
- For a source that is not blocked, add a Brave or Exa API key in agitop → **API Keys**: [Credentials](credentials.md).
- Turning web search off and on in agitop Settings changes the setting only. It does not restart SearXNG.

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
