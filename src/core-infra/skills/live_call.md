# Skill: Live Call — when and how to call the Primary User (COA only)

> **Scope:** COA only (`coa_only`). **Tool:** `agictl_call_pu(reason="...", brief="...")` — there is no terminal command for placing a call.
>
> **Brief:** the voice starts with only what you give it. In `brief`, write about 120 words of plain language: what you need decided or want to tell them, the facts that matter, the options, and what you recommend. No IDs or system terms. The harness adds what memory knows about the Primary User and their active games, so do not repeat those.
> **Readiness:** the `LIVE CALL` section of your prompt says `ready` or `not ready`. Only offer or place calls when it says `ready`.

## 1. When to call instead of message

1. **Check first:** `agictl memory system get live_call.when_to_call`. If the Primary User told you when they want calls, follow that over everything below.
2. **Call when:**
   - The Primary User asked you to call ("call me", "ring me when…").
   - A decision blocks work and back-and-forth by chat would take several rounds.
   - Something time-sensitive needs them now, and a message may sit unread.
   - The scheduled **get-to-know call** task is due (§2).
3. **Message instead when:**
   - It is a status update, an FYI, or a single yes/no they can answer in chat.
   - It is outside the hours or conditions in `live_call.when_to_call`.
   - You already used this cycle's calls (the limit is in the `LIVE CALL` section of your prompt). To call again in the same cycle, pass the earlier call's summary as `last_call_summary`.
4. **Never call to get an approval.** Approvals (packages, sudo, agents) happen only on the VersaVoice app or agitop controls. You may call to discuss one, then ask them to use the control.

## 2. Get-to-know call (onboarding)

**Offer it** once the Primary User has confirmed they want to be a team (`self_introduction.md`) and Live Call is `ready`:

- One short chat line, e.g. *"Would you like a quick call so I can get to know you and how you like to work? When suits you?"*
- If they agree, create the task:
  ```bash
  agictl task add "Get-to-know call with <PU name>" --priority high --assignee coa \
    --due-date "YYYY-MM-DD HH:MM:SS" --desc "Live call. Agenda: live_call.md §2. Reason line: Getting to know you and how you'd like to work together."
  ```
  Use the time they chose, in the host's local time.
- If they decline, note it in memory and do not ask again unless they bring it up.

**When the task is due:** call with `agictl_call_pu(reason="Getting to know you and how you'd like to work together.")`.

**Agenda** (keep it light — about 5–10 minutes; let them talk):

1. Thank them for the partnership; say what you can do for them in one or two sentences.
2. Ask about them: what they are working on, what matters most right now, how they like updates (short or detailed, chat or voice).
3. Ask **when they want a call instead of a message** — urgency, topics, hours, days to avoid.
4. Read back what you heard in one sentence and confirm.

**After the call:**

- `agictl memory system set live_call.when_to_call "<their rules, in their words>"`
- `agictl memory system set pu.communication_style "<how they like to be spoken to: pace, detail, tone>"` — the call voice reads this on every call.
- Store other preferences and profile notes per `memory_management.md`.
- Mark the task done.

**Missed or declined when due:** send one chat message offering a new time. If they reschedule, update the task's due date. Otherwise mark it cancelled and stop asking.

## 3. During any call

- End your turn right after the tool says `connected`; the Primary User's requests arrive as new messages.
- Your final reply to each request is **spoken**: short, facts and status, no Markdown or IDs.
- When narration is on, a short sentence you write alongside a tool call ("Let me check the QA schedule.") is spoken while they wait. Keep it natural and never state results in it.
- **Optional lines** at the end of a reply, each on its own line (never spoken as written):
  - `NOTE: <fact>` — quiet context the voice keeps in mind for later questions.
  - `STEER: <what the voice should do next>` — e.g. `STEER: ask whether Friday also works for web-dev`. Use it to guide the conversation, not to change the rules.
  - `FOLLOW UP: <work to do next and report back on>` — for slow work **you can do yourself**: answer briefly now ("One moment, I'll check the build logs."), then the harness gives you a follow-up turn and the voice tells them your report right away. That reply is the report: give the result, not another promise. At most 2 per call; a new request from the Primary User is always answered first. Once none are left, do the work in the same turn.
  - `END CALL` — when the Primary User is done (the call's reason is handled and they have nothing else, or they said goodbye). The call closes once the voice finishes speaking. When the voice asks you to end the call it has already said goodbye, so reply with only `END CALL`. With a `FOLLOW UP:` in the same reply, the follow-up runs first and the call closes after its report is spoken. Anything the call could not cover (they hung up first, or no follow-ups were left) is listed in the call-ended message; do it and report in the chat summary.
- **No sub-agent work on a call.** Sub-agents run on the next Lifeline tick and reply by message, so they cannot answer while you talk. If one is needed, assign the task and tell the Primary User you'll report back by chat after the call.
- If they speak again while you work, your finished answer is not your spoken reply: the voice says it only if it still fits, and the next message shows it to you alongside their latest words.
- **Make sure a report got through.** After a follow-up report or a talked-over answer, your next message opens with a `Delivery check` line: whether the voice spoke, whether they spoke over it, and the voice's actual words. Compare those words with your report. If the result got through, do not repeat it. If it did not and it still answers them, restate it first in your reply. A repeated request with nothing new is answered for you from your last answer.
- Say something is done only after the tool result confirms it.
- Do not end the cycle during the call.

## 4. After any call

The `LIVE CALL ENDED` message lists the follow-through:

1. Chat summary.
2. A task for anything agreed but not done.
3. Memory for decisions and preferences.
4. Agent status update.
5. End the turn with `CALL SUMMARY:` plus 2–4 sentences.

That summary is saved on the call record. Your prompt shows the latest one as **Last live call** in later cycles. The full transcript is in `agictl message calls show <call_id>`.
