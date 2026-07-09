# safety.spec — Non-negotiables (MECHANICALLY ENFORCED)

> Every rule here is tagged with **who enforces it** — a hook, a git gate, or CI; not the model's goodwill.
> These tags are a **checked contract**: `selfcheck` fails if a tag points at a mechanism that does not
> exist (label accuracy is itself a safety property). This is the only spec loaded at every session start.

## Enforcement legend
- `ENFORCED-BY: hook` — blocked client-side in the agent loop (`enforcement/hooks/run.py`; fast feedback,
  bypassable by a hostile host — the guarantee is below). This ring is a **harness adapter**: the Claude Code
  wiring ships (`install.sh`); any other harness can pipe the same JSON payload into the same dispatcher
  (`docs/ADAPTERS.md`). A harness with no adapter simply has no fast-feedback ring — the git and CI rings
  below bind **any** agent, any model, any harness.
- `ENFORCED-BY: git` — local `pre-commit` / `pre-push` (survives a hookless agent; `--no-verify` defers to CI).
- `ENFORCED-BY: ci` — server-side (`enforcement/ci/vemo-ci.yml` + branch protection). **This is the
  authoritative layer**; everything client-side is fast feedback for it.

## The list

1. **Scope containment.** No create/edit/delete outside the active task's `scope_in` globs.
   `ENFORCED-BY: hook+git+ci` → `enforcement/hooks/run.py` (edit guard), `enforcement/ci/pre-commit`.
   *Honest coverage note:* the hook contains the **Edit/Write tools**; file writes routed through **Bash**
   (redirects, `tee`, `sed -i`) get a client-side *warning* only — an enumerated blacklist cannot contain an
   open command space. Containment is **guaranteed at commit/CI**, where the diff shows every write however
   it was made.

2. **Plan-before-commit.** No commit touching code unless a task file with a matching plan + `scope_in` exists.
   `ENFORCED-BY: git+ci` → `enforcement/ci/pre-commit` (reads task front-matter via `validators/task_state.py`).

3. **Acceptance-before-push.** No push of an R1+ task below `AcceptancePassed`, and a `passed` acceptance must
   carry **executed ground truth**: the evidence file must exist, and when `paths.build`/`paths.smoke` are
   configured the gate trusts only the machine receipt written by `vemo verify` — never exit codes typed into
   front-matter. `ENFORCED-BY: git+ci` → `enforcement/ci/pre-push`, re-run in CI.

4. **No destructive / out-of-repo commands** without explicit approval recorded in the task file
   (`approved_commands:` list): broad `rm -rf`, `git reset --hard`, `git checkout -- <file>`,
   `git clean -f`, plain `git push --force`, `find -delete`, `xargs rm`, writes outside `repo_root`,
   privilege escalation, pipe-to-shell installs — and **git-gate evasion/tamper**: `--no-verify` on
   commit/push/merge, `core.hooksPath` redirection, writes into `.git/`.
   `ENFORCED-BY: hook` → `enforcement/hooks/run.py` (command guard, PreToolUse on Bash).
   *Honest coverage note:* this is a **blacklist** — it raises the bar, it cannot be complete. For untrusted
   work, pair VEMO with OS/container sandboxing (see `SECURITY.md`); VEMO is a cooperative-host layer.

5. **No secrets in a diff.** Content writes and staged/pushed diffs are scanned for credential/key patterns.
   `ENFORCED-BY: hook+git+ci` → `enforcement/hooks/run.py` (edit guard), `enforcement/ci/pre-commit`.

6. **No editing generated binaries / model blobs** (`*.a *.so *.dll *.exe *.bin`, model weights;
   `exclusions.third_party` vendor drops are exempt).
   `ENFORCED-BY: hook` → `enforcement/hooks/run.py` (edit guard, `blob-check`).

## The governance layer protects itself
`enforcement/**`, `.claude/**`, `.github/**`, `vemo.config*.yaml` and `specs/**` are **R2 by default**
(`risk_tiers`), and unmatched paths default to **R1** (`risk_tiers.unmatched`) — so weakening a guard, editing
hook registration, or rewriting these rules takes the *highest*-ceremony path (human review + judge), not the
lowest. Additionally `selfcheck` (run in CI) flags hooks deregistered from `.claude/settings.json` and
`ENFORCED-BY` tags pointing at missing mechanisms. `ENFORCED-BY: git+ci` via risk-tier integrity.

The judge provenance log `.vemo/judge.jsonl` is **tracked by git** (the rest of `.vemo/` is per-machine
runtime state and stays ignored): the required-judge gate's authority is CI, and CI can only read records
that are in the repo — while git history is what makes an append-only log tamper-evident. `selfcheck` fails
if the log is gitignored.

## When a gate fires
- The action is refused with a one-line reason + the rule id. The agent must address the **cause**, not retry
  verbatim, and must not attempt to disable the hook (see above — that path is R2 + judge).

## Failure semantics (a missing guard is not a safe guard)
Guards in `enforcement.fail_closed` **block** when the check itself errors; others degrade to a loud warning
plus a telemetry record. `enforcement.degrade_gracefully: true` is the explicit opt-in to fail-open — even
then, degradation is logged, never silent.

## Guardrail elements & rollout
The rules above are the **Permission** + **Audit** elements; the other two canonical guardrail elements:
- **Approval** — human-in-loop gates (plan/review by risk tier; the human owns intent + irreversible). See `task.spec` / `capability.spec`.
- **Kill switch** — `vemo auto off`, `run_budget` hard-stop (unattended), and `enforcement.mode: monitor` (observe-only).

**Action-chaining:** a low-risk action (e.g. a read) chained into a high-risk one (exec / push / destructive)
does not escape these guards — each action is checked, and the high-risk step still hits its risk-tier gate + the judge.

**Rule of Two (lethal trifecta · OWASP ASI01):** a task touching all three of {`private_data`,
`untrusted_content`, `external_comms`} (declared in the task `trifecta:` field) requires explicit **human
approval** before acting — `task_state.py trifecta-check` blocks at 3/3 (checked at push; unattended auto
sessions must stop). *Honest coverage note:* `trifecta:` is **self-declared** by the agent — this gate bounds
honest mistakes and raises the audit bar; deriving the properties from observed tool usage is on the ROADMAP.

**Monitoring mode:** `enforcement.mode: monitor` logs violations without blocking (for onboarding/tuning); flip
to `enforce` once calibrated. Honored by every guard in the single dispatcher. Blocks are always logged —
you get the audit trail first.

## Why so short
Non-negotiables you can enforce are few. Everything else is advice and belongs in the domain specs,
loaded on demand. A long safety spec is a smell: if it can't be a hook, it isn't really "hard".
