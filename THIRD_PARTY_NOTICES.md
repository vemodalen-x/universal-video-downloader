# Third-Party Notices

Universal Video Downloader includes or relies on open-source components. The project license does not replace the licenses of these components.

| Component | License | Purpose |
| --- | --- | --- |
| Python | PSF License | Runtime |
| Tcl/Tk | BSD-style license | Desktop user interface |
| yt-dlp | Unlicense | Public media extraction and download orchestration |
| Requests | Apache-2.0 | HTTP client |
| urllib3 | MIT | HTTP connection pooling and retry support |
| certifi | MPL-2.0 | Certificate authority bundle |
| charset-normalizer | MIT | HTTP response character detection |
| idna | BSD | Internationalized domain names |
| PyCryptodome | BSD / Public Domain | HLS AES-128 support |
| websockets | BSD-3-Clause | Optional yt-dlp transport support |
| cryptography | Apache-2.0 / BSD-3-Clause / PSF-2.0 | TLS and optional HTTP transport support |
| Pillow | HPND | Development-only brand asset generation tooling |
| PyInstaller | GPL-2.0-or-later with bootloader exception | Windows packaging |

Runtime and packaging versions for the Windows release are fixed in `requirements-release.txt`; development-only versions are fixed in `requirements-dev.txt`. Full license texts and source information are available from each upstream project and its installed package metadata.

No third-party component grants permission to download, redistribute, or commercially exploit copyrighted media without authorization.
