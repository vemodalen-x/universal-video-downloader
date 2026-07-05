from __future__ import annotations

import os
import queue
import sys
import threading
import time
from pathlib import Path
from tkinter import filedialog, messagebox
from tkinter.scrolledtext import ScrolledText
import tkinter as tk
from tkinter import ttk

from m3u8_core import (
    DirectDownloadJob,
    DownloadJob,
    HlsError,
    VideoCandidate,
    YouTubeDownloadJob,
    candidate_score,
    discover_candidates,
    load_best_media_playlist,
    make_headers,
    sanitize_file_name,
)


APP_NAME = "Universal Video Downloader"
APP_TITLE = "通用视频下载器"


class UniversalVideoDownloaderApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title(APP_NAME)
        self.geometry("1240x900")
        self.minsize(1120, 820)
        self._apply_window_icon()

        self.ui_queue: queue.Queue[tuple[str, dict]] = queue.Queue()
        self.candidates: list[VideoCandidate] = []
        self.current_job: DirectDownloadJob | DownloadJob | YouTubeDownloadJob | None = None
        self.download_thread: threading.Thread | None = None
        self.segment_items: dict[int, int] = {}
        self.segment_status: dict[int, str] = {}
        self.last_progress_bytes = 0
        self.last_progress_time = time.time()

        default_dir = Path.home() / "Downloads" / "Video Downloader"
        self.url_var = tk.StringVar()
        self.referer_var = tk.StringVar()
        self.output_dir_var = tk.StringVar(value=str(default_dir))
        self.file_name_var = tk.StringVar(value="video.mp4")
        self.concurrency_var = tk.IntVar(value=8)
        self.keep_cache_var = tk.BooleanVar(value=True)
        self.status_var = tk.StringVar(value="准备就绪")
        self.selection_var = tk.StringVar(value="粘贴链接后解析可下载媒体")

        self._configure_style()
        self._build_ui()
        self.after(120, self._drain_queue)

    def _apply_window_icon(self) -> None:
        icon_path = _resource_path("assets/app_icon.ico")
        if icon_path.exists():
            try:
                self.iconbitmap(str(icon_path))
            except tk.TclError:
                pass

    def _configure_style(self) -> None:
        self.configure(bg="#F5F5F7")
        style = ttk.Style(self)
        style.theme_use("clam")
        base_font = ("Segoe UI", 10)
        chinese_font = ("Microsoft YaHei UI", 10)
        title_font = ("Segoe UI", 22, "bold")
        section_font = ("Segoe UI", 11, "bold")
        caption_font = ("Segoe UI", 9)

        style.configure(".", font=base_font)
        style.configure("TFrame", background="#F5F5F7")
        style.configure("Chrome.TFrame", background="#F5F5F7")
        style.configure("Card.TFrame", background="#FFFFFF", borderwidth=0, relief="flat")
        style.configure("Panel.TFrame", background="#FFFFFF")
        style.configure("Header.TFrame", background="#F5F5F7")
        style.configure("Title.TLabel", background="#F5F5F7", foreground="#1D1D1F", font=title_font)
        style.configure("Caption.TLabel", background="#F5F5F7", foreground="#6E6E73", font=caption_font)
        style.configure("CardTitle.TLabel", background="#FFFFFF", foreground="#1D1D1F", font=section_font)
        style.configure("CardText.TLabel", background="#FFFFFF", foreground="#1D1D1F", font=chinese_font)
        style.configure("Muted.TLabel", background="#FFFFFF", foreground="#6E6E73", font=caption_font)
        style.configure("Pill.TLabel", background="#E9F2FF", foreground="#0067D1", font=("Segoe UI", 9, "bold"), padding=(10, 4))
        style.configure("Tag.TLabel", background="#E8E8ED", foreground="#3A3A3C", font=("Segoe UI", 8, "bold"), padding=(8, 3))

        style.configure(
            "TEntry",
            fieldbackground="#FFFFFF",
            foreground="#1D1D1F",
            bordercolor="#D2D2D7",
            lightcolor="#D2D2D7",
            darkcolor="#D2D2D7",
            padding=(10, 8),
        )
        style.map("TEntry", bordercolor=[("focus", "#007AFF")])
        style.configure(
            "TSpinbox",
            fieldbackground="#FFFFFF",
            foreground="#1D1D1F",
            bordercolor="#D2D2D7",
            lightcolor="#D2D2D7",
            darkcolor="#D2D2D7",
            padding=(8, 6),
        )
        style.configure("TCheckbutton", background="#FFFFFF", foreground="#1D1D1F", font=chinese_font)
        style.configure("TSeparator", background="#E5E5EA")

        style.configure("TButton", padding=(14, 7), background="#FFFFFF", foreground="#1D1D1F", borderwidth=0, relief="flat", focuscolor="#FFFFFF")
        style.map("TButton", background=[("active", "#F2F2F7"), ("disabled", "#F4F4F5")], foreground=[("disabled", "#A1A1A6")])
        style.configure("Accent.TButton", background="#007AFF", foreground="#FFFFFF", borderwidth=0, relief="flat", focuscolor="#007AFF", padding=(16, 8))
        style.map("Accent.TButton", background=[("active", "#006EDB"), ("disabled", "#B7D8FF")], foreground=[("disabled", "#FFFFFF")])
        style.configure("Danger.TButton", background="#FF3B30", foreground="#FFFFFF", borderwidth=0, relief="flat", focuscolor="#FF3B30", padding=(14, 7))
        style.map("Danger.TButton", background=[("active", "#D93229"), ("disabled", "#F2B8B5")], foreground=[("disabled", "#FFFFFF")])

        style.configure("Treeview", rowheight=34, background="#FFFFFF", fieldbackground="#FFFFFF", foreground="#1D1D1F", borderwidth=0)
        style.configure("Treeview.Heading", font=("Segoe UI", 9, "bold"), background="#F5F5F7", foreground="#6E6E73", relief="flat")
        style.map("Treeview", background=[("selected", "#D8EAFF")], foreground=[("selected", "#1D1D1F")])
        style.layout("Treeview", [("Treeview.treearea", {"sticky": "nswe"})])
        style.configure(
            "Vertical.TScrollbar",
            background="#D1D1D6",
            troughcolor="#F5F5F7",
            bordercolor="#F5F5F7",
            arrowcolor="#8E8E93",
            lightcolor="#F5F5F7",
            darkcolor="#F5F5F7",
            relief="flat",
            width=12,
        )
        style.configure("Horizontal.TProgressbar", background="#007AFF", troughcolor="#E5E5EA", bordercolor="#E5E5EA", lightcolor="#007AFF", darkcolor="#007AFF")

    def _build_ui(self) -> None:
        root = ttk.Frame(self, style="Chrome.TFrame", padding=(24, 22, 24, 24))
        root.pack(fill=tk.BOTH, expand=True)

        header = ttk.Frame(root, style="Header.TFrame")
        header.pack(fill=tk.X)
        self.logo_image = None
        logo_path = _resource_path("assets/app_icon_64.png")
        if logo_path.exists():
            try:
                self.logo_image = tk.PhotoImage(file=str(logo_path))
                ttk.Label(header, image=self.logo_image, background="#F5F5F7").pack(side=tk.LEFT, padx=(0, 14))
            except tk.TclError:
                self.logo_image = None

        title_box = ttk.Frame(header, style="Header.TFrame")
        title_box.pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Label(title_box, text=APP_TITLE, style="Title.TLabel").pack(anchor=tk.W)
        ttk.Label(title_box, text="本地媒体下载与续传工作台", style="Caption.TLabel").pack(anchor=tk.W, pady=(2, 0))
        support_row = ttk.Frame(title_box, style="Header.TFrame")
        support_row.pack(anchor=tk.W, pady=(8, 0))
        for label in ("HLS / m3u8", "MP4 / WebM", "YouTube", "无 DRM 绕过"):
            ttk.Label(support_row, text=label, style="Tag.TLabel").pack(side=tk.LEFT, padx=(0, 8))
        ttk.Label(header, text="研究版", style="Pill.TLabel").pack(side=tk.RIGHT, pady=(8, 0))

        content = ttk.Frame(root, style="Chrome.TFrame")
        content.pack(fill=tk.BOTH, expand=True, pady=(22, 0))
        content.columnconfigure(1, weight=1)
        content.rowconfigure(0, weight=1)

        left = ttk.Frame(content, style="Card.TFrame", padding=16, width=360)
        left.grid(row=0, column=0, sticky=tk.NS, padx=(0, 18))
        left.grid_propagate(False)
        left.columnconfigure(0, weight=1)

        ttk.Label(left, text="输入", style="CardTitle.TLabel").grid(row=0, column=0, sticky=tk.W)
        ttk.Label(left, text="视频页面、m3u8、mp4/webm 或 YouTube 链接", style="Muted.TLabel").grid(row=1, column=0, sticky=tk.W, pady=(12, 6))
        ttk.Entry(left, textvariable=self.url_var).grid(row=2, column=0, sticky=tk.EW)

        source_actions = ttk.Frame(left, style="Panel.TFrame")
        source_actions.grid(row=3, column=0, sticky=tk.EW, pady=(12, 0))
        source_actions.columnconfigure(0, weight=1)
        source_actions.columnconfigure(1, weight=1)
        ttk.Button(source_actions, text="粘贴链接", command=self._paste_url).grid(row=0, column=0, sticky=tk.EW, padx=(0, 8))
        self.analyze_button = ttk.Button(source_actions, text="解析媒体", style="Accent.TButton", command=self._start_analyze)
        self.analyze_button.grid(row=0, column=1, sticky=tk.EW)

        ttk.Label(left, text="请求来源 Referer（可选）", style="Muted.TLabel").grid(row=4, column=0, sticky=tk.W, pady=(12, 6))
        ttk.Entry(left, textvariable=self.referer_var).grid(row=5, column=0, sticky=tk.EW)

        tk.Frame(left, bg="#E5E5EA", height=1).grid(row=6, column=0, sticky=tk.EW, pady=16)
        ttk.Label(left, text="输出", style="CardTitle.TLabel").grid(row=7, column=0, sticky=tk.W)
        ttk.Label(left, text="保存位置", style="Muted.TLabel").grid(row=8, column=0, sticky=tk.W, pady=(12, 6))
        output_row = ttk.Frame(left, style="Panel.TFrame")
        output_row.grid(row=9, column=0, sticky=tk.EW)
        output_row.columnconfigure(0, weight=1)
        ttk.Entry(output_row, textvariable=self.output_dir_var).grid(row=0, column=0, sticky=tk.EW, padx=(0, 8))
        ttk.Button(output_row, text="选择", command=self._choose_output_dir).grid(row=0, column=1)

        ttk.Label(left, text="文件名", style="Muted.TLabel").grid(row=10, column=0, sticky=tk.W, pady=(10, 6))
        ttk.Entry(left, textvariable=self.file_name_var).grid(row=11, column=0, sticky=tk.EW)

        tk.Frame(left, bg="#E5E5EA", height=1).grid(row=12, column=0, sticky=tk.EW, pady=16)
        ttk.Label(left, text="网络与续传", style="CardTitle.TLabel").grid(row=13, column=0, sticky=tk.W)
        option_row = ttk.Frame(left, style="Panel.TFrame")
        option_row.grid(row=14, column=0, sticky=tk.EW, pady=(12, 0))
        option_row.columnconfigure(1, weight=1)
        ttk.Label(option_row, text="并发任务", style="CardText.TLabel").grid(row=0, column=0, sticky=tk.W, padx=(0, 10))
        ttk.Spinbox(option_row, from_=1, to=32, textvariable=self.concurrency_var, width=8).grid(row=0, column=1, sticky=tk.W)
        ttk.Checkbutton(left, text="保留续传缓存", variable=self.keep_cache_var).grid(row=15, column=0, sticky=tk.W, pady=(14, 0))

        left.rowconfigure(16, weight=1)
        action_block = ttk.Frame(left, style="Panel.TFrame")
        action_block.grid(row=17, column=0, sticky=tk.EW, pady=(12, 0))
        action_block.columnconfigure(0, weight=1)
        action_block.columnconfigure(1, weight=1)
        self.start_button = ttk.Button(action_block, text="下载选中媒体", style="Accent.TButton", command=self._start_download, state=tk.DISABLED)
        self.start_button.grid(row=0, column=0, columnspan=2, sticky=tk.EW)
        self.pause_button = ttk.Button(action_block, text="暂停", command=self._toggle_pause, state=tk.DISABLED)
        self.pause_button.grid(row=1, column=0, sticky=tk.EW, padx=(0, 8), pady=(10, 0))
        self.stop_button = ttk.Button(action_block, text="停止", style="Danger.TButton", command=self._stop_download, state=tk.DISABLED)
        self.stop_button.grid(row=1, column=1, sticky=tk.EW, pady=(10, 0))
        self.partial_button = ttk.Button(action_block, text="合并已完成部分", command=self._combine_partial, state=tk.DISABLED)
        self.partial_button.grid(row=2, column=0, sticky=tk.EW, padx=(0, 8), pady=(10, 0))
        ttk.Button(action_block, text="打开保存位置", command=self._open_output_dir).grid(row=2, column=1, sticky=tk.EW, pady=(10, 0))

        right = ttk.Frame(content, style="Chrome.TFrame")
        right.grid(row=0, column=1, sticky=tk.NSEW)
        right.columnconfigure(0, weight=1)
        right.rowconfigure(0, weight=0)
        right.rowconfigure(1, weight=0)
        right.rowconfigure(2, weight=1)

        candidates_frame = ttk.Frame(right, style="Card.TFrame", padding=16)
        candidates_frame.grid(row=0, column=0, sticky=tk.EW)
        candidates_frame.columnconfigure(0, weight=1)
        ttk.Label(candidates_frame, text="可下载媒体", style="CardTitle.TLabel").grid(row=0, column=0, sticky=tk.W)
        self.best_button = ttk.Button(candidates_frame, text="选择推荐项", command=self._select_best_candidate, state=tk.DISABLED)
        self.best_button.grid(row=0, column=1, sticky=tk.E)

        ttk.Label(candidates_frame, textvariable=self.selection_var, style="Muted.TLabel").grid(row=1, column=0, columnspan=2, sticky=tk.W, pady=(8, 0))

        columns = ("title", "kind", "resolution", "bandwidth", "structure", "duration", "engine", "url")
        self.candidate_tree = ttk.Treeview(candidates_frame, columns=columns, show="headings", height=6, selectmode="browse")
        headings = {
            "title": ("媒体", 220),
            "kind": ("类型", 82),
            "resolution": ("画质", 82),
            "bandwidth": ("码率", 86),
            "structure": ("结构", 92),
            "duration": ("时长", 90),
            "engine": ("引擎", 82),
            "url": ("来源地址", 360),
        }
        for key, (text, width) in headings.items():
            self.candidate_tree.heading(key, text=text)
            self.candidate_tree.column(key, width=width, minwidth=50, stretch=(key in {"title", "url"}))
        tree_scroll = ttk.Scrollbar(candidates_frame, orient=tk.VERTICAL, command=self.candidate_tree.yview)
        self.candidate_tree.configure(yscrollcommand=tree_scroll.set)
        self.candidate_tree.grid(row=2, column=0, columnspan=2, sticky=tk.NSEW, pady=(10, 0))
        tree_scroll.grid(row=2, column=2, sticky=tk.NS, pady=(10, 0))
        self.candidate_tree.bind("<<TreeviewSelect>>", lambda _event: self._sync_file_name_from_selection())

        progress_frame = ttk.Frame(right, style="Card.TFrame", padding=16)
        progress_frame.grid(row=1, column=0, sticky=tk.EW, pady=(16, 0))
        progress_frame.columnconfigure(0, weight=1)
        ttk.Label(progress_frame, text="任务进度", style="CardTitle.TLabel").grid(row=0, column=0, sticky=tk.W)
        self.progress = ttk.Progressbar(progress_frame, mode="determinate", maximum=100)
        self.progress.grid(row=1, column=0, sticky=tk.EW, pady=(14, 0))
        ttk.Label(progress_frame, textvariable=self.status_var, style="Muted.TLabel").grid(row=2, column=0, sticky=tk.W, pady=(8, 0))

        grid_frame = ttk.Frame(progress_frame, style="Panel.TFrame")
        grid_frame.grid(row=3, column=0, sticky=tk.EW, pady=(12, 0))
        self.segment_canvas = tk.Canvas(grid_frame, height=92, bg="#FFFFFF", highlightthickness=0)
        self.segment_canvas.pack(fill=tk.BOTH, expand=False)
        self.segment_canvas.bind("<Configure>", lambda _event: self._redraw_segments())

        log_frame = ttk.Frame(right, style="Card.TFrame", padding=16)
        log_frame.grid(row=2, column=0, sticky=tk.NSEW, pady=(16, 0))
        log_frame.columnconfigure(0, weight=1)
        log_frame.rowconfigure(1, weight=1)
        ttk.Label(log_frame, text="任务日志", style="CardTitle.TLabel").grid(row=0, column=0, sticky=tk.W)
        self.log_text = ScrolledText(log_frame, height=8, wrap=tk.WORD, borderwidth=0, font=("Cascadia Mono", 9))
        self.log_text.grid(row=1, column=0, sticky=tk.NSEW, pady=(10, 0))
        self.log_text.configure(bg="#F8F8FA", fg="#2C2C2E", insertbackground="#2C2C2E", relief=tk.FLAT, padx=10, pady=10)

    def _paste_url(self) -> None:
        try:
            value = self.clipboard_get().strip()
        except tk.TclError:
            value = ""
        if value:
            self.url_var.set(value)

    def _choose_output_dir(self) -> None:
        selected = filedialog.askdirectory(initialdir=self.output_dir_var.get() or str(Path.home()))
        if selected:
            self.output_dir_var.set(selected)

    def _open_output_dir(self) -> None:
        path = Path(self.output_dir_var.get()).expanduser()
        path.mkdir(parents=True, exist_ok=True)
        os.startfile(path)

    def _start_analyze(self) -> None:
        url = self.url_var.get().strip()
        if not url:
            messagebox.showwarning("缺少地址", "请先输入视频页面、媒体直链、m3u8 或 YouTube 地址。")
            return
        self._set_busy_analyzing(True)
        self._clear_candidates()
        self.progress["value"] = 0
        self._draw_segments(0)
        self._log("开始解析媒体：" + url)

        thread = threading.Thread(target=self._analyze_worker, args=(url, self.referer_var.get().strip()), daemon=True)
        thread.start()

    def _analyze_worker(self, url: str, referer: str) -> None:
        try:
            candidates = discover_candidates(url, referer=referer, callback=self._core_callback)
            self.ui_queue.put(("analysis_done", {"candidates": candidates}))
        except Exception as exc:
            self.ui_queue.put(("analysis_error", {"message": str(exc)}))

    def _start_download(self) -> None:
        candidate = self._selected_candidate()
        if not candidate:
            messagebox.showwarning("未选择媒体", "请先解析并选择一个可下载媒体。")
            return

        file_name = sanitize_file_name(self.file_name_var.get(), "video")
        if not Path(file_name).suffix:
            file_name += _default_suffix_for_candidate(candidate)
        output_path = Path(self.output_dir_var.get()).expanduser() / file_name
        referer = self.referer_var.get().strip() or candidate.referer or candidate.source_url
        headers = make_headers(referer)
        concurrency = int(self.concurrency_var.get())
        keep_cache = self.keep_cache_var.get()

        self._set_downloading_state(True)
        self._draw_segments(0)
        self.status_var.set("准备下载任务")
        self._log(f"准备下载：{candidate.url}")

        self.download_thread = threading.Thread(
            target=self._download_worker,
            args=(candidate, output_path, headers, concurrency, keep_cache),
            daemon=True,
        )
        self.download_thread.start()

    def _download_worker(
        self,
        candidate: VideoCandidate,
        output_path: Path,
        headers: dict[str, str],
        concurrency: int,
        keep_cache: bool,
    ) -> None:
        try:
            if candidate.source_type == "youtube":
                job = YouTubeDownloadJob(
                    url=candidate.url,
                    output_path=output_path,
                    concurrency=concurrency,
                    callback=self._core_callback,
                )
            elif candidate.source_type == "direct":
                job = DirectDownloadJob(
                    url=candidate.url,
                    output_path=output_path,
                    headers=headers,
                    callback=self._core_callback,
                )
            else:
                playlist = load_best_media_playlist(candidate.url, headers=headers)
                job = DownloadJob(
                    playlist=playlist,
                    output_path=output_path,
                    headers=headers,
                    concurrency=concurrency,
                    keep_cache=keep_cache,
                    callback=self._core_callback,
                )
            self.ui_queue.put(("job_ready", {"job": job}))
            job.run()
        except Exception as exc:
            self.ui_queue.put(("fatal", {"message": str(exc)}))

    def _toggle_pause(self) -> None:
        if not self.current_job:
            return
        if self.current_job.pause_event.is_set():
            self.current_job.pause()
        else:
            self.current_job.resume()

    def _stop_download(self) -> None:
        if self.current_job:
            self.current_job.stop()

    def _combine_partial(self) -> None:
        if not self.current_job:
            return

        def worker() -> None:
            try:
                self.current_job.combine(require_all=False)
            except Exception as exc:
                self.ui_queue.put(("fatal", {"message": str(exc)}))

        threading.Thread(target=worker, daemon=True).start()

    def _selected_candidate(self) -> VideoCandidate | None:
        selection = self.candidate_tree.selection()
        if not selection and self.candidates:
            self._select_best_candidate()
            selection = self.candidate_tree.selection()
        if not selection:
            return None
        index = int(selection[0])
        if 0 <= index < len(self.candidates):
            return self.candidates[index]
        return None

    def _select_best_candidate(self) -> None:
        if not self.candidates:
            return
        best_index, _best = max(enumerate(self.candidates), key=lambda item: candidate_score(item[1]))
        iid = str(best_index)
        self.candidate_tree.selection_set(iid)
        self.candidate_tree.focus(iid)
        self.candidate_tree.see(iid)
        self._sync_file_name_from_selection()

    def _sync_file_name_from_selection(self) -> None:
        candidate = self._selected_candidate()
        if candidate:
            self.file_name_var.set(sanitize_file_name(candidate.title, "video") + _default_suffix_for_candidate(candidate))
            self.selection_var.set(_candidate_summary(candidate))
            if candidate.referer and not self.referer_var.get().strip():
                self.referer_var.set(candidate.referer)

    def _clear_candidates(self) -> None:
        self.candidates = []
        self.selection_var.set("粘贴链接后解析可下载媒体")
        for item in self.candidate_tree.get_children():
            self.candidate_tree.delete(item)
        self.best_button.configure(state=tk.DISABLED)
        self.start_button.configure(state=tk.DISABLED)

    def _set_busy_analyzing(self, busy: bool) -> None:
        self.analyze_button.configure(state=tk.DISABLED if busy else tk.NORMAL)
        self.status_var.set("正在解析媒体" if busy else "等待下载")

    def _set_downloading_state(self, active: bool) -> None:
        self.start_button.configure(state=tk.DISABLED if active else tk.NORMAL)
        self.analyze_button.configure(state=tk.DISABLED if active else tk.NORMAL)
        self.pause_button.configure(state=tk.NORMAL if active else tk.DISABLED)
        self.stop_button.configure(state=tk.NORMAL if active else tk.DISABLED)
        self.partial_button.configure(state=tk.NORMAL if active else tk.DISABLED)
        if not active:
            self.pause_button.configure(text="暂停")

    def _core_callback(self, event: str, payload: dict) -> None:
        self.ui_queue.put((event, payload))

    def _drain_queue(self) -> None:
        while True:
            try:
                event, payload = self.ui_queue.get_nowait()
            except queue.Empty:
                break
            self._handle_event(event, payload)
        self.after(120, self._drain_queue)

    def _handle_event(self, event: str, payload: dict) -> None:
        if event == "analysis_done":
            self._on_analysis_done(payload["candidates"])
        elif event == "analysis_error":
            self._set_busy_analyzing(False)
            self._log("解析失败：" + payload["message"], "error")
            messagebox.showerror("解析失败", payload["message"])
        elif event == "job_ready":
            self.current_job = payload["job"]
        elif event == "started":
            self._draw_segments(payload.get("total", 0))
            self._log(f"续传缓存：{payload.get('cache_dir')}")
        elif event == "segment":
            self._update_segment(payload["index"], payload["status"])
        elif event == "progress":
            self._update_progress(payload)
        elif event == "paused":
            self.pause_button.configure(text="继续")
            self.status_var.set("已暂停，当前网络请求完成后会停住")
        elif event == "resumed":
            self.pause_button.configure(text="暂停")
            self.status_var.set("继续下载")
        elif event == "stopping":
            self.status_var.set("正在停止")
            self._log("停止请求已发送，已完成的数据会保留。")
        elif event == "stopped":
            self._set_downloading_state(False)
            self.status_var.set("已停止，可再次开始继续下载")
            self._log("任务已停止。")
        elif event == "combining":
            self.status_var.set("正在合并视频")
            self._log("开始合并：" + payload.get("output", ""))
        elif event == "combined":
            self._log("合并完成：" + payload.get("output", ""))
        elif event == "completed":
            self._set_downloading_state(False)
            self.progress["value"] = 100
            self.status_var.set("下载完成")
            self._log("下载完成：" + payload.get("output", ""))
            messagebox.showinfo("下载完成", payload.get("output", ""))
        elif event == "failed":
            self._set_downloading_state(False)
            self.status_var.set(f"任务未完成：失败 {payload.get('failed')}，缺失 {payload.get('missing')}")
            self._log("任务未完成，可再次开始下载以续传失败部分。", "warning")
        elif event == "fatal":
            self._set_downloading_state(False)
            self._log("错误：" + payload.get("message", ""), "error")
            messagebox.showerror("错误", payload.get("message", "未知错误"))
        elif event == "log":
            self._log(payload.get("message", ""), payload.get("level", "info"))

    def _on_analysis_done(self, candidates: list[VideoCandidate]) -> None:
        self._set_busy_analyzing(False)
        self.candidates = candidates
        for index, candidate in enumerate(candidates):
            self.candidate_tree.insert(
                "",
                tk.END,
                iid=str(index),
                values=(
                    candidate.title,
                    _candidate_kind_label(candidate),
                    candidate.resolution or "-",
                    f"{round(candidate.bandwidth / 1000)} kbps" if candidate.bandwidth else "-",
                    _candidate_structure_label(candidate),
                    _format_duration(candidate.duration),
                    _candidate_engine_label(candidate),
                    candidate.url,
                ),
            )
        self.best_button.configure(state=tk.NORMAL)
        self.start_button.configure(state=tk.NORMAL)
        self._select_best_candidate()
        self._log(f"发现 {len(candidates)} 个可下载媒体，已选择推荐项。")

    def _update_progress(self, payload: dict) -> None:
        total = max(1, int(payload.get("total", 0)))
        done = int(payload.get("done", 0))
        failed = int(payload.get("failed", 0))
        downloading = int(payload.get("downloading", 0))
        bytes_done = int(payload.get("bytes_done", 0))

        percent = done / total * 100
        self.progress["value"] = percent

        now = time.time()
        elapsed = max(0.1, now - self.last_progress_time)
        speed = max(0, bytes_done - self.last_progress_bytes) / elapsed
        self.last_progress_bytes = bytes_done
        self.last_progress_time = now

        self.status_var.set(
            f"任务单元 {done}/{total}，失败 {failed}，进行中 {downloading}，"
            f"{_format_size(bytes_done)}，{_format_size(speed)}/s"
        )

    def _draw_segments(self, total: int) -> None:
        self.segment_status = {index: "pending" for index in range(total)}
        self._redraw_segments()

    def _redraw_segments(self) -> None:
        self.segment_canvas.delete("all")
        self.segment_items = {}
        if not self.segment_status:
            self.segment_canvas.create_text(
                16,
                18,
                text="等待媒体任务",
                anchor=tk.W,
                fill="#687386",
                font=("Microsoft YaHei UI", 10),
            )
            return

        width = max(240, self.segment_canvas.winfo_width())
        item = 11
        gap = 4
        x = 0
        y = 0
        for index in sorted(self.segment_status):
            if x + item > width:
                x = 0
                y += item + gap
            color = _status_color(self.segment_status[index])
            rect = self.segment_canvas.create_rectangle(x, y, x + item, y + item, fill=color, outline="")
            self.segment_items[index] = rect
            x += item + gap
        self.segment_canvas.configure(scrollregion=(0, 0, width, y + item))

    def _update_segment(self, index: int, status: str) -> None:
        self.segment_status[index] = status
        item = self.segment_items.get(index)
        if item:
            self.segment_canvas.itemconfigure(item, fill=_status_color(status))

    def _log(self, message: str, level: str = "info") -> None:
        if not message:
            return
        prefix = {
            "error": "[错误]",
            "warning": "[警告]",
            "debug": "[调试]",
            "info": "[信息]",
        }.get(level, "[信息]")
        self.log_text.insert(tk.END, f"{time.strftime('%H:%M:%S')} {prefix} {message}\n")
        self.log_text.see(tk.END)


