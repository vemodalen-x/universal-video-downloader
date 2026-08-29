from __future__ import annotations

import hashlib
import html
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from collections import deque
from dataclasses import asdict, dataclass
from functools import lru_cache
from pathlib import Path
from queue import Empty, Queue
from typing import Callable, Iterable, Optional
from urllib.parse import parse_qs, unquote, urljoin, urlparse, urlsplit, urlunsplit

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

try:
    from Crypto.Cipher import AES
    from Crypto.Util.Padding import unpad
except ImportError:  # pragma: no cover - handled at runtime for users.
    AES = None
    unpad = None

try:
    import yt_dlp
except ImportError:  # pragma: no cover - handled at runtime for users.
    yt_dlp = None


USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0 Safari/537.36"
)

DIRECT_VIDEO_EXTENSIONS = {".mp4", ".webm", ".mov", ".mkv", ".m4v", ".flv", ".avi", ".wmv"}
HISTORY_ACTIVE_STATES = {"preparing", "downloading", "paused"}
HISTORY_FINAL_STATES = {"completed", "failed", "stopped", "interrupted"}
HISTORY_STATES = HISTORY_ACTIVE_STATES | HISTORY_FINAL_STATES
_HTTP_LOCAL = threading.local()

PIKPAK_SHARE_HOSTS = {"mypikpak.com", "mypikpak.net"}
PIKPAK_DRIVE_API = "https://api-drive.mypikpak.net/drive/v1"
PIKPAK_CAPTCHA_API = "https://user.mypikpak.net/v1/shield/captcha/init"
PIKPAK_WEB_CLIENT_ID = "YUMx5nI8ZU8Ap8pm"
PIKPAK_WEB_CLIENT_VERSION = "2.0.0"
PIKPAK_WEB_PACKAGE = "mypikpak.com"
PIKPAK_WEB_ALGORITHMS = (
    "C9qPpZLN8ucRTaTiUMWYS9cQvWOE",
    "+r6CQVxjzJV6LCV",
    "F",
    "pFJRC",
    "9WXYIDGrwTCz2OiVlgZa90qpECPD6olt",
    "/750aCr4lm/Sly/c",
    "RB+DT/gZCrbV",
    "",
    "CyLsf7hdkIRxRm215hl",
    "7xHvLi2tOYP0Y92b",
    "ZGTXXxu8E/MIWaEDB+Sm/",
    "1UI3",
    "E7fP5Pfijd+7K+t6Tg/NhuLq0eEUVChpJSkrKxpO",
    "ihtqpG6FMt65+Xk+tWUH2",
    "NhXXU9rg4XXdzo7u5o",
)
BAIDUPAN_SHARE_HOSTS = {"pan.baidu.com"}
BAIDUPCS_EXECUTABLE_ENV = "BAIDUPCS_GO_PATH"
BAIDUPCS_EXECUTABLE_NAMES = ("BaiduPCS-Go.exe", "BaiduPCS-Go")
BAIDUPAN_INCOMPLETE_SUFFIXES = (".part", ".download", ".tmp", ".temp")
DIRECT_DOWNLOAD_RETRIES = 4
DIRECT_RETRY_BACKOFF_SECONDS = 0.75
DIRECT_RANGE_CHUNK_BYTES = 32 * 1024 * 1024
HISTORY_RECORD_LIMIT = 2000


class HlsError(Exception):
    """Base error for playlist discovery and download failures."""


class PlaylistParseError(HlsError):
    """Raised when an m3u8 document cannot be parsed."""


class DirectResumeError(HlsError):
    """Raised when a server response cannot be appended safely to a partial file."""


@dataclass(frozen=True)
class SegmentKey:
    method: str
    uri: str
    iv_hex: Optional[str] = None


@dataclass(frozen=True)
class ByteRange:
    offset: int
    length: int


@dataclass(frozen=True)
class Segment:
    index: int
    url: str
    duration: float
    file_name: str
    key: Optional[SegmentKey] = None
    is_init: bool = False
    byte_range: Optional[ByteRange] = None


@dataclass(frozen=True)
class Variant:
    url: str
    bandwidth: int = 0
    resolution: str = ""
    codecs: str = ""


@dataclass
class MediaPlaylist:
    url: str
    segments: list[Segment]
    total_duration: float = 0.0
    target_duration: float = 0.0
    encrypted: bool = False
    has_byterange: bool = False


@dataclass
class PlaylistInfo:
    url: str
    variants: list[Variant]
    media: Optional[MediaPlaylist]


@dataclass
class VideoCandidate:
    title: str
    url: str
    source_url: str
    referer: str = ""
    bandwidth: int = 0
    resolution: str = ""
    segment_count: int = 0
    duration: float = 0.0
    encrypted: bool = False
    source_type: str = "hls"
    container: str = ""
    extractor: str = ""
    formats: tuple[MediaFormat, ...] = ()
    subtitles: tuple[SubtitleTrack, ...] = ()
    playlist_index: int = 0
    playlist_count: int = 0
    playlist_title: str = ""
    media_id: str = ""


@dataclass(frozen=True)
class MediaFormat:
    format_id: str
    ext: str = ""
    resolution: str = ""
    width: int = 0
    height: int = 0
    fps: float = 0.0
    dynamic_range: str = ""
    vcodec: str = ""
    acodec: str = ""
    tbr: float = 0.0
    filesize: int = 0
    protocol: str = ""
    has_video: bool = False
    has_audio: bool = False


@dataclass(frozen=True)
class SubtitleTrack:
    language: str
    name: str = ""
    automatic: bool = False
    formats: tuple[str, ...] = ()


@dataclass(frozen=True)
class DownloadPreferences:
    quality: str = "best"
    subtitle_languages: tuple[str, ...] = ()
    include_auto_subtitles: bool = False
    subtitle_format: str = "srt"
    embed_subtitles: bool = False


@dataclass(frozen=True)
class FFmpegCapability:
    available: bool
    path: str = ""


@dataclass
class DownloadRecord:
    """Local task metadata; source URLs are stored without credentials or query strings."""

    record_id: str
    title: str
    source_type: str
    source_url: str
    source_host: str
    output_path: str
    status: str
    media_key: str = ""
    progress: float = 0.0
    bytes_done: int = 0
    updated_at: float = 0.0
    error_code: str = ""
    error_message: str = ""

    @classmethod
    def from_dict(cls, payload: dict) -> "DownloadRecord":
        status = str(payload.get("status") or "interrupted")
        if status not in HISTORY_STATES:
            status = "interrupted"
        source_url = redact_url(str(payload.get("source_url") or ""))
        source_host = urlparse(source_url).hostname or ""
        media_key = str(payload.get("media_key") or "").strip().lower()
        if not re.fullmatch(r"[a-f0-9]{64}", media_key):
            media_key = ""
        return cls(
            record_id=str(payload.get("record_id") or ""),
            title=sanitize_file_name(str(payload.get("title") or "video"), "video"),
            source_type=str(payload.get("source_type") or "unknown")[:24],
            source_url=source_url,
            source_host=source_host,
            output_path=str(payload.get("output_path") or ""),
            status=status,
            media_key=media_key,
            progress=max(0.0, min(100.0, float(payload.get("progress") or 0.0))),
            bytes_done=max(0, int(payload.get("bytes_done") or 0)),
            updated_at=float(payload.get("updated_at") or 0.0),
            error_code=str(payload.get("error_code") or "")[:48],
            error_message=redact_sensitive_text(str(payload.get("error_message") or ""))[:500],
        )


@dataclass(frozen=True)
class UserFacingError:
    code: str
    title: str
    message: str
    action: str
    retryable: bool = True
    detail: str = ""


