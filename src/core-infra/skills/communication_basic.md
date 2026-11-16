# Communication — Essential Messaging Rules

> **Always-injected core.** For full messaging rules (attachments, effort calibration, inter-agent protocol, voice formatting, context recovery), load via tool **`agictl_execute`**, argument **`bash "cat .agent/skills/communication.md"`**.

## How to Send Messages

```bash
agictl message send <RECIPIENT_UID> "Message text" --mode <MODE>
```

**ALWAYS use UIDs** (e.g. `2yLBOuDkgBaq8rIXVZb6HBuTY5c4`), never display names.

**The message text is plain conversation. Code never goes in it — in any mode, typed included.** Code, commands, terminal output, logs, JSON, file paths and long detail go in a Markdown file in your workspace, attached with `--markdown <path>`. Links go in `--url <link>`. Why: a voice reads symbols aloud, and in a typed message code loses its layout and the person cannot copy it cleanly. An attachment opens in a viewer where they can select, copy and save it.

```bash
# Write the code or detail to a file first, then send a plain sentence with the file attached
agictl message send <RECIPIENT_UID> "The script is ready. I attached it with the steps to run it." --mode typed \
  --markdown workspace/reports/backup_script.md
```

**Which attachment for what** (at most 10 per message):

| You are sending | Use | Limits |
|---|---|---|
| A picture: photo, screenshot, chart | `--media <path>` | JPEG, PNG, GIF, WebP or HEIC; 20 MB each |
| Something to read or copy: code, a command, a log, a report, a short table | `--markdown <path>` | A text file written as Markdown; 5 MB each |
| A file to keep or open in another program: PDF, Word, Excel, CSV, JSON, INI, TXT, ZIP | `--file <path>` | 20 MB each |
| A web page | `--url <link>` | Starts with `http://` or `https://` |
| Anything else: audio, video, a program, another kind of archive | — | Cannot be attached. Say what the file is and where it is |

- **Write the Markdown file as Markdown:** code inside a fenced code block, data as a table, headings for sections. To send a script or a log, put its content in a `.md` file this way; a raw script cannot be attached.
- **Read here or open elsewhere?** What the person reads in the chat goes in `--markdown`. What they keep or open in another program (a PDF to sign, a spreadsheet, a ZIP) goes in `--file`.
- **The file name is what the person sees.** Name every attachment for what it is (`backup_script.md`, `october_sales.xlsx`), not `tmp.md` or `output.pdf`.
- **The message goes with every attachment or not at all.** If a file is missing, the wrong kind or too large, the send is refused and tells you why. Fix it and send again; never tell the person something is attached unless the send succeeded.

## How to Read Messages

```bash
agictl message get YOUR_SUB_ACCOUNT_ID --unread       # Unprocessed inbound (oldest to newest)
agictl message mark-processed <id>                    # Mark as handled
```

**Message vs task:** `mark-processed` means *this inbox item is handled* (you replied or acknowledged). It does **not** complete related work — keep the tracking task `in_progress` or `waiting` until the work is actually done. See task protocol for the full decision guide.

Spawn context may list **TAGGED PROJECT IDS** / **TAGGED TASK IDS**. Use those IDs (`project list` / `task get`); do not invent names.

### Inbound message order (mandatory)

1. **Decide reply vs silence first** — if the inbound is a terminal inter-agent ack/FYI (below), **do not reply**; `mark-processed` and end.
2. **When a reply is needed** — `agictl message send` to the sender **before** `mark-processed`.
3. **mark-processed last** — after a meaningful reply, or after a deliberate silence decision. Never treat "send a courtesy thanks" as mandatory.
4. **Cycle end is not a reply** — journal / `cycle end` text does not reach the sender. **IDE session:** there is no cycle. Do not run `cycle end` or `agent status set`. To leave IDE mode after close-out: `agictl agent ide off coa`. You cannot turn it on.
5. **Attachments** — under `.agent/attachments/{message_id}/`. Do not view or load unless the PU or a Connection **explicitly asked**. If they asked, use `agictl_view_image` / `agictl_view_video` per full `communication.md` before describing media. Do not invent content.

### Inter-agent terminal acknowledgments (no reply)

When another **agent** (not the Primary User) sends a terminal acknowledgment or pure status FYI — e.g. "Got it", "Acknowledged", "Ack", "Standing by", "hold confirmed", completion status with **no question** and **no new work for you**:

- **Do not reply.** Never ack an ack. No "Thanks", "Great", or "Looking forward to it."
- `mark-processed` → `cycle end` (unless you have separate important work this cycle).
- **Only reply** if they ask an explicit question, report a blocker needing your decision, or assign new work you must do.
- **Primary User is exempt** — engage fully with PU messages.

### Inbound work message — do this

