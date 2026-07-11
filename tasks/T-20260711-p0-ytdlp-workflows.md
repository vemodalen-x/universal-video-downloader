---
id: T-20260711-p0-ytdlp-workflows
risk: R1
state: AcceptancePassed
scope_in: ["m3u8_core.py", "m3u8_desktop_app.py", "tests/test_m3u8_core.py", "tests/test_desktop_app.py", "README.md", "CHANGELOG.md", "tasks/T-20260711-p0-ytdlp-workflows.md", ".vemo/run/T-20260711-p0-ytdlp-workflows*.log", ".vemo/run/receipt.json"]
scope_out: [".gitignore", "README_photo_archive.md", "photo_archive_app.py", "photo_archive_cli.py", "photo_archive_core.py", "tests/test_photo_archive_core.py", "assets/photo_archive_icons/**", "tasks/T-20260711-photo-archive-*.md", "vemo_photo/**", ".github/**", "enforcement/**", "specs/**"]
trifecta: ["untrusted_content", "external_comms"]
acceptance:
  status: passed
  build_exit: 0
  smoke_exit: 0
  evidence: ".vemo/run/T-20260711-p0-ytdlp-workflows-20260711T125910Z.log"
judge:
  required: false
  verdict: null
approved_commands: []
owning_chat: "chat-20260711-p0-ytdlp"
heartbeat: 2026-07-11T20:59:13+08:00
---

# P0 yt-dlp workflows

## Goal
Integrate the highest-value yt-dlp workflows into the desktop client: format policies, FFmpeg diagnostics and merge behavior, subtitle selection/conversion, and playlist batch queues.

## Scope (In / Out)
- In: normalized yt-dlp media details, quality presets, subtitle options, playlist entry selection, sequential batch execution, UI states, tests, and user documentation.
- Out: browser Cookie access, impersonation, arbitrary plugins/commands, SponsorBlock, DRM, release version bumps, and photo archive work.

## Pass/Fail Criteria
- [Formats] WHEN yt-dlp returns multiple formats, the client SHALL expose normalized resolution/FPS/HDR/codec/bitrate/filesize rows and select a valid format expression for each quality preset; metric: focused unit tests pass.
- [FFmpeg] WHEN FFmpeg is absent or required, the client SHALL report capability state and select a compatible single-file fallback; metric: selector and diagnostic tests pass.
- [Subtitles] WHEN subtitle tracks exist, the client SHALL list languages, distinguish manual/automatic tracks, and pass selected language/format/embed options to yt-dlp; metric: option-building tests pass.
- [Playlist] WHEN a playlist URL returns entries, the client SHALL expose selectable entries and download the selected queue sequentially with per-item state; metric: playlist normalization and queue tests pass.
- [Regression] WHEN both tracked test modules run, all tests SHALL pass with exit code 0 and Ruff SHALL report no errors.
- [UI] WHEN the built application launches at 1040x720, primary analyze/download controls SHALL remain visible without overlapping; metric: screenshot and four-second smoke inspection pass.

## Plan
- Introduce immutable format/subtitle/media-detail models and a single yt-dlp option builder.
- Preserve current HLS/direct candidates while enriching yt-dlp candidates with selectable formats and playlist entries.
- Add compact quality/subtitle controls plus a queue-oriented download table in the desktop client.
- Add focused tests and documentation, then build and inspect the Windows client before PR.

## Execution Log
- 2026-07-11T20:40 baseline tracked suite passed 24/24; existing product has one aggregate yt-dlp candidate and no format/subtitle/playlist UI.
- 2026-07-11T20:57 implemented normalized formats/subtitles, quality policies, FFmpeg fallback, playlist expansion, and sequential queue execution.
- 2026-07-11T20:59 full suite passed 54/54; Ruff passed; PyInstaller build and packaged EXE smoke launch passed.

## Acceptance Result
- Passed. VEMO receipt records build exit 0 and smoke exit 0; the packaged Windows client also launched successfully for five seconds.

## Conclusion
Outcome: complete | Decision: merge | Key Evidence: 54 tests, Ruff, VEMO acceptance, PyInstaller build, GUI and packaged EXE smoke checks | Risk: R1 cross-module feature | Next Action: commit and open a pull request.
