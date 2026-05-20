---
description: End-of-cycle memory write-back procedure (MANDATORY)
---

# Memory Management Skill

> **MANDATORY**: This skill MUST be executed before ending every cycle. The system prompt enforces this.
   └─ EACH TIME YOU START A WORK CYCLE, YOUR MEMORY IS FROM THE PREVIOUS COMPLETED CYCLE (ACCUMULATIVE).
   └─ Keep your memory up to date as a unit that reflects the concrete past, present situation and potential future applied to the current memory.

## Purpose

Write back all observations from this cycle into structured memory so future cycles retain context. Memory is your only persistence between cycles — **if you don't write it, you won't remember it.**

## Procedure

### Step 1: Connection Memory

For **each contact you communicated with** this cycle:

1. Review the conversation — what did you learn about this person?
2. Check for **VersaVoice emotion tone tags** in their messages (primary source for emotional/relational insight)
3. Update their connection memory:

```bash
agictl memory connection set <contact_uid> \
  --preferences '{"voice_vs_text": "voice", "language": "en", "comm_style": "casual"}' \
  --personal-notes "Enjoys hiking, has two kids, recently started a new job" \
  --comm-style "Direct communicator, appreciates brevity" \
  --rapport building \
  --emotional-notes "Excited about the new project, tone was enthusiastic"
```

**Rapport levels:**
- `new` — First or very early interactions
- `building` — Getting to know each other, establishing patterns
- `established` — Comfortable working relationship, predictable communication
- `strong` — Deep trust, shared context, efficient collaboration

> Only provide the options you want to update — existing values are preserved.

### Step 2: Project Memory

For **each project you worked on** this cycle:

1. What phase is the project in now?
2. Were any key decisions made? Document the **why**, not just the what.
3. Are there blockers? What are the next steps?

```bash
agictl memory project set <project_id> \
  --phase "Phase 2 — audition preparation" \
  --decisions "Chose to prioritize commercial auditions first because they have shorter turnaround" \
  --blockers "Waiting for headshot photographer availability" \
  --next-steps "Schedule headshots, prepare 2 commercial monologues"
```

### Step 3: System Memory

Capture any **new discoveries, constraints, or instructions** from this cycle:

```bash
agictl memory system set "reporting_schedule" "Daily updates at 6:30 PM to Stephen"
agictl memory system set "discovery_attachment_limit" "Max 10 attachments per message send"
agictl memory system set "user_instruction_no_emoji" "Primary User prefers no emoji in messages"
```

Use descriptive keys with prefixes:
- `constraint_*` — Operational boundaries
- `discovery_*` — Things learned about tools/systems
- `user_instruction_*` — Explicit instructions from the Primary User
- `schedule_*` — Recurring duties or deadlines

### Step 4: Verify

After writing, confirm your memory was saved:

```bash
agictl memory connection list
agictl memory system list
```

## Important Notes

- **Only write what changed** — don't rewrite unchanged memory
- **Be concise** — memory is injected into your context on wake; verbose memory wastes tokens
- **Emotion tags take priority** — if VersaVoice emotion tags are present in messages, use them as the primary signal for relational memory rather than your own text interpretation
- **If you didn't communicate with anyone** — skip Step 1 but still do Steps 2-3 if applicable
