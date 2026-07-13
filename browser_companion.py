from __future__ import annotations

import ctypes
import json
import os
import re
import threading
import time
from contextlib import contextmanager
from ctypes import wintypes
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterator, Optional
from urllib.parse import urlparse

from Crypto.Cipher import AES


INBOX_MAGIC = b"UVDI1"
KEY_DPAPI_MAGIC = b"UVDK1"
KEY_RAW_MAGIC = b"UVDR1"
DEFAULT_TTL_SECONDS = 15 * 60
DEFAULT_QUEUE_LIMIT = 20
MAX_URL_LENGTH = 8192
MAX_SOURCE_PAGE_LENGTH = 4096
MAX_TITLE_LENGTH = 180
ALLOWED_KINDS = {"hls", "dash", "video"}
ALLOWED_MESSAGE_KEYS = {"action", "candidate"}
ALLOWED_CANDIDATE_KEYS = {"url", "source_page", "title", "kind"}
CONTROL_CHARACTERS = re.compile(r"[\x00-\x1f\x7f]")
_PATH_LOCKS_GUARD = threading.Lock()
_PATH_LOCKS: dict[str, threading.Lock] = {}


class BrowserCompanionError(ValueError):
    """Raised when browser handoff data is malformed, unsafe, or unreadable."""


@dataclass(frozen=True)
class BrowserCandidate:
    url: str
    source_page: str
    title: str
    kind: str
    received_at: float


def default_browser_inbox_path() -> Path:
    root = Path(os.getenv("LOCALAPPDATA") or (Path.home() / ".local" / "share"))
    return root / "UniversalVideoDownloader" / "browser_inbox.bin"


def normalize_browser_message(message: object, now: Optional[float] = None) -> BrowserCandidate:
    if not isinstance(message, dict):
        raise BrowserCompanionError("浏览器消息必须是 JSON 对象")
    if set(message) - ALLOWED_MESSAGE_KEYS:
        raise BrowserCompanionError("浏览器消息包含不允许的字段")
    if message.get("action") != "enqueue":
        raise BrowserCompanionError("不支持的浏览器操作")
    payload = message.get("candidate")
    if not isinstance(payload, dict):
        raise BrowserCompanionError("浏览器候选缺失")
    if set(payload) - ALLOWED_CANDIDATE_KEYS:
        raise BrowserCompanionError("媒体候选包含不允许的字段")

    url = _validated_http_url(payload.get("url"), MAX_URL_LENGTH, "媒体地址")
    source_page = _validated_http_url(payload.get("source_page"), MAX_SOURCE_PAGE_LENGTH, "来源页面")
    title = _clean_text(payload.get("title"), MAX_TITLE_LENGTH) or "浏览器媒体"
    kind = str(payload.get("kind") or "").lower()
    if kind not in ALLOWED_KINDS:
        raise BrowserCompanionError("不支持的媒体类型")
    return BrowserCandidate(
        url=url,
        source_page=source_page,
        title=title,
        kind=kind,
        received_at=float(time.time() if now is None else now),
    )


