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
