---
id: T-20260907-downloader-queue-review
risk: R1
change_class: standard
state: AcceptancePassed
scope_in: ["m3u8_core.py", "m3u8_desktop_app.py", "tests/test_m3u8_core.py", "tests/test_desktop_app.py", "tools/desktop_ux_smoke.py", "README.md", "CHANGELOG.md", "tasks/T-20260907-downloader-queue-review.md"]
scope_out: [".gitignore", "photo_archive/**", "vemo_photo/**", "eval/out/**", "enforcement/**", "specs/**", ".github/**"]
trifecta: []
acceptance: {status: passed, build_exit: 0, smoke_exit: 0, evidence: ".vemo/run/T-20260907-downloader-queue-review-20260907-204019.log"}
judge: {required: false, verdict: null}
approved_commands: []
owning_chat: codex-20260907-downloader-queue
heartbeat: 2026-09-07T20:43
---

# Downloader queue and recovery review

## Goal
Fix confirmed queue/export state defects and improve local history and long-running log interactions, then commit and push the verified changes.

## Scope (In / Out)
- In: HLS partial export serialization and integrity, desktop queue state, local history browsing, bounded logs, regression tests, synthetic desktop smoke, and documentation.
- Out: live media downloads, user history, credentials, screenshots in git, other products, governance changes, and release binaries.

## Plan
1. Review the existing worker, HLS combine lifecycle, history actions, and logging.
2. Fix confirmed defects with focused regression coverage and preserve existing public protocols.
3. Run full tests, native smoke, lint, privacy checks, and machine acceptance.
4. Commit only scoped files and push the existing branch as authorized by the user.

## Pass/Fail Criteria
- [x] Correctness: partial-export failure does not unlock or fail the download queue, and duplicate export requests are rejected.
- [x] Integrity: empty/zero-byte HLS caches cannot produce an exported file; concurrent final/partial combines and cache cleanup are serialized.
- [x] Usability: each new queue item resets progress; history directory opening resolves provider-specific output without creating missing folders.
- [x] Performance/privacy: logs have a tested line limit, redact sensitive text, and retain the viewport when the user is reading older entries.
- [x] Build: full tests, changed-file lint, native smoke, and executed machine acceptance pass.
- [x] Delivery preparation: tracked source privacy scan passes and only scoped source/tests/docs are staged for commit.

## Execution Log
- 2026-09-07 Started a new R1 review after the preceding interaction task was committed and pushed. Existing unrelated photo-product changes remain untouched.
- 2026-09-07 Confirmed export exceptions could unlock the running queue, empty caches could publish empty files, history browsing could create missing directories, and log scrolling/growth were uncontrolled. Implemented fixes with regression coverage.
- 2026-09-07 Detected overlapping edits from the existing governance task; coordinated a stop without reverting its changes. That task confirmed no staging, commit, or push. Integrated and reviewed its nonblocking export lock, unique temporary output cleanup, nearest-existing-parent browsing, and tests under this task's existing ownership.
- 2026-09-07 Full suite passed 267 tests; native smoke passed at 1220x840 and 1040x720, including history feedback and log scrolling. Added one final actual-output-size regression and renewed acceptance for the complete changes.
- 2026-09-07 Final machine acceptance passed compilation and all 268 tests in .vemo/run/T-20260907-downloader-queue-review-20260907-204019.log. Changed-file Ruff checks passed; tracked source privacy scan passed for 110 files. Native screenshots contain synthetic data and remain outside git.

## Read Audit
- Reviewed queue callbacks, partial export threading, HLS combine/cache cleanup, history opening, log insertion, and existing unit tests.

## Conclusion
Outcome: accepted. Decision: commit the eight reviewed source/test/documentation files and push the existing branch as requested. Risk: local export/state handling only; external provider compatibility remains unchanged and was not live-tested. No release binary was rebuilt in this source-only task. Next action: scoped commit and remote hash verification.
