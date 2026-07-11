from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import requests

import m3u8_core
from m3u8_core import (
    CoalescingEventBuffer,
    DirectDownloadJob,
    DownloadHistoryStore,
    DownloadRecord,
    VideoCandidate,
    _looks_like_direct_video_url,
    _looks_like_youtube_url,
    classify_error,
    discover_candidates,
    find_direct_video_urls,
    find_m3u8_urls,
    parse_playlist,
    rank_candidates,
    redact_sensitive_text,
)


def test_parse_master_playlist_variants() -> None:
    text = """#EXTM3U
#EXT-X-STREAM-INF:BANDWIDTH=800000,RESOLUTION=640x360
low/index.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=1800000,RESOLUTION=1280x720
high/index.m3u8
"""

    info = parse_playlist("https://example.com/video/master.m3u8", text)

    assert info.media is None
    assert len(info.variants) == 2
    assert info.variants[1].url == "https://example.com/video/high/index.m3u8"
    assert info.variants[1].resolution == "1280x720"


def test_parse_media_playlist_with_default_aes_iv() -> None:
    text = """#EXTM3U
#EXT-X-TARGETDURATION:10
#EXT-X-MEDIA-SEQUENCE:42
#EXT-X-KEY:METHOD=AES-128,URI="keys/video.key"
#EXTINF:9.8,
seg-42.ts
#EXTINF:9.9,
seg-43.ts
"""

    info = parse_playlist("https://example.com/hls/index.m3u8", text)

    assert info.media is not None
    assert info.media.encrypted is True
    assert len(info.media.segments) == 2
    assert info.media.segments[0].key is not None
    assert info.media.segments[0].key.uri == "https://example.com/hls/keys/video.key"
    assert info.media.segments[0].key.iv_hex == "0000000000000000000000000000002a"
    assert info.media.segments[1].key.iv_hex == "0000000000000000000000000000002b"


def test_parse_media_playlist_marks_byte_range_segments() -> None:
    text = """#EXTM3U
#EXT-X-TARGETDURATION:10
#EXTINF:10,
#EXT-X-BYTERANGE:1024@0
video.mp4
"""

    info = parse_playlist("https://example.com/video/index.m3u8", text)

    assert info.media is not None
    assert info.media.has_byterange is True


def test_find_m3u8_urls_from_page_and_scripts() -> None:
    html = """
    <video src="/media/main.m3u8?sig=abc"></video>
    <script>var u = "https:\\/\\/cdn.example.com\\/live\\/index.m3u8";</script>
    """

    urls = find_m3u8_urls("https://example.com/watch/1", html)

    assert "https://example.com/media/main.m3u8?sig=abc" in urls
    assert "https://cdn.example.com/live/index.m3u8" in urls


def test_find_m3u8_urls_from_packed_javascript() -> None:
    packed = (
        "eval(function(p,a,c,k,e,d){e=function(c){return c.toString(36)};"
        "while(c--){if(k[c]){p=p.replace(new RegExp('\\\\b'+e(c)+'\\\\b','g'),k[c])}}"
        "return p}('f=\\'8://7.6/5/e.0\\';d=\\'8://7.6/5/c/9.0\\';"
        "b=\\'8://7.6/5/a/9.0\\';',16,16,"
        "'m3u8|unused1|unused2|unused3|unused4|demo|test|example|https|video|"
        "1080p|source1280|720p|source842|playlist|source'.split('|'),0,{}))"
    )

    urls = find_m3u8_urls("https://example.com/page", packed)

    assert "https://example.test/demo/playlist.m3u8" in urls
    assert "https://example.test/demo/720p/video.m3u8" in urls
    assert "https://example.test/demo/1080p/video.m3u8" in urls


def test_find_direct_video_urls_from_page() -> None:
    html = """
    <video src="/media/demo.mp4?sig=abc"></video>
    <source src="https://cdn.example.com/video/trailer.webm" type="video/webm">
    """

    urls = find_direct_video_urls("https://example.com/watch/1", html)

    assert "https://example.com/media/demo.mp4?sig=abc" in urls
    assert "https://cdn.example.com/video/trailer.webm" in urls
    assert _looks_like_direct_video_url("https://cdn.example.com/video/trailer.webm")


def test_youtube_url_detection() -> None:
    assert _looks_like_youtube_url("https://www.youtube.com/watch?v=VIDEO_ID")
    assert _looks_like_youtube_url("https://youtu.be/VIDEO_ID")
    assert not _looks_like_youtube_url("https://example.com/watch?v=VIDEO_ID")


