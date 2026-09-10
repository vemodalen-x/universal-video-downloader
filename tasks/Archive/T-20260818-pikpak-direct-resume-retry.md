---
id: T-20260818-pikpak-direct-resume-retry
risk: R1
change_class: standard
state: AcceptancePassed
scope_in: ["m3u8_core.py", "m3u8_desktop_app.py", "README.md", "CHANGELOG.md", "tests/test_m3u8_core.py", "tests/test_desktop_app.py", "tasks/T-20260818-pikpak-direct-resume-retry.md", ".vemo/run/T-20260818-pikpak-direct-resume-retry*.log", "build/**", "dist/**"]
scope_out: ["G:/pmv/**", ".gitignore", "requirements*.txt", "UniversalVideoDownloader.spec", "photo_archive/**", "vemo_photo/**", "eval/out/**", ".github/**", "enforcement/**", "specs/**"]
trifecta: ["untrusted_content"]
verification:
  profile: focused
  commands:
    test: "python -m pytest -q"
    lint: "python -m ruff check m3u8_core.py m3u8_desktop_app.py tests/test_m3u8_core.py tests/test_desktop_app.py"
    smoke: "python -m pytest -q tests/test_m3u8_core.py -k direct_download"
acceptance:
  status: passed
  build_exit: 0
  smoke_exit: 0
  evidence: ".vemo/run/T-20260818-pikpak-direct-resume-retry-20260818-221442.log"
judge:
  required: false
  verdict: null
approved_commands: ["powershell -ExecutionPolicy Bypass -File .\\build.ps1"]
owning_chat: "codex-20260818-pikpak-direct-resume-retry"
heartbeat: 2026-08-18T22:13:57+08:00
---

# PikPak direct-download resume retry

## Goal
Make interrupted PikPak and HTTP direct downloads automatically resume without exposing incomplete media as target-folder `.part` files or advancing the queue before the current file is complete.

## Scope (In / Out)
- In: bounded direct-download retries, internal resume caching, byte-range integrity checks, in-memory PikPak URL refresh, strict serial queue behavior, actionable progress/error behavior, tests, documentation, and a local Windows rebuild.
- Out: modifying or deleting the user's existing partial media, storing access codes, account login, CAPTCHA automation, DRM/access-control bypass, publishing a release, and unrelated photo archive work.

## Human Authorization
The user explicitly supplied a PikPak share, its access code, and an output folder for live diagnosis. This authorizes metadata-only network checks against that share in this task. The access code must remain process-memory-only and must not appear in source, tests, logs, task evidence, or Git diffs.

The authorized live diagnostic is complete. Remaining implementation and verification use synthetic local HTTP responses only, so private data and external communications are no longer active task properties.

The user previously requested a Windows rebuild and has now clarified that only fully completed files should appear in the target directory. This authorizes the repository's exact `build.ps1` command and its scoped `build/` and `dist/` replacement behavior; it does not authorize changing `G:/pmv`.

## Pass/Fail Criteria
- [x] [Correctness] WHEN a direct HTTP stream ends early or raises a retryable transport error, the job SHALL preserve valid bytes in an internal cache and resume with a matching `Range` request within a bounded retry budget.
- [x] [Integrity] WHEN a resumed response has a missing or mismatched `Content-Range`, the job SHALL reject appending it and avoid corrupting the partial file.
- [x] [Recovery] WHEN a PikPak temporary download URL expires during retry, the downloader SHALL refresh that file's URL in memory and continue from the existing byte offset without persisting the access code.
- [x] [UX] WHEN the current item cannot complete, the queue SHALL halt before later files, retain only an internal resume cache, and avoid exposing a target-folder `.part` as playable output.
- [x] [Privacy] WHEN source, tests, task files, and evidence are scanned, the supplied share identifier, access code, media names, and local output paths SHALL have zero matches.
- [x] [Regression] WHEN focused retry tests, the full test suite, Ruff, and `git diff --check` run, all SHALL exit 0.
- [x] [Build] WHEN the approved Windows build completes, the packaged executable SHALL pass an isolated smoke launch and contain no user media or private paths.

## Plan
- Reproduce the resume capability with metadata-only Range requests and inspect current retry/error flow.
- Add bounded retries to `DirectDownloadJob`, validating range responses before every append.
- Add a PikPak-specific in-memory URL refresher keyed by the stable remote file ID and wire it through the download queue.
- Keep in-progress direct bytes in an internal cache, migrate legacy visible sidecars on retry, and halt a serial queue on the first incomplete item.
- Cover connection drops, explicit stops, URL expiry, malformed ranges, retry exhaustion, and access-code non-persistence with unit tests.
- Run full acceptance, rebuild the Windows package, and record privacy-safe evidence.

## Execution Log
- 2026-08-18T21:52:08+08:00 Diagnosed two partial files as valid MP4 prefixes missing the final `moov` atom; disk space is sufficient and one sibling file completed. Existing implementation performs no automatic retry after a stream interruption.
- 2026-08-18T21:55:00+08:00 Metadata-only Range probes confirmed one partial can resume with HTTP 206 and exposed that the current HTTP 416 branch deletes partial bytes. Ended live-share access; remaining work uses synthetic local servers.
- 2026-08-18T22:06:18+08:00 Implemented bounded byte-range retries, stable-file PikPak URL refresh, internal direct-download recovery caches, legacy sidecar migration, and strict queue halting. Focused tests passed; full suite passed 134 tests, Ruff and diff checks passed, and scoped privacy scan returned zero matches.
- 2026-08-18T22:13:57+08:00 Rebuilt the Windows distribution with locked dependencies. Package privacy scan passed, the portable archive hash was recorded, and the packaged GUI passed an isolated six-second launch smoke test.
- 2026-08-18T22:14:42+08:00 Root VEMO verification passed compile and full-test commands, wrote a task-matched receipt, and opened both acceptance-before-push and required-judge gates.

## Comment Review
- Status: approved. Comments are limited to range-integrity, in-memory credential, stable-file refresh, retry classification, and legacy-cache migration boundaries where the safety intent is not self-evident.

## Acceptance Result
PASS. All seven criteria passed. The machine verification log is recorded in `.vemo/run/T-20260818-pikpak-direct-resume-retry-20260818-221442.log`; package and EXE smoke evidence is recorded in `.vemo/run/T-20260818-pikpak-direct-resume-retry-acceptance.log`.

## Conclusion
Outcome: accepted | Decision: deliver local build | Key Evidence: 134 passing tests, zero privacy matches, package scan PASS, and packaged EXE smoke PASS | Residual Risk: a remote host may still reject Range or require interactive verification | Next Action: retry the same PikPak item with the rebuilt client so the legacy sidecar is migrated into the internal cache.
