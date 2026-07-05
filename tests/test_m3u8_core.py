from m3u8_core import _looks_like_direct_video_url, _looks_like_youtube_url, find_direct_video_urls, find_m3u8_urls, parse_playlist


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
