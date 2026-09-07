"""Exercise the native desktop flow with synthetic data and optional local captures."""

from __future__ import annotations

import argparse
import ctypes
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import m3u8_desktop_app as desktop  # noqa: E402
from m3u8_core import DownloadRecord, HlsError, VideoCandidate  # noqa: E402


def assert_visible(widget) -> None:
    assert widget.winfo_ismapped(), str(widget)
    left, top = widget.winfo_rootx(), widget.winfo_rooty()
    right, bottom = left + widget.winfo_width(), top + widget.winfo_height()
    parent = widget.master
    while parent is not None:
        x, y = parent.winfo_rootx(), parent.winfo_rooty()
        assert left >= x and top >= y, f"{widget}: outside {parent}"
        assert right <= x + parent.winfo_width(), f"{widget}: clipped horizontally by {parent}"
        assert bottom <= y + parent.winfo_height(), f"{widget}: clipped vertically by {parent}"
        parent = parent.master


def capture(window, directory: Path | None, name: str) -> None:
    if directory is None:
        return
    if sys.platform != "win32":
        raise RuntimeError("Window-only screenshot capture requires Windows")
    from PIL import ImageGrab

    directory.mkdir(parents=True, exist_ok=True)
    hwnd = ctypes.windll.user32.GetParent(window.winfo_id())
    shot = ImageGrab.grab(window=hwnd)
    assert len(shot.convert("RGB").getcolors(1_000_000) or []) > 16, "Blank native capture"
    shot.save(directory / name)


def run(capture_dir: Path | None = None) -> None:
    with tempfile.TemporaryDirectory(prefix="downloader-ux-smoke-") as temp:
        with (
            patch.object(desktop, "default_history_path", lambda: Path(temp) / "history.json"),
            patch.object(desktop, "BrowserInbox", lambda: object()),
            patch.object(desktop.UniversalVideoDownloaderApp, "_poll_browser_inbox", lambda self: None),
        ):
            app = desktop.UniversalVideoDownloaderApp()
            errors = []
            app.report_callback_exception = lambda *args: errors.append(args)
            try:
                app.output_dir_var.set("Demo downloads")
                app.url_var.set("https://example.com/collection")
                app._analysis_url = app.url_var.get()
                candidates = [
                    VideoCandidate(
                        f"Demo episode {i:02}", f"https://example.com/{i}.mp4", app.url_var.get(),
                        source_type="direct", playlist_count=125,
                    ) for i in range(1, 126)
                ]
                app._on_analysis_done(candidates)
                app._select_all_candidates()
                assert len(app._selected_candidates()) == 125
                for size in ("1220x840", "1040x720"):
                    app.geometry(size)
                    app.update()
                    for name in ("url_entry", "analyze_button", "select_all_button", "deselect_button",
                                 "start_button", "pause_button", "stop_button", "segment_canvas"):
                        assert_visible(getattr(app, name))
                    capture(app, capture_dir, f"selection-{size}.png")
                    print(f"PASS {size}: primary controls and progress fully visible")

                app._deselect_candidates()
                app.update()
                assert app.start_button.instate(["disabled"])
                capture(app, capture_dir, "empty-selection.png")

                app._select_all_candidates()
                app.current_candidate = candidates[0]
                app._set_downloading_state(True)
                app.update()
                assert app.url_entry.instate(["disabled"])
                assert not app.pause_button.instate(["disabled"])
                assert not app.stop_button.instate(["disabled"])
                selection = app.candidate_tree.selection()
                x, y, width, height = app.candidate_tree.bbox("1")
                app.candidate_tree.event_generate("<Button-1>", x=x + width // 2, y=y + height // 2)
                app.candidate_tree.event_generate("<ButtonRelease-1>", x=x + width // 2, y=y + height // 2)
                app.update()
                assert app.candidate_tree.selection() == selection
                assert_visible(app.pause_button)
                assert_visible(app.stop_button)
                app._set_downloading_state(False)

                app.url_var.set("https://example.com/another-collection")
                app.update()
                assert not app.candidates and app.start_button.instate(["disabled"])
                capture(app, capture_dir, "changed-source.png")

                app._analysis_url = app.url_var.get()
                app._set_busy_analyzing(True)
                app._handle_event("analysis_error", {"error": HlsError("PikPak 需要提取码")})
                app.update()
                assert app.advanced_visible and not app.access_code_entry.instate(["disabled"])
                assert_visible(app.access_code_entry)
                assert_visible(app.concurrency_entry)
                assert_visible(app.advanced_done_button)
                capture(app.advanced_window, capture_dir, "code-recovery.png")
                app._set_advanced_visible(False)
                app.history_records = [DownloadRecord(
                    record_id="demo-history", title="Demo episode", source_type="direct",
                    source_url="https://example.com/episode", source_host="example.com",
                    output_path="Demo downloads/episode.mp4", status="failed",
                    error_code="network_timeout", error_message="Connection timed out",
                )]
                app.main_notebook.select(app.history_tab)
                app._refresh_history()
                app.history_tree.selection_set("demo-history")
                app.update()
                for control in (app.history_open_button, app.history_retry_button, app.history_detail_label):
                    assert_visible(control)
                assert "Connection timed out" in app.history_detail_var.get()
                capture(app, capture_dir, "history-recovery.png")

                app.main_notebook.select(app.download_tab)
                log_tab = app.log_text.frame.master
                log_tab.master.select(log_tab)
                for index in range(desktop.MAX_LOG_LINES + 20):
                    app._log(f"Synthetic event {index}")
                app.update()
                app.log_text.yview_moveto(0.2)
                app._log("Synthetic event while reading earlier entries")
                app.update()
                assert app.log_text.yview()[0] < 0.8
                assert int(app.log_text.index("end-1c").split(".")[0]) - 1 <= desktop.MAX_LOG_LINES
                assert not errors, errors
                print("PASS selection, queue controls, source/code recovery, history layout, and bounded log scrolling")
            finally:
                app.destroy()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capture-dir", type=Path, help="Optional local directory for synthetic screenshots")
    run(parser.parse_args().capture_dir)
