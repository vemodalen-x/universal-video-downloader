---
id: T-20260907-downloader-interaction-review
risk: R1
change_class: standard
state: AcceptancePassed
scope_in: ["m3u8_desktop_app.py", "tests/test_desktop_app.py", "tools/desktop_ux_smoke.py", "README.md", "CHANGELOG.md", "tasks/T-20260907-downloader-interaction-review.md"]
scope_out: [".gitignore", "photo_archive/**", "vemo_photo/**", "eval/out/**", "enforcement/**", "specs/**", ".github/**"]
trifecta: []
acceptance: {status: passed, build_exit: 0, smoke_exit: 0, evidence: ".vemo/run/T-20260907-downloader-interaction-review-20260907-200941.log"}
judge: {required: false, verdict: null}
approved_commands: []
owning_chat: codex-20260907-downloader-ux
heartbeat: 2026-09-07T20:02
---

# Downloader interaction review

## Goal
Review and improve the desktop discovery, selection, and error-recovery flow, then commit and push the tested source changes to the existing branch.

## Scope (In / Out)
- In: desktop state transitions, selection controls, field validation, focused tests, synthetic desktop smoke, and user documentation.
- Out: downloader engines, real media downloads, user history, private screenshots, unrelated photo products, dependency upgrades, and release publishing.

## Plan
1. Inspect the actual desktop flow using isolated synthetic records and screenshots.
2. Fix stale-source downloads, empty-selection actions, hidden recovery inputs, and invalid concurrency handling.
3. Verify regression tests, desktop layout/state smoke, changed-file lint, source privacy, and machine acceptance.
4. Commit scoped changes and push the existing branch, as explicitly requested by the user on 2026-09-07.

## Pass/Fail Criteria
- [x] Correctness: changing an analyzed URL prevents downloading stale candidates; repeated analyze/start actions do not spawn overlapping workers.
- [x] Usability: empty selection disables download; batch selection counts and filename controls match the selected items.
- [x] Recovery: share-code errors reveal and focus the code field; invalid concurrency produces an actionable notice without starting a worker.
- [x] Quality: synthetic native-window smoke passes at default and minimum sizes without overlapping primary controls.
- [x] Build: full downloader tests, compilation, changed-file lint, and machine acceptance pass.
- [x] Privacy: source scan passes; commit includes no private URLs, credentials, history, or screenshots.

## Execution Log
- 2026-09-07 Started a new R1 interaction task. Existing downloader work is committed; unrelated photo-product changes remain untouched.
- 2026-09-07 Read-only review found stale source/selection state, hidden access-code recovery, and uncaught concurrency conversion errors.
- 2026-09-07 Native baseline reproduced enabled download with zero selected rows, stale candidates after source changes, and clipped queue controls. Implemented scoped state, validation, and layout fixes.
- 2026-09-07 Native smoke passed at 1220x840 and 1040x720 with 125 synthetic candidates, active-queue mouse-selection blocking, and access-code recovery. Screenshots remain in local temporary storage only.
- 2026-09-07 RCA-inline: two full runs exposed intermittent Tcl initialization failures when each test created a new interpreter. The fixture now mirrors one desktop session, explicitly resetting state between cases; full machine acceptance then passed 237 tests in `.vemo/run/T-20260907-downloader-interaction-review-20260907-200504.log`.
- 2026-09-07 Independent read-only review identified three residual interaction defects (busy selection, loading placeholder, stale recovery errors). Fixed all three and added regression coverage; 72 desktop tests pass. Final full acceptance is being renewed for those changes.
- 2026-09-07 Final independent review reported no remaining concrete blocker in the supplied changes. Compilation and all 239 tests passed; receipt evidence is `.vemo/run/T-20260907-downloader-interaction-review-20260907-200941.log`.
- 2026-09-07 Changed-file Ruff and staged whitespace checks passed. Source privacy scan passed all 109 tracked files after replacing the smoke script's absolute example output path with portable display text. Only the six scoped files are staged; screenshots and unrelated changes are excluded.

## Read Audit
- Read desktop initialization, discovery worker, candidate selection, queue startup, history recovery, existing tests, and applicable task/safety specifications.

## Conclusion
Outcome: implementation accepted. Decision: commit and perform the user-authorized push to the existing branch. Evidence: 239 passing tests, default/minimum native-window smoke, lint, source privacy scan, and executed machine receipt. Risk: provider compatibility is unchanged; live-provider downloads and packaged EXE publishing are outside this task. Next action: verify the remote branch matches the committed source.