class DownloadHistoryStore:
    """Persist a bounded task library with atomic replacement and backup recovery. @codex-comment"""

    def __init__(self, path: Path, limit: int = HISTORY_RECORD_LIMIT) -> None:
        self.path = path
        self.limit = max(10, limit)
        self._lock = threading.RLock()

    @property
    def backup_path(self) -> Path:
        return self.path.with_suffix(self.path.suffix + ".bak")

    def _load_unlocked(self) -> list[DownloadRecord]:
        payload: object = None
        for candidate in (self.path, self.backup_path):
            if not candidate.exists():
                continue
            try:
                payload = json.loads(candidate.read_text(encoding="utf-8"))
            except (OSError, ValueError, TypeError):
                continue
            if isinstance(payload, dict):
                break
        records = payload.get("records", []) if isinstance(payload, dict) else []
        result: list[DownloadRecord] = []
        for item in records:
            if not isinstance(item, dict):
                continue
            try:
                record = DownloadRecord.from_dict(item)
            except (TypeError, ValueError, OverflowError):
                continue
            if record.record_id:
                result.append(record)
        return sorted(result, key=lambda item: item.updated_at, reverse=True)[: self.limit]

    def load(self) -> list[DownloadRecord]:
        with self._lock:
            return self._load_unlocked()

    def _save_unlocked(self, records: Iterable[DownloadRecord]) -> None:
        normalized = [DownloadRecord.from_dict(asdict(item)) for item in records]
        ordered = sorted(normalized, key=lambda item: item.updated_at, reverse=True)[: self.limit]
        payload = {"version": 1, "records": [asdict(item) for item in ordered]}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = self.path.with_name(
            f".{self.path.name}.{os.getpid()}.{threading.get_ident()}.tmp"
        )
        try:
            with temp_path.open("w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            if self.path.exists():
                try:
                    json.loads(self.path.read_text(encoding="utf-8"))
                except (OSError, ValueError, TypeError):
                    pass
                else:
                    shutil.copy2(self.path, self.backup_path)
            temp_path.replace(self.path)
        finally:
            try:
                temp_path.unlink()
            except FileNotFoundError:
                pass

    def save(self, records: Iterable[DownloadRecord]) -> None:
        with self._lock:
            self._save_unlocked(records)

    def upsert(self, record: DownloadRecord) -> list[DownloadRecord]:
        with self._lock:
            records = [item for item in self._load_unlocked() if item.record_id != record.record_id]
            records.insert(0, record)
            self._save_unlocked(records)
            return records[: self.limit]

    def clear_completed(self) -> list[DownloadRecord]:
        with self._lock:
            records = [item for item in self._load_unlocked() if item.status != "completed"]
            self._save_unlocked(records)
            return records


class CoalescingEventBuffer:
    """Keeps terminal events ordered while collapsing high-frequency UI updates."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._sequence = 0
        self._regular: deque[tuple[int, str, dict]] = deque()
        self._coalesced: dict[tuple[str, object], tuple[int, str, dict]] = {}

    def put(self, event: str, payload: dict) -> None:
        with self._lock:
            self._sequence += 1
            item = (self._sequence, event, payload)
            if event == "progress":
                self._coalesced[(event, None)] = item
            elif event == "segment":
                self._coalesced[(event, payload.get("index"))] = item
            else:
                self._regular.append(item)

    def drain(self) -> list[tuple[str, dict]]:
        with self._lock:
            items = list(self._regular) + list(self._coalesced.values())
            self._regular.clear()
            self._coalesced.clear()
        items.sort(key=lambda item: item[0])
        return [(event, payload) for _sequence, event, payload in items]


EventCallback = Callable[[str, dict], None]


def _http_session() -> requests.Session:
    session = getattr(_HTTP_LOCAL, "session", None)
    if session is not None:
        return session
    retry = Retry(
        total=3,
        connect=3,
        read=2,
        status=3,
        backoff_factor=0.45,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset({"GET", "HEAD"}),
        respect_retry_after_header=True,
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry, pool_connections=8, pool_maxsize=16)
    session = requests.Session()
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    _HTTP_LOCAL.session = session
    return session


def make_headers(referer: str = "") -> dict[str, str]:
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "*/*",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }
    if referer:
        headers["Referer"] = referer
    return headers


def fetch_text(url: str, headers: Optional[dict[str, str]] = None, timeout: int = 25) -> str:
    with _http_session().get(url, headers=headers or make_headers(), timeout=(10, timeout)) as response:
        response.raise_for_status()
        if not response.encoding:
            response.encoding = "utf-8"
        return response.text


def fetch_text_with_fallbacks(
    url: str,
    header_candidates: Iterable[dict[str, str]],
    timeout: int = 25,
) -> tuple[str, dict[str, str]]:
    last_error: Optional[Exception] = None
    tried = False
    for headers in header_candidates:
        tried = True
        try:
            return fetch_text(url, headers=headers, timeout=timeout), headers
        except Exception as exc:
            last_error = exc
            if not _should_try_next_header(exc):
                break
    if not tried:
        return fetch_text(url, timeout=timeout), make_headers()
    raise last_error or HlsError("请求失败")


def fetch_binary(
    url: str,
    path: Path,
    headers: Optional[dict[str, str]] = None,
    stop_event: Optional[threading.Event] = None,
    chunk_size: int = 256 * 1024,
) -> int:
    request_headers = dict(headers or make_headers())
    start_at = path.stat().st_size if path.exists() else 0
    mode = "ab" if start_at else "wb"
    request_headers["Accept-Encoding"] = "identity"
    if start_at:
        request_headers["Range"] = f"bytes={start_at}-"

    with _http_session().get(url, headers=request_headers, stream=True, timeout=(10, 45)) as response:
        if response.status_code == 416:
            path.unlink(missing_ok=True)
            return fetch_binary(url, path, headers, stop_event, chunk_size)
        response.raise_for_status()

        if start_at and response.status_code == 206 and _response_range_start(response) != start_at:
            path.unlink(missing_ok=True)
            return fetch_binary(url, path, headers, stop_event, chunk_size)

        if start_at and response.status_code != 206:
            mode = "wb"
            start_at = 0

        expected_total = _response_total_size(response, start_at)
        written = start_at
        with path.open(mode + "") as handle:
            for chunk in response.iter_content(chunk_size=chunk_size):
                if stop_event and stop_event.is_set():
                    raise HlsError("任务已停止")
                if not chunk:
                    continue
                handle.write(chunk)
                written += len(chunk)
        if expected_total > 0 and written < expected_total:
            raise HlsError(f"连接提前结束：预期 {expected_total} 字节，实际 {written} 字节")
        return written


def fetch_binary_range(
    url: str,
    path: Path,
    byte_range: ByteRange,
    headers: Optional[dict[str, str]] = None,
    stop_event: Optional[threading.Event] = None,
    chunk_size: int = 256 * 1024,
) -> int:
    """Fetch one exact HLS byte range and resume only within that interval."""

    if byte_range.offset < 0 or byte_range.length <= 0:
        raise HlsError("HLS 字节范围无效")
    written = path.stat().st_size if path.exists() else 0
    if written > byte_range.length:
        path.unlink(missing_ok=True)
        written = 0
    if written == byte_range.length:
        return written

    request_start = byte_range.offset + written
    request_end = byte_range.offset + byte_range.length - 1
    request_headers = dict(headers or make_headers())
    request_headers["Accept-Encoding"] = "identity"
    request_headers["Range"] = f"bytes={request_start}-{request_end}"

    with _http_session().get(url, headers=request_headers, stream=True, timeout=(10, 45)) as response:
        response.raise_for_status()
        bounds = _response_range_bounds(response)
        if response.status_code != 206 or bounds != (request_start, request_end):
            raise HlsError("服务器未按请求返回 HLS 字节范围")
        with path.open("ab" if written else "wb") as handle:
            for chunk in response.iter_content(chunk_size=chunk_size):
                if stop_event and stop_event.is_set():
                    raise HlsError("任务已停止")
                if not chunk:
                    continue
                handle.write(chunk)
                written += len(chunk)
    if written != byte_range.length:
        raise HlsError(f"HLS 字节范围长度不匹配：预期 {byte_range.length} 字节，实际 {written} 字节")
    return written


def parse_attribute_list(raw: str) -> dict[str, str]:
    attrs: dict[str, str] = {}
    i = 0
    length = len(raw)
    while i < length:
        while i < length and raw[i] in " ,":
            i += 1
        key_start = i
        while i < length and raw[i] not in "=":
            i += 1
        if i >= length:
            break
        key = raw[key_start:i].strip().upper()
        i += 1
        if i < length and raw[i] == '"':
            i += 1
            value_start = i
            while i < length and raw[i] != '"':
                i += 1
            value = raw[value_start:i]
            i += 1
        else:
            value_start = i
            while i < length and raw[i] != ",":
                i += 1
            value = raw[value_start:i].strip()
        attrs[key] = value
        while i < length and raw[i] != ",":
            i += 1
        if i < length and raw[i] == ",":
            i += 1
    return attrs


def _parse_byte_range(raw: str) -> tuple[int, Optional[int]]:
    parts = raw.strip().split("@")
    if len(parts) not in {1, 2} or not parts[0].isdigit() or (len(parts) == 2 and not parts[1].isdigit()):
        raise PlaylistParseError("HLS 字节范围格式无效")
    length = int(parts[0])
    if length <= 0:
        raise PlaylistParseError("HLS 字节范围长度必须大于零")
    return length, int(parts[1]) if len(parts) == 2 else None


def parse_playlist(url: str, text: str) -> PlaylistInfo:
    lines = [line.strip() for line in text.replace("\r\n", "\n").split("\n") if line.strip()]
    if not lines or lines[0] != "#EXTM3U":
        raise PlaylistParseError("不是有效的 m3u8 文件")

    variants: list[Variant] = []
    segments: list[Segment] = []
    pending_stream: Optional[dict[str, str]] = None
    current_duration = 0.0
    current_key: Optional[SegmentKey] = None
    media_sequence = 0
    media_segment_index = 0
    target_duration = 0.0
    total_duration = 0.0
    has_byterange = False
    pending_byterange: Optional[tuple[int, Optional[int]]] = None
    previous_media_range: Optional[tuple[str, ByteRange]] = None

    for line in lines[1:]:
        if line.startswith("#EXT-X-STREAM-INF:"):
            pending_stream = parse_attribute_list(line.split(":", 1)[1])
            continue

        if pending_stream and not line.startswith("#"):
            bandwidth = _safe_int(pending_stream.get("BANDWIDTH", "0"))
            variants.append(
                Variant(
                    url=urljoin(url, line),
                    bandwidth=bandwidth,
                    resolution=pending_stream.get("RESOLUTION", ""),
                    codecs=pending_stream.get("CODECS", ""),
                )
            )
            pending_stream = None
            continue

        if line.startswith("#EXT-X-TARGETDURATION:"):
            target_duration = float(_safe_int(line.split(":", 1)[1]))
            continue

        if line.startswith("#EXT-X-MEDIA-SEQUENCE:"):
            media_sequence = _safe_int(line.split(":", 1)[1])
            continue

        if line.startswith("#EXT-X-BYTERANGE:"):
            if pending_byterange is not None:
                raise PlaylistParseError("连续的 HLS 字节范围标签缺少媒体 URI")
            has_byterange = True
            pending_byterange = _parse_byte_range(line.split(":", 1)[1])
            continue

        if line.startswith("#EXT-X-KEY:"):
            attrs = parse_attribute_list(line.split(":", 1)[1])
            method = attrs.get("METHOD", "").upper()
            if method in ("", "NONE"):
                current_key = None
            elif method == "AES-128":
                uri = attrs.get("URI", "")
                if not uri:
                    raise PlaylistParseError("AES-128 加密片段缺少 KEY URI")
                current_key = SegmentKey(
                    method=method,
                    uri=urljoin(url, uri),
                    iv_hex=_normalize_iv(attrs.get("IV")),
                )
            else:
                raise PlaylistParseError(f"暂不支持的加密方式：{method}")
            continue

        if line.startswith("#EXT-X-MAP:"):
            attrs = parse_attribute_list(line.split(":", 1)[1])
            uri = attrs.get("URI")
            if uri:
                map_range = None
                if "BYTERANGE" in attrs:
                    length, offset = _parse_byte_range(attrs["BYTERANGE"])
                    if offset is None:
                        raise PlaylistParseError("EXT-X-MAP 字节范围必须指定起始位置")
                    map_range = ByteRange(offset=offset, length=length)
                    has_byterange = True
                segments.append(
                    Segment(
                        index=len(segments),
                        url=urljoin(url, uri),
                        duration=0.0,
                        file_name=f"{len(segments):06d}{_extension_from_url(uri, '.init')}",
                        key=current_key,
                        is_init=True,
                        byte_range=map_range,
                    )
                )
            continue

        if line.startswith("#EXTINF:"):
            raw_duration = line.split(":", 1)[1].split(",", 1)[0]
            try:
                current_duration = float(raw_duration)
            except ValueError:
                current_duration = 0.0
            continue

        if line.startswith("#"):
            continue

        key = current_key
        if key and key.iv_hex is None:
            sequence_number = media_sequence + media_segment_index
            key = SegmentKey(key.method, key.uri, f"{sequence_number:032x}")

        segment_url = urljoin(url, line)
        byte_range = None
        if pending_byterange is not None:
            length, offset = pending_byterange
            if offset is None:
                if previous_media_range is None or previous_media_range[0] != segment_url:
                    raise PlaylistParseError("HLS 隐式字节范围缺少同 URI 的前一媒体分片")
                offset = previous_media_range[1].offset + previous_media_range[1].length
            byte_range = ByteRange(offset=offset, length=length)
            pending_byterange = None

        segments.append(
            Segment(
                index=len(segments),
                url=segment_url,
                duration=current_duration,
                file_name=f"{len(segments):06d}{_extension_from_url(line, '.ts')}",
                key=key,
                byte_range=byte_range,
            )
        )
        previous_media_range = (segment_url, byte_range) if byte_range else None
        total_duration += current_duration
        current_duration = 0.0
        media_segment_index += 1

    if pending_byterange is not None:
        raise PlaylistParseError("EXT-X-BYTERANGE 后缺少媒体 URI")

    media = None
    if segments:
        media = MediaPlaylist(
            url=url,
            segments=segments,
            total_duration=total_duration,
            target_duration=target_duration,
            encrypted=any(segment.key for segment in segments),
            has_byterange=has_byterange,
        )
    return PlaylistInfo(url=url, variants=variants, media=media)


def load_playlist_info(url: str, headers: Optional[dict[str, str]] = None) -> PlaylistInfo:
    return parse_playlist(url, fetch_text(url, headers=headers))


def load_playlist_info_with_fallbacks(
    url: str,
    header_candidates: Iterable[dict[str, str]],
) -> tuple[PlaylistInfo, dict[str, str]]:
    last_error: Optional[Exception] = None
    for headers in header_candidates:
        try:
            text = fetch_text(url, headers=headers)
            return parse_playlist(url, text), headers
        except Exception as exc:
            last_error = exc
            if not _should_try_next_header(exc) and not isinstance(exc, PlaylistParseError):
                break
    raise last_error or PlaylistParseError("无法解析 m3u8")


def load_best_media_playlist(url: str, headers: Optional[dict[str, str]] = None) -> MediaPlaylist:
    info = load_playlist_info(url, headers=headers)
    if info.media:
        return info.media
    if not info.variants:
        raise PlaylistParseError("没有发现可下载的视频分片")

    best = max(info.variants, key=lambda item: (_resolution_area(item.resolution), item.bandwidth))
    nested = load_playlist_info(best.url, headers=headers)
    if not nested.media:
        raise PlaylistParseError("清晰度列表没有指向可下载的视频分片")
    return nested.media


def _looks_like_pikpak_share_url(url: str) -> bool:
    parsed = urlparse(url.strip())
    host = (parsed.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    parts = [part for part in parsed.path.split("/") if part]
    return (
        parsed.scheme in {"http", "https"}
        and host in PIKPAK_SHARE_HOSTS
        and len(parts) >= 2
        and parts[0].lower() == "s"
        and bool(re.fullmatch(r"[A-Za-z0-9_-]{12,160}", parts[1]))
    )


def _pikpak_share_reference(source_url: str, access_code: str = "") -> tuple[str, str]:
    if not _looks_like_pikpak_share_url(source_url):
        raise HlsError("请输入有效的 PikPak 分享链接")
    parsed = urlparse(source_url.strip())
    parts = [part for part in parsed.path.split("/") if part]
    query = parse_qs(parsed.query, keep_blank_values=False)
    code = access_code.strip()
    if not code:
        for key in ("s_code", "pwd", "passcode", "code"):
            values = query.get(key)
            if values and values[0].strip():
                code = values[0].strip()
                break
    return parts[1], code[:128]


def _looks_like_baidupan_share_url(url: str) -> bool:
    """Return whether a URL is a modern Baidu Netdisk `/s/` share. @codex-comment"""

    parsed = urlparse(url.strip())
    host = (parsed.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    parts = [part for part in parsed.path.split("/") if part]
    return (
        parsed.scheme in {"http", "https"}
        and host in BAIDUPAN_SHARE_HOSTS
        and len(parts) == 2
        and parts[0].lower() == "s"
        and bool(re.fullmatch(r"1[A-Za-z0-9_-]{5,40}", parts[1]))
    )


def _baidupan_share_reference(source_url: str, access_code: str = "") -> tuple[str, str]:
    """Normalize one share URL and keep its optional four-character code in memory only. @codex-comment"""

    if not _looks_like_baidupan_share_url(source_url):
        raise HlsError("请输入有效的百度网盘分享链接")
    parsed = urlparse(source_url.strip())
    query = parse_qs(parsed.query, keep_blank_values=False)
    code = access_code.strip() or str((query.get("pwd") or [""])[0]).strip()
    if code and not re.fullmatch(r"[A-Za-z0-9]{4}", code):
        raise HlsError("百度网盘提取码格式不正确")
    canonical_url = urlunsplit(("https", "pan.baidu.com", parsed.path.rstrip("/"), "", ""))
    return canonical_url, code


def _baidupan_share_title(source_url: str) -> str:
    """Use the browser route's final folder name as a local display hint when available. @codex-comment"""

    parsed = urlparse(source_url.strip())
    fragment = parse_qs(parsed.fragment, keep_blank_values=False)
    route_path = str((fragment.get("list/path") or [""])[0]).replace("\\", "/").rstrip("/")
    if route_path:
        return sanitize_file_name(route_path.rsplit("/", 1)[-1], "百度网盘分享")
    return "百度网盘分享"


def find_baidupcs_executable(explicit_path: str | Path | None = None) -> Optional[Path]:
    """Locate a bundled or explicitly installed BaiduPCS-Go connector without executing it. @codex-comment"""

    roots = [Path(sys.executable).resolve().parent] if getattr(sys, "frozen", False) else [Path(__file__).resolve().parent]
    candidates: list[Path] = []
    if explicit_path:
        candidates.append(Path(explicit_path).expanduser())
    configured = os.environ.get(BAIDUPCS_EXECUTABLE_ENV, "").strip()
    if configured:
        candidates.append(Path(configured).expanduser())
    for root in roots:
        for executable_name in BAIDUPCS_EXECUTABLE_NAMES:
            candidates.extend((root / executable_name, root / "bin" / executable_name))
        candidates.extend(sorted(root.glob("build/vendor/baidupcs-go/*/*/BaiduPCS-Go.exe"), reverse=True))
    for executable_name in BAIDUPCS_EXECUTABLE_NAMES:
        resolved = shutil.which(executable_name)
        if resolved:
            candidates.append(Path(resolved))
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    return None


def _pikpak_captcha_sign(device_id: str, timestamp: str) -> str:
    value = f"{PIKPAK_WEB_CLIENT_ID}{PIKPAK_WEB_CLIENT_VERSION}{PIKPAK_WEB_PACKAGE}{device_id}{timestamp}"
    for algorithm in PIKPAK_WEB_ALGORITHMS:
        value = hashlib.md5((value + algorithm).encode("utf-8")).hexdigest()
    return "1." + value


def _pikpak_action(method: str, url: str) -> str:
    path = urlsplit(url).path
    return f"{method.upper()}:{path}"


class _PikPakShareClient:
    """Resolve public PikPak shares without account credentials or automated human verification."""

    def __init__(
        self,
        callback: Optional[EventCallback] = None,
        session: Optional[requests.Session] = None,
        device_id: str = "",
    ) -> None:
        self.callback = callback
        self.session = session or _http_session()
        self.device_id = device_id or hashlib.sha256(os.urandom(32)).hexdigest()[:32]
        self.captcha_token = ""

    def discover(self, source_url: str, access_code: str = "") -> list[VideoCandidate]:
        share_id, pass_code_token, share_url = self._authorize_share(source_url, access_code)

        video_files = self._walk_video_files(share_id, pass_code_token)
        candidates: list[VideoCandidate] = []
        for file_info in video_files:
            candidate = self._resolve_video_candidate(
                share_id,
                pass_code_token,
                share_url,
                file_info,
                len(candidates) + 1,
                len(video_files),
            )
            if candidate is not None:
                candidates.append(candidate)

        if not candidates:
            if video_files:
                raise HlsError("PikPak 分享中的视频没有可用下载地址，可能受会员、地区或有效期限制。")
            raise HlsError("PikPak 分享中没有发现可下载的视频文件。")
        return candidates

    def refresh_file(self, source_url: str, access_code: str, file_id: str) -> VideoCandidate:
        """Resolve one stable PikPak file ID to a fresh temporary URL. @codex-comment"""

        if not file_id:
            raise HlsError("PikPak 视频缺少稳定文件标识，无法刷新临时下载地址。")
        share_id, pass_code_token, share_url = self._authorize_share(source_url, access_code)
        candidate = self._resolve_video_candidate(
            share_id,
            pass_code_token,
            share_url,
            {"id": file_id},
            0,
            0,
        )
        if candidate is None:
            raise HlsError("PikPak 没有返回新的下载地址，请重新解析分享。")
        return candidate

    def _authorize_share(self, source_url: str, access_code: str) -> tuple[str, str, str]:
        """Exchange an in-memory access code for a short-lived share token. @codex-comment"""

        share_id, resolved_code = _pikpak_share_reference(source_url, access_code)
        pass_code_token = ""
        if resolved_code:
            payload = self._request_json(
                "GET",
                f"{PIKPAK_DRIVE_API}/share",
                params={
                    "share_id": share_id,
                    "pass_code": resolved_code,
                    "thumbnail_size": "SIZE_LARGE",
                    "limit": "100",
                },
            )
            self._require_available_share(payload)
            pass_code_token = str(payload.get("pass_code_token") or "")
        return share_id, pass_code_token, redact_url(source_url)

    def _walk_video_files(self, share_id: str, pass_code_token: str) -> list[dict]:
        folders: deque[str] = deque([""])
        seen_folders = {""}
        seen_files: set[str] = set()
        video_files: list[dict] = []

        while folders:
            parent_id = folders.popleft()
            page_token = ""
            seen_page_tokens: set[str] = set()
            while True:
                payload = self._request_json(
                    "GET",
                    f"{PIKPAK_DRIVE_API}/share/detail",
                    params={
                        "parent_id": parent_id,
                        "share_id": share_id,
                        "thumbnail_size": "SIZE_LARGE",
                        "with_audit": "true",
                        "limit": "100",
                        "filters": '{"phase":{"eq":"PHASE_TYPE_COMPLETE"},"trashed":{"eq":false}}',
                        "page_token": page_token,
                        "pass_code_token": pass_code_token,
                    },
                )
                self._require_available_share(payload)
                for item in payload.get("files") or []:
                    if not isinstance(item, dict):
                        continue
                    file_id = str(item.get("id") or "")
                    if not file_id or file_id in seen_files:
                        continue
                    seen_files.add(file_id)
                    if str(item.get("kind") or "") == "drive#folder":
                        if file_id not in seen_folders:
                            seen_folders.add(file_id)
                            folders.append(file_id)
                    elif _is_pikpak_video_file(item):
                        video_files.append(item)

                next_page_token = str(payload.get("next_page_token") or "")
                if not next_page_token or next_page_token in seen_page_tokens:
                    break
                seen_page_tokens.add(next_page_token)
                page_token = next_page_token

        return video_files

    def _resolve_video_candidate(
        self,
        share_id: str,
        pass_code_token: str,
        source_url: str,
        item: dict,
        playlist_index: int,
        playlist_count: int,
    ) -> Optional[VideoCandidate]:
        payload = self._request_json(
            "GET",
            f"{PIKPAK_DRIVE_API}/share/file_info",
            params={
                "share_id": share_id,
                "file_id": str(item.get("id") or ""),
                "pass_code_token": pass_code_token,
            },
        )
        self._require_available_share(payload)
        detailed = payload.get("file_info") if isinstance(payload.get("file_info"), dict) else {}
        merged = {**item, **detailed}
        medias = [media for media in merged.get("medias") or [] if isinstance(media, dict)]
        linked_medias = [media for media in medias if _pikpak_media_url(media)]
        selected_media = max(linked_medias, key=_pikpak_media_score, default=None)
        download_url = str(merged.get("web_content_link") or "")
        if not _is_http_url(download_url) and selected_media is not None:
            download_url = _pikpak_media_url(selected_media)
        if not _is_http_url(download_url):
            _emit(
                self.callback,
                "log",
                level="warning",
                message=f"跳过没有下载地址的 PikPak 视频：{sanitize_file_name(str(merged.get('name') or 'video'))}",
            )
            return None

        title = sanitize_file_name(str(merged.get("name") or item.get("name") or "video"), "video")
        display_title = f"{title} / PikPak"
        if playlist_count > 1:
            display_title = f"{playlist_index:02d}. {display_title}"
        resolution = _pikpak_media_resolution(selected_media)
        if resolution:
            display_title = f"{display_title} / {resolution}"
        video = selected_media.get("video") if isinstance(selected_media, dict) else {}
        video = video if isinstance(video, dict) else {}
        container = Path(title).suffix.lstrip(".").lower() or _extension_from_url(download_url, "mp4")
        source_type = "hls" if _looks_like_playlist_url(download_url) else "pikpak"
        return VideoCandidate(
            title=display_title,
            url=download_url,
            source_url=source_url,
            referer=source_url,
            bandwidth=_safe_int(str(video.get("bit_rate") or 0)),
            resolution=resolution,
            segment_count=0,
            duration=_normalize_pikpak_duration(video.get("duration")),
            encrypted=False,
            source_type=source_type,
            container=container,
            extractor="PikPak Share",
            formats=tuple(_pikpak_media_format(media, merged) for media in linked_medias),
            playlist_index=playlist_index if playlist_count > 1 else 0,
            playlist_count=playlist_count if playlist_count > 1 else 0,
            playlist_title="PikPak Share" if playlist_count > 1 else "",
            media_id=str(merged.get("id") or item.get("id") or ""),
        )

    def _request_json(
        self,
        method: str,
        url: str,
        *,
        params: Optional[dict[str, str]] = None,
        body: Optional[dict] = None,
    ) -> dict:
        if not self.captcha_token:
            self._refresh_captcha_token("GET:/drive/v1/share:batch_file_info")
        for attempt in range(2):
            response = self.session.request(
                method,
                url,
                params=params,
                json=body,
                headers=self._headers(),
                timeout=(10, 30),
            )
            payload = _pikpak_json_response(response)
            if _safe_int(str(payload.get("error_code") or 0)) == 9 and attempt == 0:
                self._refresh_captcha_token(_pikpak_action(method, url))
                continue
            if response.status_code >= 400 or payload.get("error"):
                detail = str(payload.get("error_description") or payload.get("error") or getattr(response, "reason", ""))
                raise HlsError(f"PikPak 请求失败：{redact_sensitive_text(detail)}")
            return payload
        raise HlsError("PikPak 匿名访问令牌已失效，请稍后重试。")

    def _refresh_captcha_token(self, action: str) -> None:
        timestamp = str(int(time.time() * 1000))
        body = {
            "action": action,
            "captcha_token": self.captcha_token,
            "client_id": PIKPAK_WEB_CLIENT_ID,
            "device_id": self.device_id,
            "meta": {
                "client_version": PIKPAK_WEB_CLIENT_VERSION,
                "package_name": PIKPAK_WEB_PACKAGE,
                "user_id": "",
                "timestamp": timestamp,
                "captcha_sign": _pikpak_captcha_sign(self.device_id, timestamp),
            },
        }
        response = self.session.request(
            "POST",
            PIKPAK_CAPTCHA_API,
            json=body,
            headers=self._headers(include_captcha=False),
            timeout=(10, 25),
        )
        payload = _pikpak_json_response(response)
        if payload.get("url"):
            raise HlsError("PikPak 需要人机验证。请在浏览器完成验证后，使用浏览器伴侣检测已加载媒体。")
        if response.status_code >= 400 or payload.get("error"):
            detail = str(payload.get("error_description") or payload.get("error") or getattr(response, "reason", ""))
            raise HlsError(f"PikPak 匿名访问初始化失败：{redact_sensitive_text(detail)}")
        token = str(payload.get("captcha_token") or "")
        if not token:
            raise HlsError("PikPak 未返回匿名访问令牌，请稍后重试。")
        self.captcha_token = token

    def _headers(self, include_captcha: bool = True) -> dict[str, str]:
        headers = make_headers("https://mypikpak.com/")
        headers.update({"X-Client-ID": PIKPAK_WEB_CLIENT_ID, "X-Device-ID": self.device_id})
        if include_captcha and self.captcha_token:
            headers["X-Captcha-Token"] = self.captcha_token
        return headers

    @staticmethod
    def _require_available_share(payload: dict) -> None:
        status = str(payload.get("share_status") or "OK")
        if status == "OK":
            return
        if status == "PASS_CODE_EMPTY":
            raise HlsError("PikPak 分享需要提取码。请展开高级选项输入提取码后重新解析。")
        if status == "PASS_CODE_ERROR":
            raise HlsError("PikPak 提取码错误。请检查提取码后重新解析。")
        detail = str(payload.get("share_status_text") or status)
        raise HlsError(f"PikPak 分享不可用：{redact_sensitive_text(detail)}")


def _pikpak_json_response(response: requests.Response) -> dict:
    try:
        payload = response.json()
    except (TypeError, ValueError) as exc:
        if response.status_code >= 400:
            response.raise_for_status()
        raise HlsError("PikPak 返回了无法解析的响应。") from exc
    if not isinstance(payload, dict):
        raise HlsError("PikPak 返回了无效的数据结构。")
    return payload


def _is_pikpak_video_file(item: dict) -> bool:
    mime_type = str(item.get("mime_type") or "").lower()
    extension = Path(str(item.get("name") or "")).suffix.lower()
    return mime_type.startswith("video/") or extension in DIRECT_VIDEO_EXTENSIONS


def _pikpak_media_url(media: dict) -> str:
    link = media.get("link")
    if not isinstance(link, dict):
        return ""
    return str(link.get("url") or "")


def _pikpak_media_score(media: dict) -> tuple[int, int, int, int]:
    video = media.get("video") if isinstance(media.get("video"), dict) else {}
    width = _safe_int(str(video.get("width") or 0))
    height = _safe_int(str(video.get("height") or 0))
    bitrate = _safe_int(str(video.get("bit_rate") or 0))
    return (int(bool(media.get("is_origin"))), width * height, bitrate, _safe_int(str(media.get("priority") or 0)))


def _pikpak_media_resolution(media: Optional[dict]) -> str:
    if not isinstance(media, dict):
        return ""
    resolution_name = str(media.get("resolution_name") or "")
    video = media.get("video") if isinstance(media.get("video"), dict) else {}
    width = _safe_int(str(video.get("width") or 0))
    height = _safe_int(str(video.get("height") or 0))
    return resolution_name or (f"{width}x{height}" if width and height else (f"{height}p" if height else ""))


def _pikpak_media_format(media: dict, file_info: dict) -> MediaFormat:
    video = media.get("video") if isinstance(media.get("video"), dict) else {}
    url = _pikpak_media_url(media)
    extension = Path(str(file_info.get("name") or "")).suffix.lstrip(".").lower()
    return MediaFormat(
        format_id=str(media.get("media_id") or media.get("media_name") or "pikpak"),
        ext=extension or _extension_from_url(url, "mp4"),
        resolution=_pikpak_media_resolution(media),
        width=_safe_int(str(video.get("width") or 0)),
        height=_safe_int(str(video.get("height") or 0)),
        fps=_safe_float(video.get("frame_rate")),
        vcodec=str(video.get("video_codec") or ""),
        acodec=str(video.get("audio_codec") or ""),
        tbr=_safe_float(video.get("bit_rate")) / 1000,
        filesize=_safe_int(str(file_info.get("size") or 0)) if media.get("is_origin") else 0,
        protocol=urlparse(url).scheme,
        has_video=True,
        has_audio=bool(video.get("audio_codec")),
    )


def _normalize_pikpak_duration(value: object) -> float:
    return _safe_float(value) / 1000


def _is_http_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _discover_pikpak_candidates(
    source_url: str,
    access_code: str,
    callback: Optional[EventCallback],
) -> list[VideoCandidate]:
    _emit(callback, "log", level="info", message="正在解析 PikPak 公开分享中的视频")
    return _PikPakShareClient(callback=callback).discover(source_url, access_code)


def _discover_baidupan_candidates(
    source_url: str,
    access_code: str,
    callback: Optional[EventCallback],
) -> list[VideoCandidate]:
    """Create one authorized batch-download candidate without scraping Baidu's private web APIs. @codex-comment"""

    canonical_url, _code = _baidupan_share_reference(source_url, access_code)
    title = _baidupan_share_title(source_url)
    _emit(callback, "log", level="info", message="已识别百度网盘分享，将通过本地授权连接器下载完整目录")
    return [
        VideoCandidate(
            title=f"{title} / 百度网盘",
            url=canonical_url,
            source_url=canonical_url,
            referer="https://pan.baidu.com/",
            segment_count=100,
            source_type="baidupan",
            container="folder",
            extractor="Baidu Netdisk Connector",
        )
    ]


def refresh_pikpak_candidate(
    candidate: VideoCandidate,
    source_url: str,
    access_code: str = "",
    callback: Optional[EventCallback] = None,
) -> VideoCandidate:
    """Refresh one PikPak candidate without storing its access code. @codex-comment"""

    if candidate.source_type != "pikpak" or not candidate.media_id:
        raise HlsError("当前 PikPak 条目缺少可刷新的文件标识，请重新解析分享。")
    _emit(callback, "log", level="info", message="PikPak 临时链接已失效，正在刷新后继续断点下载")
    return _PikPakShareClient(callback=callback).refresh_file(
        source_url or candidate.source_url,
        access_code,
        candidate.media_id,
    )


def discover_candidates(
    source_url: str,
    referer: str = "",
    callback: Optional[EventCallback] = None,
    access_code: str = "",
) -> list[VideoCandidate]:
    return _discover_candidates_impl(source_url, referer, callback, access_code)


def _discover_candidates_impl(
    source_url: str,
    referer: str = "",
    callback: Optional[EventCallback] = None,
    access_code: str = "",
) -> list[VideoCandidate]:
    """Route known providers before bounded generic webpage and yt-dlp discovery. @codex-comment"""

    source_url = source_url.strip()
    if not source_url:
        raise HlsError("请输入网页地址或 m3u8 地址")

    if _looks_like_baidupan_share_url(source_url):
        return _discover_baidupan_candidates(source_url, access_code, callback)

    if _looks_like_pikpak_share_url(source_url):
        return _discover_pikpak_candidates(source_url, access_code, callback)

    if _looks_like_youtube_url(source_url):
        return _discover_youtube_candidates(source_url, callback)

    parsed_source = urlparse(source_url)
    if parsed_source.scheme not in {"http", "https"} or not parsed_source.netloc:
        raise HlsError("请输入有效的 http 或 https 地址")

    preferred_ytdlp_error: Optional[HlsError] = None
    if not _looks_like_direct_video_url(source_url) and not _looks_like_playlist_url(source_url):
        if _has_specific_ytdlp_extractor(source_url):
            try:
                return _discover_ytdlp_candidates(source_url, callback)
            except HlsError as exc:
                preferred_ytdlp_error = exc
                _emit(
                    callback,
                    "log",
                    level="warning",
                    message="专用解析器未返回媒体，继续尝试网页扫描",
                )

    page_headers = make_headers(referer or _default_referer(source_url))
    direct_urls: list[str] = []
    if _looks_like_direct_video_url(source_url):
        return [_candidate_from_direct_url(source_url, source_url, referer=referer or _default_referer(source_url))]
    if _looks_like_playlist_url(source_url):
        unique_urls = [source_url]
        page_referer = referer
    else:
        try:
            text, page_headers = fetch_text_with_fallbacks(
                source_url,
                _header_candidates(source_url, referer=referer, source_url=source_url),
            )
            page_referer = page_headers.get("Referer") or source_url
            discovered_urls = find_m3u8_urls(source_url, text)
            discovered_urls.extend(_discover_urls_from_scripts(source_url, text, page_headers, callback))
            unique_urls = _dedupe(discovered_urls)
            direct_urls = find_direct_video_urls(source_url, text)
        except Exception as exc:
            page_referer = referer or source_url
            unique_urls = []
            _emit(
                callback,
                "log",
                level="warning",
                message=f"网页直接扫描未成功，切换通用解析器：{classify_error(exc).title}",
            )

    if not unique_urls and not direct_urls:
        if preferred_ytdlp_error is not None:
            raise preferred_ytdlp_error
        return _discover_ytdlp_candidates(source_url, callback)

    candidates: list[VideoCandidate] = []
    seen: set[str] = set()
    for video_url in direct_urls:
        candidate = _candidate_from_direct_url(video_url, source_url, referer=page_referer)
        if candidate.url not in seen:
            candidates.append(candidate)
            seen.add(candidate.url)

    for playlist_url in unique_urls:
        header_candidates = _header_candidates(playlist_url, referer=referer or page_referer, source_url=source_url)
        try:
            info, effective_headers = load_playlist_info_with_fallbacks(playlist_url, header_candidates)
        except Exception as exc:
            _emit(
                callback,
                "log",
                level="warning",
                message=f"跳过无效 m3u8：{redact_url(playlist_url)} ({redact_sensitive_text(str(exc))})",
            )
            continue

        effective_referer = effective_headers.get("Referer", "")
        if info.media:
            candidate = _candidate_from_media(info.media, source_url, referer=effective_referer)
            if candidate.url not in seen:
                candidates.append(candidate)
                seen.add(candidate.url)
            continue

        for variant in info.variants:
            try:
                variant_headers = _dedupe_headers(
                    [effective_headers]
                    + _header_candidates(variant.url, referer=effective_referer or page_referer, source_url=source_url)
                )
                media_info, media_headers = load_playlist_info_with_fallbacks(variant.url, variant_headers)
                if not media_info.media:
                    continue
                candidate = _candidate_from_media(
                    media_info.media,
                    source_url,
                    referer=media_headers.get("Referer", effective_referer),
                    bandwidth=variant.bandwidth,
                    resolution=variant.resolution,
                )
            except Exception:
                candidate = VideoCandidate(
                    title=_candidate_title(variant.url, variant.resolution, variant.bandwidth, 0),
                    url=variant.url,
                    source_url=source_url,
                    referer=effective_referer,
                    bandwidth=variant.bandwidth,
                    resolution=variant.resolution,
                )

            if candidate.url not in seen:
                candidates.append(candidate)
                seen.add(candidate.url)

    candidates = rank_candidates(candidates)
    if not candidates:
        if preferred_ytdlp_error is not None:
            raise preferred_ytdlp_error
        return _discover_ytdlp_candidates(source_url, callback)
    return candidates


def _looks_like_youtube_url(url: str) -> bool:
    parsed = urlparse(url.strip())
    host = parsed.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    return host in {"youtube.com", "m.youtube.com", "music.youtube.com", "youtu.be", "youtube-nocookie.com"} or host.endswith(
        ".youtube.com"
    )


def _discover_youtube_candidates(
    source_url: str,
    callback: Optional[EventCallback],
) -> list[VideoCandidate]:
    return _discover_ytdlp_candidates(source_url, callback, source_type="youtube")


def _discover_ytdlp_candidates(
    source_url: str,
    callback: Optional[EventCallback],
    source_type: str = "ytdlp",
) -> list[VideoCandidate]:
    if yt_dlp is None:
        raise HlsError("通用网页解析需要 yt-dlp，请先运行 python -m pip install -r requirements.txt")

    label = "YouTube" if source_type == "youtube" else "网页"
    _emit(callback, "log", level="info", message=f"正在使用 yt-dlp 解析{label}媒体")
    options = _ytdlp_base_options(source_url)
    options["extractor_args"] = _ytdlp_extractor_args(source_url)
    options.update(
        {
            "skip_download": True,
            "quiet": True,
            "no_warnings": True,
            "noplaylist": False,
        }
    )
    try:
        with yt_dlp.YoutubeDL(options) as ydl:
            info = ydl.extract_info(source_url, download=False)
    except Exception as exc:
        raise HlsError(f"通用媒体解析失败：{redact_sensitive_text(str(exc))}") from exc

    if not isinstance(info, dict):
        raise HlsError("通用解析器没有返回可下载的视频信息")
    raw_entries = info.get("entries")
    entries = raw_entries if isinstance(raw_entries, list) else None
    playlist_entries = [item for item in entries or [] if isinstance(item, dict)] if entries is not None else [info]
    if not playlist_entries:
        raise HlsError("播放列表中没有可下载的视频")

    playlist_title = str(info.get("title") or "") if entries is not None else ""
    playlist_count = len(playlist_entries) if entries is not None else 0
    return [
        _candidate_from_ytdlp_info(
            item,
            source_url=source_url,
            source_type=source_type,
            label=label,
            playlist_index=index if playlist_count else 0,
            playlist_count=playlist_count,
            playlist_title=playlist_title,
        )
        for index, item in enumerate(playlist_entries, start=1)
    ]


def _candidate_from_ytdlp_info(
    info: dict,
    source_url: str,
    source_type: str,
    label: str,
    playlist_index: int = 0,
    playlist_count: int = 0,
    playlist_title: str = "",
) -> VideoCandidate:
    """Normalize yt-dlp metadata and retain its stable public media ID for retries. @codex-comment"""

    title = sanitize_file_name(str(info.get("title") or info.get("id") or "video"), "video")
    resolution = _youtube_resolution(info)
    display_title = f"{title} / {label}"
    if playlist_index:
        display_title = f"{playlist_index:02d}. {display_title}"
    if resolution:
        display_title = f"{display_title} / {resolution}"

    media_url = str(info.get("webpage_url") or info.get("original_url") or info.get("url") or source_url)
    return VideoCandidate(
        title=display_title,
        url=media_url,
        source_url=source_url,
        referer=_default_referer(source_url),
        bandwidth=_youtube_bandwidth(info),
        resolution=resolution,
        segment_count=100,
        duration=_safe_float(info.get("duration")),
        encrypted=False,
        source_type=source_type,
        container=str(info.get("ext") or "mp4"),
        extractor=str(info.get("extractor_key") or info.get("extractor") or "yt-dlp"),
        formats=_normalize_ytdlp_formats(info.get("formats")),
        subtitles=_normalize_ytdlp_subtitles(info),
        playlist_index=playlist_index,
        playlist_count=playlist_count,
        playlist_title=playlist_title,
        media_id=str(info.get("id") or ""),
    )


def _normalize_ytdlp_formats(raw_formats: object) -> tuple[MediaFormat, ...]:
    if not isinstance(raw_formats, list):
        return ()
    formats: list[MediaFormat] = []
    for item in raw_formats:
        if not isinstance(item, dict):
            continue
        width = _safe_int(str(item.get("width") or 0))
        height = _safe_int(str(item.get("height") or 0))
        resolution = str(item.get("resolution") or "")
        if not resolution and height:
            resolution = f"{width}x{height}" if width else f"{height}p"
        vcodec = str(item.get("vcodec") or "")
        acodec = str(item.get("acodec") or "")
        formats.append(
            MediaFormat(
                format_id=str(item.get("format_id") or ""),
                ext=str(item.get("ext") or ""),
                resolution=resolution,
                width=width,
                height=height,
                fps=_safe_float(item.get("fps")),
                dynamic_range=str(item.get("dynamic_range") or ""),
                vcodec=vcodec,
                acodec=acodec,
                tbr=_safe_float(item.get("tbr") or item.get("vbr") or item.get("abr")),
                filesize=_safe_int(str(item.get("filesize") or item.get("filesize_approx") or 0)),
                protocol=str(item.get("protocol") or ""),
                has_video=bool(vcodec and vcodec != "none"),
                has_audio=bool(acodec and acodec != "none"),
            )
        )
    return tuple(formats)


def _normalize_ytdlp_subtitles(info: dict) -> tuple[SubtitleTrack, ...]:
    tracks: list[SubtitleTrack] = []
    for automatic, key in ((False, "subtitles"), (True, "automatic_captions")):
        source = info.get(key)
        if not isinstance(source, dict):
            continue
        for language, entries in source.items():
            if not isinstance(entries, list):
                continue
            formats = tuple(
                _dedupe(str(item.get("ext") or "") for item in entries if isinstance(item, dict) and item.get("ext"))
            )
            name = next((str(item.get("name") or "") for item in entries if isinstance(item, dict) and item.get("name")), "")
            tracks.append(SubtitleTrack(language=str(language), name=name, automatic=automatic, formats=formats))
    return tuple(tracks)


def find_m3u8_urls(base_url: str, text: str) -> list[str]:
    normalized = html.unescape(text)
    normalized = normalized.replace("\\/", "/").replace("\\u0026", "&")
    search_texts = [normalized]
    search_texts.extend(_decode_packed_javascript(normalized))
    patterns = [
        r"https?://[^\s'\"<>]+?\.m3u8(?:\?[^\s'\"<>]*)?",
        r"//[^\s'\"<>]+?\.m3u8(?:\?[^\s'\"<>]*)?",
        r"(?:\.{0,2}/|/)?[A-Za-z0-9_@%:;~./?=&+-]+?\.m3u8(?:\?[A-Za-z0-9_@%:;~./?=&+-]*)?",
    ]

    urls: list[str] = []
    for content in search_texts:
        for pattern in patterns:
            for match in re.finditer(pattern, content, flags=re.IGNORECASE):
                raw = match.group(0).strip(" \"'`),;")
                if not raw or raw.startswith("data:"):
                    continue
                urls.append(urljoin(base_url, raw))
    return _dedupe(urls)


def find_direct_video_urls(base_url: str, text: str) -> list[str]:
    normalized = html.unescape(text)
    normalized = normalized.replace("\\/", "/").replace("\\u0026", "&")
    extension_pattern = "|".join(re.escape(ext.lstrip(".")) for ext in sorted(DIRECT_VIDEO_EXTENSIONS))
    patterns = [
        rf"https?://[^\s'\"<>]+?\.({extension_pattern})(?:\?[^\s'\"<>]*)?",
        rf"//[^\s'\"<>]+?\.({extension_pattern})(?:\?[^\s'\"<>]*)?",
        rf"(?:\.{{0,2}}/|/)?[A-Za-z0-9_@%:;~./?=&+-]+?\.({extension_pattern})(?:\?[A-Za-z0-9_@%:;~./?=&+-]*)?",
    ]

    urls: list[str] = []
    for pattern in patterns:
        for match in re.finditer(pattern, normalized, flags=re.IGNORECASE):
            raw = match.group(0).strip(" \"'`),;")
            if not raw or raw.startswith("data:"):
                continue
            urls.append(urljoin(base_url, raw))
    return _dedupe(urls)


def candidate_score(candidate: VideoCandidate) -> tuple[int, int, int]:
    return (_resolution_area(candidate.resolution), candidate.bandwidth, candidate.segment_count)


def candidate_identity(candidate: VideoCandidate) -> tuple[str, str, str]:
    parsed = urlsplit(candidate.url)
    stable_url = urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), parsed.path, "", ""))
    return candidate.source_type, stable_url, candidate.resolution.lower()


