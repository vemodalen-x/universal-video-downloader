---
id: T-20260910-downloader-workflow
risk: R1
change_class: standard
state: AcceptancePassed
scope_in: ["m3u8_core.py", "tests/test_m3u8_core.py", "m3u8_desktop_app.py", "tests/test_desktop_app.py", "tools/desktop_ux_smoke.py", "README.md", "CHANGELOG.md", "tasks/T-20260910-downloader-workflow.md"]
scope_out: [".gitignore", "photo_archive/**", "vemo_photo/**", "eval/out/**", "enforcement/**", "specs/**", ".github/**"]
trifecta: []
acceptance: {status: passed, build_exit: 0, smoke_exit: 0, evidence: ".vemo/run/T-20260910-downloader-workflow-20260910-215118.log"}
judge: {required: false, verdict: null}
approved_commands: []
owning_chat: codex-20260910-downloader-workflow
heartbeat: 2026-09-10T21:52
---

# Downloader workflow completion

## Goal
Review and complete the batch-download workflow with searchable ordered selection, recoverable queued items, and truthful accessible progress motion. Commit verified source changes.

## Scope (In / Out)
- In: native desktop interactions, queue planning/history persistence, same-session unfinished queue recovery, reduced-motion option, tests, synthetic desktop captures, documentation.
- Out: new provider engines, live user downloads, credentials in git, release binaries, other products, framework changes, push/release.

## Plan
1. Inspect the native flow and review selection, queue, history and progress state handling.
2. Add search/natural ordering, persist all queued outputs before worker start, and retry unfinished items into their original paths.
3. Add bounded progress interpolation and busy-state motion with reduced-motion support and terminal cleanup.
4. Run regression tests, native smoke at both window sizes, lint/privacy checks and machine acceptance; commit only scoped files.

## Pass/Fail Criteria
- [x] Correctness: visible selection order equals execution order; filtering cannot download hidden selections.
- [x] Recovery: all queued items are saved before execution, including untouched items after stop/restart; unfinished retry preserves paths/IDs and excludes completed items.
- [x] Motion: preparing/combining states are visibly busy, determinate progress never exceeds reported values, and reduced motion/terminal states stop animation callbacks.
- [x] Quality: search/retry/progress controls are visible at 1220x840 and 1040x720 using synthetic native smoke.
- [x] Verification: full tests, lint, source privacy scan and executed acceptance pass before scoped commit.

## Execution Log
- 2026-09-10 New R1 task; prior downloader task remains accepted and its ownership is unchanged. Continuity doctor reports no active fresh conflicting task; unrelated photo files remain untouched.
- 2026-09-10 Captured the existing native selection/history flows in .vemo/run/ux-20260910-before. Confirmed missing list search/sort, missing pre-persisted queued tasks and ambiguous preparation/finalization progress.
- 2026-09-10 Expanded scope to the existing history store and its tests: atomic bulk upsert is required before launching a queue; unfinished entries must not be evicted by completed-history retention.
- 2026-09-10 Implemented search/natural ordering, persisted complete queues with stable retry IDs, original-path unfinished retries, queued/unfinished filters, truthful progress motion and reduced-motion controls. Resolved minimum-window clipping found during native smoke and kept untouched queued rows in stable order.
- 2026-09-10 Full suite passed 289 tests before final review; native smoke passed both window sizes and exercised 125-item queue recovery, filtering, actual progress movement, reduced motion and terminal timer cleanup. Captures use synthetic data only and remain in .vemo/run/ux-20260910-after.
- 2026-09-10 Final review also fixed busy-motion reset after a stopped queue and positive subsecond ETA formatting. Final machine acceptance passed compilation and 291 tests in .vemo/run/T-20260910-downloader-workflow-20260910-215118.log; Ruff and source privacy scanning passed (111 tracked files).

## UX Review
1. Media search and ordering: PASS. Current captures media-search.png and selection-1040x720.png show visible search/order controls; native tests prove hidden selections are removed and execution uses visible order.
2. Download feedback: PASS. queue-progress.png plus timed native checks prove bounded movement, readable progress and reduced-motion/terminal cleanup.
3. Queue recovery: PASS. queued-history.png shows all 125 planned items retained after stop, with the failed item and untouched items distinguished; tests verify original paths and IDs on retry.
All captures are under .vemo/run/ux-20260910-after and excluded from git. Screenshots do not establish screen-reader compliance; no external provider compatibility or release-binary claim is made.

## Read Audit
- Inspected desktop candidate selection, queue worker/callbacks, history store and recovery, progress estimator, synthetic native smoke and regression fixtures.

## Conclusion
Outcome: accepted. Decision: commit the eight scoped source/test/documentation files. Key evidence: 291 tests, native flow checks at both window sizes, Ruff and source privacy scan. Risk: new workflow is verified with synthetic data; live provider access remains dependent on existing engines. Next action: local git commit only; no push, merge or EXE rebuild in this request.
