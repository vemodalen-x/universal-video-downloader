from __future__ import annotations

import hashlib
import io
import json
import socket
import threading
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
import requests

import m3u8_core
from m3u8_core import (
    BaiduPanDownloadJob,
    CoalescingEventBuffer,
    DirectDownloadJob,
    DownloadJob,
    DownloadHistoryStore,
    DownloadRecord,
    HlsError,
    PlaylistParseError,
    YouTubeDownloadJob,
    VideoCandidate,
    _baidupan_share_reference,
    _looks_like_baidupan_share_url,
    _looks_like_direct_video_url,
    _looks_like_pikpak_share_url,
    _looks_like_youtube_url,
    _pikpak_share_reference,
    classify_error,
    discover_candidates,
    find_direct_video_urls,
    find_m3u8_urls,
    parse_playlist,
    rank_candidates,
    redact_sensitive_text,
)


PIKPAK_TEST_SHARE_URL = "https://mypikpak.com/s/test-share-id-12345/test-item-id-67890"
BAIDUPAN_TEST_SHARE_URL = "https://pan.baidu.com/s/1SyntheticShareToken"


def _cached_hls_job(tmp_path, keep_cache=True):
    playlist = parse_playlist(
        "https://example.com/playlist.m3u8",
        "#EXTM3U\n#EXTINF:1,\none.ts\n#EXTINF:1,\ntwo.ts\n#EXT-X-ENDLIST\n",
    ).media
    job = DownloadJob(playlist, tmp_path / "video.ts", keep_cache=keep_cache)
    job._prepare()
    return job


@pytest.mark.parametrize("require_all", [True, False])
@pytest.mark.parametrize("zero_byte_files", [True, False])
def test_hls_combine_rejects_empty_cache_without_replacing_output(tmp_path, require_all, zero_byte_files):
    job = _cached_hls_job(tmp_path)
    if zero_byte_files:
        for segment in job.playlist.segments:
            job._segment_path(segment).touch()
    target = tmp_path / ("video.ts" if require_all else "video.partial.ts")
    target.write_bytes(b"previous output")
    with pytest.raises(HlsError):
        job.combine(require_all=require_all)
    assert target.read_bytes() == b"previous output"
    assert not target.with_suffix(".ts.part").exists()


def test_partial_hls_export_uses_available_nonempty_segments(tmp_path):
    job = _cached_hls_job(tmp_path)
    job._segment_path(job.playlist.segments[0]).write_bytes(b"first")
    job._segment_path(job.playlist.segments[1]).touch()
    assert job.combine(require_all=False).read_bytes() == b"first"
    with pytest.raises(HlsError):
        job.combine(require_all=True)


def test_hls_export_validates_actual_output_and_cleans_failed_temporary_file(monkeypatch, tmp_path):
    job = _cached_hls_job(tmp_path)
    for segment in job.playlist.segments:
        job._segment_path(segment).write_bytes(b"cached data")
    target = tmp_path / "video.partial.ts"
    target.write_bytes(b"previous preview")
    monkeypatch.setattr(m3u8_core.shutil, "copyfileobj", lambda *args, **kwargs: None)
    with pytest.raises(HlsError, match="为空"):
        job.combine(require_all=False)
    assert target.read_bytes() == b"previous preview"
    assert not list(tmp_path.glob(".*.part"))


def test_partial_export_serializes_with_final_combine_and_cache_cleanup(monkeypatch, tmp_path):
    job = _cached_hls_job(tmp_path, keep_cache=False)
    for segment in job.playlist.segments:
        job._segment_path(segment).write_bytes(bytes([segment.index + 1]))
    entered = threading.Event()
    release = threading.Event()
    prepared = threading.Event()
    combine = job._combine_locked
    prepare = job._prepare
    events = []
    errors = []
    job.callback = lambda event, payload: events.append(event)

    def blocking_combine(require_all, partial_suffix):
        if not require_all:
            entered.set()
            assert release.wait(3)
        return combine(require_all, partial_suffix)

    def record_prepare():
        prepare()
        prepared.set()

    def export():
        try:
            job.combine(require_all=False)
        except Exception as exc:
            errors.append(exc)

    monkeypatch.setattr(job, "_combine_locked", blocking_combine)
    monkeypatch.setattr(job, "_prepare", record_prepare)
    partial = threading.Thread(target=export)
    final = threading.Thread(target=job.run)
    partial.start()
    try:
        assert entered.wait(2)
        final.start()
        assert prepared.wait(2)
        assert job.cache_dir.is_dir()
        assert "completed" not in events
    finally:
        release.set()
        partial.join(3)
        if final.ident is not None:
            final.join(3)
    assert not partial.is_alive() and not final.is_alive()
    assert not errors and "fatal" not in events
    assert "completed" in events
    assert (tmp_path / "video.partial.ts").read_bytes() == b"\x01\x02"
    assert job.output_path.read_bytes() == b"\x01\x02"
    assert not job.cache_dir.exists()


@pytest.mark.parametrize("header", [
    "Authorization: Bearer demo-secret",
    "Proxy-Authorization: Basic demo-secret",
    "Cookie: first=demo-secret; second=demo-cookie",
    "Set-Cookie: first=demo-secret; second=demo-cookie",
])
def test_redaction_removes_complete_sensitive_headers(header):
    redacted = redact_sensitive_text(header + "\nHTTP 503, retry later")
    assert "demo-secret" not in redacted
    assert "demo-cookie" not in redacted
    assert "HTTP 503, retry later" in redacted


class FakePikPakResponse:
    def __init__(self, payload: object, status_code: int = 200, reason: str = "") -> None:
        self.payload = payload
        self.status_code = status_code
        self.reason = reason

    def json(self) -> object:
        return self.payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")


class FakePikPakSession:
    def __init__(self, *responses: FakePikPakResponse) -> None:
        self.responses = list(responses)
        self.requests: list[tuple[str, str, dict]] = []

    def request(self, method: str, url: str, **kwargs) -> FakePikPakResponse:
        self.requests.append((method, url, kwargs))
        if not self.responses:
            raise AssertionError(f"unexpected PikPak request: {method} {url}")
        return self.responses.pop(0)


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


def test_parse_media_playlist_resolves_byte_range_segments_and_map() -> None:
    text = """#EXTM3U
#EXT-X-TARGETDURATION:10
#EXT-X-MAP:URI="video.mp4",BYTERANGE="4@0"
#EXTINF:10,
#EXT-X-BYTERANGE:1024@4
video.mp4
#EXTINF:10,
#EXT-X-BYTERANGE:512
video.mp4
"""

    info = parse_playlist("https://example.com/video/index.m3u8", text)

    assert info.media is not None
    assert info.media.has_byterange is True
    assert [(segment.byte_range.offset, segment.byte_range.length) for segment in info.media.segments] == [
        (0, 4),
        (4, 1024),
        (1028, 512),
    ]


def test_parse_media_playlist_rejects_implicit_range_without_matching_previous_segment() -> None:
    text = """#EXTM3U
#EXTINF:10,
#EXT-X-BYTERANGE:1024
video.mp4
"""

    with pytest.raises(PlaylistParseError, match="隐式字节范围"):
        parse_playlist("https://example.com/video/index.m3u8", text)


