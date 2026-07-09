# task.spec — Risk-tiered lifecycle (velocity engine)

> Core idea: **ceremony scales with blast radius and inversely with model capability.** A typo and a
> schema migration do not deserve the same process. Default to the lowest risk tier that fits.

## 1. Risk tiers (from `vemo.config.yaml → risk_tiers`)

| Tier | Typical change | Lifecycle | Gates |
|---|---|---|---|
| **R0** trivial | docs, comments, config tweak | `plan_inline → implement → done` | inline plan only |
| **R1** standard | normal feature/bugfix in `src/lib/app` | `plan → implement → verify → done` | plan + acceptance |
| **R2** critical | core / auth / migrations / infra | `plan → review → implement → verify → procedure → archive` | + human review + judge + release procedure |

The agent picks the tier in one line: `RiskTier: R1 (path=src/foo.ts, no critical match)`.
Escalate mid-task if scope grows; never silently downgrade (downgrade requires the user).

## 2. Machine-readable state (this is what hooks read)

Every task file (`tasks/<id>.md`) opens with YAML front-matter. Hooks and CI parse it deterministically —
no prose-guessing. See `tasks/_TASK_TEMPLATE.md`.

```yaml
---
id: T-2026-0616-a3f
risk: R1
state: ImplementationDone      # PlanCreated|ReviewApproved|ImplementationDone|AcceptancePassed|ProcedureCompleted|Archived
scope_in: ["src/fusion/**", "tests/fusion/**"]   # hooks BLOCK edits outside this
acceptance: { status: passed, build_exit: 0, smoke_exit: 0, evidence: ".vemo/run/123.log" }
judge: { required: false, verdict: null }
approved_commands: []          # destructive cmds the USER approved this session (command-guard escape hatch)
owning_chat: chat-20260616-1410-x7q
heartbeat: 2026-06-16T14:55
---
```

Two integrity notes: (1) `acceptance:` is a human-readable **cache** — when `paths.build/smoke` are
configured, the push gate trusts the `vemo verify` receipt and the evidence file's existence, not these
numbers. (2) `judge.verdict` requires a matching provenance record written by the judge itself
(`judge-record`), and R2 may require multiple contiguous pass records by `capability.tier`; a verdict pasted
here alone blocks. On task start, bind the session:
`task_state.py bind --session <id> --task <id>` (see `concurrency.spec §1`).

## 3. Ceremony matrix (derived from `capability.tier`)

| Step | tier=high (Opus 4.8) | tier=medium | tier=low |
|---|---|---|---|
| Plan handshake | 1 terse line | explicit block | explicit block + user echo |
| Spec reads | headers JIT, pull on demand | listed specs in full | listed specs in full + re-confirm |
| Self-verify | agent self-checks, judge on R2 | judge on R1 + R2 | judge on R1 + R2 |
| Step size | larger autonomous steps | medium | small, frequent checkpoints |

Rationale: a stronger model needs *outcomes + verification*, not step-by-step prescription. As you upgrade
the model, raise the tier and the framework gets lighter automatically.

**`tier=frontier` (Mythos-class, e.g. Fable 5):** the lightest prescription of all — trust long-horizon
self-planning and the model's own tests/self-checks. But autonomy raises the value of guardrails, not lowers
it, so frontier work **must** run under `run_budget` stop rules (these models "run until the harness cuts
them off") and still gets the independent judge on R2. Route frontier models (`model_routing.long_horizon`)
to long-horizon/R2/migration only — never R0/R1 (2× cost; "renting a truck to deliver a letter").

## 4. Lifecycle gates (what each state requires)

- **PlanCreated** — task file exists with `Goal`, `Scope (In/Out)`, `Pass/Fail Criteria` (measurable, EARS-style; see verify.spec). R0 may inline this.
- **ReviewApproved** *(R2 only)* — human approved the plan.
- **ImplementationDone** — changes within `scope_in` only (hook-enforced). Read-audit appended if source was read.
- **AcceptancePassed** — `verify.spec` criteria evaluated PASS/FAIL with a real evidence file; when build/smoke are configured, produced via `vemo verify` (machine receipt). Failures get a disposition (`RCA-inline|RCA-subtask|Criterion-revision|Known-limitation`). The pre-push/CI gate checks this for **R1+** (R0 has no acceptance gate — that is the point of R0).
- **ProcedureCompleted** *(R2 / code release)* — release procedure run; commit-message + push are separate human-confirmed gates.
- **Archived** — moved to `tasks/Archive/` after self-check.

A gate is **blocked** by mechanism, not by hoping the model complies (see safety.spec). If `enforcement.hooks=false` the spec still asks, but CI backstop remains authoritative.

**Under auto mode** (`specs/automation.spec.md`, off by default): the human-**approval** gates — `ReviewApproved`, push confirmation, subtask review, failure-disposition approval, stale-task takeover — are auto-satisfied **and recorded** (one line + rationale to `.vemo/auto_decisions.jsonl` and the task's `## Auto-Mode Decisions`), provided the tier ≤ the auto ceiling. The **mechanical** gates (scope, risk-tier integrity, `acceptance-before-push`) and the **judge** are unchanged; the judge is *required* on auto-approved R1+/R2 because it replaces the absent human reviewer.

## 5. Conclusion block (every task)
`Outcome (accepted/partial/rejected) · Decision (continue/stop/rollback) · Key Evidence · Risk · Next Action`.
Failed directions must end `stop`/`rollback` with an explicit next-direction note.

**Execution Log discipline (token economy):** one line per event — `timestamp what-changed evidence-ref`.
The log is a flight recorder, not a diary: narration/rationale lives in the Conclusion, evidence lives in
`.vemo/run/`. Stamp `heartbeat:` with `vemo heartbeat` (in-place, machine-written) instead of hand-editing.

## 6. Anti-bloat rule (self-governance)
This spec must stay < 120 lines. New rules are classified `baseline` (reusable → here) or `task-specific`
(→ the task file). Default task-specific. If this file grows past the cap, split a domain overlay instead.
