---
id: T-20260818-batch-queue-continuation
risk: R1
change_class: standard
state: AcceptancePassed
scope_in: ["m3u8_desktop_app.py", "README.md", "CHANGELOG.md", "tests/test_desktop_app.py", "tasks/T-20260818-batch-queue-continuation.md", ".vemo/run/T-20260818-batch-queue-continuation*.log", "build/**", "dist/**"]
scope_out: ["G:/pmv/**", "m3u8_core.py", "tests/test_m3u8_core.py", ".gitignore", "requirements*.txt", "UniversalVideoDownloader.spec", "photo_archive/**", "vemo_photo/**", "eval/out/**", ".github/**", "enforcement/**", "specs/**"]
trifecta: []
verification:
  profile: focused
  commands:
    test: "python -m pytest -q"
    lint: "python -m ruff check m3u8_desktop_app.py tests/test_desktop_app.py"
    smoke: "python -m pytest -q tests/test_desktop_app.py -k download_queue"
acceptance:
  status: passed
  build_exit: 0
  smoke_exit: 0
  evidence: ".vemo/run/T-20260818-batch-queue-continuation-20260818-225659.log"
judge:
  required: false
  verdict: null
approved_commands: ["powershell -ExecutionPolicy Bypass -File .\\build.ps1"]
owning_chat: "codex-20260818-batch-queue-continuation"
heartbeat: 2026-08-18T22:56:59+08:00
---

# Continue a serial batch after one item fails

## Goal
Keep a 50-item selection strictly serial while allowing later items to start after one item exhausts its retries.

## Scope (In / Out)
- In: desktop queue control flow, user-facing queue summary, regression tests, documentation, and a local Windows rebuild.
- Out: changing direct-download integrity or retry behavior, touching user media, publishing, unrelated release files, and photo archive work.

## Human Authorization
The user reported that a 50-item selection stops after one item and requested continuous batch behavior. The prior request for a rebuilt Windows client remains active for delivering this correction. No user media or private share access is required.

## Pass/Fail Criteria
- [x] [Serial] WHEN several items are selected, at most one download job SHALL run at a time.
- [x] [Continuation] WHEN one item fails after its bounded retries, the queue SHALL record that failure and start the next item unless the user explicitly stopped the queue.
- [x] [Output] WHEN an item fails, no incomplete target video SHALL be published; its existing internal resume cache SHALL remain available.
- [x] [UX] WHEN the queue finishes with failures, the summary SHALL report completed and failed counts without claiming the queue was paused.
- [x] [Regression] WHEN focused queue tests, the full suite, Ruff, and diff checks run, all SHALL pass.
- [x] [Build] WHEN the Windows package is rebuilt, package scanning and an isolated EXE smoke launch SHALL pass.

## Plan
- Remove the failure-triggered queue halt while preserving the existing serial loop and explicit-stop behavior.
- Restore a regression test proving a failed first item does not block a successful second item.
- Update queue messaging and documentation to distinguish serial execution from stop-on-failure.
- Run acceptance, rebuild the Windows package, and smoke-test the packaged executable.

## Execution Log
- 2026-08-18T22:53:05+08:00 Removed the failure-triggered queue break while retaining the serial loop and explicit-stop break. Added continuation and explicit-stop regression coverage. Focused queue tests passed 2 tests, the full suite passed 135 tests, Ruff passed, and `git diff --check` passed.
- 2026-08-18T22:56:59+08:00 Rebuilt the Windows distribution, passed the release package scan and isolated GUI smoke launch, then generated a task-matched VEMO verification receipt with build and smoke exits equal to zero.

## Comment Review
Approved. The control flow is self-explanatory and requires no new comments.

## Acceptance Result
PASS. All six criteria passed. Machine verification is recorded in `.vemo/run/T-20260818-batch-queue-continuation-20260818-225659.log`; package evidence is recorded in `.vemo/run/T-20260818-batch-queue-continuation-package.log`.

## Conclusion
Outcome: accepted | Decision: deliver local build | Key Evidence: queue continuation regression, 135 passing tests, package scan PASS, and packaged EXE smoke PASS | Residual Risk: an individual remote item can still exhaust retries, but it no longer blocks later selections | Next Action: retry the 50-item selection with the rebuilt client.
