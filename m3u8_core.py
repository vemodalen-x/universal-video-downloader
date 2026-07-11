from __future__ import annotations

import hashlib
import html
import json
import os
import re
import shutil
import threading
import time
from collections import deque
from dataclasses import asdict, dataclass
from pathlib import Path
from queue import Empty, Queue
from typing import Callable, Iterable, Optional
from urllib.parse import unquote, urljoin, urlparse, urlsplit, urlunsplit

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


class HlsError(Exception):
    """Base error for playlist discovery and download failures."""


class PlaylistParseError(HlsError):
    """Raised when an m3u8 document cannot be parsed."""


@dataclass(frozen=True)
class SegmentKey:
    method: str
    uri: str
    iv_hex: Optional[str] = None


@dataclass(frozen=True)
class Segment:
    index: int
    url: str
    duration: float
    file_name: str
    key: Optional[SegmentKey] = None
    is_init: bool = False


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
        return cls(
            record_id=str(payload.get("record_id") or ""),
            title=sanitize_file_name(str(payload.get("title") or "video"), "video"),
            source_type=str(payload.get("source_type") or "unknown")[:24],
            source_url=source_url,
            source_host=source_host,
            output_path=str(payload.get("output_path") or ""),
            status=status,
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
    """Persists a bounded local task library with atomic replace semantics."""

    def __init__(self, path: Path, limit: int = 100) -> None:
        self.path = path
        self.limit = max(10, limit)
        self._lock = threading.Lock()

    def load(self) -> list[DownloadRecord]:
        with self._lock:
            if not self.path.exists():
                return []
            try:
                payload = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, ValueError, TypeError):
                return []
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

    def save(self, records: Iterable[DownloadRecord]) -> None:
        normalized = [DownloadRecord.from_dict(asdict(item)) for item in records]
        ordered = sorted(normalized, key=lambda item: item.updated_at, reverse=True)[: self.limit]
        payload = {"version": 1, "records": [asdict(item) for item in ordered]}
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temp_path = self.path.with_suffix(self.path.suffix + ".tmp")
            temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            temp_path.replace(self.path)

    def upsert(self, record: DownloadRecord) -> list[DownloadRecord]:
        records = [item for item in self.load() if item.record_id != record.record_id]
        records.insert(0, record)
        self.save(records)
        return records[: self.limit]

    def clear_completed(self) -> list[DownloadRecord]:
        records = [item for item in self.load() if item.status != "completed"]
        self.save(records)
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
            has_byterange = True
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
                segments.append(
                    Segment(
                        index=len(segments),
                        url=urljoin(url, uri),
                        duration=0.0,
                        file_name=f"{len(segments):06d}{_extension_from_url(uri, '.init')}",
                        key=current_key,
                        is_init=True,
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

        segments.append(
            Segment(
                index=len(segments),
                url=urljoin(url, line),
                duration=current_duration,
                file_name=f"{len(segments):06d}{_extension_from_url(line, '.ts')}",
                key=key,
            )
        )
        total_duration += current_duration
        current_duration = 0.0
        media_segment_index += 1

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
        if info.media.has_byterange:
            raise PlaylistParseError("该 HLS 使用字节范围分片，请改用通用解析器下载")
        return info.media
    if not info.variants:
        raise PlaylistParseError("没有发现可下载的视频分片")

    best = max(info.variants, key=lambda item: (_resolution_area(item.resolution), item.bandwidth))
    nested = load_playlist_info(best.url, headers=headers)
    if not nested.media:
        raise PlaylistParseError("清晰度列表没有指向可下载的视频分片")
    if nested.media.has_byterange:
        raise PlaylistParseError("该 HLS 使用字节范围分片，请改用通用解析器下载")
    return nested.media


def discover_candidates(
    source_url: str,
    referer: str = "",
    callback: Optional[EventCallback] = None,
) -> list[VideoCandidate]:
    return _discover_candidates_impl(source_url, referer, callback)


def _discover_candidates_impl(
    source_url: str,
    referer: str = "",
    callback: Optional[EventCallback] = None,
) -> list[VideoCandidate]:
    source_url = source_url.strip()
    if not source_url:
        raise HlsError("请输入网页地址或 m3u8 地址")

    if _looks_like_youtube_url(source_url):
        return _discover_youtube_candidates(source_url, callback)

    parsed_source = urlparse(source_url)
    if parsed_source.scheme not in {"http", "https"} or not parsed_source.netloc:
        raise HlsError("请输入有效的 http 或 https 地址")

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
            if info.media.has_byterange:
                _emit(callback, "log", level="info", message="检测到 HLS 字节范围分片，切换通用解析器")
                continue
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
                if media_info.media.has_byterange:
                    _emit(callback, "log", level="info", message="跳过原生下载不支持的 HLS 字节范围变体")
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
    options.update(
        {
            "skip_download": True,
            "quiet": True,
            "no_warnings": True,
            "noplaylist": True,
        }
    )
    try:
        with yt_dlp.YoutubeDL(options) as ydl:
            info = ydl.extract_info(source_url, download=False)
    except Exception as exc:
        raise HlsError(f"通用媒体解析失败：{redact_sensitive_text(str(exc))}") from exc

    if not isinstance(info, dict):
        raise HlsError("通用解析器没有返回可下载的视频信息")
    if info.get("_type") == "playlist" and info.get("entries"):
        first = next((item for item in info.get("entries") or [] if item), None)
        if isinstance(first, dict):
            info = first

    title = sanitize_file_name(str(info.get("title") or info.get("id") or "youtube-video"), "youtube-video")
    resolution = _youtube_resolution(info)
    bandwidth = _youtube_bandwidth(info)
    duration = float(info.get("duration") or 0.0)
    extractor = str(info.get("extractor_key") or info.get("extractor") or "yt-dlp")
    display_title = title
    if resolution:
        display_title = f"{display_title} / {label} / {resolution}"
    else:
        display_title = f"{display_title} / {label}"

    return [
        VideoCandidate(
            title=display_title,
            url=source_url,
            source_url=source_url,
            referer=_default_referer(source_url),
            bandwidth=bandwidth,
            resolution=resolution,
            segment_count=100,
            duration=duration,
            encrypted=False,
            source_type=source_type,
            container=str(info.get("ext") or "mp4"),
            extractor=extractor,
        )
    ]


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
        r"(?i)\b(authorization|cookie|set-cookie|token|access_token|signature|sig)\s*[:=]\s*([^\s&;,]+)",
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
    ) -> None:
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
        self.cache_dir = self._cache_root()
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
        source = f"{self.playlist.url}|{self.output_path.resolve()}"
        digest = hashlib.sha1(source.encode("utf-8")).hexdigest()[:16]
        return self.output_path.parent / ".m3u8_resume" / digest


class YouTubeDownloadJob:
    progress_total = 100

    def __init__(
        self,
        url: str,
        output_path: Path,
        concurrency: int = 4,
        referer: str = "",
        callback: Optional[EventCallback] = None,
    ) -> None:
        self.url = url
        self.output_path = output_path
        self.concurrency = max(1, min(16, concurrency))
        self.referer = referer
        self.callback = callback
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

            options = _ytdlp_base_options(self.url, self.referer)
            if _looks_like_youtube_url(self.url):
                options["extractor_args"] = {"youtube": {"player_client": ["default", "ios"]}}
            options.update(
                {
                    "outtmpl": self._output_template(),
                    "cachedir": str(self.cache_dir),
                    "continuedl": True,
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
    ) -> None:
        self.url = url
        self.output_path = output_path
        self.headers = headers or make_headers(_default_referer(url))
        self.callback = callback
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
            _emit(self.callback, "fatal", message=f"直链下载失败：{exc}")

    def _download(self) -> None:
        part_path = self.output_path.with_suffix(self.output_path.suffix + ".part")
        start_at = part_path.stat().st_size if part_path.exists() else 0
        request_headers = dict(self.headers)
        request_headers["Accept-Encoding"] = "identity"
        if start_at:
            request_headers["Range"] = f"bytes={start_at}-"

        with _http_session().get(self.url, headers=request_headers, stream=True, timeout=(10, 45)) as response:
            if response.status_code == 416:
                part_path.unlink(missing_ok=True)
                return self._download()
            response.raise_for_status()

            if start_at and response.status_code == 206 and _response_range_start(response) != start_at:
                part_path.unlink(missing_ok=True)
                return self._download()

            mode = "ab" if start_at and response.status_code == 206 else "wb"
            if mode == "wb":
                start_at = 0
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

            if total_size > 0 and written < total_size:
                raise HlsError(f"连接提前结束：预期 {total_size} 字节，实际 {written} 字节")

        part_path.replace(self.output_path)

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
        source = f"{self.url}|{self.output_path.resolve()}"
        digest = hashlib.sha1(source.encode("utf-8")).hexdigest()[:16]
        return self.output_path.parent / ".direct_resume" / digest


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


def _ytdlp_base_options(source_url: str, referer: str = "") -> dict:
    options = {
        "format": _youtube_format_selector(),
        "http_headers": make_headers(referer or _default_referer(source_url)),
    }
    if shutil.which("ffmpeg"):
        options["merge_output_format"] = "mp4"
    return options


def _youtube_base_options() -> dict:
    options = _ytdlp_base_options("https://www.youtube.com/")
    options["extractor_args"] = {"youtube": {"player_client": ["default", "ios"]}}
    return options


def _youtube_format_selector() -> str:
    if shutil.which("ffmpeg"):
        return "bv*[ext=mp4]+ba[ext=m4a]/bv*+ba/b[ext=mp4]/b"
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
    content_range = response.headers.get("Content-Range", "")
    match = re.search(r"/(\d+)\s*$", content_range)
    if match:
        return int(match.group(1))
    content_length = _safe_int(response.headers.get("Content-Length", "0"))
    if response.status_code == 206:
        return start_at + content_length
    return content_length


def _response_range_start(response: requests.Response) -> Optional[int]:
    """Return the first byte declared by a 206 response, or None when it is malformed."""

    match = re.match(r"bytes\s+(\d+)-\d+/", response.headers.get("Content-Range", ""), flags=re.IGNORECASE)
    return int(match.group(1)) if match else None


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