def _status_color(status: str) -> str:
    return {
        "pending": "#D6DEE8",
        "downloading": "#F2C94C",
        "done": "#2A9D8F",
        "error": "#D64550",
    }.get(status, "#D6DEE8")


def _format_duration(seconds: float) -> str:
    if not seconds:
        return "-"
    seconds = int(seconds)
    hours, rem = divmod(seconds, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def _format_size(value: float) -> str:
    units = ["B", "KB", "MB", "GB", "TB"]
    size = float(value)
    for unit in units:
        if size < 1024 or unit == units[-1]:
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


def _default_suffix_for_candidate(candidate: VideoCandidate) -> str:
    if candidate.source_type == "youtube":
        return ".mp4"
    if candidate.source_type == "direct":
        suffix = Path(candidate.url.split("?", 1)[0]).suffix.lower()
        return suffix if suffix else ".mp4"
    return ".ts"


def _candidate_kind_label(candidate: VideoCandidate) -> str:
    if candidate.source_type == "youtube":
        return "YouTube"
    if candidate.source_type == "direct":
        return "直链"
    return "HLS"


def _candidate_structure_label(candidate: VideoCandidate) -> str:
    if candidate.source_type == "youtube":
        return "自动合并"
    if candidate.source_type == "direct":
        return "单文件"
    if candidate.segment_count:
        return f"{candidate.segment_count} 片段"
    return "播放列表"


def _candidate_engine_label(candidate: VideoCandidate) -> str:
    if candidate.source_type == "youtube":
        return "yt-dlp"
    if candidate.source_type == "direct":
        return "HTTP"
    return "HLS/AES" if candidate.encrypted else "HLS"


def _candidate_summary(candidate: VideoCandidate) -> str:
    parts = [_candidate_kind_label(candidate), _candidate_engine_label(candidate)]
    if candidate.resolution:
        parts.append(candidate.resolution)
    if candidate.duration:
        parts.append(_format_duration(candidate.duration))
    parts.append(_candidate_structure_label(candidate))
    return " · ".join(parts)


def _resource_path(relative_path: str) -> Path:
    base_path = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return base_path / relative_path


if __name__ == "__main__":
    try:
        app = UniversalVideoDownloaderApp()
        app.mainloop()
    except HlsError as exc:
        messagebox.showerror("错误", str(exc))
