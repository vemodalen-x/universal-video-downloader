from pathlib import Path
import struct
import threading
from types import SimpleNamespace
import zlib

import m3u8_desktop_app
from m3u8_core import CoalescingEventBuffer, DownloadPreferences, DownloadRecord, HlsError, SubtitleTrack, VideoCandidate
from m3u8_desktop_app import (
    MAX_SEGMENT_BLOCKS,
    UI_REFRESH_INTERVAL_MS,
    UniversalVideoDownloaderApp,
    _available_output_path,
    _browser_companion_installed_extension_path,
    _browser_companion_package_paths,
    _candidate_format_label,
    _candidate_kind_label,
    _candidate_media_key,
    _candidate_summary,
    _default_suffix_for_candidate,
    _deduplicate_candidates,
    _duplicate_queue_indices,
    _filter_duplicate_queue,
    _history_type_label,
    _history_status_label,
    _history_filter_status,
    _history_output_settings,
    _history_record_matches,
    _history_retry_candidate_index,
    _history_retry_source_available,
    _history_source_url,
    _plan_output_paths,
    _share_access_code_from_url,
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


def test_media_key_ignores_signed_query_and_deduplicates_selection() -> None:
    first = VideoCandidate(
        "Video",
        "https://cdn.example.com/video.mp4?token=first#fragment",
        "https://example.com/watch",
        source_type="direct",
    )
    refreshed = VideoCandidate(
        "Video",
        "https://cdn.example.com/video.mp4?token=second",
        "https://example.com/watch",
        source_type="direct",
    )

    candidates, repeated = _deduplicate_candidates([first, refreshed])

    assert _candidate_media_key(first) == _candidate_media_key(refreshed)
    assert len(_candidate_media_key(first)) == 64
    assert "token" not in _candidate_media_key(first)
    assert candidates == [first]
    assert repeated == 1


def test_duplicate_queue_detects_existing_target_and_completed_media(tmp_path) -> None:
    existing_target = tmp_path / "existing.mp4"
    existing_target.write_bytes(b"complete")
    archived_output = tmp_path / "archive.mp4"
    archived_output.write_bytes(b"complete")
    first = VideoCandidate("First", "https://example.com/first", "https://example.com/first", source_type="ytdlp")
    second = VideoCandidate("Second", "https://example.com/second", "https://example.com/second", source_type="ytdlp")
    history = [
        DownloadRecord(
            record_id="completed-2",
            title="Second",
            source_type="ytdlp",
            source_url="https://example.com/second",
            source_host="example.com",
            output_path=str(archived_output),
            status="completed",
            media_key=_candidate_media_key(second),
        )
    ]

    duplicates = _duplicate_queue_indices(
        [(first, existing_target), (second, tmp_path / "new-location.mp4")],
        history,
    )

    assert duplicates == {0, 1}


def test_duplicate_filter_skips_existing_items_and_preserves_missing_order(tmp_path) -> None:
    first = VideoCandidate("First", "https://example.com/first", "https://example.com/first", source_type="ytdlp")
    second = VideoCandidate("Second", "https://example.com/second", "https://example.com/second", source_type="ytdlp")
    third = VideoCandidate("Third", "https://example.com/third", "https://example.com/third", source_type="ytdlp")
    existing = tmp_path / "second.mp4"
    existing.write_bytes(b"complete")
    queue = [
        (first, tmp_path / "first.mp4"),
        (second, existing),
        (third, tmp_path / "third.mp4"),
    ]

    filtered, skipped = _filter_duplicate_queue(queue, [])

    assert filtered == [queue[0], queue[2]]
    assert skipped == 1


def test_start_download_does_not_create_worker_when_filename_exists(monkeypatch, tmp_path) -> None:
    candidate = VideoCandidate(
        "Video",
        "https://example.com/video",
        "https://example.com/video",
        source_type="ytdlp",
    )
    (tmp_path / "video.mp4").write_bytes(b"complete")
    notices: list[tuple[str, str, str]] = []
    logs: list[str] = []
    app = object.__new__(UniversalVideoDownloaderApp)
    app._selected_candidates = lambda: [candidate]
    app.output_dir_var = SimpleNamespace(get=lambda: str(tmp_path))
    app.file_name_var = SimpleNamespace(get=lambda: "video.mp4")
    app.history_records = []
    app._show_notice = lambda kind, title, text: notices.append((kind, title, text))
    app._log = lambda message, *_args: logs.append(message)

    def unexpected_thread(*_args, **_kwargs):
        raise AssertionError("download worker must not start for an existing filename")

    monkeypatch.setattr(m3u8_desktop_app.threading, "Thread", unexpected_thread)

    app._start_download()

    assert notices == [("info", "没有需要下载的项目", "已跳过 1 个重复或已存在的视频。")]
    assert logs == ["重复检测已跳过 1 个条目"]


def test_output_planning_can_preserve_existing_name_for_confirmed_overwrite(tmp_path) -> None:
    candidate = VideoCandidate(
        "Video",
        "https://example.com/video",
        "https://example.com/video",
        source_type="ytdlp",
    )
    (tmp_path / "video.mp4").write_bytes(b"complete")

    planned = _plan_output_paths([candidate], tmp_path, "video.mp4", avoid_existing=False)

    assert planned == [(candidate, tmp_path / "video.mp4")]


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


def test_pikpak_candidate_uses_direct_resume_presentation() -> None:
    candidate = VideoCandidate(
        title="Episode 01 / PikPak",
        url="https://cdn.example.com/file?signature=private",
        source_url="https://mypikpak.com/s/share-id",
        source_type="pikpak",
        container="mp4",
        extractor="PikPak Share",
    )

    assert _candidate_kind_label(candidate) == "PikPak 分享"
    assert _candidate_format_label(candidate) == "MP4"
    assert "单文件续传" in _candidate_summary(candidate)
    assert _default_suffix_for_candidate(candidate) == ".mp4"
    assert _history_type_label(candidate.source_type) == "PikPak"


def test_baidupan_candidate_uses_directory_task_presentation_and_output(tmp_path) -> None:
    candidate = VideoCandidate(
        title="Lessons / 百度网盘",
        url="https://pan.baidu.com/s/1SyntheticShareToken",
        source_url="https://pan.baidu.com/s/1SyntheticShareToken",
        source_type="baidupan",
        container="folder",
        extractor="Baidu Netdisk Connector",
    )

    assert _candidate_kind_label(candidate) == "百度网盘分享"
    assert _candidate_format_label(candidate) == "目录"
    assert "BaiduPCS-Go" in _candidate_summary(candidate)
    assert "完整分享目录" in _candidate_summary(candidate)
    assert _default_suffix_for_candidate(candidate) == ""
    assert _history_type_label(candidate.source_type) == "百度网盘"
    assert _plan_output_paths([candidate], tmp_path, "ignored.mp4") == [(candidate, tmp_path)]


def test_analysis_worker_forwards_access_code_without_returning_it(monkeypatch) -> None:
    captured: dict[str, object] = {}
    candidate = VideoCandidate(
        title="PikPak Video",
        url="https://cdn.example.com/video.mp4",
        source_url="https://mypikpak.com/s/share-id",
        source_type="pikpak",
    )

    def fake_discover(url: str, **kwargs) -> list[VideoCandidate]:
        captured.update({"url": url, **kwargs})
        return [candidate]

    monkeypatch.setattr(m3u8_desktop_app, "discover_candidates", fake_discover)
    app = object.__new__(UniversalVideoDownloaderApp)
    app.event_buffer = CoalescingEventBuffer()
    app._core_callback = lambda *_args, **_kwargs: None

    app._analyze_worker("https://mypikpak.com/s/share-id", "", "2468")

    assert captured["access_code"] == "2468"
    assert app.event_buffer.drain() == [("analysis_done", {"candidates": [candidate]})]


def test_share_access_code_is_read_only_from_supported_provider_query() -> None:
    assert _share_access_code_from_url("https://pan.baidu.com/s/1SyntheticShareToken?pwd=a1b2") == "a1b2"
    assert _share_access_code_from_url("https://mypikpak.com/s/synthetic?s_code=c3d4") == "c3d4"
    assert _share_access_code_from_url("https://example.com/watch?pwd=should-not-be-used") == ""


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
    monkeypatch.setattr(m3u8_desktop_app, "BATCH_ITEM_RETRY_BACKOFF_SECONDS", 0)
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
    assert run_order == [
        "https://example.com/one",
        "https://example.com/one",
        "https://example.com/one",
        "https://example.com/two",
    ]
    assert [event for event, _payload in events].count("queue_item_retry") == 2
    assert [event for event, _payload in events].count("queue_item_failed") == 1
    assert [event for event, _payload in events].count("queue_item_completed") == 1
    assert events[-1] == (
        "queue_finished",
        {"completed": 1, "failed": 1, "total": 2, "stopped": False},
    )


def test_download_queue_retries_current_item_until_it_succeeds(monkeypatch, tmp_path) -> None:
    run_order: list[str] = []
    attempts: dict[str, int] = {}

    class FakeJob:
        def __init__(self, url: str, output_path: Path, callback, **_kwargs) -> None:
            self.url = url
            self.output_path = output_path
            self.callback = callback

        def run(self) -> None:
            run_order.append(self.url)
            attempts[self.url] = attempts.get(self.url, 0) + 1
            if self.url.endswith("one") and attempts[self.url] < 3:
                self.callback("fatal", {"message": "synthetic connection failure"})
            else:
                self.callback("completed", {"output": str(self.output_path)})

        def stop(self) -> None:
            return None

    monkeypatch.setattr(m3u8_desktop_app, "YouTubeDownloadJob", FakeJob)
    monkeypatch.setattr(m3u8_desktop_app, "BATCH_ITEM_RETRY_BACKOFF_SECONDS", 0)
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
    assert run_order == [
        "https://example.com/one",
        "https://example.com/one",
        "https://example.com/one",
        "https://example.com/two",
    ]
    assert [event for event, _payload in events].count("queue_item_retry") == 2
    assert [event for event, _payload in events].count("queue_item_failed") == 0
    assert events[-1] == (
        "queue_finished",
        {"completed": 2, "failed": 0, "total": 2, "stopped": False},
    )


def test_download_queue_does_not_retry_permanent_item_error(monkeypatch, tmp_path) -> None:
    run_order: list[str] = []

    class FakeJob:
        def __init__(self, url: str, output_path: Path, callback, **_kwargs) -> None:
            self.url = url
            self.output_path = output_path
            self.callback = callback

        def run(self) -> None:
            run_order.append(self.url)
            if self.url.endswith("one"):
                self.callback("fatal", {"error": HlsError("PikPak 提取码错误。")})
            else:
                self.callback("completed", {"output": str(self.output_path)})

        def stop(self) -> None:
            return None

    monkeypatch.setattr(m3u8_desktop_app, "YouTubeDownloadJob", FakeJob)
    monkeypatch.setattr(m3u8_desktop_app, "BATCH_ITEM_RETRY_BACKOFF_SECONDS", 0)
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
    assert [event for event, _payload in events].count("queue_item_retry") == 0
    assert [event for event, _payload in events].count("queue_item_failed") == 1


def test_download_queue_forwards_confirmed_overwrite_to_ytdlp(monkeypatch, tmp_path) -> None:
    captured: dict[str, object] = {}

    class FakeJob:
        def __init__(self, url: str, output_path: Path, callback, overwrite_existing: bool, **_kwargs) -> None:
            captured.update(
                {
                    "url": url,
                    "output_path": output_path,
                    "overwrite_existing": overwrite_existing,
                }
            )
            self.output_path = output_path
            self.callback = callback

        def run(self) -> None:
            self.callback("completed", {"output": str(self.output_path)})

    monkeypatch.setattr(m3u8_desktop_app, "YouTubeDownloadJob", FakeJob)
    app = object.__new__(UniversalVideoDownloaderApp)
    app.event_buffer = CoalescingEventBuffer()
    app.queue_stop_event = threading.Event()
    app.current_job = None
    candidate = VideoCandidate(
        "Video",
        "https://example.com/video",
        "https://example.com/video",
        source_type="ytdlp",
    )

    app._download_worker(
        [(candidate, tmp_path / "video.mp4")],
        concurrency=4,
        keep_cache=True,
        preferences=DownloadPreferences(),
        referer_override="",
        overwrite_existing=True,
    )

    assert captured["overwrite_existing"] is True
    assert app.event_buffer.drain()[-1] == (
        "queue_finished",
        {"completed": 1, "failed": 0, "total": 1, "stopped": False},
    )


def test_baidupan_download_uses_share_connector_job(monkeypatch, tmp_path) -> None:
    captured: dict[str, object] = {}

    class FakeBaiduJob:
        def __init__(self, source_url: str, output_dir: Path, access_code: str, callback) -> None:
            captured.update(
                {
                    "source_url": source_url,
                    "output_dir": output_dir,
                    "access_code": access_code,
                }
            )
            self.output_dir = output_dir
            self.callback = callback

        def run(self) -> None:
            self.callback("completed", {"output": str(self.output_dir)})

        def stop(self) -> None:
            return None

    monkeypatch.setattr(m3u8_desktop_app, "BaiduPanDownloadJob", FakeBaiduJob)
    app = object.__new__(UniversalVideoDownloaderApp)
    app.event_buffer = CoalescingEventBuffer()
    app.queue_stop_event = threading.Event()
    app.current_job = None
    candidate = VideoCandidate(
        "Lessons",
        "https://pan.baidu.com/s/1SyntheticShareToken",
        "https://pan.baidu.com/s/1SyntheticShareToken",
        source_type="baidupan",
    )

    app._download_worker(
        [(candidate, tmp_path)],
        concurrency=4,
        keep_cache=True,
        preferences=DownloadPreferences(),
        referer_override="",
        share_source_url="https://pan.baidu.com/s/1SyntheticShareToken",
        share_access_code="a1b2",
    )

    assert captured == {
        "source_url": "https://pan.baidu.com/s/1SyntheticShareToken",
        "output_dir": tmp_path,
        "access_code": "a1b2",
    }
    assert app.event_buffer.drain()[-1] == (
        "queue_finished",
        {"completed": 1, "failed": 0, "total": 1, "stopped": False},
    )


def test_download_queue_stop_during_retry_backoff_cancels_remaining_items(monkeypatch, tmp_path) -> None:
    run_order: list[str] = []

    class StopOnWaitEvent(threading.Event):
        def wait(self, timeout: float | None = None) -> bool:
            self.set()
            return True

    class FakeJob:
        def __init__(self, url: str, output_path: Path, callback, **_kwargs) -> None:
            self.url = url
            self.callback = callback

        def run(self) -> None:
            run_order.append(self.url)
            self.callback("fatal", {"message": "synthetic connection failure"})

        def stop(self) -> None:
            return None

    monkeypatch.setattr(m3u8_desktop_app, "YouTubeDownloadJob", FakeJob)
    app = object.__new__(UniversalVideoDownloaderApp)
    app.event_buffer = CoalescingEventBuffer()
    app.queue_stop_event = StopOnWaitEvent()
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
    assert run_order == ["https://example.com/one"]
    assert [event for event, _payload in events].count("queue_item_retry") == 1
    assert events[-1] == (
        "queue_finished",
        {"completed": 0, "failed": 0, "total": 2, "stopped": True},
    )


def test_download_queue_stops_remaining_items_after_explicit_stop(monkeypatch, tmp_path) -> None:
    run_order: list[str] = []

    class FakeJob:
        def __init__(self, url: str, output_path: Path, callback, **_kwargs) -> None:
            self.url = url
            self.callback = callback

        def run(self) -> None:
            run_order.append(self.url)
            self.callback("stopped", {})

        def stop(self) -> None:
            return None

    monkeypatch.setattr(m3u8_desktop_app, "YouTubeDownloadJob", FakeJob)
    monkeypatch.setattr(m3u8_desktop_app, "BATCH_ITEM_RETRY_BACKOFF_SECONDS", 0)
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
    assert run_order == ["https://example.com/one"]
    assert [event for event, _payload in events].count("queue_item_stopped") == 1
    assert events[-1] == (
        "queue_finished",
        {"completed": 0, "failed": 0, "total": 2, "stopped": True},
    )


def test_pikpak_download_uses_resumable_direct_job(monkeypatch, tmp_path) -> None:
    captured: dict[str, object] = {}

    class FakeDirectJob:
        def __init__(
            self,
            url: str,
            output_path: Path,
            headers: dict,
            callback,
            url_refresher=None,
            resume_key: str = "",
        ) -> None:
            captured.update(
                {
                    "url": url,
                    "output_path": output_path,
                    "headers": headers,
                    "url_refresher": url_refresher,
                    "resume_key": resume_key,
                }
            )
            self.output_path = output_path
            self.callback = callback
            self.url_refresher = url_refresher

        def run(self) -> None:
            if self.url_refresher:
                captured["refreshed_url"] = self.url_refresher()
            self.callback("completed", {"output": str(self.output_path)})

    def fake_refresh(candidate, source_url: str, access_code: str, callback) -> VideoCandidate:
        captured.update(
            {
                "refresh_candidate": candidate,
                "refresh_source_url": source_url,
                "refresh_access_code": access_code,
                "refresh_callback": callback,
            }
        )
        return VideoCandidate(
            candidate.title,
            "https://cdn.example.com/video.mp4?signature=fresh",
            candidate.source_url,
            source_type="pikpak",
            container="mp4",
            media_id=candidate.media_id,
        )

    monkeypatch.setattr(m3u8_desktop_app, "DirectDownloadJob", FakeDirectJob)
    monkeypatch.setattr(m3u8_desktop_app, "refresh_pikpak_candidate", fake_refresh)
    app = object.__new__(UniversalVideoDownloaderApp)
    app.event_buffer = CoalescingEventBuffer()
    app.queue_stop_event = threading.Event()
    app.current_job = None
    candidate = VideoCandidate(
        "PikPak Video",
        "https://cdn.example.com/video.mp4?signature=private",
        "https://mypikpak.com/s/share-id",
        source_type="pikpak",
        container="mp4",
        media_id="stable-video-id",
    )

    app._download_worker(
        [(candidate, tmp_path / "video.mp4")],
        concurrency=4,
        keep_cache=True,
        preferences=DownloadPreferences(),
        referer_override="",
        pikpak_source_url="https://mypikpak.com/s/share-id",
        pikpak_access_code="sample-code",
    )

    assert captured["url"] == candidate.url
    assert captured["output_path"] == tmp_path / "video.mp4"
    assert captured["resume_key"] == candidate.media_id
    assert callable(captured["url_refresher"])
    assert captured["refreshed_url"] == "https://cdn.example.com/video.mp4?signature=fresh"
    assert captured["refresh_candidate"] is candidate
    assert captured["refresh_source_url"] == candidate.source_url
    assert captured["refresh_access_code"] == "sample-code"
    assert "sample-code" not in repr(candidate)
    assert app.event_buffer.drain()[-1] == (
        "queue_finished",
        {"completed": 1, "failed": 0, "total": 1, "stopped": False},
    )


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


def test_history_retry_source_uses_query_free_youtube_identity() -> None:
    candidate = VideoCandidate(
        title="Video / YouTube",
        url="https://www.youtube.com/watch?v=VIDEO_ID&token=private",
        source_url="https://www.youtube.com/watch?v=VIDEO_ID&token=private",
        source_type="youtube",
        media_id="VIDEO_ID",
    )
    record = DownloadRecord(
        record_id="retry-1",
        title="Video",
        source_type="youtube",
        source_url=_history_source_url(candidate),
        source_host="youtu.be",
        output_path="C:/Videos/Video.mp4",
        status="failed",
    )
    legacy = DownloadRecord(
        record_id="legacy",
        title="Video",
        source_type="youtube",
        source_url="https://www.youtube.com/watch",
        source_host="youtube.com",
        output_path="C:/Videos/Video.mp4",
        status="failed",
    )

    assert record.source_url == "https://youtu.be/VIDEO_ID"
    assert "token" not in record.source_url
    assert _history_retry_source_available(record)
    assert not _history_retry_source_available(legacy)


def test_history_retry_candidate_prefers_media_key_and_rejects_ambiguous_fallback() -> None:
    expected = VideoCandidate("Episode 01", "https://cdn.example.com/one.m3u8", "https://example.com/watch", source_type="hls")
    other = VideoCandidate("Episode 02", "https://cdn.example.com/two.m3u8", "https://example.com/watch", source_type="hls")
    record = DownloadRecord(
        record_id="retry-1",
        title="Episode 01",
        source_type="hls",
        source_url="https://example.com/watch",
        source_host="example.com",
        output_path="C:/Videos/Episode 01.mp4",
        status="failed",
        media_key=_candidate_media_key(expected),
    )

    assert _history_retry_candidate_index(record, [other, expected]) == 1
    assert _history_retry_candidate_index(record, [other, VideoCandidate("Episode 03", "https://cdn.example.com/three.m3u8", "https://example.com/watch", source_type="hls")]) is None


def test_retry_history_restores_output_and_starts_analysis(tmp_path) -> None:
    output = tmp_path / "episode.mp4"
    record = DownloadRecord(
        record_id="retry-1",
        title="Episode",
        source_type="hls",
        source_url="https://example.com/watch/episode",
        source_host="example.com",
        output_path=str(output),
        status="failed",
    )
    values: dict[str, str] = {}
    analyzed: list[bool] = []
    app = object.__new__(UniversalVideoDownloaderApp)
    app._selected_history = lambda: record
    app.is_analyzing = False
    app.is_downloading = False
    app.url_var = SimpleNamespace(set=lambda value: values.__setitem__("url", value))
    app.output_dir_var = SimpleNamespace(set=lambda value: values.__setitem__("dir", value))
    app.file_name_var = SimpleNamespace(set=lambda value: values.__setitem__("name", value))
    app.main_notebook = SimpleNamespace(select=lambda _tab: None)
    app.download_tab = object()
    app.url_entry = SimpleNamespace(focus_set=lambda: None)
    app._show_notice = lambda *_args: None
    app._log = lambda *_args: None
    app._start_analyze = lambda: analyzed.append(True)

    app._retry_history()

    assert app.pending_history_retry is record
    assert values == {"url": record.source_url, "dir": str(tmp_path), "name": "episode.mp4"}
    assert analyzed == [True]


def test_continue_history_after_analysis_reuses_record_id_and_exact_output(tmp_path) -> None:
    candidate = VideoCandidate("Episode", "https://cdn.example.com/video.m3u8", "https://example.com/watch", source_type="hls")
    output = tmp_path / "episode.mp4"
    record = DownloadRecord(
        record_id="retry-1",
        title="Episode",
        source_type="hls",
        source_url="https://example.com/watch",
        source_host="example.com",
        output_path=str(output),
        status="failed",
        media_key=_candidate_media_key(candidate),
    )
    values: dict[str, str] = {}
    started: list[bool] = []
    selected: list[str] = []
    app = object.__new__(UniversalVideoDownloaderApp)
    app.pending_history_retry = record
    app.candidates = [candidate]
    app.candidate_tree = SimpleNamespace(
        selection_set=lambda iid: selected.append(iid),
        focus=lambda _iid: None,
        see=lambda _iid: None,
    )
    app._sync_selection = lambda: None
    app.output_dir_var = SimpleNamespace(set=lambda value: values.__setitem__("dir", value))
    app.file_name_var = SimpleNamespace(set=lambda value: values.__setitem__("name", value))
    app._show_notice = lambda *_args: None
    app._start_download = lambda: started.append(True)

    app._continue_history_after_analysis()

    assert selected == ["0"]
    assert values == {"dir": str(tmp_path), "name": "episode.mp4"}
    assert app.history_retry_record_id == "retry-1"
    assert started == [True]


def test_continue_history_does_not_start_when_media_cannot_be_matched(tmp_path) -> None:
    record = DownloadRecord(
        record_id="retry-1",
        title="Missing",
        source_type="hls",
        source_url="https://example.com/watch",
        source_host="example.com",
        output_path=str(tmp_path / "missing.mp4"),
        status="failed",
        media_key="a" * 64,
    )
    notices: list[tuple[str, str, str]] = []
    app = object.__new__(UniversalVideoDownloaderApp)
    app.pending_history_retry = record
    app.candidates = [
        VideoCandidate("First", "https://cdn.example.com/first.m3u8", record.source_url, source_type="hls"),
        VideoCandidate("Second", "https://cdn.example.com/second.m3u8", record.source_url, source_type="hls"),
    ]
    app.status_var = SimpleNamespace(set=lambda _value: None)
    app._log = lambda *_args: None
    app._show_notice = lambda *args: notices.append(args)
    app._start_download = lambda: (_ for _ in ()).throw(AssertionError("unmatched history must not start"))

    app._continue_history_after_analysis()

    assert app.pending_history_retry is None
    assert notices == [("warning", "未找到原任务媒体", "解析结果与历史记录不一致，请粘贴最新原链接并手动选择对应视频。")]


def test_history_output_settings_keep_baidupan_directory(tmp_path) -> None:
    record = DownloadRecord(
        record_id="retry-1",
        title="Share",
        source_type="baidupan",
        source_url="https://pan.baidu.com/s/share",
        source_host="pan.baidu.com",
        output_path=str(tmp_path),
        status="failed",
    )

    assert _history_output_settings(record) == (tmp_path, "由分享目录决定")


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
