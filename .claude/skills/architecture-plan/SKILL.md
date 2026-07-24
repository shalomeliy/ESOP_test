---
name: architecture-plan
description: Convert an approved feature specification into a minimal, reversible technical plan grounded in the existing repository. Use before editing application code.
---

# Architecture plan

Read the approved specification and trace the existing code paths. Treat the Spec as the product contract: it defines what the product should do and why. The Plan defines how to implement it and in what order. Do not reopen product decisions or design from memory.

Produce:

1. Current architecture and relevant data flow.
2. Proposed thin end-to-end slice.
3. Exact files to add or change and the responsibility of each change.
4. API or type changes, including failure behavior.
5. UI state model and data dependencies.
6. Tests at the cheapest meaningful layer.
7. Risks, rollback, and deliberate non-goals.
8. Ordered implementation steps with verification after each meaningful step.

After the participant approves the Plan, turn those ordered steps into a visible task list for execution:

- one bounded chunk per task,
- exactly one task in progress,
- verification as an explicit task,
- complete only after the nearest check produces evidence.

If a new product or architecture decision appears during planning, stop and return it to the participant instead of hiding it inside an implementation step.

Prefer existing dependencies and patterns. If the plan requires a new library, explain why the current stack cannot satisfy the requirement.
