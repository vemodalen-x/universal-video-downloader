# Universal Video Downloader v1.2.0

Windows x64 portable release for public HLS, direct-video, YouTube, and yt-dlp-supported webpage downloads.

## Highlights

- Redesigned desktop workflow with clearer analysis state, media counts, history search, status filters, and keyboard actions.
- Chrome/Edge active-tab companion that detects public HLS, DASH, and direct-video resources only after an explicit user click.
- Playlist expansion, quality presets, subtitle selection, FFmpeg capability fallback, and sequential batch downloads.
- Native HLS `EXT-X-BYTERANGE` support, strict range validation, retries, pause/resume, and persistent task recovery.
- Privacy-scoped local history and encrypted browser handoff; URL credentials, query strings, and common token fields are redacted before persistence or logging.

## Download

Download `UniversalVideoDownloader-v1.2.0-windows-x64.zip`, extract it, and run `UniversalVideoDownloader.exe`. The archive also contains the optional browser companion and its current-user installation script.

Verify the ZIP against `SHA256SUMS-v1.2.0.txt` before running it.

## Requirements And Limitations

- Windows 10 or Windows 11, x64.
- FFmpeg on `PATH` is recommended for separate audio/video streams and subtitle embedding.
- The portable executable is not code-signed, so Windows SmartScreen may show an unknown-publisher warning. Verify the published SHA-256 checksum and download only from this repository's Release page.
- DRM, paid-access bypass, browser Cookie import, and account-session extraction are not supported.
- Site compatibility depends partly on yt-dlp and can change when a website changes.

## Privacy And Compliance

The Release contains only executable/runtime files, the least-privilege browser extension, its installer, and public text documentation. It contains no screenshots, local file listings, download history, browser profiles, Cookies, credentials, tokens, or photo-archive data.

Use the software only for academic research, technical learning, and media you are authorized to download. Do not use it to bypass DRM or other access controls, or to download, distribute, or commercially exploit copyrighted works without permission.
