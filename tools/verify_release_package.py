"""Verify a portable Universal Video Downloader release ZIP before publishing."""

from __future__ import annotations

import argparse
import hashlib
import re
import stat
import sys
from dataclasses import dataclass
from pathlib import Path
from pathlib import PurePosixPath
from zipfile import ZipFile


PACKAGE_PATTERN = re.compile(r"^UniversalVideoDownloader-(v[^-]+)-windows-x64\.zip$")
REQUIRED_ENTRIES = frozenset(
    {
        "UniversalVideoDownloader.exe",
        "UniversalVideoDownloaderBridge.exe",
        "browser-extension/manifest.json",
        "install_browser_companion.ps1",
        "README.md",
        "CHANGELOG.md",
        "LICENSE",
        "THIRD_PARTY_NOTICES.md",
    }
)
TEXT_ENTRY_SUFFIXES = frozenset({".bak", ".cfg", ".conf", ".css", ".html", ".ini", ".js", ".json", ".md", ".ps1", ".toml", ".txt", ".yaml", ".yml"})
MAX_TEXT_ENTRY_SIZE = 1024 * 1024
FORBIDDEN_ENTRY = re.compile(
    r"(?i)(?:^|/)(?:\.vemo(?:/|$)|tasks(?:/|$)|logs?(?:/|$)|"
    r"photo_archive(?:[_.-]|/|$)|vemo_photo(?:/|$)|"
    r"(?:default|profile \d+|guest profile|system profile)(?:/|$)|"
    r"(?:login data|web data|secure preferences|visited links|network action predictor|top sites|favicons|shortcuts)"
    r"(?:(?:-(?:journal|wal|shm))|(?:\.(?:sqlite|json)(?:-(?:journal|wal|shm))?))?$|"
    r"(?:places|formhistory|permissions)\.sqlite(?:-(?:journal|wal|shm))?$|logins\.json$|key[0-9]\.db$|"
    r"(?:cookies?|history|local state)(?:(?:-(?:journal|wal|shm))|(?:\.(?:sqlite|txt|json)(?:-(?:journal|wal|shm))?))?$|"
    r"cookies?(?:\.(?:sqlite|txt|json)(?:[.-].*)?|$)|"
    r"history(?:\.(?:sqlite|json)(?:[.-].*)?|$)|local state$|"
    r"download_history(?:/|$)|.*\.(?:part|log)$)"
)
SECRET_MARKER = re.compile(
    r"(?i)(?:gh[pousr]_[a-z0-9]{20,}|github_pat_[a-z0-9_]{20,}|xox[baprs]-[a-z0-9-]{20,}|"
    r"akia[0-9a-z]{16}|xsec_token\s*=\s*[a-z0-9_-]{16,})"
)
WINDOWS_UNSAFE_PATH_CHARACTERS = re.compile(r'[<>:"|?*\x00-\x1f]')
WINDOWS_RESERVED_DEVICE_NAME = re.compile(
    r"(?i)^(?:aux|clock\$|com[1-9\u00b9\u00b2\u00b3]|con|conin\$|conout\$|lpt[1-9\u00b9\u00b2\u00b3]|nul|prn)$"
)


class PackageScanError(ValueError):
    """Raised when a release archive fails a required safety or integrity check."""


@dataclass(frozen=True)
class PackageScanResult:
    archive: Path
    entry_count: int
    sha256: str


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _checksum_path(archive: Path) -> Path:
    match = PACKAGE_PATTERN.fullmatch(archive.name)
    if not match:
        raise PackageScanError(f"Unsupported release archive name: {archive.name}")
    return archive.with_name(f"SHA256SUMS-{match.group(1)}.txt")


def _expected_checksum(checksum_path: Path, archive_name: str) -> str:
    if not checksum_path.is_file():
        raise PackageScanError(f"Missing checksum file: {checksum_path.name}")
    for line in checksum_path.read_text(encoding="utf-8").splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[-1].lstrip("*") == archive_name:
            digest = parts[0].lower()
            if re.fullmatch(r"[0-9a-f]{64}", digest):
                return digest
    raise PackageScanError(f"Checksum file does not declare {archive_name}")


def _normalized_entry_name(name: str) -> str:
    return name.replace("\\", "/")


def _is_unsafe_entry_name(name: str) -> bool:
    if name.startswith("/") or re.match(r"^[a-zA-Z]:", name):
        return True
    for part in name.split("/"):
        device_name = part.rstrip(" .").split(".", maxsplit=1)[0]
        if (
            part in {".", ".."}
            or part.rstrip(" .") != part
            or WINDOWS_UNSAFE_PATH_CHARACTERS.search(part)
            or WINDOWS_RESERVED_DEVICE_NAME.fullmatch(device_name)
        ):
            return True
    return False


