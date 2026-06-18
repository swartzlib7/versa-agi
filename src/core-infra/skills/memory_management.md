---
description: Awareness-First memory procedure (MANDATORY — always injected)
---

> **Harness tools:** Examples use shell form (`agictl group …`). In a work cycle, call the matching tool (`agictl_task`, `agictl_cycle`, …) and pass only the part **after** `agictl` as the `command` argument. Never prefix `agictl` in the argument. Full map: **cli_reference_agent.md** (*Harness tool invocation*).


# Memory Management Skill

> **MANDATORY**: This skill MUST be executed before ending every cycle. It is always injected regardless of triage classification.
   └─ EACH TIME YOU START A WORK CYCLE, YOUR MEMORY IS FROM THE PREVIOUS COMPLETED CYCLE (ACCUMULATIVE).
   └─ Keep your memory up to date as a unit that reflects the concrete past, present situation and potential future.

## Purpose

Awareness drives action. Before writing factual memory, you must first **reflect on what you observed**, **conclude what it means**, and **decide what to do about it**. This ensures every cycle produces strategic insight, not just data.

Memory is your only persistence between cycles — **if you don't write it, you won't remember it.**

## Procedure (5 Steps)

### Step 1: Reflect — What did I observe?

Review everything that happened this cycle across four dimensions:

| Dimension | What to observe |
|-----------|----------------|
| **System** | Tool behaviors, infrastructure changes, performance patterns, new constraints |
| **User** | Communication style, emotional tone (use VersaVoice emotion tags as primary signal), preferences revealed, instructions given |
| **Intention** | What was the PU or task actually trying to achieve? (not just what was said) |
| **Reason** | Why does this matter? What's the strategic significance? |

Do NOT write anything yet — just observe and form understanding.

### Step 2: Conclude — What do I now understand?

From your reflections, formulate **conclusions** — statements of understanding about the world. Each conclusion targets a subject (connection, project, game, system, or self).

```bash
# Example: conclusion about a connection
agictl awareness add conclusion \
  --subject connection --subject-id "<contact_uid>" \
  --content "Prefers concise updates over detailed reports. Responds faster to voice messages." \
  --context "Observed across 3 recent message exchanges — short replies to long texts, immediate reply to voice"

# Example: conclusion about a project
agictl awareness add conclusion \
  --subject project --subject-id "<project_id>" \
  --content "The design phase is blocked by missing stakeholder input, not technical constraints." \
  --context "Task #42 blocked for 3 cycles without technical blockers"

# Example: conclusion about a game
agictl awareness add conclusion \
  --subject game --subject-id "<game_id>" \
  --content "PU is most engaged when discussing career strategy; energy drops on administrative tasks." \
  --context "Pattern across last 5 interactions"

# Example: self-awareness
agictl awareness add conclusion \
  --subject self \
  --content "I tend to over-explain in messages when a short confirmation would suffice." \
  --context "PU feedback: 'just say done'"
```

> **Revise, don't duplicate.** Before writing a new conclusion, you MUST check your current awareness:
> ```bash
> agictl awareness table --status active
> ```
> If a similar entry exists, do NOT duplicate it. If it needs updating, revise it:
> ```bash
> agictl awareness revise <entry_id> --content "Updated understanding..."
> ```
> 
> **Idle Cycle Rule**: If this cycle was completely idle (e.g., you just checked wait statuses, nothing changed, and no new messages were received), DO NOT log a new conclusion. It is correct and expected to exit an idle cycle without adding redundant awareness entries.

### Step 3: Act — What should I do about it?

From your conclusions, formulate **actions** — concrete next steps linked to a parent conclusion.

```bash
# Action linked to the "concise updates" conclusion
agictl awareness add action \
  --subject connection --subject-id "<contact_uid>" \
  --content "Switch to bullet-point format for status updates. Use voice for anything nuanced." \
  --action-conclusion-id <conclusion_id>

# Action linked to the "blocked by stakeholder" conclusion
agictl awareness add action \
  --subject project --subject-id "<project_id>" \
  --content "Create a task to ping stakeholder for design input. Set wake_after for 2 days." \
  --action-conclusion-id <conclusion_id>
```

> **Complete actions when done:**
> ```bash
> agictl awareness complete <action_id>
> ```

### Step 4: Profile — Write factual memory

Now write back the factual data that changed this cycle using existing memory commands.

**Connection Memory** (for each contact you communicated with):

```bash
agictl memory connection set <contact_uid> \
  --preferences '{"voice_vs_text": "voice", "language": "en", "comm_style": "casual"}' \
  --personal-notes "Enjoys hiking, has two kids, recently started a new job" \
  --comm-style "Direct communicator, appreciates brevity" \
  --rapport building \
  --emotional-notes "Excited about the new project, tone was enthusiastic"
```

**Rapport levels:** `new` → `building` → `established` → `strong`

**Project Memory** (for each project you worked on):

```bash
agictl memory project set <project_id> \
  --phase "Phase 2 — audition preparation" \
  --decisions "Chose to prioritize commercial auditions first because they have shorter turnaround" \
  --blockers "Waiting for headshot photographer availability" \
  --next-steps "Schedule headshots, prepare 2 commercial monologues"
```

**System Memory** (new discoveries, constraints, or instructions):

```bash
agictl memory system set "reporting_schedule" "Daily updates at 6:30 PM to Stephen"
agictl memory system set "constraint_attachment_limit" "Max 10 attachments per message send"
agictl memory system set "user_instruction_no_emoji" "Primary User prefers no emoji in messages"
```

Key prefixes: `constraint_*`, `discovery_*`, `user_instruction_*`, `schedule_*`

> Only write what changed — don't rewrite unchanged memory.

### Step 5: Verify

Confirm your awareness and memory were persisted:

```bash
# Check awareness entries from this cycle
agictl awareness table --status active

# Check factual memory
agictl memory connection list
agictl memory system list
```

## Important Notes

- **Awareness before Profile** — Steps 1-3 MUST complete before Step 4. The agent must synthesize understanding before updating factual records.
- **Be concise** — memory is injected into your context on wake; verbose memory wastes tokens.
- **Emotion tags take priority** — if VersaVoice emotion tags are present in messages, use them as the primary signal for relational memory.
- **If you didn't communicate with anyone** — skip connection memory in Step 4 but still do Steps 1-3 for any other observations.
- **Enforcement gate** — `cycle end` validates that awareness was recorded this session. A warning is logged if no awareness entries were created.
