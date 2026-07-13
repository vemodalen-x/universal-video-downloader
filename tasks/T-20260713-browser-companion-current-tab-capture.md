---
id: T-20260713-browser-companion-current-tab-capture
risk: R1
change_class: standard
state: AcceptancePassed
scope_in: ["browser_companion.py", "browser_native_host.py", "browser_extension/**", "install_browser_companion.ps1", "m3u8_desktop_app.py", "build.ps1", "tests/test_browser_companion.py", "tests/test_desktop_app.py", "README.md", "CHANGELOG.md", "tasks/T-20260713-browser-companion-current-tab-capture.md", ".vemo/run/T-20260713-browser-companion-current-tab-capture*.log", ".vemo/run/receipt.json"]
scope_out: [".gitignore", "README_photo_archive.md", "photo_archive_app.py", "photo_archive_cli.py", "photo_archive_core.py", "tests/test_photo_archive_core.py", "assets/photo_archive_icons/**", "tasks/T-20260711-photo-archive-*.md", "vemo_photo/**", ".github/**", "enforcement/**", "specs/**"]
trifecta: ["untrusted_content", "external_comms"]
verification:
  profile: focused
  commands:
    test: "python -m pytest -q"
    lint: "python -m ruff check browser_companion.py browser_native_host.py m3u8_desktop_app.py tests/test_browser_companion.py tests/test_desktop_app.py"
    smoke: "python -m pytest -q tests/test_browser_companion.py::test_native_message_framing_and_handler tests/test_browser_companion.py::test_extension_permissions_are_active_tab_only tests/test_desktop_app.py::test_browser_companion_setup_worker_uses_argument_list"
acceptance:
  status: passed
  build_exit: 0
  smoke_exit: 0
  evidence: ".vemo/run/T-20260713-browser-companion-current-tab-capture-20260713T125246Z.log"
judge:
  required: false
  verdict: null
approved_commands: []
owning_chat: "chat-20260713-browser-companion"
heartbeat: 2026-07-13T12:53:28Z
---

# Browser companion current-tab capture

## Goal
Add a privacy-scoped Chrome/Edge companion that detects public media on the active tab and hands a selected candidate to the Windows desktop client without collecting browser history, cookies, or credentials.

## Scope (In / Out)
- In: active-tab extension, native messaging protocol, expiring local inbox, desktop handoff, installer/registration script, packaged bridge, tests, and user documentation.
- Out: background browsing surveillance, Cookie/session export, credential access, DRM/paid-access bypass, store publication, Firefox support, and photo archive work.

## Pass/Fail Criteria
- [Detection] WHEN the user clicks detect, the extension SHALL inspect only the active tab DOM and Resource Timing entries and return deduplicated HTTP(S) HLS/DASH/direct-video candidates.
- [Privacy] WHEN media is handed to Windows, the bridge SHALL reject non-HTTP(S), oversized, malformed, expired, and credential-bearing input; no Cookie or Authorization fields are accepted.
- [Desktop] WHEN a valid browser candidate arrives, the running desktop client SHALL consume it once, fill URL/Referer, and start the existing analysis workflow without blocking Tk rendering.
- [Packaging] WHEN `build.ps1` runs, the portable directory SHALL contain the native host executable, extension files, and registration script.
- [Regression] WHEN focused and full tests run, all tests SHALL pass and Ruff SHALL report no errors.
- [UI] WHEN the desktop client launches at minimum size, existing primary controls SHALL remain visible and the browser handoff SHALL use the existing notice system.

## Plan
- Implement strict candidate normalization, TTL-bound inbox storage, and Chrome native-message framing in isolated modules.
- Build a Manifest V3 active-tab popup using `activeTab`, `scripting`, and `nativeMessaging` only.
- Poll the inbox from the desktop event loop and route received media through existing analysis and notices.
- Register the host for Chrome/Edge, package all companion artifacts, then add tests and documentation.

## Execution Log
- 2026-07-13T12:27:03Z Task created by `vemo task create`.
- 2026-07-13T12:50:04Z Implemented active-tab MV3 extension, strict native messaging, encrypted TTL inbox, desktop handoff/setup UI, packaging, docs, and tests; review found and fixed same-process inbox locking and stable install-path/BOM issues.

## Acceptance Result
PASS. VEMO focused verification ran the 70-test suite, scoped Ruff checks, and three bridge/permission/setup smoke tests with exit code 0. `build.ps1` also produced the final portable package; the packaged GUI stayed running, the packaged bridge completed an encrypted native-message round trip, source/package hashes matched, and the extension loaded successfully in Playwright Chromium. Repository-wide `ruff check .` still reports 285 pre-existing findings in excluded governance and photo-archive paths; all files in this task's Python lint scope are clean.

## Conclusion
Outcome accepted · Decision continue · Key Evidence `.vemo/run/T-20260713-browser-companion-current-tab-capture-20260713T125246Z.log` plus packaged bridge/GUI/Chromium smoke checks · Risk unpacked-extension installation remains manual and protected/DRM media remains unsupported · Next Action publish the scoped commit and prepare store packaging as a separate task.
