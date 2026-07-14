from __future__ import annotations

import hashlib
import os
import stat
import time
from pathlib import Path
from zipfile import ZipFile, ZipInfo

import pytest

from tools.verify_release_package import (
    REQUIRED_ENTRIES,
    PackageScanError,
    find_latest_package,
    scan_package,
)


def _credential_text() -> str:
    return f"{'xsec_' + 'token'}=redactedtoken12345"


def _write_package(tmp_path: Path, version: str = "v9.9.9", extra_entries: dict[str, bytes] | None = None) -> Path:
    dist_dir = tmp_path / "dist"
    dist_dir.mkdir(exist_ok=True)
    archive = dist_dir / f"UniversalVideoDownloader-{version}-windows-x64.zip"
    entries = {name: b"public release content" for name in REQUIRED_ENTRIES}
    entries.update(extra_entries or {})
    with ZipFile(archive, "w") as zip_file:
        for name, content in entries.items():
            zip_file.writestr(name, content)
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    (dist_dir / f"SHA256SUMS-{version}.txt").write_text(f"{digest}  {archive.name}\n", encoding="utf-8")
    return archive


def _rewrite_checksum(archive: Path) -> None:
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    version = archive.name.removeprefix("UniversalVideoDownloader-").removesuffix("-windows-x64.zip")
    archive.with_name(f"SHA256SUMS-{version}.txt").write_text(f"{digest}  {archive.name}\n", encoding="utf-8")


def test_scan_package_accepts_valid_archive(tmp_path: Path) -> None:
    archive = _write_package(tmp_path)

    result = scan_package(archive)

    assert result.archive == archive.resolve()
    assert result.entry_count == len(REQUIRED_ENTRIES)
    assert len(result.sha256) == 64


def test_scan_package_rejects_missing_required_entry(tmp_path: Path) -> None:
    archive = _write_package(tmp_path)
    with ZipFile(archive, "w") as zip_file:
        zip_file.writestr("README.md", b"incomplete")
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    checksum = archive.with_name("SHA256SUMS-v9.9.9.txt")
    checksum.write_text(f"{digest}  {archive.name}\n", encoding="utf-8")

    with pytest.raises(PackageScanError, match="missing required entries"):
        scan_package(archive)


def test_scan_package_rejects_checksum_mismatch(tmp_path: Path) -> None:
    archive = _write_package(tmp_path)
    archive.with_name("SHA256SUMS-v9.9.9.txt").write_text(f"{'0' * 64}  {archive.name}\n", encoding="utf-8")

    with pytest.raises(PackageScanError, match="Checksum mismatch"):
        scan_package(archive)


def test_scan_package_rejects_missing_sibling_checksum(tmp_path: Path) -> None:
    archive = _write_package(tmp_path)
    archive.with_name("SHA256SUMS-v9.9.9.txt").unlink()

    with pytest.raises(PackageScanError, match="Missing checksum file"):
        scan_package(archive)


@pytest.mark.parametrize(
    "entry",
    [
        "tasks/private.md",
        "CON.txt",
        "folder/LPT1.md",
        "Default/Cookies",
        "Default/Cookies ",
        "Default/Network/Cookies",
        "Default/History",
        "Default/History.",
        "Default/Local State",
        "Default/Login Data",
        "Profile 1/Web Data",
        "Default/Secure Preferences",
        "places.sqlite",
        "logins.json",
        "Local State",
        "logs/release.txt",
        "photo_archive/export.json",
        "photo_archive_app.py",
        "vemo_photo/core.py",
        "../outside.txt",
        "browser-extension/./private.txt",
        "browser-extension/manifest.json:backup",
        "download.part",
    ],
)
def test_scan_package_rejects_private_or_transient_entries(tmp_path: Path, entry: str) -> None:
    archive = _write_package(tmp_path, extra_entries={entry: b"private"})

    with pytest.raises(PackageScanError, match="forbidden local data"):
        scan_package(archive)


def test_scan_package_rejects_credential_like_text(tmp_path: Path) -> None:
    archive = _write_package(tmp_path, extra_entries={"README.md": _credential_text().encode()})

    with pytest.raises(PackageScanError, match="credential-like text"):
        scan_package(archive)


