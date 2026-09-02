# IDE mode

Put COA in an IDE session (VSCode, Cursor, Antigravity) so it runs on the IDE's inference as the `coa` user. Lifeline does not spawn a harness cycle while the mode is on.

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
4. Open a chat and attach `.agent/versa-agi_ide.md` as context. Say hello.

Confirm you landed in the right place — `whoami` in the IDE terminal must print `coa`.

Step 4 is the part that actually makes it COA. The seed carries COA's poise, live situation, always-on skills, and the session rules. Without it you are talking to a stock assistant that happens to be logged in as `coa`.

Both the workspace and the seed live on the remote side, so the IDE's agent shares COA's `agictl` access, databases, and filesystem view. `agictl` is on the `PATH` in that terminal.

## Every turn

COA must run `agictl agent ide status coa` first. If the mode is off, it stops. If `message` reports autonomous cycles since the last IDE session, it tells you and refreshes live state before using anything remembered from the chat.

## Disable

Close the IDE chat, then:

```bash
sudo versa-agi-ide --off
# or
sudo agictl agent ide off coa
```

COA returns to autonomous spawning on the next pulse. An open IDE chat is **not** a harness process — Lifeline will not see it. The per-turn status check is what stops COA in that chat.

## What this is not

- Not a second system prompt. The seed is a one-shot file, deleted on `off`.
- Not token-accounted. IDE turns do not write cycles.
- Not a privilege grant beyond being `coa`. `on`/`off` are Primary User only (`sudo`). `status` is readable by COA on purpose.
- Not a LAN-reachable login. The sshd rule accepts `coa` only from `127.0.0.1` / `::1`, by public key, never by password.

## Troubleshooting

| Symptom | Check |
|---|---|
| Remote-SSH cannot connect | `sudo sshd -t` for config errors, then `sudo systemctl status ssh`. On WSL without systemd: `sudo service ssh start`. |
| `Permission denied (publickey)` | Confirm the Host block's `IdentityFile` exists and is `600` and owned by you. Re-run `sudo versa-agi-ide` — it reprints the block and re-probes. |
| `versa-coa` not offered as a host | The block went to the wrong SSH config. On WSL / OrbStack it must be in the **desktop** OS config, not the Linux home. |
| Connected, but `whoami` is not `coa` | You opened a local folder instead of a Remote-SSH window. |
| COA acts like it has no history | The seed was not attached to the chat, or the mode is off. Run `agictl agent ide status coa` in the terminal. |
| COA answered, then went quiet about tasks | The mode was turned off mid-chat. That is the per-turn check doing its job. |

## Related

- [Operations](operations.md)
- [Security](security.md)
- [Models](models.md)
