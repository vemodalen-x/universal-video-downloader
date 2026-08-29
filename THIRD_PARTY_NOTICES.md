# Third-Party Notices

Universal Video Downloader includes or relies on open-source components. The project license does not replace the licenses of these components.

| Component | License | Purpose |
| --- | --- | --- |
| Python | PSF License | Runtime |
| Tcl/Tk | BSD-style license | Desktop user interface |
| yt-dlp | Unlicense | Public media extraction and download orchestration |
| BaiduPCS-Go v4.0.2 | Apache-2.0 | User-authorized Baidu Netdisk share transfer and download connector |
| curl_cffi / curl-impersonate | MIT | Browser TLS fingerprint-compatible HTTP transport for yt-dlp |
| Rich | MIT | curl_cffi terminal and diagnostic support |
| markdown-it-py | MIT | Rich Markdown rendering dependency |
| mdurl | MIT | markdown-it-py URL utility dependency |
| CFFI | MIT | Native interface used by curl_cffi |
| pycparser | BSD-3-Clause | C parser used by CFFI |
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

Runtime and packaging versions for the Windows release are fixed in `requirements-release.txt`; development-only versions are fixed in `requirements-dev.txt`. The BaiduPCS-Go Windows archive and executable are independently SHA-256 pinned by `build.ps1`, and its full Apache-2.0 text is shipped as `BaiduPCS-Go-LICENSE.txt`. Full source is available from the [BaiduPCS-Go upstream repository](https://github.com/qjfoidnh/BaiduPCS-Go/tree/v4.0.2).

No third-party component grants permission to download, redistribute, or commercially exploit copyrighted media without authorization.