def rank_candidates(candidates: Iterable[VideoCandidate]) -> list[VideoCandidate]:
    """Deduplicate signed URL variants and return best-quality media first."""

    selected: dict[tuple[str, str, str], VideoCandidate] = {}
    for candidate in candidates:
        identity = candidate_identity(candidate)
        current = selected.get(identity)
        if current is None or candidate_score(candidate) > candidate_score(current):
            selected[identity] = candidate
    return sorted(selected.values(), key=candidate_score, reverse=True)


def sanitize_file_name(value: str, default: str = "video") -> str:
    value = unquote(value).strip()
    value = re.sub(r"[\\/:*?\"<>|]+", "_", value)
    value = re.sub(r"\s+", " ", value)
    value = value.strip(" .")
    return value[:120] or default


def redact_url(value: str) -> str:
    """Remove credentials, query parameters, and fragments from a URL before persistence or logging."""

    try:
        parsed = urlsplit(value.strip())
    except ValueError:
        return ""
    if not parsed.scheme or not parsed.netloc:
        return value.split("?", 1)[0].split("#", 1)[0]
    host = parsed.hostname or ""
    try:
        port = parsed.port
    except ValueError:
        port = None
    if port:
        host = f"{host}:{port}"
    return urlunsplit((parsed.scheme, host, parsed.path, "", ""))


