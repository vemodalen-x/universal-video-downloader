---
id: T-20260718-v1-2-0-commercial-release
risk: R2
state: AcceptancePassed
scope_in:
  - m3u8_core.py
  - m3u8_desktop_app.py
  - browser_companion.py
  - browser_native_host.py
  - browser_extension/**
  - install_browser_companion.ps1
  - build.ps1
  - UniversalVideoDownloader.spec
  - UniversalVideoDownloaderBridge.spec
  - assets/version_info.txt
  - assets/bridge_version_info.txt
  - README.md
  - RELEASE_NOTES.md
  - CHANGELOG.md
  - THIRD_PARTY_NOTICES.md
  - requirements*.txt
  - tools/verify_release_package.py
  - tests/test_m3u8_core.py
  - tests/test_desktop_app.py
  - tests/test_browser_companion.py
  - tests/test_verify_release_package.py
  - .claude/settings.json
  - .github/workflows/**
  - vemo.config.yaml
  - vemo.config.preset.yaml
  - .vemo/judge.jsonl
  - tasks/T-20260718-v1-2-0-commercial-release.md
  - tasks/T-20260714-release-package-scan-gate.md
  - tasks/T-20260714-v1-2-0-release-packaging.md
  - tasks/T-20260715-redesign-downloader-desktop-interaction.md
scope_out:
  - photo_archive*.py
  - PhotographerImageArchive.spec
  - assets/photo_archive*/**
  - README_photo_archive.md
  - CHANGELOG_photo_archive.md
  - build_release.ps1
  - tools/verify_photo_release_package.py
  - tests/test_photo*.py
  - tasks/*photo-archive*.md
  - vemo_photo/**
  - eval/out/**
trifecta: [private_data, external_comms]
acceptance:
  status: passed
  build_exit: 0
  smoke_exit: 0
  evidence: ".vemo/run/T-20260718-v1-2-0-commercial-release-release-evidence-final.log"
judge:
  required: true
  verdict: pass
approved_commands:
  - "Build scripts may replace generated contents under build/ and dist/."
  - "Push codex/v1.2.0-commercial-release and publish the reviewed v1.2.0 GitHub Release."
owning_chat: "codex-20260718-commercial-release"
heartbeat: 2026-07-18T10:57+08:00
---

# Universal Video Downloader v1.2.0 commercial release readiness

## Goal
Publish a reproducible, privacy-sanitized Windows v1.2.0 package for Universal Video Downloader that users can download and run directly.

## Scope (In / Out)
- In: the downloader desktop application, browser companion, packaging, release verification, public documentation, CI, and focused tests listed in `scope_in`.
- Out: the separately released Photographer Image Archive product, local photo archives, evaluation output, screenshots, user download history, browser profiles, credentials, and unrelated worktree changes.

## Pass/Fail Criteria
- [Correctness] WHEN the complete automated test suite runs, it SHALL report zero failures.
- [Quality] WHEN Ruff and Python compilation checks run on release Python sources, each command SHALL exit 0.
- [Build] WHEN `build.ps1` creates the Windows distribution, the downloader and browser bridge executables SHALL exist and the build command SHALL exit 0.
- [Build] WHEN `build.ps1` runs, the downloader ZIP SHALL contain versioned executables and its required public documentation.
- [Security] WHEN the release ZIP is scanned, it SHALL contain only approved public files, contain no forbidden local/private entries or credential markers, match the SHA-256 checksum, and the scanner SHALL exit 0.
- [Privacy] WHEN the staged source diff, tracked images, release notes, and ZIP entries are audited, they SHALL contain no absolute local paths, personal download history, browser profile data, tokens, local screenshots, or user photo archives.
- [Release] WHEN GitHub publishing completes, tag `v1.2.0` SHALL point to the reviewed merge commit and the public Release SHALL contain only the downloader ZIP and its SHA-256 checksum asset, with no screenshots.
- [Governance] WHEN the R2 gates run, two independent judge passes and all VEMO release gates SHALL succeed.

## Plan
- Audit the release branch against `origin/main`, existing v1.2.0 metadata, GitHub state, both downloader build specifications, package contents, and privacy controls.
- Remove tracked machine-specific configuration and add tested downloader release automation.
- Build and smoke-test the downloader and browser bridge, generate one release ZIP plus its checksum, and run privacy and secret scans without publishing screenshots.
- Obtain two independent judge passes, commit only downloader release files, push the branch, integrate through GitHub, then tag and publish the verified assets.

## Execution Log
- 2026-07-18T10:51+08:00 Plan approved by the user's explicit request to iterate, push, and release; repository and GitHub authentication audited.
- 2026-07-18T11:00+08:00 Audit found photo-archive commits in the old release branch and absolute local paths in tracked hook settings; scope expanded to sanitize the configuration and isolate the downloader release branch.
- 2026-07-18T11:20+08:00 VEMO selfcheck rejected the prior unconsumed `paths.package_scan` key; package scanning remains enforced directly by the build and tag-release workflow.
- 2026-07-18T11:25+08:00 Added pinned release dependencies, isolated PyInstaller specs/build environment, repository/package privacy scans, text-only release notes, and tag-driven GitHub publishing with provenance attestation.
- 2026-07-18T11:35+08:00 Full pytest 104 passed; Ruff, py_compile, PowerShell/YAML parsing, and VEMO selfcheck passed.
- 2026-07-18T11:45+08:00 Isolated Windows build and privacy scan passed: 1,058 ZIP entries, SHA-256 `226096144ba9ed314ec7537a9c22e35069023f0d220dac2cd93c46ef2059a12c`, desktop/bridge smoke passed, PE version 1.2.0, signature intentionally NotSigned and documented.
- 2026-07-18T11:46+08:00 VEMO verify receipt passed at `.vemo/run/T-20260718-v1-2-0-commercial-release-20260718T031633Z.log`.
- 2026-07-18T12:05+08:00 First independent judge failed the initial snapshot; valid findings were missing generic Bearer/private-path controls, unhashed wheels, mutable Action tags, and a floating Runner label. The unrelated request to publish photo-archive artifacts was rejected as outside this task.
- 2026-07-18T12:20+08:00 Remediation added negative scanner controls, hash-locked CPython 3.10/3.11 Windows wheels, pinned pip/setuptools, commit-pinned official Actions, and `windows-2022`.
- 2026-07-18T12:25+08:00 Final isolated build passed: 1,066 ZIP entries, SHA-256 `ec566c3aa857b2112ae77de7dade719dd007037f611a0362c548bedc4a44385b`; full pytest 107 passed, governance eval 84/84, desktop/bridge smoke passed.
- 2026-07-18T12:40+08:00 Remediation judge verified all technical fixes but failed branch-scope accounting; the three prior downloader release/UI task records in `origin/main..HEAD` were added explicitly to this release scope.
- 2026-07-18T13:00+08:00 Runtime/release judge found the tag was not constrained to current `main` and the bridge PE lacked version metadata; both were added as release blockers to remediate.
- 2026-07-18T10:57+08:00 A concurrent task temporarily expanded this record toward a dual-product release; that product was subsequently isolated and published from its own repository.
- 2026-07-18T17:19+08:00 Continuity reconciliation restored this task to downloader-only scope; Photographer Image Archive files and assets remain explicitly excluded.
- 2026-07-18T17:19+08:00 Latest release candidate passed 107 tests, Ruff, py_compile, PowerShell/YAML parsing, VEMO selfcheck, governance eval 110/110, desktop/bridge smoke, and the 1,066-entry privacy scan. Final SHA-256 is `1dfbc7badab3e00730c6e61aaa275fed7841058fe6107b64f05e24725943ad39`.
- 2026-07-18T17:22+08:00 Removed stale photo-archive paths from the Python verification preset; refreshed VEMO ground truth passed without missing-source warnings at `.vemo/run/T-20260718-v1-2-0-commercial-release-20260718-172233.log`.
- 2026-07-18T17:30+08:00 Independent correctness judge failed the snapshot because the cited receipt covered only compile/test and the path scanner did not reject arbitrary workspace roots such as `C:\src`; the fail record remains append-only.
- 2026-07-18T17:41+08:00 Remediation added broad absolute-path checks for public text, workspace-path checks for application binaries, and explicit handling for approved upstream runtime build strings, with four new negative/compatibility tests.
- 2026-07-18T17:41+08:00 A fresh isolated hash-locked build and comprehensive evidence run passed 111 tests, Ruff, py_compile, PowerShell/YAML parsing, selfcheck, eval 110/110, package scan, both PE version checks, and desktop smoke. Final SHA-256 is `a507ccd81f98f94de1729ade48b77320608d36b1dee2d1399e8fef74587a1d53`.
- 2026-07-18T17:54+08:00 Supply-chain judge failed the snapshot because CI installed pytest/Ruff without hashes inside the write-capable release job; all other release controls passed.
- 2026-07-18T17:59+08:00 Remediation hash-locked pytest, Ruff, and all transitive wheels for Windows CPython 3.10/3.11, then split CI into a read-only build job and a separate least-privilege publish job connected only by pinned artifact actions. Cross-target hash resolution, workflow parsing, 111 tests, Ruff, and package scan passed.
- 2026-07-18T18:10+08:00 Two fresh independent judges passed consecutively: supply-chain/release permissions and correctness/privacy/deliverability. Both high-confidence verdicts were recorded in `.vemo/judge.jsonl`.
- 2026-07-18T18:12+08:00 PR #7 VEMO CI failed because the net change contained three superseded historical task records while CI generated a receipt only for the current release task.
- 2026-07-18T18:16+08:00 Removed the three historical task records from the public PR diff without changing product code; the single release task now owns the complete delivery scope and CI receipt.

## Acceptance Result
- PASS Correctness: full pytest reported 111 passed.
- PASS Quality: Ruff, py_compile, PowerShell/YAML parsing, VEMO selfcheck, and governance eval 110/110 completed with exit 0.
- PASS Build: isolated hash-locked Windows build produced both executables, the portable ZIP, and checksum.
- PASS Security/Privacy: source/package scan passed across 1,066 ZIP entries; no photo data, screenshot, unapproved image, private path, credential marker, or Python source entry was accepted.
- PASS Supply chain: release and test dependencies are version/hash locked; CI builds under read-only permissions and grants write/attestation permissions only to the artifact publishing job.
- PASS Release: both PE file/product versions are 1.2.0.0/1.2.0; desktop and bridge smoke checks passed; final SHA-256 is `a507ccd81f98f94de1729ade48b77320608d36b1dee2d1399e8fef74587a1d53`.
- PASS Governance ground truth: comprehensive evidence log `.vemo/run/T-20260718-v1-2-0-commercial-release-release-evidence-final.log` records the isolated build and every acceptance check with exit 0; refreshed VEMO receipt `.vemo/run/T-20260718-v1-2-0-commercial-release-20260718-180201.log` independently records build/smoke exit 0.

## Conclusion
Outcome: accepted | Decision: continue | Key Evidence: 111 tests, 110/110 governance eval, package scan and executable smoke passed | Risk: medium (unsigned executable is documented) | Next Action: obtain two clean independent judge passes and complete GitHub release procedure.
