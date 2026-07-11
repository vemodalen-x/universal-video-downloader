from pathlib import Path
import struct
import zlib

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
