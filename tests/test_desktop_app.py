from pathlib import Path
import struct
import threading
from types import SimpleNamespace
import zlib

import m3u8_desktop_app
from m3u8_core import CoalescingEventBuffer, DownloadPreferences, DownloadRecord, SubtitleTrack, VideoCandidate
from m3u8_desktop_app import (
    MAX_SEGMENT_BLOCKS,
    UI_REFRESH_INTERVAL_MS,
    UniversalVideoDownloaderApp,
    _available_output_path,
    _browser_companion_installed_extension_path,
    _browser_companion_package_paths,
    _candidate_format_label,
    _candidate_summary,
    _history_status_label,
    _history_filter_status,
    _history_record_matches,
    _plan_output_paths,
    _subtitle_choice_map,
)


def test_ui_refresh_budget_and_segment_cap() -> None:
    assert UI_REFRESH_INTERVAL_MS >= 100
    assert UI_REFRESH_INTERVAL_MS <= 100
    assert MAX_SEGMENT_BLOCKS <= 200


def test_browser_companion_package_paths_are_portable(tmp_path) -> None:
    installer, bridge, extension = _browser_companion_package_paths(tmp_path)

    assert installer == tmp_path / "install_browser_companion.ps1"
    assert bridge == tmp_path / "UniversalVideoDownloaderBridge.exe"
    assert extension == tmp_path / "browser-extension"
    assert _browser_companion_installed_extension_path(tmp_path) == (
        tmp_path / "UniversalVideoDownloader" / "browser-companion" / "extension"
    )


def test_browser_companion_setup_worker_uses_argument_list(monkeypatch, tmp_path) -> None:
    captured: dict[str, object] = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        return SimpleNamespace(returncode=0, stdout="registered", stderr="")

    monkeypatch.setattr(m3u8_desktop_app.subprocess, "run", fake_run)
    app = object.__new__(UniversalVideoDownloaderApp)
    app.event_buffer = CoalescingEventBuffer()
    installer = tmp_path / "install_browser_companion.ps1"
    extension = tmp_path / "browser-extension"

    app._browser_companion_setup_worker(installer, extension)

    assert captured["command"][-2:] == ["-File", str(installer)]
    assert captured["kwargs"]["check"] is False
    assert "shell" not in captured["kwargs"]
    assert app.event_buffer.drain() == [("browser_companion_installed", {"extension": str(extension)})]


def test_browser_companion_setup_worker_reports_failure(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        m3u8_desktop_app.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=1, stdout="", stderr="registration denied"),
    )
    app = object.__new__(UniversalVideoDownloaderApp)
    app.event_buffer = CoalescingEventBuffer()

    app._browser_companion_setup_worker(tmp_path / "install.ps1", tmp_path / "extension")

    assert app.event_buffer.drain() == [("browser_companion_install_error", {"error": "registration denied"})]


def test_output_path_avoids_overwriting_existing_file(tmp_path) -> None:
    original = tmp_path / "video.mp4"
    original.write_bytes(b"existing")

    candidate = _available_output_path(original)

    assert candidate == tmp_path / "video (1).mp4"
    assert original.read_bytes() == b"existing"


def test_candidate_presentation_uses_human_labels() -> None:
    candidate = VideoCandidate(
        title="Example",
        url="https://cdn.example.com/video.m3u8",
        source_url="https://example.com/watch",
        resolution="1920x1080",
        segment_count=42,
        source_type="hls",
        container="m3u8",
    )

    assert _candidate_format_label(candidate) == "M3U8"
    assert "1920x1080" in _candidate_summary(candidate)
    assert "42 个分片" in _candidate_summary(candidate)
    assert _history_status_label("interrupted") == "已中断"


def test_batch_output_paths_use_titles_and_reserve_duplicate_names(tmp_path) -> None:
    first = VideoCandidate(
        title="Episode / 网页",
        url="https://example.com/one",
        source_url="https://example.com/playlist",
        source_type="ytdlp",
    )
    second = VideoCandidate(
        title="Episode / 网页",
        url="https://example.com/two",
        source_url="https://example.com/playlist",
        source_type="ytdlp",
    )
    (tmp_path / "Episode.mp4").write_bytes(b"existing")

    planned = _plan_output_paths([first, second], tmp_path, "ignored.mp4")

    assert [path.name for _candidate, path in planned] == ["Episode (1).mp4", "Episode (2).mp4"]


