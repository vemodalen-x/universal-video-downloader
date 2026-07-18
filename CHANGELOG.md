# Changelog

## v1.2.0 - 2026-07-14

Browser companion and native HLS compatibility update.

### Added

- Chrome/Edge active-tab companion for explicit, on-demand detection of public HLS, DASH, and direct-video resources.
- Native Messaging bridge with strict message schemas, HTTP(S)-only URL validation, and credential-bearing field rejection.
- AES-GCM encrypted browser inbox capped at 20 entries and 15 minutes, with Windows DPAPI protection for its local key.
- Current-user registration script for Chrome and Edge native messaging hosts.
- Quality presets for best available, 1080p maximum, 720p maximum, and smaller-file downloads.
- Playlist and multi-video expansion with up to 50 visible entries, multi-selection, select-all, and sequential queue execution.
- Normalized format metadata for resolution, frame rate, dynamic range, codecs, bitrate, estimated size, and protocol.
- Manual and automatic subtitle discovery, language selection, SRT/VTT/ASS output, and optional FFmpeg embedding.
- FFmpeg capability detection with single-file format fallback when merging is unavailable.

### Improved

- Redesigned the desktop analysis and history workflow with visible state, candidate counts, empty states, history search/status filters, and focused keyboard actions.
- Native HLS now supports `EXT-X-BYTERANGE` media and initialization ranges, validates exact `206 Content-Range` responses, and resumes partial range segments without requesting bytes outside the declared interval.
- Desktop client now consumes browser candidates through the existing Referer-aware analysis workflow without opening a localhost network service.
- Portable builds now include the native host executable, unpacked extension, and registration script.
- Every queued item receives an independent history record and collision-safe output path.
- A failed queue item no longer prevents remaining selected entries from downloading.
- Media selection now distinguishes playlist titles while keeping detailed stream metadata in a dedicated view.
- Added hash-locked Windows release dependencies, an isolated build environment, commit-pinned GitHub Actions, automatic portable ZIP/SHA-256 generation, source/package privacy scanning, artifact provenance attestation, and tag-driven GitHub Release publishing.

### Tests

- Added coverage for browser message validation, encrypted one-time handoff, expiry and queue limits, native-message framing, least-privilege extension permissions, and stable extension identity.
- Added native HLS byte-range parser, initialization-range, strict `Content-Range`, ignored-range, and within-range continuation coverage.
- Added coverage for bounded quality selectors, subtitle postprocessors, no-FFmpeg behavior, playlist normalization, media metadata, and duplicate batch filenames.
- Added negative release scans for embedded local user paths, credential markers, screenshots, unapproved images, unsafe ZIP aliases, and private/transient files.

## v1.1.0 - 2026-07-11

Commercial workflow and reliability update.

### Added

- Windows file and product version metadata for v1.1.0 release builds.
- Portable-package documentation, license files, third-party notices, and SHA-256 release checksum.
- Generic webpage fallback through yt-dlp when static HTML and script discovery find no media.
- Persistent local task history with interrupted-session restoration and completed-task cleanup.
- Actionable error categories for timeouts, access denial, missing resources, rate limiting, server errors, dependencies, permissions, and disk space.
- Human-readable candidate columns for quality, format, protocol, duration, bitrate, and origin.
- Inline success, warning, and error notices that do not interrupt the workflow with completion dialogs.

### Improved

- Replaced the demo-style icon with a professional v2 brand mark designed for clear recognition at Windows taskbar and shortcut sizes.
- Integrated the v2 mark across the application header, window icon, packaged executable, and desktop shortcut.
- Shared HTTP retry policy with bounded backoff and `Retry-After` handling for transient responses.
- Hardened Range continuation with identity encoding, `Content-Range` start validation, and incomplete-response detection.
- Signed URLs, credentials, query parameters, and common authorization fields are redacted before logging or persistence.
- Candidate results are deduplicated by stable media identity and sorted by quality.
- Progress and segment events are coalesced to a 10 Hz UI refresh budget.
- HLS byte accounting is incremental instead of repeatedly scanning every downloaded segment.
- Segment visualization is capped at 160 aggregate blocks for stable rendering on long playlists.
- Download speed uses smoothing and now includes estimated remaining time.
- Desktop layout now keeps all primary controls visible at the 1040x720 minimum window size.

### Tests

- Added coverage for history round trips and malformed records, event coalescing, URL redaction, error mapping, generic yt-dlp fallback, candidate deduplication, output collision handling, and presentation labels.

## v1.0.0 - 2026-07-05

Initial major release.

### Added

- Windows desktop client with a redesigned universal video downloader workflow.
- HLS/m3u8 discovery from page HTML and linked scripts.
- HLS master playlist parsing, best-quality selection, segment download, pause/resume/stop, and cache-based continuation.
- HLS AES-128 segment decryption for common non-DRM playlists.
- Direct video link detection for mp4, webm, mov, mkv, m4v, flv, avi, and wmv.
- Direct HTTP download continuation through `.part` files and Range requests.
- YouTube metadata discovery and download support through `yt-dlp`.
- PyInstaller build script with bundled app icon and yt-dlp collection.
- Public README with installation, usage, supported formats, limitations, privacy/desensitization notes, and compliance statement.
- Unit tests for playlist parsing, m3u8 discovery, direct video discovery, YouTube discovery, and direct download continuation.

### Compliance

- No real third-party video URLs, credentials, cookies, tokens, or private site adapters are included in the repository.
- DRM bypass, paid-access bypass, and unauthorized commercial use are explicitly out of scope.
