---
name: flow-discovery
description: Discover and document a project's primary call flow as a flow spec, using the call-graph skill. Use when the code you're about to change has no flow doc, or the change isn't covered by an existing flow.
---

# Flow Discovery

Generate a `*.flow.md` from **real call chains**. Delegates all call tracing to `skill/call-graph` — it never
reads source itself.

**When**
- auto: a code-change task starts and no existing flow covers the touched files.
- manual: "discover flow", "map the call flow", "analyze flow".
**Not when** — docs-only / spec-only changes; or an existing flow already covers the files.

**Steps**
1. Confirm the entry point(s) with the user (the human owns "where does this start").
2. `cg.py chain <entry> --depth <n>` to get the function-level tree.
3. Group the level-1 calls into pipeline stages (Init → Load → Process → Output …).
4. Show the tree + proposed stages; get an OK.
5. Write `master_spec/flow_spec/<name>.flow.md` (or your project's flow location) and register it in the catalog.

**Notes** — user confirms the entry; the agent traces + documents. The generated flow is a *starting point*:
carry over any `[virtual]` / `[function-pointer]` limits `cg.py` flagged, for manual review. Output is
instance-owned (project-specific), not template-owned.
