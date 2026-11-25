# IDE mode

Put COA in an IDE session (VSCode, Cursor, Antigravity) so it runs on the IDE's inference as the `coa` user. The chat can be the IDE's own agent or an agent extension such as Claude Code; with an extension, inference is the extension's. Lifeline does not spawn a harness cycle while the mode is on.

Native Linux, WSL, and OrbStack all work over Remote-SSH. Host class only changes how the SSH config is installed.

## Enable

```bash
sudo versa-agi-ide
# or
sudo agictl agent ide on coa
```

The command prints the workspace path, the seed path, and the SSH details. It also probes the loopback login for you — if it prints a `WARNING` about SSH, fix that before opening the IDE, because Remote-SSH will not connect.

`openssh-server` must already be installed and `sshd` running. The command will not `apt-get install` it.

## Attach the IDE

You do **not** open COA's home directory as yourself. Opening `/home/coa/coa-env` from your normal desktop session gives you a local window running as *you*, and the IDE's terminal and agent would inherit your user, not COA's. The whole point of the mode is that the IDE process runs as `coa`, which is what Remote-SSH gives you.

1. Install the **Remote - SSH** extension if it is not already there (built in on Cursor; `ms-vscode-remote.remote-ssh` on VSCode).
2. `Ctrl+Shift+P` → **Remote-SSH: Connect to Host** → pick `versa-coa`.
   On native Linux, `sudo versa-agi-ide` already added that `Host` entry to your `~/.ssh/config`. On WSL / OrbStack, paste the printed block into the **desktop** SSH config first (Windows `%USERPROFILE%\.ssh\config`, macOS `~/.ssh/config`) — the IDE reads that file, not the Linux one.
3. In the new remote window: **File → Open Folder** → `/home/coa/coa-env`.
4. Start a chat. Say hello.

Confirm you landed in the right place — `whoami` in the IDE terminal must print `coa`.

While the mode is on, `AGENTS.md` at the workspace root is picked up automatically (Cursor, VS Code Copilot, Antigravity, Claude Code). That is the door. `ide off` deletes it (and the seed). COA reads `.agent/versa-agi_ide.md` itself — you do not have to attach it. Attaching the seed is optional.

Both the workspace and the seed live on the remote side, so the IDE's agent shares COA's `agictl` access, databases, and filesystem view. `agictl` is on the `PATH` in that terminal.