def test_scan_package_rejects_oversized_text_entries(tmp_path: Path) -> None:
    archive = _write_package(tmp_path, extra_entries={"config": b"x" * (1024 * 1024 + 1)})

    with pytest.raises(PackageScanError, match="unsafe ZIP entries"):
        scan_package(archive)


@pytest.mark.parametrize("entry", ["config", ".env", "settings.bak"])
def test_scan_package_rejects_credential_like_extensionless_text(tmp_path: Path, entry: str) -> None:
    archive = _write_package(tmp_path, extra_entries={entry: _credential_text().encode()})

    with pytest.raises(PackageScanError, match="credential-like text"):
        scan_package(archive)


def test_scan_package_rejects_utf16_credential_like_text(tmp_path: Path) -> None:
    archive = _write_package(tmp_path, extra_entries={"config": _credential_text().encode("utf-16")})

    with pytest.raises(PackageScanError, match="credential-like text"):
        scan_package(archive)


def test_scan_package_rejects_utf32_credential_like_text(tmp_path: Path) -> None:
    archive = _write_package(tmp_path, extra_entries={"config": _credential_text().encode("utf-32")})

    with pytest.raises(PackageScanError, match="credential-like text"):
        scan_package(archive)


@pytest.mark.parametrize("encoding", ["utf-32-le", "utf-32-be"])
def test_scan_package_rejects_bomless_utf32_credential_like_text(tmp_path: Path, encoding: str) -> None:
    archive = _write_package(tmp_path, extra_entries={"config": _credential_text().encode(encoding)})

    with pytest.raises(PackageScanError, match="credential-like text"):
        scan_package(archive)


def test_scan_package_rejects_unsafe_directory_entries(tmp_path: Path) -> None:
    archive = _write_package(tmp_path)
    with ZipFile(archive, "a") as zip_file:
        zip_file.writestr("../", b"")
    _rewrite_checksum(archive)

    with pytest.raises(PackageScanError, match="forbidden local data"):
        scan_package(archive)


def test_scan_package_rejects_duplicate_entries(tmp_path: Path) -> None:
    archive = _write_package(tmp_path)
    with pytest.warns(UserWarning, match="Duplicate name"):
        with ZipFile(archive, "a") as zip_file:
            zip_file.writestr("README.md", b"duplicate")
    _rewrite_checksum(archive)

    with pytest.raises(PackageScanError, match="unsafe ZIP entries"):
        scan_package(archive)


@pytest.mark.parametrize(
    "alias",
    ["readme.md", "browser-extension//manifest.json", r"browser-extension\manifest.json"],
)
def test_scan_package_rejects_windows_path_collisions(tmp_path: Path, alias: str) -> None:
    archive = _write_package(tmp_path)
    with ZipFile(archive, "a") as zip_file:
        if "\\" in alias:
            entry = ZipInfo(alias)
            entry.filename = alias
            zip_file.writestr(entry, b"colliding content")
        else:
            zip_file.writestr(alias, b"colliding content")
    _rewrite_checksum(archive)

    with pytest.raises(PackageScanError, match="unsafe ZIP entries"):
        scan_package(archive)


def test_scan_package_rejects_symbolic_links(tmp_path: Path) -> None:
    archive = _write_package(tmp_path)
    link = ZipInfo("release-link")
    link.create_system = 3
    link.external_attr = (stat.S_IFLNK | 0o777) << 16
    with ZipFile(archive, "a") as zip_file:
        zip_file.writestr(link, b"target")
    _rewrite_checksum(archive)

    with pytest.raises(PackageScanError, match="unsafe ZIP entries"):
        scan_package(archive)


def test_find_latest_package_uses_most_recent_archive(tmp_path: Path) -> None:
    older = _write_package(tmp_path, version="v1.0.0")
    time.sleep(0.001)
    newer = _write_package(tmp_path, version="v2.0.0")
    os.utime(older, (older.stat().st_atime, older.stat().st_mtime - 1))

    assert find_latest_package(tmp_path / "dist") == newer
