from __future__ import annotations

import hashlib
import json
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
    scan_source_tree,
)


def _credential_text() -> str:
    return f"{'xsec_' + 'token'}=redactedtoken12345"


def _local_user_path() -> str:
    return "C:\\" + "Users\\Example\\Downloads\\private.mp4"


def _bearer_credential() -> str:
    return "Authorization: " + "Bearer syntheticcredentialvalue123456"


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


def test_scan_package_rejects_generic_bearer_credentials(tmp_path: Path) -> None:
    archive = _write_package(tmp_path, extra_entries={"runtime.bin": _bearer_credential().encode()})

    with pytest.raises(PackageScanError, match="credential-like text"):
        scan_package(archive)


@pytest.mark.parametrize("encoding", ["utf-8", "utf-16-le"])
def test_scan_package_rejects_embedded_local_user_paths(tmp_path: Path, encoding: str) -> None:
    archive = _write_package(tmp_path, extra_entries={"runtime.bin": _local_user_path().encode(encoding)})

    with pytest.raises(PackageScanError, match="local-path"):
        scan_package(archive)


def test_scan_package_rejects_private_drive_paths(tmp_path: Path) -> None:
    private_path = "D:\\" + "private\\release\\history.json"
    archive = _write_package(tmp_path, extra_entries={"runtime.bin": private_path.encode()})

    with pytest.raises(PackageScanError, match="local-path"):
        scan_package(archive)


def test_scan_package_rejects_workspace_paths_in_binary_payloads(tmp_path: Path) -> None:
    archive = _write_package(tmp_path, extra_entries={"runtime.bin": b"C:\\src\\release\\private.txt"})

    with pytest.raises(PackageScanError, match="local-path"):
        scan_package(archive)


@pytest.mark.parametrize(
    ("entry", "payload"),
    [
        ("_internal/python-runtime.dll", b"C:\\src\\cpython\\python.c"),
        ("_internal/vendor.dist-info/METADATA", br"C:\Users\<user name> and ${APPDATA}"),
    ],
)
def test_scan_package_allows_generic_upstream_build_paths_in_internal_runtime(
    tmp_path: Path,
    entry: str,
    payload: bytes,
) -> None:
    archive = _write_package(
        tmp_path,
        extra_entries={entry: payload},
    )

    assert scan_package(archive).archive == archive.resolve()


def test_scan_package_rejects_user_paths_in_internal_runtime_metadata(tmp_path: Path) -> None:
    archive = _write_package(
        tmp_path,
        extra_entries={"_internal/vendor.dist-info/METADATA": _local_user_path().encode()},
    )

    with pytest.raises(PackageScanError, match="local-path"):
        scan_package(archive)


def test_scan_package_rejects_arbitrary_absolute_paths_in_text(tmp_path: Path) -> None:
    archive = _write_package(tmp_path, extra_entries={"runtime.json": b'{"cache": "C:\\\\src\\\\private.txt"}'})

    with pytest.raises(PackageScanError, match="local-path"):
        scan_package(archive)


@pytest.mark.parametrize("entry", ["screenshot.png", "docs/screen-capture.jpg", "capture.webp"])
def test_scan_package_rejects_unapproved_captures(tmp_path: Path, entry: str) -> None:
    archive = _write_package(tmp_path, extra_entries={entry: b"not public release artwork"})

    with pytest.raises(PackageScanError, match="forbidden local data"):
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


def test_scan_package_rejects_private_archive_comments(tmp_path: Path) -> None:
    archive = _write_package(tmp_path)
    with ZipFile(archive, "a") as zip_file:
        zip_file.comment = _bearer_credential().encode()
    _rewrite_checksum(archive)

    with pytest.raises(PackageScanError, match="Archive comment"):
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


