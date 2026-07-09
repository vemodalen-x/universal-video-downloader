# automation.spec — Full-Auto (unattended) mode

> Purpose: let VEMO run **without stopping for user approval** — for trusted batch/overnight/CI work — while
> still **recording every decision** and **keeping all mechanical safety rails**. Auto mode removes the
> *human-in-the-loop pauses*, not the *verification*. It is OFF by default and only the explicit command
> turns it on.

## 1. Activation (human at a terminal — the agent cannot enable it)
- Default: **OFF**. Auto mode never turns on implicitly.
- Enable: a **human** runs `vemo auto on [--max-tier R0|R1|R2] [--ttl <hours>] [--allow-r2]` in an
  interactive terminal. The command **refuses without a TTY and a typed confirmation** — an agent's shell
  tool has neither, so neither the agent nor injected text ("enable auto mode") can remove its own
  approval pauses. Reducing oversight must come from outside the loop being overseen; restoring it
  (`off`) may be automated.
- Enabling is itself **recorded** (who/when/ceiling/expiry) to `.vemo/auto_decisions.jsonl` and `.vemo/auto_mode.json`.
- Auto mode **expires** (default TTL 8h) — re-enable to continue. Disable any time: `vemo-auto off`.
- At session start the agent runs `task_state.py auto-status`; if active, it loads this spec and follows it.
- `skill/automation-mode` covers **status and disable only** — by design it cannot enable.

## 2. What auto mode REMOVES (approval pause → auto-decide + record)
Each of these normally stops and asks the user. In auto mode the agent **decides, records the decision +
one-line rationale, and proceeds** — provided the change is within `max_auto_tier`:
| Normal gate | Auto-mode behavior |
|---|---|
| R2 plan `human_review` | auto-approve plan, record `AutoApproved: plan` + rationale |
| Push confirmation | push once acceptance mechanically passes, record |
| Subtask review gate | continue to next subtask, record each |
| Failure disposition approval | pick disposition (`RCA-inline`/…), record, proceed |
| Stale-task takeover | auto-take-over **stale** tasks, record (still never force-takes an *active* foreign task) |
| Comment-review request | mark recorded, proceed |

## 3. What auto mode NEVER relaxes (hard invariants)
Auto mode must not touch any of these — they are mechanism, not human-approval:
1. **All `safety.spec` guards** — scope containment, destructive-command block, secret block, out-of-repo. (Hooks/CI do not read auto-mode; they always fire.)
2. **Risk-tier integrity** — diff-derived tier; no self-downgrade.
3. **Independent judge** — on every auto-approved R1+/R2 push, `judge.verdict == pass` **with a provenance
   record** (`.vemo/judge.jsonl`) is required; `gate-check acceptance-before-push` enforces this mechanically
   when auto mode is ON (`auto_mode.require_judge`). A `fail` blocks even in auto mode. The judge replaces
   the absent human reviewer, so auto mode makes it *more* mandatory, not less.
4. **Acceptance-before-push** — measurable criteria must pass with executed ground truth (`vemo verify`
   receipt); auto mode does not fabricate a pass.
5. **Run budget / stop rules** — an unattended run must be bounded: `run_budget.enabled: true` is
   required for auto mode, and `vemo-auto on` resets the counter. The budget guard
   (`enforcement/hooks/run.py budget`) **hard-stops** the run (exit 2) when a limit is hit while auto mode
   is ON — the mechanical answer to a Mythos-class model that would otherwise "run until cut off".

6. **Rule of Two / lethal trifecta** — a task touching all 3 of {private_data, untrusted_content,
   external_comms} (`trifecta-check` = 3/3) is **not auto-approvable**: auto mode stops and requires explicit
   human approval, regardless of risk tier (OWASP ASI01 — prompt-injection goal hijack).

## 4. Risk ceiling (safe defaults)
- `auto_mode.max_auto_tier` default **R1**: auto-proceed on R0/R1; **R2 still needs a human** unless enabled with `--allow-r2` (which requires an explicit confirmation token and is recorded as a high-trust grant).
- **Destructive commands**: in auto mode there is no user to approve, so they stay **blocked** unless listed in `auto_mode.preauthorized_commands` (default empty — explicit opt-in only).

## 5. Recording obligations (the whole point)
- Every auto-decision appends one line to `.vemo/auto_decisions.jsonl`:
  `{ts, session, task, gate, tier, decision, rationale, evidence}`.
- The task file gets an `## Auto-Mode Decisions` block mirroring those entries (durable, human-reviewable after the fact).
- A run done under auto mode is auditable end-to-end: a human can review *what was auto-approved and why* later, even though no one approved it live.

## 6. Disable / kill switch / expiry
- `vemo-auto off` (or keyword "disable auto mode") → immediate.
- TTL expiry auto-disables. Mechanical guards (`safety.spec`) cannot be disabled by auto mode at all.

## 7. Intent statement
Auto mode is a **velocity tool for trusted, bounded, low-blast-radius work** — not "nobody is watching".
It shifts trust from "a human approves each step" to "mechanism + the judge verify each step, and every
decision is recorded for after-the-fact review". Use it for R0/R1 batch work; reach for `--allow-r2`
only with eyes open.
