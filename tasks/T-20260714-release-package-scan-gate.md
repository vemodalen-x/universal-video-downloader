---
id: T-20260714-release-package-scan-gate
risk: R2
change_class: standard
state: AcceptancePassed
scope_in: ["vemo.config.yaml", "tools/verify_release_package.py", "tests/test_verify_release_package.py", "tasks/T-20260714-release-package-scan-gate.md", ".vemo/judge.jsonl", ".vemo/run/T-20260714-release-package-scan-gate*.log", ".vemo/run/receipt.json"]
scope_out: ["README.md", "CHANGELOG.md", "assets/version_info.txt", "m3u8_core.py", "m3u8_desktop_app.py", "build.ps1", "browser_companion.py", "browser_extension/**", "browser_native_host.py", "install_browser_companion.ps1", ".gitignore", "README_photo_archive.md", "photo_archive_app.py", "photo_archive_cli.py", "photo_archive_core.py", "tests/test_photo_archive_core.py", "assets/photo_archive_icons/**", "tasks/T-20260711-photo-archive-*.md", "vemo_photo/**", "enforcement/**", "specs/**"]
trifecta: []
verification:
  profile: focused
  commands:
    test: "python -m pytest -q tests/test_verify_release_package.py"
    lint: "python -m ruff check tools/verify_release_package.py tests/test_verify_release_package.py"
    smoke: "python tools/verify_release_package.py"
acceptance:
  status: passed
  build_exit: 0
  smoke_exit: 0
  evidence: ".vemo/run/T-20260714-release-package-scan-gate-20260714T154021Z.log"
judge:
  required: true
  verdict: pass
  violations: []
  evidence_checked: ["tools/verify_release_package.py", "tests/test_verify_release_package.py", ".vemo/run/T-20260714-release-package-scan-gate-20260714T154021Z.log", ".vemo/run/T-20260714-v1-2-0-release-packaging-20260714T153236Z.log"]
  confidence: high
approved_commands: []
owning_chat: ""
heartbeat: 2026-07-14T15:40:21Z
---

# release package scan gate

## Goal
Add a version-agnostic, read-only portable-package scan command so release tasks can satisfy VEMO's required `package_scan` gate without relying on manual inspection or hard-coded release versions.

## Scope (In / Out)
- In: the reusable scanner, its tests, one `paths.package_scan` configuration entry, and machine/judge evidence.
- Out: downloader behavior, release-version docs, build layout, browser companion behavior, photo archive work, and VEMO enforcement implementation.

## Pass/Fail Criteria
- [Security] WHEN a portable ZIP is scanned, the scanner SHALL fail on missing required payloads, a missing or mismatched sibling checksum, and entries that contain task records, photo-archive files, browser cookie/history data, logs, or partial downloads.
- [Correctness] WHEN a valid ZIP and matching SHA-256 file are supplied, the scanner SHALL exit 0 and report the ZIP name, entry count, and digest.
- [Reliability] WHEN no explicit ZIP path is supplied, the scanner SHALL choose the most recently modified `UniversalVideoDownloader-v*-windows-x64.zip` in `dist`.
- [Governance] WHEN `vemo verify --no-cache` runs a release profile, it SHALL execute the configured scanner with exit code 0.
- [Regression] Focused unit tests and scoped Ruff SHALL exit 0.

## Plan
- Implement a standard-library Python scanner with explicit optional paths and deterministic newest-artifact discovery.
- Test valid archives and all intended failure classes without requiring a real video package.
- Register the scanner as `paths.package_scan`, then run focused verification and release-profile verification against the built `v1.2.0` archive.
- Request two independent R2 judge passes, record their verdicts, and only then allow the release metadata task to resume its release-profile gate.