COA first works out two things, in this order: which IDE the window is, and which tool is running the chat (the IDE's own agent, or an extension such as Claude Code). It tells you both at the start.

A same-chat loop is optional and follows the tool, not the IDE:

| Tool running the chat | Same-chat loop |
|---|---|
| Cursor agent | Yes (`/loop`) |
| Claude Code, in any of the three IDEs | Yes (`/loop`) |
| GitHub Copilot in VS Code | No: one cycle per message |
| Antigravity agent | No: one cycle per message |
| Another tool | Only if it has its own command that wakes the same chat on a timer |

Where a loop is possible, COA asks how often to wake (in minutes) and how the loop ends. Everything else in the mode works the same in every IDE and with every tool. Do not treat a loop as how the mode works.

## Leaving a loop running

You can leave the chat open and have COA check for work on a timer, for example every 15 minutes for a working day.

- **What a check does.** One command, `agictl agent ide tick coa`, tells COA whether anything is waiting: new messages from anyone, and her tasks that have come due. Nothing waiting: she ends the turn in one line. Something waiting: she handles it as in a normal work cycle.
- **How a loop ends.** After a fixed number of hours, after a number of quiet checks in a row, or whichever comes first. You choose when it starts. The system keeps the count, not the chat, so a long session does not lose track.
- **What happens when it runs out.** By default COA closes the session: she writes what she did to her memory and task journals, sends you one message (what she did, that the loop has ended, that IDE mode is off), and turns IDE mode off, so Lifeline takes over. She sends no closing notes to anyone else. To come back, turn the mode on again. If you would rather she only stop the loop and leave the mode on, say so when you start it. Telling her to stop in the chat ends only the loop.
- **If the session was not closed.** When a loop has run out and the mode is still on 45 minutes later, Lifeline sends you a message. It does not turn the mode off for you.
- **Your own messages.** Write in the chat at any time. COA follows your message; the loop carries on.
- **Sub-agents carry on as usual.** Only COA is held while IDE mode is on. Her inbox still fills; nobody but her picks up her messages and due tasks.
- **If the loop stops ticking** (the chat was closed, the tool's loop ended, the computer slept), Lifeline sends you one message after two missed checks, saying what is waiting. Open the chat again, or turn the mode off and Lifeline takes over.
- **Cost.** Each check is one turn of the IDE chat, billed by the tool you use (not by Versa AGi's providers). At 15 minutes that is 32 turns in 8 hours; at 30 minutes, 16.

## Every turn

At the start of a chat COA runs `agictl agent ide status coa` and reads the session file once. If `message` reports Lifeline cycles since the last IDE session, it tells you and refreshes live state before using anything remembered from the chat.

On every later message, and on every timer wake, COA first runs `agictl agent ide tick coa`. If the mode is off, it stops. It does not read the session file again, unless the mode was turned off and on under an open chat; the command says so.

## Disable

Close the IDE chat, then either you or COA can turn the mode off:

```bash
sudo versa-agi-ide --off
# or
sudo agictl agent ide off coa
# or, as coa after close-out:
agictl agent ide off coa
```

COA can run `off`. It cannot run `on`. Other agents cannot flip either.

COA returns to normal Lifeline spawning on the next pulse. That is not `[coa] autonomous=true` (full sudo) — IDE mode never touches that setting. An open IDE chat is **not** a harness process — Lifeline will not see it. The per-turn status check is what stops COA in that chat. `off` deletes `AGENTS.md` and `.agent/versa-agi_ide.md`. A leftover `AGENTS.md` in `/home/coa/` is removed too.

## What this is not

- Not a second system prompt. The seed and `AGENTS.md` exist only while the mode is on; both are deleted on `off`.
- Not token-accounted. IDE turns do not write cycles.
- Not a privilege grant beyond being `coa`. `on` is Primary User only (`sudo`). `off` is Primary User or COA. `status` is readable by COA on purpose.
- Not a LAN-reachable login. The sshd rule accepts `coa` only from `127.0.0.1` / `::1`, by public key, never by password.
- Not a required loop. Same-chat wake is optional, and offered only when the tool running the chat has one (Cursor agent, Claude Code).

## Troubleshooting

| Symptom | Check |
|---|---|
| Remote-SSH cannot connect | `sudo sshd -t` for config errors, then `sudo systemctl status ssh`. On WSL without systemd: `sudo service ssh start`. |
| `Permission denied (publickey)` | Confirm the Host block's `IdentityFile` exists and is `600` and owned by you. Re-run `sudo versa-agi-ide` — it reprints the block and re-probes. |
| `versa-coa` not offered as a host | The block went to the wrong SSH config. On WSL / OrbStack it must be in the **desktop** OS config, not the Linux home. |
| Connected, but `whoami` is not `coa` | You opened a local folder instead of a Remote-SSH window. |
| COA acts like it has no history | Confirm the folder is `/home/coa/coa-env` (so `AGENTS.md` loads), IDE mode is on, and `.agent/versa-agi_ide.md` exists. Run `agictl agent ide status coa`. |
| COA answered, then went quiet about tasks | The mode was turned off mid-chat. That is the per-turn check doing its job. |
| After an update COA asks the old questions, or a loop does not end the way this page says | The chat is running in a tool process that started before the update and still holds the old rules. COA should notice at the start: `agictl agent ide status coa` names the installed rules version. If it does not, reload the IDE window (or start a new chat session of the tool) after turning IDE mode on. |
| A message from Lifeline says COA's IDE loop is silent | The chat stopped waking. Open it again and ask COA to continue, or turn the mode off. Check the loop with `agictl agent ide status coa` (`loop`). |

## Related

- [Operations](operations.md)
- [Security](security.md)
- [Models](models.md)
