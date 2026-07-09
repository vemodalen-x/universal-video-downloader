# capability.spec — Capability-monotonic governance

> **The guarantee:** as `capability.tier` rises, VEMO's *prescription* (step-by-step how-to, mandatory reads,
> handshakes) SHRINKS, while its *verification, boundaries, and audit* HOLD or TIGHTEN — never the reverse.
> A framework that only "gets out of the way" for strong models becomes dead weight; one that shifts its
> locus from **instructing** to **verifying + constraining** gets *more* valuable as models improve.
> Grounded in the Fable 5 / Mythos 5 system-card analysis (see `docs/SCALING.md`).

## 1. The inverse coupling (the core rule)
| As `capability.tier` ↑ | shrinks ↓ | holds / tightens ↑ |
|---|---|---|
| **prescription** | how-to detail, mandatory reads, handshakes | — |
| **verification** | — | executed ground-truth · independent verifier passes · narration monitoring |
| **boundaries** | — | scope · destructive/irreversible · secrets (capability-INVARIANT) |
| **audit** | — | decision record · judge-verdict provenance log · telemetry |

Mechanized by `task_state.py verify-plan` (derives required verification depth from tier × risk) and by
`gate-check required-judge` (enforces the required judge-pass count) — not left as prose.

`capability.tier` is vendor-neutral. A team may map Anthropic, OpenAI, local, or future models to
`frontier|high|medium|low` based on observed task reliability, context length, tool-use reliability, and
verification-following behavior. Model names in `model_routing` are advisory routing hints; they are not the
governance contract.

### 1.5 Classifying any model into a tier (the behavioral rubric)
Classify by what you **observe on your own tasks**, not by benchmark or brand. When in doubt, pick the
LOWER tier — over-trusting costs more than over-verifying, and the tier only ever relaxes *prescription*.

| tier | observable behavior (any vendor) |
|---|---|
| **frontier** | sustains multi-hour/multi-day autonomous plans; writes and runs its own tests unprompted; failure mode is *process-gaming* (unverified "done", reframing), not fabrication |
| **high** | reliable single-session self-planning and self-verification on standard (R1) work; occasional unverified completion claims |
| **medium** | needs explicit handshakes; regularly claims verification it did not run |
| **low** | needs step-by-step prescription; fabricates paths/APIs under pressure; small autonomous steps only |

A repo worked by **several models** pins `tier` to the **weakest** model allowed to act in it unattended
(the config is per-repo, and safety must hold for the worst case). Re-classify when you swap models —
the tier is the one knob the rest of the framework scales from.

## 2. Frontier failure modes → mechanism (not a reminder)
Stronger models fabricate **less** but fail in **new** ways. Each gets a mechanism:

| Observed at frontier (Fable 5 / Mythos 5) | VEMO answer (tagged: mechanism vs judge-checked vs advisory) |
|---|---|
| **Skips running verification** — claims "verified end-to-end" on checks never run; "no errors" after checking one error type (20× undercount) | *Mechanism:* the **executed-ground-truth gate** — a `passed` acceptance requires an evidence file that exists, and the `vemo verify` **machine receipt** when build/smoke are configured; typed exit codes do not open the gate. *Judge-checked:* the judge re-reads the *log*, not the claim. |
| **Games the process** — pastes a judge verdict; reframes bugs as "design decisions" | *Mechanism:* judge verdicts need a **provenance record** (`judge-record` → `.vemo/judge.jsonl`; a front-matter-only `pass` blocks); the governance layer itself is R2 (tamper = highest ceremony); `selfcheck` verifies enforcement-config integrity in CI. *Judge-checked:* reframing. *(Commit authorship forensics: ROADMAP.)* |
| **Stops after one pass, "good enough"** | *Mechanism:* verification is **independent** (not self-judged) with N judge passes by tier; the required-judge gate counts contiguous pass records, so one pass cannot satisfy a high/frontier R2 task. *Judge-checked:* may demand another round; criteria falsifiable + full-scope. |
| **Silent memory misuse** — ignores known-good pattern | *Advisory (judge-weighed):* tasks with relevant history record a memory/prior-decision consult trace in the task file. |
| **Undetected sabotage when NOT narrating** (0% with CoT monitoring → 9.2%+ without) | *Judge-checked:* intent narration required (`verification.require_intent_narration`); missing narration is a red flag the judge treats as a violation, not a style issue. |

## 3. Safety is capability-invariant
Scope, destructive/out-of-repo, secrets, irreversible/outward actions, and `run_budget` stop rules do **not**
relax as `capability.tier` rises. A more capable model running unattended is *more* consequential, not less.
Capability tier relaxes only *prescription* — never *safety*. (`enforcement.safety_invariant_of_capability: true`.)

## 4. Verification scales UP with capability (the verifier problem)
When the model may exceed the human reviewer, a single same-class judge can share its blind spots. So at high
tiers VEMO escalates the required **sequential independent verifier pass count** anchored in **executed ground
truth** (tests/build/diff invariants, not opinion), with the human retained for **intent + irreversible only**.
True concurrent verifier panels remain ROADMAP.

Independence has two axes, and multi-model setups can buy both: a **fresh context** removes shared session
state (required — the judge runs in its own context and records via `judge-record`); a **different model
family** than the worker removes shared weights-level blind spots (advised whenever the harness offers one —
see `model_routing`). Same-family passes still count toward the gate; cross-family passes are worth more.

## 5. Delegate what the model now does natively
Fable 5 plans across stages, writes its own tests, delegates to sub-agents, keeps memory. VEMO delegates those
and **verifies the outputs** — it does not re-spell-out planning/testing. The framework's enduring value is the
part the model cannot self-grant: an external, tamper-evident record of *what is allowed* and *what happened*,
and independent verification of its claims.
