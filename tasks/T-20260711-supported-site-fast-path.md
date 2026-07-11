---
id: T-20260711-supported-site-fast-path
risk: R1
state: AcceptancePassed
scope_in: ["m3u8_core.py", "tests/test_m3u8_core.py", "tasks/T-20260711-supported-site-fast-path.md", ".vemo/run/T-20260711-supported-site-fast-path*.log", ".vemo/run/receipt.json"]
scope_out: [".gitignore", "README_photo_archive.md", "photo_archive_app.py", "photo_archive_cli.py", "photo_archive_core.py", "tests/test_photo_archive_core.py", "assets/photo_archive_icons/**", "tasks/T-20260711-photo-archive-*.md", "vemo_photo/**", ".github/**", "enforcement/**", "specs/**"]
trifecta: ["untrusted_content", "external_comms"]
acceptance:
  status: passed
  build_exit: 0
  smoke_exit: 0
  evidence: ".vemo/run/T-20260711-supported-site-fast-path-20260711-202330.log"
judge:
  required: false
  verdict: null
approved_commands: []
owning_chat: "chat-20260711-supported-site"
heartbeat: 2026-07-11T20:24
---

# Supported-site yt-dlp fast path

## Goal
Prevent long page/script scans from blocking sites that already have a dedicated yt-dlp extractor, while preserving static discovery as a fallback.

## Scope (In / Out)
- In: extractor routing, bounded yt-dlp network options, focused tests, and redacted acceptance evidence.
- Out: browser Cookie access, CAPTCHA bypasses, private site adapters, user media URLs/tokens, UI redesign, and photo archive work.

## Pass/Fail Criteria
- [Correctness] WHEN a URL matches a non-generic yt-dlp extractor, discovery SHALL try yt-dlp before any static page fetch; metric: unit test asserts zero static-fetch calls.
- [Fallback] WHEN the preferred yt-dlp attempt fails, discovery SHALL continue through static discovery; metric: unit test returns a synthetic direct-media candidate.
- [Reliability] WHEN yt-dlp options are built, socket timeout and retry counts SHALL be finite and bounded; metric: unit assertions pass.
- [Regression] WHEN tracked downloader tests run, all SHALL pass with exit code 0.
- [Compatibility] WHEN the user-provided XiaoHongShu URL is tested with its token kept out of logs, metadata discovery SHALL return at least one candidate within 15 seconds.

## Plan
- Add a cached, generic yt-dlp extractor suitability check and route supported sites through it first.
- Bound yt-dlp extraction timeouts/retries and keep static HTML/script discovery as recovery.
- Add focused routing/fallback/options tests, run the tracked suite, and perform one redacted live metadata check.

## Execution Log
- 2026-07-11T20:17 redacted reproduction: full client discovery exceeded 120 seconds, while direct yt-dlp extraction returned three 720x1280 MP4 formats in 1.05 seconds.
- 2026-07-11T20:25 added generic dedicated-extractor routing, static fallback preservation, bounded yt-dlp network settings, and four focused tests; final diff is 102 inserted lines with no unrelated formatter churn.
- 2026-07-11T20:23 final tracked suite passed 24/24, Ruff passed, conformance passed 84/84, and the redacted live check returned one 720x1280 MP4 candidate through `XiaoHongShu` in 1.03 seconds without persisting the query or content ID.
- 2026-07-11T20:23 VEMO machine verification passed build_exit=0 smoke_exit=0; evidence `.vemo/run/T-20260711-supported-site-fast-path-20260711-202330.log`.

## Acceptance Result
- PASS [Correctness] Dedicated extractor routing returned before the static fetch mock could execute.
- PASS [Fallback] A synthetic extractor failure continued to static HTML discovery and returned its direct MP4 candidate.
- PASS [Reliability] yt-dlp uses a 15-second socket timeout and bounded extraction/download retry counts.
- PASS [Regression] Both tracked downloader test modules passed all 24 tests; Ruff and VEMO conformance also passed.
- PASS [Compatibility] Redacted live metadata discovery completed in 1.03 seconds with a 720x1280 MP4 candidate and no media download.

## Conclusion
Outcome: accepted | Decision: continue | Key Evidence: 24 tests, 84/84 conformance, VEMO receipt, and 1.03-second redacted live check | Risk: low | Next Action: commit the scoped fix, run clean CI, and merge without changing the v1.1.0 release artifact.
