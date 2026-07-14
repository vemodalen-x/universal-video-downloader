---
id: T-20260714-hls-byterange-playback-support
risk: R1
change_class: standard
state: AcceptancePassed
scope_in: ["m3u8_core.py", "tests/test_m3u8_core.py", "README.md", "CHANGELOG.md", "tasks/T-20260714-hls-byterange-playback-support.md", ".vemo/run/T-20260714-hls-byterange-playback-support*.log", ".vemo/run/receipt.json"]
scope_out: ["browser_companion.py", "browser_extension/**", "browser_native_host.py", "install_browser_companion.ps1", "m3u8_desktop_app.py", "build.ps1", ".gitignore", "README_photo_archive.md", "photo_archive_app.py", "photo_archive_cli.py", "photo_archive_core.py", "tests/test_photo_archive_core.py", "assets/photo_archive_icons/**", "tasks/T-20260711-photo-archive-*.md", "vemo_photo/**", ".github/**", "enforcement/**", "specs/**"]
trifecta: []
verification:
  profile: focused
  commands:
    test: "python -m pytest -q"
    lint: "python -m ruff check m3u8_core.py tests/test_m3u8_core.py"
    smoke: "python -m pytest -q tests/test_m3u8_core.py -k byterange"
acceptance:
  status: passed
  build_exit: 0
  smoke_exit: 0
  evidence: ".vemo/run/T-20260714-hls-byterange-playback-support-20260714T135756Z.log"
judge:
  required: false
  verdict: null
approved_commands: []
owning_chat: ""
heartbeat: 2026-07-14T13:58:19Z
---

# hls byterange playback support

## Goal
Add standards-aligned native HLS byte-range support so public media playlists that reuse one resource can download and resume without falling back to a generic extractor.

## Scope (In / Out)
- In: HLS playlist parsing, byte-range retrieval and resume validation, focused tests, and public capability documentation.
- Out: DRM, authentication/Cookie access, browser companion changes, DASH parsing, native media re-encoding, and photo archive work.

## Pass/Fail Criteria
- [Correctness] WHEN an HLS playlist declares `EXT-X-BYTERANGE`, the parser SHALL attach the resolved inclusive byte interval to the following segment, including legal implicit offsets for the same URI.
- [Correctness] WHEN a playlist uses an invalid implicit range with no prior interval for that URI, the parser SHALL raise `PlaylistParseError` rather than request incorrect bytes.
- [Reliability] WHEN downloading a byte-range segment, the client SHALL request `Range: bytes=<start>-<end>`, require a matching `206 Content-Range`, and resume a partial segment from the remaining interval only.
- [Packaging] WHEN the final output is combined from a synthetic byte-range HLS playlist, it SHALL equal the concatenated init and media byte ranges exactly.
- [Regression] WHEN focused and full tests run, all tests SHALL pass and Ruff SHALL report no errors for this task's Python files.

## Plan
- Model resolved byte ranges on HLS segments and parse both media tags and `EXT-X-MAP` attributes.
- Replace the unsupported-playlist guards with a strict bounded range fetcher that supports partial retry/resume.
- Add parser, integration, resume, and invalid-server tests using a local HTTP range handler.
- Update capability documentation, run full verification, build the portable application, and review the scoped patch.

## Execution Log
- 2026-07-14T13:46:50Z Task created by `vemo task create`.
- 2026-07-14T13:57:45Z Implemented strict RFC 8216 BYTERANGE parsing, exact 206 range retrieval, partial range resume, documentation, and local HTTP regression tests. Focused tests, Ruff, full tests, and packaged-app smoke check pass.

## Acceptance Result
Passed. `verify-run --no-cache` completed the documented full test suite, scoped Ruff check, and byte-range smoke suite with exit code 0. The portable application also rebuilt successfully and launched for a four-second packaged smoke check.

## Conclusion
Ready for scoped review and release delivery.