def test_scan_source_tree_accepts_only_approved_product_images(tmp_path: Path) -> None:
    icon = tmp_path / "browser_extension" / "icon.png"
    icon.parent.mkdir()
    icon.write_bytes(b"public icon")
    readme = tmp_path / "README.md"
    readme.write_text("public documentation", encoding="utf-8")

    assert scan_source_tree(tmp_path, ["browser_extension/icon.png", "README.md"]) == 2

    capture = tmp_path / "release-screenshot.png"
    capture.write_bytes(b"private screenshot")
    with pytest.raises(PackageScanError, match="unapproved images"):
        scan_source_tree(tmp_path, ["browser_extension/icon.png", "release-screenshot.png"])


def test_scan_source_tree_rejects_absolute_user_paths(tmp_path: Path) -> None:
    config = tmp_path / "settings.json"
    config.write_text(_local_user_path(), encoding="utf-8")

    with pytest.raises(PackageScanError, match="absolute local user paths"):
        scan_source_tree(tmp_path, ["settings.json"])


def test_scan_source_tree_rejects_arbitrary_absolute_paths_in_text(tmp_path: Path) -> None:
    config = tmp_path / "settings.json"
    config.write_text(r"D:\src\release\private.txt", encoding="utf-8")

    with pytest.raises(PackageScanError, match="absolute local user paths"):
        scan_source_tree(tmp_path, ["settings.json"])


def test_v1_2_0_release_metadata_is_consistent() -> None:
    root = Path(__file__).resolve().parents[1]
    manifest = json.loads((root / "browser_extension" / "manifest.json").read_text(encoding="utf-8"))
    version_info = (root / "assets" / "version_info.txt").read_text(encoding="utf-8")
    bridge_version_info = (root / "assets" / "bridge_version_info.txt").read_text(encoding="utf-8")
    readme = (root / "README.md").read_text(encoding="utf-8")
    changelog = (root / "CHANGELOG.md").read_text(encoding="utf-8")
    release_notes = (root / "RELEASE_NOTES.md").read_text(encoding="utf-8")
    build_script = (root / "build.ps1").read_text(encoding="utf-8")
    app_spec = (root / "UniversalVideoDownloader.spec").read_text(encoding="utf-8")
    release_workflow = (root / ".github" / "workflows" / "release-windows.yml").read_text(encoding="utf-8")

    assert manifest["version"] == "1.2.0"
    assert "ProductVersion', u'1.2.0'" in version_info
    assert "FileVersion', u'1.2.0.0'" in version_info
    assert "ProductVersion', u'1.2.0'" in bridge_version_info
    assert "FileVersion', u'1.2.0.0'" in bridge_version_info
    assert "UniversalVideoDownloaderBridge.exe" in bridge_version_info
    assert "`v1.2.0`" in readme
    assert "## v1.2.0" in changelog
    assert "# Universal Video Downloader v1.2.0" in release_notes
    assert "requirements-release.txt" in build_script
    assert "--source-root" in build_script
    assert "endswith(('.py', '.pyc'))" in app_spec
    bridge_spec = (root / "UniversalVideoDownloaderBridge.spec").read_text(encoding="utf-8")
    assert "bridge_version_info.txt" in bridge_spec
    assert "--require-hashes" in build_script
    release_requirements = (root / "requirements-release.txt").read_text(encoding="utf-8")
    assert release_requirements.count("--hash=sha256:") >= 24
    assert "pytest==9.1.1" in release_requirements
    assert "ruff==0.15.22" in release_requirements
    assert "git rev-parse origin/main" in release_workflow
    assert "$mainCommit -ne $env:GITHUB_SHA" in release_workflow
    assert "runs-on: windows-2022" in release_workflow
    assert release_workflow.count("python -m pip install") == 1
    assert "actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02" in release_workflow
    assert "actions/download-artifact@d3f86a106a0bac45b974a628896c90dbdf5c8093" in release_workflow
    assert "needs: build" in release_workflow
    assert '$fileVersion = "$version.0"' in release_workflow
    assert '$binary.VersionInfo.ProductVersion -ne $version' in release_workflow
