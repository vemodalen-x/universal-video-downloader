---
id: T-20260814-cloudflare-impersonation-regression
risk: R1
change_class: standard
state: AcceptancePassed
scope_in: ["m3u8_core.py", "m3u8_desktop_app.py", "requirements.txt", "requirements-release.txt", "UniversalVideoDownloader.spec", "THIRD_PARTY_NOTICES.md", "README.md", "CHANGELOG.md", "tests/test_m3u8_core.py", "tests/test_desktop_app.py", "tests/test_verify_release_package.py", "tasks/T-20260814-cloudflare-impersonation-regression.md", ".vemo/run/T-20260814-cloudflare-impersonation-regression*.log", ".vemo/run/receipt.json", "build/**", "dist/**"]
scope_out: [".gitignore", "README_photo_archive.md", "photo_archive/**", "tests/test_photo_archive_core.py", "assets/photo_archive_icons/**", "tasks/T-20260711-photo-archive-*.md", "tasks/T-20260714-photo-archive-collection-progress.md", "vemo_photo/**", "eval/out/**", ".github/**", "enforcement/**", "specs/**", "VEMO/**"]
trifecta: ["untrusted_content", "external_comms"]
verification:
  profile: focused
  commands:
    test: "python -m pytest -q"
    lint: "python -m ruff check m3u8_core.py m3u8_desktop_app.py tests/test_m3u8_core.py tests/test_desktop_app.py tests/test_verify_release_package.py"
    smoke: "python -m pytest -q tests/test_m3u8_core.py -k impersonat"
acceptance:
  status: passed
  build_exit: 0
  smoke_exit: 0
  evidence: ".vemo/run/T-20260814-cloudflare-impersonation-regression-release-rebuild.log"
judge:
  required: false
  verdict: null
approved_commands: ["powershell -ExecutionPolicy Bypass -File .\\build.ps1"]
owning_chat: "codex-20260814-cloudflare-impersonation-regression"
heartbeat: 2026-08-14T20:38:51+08:00
---

# Cloudflare impersonation regression

## Goal
Restore generic webpage discovery for public pages that now require browser TLS impersonation while preserving privacy and existing direct/HLS behavior.

## Scope (In / Out)
- In: yt-dlp generic impersonation options, optional transport capability/error handling, Windows dependency packaging, release dependency hashes, documentation, and regression tests.
- Out: browser Cookie import, account login, CAPTCHA automation, Cloudflare challenge solving, site-specific private APIs, DRM/access-control bypass, release publishing, and photo archive work.

## Pass/Fail Criteria
- [x] [Correctness] WHEN generic yt-dlp discovery or download runs, the system SHALL request an available generic impersonation target for the initial webpage request.
- [x] [Compatibility] WHEN YouTube-specific extractor arguments are configured, the system SHALL preserve them while generic webpage jobs receive only generic impersonation arguments.
- [x] [Packaging] WHEN installation and Windows release manifests are inspected, `curl_cffi` and its required runtime files SHALL be declared and collected with hash-locked release dependencies.
- [x] [Recovery] WHEN a Cloudflare error reports unavailable impersonation support, the system SHALL return an actionable dependency-specific error rather than a generic access-denied message.
- [x] [Security] WHEN interactive verification remains required, the system SHALL direct the user to the explicit browser-companion flow without importing cookies or automating a CAPTCHA.
- [x] [Regression] WHEN the full test suite, focused impersonation tests, Ruff, and `git diff --check` run, all SHALL exit 0.

## Plan
- Centralize generic yt-dlp extractor arguments and apply them consistently in metadata discovery and actual download jobs.
- Add curl-cffi to development/release dependencies and collect its native runtime in the Windows package.
- Classify unavailable impersonation and persistent Cloudflare challenge failures with separate recovery guidance.
- Add option, error, dependency-manifest, and packaging tests; then run live metadata-only diagnostics where the installed runtime permits.

## Execution Log
- 2026-08-14T20:05:00+08:00 Reproduced HTTP 403 with `cf-mitigated: challenge`; page title is `Just a moment...` and contains no m3u8. yt-dlp 2026.07.04 requested `generic:impersonate`, but curl_cffi is unavailable in the current runtime.
- 2026-08-14T20:06:00+08:00 Confirmed the browser-observed HLS playlist is healthy and the native parser reads 2251 segments; the regression is isolated to webpage discovery.
- 2026-08-14T20:07:00+08:00 Added scoped generic impersonation to yt-dlp discovery/download, actionable Cloudflare recovery, curl_cffi runtime/release dependencies, PyInstaller collection, notices, and tests.
- 2026-08-14T20:08:55+08:00 Passed 130 tests, focused regression tests, Ruff, diff checks, in-memory wheel hash/native-content checks, and privacy scan.
- 2026-08-14T20:21:51+08:00 User explicitly approved rebuilding the Windows release package. Approved command records the release script, which clears the isolated release venv and overwrites generated build and dist outputs.
- 2026-08-14T20:31:00+08:00 Release scan caught a curl_cffi wheel metadata file containing a third-party CI path. The package spec now excludes only that non-runtime metadata entry; the scanner remains strict.
- 2026-08-14T20:33:00+08:00 Release smoke showed yt-dlp 2026.07.04 rejects curl_cffi 0.16.0 even though the library imports. Reset acceptance pending and pin curl_cffi 0.15.0, the latest supported stable series, before rebuilding.
- 2026-08-14T20:37:00+08:00 Hash-locked install exposed curl_cffi 0.15.0 runtime dependency on rich. Added rich, markdown-it-py, and mdurl with official wheel hashes and third-party notices; no dependency lock was bypassed.
- 2026-08-14T20:38:51+08:00 Final rebuild passed hash-locked installation, PyInstaller packaging, release privacy scan, checksum verification, available yt-dlp impersonation-target verification, browser-fingerprint HTTPS smoke, and isolated desktop startup smoke.

## Acceptance Result
Passed. Generic discovery and download option tests verify `generic:impersonate`; YouTube retains its dedicated extractor arguments. The rebuilt Windows package uses yt-dlp-supported curl_cffi 0.15.0 and lists available Chrome, Edge, Firefox, Safari, and Tor impersonation targets. It includes the curl_cffi native wrapper, excludes DELVEWHEEL build metadata, passes the privacy scanner and checksum check, and completes an isolated desktop startup smoke test. Full regression suite: 130 passed.

## Conclusion
Outcome: accepted | Decision: stop | Key Evidence: `.vemo/run/T-20260814-cloudflare-impersonation-regression-release-rebuild.log` | Residual Risk: TLS impersonation cannot satisfy interactive Cloudflare challenges, so browser companion remains the fallback | Next Action: install the rebuilt desktop package for use.
