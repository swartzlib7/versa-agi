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
| `STALL:` | The model gave no reply for 10 minutes (20 minutes on a local model). A reply that is streamed counts from its last piece, so a long reply that is still arriving is not a stall. | The cycle is stopped. Tasks are not frozen. The agent wakes again when work is due. | Check that the model server or provider is answering. Five stalls in a row trip the circuit breaker. |
| `CACHE KEEP-ALIVE: off for this cycle` (cycle log) | While a chat was waiting, a request that keeps a Claude model's cached price alive failed, or found nothing cached. | The chat carries on. The next message costs more, once. Keep-alive starts again in the next cycle. | Nothing. If it appears in every chat, check the provider's status, and through OpenRouter the account's privacy setting. |
| `LOOP:` | The agent made the same tool call and got the same result 6 times. It was told at the third. | The cycle is closed with a summary and a handoff. Tasks are not frozen. | Open the cycle in **Cycle Logs** and read what was blocked. |
| `RUNAWAY:` then `HALTED:` | The cycle wrote more output in 15 minutes than the agent's runaway thresholds allow (2500 lines or 2048 KB by default). | The cycle is stopped, all of the agent's tasks are frozen, and the Primary User is told. | Read the cycle log, then `agictl task unfreeze-all <name>`. |

Time limit: 30 to 240 minutes per agent, 90 by default on a new installation (45 before 3.9.0).

- agitop → agent → **Technical** → **Time limit**, then **Save**.
- Or `sudo agictl agent set-timeout <name> <minutes>`.
- Or ask your Chief Orchestrator Agent to change it.

The new limit holds from the agent's next cycle.

The agent is warned at 15 minutes and at 8 minutes before the limit, earlier when its steps are slow. Every 30 minutes of a cycle a `[CHECK-IN]` line is written to the cycle log, and the agent is asked to record its task progress.

## A model is refused through OpenRouter (404, ZDR)

The cycle log shows `404 … 0 endpoints … ZDR violation (account settings)`.

Your OpenRouter account is set to use only endpoints with zero data retention, and the model has none. Claude Fable 5.2 requires 30-day retention at Anthropic, so it is always refused under that setting. Claude Opus 5.5 is refused the same way when it searches the web; for that case Versa AGi asks once more without the search and leaves it off for the cycle (`WEB SEARCH: off for this cycle` in the cycle log), so the agent still answers.

1. Open `https://openrouter.ai/settings/privacy` and allow endpoints that retain data, or
2. Assign the model's own-API key instead (`claude-fable-5-2` with an Anthropic key), or
3. Turn the model's web search off (`sudo agictl model params set model:<catalog_key> --web-search off`).

## A GPT model fails with "Function tools with reasoning_effort are not supported"

The cycle log shows `400 … Function tools with reasoning_effort are not supported for <model> in /v1/chat/completions`.

The model is on an OpenAI key and is being called on OpenAI's older endpoint, which takes tools only with reasoning off. From 3.9.0, GPT-5.6 and later use the newer endpoint by rule.

1. Update to 3.9.0 or later (`sudo ./setup.sh --update`).
2. If the model was imported under a name that does not start with `gpt-` and a version, set the rule on its row: `sudo agictl model params set model:<catalog_key> --traits '{"api":"responses"}'`.
3. Check the result: `sudo agictl model params get model:<catalog_key>` shows `"api": "responses"` under `traits`.

## The model declined a request

The cycle log shows `[REFUSAL] <model> declined a reply`. The provider's safety check stopped the reply.

1. The declined reply is discarded and the agent is told. It can carry on another way, or tell whoever asked that the request was declined, and end the cycle.
2. If the model declines again straight away, the log shows `REFUSAL CLOSED:`. The cycle is closed with a summary that names the model and the reason, the agent waits 15 minutes before its next cycle, and you get one message per agent per day. It does not count toward the circuit breaker. To end the wait sooner: agitop → System Settings → General → **Lift**.

Read the request in the cycle log. If the work is legitimate, rephrase it or assign another model to the agent. A request that asks a Claude model to recount its own earlier reasoning can be declined; ask for the result or for new work. On Anthropic's own API the reason is shown as `refusal` without a category; through OpenRouter it is `content_filter`.

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

## A task ran at the wrong hour

Due dates are read and shown in this machine's time zone. A task that an agent created before version 3.8.1, with a plain local time, was stored early by the machine's offset from UTC: five hours in US Eastern time.

- Open the task in agitop → **Tasks**. The **Due** column is local time. Set the time you meant and save.
- Or ask COA to check its open tasks' due times. The update note it received asks for this.

## Web search returns nothing

`agictl search web "<query>"` lists the engines that did not answer and why (`unresponsive_engines`). Public search pages block automated readers from time to time; a block on one engine clears on its own.

- Run `agictl search status`. It shows which engines answer a test search and why the others did not.
- Run `sudo versa-agi-update`. It refreshes SearXNG and prints a `Test search:` line with the engines that answered.
- Check the container: `docker ps -f name=searxng`, `docker logs searxng`.
- For a source that is not blocked, add a Brave or Exa API key in agitop → **API Keys**: [Credentials](credentials.md).
- Turning web search off and on in agitop Settings changes the setting only. It does not restart SearXNG.

## An agent puts code in the text of a message

Agents are told to attach code, commands and logs as a document and keep the message text plain. In the VersaVoice AI app you open the attachment to select, copy or save it.

If an agent still writes code into the text, tell it once in a message. Smaller models are more likely to do this. If it keeps happening, check that the install is on 3.9.2 or newer (`agictl --version`): versions up to 3.9.1 named the attachment flag wrongly in the agent's instructions.

## An agent says it cannot attach a file

An agent can attach pictures, text documents, links and files: PDF, Word, Excel, CSV, JSON, INI, TXT, Markdown or ZIP, up to 20 MB each. It cannot attach audio, video, programs or other kinds of archive. For those, open the file in the agent's workspace on the machine (`~/agi-workspace` on a Normal install).

Sending files needs Versa AGi 3.9.2 or newer (`agictl --version`). Older versions upload pictures only.

A message is only sent when every attachment it names could be attached, so you do not get "see attached" with nothing there.

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
