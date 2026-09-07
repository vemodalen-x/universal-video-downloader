# Changelog

## Unreleased

### Added

- Baidu Netdisk `/s/` share recognition and complete-share batch downloads through a user-authorized BaiduPCS-Go connector.
- A dedicated Baidu connector login action that opens an isolated console without exposing account input to the desktop process.
- Hash-pinned BaiduPCS-Go v4.0.2 packaging, executable integrity verification, and bundled Apache-2.0 license text.
- PikPak public-share discovery with optional extraction codes, cycle-safe nested-folder traversal, video metadata, and direct-media candidate mapping.
- A masked desktop extraction-code field that remains only for the active queue, is cleared when the queue ends, and is never stored in candidates, history, or logs.
- Actionable PikPak errors for missing or invalid extraction codes, unavailable shares, and browser verification requirements.
- Browser TLS impersonation support for yt-dlp generic webpage discovery through the packaged curl_cffi transport.

### Improved

- Isolate HLS partial-export completion/errors from the queue lifecycle, capture the requested job, and block duplicate export requests.
- Reject empty HLS exports and zero-byte segments during final assembly; serialize partial/final output with cache cleanup and clean unique temporary output files after failed exports.
- Reset progress and controls between queue items, and show an accurate stopped-queue summary.
- Open provider-specific history directories without creating missing folders, fall back to an existing parent, and show selected task errors in the history view.
- Keep logs read-only and bounded to 2000 lines, preserve manual reading position, and redact entire authorization/cookie headers instead of only their first token.

- Invalidate old media and source-specific credentials when the input link changes; ignore late results and block overlapping analysis/download starts.
- Add explicit select-all and deselect actions with keyboard support, accurate selection counts, disabled empty-selection downloads, and clear per-title batch naming.
- Preserve custom filenames across duplicate Tk selection events, including automatic history continuation into the original output path.
- Move advanced settings to a compact native window; automatically reveal and focus extraction-code recovery, validate concurrency and empty output directories, and disable unsupported settings.
- Rebalance the desktop layout so download, pause, stop, and progress controls remain visible at the default and minimum window sizes. Add a synthetic native-window smoke command with optional local-only captures.

- Direct-media continuation now uses validated bounded byte ranges instead of open-ended ranges, allowing recovery from CDNs that require an explicit end offset and complete `200` responses when Range is ignored.
- Reject empty/nonmedia responses, compressed range bodies, overlong bodies, changing declared lengths, and changed strong ETags before publishing a direct download. Resume metadata stores only size and a hashed ETag across restarts.
- Preserve resource query selectors in media identity while excluding common signatures, so different videos sharing an endpoint are not silently dropped.
- History retry requires the recorded media fingerprint when present and never selects an unrelated sole candidate automatically.
- Restore native queue pause controls, prevent stale job events from replacing the active job, and honor cancellation during job preparation.
- Keep final progress events ordered between queue items; completion now records the actual output path and byte count and clears resolved errors.
- Refresh only changed history rows, preserving selection and scroll; batch segment repainting once per changed block.
- Serialize history transactions across processes and recover structurally invalid JSON from a valid backup without overwriting that backup.
- Reject PikPak refreshes that change the file identity or media type instead of saving an HLS manifest as a video.
- Direct-media continuation keeps requesting bounded chunks when a valid `206` response omits the total size, and publishes only after the server explicitly confirms the final byte.
- Local history now retains up to 2000 recent tasks and can recover from a valid atomic backup if the primary JSON file is interrupted or corrupted.
- Failed, stopped, and interrupted history records can now re-analyze their privacy-safe source, match the original media, reuse the same task/output identity, and continue from local recovery data.
- Native HLS retries now use a stable media cache key and adopt compatible URL-keyed legacy caches when temporary playlist signatures change.
- Newly recorded YouTube tasks keep a query-free canonical video-ID path so history retry does not persist signed or unrelated query parameters.
- Existing filenames in the selected save folder are now always excluded before queue startup; the desktop client reports skipped items and never offers to overwrite or create numbered duplicates.
- Added privacy-preserving duplicate detection with stable SHA-256 media identities and in-batch deduplication.
- Removed the 50-item media cap and the PikPak traversal count/depth/page caps, so supported playlists and shares expose every discovered video while retaining folder and pagination cycle protection.
- Baidu share tasks require connector-confirmed transfer and download completion; newly created `.part`, `.download`, `.tmp`, or `.temp` files prevent false completion and keep the task retryable.
- Baidu downloads use the same serial queue policy as other sources, retrying the current share up to three times before continuing.
- PikPak direct media now uses bounded HTTP `.part` retries and refreshes expired temporary URLs by stable file ID without persisting extraction codes; shared HLS media uses the native playlist downloader.
- Direct downloads keep partial bytes in an internal recovery cache across connection drops, rejected ranges, and explicit stops, and only publish the final file after validated byte completion.
- Multi-file queues remain strictly serial but continue after an item exhausts its retries, preserving that item's internal resume cache while processing the rest of the selection.
- Retryable batch items receive up to three queue-level attempts with cancellation-aware backoff and visible attempt status before the queue advances; permanent errors skip redundant retries.
- Anonymous token refresh is bounded to one retry; interactive verification is handed back to the user instead of being automated.
- Generic webpage metadata discovery and download now share the same scoped impersonation settings, without forcing impersonation on native direct/HLS requests.

### Tests

- Added synthetic coverage for Baidu route isolation, argument-list connector execution, extraction-code redaction, incomplete-file rejection, package binary integrity, protected PikPak shares, nested video discovery, metadata normalization, anonymous token refresh, stable-file URL refresh, dropped-stream continuation, rejected-range preservation, verification refusal, desktop argument forwarding, and presentation labels.

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