def _windows_extraction_key(name: str) -> str:
    """Return the case-insensitive path key Windows extraction would use."""
    return str(PurePosixPath(name)).casefold()


def _should_scan_text(name: str) -> bool:
    suffix = Path(name).suffix.lower()
    return suffix in TEXT_ENTRY_SUFFIXES or not suffix


def _contains_secret_marker(payload: bytes) -> bool:
    text_candidates = [payload.decode("utf-8", errors="ignore")]
    if payload.startswith((b"\xff\xfe\x00\x00", b"\x00\x00\xfe\xff")):
        text_candidates.append(payload.decode("utf-32", errors="ignore"))
    elif payload.startswith((b"\xff\xfe", b"\xfe\xff")):
        text_candidates.append(payload.decode("utf-16", errors="ignore"))
    elif b"\x00" in payload:
        text_candidates.extend(
            [
                payload.decode("utf-32-le", errors="ignore"),
                payload.decode("utf-32-be", errors="ignore"),
                payload.decode("utf-16-le", errors="ignore"),
                payload.decode("utf-16-be", errors="ignore"),
            ]
        )
    return any(SECRET_MARKER.search(text) for text in text_candidates)


def _scan_entries(archive: ZipFile) -> list[str]:
    names: list[str] = []
    forbidden: list[str] = []
    markers: list[str] = []
    unsafe: list[str] = []
    seen_windows_names: set[str] = set()
    for entry in archive.infolist():
        name = _normalized_entry_name(entry.filename)
        if not name:
            continue
        windows_key = _windows_extraction_key(name)
        if windows_key in seen_windows_names:
            unsafe.append(f"duplicate-windows-path:{name}")
        seen_windows_names.add(windows_key)
        if entry.flag_bits & 0x1:
            unsafe.append(f"encrypted:{name}")
        mode = entry.external_attr >> 16
        if entry.create_system == 3 and stat.S_IFMT(mode) == stat.S_IFLNK:
            unsafe.append(f"symlink:{name}")
        if _is_unsafe_entry_name(name) or FORBIDDEN_ENTRY.search(name):
            forbidden.append(name)
        if name.endswith("/"):
            continue
        names.append(name)
        if _should_scan_text(name):
            if entry.file_size > MAX_TEXT_ENTRY_SIZE:
                unsafe.append(f"oversized-text:{name}")
            elif _contains_secret_marker(archive.read(entry)):
                markers.append(name)
    if forbidden:
        raise PackageScanError(f"Archive contains forbidden local data: {', '.join(sorted(forbidden))}")
    if markers:
        raise PackageScanError(f"Archive contains credential-like text: {', '.join(sorted(markers))}")
    if unsafe:
        raise PackageScanError(f"Archive contains unsafe ZIP entries: {', '.join(sorted(unsafe))}")
    return names


def scan_package(archive: Path) -> PackageScanResult:
    archive = archive.resolve()
    if not archive.is_file():
        raise PackageScanError(f"Release archive not found: {archive}")

    expected_checksum = _expected_checksum(_checksum_path(archive), archive.name)
    actual_checksum = _sha256(archive)
    if actual_checksum != expected_checksum:
        raise PackageScanError(f"Checksum mismatch for {archive.name}")

    with ZipFile(archive) as zip_file:
        names = _scan_entries(zip_file)
    missing = sorted(REQUIRED_ENTRIES.difference(names))
    if missing:
        raise PackageScanError(f"Archive is missing required entries: {', '.join(missing)}")
    return PackageScanResult(archive=archive, entry_count=len(names), sha256=actual_checksum)


def find_latest_package(dist_dir: Path) -> Path:
    packages = [path for path in dist_dir.glob("UniversalVideoDownloader-v*-windows-x64.zip") if path.is_file()]
    if not packages:
        raise PackageScanError(f"No portable release archive found in: {dist_dir}")
    return max(packages, key=lambda path: (path.stat().st_mtime_ns, path.name))


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--zip", dest="archive", type=Path, help="Portable release ZIP to validate")
    parser.add_argument("--dist-dir", type=Path, default=Path("dist"), help="Directory to search when --zip is omitted")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    try:
        archive = args.archive or find_latest_package(args.dist_dir)
        result = scan_package(archive)
    except PackageScanError as error:
        print(f"package-scan: FAIL: {error}", file=sys.stderr)
        return 1
    print(f"package-scan: PASS: archive={result.archive.name} entries={result.entry_count} sha256={result.sha256}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