def redact_sensitive_text(value: str) -> str:
    text = str(value or "")
    text = re.sub(
        r"https?://[^\s'\"<>]+",
        lambda match: redact_url(match.group(0).rstrip("),.;")) + match.group(0)[len(match.group(0).rstrip("),.;")) :],
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"(?i)\b(authorization|cookie|set-cookie|token|access_token|signature|sig|pass_code|passcode|s_code|password|pwd)\s*[:=]\s*([^\s&;,]+)",
        r"\1=[已隐藏]",
        text,
    )
    return text[:1000]


def default_history_path() -> Path:
    base = Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local"))
    return base / "UniversalVideoDownloader" / "history.json"


def classify_error(error: object) -> UserFacingError:
    """Translate network and dependency failures into concise recovery guidance."""

    detail = redact_sensitive_text(str(error))
    lowered = detail.lower()
    if "百度网盘连接器未安装" in detail:
        return UserFacingError(
            "missing_baidupan_connector",
            "缺少百度网盘连接器",
            "当前安装中没有找到经过固定版本校验的百度网盘连接器。",
            "请使用完整的 Windows 便携包，或通过 BAIDUPCS_GO_PATH 指向已安装的 BaiduPCS-Go。",
            False,
            detail,
        )
    if "百度网盘连接器尚未登录" in detail or "登录状态不完整" in detail:
        return UserFacingError(
            "baidupan_login_required",
            "需要连接百度网盘账号",
            "分享转存和下载要求用户自己的百度网盘授权状态。",
            "展开高级选项并点击“连接百度网盘”，在独立终端完成登录后重试。",
            False,
            detail,
        )
    if "百度网盘分享需要提取码" in detail:
        return UserFacingError(
            "baidupan_code_required",
            "需要百度网盘提取码",
            "这个分享受四位提取码保护。",
            "展开高级选项输入提取码后重新解析。",
            False,
            detail,
        )
    if "百度网盘提取码" in detail:
        return UserFacingError(
            "baidupan_code_invalid",
            "百度网盘提取码不可用",
            "提取码格式不正确、输入错误，或分享已经失效。",
            "检查四位提取码并在浏览器确认分享仍可访问。",
            False,
            detail,
        )
    if "百度网盘分享不可用" in detail:
        return UserFacingError(
            "baidupan_share_unavailable",
            "百度网盘分享不可用",
            "分享可能已经取消、过期或限制访问。",
            "在浏览器确认分享仍可访问后再试。",
            False,
            detail,
        )
    if "百度网盘" in detail and any(marker in detail for marker in ("文件下载失败", "未完成文件", "未确认下载完成", "连接器执行失败")):
        return UserFacingError(
            "baidupan_download_interrupted",
            "百度网盘下载尚未完成",
            "连接器没有确认所有文件完整下载，已有断点会保留。",
            "保持原保存目录并重试；客户端会先重试当前分享，再继续后续队列。",
            True,
            detail,
        )
    if "百度网盘连接器无法" in detail:
        return UserFacingError(
            "baidupan_connector_error",
            "百度网盘连接器不可用",
            "连接器无法启动或不能写入所选保存目录。",
            "检查安全软件、连接器完整性和目录权限后重试。",
            False,
            detail,
        )
    if "直链续传重试已用尽" in detail:
        return UserFacingError(
            "direct_resume_exhausted",
            "下载中断，已保留恢复进度",
            "连接多次中断，因此尚未生成可播放的完整视频。",
            "重新解析原分享并再次下载；同名任务会从内部缓存的已有断点继续。",
            True,
            detail,
        )
    if "impersonat" in lowered and (
        "required impersonation dependency" in lowered
        or "no impersonate target" in lowered
        or "not available" in lowered
        or "curl_cffi" in lowered
    ):
        return UserFacingError(
            "missing_impersonation",
            "缺少浏览器兼容组件",
            "该网页要求浏览器 TLS 指纹兼容，但当前安装中没有可用的 curl_cffi 传输组件。",
            "重新安装最新版客户端；源码运行请执行 python -m pip install -r requirements.txt。",
            False,
            detail,
        )
    if "cloudflare anti-bot challenge" in lowered or "cf-mitigated" in lowered:
        return UserFacingError(
            "browser_verification_required",
            "网页需要浏览器验证",
            "站点仍返回 Cloudflare 验证页，解析器没有收到视频页面。",
            "先在浏览器完成验证并播放几秒，再使用浏览器伴侣检测当前页面媒体。",
            False,
            detail,
        )
    if "pikpak" in lowered and "需要提取码" in detail:
        return UserFacingError(
            "pikpak_code_required",
            "需要 PikPak 提取码",
            "这个分享受提取码保护。",
            "展开高级选项，输入分享提取码后重新解析。",
            False,
            detail,
        )
    if "pikpak" in lowered and "提取码错误" in detail:
        return UserFacingError(
            "pikpak_code_invalid",
            "PikPak 提取码错误",
            "分享链接可以访问，但提供的提取码不正确。",
            "检查提取码后在高级选项中重新输入。",
            False,
            detail,
        )
    if "pikpak" in lowered and "人机验证" in detail:
        return UserFacingError(
            "pikpak_verification_required",
            "PikPak 需要浏览器验证",
            "PikPak 暂时要求完成人机验证。",
            "先在浏览器打开分享并完成验证，再通过浏览器伴侣检测媒体。",
            False,
            detail,
        )
    if "pikpak" in lowered and "分享不可用" in detail:
        return UserFacingError(
            "pikpak_share_unavailable",
            "PikPak 分享不可用",
            "分享可能已过期、取消或受到访问限制。",
            "在浏览器确认分享仍可访问后再试。",
            False,
            detail,
        )
    if isinstance(error, requests.Timeout) or "timed out" in lowered or "timeout" in lowered:
        return UserFacingError("network_timeout", "连接超时", "服务器在限定时间内没有响应。", "检查网络后重试，或降低并发任务数。", True, detail)
    if isinstance(error, requests.ConnectionError) or "connection" in lowered and "failed" in lowered:
        return UserFacingError("network_unreachable", "无法连接服务器", "网络连接没有建立。", "确认地址可在浏览器访问，并检查代理、防火墙或 DNS。", True, detail)

    response = getattr(error, "response", None)
    status_code = int(getattr(response, "status_code", 0) or 0)
    match = re.search(r"\b(401|403|404|429|5\d\d)\b", detail)
    if not status_code and match:
        status_code = int(match.group(1))
    if status_code in {401, 403}:
        return UserFacingError("access_denied", "资源拒绝访问", "服务器要求有效的访问上下文，或链接已经过期。", "先在浏览器确认你有权访问；必要时更新 Referer 后重试。", False, detail)
    if status_code == 404:
        return UserFacingError("not_found", "资源不存在", "视频地址已失效或被移动。", "返回原视频页面重新解析最新地址。", False, detail)
    if status_code == 429:
        return UserFacingError("rate_limited", "请求过于频繁", "服务器暂时限制了请求。", "稍后重试，并降低并发任务数。", True, detail)
    if status_code >= 500:
        return UserFacingError("server_error", "视频服务器异常", "远端服务暂时不可用。", "保留续传缓存，稍后直接重试。", True, detail)
    if "yt-dlp" in lowered and ("install" in lowered or "需要" in detail):
        return UserFacingError("missing_ytdlp", "缺少通用解析组件", "当前安装中没有可用的 yt-dlp。", "重新运行安装程序或执行 requirements.txt 依赖安装。", False, detail)
    if "ffmpeg" in lowered:
        return UserFacingError("missing_ffmpeg", "缺少音视频合并组件", "当前任务需要 ffmpeg 才能生成完整文件。", "安装 ffmpeg 并加入 PATH 后重试。", False, detail)
    if isinstance(error, PermissionError) or "permission denied" in lowered:
        return UserFacingError("permission_denied", "无法写入文件", "保存目录没有写入权限，或文件正在被占用。", "更换保存目录，或关闭正在使用该文件的程序。", False, detail)
    if "no space left" in lowered or "磁盘空间" in detail:
        return UserFacingError("disk_full", "磁盘空间不足", "目标磁盘没有足够空间。", "释放空间或更换保存目录后继续任务。", False, detail)
    if isinstance(error, PlaylistParseError) or "m3u8" in lowered and "解析" in detail:
        return UserFacingError("playlist_invalid", "播放列表无法解析", "链接可能已经过期，或不是标准 HLS 播放列表。", "返回视频页面重新解析，或检查 Referer。", False, detail)
    return UserFacingError("unknown", "任务未完成", "下载器遇到未分类的错误。", "查看活动日志中的技术详情后重试。", True, detail)


