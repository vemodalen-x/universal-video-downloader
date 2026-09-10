---
id: T-20260905-downloader-review-reliability
risk: R1
change_class: standard
state: AcceptancePassed
scope_in: ["m3u8_core.py", "m3u8_desktop_app.py", "tests/test_m3u8_core.py", "tests/test_desktop_app.py", "README.md", "CHANGELOG.md", "tasks/T-20260905-downloader-review-reliability.md"]
scope_out: [".gitignore", "photo_archive/**", "vemo_photo/**", "eval/out/**", "enforcement/**", "specs/**", ".github/**"]
trifecta: []
acceptance: {status: passed, build_exit: 0, smoke_exit: 0, evidence: ".vemo/run/T-20260905-downloader-review-reliability-20260905-112449.log"}
judge: {required: false, verdict: null}
approved_commands: []
owning_chat: codex-20260905-downloader-review
heartbeat: 2026-09-05T03:10:00Z
---

# Downloader reliability review

## Goal
Review and improve download integrity, serial queue controls, and progress/history accuracy; push the tested downloader commits to the existing branch.

## Scope (In / Out)
- In: downloader core, desktop event handling, focused regression tests, and user documentation.
- Out: operational media downloads, private history/credentials, unrelated photo-product work, dependency upgrades, and release publishing.

## Plan
1. Review core transfer behavior and desktop state transitions with independent read-only reviewers.
2. Fix confirmed correctness and usability defects with regression coverage.
3. Run lint, complete downloader tests, desktop smoke, source privacy checks, and machine acceptance.
4. Commit scoped changes and push the current branch. The user explicitly authorized push on 2026-09-05.

## Pass/Fail Criteria
- [x] Correctness: covered invalid HTTP response scenarios never become completed media; range, size, ETag, restart, and refresh-type regression tests pass.
- [x] Usability: pause/stop target the active queue job and progress/history events stay ordered across items; regression tests and desktop smoke pass.
- [x] Build: all downloader tests and changed-file lint pass.
- [x] Privacy: tracked source checks pass without private media, credentials, or screenshots; only listed files are selected for staging.
- [ ] Delivery: machine acceptance succeeds and the remote branch matches the new commit.

## Execution Log
- 2026-09-05 Continued the authorized downloader review; previous integration is committed. Other stale photo tasks are outside scope and retain their ownership.
- 2026-09-05 Independent review confirmed query-based false deduplication, unsafe sole-candidate history retry, changing-representation corruption, refresh type mismatch, and history write races; fixes and regression tests implemented.
- 2026-09-05 Full test suite passed 206 tests before the final type-change parameter was added; targeted desktop suite then passed 44 tests. Changed-file Ruff and whitespace checks passed.
- 2026-09-05 Desktop smoke retained selection/focus/scroll with 2000 history rows (9.13 ms average refresh), enabled native pause controls, and repainted 64000 segment changes in 41.46 ms.
- 2026-09-05 Separate-process history smoke preserved all 50 records from two writers. Tracked source privacy scan passed 106 files.
- 2026-09-05 Machine acceptance recorded build=0 and smoke=0; full suite passed 207 tests. Receipt: `.vemo/run/receipt.json`.
- 2026-09-05 Final independent review found a validator-less EOF edge case. Added a one-byte validator probe; unchanged complete caches finalize and changed files remain blocked, including after restart. Seven focused resume tests passed; acceptance is being renewed after this fix.
- 2026-09-05 Renewed machine acceptance passed compilation and all 211 tests after the EOF fix. Final changed-file Ruff and whitespace checks passed; scoped source privacy scan passed 107 tracked files.

## Read Audit
- Reviewed direct download range handling, queue worker, event buffer, desktop history updates, and existing regression tests. Comments will be updated only where behavior changes.

## Conclusion
Outcome: implementation accepted. Decision: commit the scoped downloader changes and perform the user-authorized push after final independent review. Residual limits: providers without strong validators cannot guarantee detection of same-size content replacement; older query-stripped history/cache identities can require manual re-selection or a fresh download. Release binaries are outside this source-review task.
