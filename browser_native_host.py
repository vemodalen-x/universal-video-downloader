from __future__ import annotations

import json
import struct
import sys
from typing import BinaryIO, Optional

from browser_companion import BrowserCompanionError, BrowserInbox, normalize_browser_message


MAX_NATIVE_MESSAGE_BYTES = 64 * 1024


def read_native_message(stream: BinaryIO) -> Optional[dict]:
    header = stream.read(4)
    if not header:
        return None
    if len(header) != 4:
        raise BrowserCompanionError("浏览器消息头不完整")
    length = struct.unpack("<I", header)[0]
    if length <= 0 or length > MAX_NATIVE_MESSAGE_BYTES:
        raise BrowserCompanionError("浏览器消息大小超出限制")
    payload = stream.read(length)
    if len(payload) != length:
        raise BrowserCompanionError("浏览器消息内容不完整")
    try:
        message = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BrowserCompanionError("浏览器消息不是有效 JSON") from exc
    if not isinstance(message, dict):
        raise BrowserCompanionError("浏览器消息必须是 JSON 对象")
    return message


def write_native_message(stream: BinaryIO, message: dict) -> None:
    payload = json.dumps(message, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(payload) > MAX_NATIVE_MESSAGE_BYTES:
        raise BrowserCompanionError("浏览器响应大小超出限制")
    stream.write(struct.pack("<I", len(payload)))
    stream.write(payload)
    stream.flush()


def handle_message(message: dict, inbox: Optional[BrowserInbox] = None) -> dict:
    candidate = normalize_browser_message(message)
    (inbox or BrowserInbox()).push(candidate)
    return {
        "ok": True,
        "queued": True,
        "kind": candidate.kind,
        "message": "已发送到通用视频下载器；请在 15 分钟内打开桌面端。",
    }


def main() -> int:
    inbox = BrowserInbox()
    input_stream = sys.stdin.buffer
    output_stream = sys.stdout.buffer
    while True:
        try:
            message = read_native_message(input_stream)
            if message is None:
                return 0
            response = handle_message(message, inbox)
        except BrowserCompanionError as exc:
            response = {"ok": False, "error": str(exc)}
        except Exception:
            response = {"ok": False, "error": "桌面伴侣暂时无法处理该媒体"}
        write_native_message(output_stream, response)


if __name__ == "__main__":
    raise SystemExit(main())
