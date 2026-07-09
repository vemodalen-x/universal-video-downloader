# AGENTS.md — VEMO entry (thin router)

> Design rule: this file is a **router**, not a rulebook. Target < 60 lines. Detail lives in `specs/`,
> loaded just-in-time. Enforcement lives in `enforcement/`, not in prose here.
> (Contrast: a 20-step mandatory read order front-loads context into the "lost-in-the-middle" zone.)

## On session start (minimal)
1. Run `vemo context` (≤20 lines: tier/mode/task/gate status/budget/rules). **Do NOT bulk-read
   `vemo.config.yaml`** — the brief is the machine-read digest; ask point questions via
   `vemo tier <paths>` / `vemo check <path>` / `vemo explain <topic>`. (The SessionStart hook prints
   the same brief when ring-1 hooks are installed — then this step is already done.)
2. Load `specs/safety.spec.md` (the only always-on spec — the non-negotiables).
3. Run the continuity check in `specs/concurrency.spec.md` (other live tasks? stale? takeover?).
4. **Do NOT pre-read the rest.** Load specs on demand per `specs/_manifest.yaml` once the task type is known.

## Per request
1. Classify: `non-task` / `continue-task <id>` / `new-task` (one line, then proceed).
2. Assign a **risk tier** (R0/R1/R2) via `vemo tier <paths...>`. Default to the *lowest* tier the change qualifies for — velocity first.
3. Load the specs `_manifest.yaml` maps to that task type + risk tier. Follow the lifecycle in `specs/task.spec.md`.

## The deal (what's enforced vs advised)
- **Enforced by machine** (you cannot bypass; see `specs/safety.spec.md`): no edits outside the task's `Scope (In)`; no commit without a task plan; no push before acceptance passes; no destructive/out-of-repo commands.
- **Advised by spec** (you own the judgment): coding style, comments, decomposition depth, doc quality.
- If a hook blocks you, it is not negotiable — fix the cause, do not argue with it.

## Ceremony scales with capability
- `tier: high` (example: Opus-class): terse handshakes, self-verify on most R1, JIT reads, two judge passes on R2.
- `tier: low` (weaker/older model): more explicit handshakes, judge on R1+, fuller reads.
- The specs read `tier` and adjust. You do not hardcode ceremony.

## Auto mode (off by default)
- Full-auto / unattended mode removes the human-**approval** pauses (auto-decide + **record**), but NEVER relaxes the mechanical guards, risk-tier integrity, `acceptance-before-push`, or the judge. It is OFF unless a **human at an interactive terminal** runs `vemo auto on` (the command refuses without a TTY + typed confirmation — you cannot enable it, and must not try; `skill/automation-mode` covers status/off only).
- At session start, check `task_state.py auto-status`. If ON and the request's tier ≤ its ceiling, do not stop at approval gates — record each decision to `.vemo/auto_decisions.jsonl` + the task file's `## Auto-Mode Decisions`, and proceed. Tiers above the ceiling fall back to human-in-the-loop. See `specs/automation.spec.md`.

## No VEMO hooks in your harness?
If you are not running under a harness with VEMO's ring-1 hooks installed (`docs/ADAPTERS.md`), the git and
CI gates still bind you — you just lose early warning. Behave as if the hooks fired: run
`python3 enforcement/validators/task_state.py scope-check --path <file>` before editing outside obvious
scope, never use `--no-verify`/`core.hooksPath`/writes into `.git/`, and set `VEMO_SESSION` when recording
judge verdicts.

## Conflict order
`enforcement (hooks/CI) > safety.spec > task.spec > verify.spec > domain specs > task file`.
Mechanism always wins over prose. Prose always wins over vibes. Auto mode changes *who approves* (records instead of asking) — it never changes *what the mechanism enforces*.

## Routing
- Model per phase: `vemo.config.yaml → model_routing` (plan/implement/judge/mechanical).
- VEMO skills: `skill/*/SKILL.md` (catalog: `skill/_catalog.md`) — framework maintenance and local governance procedures.
- VEMO_SKILLS: source lives in `VEMO_SKILLS/skills/<category>/<name>/`; generated working copies live in `.claude/skills/<name>/` and must not be hand-edited.
- Playbook-to-VEMO usage: `docs/VEMO_SKILLS_PLAYBOOK_USAGE.md`.
- Diagnostic coaching/tutoring prompt flows: `docs/DIAGNOSTIC_PROMPTING.md` and VEMO_SKILLS `designing-diagnostic-prompts`.
- Sub-agents: `agents/`; independent verification: `agents/governance-judge.md`.