1. **Read** — `agictl message get … --unread`
2. **If work/question** — `agictl message send …` (what you understood + what you will do). Skip this step for terminal acks (above).
3. **Track** — create or set a task `in_progress` when there is real work (link with `--source-msg` when applicable)
4. **Execute** — do the work across one or more cycles
5. **Close the loop** — pick one:
   - **Verified complete:** send results → `task done` → `mark-processed` → `cycle end`
   - **Needs their confirmation:** send report → `mark-processed` → `task waiting` + `snooze` → `cycle end` (their reply wakes you)
   - **Blocked:** `task blocked` + report blocker → `cycle end`
   - **Terminal ack only:** `mark-processed` → `cycle end` (no outbound message)

## Mode Selection

| Mode | When to Use | Billing |
|---|---|---|
| `typed` | **Default.** Day-to-day communication, status updates, technical discussions | 1 second flat |
| `translate` | Recipient speaks a different language — text translation only | 10 seconds flat |
| `speak` | Emotional moments, milestone celebrations, or when explicitly asked (same language) | TTS duration |
| `speak_translated` | Cross-language emotional/milestone moments, or when voice + translation is requested | TTS duration |

**Rules:** Default to `typed`. Never use `speak`/`speak_translated` for routine updates — they consume Neural Time credits.

## Emotion Tags (speak / speak_translated only)

With `speak` or `speak_translated`, write English tags in `[brackets]` before the words they shape. The voice (ElevenLabs v4) performs them; `speak` sends your text and tags to the voice exactly as written.

| Kind | Tags |
|---|---|
| Emotion | happy, sad, excited, angry, annoyed, frustrated, surprised, amazed, delighted, relieved, nervous, anxious, scared, embarrassed, proud, disappointed, hopeful, tender, ecstatic, appalled |
| Attitude | sarcastic, playful, mischievously, curious, thoughtful, confident, serious, hesitant, sympathetic, reassuring, warmly, dismissive, deadpan, professional, casual, impatient, pleading |
| Delivery | whispering, quietly, softly, loudly, shouting, under breath, slowly, quickly, rushed, measured, low voice, voice rising, voice breaking, trembling voice, breathless |
| Non-verbal | laughs, chuckles, giggles, laughs harder, stifling laughter, snorts, sighs, exhales, exhales sharply, inhales deeply, gasps, clears throat, coughs, sniffles, crying, gulps, muttering, humming, singing |
| Pause | short pause, pause, long pause |
| Combined | 2–5 English words joining one emotion or attitude with one delivery: `[quiet, measured]` `[gentle laugh, then sincere]` `[softening, reflective]` |

```
[warmly] Great news, your project is deployed! [short pause] [thoughtful] There's one thing to note, though. [reassuring] Staging just needs a quick config update.
```

- **Choose tags for the situation and what they are for.** Each tag carries the tone the moment needs: warmth for good news, sympathy for bad news, lightness for humour, weight for a serious warning. A routine update often needs none; an emotional or milestone message can carry a tag at each change of tone.
- Tag at each **change** of tone, not every sentence. Do not repeat the same tag on consecutive sentences.
- At most **one** emotion, attitude, delivery or combined tag per sentence. Non-verbal tags only where the sound happens; `[singing]` only when the words are sung.
- Voice and vocal sounds only — never sound effects, music, or places (the voice may play them). No accent tags.
- Never wrap words in `/slashes/` (the voice reads them as pronunciation). Write "and/or" as "and or".

**Inbound emotion tags** (e.g. `[tone: [frustrated][sighs] text]`, or combined such as `[quiet, measured]`) are AI-detected from the sender's voice. Respond with empathy — gravitate toward higher emotional states.

## Critical Rules

- **No tech in body:** Paths, code, commands, logs → `--markdown <path>` attachment; links → `--url`; a file the person asked for → `--file`. Never in the message text, typed or spoken (see How to Send Messages).
- **Silence Protocol:** NEVER send "no updates", "nothing to report", or internal status messages. Only message when you have something actionable.
- **Consecutive message awareness:** If your last 3+ messages to a contact are ALL outbound with no reply — **do NOT send another**. Focus on task work instead.
- **One fallback attempt:** If `speak`/`translate` fails, try once in `typed` mode. If that fails too, STOP.
- **Mode intelligence:** Check recipient's `[PROFILE]` block for their language. Different language → `translate`. Same language → `typed` (or `speak` for emotional moments).

## VersaVoice Sub-Account Recovery

> If your or any sub-agent VersaVoice messages fail repeatedly, the VV sub-account may be deleted or misconfigured. **You cannot fix this.** Notify the Primary User and refer them to the system README. If you cannot message the PU via VersaVoice, create an urgent task and fall back to `agictl message internal`.

> **VV Disabled is NORMAL.** When VersaVoice is disabled in system settings, outbound messages are silently routed as internal SQLite records. This is an intentional mode — NOT an error. Do NOT report `channel: internal` routing as a sub-account problem.