class DownloadJob:
    def __init__(
        self,
        playlist: MediaPlaylist,
        output_path: Path,
        headers: Optional[dict[str, str]] = None,
        concurrency: int = 8,
        retries: int = 3,
        keep_cache: bool = True,
        callback: Optional[EventCallback] = None,
        resume_key: str = "",
    ) -> None:
        """Prepare an HLS job and adopt resumable data for the same stable media/output identity. @codex-comment"""

        self.playlist = playlist
        self.output_path = output_path
        self.headers = headers or make_headers()
        self.concurrency = max(1, min(32, concurrency))
        self.retries = max(1, retries)
        self.keep_cache = keep_cache
        self.callback = callback
        self.pause_event = threading.Event()
        self.pause_event.set()
        self.stop_event = threading.Event()
        self.lock = threading.Lock()
        self.status: dict[int, str] = {}
        self.errors: dict[int, str] = {}
        self.key_cache: dict[str, bytes] = {}
        self.bytes_done = 0
        self.resume_key = resume_key or redact_url(playlist.url)
        self.cache_dir = self._cache_root()
        self._adopt_legacy_cache()
        self.segment_dir = self.cache_dir / "segments"
        self.manifest_path = self.cache_dir / "manifest.json"

    def pause(self) -> None:
        self.pause_event.clear()
        _emit(self.callback, "paused")

    def resume(self) -> None:
        self.pause_event.set()
        _emit(self.callback, "resumed")

    def stop(self) -> None:
        self.stop_event.set()
        self.pause_event.set()
        _emit(self.callback, "stopping")

    def run(self) -> None:
        try:
            self._prepare()
            pending = [segment.index for segment in self.playlist.segments if self.status.get(segment.index) != "done"]
            _emit(
                self.callback,
                "started",
                total=len(self.playlist.segments),
                pending=len(pending),
                cache_dir=str(self.cache_dir),
            )
            for index, status in sorted(self.status.items()):
                _emit(self.callback, "segment", index=index, status=status)
            self._emit_progress()

            queue: Queue[int] = Queue()
            for index in pending:
                queue.put(index)

            workers = [
                threading.Thread(target=self._worker, args=(queue,), daemon=True)
                for _ in range(min(self.concurrency, max(1, len(pending))))
            ]
            for worker in workers:
                worker.start()
            for worker in workers:
                worker.join()

            if self.stop_event.is_set():
                self._write_manifest()
                _emit(self.callback, "stopped")
                return

            failed = [idx for idx, value in self.status.items() if value == "error"]
            missing = [segment.index for segment in self.playlist.segments if self.status.get(segment.index) != "done"]
            if failed or missing:
                self._write_manifest()
                _emit(self.callback, "failed", failed=len(failed), missing=len(missing))
                return

            self.combine(require_all=True)
            if not self.keep_cache:
                shutil.rmtree(self.cache_dir, ignore_errors=True)
            _emit(self.callback, "completed", output=str(self.output_path))
        except Exception as exc:
            _emit(self.callback, "fatal", message=str(exc))

    def combine(self, require_all: bool = True, partial_suffix: str = ".partial") -> Path:
        self.output_path.parent.mkdir(parents=True, exist_ok=True)
        target = self.output_path
        segments = self.playlist.segments
        if require_all:
            missing = [segment.index for segment in segments if not self._segment_path(segment).exists()]
            if missing:
                raise HlsError(f"还有 {len(missing)} 个分片未下载，不能合并完整文件")
        else:
            stem = self.output_path.stem + partial_suffix
            target = self.output_path.with_name(stem + self.output_path.suffix)

        temp_output = target.with_suffix(target.suffix + ".part")
        _emit(self.callback, "combining", output=str(target), partial=not require_all)
        with temp_output.open("wb") as output:
            for segment in segments:
                path = self._segment_path(segment)
                if not path.exists():
                    if require_all:
                        raise HlsError(f"缺少分片：{segment.index}")
                    continue
                with path.open("rb") as item:
                    shutil.copyfileobj(item, output, length=1024 * 1024)
        temp_output.replace(target)
        _emit(self.callback, "combined", output=str(target), partial=not require_all)
        return target

    def _prepare(self) -> None:
        self.output_path.parent.mkdir(parents=True, exist_ok=True)
        self.segment_dir.mkdir(parents=True, exist_ok=True)
        bytes_done = 0
        for segment in self.playlist.segments:
            path = self._segment_path(segment)
            self.status[segment.index] = "done" if path.exists() and path.stat().st_size > 0 else "pending"
            if self.status[segment.index] == "done":
                bytes_done += path.stat().st_size
        with self.lock:
            self.bytes_done = bytes_done
        self._write_manifest()

    def _worker(self, queue: Queue[int]) -> None:
        segment_by_index = {segment.index: segment for segment in self.playlist.segments}
        while not self.stop_event.is_set():
            self.pause_event.wait(0.2)
            if not self.pause_event.is_set():
                continue
            try:
                index = queue.get_nowait()
            except Empty:
                return

            segment = segment_by_index[index]
            try:
                if self.status.get(index) == "done":
                    continue
                self._set_status(index, "downloading")
                self._download_with_retries(segment)
                self._set_status(index, "done")
            except Exception as exc:
                self.errors[index] = str(exc)
                self._set_status(index, "error")
                _emit(self.callback, "log", level="error", message=f"分片 {index + 1} 下载失败：{exc}")
            finally:
                queue.task_done()

    def _download_with_retries(self, segment: Segment) -> None:
        last_error: Optional[Exception] = None
        for attempt in range(1, self.retries + 1):
            if self.stop_event.is_set():
                raise HlsError("任务已停止")
            try:
                self._download_segment(segment)
                return
            except Exception as exc:
                last_error = exc
                if attempt < self.retries and not self.stop_event.is_set():
                    time.sleep(0.6 * attempt)
        raise last_error or HlsError("未知下载错误")

    def _download_segment(self, segment: Segment) -> None:
        final_path = self._segment_path(segment)
        if final_path.exists() and final_path.stat().st_size > 0:
            return

        part_path = final_path.with_suffix(final_path.suffix + ".part")
        if segment.byte_range:
            fetch_binary_range(
                segment.url,
                part_path,
                segment.byte_range,
                headers=self.headers,
                stop_event=self.stop_event,
            )
        else:
            fetch_binary(segment.url, part_path, headers=self.headers, stop_event=self.stop_event)

        if segment.key:
            data = part_path.read_bytes()
            decrypted = self._decrypt_segment(segment, data)
            final_path.write_bytes(decrypted)
            part_path.unlink(missing_ok=True)
        else:
            part_path.replace(final_path)
        with self.lock:
            self.bytes_done += final_path.stat().st_size

    def _decrypt_segment(self, segment: Segment, data: bytes) -> bytes:
        if AES is None or unpad is None:
            raise HlsError("该视频使用 AES-128 加密，请先安装 pycryptodome")
        if not segment.key:
            return data
        key = self._fetch_key(segment.key.uri)
        iv = bytes.fromhex(segment.key.iv_hex or "0" * 32)
        cipher = AES.new(key, AES.MODE_CBC, iv)
        decrypted = cipher.decrypt(data)
        try:
            return unpad(decrypted, AES.block_size)
        except ValueError:
            return decrypted

    def _fetch_key(self, url: str) -> bytes:
        with self.lock:
            cached = self.key_cache.get(url)
        if cached:
            return cached
        with _http_session().get(url, headers=self.headers, timeout=(10, 20)) as response:
            response.raise_for_status()
            key = response.content
        if len(key) != 16:
            raise HlsError(f"AES key 长度异常：{redact_url(url)}")
        with self.lock:
            self.key_cache[url] = key
        return key

    def _set_status(self, index: int, status: str) -> None:
        with self.lock:
            self.status[index] = status
        _emit(self.callback, "segment", index=index, status=status)
        self._emit_progress()

    def _emit_progress(self) -> None:
        with self.lock:
            values = list(self.status.values())
            bytes_done = self.bytes_done
        done = values.count("done")
        failed = values.count("error")
        downloading = values.count("downloading")
        _emit(
            self.callback,
            "progress",
            done=done,
            failed=failed,
            downloading=downloading,
            total=len(self.playlist.segments),
            bytes_done=bytes_done,
        )

    def _write_manifest(self) -> None:
        payload = {
            "version": 1,
            "playlist_url": self.playlist.url,
            "output_path": str(self.output_path),
            "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "segments": [asdict(segment) for segment in self.playlist.segments],
            "status": self.status,
            "errors": self.errors,
        }
        self.manifest_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def _segment_path(self, segment: Segment) -> Path:
        return self.segment_dir / segment.file_name

    def _cache_root(self) -> Path:
        """Derive an HLS cache location that survives temporary URL signature changes. @codex-comment"""

        source = f"{self.resume_key}|{self.output_path.resolve()}"
        digest = hashlib.sha1(source.encode("utf-8")).hexdigest()[:16]
        return self.output_path.parent / ".m3u8_resume" / digest

    def _adopt_legacy_cache(self) -> None:
        """Move a URL-keyed legacy cache for this output into the stable retry location. @codex-comment"""

        if self.cache_dir.exists():
            return
        cache_parent = self.cache_dir.parent
        if not cache_parent.is_dir():
            return

        legacy_source = f"{self.playlist.url}|{self.output_path.resolve()}"
        legacy_digest = hashlib.sha1(legacy_source.encode("utf-8")).hexdigest()[:16]
        exact_legacy = cache_parent / legacy_digest
        candidates: list[Path] = [exact_legacy] if exact_legacy.is_dir() else []
        expected_output = os.path.normcase(os.path.abspath(self.output_path))
        expected_playlist = redact_url(self.playlist.url)
        for directory in cache_parent.iterdir():
            if not directory.is_dir() or directory in {self.cache_dir, exact_legacy}:
                continue
            manifest = directory / "manifest.json"
            try:
                payload = json.loads(manifest.read_text(encoding="utf-8"))
                manifest_output = os.path.normcase(os.path.abspath(str(payload.get("output_path") or "")))
                manifest_playlist = redact_url(str(payload.get("playlist_url") or ""))
            except (OSError, ValueError, TypeError):
                continue
            if manifest_output == expected_output and manifest_playlist == expected_playlist:
                candidates.append(directory)
        if not candidates:
            return

        source = max(candidates, key=lambda path: path.stat().st_mtime)
        try:
            source.replace(self.cache_dir)
        except OSError:
            self.cache_dir = source


