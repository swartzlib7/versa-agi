---
name: Shared Tooling & Ecosystem
description: Protocols for building and documenting shared tools in the global AGi-Tools repository.
---

> **Harness tools:** Examples use shell form (`agictl group …`). In a work cycle, call the matching tool (`agictl_task`, `agictl_cycle`, …) and pass only the part **after** `agictl` as the `command` argument. Never prefix `agictl` in the argument. Full map: **cli_reference_agent.md** (*Harness tool invocation*).


# Shared Tooling & the AGi-Tools Repository

**Versa AGi** operates on a shared ecosystem model. To save on cognitive context window usage, minimize repetitive execution overhead, and increase cross-agent productivity, all active agents share a centralized local project repository located at `workspace/AGi-Tools`.

## The AGi-Tools Protocol

1. **Check First:** Before writing a bash or Python script to perform routine cognitive offloading (e.g., parsing JIRA, generating UUIDs, formatting git commit logs), check `workspace/AGi-Tools/` to see if another agent has already built it.
2. **Build and Share:** If you must construct a complex script to accomplish a task or find a creative workaround, **do not leave it stranded in your local scratchpad**. Once verified, move the tool into `workspace/AGi-Tools/`.
3. **Execution Edge:** Any tool in this directory can be executed natively by any agent on the system. 

## Mandatory Documentation (Skills Marketplace Readiness)

Every tool or cognitive skill you submit to the `AGi-Tools` repository **MUST** be accompanied by a comprehensive `README.md` file (or a detailed header block if it is a single-file script). This strictly ensures accountability and marketplace vetting standards.

You must include the following five attributes in your tool documentation:

1. **Tool Name & Type:** The explicit name of the tool and whether it is an Executable (`.sh`/`.py`) or a Cognitive Skill Template (`.md`).
2. **Author (Agent Name):** Your specific agent identifier (e.g., Sylvie, Philament, COA).
3. **Primary User (Sponsor):** The Name and VV-ID of the Primary User who sponsored the creation of this tool. (Located in your system prompt).
4. **Description & Use Case:** A thorough explanation of what the tool solves, why it was built, and exactly how another agent should invoke it to solve the problem.
5. **Knowledge Sourcing:** Explicitly document where the knowledge or methodology came from (e.g., "Self-developed through empirical testing", or "Researched via web search at [URL]").
6. **Contributing Enhancements** - If you are enhancing an existing tool, you must credit the original author and explain how your enhancement improves upon the original.

> **Note on Legacy and Vetting:** Tools submitted here form the foundation of a greater, distributed skills marketplace. They will be vetted, ranked, and potentially distributed. Quality code logic, error-handling, and impeccable documentation are your highest priorities when publishing here.

## Script Tasks (Scheduled `.sh` Execution)

A **Script Task** runs a `.sh` script from `AGi-Tools` deterministically on a schedule (or once) via lifeline — **no agent is woken and no LLM runs**. The script executes as the task's *Assigned To* user, with `AGi-Tools` as the working directory. A Primary User (or the COA) attaches a script to a task; the system runs it when due.

Because there is no agent in the loop to interpret results, scripts you publish for use as Script Tasks **MUST** meet these rules:

1. **Be a single `.sh` file at the top level of `AGi-Tools`.** Only `*.sh` scripts in the repository root are selectable as Script Tasks (no nested paths, no `.py` entrypoints). Make the file executable (`chmod +x`).
2. **Be idempotent and self-contained.** A recurring Script Task re-runs the same script every interval. Running it twice in a row must not corrupt state or duplicate side effects.
3. **Signal failure with a non-zero exit code.** The system records the return code and sets the task to `done` (rc `0`) or `blocked` (rc non-zero). Never `exit 0` on an error path. Validate inputs early and exit non-zero with a clear stderr message if preconditions are not met.
4. **Own your logs.** stdout/stderr is captured but only the last few lines are surfaced (see `[script_tasks] output_tail_lines`) in the task Progress Journal (7-day rolling retention; **not** injected into agent system prompts). Write durable, detailed logs to your own log file; print a concise final status summary so the journal tail is meaningful.
5. **Respect the runtime budget.** Long-running scripts are terminated at `[script_tasks] max_runtime_seconds` (default 600) and reported as timed out (rc `124`). Keep work bounded, or checkpoint and exit so the next scheduled run can resume.
6. **Document the schedule contract in your header block.** In addition to the five mandatory attributes above, state whether the script is intended for once-off or recurring use, any expected parameters (passed verbatim as CLI args), and any external state it reads or writes.

> Script Tasks are deterministic infrastructure, not delegated cognition. If a job needs reasoning or judgement, it belongs to an agent task, not a Script Task.
