---
id: T-20260818-batch-item-retry-policy
risk: R1
change_class: standard
state: AcceptancePassed
scope_in: ["m3u8_desktop_app.py", "README.md", "CHANGELOG.md", "tests/test_desktop_app.py", "tasks/T-20260818-batch-item-retry-policy.md", ".vemo/run/T-20260818-batch-item-retry-policy*.log", "build/**", "dist/**"]
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
  evidence: ".vemo/run/T-20260818-batch-item-retry-policy-20260818-231356.log"
judge:
  required: false
  verdict: null
approved_commands: ["powershell -ExecutionPolicy Bypass -File .\\build.ps1"]
owning_chat: "codex-20260818-batch-item-retry-policy"
heartbeat: 2026-08-18T23:13:56+08:00
---

# Retry one batch item before advancing

## Goal
Retry a failed batch item several times before marking it failed and moving to the next selected item.

## Scope (In / Out)
- In: desktop queue-level retry policy, backoff and cancellation, retry status UI, tests, documentation, and a local Windows rebuild.
- Out: changing protocol-specific retry integrity, accessing user media, publishing, unrelated release files, and photo archive work.

## Human Authorization
The user explicitly requested several retries on the current item before the queue advances. The prior local Windows rebuild authorization remains active. No private share access is needed.

## Pass/Fail Criteria
- [x] [Retry] WHEN a retryable item fails, the queue SHALL recreate and retry that same item up to three total queue attempts before advancing.
- [x] [Ordering] WHEN the current item succeeds on a later attempt, the next item SHALL start only after that success.
- [x] [Exhaustion] WHEN all attempts fail, the queue SHALL emit one final item failure and then continue to the next item.
- [x] [Permanent Errors] WHEN an error is classified non-retryable, the queue SHALL avoid redundant attempts and continue to the next item.
- [x] [Cancellation] WHEN the user stops during retry or backoff, no further attempt or later item SHALL start.
- [x] [UX] WHEN retrying, the UI SHALL show the current attempt and maximum attempt count.
- [x] [Regression] WHEN focused queue tests, full tests, Ruff, and diff checks run, all SHALL pass.
- [x] [Build] WHEN rebuilt, package scanning and an isolated EXE smoke launch SHALL pass.

## Plan
- Add a bounded queue-level retry loop with cancellation-aware backoff around job creation and execution.
- Emit a dedicated retry event and display attempt progress without creating duplicate history rows.
- Test recovery, exhaustion, non-retryable errors, and explicit stop ordering.
- Run acceptance, rebuild, scan, and smoke-test the Windows package.

## Execution Log
- 2026-08-18T23:08:56+08:00 Added three bounded queue-level attempts with cancellation-aware backoff, retryability classification, and visible attempt events. Added recovery, exhaustion, permanent-error, backoff-stop, and explicit-stop coverage. Focused queue tests passed 5 tests, full regression passed 138 tests, Ruff passed, and `git diff --check` passed.
- 2026-08-18T23:13:56+08:00 The standard distribution replacement was blocked by the still-running previous executable, which was intentionally left untouched. Built the desktop and bridge side by side, rebuilt the standard release ZIP from that output, passed package scanning and isolated GUI smoke, and generated a task-matched VEMO receipt.

## Comment Review
Approved. The queue loop and event payloads remain self-explanatory; no new comments are needed.

## Acceptance Result
PASS. All eight criteria passed. Machine verification is recorded in `.vemo/run/T-20260818-batch-item-retry-policy-20260818-231356.log`; side-by-side package evidence is recorded in `.vemo/run/T-20260818-batch-item-retry-policy-package.log`.

## Conclusion
Outcome: accepted | Decision: deliver side-by-side local build | Key Evidence: 138 passing tests, package scan PASS, and packaged EXE smoke PASS | Residual Risk: the standard EXE path remains the running previous build until the user closes it | Next Action: close the previous client after its current work finishes, then launch the side-by-side executable.
