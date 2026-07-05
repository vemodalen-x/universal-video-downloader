from __future__ import annotations

import hashlib
import html
import json
import re
import shutil
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from queue import Empty, Queue
from typing import Callable, Iterable, Optional
from urllib.parse import unquote, urljoin, urlparse

import requests

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


EventCallback = Callable[[str, dict], None]


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
    response = requests.get(url, headers=headers or make_headers(), timeout=timeout)
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
    if start_at:
        request_headers["Range"] = f"bytes={start_at}-"

    with requests.get(url, headers=request_headers, stream=True, timeout=(10, 45)) as response:
        if response.status_code == 416:
            path.unlink(missing_ok=True)
            return fetch_binary(url, path, headers, stop_event, chunk_size)
        response.raise_for_status()

        if start_at and response.status_code != 206:
            mode = "wb"
            start_at = 0

        written = start_at
        with path.open(mode + "") as handle:
            for chunk in response.iter_content(chunk_size=chunk_size):
                if stop_event and stop_event.is_set():
                    raise HlsError("任务已停止")
                if not chunk:
                    continue
                handle.write(chunk)
                written += len(chunk)
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
        return info.media
    if not info.variants:
        raise PlaylistParseError("没有发现可下载的视频分片")

    best = max(info.variants, key=lambda item: (_resolution_area(item.resolution), item.bandwidth))
    nested = load_playlist_info(best.url, headers=headers)
    if not nested.media:
        raise PlaylistParseError("清晰度列表没有指向可下载的视频分片")
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

    page_headers = make_headers(referer or _default_referer(source_url))
    direct_urls: list[str] = []
    if _looks_like_direct_video_url(source_url):
        return [_candidate_from_direct_url(source_url, source_url, referer=referer or _default_referer(source_url))]
    if _looks_like_playlist_url(source_url):
        unique_urls = [source_url]
        page_referer = referer
    else:
        text, page_headers = fetch_text_with_fallbacks(
            source_url,
            _header_candidates(source_url, referer=referer, source_url=source_url),
        )
        page_referer = page_headers.get("Referer") or source_url
        discovered_urls = find_m3u8_urls(source_url, text)
        discovered_urls.extend(_discover_urls_from_scripts(source_url, text, page_headers, callback))
        unique_urls = _dedupe(discovered_urls)
        direct_urls = find_direct_video_urls(source_url, text)

    if not unique_urls and not direct_urls:
        raise HlsError("页面源码里没有发现可下载的视频地址。动态加载站点可能需要浏览器上下文、登录态或专用解析器。")

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
            _emit(callback, "log", level="warning", message=f"跳过无效 m3u8：{playlist_url} ({exc})")
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

    candidates.sort(key=lambda item: candidate_score(item), reverse=True)
    if not candidates:
        raise HlsError("发现了视频地址，但没有解析出可下载的视频内容。")
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
    if yt_dlp is None:
        raise HlsError("YouTube 支持需要 yt-dlp，请先运行 python -m pip install -r requirements.txt")

    _emit(callback, "log", level="info", message="正在使用 yt-dlp 解析 YouTube 视频")
    options = _youtube_base_options()
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
        raise HlsError(f"YouTube 解析失败：{exc}") from exc

    if not isinstance(info, dict):
        raise HlsError("YouTube 没有返回可下载的视频信息")
    if info.get("_type") == "playlist" and info.get("entries"):
        first = next((item for item in info.get("entries") or [] if item), None)
        if isinstance(first, dict):
            info = first

    title = sanitize_file_name(str(info.get("title") or info.get("id") or "youtube-video"), "youtube-video")
    resolution = _youtube_resolution(info)
    bandwidth = _youtube_bandwidth(info)
    duration = float(info.get("duration") or 0.0)
    display_title = title
    if resolution:
        display_title = f"{display_title} / YouTube / {resolution}"
    else:
        display_title = f"{display_title} / YouTube"

    return [
        VideoCandidate(
            title=display_title,
            url=source_url,
            source_url=source_url,
            referer="https://www.youtube.com/",
            bandwidth=bandwidth,
            resolution=resolution,
            segment_count=100,
            duration=duration,
            encrypted=False,
            source_type="youtube",
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


def sanitize_file_name(value: str, default: str = "video") -> str:
    value = unquote(value).strip()
    value = re.sub(r"[\\/:*?\"<>|]+", "_", value)
    value = re.sub(r"\s+", " ", value)
    value = value.strip(" .")
    return value[:120] or default


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
        for segment in self.playlist.segments:
            path = self._segment_path(segment)
            self.status[segment.index] = "done" if path.exists() and path.stat().st_size > 0 else "pending"
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
        response = requests.get(url, headers=self.headers, timeout=20)
        response.raise_for_status()
        key = response.content
        if len(key) != 16:
            raise HlsError(f"AES key 长度异常：{url}")
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
        done = values.count("done")
        failed = values.count("error")
        downloading = values.count("downloading")
        bytes_done = 0
        for segment in self.playlist.segments:
            path = self._segment_path(segment)
            if path.exists():
                bytes_done += path.stat().st_size
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
        callback: Optional[EventCallback] = None,
    ) -> None:
        self.url = url
        self.output_path = output_path
        self.concurrency = max(1, min(16, concurrency))
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

            options = _youtube_base_options()
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
            _emit(self.callback, "fatal", message=f"YouTube 下载失败：{exc}")

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
        if start_at:
            request_headers["Range"] = f"bytes={start_at}-"

        with requests.get(self.url, headers=request_headers, stream=True, timeout=(10, 45)) as response:
            if response.status_code == 416:
                part_path.unlink(missing_ok=True)
                return self._download()
            response.raise_for_status()

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
            _emit(callback, "log", level="debug", message=f"跳过脚本：{script_src} ({exc})")
    return urls


def _youtube_base_options() -> dict:
    options = {
        "format": _youtube_format_selector(),
        "http_headers": make_headers("https://www.youtube.com/"),
        "extractor_args": {"youtube": {"player_client": ["default", "ios"]}},
    }
    if shutil.which("ffmpeg"):
        options["merge_output_format"] = "mp4"
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
