# Changelog

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
