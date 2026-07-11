from __future__ import annotations

import os
import shutil
import sys
import threading
import time
import uuid
from dataclasses import replace
from pathlib import Path
from tkinter import filedialog, messagebox
from tkinter.scrolledtext import ScrolledText
import tkinter as tk
from tkinter import ttk
from urllib.parse import urlparse

from m3u8_core import (
    CoalescingEventBuffer,
    DirectDownloadJob,
    DownloadHistoryStore,
    DownloadJob,
    DownloadRecord,
    HlsError,
    UserFacingError,
    VideoCandidate,
    YouTubeDownloadJob,
    candidate_score,
    classify_error,
    default_history_path,
    discover_candidates,
    load_best_media_playlist,
    make_headers,
    redact_url,
    sanitize_file_name,
)


APP_NAME = "Universal Video Downloader"
APP_TITLE = "通用视频下载器"
UI_REFRESH_INTERVAL_MS = 100
MAX_SEGMENT_BLOCKS = 160


class UniversalVideoDownloaderApp(tk.Tk):
    """Windows desktop shell for media discovery, download control, and local task history."""

    def __init__(self) -> None:
        super().__init__()
        self.title(APP_NAME)
        self.geometry("1220x840")
        self.minsize(1040, 720)
        self._apply_window_icon()

        self.event_buffer = CoalescingEventBuffer()
        self.candidates: list[VideoCandidate] = []
        self.current_job: DirectDownloadJob | DownloadJob | YouTubeDownloadJob | None = None
        self.current_candidate: VideoCandidate | None = None
        self.current_record_id = ""
        self.download_thread: threading.Thread | None = None
        self.is_downloading = False
        self.advanced_visible = False

        self.history_store = DownloadHistoryStore(default_history_path())
        self.history_records = self._load_history()
        self.last_history_write = 0.0
        self.current_progress_value = 0.0
        self.current_bytes_done = 0

        self.segment_total = 0
        self.segment_block_count = 0
        self.segment_items: dict[int, int] = {}
        self.segment_status: dict[int, str] = {}

        self.last_progress_bytes = 0
        self.last_progress_done = 0
        self.last_progress_time = time.monotonic()
        self.smoothed_speed = 0.0
        self.smoothed_unit_rate = 0.0

        default_dir = Path.home() / "Downloads" / "Video Downloader"
        self.url_var = tk.StringVar()
        self.referer_var = tk.StringVar()
        self.output_dir_var = tk.StringVar(value=str(default_dir))
        self.file_name_var = tk.StringVar(value="video.mp4")
        self.concurrency_var = tk.IntVar(value=8)
        self.keep_cache_var = tk.BooleanVar(value=True)
        self.status_var = tk.StringVar(value="准备就绪")
        self.selection_var = tk.StringVar(value="输入链接并解析后，这里会显示可下载媒体")
        self.progress_detail_var = tk.StringVar(value="尚未开始任务")

        self._configure_style()
        self._build_ui()
        self._refresh_history()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(UI_REFRESH_INTERVAL_MS, self._drain_events)

    def _load_history(self) -> list[DownloadRecord]:
        records = self.history_store.load()
        changed = False
        restored: list[DownloadRecord] = []
        for record in records:
            if record.status in {"preparing", "downloading", "paused"}:
                record = replace(record, status="interrupted", updated_at=time.time())
                changed = True
            restored.append(record)
        if changed:
            self.history_store.save(restored)
        return restored

    def _apply_window_icon(self) -> None:
        icon_path = _resource_path("assets/app_icon_v2.ico")
        if icon_path.exists():
            try:
                self.iconbitmap(str(icon_path))
            except tk.TclError:
                pass

    def _configure_style(self) -> None:
        self.configure(bg="#F3F4F6")
        style = ttk.Style(self)
        style.theme_use("clam")

        style.configure(".", font=("Segoe UI", 10))
        style.configure("App.TFrame", background="#F3F4F6")
        style.configure("Surface.TFrame", background="#FFFFFF")
        style.configure("Header.TFrame", background="#111827")
        style.configure("HeaderTitle.TLabel", background="#111827", foreground="#FFFFFF", font=("Segoe UI", 16, "bold"))
        style.configure("HeaderText.TLabel", background="#111827", foreground="#AEB8C8", font=("Microsoft YaHei UI", 9))
        style.configure("Section.TLabel", background="#FFFFFF", foreground="#15171A", font=("Microsoft YaHei UI", 11, "bold"))
        style.configure("Body.TLabel", background="#FFFFFF", foreground="#24262A", font=("Microsoft YaHei UI", 10))
        style.configure("Muted.TLabel", background="#FFFFFF", foreground="#6B7280", font=("Microsoft YaHei UI", 9))
        style.configure("PageTitle.TLabel", background="#F3F4F6", foreground="#15171A", font=("Microsoft YaHei UI", 16, "bold"))
        style.configure("PageText.TLabel", background="#F3F4F6", foreground="#667085", font=("Microsoft YaHei UI", 9))
        style.configure("Badge.TLabel", background="#223047", foreground="#DCE7F7", font=("Microsoft YaHei UI", 8), padding=(9, 4))

        style.configure(
            "TEntry",
            fieldbackground="#FFFFFF",
            foreground="#17191C",
            bordercolor="#D5D9E0",
            lightcolor="#D5D9E0",
            darkcolor="#D5D9E0",
            padding=(10, 8),
        )
        style.map("TEntry", bordercolor=[("focus", "#1677FF")])
        style.configure(
            "TSpinbox",
            fieldbackground="#FFFFFF",
            foreground="#17191C",
            bordercolor="#D5D9E0",
            lightcolor="#D5D9E0",
            darkcolor="#D5D9E0",
            padding=(8, 6),
        )
        style.configure("TCheckbutton", background="#FFFFFF", foreground="#24262A", font=("Microsoft YaHei UI", 9))
        style.configure("TButton", padding=(13, 8), background="#FFFFFF", foreground="#24262A", borderwidth=1, relief="flat")
        style.map("TButton", background=[("active", "#F3F5F8"), ("disabled", "#F5F6F8")], foreground=[("disabled", "#A5ABB4")])
        style.configure("Primary.TButton", padding=(16, 9), background="#1677FF", foreground="#FFFFFF", borderwidth=0, relief="flat")
        style.map("Primary.TButton", background=[("active", "#0F68E0"), ("disabled", "#AFCFFF")], foreground=[("disabled", "#FFFFFF")])
        style.configure("Danger.TButton", background="#E5484D", foreground="#FFFFFF", borderwidth=0)
        style.map("Danger.TButton", background=[("active", "#CD3D42"), ("disabled", "#F0B8BA")], foreground=[("disabled", "#FFFFFF")])
        style.configure("Link.TButton", padding=(4, 3), background="#FFFFFF", foreground="#1677FF", borderwidth=0)
        style.map("Link.TButton", background=[("active", "#FFFFFF")], foreground=[("active", "#0F68E0")])
        style.configure("Compact.TButton", padding=(8, 6), background="#FFFFFF", foreground="#24262A", borderwidth=1, relief="flat")
        style.map("Compact.TButton", background=[("active", "#F3F5F8"), ("disabled", "#F5F6F8")], foreground=[("disabled", "#A5ABB4")])
        style.configure("CompactDanger.TButton", padding=(8, 6), background="#E5484D", foreground="#FFFFFF", borderwidth=0)
        style.map("CompactDanger.TButton", background=[("active", "#CD3D42"), ("disabled", "#F0B8BA")], foreground=[("disabled", "#FFFFFF")])

        style.configure("TNotebook", background="#F3F4F6", borderwidth=0)
        style.configure("TNotebook.Tab", padding=(18, 9), background="#E6E9EE", foreground="#5F6672", borderwidth=0)
        style.map("TNotebook.Tab", background=[("selected", "#FFFFFF")], foreground=[("selected", "#17191C")])
        style.configure("Treeview", rowheight=36, background="#FFFFFF", fieldbackground="#FFFFFF", foreground="#25272B", borderwidth=0)
        style.configure("Treeview.Heading", font=("Microsoft YaHei UI", 9, "bold"), background="#F5F6F8", foreground="#687080", relief="flat")
        style.map("Treeview", background=[("selected", "#E7F1FF")], foreground=[("selected", "#15171A")])
        style.layout("Treeview", [("Treeview.treearea", {"sticky": "nswe"})])
        style.configure("Horizontal.TProgressbar", background="#1677FF", troughcolor="#E7EAF0", bordercolor="#E7EAF0", lightcolor="#1677FF", darkcolor="#1677FF")

    def _build_ui(self) -> None:
        header = ttk.Frame(self, style="Header.TFrame", padding=(22, 10))
        header.pack(fill=tk.X)

        self.logo_image = None
        logo_path = _resource_path("assets/app_brand_v2_40.png")
        if logo_path.exists():
            try:
                self.logo_image = tk.PhotoImage(file=str(logo_path))
                ttk.Label(header, image=self.logo_image, background="#111827").pack(side=tk.LEFT, padx=(0, 13))
            except tk.TclError:
                self.logo_image = None

        title_box = ttk.Frame(header, style="Header.TFrame")
        title_box.pack(side=tk.LEFT)
        ttk.Label(title_box, text=APP_TITLE, style="HeaderTitle.TLabel").pack(anchor=tk.W)
        ttk.Label(title_box, text="媒体发现、断点续传与本地任务管理", style="HeaderText.TLabel").pack(anchor=tk.W, pady=(2, 0))

        capability_text = "FFmpeg 已就绪" if shutil.which("ffmpeg") else "FFmpeg 未检测到"
        ttk.Label(header, text=capability_text, style="Badge.TLabel").pack(side=tk.RIGHT, padx=(8, 0))
        ttk.Label(header, text="HLS  /  HTTP  /  yt-dlp", style="Badge.TLabel").pack(side=tk.RIGHT)

        shell = ttk.Frame(self, style="App.TFrame", padding=(18, 12, 18, 16))
        shell.pack(fill=tk.BOTH, expand=True)
        self.main_notebook = ttk.Notebook(shell)
        self.main_notebook.pack(fill=tk.BOTH, expand=True)

        self.download_tab = ttk.Frame(self.main_notebook, style="App.TFrame", padding=(0, 10, 0, 0))
        self.history_tab = ttk.Frame(self.main_notebook, style="App.TFrame", padding=(0, 10, 0, 0))
        self.main_notebook.add(self.download_tab, text="新建下载")
        self.main_notebook.add(self.history_tab, text="任务记录")

        self._build_download_tab()
        self._build_history_tab()

    def _build_download_tab(self) -> None:
        tab = self.download_tab
        tab.columnconfigure(0, weight=1)
        tab.rowconfigure(1, weight=1, minsize=216)
        tab.rowconfigure(2, weight=0, minsize=128)

        source = ttk.Frame(tab, style="Surface.TFrame", padding=14)
        source.grid(row=0, column=0, sticky=tk.EW)
        source.columnconfigure(0, weight=1)

        title_row = ttk.Frame(source, style="Surface.TFrame")
        title_row.grid(row=0, column=0, columnspan=4, sticky=tk.EW)
        title_row.columnconfigure(0, weight=1)
        ttk.Label(title_row, text="添加媒体链接", style="Section.TLabel").grid(row=0, column=0, sticky=tk.W)
        self.advanced_button = ttk.Button(title_row, text="显示高级选项", style="Link.TButton", command=self._toggle_advanced)
        self.advanced_button.grid(row=0, column=1, sticky=tk.E)

        self.url_entry = ttk.Entry(source, textvariable=self.url_var)
        self.url_entry.grid(row=1, column=0, sticky=tk.EW, pady=(12, 0), padx=(0, 8))
        self.url_entry.bind("<Return>", lambda _event: self._start_analyze())
        ttk.Button(source, text="粘贴", command=self._paste_url).grid(row=1, column=1, pady=(12, 0), padx=(0, 8))
        self.analyze_button = ttk.Button(source, text="解析媒体", style="Primary.TButton", command=self._start_analyze)
        self.analyze_button.grid(row=1, column=2, pady=(12, 0))

        self.notice_frame = tk.Frame(source, bg="#E9F2FF", highlightthickness=0)
        self.notice_frame.grid(row=2, column=0, columnspan=3, sticky=tk.EW, pady=(12, 0))
        self.notice_frame.columnconfigure(1, weight=1)
        self.notice_title = tk.Label(self.notice_frame, text="", bg="#E9F2FF", fg="#1559A6", font=("Microsoft YaHei UI", 9, "bold"), anchor=tk.W)
        self.notice_title.grid(row=0, column=0, sticky=tk.W, padx=(12, 8), pady=9)
        self.notice_text = tk.Label(self.notice_frame, text="", bg="#E9F2FF", fg="#3E628D", font=("Microsoft YaHei UI", 9), anchor=tk.W, justify=tk.LEFT, wraplength=900)
        self.notice_text.grid(row=0, column=1, sticky=tk.EW, padx=(0, 12), pady=9)
        self.notice_frame.grid_remove()

        self.advanced_frame = ttk.Frame(source, style="Surface.TFrame")
        self.advanced_frame.grid(row=3, column=0, columnspan=3, sticky=tk.EW, pady=(12, 0))
        self.advanced_frame.columnconfigure(1, weight=1)
        ttk.Label(self.advanced_frame, text="Referer", style="Muted.TLabel").grid(row=0, column=0, sticky=tk.W, padx=(0, 8))
        ttk.Entry(self.advanced_frame, textvariable=self.referer_var).grid(row=0, column=1, sticky=tk.EW, padx=(0, 16))
        ttk.Label(self.advanced_frame, text="并发", style="Muted.TLabel").grid(row=0, column=2, sticky=tk.W, padx=(0, 8))
        ttk.Spinbox(self.advanced_frame, from_=1, to=32, textvariable=self.concurrency_var, width=7).grid(row=0, column=3, sticky=tk.W, padx=(0, 16))
        ttk.Checkbutton(self.advanced_frame, text="保留续传缓存", variable=self.keep_cache_var).grid(row=0, column=4, sticky=tk.W)
        self.advanced_frame.grid_remove()

        workspace = ttk.Frame(tab, style="App.TFrame")
        workspace.grid(row=1, column=0, sticky=tk.NSEW, pady=(10, 0))
        workspace.columnconfigure(0, weight=1)
        workspace.columnconfigure(1, minsize=315)
        workspace.rowconfigure(0, weight=1)

        candidates_frame = ttk.Frame(workspace, style="Surface.TFrame", padding=14)
        candidates_frame.grid(row=0, column=0, sticky=tk.NSEW, padx=(0, 14))
        candidates_frame.columnconfigure(0, weight=1)
        candidates_frame.rowconfigure(2, weight=1)

        heading = ttk.Frame(candidates_frame, style="Surface.TFrame")
        heading.grid(row=0, column=0, columnspan=2, sticky=tk.EW)
        heading.columnconfigure(0, weight=1)
        ttk.Label(heading, text="可下载媒体", style="Section.TLabel").grid(row=0, column=0, sticky=tk.W)
        self.best_button = ttk.Button(heading, text="选择推荐项", command=self._select_best_candidate, state=tk.DISABLED)
        self.best_button.grid(row=0, column=1, sticky=tk.E)
        ttk.Label(candidates_frame, textvariable=self.selection_var, style="Muted.TLabel").grid(row=1, column=0, sticky=tk.W, pady=(7, 10))

        columns = ("quality", "format", "protocol", "duration", "bitrate", "origin")
        self.candidate_tree = ttk.Treeview(candidates_frame, columns=columns, show="headings", selectmode="browse", height=8)
        headings = {
            "quality": ("画质", 90),
            "format": ("格式", 70),
            "protocol": ("协议", 75),
            "duration": ("时长", 75),
            "bitrate": ("码率", 90),
            "origin": ("来源", 175),
        }
        for key, (label, width) in headings.items():
            self.candidate_tree.heading(key, text=label)
            self.candidate_tree.column(key, width=width, minwidth=65, stretch=(key == "origin"))
        tree_scroll = ttk.Scrollbar(candidates_frame, orient=tk.VERTICAL, command=self.candidate_tree.yview)
        self.candidate_tree.configure(yscrollcommand=tree_scroll.set)
        self.candidate_tree.grid(row=2, column=0, sticky=tk.NSEW)
        tree_scroll.grid(row=2, column=1, sticky=tk.NS)
        self.candidate_tree.bind("<<TreeviewSelect>>", lambda _event: self._sync_selection())

        action = ttk.Frame(workspace, style="Surface.TFrame", padding=12)
        action.grid(row=0, column=1, sticky=tk.NSEW)
        action.columnconfigure(0, weight=1)
        action_heading = ttk.Frame(action, style="Surface.TFrame")
        action_heading.grid(row=0, column=0, sticky=tk.EW)
        action_heading.columnconfigure(0, weight=1)
        ttk.Label(action_heading, text="保存与任务", style="Section.TLabel").grid(row=0, column=0, sticky=tk.W)
        ttk.Button(action_heading, text="打开目录", style="Link.TButton", command=self._open_output_dir).grid(row=0, column=1, sticky=tk.E)

        output_row = ttk.Frame(action, style="Surface.TFrame")
        output_row.grid(row=1, column=0, sticky=tk.EW, pady=(4, 0))
        output_row.columnconfigure(1, weight=1)
        ttk.Label(output_row, text="目录", style="Muted.TLabel", width=5).grid(row=0, column=0, sticky=tk.W)
        ttk.Entry(output_row, textvariable=self.output_dir_var).grid(row=0, column=1, sticky=tk.EW, padx=(0, 7))
        ttk.Button(output_row, text="选择", command=self._choose_output_dir).grid(row=0, column=2)

        file_row = ttk.Frame(action, style="Surface.TFrame")
        file_row.grid(row=2, column=0, sticky=tk.EW, pady=(3, 0))
        file_row.columnconfigure(1, weight=1)
        ttk.Label(file_row, text="文件", style="Muted.TLabel", width=5).grid(row=0, column=0, sticky=tk.W)
        ttk.Entry(file_row, textvariable=self.file_name_var).grid(row=0, column=1, sticky=tk.EW)
        ttk.Separator(action).grid(row=3, column=0, sticky=tk.EW, pady=3)

        self.start_button = ttk.Button(action, text="开始下载", style="Primary.TButton", command=self._start_download, state=tk.DISABLED)
        self.start_button.grid(row=4, column=0, sticky=tk.EW)
        controls = ttk.Frame(action, style="Surface.TFrame")
        controls.grid(row=5, column=0, sticky=tk.EW, pady=(4, 0))
        controls.columnconfigure(0, weight=1)
        controls.columnconfigure(1, weight=1)
        controls.columnconfigure(2, weight=0)
        self.task_controls = controls
        self.pause_button = ttk.Button(controls, text="暂停", style="Compact.TButton", command=self._toggle_pause, state=tk.DISABLED)
        self.pause_button.grid(row=0, column=0, sticky=tk.EW, padx=(0, 6))
        self.stop_button = ttk.Button(controls, text="停止", style="CompactDanger.TButton", command=self._stop_download, state=tk.DISABLED)
        self.stop_button.grid(row=0, column=1, sticky=tk.EW)
        self.partial_button = ttk.Button(controls, text="合并部分", style="Compact.TButton", command=self._combine_partial, state=tk.DISABLED)
        self.partial_button.grid(row=0, column=2, sticky=tk.EW, padx=(6, 0))
        self.partial_button.grid_remove()

        activity = ttk.Notebook(tab)
        activity.grid(row=2, column=0, sticky=tk.NSEW, pady=(10, 0))
        progress_tab = ttk.Frame(activity, style="Surface.TFrame", padding=12)
        log_tab = ttk.Frame(activity, style="Surface.TFrame", padding=12)
        activity.add(progress_tab, text="任务进度")
        activity.add(log_tab, text="活动日志")

        progress_tab.columnconfigure(0, weight=1)
        self.progress = ttk.Progressbar(progress_tab, mode="determinate", maximum=100)
        self.progress.grid(row=0, column=0, sticky=tk.EW)
        ttk.Label(progress_tab, textvariable=self.progress_detail_var, style="Muted.TLabel").grid(row=1, column=0, sticky=tk.W, pady=(8, 0))
        self.segment_canvas = tk.Canvas(progress_tab, height=34, bg="#FFFFFF", highlightthickness=0)
        self.segment_canvas.grid(row=2, column=0, sticky=tk.EW, pady=(8, 0))
        self.segment_canvas.bind("<Configure>", lambda _event: self._redraw_segments())

        log_tab.columnconfigure(0, weight=1)
        log_tab.rowconfigure(0, weight=1)
        self.log_text = ScrolledText(log_tab, height=6, wrap=tk.WORD, borderwidth=0, font=("Cascadia Mono", 9))
        self.log_text.grid(row=0, column=0, sticky=tk.NSEW)
        self.log_text.configure(bg="#F7F8FA", fg="#30343B", insertbackground="#30343B", relief=tk.FLAT, padx=10, pady=9)

    def _build_history_tab(self) -> None:
        tab = self.history_tab
        tab.columnconfigure(0, weight=1)
        tab.rowconfigure(1, weight=1)

        toolbar = ttk.Frame(tab, style="Surface.TFrame", padding=16)
        toolbar.grid(row=0, column=0, sticky=tk.EW)
        toolbar.columnconfigure(0, weight=1)
        ttk.Label(toolbar, text="本地任务记录", style="Section.TLabel").grid(row=0, column=0, sticky=tk.W)
        ttk.Label(toolbar, text="仅保存脱敏来源与本机输出信息", style="Muted.TLabel").grid(row=1, column=0, sticky=tk.W, pady=(3, 0))
        ttk.Button(toolbar, text="打开文件夹", command=self._open_history_output).grid(row=0, column=1, rowspan=2, padx=(8, 0))
        ttk.Button(toolbar, text="重新填入", command=self._reuse_history).grid(row=0, column=2, rowspan=2, padx=(8, 0))
        ttk.Button(toolbar, text="清除已完成", command=self._clear_completed_history).grid(row=0, column=3, rowspan=2, padx=(8, 0))

        list_frame = ttk.Frame(tab, style="Surface.TFrame", padding=16)
        list_frame.grid(row=1, column=0, sticky=tk.NSEW, pady=(14, 0))
        list_frame.columnconfigure(0, weight=1)
        list_frame.rowconfigure(0, weight=1)
        columns = ("title", "type", "status", "progress", "size", "updated", "location")
        self.history_tree = ttk.Treeview(list_frame, columns=columns, show="headings", selectmode="browse")
        headings = {
            "title": ("任务", 190),
            "type": ("类型", 65),
            "status": ("状态", 70),
            "progress": ("进度", 60),
            "size": ("已下载", 85),
            "updated": ("更新时间", 125),
            "location": ("保存位置", 260),
        }
        for key, (label, width) in headings.items():
            self.history_tree.heading(key, text=label)
            self.history_tree.column(key, width=width, minwidth=60, stretch=key in {"title", "location"})
        scroll = ttk.Scrollbar(list_frame, orient=tk.VERTICAL, command=self.history_tree.yview)
        self.history_tree.configure(yscrollcommand=scroll.set)
        self.history_tree.grid(row=0, column=0, sticky=tk.NSEW)
        scroll.grid(row=0, column=1, sticky=tk.NS)
        self.history_tree.bind("<Double-1>", lambda _event: self._open_history_output())

    def _toggle_advanced(self) -> None:
        self.advanced_visible = not self.advanced_visible
        if self.advanced_visible:
            self.advanced_frame.grid()
            self.advanced_button.configure(text="隐藏高级选项")
        else:
            self.advanced_frame.grid_remove()
            self.advanced_button.configure(text="显示高级选项")

    def _paste_url(self) -> None:
        try:
            value = self.clipboard_get().strip()
        except tk.TclError:
            value = ""
        if value:
            self.url_var.set(value)
            self.url_entry.focus_set()

    def _choose_output_dir(self) -> None:
        selected = filedialog.askdirectory(initialdir=self.output_dir_var.get() or str(Path.home()))
        if selected:
            self.output_dir_var.set(selected)

    def _open_output_dir(self) -> None:
        try:
            path = Path(self.output_dir_var.get()).expanduser()
            path.mkdir(parents=True, exist_ok=True)
            os.startfile(path)
        except OSError as exc:
            self._show_error(classify_error(exc))

    def _start_analyze(self) -> None:
        url = self.url_var.get().strip()
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            self._show_notice("warning", "链接格式不正确", "请输入完整的 http 或 https 视频页面、媒体直链或播放列表地址。")
            self.url_entry.focus_set()
            return

        self._hide_notice()
        self._set_busy_analyzing(True)
        self._clear_candidates()
        self.progress.stop()
        self.progress.configure(mode="indeterminate")
        self.progress.start(12)
        self.progress_detail_var.set("正在检查页面、媒体地址和通用解析器")
        self._draw_segments(0)
        self._log("开始解析媒体：" + redact_url(url))
        threading.Thread(target=self._analyze_worker, args=(url, self.referer_var.get().strip()), daemon=True).start()

    def _analyze_worker(self, url: str, referer: str) -> None:
        try:
            candidates = discover_candidates(url, referer=referer, callback=self._core_callback)
            self.event_buffer.put("analysis_done", {"candidates": candidates})
        except Exception as exc:
            self.event_buffer.put("analysis_error", {"error": exc})

    def _start_download(self) -> None:
        candidate = self._selected_candidate()
        if not candidate:
            self._show_notice("warning", "尚未选择媒体", "先解析链接，然后从列表中选择一个媒体版本。")
            return

        file_name = sanitize_file_name(self.file_name_var.get(), "video")
        if not Path(file_name).suffix:
            file_name += _default_suffix_for_candidate(candidate)
        output_dir = Path(self.output_dir_var.get()).expanduser()
        try:
            output_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            self._show_error(classify_error(exc))
            return
        output_path = _available_output_path(output_dir / file_name)
        if output_path.name != file_name:
            self.file_name_var.set(output_path.name)
            self._show_notice("info", "已避免覆盖现有文件", f"本次将保存为 {output_path.name}")

        referer = self.referer_var.get().strip() or candidate.referer or candidate.source_url
        headers = make_headers(referer)
        concurrency = max(1, min(32, int(self.concurrency_var.get())))
        keep_cache = self.keep_cache_var.get()

        self.current_candidate = candidate
        self.current_record_id = uuid.uuid4().hex
        self._create_history_record(candidate, output_path)
        self._reset_progress_estimator()
        self._set_downloading_state(True)
        self._draw_segments(0)
        self.progress.stop()
        self.progress.configure(mode="determinate", maximum=100, value=0)
        self.status_var.set("正在准备下载")
        self.progress_detail_var.set("正在建立下载任务")
        self._log(f"准备下载：{redact_url(candidate.url)}")

        self.download_thread = threading.Thread(
            target=self._download_worker,
            args=(candidate, output_path, headers, concurrency, keep_cache, referer),
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
        referer: str,
    ) -> None:
        try:
            if candidate.source_type in {"youtube", "ytdlp"}:
                job = YouTubeDownloadJob(candidate.url, output_path, concurrency=concurrency, referer=referer, callback=self._core_callback)
            elif candidate.source_type == "direct":
                job = DirectDownloadJob(candidate.url, output_path, headers=headers, callback=self._core_callback)
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
            self.event_buffer.put("job_ready", {"job": job})
            job.run()
        except Exception as exc:
            self.event_buffer.put("fatal", {"error": exc})

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
        if not isinstance(self.current_job, DownloadJob):
            return

        def worker() -> None:
            try:
                self.current_job.combine(require_all=False)
            except Exception as exc:
                self.event_buffer.put("fatal", {"error": exc})

        threading.Thread(target=worker, daemon=True).start()

    def _selected_candidate(self) -> VideoCandidate | None:
        selection = self.candidate_tree.selection()
        if not selection:
            return None
        try:
            index = int(selection[0])
        except (TypeError, ValueError):
            return None
        return self.candidates[index] if 0 <= index < len(self.candidates) else None

    def _select_best_candidate(self) -> None:
        if not self.candidates:
            return
        best_index, _best = max(enumerate(self.candidates), key=lambda item: candidate_score(item[1]))
        iid = str(best_index)
        self.candidate_tree.selection_set(iid)
        self.candidate_tree.focus(iid)
        self.candidate_tree.see(iid)
        self._sync_selection()

    def _sync_selection(self) -> None:
        candidate = self._selected_candidate()
        if not candidate:
            return
        file_stem = sanitize_file_name(candidate.title.split(" / ", 1)[0], "video")
        self.file_name_var.set(file_stem + _default_suffix_for_candidate(candidate))
        self.selection_var.set(_candidate_summary(candidate))
        if candidate.referer and not self.referer_var.get().strip():
            self.referer_var.set(candidate.referer)
        if candidate.source_type == "hls":
            self.task_controls.columnconfigure(2, weight=1)
            self.partial_button.grid()
        else:
            self.partial_button.grid_remove()
            self.task_controls.columnconfigure(2, weight=0)

    def _clear_candidates(self) -> None:
        self.candidates = []
        self.selection_var.set("正在查找可下载媒体")
        for item in self.candidate_tree.get_children():
            self.candidate_tree.delete(item)
        self.best_button.configure(state=tk.DISABLED)
        self.start_button.configure(state=tk.DISABLED)

    def _set_busy_analyzing(self, busy: bool) -> None:
        self.analyze_button.configure(state=tk.DISABLED if busy else tk.NORMAL)
        self.status_var.set("正在解析媒体" if busy else "等待开始下载")

    def _set_downloading_state(self, active: bool) -> None:
        self.is_downloading = active
        self.start_button.configure(state=tk.DISABLED if active else (tk.NORMAL if self.candidates else tk.DISABLED))
        self.analyze_button.configure(state=tk.DISABLED if active else tk.NORMAL)
        pause_supported = active and self.current_candidate is not None and self.current_candidate.source_type != "ytdlp" and self.current_candidate.source_type != "youtube"
        self.pause_button.configure(state=tk.NORMAL if pause_supported else tk.DISABLED)
        self.stop_button.configure(state=tk.NORMAL if active else tk.DISABLED)
        self.partial_button.configure(state=tk.NORMAL if active and self.current_candidate and self.current_candidate.source_type == "hls" else tk.DISABLED)
        if not active:
            self.pause_button.configure(text="暂停")

    def _core_callback(self, event: str, payload: dict) -> None:
        self.event_buffer.put(event, payload)

    def _drain_events(self) -> None:
        for event, payload in self.event_buffer.drain():
            self._handle_event(event, payload)
        self.after(UI_REFRESH_INTERVAL_MS, self._drain_events)

    def _handle_event(self, event: str, payload: dict) -> None:
        if event == "analysis_done":
            self._on_analysis_done(payload["candidates"])
        elif event == "analysis_error":
            self.progress.stop()
            self.progress.configure(mode="determinate", value=0)
            self._set_busy_analyzing(False)
            self._show_error(classify_error(payload.get("error", "解析失败")))
        elif event == "job_ready":
            self.current_job = payload["job"]
        elif event == "started":
            self._draw_segments(int(payload.get("total", 0)))
            self._history_update(status="downloading", force=True)
            self._log(f"已载入续传缓存，共 {payload.get('total', 0)} 个任务单元")
        elif event == "segment":
            self._update_segment(int(payload["index"]), str(payload["status"]))
        elif event == "progress":
            self._update_progress(payload)
        elif event == "paused":
            self.pause_button.configure(text="继续")
            self.status_var.set("已暂停")
            self._history_update(status="paused", force=True)
        elif event == "resumed":
            self.pause_button.configure(text="暂停")
            self.status_var.set("继续下载")
            self._history_update(status="downloading", force=True)
        elif event == "stopping":
            self.status_var.set("正在安全停止")
            self._log("停止请求已发送，已完成数据和续传缓存会保留。")
        elif event == "stopped":
            self._set_downloading_state(False)
            self.status_var.set("已停止，可从原链接继续")
            self._history_update(status="stopped", force=True)
            self.current_job = None
            self._show_notice("info", "任务已停止", "已下载数据仍保留在本机，再次开始相同任务时会尝试续传。")
        elif event == "combining":
            self.status_var.set("正在封装媒体文件")
            self._log("开始生成输出文件")
        elif event == "combined":
            self._log("输出文件已生成：" + Path(payload.get("output", "")).name)
        elif event == "completed":
            self._set_downloading_state(False)
            self.progress["value"] = 100
            self.status_var.set("下载完成")
            self.progress_detail_var.set("100% · 文件已写入保存位置")
            self._history_update(status="completed", progress=100.0, force=True)
            output = Path(payload.get("output", ""))
            self._log("下载完成：" + output.name)
            self._show_notice("success", "下载完成", f"{output.name} 已保存到 {output.parent}")
            self.current_job = None
        elif event == "failed":
            self._set_downloading_state(False)
            message = f"失败 {payload.get('failed', 0)} 个，缺失 {payload.get('missing', 0)} 个任务单元"
            self.status_var.set("任务未完成")
            self._history_update(status="failed", error=UserFacingError("partial_failure", "部分下载失败", message, "保留缓存并重新开始任务。"), force=True)
            self._show_notice("warning", "任务尚未完成", message + "。重新开始会继续未完成部分。")
            self.current_job = None
        elif event == "fatal":
            self._set_downloading_state(False)
            error = classify_error(payload.get("error") or payload.get("message", "未知错误"))
            self._history_update(status="failed", error=error, force=True)
            self._show_error(error)
            self.current_job = None
        elif event == "log":
            self._log(str(payload.get("message", "")), str(payload.get("level", "info")))

    def _on_analysis_done(self, candidates: list[VideoCandidate]) -> None:
        self.progress.stop()
        self.progress.configure(mode="determinate", value=0)
        self.progress_detail_var.set("解析完成，等待开始下载")
        self._set_busy_analyzing(False)
        self.candidates = candidates
        for index, candidate in enumerate(candidates):
            self.candidate_tree.insert(
                "",
                tk.END,
                iid=str(index),
                values=(
                    candidate.resolution or "自动",
                    _candidate_format_label(candidate),
                    _candidate_engine_label(candidate),
                    _format_duration(candidate.duration),
                    f"{round(candidate.bandwidth / 1000)} kbps" if candidate.bandwidth else "-",
                    _candidate_origin_label(candidate),
                ),
            )
        self.best_button.configure(state=tk.NORMAL)
        self.start_button.configure(state=tk.NORMAL)
        self._select_best_candidate()
        self._log(f"发现 {len(candidates)} 个可下载媒体，已选择推荐项。")
        self._show_notice("success", "解析完成", f"找到 {len(candidates)} 个媒体版本，已按画质与码率排序。")

    def _reset_progress_estimator(self) -> None:
        self.last_progress_bytes = 0
        self.last_progress_done = 0
        self.last_progress_time = time.monotonic()
        self.smoothed_speed = 0.0
        self.smoothed_unit_rate = 0.0
        self.current_progress_value = 0.0
        self.current_bytes_done = 0

    def _update_progress(self, payload: dict) -> None:
        total = max(1, int(payload.get("total", 0)))
        done = max(0, int(payload.get("done", 0)))
        failed = max(0, int(payload.get("failed", 0)))
        downloading = max(0, int(payload.get("downloading", 0)))
        bytes_done = max(0, int(payload.get("bytes_done", 0)))
        percent = min(100.0, done / total * 100)
        self.current_progress_value = percent
        self.current_bytes_done = max(self.current_bytes_done, bytes_done)
        self.progress["value"] = percent

        now = time.monotonic()
        elapsed = max(0.05, now - self.last_progress_time)
        byte_delta = max(0, bytes_done - self.last_progress_bytes)
        done_delta = max(0, done - self.last_progress_done)
        if byte_delta:
            sample_speed = byte_delta / elapsed
            self.smoothed_speed = sample_speed if not self.smoothed_speed else self.smoothed_speed * 0.72 + sample_speed * 0.28
        if done_delta:
            sample_rate = done_delta / elapsed
            self.smoothed_unit_rate = sample_rate if not self.smoothed_unit_rate else self.smoothed_unit_rate * 0.72 + sample_rate * 0.28

        self.last_progress_bytes = max(self.last_progress_bytes, bytes_done)
        self.last_progress_done = max(self.last_progress_done, done)
        self.last_progress_time = now

        eta = (total - done) / self.smoothed_unit_rate if self.smoothed_unit_rate > 0 and done < total else 0
        parts = [f"{percent:.0f}%", f"{done}/{total}", _format_size(bytes_done)]
        if self.smoothed_speed > 0:
            parts.append(f"{_format_size(self.smoothed_speed)}/s")
        if eta > 0:
            parts.append("剩余 " + _format_eta(eta))
        if failed:
            parts.append(f"失败 {failed}")
        elif downloading:
            parts.append(f"进行中 {downloading}")
        self.progress_detail_var.set(" · ".join(parts))
        self._history_update(progress=percent, bytes_done=bytes_done)

    def _draw_segments(self, total: int) -> None:
        self.segment_total = max(0, total)
        self.segment_block_count = min(self.segment_total, MAX_SEGMENT_BLOCKS)
        self.segment_status = {}
        self._redraw_segments()

    def _redraw_segments(self) -> None:
        if not hasattr(self, "segment_canvas"):
            return
        self.segment_canvas.delete("all")
        self.segment_items = {}
        if self.segment_block_count <= 0:
            self.segment_canvas.create_text(2, 20, text="等待下载任务", anchor=tk.W, fill="#77808E", font=("Microsoft YaHei UI", 9))
            return

        width = max(320, self.segment_canvas.winfo_width())
        gap = 3
        block_width = max(3, min(10, (width - gap * (self.segment_block_count - 1)) / self.segment_block_count))
        usable = max(1, int(width // (block_width + gap)))
        rows = 1 if self.segment_block_count <= usable else 2
        per_row = (self.segment_block_count + rows - 1) // rows
        block_width = max(3, (width - gap * (per_row - 1)) / max(1, per_row))
        block_height = 12
        for block in range(self.segment_block_count):
            row, column = divmod(block, per_row)
            x = column * (block_width + gap)
            y = row * (block_height + 8)
            rect = self.segment_canvas.create_rectangle(x, y, x + block_width, y + block_height, fill="#DDE2EA", outline="")
            self.segment_items[block] = rect

    def _update_segment(self, index: int, status: str) -> None:
        if self.segment_total <= 0 or index < 0:
            return
        self.segment_status[index] = status
        block = min(self.segment_block_count - 1, int(index * self.segment_block_count / self.segment_total))
        item = self.segment_items.get(block)
        if item:
            self.segment_canvas.itemconfigure(item, fill=_status_color(self._block_status(block)))

    def _block_status(self, block: int) -> str:
        start = int(block * self.segment_total / self.segment_block_count)
        end = int((block + 1) * self.segment_total / self.segment_block_count)
        statuses = [self.segment_status.get(index, "pending") for index in range(start, max(start + 1, end))]
        if "error" in statuses:
            return "error"
        if "downloading" in statuses:
            return "downloading"
        if all(status == "done" for status in statuses):
            return "done"
        if "done" in statuses:
            return "partial"
        return "pending"

    def _create_history_record(self, candidate: VideoCandidate, output_path: Path) -> None:
        source_url = redact_url(candidate.source_url or candidate.url)
        host = urlparse(source_url).hostname or ""
        record = DownloadRecord(
            record_id=self.current_record_id,
            title=sanitize_file_name(candidate.title.split(" / ", 1)[0], "video"),
            source_type=candidate.source_type,
            source_url=source_url,
            source_host=host,
            output_path=str(output_path),
            status="preparing",
            updated_at=time.time(),
        )
        self.history_records = self.history_store.upsert(record)
        self._refresh_history()

    def _history_update(
        self,
        status: str | None = None,
        progress: float | None = None,
        bytes_done: int | None = None,
        error: UserFacingError | None = None,
        force: bool = False,
    ) -> None:
        if not self.current_record_id:
            return
        now = time.time()
        if progress is not None:
            self.current_progress_value = max(0.0, min(100.0, progress))
        if bytes_done is not None:
            self.current_bytes_done = max(self.current_bytes_done, bytes_done)
        if not force and now - self.last_history_write < 1.0:
            return
        current = next((item for item in self.history_records if item.record_id == self.current_record_id), None)
        if current is None:
            return
        updated = replace(
            current,
            status=status or current.status,
            progress=max(current.progress, self.current_progress_value),
            bytes_done=max(current.bytes_done, self.current_bytes_done),
            updated_at=now,
            error_code=error.code if error else current.error_code,
            error_message=error.message if error else current.error_message,
        )
        self.history_records = self.history_store.upsert(updated)
        self.last_history_write = now
        self._refresh_history()

    def _refresh_history(self) -> None:
        if not hasattr(self, "history_tree"):
            return
        for item in self.history_tree.get_children():
            self.history_tree.delete(item)
        for record in self.history_records:
            self.history_tree.insert(
                "",
                tk.END,
                iid=record.record_id,
                values=(
                    record.title,
                    _history_type_label(record.source_type),
                    _history_status_label(record.status),
                    f"{record.progress:.0f}%",
                    _format_size(record.bytes_done),
                    time.strftime("%Y-%m-%d %H:%M", time.localtime(record.updated_at)),
                    record.output_path,
                ),
            )

    def _selected_history(self) -> DownloadRecord | None:
        selection = self.history_tree.selection()
        if not selection:
            return None
        record_id = selection[0]
        return next((item for item in self.history_records if item.record_id == record_id), None)

    def _open_history_output(self) -> None:
        record = self._selected_history()
        if not record:
            return
        path = Path(record.output_path).expanduser().parent
        try:
            path.mkdir(parents=True, exist_ok=True)
            os.startfile(path)
        except OSError as exc:
            self._show_error(classify_error(exc))

    def _reuse_history(self) -> None:
        record = self._selected_history()
        if not record:
            return
        self.url_var.set(record.source_url)
        output = Path(record.output_path)
        self.output_dir_var.set(str(output.parent))
        self.file_name_var.set(output.name)
        self.main_notebook.select(self.download_tab)
        self.url_entry.focus_set()
        self._show_notice("info", "任务信息已填入", "历史记录仅保留脱敏地址；若原链接带临时签名，请返回原视频页面重新解析。")

    def _clear_completed_history(self) -> None:
        self.history_records = self.history_store.clear_completed()
        self._refresh_history()

    def _show_error(self, error: UserFacingError) -> None:
        self.status_var.set(error.title)
        self._log(f"{error.title}：{error.detail or error.message}", "error")
        self._show_notice("error", error.title, f"{error.message} {error.action}")

    def _show_notice(self, kind: str, title: str, text: str) -> None:
        palette = {
            "success": ("#E8F7EF", "#18794E", "#2F6F52"),
            "warning": ("#FFF5D8", "#8A5A00", "#765B25"),
            "error": ("#FDEBEC", "#B4232A", "#85434A"),
            "info": ("#E9F2FF", "#1559A6", "#3E628D"),
        }
        background, title_color, text_color = palette.get(kind, palette["info"])
        self.notice_frame.configure(bg=background)
        self.notice_title.configure(text=title, bg=background, fg=title_color)
        self.notice_text.configure(text=text, bg=background, fg=text_color)
        self.notice_frame.grid()

    def _hide_notice(self) -> None:
        self.notice_frame.grid_remove()

    def _log(self, message: str, level: str = "info") -> None:
        if not message:
            return
        prefix = {"error": "[错误]", "warning": "[警告]", "debug": "[调试]", "info": "[信息]"}.get(level, "[信息]")
        self.log_text.insert(tk.END, f"{time.strftime('%H:%M:%S')} {prefix} {message}\n")
        self.log_text.see(tk.END)

    def _on_close(self) -> None:
        if self.is_downloading and not messagebox.askyesno("退出下载器", "当前任务仍在下载。退出后可依靠缓存继续，确定退出吗？"):
            return
        if self.current_job:
            self.current_job.stop()
        self.destroy()


def _status_color(status: str) -> str:
    return {
        "pending": "#DDE2EA",
        "partial": "#82B4F8",
        "downloading": "#F3B33D",
        "done": "#2BA471",
        "error": "#E5484D",
    }.get(status, "#DDE2EA")


def _format_duration(seconds: float) -> str:
    if not seconds:
        return "-"
    seconds = int(seconds)
    hours, rem = divmod(seconds, 3600)
    minutes, secs = divmod(rem, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"


def _format_size(value: float) -> str:
    units = ["B", "KB", "MB", "GB", "TB"]
    size = float(value)
    for unit in units:
        if size < 1024 or unit == units[-1]:
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


def _format_eta(seconds: float) -> str:
    seconds = max(0, int(seconds))
    hours, rem = divmod(seconds, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours} 小时 {minutes} 分"
    if minutes:
        return f"{minutes} 分 {secs} 秒"
    return f"{secs} 秒"


def _default_suffix_for_candidate(candidate: VideoCandidate) -> str:
    if candidate.source_type in {"youtube", "ytdlp"}:
        return ".mp4"
    if candidate.source_type == "direct":
        suffix = Path(candidate.url.split("?", 1)[0]).suffix.lower()
        return suffix if suffix else ".mp4"
    return ".ts"


def _candidate_kind_label(candidate: VideoCandidate) -> str:
    return {"youtube": "YouTube", "ytdlp": "网页媒体", "direct": "视频直链", "hls": "HLS"}.get(candidate.source_type, "媒体")


def _candidate_format_label(candidate: VideoCandidate) -> str:
    if candidate.container:
        return candidate.container.upper()
    if candidate.source_type == "direct":
        return Path(urlparse(candidate.url).path).suffix.lstrip(".").upper() or "VIDEO"
    if candidate.source_type == "hls":
        return "M3U8"
    return "自动"


def _candidate_engine_label(candidate: VideoCandidate) -> str:
    if candidate.source_type in {"youtube", "ytdlp"}:
        return "yt-dlp"
    if candidate.source_type == "direct":
        return "HTTP"
    return "HLS / AES" if candidate.encrypted else "HLS"


def _candidate_origin_label(candidate: VideoCandidate) -> str:
    if candidate.extractor:
        return candidate.extractor
    return urlparse(candidate.url).hostname or "未知来源"


def _candidate_structure_label(candidate: VideoCandidate) -> str:
    if candidate.source_type in {"youtube", "ytdlp"}:
        return "自动选择并合并"
    if candidate.source_type == "direct":
        return "单文件续传"
    return f"{candidate.segment_count} 个分片" if candidate.segment_count else "HLS 播放列表"


def _candidate_summary(candidate: VideoCandidate) -> str:
    parts = [_candidate_kind_label(candidate), _candidate_format_label(candidate), _candidate_engine_label(candidate)]
    if candidate.resolution:
        parts.append(candidate.resolution)
    if candidate.duration:
        parts.append(_format_duration(candidate.duration))
    parts.append(_candidate_structure_label(candidate))
    return " · ".join(parts)


def _history_status_label(status: str) -> str:
    return {
        "preparing": "准备中",
        "downloading": "下载中",
        "paused": "已暂停",
        "completed": "已完成",
        "failed": "需重试",
        "stopped": "已停止",
        "interrupted": "已中断",
    }.get(status, "未知")


def _history_type_label(source_type: str) -> str:
    return {"youtube": "YouTube", "ytdlp": "网页", "direct": "直链", "hls": "HLS"}.get(source_type, source_type.upper())


def _available_output_path(path: Path) -> Path:
    if not path.exists():
        return path
    for index in range(1, 1000):
        candidate = path.with_name(f"{path.stem} ({index}){path.suffix}")
        if not candidate.exists():
            return candidate
    return path.with_name(f"{path.stem}-{int(time.time())}{path.suffix}")


def _resource_path(relative_path: str) -> Path:
    base_path = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return base_path / relative_path


if __name__ == "__main__":
    try:
        app = UniversalVideoDownloaderApp()
        app.mainloop()
    except HlsError as exc:
        messagebox.showerror("错误", str(exc))