def test_download_queue_continues_after_an_item_fails(monkeypatch, tmp_path) -> None:
    run_order: list[str] = []

    class FakeJob:
        def __init__(self, url: str, output_path: Path, callback, **_kwargs) -> None:
            self.url = url
            self.output_path = output_path
            self.callback = callback

        def run(self) -> None:
            run_order.append(self.url)
            if self.url.endswith("one"):
                self.callback("fatal", {"message": "synthetic failure"})
            else:
                self.callback("completed", {"output": str(self.output_path)})

        def stop(self) -> None:
            return None

    monkeypatch.setattr(m3u8_desktop_app, "YouTubeDownloadJob", FakeJob)
    app = object.__new__(UniversalVideoDownloaderApp)
    app.event_buffer = CoalescingEventBuffer()
    app.queue_stop_event = threading.Event()
    app.current_job = None
    first = VideoCandidate("One", "https://example.com/one", "https://example.com/list", source_type="ytdlp")
    second = VideoCandidate("Two", "https://example.com/two", "https://example.com/list", source_type="ytdlp")

    app._download_worker(
        [(first, tmp_path / "one.mp4"), (second, tmp_path / "two.mp4")],
        concurrency=4,
        keep_cache=True,
        preferences=DownloadPreferences(),
        referer_override="",
    )

    events = app.event_buffer.drain()
    assert run_order == ["https://example.com/one", "https://example.com/two"]
    assert [event for event, _payload in events].count("queue_item_failed") == 1
    assert [event for event, _payload in events].count("queue_item_completed") == 1
    assert events[-1] == ("queue_finished", {"completed": 1, "failed": 1, "total": 2, "stopped": False})


def test_subtitle_choices_mark_automatic_only_languages() -> None:
    candidate = VideoCandidate(
        title="Video",
        url="https://example.com/video",
        source_url="https://example.com/video",
        subtitles=(
            SubtitleTrack("zh-Hans", automatic=False),
            SubtitleTrack("en", automatic=True),
            SubtitleTrack("ja", automatic=False),
            SubtitleTrack("ja", automatic=True),
        ),
    )

    choices = _subtitle_choice_map([candidate])

    assert choices == {
        "en（自动）": ("en", True),
        "ja": ("ja", False),
        "zh-Hans": ("zh-Hans", False),
    }


def test_history_filter_matches_status_and_searchable_metadata() -> None:
    record = DownloadRecord(
        record_id="task-1",
        title="Lecture 01",
        source_type="hls",
        source_url="https://example.com/watch",
        source_host="example.com",
        output_path="C:/Videos/Lecture 01.mp4",
        status="failed",
    )

    assert _history_filter_status("需重试") == "failed"
    assert _history_filter_status("全部状态") is None
    assert _history_record_matches(record, "example.com", "需重试")
    assert _history_record_matches(record, "lecture", "全部状态")
    assert not _history_record_matches(record, "lecture", "已完成")
    assert not _history_record_matches(record, "missing", "全部状态")


def test_v2_brand_assets_cover_windows_icon_sizes() -> None:
    assets = Path(__file__).resolve().parents[1] / "assets"
    assert _png_metadata(assets / "app_brand_v2.png") == (1024, 1024, 0)
    assert _png_metadata(assets / "app_icon_v2_64.png") == (64, 64, 0)
    assert _png_metadata(assets / "app_brand_v2_40.png") == (40, 40, 0)
    assert {(16, 16), (32, 32), (64, 64), (256, 256)} <= _ico_sizes(assets / "app_icon_v2.ico")


def _png_metadata(path: Path) -> tuple[int, int, int]:
    """Return width, height, and top-left alpha for an 8-bit RGBA PNG."""

    data = path.read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    position = 8
    idat = bytearray()
    width = height = 0
    while position < len(data):
        length = struct.unpack(">I", data[position : position + 4])[0]
        chunk_type = data[position + 4 : position + 8]
        payload = data[position + 8 : position + 8 + length]
        position += length + 12
        if chunk_type == b"IHDR":
            width, height, bit_depth, color_type = struct.unpack(">IIBB", payload[:10])
            assert bit_depth == 8
            assert color_type == 6
        elif chunk_type == b"IDAT":
            idat.extend(payload)
        elif chunk_type == b"IEND":
            break

    scanline = bytearray(zlib.decompress(bytes(idat))[1 : 1 + width * 4])
    filter_type = zlib.decompress(bytes(idat))[0]
    for index, value in enumerate(scanline):
        left = scanline[index - 4] if index >= 4 else 0
        if filter_type == 1:
            scanline[index] = (value + left) & 0xFF
        elif filter_type == 2:
            scanline[index] = value
        elif filter_type == 3:
            scanline[index] = (value + left // 2) & 0xFF
        elif filter_type == 4:
            scanline[index] = (value + left) & 0xFF
        else:
            assert filter_type == 0
    return width, height, scanline[3]


def _ico_sizes(path: Path) -> set[tuple[int, int]]:
    data = path.read_bytes()
    reserved, image_type, count = struct.unpack("<HHH", data[:6])
    assert (reserved, image_type) == (0, 1)
    sizes: set[tuple[int, int]] = set()
    for index in range(count):
        width, height = struct.unpack("BB", data[6 + index * 16 : 8 + index * 16])
        sizes.add((width or 256, height or 256))
    return sizes
