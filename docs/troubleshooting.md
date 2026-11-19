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

## Messages reach an agent slowly

A message should wake a sleeping agent at once and reach an agent in a chat within a second or two. If it takes up to a minute (asleep) or up to 10 seconds (in a chat), the link to VersaVoice is down and messages are arriving by the fetch. Nothing is lost.

1. Look at the `LINK` line in agitop's System column, or `cat /var/lib/versa-agi/link_state` — `up` is healthy. `down` names the reason. `off` means Live Link is off (agitop → System Settings → Live Chat → Live Link) or no agent has a VersaVoice account.
2. `systemctl status versa-agi-link` — the service should be active. `sudo systemctl restart versa-agi-link` starts it again.
3. `grep '\[LINK\]' /var/log/versa-agi-lifeline.log | tail` — `the VersaVoice API answered 404` means VersaVoice does not have the link route yet; the service asks again every 30 minutes. `the VersaVoice API answered 401` means the API token is refused.
4. No `versa-agi-link` service at all: the machine has no systemd (some WSL installs). The fetch is the only path there.

A woken agent still needs its pass and its model: the link removes the wait before those, not the work itself.

## The machine was offline

`grep -E 'OFFLINE|ONLINE' /var/log/versa-agi-lifeline.log | tail` shows each outage: one `OFFLINE` line when Lifeline first could not reach VersaVoice AI, one `ONLINE` line with the length when it came back. In between, inbox fetches wait and agents on cloud models are not started, so nothing fails and the circuit breaker is not touched. Agents on local models keep running. Work that came due during the outage starts on the first pass after it.

**The app shows an agent resting although it is working.** While a cycle runs, the machine proves about once a minute to VersaVoice AI that it is alive. After 3 minutes without a proof the app shows the agent resting, and working again when the proofs return. So a network gap of more than 3 minutes shows as resting. Before 3.10.0 the app kept showing a working agent for up to the rest of its time limit after the machine went off.

If messages are slow while the machine is online, check for a second network connection that carries nothing (a phone tethered by USB, a VPN): `ip route` and `ip -6 route` show which connection is the default.

## Circuit breaker

5+ consecutive failures or 20+ failures in 60 minutes:

```bash
agictl agent show <name>
agictl agent activate <name>
```

Thresholds: `setup.ini` `[agent]` or agitop Settings.

When an agent is paused this way you get a message from your Chief Orchestrator Agent's account that names the agent, the reason, and how to bring it back (from 3.9.3; earlier versions did not deliver it). The reason is the number of failed cycles and what the last failure was: the cycle ended with an error, had an input error, repeated the same tool call, stalled, or was stopped as a runaway. The error itself is in the cycle log. If no message arrives, look in `/var/log/versa-agi-lifeline.log` for `Circuit breaker (<name>)`: `NOTIFY:` means it was sent, `WARN:` means the send failed and it is tried again every minute while the agent stays paused.

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
| `RESUME FRAME: no summary was stored` or `the summary call failed` (cycle log) | A cycle started with more saved conversation than the agent's Resume Max Messages, and the summary of the older part could not be written: the Compaction Model, or the agent's own model, did not answer. | Nothing is lost. For that cycle the agent keeps more of its conversation, up to what its model allows, and the next cycle tries again. | If the line repeats, check the Compaction Model in agitop → System Settings → General. |
| The `CACHE:` line shows a large **written** figure after the agent looked at several photos (cycle log) | On a version before this was fixed, each photo after the first made a Claude model bill most of the conversation again. | The cycle costs more than it should. Nothing else is affected. | Update. A cycle with photos then shows `VIEW HOLD` lines, and its `CACHE:` line reads near 95% from the cache. |
| `SHUTDOWN: … cycle ended because the computer is shutting down` | The machine was shut down or restarted while the cycle ran. The agent was told and given `shutdown_grace_seconds` (default 120) to hand off; one still running after that was stopped. | The cycle shows as **Shutdown** on the agent card and does not count toward the circuit breaker. The agent carries on at its next start, after the startup pause. | Nothing. If agents are often stopped before they finish, raise `shutdown_grace_seconds` under `[agent]` in `setup.ini` (up to 300). |
| `CUT: … was cut off at …` | The cycle never ended: the computer lost power or was forced off, or Lifeline was stopped. | Lifeline closed the record with the time the cycle stopped and told VersaVoice AI. The agent's next wake says its last cycle was interrupted, and it checks the last thing it was doing. Not counted by the circuit breaker. | Nothing. Look at what the agent was doing if it was changing files or sending messages. |
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

## A Claude agent fails every cycle after a web search ("without a corresponding code_execution_tool_result")

The cycle log shows `400 … code_execution tool use … was found without a corresponding code_execution_tool_result block`, on every cycle of the same conversation. After five failures the agent is paused (circuit breaker).

The model is on an Anthropic key and ran a web search from a code step. Before 3.9.3 that reply was sent back incomplete, and Anthropic refused the conversation from then on.

1. Update to 3.9.3 or later (`sudo ./setup.sh --update`). The conversation itself is not damaged; it works again from the next cycle.
2. If the agent was paused: `sudo agictl agent activate <name>`, or in agitop click the agent's row → **Clear Circuit Breaker**.

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