class BrowserInbox:
    """Small encrypted cross-process queue shared by the native host and desktop app."""

    def __init__(
        self,
        path: Optional[Path] = None,
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
        limit: int = DEFAULT_QUEUE_LIMIT,
    ) -> None:
        self.path = path or default_browser_inbox_path()
        self.key_path = self.path.with_suffix(".key")
        self.lock_path = self.path.with_suffix(".lock")
        self.ttl_seconds = max(60, ttl_seconds)
        self.limit = max(1, min(100, limit))
        self._thread_lock = _shared_path_lock(self.path)

    def push(self, candidate: BrowserCandidate, now: Optional[float] = None) -> None:
        current_time = float(time.time() if now is None else now)
        with self._locked():
            queue = self._read_queue(current_time)
            queue.append(asdict(candidate))
            self._write_queue(queue[-self.limit :])

    def pop(self, now: Optional[float] = None) -> Optional[BrowserCandidate]:
        current_time = float(time.time() if now is None else now)
        with self._locked():
            queue = self._read_queue(current_time)
            if not queue:
                if self.path.exists():
                    self._write_queue([])
                return None
            item = queue.pop(0)
            self._write_queue(queue)
        try:
            return BrowserCandidate(**item)
        except TypeError as exc:
            raise BrowserCompanionError("浏览器收件箱条目损坏") from exc

    def clear(self) -> None:
        with self._locked():
            try:
                self.path.unlink()
            except FileNotFoundError:
                pass

    def _read_queue(self, now: float) -> list[dict]:
        if not self.path.exists():
            return []
        try:
            encrypted = self.path.read_bytes()
            payload = self._decrypt(encrypted)
            items = json.loads(payload.decode("utf-8"))
        except (OSError, ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise BrowserCompanionError("无法读取浏览器收件箱") from exc
        if not isinstance(items, list):
            raise BrowserCompanionError("浏览器收件箱格式不正确")
        result: list[dict] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            received_at = item.get("received_at")
            try:
                age = now - float(received_at)
            except (TypeError, ValueError):
                continue
            if 0 <= age <= self.ttl_seconds:
                result.append(item)
        return result[-self.limit :]

    def _write_queue(self, queue: list[dict]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(queue, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        encrypted = self._encrypt(payload)
        temporary = self.path.with_name(f".{self.path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
        try:
            temporary.write_bytes(encrypted)
            try:
                os.chmod(temporary, 0o600)
            except OSError:
                pass
            os.replace(temporary, self.path)
        finally:
            if temporary.exists():
                try:
                    temporary.unlink()
                except OSError:
                    pass

    def _encrypt(self, payload: bytes) -> bytes:
        cipher = AES.new(self._load_key(), AES.MODE_GCM)
        ciphertext, tag = cipher.encrypt_and_digest(payload)
        return INBOX_MAGIC + cipher.nonce + tag + ciphertext

    def _decrypt(self, payload: bytes) -> bytes:
        if not payload.startswith(INBOX_MAGIC) or len(payload) < len(INBOX_MAGIC) + 32:
            raise BrowserCompanionError("浏览器收件箱密文格式不正确")
        offset = len(INBOX_MAGIC)
        nonce = payload[offset : offset + 16]
        tag = payload[offset + 16 : offset + 32]
        ciphertext = payload[offset + 32 :]
        try:
            return AES.new(self._load_key(), AES.MODE_GCM, nonce=nonce).decrypt_and_verify(ciphertext, tag)
        except ValueError as exc:
            raise BrowserCompanionError("浏览器收件箱完整性校验失败") from exc

    def _load_key(self) -> bytes:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.key_path.exists():
            return self._decode_key(self.key_path.read_bytes())
        key = os.urandom(32)
        encoded = KEY_DPAPI_MAGIC + _dpapi_protect(key) if os.name == "nt" else KEY_RAW_MAGIC + key
        try:
            with self.key_path.open("xb") as handle:
                handle.write(encoded)
            try:
                os.chmod(self.key_path, 0o600)
            except OSError:
                pass
            return key
        except FileExistsError:
            return self._decode_key(self.key_path.read_bytes())

    @staticmethod
    def _decode_key(payload: bytes) -> bytes:
        if payload.startswith(KEY_DPAPI_MAGIC):
            key = _dpapi_unprotect(payload[len(KEY_DPAPI_MAGIC) :])
        elif payload.startswith(KEY_RAW_MAGIC):
            key = payload[len(KEY_RAW_MAGIC) :]
        else:
            raise BrowserCompanionError("浏览器收件箱密钥格式不正确")
        if len(key) != 32:
            raise BrowserCompanionError("浏览器收件箱密钥长度不正确")
        return key

    @contextmanager
    def _locked(self) -> Iterator[None]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._thread_lock, self.lock_path.open("a+b") as handle:
            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b"0")
                handle.flush()
            handle.seek(0)
            _lock_file(handle)
            try:
                yield
            finally:
                _unlock_file(handle)


def _validated_http_url(value: object, maximum_length: int, label: str) -> str:
    text = str(value or "").strip()
    if not text or len(text) > maximum_length or CONTROL_CHARACTERS.search(text):
        raise BrowserCompanionError(f"{label}无效")
    parsed = urlparse(text)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise BrowserCompanionError(f"{label}必须使用 HTTP(S)")
    if parsed.username or parsed.password:
        raise BrowserCompanionError(f"{label}不能包含用户名或密码")
    return text


def _clean_text(value: object, maximum_length: int) -> str:
    text = CONTROL_CHARACTERS.sub(" ", str(value or ""))
    return re.sub(r"\s+", " ", text).strip()[:maximum_length]


def _shared_path_lock(path: Path) -> threading.Lock:
    key = str(path.resolve(strict=False)).casefold()
    with _PATH_LOCKS_GUARD:
        return _PATH_LOCKS.setdefault(key, threading.Lock())


def _lock_file(handle) -> None:
    if os.name == "nt":
        import msvcrt

        msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
    else:
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)


def _unlock_file(handle) -> None:
    if os.name == "nt":
        import msvcrt

        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]


def _dpapi_protect(payload: bytes) -> bytes:
    return _dpapi_transform(payload, protect=True)


def _dpapi_unprotect(payload: bytes) -> bytes:
    return _dpapi_transform(payload, protect=False)


def _dpapi_transform(payload: bytes, protect: bool) -> bytes:
    if os.name != "nt":
        raise BrowserCompanionError("DPAPI 仅在 Windows 上可用")
    buffer = ctypes.create_string_buffer(payload)
    source = _DataBlob(len(payload), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte)))
    target = _DataBlob()
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    flags = 0x1
    if protect:
        succeeded = crypt32.CryptProtectData(
            ctypes.byref(source),
            "Universal Video Downloader",
            None,
            None,
            None,
            flags,
            ctypes.byref(target),
        )
    else:
        succeeded = crypt32.CryptUnprotectData(
            ctypes.byref(source),
            None,
            None,
            None,
            None,
            flags,
            ctypes.byref(target),
        )
    if not succeeded:
        raise BrowserCompanionError("Windows 无法保护浏览器收件箱密钥")
    try:
        return ctypes.string_at(target.pbData, target.cbData)
    finally:
        kernel32.LocalFree(target.pbData)
