---
# ── Machine-readable state (hooks & CI parse THIS; keep it accurate) ──
id: T-YYYYMMDD-xxx
risk: R1                       # R0 | R1 | R2  (lowest that fits)
state: PlanCreated             # PlanCreated|ReviewApproved|ImplementationDone|AcceptancePassed|ProcedureCompleted|Archived
scope_in: []                   # globs the hook allows edits within, e.g. ["src/fusion/**","tests/fusion/**"]
scope_out: []                  # explicitly excluded (documentation)
trifecta: []                    # lethal-trifecta props touched: private_data | untrusted_content | external_comms
                                # all 3 → explicit human approval required (Rule of Two); unattended auto must stop
acceptance:
  status: not_run              # not_run|passed|partial|failed|not_applicable
  build_exit: null             # cache of the `vemo verify` receipt — the gate trusts the receipt,
  smoke_exit: null             # not these numbers; evidence file must EXIST
  evidence: ""                 # path under .vemo/run/ (written by `vemo verify`)
judge:
  required: false              # set by risk tier + capability.tier
  verdict: null                # pass|fail — must match the judge's own `judge-record` entry
                               # in .vemo/judge.jsonl; a pasted verdict without that record blocks
approved_commands: []          # destructive cmds the USER approved this session, e.g. ["git reset --hard"]
owning_chat: ""                # chat-YYYYMMDD-HHMM-xxx
heartbeat: ""                  # ISO-8601, updated on every state write
---

# <Task title>

## Goal
One sentence: what done looks like.

## Scope (In / Out)
- In: <the files/areas this task may touch — mirror `scope_in` above>
- Out: <explicitly not this task>

## Pass/Fail Criteria  (EARS-style, measurable, falsifiable, attributed)
- [Correctness] WHEN <trigger>, the system SHALL <observable> — metric/threshold: <...>
- [Build] `<build cmd>` SHALL exit 0.
- [Performance] <metric> SHALL be <threshold>.

## Plan
Approach in 2–5 bullets. (R0: this can be a single inline sentence.)

## Execution Log
- <ts> <what happened, commands, exit codes, evidence path>

## Acceptance Result
Per-criterion PASS/FAIL + evidence. Any FAIL → disposition (RCA-inline|RCA-subtask|Criterion-revision|Known-limitation) + user approval where required.

## Conclusion
Outcome: accepted|partial|rejected · Decision: continue|stop|rollback · Key Evidence: <...> · Risk: low|med|high · Next Action: <...>
