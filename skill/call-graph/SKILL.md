---
name: call-graph
description: Query function call relationships (where X is defined, who calls X, what X calls, call chains, impact) using static-analysis tools. Use instead of manually reading code to trace calls — for debugging, impact analysis, refactoring, or flow tracing.
---

# Call Graph

Answer call-relationship questions with **ground truth**, not by eyeballing code. Deterministic core: `scripts/cg.py`.

**Priority — tool-first.** Use this before manually reading code to trace calls. Fall back to manual only if no
tool is installed *and* the user declines to install one. (Even a frontier model that *can* infer calls should
prefer the tool: it's verifiable and cheap.)

**When**
- auto: the agent needs call info during any task; `flow-discovery` calls it.
- manual: "who calls", "what does X call", "call chain", "impact of changing X".

**Prereqs** — Universal Ctags (defs) and/or cscope (best for callers/callees, C/C++); pyan3 (Python), madge (JS/TS).
`cg.py` detects what's installed and prints the install hint for what's missing — it never silently guesses.

**Use**
```bash
python3 skill/call-graph/scripts/cg.py index             # build/refresh index (.vemo/, gitignored)
python3 skill/call-graph/scripts/cg.py def     <symbol>  # Q1 where defined
python3 skill/call-graph/scripts/cg.py callers <symbol>  # Q2 who calls it
python3 skill/call-graph/scripts/cg.py callees <symbol>  # Q3 what it calls
python3 skill/call-graph/scripts/cg.py chain   <symbol> --depth 3   # Q4 forward tree
python3 skill/call-graph/scripts/cg.py impact  <symbol> --depth 3   # Q5 reverse tree
```

**Notes** — static analysis is best-effort: virtual dispatch, function pointers, macros, and templates can't be
fully resolved (note them as limitations). Excludes vendor/3rdparty/build dirs. This skill answers questions;
it does not write docs — for flow documentation use `flow-discovery`.