def test_youtube_discovery_uses_ytdlp_metadata(monkeypatch) -> None:
    class FakeYoutubeDL:
        def __init__(self, options: dict) -> None:
            self.options = options

        def __enter__(self) -> "FakeYoutubeDL":
            return self

        def __exit__(self, exc_type, exc, traceback) -> None:
            return None

        def extract_info(self, url: str, download: bool = False) -> dict:
            assert url == "https://www.youtube.com/watch?v=VIDEO_ID"
            assert download is False
            return {
                "id": "VIDEO_ID",
                "title": "Example Video",
                "width": 1920,
                "height": 1080,
                "duration": 95,
                "formats": [{"height": 1080, "width": 1920, "tbr": 4200}],
            }

    fake_module = type("FakeYtDlp", (), {"YoutubeDL": FakeYoutubeDL})
    monkeypatch.setattr(m3u8_core, "yt_dlp", fake_module)

    candidates = discover_candidates("https://www.youtube.com/watch?v=VIDEO_ID")

    assert len(candidates) == 1
    assert candidates[0].source_type == "youtube"
    assert candidates[0].resolution == "1920x1080"
    assert candidates[0].duration == 95
    assert "Example Video" in candidates[0].title


def test_direct_download_job_resumes_part_file(tmp_path) -> None:
    data = (b"0123456789abcdef" * 2048)
    requested_ranges: list[str] = []

    class RangeHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if self.path != "/video.mp4":
                self.send_error(404)
                return

            range_header = self.headers.get("Range", "")
            requested_ranges.append(range_header)
            assert self.headers.get("Accept-Encoding") == "identity"
            start = 0
            if range_header:
                start = int(range_header.removeprefix("bytes=").split("-", 1)[0])
                self.send_response(206)
                self.send_header("Content-Range", f"bytes {start}-{len(data) - 1}/{len(data)}")
            else:
                self.send_response(200)
            payload = data[start:]
            self.send_header("Content-Type", "video/mp4")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, format: str, *args: object) -> None:
            return None

    server = ThreadingHTTPServer(("127.0.0.1", 0), RangeHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        output_path = tmp_path / "video.mp4"
        output_path.with_suffix(output_path.suffix + ".part").write_bytes(data[:4096])
        job = DirectDownloadJob(
            url=f"http://127.0.0.1:{server.server_port}/video.mp4",
            output_path=output_path,
            callback=lambda _event, _payload: None,
        )

        job.run()

        assert output_path.read_bytes() == data
        assert "bytes=4096-" in requested_ranges
    finally:
        server.shutdown()
        server.server_close()


def test_direct_download_restarts_when_content_range_does_not_match(tmp_path) -> None:
    data = b"network-safe-resume" * 2048
    requested_ranges: list[str] = []

    class MismatchedRangeHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            range_header = self.headers.get("Range", "")
            requested_ranges.append(range_header)
            assert self.headers.get("Accept-Encoding") == "identity"
            if range_header:
                self.send_response(206)
                self.send_header("Content-Range", f"bytes 0-{len(data) - 1}/{len(data)}")
            else:
                self.send_response(200)
            self.send_header("Content-Type", "video/mp4")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, format: str, *args: object) -> None:
            return None

    server = ThreadingHTTPServer(("127.0.0.1", 0), MismatchedRangeHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        output_path = tmp_path / "video.mp4"
        output_path.with_suffix(".mp4.part").write_bytes(data[:1024])
        job = DirectDownloadJob(
            url=f"http://127.0.0.1:{server.server_port}/video.mp4",
            output_path=output_path,
            callback=lambda _event, _payload: None,
        )

        job.run()

        assert output_path.read_bytes() == data
        assert requested_ranges == ["bytes=1024-", ""]
    finally:
        server.shutdown()
        server.server_close()


def test_history_store_round_trip_redacts_signed_source(tmp_path) -> None:
    store = DownloadHistoryStore(tmp_path / "history.json")
    record = DownloadRecord(
        record_id="task-1",
        title="Example Video",
        source_type="hls",
        source_url="https://user:secret@example.com/video/master.m3u8?token=private#fragment",
        source_host="example.com",
        output_path=str(tmp_path / "video.ts"),
        status="downloading",
        progress=37.5,
        bytes_done=4096,
        updated_at=123.0,
        error_message="request https://example.com/a?sig=secret failed",
    )

    store.save([record])
    restored = store.load()

    assert len(restored) == 1
    assert restored[0].source_url == "https://example.com/video/master.m3u8"
    assert restored[0].progress == 37.5
    assert "secret" not in (tmp_path / "history.json").read_text(encoding="utf-8")


def test_history_store_skips_malformed_records(tmp_path) -> None:
    path = tmp_path / "history.json"
    path.write_text(
        '{"version":1,"records":[{"record_id":"bad","progress":"not-a-number"}]}',
        encoding="utf-8",
    )

    assert DownloadHistoryStore(path).load() == []


def test_coalescing_event_buffer_keeps_latest_progress_and_order() -> None:
    buffer = CoalescingEventBuffer()
    buffer.put("started", {"total": 10})
    buffer.put("progress", {"done": 1})
    buffer.put("progress", {"done": 2})
    buffer.put("segment", {"index": 0, "status": "downloading"})
    buffer.put("segment", {"index": 0, "status": "done"})
    buffer.put("completed", {"output": "video.mp4"})

    events = buffer.drain()

    assert [event for event, _payload in events] == ["started", "progress", "segment", "completed"]
    assert events[1][1]["done"] == 2
    assert events[2][1]["status"] == "done"
    assert buffer.drain() == []


def test_rank_candidates_deduplicates_signed_urls_and_keeps_better_variant() -> None:
    lower = VideoCandidate(
        title="Video",
        url="https://cdn.example.com/video/index.m3u8?token=one",
        source_url="https://example.com/watch",
        resolution="1280x720",
        bandwidth=1_000_000,
    )
    better = VideoCandidate(
        title="Video",
        url="https://cdn.example.com/video/index.m3u8?token=two",
        source_url="https://example.com/watch",
        resolution="1280x720",
        bandwidth=2_000_000,
    )

    ranked = rank_candidates([lower, better])

    assert ranked == [better]


def test_error_classification_is_actionable_and_redacted() -> None:
    response = requests.Response()
    response.status_code = 403
    response.url = "https://cdn.example.com/video.m3u8?token=private"
    error = requests.HTTPError("403 for https://cdn.example.com/video.m3u8?token=private", response=response)

    result = classify_error(error)

    assert result.code == "access_denied"
    assert result.retryable is False
    assert "private" not in result.detail
    assert "token=private" not in redact_sensitive_text(str(error))


def test_generic_webpage_falls_back_to_ytdlp(monkeypatch) -> None:
    class FakeYoutubeDL:
        def __init__(self, options: dict) -> None:
            self.options = options

        def __enter__(self) -> "FakeYoutubeDL":
            return self

        def __exit__(self, exc_type, exc, traceback) -> None:
            return None

        def extract_info(self, url: str, download: bool = False) -> dict:
            assert url == "https://example.com/watch/123"
            return {
                "id": "123",
                "title": "Generic Page Video",
                "width": 1280,
                "height": 720,
                "duration": 60,
                "ext": "mp4",
                "extractor_key": "Generic",
            }

    monkeypatch.setattr(m3u8_core, "yt_dlp", type("FakeYtDlp", (), {"YoutubeDL": FakeYoutubeDL}))
    monkeypatch.setattr(
        m3u8_core,
        "fetch_text_with_fallbacks",
        lambda *_args, **_kwargs: ("<html><body>No static media</body></html>", {"Referer": "https://example.com/"}),
    )

    candidates = discover_candidates("https://example.com/watch/123")

    assert len(candidates) == 1
    assert candidates[0].source_type == "ytdlp"
    assert candidates[0].extractor == "Generic"
    assert candidates[0].container == "mp4"


def test_supported_site_uses_ytdlp_before_static_scan(monkeypatch) -> None:
    candidate = VideoCandidate(
        title="Supported Site Video",
        url="https://supported.example/watch/123",
        source_url="https://supported.example/watch/123",
        source_type="ytdlp",
        container="mp4",
        extractor="SupportedSite",
    )
    monkeypatch.setattr(m3u8_core, "_has_specific_ytdlp_extractor", lambda _url: True)
    monkeypatch.setattr(m3u8_core, "_discover_ytdlp_candidates", lambda *_args, **_kwargs: [candidate])

    def unexpected_static_fetch(*_args, **_kwargs):
        raise AssertionError("static discovery should not run before a dedicated yt-dlp extractor")

    monkeypatch.setattr(m3u8_core, "fetch_text_with_fallbacks", unexpected_static_fetch)

    assert discover_candidates("https://supported.example/watch/123") == [candidate]


def test_supported_site_falls_back_to_static_scan_when_ytdlp_fails(monkeypatch) -> None:
    monkeypatch.setattr(m3u8_core, "_has_specific_ytdlp_extractor", lambda _url: True)

    def failed_ytdlp(*_args, **_kwargs):
        raise m3u8_core.HlsError("synthetic extractor failure")

    monkeypatch.setattr(m3u8_core, "_discover_ytdlp_candidates", failed_ytdlp)
    monkeypatch.setattr(
        m3u8_core,
        "fetch_text_with_fallbacks",
        lambda *_args, **_kwargs: (
            '<video src="https://cdn.example.com/fallback.mp4"></video>',
            {"Referer": "https://supported.example/"},
        ),
    )
    monkeypatch.setattr(m3u8_core, "_discover_urls_from_scripts", lambda *_args, **_kwargs: [])

    candidates = discover_candidates("https://supported.example/watch/123")

    assert len(candidates) == 1
    assert candidates[0].source_type == "direct"
    assert candidates[0].url == "https://cdn.example.com/fallback.mp4"


def test_ytdlp_network_options_are_bounded() -> None:
    options = m3u8_core._ytdlp_base_options("https://example.com/watch/123")

    assert 0 < options["socket_timeout"] <= 20
    assert 0 <= options["retries"] <= 3
    assert 0 <= options["extractor_retries"] <= 3
    assert 0 <= options["fragment_retries"] <= 5


def test_xiaohongshu_has_a_dedicated_ytdlp_extractor() -> None:
    url = "https://www.xiaohongshu.com/explore/deadbeefdeadbeefdeadbeef"

    assert m3u8_core._has_specific_ytdlp_extractor(url) is True