## Execution Log
- 2026-07-14T14:21: Independent correctness judge failed the first implementation after read-only negative controls showed that `Default/Cookies`, `Default/History`, `logs/release.txt`, `photo_archive_app.py`, and `vemo_photo/core.py` were accepted. The fail provenance was recorded before rework.
- 2026-07-14T14:22: Expanded the path policy and parameterized regression tests for all judge-reported cases. The scanner, scoped Ruff, full 97-test suite, and VEMO selfcheck pass after the fix.
- 2026-07-14T14:25: Independent safety judge found a second privacy gap for `Local State` and credential-like text in extensionless or `.env` files. Its fail provenance was recorded; the scanner now rejects those paths and scans small extensionless files. Scanner tests increased to 17 and the full suite now passes 101 tests.
- 2026-07-14T14:30: A fresh safety judge found ZIP path traversal and UTF-16 credential-text bypasses. Its fail provenance was recorded; the scanner now rejects unsafe archive path segments and recognizes UTF-16 credential markers. Scanner tests increased to 19 and the full suite now passes 103 tests.
- 2026-07-14T14:35: Hardened ZIP parsing further for duplicate entries, symbolic links, encrypted entries, and UTF-32 text. A real-package false positive from static third-party extractor source was resolved by requiring an `xsec_token` value and restricting text inspection to user-data-bearing text/configuration assets. Scanner tests pass 23 and the full suite passes 107 tests.
- 2026-07-14T14:40: A fresh safety judge found that Windows trims `Default/Cookies ` to `Default/Cookies` during extraction. Its fail provenance was recorded; the scanner now rejects trailing spaces/dots, alternate-data-stream colons, and other Windows-unsafe path characters. Scanner tests pass 26 and the full suite passes 110 tests.
- 2026-07-14T14:54: Added the missing-sibling-checksum regression test. The configured release profile executed the scanner successfully; scanner tests pass 27 and the full suite passes 115 tests.
- 2026-07-14T15:00: A fresh safety judge found BOM-less UTF-32 credential text and unsafe directory-entry gaps. Its fail provenance was recorded; the scanner now tests both UTF-32 byte orders and validates directory names before skipping them. Scanner tests pass 30 and the full suite passes 118 tests.
- 2026-07-14T15:05: Fresh independent safety and reproducibility judges both passed. They verified the 30-case scanner suite, real v1.2.0 archive digest, release-profile scanner execution, scope, and missing-sibling-checksum coverage; their provenance records are the latest contiguous R2 verdicts.
- 2026-07-14T15:10: Final staged code review found that Chrome commonly stores Cookies under `Default/Network/Cookies`. The scanner now rejects terminal cookie/history/local-state paths at any directory depth; tests pass 31 and the full suite passes 119 tests. Prior acceptance is intentionally refreshed before commit.
- 2026-07-14T14:15:16Z Task created by `vemo task create`.
- 2026-07-14T14:18:28Z Implemented a standard-library package scanner, registered paths.package_scan, added 9 focused tests, and validated the real v1.2.0 portable ZIP plus full regression suite and VEMO selfcheck.
- 2026-07-14T14:25:15Z Addressed the independent judge's failed negative controls by rejecting browser profile Cookies/History, log directories, photo archive path variants, and vemo_photo. Scanner, Ruff, full 97-test suite, and selfcheck pass.
- 2026-07-14T14:29:56Z Addressed the second independent judge failure by rejecting browser Local State paths and scanning small extensionless/.env files for credential markers. Scanner tests 17, full regression 101, Ruff, real package scan, and selfcheck pass.
- 2026-07-14T14:35:15Z Addressed fresh judge findings: scanner rejects unsafe ZIP traversal segments and detects UTF-16 credential markers. Scanner tests 19, full regression 103, real package scan, Ruff, and selfcheck pass.
- 2026-07-14T14:38:42Z Hardened ZIP scanning for duplicate entries, symlinks, encrypted entries, UTF-32 text, and scoped third-party-code false positives. Scanner tests 23, full regression 107, real package scan, Ruff, and selfcheck pass.
- 2026-07-14T14:44:16Z Addressed final safety judge path-normalization failure: scanner rejects trailing spaces/dots, ADS colons, and Windows-unsafe path characters. Scanner tests 26, full regression 110, real package scan, Ruff, and selfcheck pass.
- 2026-07-14T14:55:59Z Added missing sibling-checksum coverage and verified the release profile executes the configured package scanner. Scanner tests 27, full regression 115, Ruff, and real package scan pass.
- 2026-07-14T15:01:44Z Addressed fresh safety review findings: scanner detects BOM-less UTF-32 LE/BE credential text and validates directory entries before skipping them. Scanner tests 30, full regression 118, Ruff, and real package scan pass.
- 2026-07-14T15:12:29Z Refined terminal browser-data matching to avoid packaged third-party cookies.py/history.tcl while rejecting actual Cookies, History, Local State, and SQLite journal/WAL/SHM variants at any directory depth. Scanner tests 31, full regression 119, Ruff, and real package scan pass.
- 2026-07-14T15:14:46Z Fresh focused verification passed with the final Chrome profile-path policy: scanner tests, scoped Ruff, and the real package scan exited 0. The paired release-profile verification passed at 15:14:37Z.
- 2026-07-14T15:17:56Z Independent staged safety review failed: raw ZIP-name deduplication missed case-insensitive and separator-normalized Windows extraction collisions. Added canonical Windows-path collision detection and regression coverage; acceptance is refreshed before new verification.
- 2026-07-14T15:21:01Z Fresh focused verification passed after canonical Windows collision detection. Scanner tests 33, scoped Ruff, and the real package scan exited 0; the paired release profile passed at 15:21:02Z.
- 2026-07-14T15:23:52Z Hardened the scanner against Windows reserved device paths and oversized text/configuration entries, which are now rejected rather than silently skipped. Scanner tests 36, full regression 124, Ruff, real package scan, focused verification, and release-profile verification all passed.
- 2026-07-14T15:28:48Z Re-ran all local and VEMO evidence against the final worktree: scanner tests 38, full regression 126, Ruff, real package scan, focused verification, and release-profile verification all passed.
- 2026-07-14T15:30:00Z Final staged safety review found `Default/Login Data` was still accepted. The policy now rejects common Chromium profile directories and credential/profile databases, with regression coverage; acceptance is refreshed before new verification.
- 2026-07-14T15:32:35Z Fresh focused verification passed after browser-profile database hardening. Scanner tests 43, scoped Ruff, and the real package scan exited 0; the paired release profile passed at 15:32:36Z.
- 2026-07-14T15:40:21Z Refreshed the focused receipt after de-sensitizing the test fixture literal. Scanner tests 43, scoped Ruff, and the real package scan exited 0; the prior release profile remains valid because product behavior was unchanged.

## Acceptance Result
Passed. Focused machine verification ran 43 scanner tests, scoped Ruff, and the real package scanner with exit code 0. The paired release-profile receipt confirms that VEMO executes the configured package scanner.

## Conclusion
R2 package-scan gate is ready for final independent staged review and commit.
