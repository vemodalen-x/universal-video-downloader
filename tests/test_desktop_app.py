from pathlib import Path

from PIL import Image

from m3u8_core import VideoCandidate
from m3u8_desktop_app import (
    MAX_SEGMENT_BLOCKS,
    UI_REFRESH_INTERVAL_MS,
    _available_output_path,
    _candidate_format_label,
    _candidate_summary,
    _history_status_label,
)


def test_ui_refresh_budget_and_segment_cap() -> None:
    assert UI_REFRESH_INTERVAL_MS >= 100
    assert UI_REFRESH_INTERVAL_MS <= 100
    assert MAX_SEGMENT_BLOCKS <= 200


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


def test_v2_brand_assets_cover_windows_icon_sizes() -> None:
    assets = Path(__file__).resolve().parents[1] / "assets"
    with Image.open(assets / "app_brand_v2.png") as master:
        assert master.size == (1024, 1024)
        assert master.mode == "RGBA"
        assert master.getchannel("A").getpixel((0, 0)) == 0
        assert master.getchannel("A").getbbox() is not None

    with Image.open(assets / "app_icon_v2_64.png") as small:
        assert small.size == (64, 64)
        assert small.mode == "RGBA"

    with Image.open(assets / "app_brand_v2_40.png") as header_logo:
        assert header_logo.size == (40, 40)
        assert header_logo.getchannel("A").getpixel((0, 0)) == 0

    with Image.open(assets / "app_icon_v2.ico") as icon:
        sizes = icon.info.get("sizes", set())
        assert {(16, 16), (32, 32), (64, 64), (256, 256)} <= sizes