def test_load_best_media_playlist_accepts_byte_range_variant(monkeypatch) -> None:
    master = parse_playlist(
        "https://example.com/master.m3u8",
        """#EXTM3U
#EXT-X-STREAM-INF:BANDWIDTH=1000
media.m3u8
""",
    )
    nested = parse_playlist(
        "https://example.com/media.m3u8",
        """#EXTM3U
#EXTINF:4,
#EXT-X-BYTERANGE:4@0
media.bin
""",
    )
    requested: list[str] = []

    def fake_load(url: str, headers=None):
        requested.append(url)
        return master if url.endswith("master.m3u8") else nested

    monkeypatch.setattr(m3u8_core, "load_playlist_info", fake_load)

    playlist = m3u8_core.load_best_media_playlist("https://example.com/master.m3u8")

    assert playlist is nested.media
    assert requested == ["https://example.com/master.m3u8", "https://example.com/media.m3u8"]


def test_hls_byterange_download_combines_init_and_media_ranges(tmp_path) -> None:
    data = b"INITSEG1SEG2"
    requested_ranges: list[str] = []
    playlist_text = """#EXTM3U
#EXT-X-TARGETDURATION:4
#EXT-X-MAP:URI="media.bin",BYTERANGE="4@0"
#EXTINF:4,
#EXT-X-BYTERANGE:4@4
media.bin
#EXTINF:4,
#EXT-X-BYTERANGE:4
media.bin
"""

    class RangeHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if self.path == "/index.m3u8":
                payload = playlist_text.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/vnd.apple.mpegurl")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
                return
            if self.path != "/media.bin":
                self.send_error(404)
                return
            range_header = self.headers.get("Range", "")
            requested_ranges.append(range_header)
            start_text, end_text = range_header.removeprefix("bytes=").split("-", 1)
            start, end = int(start_text), int(end_text)
            payload = data[start : end + 1]
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {start}-{end}/{len(data)}")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, format: str, *args: object) -> None:
            return None

    server = ThreadingHTTPServer(("127.0.0.1", 0), RangeHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        source_url = f"http://127.0.0.1:{server.server_port}/index.m3u8"
        candidates = discover_candidates(source_url)
        playlist = m3u8_core.load_best_media_playlist(source_url)
        output_path = tmp_path / "byterange.mp4"
        events: list[str] = []
        DownloadJob(playlist, output_path, concurrency=1, retries=1, callback=lambda event, _payload: events.append(event)).run()

        assert len(candidates) == 1
        assert candidates[0].url == source_url
        assert output_path.read_bytes() == data
        assert requested_ranges == ["bytes=0-3", "bytes=4-7", "bytes=8-11"]
        assert "completed" in events
    finally:
        server.shutdown()
        server.server_close()


def test_hls_byterange_download_resumes_within_range(tmp_path) -> None:
    data = b"HEADDATA"
    requested_ranges: list[str] = []
    playlist_text = """#EXTM3U
#EXTINF:4,
#EXT-X-BYTERANGE:4@4
media.bin
"""

    class RangeHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            range_header = self.headers.get("Range", "")
            requested_ranges.append(range_header)
            start_text, end_text = range_header.removeprefix("bytes=").split("-", 1)
            start, end = int(start_text), int(end_text)
            payload = data[start : end + 1]
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {start}-{end}/{len(data)}")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, format: str, *args: object) -> None:
            return None

    server = ThreadingHTTPServer(("127.0.0.1", 0), RangeHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        playlist = parse_playlist(
            f"http://127.0.0.1:{server.server_port}/index.m3u8",
            playlist_text,
        ).media
        assert playlist is not None
        output_path = tmp_path / "resume.mp4"
        job = DownloadJob(playlist, output_path, concurrency=1, retries=1)
        segment_path = job._segment_path(playlist.segments[0])
        segment_path.parent.mkdir(parents=True)
        segment_path.with_suffix(segment_path.suffix + ".part").write_bytes(b"DA")

        job.run()

        assert output_path.read_bytes() == b"DATA"
        assert requested_ranges == ["bytes=6-7"]
    finally:
        server.shutdown()
        server.server_close()


def test_hls_combine_rejects_empty_segments_and_duplicate_partial_exports(tmp_path, monkeypatch) -> None:
    playlist = parse_playlist(
        "https://example.com/video/index.m3u8",
        "#EXTM3U\n#EXTINF:4,\nfirst.ts\n#EXTINF:4,\nsecond.ts\n",
    ).media
    assert playlist is not None
    output = tmp_path / "video.mp4"
    job = DownloadJob(playlist, output, concurrency=1)
    paths = [job._segment_path(segment) for segment in playlist.segments]
    paths[0].parent.mkdir(parents=True)
    paths[0].write_bytes(b"first")
    paths[1].write_bytes(b"")

    with pytest.raises(HlsError):
        job.combine(require_all=True)
    assert not output.exists()

    paths[1].write_bytes(b"second")
    entered = threading.Event()
    release = threading.Event()
    original_copyfileobj = m3u8_core.shutil.copyfileobj

    def delayed_copyfileobj(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return original_copyfileobj(*args, **kwargs)

    monkeypatch.setattr(m3u8_core.shutil, "copyfileobj", delayed_copyfileobj)
    result: list[Path] = []
    errors: list[Exception] = []

    def export_partial() -> None:
        try:
            result.append(job.combine(require_all=False))
        except Exception as exc:
            errors.append(exc)

    worker = threading.Thread(target=export_partial)
    worker.start()
    assert entered.wait(5)
    with pytest.raises(HlsError, match="已有导出任务"):
        job.combine(require_all=False)
    release.set()
    worker.join(timeout=5)

    assert not worker.is_alive()
    assert errors == []
    assert result[0].read_bytes() == b"firstsecond"
    assert not list(tmp_path.glob(".*.part"))


def test_hls_retry_adopts_legacy_cache_for_same_output(tmp_path) -> None:
    output = tmp_path / "episode.mp4"
    old_playlist = parse_playlist(
        "https://cdn.example.com/episode.m3u8?signature=old",
        "#EXTM3U\n#EXTINF:4,\nsegment.ts\n",
    ).media
    new_playlist = parse_playlist(
        "https://cdn.example.com/episode.m3u8?signature=new",
        "#EXTM3U\n#EXTINF:4,\nsegment.ts\n",
    ).media
    assert old_playlist is not None
    assert new_playlist is not None

    legacy_source = f"{old_playlist.url}|{output.resolve()}"
    legacy_dir = tmp_path / ".m3u8_resume" / hashlib.sha1(legacy_source.encode("utf-8")).hexdigest()[:16]
    legacy_segment = legacy_dir / "segments" / old_playlist.segments[0].file_name
    legacy_segment.parent.mkdir(parents=True)
    legacy_segment.write_bytes(b"already-downloaded")
    (legacy_dir / "manifest.json").write_text(
        json.dumps({"output_path": str(output), "playlist_url": old_playlist.url}),
        encoding="utf-8",
    )
    unrelated_dir = tmp_path / ".m3u8_resume" / "unrelated-cache"
    unrelated_dir.mkdir()
    (unrelated_dir / "manifest.json").write_text(
        json.dumps({"output_path": str(output), "playlist_url": "https://cdn.example.com/different.m3u8"}),
        encoding="utf-8",
    )

    job = DownloadJob(new_playlist, output, resume_key="stable-media-key")

    assert job.cache_dir != legacy_dir
    assert not legacy_dir.exists()
    assert unrelated_dir.exists()
    assert job._segment_path(new_playlist.segments[0]).read_bytes() == b"already-downloaded"
    assert job.cache_dir == DownloadJob(new_playlist, output, resume_key="stable-media-key").cache_dir


def test_hls_byterange_download_rejects_mismatched_content_range(tmp_path) -> None:
    data = b"HEADDATA"
    playlist_text = """#EXTM3U
#EXTINF:4,
#EXT-X-BYTERANGE:4@4
media.bin
"""

    class MismatchedRangeHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self.send_response(206)
            self.send_header("Content-Range", f"bytes 0-3/{len(data)}")
            self.send_header("Content-Length", "4")
            self.end_headers()
            self.wfile.write(data[:4])

        def log_message(self, format: str, *args: object) -> None:
            return None

    server = ThreadingHTTPServer(("127.0.0.1", 0), MismatchedRangeHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        playlist = parse_playlist(
            f"http://127.0.0.1:{server.server_port}/index.m3u8",
            playlist_text,
        ).media
        assert playlist is not None
        events: list[str] = []
        output_path = tmp_path / "rejected.mp4"

        DownloadJob(playlist, output_path, concurrency=1, retries=1, callback=lambda event, _payload: events.append(event)).run()

        assert not output_path.exists()
        assert "fatal" not in events
        assert "failed" in events
    finally:
        server.shutdown()
        server.server_close()


def test_hls_byterange_download_rejects_server_ignoring_range(tmp_path) -> None:
    data = b"HEADDATA"
    requested_ranges: list[str] = []
    playlist_text = """#EXTM3U
#EXTINF:4,
#EXT-X-BYTERANGE:4@4
media.bin
"""

    class FullResponseHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            requested_ranges.append(self.headers.get("Range", ""))
            self.send_response(200)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, format: str, *args: object) -> None:
            return None

    server = ThreadingHTTPServer(("127.0.0.1", 0), FullResponseHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        playlist = parse_playlist(
            f"http://127.0.0.1:{server.server_port}/index.m3u8",
            playlist_text,
        ).media
        assert playlist is not None
        events: list[str] = []
        output_path = tmp_path / "ignored-range.mp4"

        DownloadJob(playlist, output_path, concurrency=1, retries=1, callback=lambda event, _payload: events.append(event)).run()

        assert requested_ranges == ["bytes=4-7"]
        assert not output_path.exists()
        assert "fatal" not in events
        assert "failed" in events
    finally:
        server.shutdown()
        server.server_close()


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
    assert candidates[0].media_id == "VIDEO_ID"
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
        assert any(value.startswith("bytes=4096-") and value != "bytes=4096-" for value in requested_ranges)
        assert not output_path.with_suffix(".mp4.part").exists()
        assert not job._resume_path().exists()
    finally:
        server.shutdown()
        server.server_close()


def test_direct_download_uses_bounded_ranges_until_complete(monkeypatch, tmp_path) -> None:
    data = b"bounded-range-video" * 2048
    requested_ranges: list[str] = []
    monkeypatch.setattr(m3u8_core, "DIRECT_RANGE_CHUNK_BYTES", 4096)

    class BoundedOnlyHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            range_header = self.headers.get("Range", "")
            requested_ranges.append(range_header)
            if not range_header or range_header.endswith("-"):
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{len(data)}")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            start_text, end_text = range_header.removeprefix("bytes=").split("-", 1)
            start = int(start_text)
            end = min(int(end_text), len(data) - 1)
            payload = data[start : end + 1]
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {start}-{end}/{len(data)}")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, format: str, *args: object) -> None:
            return None

    server = ThreadingHTTPServer(("127.0.0.1", 0), BoundedOnlyHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        output_path = tmp_path / "video.mp4"
        output_path.with_suffix(".mp4.part").write_bytes(data[:1500])
        job = DirectDownloadJob(
            url=f"http://127.0.0.1:{server.server_port}/video.mp4",
            output_path=output_path,
            retries=0,
        )

        job.run()

        assert output_path.read_bytes() == data
        assert len(requested_ranges) > 2
        assert requested_ranges[0] == "bytes=1500-5595"
        assert all(value and not value.endswith("-") for value in requested_ranges)
    finally:
        server.shutdown()
        server.server_close()


def test_direct_download_continues_when_range_total_is_unknown(monkeypatch, tmp_path) -> None:
    data = b"unknown-total-video" * 1800
    requested_ranges: list[str] = []
    monkeypatch.setattr(m3u8_core, "DIRECT_RANGE_CHUNK_BYTES", 4096)

    class UnknownTotalHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            range_header = self.headers.get("Range", "")
            requested_ranges.append(range_header)
            start_text, end_text = range_header.removeprefix("bytes=").split("-", 1)
            start = int(start_text)
            if start >= len(data):
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{len(data)}")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            end = min(int(end_text), len(data) - 1)
            payload = data[start : end + 1]
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {start}-{end}/*")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, format: str, *args: object) -> None:
            return None

    server = ThreadingHTTPServer(("127.0.0.1", 0), UnknownTotalHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        output_path = tmp_path / "video.mp4"
        DirectDownloadJob(
            url=f"http://127.0.0.1:{server.server_port}/video.mp4",
            output_path=output_path,
            retries=0,
        ).run()

        assert output_path.read_bytes() == data
        assert len(requested_ranges) > 2
        assert requested_ranges[-1].startswith(f"bytes={len(data)}-")
    finally:
        server.shutdown()
        server.server_close()


def test_direct_download_accepts_complete_200_when_range_is_ignored(tmp_path) -> None:
    data = b"range-optional-video" * 4096
    requested_ranges: list[str] = []

    class RangeIgnoringHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            requested_ranges.append(self.headers.get("Range", ""))
            self.send_response(200)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, format: str, *args: object) -> None:
            return None

    server = ThreadingHTTPServer(("127.0.0.1", 0), RangeIgnoringHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        output_path = tmp_path / "video.mp4"
        DirectDownloadJob(
            url=f"http://127.0.0.1:{server.server_port}/video.mp4",
            output_path=output_path,
            retries=0,
        ).run()

        assert output_path.read_bytes() == data
        assert requested_ranges == [f"bytes=0-{m3u8_core.DIRECT_RANGE_CHUNK_BYTES - 1}"]
    finally:
        server.shutdown()
        server.server_close()


def test_direct_download_retries_dropped_stream_from_latest_offset(tmp_path) -> None:
    data = b"retryable-video-data" * 160000
    requested_ranges: list[str] = []

    class DroppedStreamHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            range_header = self.headers.get("Range", "")
            requested_ranges.append(range_header)
            start = int(range_header.removeprefix("bytes=").split("-", 1)[0]) if range_header else 0
            payload = data[start:]
            self.send_response(206 if range_header else 200)
            if range_header:
                self.send_header("Content-Range", f"bytes {start}-{len(data) - 1}/{len(data)}")
            self.send_header("Content-Type", "video/mp4")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            if len(requested_ranges) == 1:
                self.wfile.write(payload[:700000])
                self.wfile.flush()
                try:
                    self.connection.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                self.connection.close()
                return
            self.wfile.write(payload)

        def log_message(self, format: str, *args: object) -> None:
            return None

    server = ThreadingHTTPServer(("127.0.0.1", 0), DroppedStreamHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        output_path = tmp_path / "video.mp4"
        part_path = output_path.with_suffix(".mp4.part")
        part_path.write_bytes(data[:4096])
        job = DirectDownloadJob(
            url=f"http://127.0.0.1:{server.server_port}/video.mp4",
            output_path=output_path,
            callback=lambda _event, _payload: None,
            retries=2,
            retry_backoff_seconds=0,
        )

        job.run()

        assert output_path.read_bytes() == data
        assert not part_path.exists()
        assert requested_ranges[0].startswith("bytes=4096-")
        assert requested_ranges[0] != "bytes=4096-"
        assert int(requested_ranges[1].removeprefix("bytes=").split("-", 1)[0]) > 4096
    finally:
        server.shutdown()
        server.server_close()


def test_direct_download_stop_keeps_only_internal_resume_cache(tmp_path) -> None:
    data = b"stop-safe-video" * 180000

    class DirectHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self.send_response(200)
            self.send_header("Content-Type", "video/mp4")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, format: str, *args: object) -> None:
            return None

    server = ThreadingHTTPServer(("127.0.0.1", 0), DirectHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        output_path = tmp_path / "video.mp4"
        events: list[str] = []
        job: DirectDownloadJob

        def callback(event: str, payload: dict) -> None:
            events.append(event)
            if event == "progress" and int(payload.get("bytes_done", 0)) > 0:
                job.stop()

        job = DirectDownloadJob(
            url=f"http://127.0.0.1:{server.server_port}/video.mp4",
            output_path=output_path,
            callback=callback,
        )

        job.run()

        assert not output_path.exists()
        assert not output_path.with_suffix(".mp4.part").exists()
        assert 0 < job._resume_path().stat().st_size < len(data)
        assert events[-1] == "stopped"
    finally:
        server.shutdown()
        server.server_close()


def test_direct_download_refreshes_url_after_416_without_losing_resume_cache(tmp_path) -> None:
    data = b"fresh-signed-video" * 4096
    requests_seen: list[tuple[str, str]] = []
    refresh_count = 0

    class ExpiredUrlHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            range_header = self.headers.get("Range", "")
            requests_seen.append((self.path, range_header))
            if self.path == "/expired.mp4":
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{len(data)}")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            start = int(range_header.removeprefix("bytes=").split("-", 1)[0]) if range_header else 0
            payload = data[start:]
            self.send_response(206 if range_header else 200)
            if range_header:
                self.send_header("Content-Range", f"bytes {start}-{len(data) - 1}/{len(data)}")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, format: str, *args: object) -> None:
            return None

    server = ThreadingHTTPServer(("127.0.0.1", 0), ExpiredUrlHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        output_path = tmp_path / "video.mp4"
        part_path = output_path.with_suffix(".mp4.part")
        part_path.write_bytes(data[:8192])

        def refresh_url() -> str:
            nonlocal refresh_count
            refresh_count += 1
            return f"http://127.0.0.1:{server.server_port}/fresh.mp4"

        job = DirectDownloadJob(
            url=f"http://127.0.0.1:{server.server_port}/expired.mp4",
            output_path=output_path,
            callback=lambda _event, _payload: None,
            url_refresher=refresh_url,
            retries=1,
            retry_backoff_seconds=0,
        )

        job.run()

        assert output_path.read_bytes() == data
        assert refresh_count == 1
        requested_end = 8192 + m3u8_core.DIRECT_RANGE_CHUNK_BYTES - 1
        assert requests_seen == [
            ("/expired.mp4", f"bytes=8192-{requested_end}"),
            ("/fresh.mp4", f"bytes=8192-{requested_end}"),
        ]
    finally:
        server.shutdown()
        server.server_close()


def test_direct_download_preserves_part_when_content_range_does_not_match(tmp_path) -> None:
    data = b"network-safe-resume" * 2048
    requested_ranges: list[str] = []
    events: list[tuple[str, dict]] = []

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
            callback=lambda event, payload: events.append((event, payload)),
            retries=1,
            retry_backoff_seconds=0,
        )

        job.run()

        assert not output_path.exists()
        assert not output_path.with_suffix(".mp4.part").exists()
        assert job._resume_path().read_bytes() == data[:1024]
        expected_range = f"bytes=1024-{1024 + m3u8_core.DIRECT_RANGE_CHUNK_BYTES - 1}"
        assert requested_ranges == [expected_range, expected_range]
        assert events[-1][0] == "fatal"
        assert classify_error(events[-1][1]["error"]).code == "direct_resume_exhausted"
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
        media_key="a" * 64,
        progress=37.5,
        bytes_done=4096,
        updated_at=123.0,
        error_message="request https://example.com/a?sig=secret failed",
    )

    store.save([record])
    restored = store.load()

    assert len(restored) == 1
    assert restored[0].source_url == "https://example.com/video/master.m3u8"
    assert restored[0].media_key == "a" * 64
    assert restored[0].progress == 37.5
    assert "secret" not in (tmp_path / "history.json").read_text(encoding="utf-8")


def test_history_store_skips_malformed_records(tmp_path) -> None:
    path = tmp_path / "history.json"
    path.write_text(
        '{"version":1,"records":[{"record_id":"bad","progress":"not-a-number"}]}',
        encoding="utf-8",
    )

    assert DownloadHistoryStore(path).load() == []


def test_history_store_default_limit_retains_large_batch(tmp_path) -> None:
    store = DownloadHistoryStore(tmp_path / "history.json")
    records = [
        DownloadRecord(
            record_id=f"task-{index}",
            title=f"Video {index}",
            source_type="pikpak",
            source_url="https://mypikpak.com/s/synthetic-share",
            source_host="mypikpak.com",
            output_path=str(tmp_path / f"{index:03d}.mp4"),
            status="completed",
            updated_at=float(index),
        )
        for index in range(150)
    ]

    store.save(records)

    assert len(store.load()) == 150


def test_history_store_loads_valid_backup_when_primary_is_corrupt(tmp_path) -> None:
    store = DownloadHistoryStore(tmp_path / "history.json")
    first = DownloadRecord(
        record_id="task-1",
        title="First Video",
        source_type="direct",
        source_url="https://example.com/video.mp4",
        source_host="example.com",
        output_path=str(tmp_path / "video.mp4"),
        status="completed",
        updated_at=1.0,
    )
    second = replace(first, record_id="task-2", title="Second Video", updated_at=2.0)
    store.save([first])
    store.save([second, first])
    store.path.write_text('{"version":1,"records":[', encoding="utf-8")

    restored = store.load()

    assert [record.record_id for record in restored] == ["task-1"]


def test_download_record_rejects_non_digest_media_key() -> None:
    record = DownloadRecord.from_dict(
        {
            "record_id": "task-1",
            "title": "Video",
            "source_url": "https://example.com/video",
            "output_path": "video.mp4",
            "status": "completed",
            "media_key": "https://example.com/video?token=private",
        }
    )

    assert record.media_key == ""


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


def test_coalescing_preserves_item_progress_before_next_item() -> None:
    buffer = CoalescingEventBuffer()
    buffer.put("queue_item_started", {"index": 1})
    buffer.put("progress", {"done": 100, "bytes_done": 1000})
    buffer.put("queue_item_completed", {"index": 1})
    buffer.put("queue_item_started", {"index": 2})
    buffer.put("progress", {"done": 5, "bytes_done": 20})
    events = buffer.drain()
    assert [event for event, _ in events] == [
        "queue_item_started", "progress", "queue_item_completed", "queue_item_started", "progress",
    ]
    assert events[1][1]["bytes_done"] == 1000
    assert events[-1][1]["bytes_done"] == 20


@pytest.mark.parametrize("invalid", [{"records": None}, {"records": 42}, {"records": {}}, {}])
def test_history_recovers_structurally_invalid_primary_without_losing_backup(tmp_path, invalid) -> None:
    store = DownloadHistoryStore(tmp_path / "history.json")
    valid = {"version": 1, "records": [{"record_id": "saved", "title": "Saved"}]}
    store.backup_path.write_text(json.dumps(valid), encoding="utf-8")
    store.path.write_text(json.dumps(invalid), encoding="utf-8")
    records = store.load()
    assert [record.record_id for record in records] == ["saved"]
    store.save(records)
    assert json.loads(store.backup_path.read_text(encoding="utf-8")) == valid


@pytest.fixture
def scripted_media_server():
    replies = []
    connections = []

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_GET(self):
            connections.append(self.client_address)
            status, headers, body = replies.pop(0) if replies else (500, {}, b"")
            self.send_response(status)
            for name, value in headers.items():
                self.send_header(name, value)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/video.mp4", replies, connections
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@pytest.mark.parametrize("status,headers,body", [
    (200, {"Content-Type": "text/html; charset=utf-8"}, b"<html>expired</html>"),
    (200, {"Content-Type": "application/json"}, b'{"error":"expired"}'),
    (200, {}, b""),
    (204, {}, b""),
    (206, {"Content-Range": "bytes 0-3/2"}, b"abcd"),
    (206, {"Content-Range": "bytes 0-3/8", "Content-Encoding": "gzip"}, b"bad!"),
    (416, {"Content-Range": "bytes */0"}, b""),
    (200, {"Content-Type": "application/vnd.apple.mpegurl"}, b"#EXTM3U\n"),
    (200, {"Content-Type": "text/plain"}, b"#EXTM3U\n"),
    (200, {}, b"<html>expired</html>"),
])
def test_direct_download_rejects_nonmedia_and_invalid_responses(tmp_path, scripted_media_server, status, headers, body):
    url, replies, _ = scripted_media_server
    replies.append((status, headers, body))
    output = tmp_path / "video.mp4"
    events = []
    job = DirectDownloadJob(url, output, retries=0, callback=lambda e, p: events.append((e, p)))
    job.run()
    assert not output.exists()
    assert events[-1][0] == "fatal"
    assert not job._resume_path().exists() or job._resume_path().stat().st_size == 0


@pytest.mark.parametrize("second", [
    (206, {"Content-Range": "bytes 4-7/9"}, b"efgh"),
    (416, {"Content-Range": "bytes */4"}, b""),
])
def test_direct_download_rejects_total_size_changes(tmp_path, scripted_media_server, second):
    url, replies, _ = scripted_media_server
    replies.extend([(206, {"Content-Range": "bytes 0-3/8"}, b"abcd"), second])
    output = tmp_path / "video.mp4"
    job = DirectDownloadJob(url, output, retries=0)
    job.run()
    assert not output.exists()
    assert job._resume_path().read_bytes() == b"abcd"


def test_direct_download_reuses_connection_for_bounded_ranges(tmp_path, scripted_media_server):
    url, replies, connections = scripted_media_server
    replies.extend([
        (206, {"Content-Range": "bytes 0-3/8"}, b"abcd"),
        (206, {"Content-Range": "bytes 4-7/8"}, b"efgh"),
    ])
    output = tmp_path / "video.mp4"
    DirectDownloadJob(url, output, retries=0).run()
    assert output.read_bytes() == b"abcdefgh"
    assert len(connections) == 2
    assert len(set(connections)) == 1


def test_direct_download_does_not_append_overlong_range_body(tmp_path, scripted_media_server):
    url, replies, _ = scripted_media_server
    replies.append((206, {"Content-Range": "bytes 4-7/8"}, b"efghEXTRA"))
    output = tmp_path / "video.mp4"
    output.with_suffix(".mp4.part").write_bytes(b"abcd")
    job = DirectDownloadJob(url, output, retries=0)
    job.run()
    assert not output.exists()
    assert job._resume_path().read_bytes() == b"abcd"


@pytest.mark.parametrize("restart", [False, True])
def test_direct_resume_rejects_same_size_changed_etag(tmp_path, scripted_media_server, restart):
    url, replies, _ = scripted_media_server
    replies.extend([
        (206, {"Content-Range": "bytes 0-3/8", "ETag": '"old-version"'}, b"AAAA"),
        (206, {"Content-Range": "bytes 4-7/8", "ETag": '"new-version"'}, b"BBBB"),
    ])
    output = tmp_path / "video.mp4"
    job = DirectDownloadJob(url, output, retries=0)
    if restart:
        job.callback = lambda event, payload: job.stop() if event == "progress" and payload.get("bytes_done", 0) >= 4 else None
    job.run()
    if restart:
        job = DirectDownloadJob(url, output, retries=0)
        job.run()
    assert not output.exists()
    assert job._resume_path().read_bytes() == b"AAAA"
    metadata = json.loads(job._metadata_path().read_text(encoding="utf-8"))
    assert metadata["size"] == 8
    assert "old-version" not in repr(metadata)
    assert metadata["etag_hash"] == hashlib.sha256(b'"old-version"').hexdigest()


def test_direct_resume_accepts_unchanged_validator_after_restart(tmp_path, scripted_media_server):
    url, replies, _ = scripted_media_server
    replies.extend([
        (206, {"Content-Range": "bytes 0-3/8", "ETag": '"same"'}, b"AAAA"),
        (206, {"Content-Range": "bytes 4-7/8", "ETag": '"same"'}, b"BBBB"),
    ])
    output = tmp_path / "video.mp4"
    job = DirectDownloadJob(url, output, retries=0)
    job.callback = lambda e, p: job.stop() if e == "progress" and p.get("bytes_done", 0) >= 4 else None
    job.run()
    restarted = DirectDownloadJob(url, output, retries=0)
    restarted.run()
    assert output.read_bytes() == b"AAAABBBB"
    assert not restarted.cache_dir.exists()


@pytest.mark.parametrize("restart", [False, True])
@pytest.mark.parametrize("probe_etag,completed", [('"same"', True), ('"changed"', False)])
def test_complete_cache_probes_validator_when_416_omits_etag(tmp_path, scripted_media_server, restart, probe_etag, completed):
    url, replies, _ = scripted_media_server
    replies.extend([
        (206, {"Content-Range": "bytes 0-7/*", "ETag": '"same"'}, b"abcdefgh"),
        (416, {"Content-Range": "bytes */8"}, b""),
        (206, {"Content-Range": "bytes 0-0/8", "ETag": probe_etag}, b"a"),
    ])
    output = tmp_path / "video.mp4"
    job = DirectDownloadJob(url, output, retries=0)
    if restart:
        job.callback = lambda e, p: job.stop() if e == "progress" and p.get("bytes_done", 0) == 8 else None
    job.run()
    if restart:
        job = DirectDownloadJob(url, output, retries=0)
        job.run()
    assert output.exists() == completed
    if completed:
        assert output.read_bytes() == b"abcdefgh"
        assert not job.cache_dir.exists()
    else:
        assert job._resume_path().read_bytes() == b"abcdefgh"
    assert replies == []


def test_rank_candidates_keeps_distinct_resource_query_ids():
    one = VideoCandidate("One", "https://cdn.example.com/video.mp4?id=1&token=old", "https://example.com/watch", source_type="direct")
    two = replace(one, title="Two", url="https://cdn.example.com/video.mp4?id=2&token=new")
    assert len(rank_candidates([one, two])) == 2


def test_history_updates_from_multiple_store_instances_are_serialized(tmp_path, monkeypatch):
    first = DownloadHistoryStore(tmp_path / "history.json")
    second = DownloadHistoryStore(first.path)
    entered = threading.Event()
    release = threading.Event()
    failures = []
    first_save = first._save_unlocked

    def delayed_save(records):
        entered.set()
        assert release.wait(5)
        first_save(records)

    monkeypatch.setattr(first, "_save_unlocked", delayed_save)
    record = DownloadRecord("a", "A", "direct", "", "", "a.mp4", "completed")
    def update(store, row):
        try:
            store.upsert(row)
        except Exception as exc:
            failures.append(exc)

    a = threading.Thread(target=update, args=(first, record))
    b = threading.Thread(target=update, args=(second, replace(record, record_id="b")))
    a.start()
    assert entered.wait(5)
    b.start()
    release.set()
    a.join(timeout=10)
    b.join(timeout=10)
    assert not a.is_alive() and not b.is_alive()
    assert failures == []
    assert {row.record_id for row in first.load()} == {"a", "b"}


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


def test_pikpak_share_url_and_query_access_code_are_recognized() -> None:
    url = PIKPAK_TEST_SHARE_URL + "?s_code=2468"

    assert _looks_like_pikpak_share_url(url)
    assert _pikpak_share_reference(url) == ("test-share-id-12345", "2468")
    assert not _looks_like_pikpak_share_url("https://example.com/s/not-pikpak")


def test_baidupan_share_is_routed_without_generic_network_discovery(monkeypatch) -> None:
    source_url = BAIDUPAN_TEST_SHARE_URL + "?pwd=a1b2#list/path=%2FShared%2FLessons"

    def unexpected_discovery(*_args, **_kwargs):
        raise AssertionError("Baidu shares must not enter generic webpage discovery")

    monkeypatch.setattr(m3u8_core, "fetch_text_with_fallbacks", unexpected_discovery)
    monkeypatch.setattr(m3u8_core, "_discover_ytdlp_candidates", unexpected_discovery)

    candidates = discover_candidates(source_url)

    assert _looks_like_baidupan_share_url(source_url)
    assert _baidupan_share_reference(source_url) == (BAIDUPAN_TEST_SHARE_URL, "a1b2")
    assert len(candidates) == 1
    assert candidates[0].source_type == "baidupan"
    assert candidates[0].source_url == BAIDUPAN_TEST_SHARE_URL
    assert candidates[0].title == "Lessons / 百度网盘"
    assert "a1b2" not in repr(candidates[0])


def test_baidupan_download_job_uses_connector_without_shell_and_redacts_code(monkeypatch, tmp_path) -> None:
    connector = tmp_path / "BaiduPCS-Go.exe"
    connector.write_bytes(b"synthetic connector")
    output_dir = tmp_path / "downloads"
    commands: list[list[str]] = []
    events: list[tuple[str, dict]] = []

    class FakeProcess:
        def __init__(self, command: list[str], **kwargs) -> None:
            commands.append(command)
            assert "shell" not in kwargs
            self.returncode = 0
            if command[1:] == ["who"]:
                output = "当前帐号 uid: 123456, 用户名: synthetic-user\n"
            elif command[1:3] == ["config", "set"]:
                output = "保存目录设置成功\n"
            else:
                output = "分享链接转存到网盘成功, 保存了课程到当前目录\n下载结束, 数据总量: 10 MB\n"
            self.stdout = io.StringIO(output)

        def wait(self) -> int:
            return self.returncode

        def poll(self) -> int:
            return self.returncode

        def terminate(self) -> None:
            self.returncode = 1

    monkeypatch.setattr(m3u8_core.subprocess, "Popen", FakeProcess)
    job = BaiduPanDownloadJob(
        BAIDUPAN_TEST_SHARE_URL + "?pwd=a1b2",
        output_dir,
        callback=lambda event, payload: events.append((event, payload)),
        connector_path=connector,
    )

    job.run()

    assert commands[0][1:] == ["who"]
    assert commands[1][1:4] == ["config", "set", "-savedir"]
    assert commands[2][1:4] == ["transfer", "--download", "--collect"]
    assert commands[2][-2:] == [BAIDUPAN_TEST_SHARE_URL, "a1b2"]
    assert events[-1] == ("completed", {"output": str(output_dir)})
    assert "a1b2" not in repr(events)


def test_baidupan_download_job_rejects_new_incomplete_files(monkeypatch, tmp_path) -> None:
    connector = tmp_path / "BaiduPCS-Go.exe"
    connector.write_bytes(b"synthetic connector")
    output_dir = tmp_path / "downloads"
    events: list[tuple[str, dict]] = []

    class FakeProcess:
        def __init__(self, command: list[str], **kwargs) -> None:
            self.returncode = 0
            if command[1:] == ["who"]:
                output = "当前帐号 uid: 123456, 用户名: synthetic-user\n"
            elif command[1:3] == ["config", "set"]:
                output = "保存目录设置成功\n"
            else:
                (output_dir / "lesson.mp4.part").write_bytes(b"partial")
                output = "分享链接转存到网盘成功, 保存了课程到当前目录\n下载结束, 数据总量: 1 MB\n"
            self.stdout = io.StringIO(output)

        def wait(self) -> int:
            return self.returncode

        def poll(self) -> int:
            return self.returncode

        def terminate(self) -> None:
            self.returncode = 1

    monkeypatch.setattr(m3u8_core.subprocess, "Popen", FakeProcess)
    job = BaiduPanDownloadJob(
        BAIDUPAN_TEST_SHARE_URL,
        output_dir,
        access_code="a1b2",
        callback=lambda event, payload: events.append((event, payload)),
        connector_path=connector,
    )

    job.run()

    assert events[-1][0] == "fatal"
    assert "未完成文件" in str(events[-1][1]["message"])
    assert classify_error(events[-1][1]["message"]).code == "baidupan_download_interrupted"


@pytest.mark.parametrize(
    ("message", "expected_code", "retryable"),
    [
        ("百度网盘连接器未安装", "missing_baidupan_connector", False),
        ("百度网盘连接器尚未登录", "baidupan_login_required", False),
        ("百度网盘分享需要提取码", "baidupan_code_required", False),
        ("百度网盘提取码错误或分享已失效", "baidupan_code_invalid", False),
        ("百度网盘文件下载失败，连接器已保留断点", "baidupan_download_interrupted", True),
    ],
)
def test_baidupan_errors_have_actionable_classification(
    message: str,
    expected_code: str,
    retryable: bool,
) -> None:
    result = classify_error(HlsError(message))

    assert result.code == expected_code
    assert result.retryable is retryable


def test_pikpak_protected_share_requests_access_code(monkeypatch) -> None:
    session = FakePikPakSession(
        FakePikPakResponse({"captcha_token": "anonymous-token"}),
        FakePikPakResponse({"share_status": "PASS_CODE_EMPTY", "files": []}),
    )
    monkeypatch.setattr(m3u8_core, "_http_session", lambda: session)

    with pytest.raises(HlsError, match="需要提取码"):
        discover_candidates(PIKPAK_TEST_SHARE_URL)

    assert [request[0] for request in session.requests] == ["POST", "GET"]
    assert session.requests[1][1].endswith("/share/detail")


def test_pikpak_share_discovers_nested_video_and_download_metadata(monkeypatch) -> None:
    session = FakePikPakSession(
        FakePikPakResponse({"captcha_token": "anonymous-token"}),
        FakePikPakResponse({"share_status": "OK", "pass_code_token": "share-token"}),
        FakePikPakResponse(
            {
                "share_status": "OK",
                "files": [{"id": "folder-1", "name": "Season 1", "kind": "drive#folder"}],
            }
        ),
        FakePikPakResponse(
            {
                "share_status": "OK",
                "files": [
                    {
                        "id": "video-1",
                        "name": "Episode 01.mp4",
                        "kind": "drive#file",
                        "mime_type": "video/mp4",
                    }
                ],
            }
        ),
        FakePikPakResponse(
            {
                "share_status": "OK",
                "file_info": {
                    "id": "video-1",
                    "name": "Episode 01.mp4",
                    "size": "734003200",
                    "web_content_link": "https://cdn.example.com/Episode01.mp4?signature=private",
                    "medias": [
                        {
                            "media_id": "origin",
                            "is_origin": True,
                            "resolution_name": "1080p",
                            "link": {"url": "https://cdn.example.com/original.mp4?token=private"},
                            "video": {
                                "width": 1920,
                                "height": 1080,
                                "duration": 120000,
                                "bit_rate": 4500000,
                                "frame_rate": 30,
                                "video_codec": "h264",
                                "audio_codec": "aac",
                            },
                        }
                    ],
                },
            }
        ),
    )
    monkeypatch.setattr(m3u8_core, "_http_session", lambda: session)

    candidates = discover_candidates(
        PIKPAK_TEST_SHARE_URL,
        access_code="2468",
    )

    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.source_type == "pikpak"
    assert candidate.extractor == "PikPak Share"
    assert candidate.resolution == "1080p"
    assert candidate.duration == 120
    assert candidate.container == "mp4"
    assert candidate.media_id == "video-1"
    assert candidate.formats[0].filesize == 734003200
    assert session.requests[1][2]["params"]["pass_code"] == "2468"
    assert all("pass_code" not in request[2].get("params", {}) for request in session.requests[2:])


def test_pikpak_share_returns_every_video_beyond_fifty(monkeypatch) -> None:
    client = m3u8_core._PikPakShareClient(session=FakePikPakSession(), device_id="device-123")
    video_files = [
        {"id": f"video-{index}", "name": f"Episode {index:03d}.mp4"}
        for index in range(1, 76)
    ]
    monkeypatch.setattr(
        client,
        "_authorize_share",
        lambda _source_url, _access_code: ("share-id", "share-token", PIKPAK_TEST_SHARE_URL),
    )
    monkeypatch.setattr(client, "_walk_video_files", lambda _share_id, _token: video_files)

    def resolve_candidate(
        _share_id: str,
        _token: str,
        source_url: str,
        item: dict,
        playlist_index: int,
        playlist_count: int,
    ) -> VideoCandidate:
        return VideoCandidate(
            title=str(item["name"]),
            url=f"https://cdn.example.com/{item['id']}.mp4",
            source_url=source_url,
            source_type="pikpak",
            playlist_index=playlist_index,
            playlist_count=playlist_count,
            media_id=str(item["id"]),
        )

    monkeypatch.setattr(client, "_resolve_video_candidate", resolve_candidate)

    candidates = client.discover(PIKPAK_TEST_SHARE_URL)

    assert len(candidates) == 75
    assert candidates[50].media_id == "video-51"
    assert candidates[-1].playlist_index == 75
    assert candidates[-1].playlist_count == 75


def test_pikpak_walk_continues_past_one_hundred_pages(monkeypatch) -> None:
    client = m3u8_core._PikPakShareClient(session=FakePikPakSession(), device_id="device-123")

    def request_page(_method: str, _url: str, *, params: dict[str, str], body=None) -> dict:
        page = int(params["page_token"] or "0")
        return {
            "share_status": "OK",
            "files": [
                {
                    "id": f"video-{page}",
                    "name": f"Episode {page:03d}.mp4",
                    "kind": "drive#file",
                    "mime_type": "video/mp4",
                }
            ],
            "next_page_token": str(page + 1) if page < 100 else "",
        }

    monkeypatch.setattr(client, "_request_json", request_page)

    video_files = client._walk_video_files("share-id", "share-token")

    assert len(video_files) == 101
    assert video_files[-1]["id"] == "video-100"


def test_pikpak_candidate_refresh_uses_stable_file_id_and_memory_only_code(monkeypatch) -> None:
    session = FakePikPakSession(
        FakePikPakResponse({"captcha_token": "anonymous-token"}),
        FakePikPakResponse({"share_status": "OK", "pass_code_token": "share-token"}),
        FakePikPakResponse(
            {
                "share_status": "OK",
                "file_info": {
                    "id": "stable-video-id",
                    "name": "Refreshed Video.mp4",
                    "web_content_link": "https://cdn.example.com/refreshed.mp4?signature=fresh",
                },
            }
        ),
    )
    monkeypatch.setattr(m3u8_core, "_http_session", lambda: session)
    candidate = VideoCandidate(
        title="Original Video",
        url="https://cdn.example.com/expired.mp4?signature=expired",
        source_url=PIKPAK_TEST_SHARE_URL,
        source_type="pikpak",
        media_id="stable-video-id",
    )

    refreshed = m3u8_core.refresh_pikpak_candidate(
        candidate,
        PIKPAK_TEST_SHARE_URL,
        access_code="sample-code",
    )

    assert refreshed.url == "https://cdn.example.com/refreshed.mp4?signature=fresh"
    assert refreshed.media_id == candidate.media_id
    assert [request[0] for request in session.requests] == ["POST", "GET", "GET"]
    assert session.requests[1][2]["params"]["pass_code"] == "sample-code"
    file_info_params = session.requests[2][2]["params"]
    assert file_info_params["file_id"] == candidate.media_id
    assert file_info_params["pass_code_token"] == "share-token"
    assert "pass_code" not in file_info_params
    assert "sample-code" not in repr(refreshed)


def test_pikpak_expired_captcha_token_is_refreshed_once() -> None:
    session = FakePikPakSession(
        FakePikPakResponse({"error_code": 9}, status_code=400),
        FakePikPakResponse({"captcha_token": "fresh-token"}),
        FakePikPakResponse({"share_status": "OK", "files": []}),
    )
    client = m3u8_core._PikPakShareClient(session=session, device_id="device-123")
    client.captcha_token = "expired-token"

    payload = client._request_json("GET", f"{m3u8_core.PIKPAK_DRIVE_API}/share/detail")

    assert payload["share_status"] == "OK"
    assert client.captcha_token == "fresh-token"
    assert [request[0] for request in session.requests] == ["GET", "POST", "GET"]


def test_pikpak_human_verification_is_not_automated() -> None:
    session = FakePikPakSession(FakePikPakResponse({"url": "https://verify.example/challenge"}))
    client = m3u8_core._PikPakShareClient(session=session, device_id="device-123")

    with pytest.raises(HlsError, match="人机验证"):
        client._request_json("GET", f"{m3u8_core.PIKPAK_DRIVE_API}/share/detail")


@pytest.mark.parametrize(
    ("message", "expected_code"),
    [
        ("PikPak 分享需要提取码。", "pikpak_code_required"),
        ("PikPak 提取码错误。", "pikpak_code_invalid"),
        ("PikPak 需要人机验证。", "pikpak_verification_required"),
        ("PikPak 分享不可用：DELETED", "pikpak_share_unavailable"),
    ],
)
def test_pikpak_errors_have_actionable_classification(message: str, expected_code: str) -> None:
    result = classify_error(HlsError(message))

    assert result.code == expected_code
    assert result.retryable is False


def test_pikpak_access_codes_are_redacted_from_error_text() -> None:
    redacted = redact_sensitive_text("PikPak failed pass_code=2468 s_code:abcd password=secret")

    assert "2468" not in redacted
    assert "abcd" not in redacted
    assert "secret" not in redacted


def test_generic_webpage_falls_back_to_ytdlp(monkeypatch) -> None:
    captured_options: dict = {}

    class FakeYoutubeDL:
        def __init__(self, options: dict) -> None:
            self.options = options
            captured_options.update(options)

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
    assert captured_options["extractor_args"] == {"generic": {"impersonate": [""]}}


def test_ytdlp_extractor_args_keep_youtube_and_generic_scoped() -> None:
    assert m3u8_core._ytdlp_extractor_args("https://example.com/watch/123") == {
        "generic": {"impersonate": [""]}
    }
    assert m3u8_core._ytdlp_extractor_args("https://www.youtube.com/watch?v=VIDEO_ID") == {
        "youtube": {"player_client": ["default", "ios"]}
    }


def test_generic_ytdlp_download_uses_initial_page_impersonation(monkeypatch, tmp_path) -> None:
    captured_options: dict = {}

    class FakeYoutubeDL:
        def __init__(self, options: dict) -> None:
            captured_options.update(options)

        def __enter__(self) -> "FakeYoutubeDL":
            return self

        def __exit__(self, exc_type, exc, traceback) -> None:
            return None

        def download(self, urls: list[str]) -> None:
            assert urls == ["https://example.com/watch/123"]

    monkeypatch.setattr(m3u8_core, "yt_dlp", type("FakeYtDlp", (), {"YoutubeDL": FakeYoutubeDL}))
    output_path = tmp_path / "video.mp4"
    job = YouTubeDownloadJob("https://example.com/watch/123", output_path)
    monkeypatch.setattr(job, "_find_output_file", lambda: output_path)

    job.run()

    assert captured_options["extractor_args"] == {"generic": {"impersonate": [""]}}
    assert captured_options["continuedl"] is True
    assert "overwrites" not in captured_options


def test_ytdlp_explicit_overwrite_disables_completed_file_shortcut(monkeypatch, tmp_path) -> None:
    captured_options: dict = {}

    class FakeYoutubeDL:
        def __init__(self, options: dict) -> None:
            captured_options.update(options)

        def __enter__(self) -> "FakeYoutubeDL":
            return self

        def __exit__(self, exc_type, exc, traceback) -> None:
            return None

        def download(self, _urls: list[str]) -> None:
            return None

    monkeypatch.setattr(m3u8_core, "yt_dlp", type("FakeYtDlp", (), {"YoutubeDL": FakeYoutubeDL}))
    output_path = tmp_path / "video.mp4"
    job = YouTubeDownloadJob(
        "https://example.com/watch/123",
        output_path,
        overwrite_existing=True,
    )
    monkeypatch.setattr(job, "_find_output_file", lambda: output_path)

    job.run()

    assert captured_options["overwrites"] is True
    assert captured_options["continuedl"] is False


@pytest.mark.parametrize(
    ("message", "expected_code"),
    [
        (
            "Got HTTP Error 403 caused by Cloudflare anti-bot challenge; install the required impersonation dependency",
            "missing_impersonation",
        ),
        ("HTTP 403 caused by Cloudflare anti-bot challenge", "browser_verification_required"),
    ],
)
def test_cloudflare_errors_have_actionable_classification(message: str, expected_code: str) -> None:
    result = classify_error(HlsError(message))

    assert result.code == expected_code
    assert result.retryable is False


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


def test_ytdlp_quality_and_subtitle_options_use_ffmpeg_when_available() -> None:
    preferences = m3u8_core.DownloadPreferences(
        quality="1080p",
        subtitle_languages=("zh-Hans",),
        include_auto_subtitles=True,
        subtitle_format="srt",
        embed_subtitles=True,
    )

    options = m3u8_core.build_ytdlp_options(
        "https://example.com/watch/123",
        preferences=preferences,
        ffmpeg_available=True,
    )

    assert "height<=?1080" in options["format"]
    assert options["merge_output_format"] == "mp4"
    assert options["writesubtitles"] is True
    assert options["writeautomaticsub"] is True
    assert options["subtitleslangs"] == ["zh-Hans"]
    assert [processor["key"] for processor in options["postprocessors"]] == [
        "FFmpegSubtitlesConvertor",
        "FFmpegEmbedSubtitle",
    ]


def test_ytdlp_without_ffmpeg_uses_single_file_format_and_skips_embedding() -> None:
    preferences = m3u8_core.DownloadPreferences(
        quality="720p",
        subtitle_languages=("en",),
        embed_subtitles=True,
    )

    options = m3u8_core.build_ytdlp_options(
        "https://example.com/watch/123",
        preferences=preferences,
        ffmpeg_available=False,
    )

    assert "+" not in options["format"]
    assert "height<=?720" in options["format"]
    assert "merge_output_format" not in options
    assert "postprocessors" not in options


def test_ytdlp_playlist_exposes_entries_formats_and_subtitles(monkeypatch) -> None:
    class FakeYoutubeDL:
        def __init__(self, options: dict) -> None:
            assert options["noplaylist"] is False
            assert "playlistend" not in options

        def __enter__(self) -> "FakeYoutubeDL":
            return self

        def __exit__(self, exc_type, exc, traceback) -> None:
            return None

        def extract_info(self, url: str, download: bool = False) -> dict:
            return {
                "_type": "playlist",
                "title": "Example Playlist",
                "entries": [
                    {
                        "id": "one",
                        "title": "First Video",
                        "webpage_url": "https://example.com/watch/one",
                        "extractor_key": "Example",
                        "formats": [
                            {
                                "format_id": "1080",
                                "ext": "mp4",
                                "width": 1920,
                                "height": 1080,
                                "fps": 60,
                                "dynamic_range": "HDR10",
                                "vcodec": "avc1",
                                "acodec": "none",
                                "tbr": 4200,
                                "filesize": 4096,
                                "protocol": "https",
                            }
                        ],
                        "subtitles": {"zh-Hans": [{"ext": "vtt", "name": "Chinese"}]},
                        "automatic_captions": {"en": [{"ext": "vtt"}]},
                    },
                    {
                        "id": "two",
                        "title": "Second Video",
                        "webpage_url": "https://example.com/watch/two",
                    },
                ]
                + [
                    {
                        "id": f"item-{index}",
                        "title": f"Video {index}",
                        "webpage_url": f"https://example.com/watch/item-{index}",
                    }
                    for index in range(3, 76)
                ],
            }

    monkeypatch.setattr(m3u8_core, "yt_dlp", type("FakeYtDlp", (), {"YoutubeDL": FakeYoutubeDL}))

    candidates = m3u8_core._discover_ytdlp_candidates("https://example.com/playlist", None)

    assert len(candidates) == 75
    assert candidates[0].url == "https://example.com/watch/one"
    assert candidates[0].playlist_index == 1
    assert candidates[0].playlist_count == 75
    assert candidates[0].playlist_title == "Example Playlist"
    assert candidates[0].formats[0].resolution == "1920x1080"
    assert candidates[0].formats[0].has_video is True
    assert [(track.language, track.automatic) for track in candidates[0].subtitles] == [
        ("zh-Hans", False),
        ("en", True),
    ]
    assert candidates[1].url == "https://example.com/watch/two"
    assert candidates[50].url == "https://example.com/watch/item-51"
    assert candidates[-1].playlist_index == 75


def test_xiaohongshu_has_a_dedicated_ytdlp_extractor() -> None:
    url = "https://www.xiaohongshu.com/explore/deadbeefdeadbeefdeadbeef"

    assert m3u8_core._has_specific_ytdlp_extractor(url) is True