class BaiduPanDownloadJob:
    """Run an authorized Baidu share transfer/download through the pinned local connector. @codex-comment"""

    progress_total = 100

    def __init__(
        self,
        source_url: str,
        output_dir: Path,
        access_code: str = "",
        callback: Optional[EventCallback] = None,
        connector_path: str | Path | None = None,
    ) -> None:
        self.source_url = source_url
        self.output_dir = output_dir
        self.access_code = access_code
        self.callback = callback
        self.connector_path = Path(connector_path) if connector_path else None
        self.pause_event = threading.Event()
        self.pause_event.set()
        self.stop_event = threading.Event()
        self.lock = threading.Lock()
        self.process: Optional[subprocess.Popen[str]] = None
        self.last_done = 0

    def pause(self) -> None:
        _emit(self.callback, "log", level="warning", message="百度网盘连接器不支持安全暂停；可停止任务后重新下载并续传。")

    def resume(self) -> None:
        _emit(self.callback, "resumed")

    def stop(self) -> None:
        self.stop_event.set()
        with self.lock:
            process = self.process
        if process is not None and process.poll() is None:
            process.terminate()
        _emit(self.callback, "stopping")

    def combine(self, require_all: bool = True, partial_suffix: str = ".partial") -> Path:
        _emit(self.callback, "log", level="info", message="百度网盘连接器会直接生成完整文件，不需要手动合并。")
        return self.output_dir

    def run(self) -> None:
        """Validate authorization, download the complete share, and emit queue-compatible events. @codex-comment"""

        try:
            canonical_url, code = _baidupan_share_reference(self.source_url, self.access_code)
            connector = find_baidupcs_executable(self.connector_path)
            if connector is None:
                raise HlsError("百度网盘连接器未安装")
            self.output_dir.mkdir(parents=True, exist_ok=True)
            incomplete_before = _snapshot_incomplete_files(self.output_dir)
            _emit(
                self.callback,
                "started",
                total=self.progress_total,
                pending=self.progress_total,
                cache_dir=str(self.output_dir),
            )
            self._emit_progress(0, downloading=1)

            who_code, who_output = self._run_connector(connector, ["who"])
            if who_code != 0 or re.search(r"uid:\s*0\b", who_output) or "未设置任何百度帐号" in who_output:
                raise HlsError("百度网盘连接器尚未登录")

            config_code, config_output = self._run_connector(
                connector,
                ["config", "set", "-savedir", str(self.output_dir)],
            )
            if config_code != 0 or _baidupan_output_failed(config_output):
                raise HlsError("百度网盘连接器无法设置保存目录")

            transfer_args = ["transfer", "--download", "--collect", canonical_url]
            if code:
                transfer_args.append(code)
            transfer_code, transfer_output = self._run_connector(connector, transfer_args, emit_progress=True)
            if self.stop_event.is_set():
                _emit(self.callback, "stopped")
                return
            if transfer_code != 0 or _baidupan_output_failed(transfer_output):
                raise _baidupan_error_from_output(transfer_output, bool(code))
            if "分享链接转存到网盘成功" not in transfer_output or "下载结束" not in transfer_output:
                raise HlsError("百度网盘连接器未确认下载完成")

            incomplete_after = _snapshot_incomplete_files(self.output_dir)
            changed_incomplete = {
                path
                for path, signature in incomplete_after.items()
                if incomplete_before.get(path) != signature
            }
            if changed_incomplete:
                raise HlsError("百度网盘下载仍有未完成文件，已保留断点")

            self._mark_done(self.progress_total)
            self._emit_progress(self.progress_total, downloading=0)
            _emit(self.callback, "completed", output=str(self.output_dir))
        except Exception as exc:
            if self.stop_event.is_set():
                _emit(self.callback, "stopped")
                return
            _emit(self.callback, "fatal", message=redact_sensitive_text(str(exc)))

    def _run_connector(
        self,
        connector: Path,
        arguments: list[str],
        emit_progress: bool = False,
    ) -> tuple[int, str]:
        """Execute one command without a shell and retain bounded diagnostic output. @codex-comment"""

        try:
            process = subprocess.Popen(
                [str(connector), *arguments],
                cwd=self.output_dir,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except OSError as exc:
            raise HlsError("百度网盘连接器无法启动") from exc
        with self.lock:
            self.process = process
        output_lines: deque[str] = deque(maxlen=400)
        try:
            if process.stdout is not None:
                for raw_line in process.stdout:
                    line = raw_line.strip()
                    if line:
                        output_lines.append(line)
                        if emit_progress and ("↓" in line or "%" in line):
                            self._mark_done(min(95, self.last_done + 1))
                            self._emit_progress(self.last_done, downloading=1)
                    if self.stop_event.is_set() and process.poll() is None:
                        process.terminate()
                        break
            return_code = process.wait()
        finally:
            with self.lock:
                if self.process is process:
                    self.process = None
        return return_code, "\n".join(output_lines)

    def _mark_done(self, done: int) -> None:
        done = max(0, min(self.progress_total, done))
        start = self.last_done
        if done <= start:
            return
        self.last_done = done
        for index in range(start, done):
            _emit(self.callback, "segment", index=index, status="done")

    def _emit_progress(self, done: int, downloading: int) -> None:
        _emit(
            self.callback,
            "progress",
            done=done,
            failed=0,
            downloading=downloading,
            total=self.progress_total,
            bytes_done=0,
        )


def _snapshot_incomplete_files(root: Path) -> dict[Path, tuple[int, int]]:
    """Capture resumable temporary files so newly changed leftovers can block completion. @codex-comment"""

    result: dict[Path, tuple[int, int]] = {}
    for path in root.rglob("*"):
        if not path.is_file() or not path.name.lower().endswith(BAIDUPAN_INCOMPLETE_SUFFIXES):
            continue
        try:
            stat_result = path.stat()
        except OSError:
            continue
        result[path.resolve()] = (stat_result.st_size, stat_result.st_mtime_ns)
    return result


def _baidupan_output_failed(output: str) -> bool:
    """Detect connector failure markers because its legacy CLI may still exit zero. @codex-comment"""

    return any(marker in output for marker in ("失败:", "以下文件下载失败", "panic:", "fatal error"))


def _baidupan_error_from_output(output: str, has_code: bool) -> HlsError:
    """Map connector text to bounded errors without returning account or file-list output. @codex-comment"""

    lowered = output.lower()
    if "未设置任何百度帐号" in output or "请先登录" in output or "stoken" in lowered:
        return HlsError("百度网盘连接器尚未登录或登录状态不完整")
    if "链接地址或提取码非法" in output or "提取码" in output:
        return HlsError("百度网盘提取码错误或分享已失效" if has_code else "百度网盘分享需要提取码")
    if any(marker in output for marker in ("分享已取消", "分享不存在", "页面不存在", "分享的文件已经被取消")):
        return HlsError("百度网盘分享不可用")
    if "以下文件下载失败" in output:
        return HlsError("百度网盘文件下载失败，连接器已保留断点")
    return HlsError("百度网盘连接器执行失败")


class YouTubeDownloadJob:
    progress_total = 100

    def __init__(
        self,
        url: str,
        output_path: Path,
        concurrency: int = 4,
        referer: str = "",
        callback: Optional[EventCallback] = None,
        preferences: Optional[DownloadPreferences] = None,
        overwrite_existing: bool = False,
    ) -> None:
        self.url = url
        self.output_path = output_path
        self.concurrency = max(1, min(16, concurrency))
        self.referer = referer
        self.callback = callback
        self.preferences = preferences or DownloadPreferences()
        self.overwrite_existing = overwrite_existing
        self.pause_event = threading.Event()
        self.pause_event.set()
        self.stop_event = threading.Event()
        self.cache_dir = self._cache_root()
        self.lock = threading.Lock()
        self.last_done = 0
        self.max_bytes_done = 0
        self.last_filename: Optional[Path] = None

    def pause(self) -> None:
        _emit(self.callback, "log", level="warning", message="YouTube 下载由 yt-dlp 管理；如需中断，请点击停止，稍后可继续续传。")

    def resume(self) -> None:
        _emit(self.callback, "resumed")

    def stop(self) -> None:
        self.stop_event.set()
        self.pause_event.set()
        _emit(self.callback, "stopping")

    def combine(self, require_all: bool = True, partial_suffix: str = ".partial") -> Path:
        _emit(self.callback, "log", level="info", message="YouTube 下载完成后会自动合并音视频，不需要手动合并。")
        return self._find_output_file()

    def run(self) -> None:
        if yt_dlp is None:
            _emit(self.callback, "fatal", message="YouTube 支持需要 yt-dlp，请先运行 python -m pip install -r requirements.txt")
            return

        try:
            self.output_path.parent.mkdir(parents=True, exist_ok=True)
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            _emit(
                self.callback,
                "started",
                total=self.progress_total,
                pending=self.progress_total,
                cache_dir=str(self.cache_dir),
            )
            self._emit_progress(done=0, downloading=1, bytes_done=0)

            capability = ffmpeg_capability()
            options = build_ytdlp_options(self.url, self.referer, self.preferences, capability.available)
            if capability.available:
                _emit(self.callback, "log", level="info", message=f"FFmpeg 已就绪：{capability.path}")
            elif self.preferences.embed_subtitles:
                _emit(self.callback, "log", level="warning", message="未检测到 FFmpeg，字幕将单独保存，无法嵌入视频。")
            options["extractor_args"] = _ytdlp_extractor_args(self.url)
            options.update(
                {
                    "outtmpl": self._output_template(),
                    "cachedir": str(self.cache_dir),
                    "continuedl": not self.overwrite_existing,
                    "noplaylist": True,
                    "quiet": True,
                    "no_warnings": True,
                    "progress_hooks": [self._progress_hook],
                    "concurrent_fragment_downloads": self.concurrency,
                    "retries": 10,
                    "fragment_retries": 10,
                    "file_access_retries": 10,
                }
            )
            if self.overwrite_existing:
                options["overwrites"] = True
            with yt_dlp.YoutubeDL(options) as ydl:
                ydl.download([self.url])

            if self.stop_event.is_set():
                _emit(self.callback, "stopped")
                return

            self._mark_done(self.progress_total)
            output = self._find_output_file()
            self._emit_progress(done=self.progress_total, downloading=0, bytes_done=self.max_bytes_done)
            _emit(self.callback, "completed", output=str(output))
        except Exception as exc:
            if self.stop_event.is_set():
                _emit(self.callback, "stopped")
                return
            _emit(self.callback, "fatal", message=f"媒体下载失败：{redact_sensitive_text(str(exc))}")

    def _progress_hook(self, data: dict) -> None:
        if self.stop_event.is_set():
            raise HlsError("任务已停止")

        status = data.get("status")
        filename = data.get("filename")
        if filename:
            self.last_filename = Path(str(filename))

        downloaded = int(data.get("downloaded_bytes") or 0)
        total = int(data.get("total_bytes") or data.get("total_bytes_estimate") or 0)
        with self.lock:
            self.max_bytes_done = max(self.max_bytes_done, downloaded)
            bytes_done = self.max_bytes_done

        if status == "downloading":
            percent = self.last_done
            if total > 0:
                percent = int(downloaded / total * self.progress_total)
            percent = max(self.last_done, min(self.progress_total - 1, percent))
            self._mark_done(percent)
            _emit(self.callback, "segment", index=percent, status="downloading")
            self._emit_progress(done=percent, downloading=1, bytes_done=bytes_done)
        elif status == "finished":
            self._mark_done(self.progress_total - 1)
            self._emit_progress(done=self.progress_total - 1, downloading=1, bytes_done=bytes_done)
            _emit(self.callback, "combining", output=str(self.output_path), partial=False)

    def _mark_done(self, done: int) -> None:
        done = max(0, min(self.progress_total, done))
        with self.lock:
            start = self.last_done
            if done <= start:
                return
            self.last_done = done
        for index in range(start, done):
            _emit(self.callback, "segment", index=index, status="done")

    def _emit_progress(self, done: int, downloading: int, bytes_done: int) -> None:
        _emit(
            self.callback,
            "progress",
            done=done,
            failed=0,
            downloading=downloading,
            total=self.progress_total,
            bytes_done=bytes_done,
        )

    def _output_template(self) -> str:
        return str(self.output_path.with_suffix("")) + ".%(ext)s"

    def _find_output_file(self) -> Path:
        if self.output_path.exists():
            return self.output_path
        if self.last_filename and self.last_filename.exists():
            return self.last_filename

        stem = self.output_path.stem
        candidates = [
            path
            for path in self.output_path.parent.glob(stem + ".*")
            if path.is_file()
            and not path.name.endswith((".part", ".ytdl", ".temp"))
            and path.suffix.lower() in {".mp4", ".mkv", ".webm", ".mov", ".m4v"}
        ]
        if candidates:
            return max(candidates, key=lambda path: path.stat().st_mtime)
        return self.output_path

    def _cache_root(self) -> Path:
        source = f"{self.url}|{self.output_path.resolve()}"
        digest = hashlib.sha1(source.encode("utf-8")).hexdigest()[:16]
        return self.output_path.parent / ".youtube_resume" / digest


class DirectDownloadJob:
    progress_total = 100

    def __init__(
        self,
        url: str,
        output_path: Path,
        headers: Optional[dict[str, str]] = None,
        callback: Optional[EventCallback] = None,
        url_refresher: Optional[Callable[[], str]] = None,
        resume_key: str = "",
        retries: int = DIRECT_DOWNLOAD_RETRIES,
        retry_backoff_seconds: float = DIRECT_RETRY_BACKOFF_SECONDS,
    ) -> None:
        self.url = url
        self.output_path = output_path
        self.headers = headers or make_headers(_default_referer(url))
        self.callback = callback
        self.url_refresher = url_refresher
        self.resume_key = resume_key or redact_url(url)
        self.retries = max(0, min(10, retries))
        self.retry_backoff_seconds = max(0.0, min(10.0, retry_backoff_seconds))
        self.pause_event = threading.Event()
        self.pause_event.set()
        self.stop_event = threading.Event()
        self.cache_dir = self._cache_root()
        self.lock = threading.Lock()
        self.last_done = 0

    def pause(self) -> None:
        self.pause_event.clear()
        _emit(self.callback, "paused")

    def resume(self) -> None:
        self.pause_event.set()
        _emit(self.callback, "resumed")

    def stop(self) -> None:
        self.stop_event.set()
        self.pause_event.set()
        _emit(self.callback, "stopping")

    def combine(self, require_all: bool = True, partial_suffix: str = ".partial") -> Path:
        _emit(self.callback, "log", level="info", message="直链视频下载完成后就是完整文件，不需要手动合并。")
        return self.output_path

    def run(self) -> None:
        try:
            self.output_path.parent.mkdir(parents=True, exist_ok=True)
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            _emit(
                self.callback,
                "started",
                total=self.progress_total,
                pending=self.progress_total,
                cache_dir=str(self.cache_dir),
            )
            self._download()
            if self.stop_event.is_set():
                _emit(self.callback, "stopped")
                return
            self._mark_done(self.progress_total)
            self._emit_progress(self.progress_total, downloading=0, bytes_done=self.output_path.stat().st_size)
            _emit(self.callback, "completed", output=str(self.output_path))
        except Exception as exc:
            if self.stop_event.is_set():
                _emit(self.callback, "stopped")
                return
            _emit(self.callback, "fatal", error=exc, message=f"直链下载失败：{redact_sensitive_text(str(exc))}")

    def _download(self) -> None:
        resume_path = self._resume_path()
        self._migrate_legacy_part(resume_path)
        last_error: Optional[Exception] = None
        for attempt in range(self.retries + 1):
            if self.stop_event.is_set():
                return
            try:
                self._download_attempt(resume_path)
                if self.stop_event.is_set():
                    return
                resume_path.replace(self.output_path)
                self._remove_empty_resume_dirs()
                return
            except Exception as exc:
                last_error = exc
                retryable = _is_retryable_direct_error(exc, can_refresh=self.url_refresher is not None)
                if not retryable:
                    raise
                if attempt >= self.retries:
                    detail = redact_sensitive_text(str(exc))
                    raise HlsError(f"直链续传重试已用尽，已保留内部续传缓存：{detail}") from exc

                next_attempt = attempt + 2
                _emit(
                    self.callback,
                    "log",
                    level="warning",
                    message=f"连接中断，保留已下载内容并准备第 {next_attempt} 次续传",
                )
                if self.url_refresher and (_direct_error_requires_refresh(exc) or attempt >= 1):
                    try:
                        refreshed_url = self.url_refresher()
                        if not _is_http_url(refreshed_url):
                            raise HlsError("刷新后的下载地址无效")
                        self.url = refreshed_url
                    except Exception as refresh_exc:
                        last_error = refresh_exc
                        _emit(
                            self.callback,
                            "log",
                            level="warning",
                            message=f"临时链接刷新失败，将继续有限重试：{redact_sensitive_text(str(refresh_exc))}",
                        )

                delay = min(8.0, self.retry_backoff_seconds * (2**attempt))
                if delay and self.stop_event.wait(delay):
                    return

        raise last_error or HlsError("直链下载未完成，已保留内部续传缓存。")

    def _download_attempt(self, part_path: Path) -> None:
        """Append validated bounded Range responses until the declared media size is complete. @codex-comment"""

        start_at = part_path.stat().st_size if part_path.exists() else 0
        while not self.stop_event.is_set():
            requested_end = start_at + DIRECT_RANGE_CHUNK_BYTES - 1
            request_headers = dict(self.headers)
            request_headers["Accept-Encoding"] = "identity"
            request_headers["Range"] = f"bytes={start_at}-{requested_end}"

            with _http_session().get(self.url, headers=request_headers, stream=True, timeout=(10, 45)) as response:
                if response.status_code == 416:
                    total_size = _response_unsatisfied_total(response)
                    if total_size == start_at:
                        return
                    raise DirectResumeError("服务器拒绝当前断点范围，已保留 .part 文件等待刷新链接。")
                response.raise_for_status()

                bounds = _response_range_bounds(response) if response.status_code == 206 else None
                if start_at and response.status_code != 206:
                    raise DirectResumeError("服务器未接受断点续传范围，已保留 .part 文件。")
                if bounds is not None and bounds[0] != start_at:
                    raise DirectResumeError("服务器返回的续传范围与本地断点不匹配，已保留 .part 文件。")
                if bounds is not None and bounds[1] > requested_end:
                    raise DirectResumeError("服务器返回的数据超过请求范围，拒绝追加到断点文件。")
                if response.status_code == 206 and bounds is None:
                    raise DirectResumeError("服务器返回了无效的续传范围，已保留 .part 文件。")

                mode = "ab" if start_at else "wb"
                total_size = _response_total_size(response, start_at)
                written = start_at
                self._emit_download_progress(written, total_size)

                with part_path.open(mode) as handle:
                    for chunk in response.iter_content(chunk_size=512 * 1024):
                        while not self.pause_event.wait(0.2):
                            if self.stop_event.is_set():
                                return
                        if self.stop_event.is_set():
                            return
                        if not chunk:
                            continue
                        handle.write(chunk)
                        written += len(chunk)
                        self._emit_download_progress(written, total_size)

                if bounds is not None and written != bounds[1] + 1:
                    raise HlsError(
                        f"连接提前结束：响应范围应结束于 {bounds[1]}，实际写入至 {written - 1}"
                    )
                if response.status_code == 200:
                    if total_size > 0 and written != total_size:
                        raise HlsError(f"连接提前结束：预期 {total_size} 字节，实际 {written} 字节")
                    return
                if total_size > 0 and written == total_size:
                    return
                if total_size > 0 and written > total_size:
                    raise DirectResumeError("服务器返回的数据超过媒体总大小，拒绝发布文件。")
                start_at = written

    def _emit_download_progress(self, written: int, total_size: int) -> None:
        if total_size > 0:
            done = int(written / total_size * self.progress_total)
        else:
            done = min(self.progress_total - 1, self.last_done + 1)
        done = max(0, min(self.progress_total - 1, done))
        self._mark_done(done)
        _emit(self.callback, "segment", index=done, status="downloading")
        self._emit_progress(done, downloading=1, bytes_done=written)

    def _mark_done(self, done: int) -> None:
        done = max(0, min(self.progress_total, done))
        with self.lock:
            start = self.last_done
            if done <= start:
                return
            self.last_done = done
        for index in range(start, done):
            _emit(self.callback, "segment", index=index, status="done")

    def _emit_progress(self, done: int, downloading: int, bytes_done: int) -> None:
        _emit(
            self.callback,
            "progress",
            done=done,
            failed=0,
            downloading=downloading,
            total=self.progress_total,
            bytes_done=bytes_done,
        )

    def _cache_root(self) -> Path:
        source = f"{self.resume_key}|{self.output_path.resolve()}"
        digest = hashlib.sha1(source.encode("utf-8")).hexdigest()[:16]
        return self.output_path.parent / ".direct_resume" / digest

    def _resume_path(self) -> Path:
        return self.cache_dir / "payload.cache"

    def _migrate_legacy_part(self, resume_path: Path) -> None:
        """Move an old visible sidecar into the internal resume cache once. @codex-comment"""

        legacy_path = self.output_path.with_suffix(self.output_path.suffix + ".part")
        if not resume_path.exists() and legacy_path.exists():
            legacy_path.replace(resume_path)

    def _remove_empty_resume_dirs(self) -> None:
        for directory in (self.cache_dir, self.cache_dir.parent):
            try:
                directory.rmdir()
            except OSError:
                break


def _candidate_from_media(
    media: MediaPlaylist,
    source_url: str,
    referer: str = "",
    bandwidth: int = 0,
    resolution: str = "",
) -> VideoCandidate:
    return VideoCandidate(
        title=_candidate_title(media.url, resolution, bandwidth, len(media.segments)),
        url=media.url,
        source_url=source_url,
        referer=referer,
        bandwidth=bandwidth,
        resolution=resolution,
        segment_count=len(media.segments),
        duration=media.total_duration,
        encrypted=media.encrypted,
        container="m3u8",
    )


def _candidate_from_direct_url(
    video_url: str,
    source_url: str,
    referer: str = "",
) -> VideoCandidate:
    path = Path(urlparse(video_url).path)
    title = sanitize_file_name(path.stem, "direct-video")
    suffix = path.suffix.lower().lstrip(".")
    if suffix:
        title = f"{title} / {suffix.upper()} 直链"
    else:
        title = f"{title} / 直链"
    return VideoCandidate(
        title=title,
        url=video_url,
        source_url=source_url,
        referer=referer,
        segment_count=100,
        source_type="direct",
        container=suffix or "video",
    )


def _candidate_title(url: str, resolution: str, bandwidth: int, segment_count: int) -> str:
    path_name = sanitize_file_name(Path(urlparse(url).path).stem, "video")
    parts = [path_name]
    if resolution:
        parts.append(resolution)
    if bandwidth:
        parts.append(f"{round(bandwidth / 1000)}kbps")
    if segment_count:
        parts.append(f"{segment_count}片")
    return " / ".join(parts)


def _header_candidates(url: str, referer: str = "", source_url: str = "") -> list[dict[str, str]]:
    referers: list[str] = []
    if referer:
        referers.append(referer)
    if source_url and not _looks_like_playlist_url(source_url):
        referers.append(source_url)
    if source_url:
        referers.append(_default_referer(source_url))
    referers.append(_default_referer(url))
    referers.extend(_known_referers_for_url(url))

    candidates = [make_headers(item) for item in _dedupe(referers) if item]
    candidates.append(make_headers())
    return _dedupe_headers(candidates)


def _known_referers_for_url(url: str) -> list[str]:
    return []


def _dedupe_headers(headers_list: Iterable[dict[str, str]]) -> list[dict[str, str]]:
    seen: set[tuple[tuple[str, str], ...]] = set()
    result: list[dict[str, str]] = []
    for headers in headers_list:
        key = tuple(sorted(headers.items()))
        if key in seen:
            continue
        seen.add(key)
        result.append(headers)
    return result


def _should_try_next_header(exc: Exception) -> bool:
    if isinstance(exc, requests.HTTPError) and exc.response is not None:
        return exc.response.status_code in {401, 403, 404, 429}
    if isinstance(exc, (requests.ConnectionError, requests.Timeout)):
        return True
    return False


def _decode_packed_javascript(text: str) -> list[str]:
    decoded: list[str] = []
    pattern = re.compile(
        r"eval\(function\(p,a,c,k,e,[rd]\).*?\}\('"
        r"(?P<p>(?:\\.|[^'])*)',"
        r"(?P<a>\d+),"
        r"(?P<c>\d+),'"
        r"(?P<k>(?:\\.|[^'])*)'\.split\('\|'\)",
        re.DOTALL,
    )
    for match in pattern.finditer(text):
        try:
            source = _decode_js_string(match.group("p"))
            radix = int(match.group("a"))
            count = int(match.group("c"))
            words = _decode_js_string(match.group("k")).split("|")
            for index in range(count - 1, -1, -1):
                key = _base_n(index, radix)
                value = words[index] if index < len(words) and words[index] else key
                source = re.sub(r"\b" + re.escape(key) + r"\b", value, source)
            decoded.append(source)
        except Exception:
            continue
    return decoded


def _decode_js_string(value: str) -> str:
    return bytes(value, "utf-8").decode("unicode_escape")


def _base_n(value: int, radix: int) -> str:
    alphabet = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
    if value == 0:
        return "0"
    digits: list[str] = []
    while value:
        value, remainder = divmod(value, radix)
        digits.append(alphabet[remainder])
    return "".join(reversed(digits))


def _discover_urls_from_scripts(
    base_url: str,
    text: str,
    headers: dict[str, str],
    callback: Optional[EventCallback],
) -> list[str]:
    scripts = re.findall(r"<script[^>]+src=[\"']([^\"']+)[\"']", text, flags=re.IGNORECASE)
    urls: list[str] = []
    for script_src in _dedupe(urljoin(base_url, src) for src in scripts)[:20]:
        try:
            script_text = fetch_text(script_src, headers=headers, timeout=15)
            urls.extend(find_m3u8_urls(script_src, script_text))
        except Exception as exc:
            _emit(
                callback,
                "log",
                level="debug",
                message=f"跳过脚本：{redact_url(script_src)} ({redact_sensitive_text(str(exc))})",
            )
    return urls


@lru_cache(maxsize=256)
def _has_specific_ytdlp_extractor(source_url: str) -> bool:
    if yt_dlp is None:
        return False
    try:
        from yt_dlp.extractor import gen_extractor_classes
    except ImportError:
        return False

    for extractor_class in gen_extractor_classes():
        if extractor_class.ie_key() == "Generic":
            continue
        try:
            if extractor_class.suitable(source_url):
                return True
        except Exception:
            continue
    return False


@lru_cache(maxsize=1)
def ffmpeg_capability() -> FFmpegCapability:
    path = shutil.which("ffmpeg") or ""
    return FFmpegCapability(available=bool(path), path=path)


def build_ytdlp_options(
    source_url: str,
    referer: str = "",
    preferences: Optional[DownloadPreferences] = None,
    ffmpeg_available: Optional[bool] = None,
) -> dict:
    preferences = preferences or DownloadPreferences()
    has_ffmpeg = ffmpeg_capability().available if ffmpeg_available is None else ffmpeg_available
    options = {
        "format": _quality_format_selector(preferences.quality, has_ffmpeg),
        "http_headers": make_headers(referer or _default_referer(source_url)),
        "socket_timeout": 15,
        "retries": 2,
        "extractor_retries": 2,
        "fragment_retries": 3,
    }
    if preferences.quality == "compact":
        options["format_sort"] = ["+size", "+br", "+res", "+fps"]
    if has_ffmpeg:
        options["merge_output_format"] = "mp4"

    languages = tuple(language for language in preferences.subtitle_languages if language)
    if languages:
        options.update(
            {
                "writesubtitles": True,
                "writeautomaticsub": preferences.include_auto_subtitles,
                "subtitleslangs": list(languages),
                "subtitlesformat": f"{preferences.subtitle_format}/best",
            }
        )
        postprocessors: list[dict] = []
        if has_ffmpeg and preferences.subtitle_format in {"srt", "vtt", "ass", "lrc"}:
            postprocessors.append(
                {
                    "key": "FFmpegSubtitlesConvertor",
                    "format": preferences.subtitle_format,
                    "when": "before_dl",
                }
            )
        if has_ffmpeg and preferences.embed_subtitles:
            postprocessors.append({"key": "FFmpegEmbedSubtitle", "already_have_subtitle": False})
        if postprocessors:
            options["postprocessors"] = postprocessors
    return options


def _ytdlp_base_options(source_url: str, referer: str = "") -> dict:
    return build_ytdlp_options(source_url, referer)


def _ytdlp_extractor_args(source_url: str) -> dict[str, dict[str, list[str]]]:
    if _looks_like_youtube_url(source_url):
        return {"youtube": {"player_client": ["default", "ios"]}}
    return {"generic": {"impersonate": [""]}}


def _youtube_base_options() -> dict:
    options = _ytdlp_base_options("https://www.youtube.com/")
    options["extractor_args"] = _ytdlp_extractor_args("https://www.youtube.com/")
    return options


def _youtube_format_selector() -> str:
    return _quality_format_selector("best", ffmpeg_capability().available)


def _quality_format_selector(quality: str, has_ffmpeg: bool) -> str:
    max_height = {"1080p": 1080, "720p": 720}.get(quality)
    if has_ffmpeg:
        if max_height:
            return f"bv*[height<=?{max_height}]+ba/b[height<=?{max_height}]/b"
        return "bv*+ba/b"
    if max_height:
        return f"b[height<=?{max_height}][ext=mp4]/b[height<=?{max_height}]/best[ext=mp4]/best"
    return "best[ext=mp4]/best"


def _youtube_resolution(info: dict) -> str:
    width = _safe_int(str(info.get("width") or 0))
    height = _safe_int(str(info.get("height") or 0))
    formats = info.get("formats") or []
    if (not width or not height) and isinstance(formats, list):
        best = None
        for item in formats:
            if not isinstance(item, dict):
                continue
            item_height = _safe_int(str(item.get("height") or 0))
            item_width = _safe_int(str(item.get("width") or 0))
            item_tbr = _safe_int(str(item.get("tbr") or 0))
            if item_height <= 0:
                continue
            key = (item_height, item_width, item_tbr)
            if best is None or key > best[0]:
                best = (key, item)
        if best:
            width = _safe_int(str(best[1].get("width") or 0))
            height = _safe_int(str(best[1].get("height") or 0))
    if width and height:
        return f"{width}x{height}"
    if height:
        return f"{height}p"
    return ""


def _youtube_bandwidth(info: dict) -> int:
    values: list[int] = []
    for key in ("tbr", "vbr", "abr"):
        value = _safe_int(str(info.get(key) or 0))
        if value > 0:
            values.append(value)
    formats = info.get("formats") or []
    if isinstance(formats, list):
        for item in formats:
            if not isinstance(item, dict):
                continue
            value = _safe_int(str(item.get("tbr") or item.get("vbr") or item.get("abr") or 0))
            if value > 0:
                values.append(value)
    if not values:
        return 0
    return max(values) * 1000


def _extension_from_url(url: str, fallback: str) -> str:
    suffix = Path(urlparse(url).path).suffix
    if not suffix or len(suffix) > 8:
        return fallback
    return suffix


def _looks_like_playlist_url(url: str) -> bool:
    return ".m3u8" in urlparse(url).path.lower()


def _looks_like_direct_video_url(url: str) -> bool:
    return Path(urlparse(url).path).suffix.lower() in DIRECT_VIDEO_EXTENSIONS


def _response_total_size(response: requests.Response, start_at: int) -> int:
    """Return the declared object size, or zero when a valid partial response leaves it unknown. @codex-comment"""

    content_range = response.headers.get("Content-Range", "")
    match = re.search(r"/(\d+)\s*$", content_range)
    if match:
        return int(match.group(1))
    if response.status_code == 206 and re.search(r"/\*\s*$", content_range):
        return 0
    content_length = _safe_int(response.headers.get("Content-Length", "0"))
    if response.status_code == 206:
        return start_at + content_length
    return content_length


def _response_unsatisfied_total(response: requests.Response) -> int:
    match = re.match(
        r"bytes\s+\*/(\d+)\s*$",
        response.headers.get("Content-Range", ""),
        flags=re.IGNORECASE,
    )
    return int(match.group(1)) if match else 0


def _response_range_bounds(response: requests.Response) -> Optional[tuple[int, int]]:
    """Return inclusive bounds declared by Content-Range, or None when malformed."""

    match = re.match(
        r"bytes\s+(\d+)-(\d+)/(?:\d+|\*)\s*$",
        response.headers.get("Content-Range", ""),
        flags=re.IGNORECASE,
    )
    if not match:
        return None
    start, end = int(match.group(1)), int(match.group(2))
    return (start, end) if end >= start else None


def _response_range_start(response: requests.Response) -> Optional[int]:
    """Return the first byte declared by a 206 response, or None when it is malformed."""

    bounds = _response_range_bounds(response)
    return bounds[0] if bounds else None


def _direct_error_status(error: Exception) -> int:
    response = getattr(error, "response", None)
    return int(getattr(response, "status_code", 0) or 0)


def _is_retryable_direct_error(error: Exception, can_refresh: bool) -> bool:
    """Classify bounded direct-download retries without retrying permanent 4xx failures. @codex-comment"""

    if isinstance(error, DirectResumeError):
        return True
    if isinstance(error, requests.HTTPError):
        status = _direct_error_status(error)
        return status in {408, 416, 425, 429} or status >= 500 or (can_refresh and status in {401, 403, 404})
    if isinstance(error, requests.RequestException):
        return True
    return isinstance(error, HlsError) and "连接提前结束" in str(error)


def _direct_error_requires_refresh(error: Exception) -> bool:
    if isinstance(error, DirectResumeError):
        return True
    return _direct_error_status(error) in {401, 403, 404, 416}


def _normalize_iv(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    value = value.lower().removeprefix("0x")
    return value.rjust(32, "0")[-32:]


def _resolution_area(resolution: str) -> int:
    match = re.match(r"(\d+)x(\d+)", resolution or "")
    if not match:
        return 0
    return int(match.group(1)) * int(match.group(2))


def _safe_int(value: str) -> int:
    try:
        return int(float(value.strip()))
    except (TypeError, ValueError):
        return 0


def _safe_float(value: object) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _default_referer(url: str) -> str:
    parsed = urlparse(url)
    if not parsed.scheme or not parsed.netloc:
        return ""
    return f"{parsed.scheme}://{parsed.netloc}/"


def _dedupe(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if not value or value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def _emit(callback: Optional[EventCallback], event: str, **payload: object) -> None:
    if callback:
        callback(event, payload)
