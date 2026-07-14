---
id: T-20260714-v1-2-0-release-packaging
risk: R1
change_class: release
state: AcceptancePassed
scope_in: ["README.md", "CHANGELOG.md", "assets/version_info.txt", "tasks/T-20260714-v1-2-0-release-packaging.md", "VEMO/tasks/T-20260714-v1-2-0-release-packaging.md", ".vemo/run/T-20260714-v1-2-0-release-packaging*.log", ".vemo/run/receipt.json"]
scope_out: ["m3u8_core.py", "m3u8_desktop_app.py", "build.ps1", "browser_companion.py", "browser_extension/**", "browser_native_host.py", "install_browser_companion.ps1", ".gitignore", "README_photo_archive.md", "photo_archive_app.py", "photo_archive_cli.py", "photo_archive_core.py", "tests/test_photo_archive_core.py", "assets/photo_archive_icons/**", "tasks/T-20260711-photo-archive-*.md", "vemo_photo/**", ".github/**", "enforcement/**", "specs/**"]
trifecta: []
verification:
  profile: release
  commands:
    test: "python -m pytest -q"
    lint: "python -m ruff check m3u8_core.py tests/test_m3u8_core.py"
    smoke: "powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\\build.ps1"
acceptance:
  status: passed
  build_exit: 0
  smoke_exit: 0
  evidence: ".vemo/run/T-20260714-v1-2-0-release-packaging-20260714T153236Z.log"
judge:
  required: false
  verdict: null
approved_commands: []
owning_chat: ""
heartbeat: 2026-07-14T15:32:36Z
---

# v1.2.0 release packaging

## Goal
Publish a traceable Windows portable `v1.2.0` release that matches the repository version metadata and documents the browser companion and native HLS byte-range capabilities already merged to `main`.

## Scope (In / Out)
- In: release version metadata, public stable-version and changelog documentation, task evidence, the generated portable ZIP, checksum, GitHub tag, and GitHub Release metadata.
- Out: feature implementation, application behavior, browser-extension permissions, build-script changes, DRM or access-control behavior, and photo archive work.

## Pass/Fail Criteria
- [Versioning] The README, changelog release heading, and Windows file/product version metadata SHALL identify `v1.2.0` / `1.2.0.0` consistently.
- [Packaging] The portable ZIP SHALL contain the desktop executable, Native Messaging bridge, browser extension, registration script, public documentation, license, and third-party notices.
- [Integrity] The published SHA-256 file SHALL match the uploaded ZIP exactly.
- [Regression] Full tests and scoped Ruff SHALL pass; a freshly built packaged executable SHALL remain running during a four-second smoke check.
- [Release] The GitHub Release SHALL target the merged `main` commit and describe capability, validation, compliance boundaries, and the checksum.

## Plan
- Move the accumulated Unreleased customer-visible updates into a dated `v1.2.0` section and update stable-version metadata.
- Rebuild the portable Windows directory, inspect the package manifest, create the ZIP and SHA-256 checksum, and verify both locally.
- Run release verification, complete the VEMO acceptance record, then commit and merge the metadata update through GitHub Actions.
- Create the GitHub `v1.2.0` release from `main` and upload the verified ZIP and checksum.
- Remove the accidentally created untracked task draft in the nested VEMO framework checkout; it is not product work and will not be committed.

## Execution Log
- 2026-07-14T14:09: VEMO's release verification profile was unavailable because the project configuration does not define `paths.package_scan`; no framework configuration was changed. The task uses focused machine verification for full tests, scoped lint, and the release build, while package integrity is independently checked below.
- 2026-07-14T14:46: A reusable package scanner and the missing `paths.package_scan` configuration entry are now under separate R2 review. Release-profile verification will be rerun before publication; the earlier focused receipt is retained only as historical evidence.
- 2026-07-14T14:10: Machine verification passed with full pytest, scoped Ruff, and `build.ps1`; receipt `.vemo/run/T-20260714-v1-2-0-release-packaging-20260714T141039Z.log` recorded exit code 0 for build and smoke.
- 2026-07-14T14:11: Final rebuilt `UniversalVideoDownloader.exe` reported FileVersion `1.2.0.0` and ProductVersion `1.2.0`, then remained running for four seconds.
- 2026-07-14T14:12: Generated the portable ZIP from the final build. Archive inspection found all eight required top-level components, no task/photo archive/cookie/history/log/partial entries, and 2,145 total entries. SHA-256: `200aec1a1ab3037450cc5f99eba78a162bef683fc2492564be35de05c7ed6a70`.
- 2026-07-14T14:54: Release-profile verification passed after executing the configured build, selfcheck, and `python tools/verify_release_package.py` package scanner. Receipt: `.vemo/run/T-20260714-v1-2-0-release-packaging-20260714T145405Z.log`.
- 2026-07-14T15:14:37Z Re-ran release-profile verification after the final package-scan path-policy fix. Build, selfcheck, and `python tools/verify_release_package.py` all exited 0. Receipt: `.vemo/run/T-20260714-v1-2-0-release-packaging-20260714T151437Z.log`.
- 2026-07-14T15:17:56Z The R2 staged safety review found a Windows extraction-collision gap in the package scanner. Release acceptance is refreshed pending the scanner fix and a new release-profile verification.
- 2026-07-14T15:21:02Z Re-ran release-profile verification after canonical Windows path-collision detection. Build, selfcheck, and `python tools/verify_release_package.py` all exited 0. Receipt: `.vemo/run/T-20260714-v1-2-0-release-packaging-20260714T152102Z.log`.
- 2026-07-14T15:23:52Z Re-ran release-profile verification after the final reserved-device and oversized-text scanner hardening. Build, selfcheck, and `python tools/verify_release_package.py` all exited 0. Receipt: `.vemo/run/T-20260714-v1-2-0-release-packaging-20260714T152352Z.log`.
- 2026-07-14T15:28:48Z Re-ran release-profile verification against the final worktree. Build, selfcheck, and `python tools/verify_release_package.py` all exited 0. Receipt: `.vemo/run/T-20260714-v1-2-0-release-packaging-20260714T152848Z.log`.
- 2026-07-14T15:30:00Z A final safety review found a browser credential-database policy gap (`Default/Login Data`). Release acceptance is refreshed pending the scanner hardening and a new release-profile verification.
- 2026-07-14T15:32:36Z Re-ran release-profile verification after browser-profile database hardening. Build, selfcheck, and `python tools/verify_release_package.py` all exited 0. Receipt: `.vemo/run/T-20260714-v1-2-0-release-packaging-20260714T153236Z.log`.
- 2026-07-14T14:03:47Z Task created by `vemo task create`.
- 2026-07-14T14:09:06Z Updated v1.2.0 public version metadata and release notes. Rebuilt the portable package, verified Windows file/product version 1.2.0.0/1.2.0, required package entries, packaged startup, and matching SHA-256.

## Acceptance Result
Passed. Release-profile `verify-run --no-cache` executed the configured build, selfcheck, and portable package scanner with exit code 0. The portable application also rebuilt successfully and launched for a four-second packaged smoke check.

## Conclusion
Version metadata, release documentation, package contents, checksum, source tests, static checks, build, packaged startup, and release-profile verification are ready for publication once the R2 package-scan gate is accepted.
