---
id: T-20260811-pikpak-share-support
risk: R1
change_class: standard
state: AcceptancePassed
scope_in: ["m3u8_core.py", "m3u8_desktop_app.py", "tests/test_m3u8_core.py", "tests/test_desktop_app.py", "README.md", "CHANGELOG.md", "tasks/T-20260811-pikpak-share-support.md", ".vemo/run/T-20260811-pikpak-share-support*.log", ".vemo/run/receipt.json"]
scope_out: [".gitignore", "README_photo_archive.md", "photo_archive/**", "tests/test_photo_archive_core.py", "assets/photo_archive_icons/**", "tasks/T-20260711-photo-archive-*.md", "tasks/T-20260714-photo-archive-collection-progress.md", "vemo_photo/**", ".github/**", "enforcement/**", "specs/**", "VEMO/**"]
trifecta: ["untrusted_content", "external_comms"]
verification:
  profile: focused
  commands:
    test: "python -m pytest -q tests/test_m3u8_core.py tests/test_desktop_app.py"
    lint: "python -m ruff check m3u8_core.py m3u8_desktop_app.py tests/test_m3u8_core.py tests/test_desktop_app.py"
    smoke: "python -m pytest -q tests/test_m3u8_core.py -k pikpak"
acceptance:
  status: passed
  build_exit: 0
  smoke_exit: 0
  evidence: ".vemo/run/T-20260811-pikpak-share-support-acceptance.log"
judge:
  required: false
  verdict: null
approved_commands: []
owning_chat: "codex-20260811-pikpak-share-support"
heartbeat: 2026-08-11T18:00:08+08:00
---

# PikPak share video support

## Goal
Resolve accessible PikPak share links into downloadable video candidates while preserving the existing resumable direct-download path.

## Scope (In / Out)
- In: PikPak share URL recognition, anonymous public-share API flow, optional extraction-code handling, bounded folder traversal, direct-media candidate mapping, desktop input, tests, and capability documentation.
- Out: PikPak account login, credential storage, automated human-verification challenges, DRM bypass, private-share access without a supplied code, browser automation, release publishing, and photo archive work.

## Pass/Fail Criteria
- [x] [Correctness] WHEN a valid accessible PikPak share contains video files, the resolver SHALL return one direct candidate per discovered video with title, size/quality metadata where available, and a resumable media URL.
- [x] [Correctness] WHEN the share requires or rejects an extraction code, the resolver SHALL return a specific actionable error and SHALL NOT fall through to generic yt-dlp parsing.
- [x] [Reliability] WHEN a share contains folders or pagination, discovery SHALL traverse them within explicit file, folder, page, and depth limits and SHALL deduplicate file IDs.
- [x] [Security] WHEN PikPak requests interactive verification, the resolver SHALL stop with a browser-companion action instead of automating or bypassing the challenge; extraction codes and signed URLs SHALL not be logged or persisted.
- [x] [Regression] WHEN focused tests and Ruff run, both SHALL exit 0, and a live diagnostic against the supplied share SHALL classify it as extraction-code protected.

## Plan
- Add a small PikPak public-share client around the documented anonymous captcha-token and share endpoints, with strict URL parsing and bounded traversal.
- Map original or transcoded video links onto the existing `DirectDownloadJob` so pause/resume behavior remains unchanged.
- Add an optional extraction-code field to the desktop analysis flow and keep it out of history and logs.
- Cover URL parsing, token refresh, protected-share errors, nested/paginated discovery, media selection, and UI argument forwarding with mocked tests.

## Execution Log
- 2026-08-11T17:46:00+08:00 Confirmed the supplied link returns `PASS_CODE_EMPTY`; current yt-dlp reports unsupported URL. Reviewed PikPak's published web bundle/API definitions and AList's open-source `pikpak_share` protocol flow.
- 2026-08-11T17:53:00+08:00 Added a dedicated public-share client with URL validation, anonymous token refresh, bounded recursive traversal, media normalization, and specific protected-share errors.
- 2026-08-11T17:56:00+08:00 Added the masked desktop extraction-code flow and routed direct PikPak media through the existing resumable HTTP job.
- 2026-08-11T18:00:08+08:00 Completed documentation, privacy hardening, full regression tests, focused protocol tests, live protected-share classification, and desktop UI smoke verification.

## Acceptance Result
Passed. Full suite: 126 tests. PikPak-focused suite: 10 tests. Ruff, `git diff --check`, desktop UI smoke, and the live protected-share diagnostic all exited successfully. The supplied share requires an extraction code, so downloading its media cannot be exercised without that user-provided code.

## Conclusion
Outcome: complete | Decision: accept | Key Evidence: `.vemo/run/T-20260811-pikpak-share-support-acceptance.log` | Residual Risk: PikPak may change its public web protocol, and signed media URLs can expire | Next Action: user enters the share extraction code in Advanced Options and re-runs analysis.
