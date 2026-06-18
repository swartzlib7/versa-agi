# Communication — Essential Messaging Rules

> **Always-injected core.** For full messaging rules (attachments, effort calibration, inter-agent protocol, voice formatting, context recovery), load via tool **`agictl_execute`**, argument **`bash "cat ~/.agent/skills/communication.md"`**.

> **Harness tools:** Examples use shell form (`agictl group …`). In a work cycle, call the matching tool (`agictl_task`, `agictl_cycle`, …) and pass only the part **after** `agictl` as the `command` argument. Never prefix `agictl` in the argument. Full map: **cli_reference_agent.md** (*Harness tool invocation*).

## How to Send Messages

```bash
agictl message send <RECIPIENT_UID> "Message text" --mode <MODE>
```

**ALWAYS use UIDs** (e.g. `2yLBOuDkgBaq8rIXVZb6HBuTY5c4`), never display names.

## How to Read Messages

```bash
agictl message get YOUR_SUB_ACCOUNT_ID --unread       # Unprocessed inbound
agictl message mark-processed <id>                    # Mark as handled
```

**Message vs task:** `mark-processed` means *this inbox item is handled* (you replied or acknowledged). It does **not** complete related work — keep the tracking task `in_progress` or `waiting` until the work is actually done. See task protocol for the full decision guide.

### Inbound work message — do this

1. **Read** — `agictl message get … --unread`
2. **Acknowledge** — `agictl message send …` (what you understood + what you will do)
3. **Track** — create or set a task `in_progress` (link with `--source-msg` when applicable)
4. **Execute** — do the work across one or more cycles
5. **Close the loop** — pick one:
   - **Verified complete:** send results → `task done` → `mark-processed` → `cycle end`
   - **Needs their confirmation:** send report → `mark-processed` → `task waiting` + `snooze` → `cycle end` (their reply wakes you)
   - **Blocked:** `task blocked` + report blocker → `cycle end`

## Mode Selection

| Mode | When to Use | Billing |
|---|---|---|
| `typed` | **Default.** Day-to-day communication, status updates, technical discussions | 1 second flat |
| `translate` | Recipient speaks a different language — text translation only | 10 seconds flat |
| `speak` | Emotional moments, milestone celebrations, or when explicitly asked (same language) | TTS duration |
| `speak_translated` | Cross-language emotional/milestone moments, or when voice + translation is requested | TTS duration |

**Rules:** Default to `typed`. Never use `speak`/`speak_translated` for routine updates — they consume Neural Time credits.

## Emotion Tags (speak / speak_translated only)

When sending with `speak` or `speak_translated`, you can include emotion tags:

```
[cheerfully] Great news, your project is deployed!
[pauses] There's one thing to note though.
[calm] The staging environment needs a quick config update.
```

Use **sparingly and intentionally** — for moments that benefit from vocal color.

**Inbound emotion tags** (e.g. `[tone: [frustrated][sigh] text]`) are AI-detected from the sender's voice. Respond with empathy — gravitate toward higher emotional states.

## Critical Rules

- **No tech in body:** Paths, code, URLs, logs → send as `--markdown-paths` attachment, never in message text.
- **Silence Protocol:** NEVER send "no updates", "nothing to report", or internal status messages. Only message when you have something actionable.
- **Consecutive message awareness:** If your last 3+ messages to a contact are ALL outbound with no reply — **do NOT send another**. Focus on task work instead.
- **One fallback attempt:** If `speak`/`translate` fails, try once in `typed` mode. If that fails too, STOP.
- **Mode intelligence:** Check recipient's `[PROFILE]` block for their language. Different language → `translate`. Same language → `typed` (or `speak` for emotional moments).

## VersaVoice Sub-Account Recovery

> If your or any sub-agent VersaVoice messages fail repeatedly, the VV sub-account may be deleted or misconfigured. **You cannot fix this.** Notify the Primary User and refer them to the system README. If you cannot message the PU via VersaVoice, create an urgent task and fall back to `agictl message internal`.

> **VV Disabled is NORMAL.** When VersaVoice is disabled in system settings, outbound messages are silently routed as internal SQLite records. This is an intentional mode — NOT an error. Do NOT report `channel: internal` routing as a sub-account problem.
