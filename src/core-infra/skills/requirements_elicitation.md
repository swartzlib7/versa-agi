# Requirements Elicitation Protocol (5W1H)

> **Trigger:** New work request detected by triage — apply this BEFORE starting any technical work.

> **Harness tools:** Examples use shell form (`agictl group …`). In a work cycle, call the matching tool (`agictl_task`, `agictl_cycle`, …) and pass only the part **after** `agictl` as the `command` argument. Never prefix `agictl` in the argument. Full map: **cli_reference_agent.md** (*Harness tool invocation*).

## The 5W1H Matrix

Evaluate the request against these 6 dimensions:

- **What (Scope):** Exactly what entity, UI component, or data model is being modified or built?
- **Why (Motivation):** What is the underlying strategic rationale or value proposition?
- **When (Triggers):** In what state, lifecycle hook, or event stream does this execute or scale?
- **Where (Context):** What are the environmental boundaries? Where does this physically live?
- **Who (Actor):** Who triggers this action? (Primary User, Sub-Account, System, Connection).
- **How (Mechanism):** What is the exact technical API, data flow, or implementation logic?

## Consistency Test (Architectural Drift)

Does this new concept conflict with previously established immutable data or architectural pillars? If conflicting, raise an architectural discrepancy alert.

## Elicitation Interview

If any 5W1H dimension fails or the request is ambiguous, the task is **Undefined**:

1. **Announce the Shift**: Advise the sender you are shifting into Requirements Elicitation to gather clarifications before work begins.
2. **Conduct the Interview**: Present missing dimensions as specific questions. Support conversational back-and-forth:
   - Accept provided data piece-by-piece or by attachment (image, link, markdown).
   - Acknowledge received context and explicitly point out remaining missing dimensions until the matrix is fully satisfied.
3. **Handle Formulation Assistance**: If the user asks for help formulating architecture, generate documented assumptions labeled `[User Requested Assumptions]`.
4. **Offer Feasibility Research**: For new constructions, proactively offer to check if existing OSS or paid options solve the need.

**NEVER begin code execution on assumed parameters or inferred mechanisms.** Once the matrix is satisfied, identify if an existing Project can house the requirements, or create a new Project to track implementation before generating a Work Breakdown Structure (WBS).
