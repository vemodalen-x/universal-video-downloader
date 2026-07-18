from __future__ import annotations

import base64
import hashlib
import io
import json
import struct
import threading
from pathlib import Path

import pytest

from browser_companion import BrowserCompanionError, BrowserInbox, normalize_browser_message
from browser_native_host import handle_message, read_native_message, write_native_message


def _message(url: str = "https://cdn.example.com/video/master.m3u8?sig=temporary") -> dict:
    return {
        "action": "enqueue",
        "candidate": {
            "url": url,
            "source_page": "https://example.com/watch/123",
            "title": "Example Video",
            "kind": "hls",
        },
    }


def test_browser_message_normalization_preserves_required_download_context() -> None:
    candidate = normalize_browser_message(_message(), now=100.0)

    assert candidate.url.endswith("master.m3u8?sig=temporary")
    assert candidate.source_page == "https://example.com/watch/123"
    assert candidate.title == "Example Video"
    assert candidate.kind == "hls"
    assert candidate.received_at == 100.0


@pytest.mark.parametrize(
    "message",
    [
        {"action": "enqueue", "candidate": {**_message()["candidate"], "cookie": "private"}},
        _message("file:///C:/private/video.mp4"),
        _message("https://user:password@example.com/video.mp4"),
        {"action": "download", "candidate": _message()["candidate"]},
    ],
)
def test_browser_message_rejects_sensitive_or_unsupported_input(message: dict) -> None:
    with pytest.raises(BrowserCompanionError):
        normalize_browser_message(message)


def test_browser_inbox_encrypts_at_rest_and_consumes_once(tmp_path: Path) -> None:
    inbox_path = tmp_path / "browser_inbox.bin"
    inbox = BrowserInbox(inbox_path)
    candidate = normalize_browser_message(_message(), now=100.0)

    inbox.push(candidate, now=100.0)

    encrypted = inbox_path.read_bytes()
    assert b"temporary" not in encrypted
    assert b"example.com" not in encrypted
    assert inbox.pop(now=101.0) == candidate
    assert inbox.pop(now=102.0) is None


def test_browser_inbox_drops_expired_and_oldest_entries(tmp_path: Path) -> None:
    inbox = BrowserInbox(tmp_path / "browser_inbox.bin", ttl_seconds=60, limit=2)
    first = normalize_browser_message(_message("https://example.com/one.mp4"), now=100.0)
    second = normalize_browser_message(_message("https://example.com/two.mp4"), now=110.0)
    third = normalize_browser_message(_message("https://example.com/three.mp4"), now=120.0)

    inbox.push(first, now=100.0)
    inbox.push(second, now=110.0)
    inbox.push(third, now=120.0)

    assert inbox.pop(now=121.0) == second
    assert inbox.pop(now=181.0) is None


def test_browser_inbox_preserves_concurrent_cross_instance_pushes(tmp_path: Path) -> None:
    inbox_path = tmp_path / "browser_inbox.bin"
    failures: list[Exception] = []

    def push(index: int) -> None:
        try:
            candidate = normalize_browser_message(_message(f"https://example.com/{index}.mp4"), now=100.0)
            BrowserInbox(inbox_path).push(candidate, now=100.0)
        except Exception as exc:  # pragma: no cover - asserted below for clearer thread failures
            failures.append(exc)

    threads = [threading.Thread(target=push, args=(index,)) for index in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert failures == []
    inbox = BrowserInbox(inbox_path)
    candidates = [inbox.pop(now=120.0) for _index in range(8)]
    assert {candidate.url for candidate in candidates if candidate} == {
        f"https://example.com/{index}.mp4" for index in range(8)
    }


def test_native_message_framing_and_handler(tmp_path: Path) -> None:
    encoded = io.BytesIO()
    write_native_message(encoded, _message())
    encoded.seek(0)

    decoded = read_native_message(encoded)
    inbox = BrowserInbox(tmp_path / "browser_inbox.bin")
    response = handle_message(decoded, inbox)

    assert response["ok"] is True
    assert inbox.pop() is not None


def test_native_message_rejects_oversized_frames() -> None:
    stream = io.BytesIO(struct.pack("<I", 70 * 1024))

    with pytest.raises(BrowserCompanionError):
        read_native_message(stream)


def test_extension_permissions_are_active_tab_only() -> None:
    root = Path(__file__).resolve().parents[1]
    manifest = json.loads((root / "browser_extension" / "manifest.json").read_text(encoding="utf-8"))
    permissions = set(manifest["permissions"])

    assert manifest["version"] == "1.2.0"
    assert permissions == {"activeTab", "scripting", "nativeMessaging"}
    assert "host_permissions" not in manifest
    assert "background" not in manifest
    assert "content_scripts" not in manifest
    assert set(manifest["icons"].values()) == {"icon.png"}
    assert (root / "browser_extension" / "icon.png").is_file()


def test_extension_id_matches_native_host_installer() -> None:
    root = Path(__file__).resolve().parents[1]
    manifest = json.loads((root / "browser_extension" / "manifest.json").read_text(encoding="utf-8"))
    public_key = base64.b64decode(manifest["key"])
    digest = hashlib.sha256(public_key).digest()[:16]
    extension_id = "".join(chr(ord("a") + (byte >> 4)) + chr(ord("a") + (byte & 15)) for byte in digest)
    installer = (root / "install_browser_companion.ps1").read_text(encoding="utf-8")

    assert extension_id == "pafgpejhjpgdagalhphhdlkfkjldepme"
    assert extension_id in installer
    assert "Chrome\\NativeMessagingHosts" in installer
    assert "Edge\\NativeMessagingHosts" in installer
    assert "[System.IO.File]::WriteAllText" in installer
    assert "[System.Text.UTF8Encoding]::new($false)" in installer


def test_extension_scans_on_demand_and_uses_native_messaging() -> None:
    script = (Path(__file__).resolve().parents[1] / "browser_extension" / "popup.js").read_text(encoding="utf-8")

    assert 'getEntriesByType("resource")' in script
    assert "chrome.scripting.executeScript" in script
    assert "chrome.runtime.sendNativeMessage" in script
    assert "chrome.webRequest" not in script
