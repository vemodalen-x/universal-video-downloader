from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import m3u8_core
from m3u8_core import (
    DirectDownloadJob,
    _looks_like_direct_video_url,
    _looks_like_youtube_url,
    discover_candidates,
    find_direct_video_urls,
    find_m3u8_urls,
    parse_playlist,
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
